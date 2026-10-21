import re
from pathlib import Path

import pytest

from bogoseo.collect import fetch
from bogoseo.collect.sources import REGISTRY, adapters, get_adapter
from collect_fx import SOURCES, FixtureRunner, fake_time


def _fetcher(name, runner, interval=3):
    clock, sleep, sleeps, _ = fake_time()
    fx = fetch.Fetcher(name, settings={"request_interval_s": interval, "source_intervals": {},
                                       "timeouts": {"insane_search_s": 60}, "retries": 0},
                       runner=runner, clock=clock, sleep=sleep)
    return fx, sleeps


def test_fifteen_sources_registered():
    names = {a.name for a in adapters()}
    assert names == set(SOURCES)
    assert sum(a.kind == "api" for a in adapters()) == 9
    assert sum(a.kind == "web" for a in adapters()) == 6


@pytest.mark.parametrize("name", sorted(SOURCES))
def test_adapter_lists_items_from_fixture(name, monkeypatch):
    from bogoseo import config
    base = config.settings()
    monkeypatch.setattr(config, "settings", lambda: {**base, "nars_service": "NARSRPT"})
    runner = FixtureRunner(SOURCES[name])
    fx, _ = _fetcher(name, runner, interval=0)
    ad = get_adapter(name)
    key = "TESTKEY" if ad.kind == "api" else None
    items = ad.list_items(fx, limit=3, cursor=None, key=key)
    assert items, name
    assert 1 <= len(items) <= 3
    for it in items:
        assert it.title and it.source_url
        assert it.download_url or it.text
    for u in runner.urls:            # every request went through the injected (insane-search) runner
        assert u.startswith("http")


def test_requests_spaced_by_interval():
    runner = FixtureRunner("opm")
    fx, sleeps = _fetcher("국무조정실", runner, interval=3)
    get_adapter("국무조정실").list_items(fx, limit=2, cursor=None, key=None)
    assert len(fx.starts) >= 3
    gaps = [b - a for a, b in zip(fx.starts, fx.starts[1:])]
    assert all(g >= 3 for g in gaps)


def test_every_request_logged_via_insane_search():
    runner = FixtureRunner("moef")
    fx, _ = _fetcher("재정경제부_보도자료", runner, interval=0)
    get_adapter("재정경제부_보도자료").list_items(fx, limit=2, cursor=None, key="SECRETKEYVALUE")
    assert len(fx.log) == len(runner.urls) > 0
    assert all(row[2] == 1 for row in fx.log)
    assert not any("SECRETKEYVALUE" in row[1] for row in fx.log)


def test_no_bypass_of_insane_search():
    pkg = Path(fetch.__file__).parent
    for p in pkg.glob("*.py"):
        if p.name == "_bridge.py":
            continue
        src = p.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(requests|urllib\.request|http\.client|httpx|curl_cffi|socket)\b", src, re.M), p.name


