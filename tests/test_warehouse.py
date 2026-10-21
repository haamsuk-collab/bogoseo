import hashlib
import re

import pytest
import yaml

from bogoseo import warehouse
from conftest import INBOX, REPO

pytestmark = pytest.mark.skipif(not (INBOX / "표본_개선안_1015.pdf").exists(),
                                reason="real inbox PDFs missing")

DRAFT_REPORT = "표본_선발전형_출제방식_개선안"
FILENAME_RE = warehouse.FILENAME_RE


def sha_all(d):
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.iterdir())}


def ingest_inbox(conn, asked):
    def ask(question, options):
        asked.append((question, options))
        return options[0]  # best candidate / first option

    results = []
    for p in sorted(INBOX.glob("*.pdf")):
        kw = {"draft": DRAFT_REPORT} if "작성중" in p.name else {}
        results.append(warehouse.ingest_file(
            p, conn=conn, institution="○○본부", institution_class="국방", ask=ask, **kw))
    return results


def test_ingest_real_inbox(tmp_root):
    before = sha_all(INBOX)
    conn = warehouse.connect()
    asked = []
    results = ingest_inbox(conn, asked)
    by_name = {r["filename"]: r for r in results}

    # starting draft routed to the report folder, no warehouse row
    draft_res = next(r for r in results if r["status"] == "draft")
    folder = tmp_root / "reports" / DRAFT_REPORT / "starting_draft"
    assert (folder / "초안_개선안_작성중.pdf").exists() and (folder / "extract.md").exists()
    text = (folder / "extract.md").read_text(encoding="utf-8")
    assert "□" in text and sum("가" <= ch <= "힣" for ch in text) >= 300
    assert draft_res["doc_id"] is None
    assert conn.execute("SELECT COUNT(*) FROM files WHERE original_name LIKE '%작성중%'").fetchone()[0] == 0

    # one document ID for the four 표본 PDFs
    docs = warehouse.list_documents(conn)
    assert len(docs) == 1
    doc = docs[0]
    files = warehouse.list_files(conn, doc["doc_id"])
    assert len(files) == 4 and {f["original_name"] for f in files} == {
        p.name for p in INBOX.glob("표본*.pdf")}
    roles = {f["original_name"]: f["role"] for f in files}
    assert roles["표본_개선안_1022_본문.pdf"] == "본문"          # latest-dated body
    assert roles["표본_개선안_1015.pdf"] == "판본"
    assert roles["표본_개선안_참고자료_스캔.pdf"] == "참고"
    assert roles["표본_개선안_참고자료_디지털.pdf"] == "참고"
    assert any(r["status"] == "version" and r["filename"].startswith("2026102") for r in results)
    assert asked, "the grouping question must have been asked for the 참고 files"

    # folder + naming
    assert (doc["doc_type"], doc["institution_class"]) == ("개선안", "국방")
    for f in files:
        assert FILENAME_RE.match(f["filename"]), f["filename"]
        assert "_○○본부_" in f["filename"]
        assert f["file_path"].startswith("references/개선안/국방/원본/")
        assert (tmp_root / f["file_path"]).exists()
        md = tmp_root / f["md_path"]
        assert md.exists() and md.read_text(encoding="utf-8").strip()
        assert md.parent == tmp_root / "references" / "개선안" / "국방"
    assert any(f["filename"].startswith("20261015_○○본부_개선안_") for f in files)
    # version date from the author line, scanned flag, hashes, pages
    f1015 = next(f for f in files if f["original_name"] == "표본_개선안_1015.pdf")
    assert f1015["version_date"] == "2026-10-15" and f1015["scanned"] == 1 and f1015["pages"] == 3
    assert len(f1015["sha256"]) == 64 and len(f1015["text_hash"]) == 64
    dig = next(f for f in files if "디지털" in f["original_name"])
    assert dig["scanned"] == 0 and dig["pages"] == 4

    # per-ID summary + index metadata
    sp = tmp_root / doc["summary_path"]
    assert sp.exists() and sp.name.endswith(".summary.yaml")
    summ = yaml.safe_load(sp.read_text(encoding="utf-8"))
    assert summ["doc_id"] == doc["doc_id"] and summ["outline"] and summ["marker_system"]["levels"]
    assert summ["institution"] == "○○본부" and summ["doc_type"] == "개선안"
    assert {x["role"] for x in summ["files"]} == {"본문", "참고", "판본"}
    assert doc["quality_score"] is not None and doc["report_like"] == 1
    assert doc["collection_date"] and doc["needs_classification_check"] == 0

    # inbox untouched (copy-only)
    assert sha_all(INBOX) == before

    # re-ingest: duplicates, row counts unchanged
    n_docs = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
    n_files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    again = ingest_inbox(conn, asked)
    assert [r["status"] for r in again if r["status"] != "draft"] == ["duplicate"] * 4
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == n_docs
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == n_files
    assert sha_all(INBOX) == before


