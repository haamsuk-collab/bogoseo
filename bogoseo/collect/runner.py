"""Collection runner: one adapter -> download -> ``warehouse.ingest_file`` with cursor and run record."""
from __future__ import annotations

import hashlib
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .. import config, diskcap, warehouse
from .fetch import Fetcher, Response, stages_summary
from .sources import Adapter, Item, SourceError, adapters, get_adapter

NO_KEY_WARNING = "⚠ API 키 미입력"
FILE_EXTS = (".pdf", ".hwp", ".hwpx", ".md", ".txt")


@dataclass
class RunResult:
    source: str
    kind: str
    result: str                 # 성공|건너뜀|차단됨|실패|0건
    status: str                 # API: 통과|미검증|실패 ; web: 입고|차단됨|실패|0건
    added: int = 0
    seen: int = 0
    detail: str = ""
    stages: str = ""
    code: str = ""
    cursor: Optional[str] = None
    counts: dict = field(default_factory=dict)


def _safe_name(s: str, limit: int = 60) -> str:
    s = re.sub(r"[\\/:*?\"<>|\s]+", "_", s).strip("._")
    return s[:limit] or "doc"


def _file_name(item: Item, resp: Optional[Response]) -> str:
    name = item.filename or ""
    if not name and resp is not None and resp.disposition:
        m = re.search(r"filename\*?=(?:UTF-8'')?\"?([^\";]+)", resp.disposition)
        if m:
            from urllib.parse import unquote
            name = unquote(m.group(1))
    ext = Path(name).suffix.lower()
    if ext not in FILE_EXTS:
        ct = (resp.content_type if resp is not None else "").lower()
        ext = ".pdf" if "pdf" in ct else (".hwpx" if "hwpx" in ct else ".hwp" if "hwp" in ct else ext)
    if ext not in FILE_EXTS:
        ext = ".pdf" if resp is not None and resp.body[:4] == b"%PDF" else ".hwp" if resp is not None and resp.body[:4] == b"\xd0\xcf\x11\xe0" else ".hwpx" if resp is not None and resp.body[:2] == b"PK" else ext
    stem = Path(name).stem if name else item.title
    return f"{_safe_name(stem)}{ext}"


def _is_html(resp: Response) -> bool:
    head = resp.body[:200].lstrip().lower()
    return head.startswith((b"<!doctype html", b"<html")) or "text/html" in resp.content_type.lower()


def _record_source(conn, name: str, kind: str, status: str, stages: str, code: str, key_present: bool) -> None:
    conn.execute(
        "INSERT INTO sources(name, kind, status, stages_tried, last_response_code, key_present, updated_at) "
        "VALUES(?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, status=excluded.status, "
        "stages_tried=excluded.stages_tried, last_response_code=excluded.last_response_code, "
        "key_present=excluded.key_present, updated_at=excluded.updated_at",
        (name, kind, status, stages, code, int(key_present), warehouse.now_iso()))


def _record_run(conn, r: RunResult, started: str) -> None:
    conn.execute(
        "INSERT INTO collection_runs(source, started_at, finished_at, added_count, cursor, result, detail) "
        "VALUES(?,?,?,?,?,?,?)",
        (r.source, started, warehouse.now_iso(), r.added, r.cursor, r.result, r.detail))


def _permanently_skipped(conn, url: str) -> bool:
    return bool(url) and conn.execute(
        "SELECT 1 FROM item_skips WHERE source_url=? AND permanent=1", (url,)).fetchone() is not None


def _note_skip(conn, source: str, url: str, reason: str, *, immediate: bool, after: int,
               noted: Optional[dict] = None) -> bool:
    """Record a failed fetch of ``url``; becomes a permanent skip at once (``immediate``) or after
    ``after`` consecutive failed runs.  ``noted`` (per-run) makes a URL count at most once per run.
    Returns True when the item is now permanently skipped."""
    if noted is not None and url in noted and not immediate:
        return noted[url]
    row = conn.execute("SELECT attempts FROM item_skips WHERE source_url=?", (url,)).fetchone()
    attempts = (row["attempts"] if row else 0) + 1
    permanent = int(immediate or attempts >= after)
    conn.execute(
        "INSERT INTO item_skips(source_url, source, reason, attempts, permanent, updated_at) VALUES(?,?,?,?,?,?) "
        "ON CONFLICT(source_url) DO UPDATE SET reason=excluded.reason, attempts=excluded.attempts, "
        "permanent=excluded.permanent, updated_at=excluded.updated_at",
        (url, source, reason, attempts, permanent, warehouse.now_iso()))
    if noted is not None:
        noted[url] = bool(permanent)
    return bool(permanent)


def _already(conn, url: str) -> bool:
    return bool(url) and conn.execute("SELECT 1 FROM files WHERE source_url=? LIMIT 1", (url,)).fetchone() is not None


