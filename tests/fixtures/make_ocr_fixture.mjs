// Generates tests/fixtures/ocr_page.png (synthetic page, no real document content).
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
const require = createRequire(import.meta.url);
const here = path.dirname(new URL(import.meta.url).pathname);
const { createCanvas, GlobalFonts } = require(path.join(here, "../../node_modules/@napi-rs/canvas"));
GlobalFonts.registerFromPath("/usr/share/fonts/nanum/NanumGothic-ExtraBold.ttf", "KoFont");
const c = createCanvas(1200, 700);
const g = c.getContext("2d");
g.fillStyle = "#fff"; g.fillRect(0, 0, 1200, 700);
g.fillStyle = "#000"; g.font = "44px KoFont";
const lines = ["시험문제 출제방식 개선 보고", "비합숙 출제방식 도입을 검토함", "문제은행 최신화 완료 예정", "병과별 소관부서 담당자 제출"];
lines.forEach((t, i) => g.fillText(t, 80, 120 + i * 120));
fs.writeFileSync(path.join(here, "ocr_page.png"), c.toBuffer("image/png"));
