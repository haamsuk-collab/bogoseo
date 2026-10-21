"""국방부 (mnd.go.kr) 보도자료 board: list page, detail page, attachment download."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from .sources import Adapter, Item, SourceError, html_to_text, md_doc, norm_date, pick_file, register

BASE = "https://www.mnd.go.kr"
BOARD = "/bbs/mnd/13000005"
MAX_PAGES = 5


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


@register
class MndWeb(Adapter):
    name = "국방부"
    kind = "web"
    institution = "국방부"
    institution_class = "국방"
    endpoint = BASE + BOARD + "/artclList.do"

    def _rows(self, page_html: str) -> list:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_html, re.S):
            m = re.search(rf'href="({BOARD}/(\w+)/artclView\.do)"[^>]*>(.*?)</a>', tr, re.S)
            if not m:
                continue
            d = re.search(r'class="td-date">([^<]*)<', tr)
            rows.append((m[2], _strip(re.sub(r"<span class=\"new\">.*?</span>", "", m[3], flags=re.S)), norm_date(d[1]) if d else None, m[1]))
        return rows

    def list_items(self, fx, *, limit, cursor, key=None):
        rows, seen = [], set()
        for p in range(1, MAX_PAGES + 1):
            resp = self._get(fx, f"{BASE}{BOARD}/artclList.do" + (f"?page={p}" if p > 1 else ""))
            page_rows = self._rows(resp.text())
            if not page_rows:
                if p == 1:
                    raise SourceError("국방부: no board rows (blocked or layout changed)", resp)
                break
            stop = False
            for r in page_rows:
                if r[0] in seen:
                    continue
                seen.add(r[0])
                if cursor and r[2] and r[2] < cursor:
                    stop = True
                    continue
                rows.append(r)
            if stop or len(rows) >= limit:
                break
        items = []
        for art, title, date, path in rows[:limit]:
            durl = BASE + path
            t = self._get(fx, durl).text()
            files = [(urljoin(durl, h), _strip(n)) for h, n in re.findall(
                r'<li>\s*<a href="(/bbs/mnd/\d+/\w+/download\.do)">\s*<span>(.*?)</span>', t, re.S)]
            best = pick_file(files)
            item = Item(key=art, title=title, source_url=durl, date=date,
                        institution=self.institution, institution_class=self.institution_class)
            if best:
                item.download_url, item.filename = best
            else:
                m = re.search(r'<div class="txt">(.*?)</div>', t, re.S)
                body = html_to_text(m[1]) if m else ""
                if not body:
                    continue
                item.text = md_doc(title, meta={"출처": "국방부 보도자료", "작성일": date, "원문": durl}, body=body)
            items.append(item)
        return items
