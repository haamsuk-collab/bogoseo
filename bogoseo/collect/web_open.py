"""정보공개포털 (open.go.kr) 원문정보: published approval-document records of public agencies.

GET-only route: ``/othicInfo/infoList/orginlInfoList.do?viewPage=N`` renders the result rows as a
JSON literal (``var result = {...}``) inside the page, and ``/othicInfo/infoList/infoListDetl.do``
renders the document facts and attachment list the same way.  The original files themselves are
served only through a POST/login flow (``wonmunFileRequest.ajax``, HTTP 405 on GET), so each
document is stored as a Markdown record of its published facts (``text``), not as a file.
"""
from __future__ import annotations

import json
import re
from urllib.parse import urlencode

from .sources import Adapter, Item, SourceError, md_doc, norm_date, register

BASE = "https://www.open.go.kr"
LIST = BASE + "/othicInfo/infoList/orginlInfoList.do"
DETAIL = BASE + "/othicInfo/infoList/infoListDetl.do"
MAX_PAGES = 5


def parse_result(html: str):
    """The ``var result = {...};`` literal of a portal page, or None."""
    for m in re.finditer(r"var result\s*=\s*(\{.*?\});\s*\n", html, re.S):
        try:
            return json.loads(m.group(1))
        except ValueError:
            continue
    return None


def detail_url(row: dict) -> str:
    return DETAIL + "?" + urlencode({
        "prdnNstRgstNo": row["PRDCTN_INSTT_REGIST_NO"], "prdnDt": row["PRDCTN_DT"], "nstSeCd": row["INSTT_SE_CD"]})


def _size(n) -> str:
    try:
        n = int(n)
    except (TypeError, ValueError):
        return ""
    return f"{n / 1048576:.1f} MB" if n >= 1048576 else f"{n / 1024:.1f} KB"


@register
class OpenGoWeb(Adapter):
    name = "정보공개포털"
    kind = "web"
    institution = "행정안전부"
    institution_class = "중앙부처"
    endpoint = LIST

    def list_items(self, fx, *, limit, cursor, key=None):
        rows = []
        for page in range(1, MAX_PAGES + 1):
            resp = fx.get(f"{LIST}?viewPage={page}")
            if not resp.ok and (resp.blocked or not resp.body):
                raise SourceError("blocked" if resp.blocked else f"{self.name}: list failed ({resp.code})", resp)
            res = parse_result(resp.text())
            page_rows = (res or {}).get("rtnList") or []
            if page == 1 and not page_rows:
                raise SourceError(f"{self.name}: no rows in the list page", resp)
            if not page_rows:
                break
            rows += [r for r in page_rows if r.get("PRDCTN_INSTT_REGIST_NO")]
            done = bool(cursor) and (norm_date(page_rows[-1].get("PRDCTN_DT")) or "9") < cursor
            if len(rows) >= limit or done:
                break
        items = []
        for row in rows:
            if len(items) >= limit:
                break
            date = norm_date(row.get("PRDCTN_DT"))
            if cursor and date and date < cursor:
                continue
            url = detail_url(row)
            files = []
            resp = fx.get(url)
            if resp.ok:
                res = parse_result(resp.text()) or {}
                files = (res.get("openCateSearchVO") or {}).get("fileList") or []
                vo = res.get("openCateSearchVO") or {}
            else:
                vo = {}
            title = row.get("INFO_SJ") or vo.get("infoSj") or ""
            meta = {
                "생산기관": row.get("PROC_INSTT_NM") or vo.get("prcsNstNm"),
                "담당부서": row.get("NFLST_CHRG_DEPT_NM") or row.get("CHRG_DEPT_NM"),
                "문서번호": row.get("DOC_NO") or vo.get("docNo"),
                "단위업무": row.get("UNIT_JOB_NM"),
                "분류": vo.get("nstClNm"),
                "생산일자": date,
                "공개구분": vo.get("dlsrCdNm"),
                "출처": url,
            }
            body = ""
            if files:
                body = "## 첨부파일\n\n" + "\n".join(
                    f"- [{f.get('fileSeDc') or '첨부'}] {f.get('fileNm')}"
                    + (f" ({_size(f.get('fileByteNum'))})" if f.get("fileByteNum") else "") for f in files)
            elif row.get("FILE_NM"):
                body = "## 첨부파일\n\n- " + row["FILE_NM"]
            items.append(Item(
                key=row["PRDCTN_INSTT_REGIST_NO"], title=title, source_url=url, date=date,
                text=md_doc(title, meta=meta, body=body),
                institution=self.institution, institution_class=self.institution_class))
        return items
