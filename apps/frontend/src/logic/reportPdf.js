// reportPdf — a report card's refresh as a PDF, set like the page it was exported from.
//
// Export used to write markdown (8 Oct 2026: "it should be downloaded as a pdf not md file").
// The document carries the header the page shows - what the report asks, when it refreshed,
// its cadence - then every card of the answer in the page's order: the duration bars, the
// figure rings, the register strip, the assessment, actions, groups, insights, the
// certificate table, and for a plain answer its sections, figures and tables. Cards put in
// the tray are included: the tray hides a card from the page, never from the answer.
//
// Text is the reader's, not the platform's: every string passes scrubInternal() as it does on
// screen, and the run's tool names - internal - are not printed. Pure: (card, run, cards,
// opts) in, PDF bytes out. Tested in test/reportPdf.test.mjs.
import { createPdf, wrapText, textWidth } from './pdfDoc.js';
import { scrubInternal } from './publicText.js';
import { parseMarkdownTables } from './reportCharts.js';
import { fmtDateTime } from './homeLive.js';

// The light theme's ink, rules and status colours (styles/tokens.css).
const C = {
  ink: '#1A1A18', ink2: '#5C5A54', ink3: '#6E6B63', rule: '#E7E4DB', track: '#F4F2EC',
  accent: '#FF5A1F', ok: '#15703A', tint: '#FFEDE6', quiet: '#8E8B82', faint: '#CFCCC2'
};
const VAR = {
  'var(--st-risk)': C.accent, 'var(--st-warn)': C.accent, 'var(--color-accent)': C.accent,
  'var(--st-ok)': C.ok, 'var(--color-neutral-500)': C.ink3, 'var(--color-neutral-600)': C.ink3
};
const colorOf = (v) => VAR[v] || (/^#[0-9a-f]{3,6}$/i.test(String(v || '')) ? v : C.ink3);
const SEVERITY = { critical: C.accent, risk: C.accent, warning: C.accent, anomaly: C.accent, info: C.accent, ok: C.ok };
const sevColor = (s) => SEVERITY[String(s || '').toLowerCase()] || C.ink3;
// A distribution's categories, coloured as the page colours them (ReportCharts.jsx seriesColor).
const SERIES = [C.accent, C.accent, C.accent, C.ok, C.ink3, C.ink3];
const PRIORITY = { critical: C.accent, high: C.accent, highest: C.accent, urgent: C.accent, medium: C.accent, normal: C.accent,
  low: C.ok, lowest: C.ok, open: C.accent, closed: C.ok, lapsed: C.accent, expired: C.accent, valid: C.ok, pending: C.accent,
  draft: C.ink3, held: C.accent };
const seriesColor = (value, i) => PRIORITY[String(value || '').trim().toLowerCase()] || SERIES[i % SERIES.length];

const S = (v) => scrubInternal(String(v == null ? '' : v)).trim();
const arr = (v) => (Array.isArray(v) ? v : []);

// Markdown's inline marks, taken out: bold is kept as a flag, the rest as plain text.
function inlineRuns(text) {
  const runs = [];
  String(text || '').split(/(\*\*[^*]+\*\*|__[^_]+__)/).forEach((part) => {
    if (!part) return;
    const bold = /^(\*\*|__).+\1$/.test(part);
    const plain = (bold ? part.slice(2, -2) : part)
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/\[([^\]]+)\]\(([^)]+)\)/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/(^|[\s(])[*_]([^*_\n]+)[*_](?=[\s).,;:!?]|$)/g, '$1$2');
    // Not trimmed: the space either side of a bold run is what keeps it a separate word.
    runs.push({ text: scrubInternal(plain), bold: bold });
  });
  return runs;
}