def test_run_source_ingests_and_second_run_adds_zero(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    conn = warehouse.connect()
    ad = get_adapter("정책브리핑")

    def factory(name, conn=None, settings=None):
        clock, sleep, _, _ = fake_time()
        return fetch.Fetcher(name, conn=conn, settings={"request_interval_s": 3, "source_intervals": {},
                             "timeouts": {"insane_search_s": 60}, "retries": 0},
                             runner=FixtureRunner("policy"), clock=clock, sleep=sleep)
    r1 = runner.run_source(ad, conn, keys={"data_go_kr": "K"}, limit=3, fetcher_factory=factory, disk_ok=lambda: True)
    assert r1.added >= 1 and r1.result == "성공" and r1.status == "통과"
    assert warehouse.get_cursor(conn, ad.name)
    r2 = runner.run_source(ad, conn, keys={"data_go_kr": "K"}, limit=3, fetcher_factory=factory, disk_ok=lambda: True)
    assert r2.added == 0 and r2.result == "0건"
    n = conn.execute("SELECT COUNT(*) FROM web_access_log WHERE via_insane_search=1").fetchone()[0]
    assert n == conn.execute("SELECT COUNT(*) FROM web_access_log").fetchone()[0] > 0


def test_keyless_api_is_skipped(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    conn = warehouse.connect()
    r = runner.run_source(get_adapter("국회도서관"), conn, keys={})
    assert (r.status, r.result) == ("미검증", "건너뜀")
    assert conn.execute("SELECT result FROM collection_runs").fetchone()[0] == "건너뜀"


def test_disk_full_stops_collection(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    conn = warehouse.connect()
    r = runner.run_source(get_adapter("재정경제부_보도자료"), conn, keys={"data_go_kr": "K"}, limit=2,
                          fetcher_factory=lambda n, conn=None, settings=None: fetch.Fetcher(
                              n, settings={"request_interval_s": 0, "retries": 0, "timeouts": {"insane_search_s": 5}},
                              runner=FixtureRunner("moef"), sleep=lambda s: None),
                          disk_ok=lambda: False)
    assert r.added == 0 and "디스크" in r.detail


def test_second_collect_exits_zero_when_lock_held(tmp_root, capsys):
    from types import SimpleNamespace
    from bogoseo import cli_collect, config
    from bogoseo.collect import runner
    from bogoseo.collect.lock import run_lock
    calls = []
    orig = runner.run_all
    runner.run_all = lambda *a, **k: calls.append(1) or []
    try:
        lock_path = config.refs_dir() / ".collect.lock"
        with run_lock(lock_path):
            rc = cli_collect.cmd_collect(SimpleNamespace(source=None, limit=None))
        assert rc == 0 and calls == []
        assert f"수집이 이미 실행 중입니다(잠금: {lock_path})" in capsys.readouterr().err
        assert cli_collect.cmd_collect(SimpleNamespace(source=None, limit=None)) == 0
        assert calls == [1]
    finally:
        runner.run_all = orig


# ---- regression tests for collector hardening -------------------------------------------------

def _b64(body: bytes) -> str:
    import base64
    return base64.b64encode(body).decode()


def _stub_adapter(items):
    from bogoseo.collect.sources import Adapter

    class Stub(Adapter):
        name = "스텁"
        kind = "web"
        institution = "스텁기관"

        def list_items(self, fx, *, limit, cursor, key):
            return list(items)
    return Stub()


def _stub_factory(responses):
    def runner(url, timeout):
        body = responses.get(url)
        if body is None:
            return {"ok": False, "status": 500, "last_code": 500, "trace": [{"status": 500}], "body_b64": ""}
        if isinstance(body, dict):
            return body
        return {"ok": True, "status": 200, "last_code": 200, "content_type": "text/plain", "final_url": url,
                "trace": [{"executor": "x", "impersonate": "-", "status": 200, "verdict": "ok"}],
                "body_b64": _b64(body)}

    def factory(name, conn=None, settings=None):
        return fetch.Fetcher(name, conn=conn, settings={"request_interval_s": 0, "retries": 0,
                             "timeouts": {"insane_search_s": 5}, "max_download_mb": 1},
                             runner=runner, sleep=lambda s: None)
    return factory


def test_cursor_does_not_pass_failed_item(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [
        Item("1", "첫번째 문서", "u://1", "2026-01-01", download_url="d://1", filename="a.txt"),
        Item("2", "둘째 문서 실패", "u://2", "2026-01-02", download_url="d://2", filename="b.txt"),
        Item("3", "셋째 문서", "u://3", "2026-01-03", download_url="d://3", filename="c.txt"),
    ]
    fac = _stub_factory({"d://1": b"alpha document body text one " * 5, "d://3": b"gamma document body text three " * 5})
    r = runner.run_source(_stub_adapter(items), conn, keys={}, limit=5, fetcher_factory=fac, disk_ok=lambda: True)
    assert warehouse.get_cursor(conn, "스텁") == "2026-01-01"
    assert r.cursor == "2026-01-01"


def test_failed_attachment_not_ingested_from_list_text(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "첨부 실패 문서", "u://1", "2026-02-01", download_url="d://1", filename="a.pdf",
                  text="list text fallback body " * 10)]
    r = runner.run_source(_stub_adapter(items), conn, keys={}, limit=5, fetcher_factory=_stub_factory({}),
                          disk_ok=lambda: True)
    assert r.added == 0 and r.counts["failed"] == 1
    assert conn.execute("SELECT COUNT(*) FROM files WHERE source_url='u://1'").fetchone()[0] == 0
    assert warehouse.get_cursor(conn, "스텁") is None


def test_too_large_download_is_skip_not_crash(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect import runner
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "큰 파일", "u://1", "2026-03-01", download_url="d://1", filename="a.pdf")]
    big = b"x" * (1024 * 1024 + 1)
    r = runner.run_source(_stub_adapter(items), conn, keys={}, limit=5,
                          fetcher_factory=_stub_factory({"d://1": big}), disk_ok=lambda: True)
    assert r.counts["too_large"] == 1 and r.counts["skipped"] == 1 and r.counts["failed"] == 0
    assert r.added == 0


def test_credential_url_over_http_refused():
    called = []
    fx, _ = _fetcher("x", lambda u, t: called.append(u) or {"ok": True, "trace": [], "body_b64": ""})
    resp = fx.get("http://example.org/a?ServiceKey=SECRETKEYVALUE&x=1")
    assert resp.error == "refused_insecure_credential_url" and not called
    fx.get("https://example.org/a?ServiceKey=SECRETKEYVALUE")
    assert len(called) == 1


def test_credentialed_endpoints_use_https():
    assert get_adapter("국회도서관")._url("K", 1).startswith("https://")
    ad = get_adapter("법제처")
    assert ad._list_url("K", 1).startswith("https://") and ad._body_url("K", "1").startswith("https://")


def test_bridge_url_passed_on_stdin_not_argv(monkeypatch):
    import subprocess
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["input"] = cmd, kw.get("input")

        class P:
            stdout = ""
            stderr = "boom https://h/x?ServiceKey=SECRETKEYVALUE failed"
        return P()
    monkeypatch.setattr(subprocess, "run", fake_run)
    out = fetch.insane_run("https://h/x?ServiceKey=SECRETKEYVALUE", 5)
    assert "SECRETKEYVALUE" not in " ".join(seen["cmd"]) and "-" in seen["cmd"]
    assert "SECRETKEYVALUE" in seen["input"]
    assert "SECRETKEYVALUE" not in out["error"]


def _skip_run(conn, items, responses, n=1):
    from bogoseo.collect import runner
    out = None
    for _ in range(n):
        out = runner.run_source(_stub_adapter(items), conn, keys={}, limit=5,
                                fetcher_factory=_stub_factory(responses), disk_ok=lambda: True)
    return out


def test_repeated_404_becomes_persisted_permanent_skip(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "없는 본문", "u://1", "2026-03-01", download_url="d://1", filename="a.pdf")]
    gone = {"d://1": {"ok": False, "status": 404, "last_code": 404, "trace": [{"status": 404}], "body_b64": ""}}
    for _ in range(2):      # first runs below permanent_skip_after stay failures
        r = _skip_run(conn, items, gone)
        assert r.counts["failed"] == 1 and "skipped_permanent" not in r.counts
    r = _skip_run(conn, items, gone)
    assert r.counts["skipped_permanent"] == 1 and r.counts["failed"] == 0 and "skipped_permanent=1" in r.detail
    row = conn.execute("SELECT reason, permanent FROM item_skips WHERE source_url='u://1'").fetchone()
    assert (row["reason"], row["permanent"]) == ("http_404", 1)
    # persisted: the next run does not even fetch the attachment
    calls = []
    from bogoseo.collect import runner
    fac = _stub_factory(gone)
    r = runner.run_source(_stub_adapter(items), conn, keys={}, limit=5,
                          fetcher_factory=lambda n, conn=None, settings=None: _spy(fac, calls)(n, conn, settings),
                          disk_ok=lambda: True)
    assert r.counts["skipped_permanent"] == 1 and not calls


def _spy(fac, calls):
    def f(name, conn=None, settings=None):
        fx = fac(name, conn=conn, settings=settings)
        orig = fx.runner
        fx.runner = lambda u, t: calls.append(u) or orig(u, t)
        return fx
    return f


def test_too_large_is_persisted_permanent_skip(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "큰 파일", "u://1", "2026-03-01", download_url="d://1", filename="a.pdf")]
    r = _skip_run(conn, items, {"d://1": b"x" * (1024 * 1024 + 1)})
    assert r.counts["skipped_permanent"] == 1
    assert conn.execute("SELECT reason FROM item_skips WHERE permanent=1").fetchone()["reason"] == "too_large"


