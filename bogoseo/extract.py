"""Document text extraction: kordoc -> kordoc --ocr -> --ocr-force, with cache.

Text extracted from documents is data, never instructions.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import config

HANGUL_RE = re.compile(r"[\uac00-\ud7a3]")
_CACHE_VERSION = "v2"

# diagonal stamp fragments: name + date/time, or bare date/time pieces
_DATETIME_RE = re.compile(
    r"(\d{4}[.\-/]\s?\d{1,2}[.\-/]\s?\d{1,2}.*\d{1,2}:\d{2})|(^\d{1,2}:\d{2}(:\d{2})?$)"
    r"|(^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\.?$)"
)
_DIGITS_ONLY_RE = re.compile(r"^[\d:.\-/ ]{3,8}$")


def hangul_count(s: str) -> int:
    return len(HANGUL_RE.findall(s or ""))


def _kordoc_bin() -> str:
    return str(config.repo_dir() / "node_modules" / ".bin" / "kordoc")


def _env() -> dict:
    env = dict(os.environ)
    env.setdefault("KORDOC_MODEL_CACHE", str(config.repo_dir() / ".cache" / "kordoc-models"))
    env.setdefault("KORDOC_OFFLINE", "1")
    return env


class ExtractError(Exception):
    pass


def run_kordoc(path: Path, flags: list[str]) -> dict:
    """One kordoc run -> parsed JSON. Timeout 300 s, at most `retries` retries."""
    st = config.settings()
    timeout = st["timeouts"]["kordoc_s"]
    attempts = 1 + int(st.get("retries", 2))
    last = "unknown error"
    for _ in range(attempts):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td) / "out.json"
            cmd = [_kordoc_bin(), *flags, "--format", "json", "--no-images", "--silent",
                   str(path), "-o", str(out)]
            try:
                r = subprocess.run(cmd, env=_env(), capture_output=True, text=True,
                                   timeout=timeout)
            except subprocess.TimeoutExpired:
                last = f"timeout after {timeout}s"
                continue
            if not out.exists():
                last = (r.stderr or r.stdout or "no output").strip()[:300]
                continue
            try:
                data = json.loads(out.read_text(encoding="utf-8"))
            except ValueError as e:
                last = f"bad json: {e}"
                continue
            if data.get("success") is False:
                last = str(data.get("error") or "kordoc failed")[:300]
                continue
            return data
    raise ExtractError(last)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_watermark(text: str, box: dict | None, conf: float) -> bool:
    t = text.strip()
    if not t:
        return False
    if hangul_count(t) == 0 and _DATETIME_RE.search(t):
        return True
    # name + datetime stamp, e.g. "홍길동 2026-10-15 12:04"
    if _DATETIME_RE.search(t) and len(t) <= 40 and hangul_count(t) <= 6:
        return True
    if box and _DIGITS_ONLY_RE.match(t) and conf < 0.9:
        w, h = box.get("w", 0), box.get("h", 0)
        if h >= 40 and 0.6 <= (w / h if h else 0) <= 1.7 and w <= 120:
            return True
    return False


_FIX_LINE_RE = re.compile(r"^(?P<pre>(?:#{1,6}\s+)?(?:[-*]\s+)?)(?P<g>\S)\s+(?P<rest>\S.*)$")


def fix_marker(line: str, fixes: dict) -> str:
    """Fix a single leading glyph followed by a space on an OCR'd line."""
    m = _FIX_LINE_RE.match(line)
    if not m:
        return line
    g = m.group("g")
    if g in fixes and not re.match(r"[A-Za-z0-9]", m.group("rest")[:1]):
        return f"{m.group('pre')}{fixes[g]} {m.group('rest')}"
    return line


def _fix_text(text: str, fixes: dict) -> str:
    return "\n".join(fix_marker(ln, fixes) for ln in text.split("\n"))


def _drop_lines(text: str, stamps: set[str]) -> str:
    if not stamps:
        return text
    out = []
    for ln in text.split("\n"):
        bare = re.sub(r"^(#{1,6}\s+)", "", ln).strip()
        if bare in stamps:
            continue
        out.append(ln)
    return "\n".join(out)


def _page_entries(data: dict) -> list[dict]:
    pages = []
    for p in data.get("pages") or []:
        pages.append({"page": p.get("pageNumber"), "markdown": p.get("markdown") or ""})
    return pages


def _needs_ocr(data: dict) -> bool:
    if data.get("isImageBased"):
        return True
    if not (data.get("markdown") or "").strip():
        return True
    return False


