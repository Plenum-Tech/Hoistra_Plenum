// runs — the runs list, a run's questions, and one question: the header, the expected answer before the trace, each
// step's input block followed by its own output block, everyone's notes beside their block (no Right / Wrong), the comment
// box for the question as a whole, and the page when no answer is linked yet.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const R = await bundle('../lab/Runs.jsx');
const Q = await bundle('../lab/QuestionPage.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const mc = (o) => Object.assign({ usage: { input_tokens: 800, output_tokens: 60 }, status: 'ok', latency_s: 1.2, total_cost: 0.002 }, o);
// Shaped like the service's normalized record: the question, the routing call, a two-call plan, the fetch stage with its
// read and a tool, the analyst, the answer.
const REC = { id: 'tq', timestamp: '2026-10-22T09:14:00Z', models: ['gpt-5.6-terra', 'claude-sonnet-5'], tools: ['lookup'],
  groups: [{ span_id: 'g1', name: 'compliance', latency_ms: 4100, cost_usd: null, child_count: 5 }],
  messages: [
    { id: 'tq:root:user', role: 'user', depth: 0, seq: 0, text: 'Which certificates expire this month?' },
    { id: 'tq:r:call', role: 'model_call', depth: 1, seq: 1, label: 'routing decision',
      model_call: mc({ model: 'gpt-5.6-terra', call_kind: 'router', request: { prompt: 'ROUTE-THIS' }, response: { content: 'compliance' } }) },
    { id: 'tq:p:call', role: 'model_call', depth: 1, seq: 2, group: 'compliance', parent_span_id: 'g1', label: 'plan',
      model_call: mc({ model: 'gpt-5.6-terra', request: { q: 'PLAN-IN-1' }, response: { content: 'PLAN-OUT-1' } }) },
    { id: 'tq:p2:call', role: 'model_call', depth: 1, seq: 3, group: 'compliance', parent_span_id: 'g1', label: 'plan',
      model_call: mc({ model: 'gpt-5.6-terra', request: { q: 'PLAN-IN-2' }, response: { content: 'PLAN-OUT-2' } }) },
    { id: 'tq:f:note', role: 'system_note', subtype: 'stage', depth: 1, seq: 4, group: 'compliance', parent_span_id: 'g1', name: 'fetch', label: 'fetch' },
    { id: 'tq:c:query', role: 'db_query', depth: 2, seq: 5, group: 'compliance', label: 'certificates', row_count: 1, ok: true,
      sql: 'SELECT name, expires FROM certificates', rows: [{ name: 'Gas safety', expires: '2026-10-30' }] },
    { id: 'tq:an:call', role: 'model_call', depth: 1, seq: 6, group: 'compliance', parent_span_id: 'g1', label: 'analyst',
      model_call: mc({ model: 'claude-sonnet-5', request: { q: 'ANALYST-IN' }, response: { content: 'ANALYST-OUT' } }) },
    { id: 'tq:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: 'One certificate expires: Gas safety.' }] };

const note = (o) => Object.assign({ lineage_id: o.id, reviewer_id: 'other', reviewer_email: 'sam@northbridge.example', reviewer_role: 'domain_expert',
  at: '2026-10-22T10:00:00Z', edited: false, quote: null }, o);
const LABELS = [
  note({ id: 'n1', step_key: 'tq:c:query#output', note: 'Only one row came back.', reviewer_id: 'me', reviewer_email: 'me@x.test', reviewer_role: 'tester', at: '2026-10-22T09:30:00Z' }),
  note({ id: 'n2', step_key: 'tq:c:query#output', note: 'The read should include B-102.', quote: 'Gas safety', edited: true }),
  note({ id: 'n3', step_key: 'tq:p2:call', note: 'Second plan call repeats the first.', label: 'wrong' }),
  note({ id: 'n4', step_key: 'tq:scenario', note: 'A fair question for a facilities manager.' }),
  note({ id: 'n5', step_key: 'tq:g1:agent', note: 'An older note on the agent.' })];
const DATA = {
  question: { code: 'R3-12', question: 'Which certificates expire this month?', followups: ['And which are gas safety?'], role: 'Facilities manager',
    intent: 'compliance lookup', asked_at: '2026-10-22T09:14:00Z', turn_ids: ['tq', 'tq2'] },
  run: { code: 'R3', title: 'Certificates batch' },
  expected: { id: 'e2', expected: 'Gas safety (B-101) and Fire alarm (B-102) expire this month.', expected_id: 'EXP-12', evidence: { register: 'certificates' },
    source: 'excel', author_email: 'ana@northbridge.example', at: '2026-10-21T10:00:00Z' },
  expected_history: [{ id: 'e1', expected: 'One certificate expires.', source: 'manual', author_email: 'ana@northbridge.example', at: '2026-10-20T10:00:00Z' }],
  traces: [{ turn_id: 'tq', source: 'manual' }, { turn_id: 'tq2', source: 'manual' }],
  active: { turn_id: 'tq', organization_id: 'o1', conversation_key: 'c1', trace: REC },
  labels: LABELS, me: 'me' };
const rowOf = (out, key) => out.split('<li class="lab-tl-row"').find((chunk) => chunk.includes('data-key="' + key + '"')) || '';

test('runs: each card has its code, title, synthetic or live with owner and day, source, time range and counts', () => {
  const out = html(R.RunsList, { runs: [
    { code: 'R3', title: 'Certificates batch', kind: 'synthetic', source: 'coding_agent', n_questions: 12, n_traced: 9, n_expected: 10, n_notes: 4,
      n_reviewed: 3, first_at: '2026-10-21T09:00:00Z', last_at: '2026-10-22T17:00:00Z' },
    { code: 'R2', title: 'ana@northbridge.example · 2026-10-21', kind: 'production', owner_email: 'ana@northbridge.example', run_day: '2026-10-21',
      source: 'sync', n_questions: 1, n_traced: 1, n_expected: 0, n_notes: 1, n_reviewed: 1 }], onOpen() {} });
  ['>R3<', 'Certificates batch', '>synthetic<', '>coding agent<', '12 questions · 9 with answers · 10 with expected · 4 notes · 3 reviewed', ' → ',
    '>R2<', 'live · ana@northbridge.example · 21 Oct 2026', '>synced<', '1 question · 1 with answers · 0 with expected · 1 note · 1 reviewed']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.ok(html(R.RunsList, { runs: [], loading: false, curator: true }).includes('No runs yet'));
});

test('a run\'s questions: the way back, each code, the question, role, expected, answers and notes', () => {
  const out = html(R.RunQuestionsList, { run: { code: 'R3', title: 'Certificates batch', kind: 'synthetic', source: 'excel', n_questions: 2 },
    questions: [{ code: 'R3-1', question: 'Which vendors are blocked?', role: 'Facilities manager', has_expected: true, turn_ids: ['t1', 't2'], n_notes: 3, n_annotators: 2 },
      { code: 'R3-2', question: 'Which assets are overdue?', has_expected: false, turn_ids: [], n_notes: 0 }], selected: 'R3-1', onOpen() {}, onBack() {} });
  ['All runs', '>R3-1<', '>R3-2<', 'Which vendors are blocked?', 'Facilities manager', 'expected ✓', '2 answers', '3 notes · 2 people', 'no answer yet']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.match(out, /class="lab-qitem lab-on" aria-current="true"><span class="lab-qcode">R3-1</);
  assert.equal((out.match(/expected ✓/g) || []).length, 1);
});

test('a question: header, expected answer before the trace, input then output blocks in order, notes beside them, no Right / Wrong', () => {
  const out = html(Q.QuestionView, { data: DATA, curator: true, openAll: true });
  ['<strong class="lab-qp-code">R3-12</strong>', '>R3<', '2 supporting answers', 'active answer: <code class="lab-mono">tq</code>', 'gpt-5.6-terra',
    'claude-sonnet-5', '>lookup<', 'Opening: </strong>Which certificates expire this month?', 'Follow-up 1: </strong>And which are gas safety?',
    '<strong class="lab-qp-eid">EXP-12</strong>', 'Source: Excel · by ana@northbridge.example', '1 earlier version', 'Answer 1', 'Answer 2', 'Link an answer']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.ok(out.indexOf('Expected answer') < out.indexOf('class="lab-tl"'), 'the expected answer comes before the trace');
  const order = [...out.matchAll(/data-block="(\w+)" data-kind="(\w+)"/g)].map((m) => m[1] + ':' + m[2]);
  assert.deepEqual(order, ['user:user', 'input:orchestrator', 'output:orchestrator', 'input:model', 'output:model', 'input:code', 'output:code',
    'input:model', 'output:model', 'answer:answer']);
  // Each side shows its own half: the plan's inputs above, only its last output below; the read's SQL above, its rows below.
  const plan = rowOf(out, 'tq:p:call');
  const planOut = rowOf(out, 'tq:p:call#output');
  assert.ok(plan.includes('PLAN-IN-1') && plan.includes('PLAN-IN-2') && !plan.includes('PLAN-OUT-2'));
  assert.ok(planOut.includes('PLAN-OUT-2') && !planOut.includes('PLAN-IN-1') && !planOut.includes('PLAN-OUT-1'));
  assert.ok(rowOf(out, 'tq:f:note').includes('SELECT name, expires FROM certificates'));
  assert.ok(rowOf(out, 'tq:f:note#output').includes('<th scope="col">expires</th>') && rowOf(out, 'tq:f:note#output').includes('Gas safety'));
  // Notes sit beside their block, mine first, each with who, role and when; an old note on an agent goes to Other notes.
  const notes = rowOf(out, 'tq:f:note#output');
  assert.ok(notes.indexOf('Only one row came back.') < notes.indexOf('The read should include B-102.'), 'mine first');
  ['>You<', 'Tester', 'sam@northbridge.example', 'Domain expert', 'Annotated at ', ' · edited', '>Edit<', '>Delete<']
    .forEach((t) => assert.ok(notes.includes(t), t));
  assert.ok(rowOf(out, 'tq:p:call').includes('Second plan call repeats the first.'), 'a note on a part belongs to its step');
  assert.ok(out.includes('Other notes') && out.includes('An older note on the agent.'));
  assert.ok(!/\bRight\b|\bWrong\b|Not sure/.test(out), 'no Right / Wrong / Not sure anywhere');
  // The question as a whole.
  assert.ok(out.includes('placeholder="Add a comment about this question as a whole…"') && out.includes('>Save comment<'));
  assert.ok(out.includes('A fair question for a facilities manager.'));
  // Folded by default: the header rows stay, the parts wait behind Show.
  const shut = html(Q.QuestionView, { data: DATA, curator: false });
  assert.ok(!shut.includes('lab-part-body') && !shut.includes('<th scope="col">expires</th>') && shut.includes('>Show<') && shut.includes('Orchestrator'));
  assert.ok(!shut.includes('aria-expanded="true"'), 'every part starts folded, with a one-line glimpse');
  assert.ok(!shut.includes('Link an answer'), 'only curators link answers');
});

test('no answer linked yet; the note editor opens beside its block with the selected text', () => {
  const none = html(Q.QuestionView, { data: Object.assign({}, DATA, { traces: [], active: null, labels: [] }), curator: true });
  assert.ok(none.includes('No recorded answer is linked to this question yet') && none.includes('Link an answer') && none.includes('Expected answer'));
  assert.ok(!none.includes('class="lab-tl"') && !none.includes('Save comment'));
  const empty = html(Q.QuestionView, { data: Object.assign({}, DATA, { expected: null, expected_history: [], active: null }) });
  assert.ok(empty.includes('No expected answer yet') && empty.includes('Add expected answer'));
  const open = html(Q.QuestionView, { data: DATA, editor: { block: 'tq:an:call#output', quote: 'ANALYST-OUT', text: '', target: 'Output · analyst', saving: false, error: '' } });
  const row = rowOf(open, 'tq:an:call#output');
  assert.ok(row.includes('class="lab-ne"') && row.includes('New note · Output · analyst') && row.includes('ANALYST-OUT'));
  assert.match(row, /<button type="button" class="lab-btn lab-btn-sm" disabled="">Save</);
  assert.equal((open.match(/class="lab-ne"/g) || []).length, 1, 'one editor, beside its block');
});

test('linking an answer: recorded answers to pick from, the same question marked, a way out when none match', () => {
  const picks = [
    { turn_id: 'turn-77', question: 'Which vendors are blocked?', answer: 'ProudCastle Fire is blocked.', email: 'ann@a.test',
      asked_at: '2026-10-21T10:00:00Z', ok: true, same: true },
    { turn_id: 'turn-78', question: 'Which vendors are lapsed?', answer: null, email: 'bob@a.test', asked_at: '2026-10-20T10:00:00Z', ok: false, same: false }];
  const out = html(Q.AnswerPicks, { picks, busy: '', onPick() {} });
  assert.ok(out.includes('aria-label="Recorded answers you can link"') && out.includes('same question'));
  assert.equal((out.match(/same question/g) || []).length, 1, 'only the word-for-word match is marked');
  assert.ok(out.includes('ProudCastle Fire is blocked.') && out.includes('turn-78') && out.includes('failed'));
  assert.equal((out.match(/>Link</g) || []).length, 2);
  assert.match(html(Q.AnswerPicks, { picks, busy: 'turn-77', onPick() {} }), />Linking…</);
  assert.ok(html(Q.AnswerPicks, { picks: [] }).includes('Ask the question in the chat first'));
  assert.ok(html(Q.AnswerPicks, { picks: null }).includes('Reading the recorded answers'));
});
