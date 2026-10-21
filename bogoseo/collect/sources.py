"""Source adapters of the bulk collector: 15 sources, one adapter each.

An adapter turns responses (fetched only through ``Fetcher``) into ``Item`` objects; the runner
downloads/ingests them through ``warehouse.ingest_file``.  Adapters never touch the network
directly and never read key files: the runner hands them the key value.
"""
from __future__ import annotations

import html
import importlib
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import quote, urlencode

from .fetch import Fetcher, Response

FILE_EXT_PREF = (".pdf", ".hwpx", ".hwp", ".docx", ".doc", ".xlsx", ".pptx", ".odt", ".txt", ".md", ".zip")


# --------------------------------------------------------------------------- helpers
@dataclass
class Item:
    key: str                          # stable id inside the source
    title: str
    source_url: str                   # unique per document; used to skip already ingested items
    date: Optional[str] = None        # YYYY-MM-DD (document date) when known
    download_url: Optional[str] = None  # original file (pdf/hwp/hwpx...); None -> ``text`` is stored as .md
    filename: Optional[str] = None
    text: Optional[str] = None        # markdown body used when there is no downloadable file
    institution: Optional[str] = None
    institution_class: Optional[str] = None
    doc_type: Optional[str] = None


@dataclass
class VerifyResult:
    ok: bool
    code: Optional[str] = None        # HTTP status or API result code
    detail: str = ""


class SourceError(Exception):
    """Listing failed with a response the adapter could not use (carries the Response)."""

    def __init__(self, msg: str, resp: Optional[Response] = None):
        super().__init__(msg)
        self.resp = resp