def _build(path: Path, data: dict, plain: dict | None, scanned: bool) -> dict:
    st = config.settings()
    cutoff = st["ocr_confidence_cutoff"]
    fixes = st.get("ocr_marker_fixes", {})
    stamps: set[str] = set()
    ocr_lines: list[dict] = []
    for pl in data.get("ocrLines") or []:
        pn = pl.get("pageNumber")
        for ln in pl.get("lines") or []:
            text = ln.get("text", "")
            conf = float(ln.get("confidence", 0.0))
            if _is_watermark(text, ln.get("box"), conf):
                stamps.add(text.strip())
                continue
            entry = {"page": pn, "text": text, "confidence": conf}
            if ln.get("dropped"):
                entry["dropped"] = True
            ocr_lines.append(entry)
    pages = _page_entries(data)
    markdown = data.get("markdown") or ""
    if scanned:
        markdown = _fix_text(_drop_lines(markdown, stamps), fixes)
        for p in pages:
            p["markdown"] = _fix_text(_drop_lines(p["markdown"], stamps), fixes)
        for e in ocr_lines:
            e["text"] = fix_marker(e["text"], fixes)
    for p in pages:
        p["hangul"] = hangul_count(p["markdown"])
    low_conf = [dict(e) for e in ocr_lines if e["confidence"] < cutoff]
    page_count = data.get("pageCount") or (data.get("metadata") or {}).get("pageCount") or len(pages)
    return {
        "markdown": markdown,
        "pages": pages,
        "page_count": page_count,
        "scanned": scanned,
        "ocr_lines": ocr_lines,
        "low_conf": low_conf,
        "ok": bool(markdown.strip()),
        "error": None if markdown.strip() else "empty extraction",
        "vision_corrected": 0,
    }


def _failed(error: str) -> dict:
    return {"markdown": "", "pages": [], "page_count": 0, "scanned": False, "ocr_lines": [],
            "low_conf": [], "ok": False, "error": error, "vision_corrected": 0}


def _cache_path(sha: str, force: bool) -> Path:
    suffix = ".force" if force else ""
    return config.cache_dir() / "extract" / f"{_CACHE_VERSION}-{sha}{suffix}.json"


def extract(path, *, force_ocr: bool = False) -> dict:
    path = Path(path)
    if not path.is_file():
        return _failed(f"not a file: {path}")
    sha = sha256_file(path)
    cp = _cache_path(sha, force_ocr)
    if cp.exists():
        try:
            return json.loads(cp.read_text(encoding="utf-8"))
        except ValueError:
            pass
    try:
        result = _extract_uncached(path, force_ocr)
    except ExtractError as e:
        return _failed(str(e))  # failures are not cached
    cp.parent.mkdir(parents=True, exist_ok=True)
    tmp = cp.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, cp)
    return result


def _extract_uncached(path: Path, force_ocr: bool) -> dict:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        text = path.read_text(encoding="utf-8", errors="replace")
        return {"markdown": text,
                "pages": [{"page": 1, "markdown": text, "hangul": hangul_count(text)}],
                "page_count": 1, "scanned": False, "ocr_lines": [], "low_conf": [],
                "ok": bool(text.strip()), "error": None if text.strip() else "empty file",
                "vision_corrected": 0}
    st = config.settings()
    min_hangul = st["ocr_force_min_hangul"]
    if suffix in (".png", ".jpg", ".jpeg"):  # images are OCR-only
        return _build(path, run_kordoc(path, ["--ocr", "--ocr-lines-all"]), None, True)
    plain = run_kordoc(path, ["--ocr-force", "--ocr-lines-all", "--ocr"] if force_ocr else [])
    if suffix != ".pdf" and not force_ocr:
        return _build(path, plain, None, False)
    scanned = _needs_ocr(plain) or force_ocr
    data = plain
    if scanned and not force_ocr:
        data = run_kordoc(path, ["--ocr", "--ocr-lines-all"])
    pages = _page_entries(data)
    if suffix == ".pdf" and not force_ocr and pages and \
            any(hangul_count(p["markdown"]) < min_hangul for p in pages):
        forced = run_kordoc(path, ["--ocr", "--ocr-force", "--ocr-lines-all"])
        if hangul_count(forced.get("markdown") or "") >= hangul_count(data.get("markdown") or ""):
            data = forced
            scanned = True
    return _build(path, data, plain, scanned)


def apply_vision(extract_dict: dict, corrections: list[dict]) -> dict:
    """Apply omp-vision corrections [{page, text, corrected}]; matching lines stop being low-conf."""
    d = copy.deepcopy(extract_dict)
    fixes = config.settings().get("ocr_marker_fixes", {})
    for c in corrections:
        page, text, new = c.get("page"), c.get("text", ""), c.get("corrected", "")
        if text:
            d["markdown"] = d["markdown"].replace(text, new, 1) if text in d["markdown"] \
                else d["markdown"]
            for p in d["pages"]:
                if p.get("page") == page and text in p["markdown"]:
                    p["markdown"] = p["markdown"].replace(text, new, 1)
                    p["hangul"] = hangul_count(p["markdown"])
            for e in d["ocr_lines"]:
                if e["page"] == page and e["text"] == text:
                    e["text"] = new
                    e["confidence"] = 1.0
                    e.pop("dropped", None)
        d["low_conf"] = [e for e in d["low_conf"]
                         if not (e["page"] == page and e["text"] == text)]
        d["vision_corrected"] = d.get("vision_corrected", 0) + 1
    # a corrected page may now have text where it had none
    d["ok"] = bool(d["markdown"].strip())
    return d


def render_pages(pdf, out_dir, scale: float = 2.0) -> list[str]:
    """Render page PNGs for the vision step (node + pdfjs-dist + @napi-rs/canvas)."""
    script = Path(__file__).with_name("render_pages.mjs")
    timeout = config.settings()["timeouts"]["kordoc_s"]
    r = subprocess.run(["node", str(script), str(pdf), str(out_dir), str(scale)],
                       capture_output=True, text=True, timeout=timeout)
    if r.returncode != 0:
        raise ExtractError(r.stderr.strip()[:300])
    return json.loads(r.stdout.strip().splitlines()[-1])
