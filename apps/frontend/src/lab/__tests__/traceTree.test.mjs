// traceTree — the Trace tab's pure helpers (9 Nov 2026): which tree rows show (collapsed, searched), where each step's
// bar sits on the timeline, the graph of agents, model calls and tools, and Langfuse's number formats.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const T = await import('../logic/traceTree.js');

const at = (ms) => new Date(Date.UTC(2026, 10, 9, 12, 0, 0) + ms).toISOString();
const n = (id, parent, depth, type, name, start, end, extra) => Object.assign({ id, parent_id: parent, depth, type, name,
  started_at: start == null ? null : at(start), ended_at: end == null ? null : at(end),
  latency_ms: start == null || end == null ? null : end - start }, extra || {});

const NODES = [
  n('r', null, 0, 'agent', 'chat', 0, 18790),
  n('a', 'r', 1, 'generation', 'router', 10, 2720),
  n('b', 'r', 1, 'agent', 'maintenance', 2750, 17610),
  n('c', 'b', 2, 'generation', 'model', 2760, 5150),
  n('d', 'b', 2, 'tool', 'answer_from_records', 5160, 10920),
  n('e', 'd', 3, 'span', 'count', 5170, 5180),
  n('f', 'd', 3, 'span', 'focus WorkOrder', 5190, 5210),
  n('g', 'b', 2, 'span', 'plan', 2755, 17600),
  n('h', 'b', 2, 'tool', 'list_vendor_accreditations', 11000, 41000),
];
const TURN = { started_at: at(0), ended_at: at(18790), latency_ms: 18790 };

test('rows: depth-first, collapse hides children, search keeps ancestors', () => {
  const all = T.visibleRows(NODES, new Set(), '');
  assert.deepEqual(all.map((r) => r.node.id), ['r', 'a', 'b', 'c', 'd', 'e', 'f', 'g', 'h']);
  assert.deepEqual(all.filter((r) => r.hasKids).map((r) => r.node.id), ['r', 'b', 'd']);
  assert.equal(all.find((r) => r.node.id === 'd').open, true);
  assert.equal(all.find((r) => r.node.id === 'e').depth, 3);

  const shut = T.visibleRows(NODES, new Set(['d']), '');
  assert.deepEqual(shut.map((r) => r.node.id), ['r', 'a', 'b', 'c', 'd', 'g', 'h']);
  assert.equal(shut.find((r) => r.node.id === 'd').open, false);
  assert.deepEqual(T.visibleRows(NODES, new Set(['r']), '').map((r) => r.node.id), ['r']);

  assert.deepEqual(T.visibleRows(NODES, new Set(), 'FOCUS').map((r) => r.node.id), ['r', 'b', 'd', 'f']);
  assert.deepEqual(T.visibleRows(NODES, new Set(['d']), 'focus').map((r) => r.node.id), ['r', 'b', 'd', 'f'],
    'a search shows what matches even under a collapsed step');
  assert.deepEqual(T.visibleRows(NODES, new Set(), 'nothing like it'), []);
  assert.deepEqual(T.visibleRows([], new Set(), ''), []);
});

test('timeline bars stay inside the axis', () => {
  const bars = T.timelineBars(NODES, TURN);
  assert.equal(bars.length, NODES.length);
  const by = Object.fromEntries(bars.map((b) => [b.id, b]));
  assert.equal(by.r.leftPct, 0);
  assert.ok(Math.abs(by.r.widthPct - (18790 / 41000) * 100) < 0.01, 'the axis runs to the latest end');
  assert.ok(by.b.leftPct > by.a.leftPct && by.c.depth === 2);
  const odd = [n('x', 'r', 1, 'span', 'open stage', null, null), n('y', 'r', 1, 'span', 'backwards', 900, 100),
    n('z', 'r', 1, 'span', 'late', 50000, 60000)];
  for (const b of T.timelineBars(NODES.concat(odd), TURN).concat(T.timelineBars([n('r', null, 0, 'agent', 'chat', 0, 0)],
    { started_at: at(0), ended_at: at(0), latency_ms: 0 }))) {
    for (const v of [b.leftPct, b.widthPct]) assert.ok(Number.isFinite(v), b.id + ' ' + v);
    assert.ok(b.leftPct >= 0 && b.widthPct >= 0.5 && b.leftPct + b.widthPct <= 100.0001, JSON.stringify(b));
  }
});

