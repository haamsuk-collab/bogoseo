import subprocess

import pytest

from bogoseo import extract


def fake_kordoc(calls, plain, ocr, force=None):
    def run(path, flags):
        calls.append(flags)
        if "--ocr-force" in flags:
            return force
        return ocr if "--ocr" in flags else plain
    return run


def page(n, md):
    return {"pageNumber": n, "markdown": md}


def lines(*items):
    return [{"pageNumber": 1, "lines": [
        {"text": t, "box": b, "confidence": c} for t, b, c in items]}]


BOX = {"x": 0, "y": 0, "w": 500, "h": 40}


@pytest.fixture
def pdf(tmp_path, monkeypatch):
    monkeypatch.setenv("BOGOSEO_CACHE", str(tmp_path / "cache"))
    p = tmp_path / "a.pdf"
    p.write_bytes(b"%PDF-1.4 x")
    return p


def test_marker_fixes_and_watermark_and_low_conf(pdf, monkeypatch):
    hangul = "가나다라마바사아자차" * 6
    md = f"제목\n\nG {hangul}\n\no {hangul}\n\n홍길동 2026-10-15 12:04\n\n#### 口 {hangul}\n\nGood morning"
    ocr = {"markdown": md, "pages": [page(1, md)], "pageCount": 1, "isImageBased": True,
           "ocrLines": lines(("G " + hangul, BOX, 0.95), ("저신뢰 줄", BOX, 0.5),
                             ("홍길동 2026-10-15 12:04", BOX, 0.9),
                             ("1204", {"x": 0, "y": 0, "w": 60, "h": 60}, 0.73))}
    calls = []
    monkeypatch.setattr(extract, "run_kordoc", fake_kordoc(calls, {"markdown": "", "isImageBased": True,
                        "pages": [], "pageCount": 1}, ocr))
    r = extract.extract(pdf)
    assert r["scanned"] and r["ok"] and ["--ocr", "--ocr-lines-all"] in calls
    assert "홍길동 2026-10-15 12:04" not in r["markdown"]           # watermark stripped
    assert f"☞ {hangul}" in r["markdown"] and f"ㅇ {hangul}" in r["markdown"]
    assert f"#### □ {hangul}" in r["markdown"]                       # 口 -> □, heading prefix kept
    assert "Good morning" in r["markdown"]                           # only single-glyph prefixes
    texts = [e["text"] for e in r["ocr_lines"]]
    assert "홍길동 2026-10-15 12:04" not in texts and "1204" not in texts
    assert [e["text"] for e in r["low_conf"]] == ["저신뢰 줄"]
    # cached: no new kordoc runs
    n = len(calls)
    assert extract.extract(pdf)["ok"] and len(calls) == n


def test_force_ocr_when_page_has_few_hangul(pdf, monkeypatch):
    few = {"markdown": "짧음", "pages": [page(1, "짧음")], "pageCount": 1, "isImageBased": False}
    forced_md = "가나다라마" * 20
    forced = {"markdown": forced_md, "pages": [page(1, forced_md)], "pageCount": 1,
              "isImageBased": False, "ocrLines": lines((forced_md, BOX, 0.99))}
    calls = []
    monkeypatch.setattr(extract, "run_kordoc", fake_kordoc(calls, few, few, forced))
    r = extract.extract(pdf)
    assert any("--ocr-force" in c for c in calls)
    assert r["markdown"] == forced_md and r["pages"][0]["hangul"] == 100


def test_timeout_is_reported_not_hung(pdf, monkeypatch):
    attempts = []

    def fake_run(*a, **k):
        attempts.append(1)
        raise subprocess.TimeoutExpired(cmd="kordoc", timeout=1)
    monkeypatch.setattr(subprocess, "run", fake_run)
    r = extract.extract(pdf)
    assert r["ok"] is False and "timeout" in r["error"]
    assert len(attempts) == 3                      # first try + 2 retries


def test_apply_vision_clears_low_conf():
    d = {"markdown": "가 저신뢰 줄 나", "pages": [{"page": 1, "markdown": "가 저신뢰 줄 나", "hangul": 6}],
         "page_count": 1, "scanned": True, "ok": True, "error": None,
         "ocr_lines": [{"page": 1, "text": "저신뢰 줄", "confidence": 0.5}],
         "low_conf": [{"page": 1, "text": "저신뢰 줄", "confidence": 0.5}]}
    out = extract.apply_vision(d, [{"page": 1, "text": "저신뢰 줄", "corrected": "고신뢰 줄"}])
    assert out["low_conf"] == [] and "고신뢰 줄" in out["markdown"]
    assert d["low_conf"]                          # input not mutated
    assert out["ocr_lines"][0]["confidence"] == 1.0
