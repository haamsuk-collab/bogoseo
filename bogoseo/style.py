"""Sentence-ending style judgment with kiwipiepy (rule iv)."""
from __future__ import annotations

import re
import threading

_kiwi = None
_lock = threading.Lock()

NOUN_TAGS = {"NNG", "NNP", "NNB"}
# nouns plus root / number / unit / noun-forming suffix / latin / hanja / symbol-unit
GAEJO_OK_TAGS = NOUN_TAGS | {"XR", "NR", "SN", "SL", "SH", "SW", "XSN"}
_PUNCT_TAGS = {"SF", "SP", "SS", "SSO", "SSC", "SE", "SO"}
_JONGSEONG_M = {"ᆷ", "ㅁ", "음", "슴"}

_TRAILING_MARK_RE = re.compile(
    r"\s*(?:\[\s*참고\s*\d+\s*\]|【\s*참고\s*\d+\s*[】\]\)]|\[\s*확인\s*필요\s*\d+\s*:[^\]]*\])\s*$"
)
_LABEL_RE = re.compile(r"^(?P<label>[^:：]{1,25}?)\s*[:：]\s*(?P<val>\S.*)$")


def kiwi():
    global _kiwi
    if _kiwi is None:
        with _lock:
            if _kiwi is None:
                from kiwipiepy import Kiwi
                _kiwi = Kiwi()
    return _kiwi


def strip_trailing_marks(s: str) -> str:
    s = s.rstrip()
    while True:
        t = _TRAILING_MARK_RE.sub("", s)
        if t == s:
            return s.rstrip()
        s = t.rstrip()


def _tokens(s: str):
    return kiwi().tokenize(s)


def _content_tokens(s: str):
    """Tokens without trailing punctuation / closing brackets."""
    toks = _tokens(s)
    while toks and toks[-1].tag in _PUNCT_TAGS:
        toks = toks[:-1]
    return toks


def nouns(text: str) -> set:
    return {t.form for t in _tokens(text) if t.tag in ("NNG", "NNP")}


def _clean(line_body: str) -> str:
    return strip_trailing_marks(line_body).strip()


def _ends_with_noun(s: str) -> bool:
    toks = _content_tokens(s)
    if not toks:
        return False
    if toks[-1].tag in GAEJO_OK_TAGS:
        return True
    if toks[-1].tag.startswith("E"):  # verb/adjective ending (잡다 also has an XR reading)
        return False
    return _last_word_is_root(s)


def _last_word_is_root(s: str) -> bool:
    """Kiwi splits a bare Sino-Korean root after a noun ("난이도 상이" -> 상/XSN + 이/JKS).
    Accept the line when the last word alone has a whole-word root (XR) reading.
    Only XR: NNG/NNP guesses would also accept noun+particle words such as "수준이"."""
    words = s.split()
    if not words:
        return False
    word = words[-1].rstrip(".,;:!?)]】」』'\"")
    if not word:
        return False
    return any(
        len(toks) == 1 and toks[0].form == word and toks[0].tag == "XR"
        for toks, _ in kiwi().analyze(word, top_n=5)
    )


def _split_period(s: str) -> tuple[str, bool]:
    t = s.rstrip()
    if t.endswith("."):
        return t[:-1].rstrip(), True
    return t, False


def _is_b3(core: str) -> bool:
    return core.endswith("니다")


def _is_b2(core: str) -> bool:
    if _is_b3(core):
        return False
    toks = _content_tokens(core)
    return bool(toks) and toks[-1].tag == "EF" and toks[-1].form.endswith("다")


def _is_b1(core: str) -> bool:
    toks = _content_tokens(core)
    if not toks:
        return False
    last = toks[-1]
    if last.tag == "ETN":
        return True
    if last.form in _JONGSEONG_M:
        return True
    word = core.split()[-1] if core.split() else core
    return word.endswith(("함", "임", "됨", "음"))


def ending_class(line_body: str) -> str | None:
    s = _clean(line_body)
    if not s:
        return None
    if s.endswith(("?", "!")):
        return None
    core, has_period = _split_period(s)
    if not has_period:
        return "gaejo" if _ends_with_noun(core) else None
    if _is_b3(core):
        return "b3"
    if _is_b2(core):
        return "b2"
    if _is_b1(core):
        return "b1"
    return None


def judge_ending(line_body: str, style: dict) -> tuple[bool, str]:
    """Judge one line body against {kind: 개조식|서술식, ending: b1|b2|b3|None}."""
    s = _clean(line_body)
    kind = (style or {}).get("kind")
    ending = (style or {}).get("ending")
    if not s:
        return True, "empty"
    if kind == "개조식":
        if s.endswith((".", "?", "!")):
            return False, "개조식 line ends with punctuation"
        if not _ends_with_noun(s):
            return False, "개조식 line does not end in a noun"
        return True, "ok"
    if kind == "서술식":
        if ending not in ("b1", "b2", "b3"):
            return False, "서술식 style has no ending chosen"
        core, has_period = _split_period(s)
        if len(s) <= 20 and _ends_with_noun(core):
            return True, "short noun-ending exception"
        if not has_period:
            return False, "서술식 line does not end with a period"
        if s.endswith(("?", "!")):
            return False, "bad terminal punctuation"
        checks = {"b1": _is_b1, "b2": _is_b2, "b3": _is_b3}
        if checks[ending](core):
            return True, "ok"
        actual = ending_class(s)
        return False, f"ending {actual or 'unknown'} does not match chosen {ending}"
    return False, f"unknown style kind {kind!r}"


def in_scope(parsed_line: dict) -> bool:
    """Rule (iv) scope: level-2/3 lines under top-level headings and ☞ conclusion lines."""
    kind = parsed_line.get("kind")
    section = parsed_line.get("section", "body")
    if section == "checklist":
        return False
    body = _clean(parsed_line.get("body", ""))
    if not body:
        return False
    if kind == "conclusion":
        return True
    if kind != "item" or parsed_line.get("level", 0) < 2:
        return False
    if section == "attachment":
        return False
    return not is_label_value(body)


def is_label_value(body: str) -> bool:
    """`항목 : 값/일정` lines are exempt."""
    m = _LABEL_RE.match(body)
    if not m:
        return False
    label = m.group("label")
    if label.endswith(".") or re.search(r"\d$", label) and re.match(r"^\d", m.group("val")):
        return False
    return True