def test_filename_regex_cases():
    ok = ["20261015_○○본부_개선안_표본선발제도.pdf", "20261020x_국방부_기타_제목_2.hwpx"]
    bad = ["2026_○○본부_개선안_제목.pdf", "20261015_○○본부_메모_제목.pdf",
           "20261015_○○본부_개선안_제 목.pdf", "20261015_○○본부_개선안_" + "가" * 41 + ".pdf"]
    assert all(FILENAME_RE.match(n) for n in ok)
    assert not any(FILENAME_RE.match(n) for n in bad)


def test_unknown_date_and_classification_fallback(tmp_root, tmp_path):
    src = tmp_path / "x.md"
    src.write_text("무제 자료\n\n일반 문장입니다.\n", encoding="utf-8")
    conn = warehouse.connect()
    r = warehouse.ingest_file(src, conn=conn, institution="어떤기관")
    assert r["status"] == "added" and FILENAME_RE.match(r["filename"])
    assert re.match(r"^\d{8}x_어떤기관_기타_", r["filename"])
    d = warehouse.list_documents(conn)[0]
    assert (d["doc_type"], d["institution_class"], d["needs_classification_check"]) == ("기타", "기타", 1)
    # collision gets _2
    src2 = tmp_path / "y.md"
    src2.write_text("무제 자료\n\n다른 문장입니다 완전히.\n", encoding="utf-8")
    r2 = warehouse.ingest_file(src2, conn=conn, institution="어떤기관", doc_id=r["doc_id"])
    assert r2["filename"].endswith("_2.md")


def test_needs_user_without_ask_and_cursor_transaction(tmp_root, tmp_path):
    conn = warehouse.connect()
    a = tmp_path / "작성중_초안.md"
    a.write_text("초안\n", encoding="utf-8")
    assert warehouse.ingest_file(a, conn=conn)["status"] == "needs_user"
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 0
    b = tmp_path / "b.md"
    b.write_text("□ 가\nㅇ 나\n- 다 필요\n", encoding="utf-8")
    r = warehouse.ingest_file(b, conn=conn, institution="기관", cursor=("src1", "42"))
    assert r["status"] == "added" and warehouse.get_cursor(conn, "src1") == "42"
    # a duplicate still advances the cursor
    r = warehouse.ingest_file(b, conn=conn, institution="기관", cursor=("src1", "43"))
    assert r["status"] == "duplicate" and warehouse.get_cursor(conn, "src1") == "43"


def test_text_dup_vs_version(tmp_root, tmp_path):
    conn = warehouse.connect()
    a = tmp_path / "a.md"
    a.write_text("□ 가\nㅇ 나\n- 다 필요\n", encoding="utf-8")
    b = tmp_path / "b.md"  # same normalized text, different bytes
    b.write_text("□ 가\n\nㅇ 나\n\n- 다  필요\n", encoding="utf-8")
    ra = warehouse.ingest_file(a, conn=conn, institution="기관")
    rb = warehouse.ingest_file(b, conn=conn, institution="기관")
    assert rb["status"] == "version" and rb["doc_id"] == ra["doc_id"]
    c = tmp_path / "c.md"
    c.write_text("□ 가\nㅇ 나\n- 다 필요\n\n", encoding="utf-8")
    rc = warehouse.ingest_file(c, conn=conn, institution="다른기관")
    assert rc["status"] == "duplicate"   # same text under a different document ID


def test_same_stem_different_extension_gets_own_extract(tmp_root, tmp_path):
    conn = warehouse.connect()
    a = tmp_path / "a" / "공문.md"
    a.parent.mkdir()
    a.write_text("같은 제목 문서\n\n□ 가\nㅇ 나\n- 첫째 본문 필요\n", encoding="utf-8")
    b = tmp_path / "b" / "공문.txt"
    b.parent.mkdir()
    b.write_text("같은 제목 문서\n\n□ 다\nㅇ 라\n- 완전히 다른 둘째 본문\n", encoding="utf-8")
    ra = warehouse.ingest_file(a, conn=conn, institution="기관", document_date="2026-09-01")
    rb = warehouse.ingest_file(b, conn=conn, institution="기관", doc_id=ra["doc_id"],
                               document_date="2026-09-01")
    fa, fb = (conn.execute("SELECT * FROM files WHERE id=?", (r["file_id"],)).fetchone()
              for r in (ra, rb))
    assert fa["md_path"] != fb["md_path"]
    assert "첫째" in warehouse.abs_path(conn, fa["md_path"]).read_text(encoding="utf-8")
    assert "둘째" in warehouse.abs_path(conn, fb["md_path"]).read_text(encoding="utf-8")


