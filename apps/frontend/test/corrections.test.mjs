// corrections — teaching the agent from the trace: the before -> after preview, the stored
// run joined to the rail, and the sentence a correction becomes.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { previewExclude, runChildren, planSpan, correctionText, statusColumn, rowsFromRun, KIND_MODE } from '../src/logic/corrections.js';

const ROWS = [{ WorkOrder_status: 'Completed', count: 781 }, { WorkOrder_status: 'Draft', count: 5 }, { WorkOrder_status: 'In progress', count: 4 },
  { WorkOrder_status: 'Held', count: 2 }, { WorkOrder_status: 'Scheduled', count: 2 }, { WorkOrder_status: 'Cancelled', count: 3 }];

test('excluding a status previews the totals from the rows the query returned', () => {
  assert.equal(statusColumn(ROWS), 'WorkOrder_status');
  const pv = previewExclude(ROWS, ['Cancelled']);
  assert.equal(pv.before, 797);
  assert.equal(pv.after, 794);
  assert.deepEqual(pv.statuses, ['Completed', 'Draft', 'In progress', 'Held', 'Scheduled', 'Cancelled']);
  assert.equal(pv.lines.find((l) => l.status === 'Cancelled').after, 0);
  // rows that are records, not counts, count one each
  const recs = previewExclude([{ status: 'Open' }, { status: 'Held' }, { status: 'Held' }], ['held']);
  assert.equal(recs.before, 3);
  assert.equal(recs.after, 1);
  assert.equal(previewExclude([{ n: 9 }], ['x']), null);
  assert.equal(previewExclude([], []), null);
});

test('the stored run hangs its queries under each tool, in order', () => {
  const spans = [
    { id: 't', seq: 0, kind: 'turn', name: 'turn' },
    { id: 'p', seq: 1, kind: 'plan', name: 'plan: 2 steps', output: JSON.stringify({ plan: { steps: [{ id: 's1', target: 'answer_from_records', ask: 'count' }] } }) },
    { id: 'a', seq: 2, kind: 'tool', name: 'answer_from_records', ok: true, latency_ms: 3900, input: { question: 'q' }, output: { total: 794 } },
    { id: 'q1', seq: 3, kind: 'db', name: 'aggregate', parent_id: 'a', input: { sql: 'SELECT status, count(*) …', params: { p0: ['b'] } }, output: { row_count: 5, rows: ROWS.slice(0, 5) }, ok: true },
    { id: 'q2', seq: 4, kind: 'db', name: 'count', parent_id: 'a', input: { sql: 'SELECT count(*)' }, output: { row_count: 1, rows: [{ n: 9 }] }, ok: false, error: 'timeout' },
    { id: 'b', seq: 5, kind: 'tool', name: 'get_cost_savings', ok: true, input: { building_name: 'B' }, output: { ok: true } }
  ];
  const tools = runChildren(spans);
  assert.deepEqual(tools.map((t) => t.name), ['answer_from_records', 'get_cost_savings']);
  assert.equal(tools[0].queries.length, 2);
  assert.equal(tools[0].queries[0].sql, 'SELECT status, count(*) …');
  assert.equal(tools[0].queries[0].rows.length, 5);
  assert.equal(tools[0].queries[1].ok, false);
  assert.equal(tools[1].queries.length, 0);
  assert.equal(planSpan(spans).plan.steps[0].target, 'answer_from_records');
  assert.equal(planSpan([]), null);
});

test('a correction reads as one sentence the orchestrator or a memory can act on', () => {
  assert.equal(correctionText({ exclude: ['Cancelled'], period: '', field: '', why: 'they are not raised work', note: '' }, { building: 'Bishopsgate Tower' }),
    'Exclude work orders with status "Cancelled" from "raised" at Bishopsgate Tower. Reason: they are not raised work.');
  assert.equal(correctionText({ exclude: [], period: 'this_month', field: 'raised_at', why: '', note: '' }, {}),
    'Use the period this month; date work orders by raised_at.');
  assert.equal(correctionText({ exclude: [], period: '', field: '', why: '', note: 'the DEC for Manchester Town Hall is missing' }, { building: '' }),
    'The DEC for Manchester Town Hall is missing.');
  assert.equal(correctionText({ exclude: [], period: '', field: '', why: 'x', note: '' }, {}), '');
});

