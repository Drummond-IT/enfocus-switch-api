// Makes one PDF per defect the portal preflight must catch, plus expected.json (what PitStop should report, and
// whether "Portal auto-fix" should fix it). Run: npm install && npm run make   (writes ./out/)
// Every file is 8.5 x 11 in unless the defect is the size. Nothing here is customer artwork.
// Text uses an embedded subset of DejaVu Sans (free license), or the TrueType font at $FONT, so only file 06 has a
// font problem. The made files are committed in out/, so running this is only needed to change them.
const FONT = readFileSync(process.env.FONT || '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf');
import { PDFDocument, PDFName, rgb, cmyk } from 'pdf-lib';
import fontkit from '@pdf-lib/fontkit';
import { readFileSync } from 'node:fs';
import { deflateSync } from 'node:zlib';
import { mkdirSync, writeFileSync } from 'node:fs';

const IN = 72, B = 0.125 * IN, W = 8.5 * IN, H = 11 * IN;
mkdirSync('out', { recursive: true });

// A flat RGB PNG, w x h pixels (stands in for a photo).
function png(w, h, shade = 170) {
  const table = [...Array(256)].map((_, n) => { let c = n; for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1; return c >>> 0; });
  const crc = b => { let x = 0xffffffff; for (const v of b) x = table[(x ^ v) & 255] ^ (x >>> 8); return (x ^ 0xffffffff) >>> 0; };
  const chunk = (t, d) => { const l = Buffer.alloc(4); l.writeUInt32BE(d.length); const td = Buffer.concat([Buffer.from(t), d]); const c = Buffer.alloc(4); c.writeUInt32BE(crc(td)); return Buffer.concat([l, td, c]); };
  const ih = Buffer.alloc(13); ih.writeUInt32BE(w, 0); ih.writeUInt32BE(h, 4); ih[8] = 8; ih[9] = 2;
  const raw = Buffer.alloc((w * 3 + 1) * h, shade); for (let y = 0; y < h; y++) raw[y * (w * 3 + 1)] = 0;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', ih), chunk('IDAT', deflateSync(raw)), chunk('IEND', Buffer.alloc(0))]);
}

// A page with trim at w x h inches and 1/8" bleed, CMYK background to the bleed edge and CMYK text.
async function base({ w = 8.5, h = 11, bleed = true, trimBox = true } = {}) {
  const doc = await PDFDocument.create(); doc.registerFontkit(fontkit);
  const b = bleed ? B : 0, pw = w * IN + 2 * b, ph = h * IN + 2 * b;
  const page = doc.addPage([pw, ph]);
  page.drawRectangle({ x: 0, y: 0, width: pw, height: ph, color: cmyk(0.05, 0, 0.1, 0) });
  if (trimBox) { page.setTrimBox(b, b, w * IN, h * IN); page.setBleedBox(0, 0, pw, ph); }
  const font = await doc.embedFont(FONT, { subset: true });
  page.drawText('Drummond preflight test', { x: b + 54, y: ph - b - 90, size: 24, font, color: cmyk(0, 0, 0, 1) });
  doc.setProducer('Drummond preflight test set');
  return { doc, page, b, pw, ph, font };
}
const save = async (name, doc) => writeFileSync(`out/${name}`, await doc.save({ useObjectStreams: false }));

const expected = [];
const add = (file, defect, category, autofix, notes) => expected.push({ file, defect, category, severity: category ? (autofix ? 'warning' : 'error') : 'pass', autofix, notes });

// 01 clean: should pass.
{ const { doc } = await base(); await save('01-clean-letter-bleed.pdf', doc); add('01-clean-letter-bleed.pdf', 'none', null, false, 'Must pass with no errors or warnings.'); }

// 02 RGB image at 300 ppi.
{ const { doc, page, b } = await base(); const img = await doc.embedPng(png(1500, 1200)); page.drawImage(img, { x: b + 54, y: b + 200, width: 5 * IN, height: 4 * IN });
  await save('02-rgb-image.pdf', doc); add('02-rgb-image.pdf', 'RGB image, 300 ppi', 'rgb_color', true, 'Auto-fix converts to press CMYK.'); }

// 03 RGB vector fill.
{ const { doc, page, b } = await base(); page.drawRectangle({ x: b + 54, y: b + 200, width: 300, height: 200, color: rgb(0.9, 0.1, 0.2) });
  await save('03-rgb-vector.pdf', doc); add('03-rgb-vector.pdf', 'RGB vector fill', 'rgb_color', true, 'Auto-fix converts to press CMYK.'); }

// 04 no bleed: background stops at the trim (Canva PDF Standard export).
{ const { doc } = await base({ bleed: false }); await save('04-no-bleed.pdf', doc); add('04-no-bleed.pdf', 'Background to the trim, no bleed', 'missing_bleed', true, 'Auto-fix mirrors 0.125 in bleed; prepress reviews.'); }

// 05 low resolution: a 96 ppi screen export placed full page (Canva PNG at 816 x 1056).
{ const { doc, page } = await base({ bleed: false }); const img = await doc.embedPng(png(816, 1056)); page.drawImage(img, { x: 0, y: 0, width: W, height: H });
  await save('05-low-res-96ppi.pdf', doc); add('05-low-res-96ppi.pdf', '96 ppi image, full page, RGB, no bleed', 'low_resolution', false, 'Customer must supply a larger image; RGB and bleed also reported.'); }