def test_version_ingest_keeps_old_summary_until_commit(tmp_root, tmp_path, monkeypatch):
    conn = warehouse.connect()
    a = tmp_path / "v1.md"
    a.write_text("제목 문서\n\n□ 가\nㅇ 나\n- 첫째 본문 필요\n", encoding="utf-8")
    ra = warehouse.ingest_file(a, conn=conn, institution="기관", document_date="2026-09-01")
    old = warehouse.abs_path(conn, conn.execute(
        "SELECT summary_path FROM documents WHERE doc_id=?", (ra["doc_id"],)).fetchone()[0])
    assert old.exists()
    b = tmp_path / "v2.md"
    b.write_text("제목 문서\n\n□ 가\nㅇ 나\n- 둘째 본문 필요 수정\n", encoding="utf-8")

    def boom(*a, **k):
        raise RuntimeError("fail after summary written")

    real = warehouse.refresh_report_references
    monkeypatch.setattr(warehouse, "refresh_report_references", boom)
    with pytest.raises(RuntimeError):
        warehouse.ingest_file(b, conn=conn, institution="기관", doc_id=ra["doc_id"],
                              document_date="2026-10-01")
    assert old.exists()                                        # failure keeps the old summary
    assert conn.execute("SELECT COUNT(*) FROM files").fetchone()[0] == 1
    assert len(list(old.parent.glob("*.summary.yaml"))) == 1   # no orphan new summary
    assert len(list(old.parent.glob("*.md"))) == 1             # failed ingest left no extract

    monkeypatch.setattr(warehouse, "refresh_report_references", real)
    rb = warehouse.ingest_file(b, conn=conn, institution="기관", doc_id=ra["doc_id"],
                               document_date="2026-10-01")
    new = warehouse.abs_path(conn, conn.execute(
        "SELECT summary_path FROM documents WHERE doc_id=?", (rb["doc_id"],)).fetchone()[0])
    assert new.exists() and new != old and not old.exists()


