"""의안정보시스템 (likms.assembly.go.kr/bill): bill texts (의안원문) of the National Assembly.

GET-only route: the home page lists recent bills (billId, 의안번호, date, title); the JSON endpoint
``/bi/dwld/selectFileList.do?billId=`` answers GET with the bill's documents and filegate download
URLs (hwp ``type=0``, pdf ``type=1``).
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from .sources import Adapter, Item, SourceError, norm_date, pick_file, register

BASE = "https://likms.assembly.go.kr"
HOME = BASE + "/bill/main.do"
DETAIL = BASE + "/bill/bi/billDetailPage.do?billId="
FILES = BASE + "/bill/bi/dwld/selectFileList.do?billId="


def _clean_name(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", s).strip("_")[:80]


def parse_home(html: str) -> list:
    """[(billId, billNo|None, date|None, title)] in page order, de-duplicated."""
    out, seen = [], set()
    for p in re.split(r'(?=<a href="/bill/bi/billDetailPage\.do\?billId=)', html)[1:]:
        m = re.match(r'<a href="/bill/bi/billDetailPage\.do\?billId=(PRC_\w+)', p)
        if not m or m[1] in seen:
            continue
        seen.add(m[1])
        title = re.search(r'title_roll">([^<]*)', p)
        no = re.search(r"<dt>(\d+)</dt>", p)
        dt = re.search(r'class="date">([^<]*)', p)
        out.append((m[1], no[1] if no else None, norm_date(dt[1]) if dt else None,
                    re.sub(r"\s+", " ", title[1]).strip() if title else ""))
    return out


@register
class LikmsWeb(Adapter):
    name = "의안정보시스템"
    kind = "web"
    institution = "국회사무처"
    institution_class = "국회"
    endpoint = HOME

    def list_items(self, fx, *, limit, cursor, key=None):
        home = fx.get(HOME)
        if not home.ok and (home.blocked or not home.body):
            raise SourceError("blocked" if home.blocked else f"{self.name}: list page not usable ({home.code})", home)
        rows = parse_home(home.text())
        if not rows:
            raise SourceError(f"{self.name}: no bills found on the home page", home)
        rows.sort(key=lambda r: r[2] or "", reverse=True)  # stable: page order inside a date
        items, fetched = [], 0
        for bill_id, bill_no, date, title in rows:
            if len(items) >= limit or fetched >= limit + 10:
                break
            if cursor and date and date < cursor:
                continue
            fetched += 1
            resp = fx.get(FILES + bill_id)
            if not resp.ok:
                continue
            try:
                docs = resp.json().get("billFileList") or []
            except ValueError:
                continue
            docs.sort(key=lambda d: d.get("docKindCd") != "DKC_0020")  # 의안원문 first
            files = []
            for d in docs[:1]:
                for k, ext in (("pdfFileDwnldUrl", "pdf"), ("hwpFileDwnldUrl", "hwp")):
                    if d.get(k):
                        base = _clean_name(f"{d.get('billNo') or bill_no or bill_id}_{title}_{d.get('docKindName') or '의안원문'}")
                        files.append((urljoin(BASE, d[k]), f"{base}.{ext}"))
            best = pick_file(files)
            if not best:
                continue  # documents are not registered yet; picked up on a later run
            items.append(Item(
                key=bill_id, title=f"[{bill_no}] {title}" if bill_no else title,
                source_url=DETAIL + bill_id, date=date, download_url=best[0], filename=best[1],
                institution=self.institution, institution_class=self.institution_class))
        return items
