import json

from bogoseo import verify
from collect_fx import FixtureRunner
from bogoseo.collect.fetch import Fetcher
import base64


def _factory(responses):
    def make(source, conn=None):
        name = source.split(":", 1)[1]
        r = responses.get(name)
        return Fetcher(source, settings={"request_interval_s": 0, "retries": 0, "timeouts": {"insane_search_s": 5}},
                       runner=r, sleep=lambda s: None)
    return make


def _raw(status, body):
    return lambda url, t: {"ok": status < 400, "status": status, "last_code": status, "trace": [], "content_type": "",
                           "body_b64": base64.b64encode(body.encode()).decode()}


def test_lines():
    keys = {"data_go_kr": "K", "nabo": "K2", "law_oc": "K3", "nars": "K4"}
    resp = {
        "정책브리핑": FixtureRunner("policy"),
        "국회예산정책처": _raw(401, json.dumps({"code": "INVALID_KEY"})),
        "국회입법조사처": FixtureRunner("assembly"),
        "법제처": FixtureRunner("law"),
        "행정안전부_정부 공문서 AI 학습데이터": FixtureRunner("mois"),
        "재정경제부_보도자료": FixtureRunner("moef"),
        "과학기술정보통신부_보도자료": FixtureRunner("msit"),
    }
    out = {n: line for n, _, line in verify.verify_api(None, keys=keys, fetcher_factory=_factory(resp))}
    assert out["정책브리핑"].endswith("통과")
    assert out["국회예산정책처"].endswith("실패 (401)")
    assert out["국회도서관"].endswith("미검증 + API 키 미입력")
    assert out["열린국회정보"].endswith("미검증 + API 키 미입력")
    assert out["법제처"].endswith("통과")
    assert len(out) == 9


def _verify(name, runner, keys):
    out = {n: line for n, _, line in verify.verify_api(None, keys=keys, fetcher_factory=_factory({name: runner}))}
    return out[name]


def _raw_engine_not_ok(status, body):
    return lambda url, t: {"ok": False, "status": status, "last_code": status, "trace": [], "content_type": "",
                           "body_b64": base64.b64encode(body.encode()).decode()}


def test_law_xml_without_result_code_passes():
    body = "<?xml version='1.0' encoding='UTF-8'?><AdmRulSearch><target>admrul</target><totalCnt>1</totalCnt><admrul/></AdmRulSearch>"
    assert _verify("법제처", _raw(200, body), {"law_oc": "K"}).endswith("통과")


def test_law_404_html_fails():
    assert _verify("법제처", _raw(404, "<html>Not Found</html>"), {"law_oc": "K"}).endswith("실패 (404)")


def test_data_go_kr_result_code_ok_despite_engine_verdict():
    body = "<response><header><resultCode>0</resultCode><resultMsg>NORMAL_SERVICE</resultMsg></header></response>"
    assert _verify("정책브리핑", _raw_engine_not_ok(200, body), {"data_go_kr": "K"}).endswith("통과")


def test_nars_publication_verify_requires_header_code_00():
    assert _verify("국회입법조사처", FixtureRunner("nars"), {"data_go_kr": "K"}).endswith("통과")
    bad = json.dumps({"getResearchReportList": {"header": {"code": "30", "message": "SERVICE KEY IS NOT REGISTERED"}}})
    assert _verify("국회입법조사처", _raw(200, bad), {"data_go_kr": "K"}).endswith("실패 (30)")
    assert _verify("국회입법조사처", _raw(500, "err"), {"data_go_kr": "K"}).endswith("실패 (500)")


_NANET_LIVE = ("<?xml version='1.0' encoding='UTF-8' standalone='yes'?><response><header><resultMsg>NORMAL_CODE</resultMsg>"
               "<resultCode>00</resultCode></header><total>516288</total><recode>"
               "<item><name>제어번호</name><value>KINX2026067688</value></item>"
               "<item><name>기사명</name><value>제주 4⋅3 사건의 기억 = The memory / 탐라문화.</value></item>"
               "<item><name>수록지명</name><value>탐라문화. 제81호 (2026년 3월)</value></item>"
               "<item><name>발행년도</name><value>2026</value></item></recode></response>")


def test_nanet_live_shape_verifies_despite_engine_verdict():
    assert _verify("국회도서관", _raw_engine_not_ok(200, _NANET_LIVE), {"nanet": "K"}).endswith("통과")
    bad = "<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode></cmmMsgHeader></OpenAPI_ServiceResponse>"
    assert _verify("국회도서관", _raw(403, bad), {"nanet": "K"}).endswith("실패 (403)")


def test_nanet_recode_rows_parsed_with_title_and_issue_date():
    from bogoseo.collect import fetch
    from bogoseo.collect.sources import get_adapter
    fx = fetch.Fetcher("국회도서관", runner=_raw_engine_not_ok(200, _NANET_LIVE),
                       settings={"request_interval_s": 0, "source_intervals": {}, "timeouts": {"insane_search_s": 60},
                                 "retries": 0})
    [item] = get_adapter("국회도서관").list_items(fx, limit=5, cursor=None, key="K")
    assert (item.key, item.title, item.date) == ("KINX2026067688", "제주 4⋅3 사건의 기억", "2026-03-01")


def test_nanet_normal_zero_hits_is_empty_list_not_error():
    from bogoseo.collect import fetch
    from bogoseo.collect.sources import get_adapter
    body = ("<response><header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>"
            "<total>0</total></response>")
    fx = fetch.Fetcher("국회도서관", runner=_raw_engine_not_ok(200, body),
                       settings={"request_interval_s": 0, "source_intervals": {}, "timeouts": {"insane_search_s": 60},
                                 "retries": 0})
    assert get_adapter("국회도서관").list_items(fx, limit=5, cursor=None, key="K") == []
