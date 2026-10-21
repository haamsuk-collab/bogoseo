import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import FIXTURES, REPO

MODELS = REPO / ".cache" / "kordoc-models"
KORDOC = REPO / "node_modules" / ".bin" / "kordoc"
PY = REPO / ".venv" / "bin" / "python"


def _unshare_works() -> bool:
    if not shutil.which("unshare"):
        return False
    return subprocess.run(["unshare", "-rn", "true"], capture_output=True).returncode == 0


def _offline(cmd: list[str]) -> list[str]:
    """Run cmd without network: a new network namespace, or (fallback) dead proxies."""
    return ["unshare", "-rn", *cmd] if _unshare_works() else cmd


def _env() -> dict:
    env = dict(os.environ)
    env.update(KORDOC_MODEL_CACHE=str(MODELS), KORDOC_OFFLINE="1")
    if not _unshare_works():
        for k in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY"):
            env[k] = "http://127.0.0.1:9"
    return env


def _snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): p.stat().st_size for p in sorted(d.rglob("*")) if p.is_file()}


def test_skill_symlink_and_skill_md():
    link = Path.home() / ".agents" / "skills" / "bogoseo"
    assert link.exists() and link.resolve() == REPO.resolve()
    assert (link / "SKILL.md").is_file()


def test_pins():
    pkg = json.loads((REPO / "package.json").read_text())
    assert re.match(r"^\d", pkg["dependencies"]["kordoc"])          # exact pin, no ^ or ~
    req = (REPO / "requirements.txt").read_text()
    assert re.search(r"^kiwipiepy==\d", req, re.M)
    out = subprocess.run([str(PY), "-c", "import importlib.metadata as m;print(m.version('kiwipiepy'))"],
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == re.search(r"^kiwipiepy==(\S+)", req, re.M).group(1)
    assert (MODELS / "ppocr" / "det.onnx").is_file() and (MODELS / "ppocr" / "rec_korean.onnx").is_file()


def test_network_really_disabled():
    if not _unshare_works():
        pytest.skip("unshare -rn unavailable; proxy fallback is used by the other tests")
    code = "import socket;s=socket.socket();s.settimeout(3);s.connect(('1.1.1.1',443))"
    r = subprocess.run(_offline([str(PY), "-c", code]), capture_output=True, text=True)
    assert r.returncode != 0


def test_kordoc_ocr_fixture_page_offline(tmp_path):
    before = _snapshot(MODELS)
    out = tmp_path / "o.json"
    cmd = _offline([str(KORDOC), "--ocr", "--ocr-lines", "--format", "json", "--no-images",
                    "--silent", str(FIXTURES / "ocr_page.png"), "-o", str(out)])
    r = subprocess.run(cmd, env=_env(), capture_output=True, text=True, timeout=300)
    assert r.returncode == 0 and out.exists(), r.stderr
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "출제방식" in data["markdown"] and "문제은행" in data["markdown"]
    assert data["ocrLines"][0]["lines"] and data["ocrLines"][0]["lines"][0]["confidence"] > 0.8
    assert _snapshot(MODELS) == before                 # nothing was downloaded


def test_kiwipiepy_tags_offline():
    code = ("from kiwipiepy import Kiwi;"
            "t=Kiwi().tokenize('비합숙 출제방식 도입이 필요하다.');"
            "print([(x.form,x.tag) for x in t])")
    r = subprocess.run(_offline([str(PY), "-c", code]), env=_env(), capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert "('필요', 'NNG')" in r.stdout and "EF" in r.stdout


def test_extract_wrapper_runs_offline_cached(tmp_path, monkeypatch):
    """bogoseo.extract on an image uses the same offline chain and flags the lines."""
    monkeypatch.setenv("BOGOSEO_CACHE", str(tmp_path / "cache"))
    from bogoseo import extract
    res = extract.extract(FIXTURES / "ocr_page.png")
    assert res["ok"] and res["scanned"] and "출제방식" in res["markdown"]
    assert res["ocr_lines"] and not res["low_conf"]
    assert (tmp_path / "cache" / "extract").is_dir()
