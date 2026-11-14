// noteAnchors — where a note sits, always in view (14 Nov 2026). A reviewer could not tell which span a note was on or
// where in the text it applied. Now every note card names its span and side ("compliance › analyst › Output"), a quote
// is marked inside the span's text with the note's number, a note on the whole block is numbered by the block heading,
// the tree says which spans hold notes (and a collapsed span, that the spans inside it do), and "Notes on this turn"
// lists every note in tree order.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';
import { traceNotesIndex } from '../logic/traceNotes.js';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const TT = await bundle('../lab/TraceTab.jsx');
const TN = await bundle('../lab/TraceNotes.jsx');
const P = await bundle('../lab/SessionPage.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const RECORD = { id: 'tq', messages: [
  { id: 'tq:turn:user', role: 'user', span_id: null },
  { id: 'tq:r:call', role: 'model_call', span_id: 'r' },
  { id: 'tq:an:call', role: 'model_call', span_id: 'an' },
  { id: 'tq:t:call', role: 'tool_call', span_id: 't' },
  { id: 'tq:turn:answer', role: 'assistant', span_id: null }] };
const node = (id, parent, depth, type, name, input, output) => ({ id, parent_id: parent, depth, seq: depth, type, name,
  started_at: '2026-11-14T09:00:00Z', ended_at: '2026-11-14T09:00:01Z', latency_ms: 1000, level: 'DEFAULT', filled: {},
  input, output });
const Q = 'Which certificates at <B-301> expire this month?';
const ANSWER = '**Two certificates expire.**\n\n- Gas safety — 30 Nov\n- EICR — 2 Dec';
const NODES = [
  node('s0', null, 0, 'agent', 'chat', Q, ANSWER),
  node('r', 's0', 1, 'generation', 'router', { q: 'ROUTER-IN' }, { route: 'compliance' }),
  node('g1', 's0', 1, 'agent', 'compliance', Q, 'COMP-OUT'),
  node('an', 'g1', 2, 'generation', 'analyst', { messages: ['ANALYST-IN'] }, 'The read found two rows.'),
  node('t', 'g1', 2, 'tool', 'lookup', { question: 'TOOL-IN' }, { rows: 2 })];
const TREE = { turn: { turn_id: 'tq' }, purged: false, nodes: NODES };
const label = (id, key, o) => Object.assign({ id, lineage_id: id, step_key: key, note: '', reviewer_email: 'sme@example.com',
  reviewer_id: 'u2', reviewer_role: 'domain_expert', at: '2026-11-14T10:00:00Z' }, o || {});
const LABELS = [
  label('a', 'tq:turn:user', { quote: '<B-301>', note: 'Asked about <B-301>.', reviewer_id: 'me', reviewer_email: 'aasim.shaik@example.com' }),
  label('b', 'tq:turn:user', { note: 'The question is clear.', at: '2026-11-14T11:00:00Z' }),
  label('c', 'tq:turn:answer', { quote: 'certificates expire.\nGas safety', note: 'A quote across two blocks.' }),
  label('d', 'tq:an:call#output', { quote: 'This text is gone', note: 'The quote no longer matches.' }),
  label('e', 'tq:t:call', { note: 'The lookup asked the wrong question.' }),
  label('f', 'tq:orchestrator', { note: 'An old orchestrator note.' }),
  label('g', 'tq:scenario', { note: 'The turn as a whole.' })];
const placed = () => TN.placeNotes(traceNotesIndex(LABELS, RECORD, NODES, 'me', TN.noteView), RECORD, NODES, 'tq');
const SLOT = (id, side) => React.createElement('div', { className: 'slot' }, 'SLOT-' + id + '-' + side);
const annotate = (notes) => ({ keyOf: (id, side) => 'KEY-' + id + '-' + side, count: () => 0, render: SLOT, up() {}, double() {},
  other: () => null, notes });
const order = (s, parts) => parts.map((p) => s.indexOf(p)).every((v, i, a) => v >= 0 && (i === 0 || v > a[i - 1]));

test('the anchor names the span, the spans above it (chat aside) and the side', () => {
  const A = (key, title) => TN.noteAnchor(key, RECORD, NODES, 'tq', title);
  assert.equal(A('tq:turn:user').text, 'chat › Input');
  assert.equal(A('tq:turn:answer').text, 'chat › Output');
  assert.equal(A('tq:an:call#output').text, 'compliance › analyst › Output');
  assert.equal(A('tq:t:call').text, 'compliance › lookup › Input');
  assert.equal(A('tq:g1:agent#output').text, 'compliance › Output');
  assert.equal(A('tq:orchestrator').text, 'Orchestrator');
  assert.equal(A('tq:scenario').text, 'Turn as a whole');
  assert.equal(A('tq:gone:call#output', 'Output · fetch (compliance)').text, 'Output · fetch (compliance)', 'a purged span keeps its saved title');
  const nested = A('tq:an:call#output');
  assert.deepEqual([nested.type, nested.path, nested.name, nested.side], ['generation', ['compliance'], 'analyst', 'out']);
});