def test_download_uses_long_timeout_and_single_attempt(monkeypatch):
    from bogoseo.collect import fetch
    seen = []
    monkeypatch.setattr(fetch.subprocess, "run", lambda cmd, **kw: seen.append((cmd, kw["timeout"])) or (_ for _ in ()).throw(
        fetch.subprocess.TimeoutExpired(cmd, kw["timeout"])))
    fx = fetch.Fetcher("x", settings={"request_interval_s": 0, "retries": 2, "timeouts": {"insane_search_s": 60, "download_s": 300}},
                       sleep=lambda s: None)
    resp = fx.get("https://example.org/big.hwpx", download=True)
    assert resp.error == "timeout" and len(seen) == 2              # retries capped at 1
    assert seen[0][1] == 300 and seen[0][0][-2:] == ["290", "1"]    # long single attempt
    seen.clear()
    fx.get("https://example.org/page")
    assert seen[0][1] == 60 and seen[0][0][-2:] == [str(fetch.ATTEMPT_TIMEOUT_S), str(fetch.MAX_ATTEMPTS)]


def test_engine_trace_timeout_normalized_to_response_error():
    d = {"ok": False, "status": None, "trace": [{"executor": "x", "impersonate": "-", "status": None,
                                                 "verdict": None, "error": "ReadTimeout: timed out"}],
         "body_b64": ""}
    assert fetch._response_from_bridge("u://x", d).error == "timeout"


