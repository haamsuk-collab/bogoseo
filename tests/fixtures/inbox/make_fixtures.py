#!/usr/bin/env python3
"""Generate the synthetic inbox PDFs used when `inbox_sample/` is absent.

All content is made up (generic 공문서 about a fictional civil-service counter).
Needs: reportlab + Pillow (requirements-dev.txt) and a Korean TTF (NanumGothic,
or any font found by `fc-list :lang=ko`; override with BOGOSEO_FIXTURE_FONT).

    .venv/bin/python tests/fixtures/inbox/make_fixtures.py

Fixture roles (see tests/test_warehouse.py, tests/test_quality.py):
  표본_개선안_1015.pdf             3 pages, image-only (OCR), author date 26. 10. 15.  -> 판본
  표본_개선안_1022_본문.pdf        digital text, author date 26. 10. 22.                -> 본문
  표본_개선안_참고자료_디지털.pdf  4 digital pages, starts with [참고 1]                -> 참고
  표본_개선안_참고자료_스캔.pdf    image-only (OCR), starts with [참고 2]                -> 참고
  초안_개선안_작성중.pdf           digital draft, □ markers, >= 300 Hangul syllables
"""
import io
import os
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.pagesizes import A4
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

OUT = Path(__file__).resolve().parent
TOPIC = "민원 안내 체계 개선 방안"
AUTHOR = "<민원과 사무관 이순신(0000), '26. 10. {d}.(목)>"

FONT_CANDIDATES = [
    "/usr/share/fonts/nanum/NanumGothic-Regular.ttf",
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",
    "/usr/share/fonts/nanum/NanumGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
]


def find_font() -> str:
    env = os.environ.get("BOGOSEO_FIXTURE_FONT")
    if env:
        return env
    for c in FONT_CANDIDATES:
        if Path(c).exists():
            return c
    try:
        out = subprocess.run(["fc-list", ":lang=ko", "file"], capture_output=True, text=True).stdout
        for ln in out.splitlines():
            p = ln.split(":")[0].strip()
            if p.lower().endswith((".ttf", ".otf")):
                return p
    except OSError:
        pass
    raise SystemExit("no Korean TTF found; set BOGOSEO_FIXTURE_FONT=/path/to/font.ttf")


FONT = find_font()
pdfmetrics.registerFont(TTFont("KO", FONT))

# ------------------------------------------------------------------ content
# Each file is a list of pages; each page a list of lines (already short enough to fit).

P1015 = [
    [TOPIC, "", AUTHOR.format(d=15), "",
     "□ 추진배경",
     "ㅇ 방문 민원인의 창구 대기시간 증가",
     "- 안내 표지 부족으로 담당 부서 혼선",
     "ㅇ 안내 체계 정비 요구 확대",
     "□ 현황",
     "ㅇ 일일 방문 민원인 약 400명",
     "- 전화 문의 비중 35%"],
    ["□ 문제점",
     "ㅇ 안내 담당자 배치 기준 없음",
     "- 창구별 업무 설명이 서로 다름",
     "ㅇ 안내 자료 갱신 주기 불규칙",
     "□ 개선방향",
     "ㅇ 안내 창구를 한 곳으로 통합한다.",
     "- 방문 목적별 접수 순서를 표준화함."],
    ["□ 추진계획",
     "ㅇ 1단계 안내 표지 정비",
     "- 층별 안내도 교체",
     "ㅇ 2단계 안내 담당자 교육",
     "- 분기별 1회 실시",
     "□ 기대효과",
     "ㅇ 대기시간 20% 단축 예상",
     "☞ 안내 체계 통합을 우선 추진"],
]

P1022 = [
    [TOPIC, "", AUTHOR.format(d=22), "",
     "□ 추진배경",
     "ㅇ 방문 민원인의 창구 대기시간 증가",
     "- 안내 표지 부족으로 담당 부서 혼선",
     "ㅇ 안내 체계 정비 요구 확대",
     "□ 현황",
     "ㅇ 일일 방문 민원인 약 400명",
     "- 전화 문의 비중 35%",
     "- 재방문 비율 12%"],
    ["□ 문제점",
     "ㅇ 안내 담당자 배치 기준 없음",
     "- 창구별 업무 설명이 서로 다름",
     "ㅇ 안내 자료 갱신 주기 불규칙",
     "□ 개선방향",
     "ㅇ 안내 창구를 한 곳으로 통합한다.",
     "- 방문 목적별 접수 순서를 표준화함.",
     "ㅇ 온라인 사전 안내를 병행한다."],
    ["□ 추진계획",
     "ㅇ 1단계 안내 표지 정비",
     "- 층별 안내도 교체",
     "ㅇ 2단계 안내 담당자 교육",
     "- 분기별 1회 실시",
     "ㅇ 3단계 만족도 조사",
     "- 반기별 1회 실시",
     "□ 소요예산",
     "ㅇ 총 3,000만 원 규모",
     "□ 기대효과",
     "ㅇ 대기시간 20% 단축 예상",
     "ㅇ 민원 만족도 10점 향상 기대",
     "☞ 안내 체계 통합을 우선 추진"],
]

