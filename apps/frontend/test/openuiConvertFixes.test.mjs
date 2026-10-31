// openuiConvertFixes — the converters' defects found by the 27 Oct 2026 review, each a case that
// once drew something the answer did not say: a year as a bar, a table swapped for a chart whose
// figures only show on hover, a month series running backwards, a bad count painted green, an
// answer cut to its first paragraph. Every program here must still parse whole and keep every
// word of the answer (test/openuiConvert.test.mjs holds the shapes these sit beside).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const here = path.dirname(fileURLToPath(import.meta.url));
const outFile = path.join(here, '.openui-convert-fixes.bundle.mjs');
const res = await build({
  stdin: {
    contents: [
      "export { library } from '../src/openui/library.jsx';",
      "export { parseProgram } from '../src/openui/parse.js';",
      "export { programFromMarkdown, draftSafe } from '../src/openui/fromMarkdown.js';",
      "export { programFromCompliance } from '../src/openui/fromCompliance.js';",
      "export { kpiOf, keyValue, toneOf } from '../src/logic/answerCards.js';",
      "export { parseBlocks } from '../src/logic/markdownBlocks.js';"
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
const render = (program) => renderToStaticMarkup(
  React.createElement(Renderer, { library: M.library, response: program, isStreaming: false, publishObservability: false })
);

function clean(program) {
  const r = M.parseProgram(program);
  assert.ok(r && r.root && r.root.typeName === 'Answer', 'not a program:\n' + program);
  assert.deepEqual(r.meta.errors, [], JSON.stringify(r.meta.errors));
  assert.deepEqual(r.meta.unresolved, []);
  assert.deepEqual(r.meta.orphaned, []);
  return r;
}
const words = (s) => new Set((String(s).match(/[A-Za-z0-9£%]+/g) || []).map((w) => w.toLowerCase()));
// Words a component says itself: the "Ask next" lead of the chips, and the name of a bar chart's
// label column ("Vendor", "Tariff"), whose rows are the bars' own labels.
function keepsEveryWord(md, program, spoken = []) {
  const have = words(program.replace(/\\n/g, ' '));
  const lost = [...words(md.replace(/^\s*\d+[.)]\s+/gm, ''))].filter((w) => !have.has(w) && !spoken.includes(w));
  assert.deepEqual(lost, [], 'words dropped');
}
// Converted, parsed whole, every word kept.
function convert(md, spoken) {
  const p = M.programFromMarkdown(md);
  clean(p);
  keepsEveryWord(md, p, spoken);
  return p;
}
const steps = (p) => (p.match(/= Step\(/g) || []).length;

// ── Charts draw measures only ──────────────────────────────────────────────────────────────────

test('a column of years, codes, ranks or phone numbers is a table, never bars (#3, #11)', () => {
  const tables = {
    year: ['Install years.\n\n| Asset | Install year |\n|---|---|\n| Lift 1 | 1998 |\n| Lift 2 | 2004 |\n| Lift 3 | 2012 |', '"1998"'],
    opened: ['Opening years for your buildings.\n\n| Building | Year opened |\n|---|---|\n| Bishopsgate Tower | 1998 |\n| Harbour Point | 2011 |\n| Manchester Town Hall | 1877 |', '"1877"'],
    centre: ['Cost centres.\n\n| Building | Cost centre |\n|---|---|\n| Bishopsgate Tower | 004120 |\n| Harbour Point | 004133 |\n| Manchester Town Hall | 0071 |', '"004120"'],
    rank: ['Vendor ranking.\n\n| Vendor | Rank |\n|---|---|\n| Apex Lifts | 1 |\n| Corvane Mechanical | 2 |\n| Kestamere Fire | 3 |\n| Brightline Electrical | 4 |', '"1"'],
    phone: ['Site phones.\n\n| Site | Phone |\n|---|---|\n| A | 02079460000 |\n| B | 01614960000 |\n| C | 01134960000 |', '"02079460000"'],
    wo: ['Open jobs.\n\n| Building | WO |\n|---|---|\n| A | 100234 |\n| B | 100871 |\n| C | 101502 |', '"100234"']
  };
  for (const [name, [md, cell]] of Object.entries(tables)) {
    const p = convert(md);
    assert.ok(!/BarChart\(/.test(p), name + ' drew bars:\n' + p);
    assert.ok(/= Table\(/.test(p) && p.includes(cell), name + ' lost its table:\n' + p);
  }
  // In a records table the id-like columns are not the bars either.
  const records = {
    Invoice: 'Invoices.\n\n| Vendor | Invoice | Status |\n|---|---|---|\n| Apex | 100234 | Paid |\n| Brite | 100871 | Open |\n| Coyle | 101502 | Open |',
    Floor: 'Rooms.\n\n| Room | Floor | Priority | Status |\n|---|---|---|---|\n| R1 | 3 | 1 | Open |\n| R2 | 12 | 2 | Open |\n| R3 | 7 | 3 | Closed |',
    'Cost centre': 'Spend.\n\n| Building | Cost centre | Rank | Note |\n|---|---|---|---|\n| A | 004120 | 1 | x |\n| B | 004133 | 2 | y |\n| C | 0071 | 3 | z |'
  };
  for (const [name, md] of Object.entries(records)) {
    const p = convert(md);
    assert.ok(!/BarChart\(/.test(p), name + ' drew bars:\n' + p);
  }
  // A real measure still draws.
  assert.match(convert('Overdue by building.\n\n| Building | Overdue |\n|---|---|\n| A | 4 |\n| B | 2 |\n| C | 7 |'), /BarChart\(/);
  // "Number of", "per year" and an area are measures, not ids.
  assert.match(convert('Jobs.\n\n| Vendor | Number of jobs |\n|---|---|\n| A | 4 |\n| B | 2 |\n| C | 7 |', ['vendor']), /BarChart\(/);
  assert.match(convert('Spend.\n\n| Vendor | Spend this year |\n|---|---|\n| A | 400 |\n| B | 200 |\n| C | 700 |', ['vendor']), /BarChart\(/);
});

test('bars show each figure as written, never rounded (#12)', () => {
  const p = convert('## Unit rates\n\n| Tariff | Rate (£/kWh) |\n|---|---|\n| Day | 0.245 |\n| Night | 0.125 |\n| Weekend | 0.1875 |', ['tariff']);
  assert.match(p, /BarChart\("Unit rates · Rate \(£\/kWh\)", \["Day", "Night", "Weekend"\], \["0\.245", "0\.125", "0\.1875"\]\)/);
  assert.match(render(p), /0\.1875/);
  const rec = convert('Scores.\n\n| Vendor | Trade | Score |\n|---|---|---|\n| Apex | HVAC | 86.125 |\n| Brite | Electrical | 86.12 |\n| Coyle | Plumbing | 1,200.50 |');
  assert.match(rec, /BarChart\("Score", \["Apex", "Brite", "Coyle"\], \["86\.125", "86\.12", "1,200\.50"\]\)/);
});

test('a 2-column table whose rows carry extra cells keeps them in a table', () => {
  const p = convert('Lead.\n\n| Trade | Overdue |\n|---|---|\n| HVAC | 3 | since Monday |\n| Electrical | 2 |\n| Plumbing | 1 |');
  assert.match(p, /= Table\(/);
  assert.match(p, /"since Monday"/);
});

test('fractions over different wholes are not one measure (#14, #26)', () => {
  const weights = convert('Score breakdown.\n\n| Component | Weight | Score |\n|---|---|---|\n| SLA response | 25% | 23.44/25 |\n| Quality | 10% | 8/10 |\n| Safety | 5% | 4.5/5 |\n| Overall | 100% | 86.12/100 |');
  assert.ok(!/BarChart\("Score"/.test(weights), weights);
  const jobs = convert('Jobs done.\n\n| Vendor | Jobs completed | Trade |\n|---|---|---|\n| Apex | 9/10 | HVAC |\n| Brite | 10/40 | Electrical |\n| Coyle | 3/4 | Plumbing |');
  assert.ok(!/BarChart\(/.test(jobs), jobs);
  // One whole for every row (all out of 25) is one measure.
  assert.match(convert('Scores.\n\n| Vendor | SLA | Trade |\n|---|---|---|\n| A | 23.44/25 | HVAC |\n| B | 24.11/25 | Lifts |\n| C | 22.45 / 25 | HVAC |'), /BarChart\("SLA", \["A", "B", "C"\], \["23\.44\/25", "24\.11\/25", "22\.45 \/ 25"\]\)/);
});

// ── Period tables: the line goes above the table, oldest to newest, one unit ─────────────────

test('a period table keeps its table under the line, and units never share an axis (#10)', () => {
  const md = '## Energy cost and SLA by month\n\n| Month | Spend (£) | SLA met (%) |\n| --- | --- | --- |\n| Jul | 12,400 | 91 |\n| Aug | 13,100 | 88 |\n| Sep | 9,850 | 94 |';
  const p = convert(md);
  assert.match(p, /= Table\(\["Month", "Spend \(£\)", "SLA met \(%\)"\]/);
  assert.ok(p.includes('"12,400"') && p.includes('"91"'));
  const line = /LineChart\([^\n]*\[(n\d+(?:, n\d+)*)\]\)/.exec(p);
  assert.ok(line, p);
  assert.equal(line[1].split(', ').length, 1, 'two units on one axis:\n' + p);
  assert.ok(p.indexOf('LineChart(') < p.indexOf('Table('));
  // An id beside a count is not a series.
  const ids = convert('Lead.\n\n| Month | WO id | Count |\n| --- | --- | --- |\n| Jul | 10012 | 5 |\n| Aug | 10045 | 7 |\n| Sep | 10078 | 9 |');
  assert.ok(!/Series\("WO id"/.test(ids), ids);
  assert.match(ids, /Series\("Count", \["5", "7", "9"\]\)/);
  // Build years are not a series either.
  const built = convert('Lead.\n\n| Year | Built | Floors |\n| --- | --- | --- |\n| 2024 | 1998 | 12 |\n| 2025 | 2004 | 14 |\n| 2026 | 2010 | 9 |');
  assert.ok(!/Series\("Built"/.test(built), built);
  // Two series in one unit and range still share the chart.
  const kwh = convert('Lead.\n\n| Month | Electricity kWh | Gas kWh |\n|---|---|---|\n| Jul 2026 | 290,000 | 40,000 |\n| Aug 2026 | 300,000 | 41,000 |\n| Sep 2026 | 380,000 | 48,000 |');
  assert.match(kwh, /LineChart\("Electricity kWh and Gas kWh", \["Jul 2026", "Aug 2026", "Sep 2026"\], \[n\d+, n\d+\]\)/);
});

test('a newest-first period table is drawn oldest to newest; an unordered one is only a table (#24)', () => {
  const md = 'Electricity rose sharply this autumn.\n\n| Month | Electricity kWh | Gas kWh |\n|---|---|---|\n| Oct 2026 | 410,000 | 52,000 |\n| Sep 2026 | 380,000 | 48,000 |\n| Aug 2026 | 300,000 | 41,000 |\n| Jul 2026 | 290,000 | 40,000 |';
  const p = convert(md);
  assert.match(p, /LineChart\("Electricity kWh and Gas kWh", \["Jul 2026", "Aug 2026", "Sep 2026", "Oct 2026"\]/);
  assert.match(p, /Series\("Electricity kWh", \["290,000", "300,000", "380,000", "410,000"\]\)/);
  assert.match(p, /Series\("Gas kWh", \["40,000", "41,000", "48,000", "52,000"\]\)/);
  // The table keeps the order the answer wrote.
  assert.match(p, /Table\(\["Month", "Electricity kWh", "Gas kWh"\], \[\["Oct 2026", "410,000", "52,000"\]/);
  for (const labels of [['2026-10', '2026-09', '2026-08'], ['Q3 2026', 'Q2 2026', 'Q1 2026'], ['Week 3', 'Week 2', 'Week 1']]) {
    const t = convert('Lead.\n\n| Period | Jobs |\n|---|---|\n' + labels.map((l, i) => '| ' + l + ' | ' + (9 - i) + ' |').join('\n'));
    assert.match(t, new RegExp('LineChart\\([^\\n]*\\[' + labels.slice().reverse().map((l) => '"' + l + '"').join(', ') + '\\]'), t);
  }
  // Out of order, or labels that say no order: no line at all, the table alone.
  const mixed = convert('Lead.\n\n| Month | Jobs | Hours |\n|---|---|---|\n| Aug | 4 | 9 |\n| Jul | 5 | 8 |\n| Sep | 6 | 7 |');
  assert.ok(!/LineChart/.test(mixed) && /= Table\(/.test(mixed), mixed);
  const named = convert('Lead.\n\n| Week | Jobs |\n|---|---|\n| Week A | 4 |\n| Week B | 5 |\n| Week C | 6 |');
  assert.ok(!/LineChart/.test(named) && /= Table\(/.test(named), named);
  // A period column of plain ordinals (hour 9, 10, 11) is in order too.
  assert.match(convert('Lead.\n\n| Hour | Jobs |\n|---|---|\n| 9 | 4 |\n| 10 | 5 |\n| 11 | 6 |'), /LineChart\("Jobs", \["9", "10", "11"\]/);
});

// ── Steps ─────────────────────────────────────────────────────────────────────────────────────

test('an indented line under an action is its detail, and a lead-in is not an action (#9)', () => {
  const tight = 'Two things need you.\n\n## What to do\n\n1. **Book the gas safety inspection**\n   The certificate expires on Friday.\n2. **Chase Apex Mechanical**\n   Two jobs are past SLA.';
  const loose = 'Two things need you.\n\n## What to do\n\n1. **Book the gas safety inspection**\n\n   The certificate expires on Friday.\n\n2. **Chase Apex Mechanical**\n\n   Two jobs are past SLA.';
  for (const md of [tight, loose]) {
    const p = convert(md);
    assert.equal(steps(p), 2, p);
    assert.match(p, /Step\("\*\*Book the gas safety inspection\*\*", "The certificate expires on Friday\."\)/);
    assert.match(p, /Step\("\*\*Chase Apex Mechanical\*\*", "Two jobs are past SLA\."\)/);
  }
  const lead = convert('Two calls.\n\n## Next steps\n\nThese matter most:\n\n1. Call Apex\n2. Call Corvane');
  assert.equal(steps(lead), 2, lead);
  assert.match(lead, /Text\("These matter most:"\)/);
  assert.ok(!/Step\("These matter most:/.test(lead));
});

// ── Lead and headings ─────────────────────────────────────────────────────────────────────────

test('a bold opening sentence is the headline, not a section label (#13)', () => {
  const md = '**Three certificates lapse this month, two of them life-safety.**\n\nThe fire alarm certificate at Bishopsgate expires on Friday.\n\n- **Lapsing:** 3\n- **Life-safety:** 2';
  const p = convert(md);
  assert.ok(p.startsWith('root = Answer([n1'), p);
  assert.match(p, /^n1 = Headline\("\*\*Three certificates lapse this month, two of them life-safety\.\*\*"\)$/m);
  assert.ok(!/Section\("Three certificates/.test(p));
  // A bold label ending in a colon still titles what follows it.
  assert.match(convert('**Contracts Behind Plan:**\n\n- A: 3\n- B: 4'), /Section\("Contracts Behind Plan", /);
});

// ── Follow-ups ────────────────────────────────────────────────────────────────────────────────

test('"Ask next" makes chips only from questions after its colon (#15)', () => {
  const bare = convert('Seven are overdue.\n\n## Overdue work orders\n\nApex holds four.\n\nAsk next:\n- Which vendor is slipping?\n- Show the HVAC backlog');
  const bold = convert('Seven are overdue.\n\n## Overdue\n\nApex holds four.\n\n**Ask next:**');
  const week = convert('Seven are overdue.\n\nApex holds four.\n\nAsk next week for the revised schedule from Apex.');
  const step = convert('Seven are overdue.\n\n## What to do\n\n1. Chase Apex.\n2. Ask next week for the revised schedule from Apex.');
  for (const p of [bare, bold, week, step]) {
    assert.ok(!/FollowUps\(\[":"\]\)|FollowUps\(\["\*\*"\]\)/.test(p), p);
    assert.ok(!/FollowUps\(\[[^\]]*week for/.test(p), p);
  }
  assert.match(week, /Text\("[^"]*Ask next week for the revised schedule from Apex\./);
  assert.match(step, /Step\("Ask next week for the revised schedule from Apex\."\)/);
  // The rendition's own line still becomes chips, bold or not.
  assert.match(convert('Lead.\n\n**Ask next:** Which vendor is slipping? · Show the HVAC backlog', ['ask', 'next']), /FollowUps\(\["Which vendor is slipping\?", "Show the HVAC backlog"\]\)/);
  assert.match(convert('Lead.\n\n**Ask next**: Which vendor is slipping?', ['ask', 'next']), /FollowUps\(\["Which vendor is slipping\?"\]\)/);
});

// ── Lists ─────────────────────────────────────────────────────────────────────────────────────

test('a sub-bullet under a numbered item keeps its own mark, and the items their numbers (#16)', () => {
  const md = '1. **Apex** missed 4 SLAs\n   - 2 were HVAC\n2. **Brightline** missed 1 SLA\n   - fire alarm test\n3. **Corvane** missed none';
  const p = convert(md);
  assert.match(p, /Text\("1\. \*\*Apex\*\* missed 4 SLAs\\n {3}- 2 were HVAC\\n2\. \*\*Brightline\*\* missed 1 SLA\\n {3}- fire alarm test\\n3\. \*\*Corvane\*\* missed none"\)/);
  const b = M.parseBlocks(md)[0];
  assert.deepEqual(b.marks, ['1.', '-', '2.', '-', '3.']);
});

// ── Figures ───────────────────────────────────────────────────────────────────────────────────

test('a duration, a date or a change is not a figure tile (#20, #45, #54)', () => {
  const kpi = (s) => M.kpiOf(M.keyValue(s));
  assert.equal(kpi('Average response time: 4h 30m'), null);
  assert.equal(kpi('MTTR: 4h'), null);
  assert.equal(kpi('Lead time: 3d 4h'), null);
  assert.equal(kpi('Average backlog: 5.25 days').value, '5.25');
  assert.equal(kpi('**Expires:** 28 Oct 2026'), null);
  assert.equal(kpi('**Due:** 1st March 2026'), null);
  assert.equal(kpi('**Raised:** 14 February 2026'), null);
  assert.equal(kpi('**Inspected:** 5 May'), null);
  assert.equal(kpi('Overall score: 2.04 points lower than August'), null);
  assert.equal(kpi('Energy use: 18% more than early October'), null);
  // Counts whose words only look like those are still figures.
  assert.equal(kpi('**Sites:** 3 marked overdue').value, '3');
  assert.equal(kpi('**Count:** 5 may need review').value, '5');
  assert.equal(kpi('**Buildings:** 5 below target').value, '5');
  assert.deepEqual(kpi('**Unconverted Recommendations:** 16 (8 flagged by energy)'), { value: '16', label: 'Unconverted Recommendations', sub: '(8 flagged by energy)' });

  const dates = convert('Lead.\n\n- **Expires:** 28 Oct 2026\n- **Last inspected:** 3 Sep 2025\n- **Engineer:** Pennard Fire Services');
  assert.ok(!/Metric\(/.test(dates), dates);
  assert.match(dates, /28 Oct 2026/);
  const times = convert('Lead.\n\n- Average response time: 4h 30m\n- MTTR: 4h\n- Open: 4');
  assert.ok(!/Metric\("Average response time"/.test(times) && !/Metric\("MTTR"/.test(times), times);
  const change = convert('Lead.\n\n- Overall score: 2.04 points lower than August\n- Energy use: 18% more than early October\n- Open: 4');
  assert.ok(!/Metric\(/.test(change), change);
  assert.match(change, /Overall score: 2\.04 points lower than August/);
  assert.equal((convert('Lead.\n\n- **Sites:** 3 marked overdue\n- **Count:** 5 may need review').match(/= Metric\(/g) || []).length, 2);
});

// ── Tone ──────────────────────────────────────────────────────────────────────────────────────

const toneIn = (p, label) => {
  const m = new RegExp('Metric\\(' + JSON.stringify(label).replace(/[()]/g, '\\$&') + ', [^\\n]*\\)$', 'm').exec(p);
  assert.ok(m, label + ' not a Metric:\n' + p);
  const t = /, "(risk|warn|ok)"\)$/.exec(m[0]);
  return t ? t[1] : null;
};

test('a figure is green only when its label is good news and the figure is not none (#25, #46, #47)', () => {
  const p = convert('Portfolio status for the estate this month.\n\n- Buildings not compliant: 3\n- Assets not in control: 5\n- PPM jobs not completed: 12\n- Low-risk assets: 40\n- Overdue jobs: 0\n- Compliant buildings: 12');
  assert.equal(toneIn(p, 'Buildings not compliant'), 'risk');
  assert.equal(toneIn(p, 'Assets not in control'), 'risk');
  assert.equal(toneIn(p, 'PPM jobs not completed'), 'risk');
  assert.notEqual(toneIn(p, 'Low-risk assets'), 'risk');
  assert.equal(toneIn(p, 'Compliant buildings'), 'ok');
  const z = convert('Lead.\n\n- **Compliant buildings:** 0 of 12\n- **Completed on time:** 0%\n- **Open jobs:** 14\n- **In control:** 11 of 12');
  assert.notEqual(toneIn(z, 'Compliant buildings'), 'ok');
  assert.notEqual(toneIn(z, 'Completed on time'), 'ok');
  assert.equal(toneIn(z, 'In control'), 'ok');
  assert.equal(M.toneOf('Buildings not compliant'), 'critical');
  assert.equal(M.toneOf('Non-compliant buildings'), 'critical');
  assert.equal(M.toneOf('Low-risk assets'), 'neutral');
  assert.equal(M.toneOf('Low risk assets overdue'), 'critical');
  assert.equal(M.toneOf('High-risk assets'), 'critical');
});

test('good news under a risk-worded heading is not a red finding (#44)', () => {
  const p = convert('Portfolio status for this week.\n\n## Overdue work orders\n\nNone — every work order is within its due date.\n\n## Lapsed certificates\n\nNo certificate has lapsed.\n\n## Due this week\n\nNothing is due this week.\n\n## Overdue jobs\n\n0 work orders are overdue.');
  assert.ok(!/Callout\("risk"/.test(p) && !/Callout\("warn"/.test(p), p);
  assert.match(convert('Portfolio status for this week.\n\n## Lapsed certificates\n\nTwo certificates have lapsed.'), /Callout\("risk", "Two certificates have lapsed\.", "Lapsed certificates"\)/);
});

// ── Compliance cards: the whole answer, not its first paragraph ───────────────────────────────

// The planner's _lead (svc-deepagents agents/planner.py): the first paragraph over 20 characters
// that is not a bullet, Markdown marks stripped, at most 600 characters.
const lead = (md) => {
  for (const para of md.split(/\n\s*\n/)) {
    const t = para.replace(/[*_`#>|]/g, '').trim();
    if (t && !t.startsWith('-') && t.length > 20) return t.slice(0, 600);
  }
  return '';
};
const CANDIDATES = 'Three assets are due for replacement this year, led by **Chiller 1** at Harbour Point (condition score 2.1/5).\n\n| Asset | Building | Cost |\n| --- | --- | --- |\n| Chiller 1 | Harbour Point | £84,000 |\n| AHU-2 | Harbour Point | £31,500 |\n| Boiler B2 | Bishopsgate Tower | £22,800 |\n\n> Costs exclude VAT and are indicative.\n\n1. Request a quote for Chiller 1.\n2. Schedule a survey of AHU-2.';

test('a card answer whose narrative is the answer\'s opening shows the whole answer (#1)', () => {
  const rich = {
    narrative: lead(CANDIDATES),
    kpis: [{ count: 3, label: 'Replacement candidates', unit: 'assets', severity: 'warning' }],
    actions: [{ title: 'Review Chiller 1 at Harbour Point', severity: 'critical' }]
  };
  const p = M.programFromCompliance(rich, CANDIDATES);
  clean(p);
  keepsEveryWord(CANDIDATES, p);
  for (const s of ['£84,000', '£31,500', '£22,800', 'Costs exclude VAT and are indicative.', 'Request a quote for Chiller 1.', 'Schedule a survey of AHU-2.']) assert.ok(p.includes(s), s);
  assert.match(p, /^n1 = Headline\("Three assets are due for replacement this year/m);
  assert.match(p, /Metric\("Replacement candidates", 3, "assets", "warn"\)/);
  assert.match(p, /Step\("Review Chiller 1 at Harbour Point"/);

  // A bullet-only answer has no narrative at all.
  const bullets = '- Chiller 1 needs replacing\n- AHU-2 needs a survey';
  const b = M.programFromCompliance({ narrative: lead(bullets), kpis: rich.kpis }, bullets);
  clean(b);
  keepsEveryWord(bullets, b);
  // A table-first answer: the narrative is the table flattened, never a headline of pipes.
  const tableFirst = '| Asset | Building | Condition |\n|---|---|---|\n| Chiller 1 | Harbour Point | 2.1 |\n| AHU-2 | Harbour Point | 3.4 |\n\nNo quote exists yet for AHU-2.';
  const t = M.programFromCompliance({ narrative: lead(tableFirst), kpis: rich.kpis }, tableFirst);
  clean(t);
  keepsEveryWord(tableFirst, t);
  assert.ok(!/Headline\("Asset/.test(t) && !/---/.test(t), t);
  // A lead the planner took from a later paragraph is still the answer's own.
  const later = 'Short.\n\nThree assets are due for replacement this year at Harbour Point.\n\n- Chiller 1\n- AHU-2';
  const l = M.programFromCompliance({ narrative: lead(later), kpis: rich.kpis }, later);
  clean(l);
  keepsEveryWord(later, l);
});

test('a compliance narrative of its own is kept, and no answer means the narrative alone (#1)', () => {
  const rich = { narrative: 'Three certificates lapse in the next 30 days.', kpis: [{ count: 3, label: 'Expiring', severity: 'warning' }] };
  const own = M.programFromCompliance(rich, 'Here is your compliance summary for the week.');
  clean(own);
  assert.match(own, /Headline\("Three certificates lapse in the next 30 days\."\)/);
  assert.ok(!/compliance summary for the week/.test(own));
  assert.equal(M.programFromCompliance(rich), M.programFromCompliance(rich, ''));
});

// ── The live draft ────────────────────────────────────────────────────────────────────────────

test('the live draft hides an unclosed bold mark and a table row with no divider yet (#48)', () => {
  assert.equal(M.draftSafe('**25 work orders are o'), '25 work orders are o');
  assert.ok(!/\*\*/.test(M.programFromMarkdown(M.draftSafe('**25 work orders are o'))));
  assert.equal(M.draftSafe('Energy use fell.\n\n| Building | Aug kWh/m² | Sep kWh/m² '), 'Energy use fell.\n');
  assert.equal(M.draftSafe('x\n| A | B |'), 'x');
  assert.equal(M.draftSafe('x\n| A | B |\n'), 'x');
  // Once the divider is in, the table stays, its last row however far it got.
  const t = 'x\n\n| A | B |\n| --- | --- |\n| 1 | 2';
  assert.equal(M.draftSafe(t), t);
  // A finished answer is left exactly as it is.
  for (const s of ['**Bold** and done.', 'Seven are overdue.', '', 'A **b** c **d**']) assert.equal(M.draftSafe(s), s);
  assert.equal(M.draftSafe('A **b** c **d'), 'A **b** c d');
  assert.equal(M.draftSafe(null), '');
});

test('an overdue chart cut to eight bars says how many more there are and where', () => {
  const rows = Array.from({ length: 8 }, (_, i) => ({ label: 'Owner ' + i + ' · Cert ' + i, days: 30 - i }));
  const p = M.programFromCompliance({ narrative: 'Lead.', overdue: { buildings: rows, vendors: [], more: { buildings: 3, vendors: 0 } } });
  assert.match(p, /Text\("\+3 more in Certificates in scope"\)/);
  const none = M.programFromCompliance({ narrative: 'Lead.', overdue: { buildings: rows.slice(0, 2), vendors: [] } });
  assert.doesNotMatch(none, /more in Certificates/);
});

test('a compliance engine answer draws each owner group once, not as text and again as cards', () => {
  // 28 Oct 2026: the stored answer is the groups as text plus the engine's own narrative
  // (orchestrator._compliance_plain_text); reading that narrative as a lead drew every group twice.
  const rich = {
    narrative: 'One fire risk assessment needs action this week.',
    groups: [{ owner: 'Hartley FM', headline: 'Fire risk assessment lapsed 41 days ago', points: ['FRA-2291 expired 17 Sep 2026', 'No renewal booked'], severity: 'critical' }]
  };
  const answer = '**Hartley FM** — Fire risk assessment lapsed 41 days ago\n- FRA-2291 expired 17 Sep 2026\n- No renewal booked\n\nOne fire risk assessment needs action this week.';
  const p = M.programFromCompliance(rich, answer);
  for (const fact of ['Fire risk assessment lapsed 41 days ago', 'FRA-2291 expired 17 Sep 2026', 'No renewal booked']) {
    assert.equal(p.split(fact).length - 1, 1, fact + ' drawn once');
  }
  assert.match(p, /One fire risk assessment needs action this week\./);
});