export function reportPdf(card, run, cards, opts) {
  const o = opts || {};
  const doc = createPdf();
  const W = doc.W, H = doc.H;
  const M = { l: 50, r: 50, t: 56, b: 64 };
  const X0 = M.l, CW = W - M.l - M.r;
  let top = M.t;

  const fits = (h) => top + h <= H - M.b;
  const ensure = (h) => { if (!fits(h)) { doc.addPage(); top = M.t; } };
  const gap = (h) => { top += h; };

  // A wrapped paragraph in one style; each line is placed on the page it fits.
  function para(text, st) {
    const s = Object.assign({ font: 'regular', size: 10, color: C.ink, lead: 1.45, x: X0, width: CW }, st || {});
    const lh = s.size * s.lead;
    wrapText(text, s.font, s.size, s.width).forEach((line) => {
      ensure(lh);
      doc.text(line, s.x, top + s.size, { font: s.font, size: s.size, color: s.color, tracking: s.tracking });
      top += lh;
    });
  }

  // A paragraph with bold runs (the markdown's **...**), wrapped word by word.
  function richPara(text, st) {
    const s = Object.assign({ size: 10, color: C.ink2, lead: 1.5, x: X0, width: CW, indent: 0 }, st || {});
    const lh = s.size * s.lead;
    // A word is one or more pieces: "**Ostley**," is a bold piece and a regular comma with no
    // space between them.
    const words = [];
    let open = false;
    inlineRuns(text).forEach((r) => {
      const font = r.bold ? 'bold' : 'regular';
      r.text.split(/(\s+)/).forEach((tok, k) => {
        if (!tok) return;
        if (/^\s+$/.test(tok)) { open = false; return; }
        if (open && k === 0 && words.length) words[words.length - 1].push({ w: tok, font: font });
        else words.push([{ w: tok, font: font }]);
        open = true;
      });
      if (/\s$/.test(r.text)) open = false;
    });
    if (!words.length) return;
    const space = textWidth(' ', 'regular', s.size);
    const wordWidth = (word) => word.reduce((a, p) => a + textWidth(p.w, p.font, s.size), 0);
    let line = [], width = 0;
    const flush = () => {
      if (!line.length) return;
      ensure(lh);
      let x = s.x + s.indent;
      line.forEach((word) => {
        word.forEach((p) => {
          doc.text(p.w, x, top + s.size, { font: p.font, size: s.size, color: p.font === 'bold' ? C.ink : s.color });
          x += textWidth(p.w, p.font, s.size);
        });
        x += space;
      });
      top += lh;
      line = []; width = 0;
    };
    const room = s.width - s.indent;
    words.forEach((word) => {
      const ww = wordWidth(word);
      if (line.length && width + space + ww > room) flush();
      if (!line.length && ww > room) {
        // One word wider than the line: let wrapText cut it.
        const font = word[0].font;
        wrapText(word.map((p) => p.w).join(''), font, s.size, room).forEach((piece) => { line = [[{ w: piece, font: font }]]; flush(); });
        return;
      }
      line.push(word);
      width += (line.length > 1 ? space : 0) + ww;
    });
    flush();
  }

  // A one-line string cut with an ellipsis to fit.
  function fit(text, font, size, width, tracking) {
    const t = String(text || '');
    if (textWidth(t, font, size, tracking) <= width) return t;
    let k = t.length;
    while (k > 1 && textWidth(t.slice(0, k) + '…', font, size, tracking) > width) k -= 1;
    return t.slice(0, k).trimEnd() + '…';
  }

  // Small capitals over a section, as the page labels its cards.
  function label(text, x, color) {
    const x0 = x === undefined ? X0 : x;
    doc.text(fit(String(text || '').toUpperCase(), 'regular', 7.5, X0 + CW - x0, 0.9), x0, top + 7.5, { size: 7.5, color: color || C.ink3, tracking: 0.9 });
  }

  // One labelled bar: the label left, the figure right, the bar under both.
  function bar(name, figure, pct, color) {
    ensure(24);
    doc.text(fit(name, 'regular', 9, CW - 90), X0, top + 9, { size: 9, color: C.ink });
    const fw = textWidth(figure, 'mono', 9);
    doc.text(figure, X0 + CW - fw, top + 9, { font: 'mono', size: 9, color: color });
    doc.rect(X0, top + 13.5, CW, 5, C.track);
    doc.rect(X0, top + 13.5, Math.max(1.5, (CW * Math.max(0, Math.min(100, pct))) / 100), 5, color);
    top += 25;
  }

  // A table that sizes its columns to what is in them and repeats its header on a new page.
  // A cell is a string or {main, sub, color}.
  function table(headers, rows) {
    const cols = Math.max(headers.length, ...rows.map((r) => r.length), 1);
    const pad = 6, size = 8.5, lh = size * 1.4;
    const main = (c) => (c && typeof c === 'object' ? c.main : c);
    const natural = [];
    for (let i = 0; i < cols; i++) {
      const cells = [String(headers[i] || '').toUpperCase()].concat(rows.map((r) => S(main(r[i]))));
      natural[i] = Math.min(220, Math.max(36, ...cells.map((t) => textWidth(t, 'regular', size) + pad * 2)));
    }
    const sum = natural.reduce((a, b) => a + b, 0);
    const widths = natural.map((w) => (w * CW) / sum);
    const head = () => {
      const hLines = headers.map((h, i) => wrapText(String(h || '').toUpperCase(), 'regular', 7, widths[i] - pad * 2));
      const hh = Math.max(1, ...hLines.map((l) => l.length)) * 9 + pad * 1.4;
      doc.rect(X0, top, CW, hh, C.track);
      let x = X0;
      hLines.forEach((ls, i) => {
        ls.forEach((l, k) => doc.text(l, x + pad, top + pad + 6 + k * 9, { size: 7, color: C.ink3, tracking: 0.6 }));
        x += widths[i];
      });
      top += hh;
    };
    // The header and a few rows together, or the table starts on the next page.
    ensure(80);
    head();
    rows.forEach((r) => {
      const cells = [];
      for (let i = 0; i < cols; i++) {
        const c = r[i];
        const isObj = c && typeof c === 'object';
        cells.push({
          main: wrapText(S(isObj ? c.main : c), 'regular', size, widths[i] - pad * 2),
          sub: isObj && c.sub ? wrapText(S(c.sub), 'regular', 7.5, widths[i] - pad * 2) : [],
          color: (isObj && c.color) || C.ink
        });
      }
      const rh = Math.max(...cells.map((c) => c.main.length * lh + c.sub.length * 7.5 * 1.35)) + pad * 1.2;
      if (!fits(rh)) { doc.addPage(); top = M.t; head(); }
      doc.line(X0, top, X0 + CW, top, C.rule, 0.5);
      let x = X0;
      cells.forEach((c, i) => {
        let y = top + pad * 0.6 + size;
        c.main.forEach((l) => { doc.text(l, x + pad, y, { size: size, color: c.color }); y += lh; });
        c.sub.forEach((l) => { doc.text(l, x + pad, y - 1, { size: 7.5, color: C.ink3 }); y += 7.5 * 1.35; });
        x += widths[i];
      });
      top += rh;
    });
    doc.line(X0, top, X0 + CW, top, C.rule, 0.5);
    top += 4;
  }

  // Markdown prose: headings, lists, paragraphs, tables, code, rules.
  function markdown(text) {
    const lines = String(text || '').split(/\r?\n/);
    let buf = [];
    const flushPara = () => {
      if (!buf.length) return;
      richPara(buf.join(' '), {});
      gap(5);
      buf = [];
    };
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (/^\s*\|/.test(line) && /^\s*\|?[\s:-]*-[\s:|-]*$/.test(lines[i + 1] || '')) {
        flushPara();
        let j = i + 2;
        while (j < lines.length && /^\s*\|/.test(lines[j])) j++;
        const t = parseMarkdownTables(lines.slice(i, j).join('\n'))[0];
        if (t) { table(t.headers, t.rows); gap(6); }
        i = j - 1;
        continue;
      }
      if (/^\s*```/.test(line)) {
        flushPara();
        let j = i + 1;
        while (j < lines.length && !/^\s*```/.test(lines[j])) {
          para(scrubInternal(lines[j], { code: true }) || ' ', { font: 'mono', size: 8, color: C.ink2, lead: 1.4 });
          j++;
        }
        i = j;
        gap(5);
        continue;
      }
      const h = /^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$/.exec(line);
      if (h) {
        flushPara();
        ensure(30);
        gap(4);
        para(inlineRuns(h[2]).map((r) => r.text).join(''), { font: 'bold', size: h[1].length <= 2 ? 12 : 10.5, color: C.ink, lead: 1.35 });
        gap(3);
        continue;
      }
      if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { flushPara(); ensure(10); doc.line(X0, top + 4, X0 + CW, top + 4, C.rule, 0.5); gap(10); continue; }
      const li = /^(\s*)([-*+•]|\d+[.)])\s+(.*)$/.exec(line);
      if (li) {
        flushPara();
        const depth = Math.min(3, Math.floor(li[1].replace(/\t/g, '  ').length / 2));
        const indent = 12 + depth * 12;
        const marker = /\d/.test(li[2]) ? li[2] : '•';
        ensure(14);
        doc.text(marker, X0 + indent - (/\d/.test(marker) ? 12 : 8), top + 9.5, { size: 9.5, color: C.accent });
        richPara(li[3], { size: 9.5, indent: indent });
        gap(2);
        continue;
      }
      if (!line.trim()) { flushPara(); continue; }
      buf.push(line.trim());
    }
    flushPara();
  }

  // ── card bodies ──────────────────────────────────────────────────────────────
  function kpiRow(row) {
    const g = 14, colW = (CW - g * 2) / 3;
    const cells = row.map((c) => {
      const d = c.data || {}, dial = d.dial || {};
      const title = wrapText(S(c.title).toUpperCase(), 'regular', 7.5, colW, 0.9).slice(0, 2);
      const sub = d.sublabel ? wrapText(S(d.sublabel), 'regular', 8.5, colW - 48).slice(0, 4) : [];
      const h = title.length * 10 + 6 + Math.max(40, (dial.denom ? 12 : 0) + sub.length * 11.5);
      return { c: c, d: d, dial: dial, title: title, sub: sub, h: h };
    });
    const rowH = Math.max(...cells.map((x) => x.h));
    ensure(rowH);
    cells.forEach((x, i) => {
      const cx0 = X0 + i * (colW + g);
      x.title.forEach((l, k) => doc.text(l, cx0, top + 7.5 + k * 10, { size: 7.5, color: C.ink3, tracking: 0.9 }));
      const y0 = top + x.title.length * 10 + 6;
      const color = colorOf(x.dial.color);
      const r = 17, cx = cx0 + r + 2, cy = y0 + r + 2;
      doc.arc(cx, cy, r, 1, C.track, 4);
      if (typeof x.dial.pct === 'number') doc.arc(cx, cy, r, x.dial.pct / 100, color, 4);
      else doc.arc(cx, cy, r, 1, color, 4);
      const count = String(x.dial.count === undefined || x.dial.count === null ? (x.d.count == null ? '—' : x.d.count) : x.dial.count);
      const fs = count.length > 4 ? 9 : 12;
      doc.text(count, cx - textWidth(count, 'monoBold', fs) / 2, cy + fs * 0.35, { font: 'monoBold', size: fs, color: color });
      let ty = y0 + 4;
      if (typeof x.dial.pct === 'number' && x.dial.denom) { doc.text('of ' + x.dial.denom, cx0 + 46, ty + 8, { font: 'mono', size: 8, color: C.ink3 }); ty += 12; }
      x.sub.forEach((l) => { doc.text(l, cx0 + 46, ty + 8.5, { size: 8.5, color: C.ink3 }); ty += 11.5; });
    });
    top += rowH;
  }

  function body(c) {
    const d = c.data || {};
    if (c.kind === 'duration') {
      const w = d.worst;
      if (w) {
        ensure(30);
        const big = String(w.yearsLabel || '');
        doc.text(big, X0, top + 20, { font: 'mono', size: 22, color: colorOf(w.color) });
        doc.text(fit('worst — ' + S(w.label), 'regular', 9.5, CW - textWidth(big, 'mono', 22) - 12), X0 + textWidth(big, 'mono', 22) + 10, top + 19, { size: 9.5, color: C.ink2 });
        top += 32;
      }
      arr(d.rows).forEach((r) => bar(S(r.label), String(r.yearsLabel || ''), r.pct, colorOf(r.color)));
      return;
    }
    if (c.kind === 'status') {
      const segs = arr(d.segments);
      ensure(34);
      let x = X0;
      segs.forEach((sg) => { const w = (CW * sg.pct) / 100; doc.rect(x, top, w, 8, colorOf(sg.color)); x += w; });
      top += 16;
      let lx = X0;
      segs.forEach((sg) => {
        const t = sg.n + ' ' + S(sg.status);
        const w = textWidth(t, 'regular', 9) + 26;
        if (lx + w > X0 + CW) { lx = X0; top += 14; ensure(14); }
        doc.rect(lx, top + 2, 7, 7, colorOf(sg.color));
        doc.text(t, lx + 11, top + 9, { size: 9, color: C.ink });
        lx += w;
      });
      if (d.total) {
        const t = 'of ' + d.total;
        doc.text(t, X0 + CW - textWidth(t, 'regular', 9), top + 9, { size: 9, color: C.ink3 });
      }
      top += 16;
      return;
    }
    if (c.kind === 'holders') {
      arr(d.rows).forEach((r) => bar(S(r.company), String(r.n), r.pct, r.concentrated && !r.unattributed ? C.accent : r.unattributed ? C.faint : C.quiet));
      return;
    }
    if (c.kind === 'figures') {
      arr(d.figures).forEach((f) => bar(S(f.label), String(f.value) + (f.unit ? ' ' + S(f.unit) : ''), f.pct, C.accent));
      return;
    }
    if (c.kind === 'distribution') {
      const rows = arr(d.rows);
      rows.forEach((r, i) => bar(S(r.value), String(r.n) + (d.total ? ' of ' + d.total : ''), r.pct, seriesColor(r.value, i)));
      return;
    }
    if (c.kind === 'table') { table(arr(d.headers), arr(d.rows)); return; }
    if (c.kind === 'narrative') { richPara(d.text, { size: 10, color: C.ink2, lead: 1.55 }); return; }
    if (c.kind === 'action') {
      const pip = [d.severity, d.scope].filter(Boolean).map(S).join(' · ');
      if (pip) {
        ensure(14);
        doc.arc(X0 + 3, top + 5, 2, 1, sevColor(d.severity), 4);
        doc.text(fit(pip.toUpperCase(), 'regular', 7.5, CW - 10, 0.8), X0 + 10, top + 7.5, { size: 7.5, color: sevColor(d.severity), tracking: 0.8 });
        top += 13;
      }
      para(S(d.title), { size: 10.5, color: C.ink, lead: 1.45 });
      if (arr(d.tags).length) para(arr(d.tags).map(S).join('  ·  '), { size: 8, color: C.ink3 });
      return;
    }
    if (c.kind === 'group') {
      if (d.headline) para(S(d.headline), { size: 10, color: sevColor(d.severity), lead: 1.45 });
      arr(d.points).forEach((p) => {
        ensure(13);
        doc.text('•', X0 + 2, top + 9.5, { size: 9.5, color: sevColor(d.severity) });
        richPara(p, { size: 9.5, indent: 12 });
      });
      return;
    }
    if (c.kind === 'insight') { richPara(d.text || d, { size: 9.5 }); return; }
    if (c.kind === 'certificates') {
      table(['Certificate', 'Held by', 'Scope', 'Status'], arr(d.rows).map((r) => [
        { main: r.name, sub: r.reason }, r.company, r.scope, { main: r.status, color: sevColor(r.severity) }
      ]));
      return;
    }
    if (c.kind === 'pending') { richPara(d.what_is_pending || '', { size: 9.5 }); return; }
    markdown(d.text || '');
  }

  // ── the header ──────────────────────────────────────────────────────────────
  const name = S(card && card.name) || 'Untitled report';
  label('Custom report · dynamic', X0, C.accent);
  top += 16;
  para(name, { font: 'bold', size: 22, lead: 1.2 });
  gap(4);
  const asks = 'Asks “' + S(card && card.prompt) + '”' + (card && card.source_page ? ', pinned from ' + S(card.source_page) : '') + '.';
  para(asks, { size: 10.5, color: C.ink2 });
  gap(6);
  const refreshed = run && run.ran_at ? 'Refreshed ' + fmtDateTime(run.ran_at)
    + (typeof run.duration_ms === 'number' ? ' · ' + Math.round(run.duration_ms / 1000) + ' s' : '') : 'Not refreshed yet';
  para([refreshed, S(card && card.refresh_label)].filter(Boolean).join(' · ') + ' · refreshed by the server', { size: 8.5, color: C.ink3 });
  const by = [o.exportedBy ? 'by ' + S(o.exportedBy) : '', o.company ? S(o.company) : ''].filter(Boolean).join(' · ');
  para('Exported ' + fmtDateTime((o.exportedAt || new Date()).toISOString()) + (by ? ' ' + by : ''), { size: 8.5, color: C.ink3 });
  gap(10);
  doc.rect(X0, top, 36, 2.5, C.accent);
  doc.line(X0 + 40, top + 1.25, X0 + CW, top + 1.25, C.rule, 0.5);
  gap(22);

  // ── the answer ──────────────────────────────────────────────────────────────
  if (!run) {
    para('This report has not run yet. The server asks its question at the next due time; Run now builds it immediately.', { size: 10.5, color: C.ink2 });
  } else if (run.error || run.ok === false) {
    const msg = S(run.error);
    const lines = wrapText(msg, 'regular', 9.5, CW - 28).slice(0, 30);
    const h = 30 + lines.length * 14;
    ensure(h);
    doc.rect(X0, top, CW, h, C.tint);
    doc.rect(X0, top, 3, h, C.accent);
    doc.text('This refresh did not complete.', X0 + 14, top + 18, { font: 'bold', size: 10.5, color: C.ink });
    lines.forEach((l, i) => doc.text(l, X0 + 14, top + 34 + i * 14, { size: 9.5, color: C.ink2 }));
    top += h;
  } else if (!arr(cards).length) {
    para('This refresh came back with no answer.', { size: 10.5, color: C.ink2 });
  } else {
    const list = arr(cards);
    for (let i = 0; i < list.length; i++) {
      const c = list[i];
      if (i) { ensure(48); doc.line(X0, top, X0 + CW, top, C.rule, 0.5); gap(14); }
      if (c.kind === 'kpi') {
        const row = [c];
        while (row.length < 3 && list[i + 1] && list[i + 1].kind === 'kpi') { row.push(list[i + 1]); i += 1; }
        kpiRow(row);
        gap(16);
        continue;
      }
      // The label travels with the start of what it labels: a table needs its header and a
      // few rows, a chart its first bars.
      ensure(c.kind === 'table' || c.kind === 'certificates' ? 128 : c.kind === 'duration' ? 96 : 48);
      const accent = c.kind === 'duration' ? colorOf(c.data && c.data.worst && c.data.worst.color)
        : (c.kind === 'action' || c.kind === 'group' || c.kind === 'insight') && /risk|critical|warning|anomaly/i.test(String((c.data && (c.data.severity || c.data.type)) || ''))
          ? C.accent : null;
      if (accent) doc.rect(X0 - 10, top, 2.5, 10, accent);
      // An action's title is its body, so its label says what it is instead of repeating it.
      const head = c.kind === 'action' ? 'Priority action' : S(c.title);
      if (head) { label(head); top += 17; }
      body(c);
      if (c.data && c.data.note) { gap(4); richPara(c.data.note, { size: 8.5, color: C.ink3 }); }
      gap(16);
    }
  }

  // ── the footer, once the page count is known ───────────────────────────────
  const total = doc.pageCount;
  for (let p = 1; p <= total; p++) {
    doc.onPage(p, () => {
      const fy = H - 38;
      doc.line(X0, fy - 10, X0 + CW, fy - 10, C.rule, 0.5);
      const right = 'Page ' + p + ' of ' + total;
      doc.text(right, X0 + CW - textWidth(right, 'regular', 7.5), fy, { size: 7.5, color: C.ink3 });
      doc.text(fit('Hoistra · ' + name, 'regular', 7.5, CW - 80), X0, fy, { size: 7.5, color: C.ink3 });
    });
  }
  return doc.save({ title: name, author: o.exportedBy ? S(o.exportedBy) : '', date: o.exportedAt });
}

// The file name a report is saved under.
export function reportPdfName(card) {
  return (String((card && card.name) || 'report').replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() || 'report') + '.pdf';
}
