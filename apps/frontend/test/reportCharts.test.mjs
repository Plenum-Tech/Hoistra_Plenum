// reportCharts — the numbers behind the report page's pictures.
//
// Fixtures are lifted verbatim from one real refresh stored in plenum_cafm.report_card_runs
// (14 Sep 2026), so the shapes here are the shapes the engine actually emits.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const {
  yearsFrom, yearsLabel, cleanLabel, durationSeries, statusMix, holderMix, kpiDial, chartsFor, toneOf
} = await import('../src/logic/reportCharts.js');

const RICH = {
  certificates: [
    { name: 'Fire Risk Assessment', company: 'AN Other (Holdings) Limited', status: 'Lapsed' },
    { name: 'EICR (00568607)', company: 'RDM Electrical Services Ltd', status: 'Lapsed' },
    { name: 'EICR (unlinked)', company: 'Not recorded', status: 'Lapsed' },
    { name: 'EICR (unlinked)', company: 'Not recorded', status: 'Lapsed' },
    { name: "Employers' Liability", company: 'Architectural Association (Inc)', status: 'Lapsed' },
    { name: 'Display Energy Certificate', company: 'Manchester City Council', status: 'Lapsed' }
  ],
  overdue: {
    buildings: [
      { label: 'Building — AN Other (Holdings) Limited', days: 7288, pct: '100%', severity: 'critical' },
      { label: 'Building — Not recorded', days: 5107, pct: '70%', severity: 'critical' },
      { label: 'Building — Manchester City Council', days: 4732, pct: '65%', severity: 'critical' },
      { label: 'Building — RDM Electrical Services Ltd', days: 4025, pct: '55%', severity: 'warning' },
      { label: 'Building — Architectural Association (Inc)', days: 1586, pct: '22%', severity: 'warning' }
    ],
    vendors: []
  },
  kpis: [{ label: 'Expiring this month', count: 0 }, { label: 'Already lapsed', count: 6 }]
};

test('duration is read in years, because that is the finding', () => {
  assert.equal(Math.round(yearsFrom(7288)), 20);
  assert.equal(yearsLabel(7288), '20 yrs');
  assert.equal(yearsLabel(4025), '11 yrs');
  assert.equal(yearsLabel(400), '1 yr');
  assert.equal(yearsLabel(60), '2 mo');
  assert.equal(yearsLabel(0), 'today', 'expired today, which is not the same as no data');
  assert.equal(yearsLabel(null), '', 'no data is blank');
  assert.equal(yearsFrom(null), null);
  assert.equal(yearsFrom('nonsense'), null);
});

test('the engine prefix on every row is dropped — it is noise when every row is a building', () => {
  assert.equal(cleanLabel('Building — AN Other (Holdings) Limited'), 'AN Other (Holdings) Limited');
  assert.equal(cleanLabel('Vendor — Apex Lifts'), 'Apex Lifts');
  assert.equal(cleanLabel('Bishopsgate Tower'), 'Bishopsgate Tower');
  assert.equal(cleanLabel(''), '');
});

test('the duration series is ranked longest-first and scaled against the worst', () => {
  const d = durationSeries(RICH);
  assert.equal(d.length, 5);
  assert.equal(d[0].days, 7288);
  assert.equal(d[0].label, 'AN Other (Holdings) Limited');
  assert.equal(d[0].pct, 100, 'the worst row is the full width of the axis');
  assert.equal(d[0].yearsLabel, '20 yrs');
  assert.equal(d[4].days, 1586);
  assert.ok(d[4].pct > 2 && d[4].pct < 30);
  assert.deepEqual(d.map((r) => r.days), [7288, 5107, 4732, 4025, 1586], 'sorted, not as given');
  assert.equal(d[0].color, toneOf('critical'));
  assert.equal(d[3].color, toneOf('warning'));
});

test('a row with no usable number is dropped rather than drawn at zero', () => {
  const d = durationSeries({ overdue: { buildings: [
    { label: 'A', days: 100, severity: 'critical' },
    { label: 'B', days: null }, { label: 'C', days: 0 }, { label: 'D' }
  ] } });
  assert.equal(d.length, 1);
  assert.equal(d[0].label, 'A');
  assert.deepEqual(durationSeries({}), []);
  assert.deepEqual(durationSeries(null), []);
  assert.deepEqual(durationSeries({ overdue: { buildings: [] } }), []);
});

test('a single overdue row still draws — the one-point case is not an empty chart', () => {
  const d = durationSeries({ overdue: { buildings: [{ label: 'Only one', days: 900, severity: 'warning' }] } });
  assert.equal(d.length, 1);
  assert.equal(d[0].pct, 100);
  assert.equal(d[0].yearsLabel, '2 yrs');
});

