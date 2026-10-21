"""Per-report workflow: folder files are the source of truth for resume."""
from __future__ import annotations

import difflib
import hashlib
import re
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

PLACEHOLDER = "(작성 필요)"
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?\s?(?:%|명|개|건|회|원|억원|만원|천원|년|개월|월|일|점|배|세|시간|km|kg|㎡|p)?")


# ---------- small io helpers ----------
def _load(path, default):
    p = Path(path)
    if not p.exists():
        return default
    data = yaml.safe_load(p.read_text(encoding="utf-8"))
    return default if data is None else data


def _dump(path, data):
    Path(path).write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _read_text(path) -> str:
    p = Path(path)
    if p.suffix.lower() in (".md", ".txt"):
        return p.read_text(encoding="utf-8", errors="replace")
    from . import extract as ex
    r = ex.extract(str(p))
    if not r.get("ok"):
        raise RuntimeError(f"extract failed for {p}: {r.get('error')}")
    return r["markdown"]


# ---------- state ----------
def new_state(name):
    return {"report_name": name, "style": {"kind": None, "ending": None},
            "chosen_reference_id": None, "reference_text_hash": None,
            "remaining_questions": [], "ouroboros_session_id": None,
            "last_checked_draft_hash": None, "phase": "init"}


def load_state(folder):
    return _load(Path(folder) / "state.yaml", None) or new_state(Path(folder).name)


def save_state(folder, state):
    _dump(Path(folder) / "state.yaml", state)


