"""Checks the recorded state of the integrator's real collection run (real root, real index.sqlite)."""
import sqlite3
import subprocess
from pathlib import Path

import pytest

from bogoseo import config
from bogoseo.collect.sources import adapters

DB = config.repo_dir() / "references" / "index.sqlite"
pytestmark = pytest.mark.skipif(not DB.exists(), reason="no real collection run recorded yet")


@pytest.fixture
def db():
    c = sqlite3.connect(str(DB))
    c.row_factory = sqlite3.Row
    yield c
    c.close()


def test_at_least_four_web_sources_have_report_like_documents(db):
    web = [a.name for a in adapters("web")]
    ok = [n for n in web if db.execute("SELECT COUNT(*) FROM documents WHERE source=? AND report_like=1", (n,)).fetchone()[0] >= 1]
    assert len(ok) >= 4, ok


def test_blocked_sources_report_stages_and_code(db):
    for r in db.execute("SELECT * FROM sources WHERE status='차단됨'"):
        assert r["stages_tried"] and r["last_response_code"], r["name"]


def test_no_bypass_of_insane_search(db):
    assert db.execute("SELECT COUNT(*) FROM web_access_log WHERE via_insane_search!=1").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM web_access_log").fetchone()[0] > 0


def test_second_run_added_zero(db):
    rows = db.execute("SELECT source, added_count, started_at FROM collection_runs ORDER BY run_id").fetchall()
    last = {}
    for r in rows:
        last.setdefault(r["source"], []).append(r["added_count"])
    assert any(len(v) >= 2 for v in last.values())
    assert all(v[-1] == 0 for v in last.values() if len(v) >= 2)


def test_keyless_api_sources_unverified_and_skipped(db):
    keys = config.load_keys()
    for a in adapters("api"):
        if keys.get(a.key_item):
            continue
        assert db.execute("SELECT status FROM sources WHERE name=?", (a.name,)).fetchone()["status"] == "미검증"
        res = [r["result"] for r in db.execute("SELECT result FROM collection_runs WHERE source=?", (a.name,))]
        assert res and set(res) == {"건너뜀"}


def test_service_result_success():
    out = subprocess.run(["systemctl", "--user", "show", "bogoseo-collect.service", "-p", "Result"],
                         capture_output=True, text=True).stdout.strip()
    assert out == "Result=success"
