"""Reference warehouse: naming, folders, SQLite index, dedup/versioning, doc-ID grouping.

Copy-only: originals in the inbox are never moved or deleted.
"""
from __future__ import annotations

import contextlib
import difflib
import datetime as dt
import hashlib
import json
import re
import shutil
import sqlite3
from pathlib import Path

import yaml

from . import config, extract as ex, markers, style

DOC_TYPES = ["보고", "개선안", "계획", "결과", "검토", "건의", "지침", "기타"]
INST_CLASSES = ["국방", "중앙부처", "국회", "지자체", "공공기관", "민간"]
SUPPORTED = {".pdf", ".hwp", ".hwpx", ".docx", ".xlsx", ".md", ".txt", ".png", ".jpg", ".jpeg"}
FILENAME_RE = re.compile(
    r"^\d{8}x?_[^_]+_(보고|개선안|계획|결과|검토|건의|지침|기타)_[^_\s]{1,40}(_\d+)?\.[A-Za-z0-9]+$")
GENERIC_HEADINGS = {"개요", "배경", "목적", "현황", "결론", "문제점", "현황문제점", "추진배경",
                    "개선안", "향후추진", "기대효과"}
JACCARD_JOIN = 0.6
TITLE_MATCH = 0.9
# Headings/titles that occur in unrelated documents: never evidence of identity.
GENERIC_TITLES = ["첨부파일", "첨부", "법률제호", "의안", "제1장총칙", "총칙", "보도자료", "본문", "목차",
                  "개요", "붙임", "별첨", "별지", "참고자료", "알림", "공지", "공고", "안내", "제목없음"]
GENERIC_TITLE_PATTERNS = [r"\d*대안의제안이유", r"제안이유", r"제?\d+장\w{0,6}", r"제?\d+조", r"붙임\d*",
                          r"첨부\d*", r"별표\d*", r"별지\d*", r"\d+", r"보도시점.*",
                          r"자료제공.*", r"보도자료", r"\d{6,8}[월화수목금토일]?(조간|석간)?용?",
                          r"(조간|석간)용?\d*"]


def _title_norm(s: str) -> str:
    return _norm_heading(_clean_title(s or ""))


def _is_generic_title(norm: str) -> bool:
    return (not norm or norm in GENERIC_HEADINGS or norm in GENERIC_TITLES
            or any(re.fullmatch(p, norm) for p in GENERIC_TITLE_PATTERNS))


def _item_title(name: str) -> str:
    """Title part of a collected file name: extension, date prefix and [institution_kind] removed."""
    s = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", name or "")
    s = re.sub(r"^\d{6,8}x?_", "", s)
    return s


def _file_item_title(name: str) -> str:
    """Display form of a collected file's item title ('' when empty or generic)."""
    t = re.sub(r"\s+", " ", _clean_title(_item_title(name)).replace("_", " ")).strip()
    return "" if _is_generic_title(_title_norm(t)) else t


def _ftitle(f) -> str:
    """Item title of a collected file row: the persisted collector title, else the file name's."""
    return f["item_title"] or _item_title(f["original_name"] or f["filename"] or "")


def _fdisplay(f) -> str:
    """Display form of _ftitle ('' when generic), as stored in documents.title."""
    if f["item_title"]:
        t = re.sub(r"\s+", " ", _clean_title(f["item_title"]).replace("_", " ")).strip()
        return "" if _is_generic_title(_title_norm(t)) else t
    return _file_item_title(f["original_name"] or f["filename"] or "")


def _body_title(s: str) -> str:
    """Cleaned body-derived title, '' when it is boilerplate."""
    t = _clean_title(s or "")
    return "" if _is_generic_title(_title_norm(t)) else t


def _titles_match(a: str, b: str) -> bool:
    """Same non-generic title (normalized similarity >= TITLE_MATCH); empty/generic never matches."""
    na, nb = _title_norm(a), _title_norm(b)
    if _is_generic_title(na) or _is_generic_title(nb):
        return False
    return na == nb or difflib.SequenceMatcher(None, na, nb).ratio() >= TITLE_MATCH


SCHEMA = """
CREATE TABLE IF NOT EXISTS documents(
 doc_id TEXT PRIMARY KEY, title TEXT, institution TEXT, institution_class TEXT, doc_type TEXT,
 quality_score REAL, unmeasurable INTEGER DEFAULT 0, report_like INTEGER DEFAULT 0,
 starred INTEGER DEFAULT 0, needs_classification_check INTEGER DEFAULT 0, source TEXT,
 source_url TEXT, document_date TEXT, collection_date TEXT, folder_path TEXT, summary_path TEXT);
CREATE TABLE IF NOT EXISTS files(
 id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, filename TEXT, file_path TEXT, md_path TEXT,
 role TEXT, version_date TEXT, sha256 TEXT, text_hash TEXT, pages INTEGER, scanned INTEGER,
 original_deleted INTEGER DEFAULT 0, source_url TEXT, collection_date TEXT,
 extract_ok INTEGER DEFAULT 1, ocr_line_count INTEGER DEFAULT 0, low_conf_count INTEGER DEFAULT 0,
 is_attachment INTEGER DEFAULT 0, heading_norm TEXT, tokens TEXT, title_line TEXT,
 original_name TEXT);
CREATE INDEX IF NOT EXISTS files_sha ON files(sha256);
CREATE INDEX IF NOT EXISTS files_text ON files(text_hash);
CREATE INDEX IF NOT EXISTS files_doc ON files(doc_id);
CREATE TABLE IF NOT EXISTS deletion_journal(
 id INTEGER PRIMARY KEY AUTOINCREMENT, doc_id TEXT, file_id INTEGER, file_path TEXT,
 marked_at TEXT, unlinked_at TEXT);
CREATE TABLE IF NOT EXISTS sources(
 name TEXT PRIMARY KEY, kind TEXT, status TEXT, stages_tried TEXT, last_response_code TEXT,
 cursor TEXT, key_present INTEGER, updated_at TEXT);
CREATE TABLE IF NOT EXISTS collection_runs(
 run_id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, started_at TEXT, finished_at TEXT,
 added_count INTEGER, cursor TEXT, result TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS item_skips(
 source_url TEXT PRIMARY KEY, source TEXT, reason TEXT, attempts INTEGER DEFAULT 0,
 permanent INTEGER DEFAULT 0, updated_at TEXT);
CREATE TABLE IF NOT EXISTS web_access_log(
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT, url TEXT, via_insane_search INTEGER,
 status_code TEXT, ts TEXT);
"""


