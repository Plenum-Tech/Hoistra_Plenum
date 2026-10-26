// watchChart — the Watch tab's pure helpers (logic/watchChart.js): chart geometry drawn only from the server's points
// (the scale tops out at y_max, the threshold line is labelled with its value, a point carries a whisker for its interval
// and a hollow raw rate, a missing number is never drawn as zero), the automatic-vs-people shares (they add up to one and
// an empty week reads as a dash), the Due marker, and the failure modes of the latest batches. No requests, no store.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { CHART, chartGeometry, dueLabel, latestModes, pctText, stackShares, usdText } = await import('../logic/watchChart.js');

const CHART_DATA = { mode: { id: 'm1', name: 'wrong_period' }, threshold: 0.15, y_max: 0.35, points: [
  { period_label: '2026-W40', raw: 0.12, corrected: 0.1, ci_low: 0.02, ci_high: 0.2, threshold: 0.15, crossed: false },
  { period_label: '2026-W41', raw: 0.19, corrected: 0.17, ci_low: 0.06, ci_high: 0.28, threshold: 0.15, crossed: true }] };
const round1 = (v) => Math.round(v * 10) / 10;

test('a rate reads as a percentage with one decimal at most, and a missing one as a dash', () => {
  assert.equal(pctText(0.1), '10%');
  assert.equal(pctText(0.1234), '12.3%');
  assert.equal(pctText(0), '0%');
  assert.equal(pctText(null), '—');
  assert.equal(pctText(undefined), '—');
  assert.equal(pctText('not a number'), '—');
  assert.equal(pctText(''), '—', 'an empty string is no number, not zero');
  assert.equal(usdText(0.5), '$0.50');
  assert.equal(usdText(null), '—');
  assert.equal(usdText(''), '—');
});

test('the chart scale tops out at y_max and the threshold line is labelled with its value', () => {
  const g = chartGeometry(CHART_DATA);
  const innerH = CHART.height - CHART.padT - CHART.padB;
  assert.equal(g.empty, false);
  assert.equal(g.points.length, 2);
  assert.equal(g.thresholdLabel, 'threshold 0.15');
  assert.equal(g.ticks[2].y, CHART.padT, 'the top tick is y_max');
  assert.equal(g.ticks[0].y, round1(CHART.padT + innerH), 'the bottom tick is zero');
  assert.deepEqual(g.ticks.map((t) => t.label), ['0%', '17.5%', '35%']);
  assert.equal(g.points[1].high, round1(CHART.padT + innerH - 0.8 * innerH), 'ci_high = y_max / 1.25 sits at 80%');
  assert.ok(g.thresholdY > g.points[1].corrected, 'a point above the threshold is drawn above its line');
  assert.ok(g.thresholdY < g.points[0].corrected, 'a point below the threshold is drawn below its line');
  assert.equal(g.points[1].crossed, true);
  assert.equal(g.points[0].crossed, false);
});

test('points carry a whisker for the interval and a hollow raw rate', () => {
  const p = chartGeometry(CHART_DATA).points[0];
  assert.ok(p.low > p.corrected && p.corrected > p.high, 'the whisker spans the interval around the estimate');
  assert.equal(typeof p.raw, 'number');
  assert.ok(p.raw < p.corrected, 'the flagged share (12%) sits above the corrected rate (10%), so its y is smaller');
  assert.equal(p.raw, round1(CHART.padT + (CHART.height - CHART.padT - CHART.padB) * (1 - 0.12 / 0.35)), 'and it is the raw rate that is drawn there');
  assert.equal(p.title, '2026-W40: 10% corrected (2% to 20%), 12% flagged in the sample');
  assert.equal(chartGeometry(CHART_DATA).points[1].title, '2026-W41: 17% corrected (6% to 28%), 19% flagged in the sample, above the threshold');
});