test('notes are numbered in tree order: whole-block notes first, then quotes in the order they appear', () => {
  const p = placed();
  assert.deepEqual(p.list.map((x) => [x.id, x.n]), [['b', 1], ['a', 2], ['c', 3], ['d', 4], ['e', 5], ['f', 6]]);
  assert.deepEqual(p.slots['s0:in'].map((x) => x.id), ['b', 'a'], 'a slot lists its notes by number');
  assert.equal(p.list.find((x) => x.id === 'f').nodeId, 's0', 'other notes sit under chat');
  assert.deepEqual(p.list.map((x) => x.found), [null, true, true, false, null, null]);
  assert.deepEqual(p.scenario.map((x) => x.id), ['g'], 'the turn comment is not a span note');
});

test('every card says where its note is, compactly says who and when, and keeps Edit / Delete', () => {
  const p = placed();
  const chat = html(TN.NoteList, { items: p.slots['s0:in'] });
  assert.ok(order(chat, ['The question is clear.', 'Asked about &lt;B-301&gt;.']), 'cards in number order');
  assert.match(chat, /class="lab-an-head"[\s\S]*?class="lab-an-pin"[^>]*>1<[\s\S]*?title="chat › Input"/);
  assert.match(chat, /class="lab-an-avatar"[^>]*>S</, 'initials stand for the author');
  assert.match(chat, /class="lab-an-avatar"[^>]*>AS</);
  assert.ok(chat.includes('>You<') && chat.includes('Domain expert') && !chat.includes('Annotated at'));
  assert.match(chat, /<time[^>]*dateTime="2026-11-14T10:00:00Z"[^>]*title="[^"]+"/, 'the exact time on hover');
  assert.ok(chat.includes('>Edit<') && chat.includes('>Delete<'), 'my own note can still be edited and deleted');
  const nested = html(TN.NoteList, { items: p.slots['an:out'] });
  assert.match(nested, /title="compliance › analyst › Output"/);
  assert.ok(nested.includes('title="Generation"'), 'the span kind icon the tree uses');
  assert.ok(nested.includes('(quoted text changed)'), 'a quote that no longer matches says so');
  const other = html(TN.NoteList, { items: p.other });
  assert.match(other, /title="Orchestrator"/);
  const turn = html(TN.NoteList, { items: p.scenario });
  assert.ok(turn.includes('Turn as a whole') && !turn.includes('lab-an-pin'), 'a turn comment is named but not numbered');
});

test('a quote is marked in the text with its number; HTML in it stays text', () => {
  const p = placed();
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(p.list) });
  assert.match(s, /<mark class="lab-an-mark" data-note="a">&lt;B-301&gt;<\/mark><button type="button" class="lab-an-pin"[^>]*data-n="2"/);
  assert.ok(!s.includes('<B-301>'), 'never markup');
  assert.match(s, /<button type="button" class="lab-an-pin"[^>]*aria-label="Go to note 2"/, 'a mark is reached by keyboard and named by its number');
  // A quote across the bold line and the first list item: each part marked, the number after the last.
  assert.match(s, /<strong>Two <mark class="lab-an-mark" data-note="c">certificates expire\.<\/mark><\/strong>/);
  assert.match(s, /<li><mark class="lab-an-mark" data-note="c">Gas safety<\/mark><button type="button" class="lab-an-pin"[^>]*data-n="3"/);
  // The note on the whole Input is numbered by the Input heading.
  assert.match(s, /class="lab-tt-iohead"(?:(?!<\/div>)[\s\S])*>Input<(?:(?!<\/div>)[\s\S])*data-n="1"/);
  const raw = html(TT.TraceView, { tree: TREE, annotate: annotate(p.list), mode: 'raw' });
  assert.match(raw, /<mark class="lab-an-mark" data-note="a">&lt;B-301&gt;<\/mark>/, 'raw text is marked too');
});

test('a quote that no longer matches is not marked: its number sits by the heading', () => {
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(placed().list), selectedId: 'an' });
  assert.ok(!s.includes('<mark'));
  assert.match(s, /class="lab-tt-iohead"(?:(?!<\/div>)[\s\S])*>Output<(?:(?!<\/div>)[\s\S])*data-n="4"/);
  assert.ok(s.includes('The read found two rows.'));
});

test('the tree marks spans with notes, per side, and a collapsed span says notes are inside', () => {
  const p = placed();
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(p.list) });
  assert.match(s, />chat<(?:(?!<\/li>)[\s\S])*aria-label="4 notes · Input 2 · Output 1 · Other notes 1"/);
  assert.match(s, />analyst<(?:(?!<\/li>)[\s\S])*aria-label="1 note · Output 1"/);
  assert.match(s, />lookup<(?:(?!<\/li>)[\s\S])*aria-label="1 note · Input 1"/);
  assert.ok(/class="lab-tt-ncount"[^>]*title="1 note · Input 1"/.test(s), 'the tooltip');
  assert.ok(!/>router<(?:(?!<\/li>)[\s\S])*lab-tt-ncount/.test(s), 'no badge without notes');
  const t = TT.noteTally(p.list, NODES);
  assert.deepEqual([t.get('g1').total, t.get('g1').inside, t.get('s0').inside], [0, 2, 2]);
  const shut = html(TT.NoteBadges, { tally: t.get('g1'), closed: true });
  assert.match(shut, /class="lab-tt-ncount lab-tt-ncount-in"[^>]*aria-label="2 notes on spans inside"/);
  assert.equal(html(TT.NoteBadges, { tally: t.get('g1'), closed: false }), '', 'an open span shows its own notes only');
});