class Conn(sqlite3.Connection):
    root: Path


def connect(root=None) -> Conn:
    root = Path(root) if root else config.root()
    refs = root / "references"
    refs.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(refs / "index.sqlite"), factory=Conn, isolation_level=None,
                           timeout=30)
    conn.root = root
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(SCHEMA)
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(files)")}
    if "item_title" not in cols:
        conn.execute("ALTER TABLE files ADD COLUMN item_title TEXT")
        _backfill_item_titles(conn)
    return conn


@contextlib.contextmanager
def tx(conn):
    """Re-entrant transaction: BEGIN IMMEDIATE ... COMMIT, ROLLBACK on error."""
    if conn.in_transaction:
        yield
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def abs_path(conn, rel) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else conn.root / p


def rel_path(conn, p) -> str:
    p = Path(p)
    try:
        return str(p.resolve().relative_to(conn.root.resolve()))
    except ValueError:
        return str(p)


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def today() -> str:
    return dt.date.today().isoformat()


# ---------------------------------------------------------------- cursors

def get_cursor(conn, source: str) -> str | None:
    r = conn.execute("SELECT cursor FROM sources WHERE name=?", (source,)).fetchone()
    return r["cursor"] if r else None


def set_cursor(conn, source: str, value) -> None:
    with tx(conn):
        conn.execute(
            "INSERT INTO sources(name, cursor, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
            (source, None if value is None else str(value), now_iso()))


# ---------------------------------------------------------------- helpers

def norm_text_hash(text: str) -> str:
    t = re.sub(r"[^0-9A-Za-z\uac00-\ud7a3]+", "", text).lower()
    return hashlib.sha256(t.encode("utf-8")).hexdigest()


def _norm_heading(s: str) -> str:
    return re.sub(r"[^0-9A-Za-z\uac00-\ud7a3]+", "", s or "")