test('a route correction names the engine, or asks for a plan', () => {
  assert.equal(correctionText({ exclude: [], period: '', field: '', route: 'compliance', why: '', note: '' }, {}),
    'Route questions like this to the compliance engine.');
  assert.equal(correctionText({ exclude: [], period: '', field: '', route: 'planner', why: 'two parts', note: '' }, { building: 'B-301' }),
    'Plan questions like this in steps rather than sending them to one engine at B-301. Reason: two parts.');
});

test('once the run is on record every agent and stage is a rail row with the right drawer', () => {
  const spans = [
    { id: 't', seq: 0, kind: 'turn', name: 'turn' },
    { id: 'r', seq: 1, kind: 'router', name: 'router', parent_id: 't', model: 'claude-sonnet-5', latency_ms: 2100, cost_usd: 0.006, output: { model_output: { agent: 'compliance', reason: 'expiry' } } },
    { id: 'p', seq: 2, kind: 'plan', name: 'plan: 1 step', parent_id: 't', output: { plan: { mode: 'single', steps: [{ target: 'compliance' }] } } },
    { id: 'g', seq: 3, kind: 'agent', name: 'compliance', parent_id: 't', latency_ms: 90000, cost_usd: 0.5 },
    { id: 'a', seq: 4, kind: 'llm', name: 'analyst', parent_id: 'g', model: 'claude-sonnet-5', latency_ms: 80000, output: { content: 'Eight lapsed…' } },
    { id: 'v', seq: 5, kind: 'llm', name: 'review', parent_id: 'g', latency_ms: 32000, ok: false, error: 'revise: undercount' },
    { id: 'x', seq: 6, kind: 'tool', name: 'list_building_certificates', parent_id: 'g', latency_ms: 49 },
    { id: 'q', seq: 7, kind: 'db', name: 'count', parent_id: 'x', output: { row_count: 9 } }
  ];
  const opened = [];
  const rows = rowsFromRun(spans, (id, mode) => opened.push([id, mode]));
  assert.deepEqual(rows.map((r) => [r.title, r.depth]), [
    ['router · claude-sonnet-5', 0], ['plan: 1 step', 0], ['compliance', 0], ['analyst · claude-sonnet-5', 1], ['review', 1], ['list_building_certificates', 1], ['count', 2]]);
  assert.equal(rows[0].detail, '→ compliance — expiry');
  assert.equal(rows[1].detail, 'One step: compliance');
  assert.equal(rows[4].ok, false);
  assert.equal(rows[4].detail, 'Failed: revise: undercount');
  assert.equal(rows[6].detail, '9 rows');
  assert.equal(rows[0].meta, '2.1 s · $0.0060');
  rows.forEach((r) => r.correct());
  assert.deepEqual(opened.map((o) => o[1]), ['route', 'plan', 'agent', 'model', 'model', 'tool', 'query']);
  assert.equal(KIND_MODE.step, 'plan');
  assert.deepEqual(rowsFromRun([], () => {}), []);
});

test('a correction from an agent row is addressed to that agent', () => {
  assert.equal(correctionText({ exclude: [], period: '', field: '', route: '', why: '', note: 'include building certificates in lapsed counts' }, { agent: 'compliance engine', building: '' }),
    'For the compliance engine: include building certificates in lapsed counts.');
});


test('a thumbs-down reason teaches only when it says why - the same rule the server applies', async () => {
  const { whyTeaches, WHY_MIN } = await import('../src/logic/corrections.js');
  assert.equal(WHY_MIN, 12);
  assert.equal(whyTeaches('wrong'), false);
  assert.equal(whyTeaches('Not right.'), false);
  assert.equal(whyTeaches('   bad   '), false);
  assert.equal(whyTeaches('It counted cancelled jobs as raised'), true);
  assert.equal(whyTeaches(''), false);
});
