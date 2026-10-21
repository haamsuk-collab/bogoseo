"""Marker-system extraction and line classification (reference-driven, not hardcoded).

A document's marker hierarchy is the order in which its marker glyph groups first appear
(e.g. □ -> ㅇ -> -). Check script and quality score both read this system.
"""
from __future__ import annotations

import re

# Glyph groups (synonyms). A group is a *shape family*; hierarchy order comes from the document.
_GROUPS: list[str] = ["□■◇◆▣▢", "ㅇ○●◦o", "-–—•·∙▪‧▶▷▸"]
_GLYPH_TO_GROUP = {g: i for i, grp in enumerate(_GROUPS) for g in grp}
_ALL_GLYPHS = "".join(_GROUPS)

CONCLUSION = "☞"
NOTE = "*"
CAPTION = "※"

ATTACHMENT_PATTERN = r"^[\[【]\s*참고\s*\d+\s*[\]】)]"
AUTHOR_PATTERN = (r"^<[^<>]*?\d{2}\.\s*\d{1,2}\.\s*\d{1,2}\.\s*\([월화수목금토일]\)\s*>$")

_HASH_RE = re.compile(r"^#{1,6}\s*")
_TABLE_RE = re.compile(r"^(\||</?(table|tr|td|th|thead|tbody)\b|<br)", re.I)
_IMAGE_RE = re.compile(r"^!\[[^\]]*\]\([^)]*\)$")
_CHECKLIST_RE = re.compile(r"^확인\s*필요\s*목록$")
_SHORT_LABEL_RE = re.compile(r"^(?=(?:.*[가-힣]){2})[가-힣\d\s/·()【】\[\]]{2,16}$")
_DATE_RE = re.compile(r"(\d{2})\.\s*(\d{1,2})\.\s*(\d{1,2})\.\s*\([월화수목금토일]\)\s*>?$")


_MD_ESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!|~<>])")


def _strip_hash(line: str) -> tuple[str, bool]:
    """Line without markdown heading prefix and backslash escapes (kordoc writes `\\*`)."""
    s = _MD_ESCAPE_RE.sub(r"\1", line.strip())
    m = _HASH_RE.match(s)
    if m:
        return s[m.end():].strip(), True
    return s, False


def _glyph_of(s: str) -> tuple[str | None, str]:
    """Return (glyph, body) when `s` starts with a marker glyph, else (None, s)."""
    if not s:
        return None, s
    g = s[0]
    if g not in _GLYPH_TO_GROUP:
        return None, s
    rest = s[1:]
    if g == "o":  # latin o only counts as a bullet when followed by a space
        if not rest.startswith(" "):
            return None, s
    if g in "-–—" and re.match(r"^[-–—]{2,}", s):  # rules / separators
        return None, s
    if not rest.strip():
        return None, s
    return g, rest.strip()


def _is_table(s: str) -> bool:
    return bool(_TABLE_RE.match(s))


def extract_marker_system(text: str) -> dict | None:
    """Return the marker system of `text` or None when no marker hierarchy is present."""
    first: dict[int, int] = {}   # group -> first appearance order
    seen: dict[int, list[str]] = {}
    count = 0
    for raw in text.split("\n"):
        s, _ = _strip_hash(raw)
        if not s or _is_table(s) or s[0] in (CONCLUSION, NOTE, CAPTION):
            continue
        g, _body = _glyph_of(s)
        if g is None:
            continue
        gi = _GLYPH_TO_GROUP[g]
        count += 1
        if gi not in first:
            first[gi] = len(first)
        lst = seen.setdefault(gi, [])
        if g not in lst:
            lst.append(g)
    if count < 3 or len(first) < 2:
        return None
    # square glyphs are always the top level when present (OCR often drops them on headings,
    # so first appearance alone would push them down); other groups keep first-appearance order
    order = sorted(first, key=lambda k: (k != 0, first[k]))
    levels = [seen[k] for k in order]
    leading = {_strip_hash(ln)[0][:1] for ln in text.split("\n") if ln.strip()}
    return {
        "levels": levels,
        "conclusion": CONCLUSION if CONCLUSION in leading else None,
        "note": NOTE if _has_leading(text, NOTE) else None,
        "caption": CAPTION if CAPTION in leading else None,
        "attachment": ATTACHMENT_PATTERN,
        "author": AUTHOR_PATTERN,
    }


