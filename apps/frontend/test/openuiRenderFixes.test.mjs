// openuiRenderFixes — the renderer defects found in the 27 Oct 2026 review of the orchestrator's
// OpenUI answers (src/openui), each pinned by what a reader would get.
//
// Built and rendered the way test/openuiRender.test.mjs does: the library bundled with esbuild,
// drawn through OpenUI's own <Renderer> with react-dom/server.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync, readFileSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-render-fixes.bundle.mjs');
const res = await build({
  stdin: {
    contents: [
      "export { library } from '../src/openui/library.jsx';",
      "export { fmt, cell, toNumber, onOneScale, toneOf } from '../src/openui/shared.jsx';",
      "export { labelIndexes } from '../src/openui/components/Charts.jsx';",
      "export { routeAction, canOpen } from '../src/openui/actions.js';",
      "export { default as OpenUIAnswer } from '../src/openui/OpenUIAnswer.jsx';"
    ].join('\n'),
    resolveDir: here, loader: 'js'
  },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*', '@openuidev/react-lang', '@openuidev/lang-core', 'zod'],
  loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(outFile, res.outputFiles[0].text);
after(() => rmSync(outFile, { force: true }));

const M = await import(pathToFileURL(outFile).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { Renderer } = await import('@openuidev/react-lang');

const render = (program, streaming = false) => renderToStaticMarkup(
  React.createElement(Renderer, { library: M.library, response: program, isStreaming: streaming, publishObservability: false })
);
const count = (html, re) => (html.match(re) || []).length;
const prog = (...lines) => lines.join('\n');
const css = readFileSync(new URL('../src/openui/openui.css', import.meta.url), 'utf8');
const rule = (sel) => {
  const at = css.indexOf(sel + ' {');
  assert.ok(at > -1, 'no rule for ' + sel);
  return css.slice(at, css.indexOf('}', at) + 1);
};
// Bar fills in order: [classes, left %, width %].
const fills = (html) => [...html.matchAll(/<span class="(oui-bar-fill[^"]*)" style="([^"]*)"/g)].map(([, cls, style]) => {
  const left = /left:\s*([\d.]+)%/.exec(style);
  const width = /width:\s*([\d.]+)%/.exec(style);
  return [cls, left ? Number(left[1]) : 0, width ? Number(width[1]) : 0];
});
const near = (a, b, msg) => assert.ok(Math.abs(a - b) < 0.2, msg + ': ' + a + ' is not ' + b);
const tableOf = (html) => {
  const m = /<table[\s\S]*?<\/table>/.exec(html);
  return m ? m[0] : '';
};

// ── figures shown as written (#28, #39) ─────────────────────────────────────
test('fmt shows a number as its plain digits: never rounded, never grouped', () => {
  assert.equal(M.fmt(2004), '2004');
  assert.equal(M.fmt(118432), '118432');
  assert.equal(M.fmt(12400), '12400');
  assert.equal(M.fmt(0.004), '0.004');
  assert.equal(M.fmt(3.14159), '3.14159');
  assert.equal(M.fmt(21.875), '21.875');
  // float noise and negative zero are not figures anyone wrote
  assert.equal(M.fmt(0.1 + 0.2), '0.3');
  assert.equal(M.fmt(-0), '0');
  assert.equal(M.fmt(-12.5), '-12.5');
  // a string is shown exactly as written
  assert.equal(M.fmt('£84,000'), '£84,000');
  assert.equal(M.fmt('23.44 / 25'), '23.44 / 25');
  assert.equal(M.cell(1998), '1998');
});

test('years, record numbers and small decimals reach the reader as written', () => {
  const html = render(prog('root = Answer([t, d, k])',
    't = Table(["Asset", "Install year", "Work order", "Leak rate"], [["Chiller 1", 2004, 118432, 0.004]])',
    'd = Details("Chiller 1", [f1, f2])', 'f1 = Field("Year built", 1998)', 'f2 = Field("Asset number", 40021)',
    'k = Metrics([m1, m2])', 'm1 = Metric("Leak rate", 0.004, null, "risk")', 'm2 = Metric("Spend", 12400)'));
  for (const v of ['2004', '118432', '0.004', '1998', '40021', '12400']) assert.match(html, new RegExp('>' + v.replace('.', '\\.') + '<'), v);
  assert.doesNotMatch(html, /2,004|118,432|1,998|40,021/);
});

// ── chart values: signs, scales, ratios (#18, #27, #37, #26) ────────────────
test('toNumber reads the sign, the scale and the currency of a figure written as text', () => {
  const cases = {
    '-£1,200': -1200, '−950': -950, '−£950': -950, '£-300': -300, '£−5': -5, '- 5': -5, '–3': -3,
    '(1,200)': -1200, '(£1,200)': -1200, '−4%': -4, '−2°C': -2, '+5%': 5,
    '£1.2m': 1200000, '£800k': 800000, '£1.05bn': 1050000000, '£950K': 950000,
    '£1,200': 1200, '4.1 kWh': 4.1, '241 kWh/m²': 241, '12m²': 12, '5 min': 5, '12 (3 overdue)': 12,
    '23.44 / 25': 23.44
  };
  for (const [s, n] of Object.entries(cases)) assert.equal(M.toNumber(s), n, s);
  assert.equal(M.toNumber(800), 800);
  assert.equal(M.toNumber('n/a'), null);
  assert.equal(M.toNumber(null), null);
});

test('onOneScale puts figures of one measure on one scale, and refuses to mix measures', () => {
  assert.deepEqual(M.onOneScale(['£1.2m', '£800k']), [1200000, 800000]);
  // the same denominator: drawn by the numerator; different denominators: by the ratio
  assert.deepEqual(M.onOneScale(['23.44/25', '21.88/25']), [23.44, 21.88]);
  assert.deepEqual(M.onOneScale(['9/10', '10/40', '3/4']), [0.9, 0.25, 0.75]);
  // a ratio beside a plain score, a percentage beside money: not one measure
  assert.equal(M.onOneScale(['23.44/25', '21.88/25', '86.12']), null);
  assert.equal(M.onOneScale(['−12%', '-£400', '(3)', '+5%']), null);
  // a number the model wrote beside a figure written as text is the same measure
  assert.deepEqual(M.onOneScale(['£1,200', 800, null]), [1200, 800, null]);
});

test('a BarChart draws negatives from a zero line, every value as written, and accents nothing', () => {
  const html = render(prog('root = Answer([c])', 'c = BarChart("Change", ["A", "B", "C"], [-10.3, 5, -8.9], "%")'));
  const f = fills(html);
  assert.equal(f.length, 3, 'every value has a bar');
  assert.equal(f.filter(([c]) => /is-neg/.test(c)).length, 2);
  assert.equal(count(html, /is-top/g), 0);
  // the zero line sits where -10.3 ends: 10.3 / 15.3 of the track
  near(f[0][1] + f[0][2], 100 * 10.3 / 15.3, 'A ends at zero');
  near(f[1][1], 100 * 10.3 / 15.3, 'B starts at zero');
  assert.match(html, /class="oui-bar-zero"/);
  assert.match(html, />-10\.3%</);
  assert.match(html, />5%</);
  const all = render(prog('root = Answer([c])', 'c = BarChart("Temperature", ["A", "B", "C"], [-3, -1, -2], "°C")'));
  assert.equal(fills(all).filter(([c]) => /is-neg/.test(c)).length, 3, 'all negative still draws three bars');
  const text = render(prog('root = Answer([c])', 'c = BarChart("Net cost", ["A", "B", "C"], ["-£1,200", "£300", "−£950"])'));
  const t = fills(text);
  assert.deepEqual(t.map(([c]) => /is-neg/.test(c)), [true, false, true]);
  near(t[0][2], 100 * 1200 / 1500, 'A is the biggest credit, drawn left of zero');
  assert.match(text, />-£1,200</);
  assert.match(text, />−£950</);
});

test('a BarChart scales "£1.2m" beside "£800k" by what they are, not by their digits', () => {
  const f = fills(render(prog('root = Answer([c])', 'c = BarChart("Contract value", ["Apex", "Brightline"], ["£1.2m", "£800k"])')));
  near(f[0][2], 100, 'Apex');
  near(f[1][2], 100 * 800 / 1200, 'Brightline');
});

test('a BarChart of ratios draws each one by what it is out of; mixed measures draw no bars', () => {
  const f = fills(render(prog('root = Answer([c])', 'c = BarChart("Jobs completed", ["Apex", "Brite", "Coyle"], ["9/10", "10/40", "3/4"])')));
  near(f[0][2], 100, 'Apex 9/10');
  near(f[1][2], 100 * 0.25 / 0.9, 'Brite 10/40 is the shortest');
  near(f[2][2], 100 * 0.75 / 0.9, 'Coyle 3/4');
  const mixed = render(prog('root = Answer([c])', 'c = BarChart("Scores", ["SLA response", "SLA completion", "Overall score"], ["23.44/25", "21.88/25", "86.12"])'));
  assert.equal(count(mixed, /oui-bar-fill/g), 0);
  for (const v of ['23.44/25', '21.88/25', '86.12']) assert.ok(mixed.includes('>' + v + '<'), v);
  const same = render(prog('root = Answer([c])', 'c = BarChart("SLA", ["A", "B"], ["23.44/25", "21.88/25"])'));
  assert.equal(count(same, /oui-bar-fill/g), 2);
});

test('the bar the answer is about is not outshone: no bar takes the accent (#53)', () => {
  const html = render(prog('root = Answer([h, c])',
    'h = Headline("**Apex Mechanical is slipping most on SLA** in September 2026.")',
    'c = BarChart("September 2026 SLA completion", ["Apex Mechanical", "Apex Lifts", "Corvane Mechanical"], [21.88, 23.21, 20.92], "/25")'));
  assert.equal(count(html, /is-top/g), 0);
  assert.doesNotMatch(css, /\.is-top/);
});

// ── LineChart: every value without hover; axis labels that do not collide (#7, #42) ──
test('a LineChart carries every value in a table, as written', () => {
  const html = render(prog('root = Answer([c])', 'c = LineChart("Spend", ["Jul", "Aug", "Sep"], [a, b])',
    'a = Series("A", [12400, 13100, 11900])', 'b = Series("B", [8200, 8100, 9400])'));
  const table = tableOf(html);
  assert.ok(table, 'a figures table is drawn');
  for (const v of ['12400', '13100', '11900', '8200', '8100', '9400']) assert.ok(table.includes('>' + v + '<'), v);
  for (const p of ['Jul', 'Aug', 'Sep']) assert.ok(table.includes('>' + p + '<'), p);
  const written = render(prog('root = Answer([c])', 'c = LineChart("Spend", ["Jul", "Aug"], [a])', 'a = Series("Harbour Point", ["£12,400", "£13,100"])'));
  assert.ok(tableOf(written).includes('>£12,400<'), 'a figure written as text is shown as written');
});

test('a LineChart with few points prints each value on the chart', () => {
  const html = render(prog('root = Answer([c])', 'c = LineChart("Spend", ["Jul", "Aug", "Sep"], [a])', 'a = Series("A", [12400, 13100, 11900])'));
  const svg = /<svg[\s\S]*<\/svg>/.exec(html)[0];
  for (const v of ['12400', '13100', '11900']) assert.ok(svg.includes('>' + v + '<'), v);
  // with the values on the chart the table is for screen readers only
  assert.match(html, /<table class="oui-sr-only"/);
});

test('a LineChart with many points keeps its figures in a table under it, not hover-only', () => {
  const labels = Array.from({ length: 24 }, (_, i) => '"M' + (i + 1) + '"').join(', ');
  const vals = Array.from({ length: 24 }, (_, i) => 100 + i).join(', ');
  const html = render(prog('root = Answer([c])', 'c = LineChart("Monthly", [' + labels + '], [a])', 'a = Series("A", [' + vals + '])'));
  assert.match(html, /<details class="oui-figures" open="">/);
  assert.match(html, /<summary[^>]*>[\s\S]*Figures/);
  for (const v of ['100', '111', '123']) assert.ok(tableOf(html).includes('>' + v + '<'), v);
});

test('a LineChart draws a negative figure written as text below zero and prints it as written', () => {
  const html = render(prog('root = Answer([c])', 'c = LineChart("Change", ["Q1", "Q2", "Q3"], [s], "%")', 's = Series("Cost change", ["2%", "−1.5%", "−4%"])'));
  assert.ok(html.includes('>−4%<'));
  assert.doesNotMatch(html, />4\.0%<|>4%</);
});

const extent = (i, n, span, w) => (i === 0 ? [0, w[i]] : i === n - 1 ? [span - w[i], span] : [(i * span) / (n - 1) - w[i] / 2, (i * span) / (n - 1) + w[i] / 2]);
const clearOf = (idx, n, span, w) => idx.every((i, k) => k === 0 || extent(idx[k - 1], n, span, w)[1] < extent(i, n, span, w)[0]);

test('LineChart axis labels never overlap, in the page or the dock', () => {
  const months = ['September 2025', 'October 2025', 'November 2025', 'December 2025', 'January 2026', 'February 2026'];
  const weeks = ['Week commencing 6 Oct', 'Week commencing 13 Oct', 'Week commencing 20 Oct', 'Week commencing 27 Oct'];
  for (const labels of [months, weeks]) {
    const w = labels.map((l) => l.length * 6.2);
    for (const span of [479, 246, 150, 120]) {
      const idx = M.labelIndexes(labels.length, span, w);
      assert.ok(idx.length >= 1, 'at least one label');
      assert.ok(clearOf(idx, labels.length, span, w), labels[0] + ' @' + span + ': ' + idx.join(','));
      assert.equal(idx[idx.length - 1], labels.length - 1, 'the latest period keeps its label');
    }
  }
  // short labels with room all show
  assert.deepEqual(M.labelIndexes(4, 400, [12, 12, 12, 12]), [0, 1, 2, 3]);
});

// ── closed sections (#17, #29) ──────────────────────────────────────────────
test('a chart drawn from the closed table itself does not keep that table closed', () => {
  const html = render(prog('root = Answer([c, s])',
    'c = BarChart("Sep 2026 SLA completion", ["Apex Mechanical", "Apex Lifts", "Corvane Mechanical"], [21.88, 23.21, 20.92], "/25")',
    's = Section("September 2026 vendor SLA scorecard", [t], "Confirmed, contract-sourced scorecards", true)',
    't = Table(["Vendor", "SLA response", "Change"], [["Apex Mechanical", "23.44/25", "-0.06"], ["Apex Lifts", "24.11/25", "+0.45"], ["Corvane Mechanical", "22.45/25", "No prior trend"]])'));
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /24\.11\/25/);
  assert.match(html, /No prior trend/);
});

test('a closed list of certificates stays closed when the answer shows its figures elsewhere', () => {
  const html = render(prog('root = Answer([c, s])',
    'c = BarChart("Days overdue — vendors", ["Pennard Fire", "Apex Lifts"], [12, 3], "days")',
    's = Section("Certificates in scope · 2", [t], "1 expiring, 1 lapsed", true)',
    't = Table(["Certificate", "Held by", "Scope", "Status"], [["Fire alarm service", "Pennard Fire", "Building", "lapsed"], ["LOLER", "Apex Lifts", "Building", "expiring"]])'));
  assert.match(html, /aria-expanded="false"/);
  assert.doesNotMatch(html, /Fire alarm service/);
});

test('a closed section holding a caveat starts open', () => {
  const html = render(prog('root = Answer([c, s])', 'c = BarChart("Jobs", ["HVAC", "Electrical"], [3, 2])',
    's = Section("Caveats", [w], "1 note", true)', 'w = Callout("warn", "Two buildings have no meter readings for August.")'));
  assert.match(html, /aria-expanded="true"/);
  assert.match(html, /no meter readings/);
});

// ── a repeat is only dropped when nothing is lost (#41) ─────────────────────
test('a table with a caption or a unit in its heading is never hidden as a repeat', () => {
  const caption = render(prog('root = Answer([c, t])', 'c = BarChart("Spend by trade", ["HVAC", "Electrical"], [12.4, 8.1])',
    't = Table(["Trade", "Spend"], [["HVAC", 12.4], ["Electrical", 8.1]], "Excludes the £40k chiller replacement")'));
  assert.match(caption, /Excludes the £40k chiller replacement/);
  const unit = render(prog('root = Answer([c, t])', 'c = BarChart("Spend by trade", ["HVAC", "Electrical"], [12.4, 8.1])',
    't = Table(["Trade", "Spend (£k, ex VAT)"], [["HVAC", 12.4], ["Electrical", 8.1]])'));
  assert.match(unit, /£k, ex VAT/);
  const sign = render(prog('root = Answer([c, t])', 'c = BarChart("Net", ["A", "B"], ["-£1,200", "£300"])',
    't = Table(["Item", "Value"], [["A", "£1,200"], ["B", "£300"]])'));
  assert.equal(count(sign, /class="oui-table"/g), 1, 'a sign the chart and the table disagree on is not a repeat');
});

// ── tones (#36) ─────────────────────────────────────────────────────────────
test('a zero turns a bad tone good only when it is a plain count of something bad', () => {
  assert.equal(M.toneOf(0, 'risk', 'Overdue work orders'), 'ok');
  assert.equal(M.toneOf('0', 'warn', 'Certificates expiring this month'), 'ok');
  assert.equal(M.toneOf('0%', 'risk', 'SLA met this month'), 'risk');
  assert.equal(M.toneOf(0, 'risk', 'Days until gas certificate expires'), 'risk');
  assert.equal(M.toneOf('£0', 'warn', 'Budget remaining'), 'warn');
  assert.equal(M.toneOf(0, 'warn', 'Days left'), 'warn');
  assert.equal(M.toneOf(0, 'risk', 'Compliance score'), 'risk');
  assert.equal(M.toneOf(0, 'risk', 'Response rate'), 'risk');
  assert.equal(M.toneOf(7, 'risk', 'Overdue'), 'risk');
  const html = render(prog('root = Answer([k])', 'k = Metrics([a, b])',
    'a = Metric("Days until gas certificate expires", 0, null, "risk")', 'b = Metric("Overdue work orders", 0, null, "risk")'));
  assert.match(html, /oui-metric oui-tone-risk/);
  assert.match(html, /oui-metric oui-tone-ok/);
});

// ── tables (#60) ────────────────────────────────────────────────────────────
test('a table keeps cells beyond its columns, and a table with no headings still shows its rows', () => {
  const ragged = render(prog('root = Answer([t])', 't = Table(["Vendor", "Score"], [["Apex", 21.88, "Below threshold"], ["Lifts"]])'));
  assert.match(ragged, /Below threshold/);
  assert.equal(count(ragged, /<th[ >]/g), 3);
  const bare = render(prog('root = Answer([t])', 't = Table([], [["Apex", 21.88], ["Lifts", 3]])'));
  assert.match(bare, />Apex</);
  assert.match(bare, />21\.88</);
  assert.doesNotMatch(bare, /<thead>/);
});

// ── email drafts (#43, #55) ─────────────────────────────────────────────────
const hrefOf = (html) => {
  const m = /href="(mailto:[^"]*)"/.exec(html);
  return m ? m[1].replace(/&amp;/g, '&') : null;
};

