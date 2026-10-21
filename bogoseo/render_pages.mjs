// Render PDF pages to PNG with pdfjs-dist + @napi-rs/canvas (both are kordoc dependencies).
// usage: node render_pages.mjs <pdf> <outDir> [scale]
import { createRequire } from "node:module";
import fs from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";

const require = createRequire(import.meta.url);
const [pdf, outDir, scaleArg] = process.argv.slice(2);
const scale = Number(scaleArg || 2);
const root = path.resolve(path.dirname(new URL(import.meta.url).pathname), "..", "node_modules");
const pdfjs = await import(pathToFileURL(path.join(root, "pdfjs-dist/legacy/build/pdf.mjs")).href);
const { createCanvas } = require(path.join(root, "@napi-rs/canvas"));
const data = new Uint8Array(fs.readFileSync(pdf));
const doc = await pdfjs.getDocument({ data, useSystemFonts: true, disableFontFace: true, verbosity: 0 }).promise;
fs.mkdirSync(outDir, { recursive: true });
const out = [];
for (let n = 1; n <= doc.numPages; n++) {
  const page = await doc.getPage(n);
  const vp = page.getViewport({ scale });
  const canvas = createCanvas(Math.ceil(vp.width), Math.ceil(vp.height));
  await page.render({ canvasContext: canvas.getContext("2d"), viewport: vp }).promise;
  const p = path.join(outDir, `page-${String(n).padStart(3, "0")}.png`);
  fs.writeFileSync(p, canvas.toBuffer("image/png"));
  out.push(p);
}
console.log(JSON.stringify(out));