def run_source(adapter: Adapter, conn, *, keys: dict, limit: Optional[int] = None,
               fetcher_factory: Optional[Callable[..., Fetcher]] = None,
               disk_ok: Optional[Callable[[], bool]] = None, tmp_root: Optional[Path] = None) -> RunResult:
    settings = config.settings()
    limit = limit or int(settings.get("collect_limit_per_source", 20))
    disk_ok = disk_ok or diskcap.free_disk_ok
    started = warehouse.now_iso()
    name, kind = adapter.name, adapter.kind
    key = keys.get(adapter.key_item) if adapter.key_item else None
    cursor0 = warehouse.get_cursor(conn, name)

    if kind == "api" and not key:
        r = RunResult(name, kind, "건너뜀", "미검증", detail="API 키 미입력", cursor=cursor0)
        _record_source(conn, name, kind, "미검증", "", "", False)
        _record_run(conn, r, started)
        return r

    fx = (fetcher_factory or Fetcher)(name, conn=conn, settings=settings)
    r = RunResult(name, kind, "실패", "실패", cursor=cursor0)
    items: list = []
    try:
        items = adapter.list_items(fx, limit=limit, cursor=cursor0, key=key)
    except SourceError as e:
        resp = e.resp or fx.last_response
        r.detail = str(e)
        _classify_failure(r, resp, blocked_hint="blocked" in str(e).lower())
    except Exception as e:  # parse errors on a block/error page
        r.detail = f"{type(e).__name__}: {e}"
        _classify_failure(r, fx.last_response)
    else:
        r.seen = len(items)
        last = fx.last_response
        r.stages, r.code = stages_summary(last) if last else "", str(last.code or "") if last else ""
        _ingest_items(adapter, conn, fx, items, r, settings, disk_ok, tmp_root)
    if r.result == "실패" and not r.detail and not r.stages:
        r.detail = "no result"
    _record_source(conn, name, kind, r.status, r.stages, r.code, bool(key) or kind == "web")
    _record_run(conn, r, started)
    return r


def _classify_failure(r: RunResult, resp: Optional[Response], blocked_hint: bool = False) -> None:
    if resp is not None:
        r.stages, r.code = stages_summary(resp), str(resp.code or resp.error or "")
    if (resp is not None and resp.blocked) or blocked_hint:
        r.result, r.status = "차단됨", ("차단됨" if r.kind == "web" else "실패")
        r.detail = f"차단됨: stages={r.stages} last_code={r.code or '-'}"
    else:
        r.result, r.status = "실패", "실패"
        r.detail = f"{r.detail} (code={r.code or '-'})".strip()


TOO_LARGE = "too_large"
# Fetch failures that stay the same on retry; they become permanent skips after ``permanent_skip_after`` runs.
REPEATABLE = {"timeout": "timeout", 404: "http_404", 410: "http_410"}


@dataclass
class DownloadFailed:
    reason: str = ""            # "" = unclassified (always retried)


def _download(adapter, fx, item: Item):
    """Return (bytes, filename), None on failure, DownloadFailed(reason) on a fetch failure, or TOO_LARGE
    when the file exceeds max_download_mb.

    The list-text fallback is used only for items without a ``download_url``; a failed
    attachment download is a failure so the original is retried on the next run."""
    if item.download_url:
        resp = fx.get(item.download_url, download=True)
        if resp.error == TOO_LARGE:
            return TOO_LARGE
        if resp.ok and resp.body and not _is_html(resp):
            body = resp.body
            fname = _file_name(item, resp)
            if hasattr(adapter, "render") and item.filename and item.filename.endswith(".xml"):
                return adapter.render(body, item).encode(), f"{_safe_name(item.title)}.md"
            return body, fname
        return DownloadFailed(REPEATABLE.get(resp.error if resp.error == "timeout" else resp.code, ""))
    if item.text:
        return item.text.encode(), f"{_safe_name(item.title)}.md"
    return None