test('an EmailDraft cannot be copied or opened while it is still being written', () => {
  const html = render(prog('root = Answer([e])', 'e = EmailDraft("Gas safety", "ops@example.com", "Hello, please book the insp")'), true);
  assert.match(html, /<button[^>]*disabled=""[^>]*>Copy</);
  assert.equal(hrefOf(html), null);
  assert.match(html, /aria-disabled="true"/);
  const done = render(prog('root = Answer([e])', 'e = EmailDraft("Gas safety", "ops@example.com", "Hello, please book the inspection.")'));
  assert.doesNotMatch(done, /disabled=""/);
  assert.ok(hrefOf(done));
});

test('an EmailDraft sends the words it shows: the same scrubbed subject and body', () => {
  const html = render(prog('root = Answer([e])', 'e = EmailDraft("Data gap in UDR", "ops@acme.com", "Readings from svc-udr are missing.")'));
  const href = hrefOf(html);
  const body = decodeURIComponent(/body=([^&]*)/.exec(href)[1]);
  const subject = decodeURIComponent(/subject=([^&]*)/.exec(href)[1]);
  assert.doesNotMatch(body + subject, /UDR|svc-udr/);
  assert.ok(html.includes('>' + subject + '<'), 'the shown subject is the sent subject');
  assert.ok(html.includes(body), 'the shown body is the sent body');
});

