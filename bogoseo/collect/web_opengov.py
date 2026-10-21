"""서울시 정보소통광장 (opengov.seoul.go.kr): 보도자료, 주요사업백서, 정책연구자료 boards.

Each board is a plain HTML list (``/<board>/list``) with detail pages ``/<board>/<nid>``.  A
detail page lists its attachments; the original file (mostly HWPX/PDF) downloads with a plain
GET of ``/og/com/download.php?nid=<nid>&dtype=basic&rid=<id>&fid=<id>``.
"""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from .sources import Adapter, Item, SourceError, html_to_text, md_doc, norm_date, pick_file, register

BASE = "https://opengov.seoul.go.kr"
BOARDS = (("press", "보도자료"), ("paper", "주요사업백서"), ("research", "정책연구자료"))
_ROW = re.compile(r'href="/(?P<b>%s)/(?P<nid>\d+)"[^>]*>(?P<title>.*?)</a>' % "|".join(b for b, _ in BOARDS), re.S)
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_ATTACH_UL = re.compile(r'<ul class="list-attachment">(.*?)</ul>', re.S)
_FILE_NAME = re.compile(r"([^<>\n]+?\.[A-Za-z0-9]{2,5})\s*(?:<span class=\"txt-gray\">|\()")
_FILE_LINK = re.compile(r'href="(/og/com/download\.php\?[^"]*\bnid=[^"]+)"')
_BLOCK_MARKERS = ("captcha", "접근이 제한", "Access Denied")


def _strip(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", " ", s)).replace("\xa0", " ").strip()


def _list_rows(page: str, board: str) -> list:
    """[(date, nid, title)] of one list page, in page order."""
    matches = [m for m in _ROW.finditer(page) if m["b"] == board]
    rows, seen = [], set()
    for i, m in enumerate(matches):
        if m["nid"] in seen:
            continue
        seen.add(m["nid"])
        end = matches[i + 1].start() if i + 1 < len(matches) else len(page)
        d = _DATE.search(page, m.end(), end)
        rows.append((d.group(0) if d else None, m["nid"], re.sub(r"^제목\s*:\s*", "", re.sub(r"\s+", " ", _strip(m["title"])))))
    return rows


def _attachments(page: str) -> list:
    out = []
    for ul in _ATTACH_UL.findall(page):
        for li in re.split(r"<li\b", ul)[1:]:
            link = _FILE_LINK.search(li)
            name = _FILE_NAME.search(html.unescape(re.sub(r"<(?!span)[^>]+>", "", li.split("btn-downset")[0])))
            if link and name:
                out.append((urljoin(BASE, html.unescape(link.group(1))), name.group(1).strip()))
    return out


def _detail_date(page: str):
    m = re.search(r"(?:생산일|등록일)</th>\s*<td[^>]*>\s*(\d{4}-\d{2}-\d{2})", page)
    return m.group(1) if m else None


@register
class OpengovWeb(Adapter):
    name = "서울시 정보소통광장"
    kind = "web"
    institution = "서울특별시"
    institution_class = "지자체"

    def list_items(self, fx, *, limit, cursor, key=None) -> list:
        rows = []
        for board, label in BOARDS:
            resp = self._get(fx, f"{BASE}/{board}/list")
            if resp.blocked or any(k in resp.text()[:3000] for k in _BLOCK_MARKERS):
                raise SourceError("blocked", resp)
            rows += [(d, board, nid, title) for d, nid, title in _list_rows(resp.text(), board)]
        rows.sort(key=lambda r: (r[0] or "", r[2]), reverse=True)
        if cursor:
            rows = [r for r in rows if not r[0] or r[0] >= cursor]
        labels = dict(BOARDS)
        items = []
        for date, board, nid, title in rows[:limit]:
            src = f"{BASE}/{board}/{nid}"
            page = self._get(fx, src).text()
            it = Item(key=f"{board}:{nid}", title=title, source_url=src, date=_detail_date(page) or norm_date(date),
                      institution=self.institution, institution_class=self.institution_class)
            best = pick_file(_attachments(page))
            if best:
                it.download_url, it.filename = best
            else:
                m = re.search(r'<h4>문서 본문</h4>\s*<div class="line-all">(.*?)</div>', page, re.S)
                body = html_to_text(m.group(1)) if m else ""
                if not body:
                    continue
                it.text = md_doc(title, meta={"출처": "서울시 정보소통광장 " + labels[board], "게시일": it.date,
                                              "원문": src}, body=body)
            items.append(it)
        return items