def _clean_title(s: str) -> str:
    """Display title: markers/brackets that carry no topic removed."""
    s = re.sub(r"[【\[(][^】\])]{0,12}[】\])]", " ", s or "")
    s = re.sub(r"[【】\[\]()]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _title_summary(s: str, fallback: str) -> str:
    t = _clean_title(s) or fallback
    t = re.sub(r"[\s_/\\:*?\"<>|]+", "", t)
    t = t[:40] or re.sub(r"[\s_/\\:*?\"<>|]+", "", fallback)[:40] or "문서"
    return t


def _safe_part(s: str) -> str:
    return re.sub(r"[\s_/\\:*?\"<>|]+", "", s or "") or "미상"


_TYPE_KEYWORDS = [
    ("개선안", ("개선",)), ("건의", ("건의",)), ("계획", ("계획",)), ("결과", ("결과",)),
    ("검토", ("검토",)), ("지침", ("지침", "규정", "훈령", "매뉴얼", "가이드")),
    ("보고", ("보고",)),
]
_CLASS_KEYWORDS = [
    ("국방", ("국방", "공군", "육군", "해군", "해병", "합참", "합동참모", "방위사업", "병무")),
    ("국회", ("국회",)),
    ("지자체", ("시청", "도청", "구청", "군청", "특별시", "광역시", "서울시", "도지사", "시의회")),
    ("공공기관", ("공사", "공단", "진흥원", "연구원", "재단", "공공기관", "감사원", "조달청")),
    ("중앙부처", ("부", "처", "청", "위원회", "총리")),
    ("민간", ("주식회사", "(주)", "협회", "대학")),
]


def auto_doc_type(title: str) -> str | None:
    for dtype, kws in _TYPE_KEYWORDS:
        if any(k in (title or "") for k in kws):
            return dtype
    return None


def auto_inst_class(institution: str) -> str | None:
    for cls, kws in _CLASS_KEYWORDS:
        if any(k in (institution or "") for k in kws):
            return cls
    return None


def _date8(d: str | None) -> str | None:
    if not d:
        return None
    digits = re.sub(r"\D", "", str(d))
    return digits[:8] if len(digits) >= 8 else None


def _iso(d: str | None) -> str | None:
    d8 = _date8(d)
    return f"{d8[:4]}-{d8[4:6]}-{d8[6:]}" if d8 else None


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _body_without_title(text: str, lines: list[dict] | None = None) -> str:
    """Document text minus its title line (a title never vouches for itself)."""
    out, dropped = [], False
    for raw in text.split("\n"):
        if not dropped and raw.strip():
            dropped = True
            if lines is None or any(ln["raw"] == raw and ln["kind"] == "title" for ln in lines):
                continue
        out.append(raw)
    return "\n".join(out)


def _group_tokens(lines: list[dict], full_text: str) -> tuple[set, str, str, bool]:
    """(token set, normalized first heading, title line, first-heading-is-attachment)."""
    title = next((ln["body"] for ln in lines if ln["kind"] == "title"), "")
    first = next((ln for ln in lines if ln["kind"] in ("heading", "attachment")), None)
    heading = first["body"] if first else ""
    is_att = bool(first and first["kind"] == "attachment")
    body_nouns = style.nouns(_body_without_title(full_text, lines))
    t_title = {n for n in style.nouns(title) if n in body_nouns}
    t_head = style.nouns(re.sub(markers.ATTACHMENT_PATTERN, "", heading)) if heading else set()
    toks = (t_title | t_head) - {"참고"}
    return toks, _norm_heading(heading), title, is_att


# ---------------------------------------------------------------- ingest

def _route_draft(path: Path, report: str, conn) -> dict:
    d = config.reports_dir() if conn is None else conn.root / "reports"
    sd = d / report / "starting_draft"
    sd.mkdir(parents=True, exist_ok=True)
    dest = sd / path.name
    if not dest.exists() or ex.sha256_file(dest) != ex.sha256_file(path):
        shutil.copyfile(path, dest)
    res = ex.extract(path)
    (sd / "extract.md").write_text(res.get("markdown") or "", encoding="utf-8")
    return {"status": "draft", "doc_id": None, "file_id": None, "filename": path.name,
            "report_dir": str(d / report), "extract_ok": bool(res.get("ok"))}


def _advance_cursor(conn, cursor):
    if cursor:
        conn.execute(
            "INSERT INTO sources(name, cursor, updated_at) VALUES(?,?,?) "
            "ON CONFLICT(name) DO UPDATE SET cursor=excluded.cursor, updated_at=excluded.updated_at",
            (cursor[0], None if cursor[1] is None else str(cursor[1]), now_iso()))


def _new_doc_id(conn) -> str:
    n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] + 1
    while conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (f"doc_{n:04d}",)).fetchone():
        n += 1
    return f"doc_{n:04d}"


def _match_choice(answer: str, options: list[str], ids: list[str]) -> str | None:
    """Map the ask() answer to a doc id, or None for 'new document'."""
    a = (answer or "").strip()
    for did in ids:
        if a == did or a.startswith(did) or a in [o for o in options if o.startswith(did)]:
            return did
    for did in ids:
        if did in a:
            return did
    return None


def _group(conn, institution, toks, heading_norm, is_att, title, ask, auto_only=False,
           source_url=None, item_title=None):
    """Return ('join', doc_id) | ('new', None) | ('ask', None).

    Automatic join needs a close, non-generic title match with an existing file of the document;
    a generic or empty heading (token overlap) is never evidence of identity.  A collected item
    (``source_url``) is identified by its item title (``item_title``: collector title / title part
    of the file name), never by the body's first line (often boilerplate), and when nothing
    matches it is a new document: nobody can be asked."""
    cands = []
    for d in conn.execute("SELECT doc_id FROM documents WHERE institution=? ORDER BY doc_id",
                          (institution,)).fetchall():
        best, teq = 0.0, False
        for f in conn.execute("SELECT tokens, title_line, source_url, original_name, item_title "
                              "FROM files WHERE doc_id=?", (d["doc_id"],)).fetchall():
            best = max(best, _jaccard(toks, set(json.loads(f["tokens"] or "[]"))))
            f_coll = bool(f["source_url"])
            if source_url and f_coll:
                same = _titles_match(item_title or "", _ftitle(f))
            elif source_url or f_coll:
                same = _titles_match(item_title or title,
                                     _ftitle(f) if f_coll
                                     else (f["title_line"] or ""))
            else:
                same = _titles_match(title, f["title_line"] or "")
            if same:
                teq = True
        cands.append((d["doc_id"], best, teq))
    auto = [c for c in cands if c[2]]
    if len(auto) == 1:
        return "join", auto[0][0], cands
    if len(auto) > 1:
        return "ask", None, sorted(auto, key=lambda c: (-c[1], c[0]))
    if source_url:
        return "new", None, cands
    related = [c for c in cands if c[1] > 0 or is_att]
    if related:
        return "ask", None, sorted(related, key=lambda c: (-c[1], c[0]))
    return "new", None, cands