test('"Notes on this turn" lists every note in tree order and counts the spans', () => {
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(placed().list) });
  assert.ok(s.includes('Notes on this turn') && s.includes('6 notes on 3 spans'));
  const rows = [...s.matchAll(/class="lab-tt-ovrow"[^>]*data-note="(\w+)"/g)].map((m) => m[1]);
  assert.deepEqual(rows, ['b', 'a', 'c', 'd', 'e', 'f']);
  assert.ok(order(s, ['Notes on this turn', 'compliance › analyst › Output', 'The quote no longer matches.', 'Span chat']),
    'the list sits above the span in view');
  assert.ok(s.includes('A quote across two blocks.') && s.includes('certificates expire. Gas safety'), 'note and quote excerpts');
  assert.match(s, /class="lab-tt-ovquote lab-tt-ovquote-stale">“This text is gone” \(quoted text changed\)</, 'a gone quote says so here too');
  assert.ok(s.indexOf('Notes on this turn') < s.indexOf('aria-label="Span chat"'));
});

test('nothing about notes shows when there are none, or when the page passes no notes list', () => {
  const none = html(TT.TraceView, { tree: TREE, annotate: annotate([]) });
  assert.ok(!none.includes('Notes on this turn') && !none.includes('lab-an-') && !none.includes('lab-tt-ncount') && !none.includes('<mark'));
  const old = html(TT.TraceView, { tree: TREE, annotate: Object.assign(annotate(undefined), { count: (id) => (id === 'r' ? 2 : 0) }) });
  assert.ok(!old.includes('Notes on this turn') && !old.includes('<mark'));
  assert.match(old, />router<(?:(?!<\/li>)[\s\S])*aria-label="2 notes"/, 'the question page keeps its counts');
  assert.ok(!html(TT.TraceView, { tree: TREE }).includes('lab-an-'));
});

test('times read relative, with the exact time kept for hover', () => {
  const now = Date.parse('2026-11-14T12:00:00Z');
  assert.equal(TT.relTime('2026-11-14T11:59:40Z', now), 'just now');
  assert.equal(TT.relTime('2026-11-14T11:55:00Z', now), '5 min ago');
  assert.equal(TT.relTime('2026-11-14T09:00:00Z', now), '3 h ago');
  assert.equal(TT.relTime('2026-11-13T09:00:00Z', now), 'yesterday');
  assert.equal(TT.relTime('2026-11-11T12:00:00Z', now), '3 days ago');
  assert.equal(TT.relTime('2026-10-25T12:00:00Z', now), '25 Oct');
  assert.equal(TT.relTime('2025-10-25T12:00:00Z', now), '25 Oct 2025');
  assert.equal(TT.relTime('', now), '');
});

test('on the session page a quoted note is marked, its card anchored and the turn overview drawn', () => {
  const tree = { turn: { turn_id: 'turn-1' }, purged: false, nodes: [
    { id: 'root', parent_id: null, depth: 0, seq: 0, type: 'agent', name: 'chat', input: 'Which certificates expire?', output: 'Two do.',
      started_at: '2026-11-11T09:00:00Z', ended_at: '2026-11-11T09:00:05Z' }] };
  const notes = { trace_id: 'turn-1', organization_id: 'org-1', me: 'me',
    record: { id: 'turn-1', messages: [{ id: 'm-u', role: 'user', span_id: 'root' }, { id: 'm-a', role: 'assistant', span_id: 'root' }] },
    labels: [label('n1', 'm-u', { quote: 'certificates', note: 'Which ones?' })] };
  const data = { session: { id: 'scn__a__SD-1__1', run_tag: 'a' }, scenario: null,
    traces: [{ id: 'turn-1', turn_index: 1, input: 'Which certificates expire?', output: 'Two do.', status: 'ok', metadata: {}, scores: {} }] };
  const out = html(P.SessionBody, { data, active: 'turn-1', trees: { 'turn-1': { tree } }, notes: { 'turn-1': notes }, labels: {},
    onTab() {}, me: { user_id: 'me' } });
  assert.match(out, /<mark class="lab-an-mark" data-note="n1">certificates<\/mark>/);
  assert.match(out, /title="chat › Input"/);
  assert.ok(out.includes('1 note on 1 span') && out.includes('Which ones?'));
  assert.match(out, /aria-label="Add note on Input · chat"/, 'Add note is still there');
});
