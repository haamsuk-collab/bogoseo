"""Warehouse disk cap (journal-then-unlink deletion with resume) and free-space guard."""
from __future__ import annotations

import shutil
from pathlib import Path

from . import config, warehouse


def free_disk_ok(min_free: int | None = None, path=None) -> bool:
    """True when free space under `path` (default: the warehouse root) is at least min_free."""
    if min_free is None:
        min_free = config.settings()["min_free_bytes"]
    p = Path(path) if path else config.root()
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free >= min_free


def warehouse_bytes(conn) -> int:
    """Disk usage of the document files under references/ (index.sqlite* is bookkeeping)."""
    total = 0
    for p in (conn.root / "references").rglob("*"):
        if p.is_file() and not p.name.startswith("index.sqlite"):
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def _order(conn) -> list[dict]:
    """Deletion order: unmeasurable first, then lowest score, older collection date, doc id."""
    rows = [dict(r) for r in conn.execute(
        "SELECT doc_id, quality_score, unmeasurable, collection_date FROM documents "
        "WHERE starred=0").fetchall()]

    def key(d):
        unm = 0 if (d["unmeasurable"] or d["quality_score"] is None) else 1
        return (unm, d["quality_score"] if d["quality_score"] is not None else 0.0,
                d["collection_date"] or "", d["doc_id"])
    return sorted(rows, key=key)


def resume_journal(conn) -> list[str]:
    """Finish interrupted deletions: unlink journaled files (unless the doc was starred since)."""
    done = []
    pending = conn.execute(
        "SELECT j.*, d.starred AS starred FROM deletion_journal j "
        "LEFT JOIN documents d ON d.doc_id=j.doc_id WHERE j.unlinked_at IS NULL ORDER BY j.id"
    ).fetchall()
    for j in pending:
        if j["starred"]:
            p = warehouse.abs_path(conn, j["file_path"])
            with warehouse.tx(conn):
                if p.exists():          # still there: cancel the deletion
                    conn.execute("UPDATE files SET original_deleted=0 WHERE id=?", (j["file_id"],))
                    conn.execute("DELETE FROM deletion_journal WHERE id=?", (j["id"],))
                else:                   # already gone: record it, keep the history
                    conn.execute("UPDATE files SET original_deleted=1 WHERE id=?", (j["file_id"],))
                    conn.execute("UPDATE deletion_journal SET unlinked_at=? WHERE id=?",
                                 (warehouse.now_iso(), j["id"]))
            continue
        warehouse.abs_path(conn, j["file_path"]).unlink(missing_ok=True)
        with warehouse.tx(conn):
            conn.execute("UPDATE deletion_journal SET unlinked_at=? WHERE id=?",
                         (warehouse.now_iso(), j["id"]))
        done.append(j["file_path"])
    return done


def _delete_file(conn, f) -> bool:
    """Mark in the journal (committed), then unlink, then record the unlink.

    Returns False (nothing done) if the document was starred meanwhile."""
    with warehouse.tx(conn):
        row = conn.execute("SELECT starred FROM documents WHERE doc_id=?", (f["doc_id"],)).fetchone()
        if row is not None and row["starred"]:
            return False
        cur = conn.execute(
            "INSERT INTO deletion_journal(doc_id, file_id, file_path, marked_at) VALUES(?,?,?,?)",
            (f["doc_id"], f["id"], f["file_path"], warehouse.now_iso()))
        jid = cur.lastrowid
        conn.execute("UPDATE files SET original_deleted=1 WHERE id=?", (f["id"],))
    _after_mark_hook(jid)
    warehouse.abs_path(conn, f["file_path"]).unlink(missing_ok=True)
    with warehouse.tx(conn):
        conn.execute("UPDATE deletion_journal SET unlinked_at=? WHERE id=?",
                     (warehouse.now_iso(), jid))
    return True


def _after_mark_hook(jid: int) -> None:
    """Seam between journal mark and unlink; tests patch this to simulate an interruption."""


def enforce_cap(conn, cap_bytes: int | None = None, dry_run: bool = False) -> dict:
    """Delete originals (lowest score first) until warehouse usage <= cap. Never starred."""
    cap = cap_bytes if cap_bytes is not None else config.settings()["cap_bytes"]
    resumed = [] if dry_run else resume_journal(conn)
    usage = warehouse_bytes(conn)
    report = {"cap": cap, "before": usage, "deleted": [], "resumed": resumed, "dry_run": dry_run,
              "after": usage, "over_cap": usage > cap}
    if usage <= cap:
        return report
    for d in _order(conn):
        files = conn.execute(
            "SELECT * FROM files WHERE doc_id=? AND original_deleted=0 ORDER BY id",
            (d["doc_id"],)).fetchall()
        for f in files:
            if usage <= cap:
                if dry_run:
                    break
                usage = warehouse_bytes(conn)      # estimate reached: confirm with real usage
                if usage <= cap:
                    break
            p = warehouse.abs_path(conn, f["file_path"])
            size = p.stat().st_size if p.exists() else 0
            if not dry_run and not _delete_file(conn, f):
                break                       # starred since the snapshot: skip this document
            usage -= size
            report["deleted"].append(f["file_path"])
        if usage <= cap:
            if dry_run:
                break
            usage = warehouse_bytes(conn)
            if usage <= cap:
                break
    report["after"] = usage
    report["over_cap"] = usage > cap
    return report