def ingest_file(path, *, conn, source=None, source_url=None, institution=None,
                institution_class=None, doc_type=None, title=None, document_date=None,
                doc_id=None, ask=None, draft=None, cursor=None, confirm_warehouse=False,
                report_like_hint=None) -> dict:
    path = Path(path)
    res = {"status": "failed", "doc_id": None, "file_id": None, "filename": path.name}
    if not path.is_file():
        res["error"] = "not a file"
        return res
    if path.suffix.lower() not in SUPPORTED:
        res["error"] = f"unsupported extension {path.suffix}"
        return res

    # ---- routing: starting drafts never enter the warehouse
    if draft is None and "작성중" in path.name and not confirm_warehouse:
        if ask is None:
            res["status"] = "needs_user"
            res["question"] = "작성중 파일: 초안으로 보고서 폴더에 넣을지 확인 필요"
            return res
        ans = ask(f"'{path.name}'은(는) 작성중 초안으로 보입니다. 어떻게 처리할까요?",
                  ["초안(보고서 폴더로)", "참고문서(창고로)"])
        if ans.startswith("초안") or "초안" in ans and "참고" not in ans:
            name = ask("보고서 폴더 이름은?", [])
            draft = (name or "").strip() or None
            if draft is None:
                res["status"] = "needs_user"
                return res
    if draft:
        return _route_draft(path, draft, conn)

    sha = ex.sha256_file(path)
    with tx(conn):
        dup = conn.execute("SELECT id, doc_id, filename FROM files WHERE sha256=?",
                           (sha,)).fetchone()
        if dup:
            _advance_cursor(conn, cursor)
            return {"status": "duplicate", "doc_id": dup["doc_id"], "file_id": dup["id"],
                    "filename": dup["filename"]}

    extracted = ex.extract(path)
    text = extracted.get("markdown") or ""
    ok = bool(extracted.get("ok"))
    ms = markers.extract_marker_system(text) if ok else None
    lines = markers.parse_lines(text, ms) if ok else []
    toks, heading_norm, title_line, is_att = _group_tokens(lines, text) if ok else (set(), "", "", False)
    version_date = (markers.author_date(text) if ok else None) or _iso(document_date)
    text_hash = norm_text_hash(text) if ok else None
    inst = _safe_part(institution) if institution else "미상"

    with tx(conn):
        # re-check (another writer may have inserted the same hash meanwhile)
        dup = conn.execute("SELECT id, doc_id, filename FROM files WHERE sha256=?",
                           (sha,)).fetchone()
        if dup:
            _advance_cursor(conn, cursor)
            return {"status": "duplicate", "doc_id": dup["doc_id"], "file_id": dup["id"],
                    "filename": dup["filename"]}
        status = "added"
        target = None
        match = (conn.execute("SELECT f.doc_id, d.institution FROM files f JOIN documents d "
                              "ON d.doc_id=f.doc_id WHERE f.text_hash=? ORDER BY f.id",
                              (text_hash,)).fetchone() if text_hash else None)
        if match:
            if (doc_id and doc_id != match["doc_id"]) or (not doc_id and match["institution"] != inst):
                _advance_cursor(conn, cursor)
                return {"status": "duplicate", "doc_id": match["doc_id"], "file_id": None,
                        "filename": path.name}
            target = match["doc_id"]
            status = "version"
        elif doc_id:
            target = doc_id
        else:
            verdict, joined, cands = _group(conn, inst, toks, heading_norm, is_att, title_line, ask,
                                            source_url=source_url,
                                            item_title=(title if source_url and title else "")
                                            or _item_title(path.name))
            if verdict == "join":
                target = joined
            elif verdict == "ask":
                if ask is None:
                    return {"status": "needs_user", "doc_id": None, "file_id": None,
                            "filename": path.name,
                            "question": "문서 ID 그룹 확인 필요",
                            "candidates": [c[0] for c in cands]}
                titles = {c[0]: conn.execute("SELECT title FROM documents WHERE doc_id=?",
                                             (c[0],)).fetchone()["title"] for c in cands}
                opts = [f"{c[0]}: {titles[c[0]]} (유사도 {c[1]:.2f})" for c in cands]
                opts.append("새 문서로 입고")
                ans = ask(f"'{path.name}'(제목 '{title_line or heading_norm}')을(를) 기존 문서 ID에 "
                          f"포함할까요, 새 문서로 입고할까요?", opts)
                target = _match_choice(ans, opts, [c[0] for c in cands])
        new_doc = not conn.execute("SELECT 1 FROM documents WHERE doc_id=?",
                                   (target,)).fetchone() if target else True
        if not new_doc:
            status = "version"
        if target is None:
            target = _new_doc_id(conn)

        # ---- document row
        explicit = title if title and not _is_generic_title(_title_norm(title)) else ""
        disp_title = explicit or (_file_item_title(path.name) if source_url else "") \
            or title or (_body_title(title_line) if source_url else _clean_title(title_line)) or _clean_title(
                next((ln["body"] for ln in lines if ln["kind"] in ("heading", "attachment")), "")) \
            or path.stem
        if new_doc:
            dtype = doc_type or auto_doc_type(disp_title)
            dclass = institution_class or auto_inst_class(institution or "")
            needs_check = 0
            if not dtype or not dclass:
                needs_check = 1
            dtype = dtype or "기타"
            dclass = dclass or "기타"
            folder = conn.root / "references" / dtype / dclass
            conn.execute(
                "INSERT INTO documents(doc_id,title,institution,institution_class,doc_type,"
                "needs_classification_check,source,source_url,document_date,collection_date,"
                "folder_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (target, disp_title, inst, dclass, dtype, needs_check, source, source_url,
                 _iso(document_date), today(), rel_path(conn, folder)))
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (target,)).fetchone()
        folder = abs_path(conn, doc["folder_path"])

        # ---- file naming + copies
        d8 = _date8(version_date) or _date8(document_date)
        date_part = d8 if d8 else today().replace("-", "") + "x"
        name_title = disp_title
        if not new_doc and not title and title_line and title_consistency(title_line, text) < 0.8:
            name_title = doc["title"] or disp_title  # the file's own title does not match its body
        base = f"{date_part}_{doc['institution']}_{doc['doc_type']}_{_title_summary(name_title, path.stem)}"
        ext_ = path.suffix.lower().lstrip(".")
        n, filename = 1, None
        while True:
            filename = f"{base}.{ext_}" if n == 1 else f"{base}_{n}.{ext_}"
            md_name = Path(filename).stem + ".md"
            # the derived extract (<stem>.md) must be unique too: same-stem originals of
            # different extensions (pdf vs hwp) would otherwise share one extract
            if not conn.execute("SELECT 1 FROM files WHERE filename=? OR md_path=?",
                                (filename, rel_path(conn, folder / md_name))).fetchone() \
                    and not (folder / "원본" / filename).exists() \
                    and not (folder / md_name).exists():
                break
            n += 1
        orig_dir = folder / "원본"
        orig_dir.mkdir(parents=True, exist_ok=True)
        dest = orig_dir / filename
        md_dest = folder / md_name
        created: list[Path] = []
        trace: dict = {"old": [], "new": []}
        try:
            shutil.copyfile(path, dest)
            created.append(dest)
            md_dest.write_text(text, encoding="utf-8")
            created.append(md_dest)
            cur = conn.execute(
                "INSERT INTO files(doc_id,filename,file_path,md_path,role,version_date,sha256,"
                "text_hash,pages,scanned,original_deleted,source_url,collection_date,extract_ok,"
                "ocr_line_count,low_conf_count,is_attachment,heading_norm,tokens,title_line,"
                "original_name,item_title) VALUES(?,?,?,?,?,?,?,?,?,?,0,?,?,?,?,?,?,?,?,?,?,?)",
                (target, filename, rel_path(conn, dest), rel_path(conn, md_dest), "본문",
                 version_date, sha, text_hash, extracted.get("page_count") or 0,
                 1 if extracted.get("scanned") else 0, source_url, today(), 1 if ok else 0,
                 len(extracted.get("ocr_lines") or []), len(extracted.get("low_conf") or []),
                 1 if is_att else 0, heading_norm, json.dumps(sorted(toks), ensure_ascii=False),
                 title_line, path.name, (title or None) if source_url else None))
            file_id = cur.lastrowid
            _advance_cursor(conn, cursor)
            rebuild_summary(conn, target, _hint=report_like_hint, _trace=trace)
        except BaseException:
            for p in created + trace["new"]:
                p.unlink(missing_ok=True)
            raise
    for p in trace["old"]:
        p.unlink(missing_ok=True)   # old summary goes only after the commit
    return {"status": status, "doc_id": target, "file_id": file_id, "filename": filename}