test('flow: one node per step name in the order they ran, repeats grouped, edges in sequence', () => {
  const twice = NODES.concat([n('c2', 'b', 2, 'generation', 'model', 11000, 12000), n('b2', 'b', 2, 'agent', 'maintenance', 12100, 12200)]);
  const f = T.flowOf(twice);
  assert.deepEqual(f.steps.map((x) => x.name), ['chat', 'router', 'maintenance', 'plan', 'model', 'answer_from_records', 'count',
    'focus WorkOrder', 'list_vendor_accreditations']);
  const model = f.steps.find((x) => x.name === 'model');
  assert.deepEqual(model.ids, ['c', 'c2']);
  assert.equal(model.type, 'generation');
  assert.deepEqual(f.steps.find((x) => x.name === 'maintenance').ids, ['b', 'b2']);
  assert.deepEqual(f.edges.map((e) => e.from + '>' + e.to), ['chat>router', 'router>maintenance', 'maintenance>plan', 'plan>model',
    'model>answer_from_records', 'answer_from_records>count', 'count>focus WorkOrder', 'focus WorkOrder>list_vendor_accreditations']);
  assert.deepEqual(T.flowOf([]), { steps: [], edges: [] });
});

test('flow: a step name shared by two kinds of step is two nodes, each with its own icon (10 Nov 2026)', () => {
  // A 'plan' stage and a 'plan' model call are different steps; grouped by name alone the graph showed one node with the
  // first one's icon, and clicking it cycled through runs of both.
  const f = T.flowOf([n('r', null, 0, 'agent', 'chat', 0, 100), n('p1', 'r', 1, 'span', 'plan', 1, 2),
    n('p2', 'r', 1, 'generation', 'plan', 3, 4), n('p3', 'r', 1, 'span', 'plan', 5, 6)]);
  assert.deepEqual(f.steps.map((x) => x.name + ':' + x.type), ['chat:agent', 'plan:span', 'plan:generation']);
  assert.deepEqual(f.steps.map((x) => x.ids), [['r'], ['p1', 'p3'], ['p2']]);
  assert.equal(new Set(f.steps.map((x) => x.key)).size, 3, 'each node has its own key');
  assert.deepEqual(f.edges.map((e) => e.fromKey + '>' + e.toKey), [f.steps[0].key + '>' + f.steps[1].key, f.steps[1].key + '>' + f.steps[2].key]);
});

test('timeline: Langfuse durations, a ruler, and costs that roll up', () => {
  assert.equal(T.fmtDur(0), '0ms'); assert.equal(T.fmtDur(101), '101ms'); assert.equal(T.fmtDur(2050), '2.05s');
  assert.equal(T.fmtDur(50980), '50.98s'); assert.equal(T.fmtDur(62000), '1m 02s'); assert.equal(T.fmtDur(null), '');
  const ticks = T.timeTicks(62000);
  assert.deepEqual(ticks.slice(0, 4).map((t) => t.label), ['0ms', '15.00s', '30.00s', '45.00s']);
  assert.ok(ticks.every((t) => t.pct >= 0 && t.pct <= 100) && ticks.length >= 3 && ticks.length <= 8);
  assert.deepEqual(T.timeTicks(250).map((t) => t.label).slice(0, 3), ['0ms', '50ms', '100ms']);
  assert.deepEqual(T.timeTicks(0).map((t) => t.label), ['0ms']);
  const costs = T.subtreeCost([
    { id: 'r', parent_id: null, type: 'agent', cost_usd: 0.12 }, { id: 'a', parent_id: 'r', type: 'generation', cost_usd: 0.005 },
    { id: 'b', parent_id: 'r', type: 'agent', cost_usd: null }, { id: 'c', parent_id: 'b', type: 'generation', cost_usd: 0.1 },
    { id: 'd', parent_id: 'b', type: 'span', cost_usd: 9 }]);
  assert.ok(Math.abs(costs.get('r') - 0.105) < 1e-9, 'the root sums its model calls, not its own turn total');
  assert.ok(Math.abs(costs.get('b') - 0.1) < 1e-9);
  assert.equal(costs.get('d'), 0, 'only model calls carry cost');
});

test('formats match Langfuse', () => {
  assert.equal(T.fmtTime(10), '0.01s'); assert.equal(T.fmtTime(2710), '2.71s'); assert.equal(T.fmtTime(18790), '18.79s');
  assert.equal(T.fmtTime(62000), '1m 02s'); assert.equal(T.fmtTime(null), '');
  assert.equal(T.fmtCost(0.00769), '$0.00769'); assert.equal(T.fmtCost(0.034392), '$0.034392'); assert.equal(T.fmtCost(null), '');
  assert.equal(T.fmtTotalCost(0.11312), '$0.113'); assert.equal(T.fmtTotalCost(null), '');
  assert.equal(T.fmtTokens(50210), '50k tokens'); assert.equal(T.fmtTokens(2210), '2.2k tokens');
  assert.equal(T.fmtTokens(950), '950 tokens'); assert.equal(T.fmtTokens(null), '');
  assert.match(T.fmtStart('2026-11-09T12:08:22.650Z'), /^2026-11-09 \d{2}:\d{2}:22\.650$/);
  assert.equal(T.fmtStart(null), '');
});
