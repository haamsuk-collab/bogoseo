"""국무조정실 (opm.go.kr) 보도자료 board: list page, detail page, attachment download."""
from __future__ import annotations

import html
import re
from urllib.parse import urljoin

from .sources import Adapter, Item, SourceError, classify_institution, html_to_text, md_doc, norm_date, pick_file, register

BASE = "https://www.opm.go.kr/opm/news/press-release.do"
PAGE_SIZE = 10
MAX_PAGES = 5


def _strip(s: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", "", s))).strip()


@register
class OpmWeb(Adapter):
    name = "국무조정실"
    kind = "web"
    institution = "국무조정실"
    institution_class = "중앙부처"
    endpoint = BASE

    def _rows(self, page_html: str) -> list:
        rows = []
        for tr in re.findall(r"<tr\b[^>]*>(.*?)</tr>", page_html, re.S):
            m = re.search(r'href="\?mode=view&amp;articleNo=(\d+)[^"]*"[^>]*class="c-board-title"[^>]*>(.*?)</a>', tr, re.S)
            if not m:
                continue
            tds = re.findall(r"<td\b[^>]*>(.*?)</td>", tr, re.S)
            date = next((norm_date(_strip(t)) for t in tds if re.fullmatch(r"\d{4}\.\d{2}\.\d{2}", _strip(t))), None)
            rows.append((m[1], _strip(m[2]), date))
        return rows

    def list_items(self, fx, *, limit, cursor, key=None):
        rows, seen = [], set()
        for p in range(MAX_PAGES):
            url = BASE if p == 0 else f"{BASE}?mode=list&articleLimit={PAGE_SIZE}&article.offset={p * PAGE_SIZE}"
            resp = self._get(fx, url)
            page_rows = self._rows(resp.text())
            if not page_rows:
                if p == 0:
                    raise SourceError("국무조정실: no board rows (blocked or layout changed)", resp)
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
        for art, title, date in rows[:limit]:
            durl = f"{BASE}?mode=view&articleNo={art}"
            dresp = self._get(fx, durl)
            t = dresp.text()
            files = [(urljoin(durl, html.unescape(h)), _strip(n)) for h, n in re.findall(
                r'<a\s+href="(\?mode=download&amp;articleNo=\d+&amp;attachNo=\d+)"[^>]*>(.*?)</a>', t, re.S)]
            best = pick_file(files)
            item = Item(key=art, title=title, source_url=durl, date=date,
                        institution=self.institution, institution_class=self.institution_class)
            if best:
                item.download_url, item.filename = best
            else:
                m = re.search(r'<div class="fr-view">(.*?)</div>\s*</div>', t, re.S)
                body = html_to_text(m[1]) if m else ""
                if not body:
                    continue
                item.text = md_doc(title, meta={"출처": "국무조정실 보도자료", "작성일": date, "원문": durl}, body=body)
            items.append(item)
        return items
