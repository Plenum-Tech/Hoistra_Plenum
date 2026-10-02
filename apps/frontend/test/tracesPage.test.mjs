// tracesPage — Administration › Hoist Traces: the span tree and waterfall from a run's spans,
// the tiles from the aggregates, and the run rows.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildTree, tracesPageVals, fmtUsd, fmtMs, fmtTok } from '../src/logic/tracesPage.js';

const T = (s) => '2026-10-02T10:00:' + s + 'Z';
const SPANS = [
  { id: 'root', parent_id: null, seq: 0, kind: 'turn', name: 'turn', started_at: T('00.000'), ended_at: T('10.000'), latency_ms: 10000, ok: true },
  { id: 'r', parent_id: 'root', seq: 1, kind: 'router', name: 'router', model: 'claude-haiku-4-5', started_at: T('00.100'), ended_at: T('00.500'), latency_ms: 400, cost_usd: 0.001, ok: true },
  { id: 'g', parent_id: 'root', seq: 2, kind: 'agent', name: 'compliance', started_at: T('00.500'), ended_at: T('09.000'), latency_ms: 8500, ok: true },
  { id: 'f', parent_id: 'g', seq: 3, kind: 'stage', name: 'fetch', started_at: T('00.500'), ended_at: T('01.000'), latency_ms: 500, ok: true },
  { id: 'a', parent_id: 'g', seq: 4, kind: 'llm', name: 'analyst', model: 'claude-sonnet-5', started_at: T('01.000'), ended_at: T('09.000'), latency_ms: 8000, cost_usd: 0.07, input_tokens: 30000, output_tokens: 600, cache_read_tokens: 25000, ok: true,
    input: { system_prompt: 'You are…' }, output: { model_output: 'Two expire.' } },
  { id: 'orphan', parent_id: 'gone', seq: 5, kind: 'tool', name: 'list_vendor_accreditations', started_at: T('09.000'), ended_at: T('09.500'), latency_ms: 500, ok: false, error: 'timeout' }
];

test('the tree is depth-first with the waterfall as shares of the turn; an orphan hangs from the root', () => {
  const tree = buildTree(SPANS);
  assert.deepEqual(tree.map((n) => [n.name, n.depth]), [['turn', 0], ['router', 1], ['compliance', 1], ['fetch', 2], ['analyst', 2], ['list_vendor_accreditations', 1]]);
  const analyst = tree.find((n) => n.name === 'analyst');
  assert.equal(analyst.start, 10);
  assert.equal(analyst.width, 80);
  assert.equal(analyst.kindLabel, 'Model');
  assert.equal(tree[0].children, 3);
  assert.equal(tree.find((n) => n.name === 'list_vendor_accreditations').ok, false);
  assert.deepEqual(buildTree([]), []);
});

test('formatters land in the bands the page shows', () => {
  assert.equal(fmtUsd(0.00086), '$0.0009');
  assert.equal(fmtUsd(0.0723), '$0.072');
  assert.equal(fmtUsd(12.5), '$12.50');
  assert.equal(fmtUsd(0), '$0');
  assert.equal(fmtMs(420), '420 ms');
  assert.equal(fmtMs(7000), '7.00 s');
  assert.equal(fmtMs(35200), '35.2 s');
  assert.equal(fmtMs(null), '—');
  assert.equal(fmtTok(25000), '25k');
  assert.equal(fmtTok(1234), '1.2k');
});

function ctl(state) {
  const calls = [];
  const c = { state: Object.assign({ signedIn: true, view: 'traces', tpLoadedAt: 1 }, state) };
  ['tpLoad', 'tpSetRange', 'tpSetStatus', 'tpSetQuery', 'tpSearch', 'tpOpenTurn', 'tpBack', 'tpSelectSpan', 'tpRate', 'tpSetComment', 'tpSaveComment', 'tpExport']
    .forEach((m) => { c[m] = (...a) => calls.push([m, ...a]); });
  return { c, calls };
}

const STATS = { totals: { turns: 40, failed: 2, cost_usd: 2.92, llm_calls: 95, tool_calls: 60, users: 3, p50_ms: 15000, p95_ms: 48000, thumbs_up: 5, thumbs_down: 1, cache_read_tokens: 1800000 },
  by_day: [{ day: '2026-10-01T00:00:00Z', turns: 25, failed: 0, cost_usd: 2.0, p95_ms: 40000 }, { day: '2026-10-02T00:00:00Z', turns: 15, failed: 2, cost_usd: 0.9, p95_ms: 48000 }],
  by_model: [{ model: 'claude-sonnet-5', calls: 40, cost_usd: 2.6, input_tokens: 1200000, output_tokens: 30000, cache_read_tokens: 1800000, p50_ms: 9000, failed: 0 }],
  by_tool: [{ name: 'answer_from_records', calls: 30, p50_ms: 800, p95_ms: 2500, failed: 1 }] };