def test_trace_timeouts_become_permanent_skip(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "느린 본문", "u://1", "2026-03-01", download_url="d://1", filename="a.pdf")]
    slow = {"d://1": {"ok": False, "status": None, "trace": [{"error": "Timeout: timed out"}], "body_b64": ""}}
    _skip_run(conn, items, slow, n=3)
    row = conn.execute("SELECT reason, permanent FROM item_skips WHERE source_url='u://1'").fetchone()
    assert (row["reason"], row["permanent"]) == ("timeout", 1)


def test_success_clears_item_skips_row(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    items = [Item("1", "복구 본문", "u://1", "2026-03-01", download_url="d://1", filename="a.txt")]
    gone = {"d://1": {"ok": False, "status": 404, "last_code": 404, "trace": [{"status": 404}], "body_b64": ""}}
    _skip_run(conn, items, gone)
    assert conn.execute("SELECT attempts FROM item_skips WHERE source_url='u://1'").fetchone()[0] == 1
    _skip_run(conn, items, {"d://1": "복구된 본문 내용입니다".encode()})
    assert conn.execute("SELECT COUNT(*) FROM item_skips WHERE source_url='u://1'").fetchone()[0] == 0


def test_skip_counted_once_per_url_per_run(tmp_root):
    from bogoseo import warehouse
    from bogoseo.collect.sources import Item
    conn = warehouse.connect()
    it = Item("1", "중복 항목", "u://1", "2026-03-01", download_url="d://1", filename="a.pdf")
    gone = {"d://1": {"ok": False, "status": 404, "last_code": 404, "trace": [{"status": 404}], "body_b64": ""}}
    _skip_run(conn, [it, it, it], gone)
    row = conn.execute("SELECT attempts, permanent FROM item_skips WHERE source_url='u://1'").fetchone()
    assert (row["attempts"], row["permanent"]) == (1, 0)


def test_nars_publication_parse_sorted_and_https():
    runner = FixtureRunner("nars")
    fx, _ = _fetcher("국회입법조사처", runner, interval=0)
    ad = get_adapter("국회입법조사처")
    assert ad.key_item == "data_go_kr"
    items = ad.list_items(fx, limit=2, cursor=None, key="TESTKEY")
    assert [i.date for i in items] == ["2026-10-19", "2026-10-16"]
    top = items[0]
    assert top.key == "BDM00000018475" and top.title.startswith("AI 학생부")
    assert top.download_url.startswith("https://www.nars.go.kr/fileDownload2.do")
    assert top.filename.endswith(".pdf") and top.source_url.startswith("http://drm.nars.go.kr")
    assert (top.institution, top.institution_class, top.doc_type) == ("국회입법조사처", "국회", "보고")
    assert "ServiceKey=TESTKEY" in runner.urls[0] and "numOfRows=2" in runner.urls[0]
    last = ad.list_items(fx, limit=3, cursor=None, key="K")[-1]
    assert last.source_url == last.download_url      # no viewer -> falls back to the PDF url