class _Text(HTMLParser):
    BLOCK = {"p", "br", "div", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "table", "section"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        if tag in self.BLOCK:
            self.out.append("\n")
        if tag in ("td", "th"):
            self.out.append(" | ")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        if tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.out.append(data)


def html_to_text(s: str) -> str:
    """Plain text with line structure from an HTML fragment (marker glyphs are preserved)."""
    p = _Text()
    p.feed(s or "")
    text = html.unescape("".join(p.out)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out, blank = [], False
    for ln in lines:
        if ln:
            out.append(ln)
            blank = False
        elif not blank and out:
            out.append("")
            blank = True
    return "\n".join(out).strip()


def norm_date(s: Optional[str]) -> Optional[str]:
    """'2026-10-20', '20261020', '2026.10.20', '10/20/2026 17:11:00' -> '2026-10-20'."""
    if not s:
        return None
    s = s.strip()
    m = re.match(r"(\d{4})[-./]?\s*(\d{1,2})[-./]?\s*(\d{1,2})", s)
    if m:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}"
    m = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if m:
        return f"{int(m[3]):04d}-{int(m[1]):02d}-{int(m[2]):02d}"
    return None


def _year_month(issue: str, year: str) -> Optional[str]:
    """'탐라문화. 제81호 (2026년 3월)' -> '2026-03-01'; else a bare year '2026' -> '2026-01-01'."""
    m = re.search(r"(\d{4})\s*년\s*(\d{1,2})\s*월", issue)
    if m and 1 <= int(m[2]) <= 12:
        return f"{int(m[1]):04d}-{int(m[2]):02d}-01"
    m = re.fullmatch(r"\s*(\d{4})\s*", year or "")
    return f"{m[1]}-01-01" if m else None


def pick_file(files: list) -> Optional[tuple]:
    """files: [(url, filename)] -> the best original by extension preference (pdf first)."""
    best, rank = None, 99
    for url, name in files:
        if not url:
            continue
        ext = ("." + (name or url).rsplit(".", 1)[-1].lower()) if "." in (name or url) else ""
        r = FILE_EXT_PREF.index(ext) if ext in FILE_EXT_PREF else 50
        if r < rank:
            best, rank = (url, name), r
    return best


def classify_institution(name: str) -> str:
    n = name or ""
    if re.search(r"국방|합동참모|육군|해군|공군|해병|방위사업|병무|국군|군수사|사령부", n):
        return "국방"
    if n.startswith("국회") or "국회" in n[:4]:
        return "국회"
    if re.search(r"(특별|광역)?시$|도$|군$|구$|시청|도청|서울|부산|인천|대구|대전|광주|울산|세종|경기|강원|충청|전라|경상|제주", n):
        return "지자체"
    if re.search(r"공사$|공단$|진흥원$|연구원$|재단$|협회$|은행$", n):
        return "공공기관"
    return "중앙부처"


def md_doc(title: str, *, meta: dict, body: str = "") -> str:
    """Markdown rendering of a text-only record: title, source facts, body."""
    lines = [f"# {title}", ""]
    for k, v in meta.items():
        if v:
            lines.append(f"- {k}: {v}")
    if body:
        lines += ["", body]
    return "\n".join(lines).rstrip() + "\n"


def ymd(d: _date) -> str:
    return d.strftime("%Y%m%d")


def window(cursor: Optional[str], days_first: int = 3, today: Optional[_date] = None) -> tuple:
    """(start, end) dates for date-range APIs: from the cursor (or ``days_first`` days back) to today."""
    today = today or _date.today()
    start = today - timedelta(days=days_first)
    if cursor:
        m = re.match(r"(\d{4})-(\d{2})-(\d{2})", cursor)
        if m:
            start = _date(int(m[1]), int(m[2]), int(m[3]))
    return start, today


# --------------------------------------------------------------------------- base
class Adapter:
    name: str = ""
    kind: str = "api"                 # api | web
    key_item: Optional[str] = None    # keys.yaml item (api sources)
    institution: str = ""
    institution_class: str = "중앙부처"
    default_doc_type: Optional[str] = None
    endpoint: str = ""

    def list_items(self, fx: Fetcher, *, limit: int, cursor: Optional[str], key: Optional[str]) -> list:
        raise NotImplementedError

    def verify(self, fx: Fetcher, key: str) -> VerifyResult:
        raise NotImplementedError

    # shared
    def _get(self, fx: Fetcher, url: str) -> Response:
        resp = fx.get(url)
        if not resp.ok and not resp.body:
            raise SourceError(f"{self.name}: request failed ({resp.code or resp.error})", resp)
        return resp


REGISTRY: dict = {}


def register(cls):
    REGISTRY[cls.name] = cls
    return cls


# --------------------------------------------------------------------------- data.go.kr family
def _dgk_result_code(resp: Response) -> tuple:
    """(ok, code) for apis.data.go.kr style bodies (JSON header/resultCode or XML)."""
    t = resp.text()
    m = re.search(r'"resultCode"\s*:\s*"?(\w+)"?', t) or re.search(r"<resultCode>(\w+)</resultCode>", t) \
        or re.search(r"<returnReasonCode>(\w+)</returnReasonCode>", t)
    code = m.group(1) if m else None
    if code is None:
        return resp.ok, str(resp.code or "")
    return code in ("0", "00", "Success", "SUCCESS", "NORMAL_CODE"), code


class _DataGoKr(Adapter):
    key_item = "data_go_kr"

    def verify(self, fx, key):
        resp = fx.get(self.verify_url(key))
        ok, code = _dgk_result_code(resp)
        if resp.code == 200 and ok:
            return VerifyResult(True, str(resp.code or 200))
        return VerifyResult(False, str(code if code not in (None, "") else (resp.code or resp.error)), _err_msg(resp))

    def verify_url(self, key: str) -> str:
        raise NotImplementedError


def loose_json(resp: Response):
    """The 재정경제부 API emits trailing commas; strip them before parsing."""
    import json
    return json.loads(re.sub(r",(\s*[\]}])", r"\1", resp.text()), strict=False)


def _err_msg(resp: Response) -> str:
    t = resp.text()
    m = re.search(r"<errMsg>([^<]+)</errMsg>|\"resultMsg\"\s*:\s*\"([^\"]+)\"|\"message\"\s*:\s*\"([^\"]+)\"", t)
    return next((g for g in (m.groups() if m else ()) if g), "")[:120]


@register
class PolicyBriefing(_DataGoKr):
    """정책브리핑 (korea.kr) policy news via data.go.kr 문화체육관광부_정책브리핑_정책뉴스_API (15095335)."""
    name = "정책브리핑"
    institution = "문화체육관광부"
    endpoint = "https://apis.data.go.kr/1371000/policyNewsService2/policyNewsList2"

    def _url(self, key, start, end):
        return f"{self.endpoint}?serviceKey={quote(key, safe='')}&startDate={ymd(start)}&endDate={ymd(end)}"

    def verify_url(self, key):
        d = _date.today()
        return self._url(key, d - timedelta(days=2), d)

    def list_items(self, fx, *, limit, cursor, key):
        start, end = window(cursor, 2)
        resp = self._get(fx, self._url(key, start, end))
        ok, code = _dgk_result_code(resp)
        if not ok:
            raise SourceError(f"{self.name}: result {code}", resp)
        root = ET.fromstring(resp.body)
        items = []
        for n in root.findall("body/NewsItem"):
            g = lambda t: (n.findtext(t) or "").strip()
            title = g("Title")
            body = html_to_text(g("DataContents"))
            minister = g("MinisterCode") or self.institution
            d = norm_date(g("ApproveDate"))
            items.append(Item(
                key=g("NewsItemId"), title=title, date=d,
                source_url=g("OriginalUrl") or f"https://www.korea.kr/news/policyNewsView.do?newsId={g('NewsItemId')}",
                text=md_doc(title, meta={"부제": html_to_text(g("SubTitle1")).replace("\n", " / "), "기관": minister, "일자": d}, body=body),
                institution=minister, institution_class=classify_institution(minister)))
        items.sort(key=lambda i: i.date or "", reverse=True)
        return items[:limit]


@register
class MoefPress(_DataGoKr):
    """재정경제부_보도자료 (data.go.kr 15162042): list items carry hwp/pdf attachments."""
    name = "재정경제부_보도자료"
    institution = "재정경제부"
    endpoint = "https://apis.data.go.kr/1053000/brief/list"

    def _url(self, key, start, end, size):
        return (f"{self.endpoint}?serviceKey={quote(key, safe='')}&start_date={ymd(start)}&end_date={ymd(end)}"
                f"&page_no=1&page_unit={size}&result_type=json")

    def verify_url(self, key):
        d = _date.today()
        return self._url(key, d - timedelta(days=7), d, 1)

    def list_items(self, fx, *, limit, cursor, key):
        start, end = window(cursor, 14)
        resp = self._get(fx, self._url(key, start, end, limit))
        data = loose_json(resp)
        res = data.get("result")
        if res is None:
            raise SourceError(f"{self.name}: {_err_msg(resp) or 'bad response'}", resp)
        out = []
        for it in res.get("items") or []:
            files = [(f.get("fileUrl"), f.get("fileName")) for f in it.get("files") or []]
            best = pick_file(files)
            title = (it.get("title") or "").strip()
            d = norm_date(it.get("registDate"))
            body = html_to_text(it.get("contents") or "")
            out.append(Item(
                key=str(it.get("itemId")), title=title, date=d,
                source_url=(best[0] if best else f"{self.endpoint}#{it.get('itemId')}"),
                download_url=best[0] if best else None, filename=best[1] if best else None,
                text=md_doc(title, meta={"기관": self.institution, "부서": it.get("deptName"), "일자": d}, body=body),
                institution=self.institution, institution_class="중앙부처"))
        out.sort(key=lambda i: i.date or "", reverse=True)
        return out[:limit]


@register
class MsitPress(_DataGoKr):
    """과학기술정보통신부_보도자료 (data.go.kr 15074632)."""
    name = "과학기술정보통신부_보도자료"
    institution = "과학기술정보통신부"
    endpoint = "https://apis.data.go.kr/1721000/msitpressreleaseinfo/pressReleaseList"

    def _url(self, key, size):
        return f"{self.endpoint}?serviceKey={quote(key, safe='')}&pageNo=1&numOfRows={size}&returnType=json"

    def verify_url(self, key):
        return self._url(key, 1)

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._url(key, limit))
        data = resp.json()
        body = next((x["body"] for x in data.get("response", []) if isinstance(x, dict) and "body" in x), None)
        if body is None:
            raise SourceError(f"{self.name}: {_err_msg(resp) or 'bad response'}", resp)
        out = []
        for wrap in body.get("items") or []:
            it = wrap.get("item", wrap)
            files = [((f.get("file") or f).get("fileUrl"), (f.get("file") or f).get("fileName")) for f in it.get("files") or []]
            best = pick_file(files)
            title = (it.get("subject") or "").strip()
            d = norm_date(it.get("pressDt"))
            url = it.get("viewUrl") or ""
            out.append(Item(
                key=url, title=title, date=d, source_url=(best[0] if best else url),
                download_url=best[0] if best else None, filename=best[1] if best else None,
                text=md_doc(title, meta={"기관": self.institution, "부서": (it.get("managerName") or "").strip(), "일자": d, "원문": url},
                            body=(it.get("deptName") or "").strip()),
                institution=self.institution, institution_class="중앙부처"))
        out.sort(key=lambda i: i.date or "", reverse=True)
        return out[:limit]