def _collect(conn, tmp_path, name, text, url, **kw):
    p = tmp_path / name
    p.parent.mkdir(exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return warehouse.ingest_file(p, conn=conn, institution="정보공개포털", source="포털",
                                 source_url=url, document_date="2026-09-01", **kw)


GENERIC_A = "알파 사업 추진 현황 점검 결과\n\n첨부파일\n\n□ 가\nㅇ 나\n- 알파 사업 필요 내용\n"
GENERIC_B = "베타 제도 개선 방안 검토 보고\n\n첨부파일\n\n□ 가\nㅇ 나\n- 베타 제도 필요 내용\n"


def test_generic_heading_different_title_makes_new_documents(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "a.md", GENERIC_A, "https://x/1")
    rb = _collect(conn, tmp_path, "b.md", GENERIC_B, "https://x/2")
    assert ra["status"] == "added" and rb["status"] == "added"
    assert ra["doc_id"] != rb["doc_id"]
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2


def test_collected_same_title_new_version_still_version(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "a.md", GENERIC_A, "https://x/1")
    rb = _collect(conn, tmp_path, "v2/a.md", GENERIC_A.replace("필요 내용", "필요 수정 내용"),
                  "https://x/9")
    assert rb["status"] == "version" and rb["doc_id"] == ra["doc_id"]


def test_empty_title_never_matches(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "a.md", "일반 문장입니다 하나.\n", "https://x/1")
    rb = _collect(conn, tmp_path, "b.md", "일반 문장입니다 둘.\n", "https://x/2")
    assert ra["doc_id"] != rb["doc_id"]


def test_split_misgrouped_leaves_manual_join(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "a.md", GENERIC_A, "https://x/1")
    rb = _collect(conn, tmp_path, "b.md", GENERIC_B, "https://x/2")
    # seed the old bug: b merged into a's document
    doc_a, doc_b = ra["doc_id"], rb["doc_id"]
    old_b = conn.execute("SELECT file_path, md_path FROM files WHERE id=?", (rb["file_id"],)).fetchone()
    conn.execute("UPDATE files SET doc_id=? WHERE id=?", (doc_a, rb["file_id"]))
    conn.execute("DELETE FROM documents WHERE doc_id=?", (doc_b,))
    # a manual --doc-id join (no source_url) of an unrelated-looking file
    m = tmp_path / "m.md"
    m.write_text("전혀 다른 수기 문서\n\n□ 가\nㅇ 나\n- 수기 필요 내용\n", encoding="utf-8")
    rm = warehouse.ingest_file(m, conn=conn, institution="정보공개포털", doc_id=doc_a)
    assert rm["doc_id"] == doc_a

    plan = warehouse.split_misgrouped(conn, dry_run=True)
    assert [(p["file_id"], p["from_doc"]) for p in plan] == [(rb["file_id"], doc_a)]
    assert conn.execute("SELECT doc_id FROM files WHERE id=?", (rb["file_id"],)).fetchone()[0] == doc_a

    moves = warehouse.split_misgrouped(conn)
    assert len(moves) == 1 and moves[0]["file_id"] == rb["file_id"]
    new = moves[0]["to_doc"]
    assert new != doc_a
    f = conn.execute("SELECT * FROM files WHERE id=?", (rb["file_id"],)).fetchone()
    assert f["doc_id"] == new and warehouse.abs_path(conn, f["file_path"]).exists()
    assert warehouse.abs_path(conn, f["md_path"]).exists()
    assert conn.execute("SELECT doc_id FROM files WHERE id=?", (rm["file_id"],)).fetchone()[0] == doc_a
    for d in (doc_a, new):
        row = conn.execute("SELECT * FROM documents WHERE doc_id=?", (d,)).fetchone()
        assert row["summary_path"] and warehouse.abs_path(conn, row["summary_path"]).exists()
    assert warehouse.split_misgrouped(conn) == []


BOILER = "보도시점 배포 이후 배포 2026.10.13.(화)\n\n□ 가\nㅇ 나\n- %s 필요 내용\n"


def test_boilerplate_title_line_different_item_titles_stay_separate(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "261013_[국방부_보도자료]_장관_예비역_단체_방문.md", BOILER % "예비역", "https://x/1")
    rb = _collect(conn, tmp_path, "261016_[국방부_보도자료]_장관_해병대_현장점검.md", BOILER % "해병대", "https://x/2")
    assert ra["doc_id"] != rb["doc_id"]
    # seeded mis-grouping is split again by the repair
    conn.execute("UPDATE files SET doc_id=? WHERE id=?", (ra["doc_id"], rb["file_id"]))
    conn.execute("DELETE FROM documents WHERE doc_id=?", (rb["doc_id"],))
    moves = warehouse.split_misgrouped(conn)
    assert [m["file_id"] for m in moves] == [rb["file_id"]]


JOHAN = "2026. 10. 21. 조간용\n\n□ 가\nㅇ 나\n- %s 필요 내용\n"


def test_collected_item_title_beats_boilerplate_body_line(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect(conn, tmp_path, "서울창포원_일대_공공미술의_명소로_탈바꿈한다.md", JOHAN % "창포원", "https://x/1")
    rb = _collect(conn, tmp_path, "b.md", JOHAN % "다른", "https://x/2",
                  title="한강 야간 관광 확대 계획")
    t = lambda r: conn.execute("SELECT title FROM documents WHERE doc_id=?", (r["doc_id"],)).fetchone()[0]
    assert t(ra) == "서울창포원 일대 공공미술의 명소로 탈바꿈한다"
    assert t(rb) == "한강 야간 관광 확대 계획"


def test_repair_titles_fixes_seeded_generic_title(tmp_root, tmp_path):
    conn = warehouse.connect()
    r = _collect(conn, tmp_path, "서울창포원_일대_공공미술의_명소로_탈바꿈한다.md", JOHAN % "창포원", "https://x/1")
    conn.execute("UPDATE documents SET title=? WHERE doc_id=?", ("2026. 10. 21. 조간용", r["doc_id"]))
    conn.commit()
    dry = warehouse.repair_titles(conn, dry_run=True)
    assert [c["doc_id"] for c in dry] == [r["doc_id"]]
    assert conn.execute("SELECT title FROM documents WHERE doc_id=?", (r["doc_id"],)).fetchone()[0] \
        == "2026. 10. 21. 조간용"
    done = warehouse.repair_titles(conn)
    assert done[0]["new"] == "서울창포원 일대 공공미술의 명소로 탈바꿈한다"
    assert conn.execute("SELECT title FROM documents WHERE doc_id=?", (r["doc_id"],)).fetchone()[0] \
        == done[0]["new"]
    assert warehouse.repair_titles(conn) == []


SAME_NAME = "공개문 전문"


def _collect_titled(conn, tmp_path, sub, text, url, title):
    return _collect(conn, tmp_path, f"{sub}/{SAME_NAME}.md", text, url, title=title)


def test_same_attachment_name_different_item_titles_stay_separate(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra = _collect_titled(conn, tmp_path, "a", GENERIC_A, "https://x/1", "기상청 기관정기감사")
    rb = _collect_titled(conn, tmp_path, "b", GENERIC_B, "https://x/2",
                         "한국교육시설안전원 운영실태 감사")
    assert ra["doc_id"] != rb["doc_id"]
    assert conn.execute("SELECT item_title FROM files WHERE id=?",
                        (rb["file_id"],)).fetchone()[0] == "한국교육시설안전원 운영실태 감사"
    # seeded merge is split by the repair, which also compares persisted item titles
    conn.execute("UPDATE files SET doc_id=? WHERE id=?", (ra["doc_id"], rb["file_id"]))
    conn.execute("DELETE FROM documents WHERE doc_id=?", (rb["doc_id"],))
    assert [m["file_id"] for m in warehouse.split_misgrouped(conn)] == [rb["file_id"]]


def _seed_merged(conn, tmp_path):
    ra = _collect_titled(conn, tmp_path, "a", GENERIC_A, "https://x/1", "기상청 기관정기감사")
    rb = _collect_titled(conn, tmp_path, "b", GENERIC_B, "https://x/2",
                         "한국교육시설안전원 운영실태 감사")
    conn.execute("UPDATE files SET doc_id=? WHERE id=?", (ra["doc_id"], rb["file_id"]))
    conn.execute("DELETE FROM documents WHERE doc_id=?", (rb["doc_id"],))
    return ra, rb


def test_split_misgrouped_new_document_inherits_starred(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra, rb = _seed_merged(conn, tmp_path)
    conn.execute("UPDATE documents SET starred=1 WHERE doc_id=?", (ra["doc_id"],))
    moves = warehouse.split_misgrouped(conn)
    assert len(moves) == 1
    assert conn.execute("SELECT starred FROM documents WHERE doc_id=?",
                        (moves[0]["to_doc"],)).fetchone()[0] == 1


def test_split_misgrouped_updates_pending_journal_path(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra, rb = _seed_merged(conn, tmp_path)
    old = conn.execute("SELECT file_path FROM files WHERE id=?", (rb["file_id"],)).fetchone()[0]
    conn.execute("INSERT INTO deletion_journal(doc_id,file_id,file_path,marked_at) VALUES(?,?,?,?)",
                 (ra["doc_id"], rb["file_id"], old, warehouse.now_iso()))
    conn.execute("INSERT INTO deletion_journal(doc_id,file_id,file_path,marked_at,unlinked_at) "
                 "VALUES(?,?,?,?,?)", (ra["doc_id"], rb["file_id"], "done/x", "t", "t"))
    warehouse.split_misgrouped(conn)
    new = conn.execute("SELECT file_path FROM files WHERE id=?", (rb["file_id"],)).fetchone()[0]
    assert new != old
    rows = conn.execute("SELECT file_path, unlinked_at FROM deletion_journal ORDER BY id").fetchall()
    assert rows[0]["file_path"] == new and rows[1]["file_path"] == "done/x"


def test_split_misgrouped_retitles_source_document(tmp_root, tmp_path):
    conn = warehouse.connect()
    ra, rb = _seed_merged(conn, tmp_path)
    conn.execute("UPDATE documents SET title=? WHERE doc_id=?", ("한국교육시설안전원 운영실태 감사", ra["doc_id"]))
    warehouse.split_misgrouped(conn)
    assert conn.execute("SELECT title FROM documents WHERE doc_id=?", (ra["doc_id"],)).fetchone()[0] \
        == "기상청 기관정기감사"
    conn.execute("UPDATE documents SET title=? WHERE doc_id=?", ("엉뚱한 제목", ra["doc_id"]))
    dry = warehouse.repair_titles(conn, dry_run=True)
    assert [(c["doc_id"], c["new"]) for c in dry] == [(ra["doc_id"], "기상청 기관정기감사")]
    warehouse.repair_titles(conn)
    assert warehouse.repair_titles(conn) == []


def test_repair_titles_prefers_stored_item_title_over_generic_filename(tmp_root, tmp_path):
    conn = warehouse.connect()
    r = _collect_titled(conn, tmp_path, "a", JOHAN % "기상청", "https://x/9", "기상청 기관정기감사")
    conn.execute("UPDATE documents SET title=? WHERE doc_id=?", ("2026. 10. 21. 조간용", r["doc_id"]))
    conn.commit()
    done = warehouse.repair_titles(conn)
    assert [(c["doc_id"], c["new"]) for c in done] == [(r["doc_id"], "기상청 기관정기감사")]