test('weeks are spread evenly across the plot, and a lone week sits in its middle', () => {
  const g = chartGeometry(CHART_DATA);
  assert.equal(g.points[0].x, CHART.padL);
  assert.equal(g.points[1].x, CHART.width - CHART.padR);
  const one = chartGeometry({ y_max: 0.2, points: [CHART_DATA.points[0]] });
  assert.equal(one.points[0].x, round1(CHART.padL + (CHART.width - CHART.padL - CHART.padR) / 2));
  const many = chartGeometry({ y_max: 0.35, points: [0, 1, 2, 3, 4].map((i) => Object.assign({}, CHART_DATA.points[0], { period_label: 'w' + i })) });
  const gaps = many.points.slice(1).map((p, i) => round1(p.x - many.points[i].x));
  assert.ok(Math.max(...gaps) - Math.min(...gaps) <= 0.2, gaps.join(' '));
});

test('a value above y_max is held at the top of the plot, and a missing number is not drawn (never as zero)', () => {
  const g = chartGeometry({ y_max: 0.2, threshold: 0.15, points: [
    { period_label: 'a', raw: 0.9, corrected: 0.5, ci_low: 0.3, ci_high: 0.8, crossed: true },
    { period_label: 'b', raw: null, corrected: undefined, ci_low: '', ci_high: null, crossed: false }] });
  assert.equal(g.points[0].corrected, CHART.padT);
  assert.equal(g.points[0].raw, CHART.padT);
  assert.deepEqual([g.points[1].raw, g.points[1].corrected, g.points[1].low, g.points[1].high], [null, null, null, null]);
  const below = chartGeometry({ y_max: 0.2, points: [{ period_label: 'c', raw: -0.05, corrected: -0.01 }] }).points[0];
  assert.equal(below.corrected, round1(CHART.height - CHART.padB), 'a value below zero is held on the axis, not drawn under it');
  assert.equal(g.points[1].title, 'b: — corrected (— to —), — flagged in the sample');
});

test('a point shows the server\'s validity warning, and no threshold means no line', () => {
  const g = chartGeometry({ y_max: 0.2, points: [Object.assign({}, CHART_DATA.points[0], { validity_warning: 'Few labelled answers.' })] });
  assert.equal(g.points[0].warning, 'Few labelled answers.');
  assert.equal(chartGeometry(CHART_DATA).points[0].warning, '');
  assert.equal(g.thresholdY, null);
  assert.equal(g.thresholdLabel, '');
});

test('with no points, no scale, or nothing at all, the chart is empty and draws nothing', () => {
  assert.equal(chartGeometry({ points: [], y_max: null }).empty, true);
  assert.equal(chartGeometry(null).empty, true);
  assert.equal(chartGeometry(undefined).empty, true);
  assert.equal(chartGeometry({ points: CHART_DATA.points, y_max: 0 }).empty, true, 'no scale, no chart');
  assert.equal(chartGeometry({ points: CHART_DATA.points, y_max: -1 }).empty, true);
  const e = chartGeometry(null);
  assert.deepEqual([e.points, e.ticks, e.thresholdY, e.width, e.height], [[], [], null, CHART.width, CHART.height]);
});

test('the chart size can be asked for', () => {
  const g = chartGeometry(CHART_DATA, { width: 300, height: 120 });
  assert.equal(g.width, 300);
  assert.equal(g.height, 120);
  assert.equal(g.points[1].x, 300 - CHART.padR);
});

test('the Due marker names the week that still needs a batch', () => {
  assert.deepEqual(dueLabel({ period_label: '2026-W41', is_due: true }), { text: 'Due: 2026-W41 has not been checked yet', isDue: true });
  assert.deepEqual(dueLabel({ period_label: '2026-W41', is_due: false }), { text: 'Up to date: 2026-W41 checked', isDue: false });
  assert.equal(dueLabel(null), null);
  assert.equal(dueLabel({ is_due: true }), null, 'no week named, nothing to say');
});

