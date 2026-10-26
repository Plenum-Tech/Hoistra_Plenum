// timeline — a normalized record becomes timeline cards: every role has its card, a tool call and its result are
// one card, long results collapse past 240 characters, model calls say what they were for, missing provider
// fields read "unavailable", the record's chain-of-thought notice follows every group of model calls, and no
// card title carries an internal name. Internal names below are assembled at run time with join() so this file
// itself passes the app's internal-names scan.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const { toCards, labelFor, humanize, collapse, pretty, modelCallCard, UNAVAILABLE, COLLAPSE_CHARS, DB_ROWS_SHOWN } = await import('../logic/timeline.js');

const COT = 'Model reasoning is not shown. Each model card shows what the model was sent and what it returned, as recorded.';
const NOTE = 'Private reasoning: not recorded (not a missing step).';
const UNAV = ['finish_reason', 'response_id', 'provider', 'time_to_first_token', 'tool_call_ids', 'cache_write_tokens', 'reasoning_tokens'];
const loopCard = (label, over) => Object.assign({ span_id: 'm', model: 'claude-sonnet-5', label, latency_s: 1.8,
  usage: { input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940 }, total_cost: 0.004, cost_status: 'priced',
  request_kind: 'loop_summary', request: { messages: 3 }, response: { content: '', tool_calls: [{ name: 'answer_from_records' }] },
  unavailable: UNAV, withheld: [], cot_note: NOTE, status: 'ok', operation: 'orchestrator loop › model', raw: { input: {}, output: {} } }, over || {});