test('the tiles, charts and run rows read from the aggregates and the list', () => {
  const NOW = Date.parse('2026-10-02T12:00:00Z');
  const { c, calls } = ctl({ tpStats: STATS, tpTurns: [
    { turn_id: 't1', started_at: '2026-10-02T09:30:00Z', email: 'fm@example.com', question: 'Which certificates expire?', answer: 'Two.', llm_calls: 2, tool_calls: 1, cost_usd: 0.0723, cost_complete: true, latency_ms: 15000, ok: true, models: ['claude-sonnet-5'], feedback_rating: 'up' },
    { turn_id: 't2', started_at: '2026-10-01T09:30:00Z', email: 'ops@example.com', question: 'Boilers?', llm_calls: 1, tool_calls: 0, cost_usd: 0.001, cost_complete: false, latency_ms: 900, ok: false, error: 'boom', models: [] }
  ], tpCanManage: true });
  const v = tracesPageVals(c, NOW);
  assert.equal(v.isTraces, true);
  assert.deepEqual(v.tpTiles.map((t) => [t.label, t.value]), [['Turns', '40'], ['LLM cost', '$2.92'], ['Latency p50', '15.0 s'], ['Failed', '5%'], ['Rated up / down', '5 / 1']]);
  assert.equal(v.tpTiles[1].hint, '$0.073 per turn · 1800k cached tokens');
  assert.equal(v.tpDays.length, 2);
  assert.equal(v.tpDays[0].costPct, 100);
  assert.equal(v.tpDays[1].turnsPct, 60);
  assert.equal(v.tpModels[0].cost, '$2.60');
  assert.equal(v.tpTools[0].p95, '2.50 s');
  assert.equal(v.tpRuns[0].when, 'today 09:30'.replace('09:30', String(new Date('2026-10-02T09:30:00Z').getHours()).padStart(2, '0') + ':30'));
  assert.equal(v.tpRuns[0].cost, '$0.072');
  assert.equal(v.tpRuns[0].rating, 'up');
  assert.equal(v.tpRuns[1].ok, false);
  assert.equal(v.tpRuns[1].costIncomplete, true);
  v.tpRuns[0].open();
  v.tpExportRated();
  assert.deepEqual(calls, [['tpOpenTurn', 't1'], ['tpExport', true]]);
  assert.equal(v.tpDetail, null);
});

test('an open run shows its tree and the selected span with input and output', () => {
  const { c, calls } = ctl({ tpTurn: { turn_id: 't1', started_at: '2026-10-02T09:30:00Z', email: 'fm@example.com', question: 'Which?', answer: 'Two.', cost_usd: 0.071, latency_ms: 10000, llm_calls: 2, tool_calls: 1, input_tokens: 30900, output_tokens: 620, cache_read_tokens: 25000, models: ['claude-haiku-4-5', 'claude-sonnet-5'], ok: true, feedback_rating: 'down', spans: SPANS }, tpSpanId: 'a' });
  const v = tracesPageVals(c, Date.parse('2026-10-02T12:00:00Z'));
  const d = v.tpDetail;
  assert.equal(d.tree.length, 6);
  assert.equal(d.tree.find((n) => n.id === 'a').on, true);
  assert.equal(d.span.name, 'analyst');
  assert.equal(d.span.model, 'claude-sonnet-5');
  assert.equal(d.span.tokens, '30k in · 600 out · 25k cached');
  assert.equal(d.span.cost, '$0.070');
  assert.ok(d.span.input.indexOf('"system_prompt"') > -1);
  assert.equal(d.rating, 'down');
  assert.equal(d.tokens, '31k in · 620 out · 25k cached');
  d.rateUp();
  d.tree[1].pick();
  assert.deepEqual(calls, [['tpRate', 't1', 'up'], ['tpSelectSpan', 'r']]);
});


test('a query span shows the statement, its parameters and the rows the database answered', () => {
  const spans = SPANS.concat([{ id: 'q', parent_id: 'orphan', seq: 7, kind: 'db', name: 'records: work_orders', started_at: T('09.000'), ended_at: T('09.100'), latency_ms: 84, ok: true,
    input: { sql: 'SELECT f.wo_code FROM plenum_scoped.work_orders f WHERE f.building_id = :b', params: { b: 'bld-301' } },
    output: { row_count: 60, rows: [{ wo_code: 'WO-1', status: 'Open' }, { wo_code: 'WO-2', status: 'Held' }], truncated: 58 } }]);
  const { c } = ctl({ tpTurn: { turn_id: 't1', started_at: '2026-10-02T09:30:00Z', question: 'q', answer: 'a', spans: spans, models: [] }, tpSpanId: 'q' });
  const d = tracesPageVals(c, Date.parse('2026-10-02T12:00:00Z')).tpDetail;
  const q = d.tree.find((n) => n.id === 'q');
  assert.equal(q.kindLabel, 'Query');
  assert.equal(q.depth, 2);
  assert.equal(d.span.sql, 'SELECT f.wo_code FROM plenum_scoped.work_orders f WHERE f.building_id = :b');
  assert.ok(d.span.sqlParams.indexOf('"b": "bld-301"') > -1);
  assert.equal(d.span.rows.length, 2);
  assert.equal(d.span.rowCount, 60);
  assert.equal(d.span.rowsTruncated, 58);
  assert.equal(d.span.cost, '—');
  const plain = tracesPageVals(ctl({ tpTurn: { turn_id: 't1', spans: spans, models: [] }, tpSpanId: 'a' }).c).tpDetail.span;
  assert.equal(plain.sql, '');
  assert.equal(plain.rows, null);
});