@register
class MoisGovDocs(_DataGoKr):
    """행정안전부_정부 공문서 AI 학습데이터 (data.go.kr 15125451): policy reports/plans with full text and record metadata."""
    name = "행정안전부_정부 공문서 AI 학습데이터"
    institution = "행정안전부"
    endpoint = "https://apis.data.go.kr/1741000/publicDoc"
    # (service, 문서유형) — report-like kinds only; 보도자료/연설문/발간사 are not selected.
    SERVICES = (("getDocReport", "보고"), ("getDocPlan", "계획"))
    KEYWORDS = ("계획", "개선", "방안", "결과", "검토", "보고")

    def _url(self, key, service, title, size=10, page=1):
        return (f"{self.endpoint}/{service}?serviceKey={quote(key, safe='')}&numOfRows={size}&pageNo={page}"
                f"&format=json&title={quote(title)}")

    def verify_url(self, key):
        return self._url(key, "getDocReport", "계획", 1)

    def list_items(self, fx, *, limit, cursor, key):
        out, seen = [], set()
        for service, default_type in self.SERVICES:
            for kw in self.KEYWORDS:
                if len(out) >= limit:
                    return out[:limit]
                resp = self._get(fx, self._url(key, service, kw, size=max(1, min(limit, 10))))
                body = resp.json().get("response", {}).get("body") or {}
                for rec in body.get("resultList") or []:
                    meta, data = rec.get("meta", {}), rec.get("data", {})
                    doc_id = meta.get("doc_id")
                    if not doc_id or doc_id in seen or not data.get("text"):
                        continue
                    seen.add(doc_id)
                    title = (meta.get("title") or "").strip()
                    ministry = (meta.get("ministry") or self.institution).strip()
                    out.append(Item(
                        key=doc_id, title=title, date=norm_date(meta.get("date")),
                        source_url=f"{self.endpoint}/{service}#{doc_id}",
                        text=md_doc(title, meta={"기관": ministry, "부서": meta.get("department"), "일자": meta.get("date"),
                                                 "문서종류": meta.get("doc_type")}, body=data["text"]),
                        institution=ministry, institution_class=classify_institution(ministry),
                        doc_type=default_type))
        return out[:limit]


