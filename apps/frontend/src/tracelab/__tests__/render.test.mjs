// The Trace Lab screens rendered to markup (bundled with esbuild, rendered with react-dom/server, as the app's
// own test/vendorDeleteDialog.test.mjs does): a tablist of three tabs, the timeline as articles labelled by role,
// model-call panels that start closed and print "unavailable", the record's chain-of-thought notice, SQL only in
// an opened technical panel, the model-call dialog, the live pseudo-run, and the page notice for a refusal.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { initialState } = await import('../store.js');
const { toCards } = await import('../logic/timeline.js');
const { Shell } = await bundle('TraceLabApp.jsx');
const { default: Timeline } = await bundle('views/Timeline.jsx');

const NOW = Date.parse('2026-10-15T12:00:00Z');
const COT = 'Model reasoning is not shown. Each model card shows what the model was sent and what it returned, as recorded.';
const NOTE = 'Private reasoning: not recorded (not a missing step).';
const loopCard = (label, over) => Object.assign({ model: 'claude-sonnet-5', label, latency_s: 1.8,
  usage: { input_tokens: 900, output_tokens: 40, cache_read_tokens: 0, total_tokens: 940 }, total_cost: 0.004, cost_status: 'priced',
  request_kind: 'loop_summary', request: { messages: 3 }, response: { content: '', tool_calls: [{ name: 'answer_from_records' }] },
  withheld: [], cot_note: NOTE, status: 'ok', operation: 'orchestrator loop › model', raw: { input: {}, output: {} } }, over || {});