test('status mix counts the register and puts the exposure first', () => {
  const m = statusMix(RICH.certificates);
  assert.equal(m.length, 1);
  assert.equal(m[0].status, 'Lapsed');
  assert.equal(m[0].n, 6);
  assert.equal(m[0].pct, 100);
  assert.equal(m[0].color, toneOf('critical'));
  const mixed = statusMix([{ status: 'Valid' }, { status: 'Lapsed' }, { status: 'Valid' }, { status: 'Expiring' }]);
  assert.deepEqual(mixed.map((r) => r.status), ['Lapsed', 'Expiring', 'Valid'], 'worst first regardless of input order');
  assert.equal(mixed[2].n, 2);
  assert.deepEqual(statusMix([]), []);
  assert.deepEqual(statusMix(null), []);
});

test('holder mix surfaces concentration and rows that belong to nobody', () => {
  const h = holderMix(RICH.certificates);
  assert.equal(h[0].company, 'Unattributed', 'two rows share it, so it ranks first');
  assert.equal(h[0].n, 2);
  assert.equal(h[0].unattributed, true);
  assert.equal(h[0].concentrated, true);
  assert.equal(h[0].pct, 100, 'scaled against the largest holder');
  const named = h.filter((r) => !r.unattributed);
  assert.equal(named.length, 4);
  assert.ok(named.every((r) => r.n === 1 && r.concentrated === false));
  assert.ok(Math.abs(h[0].share - (2 / 6) * 100) < 0.01);
});

test('every way of saying "nobody" folds into one bar, not four', () => {
  const h = holderMix([{ company: 'Not recorded' }, { company: '' }, { company: 'Unknown' }, { company: '—' }, { company: 'Real Ltd' }]);
  assert.equal(h[0].company, 'Unattributed');
  assert.equal(h[0].n, 4);
  assert.equal(h.length, 2);
});

test('a KPI ring is only drawn when the denominator means something', () => {
  const lapsed = kpiDial({ label: 'Already lapsed', count: 6 }, RICH);
  assert.equal(lapsed.count, 6);
  assert.equal(lapsed.denom, 6);
  assert.equal(lapsed.pct, 100);
  assert.equal(lapsed.color, toneOf('critical'));
  const zero = kpiDial({ label: 'Expiring this month', count: 0 }, RICH);
  assert.equal(zero.pct, 0);
  assert.equal(zero.color, toneOf('ok'), 'nothing expiring is good news and reads as good news');
  // No certificates to measure against, or a count larger than the register: no ring.
  assert.equal(kpiDial({ count: 3 }, { certificates: [] }).pct, null);
  assert.equal(kpiDial({ count: 99 }, RICH).pct, null);
  assert.equal(kpiDial({ count: 'many' }, RICH).pct, null, 'a non-numeric count is not charted');
});

test('chartsFor gathers the lot and names the row the headline is about', () => {
  const c = chartsFor(RICH);
  assert.equal(c.duration.length, 5);
  assert.equal(c.status.length, 1);
  assert.equal(c.holders.length, 5);
  assert.equal(c.worst.days, 7288);
  assert.equal(c.worst.yearsLabel, '20 yrs');
  const empty = chartsFor({});
  assert.deepEqual(empty.duration, []);
  assert.equal(empty.worst, null);
  assert.equal(chartsFor(null).worst, null);
});

// ── charts out of plain markdown ────────────────────────────────────────────
// Not every question routes through the compliance engine. "What needs my approval today"
// comes back as markdown: a bullet list of figures and a nine-column table. Fixtures below
// are verbatim from that run (15 Sep 2026).
const { parseFigures, parseMarkdownTables, columnDistribution, chartableColumns, distinctCount } =
  await import('../src/logic/reportCharts.js');

const APPROVALS = `### Findings
You have several work orders pending your approval today.

- **Total Pending Approvals:** 10 work orders
- **Critical Priority:** 6 work orders
- **Urgent Priority:** 2 work orders
- **Medium Priority:** 2 work orders

| Work Order ID | Asset | Priority | Requester Email |
|---------------|-------|----------|-----------------|
| WO-1 | Painting | Medium | system@plenum-tech.com |
| WO-2 | Chiller #2 | Urgent | bala.r@plenum-tech.com |
| WO-3 | Chiller #2 | Urgent | bala.r@plenum-tech.com |
| WO-4 | Chiller #2 | Critical | bala.r@plenum-tech.com |
| WO-5 | Chiller #2 | Critical | system@plenum-tech.com |
`;

test('a bullet list of figures is a bar chart, not a paragraph', () => {
  const f = parseFigures(APPROVALS);
  assert.deepEqual(f.map((x) => [x.label, x.value]), [
    ['Total Pending Approvals', 10], ['Critical Priority', 6], ['Urgent Priority', 2], ['Medium Priority', 2]
  ]);
  assert.equal(f[0].unit, 'work orders');
  assert.equal(f[0].pct, 100, 'the largest figure sets the scale');
  assert.ok(f[1].pct > 50 && f[1].pct < 70);
});