test('automatic and people shares add up to one, and an empty week reads as a dash', () => {
  const weeks = stackShares([
    { week: '2026-W41', checked_any: 5, auto_share: 0.4, people_share: 0.6, auto_by_source: { golden_auto: 1 }, people_by_source: {},
      golden_accuracy: { decided: 2, correct: 1, rate: 0.5, low: 0.1, high: 0.9 }, live_accuracy: { reviewed: 0 } },
    { week: '2026-W42', checked_any: 0, auto_share: null, people_share: null }]);
  assert.ok(Math.abs(weeks[0].autoShare + weeks[0].peopleShare - 1) < 1e-9);
  assert.equal(weeks[0].autoPct + weeks[0].peoplePct, 100);
  assert.equal(weeks[0].autoText, '40%');
  assert.equal(weeks[0].peopleText, '60%');
  assert.equal(weeks[0].golden, '50% of 2 (10% to 90%)');
  assert.equal(weeks[0].live, '—');
  assert.deepEqual(weeks[0].sources, { golden_auto: 1 });
  assert.equal(weeks[1].empty, true);
  assert.equal(weeks[1].autoText, '—');
  assert.equal(weeks[1].peopleText, '—');
  assert.equal(weeks[1].golden, '—');
});

test('the two shares stay one whole for any split, a week with answers but no share is empty, and nothing is invented', () => {
  for (const share of [0, 0.001, 0.333, 0.3333, 0.5, 0.6667, 0.999, 1]) {
    const w = stackShares([{ week: 'w', checked_any: 3, auto_share: share }])[0];
    assert.ok(Math.abs(w.autoPct + w.peoplePct - 100) < 1e-9, share + ': ' + w.autoPct + ' + ' + w.peoplePct);
    assert.ok(Math.abs(w.autoShare + w.peopleShare - 1) < 1e-9, String(share));
    assert.equal(w.empty, false);
  }
  assert.equal(stackShares([{ week: 'w', checked_any: 3, auto_share: null }])[0].empty, true, 'answers were checked but the server gave no share: a dash');
  assert.equal(stackShares([{ week: 'w', checked_any: 0, auto_share: 0.5 }])[0].empty, true, 'nothing checked: a dash, whatever the share says');
  for (const bad of [1.2, -0.1, 'x']) assert.equal(stackShares([{ week: 'w', checked_any: 3, auto_share: bad }])[0].empty, true, 'a share outside 0 to 1 is no share: ' + bad);
  const w = stackShares([{ week: 'w', checked_any: 4, auto_share: 0.5, modes_automated: 1, modes_confirmed: 3 }])[0];
  assert.deepEqual([w.n, w.modesAutomated, w.modesConfirmed], [4, 1, 3]);
  assert.deepEqual(stackShares(null), []);
  assert.deepEqual(stackShares([{ week: 'w' }])[0].sources, {});
});

test('the failure modes of the latest batches come once each, newest batch first, with the batch that has them', () => {
  const batches = [
    { id: 'b2', results: { modes: [{ mode_id: 'm1', mode_name: 'wrong_period', crossed: true }, { mode_id: 'm2', mode_name: 'invented_codes', crossed: false }] } },
    { id: 'b1', results: { modes: [{ mode_id: 'm1', mode_name: 'wrong_period', crossed: false }, { mode_id: 'm3' }] } },
    { id: 'b0' }];
  assert.deepEqual(latestModes(batches), [
    { modeId: 'm1', name: 'wrong_period', crossed: true, batchId: 'b2' },
    { modeId: 'm2', name: 'invented_codes', crossed: false, batchId: 'b2' },
    { modeId: 'm3', name: 'failure mode', crossed: false, batchId: 'b1' }]);
  assert.deepEqual(latestModes(null), []);
  assert.deepEqual(latestModes([{ id: 'x', results: { modes: [{ mode_name: 'no id' }] } }]), [], 'a mode with no id has no chart to ask for');
});
