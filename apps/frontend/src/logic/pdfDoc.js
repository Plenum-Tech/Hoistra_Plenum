// pdfDoc — a small PDF writer for the documents the app hands people (report exports).
//
// No library: the app ships almost none, and what a report needs is text, rules, bars and
// rings on A4 — a few hundred lines, not a dependency. Text is set in the PDF standard fonts
// (Helvetica, Helvetica-Bold, Courier), which every reader has, so nothing is embedded and a
// one-page report is a few kilobytes. Those fonts carry Windows-1252 only: winAnsi() maps
// what it can (curly quotes, dashes, £, €, ², ·), spells out a few symbols (→ as ->), drops
// emoji and shows anything else as "?" rather than a wrong glyph.
//
// Widths are the fonts' own metrics (Adobe AFM, as reportlab ships them), so wrapped text
// fits its column in any reader. Coordinates in the API run top-down from the page's top
// edge, in points; the writer flips them for PDF. Pure - tested in test/pdfDoc.test.mjs.

export const A4 = { w: 595.28, h: 841.89 };

// Advance widths per 1000 units for codes 32-255 (Windows-1252), from the AFM files.
const HELVETICA = [
  278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,
  584,584,584,556,1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,
  667,667,611,278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,
  278,556,500,722,500,500,500,334,260,334,584,761,556,0,222,556,333,1000,556,556,333,1000,667,333,1000,0,611,0,
  0,222,222,333,333,350,556,1000,333,1000,500,333,944,0,500,667,278,333,556,556,556,556,260,556,333,737,370,556,
  584,333,737,333,400,584,333,333,333,556,537,278,333,333,365,556,834,834,834,611,667,667,667,667,667,667,1000,722,
  667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,
  556,556,889,500,556,556,556,556,278,278,278,278,556,556,556,556,556,556,556,584,611,556,556,556,556,500,556,500
];
const HELVETICA_BOLD = [
  278,333,474,556,556,889,722,238,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,333,333,
  584,584,584,611,975,722,722,722,722,667,611,778,722,278,556,722,611,833,722,778,667,778,722,667,611,722,667,944,
  667,667,611,333,278,333,584,556,333,556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,
  333,611,556,778,556,556,500,389,280,389,584,761,556,0,278,556,500,1000,556,556,333,1000,667,333,1000,0,611,0,
  0,278,278,500,500,350,556,1000,333,1000,556,333,944,0,500,667,278,333,556,556,556,556,280,556,333,737,370,556,
  584,333,737,333,400,584,333,333,333,611,556,278,333,333,365,556,834,834,834,611,722,722,722,722,722,722,1000,722,
  667,667,667,667,278,278,278,278,722,722,778,778,778,778,778,584,778,722,722,722,722,667,667,611,556,556,556,556,
  556,556,889,556,556,556,556,556,278,278,278,278,611,611,611,611,611,611,611,584,611,611,611,611,611,556,611,556
];
const FONTS = {
  regular: { id: 'F1', base: 'Helvetica', widths: HELVETICA },
  bold: { id: 'F2', base: 'Helvetica-Bold', widths: HELVETICA_BOLD },
  mono: { id: 'F3', base: 'Courier', widths: null },          // every glyph is 600
  monoBold: { id: 'F4', base: 'Courier-Bold', widths: null }
};

// Unicode -> Windows-1252 for the 0x80-0x9F block; Latin-1 (0xA0-0xFF) maps to itself.
const CP1252 = {
  0x20AC: 0x80, 0x201A: 0x82, 0x0192: 0x83, 0x201E: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87,
  0x02C6: 0x88, 0x2030: 0x89, 0x0160: 0x8A, 0x2039: 0x8B, 0x0152: 0x8C, 0x017D: 0x8E, 0x2018: 0x91,
  0x2019: 0x92, 0x201C: 0x93, 0x201D: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02DC: 0x98,
  0x2122: 0x99, 0x0161: 0x9A, 0x203A: 0x9B, 0x0153: 0x9C, 0x017E: 0x9E, 0x0178: 0x9F
};
// Symbols the fonts lack, spelled the way a person would type them.
const SPELLED = {
  0x2192: '->', 0x2190: '<-', 0x2194: '<->', 0x21D2: '=>', 0x2264: '<=', 0x2265: '>=', 0x2260: '!=',
  0x2248: '~', 0x2212: '-', 0x2011: '-', 0x2010: '-', 0x2032: "'", 0x2033: '"', 0x2713: 'v', 0x2714: 'v',
  0x2715: 'x', 0x2717: 'x', 0x2718: 'x', 0x25B2: '^', 0x25BC: 'v', 0x2191: '^', 0x2193: 'v',
  0x2009: ' ', 0x200A: ' ', 0x202F: ' ', 0x2007: ' ', 0x2002: ' ', 0x2003: ' ', 0x2060: '', 0x200B: '',
  0x200C: '', 0x200D: '', 0xFE0F: '', 0x2028: ' ', 0x2029: ' ', 0x2044: '/', 0x2215: '/', 0x00AD: ''
};
const isEmoji = (cp) => (cp >= 0x1F000 && cp <= 0x1FAFF) || (cp >= 0x2600 && cp <= 0x27BF) || (cp >= 0x1F1E6 && cp <= 0x1F1FF);