# --------------------------------------------------------------------------- other APIs
@register
class NaboApi(Adapter):
    """국회예산정책처 Open API (https://www.nabo.go.kr/api/v1/report.do, parameter ``key``)."""
    name = "국회예산정책처"
    key_item = "nabo"
    institution = "국회예산정책처"
    institution_class = "국회"
    endpoint = "https://www.nabo.go.kr/api/v1/report.do"

    def _url(self, key, size):
        return f"{self.endpoint}?key={quote(key, safe='')}&page=1&size={size}&scSort=pubDt&scOrder=desc"

    def verify(self, fx, key):
        resp = fx.get(self._url(key, 1))
        t = resp.text()
        m = re.search(r'"code"\s*:\s*"(\w+)"', t)
        if resp.ok and not m:
            return VerifyResult(True, str(resp.code or 200))
        return VerifyResult(False, str(resp.code or (m.group(1) if m else resp.error)), (m.group(1) if m else _err_msg(resp)))

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._url(key, limit))
        data = resp.json()
        if "code" in data and not data.get("list") and not data.get("items"):
            raise SourceError(f"{self.name}: {data.get('code')}", resp)
        rows = data.get("list") or data.get("items") or data.get("data") or []
        out = []
        for r in rows:
            title = (r.get("subj") or "").strip()
            files = [(f.get("url"), f.get("name")) for f in (r.get("files") or r.get("attachments") or []) if isinstance(f, dict)]
            if r.get("url") and r.get("name"):
                files.append((r["url"], r["name"]))
            best = pick_file(files)
            detail = r.get("detailUrl") or ""
            d = norm_date(r.get("pubDt"))
            out.append(Item(
                key=detail or title, title=title, date=d,
                source_url=(best[0] if best else detail),
                download_url=best[0] if best else None, filename=best[1] if best else None,
                text=md_doc(title, meta={"기관": self.institution, "부서": r.get("cdNm"), "일자": d, "원문": detail},
                            body=html_to_text(r.get("text") or "")),
                institution=self.institution, institution_class="국회"))
        return out[:limit]


