"""Deterministic quality score (0-100) per document ID, style detection, recommendation."""
from __future__ import annotations

import re
from collections import Counter

from . import config, markers, style
from .check import NUM_RE

_MARK_RE = re.compile(r"\[\s*참고\s*\d+\s*\]|【\s*참고\s*\d+\s*[】\]\)]")
_TABLE_LINE_RE = re.compile(r"^\s*(\||<table\b)", re.I)
_ENUM_RE = re.compile(r"^\s*\(?\d+[.)]\s+")

COMPONENTS = ("title", "headings", "conclusion", "markers", "style", "table", "numbers", "length")


def detect_style(lines: list[dict]) -> dict:
    """Majority style of the in-scope lines: {kind, ending, counts}."""
    c: Counter = Counter()
    for ln in lines:
        if style.in_scope(ln):
            cls = style.ending_class(ln["body"])
            if cls:
                c[cls] += 1
    sentence = {k: c[k] for k in ("b1", "b2", "b3") if c[k]}
    if not c:
        return {"kind": None, "ending": None, "counts": {}}
    if c["gaejo"] >= sum(sentence.values()):
        return {"kind": "개조식", "ending": None, "counts": dict(c)}
    ending = max(sorted(sentence), key=lambda k: sentence[k])
    return {"kind": "서술식", "ending": ending, "counts": dict(c)}


def _number_stats(lines: list[dict]) -> tuple[int, int]:
    """(sourced numbers, all numbers)."""
    total = sourced = 0
    for ln in lines:
        if ln["kind"] in ("blank", "author", "checklist_section"):
            continue
        raw = _ENUM_RE.sub("", _MARK_RE.sub(" ", ln["raw"]))
        n = len(NUM_RE.findall(raw))
        if not n:
            continue
        total += n
        if (ln["kind"] == "note" or _MARK_RE.search(ln["raw"])
                or ln["section"] == "attachment"):
            sourced += n
    return sourced, total


def score_texts(main_text: str, attach_texts: list[str] | None = None, *, body_pages: int = 1,
                unmeasurable: bool = False) -> dict:
    """Score one document ID given its 본문 text and 참고 texts (판본 are excluded by the caller)."""
    attach_texts = attach_texts or []
    if unmeasurable:
        return {"score": None, "unmeasurable": True, "components": {}, "marker_system": None,
                "style": {"kind": None, "ending": None}}
    all_texts = [main_text, *attach_texts]
    ms = markers.extract_marker_system(main_text) or markers.extract_marker_system(
        "\n".join(all_texts))
    parsed = [markers.parse_lines(t, ms) for t in all_texts]
    main_lines = parsed[0]
    flat = [ln for p in parsed for ln in p]
    comp = dict.fromkeys(COMPONENTS, 0.0)
    comp["title"] = 10.0 if any(ln["kind"] == "title" for ln in main_lines) else 0.0
    comp["headings"] = 10.0 if sum(1 for ln in main_lines if ln["kind"] == "heading") >= 2 else 0.0
    comp["conclusion"] = 10.0 if any(ln["kind"] == "conclusion" for ln in flat) else 0.0
    if ms:
        bad = tot = 0
        for p in parsed:
            b, t = markers.consistency(p)
            bad, tot = bad + b, tot + t
        comp["markers"] = 25.0 * (1 - bad / tot) if tot else 0.0
    st = detect_style(flat)
    in_scope = [ln for ln in flat if style.in_scope(ln)]
    if in_scope and st["kind"]:
        ok = sum(1 for ln in in_scope if style.judge_ending(ln["body"], st)[0])
        comp["style"] = 25.0 * ok / len(in_scope)
    has_table = any(_TABLE_LINE_RE.match(ln["raw"]) for ln in flat)
    comp["table"] = 7.0 if has_table else 0.0
    sourced, total = _number_stats(flat)
    comp["numbers"] = 8.0 if total == 0 else 8.0 * sourced / total
    comp["length"] = 5.0 if 1 <= body_pages <= 10 else 0.0
    comp = {k: round(v, 4) for k, v in comp.items()}
    score = round(sum(comp.values()), 2)
    return {"score": score, "unmeasurable": False, "components": comp, "marker_system": ms,
            "style": {"kind": st["kind"], "ending": st["ending"]},
            "report_like": ms is not None}


def _read_md(conn, rel) -> str:
    from . import warehouse
    if not rel:
        return ""
    p = warehouse.abs_path(conn, rel)
    return p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""


def score_document(conn, doc_id: str, *, store: bool = True) -> dict:
    """Score a document ID from its stored extracts; optionally write documents row."""
    rows = conn.execute("SELECT * FROM files WHERE doc_id=? ORDER BY id", (doc_id,)).fetchall()
    main = [r for r in rows if r["role"] == "본문"]
    attach = [r for r in rows if r["role"] == "참고"]
    cutoff_ratio = config.settings()["unmeasurable_low_conf_ratio"]
    used = main + attach
    failed = any(not r["extract_ok"] for r in used) or not used
    ocr_n = sum(r["ocr_line_count"] or 0 for r in used)
    low_n = sum(r["low_conf_count"] or 0 for r in used)
    low_bad = ocr_n > 0 and low_n / ocr_n > cutoff_ratio
    unmeasurable = bool(failed or low_bad)
    main_text = _read_md(conn, main[0]["md_path"]) if main else ""
    if not main and attach:
        main_text = _read_md(conn, attach[0]["md_path"])
    attach_texts = [_read_md(conn, r["md_path"]) for r in attach if main or r is not attach[0]]
    pages = (main[0]["pages"] if main else 0) or 0
    result = score_texts(main_text, attach_texts, body_pages=pages, unmeasurable=unmeasurable)
    if store:
        from . import warehouse
        with warehouse.tx(conn):
            conn.execute(
                "UPDATE documents SET quality_score=?, unmeasurable=?, report_like=? WHERE doc_id=?",
                (result["score"], 1 if result["unmeasurable"] else 0,
                 1 if (result.get("report_like") and not result["unmeasurable"]) else 0, doc_id))
    return result


def score_all(conn) -> dict[str, float | None]:
    out = {}
    for r in conn.execute("SELECT doc_id FROM documents ORDER BY doc_id").fetchall():
        out[r["doc_id"]] = score_document(conn, r["doc_id"])["score"]
    return out


def recommend(conn, doc_type: str | None = None) -> list[dict]:
    """Starred first, then matching document type, then higher score (unmeasurable last)."""
    rows = [dict(r) for r in conn.execute("SELECT * FROM documents").fetchall()]

    def key(d):
        score = d["quality_score"]
        return (-(1 if d["starred"] else 0),
                -(1 if doc_type and d["doc_type"] == doc_type else 0),
                -(score if score is not None else -1.0),
                d["doc_id"])
    return sorted(rows, key=key)