# ---------------------------------------------------------------- summary / roles

def _outline(lines: list[dict]) -> list[dict]:
    out = []
    for ln in lines:
        if ln["kind"] in ("title", "heading", "attachment"):
            out.append({"kind": ln["kind"], "level": ln["level"], "text": ln["body"]})
    return out


def _reference_files(conn, doc_id):
    return conn.execute(
        "SELECT * FROM files WHERE doc_id=? AND role IN ('본문','참고') "
        "ORDER BY CASE role WHEN '본문' THEN 0 ELSE 1 END, id", (doc_id,)).fetchall()


def reference_hash(conn, doc_id: str) -> str:
    h = hashlib.sha256()
    for f in _reference_files(conn, doc_id):
        p = abs_path(conn, f["md_path"])
        h.update(f["role"].encode())
        h.update(p.read_bytes() if p.exists() else b"")
    return h.hexdigest()


def title_consistency(title: str, body: str) -> float:
    """Share of the title's nouns that occur in the body (a wrong title scores low)."""
    tn = style.nouns(title or "")
    if not tn:
        return 0.0
    return len(tn & style.nouns(_body_without_title(body))) / len(tn)


def _best_title(conn, bodies, main, current: str = "") -> str:
    if main["source_url"]:
        if current and not _is_generic_title(_title_norm(current)):
            return current  # collector/item title chosen at ingest stays
        item = _file_item_title(main["original_name"] or "")
        if item:
            return item
    best, best_key = "", None
    for f in bodies:
        title = _body_title(f["title_line"] or "") if f["source_url"] else _clean_title(f["title_line"] or "")
        if not title:
            continue
        p = abs_path(conn, f["md_path"])
        body = p.read_text(encoding="utf-8", errors="replace") if p.exists() else ""
        key = (round(title_consistency(title, body), 3), f["version_date"] or "", f["id"])
        if best_key is None or key > best_key:
            best, best_key = title, key
    if best:
        return best
    return (_body_title(main["title_line"] or "") if main["source_url"]
            else _clean_title(main["title_line"] or ""))


