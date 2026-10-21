import shutil
from collections import namedtuple

import pytest

from bogoseo import diskcap, warehouse

PAD = "추가 설명 필요\n" * 400   # ~6 KB so deletions are measurable


def add_doc(conn, tmp_path, name, score, *, starred=False, unmeasurable=False, date="2026-01-01"):
    p = tmp_path / f"{name}.md"
    p.write_text(f"{name} 제목\n\n□ 가\nㅇ 나\n- {name} 다 필요\n{PAD}", encoding="utf-8")
    r = warehouse.ingest_file(p, conn=conn, institution=f"기관{name}", institution_class="국방",
                              doc_type="보고")
    assert r["status"] == "added", r
    with warehouse.tx(conn):
        conn.execute("UPDATE documents SET quality_score=?, unmeasurable=?, starred=?, "
                     "collection_date=? WHERE doc_id=?",
                     (None if unmeasurable else score, 1 if unmeasurable else 0,
                      1 if starred else 0, date, r["doc_id"]))
    return r["doc_id"]


@pytest.fixture
def wh(tmp_root, tmp_path):
    conn = warehouse.connect()
    src = tmp_path / "src"
    src.mkdir()
    ids = {
        "high": add_doc(conn, src, "high", 90.0),
        "mid": add_doc(conn, src, "mid", 50.0),
        "low": add_doc(conn, src, "low", 10.0, date="2026-02-01"),
        "low_old": add_doc(conn, src, "lowold", 10.0, date="2026-01-01"),
        "unm": add_doc(conn, src, "unm", None, unmeasurable=True),
        "star": add_doc(conn, src, "star", 1.0, starred=True),
    }
    return conn, ids, tmp_root


def original(conn, doc_id):
    return warehouse.abs_path(conn, conn.execute(
        "SELECT file_path FROM files WHERE doc_id=?", (doc_id,)).fetchone()[0])


def row(conn, doc_id):
    return conn.execute("SELECT * FROM files WHERE doc_id=?", (doc_id,)).fetchone()


def invariants(conn):
    for f in conn.execute("SELECT * FROM files").fetchall():
        exists = warehouse.abs_path(conn, f["file_path"]).exists()
        assert exists == (not f["original_deleted"]), f["file_path"]
        assert warehouse.abs_path(conn, f["md_path"]).exists()
    for j in conn.execute("SELECT * FROM deletion_journal").fetchall():
        assert j["unlinked_at"] is not None


def test_cap_deletes_lowest_first_and_keeps_rows(wh):
    conn, ids, root = wh
    size = original(conn, ids["high"]).stat().st_size
    cap = diskcap.warehouse_bytes(conn) - int(size * 2.5)   # needs three deletions
    rep = diskcap.enforce_cap(conn, cap_bytes=cap)
    deleted_docs = [conn.execute("SELECT doc_id FROM files WHERE file_path=?", (p,)).fetchone()[0]
                    for p in rep["deleted"]]
    # unmeasurable first, then lowest score; tie (10.0) broken by older collection date
    assert deleted_docs == [ids["unm"], ids["low_old"], ids["low"]]
    assert rep["after"] <= cap and not rep["over_cap"]
    for k in ("unm", "low_old", "low"):
        f = row(conn, ids[k])
        assert f["original_deleted"] == 1 and not original(conn, ids[k]).exists()
        assert warehouse.abs_path(conn, f["md_path"]).exists()                   # .md kept
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (ids[k],)).fetchone()
        assert warehouse.abs_path(conn, doc["summary_path"]).exists()            # .summary.yaml kept
    for k in ("high", "mid", "star"):
        assert original(conn, ids[k]).exists() and row(conn, ids[k])["original_deleted"] == 0
    invariants(conn)
    assert diskcap.enforce_cap(conn, cap_bytes=cap)["deleted"] == []          # idempotent


def test_cap_never_deletes_starred(wh):
    conn, ids, root = wh
    rep = diskcap.enforce_cap(conn, cap_bytes=0)
    assert rep["over_cap"] is True                      # cannot get under cap: starred stays
    assert original(conn, ids["star"]).exists() and row(conn, ids["star"])["original_deleted"] == 0
    for k in ("high", "mid", "low", "low_old", "unm"):
        assert not original(conn, ids[k]).exists()
    invariants(conn)


def test_cap_dry_run_changes_nothing(wh):
    conn, ids, root = wh
    rep = diskcap.enforce_cap(conn, cap_bytes=0, dry_run=True)
    assert rep["deleted"] and all(original(conn, ids[k]).exists() for k in ids)
    assert conn.execute("SELECT COUNT(*) FROM deletion_journal").fetchone()[0] == 0