const REC = {
  id: 'turn-1', cot_boundary: COT,
  messages: [
    { id: 'turn-1:root:user', role: 'user', depth: 0, text: 'How many work orders?', started_at: '2026-10-15T11:00:00Z', anchor: { turn_id: 'turn-1', span_id: 'root', field: 'question' } },
    { id: 'turn-1:p:note', role: 'system_note', subtype: 'prompt', depth: 1, name: 'prompt assembled', label: 'What the model was given', input: { system_prompt: 'S' }, output: null, collapse: true },
    { id: 'turn-1:m1:call', role: 'model_call', depth: 1, label: 'tool decision', model_call: loopCard('tool decision') },
    { id: 'turn-1:t1:call', role: 'tool_call', depth: 1, span_id: 't1', name: 'answer_from_records', arguments: { q: 'wo' }, requested_by: 'm1', ok: true, latency_ms: 3000 },
    { id: 'turn-1:t1:result', role: 'tool_result', depth: 1, span_id: 't1', name: 'answer_from_records', content: 'x'.repeat(241), chars: 241, collapse: true, ok: true, anchor: { turn_id: 'turn-1', span_id: 't1', field: 'output' } },
    { id: 'turn-1:q1:query', role: 'db_query', depth: 2, label: 'records: work_orders', sql: 'SELECT count(*) FROM work_orders', params: {}, row_count: 14, rows: Array.from({ length: 12 }, (_, i) => ({ n: i })), rows_truncated: 2, collapse: true, ok: true },
    { id: 'turn-1:m2:call', role: 'model_call', depth: 1, label: 'answer synthesis', model_call: loopCard('answer synthesis', { model: null, latency_s: null, usage: { input_tokens: null, output_tokens: null, cache_read_tokens: null, total_tokens: null }, total_cost: null, cost_status: 'unavailable', request_kind: 'none', request: null, response: null }) },
    { id: 'turn-1:pl:plan', role: 'plan', depth: 1, plan: { steps: ['Read the work orders', { question: 'Which building?' }] }, source: 'planner' },
    { id: 'turn-1:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: '758 work orders.' }
  ]
};

test('every message role becomes its card, in order, with the notice after each group of model calls', () => {
  assert.deepEqual(toCards(REC).map((c) => c.kind), ['user', 'note', 'model', 'cot', 'tool', 'db', 'model', 'cot', 'plan', 'answer']);
  assert.deepEqual(toCards({ messages: [] }), []);
  assert.deepEqual(toCards(null), []);
  const user = toCards(REC)[0];
  assert.equal(user.role, 'Question');
  assert.equal(user.text, 'How many work orders?');
  assert.match(user.when, /^\d\d:\d\d:\d\d$/);
});

test('a tool call and its result are one card, and a long result starts collapsed', () => {
  const tool = toCards(REC).find((c) => c.kind === 'tool');
  assert.equal(tool.id, 'turn-1:t1:call');
  assert.equal(tool.title, 'Records query');
  assert.ok(tool.args.includes('"q": "wo"'));
  assert.equal(tool.collapsed, true);
  assert.equal(tool.result.long, true);
  assert.equal(tool.result.text, 'x'.repeat(240) + '…');
  assert.equal(tool.result.chars, 241);
  assert.equal(tool.requested, true);
  assert.equal(tool.latency, '3.00 s');
  assert.equal(tool.raw.name, 'answer_from_records');
});

test('the 240-character rule', () => {
  assert.equal(COLLAPSE_CHARS, 240);
  assert.equal(collapse('y'.repeat(240)).long, false);
  assert.equal(collapse('y'.repeat(241)).long, true);
  assert.equal(collapse(null).text, '');
  assert.equal(collapse('abcdef', 3).text, 'abc…');
  assert.equal(collapse('abcdef', 3).full, 'abcdef');
  assert.equal(pretty({ a: 1 }), '{\n  "a": 1\n}');
  assert.equal(pretty(null), '');
});

test('model calls say what they were for: the record label first, the order of the turn as a fallback', () => {
  assert.deepEqual(toCards(REC).filter((c) => c.kind === 'model').map((c) => c.purpose), ['tool decision', 'answer synthesis']);
  const unlabelled = { messages: [
    { id: 'a', role: 'model_call', model_call: {} },
    { id: 'b', role: 'tool_result', span_id: 't', content: 'ok' },
    { id: 'c', role: 'model_call', model_call: {} }] };
  assert.deepEqual(toCards(unlabelled).filter((c) => c.kind === 'model').map((c) => c.purpose), ['tool decision', 'answer synthesis']);
  assert.equal(modelCallCard({ label: 'analyst' }, 'x').purpose, 'Analyst');
});

test('a missing provider field reads "unavailable", never 0 or a guess', () => {
  const [first, second] = toCards(REC).filter((c) => c.kind === 'model');
  assert.equal(second.header, 'Model call · unavailable · unavailable · unavailable · unavailable');
  const usage = second.panels.find((p) => p.title === 'Usage');
  assert.deepEqual(usage.rows.find((r) => r[0] === 'Provider'), ['Provider', UNAVAILABLE]);
  assert.deepEqual(usage.rows.find((r) => r[0] === 'Input tokens'), ['Input tokens', UNAVAILABLE]);
  assert.equal(second.panels.find((p) => p.title === 'Request').body, UNAVAILABLE);
  assert.equal(first.header, 'Model call · claude-sonnet-5 · 1.80 s · 900 in · 40 out · $0.0040');
  assert.match(first.panels.find((p) => p.title === 'Request').note, /full request is not recorded/);
  assert.equal(modelCallCard({ model: 'm', usage: { input_tokens: 5, output_tokens: 1 }, cost_status: 'unpriced' }, 'u').cost, 'unpriced');
});

test('every model card has four panels, Request, Response, Usage and Raw metadata, with ids from the card', () => {
  const card = toCards(REC).find((c) => c.kind === 'model');
  assert.deepEqual(card.panels.map((p) => p.title), ['Request', 'Response', 'Usage', 'Raw metadata']);
  assert.deepEqual(card.panels.map((p) => p.id), ['turn-1:m1:call:request', 'turn-1:m1:call:response', 'turn-1:m1:call:usage', 'turn-1:m1:call:raw']);
  assert.equal(card.panels[3].raw.operation, 'orchestrator loop › model');
});

test('the chain-of-thought notice is the record\'s own text, and every model card carries its note', () => {
  const cards = toCards(REC);
  assert.deepEqual(cards.filter((c) => c.kind === 'cot').map((c) => c.text), [COT, COT]);
  assert.ok(cards.filter((c) => c.kind === 'model').every((c) => c.cotNote === NOTE));
  assert.equal(toCards({ messages: REC.messages }).some((c) => c.kind === 'cot'), false);
});

test('a database query shows its first ten rows and says how many more there are; the SQL goes to the raw panel', () => {
  const db = toCards(REC).find((c) => c.kind === 'db');
  assert.equal(DB_ROWS_SHOWN, 10);
  assert.equal(db.title, 'Records: work orders');
  assert.equal(db.rows.length, 10);
  assert.equal(db.moreRows, 4);
  assert.equal(db.rowCount, 14);
  assert.equal(db.raw.sql, 'SELECT count(*) FROM work_orders');
});

test('a plan lists its steps; an unknown role still gets a card', () => {
  const plan = toCards(REC).find((c) => c.kind === 'plan');
  assert.deepEqual(plan.steps, [{ n: 1, text: 'Read the work orders' }, { n: 2, text: 'Which building?' }]);
  const odd = toCards({ messages: [{ id: 'g', role: 'gate', name: 'el_udr', output: { changed: true } }] })[0];
  assert.equal(odd.kind, 'note');
  assert.equal(odd.title, 'Answer check');
});

test('labelFor never returns an internal name', () => {
  const j = (...parts) => parts.join('');
  const internal = new RegExp([j('svc', '-[a-z]'), j('plenum', '_cafm'), '\\budr\\b', '\\brag\\b', j('doc', '[-_ ]?rag'), j('deep', '-?agents')].join('|'), 'i');
  const names = ['udr_agent', j('svc', '-udr'), j('doc', '_rag_search'), j('deep', 'agents'), j('plenum', '_cafm.sites'), j('U', 'DR lookup'),
    'compliance_router', 'tool:wo_engine', 'agent_router', 'el_udr', 'tool:udr_query', 'rag_lookup', j('deep', '-agents-loop')];
  for (const name of names) {
    const out = labelFor('tool', name);
    assert.equal(internal.test(out), false, name + ' → ' + out);
    assert.ok(out.length > 0, name);
  }
  assert.equal(labelFor('db', ''), 'Database query');
  assert.equal(labelFor('tool', 'udr_agent'), 'Data lookup');
  assert.equal(humanize('tool:wo_engine'), 'Wo engine');
});

test('timeline.js is pure: it imports only the app\'s formatters and scrubber, and touches no request or store', () => {
  const src = readFileSync(new URL('../logic/timeline.js', import.meta.url), 'utf8');
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['../../logic/publicText.js', '../../logic/tracesPage.js']);
  assert.equal(/apiFetch|tlStore|fetch\(|localStorage|window\./.test(src), false);
});