REF_DIGITAL = [
    ["[참고 1] 민원 안내 체계 운영 현황",
     "ㅇ 안내 창구는 본관 1층과 별관 2층에 각각 운영되고 있다.",
     "ㅇ 창구별 업무 범위가 겹쳐 방문객이 여러 번 이동한다.",
     "ㅇ 안내 담당자는 교대 근무로 설명 기준이 일정하지 않다."],
    ["ㅇ 층별 안내도는 지난 개편 이후 갱신되지 않았다.",
     "ㅇ 외국어 안내 자료는 일부 창구에만 비치되어 있다.",
     "ㅇ 노약자 우선 접수 절차가 표지에 표시되지 않았다.",
     "ㅇ 대기번호 발급기 위치가 입구에서 멀다."],
    ["ㅇ 전화 문의 응대 지침은 담당자마다 다르게 해석된다.",
     "ㅇ 자주 묻는 질문 목록은 연 1회 수정에 그친다.",
     "ㅇ 온라인 안내 페이지의 접속 안내가 눈에 띄지 않는다.",
     "ㅇ 방문 전 예약 제도의 활용도가 낮은 편이다."],
    ["ㅇ 타 기관의 안내 창구 통합 사례를 참고할 필요가 있다.",
     "ㅇ 통합 이후 대기시간이 줄었다는 보고가 다수 있다.",
     "ㅇ 안내 담당자 교육 시간을 늘리면 오안내가 줄어든다.",
     "ㅇ 만족도 조사 결과는 분기마다 공유하는 것이 바람직하다."],
]

REF_SCAN = [
    ["[참고 2] 민원 안내 체계 개선 일정",
     "ㅇ 안내 표지 정비는 10월 중 착수한다.",
     "ㅇ 안내 담당자 교육은 11월부터 시작한다.",
     "ㅇ 만족도 조사는 12월에 첫 회를 실시한다.",
     "ㅇ 안내 창구 통합은 내년 1월 완료한다."],
    ["ㅇ 단계별 점검 회의를 매월 개최한다.",
     "ㅇ 점검 결과는 민원 안내 체계 개선 방안에 반영한다."],
]

DRAFT = [
    ["민원 안내 체계 개선 방안 (작성중)", "",
     "□ 추진배경",
     "ㅇ 방문 민원인이 늘어나면서 창구 앞 대기 줄이 길어지고 있다.",
     "ㅇ 안내 표지가 부족하여 담당 부서를 찾지 못하는 사례가 많다.",
     "- 방문객은 평균 두 번 이상 창구를 옮겨 다닌다.",
     "□ 현황 및 문제점",
     "ㅇ 안내 담당자의 배치 기준과 설명 기준이 정해져 있지 않다.",
     "ㅇ 창구마다 업무 범위가 겹쳐 같은 질문에 다른 답을 듣는다.",
     "- 안내 자료의 갱신 주기가 일정하지 않아 오래된 정보가 남아 있다.",
     "- 전화 문의 응대 지침이 담당자마다 다르게 적용되고 있다."],
    ["□ 개선방향",
     "ㅇ 안내 창구를 한 곳으로 통합하여 방문객의 이동을 줄인다.",
     "ㅇ 방문 목적별 접수 순서를 표준화하고 안내 표지를 새로 정비한다.",
     "- 층별 안내도와 대기번호 발급기의 위치를 입구 가까이로 옮긴다.",
     "□ 향후 추진계획",
     "ㅇ 안내 담당자 교육을 분기마다 실시하여 설명 기준을 맞춘다.",
     "ㅇ 온라인 사전 안내와 방문 예약 제도를 함께 활성화한다.",
     "- 만족도 조사를 반기마다 실시하고 결과를 공유한다.",
     "□ 기대효과",
     "ㅇ 대기시간이 줄고 민원 만족도가 높아질 것으로 기대한다."],
]

# ------------------------------------------------------------------ writers
W, H = A4


def text_pdf(path: Path, pages: list[list[str]], size: int = 12, lead: int = 24) -> None:
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(path.stem)
    for lines in pages:
        y = H - 80
        for ln in lines:
            if ln:
                c.setFont("KO", size)
                c.drawString(60, y, ln)
            y -= lead
        c.showPage()
    c.save()


def image_pdf(path: Path, pages: list[list[str]], dpi: int = 150, size_pt: int = 15) -> None:
    """Rasterise each page with Pillow and embed it as a picture (no text layer)."""
    px_w, px_h = int(8.27 * dpi), int(11.69 * dpi)
    font = ImageFont.truetype(FONT, int(size_pt * dpi / 72 * 1.25))
    c = canvas.Canvas(str(path), pagesize=A4, invariant=1)
    c.setTitle(path.stem)
    for lines in pages:
        im = Image.new("L", (px_w, px_h), 255)
        d = ImageDraw.Draw(im)
        y = int(1.1 * dpi)
        for ln in lines:
            if ln:
                d.text((int(0.9 * dpi), y), ln, font=font, fill=0)
            y += int(0.42 * dpi)
        buf = io.BytesIO()
        im.save(buf, "PNG", optimize=True)
        buf.seek(0)
        c.drawImage(ImageReader(buf), 0, 0, width=W, height=H)
        c.showPage()
    c.save()


def main() -> None:
    image_pdf(OUT / "표본_개선안_1015.pdf", P1015)
    text_pdf(OUT / "표본_개선안_1022_본문.pdf", P1022)
    text_pdf(OUT / "표본_개선안_참고자료_디지털.pdf", REF_DIGITAL)
    image_pdf(OUT / "표본_개선안_참고자료_스캔.pdf", REF_SCAN)
    text_pdf(OUT / "초안_개선안_작성중.pdf", DRAFT)
    for p in sorted(OUT.glob("*.pdf")):
        print(f"{p.name}  {p.stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