test('Open in email is addressed only to real email addresses', () => {
  const mailto = (to, cc) => hrefOf(render(prog('root = Answer([e])', 'e = EmailDraft("S", ' + JSON.stringify(to) + ', "Hello"' + (cc ? ', ' + JSON.stringify(cc) : '') + ')')));
  assert.match(mailto('Pennard Fire Services (contract manager)', '[Your facilities manager]'), /^mailto:\?subject=S&body=Hello$/);
  assert.match(mailto('Jane Doe <jane@x.com>'), /^mailto:jane%40x\.com\?/);
  assert.match(mailto('a@b.com, Pennard'), /^mailto:a%40b\.com\?/);
  assert.match(mailto('ops@example.com; pm@x.co.uk'), /^mailto:ops%40example\.com,pm%40x\.co\.uk\?/);
  assert.match(mailto('—'), /^mailto:\?/);
  // the card still shows the To line as written
  const html = render(prog('root = Answer([e])', 'e = EmailDraft("S", "Jane Doe <jane@x.com>", "Hello")'));
  assert.match(html, /Jane Doe &lt;jane@x\.com&gt;/);
});

// ── buttons that would do nothing (#61) ─────────────────────────────────────
test('a link to a page inside one of the app\'s pages opens that page', () => {
  const calls = [];
  const host = { ask: (q) => calls.push(['ask', q]), open: (t) => calls.push(['open', t]) };
  assert.equal(M.routeAction({ type: 'open_url', params: { url: '/work-orders/WO-1184' } }, host), 'open');
  assert.equal(M.routeAction({ type: 'open_url', params: { url: '/compliance/vault' } }, host), 'open');
  assert.deepEqual(calls, [['open', { page: 'ops' }], ['open', { page: 'compliance' }]]);
  assert.equal(M.canOpen('/admin/secret'), false);
  assert.equal(M.canOpen('//evil.example/x'), false);
  assert.equal(M.canOpen('https://www.gov.uk/gas-safety'), true);
});

