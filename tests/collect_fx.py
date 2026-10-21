"""Fixture-serving runner for collector tests: replaces the insane-search subprocess."""
import base64
import json
import re
from pathlib import Path

FX = Path(__file__).resolve().parent / "fixtures" / "collect"


class FixtureRunner:
    def __init__(self, slug):
        self.dir = FX / slug
        self.index = json.loads((self.dir / "index.json").read_text(encoding="utf-8"))
        self.urls = []

    def __call__(self, url, timeout):
        self.urls.append(url)
        for e in self.index:
            if re.search(e["match"], url):
                body = (self.dir / e["file"]).read_bytes()
                st = e.get("status", 200)
                return {"ok": st < 400, "status": st, "last_code": st, "content_type": e.get("content_type", ""),
                        "content_disposition": e.get("content_disposition", ""), "final_url": url,
                        "trace": [{"executor": "curl_cffi", "impersonate": "safari", "status": st, "verdict": "ok"}],
                        "body_b64": base64.b64encode(body).decode()}
        return {"ok": False, "status": 404, "last_code": 404, "trace": [], "body_b64": "", "content_type": ""}


def fake_time():
    t = {"now": 1000.0}
    sleeps = []

    def clock():
        return t["now"]

    def sleep(s):
        sleeps.append(s)
        t["now"] += s
    return clock, sleep, sleeps, t


SOURCES = {  # source name -> (fixture slug)
    "정책브리핑": "policy", "국회예산정책처": "nabo", "국회입법조사처": "nars", "국회도서관": "nanet",
    "법제처": "law", "열린국회정보": "assembly", "행정안전부_정부 공문서 AI 학습데이터": "mois",
    "재정경제부_보도자료": "moef", "과학기술정보통신부_보도자료": "msit", "국무조정실": "opm",
    "정보공개포털": "open", "국방부": "mnd", "의안정보시스템": "likms", "서울시 정보소통광장": "opengov", "감사원": "bai",
}