// The text as Windows-1252 codes. Control characters become spaces (a tab or newline inside
// a run would otherwise print as a box).
export function winAnsi(text) {
  const out = [];
  for (const ch of String(text == null ? '' : text)) {
    const cp = ch.codePointAt(0);
    if (cp < 32 || cp === 127) { out.push(32); continue; }
    if (cp < 127 || (cp >= 0xA0 && cp <= 0xFF)) { out.push(cp); continue; }
    if (CP1252[cp]) { out.push(CP1252[cp]); continue; }
    if (SPELLED[cp] !== undefined) { for (const c of SPELLED[cp]) out.push(c.charCodeAt(0)); continue; }
    if (isEmoji(cp)) continue;
    out.push(63); // '?'
  }
  return out;
}

const widthOf = (code, font) => (font.widths ? (font.widths[code - 32] || 556) : 600);

// `tracking` is the extra space after each character, as text() sets it.
export function textWidth(text, fontKey, size, tracking) {
  const font = FONTS[fontKey] || FONTS.regular;
  const codes = winAnsi(text);
  let w = 0;
  codes.forEach((c) => { w += widthOf(c, font); });
  return (w * size) / 1000 + (tracking || 0) * codes.length;
}

// Words broken into lines no wider than `width`. A word longer than the line is cut where it
// stops fitting rather than running off the page.
export function wrapText(text, fontKey, size, width, tracking) {
  const lines = [];
  String(text == null ? '' : text).split(/\n/).forEach((para) => {
    const words = para.split(/\s+/).filter(Boolean);
    let line = '';
    words.forEach((word) => {
      const next = line ? line + ' ' + word : word;
      if (textWidth(next, fontKey, size, tracking) <= width) { line = next; return; }
      if (line) lines.push(line);
      line = word;
      while (textWidth(line, fontKey, size, tracking) > width && line.length > 1) {
        let cut = line.length - 1;
        while (cut > 1 && textWidth(line.slice(0, cut), fontKey, size, tracking) > width) cut -= 1;
        lines.push(line.slice(0, cut));
        line = line.slice(cut);
      }
    });
    lines.push(line);
  });
  return lines;
}

// A string as a PDF literal: ASCII as itself, everything else as an octal escape, so the
// file stays 7-bit and every byte offset in the cross-reference table is a character offset.
function literal(codes) {
  let s = '(';
  codes.forEach((c) => {
    if (c === 40 || c === 41 || c === 92) s += '\\' + String.fromCharCode(c);
    else if (c >= 32 && c < 127) s += String.fromCharCode(c);
    else s += '\\' + c.toString(8).padStart(3, '0');
  });
  return s + ')';
}

const n = (v) => (Math.round(v * 100) / 100).toString();
export function rgb(hex) {
  const h = String(hex || '#000000').replace('#', '');
  const v = parseInt(h.length === 3 ? h.split('').map((c) => c + c).join('') : h, 16);
  return [((v >> 16) & 255) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255].map((x) => n(x)).join(' ');
}

// A UTF-16BE hex string with its byte-order mark - how the document's title reaches a
// reader's title bar intact, whatever script it is in.
function utf16(text) {
  let hex = 'FEFF';
  for (let i = 0; i < text.length; i++) hex += text.charCodeAt(i).toString(16).toUpperCase().padStart(4, '0');
  return '<' + hex + '>';
}