# ---------- init ----------
def init_report(name, folder, draft=None, evidence=()):
    """Create the folder. Starting draft is inherited into draft.md (never overwritten)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "reference").mkdir(exist_ok=True)
    if not (folder / "state.yaml").exists():
        save_state(folder, new_state(name))
    ev = _load(folder / "evidence.yaml", [])
    known = {e["path"] for e in ev}
    for e in evidence:
        e = str(Path(e).resolve())
        if e not in known:
            ev.append({"path": e, "kind": "evidence"})
    if draft:
        sd = folder / "starting_draft"
        sd.mkdir(exist_ok=True)
        src = Path(draft)
        if src.resolve() != (sd / src.name).resolve():
            shutil.copy2(src, sd / src.name)
        text = _read_text(src)
        (sd / "extract.md").write_text(text, encoding="utf-8")
        path = str((sd / src.name).resolve())
        if path not in known:
            ev.append({"path": path, "kind": "starting_draft"})
        if not (folder / "draft.md").exists():
            (folder / "draft.md").write_text(text, encoding="utf-8")
    _dump(folder / "evidence.yaml", ev)
    for fn in ("facts.yaml", "candidates.yaml", "interview_log.yaml"):
        if not (folder / fn).exists():
            _dump(folder / fn, [])
    prefill_candidates(folder)
    st = load_state(folder)
    st["phase"] = "confirming"
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)
    return folder


# ---------- candidates / facts ----------
def _scan(text, source):
    out = []
    for raw in text.splitlines():
        line = re.sub(r"^[#>\-\*\s]+", "", raw).strip()
        if not line:
            continue
        cells = [c.strip() for c in line.strip("|").split("|")] if "|" in line else []
        if len(cells) >= 2 and not set("".join(cells)) <= set("-: "):
            if re.search(r"\d", cells[1]):
                out.append((cells[0], cells[1]))
            continue
        m = re.match(r"([^:：]{1,30})[:：]\s*(.+)$", line)
        if m and re.search(r"\d", m.group(2)):
            out.append((m.group(1).strip(), m.group(2).strip()))
            continue
        for n in _NUM.finditer(line):
            tok = n.group(0).strip()
            if len(re.sub(r"\D", "", tok)) < 1:
                continue
            before, after = line[max(0, n.start() - 1):n.start()], line[n.end():n.end() + 1]
            if (before and before in "-_:/.") or (after and after in "-_"):
                continue  # piece of a date, id, path or file:line reference
            ctx = line[max(0, n.start() - 12):n.start()].strip() or line[:12]
            if not re.search(r"[가-힣]", ctx + tok):
                continue
            out.append((f"{ctx} {tok}".strip(), tok))
    return [{"name": n, "value": v, "evidence_source": source} for n, v in out]


def prefill_candidates(folder):
    """Rebuild candidates.yaml from evidence + starting draft. Confirmed facts untouched."""
    folder = Path(folder)
    found, failed = [], set()
    for e in _load(folder / "evidence.yaml", []):
        p = Path(e["path"])
        try:
            text = _read_text(p) if e["kind"] == "evidence" else \
                (folder / "starting_draft" / "extract.md").read_text(encoding="utf-8")
        except Exception as exc:
            print(f"경고: 증거 읽기 실패, 기존 후보 유지: {p.name}: {exc}", file=sys.stderr)
            failed.add(p.name)
            continue
        found += _scan(text, p.name)
    old = {(c["name"], c["value"]): c for c in _load(folder / "candidates.yaml", [])}
    seen, cands = {}, []
    for c in found:
        key = (c["name"], c["value"])
        if key in seen:
            continue
        seen[key] = c
        c["conflict_with"] = []
        c["status"] = old.get(key, {}).get("status", "pending")
        cands.append(c)
    for key, c in old.items():  # agent-added candidates and those of unreadable evidence survive
        if key not in seen and (c.get("added") or c.get("evidence_source") in failed):
            seen[key] = c
            cands.append(c)
    for c in cands:  # same name, different value
        c["conflict_with"] = sorted({o["evidence_source"] for o in cands
                                     if o["name"] == c["name"] and o["value"] != c["value"]})
    _dump(folder / "candidates.yaml", cands)
    return cands


def add_candidate(folder, name, value, source):
    """Agent-proposed candidate read from evidence; still needs user confirmation."""
    folder = Path(folder)
    cands = [c for c in _load(folder / "candidates.yaml", [])
             if (c["name"], str(c["value"])) != (name, str(value))]
    others = sorted({c["evidence_source"] for c in cands if c["name"] == name})
    cands.append({"name": name, "value": str(value), "evidence_source": source,
                  "conflict_with": others, "status": "pending", "added": True})
    _dump(folder / "candidates.yaml", cands)
    _refresh_remaining(folder)


def dismiss_candidates(folder, names=None, rest=False):
    """User rejects candidates that are not facts of this report. rest=True: every pending one."""
    folder = Path(folder)
    confirmed = _confirmed_keys(folder)
    cands = _load(folder / "candidates.yaml", [])
    n = 0
    for c in cands:
        if c.get("status", "pending") != "pending" or (c["name"], str(c["value"])) in confirmed:
            continue
        if rest or c["name"] in (names or ()):
            c["status"] = "dismissed"
            n += 1
    _dump(folder / "candidates.yaml", cands)
    _refresh_remaining(folder)
    return n


def _refresh_remaining(folder):
    st = load_state(folder)
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)


def load_facts(folder):
    return _load(Path(folder) / "facts.yaml", [])


def _confirmed_keys(folder):
    return {(f["name"], str(f["value"])) for f in load_facts(folder)}


def confirm_fact(folder, name, value=None, aliases=None, used_in_body=True, source=None):
    """User confirms (or corrects) a candidate. facts.yaml holds only confirmed items; latest wins.

    The candidate with the same name and value is the one confirmed; other pending candidates
    of that name were the alternatives the user chose against and become dismissed.
    """
    folder = Path(folder)
    cands = _load(folder / "candidates.yaml", [])
    named = [c for c in cands if c["name"] == name]
    if value is None:
        if not named:
            raise KeyError(f"no candidate named {name!r}; pass --value")
        value = named[0]["value"]
    cand = next((c for c in named if str(c["value"]) == str(value)), None)
    fact = {"name": name, "value": str(value), "aliases": list(aliases or []),
            "used_in_body": bool(used_in_body),
            "evidence_source": source or (cand or {}).get("evidence_source", "user")}
    for c in named:
        if c is cand:
            c["status"] = "confirmed"
        elif c.get("status", "pending") == "pending":
            c["status"] = "dismissed"
    if named:
        _dump(folder / "candidates.yaml", cands)
    facts = [f for f in load_facts(folder) if f["name"] != name] + [fact]
    _dump(folder / "facts.yaml", facts)
    st = load_state(folder)
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)
    return fact


# ---------- interview log ----------
def log_answer(folder, question, answer, source="user"):
    """Latest answer wins: an existing entry for the question is replaced in place."""
    folder = Path(folder)
    log = _load(folder / "interview_log.yaml", [])
    e = next((x for x in log if x["question"] == question), None)
    if e is None:
        e = {"question": question, "answer": answer, "answer_source": source, "approvals": []}
        log.append(e)
    else:
        e["answer"], e["answer_source"] = answer, source
    _dump(folder / "interview_log.yaml", log)
    st = load_state(folder)
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)
    return e


def record_approval(folder, question, before, after, approved):
    folder = Path(folder)
    log = _load(folder / "interview_log.yaml", [])
    e = next((x for x in log if x["question"] == question), None)
    if e is None:
        e = {"question": question, "answer": None, "answer_source": "refined", "approvals": []}
        log.append(e)
    e["approvals"].append({"before": before, "after": after, "approved": bool(approved)})
    _dump(folder / "interview_log.yaml", log)
    return e


# ---------- remaining questions ----------
def remaining_questions(folder):
    folder = Path(folder)
    st = load_state(folder)
    qs = []
    if not st.get("chosen_reference_id"):
        qs.append("따를 라이브러리 참고문서를 선택")
    if not (st.get("style") or {}).get("kind"):
        qs.append("보고서 문체(개조식/서술식, 종결어미) 선택")
    confirmed = _confirmed_keys(folder)
    for c in _load(folder / "candidates.yaml", []):
        if (c["name"], str(c["value"])) not in confirmed and c.get("status", "pending") == "pending":
            qs.append(f"후보 확인: {c['name']} = {c['value']}")
    draft = folder / "draft.md"
    if draft.exists():
        for ln in draft.read_text(encoding="utf-8").splitlines():
            if PLACEHOLDER in ln:
                qs.append(f"작성 필요: {ln.strip()[:60]}")
    answered = {e["question"] for e in _load(folder / "interview_log.yaml", []) if e.get("answer")}
    return [q for q in qs if q not in answered]


def resume_summary(folder):
    """Everything a fresh process needs, from folder files only."""
    folder = Path(folder)
    st = load_state(folder)
    return {"report_name": st["report_name"], "phase": st.get("phase"),
            "style": st.get("style"), "chosen_reference_id": st.get("chosen_reference_id"),
            "session_id": st.get("ouroboros_session_id"),
            "confirmed_facts": [{"name": f["name"], "value": f["value"]} for f in load_facts(folder)],
            "remaining_questions": remaining_questions(folder)}


def format_summary(s):
    lines = [f"보고서: {s['report_name']} (phase={s['phase']})",
             f"문체: {s['style']}", f"참고문서: {s['chosen_reference_id']}", "확정 사실:"]
    lines += [f"  - {f['name']} = {f['value']}" for f in s["confirmed_facts"]] or ["  (없음)"]
    lines.append("남은 질문:")
    lines += [f"  - {q}" for q in s["remaining_questions"]] or ["  (없음)"]
    return "\n".join(lines)


# ---------- interview backend ----------
def seed_context(folder):
    """Interview seed built from folder files only."""
    folder = Path(folder)
    s = resume_summary(folder)
    parts = [f"Report: {s['report_name']}",
             f"Style: {s['style']}", f"Reference doc: {s['chosen_reference_id']}",
             "Confirmed facts:"]
    parts += [f"- {f['name']}: {f['value']}" for f in s["confirmed_facts"]]
    parts.append("Interview so far:")
    for e in _load(folder / "interview_log.yaml", []):
        parts.append(f"- Q: {e['question']} / A: {e.get('answer')}")
    parts.append("Remaining questions:")
    parts += [f"- {q}" for q in s["remaining_questions"]]
    ref = outline_of_reference(folder)
    if ref:
        parts.append("Reference outline:")
        parts += [f"- {x}" for x in ref]
    return "\n".join(parts)


def interview_backend(state, mcp_call, folder=None):
    """Return 'mcp' or 'ask'. mcp_call(args: dict) -> dict (or raises).

    A cached session_id is tried first; if it is unreadable a new interview is
    seeded from the folder. Any MCP failure degrades to 'ask'; never raises.
    """
    sid = state.get("ouroboros_session_id")

    def _ok(r):
        return isinstance(r, dict) and not r.get("is_error") and not r.get("error")

    try:
        if sid:
            try:
                r = mcp_call({"session_id": sid})
                if _ok(r):
                    return "mcp"
            except Exception:
                pass
            state["ouroboros_session_id"] = None
        seed = seed_context(folder) if folder else ""
        r = mcp_call({"initial_context": seed})
        if not _ok(r):
            raise RuntimeError("interview start failed")
        state["ouroboros_session_id"] = r.get("session_id") or (r.get("meta") or {}).get("session_id")
        state["interview_backend"] = "mcp"
        return "mcp"
    except Exception:
        state["ouroboros_session_id"] = None
        state["interview_backend"] = "ask"
        return "ask"
    finally:
        if folder:
            save_state(folder, state)


# ---------- reference ----------
def recommend(conn, doc_type=None, limit=5):
    from . import quality
    return quality.recommend(conn, doc_type)[:limit]


def sync_reference(folder, doc_id, conn, base=None, force=False):
    """Copy the chosen doc's extracts + marker system into reference/. Rebuild when the
    doc gains a file or its text hash changes. Returns True when rebuilt."""
    from . import warehouse
    folder = Path(folder)
    h = warehouse.reference_hash(conn, doc_id)
    st = load_state(folder)
    if (not force and st.get("chosen_reference_id") == doc_id
            and st.get("reference_text_hash") == h):
        return False
    ref = folder / "reference"
    if ref.exists():
        shutil.rmtree(ref)
    r = warehouse.write_reference_folder(conn, doc_id, ref)
    st["chosen_reference_id"] = doc_id
    st["reference_text_hash"] = r.get("text_hash", h)
    save_state(folder, st)
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)
    return True


def set_style(folder, kind, ending=None):
    if kind not in ("개조식", "서술식"):
        raise ValueError("kind must be 개조식 or 서술식")
    if kind == "서술식" and ending not in ("b1", "b2", "b3"):
        raise ValueError("서술식 needs --ending b1|b2|b3")
    st = load_state(folder)
    st["style"] = {"kind": kind, "ending": ending if kind == "서술식" else None}
    save_state(folder, st)
    st["remaining_questions"] = remaining_questions(folder)
    save_state(folder, st)
    return st["style"]


# ---------- outline ----------
_HEAD = re.compile(r"^\s*(#+\s*\S|[□■]\s*\S|\d{1,2}\.\s+\S|[IVX]+\.\s+\S)")
# Section names that OCR often leaves without their □ glyph.
_SECTION = re.compile(r"^(현황(및|/)?문제점|문제점|추진배경|추진경과|검토(내용|결과)|개선[(\[【]?안[)\]】]?|"
                      r"적용시기|향후(추진|계획)|기대효과|건의(사항)?|조치(사항|계획)|결론)(?:[:：].*)?$")
_ATTACH = re.compile(r"^\s*[\[【]\s*참고\s*\d+\s*[\]】)]")


def _headings(text):
    out = []
    for ln in text.splitlines():
        s = re.sub(r"^[#\s]+", "", ln).strip()
        if not s or s.startswith("<"):
            continue
        bare = re.sub(r"\s+", "", s.lstrip("□■ "))
        if _HEAD.match(ln) or _ATTACH.match(s) or (len(bare) <= 30 and _SECTION.match(bare)):
            out.append(s)
    return out


def outline_of_reference(folder):
    """Body (본문_) headings, then the title line of each attachment file when the body
    lists no [참고 N] itself."""
    ref = Path(folder) / "reference"
    files = sorted(ref.glob("*.md")) if ref.exists() else []
    body = [f for f in files if f.name.startswith("본문_")]
    if not body:
        return [h for p in files for h in _headings(p.read_text(encoding="utf-8"))]
    out = [h for p in body for h in _headings(p.read_text(encoding="utf-8"))]
    if not any(_ATTACH.match(h) for h in out):
        for p in files:
            if p.name.startswith("참고_"):
                first = next((ln.strip() for ln in p.read_text(encoding="utf-8").splitlines()
                              if ln.strip()), "")
                if first:
                    out.append(re.sub(r"^#+\s*", "", first))
    return out


def outline_of_draft(folder):
    d = Path(folder) / "draft.md"
    return _headings(d.read_text(encoding="utf-8")) if d.exists() else []


def outline(folder):
    a, b = outline_of_reference(folder), outline_of_draft(folder)
    w = max([len(x) for x in a] + [10])
    n = max(len(a), len(b))
    rows = [f"{'REFERENCE'.ljust(w)} | DRAFT"]
    for i in range(n):
        rows.append(f"{(a[i] if i < len(a) else '').ljust(w)} | {b[i] if i < len(b) else ''}")
    return "\n".join(rows)


# ---------- regeneration guard ----------
def draft_hash(folder):
    d = Path(folder) / "draft.md"
    return hashlib.sha256(d.read_bytes()).hexdigest() if d.exists() else None


def mark_checked(folder):
    folder = Path(folder)
    st = load_state(folder)
    st["last_checked_draft_hash"] = draft_hash(folder)
    save_state(folder, st)
    if (folder / "draft.md").exists():
        shutil.copy2(folder / "draft.md", folder / ".draft.last_checked.md")


def regen_guard(folder, proposed=None):
    """User edits (draft hash != last checked) are kept unless the user approves replacement."""
    folder = Path(folder)
    st = load_state(folder)
    cur = draft_hash(folder)
    last = st.get("last_checked_draft_hash")
    edited = last is not None and cur is not None and cur != last
    diff = ""
    snap = folder / ".draft.last_checked.md"
    if edited and snap.exists():
        diff = "".join(difflib.unified_diff(
            snap.read_text(encoding="utf-8").splitlines(True),
            (folder / "draft.md").read_text(encoding="utf-8").splitlines(True),
            "last_checked", "current"))
    if proposed is not None and cur is not None:
        diff += "".join(difflib.unified_diff(
            (folder / "draft.md").read_text(encoding="utf-8").splitlines(True),
            Path(proposed).read_text(encoding="utf-8").splitlines(True),
            "current", "proposed"))
    return {"user_edited": edited, "keep_user_text": edited, "diff": diff}