def _has_leading(text: str, ch: str) -> bool:
    return any(_strip_hash(ln)[0].startswith(ch) for ln in text.split("\n"))


def author_date(text: str) -> str | None:
    """Version date (YYYY-MM-DD) from the first author line, else None."""
    for raw in text.split("\n"):
        s, _ = _strip_hash(raw)
        if re.match(AUTHOR_PATTERN, s):
            m = _DATE_RE.search(s)
            if m:
                yy, mm, dd = (int(x) for x in m.groups())
                return f"{2000 + yy:04d}-{mm:02d}-{dd:02d}"
    return None


def parse_lines(text: str, ms: dict | None) -> list[dict]:
    """One dict per line: {raw, kind, level, body, section, marker}."""
    levels = (ms or {}).get("levels") or []
    glyph_level = {g: i + 1 for i, grp in enumerate(levels) for g in grp}
    # glyphs of the doc's groups at their level; synonyms of the same group share the level
    for g, gi in _GLYPH_TO_GROUP.items():
        if g not in glyph_level:
            for i, grp in enumerate(levels):
                if any(_GLYPH_TO_GROUP.get(x) == gi for x in grp):
                    glyph_level[g] = i + 1
    concl = (ms or {}).get("conclusion") if ms else CONCLUSION
    note = (ms or {}).get("note") if ms else NOTE
    cap = (ms or {}).get("caption") if ms else CAPTION
    att_re = re.compile((ms or {}).get("attachment") or ATTACHMENT_PATTERN)
    auth_re = re.compile((ms or {}).get("author") or AUTHOR_PATTERN)

    out: list[dict] = []
    title_seen = False
    heading_seen = False
    section = "body"

    def add(raw, kind, level=0, body="", marker=None):
        out.append({"raw": raw, "kind": kind, "level": level, "body": body,
                    "section": section, "marker": marker})

    for raw in text.split("\n"):
        s, hashed = _strip_hash(raw)
        if not s or _IMAGE_RE.match(s):
            add(raw, "blank")
            continue
        if auth_re.match(s):
            add(raw, "author", 0, s)
            continue
        if _is_table(s):
            add(raw, "table", 0, s)
            continue
        if att_re.match(s):
            section = "attachment"
            title_seen = True
            add(raw, "attachment", 0, s)
            continue
        g, body = _glyph_of(s)
        if g is not None and g in glyph_level:
            lvl = glyph_level[g]
            if lvl == 1:
                if _CHECKLIST_RE.match(body):
                    section = "checklist"
                    add(raw, "checklist_section", 1, body, g)
                    continue
                if section == "checklist":
                    section = "body"
                heading_seen = True
                add(raw, "heading", 1, body, g)
            else:
                kind = "checklist_section" if section == "checklist" else "item"
                add(raw, kind, lvl, body, g)
            continue
        if concl and s.startswith(concl):
            add(raw, "conclusion", 0, s[len(concl):].strip(), concl)
            continue
        if note and s.startswith(note):
            add(raw, "note", 0, s[len(note):].strip(), note)
            continue
        if cap and s.startswith(cap):
            add(raw, "caption", 0, s[len(cap):].strip(), cap)
            continue
        if section == "checklist":
            add(raw, "checklist_section", 2, s)
            continue
        if not title_seen and not heading_seen:
            title_seen = True
            add(raw, "title", 0, s)
            continue
        if hashed or _SHORT_LABEL_RE.match(s):
            if section == "attachment" and not hashed:
                add(raw, "text", 0, s)
                continue
            heading_seen = True
            if section == "attachment":
                section = "body"
            add(raw, "heading", 1, s)
            continue
        add(raw, "text", 0, s)
    return out


def consistency(lines: list[dict]) -> tuple[int, int]:
    """(violating marker lines, all marker lines). A violation skips a level of the hierarchy."""
    cur = 0
    bad = total = 0
    for ln in lines:
        if ln["kind"] == "heading" and not ln.get("marker"):
            cur = 1  # a heading whose glyph was lost (OCR) still opens a top-level section
            continue
        if ln["kind"] not in ("heading", "item") or not ln.get("marker"):
            continue
        total += 1
        lvl = ln["level"]
        if lvl > cur + 1:
            bad += 1
        cur = lvl
    return bad, total