test('a button to somewhere the app cannot open asks its own label instead of doing nothing', () => {
  const html = render(prog('root = Answer([b])', 'b = Buttons([x, y])', 'x = Button("Open the asset register", @OpenUrl("/registers/assets"))', 'y = Button("Open Compliance", @OpenUrl("/compliance"))'));
  assert.equal(count(html, /ph-arrow-up-right/g), 1, 'only the page that opens is marked as opening one');
  assert.match(html, /Open the asset register<i class="ph ph-arrow-right"/);
});

// ── a stored view that draws nothing (#52) ──────────────────────────────────
test('a view that draws nothing shows the whole answer it came with, once', () => {
  const html = renderToStaticMarkup(React.createElement(M.OpenUIAnswer, {
    text: 'Apex is slipping.\nroot = Answer([k, t])\nk = Metricz("Overall score", 86.12, "down 2.04 points", "risk")\nt = Tablez(["Vendor", "Days overdue"], [["Apex", 12], ["Brightline", 3]])',
    fallbackText: 'Apex is slipping.\n\nOverall score **86.12**, down 2.04 points.\n\n| Vendor | Days overdue |\n| --- | --- |\n| Apex | 12 |\n| Brightline | 3 |'
  }));
  assert.match(html, /86\.12/);
  assert.match(html, />12</);
  assert.equal(count(html, /Apex is slipping\./g), 1);
});

