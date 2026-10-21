"""Mechanical draft gate: (i) facts, (ii) 확인 필요 marks, (iii) unsupported numbers, (iv) endings.

Reads reports/<name>/{facts,state}.yaml and reference/*; never reads candidates.yaml.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import re
from pathlib import Path

import yaml

from . import markers, style

NUM_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
INLINE_MARK_RE = re.compile(r"\[\s*확인\s*필요\s*(\d+)\s*:")
LIST_ITEM_RE = re.compile(r"\[\s*확인\s*필요\s*(\d+)\s*\]")
_NOISE_RE = re.compile(r"\[\s*참고\s*\d+\s*\]|【\s*참고\s*\d+\s*[】\]\)]|\[\s*확인\s*필요\s*\d+\s*[:\]]")
_ENUM_RE = re.compile(r"^\s*\(?\d+[.)]\s+")


def extract_numbers(text: str) -> set[str]:
    t = _NOISE_RE.sub(" ", text)
    return {m.replace(",", "") for m in NUM_RE.findall(t)}


def _load_yaml(p: Path, default):
    try:
        with open(p, encoding="utf-8") as f:
            d = yaml.safe_load(f)
        return default if d is None else d
    except (OSError, yaml.YAMLError):
        return default


def _fact_strings(fact: dict) -> list[str]:
    out = []
    if fact.get("value") is not None:
        out.append(str(fact["value"]))
    for a in fact.get("aliases") or []:
        out.append(str(a))
    return out


def _split_checklist(lines: list[str]) -> tuple[int | None, list[str]]:
    """Index of the '□ 확인 필요 목록' heading (or None) and its content lines."""
    for i, raw in enumerate(lines):
        s = re.sub(r"^#{1,6}\s*", "", raw.strip())
        if re.match(r"^[□■]\s*확인\s*필요\s*목록\s*$", s):
            return i, lines[i + 1:]
    return None, []


def check_text(draft_text: str, folder: Path) -> dict:
    failures: dict[str, list[str]] = {"i": [], "ii": [], "iii": [], "iv": []}
    lines = draft_text.split("\n")
    facts = _load_yaml(folder / "facts.yaml", [])
    if isinstance(facts, dict):
        facts = facts.get("facts", [])
    state = _load_yaml(folder / "state.yaml", {})
    ms = _load_yaml(folder / "reference" / "marker_system.yaml", None)
    ref_dir = folder / "reference"

    # (i) facts used in body appear verbatim
    for f in facts:
        if not isinstance(f, dict) or not f.get("used_in_body"):
            continue
        cands = _fact_strings(f)
        if not any(c and c in draft_text for c in cands):
            failures["i"].append(f"fact '{f.get('name')}' not found verbatim (value or alias)")

    # (ii) inline marks <-> checklist, 1:1 by N
    ck_idx, ck_lines = _split_checklist(lines)
    body_lines = lines if ck_idx is None else lines[:ck_idx]
    inline: list[int] = []
    for ln in body_lines:
        inline += [int(n) for n in INLINE_MARK_RE.findall(ln)]
    listed: list[int] = []
    for ln in ck_lines:
        m = LIST_ITEM_RE.search(ln)
        if m:
            listed.append(int(m.group(1)))
    for n in sorted({n for n in inline if inline.count(n) > 1}):
        failures["ii"].append(f"duplicate inline mark {n}")
    for n in sorted({n for n in listed if listed.count(n) > 1}):
        failures["ii"].append(f"duplicate checklist entry {n}")
    for n in sorted(set(inline) - set(listed)):
        failures["ii"].append(f"mark {n} has no entry in '□ 확인 필요 목록'")
    for n in sorted(set(listed) - set(inline)):
        failures["ii"].append(f"checklist entry {n} has no inline mark")

    # (iii) numbers not supported by facts or reference extracts must sit in a marked line
    supported: set[str] = set()
    for f in facts:
        if isinstance(f, dict):
            for s in _fact_strings(f):
                supported |= extract_numbers(s)
    if ref_dir.is_dir():
        for p in sorted(ref_dir.glob("*.md")):
            supported |= extract_numbers(p.read_text(encoding="utf-8", errors="replace"))
    parsed = markers.parse_lines(draft_text, ms)
    for i, (raw, pl) in enumerate(zip(lines, parsed), start=1):
        if ck_idx is not None and i - 1 >= ck_idx:
            break
        if pl["kind"] in ("blank", "author") or INLINE_MARK_RE.search(raw):
            continue
        for n in sorted(extract_numbers(_ENUM_RE.sub("", raw))):
            if n not in supported:
                failures["iii"].append(f"line {i}: number {n} is unsupported and unmarked")

    # (iv) endings follow the stored style
    st = state.get("style") if isinstance(state, dict) else None
    if not st or not st.get("kind"):
        failures["iv"].append("state.yaml has no style")
    elif not ms:
        failures["iv"].append("reference/marker_system.yaml missing")
    else:
        for i, pl in enumerate(parsed, start=1):
            if style.in_scope(pl):
                ok, reason = style.judge_ending(pl["body"], st)
                if not ok:
                    failures["iv"].append(f"line {i}: {reason}: {pl['body'][:40]}")
    return failures


def check_draft(draft_path) -> dict:
    draft_path = Path(draft_path)
    folder = draft_path.resolve().parent
    raw = draft_path.read_bytes()
    failures = check_text(raw.decode("utf-8"), folder)
    ok = not any(failures.values())
    result = {
        "exit_code": 0 if ok else 1,
        "failures": failures,
        "checked_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "draft_sha256": hashlib.sha256(raw).hexdigest(),
    }
    (folder / "check_result.yaml").write_text(
        yaml.safe_dump(result, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return {"ok": ok, "failures": failures, "exit_code": result["exit_code"],
            "draft_sha256": result["draft_sha256"]}