@register
class AssemblyApi(Adapter):
    """열린국회정보 Open API (https://open.assembly.go.kr/portal/openapi/<SERVICE>?KEY=...)."""
    name = "열린국회정보"
    key_item = "assembly"
    institution = "국회사무처"
    institution_class = "국회"
    base = "https://open.assembly.go.kr/portal/openapi"
    service = "TVBPMBILL11"          # 국회의원 발의법률안

    def _url(self, key, size, index=1):
        return f"{self.base}/{self.service}?KEY={quote(key, safe='')}&Type=json&pIndex={index}&pSize={size}&AGE=22"

    def _rows(self, resp):
        data = resp.json()
        if self.service in data:
            head = data[self.service][0]["head"]
            code = next((h["RESULT"]["CODE"] for h in head if "RESULT" in h), None)
            rows = (data[self.service][1] if len(data[self.service]) > 1 else {}).get("row", [])
            return code, rows
        res = data.get("RESULT") or {}
        return res.get("CODE"), []

    def verify(self, fx, key):
        resp = fx.get(self._url(key, 1))
        code, _ = self._rows(resp) if resp.body else (None, [])
        if resp.ok and code == "INFO-000":
            return VerifyResult(True, str(resp.code or 200))
        return VerifyResult(False, str(code or resp.code or resp.error))

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._url(key, limit))
        code, rows = self._rows(resp)
        if code != "INFO-000":
            raise SourceError(f"{self.name}: {code}", resp)
        out = []
        for r in rows:
            title = (r.get("BILL_NAME") or "").strip()
            d = norm_date(r.get("PROPOSE_DT"))
            out.append(Item(
                key=r.get("BILL_ID") or title, title=title, date=d,
                source_url=r.get("LINK_URL") or f"{self.base}/{self.service}#{r.get('BILL_ID')}",
                text=md_doc(title, meta={"의안번호": r.get("BILL_NO"), "제안자": r.get("PROPOSER"), "제안일": d,
                                         "소관위원회": r.get("CURR_COMMITTEE"), "처리상태": r.get("PASS_GUBUN"),
                                         "원문": r.get("LINK_URL")}),
                institution=self.institution, institution_class="국회", doc_type="기타"))
        out.sort(key=lambda i: i.date or "", reverse=True)
        return out[:limit]


