// A report's Export is a PDF (Hussain, 8 Oct 2026: "it should be downloaded as an pdf not md
// file"). These read the file back the way a PDF reader would - the cross-reference table must
// point at real objects - and read its text out of the page streams.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { reportPdf, reportPdfName } = await import('../src/logic/reportPdf.js');
const { winAnsi, wrapText, textWidth } = await import('../src/logic/pdfDoc.js');
const { answerCards } = await import('../src/logic/reportCards.js');

const latin1 = (bytes) => Array.from(bytes, (b) => String.fromCharCode(b)).join('');
const cp1252 = new TextDecoder('windows-1252');

// Every string the pages draw, in order, joined by spaces.
function textOf(bytes) {
  const src = latin1(bytes);
  const out = [];
  const re = /\(((?:\\.|[^\\)])*)\) Tj/g;
  let m;
  while ((m = re.exec(src))) {
    const codes = [];
    for (let i = 0; i < m[1].length; i++) {
      const ch = m[1][i];
      if (ch !== '\\') { codes.push(ch.charCodeAt(0)); continue; }
      const oct = /^[0-7]{3}/.exec(m[1].slice(i + 1));
      if (oct) { codes.push(parseInt(oct[0], 8)); i += 3; } else { codes.push(m[1].charCodeAt(i + 1)); i += 1; }
    }
    out.push(cp1252.decode(new Uint8Array(codes)));
  }
  return out.join(' ');
}

function assertValidPdf(bytes) {
  const src = latin1(bytes);
  assert.ok(src.startsWith('%PDF-1.4\n'), 'a PDF header');
  assert.ok(src.endsWith('%%EOF\n'), 'an end-of-file marker');
  const xref = Number(/startxref\n(\d+)\n%%EOF\n$/.exec(src)[1]);
  assert.equal(src.slice(xref, xref + 4), 'xref', 'startxref points at the table');
  const size = Number(/xref\n0 (\d+)\n/.exec(src.slice(xref))[1]);
  const entries = src.slice(xref).split('\n').slice(3, 3 + size - 1);
  entries.forEach((e, i) => {
    assert.match(e + '\n', /^\d{10} 00000 n \n$/, 'a 20-byte entry');
    const off = Number(e.slice(0, 10));
    assert.equal(src.slice(off, off + String(i + 1).length + 6), (i + 1) + ' 0 obj', 'entry ' + (i + 1) + ' points at its object');
  });
  // Each stream's /Length is its real length.
  const streams = [...src.matchAll(/<< \/Length (\d+) >>\nstream\n/g)];
  assert.ok(streams.length >= 1);
  streams.forEach((s) => {
    const start = s.index + s[0].length;
    assert.equal(src.slice(start + Number(s[1]), start + Number(s[1]) + 10), '\nendstream');
  });
  assert.ok(Array.from(bytes).slice(20).every((b) => b < 128), 'past the binary marker the file is 7-bit');
}

const pages = (bytes) => Number(/\/Type \/Pages \/Kids \[[^\]]*\] \/Count (\d+)/.exec(latin1(bytes))[1]);
const CARD = { id: 'c1', name: 'Which vendors are blocked right now?', prompt: 'Which vendors are blocked right now?', refresh_label: 'every 1 hour', source_page: 'Home' };
const AT = new Date('2026-10-08T18:40:00Z');

test('a plain answer: the title, the question, the refresh and the answer, in a valid file', () => {
  const run = { ran_at: '2026-10-08T17:22:00Z', duration_ms: 45000, ok: true,
    answer: '## Findings\n\nTwo buildings carry **lapsed** certificates.\n\n- Harbour Point: 3\n- Ashgrove: 1',
    tool_calls: [{ tool: 'compliance_scan' }, { tool: 'get_sites' }] };
  const bytes = reportPdf(CARD, run, answerCards(null, run.answer), { exportedAt: AT, exportedBy: 'Husain Kalabhai', company: 'Plenum Technologies' });
  assertValidPdf(bytes);
  const t = textOf(bytes);
  assert.match(t, /Which vendors are blocked right now\?/);
  assert.match(t, /Asks “Which vendors are blocked right now\?”, pinned from Home\./);
  assert.match(t, /every 1 hour/);
  assert.match(t, /45 s/);
  assert.match(t, /Husain Kalabhai · Plenum Technologies/);
  assert.match(t, /Two buildings carry lapsed certificates\./);
  assert.match(t, /Harbour Point/);
  assert.doesNotMatch(t, /compliance_scan|get_sites/, 'the run\'s tools are internal and not printed');
  assert.match(t, /Page 1 of 1/);
});

