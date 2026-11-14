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

// The answer as its trace (9 Nov 2026): the same record with its span ids, the tree the service builds for it, and the
// notes written on its steps (old keys land on their step and side).
const spanOf = (id) => { const p = String(id).split(':')[1]; return p === 'root' ? null : p; };
const REC2 = Object.assign({}, REC, { messages: REC.messages.map((m) => Object.assign({}, m, { span_id: spanOf(m.id) })) });
const tn = (id, parent, type, name, extra) => Object.assign({ id, parent_id: parent, depth: parent ? 1 : 0, type, name,
  started_at: '2026-10-22T09:14:00Z', ended_at: '2026-10-22T09:14:01Z', latency_ms: 1000, level: 'DEFAULT', filled: {},
  input: null, output: null }, extra || {});
const TREE2 = { purged: false, turn: { turn_id: 'tq', latency_ms: 9000, cost_usd: 0.01, total_tokens: 900, models: ['gpt-5.6-terra'] },
  nodes: [tn('s0', null, 'agent', 'chat', { input: 'Which certificates expire this month?', output: 'One certificate expires: Gas safety.' }),
    tn('r', 's0', 'generation', 'router', { input: { prompt: 'ROUTE-THIS' }, output: 'compliance' }),
    tn('g1', 's0', 'agent', 'compliance', { input: 'Which certificates expire this month?', output: 'ANALYST-OUT' }),
    tn('p', 'g1', 'generation', 'plan', { input: { q: 'PLAN-IN-1' }, output: 'PLAN-OUT-1' }),
    tn('p2', 'g1', 'generation', 'plan', { input: { q: 'PLAN-IN-2' }, output: 'PLAN-OUT-2' }),
    tn('c', 'g1', 'span', 'certificates', { input: { sql: 'SELECT name, expires FROM certificates' }, output: { rows: [{ name: 'Gas safety' }] } }),
    tn('an', 'g1', 'generation', 'analyst', { input: { q: 'ANALYST-IN' }, output: 'ANALYST-OUT' })] };
const DATA2 = Object.assign({}, DATA, { active: Object.assign({}, DATA.active, { trace: REC2 }) });
const TREE_OK = { turn_id: 'tq', missing: false, tree: TREE2 };

test('a question: header, expected answer, then the trace with notes under their steps', () => {
  const out = html(Q.QuestionView, { data: DATA2, tree: TREE_OK, curator: true });
  ['<strong class="lab-qp-code">R3-12</strong>', '>R3<', '2 supporting answers', 'active answer: <code class="lab-mono">tq</code>', 'gpt-5.6-terra',
    'claude-sonnet-5', '>lookup<', 'Opening: </strong>Which certificates expire this month?', 'Follow-up 1: </strong>And which are gas safety?',
    '<strong class="lab-qp-eid">EXP-12</strong>', 'Source: Excel · by ana@northbridge.example', '1 earlier version', 'Answer 1', 'Answer 2', 'Link an answer']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.ok(out.indexOf('Expected answer') < out.indexOf('class="lab-tt"'), 'the expected answer comes before the trace');
  assert.ok(!out.includes('class="lab-tl"') && !out.includes('lab-legend') && !out.includes('aria-label="Answer view"'),
    'the block view and the tab bar are gone');
  // Where people wrote: counts on the steps (the read has two, the second plan call one, the agent one).
  assert.match(out, />certificates<(?:(?!<\/li>)[\s\S])*aria-label="2 notes"/);
  assert.match(out, /aria-label="1 note"/);
  // chat is selected on open: its Input and Output each take notes.
  assert.equal((out.match(/>Add note</g) || []).length, 2);
  assert.match(out, /data-note-key="tq:root:user"/);
  assert.match(out, /data-note-key="tq:root:answer"/);
  // The question as a whole, and no Right / Wrong anywhere.
  assert.ok(out.includes('placeholder="Add a comment about this question as a whole…"') && out.includes('>Save comment<'));
  assert.ok(out.includes('A fair question for a facilities manager.'));
  assert.ok(!/\bRight\b|\bWrong\b|Not sure/.test(out), 'no Right / Wrong / Not sure anywhere');
  // A step's notes sit under the side they were written on, mine first, each with who, role and when.
  const read = html(Q.QuestionView, { data: DATA2, tree: TREE_OK, traceView: { selectedId: 'c' } });
  assert.ok(read.indexOf('SELECT name, expires FROM certificates') < read.indexOf('Only one row came back.'), 'under its output');
  assert.ok(read.indexOf('Only one row came back.') < read.indexOf('The read should include B-102.'), 'mine first');
  ['>You<', 'Tester', 'sam@northbridge.example', 'Domain expert', 'Annotated at ', ' · edited', '>Edit<', '>Delete<', 'Gas safety']
    .forEach((t) => assert.ok(read.includes(t), t));
  const plan2 = html(Q.QuestionView, { data: DATA2, tree: TREE_OK, traceView: { selectedId: 'p2' } });
  assert.ok(plan2.includes('Second plan call repeats the first.'), 'a note on a part now sits on its own step');
  const agent = html(Q.QuestionView, { data: DATA2, tree: TREE_OK, traceView: { selectedId: 'g1' } });
  assert.ok(agent.includes('An older note on the agent.'), 'a note on an agent sits on that agent');
});

