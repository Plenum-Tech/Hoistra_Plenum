// openuiConvert — every answer drawn with the OpenUI components, whatever route wrote it
// (src/openui/fromMarkdown.js, src/openui/fromCompliance.js).
//
// Each program is read by OpenUI's own parser and drawn by its own <Renderer>: it must parse with
// nothing dropped, unresolved or orphaned, and every word of the answer must still be in it.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-convert.bundle.mjs');
const res = await build({
  stdin: {
    contents: [
      "export { library } from '../src/openui/library.jsx';",
      "export { parseProgram } from '../src/openui/parse.js';",
      "export { programStart } from '../src/openui/detect.js';",
      "export { programFromMarkdown, headlineOf, lit } from '../src/openui/fromMarkdown.js';",
      "export { programFromCompliance } from '../src/openui/fromCompliance.js';"
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

// Parses whole: no component dropped for a wrong type, no name left undefined or unused.
function clean(program) {
  const r = M.parseProgram(program);
  assert.ok(r && r.root && r.root.typeName === 'Answer', 'not a program:\n' + program);
  assert.deepEqual(r.meta.errors, [], JSON.stringify(r.meta.errors));
  assert.deepEqual(r.meta.unresolved, []);
  assert.deepEqual(r.meta.orphaned, []);
  return r;
}
const words = (s) => new Set((String(s).match(/[A-Za-z0-9£%]+/g) || []).map((w) => w.toLowerCase()));
// The rendition's words for a chart's table header and the follow-up lead; the chart and chips say them.
// and the opening line's own label ("**Headline:**"), which the Headline component says.
const SPOKEN_BY_COMPONENT = new Set(['item', 'value', 'ask', 'next', 'headline']);
function keepsEveryWord(md, program) {
  const have = words(program.replace(/\\n/g, ' '));
  const lost = [...words(md)].filter((w) => !have.has(w) && !SPOKEN_BY_COMPONENT.has(w));
  assert.deepEqual(lost, [], 'words dropped');
}
const names = (program) => (program.match(/^n\d+ = ([A-Z]\w*)\(/gm) || []).map((s) => s.replace(/^n\d+ = /, '').slice(0, -1));

// What svc-deepagents' rendition of the example dashboard reads as (tests/test_openui_view.py).
const DASHBOARD_MD = `Three things need you this week: the gas safety certificate at Building A expires on **Friday**, **7 work orders** are overdue and Building A is using **18% more energy** than in early October.

- Overdue work orders: 7 (+3 on last week)
- Certificates due in 30 days: 4 (1 this week)
- SLA met this month: 91% (-2 pt on September)
- Building A energy use: 241.0 kWh/m² (Building B: 178.4)

**Overdue work orders by trade**

| Item | Value |
| --- | --- |
| HVAC | 3 |
| Electrical | 2 |
| Plumbing | 1 |
| Fire safety | 1 |

## Overdue work orders

7 past their due date, 3 of them HVAC

| Work order | Job | Building | Days overdue | Vendor |
| --- | --- | --- | --- | --- |
| WO-1184 | AHU-2 drive belt replacement | Building A | 9 | Acme Mechanical |
| WO-1179 | Emergency lighting monthly test | Building B | 6 | Brightline Electrical |
| WO-1171 | Chiller 1 high-pressure alarm | Building A | 5 | Acme Mechanical |

_4 more: 2 plumbing, 1 electrical, 1 fire safety_

## What to do

1. **Book the gas safety inspection at Building A** — The certificate expires on Friday. (Draft the booking email)
2. **Chase Acme Mechanical on the 3 HVAC jobs** — Two are past their SLA. (Show the HVAC backlog)
3. **Check the AHU-2 schedule at Building A** — It has run overnight since 12 Oct.

Ask next: Compare energy use at Building A and Building B · Which vendors are slipping on SLA?`;

// An engine sub-agent's answer as it came back on 27 Oct (contract_performance).
const VENDORS_MD = `**Apex Mechanical is slipping most on SLA** in the latest available scorecard month, **September 2026**. Its SLA response component fell **0.73 points** to **23.44/25**, and SLA completion fell **0.62 points** to **21.88/25** versus August; its overall score also declined **2.04 points** to **86.12**.

| Vendor | Sep 2026 SLA response | Change vs Aug | Sep 2026 SLA completion | Change vs Aug | Overall trend |
| --- | --- | --- | --- | --- | --- |
| **Apex Mechanical** | 23.44/25 | **-0.73** | 21.88/25 | **-0.62** | **-2.04** |
| Apex Lifts | 24.11/25 | -0.06 | 23.21/25 | -0.12 | -0.40 |
| Corvane Mechanical | 22.45/25 | +0.45 | 20.92/25 | +0.42 | No prior trend |

All figures are from confirmed, contract-sourced scorecards and none is compliance-block capped. There is **no October 2026 scorecard yet**, so September is the newest available month.

**What to do:** Review Apex Mechanical's September SLA misses first—particularly completion performance—and request the underlying work-order scoring if you need the jobs and causes behind the decline.`;

// The compliance summary model's fallback answer, 27 Oct.
const SUMMARY_MD = `# Weekly Summary — Portfolio (Work Orders, Compliance, Energy)

**Headline:** No life-safety job is being actively worked, but one is overdue on both counts — Bishopsgate Tower's Fire Risk Assessment lapsed 120 days ago.

## 1. Risk to life — top priority

**Fire Risk Assessment (FRA-B-301-2025), Bishopsgate Tower — lapsed 120 days ago (expired 2026-06-29)**
Pennard Fire Services found compartmentation breaches at the L12 riser.

## 3. Energy — Bishopsgate Tower

10 open anomalies totalling **£214,927/year** in wasted energy cost. Largest contributors:

- Asset spike +157%, **£40,151/yr**, detected 2026-09-14
- Asset spike +137%, **£112,409/yr**, detected 2026-09-21

## Work orders

No work order data was returned in this week's feed.

## What to do

- **Reassess the Bishopsgate FRA now** (FRA-B-301-2025) and close the L12 riser fire-stopping remedial.`;

test('the example dashboard comes back as the components it was written with', () => {
  const p = M.programFromMarkdown(DASHBOARD_MD);
  clean(p);
  keepsEveryWord(DASHBOARD_MD, p);
  const n = names(p);
  assert.equal(n[0], 'Headline');
  assert.equal(n.filter((x) => x === 'Metric').length, 4);
  // Each bar carries its figure as written (27 Oct 2026), so the chart can stand in for the table.
  assert.match(p, /BarChart\("Overdue work orders by trade", \["HVAC", "Electrical", "Plumbing", "Fire safety"\], \["3", "2", "1", "1"\]\)/);
  assert.match(p, /Section\("Overdue work orders", /);
  assert.match(p, /Table\(\["Work order", "Job", "Building", "Days overdue", "Vendor"\]/);
  assert.equal(n.filter((x) => x === 'Step').length, 3);
  assert.match(p, /Step\("Book the gas safety inspection at Building A", "The certificate expires on Friday\. \(Draft the booking email\)"\)/);
  assert.match(p, /FollowUps\(\["Compare energy use at Building A and Building B", "Which vendors are slipping on SLA\?"\]\)$/);
});

test('an engine answer: headline, prose, its table, and nothing lost', () => {
  const p = M.programFromMarkdown(VENDORS_MD);
  clean(p);
  keepsEveryWord(VENDORS_MD, p);
  const n = names(p);
  assert.equal(n[0], 'Headline');
  // The score column is drawn as bars above the table, which stays whole beneath them.
  assert.match(p, /BarChart\("Sep 2026 SLA response", \["Apex Mechanical", "Apex Lifts", "Corvane Mechanical"\], \["23\.44\/25", "24\.11\/25", "22\.45\/25"\]\)/);
  assert.ok(n.indexOf('BarChart') < n.indexOf('Table'));
  // A signed change is not a length: it is never drawn as a bar.
  assert.ok(!/BarChart\("Change vs Aug"/.test(p) && !/BarChart\("Overall trend"/.test(p));
  const html = render(p);
  // A bold phrase is stressed words, not a figure: weight, no marker (28 Oct 2026).
  assert.match(html, /<strong class="oui-key">Apex Mechanical is slipping most on SLA<\/strong>/);
  assert.match(html, /class="oui-table"/);
  assert.match(html, /class="oui-bars"/);
  // The closing "**What to do:** …" is a step under its own label.
  assert.match(p, /Section\("What to do", \[n\d+\]\)/);
  assert.match(p, /Step\("Review Apex Mechanical's September SLA misses first/);
});

test('a records table only gets bars for a column that is one measure', () => {
  const mixed = M.programFromMarkdown('Lead.\n\n| Vendor | Score | Ref |\n| --- | --- | --- |\n| A | 91% | 1 |\n| B | 23.4/25 | 2 |\n| C | 80% | 3 |');
  assert.ok(!/BarChart/.test(mixed), 'percent beside a fraction is two measures; a Ref is not a measure');
  const linked = M.programFromMarkdown('Lead.\n\n| Vendor | Spend | Jobs |\n| --- | --- | --- |\n| [A](doc:1) | £100 | 3 |\n| B | £200 | 4 |\n| C | £300 | 5 |');
  assert.ok(!/BarChart/.test(linked), 'a label with a link stays in the table');
  const spend = M.programFromMarkdown('Lead.\n\n| Vendor | Jobs | Spend |\n| --- | --- | --- |\n| A | 3 | £1,200 |\n| B | 4 | — |\n| C | 5 | £300 |\n| D | 2 | £950 |');
  clean(spend);
  // Spend is named like a measure, so it is drawn rather than the first figure column (Jobs);
  // B's missing figure keeps its row with no bar.
  assert.match(spend, /BarChart\("Spend", \["A", "B", "C", "D"\], \["£1,200", null, "£300", "£950"\]\)/);
});

test('the compliance summary fallback: sections, figures and the steps to take', () => {
  const p = M.programFromMarkdown(SUMMARY_MD);
  clean(p);
  keepsEveryWord(SUMMARY_MD, p);
  assert.match(p, /Section\("1\. Risk to life — top priority"/);
  // The opening line under the title is the headline, its label said by the component.
  assert.match(p, /Section\("Weekly Summary — Portfolio \(Work Orders, Compliance, Energy\)", \[n1\]\)/);
  assert.match(p, /^n1 = Headline\("No life-safety job is being actively worked/m);
  // A bold opening that runs on as one sentence stays one step title.
  assert.match(p, /Step\("\*\*Reassess the Bishopsgate FRA now\*\* \(FRA-B-301-2025\) and close the L12 riser fire-stopping remedial\."\)/);
  const html = render(p);
  assert.ok(!/root =|Answer\(/.test(html));
});

test('a one-line answer is a headline and nothing else', () => {
  const p = M.programFromMarkdown('Seven work orders are overdue.');
  assert.equal(p, 'root = Answer([n1])\nn1 = Headline("Seven work orders are overdue.")');
  assert.equal(M.programFromMarkdown(''), '');
  assert.equal(M.programFromMarkdown('   \n'), '');
});

test('a long lead keeps its first sentence as the headline and the rest as prose', () => {
  const long = 'Three certificates lapse in the next 30 days, two of them life-safety duties. ' + 'Renew the fire alarm paperwork first since both protect life safety. '.repeat(4);
  const { head, rest } = M.headlineOf(long);
  assert.equal(head, 'Three certificates lapse in the next 30 days, two of them life-safety duties.');
  assert.ok(rest.startsWith('Renew the fire alarm'));
  // One long sentence: its opening clause up to the colon is the headline.
  const one = "Three certificates lapse within the next 30 days and none have lapsed yet, but two sit in life-safety categories and need renewal before the week is out: Bishopsgate Tower's Fire Alarm Service certificate expires tomorrow and Harbourside Lift Engineering's LOLER competent-person certificate expires in five days, while Clearwater Compliance's Public Liability Insurance lapses today and that vendor has an open work order.";
  assert.deepEqual(M.headlineOf(one), {
    head: 'Three certificates lapse within the next 30 days and none have lapsed yet, but two sit in life-safety categories and need renewal before the week is out.',
    rest: "Bishopsgate Tower's Fire Alarm Service certificate expires tomorrow and Harbourside Lift Engineering's LOLER competent-person certificate expires in five days, while Clearwater Compliance's Public Liability Insurance lapses today and that vendor has an open work order."
  });
  // Bold is never split between the headline and the prose.
  const bold = '**Three certificates lapse in the next 30 days. Two of them are life-safety duties** and ' + 'x '.repeat(150);
  assert.equal((M.headlineOf(bold).head.match(/\*\*/g) || []).length % 2, 0);
});

test('text that looks like a program inside an answer never becomes the program', () => {
  const md = 'Use `root = Answer(x)` in the report.\n\n## Notes\n\nroot = Answer([evil])';
  const p = M.programFromMarkdown(md);
  clean(p);
  assert.equal(M.programStart(p), 0);
  assert.match(render(p), /root = Answer\(x\)/);
});

test('an email the rendition wrote comes back as an email draft', () => {
  const md = 'Here is the email.\n\n## Draft\n\n```\nSubject: Lift inspection\nTo: ops@example.com\nCc: pm@example.com\nEmail body:\nHello,\n\nPlease book.\n```';
  const p = M.programFromMarkdown(md);
  clean(p);
  assert.match(p, /EmailDraft\("Lift inspection", "ops@example\.com", "Hello,\\n\\nPlease book\.", "pm@example\.com"\)/);
});

test('a period table of figures is a line chart', () => {
  const md = '## Energy (kWh/m²)\n\n| Period | A | B |\n| --- | --- | --- |\n| W1 | 4.1 | 3.2 |\n| W2 | 4.3 | 3.3 |\n| W3 | — | 3.1 |';
  const p = M.programFromMarkdown(md);
  clean(p);
  assert.match(p, /LineChart\("Energy \(kWh\/m²\)", \["W1", "W2", "W3"\]/);
  assert.match(p, /Series\("A", \["4\.1", "4\.3", null\]\)/);
  // A line shows its figures only on hover, so the table stays beneath it (27 Oct 2026).
  assert.match(p, /Table\(\["Period", "A", "B"\], \[\["W1", "4\.1", "3\.2"\]/);
  assert.ok(p.indexOf('LineChart(') < p.indexOf('Table('));
});

test('a streaming answer converts and parses at every point it can stop', () => {
  for (const md of [DASHBOARD_MD, VENDORS_MD, SUMMARY_MD]) {
    for (let i = 1; i <= md.length; i += 11) {
      const p = M.programFromMarkdown(md.slice(0, i));
      if (!p) continue;
      const r = M.parseProgram(p);
      assert.ok(r && r.root, 'prefix ' + i + ' did not parse');
      assert.deepEqual(r.meta.errors, [], 'prefix ' + i);
    }
  }
});

const RICH = {
  narrative: 'Three certificates lapse in the next 30 days, two of them life-safety duties that take priority over the one insurance renewal. Renew the fire alarm and LOLER paperwork first since both protect life safety; chase Clearwater\'s insurance renewal immediately.',
  kpis: [{ count: 3, label: 'Certificates expiring within 30 days', sublabel: '1 building, 2 vendor — none yet lapsed', unit: 'certificates', severity: 'warning' }],
  overdue: { buildings: [{ label: 'Building — Bishopsgate Tower', days: 120, severity: 'critical' }], vendors: [] },
  actions: [
    { severity: 'critical', scope: 'building', title: 'Book the Fire Alarm Service renewal for Bishopsgate Tower (B-301) before 2026-10-28', cert_ids: ['c1'], tags: ['fire', 'life-safety'] },
    { severity: 'warning', scope: 'vendor', title: 'Chase Clearwater Compliance to renew Public Liability Insurance', cert_ids: ['c3'], tags: ['insurance'] }
  ],
  groups: [{ owner: 'Bishopsgate Tower (Fire Alarm)', scope: 'building', severity: 'critical', headline: 'Fire Alarm Service certificate for block B-301 expires tomorrow', points: ['Certificate FAS-B-301-2026-03 (Fire Alarm Service) expires 2026-10-28 — 1 day remaining'], cert_ids: ['c1'] }],
  insights: [{ type: 'correlation', text: 'Both lapsing building certificates sit on the same riser.' }],
  certificates: [
    { id: 'c1', name: 'Fire Alarm Service', company: 'Bishopsgate Tower', scope: 'building', status: 'expiring', reason: '1 day left', severity: 'critical' },
    { id: 'c3', name: 'Public Liability Insurance', company: 'Clearwater Compliance', scope: 'vendor', status: 'lapsed', severity: 'critical' }
  ],
  pending: [], offers: [], steps: [], cost: null
};

test('a compliance answer is drawn with the same components, every value its own', () => {
  const p = M.programFromCompliance(RICH);
  clean(p);
  const n = names(p);
  assert.deepEqual(n.slice(0, 2), ['Headline', 'Text']);
  assert.match(p, /Metric\("Certificates expiring within 30 days", 3, "1 building, 2 vendor — none yet lapsed · certificates", "warn"\)/);
  assert.match(p, /BarChart\("Days overdue — buildings", \["Building — Bishopsgate Tower"\], \[120\], "days"\)/);
  assert.match(p, /Step\("Book the Fire Alarm Service renewal for Bishopsgate Tower \(B-301\) before 2026-10-28", "Critical · Building · 1 cert · fire, life-safety"\)/);
  assert.match(p, /Callout\("risk", "Fire Alarm Service certificate for block B-301 expires tomorrow"\)/);
  assert.match(p, /Callout\("info", "Both lapsing building certificates sit on the same riser\.", "Correlation detected"\)/);
  assert.match(p, /Section\("Certificates in scope · 2", \[n\d+\], "1 expiring, 1 lapsed", true\)/);
  const html = render(p);
  assert.match(html, /Priority actions/);
  assert.match(html, /oui-disclosure/);
});

test('a compliance payload with nothing in it writes no program', () => {
  assert.equal(M.programFromCompliance({ narrative: '', kpis: [], actions: [], groups: [], insights: [], certificates: [] }), '');
  assert.equal(M.programFromCompliance(null), '');
});

// Found by fuzzing every block shape together (27 Oct 2026): each of these once lost words.
test('a heading with only a rule under it keeps its words', () => {
  const p = M.programFromMarkdown('Lead.\n\n### What to do\n\n---\n\n## Totals\n\n- **Open:** 7\n- **Closed:** 4');
  clean(p);
  assert.match(p, /Text\("\*\*What to do\*\*"\)/);
});

test('a section title is never dropped for a chart that does not carry it', () => {
  // A rule before the table: the chart still opens the section, so it takes the title.
  const ruled = M.programFromMarkdown('Lead.\n\n## Energy by month\n\n---\n\n| Month | A | B |\n| --- | --- | --- |\n| Jan | 1 | 2 |\n| Feb | 3 | 4 |\n| Mar | 5 | 6 |');
  clean(ruled);
  assert.match(ruled, /LineChart\("Energy by month", /);
  // Prose before the table: the chart is named by its columns, and the section keeps the title.
  const prose = M.programFromMarkdown('Lead.\n\n## Energy by month\n\nUse fell.\n\n| Month | A | B |\n| --- | --- | --- |\n| Jan | 1 | 2 |\n| Feb | 3 | 4 |\n| Mar | 5 | 6 |');
  clean(prose);
  assert.match(prose, /Section\("Energy by month", \[n\d+, n\d+, n\d+\]\)/);
  assert.match(prose, /LineChart\("A and B", /);
});

test('a pipe written inside a table cell stays in its cell', () => {
  const p = M.programFromMarkdown('Lead.\n\n| Code | Note |\n| --- | --- |\n| A1 | in \\| out |\n| B2 | plain |');
  clean(p);
  assert.match(p, /\["A1", "in \| out"\]/);
  // and a row wider than its header keeps every cell
  const wide = M.programFromMarkdown('Lead.\n\n| Code | Note |\n| --- | --- |\n| A1 | x | extra |');
  assert.match(wide, /Table\(\["Code", "Note", ""\], \[\["A1", "x", "extra"\]\]\)/);
});
