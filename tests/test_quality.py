import pytest

from bogoseo import quality, warehouse
from conftest import FIXTURES, INBOX

REAL = pytest.mark.skipif(not (INBOX / "표본_개선안_1015.pdf").exists(),
                          reason="real inbox PDFs missing")


def build(tmp_root):
    conn = warehouse.connect()
    ask = lambda q, opts: opts[0]  # noqa: E731
    for p in sorted(INBOX.glob("표본*.pdf")):
        warehouse.ingest_file(p, conn=conn, institution="○○본부", institution_class="국방", ask=ask)
    mixed = warehouse.ingest_file(FIXTURES / "mixed_marker.md", conn=conn, institution="시설과",
                                  institution_class="국방", doc_type="보고")
    article = warehouse.ingest_file(FIXTURES / "article_style.md", conn=conn, institution="소고",
                                    institution_class="민간", doc_type="기타")
    docs = {d["institution"]: d for d in warehouse.list_documents(conn)}
    return conn, docs["○○본부"]["doc_id"], mixed["doc_id"], article["doc_id"]


@REAL
def test_scores_rank_and_rescore_identical(tmp_root):
    conn, real, mixed, article = build(tmp_root)
    first = quality.score_all(conn)
    print("SCORES", {k: first[k] for k in (real, mixed, article)})
    for k in (real, mixed, article):
        r = quality.score_document(conn, k, store=False)
        print(k, r["score"], r["components"], r["style"])
    assert first[real] > first[mixed] > first[article]
    assert all(0 <= v <= 100 for v in first.values())
    assert quality.score_all(conn) == first                     # rescoring identical
    rows = {d["doc_id"]: d for d in warehouse.list_documents(conn)}
    assert rows[real]["quality_score"] == first[real]
    assert rows[real]["report_like"] == 1 and rows[article]["report_like"] == 0
    comp = quality.score_document(conn, article, store=False)["components"]
    assert comp["markers"] == 0 and comp["table"] == 0 and comp["headings"] == 0


@REAL
def test_recommend_order(tmp_root):
    conn, real, mixed, article = build(tmp_root)
    quality.score_all(conn)
    ids = lambda t=None: [d["doc_id"] for d in quality.recommend(conn, t)]  # noqa: E731
    assert ids() == [real, mixed, article]                       # score
    assert ids("보고") == [mixed, real, article]                  # type match beats score
    assert ids("개선안") == [real, mixed, article]
    assert warehouse.set_star(conn, article, True)
    assert ids("보고") == [article, mixed, real]                  # starred first
    assert warehouse.set_star(conn, mixed, True)
    assert ids("보고") == [mixed, article, real]                  # among starred: type, then score
    assert ids("기타")[:2] == [article, mixed]


def test_unmeasurable_rules(tmp_root, tmp_path):
    conn = warehouse.connect()
    good = warehouse.ingest_file(FIXTURES / "mixed_marker.md", conn=conn, institution="시설과",
                                 doc_type="보고", institution_class="국방")
    bad = tmp_path / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 not really a pdf")
    r = warehouse.ingest_file(bad, conn=conn, institution="깨진기관", doc_type="기타",
                              institution_class="민간")
    assert r["status"] == "added"
    d = {x["doc_id"]: x for x in warehouse.list_documents(conn)}
    assert d[r["doc_id"]]["unmeasurable"] == 1 and d[r["doc_id"]]["quality_score"] is None
    assert [x["doc_id"] for x in quality.recommend(conn)][-1] == r["doc_id"]   # sorts last
    # >30% low-confidence lines after vision -> unmeasurable; <=30% stays measurable
    with warehouse.tx(conn):
        conn.execute("UPDATE files SET ocr_line_count=10, low_conf_count=4 WHERE doc_id=?",
                     (good["doc_id"],))
    assert quality.score_document(conn, good["doc_id"])["unmeasurable"] is True
    with warehouse.tx(conn):
        conn.execute("UPDATE files SET low_conf_count=3 WHERE doc_id=?", (good["doc_id"],))
    res = quality.score_document(conn, good["doc_id"])
    assert res["unmeasurable"] is False and res["score"] is not None


def test_score_formula_components():
    ref = (FIXTURES / "mixed_marker.md").read_text(encoding="utf-8")
    r = quality.score_texts(ref, [], body_pages=1)
    c = r["components"]
    assert c["title"] == 10 and c["headings"] == 10 and c["conclusion"] == 0
    assert 0 < c["markers"] < 25 and 0 < c["style"] < 25 and c["table"] == 0
    assert c["length"] == 5 and quality.score_texts(ref, [], body_pages=11)["components"]["length"] == 0
    assert quality.score_texts(ref, [], body_pages=0)["components"]["length"] == 0
    assert quality.score_texts(ref, [], unmeasurable=True)["score"] is None
    assert quality.score_texts("제목\n\n본문만 있는 글입니다.", [])["components"]["numbers"] == 8