def _ingest_items(adapter, conn, fx, items, r: RunResult, settings, disk_ok, tmp_root) -> None:
    counts = {"added": 0, "duplicate": 0, "version": 0, "skipped": 0, "failed": 0, "needs_user": 0}
    tmp = Path(tempfile.mkdtemp(prefix="bogoseo-collect-", dir=str(tmp_root) if tmp_root else None))
    cursor0 = r.cursor
    done_dates: list = []       # dates of items processed successfully before the first failure
    first_fail: Optional[str] = None
    failed_any = False
    after = int(settings.get("permanent_skip_after", 3))
    noted: dict = {}            # url -> permanent, so a URL repeated within one run counts once

    def _fail(item: Item) -> None:
        nonlocal first_fail, failed_any
        if not failed_any:
            failed_any, first_fail = True, item.date or ""

    try:
        for item in sorted(items, key=lambda i: i.date or ""):      # oldest first
            if not disk_ok():
                r.detail = "디스크 여유 부족으로 수집 중단"
                counts["stopped_disk"] = 1
                break
            if _already(conn, item.source_url):
                counts["skipped"] += 1
                if not failed_any and item.date:
                    done_dates.append(item.date)
                continue
            if _permanently_skipped(conn, item.source_url):     # persisted earlier: not fetched again
                counts["skipped"] += 1
                counts["skipped_permanent"] = counts.get("skipped_permanent", 0) + 1
                if not failed_any and item.date:
                    done_dates.append(item.date)
                continue
            got = _download(adapter, fx, item)
            permanent = None
            if got == TOO_LARGE:
                permanent = TOO_LARGE
            elif isinstance(got, DownloadFailed) and got.reason and _note_skip(
                    conn, adapter.name, item.source_url, got.reason, immediate=False, after=after, noted=noted):
                permanent = got.reason
            if permanent:       # logged as a skip, never retried, does not hold the cursor
                if got == TOO_LARGE:
                    _note_skip(conn, adapter.name, item.source_url, TOO_LARGE, immediate=True, after=after)
                    counts["too_large"] = counts.get("too_large", 0) + 1
                counts["skipped"] += 1
                counts["skipped_permanent"] = counts.get("skipped_permanent", 0) + 1
                if not failed_any and item.date:
                    done_dates.append(item.date)
                continue
            if got is None or isinstance(got, DownloadFailed):
                counts["failed"] += 1
                _fail(item)
                continue
            body, fname = got
            d = tmp / hashlib.sha1((item.source_url + fname).encode()).hexdigest()[:10]
            d.mkdir()
            path = d / fname
            path.write_bytes(body)
            res = warehouse.ingest_file(
                path, conn=conn, source=adapter.name, source_url=item.source_url,
                institution=item.institution or adapter.institution,
                institution_class=item.institution_class or adapter.institution_class,
                doc_type=item.doc_type or adapter.default_doc_type, title=item.title,
                document_date=item.date, ask=lambda q, opts: opts[-1])
            st = res.get("status", "failed")
            counts[st] = counts.get(st, 0) + 1
            if st in ("added", "duplicate", "version"):
                conn.execute("DELETE FROM item_skips WHERE source_url=?", (item.source_url,))
                if not failed_any and item.date:
                    done_dates.append(item.date)
            else:
                _fail(item)
            if st in ("added", "version"):
                r.added += 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    # Cursor = newest date of the contiguous successful prefix (strictly before the first failure's date).
    ok_dates = [d for d in done_dates if first_fail is None or d < first_fail]
    newest = max([d for d in [cursor0, *ok_dates] if d], default=None)
    if newest is not None and newest != cursor0:
        warehouse.set_cursor(conn, adapter.name, newest)
    r.counts = counts
    r.cursor = newest
    detail = ", ".join(f"{k}={v}" for k, v in counts.items() if v)
    r.detail = (r.detail + "; " if r.detail else "") + detail
    ok_status = "통과" if adapter.kind == "api" else None
    if r.added > 0:
        r.result, r.status = "성공", ok_status or "입고"
    elif counts.get("failed") and not counts.get("duplicate") and not counts.get("skipped"):
        r.result, r.status = "실패", "실패"
    else:
        r.result, r.status = "0건", ok_status or "0건"


def run_all(conn, *, keys: Optional[dict] = None, only: Optional[list] = None, limit: Optional[int] = None,
            fetcher_factory=None, disk_ok=None, tmp_root=None, out: Callable[[str], None] = print) -> list:
    keys = config.load_keys() if keys is None else keys
    results = []
    for adapter in adapters():
        if only and adapter.name not in only:
            continue
        if adapter.kind == "api" and not keys.get(adapter.key_item):
            out(f"{NO_KEY_WARNING}: {adapter.name} ({adapter.key_item})")
        if (disk_ok or diskcap.free_disk_ok)() is False:
            r = RunResult(adapter.name, adapter.kind, "건너뜀", "미검증" if adapter.kind == "api" else "실패",
                          detail="디스크 여유 부족으로 수집 중단")
            _record_run(conn, r, warehouse.now_iso())
            results.append(r)
            out(f"{r.source}: {r.status} / {r.result} (+0) {r.detail}")
            continue
        try:
            r = run_source(adapter, conn, keys=keys, limit=limit, fetcher_factory=fetcher_factory,
                           disk_ok=disk_ok, tmp_root=tmp_root)
        except Exception as e:      # defined status even on an unexpected bug
            r = RunResult(adapter.name, adapter.kind, "실패", "실패", detail=f"{type(e).__name__}: {e}")
            _record_source(conn, adapter.name, adapter.kind, "실패", "", "", bool(keys.get(adapter.key_item)))
            _record_run(conn, r, warehouse.now_iso())
        out(f"{r.source}: {r.status} / {r.result} (+{r.added}) {r.detail}".rstrip())
        results.append(r)
    return results