@register
class NarsApi(Adapter):
    """국회입법조사처 발간물 (data.go.kr 3037296, apis.data.go.kr/9735000/PublicationService, ``ServiceKey``).

    Reply shape: ``{"getResearchReportList": {"header": {"code": "00", ...}, "item": [...]}}``; items carry
    DATA_ID, BOOK_NM (title), INSERT_DT, DIV_NM (series), PDF_FILE_NM/PDF_FILE_URL and VIEWER_URL.
    """
    name = "국회입법조사처"
    key_item = "data_go_kr"
    institution = "국회입법조사처"
    institution_class = "국회"
    endpoint = "https://apis.data.go.kr/9735000/PublicationService/getResearchReportList"

    def _url(self, key, size):
        return f"{self.endpoint}?ServiceKey={quote(key, safe='')}&pageNo=1&numOfRows={size}&resultType=json"

    @staticmethod
    def _payload(resp):
        try:
            body = resp.json().get("getResearchReportList") or {}
        except Exception:
            return None, []
        code = (body.get("header") or {}).get("code")
        item = body.get("item") or []
        return code, [item] if isinstance(item, dict) else item

    def verify(self, fx, key):
        resp = fx.get(self._url(key, 1))
        code, _ = self._payload(resp)
        if resp.code == 200 and code == "00":
            return VerifyResult(True, "200")
        return VerifyResult(False, str(code or resp.code or resp.error), _err_msg(resp))

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._url(key, limit))
        code, rows = self._payload(resp)
        if code != "00":
            raise SourceError(f"{self.name}: {code}", resp)
        out = []
        for r in rows:
            title = (r.get("BOOK_NM") or "").strip()
            pdf = (r.get("PDF_FILE_URL") or "").strip()
            if pdf.startswith("http://www.nars.go.kr/"):   # nars.go.kr serves the same file over https
                pdf = "https://" + pdf[len("http://"):]
            viewer = (r.get("VIEWER_URL") or "").strip()
            d = norm_date(r.get("INSERT_DT"))
            out.append(Item(
                key=r.get("DATA_ID") or title, title=title, date=d,
                source_url=viewer or pdf, download_url=pdf or None, filename=r.get("PDF_FILE_NM") or None,
                text=md_doc(title, meta={"기관": self.institution, "분류": r.get("DIV_NM"), "일자": d}),
                institution=self.institution, institution_class="국회", doc_type="보고"))
        out.sort(key=lambda i: i.date or "", reverse=True)
        return out[:limit]


@register
class NanetApi(Adapter):
    """국회도서관 OpenAPI (data.go.kr gateway 9720000/searchservice/basic, ``ServiceKey``)."""
    name = "국회도서관"
    key_item = "nanet"
    institution = "국회도서관"
    institution_class = "국회"
    endpoint = "https://apis.data.go.kr/9720000/searchservice/basic"

    def _url(self, key, size):
        return (f"{self.endpoint}?pageno=1&displaylines={size}&search={quote('전체,보고서')}"
                f"&ServiceKey={quote(key, safe='')}")

    def verify(self, fx, key):
        resp = fx.get(self._url(key, 1))
        ok, code = _dgk_result_code(resp)
        # The gateway body is judged by HTTP status + resultCode, not by the engine's content verdict.
        if resp.code == 200 and ok and b"<total>" in resp.body:
            return VerifyResult(True, "200")
        return VerifyResult(False, str(resp.code if resp.code != 200 else (code or "bad_response")), _err_msg(resp))

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._url(key, limit))
        root = ET.fromstring(resp.body)
        # The live service names its rows <recode>; <record> is kept for the documented spelling.
        records = root.findall("record") or root.findall("recode")
        if not records:
            ok, _ = _dgk_result_code(resp)
            if resp.code == 200 and ok and root.findtext("total") is not None:
                return []           # a normal answer with zero hits
            raise SourceError(f"{self.name}: {_err_msg(resp) or 'no records'}", resp)
        out = []
        for rec in records:
            f = {(i.findtext("name") or "").strip(): (i.findtext("value") or "").strip() for i in rec.findall("item")}
            full = f.get("자료명/저자사항") or f.get("자료명") or f.get("기사명") or ""
            title = full.split(" / ")[0].split(" = ")[0].strip()
            cid = f.get("제어번호") or title
            d = norm_date(f.get("발행년도") or f.get("발행년") or "") or _year_month(
                f.get("수록지명") or "", f.get("발행년도") or f.get("발행년") or "")
            out.append(Item(
                key=cid, title=title, date=d,
                source_url=f"{self.endpoint}#{cid}",
                text=md_doc(title, meta={"기관": self.institution, "제어번호": cid,
                                         "자료사항": full, "저자": f.get("저자명"), "수록지": f.get("수록지명"),
                                         "청구기호": f.get("청구기호"),
                                         "발행": f.get("발행사항") or f.get("발행처") or f.get("발행자")}),
                institution=self.institution, institution_class="국회", doc_type="기타"))
        return out[:limit]