export function createPdf(opts) {
  const o = opts || {};
  const W = A4.w, H = A4.h;
  const pages = [];
  let ops = null;
  const page = () => { ops = []; pages.push(ops); return pages.length; };
  page();
  const y = (top) => n(H - top);

  return {
    W: W, H: H,
    get pageCount() { return pages.length; },
    get page() { return pages.length; },
    addPage: page,
    // Draws on an earlier page (a footer that needs the final page count).
    onPage(i, fn) { const cur = ops; ops = pages[i - 1]; try { fn(); } finally { ops = cur; } },
    // `top` is the text's baseline, measured from the top of the page.
    text(str, x, top, s) {
      const st = s || {};
      const font = FONTS[st.font] || FONTS.regular;
      const codes = winAnsi(str);
      if (!codes.length) return;
      // Character spacing is set on every run: it is text state, which outlives ET, so a
      // tracked label would otherwise spread every line drawn after it.
      ops.push('BT /' + font.id + ' ' + n(st.size || 10) + ' Tf ' + rgb(st.color) + ' rg ' + n(st.tracking || 0) + ' Tc '
        + n(x) + ' ' + y(top) + ' Td ' + literal(codes) + ' Tj ET');
    },
    rect(x, top, w, h, color) {
      if (!(w > 0) || !(h > 0)) return;
      ops.push(rgb(color) + ' rg ' + n(x) + ' ' + n(H - top - h) + ' ' + n(w) + ' ' + n(h) + ' re f');
    },
    line(x1, top1, x2, top2, color, width) {
      ops.push(rgb(color) + ' RG ' + n(width || 0.5) + ' w ' + n(x1) + ' ' + y(top1) + ' m ' + n(x2) + ' ' + y(top2) + ' l S');
    },
    // An arc of a circle centred at (cx, cy-from-top), from 12 o'clock clockwise through
    // `frac` of a turn, stroked. frac 1 is the whole ring.
    arc(cx, cyTop, r, frac, color, width) {
      const f = Math.max(0, Math.min(1, frac));
      if (f <= 0) return;
      const cy = H - cyTop;
      const segs = Math.max(1, Math.ceil(f * 4));
      const total = f * Math.PI * 2;
      const step = total / segs;
      const k = (4 / 3) * Math.tan(step / 4);
      // Angle measured clockwise from 12 o'clock.
      const pt = (a) => [cx + r * Math.sin(a), cy + r * Math.cos(a)];
      const tan = (a) => [Math.cos(a), -Math.sin(a)];
      let a0 = 0;
      let p = pt(a0);
      let path = n(p[0]) + ' ' + n(p[1]) + ' m';
      for (let i = 0; i < segs; i++) {
        const a1 = a0 + step;
        const p1 = pt(a1), t0 = tan(a0), t1 = tan(a1);
        const c1 = [p[0] + k * r * t0[0], p[1] + k * r * t0[1]];
        const c2 = [p1[0] - k * r * t1[0], p1[1] - k * r * t1[1]];
        path += ' ' + [c1[0], c1[1], c2[0], c2[1], p1[0], p1[1]].map(n).join(' ') + ' c';
        a0 = a1; p = p1;
      }
      ops.push(rgb(color) + ' RG ' + n(width || 1) + ' w 1 J ' + path + ' S 0 J');
    },
    // The file. Objects: 1 catalog, 2 page tree, 3-6 fonts, 7 info, then a page and its
    // content stream per page.
    save(meta) {
      const m = meta || {};
      const objs = [];
      const fontIds = ['regular', 'bold', 'mono', 'monoBold'];
      const firstPage = 8;
      const kids = pages.map((_, i) => (firstPage + i * 2) + ' 0 R').join(' ');
      objs[1] = '<< /Type /Catalog /Pages 2 0 R >>';
      objs[2] = '<< /Type /Pages /Kids [' + kids + '] /Count ' + pages.length + ' >>';
      fontIds.forEach((k, i) => {
        objs[3 + i] = '<< /Type /Font /Subtype /Type1 /BaseFont /' + FONTS[k].base + ' /Encoding /WinAnsiEncoding >>';
      });
      const d = m.date instanceof Date ? m.date : new Date();
      const pad = (v) => String(v).padStart(2, '0');
      const stamp = 'D:' + d.getUTCFullYear() + pad(d.getUTCMonth() + 1) + pad(d.getUTCDate())
        + pad(d.getUTCHours()) + pad(d.getUTCMinutes()) + pad(d.getUTCSeconds()) + 'Z';
      objs[7] = '<< /Title ' + utf16(String(m.title || 'Report')) + ' /Producer ' + utf16(String(m.producer || 'Hoistra'))
        + (m.author ? ' /Author ' + utf16(String(m.author)) : '') + ' /CreationDate (' + stamp + ') >>';
      const fontRes = fontIds.map((k, i) => '/' + FONTS[k].id + ' ' + (3 + i) + ' 0 R').join(' ');
      pages.forEach((p, i) => {
        const pageObj = firstPage + i * 2;
        const stream = p.join('\n');
        objs[pageObj] = '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ' + n(W) + ' ' + n(H) + '] /Resources << /Font << '
          + fontRes + ' >> >> /Contents ' + (pageObj + 1) + ' 0 R >>';
        objs[pageObj + 1] = '<< /Length ' + stream.length + ' >>\nstream\n' + stream + '\nendstream';
      });
      let out = '%PDF-1.4\n%\u00e2\u00e3\u00cf\u00d3\n';
      const offsets = [];
      for (let i = 1; i < objs.length; i++) {
        offsets[i] = out.length;
        out += i + ' 0 obj\n' + objs[i] + '\nendobj\n';
      }
      const xref = out.length;
      out += 'xref\n0 ' + objs.length + '\n0000000000 65535 f \n';
      for (let i = 1; i < objs.length; i++) out += String(offsets[i]).padStart(10, '0') + ' 00000 n \n';
      out += 'trailer\n<< /Size ' + objs.length + ' /Root 1 0 R /Info 7 0 R >>\nstartxref\n' + xref + '\n%%EOF\n';
      const bytes = new Uint8Array(out.length);
      for (let i = 0; i < out.length; i++) bytes[i] = out.charCodeAt(i) & 255;
      return bytes;
    }
  };
}
