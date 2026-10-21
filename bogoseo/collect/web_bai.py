"""감사원 (www.bai.go.kr): 감사결과 공개문 and 보도자료/결산검사보고 boards.

The public site is a Nuxt SPA over a JSON API; every endpoint below is GET-reachable and the
attachment download (``/api/files/download``) also answers GET with the raw file bytes.
"""
from __future__ import annotations

import json
from typing import Optional
from urllib.parse import quote

from .sources import (Adapter, Item, SourceError, html_to_text, md_doc, norm_date,
                      pick_file, register)

BASE = "https://www.bai.go.kr"
_COMMON = "searchType=0&searchText=&fromRegiDt=&toRegiDt=&searchYear=&size={size}&index=0&page=0"
# (board id, label) of the generic board API (/api/boards); 감사결과 has its own API.
BOARDS = (("BAK_0007", "보도자료"), ("BAK_0022", "결산검사보고"))
RESULT_API = BASE + "/api/bak/dar/AWUBAKDAR001E"
FILE_LISTS = ("commonFileOpenDtoList", "commonFileNewsDtoList", "commonFileDtlDtoList",
              "commonFileDetailDtoList", "commonFileDetailTwoDtoList", "commonFileMainDtoList")


def _json(adapter, fx, url: str) -> dict:
    resp = adapter._get(fx, url)
    if resp.blocked:
        raise SourceError("blocked", resp)
    try:
        data = resp.json()
    except (ValueError, json.JSONDecodeError):
        if resp.blocked or not resp.ok:
            raise SourceError("blocked", resp)
        raise SourceError(f"{adapter.name}: non-JSON response ({resp.code})", resp)
    if not isinstance(data, dict) or "errorCode" in data:
        raise SourceError(f"{adapter.name}: API error {str(data)[:120]}", resp)
    return data


def _files(detail: dict) -> list:
    out, seen = [], set()
    for k in FILE_LISTS:
        for f in detail.get(k) or []:
            fid, sn = f.get("fileId"), f.get("fileSn")
            if not fid or (fid, sn) in seen:
                continue
            seen.add((fid, sn))
            url = f"{BASE}/api/files/download?fileId={quote(str(fid))}&fileSn={sn}"
            out.append((url, f.get("fileName") or f"{fid}.{f.get('fileExt') or 'bin'}"))
    return out


@register
class BaiWeb(Adapter):
    name = "감사원"
    kind = "web"
    institution = "감사원"
    institution_class = "중앙부처"

    def list_items(self, fx, *, limit, cursor, key=None) -> list:
        rows = []  # (date, kind, ident, title, dept, label)
        res = _json(self, fx, f"{RESULT_API}?audSphCd=&audSphDtlCd=&audKndCd=&" + _COMMON.format(size=limit))
        for r in (res.get("_embedded") or {}).get("aWUBAKDAR001EDtoList") or []:
            rows.append((norm_date(r.get("openDt")), "result", str(r["srno"]), r.get("titNm") or "",
                         r.get("hndlDptNm"), "감사결과"))
        for brd, label in BOARDS:
            res = _json(self, fx, f"{BASE}/api/boards/list?brdId={brd}&" + _COMMON.format(size=limit))
            for r in (res.get("_embedded") or {}).get("boardDtoList") or []:
                rows.append((norm_date(r.get("regiDt")), brd, str(r["postNo"]), r.get("titNm") or "",
                             r.get("chgrNm"), label))
        rows.sort(key=lambda r: (r[0] or "", r[2]), reverse=True)
        if cursor:
            rows = [r for r in rows if not r[0] or r[0] >= cursor]
        items = []
        for date, kind, ident, title, dept, label in rows[:limit]:
            if kind == "result":
                src = f"{BASE}/bai/result/branch/detail?srno={ident}"
                detail = _json(self, fx, f"{RESULT_API}/{ident}")
                body_html = detail.get("openTxt") or ""
            else:
                src = f"{BASE}/bai/board/base/detail?brdId={kind}&postNo={ident}"
                detail = _json(self, fx, f"{BASE}/api/boards/detail?brdId={kind}&postNo={ident}&"
                               + _COMMON.format(size=10))
                body_html = detail.get("postTxt") or ""
            best = pick_file(_files(detail))
            it = Item(key=f"{kind}:{ident}", title=title.strip(), source_url=src, date=date,
                      institution=self.institution, institution_class=self.institution_class)
            if best:
                it.download_url, it.filename = best
            else:
                body = html_to_text(body_html)
                if not body:
                    continue
                it.text = md_doc(it.title, meta={"출처": "감사원 " + label, "담당부서": dept,
                                                 "게시일": date, "원문": src}, body=body)
            items.append(it)
        return items