// 06 font not embedded (a non-standard font with no FontFile).
{ const { doc, page, b } = await base(); const ctx = doc.context;
  const font = ctx.register(ctx.obj({ Type: 'Font', Subtype: 'TrueType', BaseFont: 'Montserrat-Regular', FirstChar: 32, LastChar: 126, Encoding: 'WinAnsiEncoding',
    FontDescriptor: ctx.register(ctx.obj({ Type: 'FontDescriptor', FontName: 'Montserrat-Regular', Flags: 32, FontBBox: [-200, -200, 1000, 900], ItalicAngle: 0, Ascent: 900, Descent: -200, CapHeight: 700, StemV: 80 })) }));
  page.node.Resources().get(PDFName.of('Font')).set(PDFName.of('FMont'), font);
  const s = ctx.stream(`BT /FMont 18 Tf ${b + 54} ${b + 400} Td (Montserrat is not embedded) Tj ET`); page.node.addContentStream(ctx.register(s));
  await save('06-font-not-embedded.pdf', doc); add('06-font-not-embedded.pdf', 'Montserrat-Regular not embedded', 'fonts_not_embedded', false, 'Customer re-exports with fonts embedded (PDF/X-4).'); }

// 07 hairline 0.1 pt.
{ const { doc, page, b } = await base(); page.drawLine({ start: { x: b + 54, y: b + 300 }, end: { x: b + 500, y: b + 300 }, thickness: 0.1, color: cmyk(0, 0, 0, 1) });
  await save('07-hairline-0.1pt.pdf', doc); add('07-hairline-0.1pt.pdf', '0.1 pt line', 'thin_lines', true, 'Auto-fix thickens to 0.25 pt.'); }

// 08 white overprint: a white box set to overprint (disappears in print).
{ const { doc, page, b } = await base(); const ctx = doc.context;
  const gs = ctx.register(ctx.obj({ Type: 'ExtGState', OP: true, op: true, OPM: 1 }));
  const res = page.node.Resources(); if (!res.get(PDFName.of('ExtGState'))) res.set(PDFName.of('ExtGState'), ctx.obj({})); res.get(PDFName.of('ExtGState')).set(PDFName.of('GSop'), gs);
  page.node.addContentStream(ctx.register(ctx.stream(`q /GSop gs 0 0 0 0 k ${b + 54} ${b + 250} 200 100 re f Q`)));
  await save('08-white-overprint.pdf', doc); add('08-white-overprint.pdf', 'White fill set to overprint', 'overprint', true, 'Auto-fix removes white overprint.'); }

// 09 wrong size: 8.25 x 10.75 in (A4-ish rounding from a design app).
{ const { doc } = await base({ w: 8.25, h: 10.75 }); await save('09-wrong-size-8.25x10.75.pdf', doc); add('09-wrong-size-8.25x10.75.pdf', 'Trim 8.25 x 10.75 in on a letter job', 'page_size', false, 'Fails against a letter job spec; scale-to-trim is a review fix only.'); }

// 10 crop marks, no TrimBox (Canva "PDF Print" with crop marks and bleed): media 0.5 in larger, marks drawn.
{ const doc = await PDFDocument.create(); const m = 0.5 * IN, pw = W + 2 * m, ph = H + 2 * m; const page = doc.addPage([pw, ph]);
  page.drawRectangle({ x: m - B, y: m - B, width: W + 2 * B, height: H + 2 * B, color: cmyk(0.05, 0, 0.1, 0) });
  for (const [x, y] of [[m, m], [m + W, m], [m, m + H], [m + W, m + H]]) {
    page.drawLine({ start: { x, y: y < ph / 2 ? 0 : ph }, end: { x, y: y < ph / 2 ? m - B - 4 : m + H + B + 4 }, thickness: 0.25, color: cmyk(1, 1, 1, 1) });
    page.drawLine({ start: { x: x < pw / 2 ? 0 : pw, y }, end: { x: x < pw / 2 ? m - B - 4 : m + W + B + 4, y }, thickness: 0.25, color: cmyk(1, 1, 1, 1) });
  }
  await save('10-crop-marks-no-trimbox.pdf', doc); add('10-crop-marks-no-trimbox.pdf', 'Crop marks drawn, no TrimBox, media 9.5 x 12 in', 'page_size', true, 'Auto-fix sets TrimBox from crop marks and removes the marks; prepress reviews.'); }

// 11 mixed page sizes.
{ const { doc } = await base(); doc.addPage([5.5 * IN, 8.5 * IN]); await save('11-mixed-page-sizes.pdf', doc); add('11-mixed-page-sizes.pdf', 'Page 2 is 5.5 x 8.5 in', 'page_size', false, 'Customer fixes page setup.'); }

// 12 rich black small text (registration-style 4-color black at 6 pt).
{ const { doc, page, b, font } = await base(); page.drawText('Small rich black text 6 pt', { x: b + 54, y: b + 150, size: 6, font, color: cmyk(1, 1, 1, 1) });
  await save('12-registration-black-text.pdf', doc); add('12-registration-black-text.pdf', '6 pt text in 400% black', 'registration_black', true, 'Auto-fix sets small black text to 100K.'); }

writeFileSync('out/expected.json', JSON.stringify(expected, null, 2) + '\n');
console.log(`Wrote ${expected.length} files to out/`);