test('the note editor opens under its side with the selected text', () => {
  const editor = { block: 'tq:an:call#output', quote: 'ANALYST-OUT', text: '', target: 'Output · analyst', saving: false, error: '' };
  const open = html(Q.QuestionView, { data: DATA2, tree: TREE_OK, editor, traceView: { selectedId: 'an' } });
  assert.ok(open.includes('class="lab-ne"') && open.includes('New note · Output · analyst'));
  assert.match(open, /<button type="button" class="lab-btn lab-btn-sm" disabled="">Save</);
  assert.equal((open.match(/class="lab-ne"/g) || []).length, 1, 'one editor, under its side');
  assert.ok(open.indexOf('data-note-key="tq:an:call#output"') < open.indexOf('class="lab-ne"'), 'under the output');
  assert.equal((open.match(/>Add note</g) || []).length, 1, 'the side being written on has the editor instead of Add note');
});

test('the page without a trace still works', () => {
  const none = html(Q.QuestionView, { data: Object.assign({}, DATA, { traces: [], active: null, labels: [] }), curator: true });
  assert.ok(none.includes('No recorded answer is linked to this question yet') && none.includes('Link an answer') && none.includes('Expected answer'));
  assert.ok(!none.includes('class="lab-tt"') && !none.includes('Save comment'));
  const empty = html(Q.QuestionView, { data: Object.assign({}, DATA, { expected: null, expected_history: [], active: null }) });
  assert.ok(empty.includes('No expected answer yet') && empty.includes('Add expected answer'));
  const gone = html(Q.QuestionView, { data: Object.assign({}, DATA, { active: { turn_id: 'tq', missing: true } }) });
  assert.ok(gone.includes('This answer can no longer be read'));
  const reading = html(Q.QuestionView, { data: DATA2, tree: { loading: true } });
  assert.ok(reading.includes('Reading the trace…') && reading.includes('Save comment'), 'the comment box does not wait for the trace');
  assert.ok(html(Q.QuestionView, { data: DATA2 }).includes('Reading the trace…'));
  const failed = html(Q.QuestionView, { data: DATA2, tree: { error: 'boom' } });
  assert.match(failed, /role="alert"[^>]*>Could not read this answer’s trace: boom/);
  assert.ok(html(Q.QuestionView, { data: DATA2, tree: { turn_id: 'tq', missing: true, tree: null } }).includes('This answer can no longer be read'));
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

// 13 Nov 2026: a session copied from another Trace Lab was listed in Sessions and not in Runs, because Runs is built
// from the chat here and the chat here never recorded it. Its turns are questions now; the answer is its stored trace.
test('a question whose answer was asked on another machine shows its stored trace and links to its session', () => {
  const stored = { session_id: 'scn__frontend_nov13__frontend-pilot-001__1', turn_index: 1, run_tag: 'frontend_nov13', origin: 'ubuntu-4gb-hel1-2' };
  const data = Object.assign({}, DATA, { active: { turn_id: 'turn-p1', organization_id: 'o-1', conversation_key: null, trace: null, stored } });
  const out = html(Q.QuestionView, { data, tree: Object.assign({}, TREE_OK, { stored: true }) });
  assert.ok(out.includes('asked on ubuntu-4gb-hel1-2'));
  assert.ok(out.includes('?view=runs&amp;session=scn__frontend_nov13__frontend-pilot-001__1'), 'the session inside Runs (14 Nov 2026)');
  assert.ok(out.includes('run tag frontend_nov13'));
  assert.ok(!out.includes('This answer can no longer be read'));
  // an answer the chat here recorded says nothing of the kind
  assert.ok(!html(Q.QuestionView, { data: DATA2, tree: TREE_OK }).includes('copied into Trace Lab'));
});

// ── Sessions in Runs (13 Nov 2026): Synthetic is gone from the filter; the runs made from sessions say "sessions" ────
test('runs: the filter is All, Sessions and Live; Synthetic is gone', () => {
  assert.deepEqual(R.KINDS.map((k) => k[1]), ['All', 'Sessions', 'Live']);
  assert.deepEqual(R.KINDS.map((k) => k[0]), ['', 'sessions', 'production'], 'what the service is asked for');
});

test("runs: a run made from a run tag's sessions says sessions and synced, never synthetic", () => {
  const out = html(R.RunsList, { runs: [
    { code: 'R60', title: 'Sessions · hussain_5q_v0', kind: 'synthetic', source: 'sync', n_questions: 8, n_traced: 8 },
    { code: 'R3', title: 'Certificates batch', kind: 'synthetic', source: 'coding_agent', n_questions: 12 }], onOpen() {} });
  assert.ok(out.includes('Sessions · hussain_5q_v0') && out.includes('>sessions<') && out.includes('>synced<'));
  assert.equal((out.match(/>synthetic</g) || []).length, 1, 'only the typed batch is synthetic');
});

// 13 Nov 2026 review: Sync now reloads the list with the filter in view when it finishes, not the one in view when it
// was pressed. (No DOM in these tests: the page keeps the newest filter in a ref, and every reload reads it.)
test('runs: Sync now reloads with the filter in view when it finishes', async () => {
  const { readFileSync: read } = await import('node:fs');
  const src = read(new URL('../Runs.jsx', import.meta.url), 'utf-8');
  const body = src.slice(src.indexOf('const runSync = async'), src.indexOf('const toggle ='));
  assert.ok(body.includes('load(appliedRef.current, 0)'), body);
  assert.ok(src.includes('appliedRef.current = g;'));
});

// 14 Nov 2026 (Hussain): a run made from a run tag's sessions opens that tag on the Sessions page, not as a run.
test('runs: the sessions run of a tag is known by its title and the sync that made it', () => {
  assert.equal(R.sessionsTagOf({ title: 'Sessions · frontend_nov13_202718', kind: 'synthetic', source: 'sync' }), 'frontend_nov13_202718');
  assert.equal(R.sessionsTagOf({ title: 'Sessions · frontend_nov13_202718', kind: 'synthetic', source: 'excel' }), null, 'a typed batch named so is a run');
  assert.equal(R.sessionsTagOf({ title: 'aasim@x.test · 2026-11-13', kind: 'production', source: 'sync' }), null);
  assert.equal(R.sessionsTagOf(null), null);
});

// 14 Nov 2026 (Hussain): the Sessions tab is gone, so every run opens inside Runs; a sessions run and a live run open
// there as their conversations (runSessions.test.mjs).
test('runs: clicking any run opens it inside Runs, a sessions run too', () => {
  const opened = [];
  const out = html(R.RunsList, { runs: [
    { code: 'R47', title: 'Sessions · frontend_nov13_202718', kind: 'synthetic', source: 'sync', n_questions: 40 },
    { code: 'R48', title: 'aasim@x.test · 2026-11-13', kind: 'production', source: 'sync', n_questions: 4 }],
  onOpen: (c) => opened.push(['run', c]) });
  assert.ok(out.includes('Sessions · frontend_nov13_202718'));
  assert.equal(typeof R.openRow, 'function');
  R.openRow({ code: 'R47', title: 'Sessions · frontend_nov13_202718', kind: 'synthetic', source: 'sync' }, (c) => opened.push(['run', c]));
  R.openRow({ code: 'R48', title: 'aasim@x.test · 2026-11-13', kind: 'production', source: 'sync' }, (c) => opened.push(['run', c]));
  assert.deepEqual(opened, [['run', 'R47'], ['run', 'R48']]);
});