def test_cap_resume_after_interruption(wh, monkeypatch):
    conn, ids, root = wh
    size = original(conn, ids["high"]).stat().st_size
    cap = diskcap.warehouse_bytes(conn) - int(size * 2.5)

    class Crash(Exception):
        pass

    def boom(jid):
        raise Crash()

    monkeypatch.setattr(diskcap, "_after_mark_hook", boom)
    with pytest.raises(Crash):
        diskcap.enforce_cap(conn, cap_bytes=cap)
    # interrupted between journal mark and unlink: row marked, file still present, journal pending
    pending = conn.execute("SELECT * FROM deletion_journal WHERE unlinked_at IS NULL").fetchall()
    assert len(pending) == 1
    victim = warehouse.abs_path(conn, pending[0]["file_path"])
    assert victim.exists() and row(conn, pending[0]["doc_id"])["original_deleted"] == 1
    monkeypatch.setattr(diskcap, "_after_mark_hook", lambda jid: None)

    conn2 = warehouse.connect(root)       # a fresh connection resumes
    rep = diskcap.enforce_cap(conn2, cap_bytes=cap)
    assert rep["resumed"] == [pending[0]["file_path"]]
    assert not victim.exists()
    invariants(conn2)
    assert rep["after"] <= cap
    # every marked row has no original; every remaining original has an unmarked row
    for f in conn2.execute("SELECT * FROM files").fetchall():
        assert warehouse.abs_path(conn2, f["file_path"]).exists() != bool(f["original_deleted"])
    assert original(conn2, ids["star"]).exists()


def test_cap_resume_keeps_file_if_starred_meanwhile(wh, monkeypatch):
    conn, ids, root = wh

    class Crash(Exception):
        pass

    monkeypatch.setattr(diskcap, "_after_mark_hook", lambda jid: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        diskcap.enforce_cap(conn, cap_bytes=0)
    monkeypatch.setattr(diskcap, "_after_mark_hook", lambda jid: None)
    marked = conn.execute("SELECT doc_id FROM deletion_journal").fetchone()[0]
    warehouse.set_star(conn, marked, True)
    diskcap.resume_journal(conn)
    assert original(conn, marked).exists() and row(conn, marked)["original_deleted"] == 0
    invariants(conn)


def test_free_disk_guard(tmp_root, monkeypatch):
    assert diskcap.free_disk_ok(min_free=1)
    assert not diskcap.free_disk_ok(min_free=10**18)
    Usage = namedtuple("Usage", "total used free")
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 10**12 - 10**9, 10**9))
    assert not diskcap.free_disk_ok()            # default threshold 2 GB, only 1 GB free
    monkeypatch.setattr(shutil, "disk_usage", lambda p: Usage(10**12, 0, 3 * 10**9))
    assert diskcap.free_disk_ok()


def test_cap_skips_doc_starred_after_snapshot(wh, monkeypatch):
    conn, ids, root = wh
    real_order = diskcap._order

    def order_then_star(c):
        out = real_order(c)
        warehouse.set_star(c, out[0]["doc_id"], True)   # starred after the snapshot was taken
        return out

    monkeypatch.setattr(diskcap, "_order", order_then_star)
    first = real_order(conn)[0]["doc_id"]
    diskcap.enforce_cap(conn, cap_bytes=0)
    assert original(conn, first).exists() and row(conn, first)["original_deleted"] == 0
    assert conn.execute("SELECT COUNT(*) FROM deletion_journal WHERE doc_id=?",
                        (first,)).fetchone()[0] == 0


def test_resume_records_missing_file_as_deleted_even_if_starred(wh, monkeypatch):
    conn, ids, root = wh

    class Crash(Exception):
        pass

    monkeypatch.setattr(diskcap, "_after_mark_hook", lambda jid: (_ for _ in ()).throw(Crash()))
    with pytest.raises(Crash):
        diskcap.enforce_cap(conn, cap_bytes=0)
    monkeypatch.setattr(diskcap, "_after_mark_hook", lambda jid: None)
    marked = conn.execute("SELECT doc_id FROM deletion_journal").fetchone()[0]
    original(conn, marked).unlink()                  # file vanished before the resume
    warehouse.set_star(conn, marked, True)
    diskcap.resume_journal(conn)
    assert row(conn, marked)["original_deleted"] == 1
    j = conn.execute("SELECT * FROM deletion_journal WHERE doc_id=?", (marked,)).fetchone()
    assert j is not None and j["unlinked_at"] is not None
    invariants(conn)