def rebuild_summary(conn, doc_id: str, _hint=None, _trace: dict | None = None) -> dict:
    """Recompute roles, quality, per-ID summary yaml and stale report reference caches.

    The superseded summary file is removed only after the transaction commits; with a caller
    owned ``_trace`` (``old``/``new`` path lists) the caller removes it after its own commit
    and cleans ``new`` on failure."""
    from . import quality
    nested = conn.in_transaction
    trace = _trace if _trace is not None else {"old": [], "new": []}
    with tx(conn):
        files = conn.execute("SELECT * FROM files WHERE doc_id=? ORDER BY id", (doc_id,)).fetchall()
        bodies = [f for f in files if not f["is_attachment"]]
        main_id = max(bodies, key=lambda f: (f["version_date"] or "", f["id"]))["id"] if bodies else None
        for f in files:
            role = "참고" if f["is_attachment"] else ("본문" if f["id"] == main_id else "판본")
            conn.execute("UPDATE files SET role=? WHERE id=?", (role, f["id"]))
        result = quality.score_document(conn, doc_id, store=False)
        if _hint is not None:
            result["report_like"] = bool(_hint) and result.get("report_like", False)
        doc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
        main = next((f for f in files if f["id"] == main_id), files[0] if files else None)
        updates = {"quality_score": result["score"], "unmeasurable": 1 if result["unmeasurable"] else 0,
                   "report_like": 1 if (result.get("report_like") and not result["unmeasurable"]) else 0}
        if main is not None:
            updates["title"] = _best_title(conn, bodies, main, doc["title"] or "") or doc["title"]
            if main["version_date"]:
                updates["document_date"] = main["version_date"]
        folder = abs_path(conn, doc["folder_path"])
        summary_rel = None
        if main is not None:
            summary_p = folder / (Path(main["filename"]).stem + ".summary.yaml")
            summary_rel = rel_path(conn, summary_p)
            old = doc["summary_path"]
            if old and old != summary_rel:
                trace["old"].append(abs_path(conn, old))
            main_text = abs_path(conn, main["md_path"]).read_text(encoding="utf-8", errors="replace") \
                if abs_path(conn, main["md_path"]).exists() else ""
            ms = result.get("marker_system")
            lines = markers.parse_lines(main_text, ms)
            summary = {
                "doc_id": doc_id,
                "title": updates.get("title", doc["title"]),
                "institution": doc["institution"],
                "institution_class": doc["institution_class"],
                "doc_type": doc["doc_type"],
                "source": doc["source"],
                "source_url": doc["source_url"],
                "document_date": updates.get("document_date", doc["document_date"]),
                "outline": _outline(lines),
                "marker_system": ms,
                "style": result["style"]["kind"],
                "ending": result["style"]["ending"],
                "quality_score": result["score"],
                "unmeasurable": result["unmeasurable"],
                "files": [{"filename": f["filename"],
                           "role": "참고" if f["is_attachment"] else ("본문" if f["id"] == main_id else "판본"),
                           "version_date": f["version_date"], "scanned": bool(f["scanned"]),
                           "pages": f["pages"]} for f in files],
            }
            summary_p.parent.mkdir(parents=True, exist_ok=True)
            if not summary_p.exists():
                trace["new"].append(summary_p)
            summary_p.write_text(yaml.safe_dump(summary, allow_unicode=True, sort_keys=False),
                                 encoding="utf-8")
            updates["summary_path"] = summary_rel
        sets = ", ".join(f"{k}=?" for k in updates)
        conn.execute(f"UPDATE documents SET {sets} WHERE doc_id=?", (*updates.values(), doc_id))
    if _trace is None and not nested:
        for p in trace["old"]:
            p.unlink(missing_ok=True)
    refresh_report_references(conn, doc_id)
    return result


def repair_titles(conn, dry_run: bool = False) -> list[dict]:
    """Give collected documents with a generic title the item title of their main file.

    Only documents.title (plus the rebuilt summary/score) changes; files on disk keep their names.
    One transaction; returns [{"doc_id", "old", "new"}]."""
    changes = []
    for d in conn.execute("SELECT doc_id, title FROM documents ORDER BY doc_id").fetchall():
        if not _is_generic_title(_title_norm(d["title"] or "")):
            continue
        files = conn.execute("SELECT * FROM files WHERE doc_id=? AND source_url IS NOT NULL "
                             "AND source_url<>'' AND is_attachment=0", (d["doc_id"],)).fetchall()
        if not files:
            continue
        main = max(files, key=lambda f: (f["role"] == "본문", f["version_date"] or "", f["id"]))
        new = _fdisplay(main)
        if new and new != d["title"]:
            changes.append({"doc_id": d["doc_id"], "old": d["title"], "new": new})
    seen = {c["doc_id"] for c in changes}
    for d in conn.execute("SELECT doc_id, title FROM documents ORDER BY doc_id").fetchall():
        if d["doc_id"] in seen:
            continue
        files = conn.execute("SELECT * FROM files WHERE doc_id=? AND source_url IS NOT NULL "
                             "AND source_url<>'' AND is_attachment=0", (d["doc_id"],)).fetchall()
        titles = {_fdisplay(f) for f in files}
        if len(titles) == 1:
            new = titles.pop()
            if new and not _titles_match(new, d["title"] or ""):
                changes.append({"doc_id": d["doc_id"], "old": d["title"], "new": new})
    if dry_run or not changes:
        return changes
    trace = {"old": [], "new": []}
    try:
        with tx(conn):
            for c in changes:
                conn.execute("UPDATE documents SET title=? WHERE doc_id=?", (c["new"], c["doc_id"]))
                rebuild_summary(conn, c["doc_id"], _trace=trace)
    except BaseException:
        for p in trace["new"]:
            p.unlink(missing_ok=True)
        raise
    for p in trace["old"]:
        p.unlink(missing_ok=True)
    return changes