@register
class LawApi(Adapter):
    """법제처 국가법령정보 공동활용 (https://www.law.go.kr/DRF/lawSearch.do, ``OC``): 행정규칙(훈령·예규·고시)."""
    name = "법제처"
    key_item = "law_oc"
    institution = "법제처"
    endpoint = "https://www.law.go.kr/DRF/lawSearch.do"
    service = "https://www.law.go.kr/DRF/lawService.do"

    def _list_url(self, oc, size):
        return f"{self.endpoint}?OC={quote(oc, safe='')}&target=admrul&type=XML&display={size}&sort=ddes"

    def _body_url(self, oc, serial):
        return f"{self.service}?OC={quote(oc, safe='')}&target=admrul&type=XML&ID={serial}"

    def verify(self, fx, oc):
        resp = fx.get(self._list_url(oc, 10))
        if resp.code == 200:
            try:
                if ET.fromstring(resp.body).tag == "AdmRulSearch":
                    return VerifyResult(True, "200")
            except ET.ParseError:
                pass
            return VerifyResult(False, "bad_response", _err_msg(resp))
        return VerifyResult(False, str(resp.code or resp.error or "bad_response"), _err_msg(resp))

    def list_items(self, fx, *, limit, cursor, key):
        resp = self._get(fx, self._list_url(key, max(limit, 10)))
        root = ET.fromstring(resp.body)
        out = []
        for n in root.findall("admrul"):
            g = lambda t: (n.findtext(t) or "").strip()
            serial = g("행정규칙일련번호")
            title = g("행정규칙명")
            org = g("소관부처명") or "법제처"
            d = norm_date(g("발령일자") or g("시행일자"))
            body_url = self._body_url(key, serial)
            out.append(Item(
                key=serial, title=title, date=d,
                source_url=f"https://www.law.go.kr/행정규칙/{quote(title)}#{serial}",
                download_url=body_url, filename=f"{serial}.xml",
                institution=org, institution_class=classify_institution(org),
                doc_type="지침"))
        return out[:limit]

    def render(self, body: bytes, item: Item) -> str:
        """Convert a lawService.do admrul XML body into Markdown (articles keep their marker lines)."""
        root = ET.fromstring(body)
        lines = [f"# {item.title}", ""]
        info = root.find("행정규칙기본정보")
        if info is not None:
            for tag in ("소관부처명", "발령일자", "시행일자", "발령번호", "행정규칙종류"):
                v = (info.findtext(tag) or "").strip()
                if v:
                    lines.append(f"- {tag}: {v}")
            lines.append("")
        for art in root.iter("조문내용"):
            t = html_to_text(art.text or "")
            if t:
                lines.append(t)
        return "\n".join(lines).rstrip() + "\n"


# --------------------------------------------------------------------------- web sources
WEB_MODULES = ("web_opm", "web_open", "web_mnd", "web_likms", "web_opengov", "web_bai")


def load_web_adapters() -> None:
    for m in WEB_MODULES:
        try:
            importlib.import_module(f"{__package__}.{m}")
        except ModuleNotFoundError as e:       # module not written yet
            if e.name != f"{__package__}.{m}":
                raise


load_web_adapters()

API_ORDER = ("정책브리핑", "국회예산정책처", "국회입법조사처", "국회도서관", "법제처", "열린국회정보",
             "행정안전부_정부 공문서 AI 학습데이터", "재정경제부_보도자료", "과학기술정보통신부_보도자료")
WEB_ORDER = ("국무조정실", "정보공개포털", "국방부", "의안정보시스템", "서울시 정보소통광장", "감사원")


def adapters(kind: Optional[str] = None) -> list:
    order = API_ORDER + WEB_ORDER
    out = [REGISTRY[n]() for n in order if n in REGISTRY]
    return [a for a in out if kind in (None, a.kind)]


def get_adapter(name: str) -> Adapter:
    return REGISTRY[name]()