test('a structured compliance answer: narrative, figures, actions, groups, insights and the certificate table', () => {
  const rich = {
    narrative: 'Six building certificates have lapsed.',
    kpis: [{ label: 'Lapsed', count: 6, unit: 'certificates' }, { label: 'Drafts', count: 5 }],
    insights: [{ type: 'risk', text: 'The FRA at Building 5 expired in 2006.' }],
    actions: [{ title: 'Book the FRA renewal', severity: 'critical', scope: 'building' }],
    groups: [{ owner: 'Building 5', scope: 'building', severity: 'critical', headline: 'One lapsed statutory certificate', points: ['FRA lapsed 2006-10-01'] }],
    certificates: [{ name: 'Fire Risk Assessment', company: 'Building 5', scope: 'building', status: 'Lapsed', reason: 'expired 2006-10-01', severity: 'critical' }],
    overdue: { buildings: [{ label: 'Building — Ostley Power Services', days: 400, severity: 'critical' }, { label: 'ProudCastle Fire', days: 60, severity: 'critical' }] }
  };
  const run = { ran_at: '2026-10-08T17:22:00Z', duration_ms: 1000, ok: true, answer: '', rich };
  const bytes = reportPdf(CARD, run, answerCards(rich, ''), { exportedAt: AT });
  assertValidPdf(bytes);
  const t = textOf(bytes);
  for (const want of [/HOW LONG EACH DUTY HAS BEEN UNEVIDENCED/, /1 yr/, /worst — Ostley Power Services/, /ProudCastle Fire/,
    /LAPSED/, /\b6\b/, /DRAFTS/, /Six building certificates have lapsed\./, /Book the FRA renewal/, /CRITICAL · BUILDING/,
    /One lapsed statutory certificate/, /FRA lapsed 2006-10-01/, /The FRA at Building 5 expired in 2006\./,
    /CERTIFICATE/, /HELD BY/, /Fire Risk Assessment/, /expired 2006-10-01/]) assert.match(t, want);
});

test('a failed run exports as a failure with the internals scrubbed, and an unrun card says so', () => {
  const failed = reportPdf(CARD, { ran_at: '2026-10-08T17:22:00Z', ok: false, error: 'svc-deepagents is not reachable' }, [], { exportedAt: AT });
  assertValidPdf(failed);
  const t = textOf(failed);
  assert.match(t, /This refresh did not complete\./);
  assert.match(t, /the platform is not reachable/);
  assert.doesNotMatch(t, /svc-/, 'no internal service name in the download');
  assert.match(textOf(reportPdf(CARD, null, [], { exportedAt: AT })), /has not run yet/);
});

test('a long table runs onto more pages, repeating its header, and every page is numbered', () => {
  const rows = Array.from({ length: 120 }, (_, i) => '| WO-' + (1000 + i) + ' | Chiller ' + (i % 7) + ' | ' + (i % 3 ? 'Open' : 'Closed') + ' |').join('\n');
  const answer = '## Work orders\n\n| Code | Asset | Status |\n| --- | --- | --- |\n' + rows;
  const bytes = reportPdf(CARD, { ran_at: '2026-10-08T17:22:00Z', ok: true, answer }, answerCards(null, answer), { exportedAt: AT });
  assertValidPdf(bytes);
  const n = pages(bytes);
  assert.ok(n >= 3, n + ' pages');
  const t = textOf(bytes);
  assert.match(t, /WO-1000/);
  assert.match(t, /WO-1119/, 'the last row is there');
  assert.ok((t.match(/\bCODE\b/g) || []).length >= n, 'the header repeats on each page');
  assert.match(t, new RegExp('Page ' + n + ' of ' + n));
});

test('text the standard fonts cannot draw is mapped, spelled or marked - never a wrong glyph', () => {
  const s = (str) => cp1252.decode(new Uint8Array(winAnsi(str)));
  assert.equal(s('“Quoted” — £1,200 · 241 kWh/m²'), '“Quoted” — £1,200 · 241 kWh/m²');
  assert.equal(s('A → B, ≤ 3'), 'A -> B, <= 3');
  assert.equal(s('Done ✅🔥'), 'Done ');
  assert.equal(s('東京'), '??');
  assert.equal(s('tab\there'), 'tab here');
});

test('wrapped lines fit their column, and a word wider than the column is cut', () => {
  const lines = wrapText('The Fire Risk Assessment at Harbour Point expired in 2006 and has not been renewed since.', 'regular', 10, 120);
  assert.ok(lines.length > 2);
  lines.forEach((l) => assert.ok(textWidth(l, 'regular', 10) <= 120, l));
  const long = wrapText('x'.repeat(200), 'regular', 10, 100);
  assert.ok(long.length > 1);
  long.forEach((l) => assert.ok(textWidth(l, 'regular', 10) <= 100));
});

test('the file is named after the report, as a .pdf', () => {
  assert.equal(reportPdfName({ name: 'Which vendors are blocked right now?' }), 'which-vendors-are-blocked-right-now.pdf');
  assert.equal(reportPdfName({ name: '???' }), 'report.pdf');
  assert.equal(reportPdfName(null), 'report.pdf');
});