def write_reference_folder(conn, doc_id: str, dest: Path) -> dict:
    """Write reference extracts + marker_system.yaml + summary.yaml for a report folder."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for old in dest.glob("*.md"):
        old.unlink()
    files = _reference_files(conn, doc_id)
    texts = []
    for f in files:
        p = abs_path(conn, f["md_path"])
        t = p.read_text(encoding="utf-8") if p.exists() else ""
        texts.append(t)
        (dest / f"{f['role']}_{Path(f['filename']).stem}.md").write_text(t, encoding="utf-8")
    main_text = texts[0] if texts else ""
    ms = markers.extract_marker_system(main_text) or markers.extract_marker_system("\n".join(texts))
    (dest / "marker_system.yaml").write_text(
        yaml.safe_dump(ms, allow_unicode=True, sort_keys=False), encoding="utf-8")
    doc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (doc_id,)).fetchone()
    summ = {}
    if doc and doc["summary_path"] and abs_path(conn, doc["summary_path"]).exists():
        summ = yaml.safe_load(abs_path(conn, doc["summary_path"]).read_text(encoding="utf-8")) or {}
    (dest / "summary.yaml").write_text(yaml.safe_dump(summ, allow_unicode=True, sort_keys=False),
                                       encoding="utf-8")
    return {"text_hash": reference_hash(conn, doc_id), "marker_system": ms}


def refresh_report_references(conn, doc_id: str) -> list[str]:
    """Rebuild reports/<name>/reference caches whose chosen reference is this document ID."""
    rdir = conn.root / "reports"
    refreshed = []
    if not rdir.is_dir():
        return refreshed
    current = reference_hash(conn, doc_id)
    for sp in sorted(rdir.glob("*/state.yaml")):
        try:
            st = yaml.safe_load(sp.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        if st.get("chosen_reference_id") != doc_id or st.get("reference_text_hash") == current:
            continue
        info = write_reference_folder(conn, doc_id, sp.parent / "reference")
        st["reference_text_hash"] = info["text_hash"]
        sp.write_text(yaml.safe_dump(st, allow_unicode=True, sort_keys=False), encoding="utf-8")
        refreshed.append(sp.parent.name)
    return refreshed


# ---------------------------------------------------------------- misc API

def set_star(conn, doc_id: str, on: bool = True) -> bool:
    with tx(conn):
        cur = conn.execute("UPDATE documents SET starred=? WHERE doc_id=?", (1 if on else 0, doc_id))
    return cur.rowcount > 0


def list_documents(conn) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY doc_id").fetchall()]


def list_files(conn, doc_id: str | None = None) -> list[dict]:
    q = "SELECT * FROM files" + (" WHERE doc_id=?" if doc_id else "") + " ORDER BY id"
    return [dict(r) for r in conn.execute(q, (doc_id,) if doc_id else ()).fetchall()]


def _clusters(files: list) -> list[list]:
    """Group collected files (source_url set) that belong together: same source_url or same
    non-generic title.  Ordered by lowest file id."""
    parent = list(range(len(files)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(files)):
        for j in range(i):
            if (files[i]["source_url"] == files[j]["source_url"]
                    or _titles_match(_ftitle(files[i]), _ftitle(files[j]))):
                parent[find(i)] = find(j)
    groups: dict[int, list] = {}
    for i, f in enumerate(files):
        groups.setdefault(find(i), []).append(f)
    return sorted(groups.values(), key=lambda g: min(f["id"] for f in g))


def split_misgrouped(conn, dry_run: bool = False) -> list[dict]:
    """Move wrongly merged collected files into documents of their own.

    Manual joins are told apart by ``files.source_url``: files ingested through the collector
    carry one, files ingested by hand (``bogoseo ingest --doc-id``) do not, and the latter
    always stay in their document.  Among the collected files of one document, those sharing a
    source_url or a close non-generic title stay together; the cluster holding the lowest
    file id keeps the document ID, every other cluster becomes a new document.
    Returns one record per moved file: file_id, filename, from_doc, to_doc, title."""
    plan = []   # (old_doc, [files of one cluster])
    for d in conn.execute("SELECT doc_id FROM documents ORDER BY doc_id").fetchall():
        coll = conn.execute("SELECT * FROM files WHERE doc_id=? AND source_url IS NOT NULL "
                            "AND source_url<>'' ORDER BY id", (d["doc_id"],)).fetchall()
        for cl in _clusters(coll)[1:]:
            plan.append((d["doc_id"], cl))
    if not plan:
        return []
    taken: set[str] = set()

    def alloc():
        n = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] + 1
        while (conn.execute("SELECT 1 FROM documents WHERE doc_id=?", (f"doc_{n:04d}",)).fetchone()
               or f"doc_{n:04d}" in taken):
            n += 1
        taken.add(f"doc_{n:04d}")
        return f"doc_{n:04d}"

    def title_of(f):
        return (f["item_title"] or _item_title(f["original_name"] or "")
                or _clean_title(f["title_line"] or "")
                or Path(f["filename"]).stem)

    out = [{"file_id": f["id"], "filename": f["filename"], "from_doc": old, "to_doc": None,
            "title": title_of(cl[0])} for old, cl in plan for f in cl]
    if dry_run:
        i = 0
        for old, cl in plan:
            new = alloc()
            for _ in cl:
                out[i]["to_doc"] = new
                i += 1
        return out

    moved: list[tuple[Path, Path]] = []
    created_dirs: list[Path] = []
    trace: dict = {"old": [], "new": []}
    try:
        with tx(conn):
            i = 0
            touched = []
            for old, cl in plan:
                odoc = conn.execute("SELECT * FROM documents WHERE doc_id=?", (old,)).fetchone()
                title = title_of(cl[0])
                dtype = auto_doc_type(title)
                new = alloc()
                folder = conn.root / "references" / (dtype or "기타") / odoc["institution_class"]
                conn.execute(
                    "INSERT INTO documents(doc_id,title,institution,institution_class,doc_type,"
                    "needs_classification_check,source,source_url,document_date,collection_date,"
                    "folder_path) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (new, title, odoc["institution"], odoc["institution_class"], dtype or "기타",
                     0 if dtype else 1, odoc["source"], cl[0]["source_url"], cl[0]["version_date"],
                     cl[0]["collection_date"] or today(), rel_path(conn, folder)))
                conn.execute("UPDATE documents SET starred=? WHERE doc_id=?",
                             (odoc["starred"] or 0, new))
                for f in cl:
                    d8 = _date8(f["version_date"])
                    date_part = d8 if d8 else today().replace("-", "") + "x"
                    stem = Path(f["original_name"] or f["filename"]).stem
                    base = f"{date_part}_{odoc['institution']}_{dtype or '기타'}_{_title_summary(title, stem)}"
                    ext_ = Path(f["filename"]).suffix.lower().lstrip(".")
                    n = 1
                    while True:
                        filename = f"{base}.{ext_}" if n == 1 else f"{base}_{n}.{ext_}"
                        md_name = Path(filename).stem + ".md"
                        if not conn.execute("SELECT 1 FROM files WHERE filename=? OR md_path=?",
                                            (filename, rel_path(conn, folder / md_name))).fetchone() \
                                and not (folder / "원본" / filename).exists() \
                                and not (folder / md_name).exists():
                            break
                        n += 1
                    new_orig, new_md = folder / "원본" / filename, folder / md_name
                    for src_rel, dst in ((f["file_path"], new_orig), (f["md_path"], new_md)):
                        src = abs_path(conn, src_rel) if src_rel else None
                        if src is not None and src.exists():
                            if not dst.parent.exists():
                                dst.parent.mkdir(parents=True)
                                created_dirs.append(dst.parent)
                            shutil.move(str(src), str(dst))
                            moved.append((src, dst))
                    conn.execute("UPDATE files SET doc_id=?, filename=?, file_path=?, md_path=? "
                                 "WHERE id=?", (new, filename, rel_path(conn, new_orig),
                                                rel_path(conn, new_md), f["id"]))
                    conn.execute("UPDATE deletion_journal SET doc_id=? WHERE file_id=?",
                                 (new, f["id"]))
                    conn.execute("UPDATE deletion_journal SET file_path=? WHERE file_id=? "
                                 "AND unlinked_at IS NULL", (rel_path(conn, new_orig), f["id"]))
                    out[i]["to_doc"], out[i]["filename"] = new, filename
                    i += 1
                touched += [old, new]
                rest = [f for f in conn.execute(
                    "SELECT * FROM files WHERE doc_id=? AND source_url IS NOT NULL "
                    "AND source_url<>'' AND is_attachment=0", (old,)).fetchall()]
                if rest:
                    main = max(rest, key=lambda f: (f["role"] == "본문", f["version_date"] or "",
                                                    f["id"]))
                    keep = _fdisplay(main)
                    if keep and keep != odoc["title"]:
                        conn.execute("UPDATE documents SET title=? WHERE doc_id=?", (keep, old))
            for did in dict.fromkeys(touched):
                rebuild_summary(conn, did, _trace=trace)
    except BaseException:
        for src, dst in reversed(moved):
            if dst.exists():
                shutil.move(str(dst), str(src))
        for p in trace["new"]:
            p.unlink(missing_ok=True)
        for p in reversed(created_dirs):
            with contextlib.suppress(OSError):
                p.rmdir()
        raise
    for p in trace["old"]:
        p.unlink(missing_ok=True)
    return out


def _backfill_item_titles(conn) -> int:
    """One-time fill of files.item_title for rows collected before the column existed.

    A document whose collected files share one source_url gets its documents.title; a document
    holding several source_urls (the title belongs to only one of them) gets, per file, the title
    part of the stored file name ``<date>_<institution>_<type>_<title>``."""
    n = 0
    for d in conn.execute("SELECT doc_id, title FROM documents").fetchall():
        coll = conn.execute("SELECT id, filename, source_url FROM files WHERE doc_id=? AND "
                            "source_url IS NOT NULL AND source_url<>'' AND item_title IS NULL",
                            (d["doc_id"],)).fetchall()
        single = len({f["source_url"] for f in coll}) == 1
        for f in coll:
            parts = Path(f["filename"] or "").stem.split("_", 3)
            t = d["title"] if single else (parts[3].replace("_", " ") if len(parts) == 4 else None)
            if t:
                conn.execute("UPDATE files SET item_title=? WHERE id=?", (t, f["id"]))
                n += 1
    return n