test('one figure is a sentence — it takes two to be a chart', () => {
  assert.deepEqual(parseFigures('- **Total:** 10 work orders'), []);
  assert.deepEqual(parseFigures('no figures here at all'), []);
  assert.deepEqual(parseFigures(''), []);
});

test('a table row is never mistaken for a figure', () => {
  // "| WO-1 | Painting | Medium |" must not parse as label:value.
  const f = parseFigures('| Asset | Count |\n|---|---|\n| Chiller | 9 |\n| Painting | 1 |');
  assert.deepEqual(f, []);
});

test('markdown tables are parsed into headers and rows', () => {
  const t = parseMarkdownTables(APPROVALS);
  assert.equal(t.length, 1);
  assert.deepEqual(t[0].headers, ['Work Order ID', 'Asset', 'Priority', 'Requester Email']);
  assert.equal(t[0].rows.length, 5);
  assert.deepEqual(t[0].rows[0], ['WO-1', 'Painting', 'Medium', 'system@plenum-tech.com']);
  assert.deepEqual(parseMarkdownTables('no table here'), []);
});

test('a column that repeats is a distribution; one that never repeats is not a chart', () => {
  const t = parseMarkdownTables(APPROVALS)[0];
  const cols = chartableColumns(t, 3).map((c) => c.header);
  assert.ok(cols.includes('Priority'), 'what a row IS gets charted');
  assert.ok(cols.includes('Asset'));
  assert.ok(!cols.includes('Work Order ID'), 'five distinct ids across five rows draws five bars of one');
  assert.ok(!cols.includes('Requester Email'), 'an email column is noise, not a category');
  assert.equal(cols[0], 'Priority', 'the decision column outranks the context column');
  const dist = columnDistribution(t, 2);
  assert.deepEqual(dist.map((d) => [d.value, d.n]), [['Critical', 2], ['Urgent', 2], ['Medium', 1]]);
  assert.equal(dist[0].pct, 100);
});

test('a table too short to have a shape is not charted', () => {
  const t = parseMarkdownTables('| A | B |\n|---|---|\n| x | y |\n| p | q |')[0];
  assert.deepEqual(chartableColumns(t, 3), [], 'two rows is not a distribution');
});

// ── things that are not quantities ──────────────────────────────────────────
test('a date is never charted as a quantity', () => {
  // These were drawn as bars of height 2027, 2019 and 2024.
  const f = parseFigures([
    '- **Expiry:** 2027-03-01',
    '- **Issued:** 2019-02-02',
    '- **Inspection date:** 2024-11-30',
    '- **Total:** 10 work orders',
    '- **Open:** 4 work orders'
  ].join('\n'));
  assert.deepEqual(f.map((x) => x.label), ['Total', 'Open'], 'only the real counts survive');
});

test('a decimal comma is not read as a thousands separator', () => {
  // "3,5 %" was becoming 35 — an order of magnitude out.
  const f = parseFigures('- **Rate:** 3,5 %\n- **Other rate:** 4,1 %\n- **Count:** 12 items\n- **More:** 3 items');
  assert.deepEqual(f.map((x) => x.value), [12, 3]);
  // A genuine thousands separator still parses.
  const g = parseFigures('- **Total:** 1,250 rows\n- **Open:** 300 rows');
  assert.deepEqual(g.map((x) => x.value), [1250, 300]);
});

test('the cardinality guard fires on a big table, not just a small one', () => {
  // distinct was read off the top-8 slice, capping it at 8, so on 14+ rows the "too many
  // distinct values to be a category" test could never fire and an id column was charted.
  const headers = ['Ref', 'Priority', 'Summary'];
  const rows = Array.from({ length: 20 }, (_, i) => ['WO-' + i, i % 3 === 0 ? 'Critical' : 'Low', 'unique text ' + i]);
  const cols = chartableColumns({ headers, rows }, 3).map((c) => c.header);
  assert.deepEqual(cols, ['Priority'], 'only the genuine category is charted: ' + cols.join(','));
  assert.equal(distinctCount({ headers, rows }, 0), 20, 'distinct is the true count, uncapped');
});

test('an explicit id column is never charted even when it names an entity', () => {
  const headers = ['Asset ID', 'Vendor ID', 'Status'];
  const rows = Array.from({ length: 12 }, (_, i) => ['A' + (i % 4), 'V' + (i % 3), i % 2 ? 'Open' : 'Closed']);
  const cols = chartableColumns({ headers, rows }, 3).map((c) => c.header);
  assert.deepEqual(cols, ['Status']);
});