const REC = {
  id: 't2', cot_boundary: COT, timestamp: '2026-10-15T11:00:00Z', models: ['claude-sonnet-5'], usage: { cost_usd: 0.03, latency_ms: 9000, tool_calls: 1 },
  meta: { channel: 'chat', channel_source: 'inferred', turn_index: 2, turn_count: 2, attempt: 1, asker: { email: 'fm@example.com' } },
  flags: ['question_repaired'], feedback: null,
  messages: [
    { id: 't2:root:user', role: 'user', depth: 0, text: 'How many work orders?' },
    { id: 't2:p:note', role: 'system_note', subtype: 'prompt', depth: 1, name: 'prompt assembled', input: { system_prompt: 'S' }, collapse: true },
    { id: 't2:m1:call', role: 'model_call', depth: 1, label: 'tool decision', model_call: loopCard('tool decision') },
    { id: 't2:t1:call', role: 'tool_call', depth: 1, span_id: 't1', name: 'answer_from_records', arguments: { q: 'wo' }, requested_by: 'm1', ok: true, latency_ms: 3000 },
    { id: 't2:t1:result', role: 'tool_result', depth: 1, span_id: 't1', name: 'answer_from_records', content: 'x'.repeat(241), collapse: true, ok: true },
    { id: 't2:q1:query', role: 'db_query', depth: 2, label: 'records: work_orders', sql: 'SELECT count(*) FROM work_orders', params: {}, row_count: 1, rows: [{ count: 758 }], ok: true },
    { id: 't2:m2:call', role: 'model_call', depth: 1, label: 'answer synthesis', model_call: loopCard('answer synthesis', { model: null, latency_s: null, usage: {}, total_cost: null, cost_status: 'unavailable', request: null, response: null }) },
    { id: 't2:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: '758 work orders.' }
  ]
};
const CONV = { key: 's:chat-1', session_kind: 'observed', session_label: 'Boiler certificates',
  turns: [{ turn_id: 't1', turn_index: 1, question: 'Which boilers?', ok: true }, { turn_id: 't2', turn_index: 2, question: 'How many?', ok: true }], active: REC };
const ROWS = [
  { key: 's:chat-1', session_id: 'chat-1', session_kind: 'observed', turns_in_window: 2, first_question: 'Which boilers?', last_turn_at: '2026-10-15T11:00:00Z', asker: { email: 'fm@example.com' } },
  { key: 's:report-1', session_id: 'report-1', session_kind: 'synthetic', turns_in_window: 1, first_question: 'Weekly summary', last_turn_at: '2026-10-15T10:00:00Z', asker: { email: 'cron@example.com' } }];
const state = (over) => Object.assign(initialState(), over || {});
const part = (key, over) => Object.assign(initialState()[key], over);
const page = (over) => renderToStaticMarkup(React.createElement(Shell, { s: state(over), now: NOW }));
const timeline = (opened) => renderToStaticMarkup(React.createElement(Timeline, {
  cards: toCards(REC), isOpen: (id, d) => (opened && id in opened ? opened[id] : !!d), setOpen: () => {} }));
// Every step row opened, as "Open every step" leaves them, plus any panel a test opens on top.
const stepsOpen = (more) => Object.assign(Object.fromEntries(toCards(REC).map((c) => [c.id + ':step', true])), more || {});

test('the page is a tablist of Conversations, Runs, Golden set, Model calls, Map, Progress, Labels, Judges and Watch with the open one selected', () => {
  const html = page({ tab: 'runs' });
  assert.match(html, /role="tablist"/);
  const tabs = [...html.matchAll(/<button[^>]*role="tab"[^>]*aria-selected="(true|false)"[^>]*>([^<]*)<\/button>/g)].map((m) => [m[2], m[1]]);
  assert.deepEqual(tabs, [['Conversations', 'false'], ['Runs', 'true'], ['Golden set', 'false'], ['Model calls', 'false'], ['Map', 'false'], ['Progress', 'false'], ['Labels', 'false'], ['Judges', 'false'], ['Watch', 'false']]);
  assert.ok(html.includes('<h2>Trace Lab</h2>'));
});

test('without Hoistra\'s frame (the standalone lab) the page has no crumb, no heading and no tab bar; the views stay', () => {
  const framed = page({ tab: 'runs' });
  assert.ok(framed.includes('Administration') && framed.includes('<h2>Trace Lab</h2>') && framed.includes('role="tablist"'));
  const bare = renderToStaticMarkup(React.createElement(Shell, { s: state({ tab: 'runs' }), now: NOW, chrome: false }));
  assert.equal(bare.includes('Administration'), false);
  assert.equal(bare.includes('<h2>Trace Lab</h2>'), false);
  assert.equal(bare.includes('role="tablist"'), false);
  assert.ok(bare.includes('tl-page'), 'the page itself is still drawn');
});

test('the timeline is a list of articles labelled by role, in order, with the reasoning note shown once', () => {
  const html = timeline();
  const labels = [...html.matchAll(/<article aria-label="([^"]*)"/g)].map((m) => m[1].split(' · ')[0]);
  assert.deepEqual(labels, ['Question', 'What the model was given', 'Model call', 'About model calls', 'Tool', 'Database query', 'Model call', 'Answer']);
  assert.match(html, /<ol class="tl-timeline" aria-label="How this answer was produced">/);
});

test('every step between the question and the answer is one closed line naming it, and opens in place', () => {
  const html = timeline();
  assert.ok(html.includes('5 steps between the question and the answer'));
  assert.equal((html.match(/class="tl-step-row" aria-expanded="false"/g) || []).length, 5);
  assert.ok(html.includes('Open every step'));
  assert.ok(html.includes('758 work orders.'), 'the answer is read in full');
  assert.equal(html.includes('<summary>Request</summary>'), false, 'a closed step holds no body');
  assert.ok(html.includes('1 row'), 'the query row says how many rows came back');
  const open = timeline(stepsOpen());
  assert.equal((open.match(/aria-expanded="true"/g) || []).length >= 5, true);
  assert.ok(open.includes('Close every step'));
});

test('model-call panels start closed, missing fields say "unavailable", and the notice is the record\'s text', () => {
  const html = timeline(stepsOpen());
  assert.equal(/<details[^>]*\sopen=""/.test(html), false);
  for (const t of ['Request', 'Response', 'Usage', 'Raw metadata']) assert.ok(html.includes('<summary>' + t + '</summary>'), t);
  assert.ok(html.includes('unavailable · unavailable · unavailable · unavailable'));
  assert.ok(html.includes(COT));
  assert.ok(html.includes(NOTE));
  assert.ok(html.includes('Show all (241 characters)'));
});

test('a panel the reviewer opened renders open with its content, because its state is the store\'s', () => {
  const html = timeline(stepsOpen({ 't2:m1:call:usage': true }));
  assert.match(html, /<details class="tl-panel" open="">/);
  assert.ok(html.includes('Input tokens'));
  assert.equal(timeline(stepsOpen()).includes('Input tokens'), false, 'closed panels hold no content');
});

test('SQL appears only inside an opened technical panel, and card titles use plain labels', () => {
  assert.equal(timeline(stepsOpen()).includes('SELECT'), false);
  const html = timeline(stepsOpen({ 't2:q1:query:raw': true }));
  const raws = (html.match(/<details class="tl-raw"[\s\S]*?<\/details>/g) || []).join('');
  assert.ok(raws.includes('SELECT count(*) FROM work_orders'));
  assert.equal(html.replace(/<details class="tl-raw"[\s\S]*?<\/details>/g, '').includes('SELECT'), false);
  const chrome = html.replace(/<details[\s\S]*?<\/details>/g, '').replace(/<pre[\s\S]*?<\/pre>/g, '');
  assert.equal(chrome.includes('answer_from_records'), false);
  assert.ok(chrome.includes('Records query'));
});

test('Conversations shows the list with grouping chips and the open conversation as a timeline', () => {
  const html = page({ tab: 'conversations', convKey: 's:chat-1', turnId: 't2', conv: { data: CONV, loading: false, error: '', reason: '' },
    convs: part('convs', { rows: ROWS, total: 2, loadedAt: 1 }), poll: { updatedAt: NOW, newTurns: 0, error: '' } });
  assert.ok(html.includes('Grouped by report run'));
  assert.ok(html.includes('2 conversations'));
  assert.ok(html.includes('Answer 2 of 2'));
  assert.match(html, /aria-pressed="true"[^>]*>Answer 2</);
  assert.ok(html.includes('The question shown is the asker&#x27;s own words, recovered from the record.'));
  assert.ok(html.includes('<article aria-label="Question'));
  assert.match(html, /<span aria-live="polite">Updated \d\d:\d\d<\/span>/);
  assert.ok(html.includes('Refresh now'));
});

test('Model calls is a table with an aggregate line, and an open call is a dialog with its card', () => {
  const html = page({ tab: 'models',
    calls: part('calls', { loadedAt: 1, aggregate: { calls: 2, errors: 0, latency_p50_ms: 1800, latency_p95_ms: 1800, input_tokens: 900, output_tokens: 40, cost_usd: 0.004 },
      rows: [{ span_id: 'sp1', started_at: '2026-10-15T11:00:00Z', model: null, label: 'tool decision', latency_ms: null, input_tokens: null, output_tokens: null, cost_usd: null, ok: true, question_excerpt: 'How many?' }] }),
    call: { spanId: 'sp1', loading: false, error: '', turnId: 't2', conversationKey: 's:chat-1', data: { model: null, label: 'tool decision', usage: {}, cost_status: 'unavailable', withheld: [], cot_note: 'N' } } });
  assert.ok(html.includes('2 calls · p50 1.80 s'));
  assert.match(html, /role="dialog" aria-modal="true" aria-label="Model call"/);
  assert.ok(html.includes('Open in Conversations'));
  assert.ok(html.includes('unavailable'));
  assert.equal(html.includes('aria-live'), false, 'Model calls is not polled, so it has no update line');
});

test('Runs lists the live pseudo-run and says there are no golden runs yet', () => {
  const html = page({ tab: 'runs', runs: part('runs', { loadedAt: 1, rows: [{ run_id: 'live', kind: 'live', name: 'All live traffic', conversation_count: 4, turn_count: 9, cost_usd: 0.5 }] }),
    golden: part('golden', { runs: { rows: [], loading: false, error: '', reason: '', loadedAt: 1 } }) });
  assert.ok(html.includes('All live traffic'));
  assert.ok(html.includes('No golden runs yet.'));
});

test('a refusal replaces the tab content with a plain notice', () => {
  const html = page({ tab: 'conversations', convs: part('convs', { error: 'x', reason: 'name_a_company' }) });
  assert.match(html, /role="status">Trace Lab reviews one company at a time/);
  assert.equal(html.includes('tl-filters'), false);
  assert.match(html, /role="tablist"/);
});

test('loading, empty and error states say so in words', () => {
  assert.ok(page({ tab: 'conversations', convs: part('convs', { loading: true }) }).includes('Reading conversations…'));
  assert.ok(page({ tab: 'conversations', convs: part('convs', { loadedAt: 1, rows: [] }) }).includes('No answers in this period.'));
  assert.ok(page({ tab: 'conversations', convs: part('convs', { error: 'timed out after 20s' }) }).includes('Couldn&#x27;t read the conversations: timed out after 20s'));
  assert.ok(page({ tab: 'conversations' }).includes('Pick a conversation on the left.'));
  assert.ok(page({ tab: 'models', calls: part('calls', { loadedAt: 1, rows: [] }) }).includes('No model calls in this period.'));
});