// ── layout in the dock (#8, #38, #40, #50) ──────────────────────────────────
test('a record\'s values keep their width in the dock: the key column is capped and stacks when narrow', () => {
  assert.match(rule('.oui-fields'), /grid-template-columns:\s*fit-content\(40%\) minmax\(0, 1fr\)/);
  assert.match(css, /@container oui \(max-width: 380px\)\s*\{[^@]*\.oui-fields\s*\{\s*grid-template-columns:\s*minmax\(0, 1fr\)/);
});

test('a metric figure shrinks to fit its tile rather than breaking mid-number', () => {
  const html = render(prog('root = Answer([k])', 'k = Metrics([a, b, c])', 'a = Metric("Spend", "£12,480,300")', 'b = Metric("Intensity", "241.0 kWh/m²")',
    'c = Metric("Spend", "£12,480,300 across all fourteen sites")'));
  assert.match(html, /class="oui-metric-value" style="--oui-run:11"/);
  // a short figure and its unit are sized as one, so they stay on one line
  assert.match(html, /class="oui-metric-value" style="--oui-run:12"/);
  // a long one is sized by its longest unbroken run, and wraps between words
  assert.match(html, /class="oui-metric-value" style="--oui-run:11">£12,480,300 across/);
  assert.match(rule('.oui-metric'), /container-type:\s*inline-size/);
  assert.match(rule('.oui-metric-value'), /var\(--oui-run/);
});

test('long bar labels wrap rather than truncate to the same text', () => {
  const label = rule('.oui-bar-label');
  assert.doesNotMatch(label, /text-overflow:\s*ellipsis|white-space:\s*nowrap/);
  assert.match(label, /overflow-wrap:\s*anywhere/);
});

test('a table wider than its box shows that it scrolls', () => {
  const scroll = rule('.oui-scroll');
  assert.match(scroll, /local/);
  assert.match(scroll, /scroll(,|;|\s*$)/m);
});

test('a LineChart of figures written as text draws its line and prints each figure as written', () => {
  // The converters pass the answer's own figures ("290,000"), 27 Oct 2026.
  const html = render(prog('root = Answer([c])', 'c = LineChart("Electricity kWh", ["Jul", "Aug", "Sep"], [s])', 's = Series("Electricity kWh", ["290,000", "300,000", "380,000"])'));
  assert.match(html, /<(path|polyline)[^>]*(d|points)="[^"]*\d/);
  for (const v of ['290,000', '300,000', '380,000']) assert.match(html, new RegExp(v));
  assert.doesNotMatch(html, /290000/);
});

test('a chart value that is a word is printed as written, and its table is never dropped as a repeat', () => {
  // 28 Oct 2026: "Exempt" drew as "—", and the identical table beside it was hidden, so "Exempt" was nowhere.
  const html = render(prog('root = Answer([c, t])',
    'c = BarChart("SLA met", ["Apex", "Corvane", "Brightline Electrical"], ["94%", "81%", "Exempt"])',
    't = Table(["Vendor", "SLA met"], [["Apex", "94%"], ["Corvane", "81%"], ["Brightline Electrical", "Exempt"]])'));
  assert.ok(count(html, /Exempt/g) >= 2, 'in the chart and in the table');
  assert.equal(count(html, /class="oui-table"/g), 1);
  const line = render(prog('root = Answer([c])', 'c = LineChart("Score", ["Jul", "Aug", "Sep"], [s])', 's = Series("Score", ["82", "Not recorded", "85"])'));
  assert.match(line, /Not recorded/);
  // nothing written is still a dash
  const gap = render(prog('root = Answer([c])', 'c = BarChart("Jobs", ["A", "B"], [3, null])'));
  assert.match(gap, /—/);
});

test('a line too long to print its figures shows them in an open table under it', () => {
  // 28 Oct 2026: thirteen months drew only the last value; the rest sat in a closed "Figures".
  const months = Array.from({ length: 13 }, (_, i) => '"M' + (i + 1) + '"');
  const vals = Array.from({ length: 13 }, (_, i) => '"' + (400 + i * 7) + '"');
  const html = render(prog('root = Answer([c])', 'c = LineChart("kWh", [' + months.join(', ') + '], [s])', 's = Series("kWh", [' + vals.join(', ') + '])'));
  assert.match(html, /<details class="oui-figures" open="">/);
  for (const v of ['400', '442', '484']) assert.match(html, new RegExp('>' + v + '<'));
});
