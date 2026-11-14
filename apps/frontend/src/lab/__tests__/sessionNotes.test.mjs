// sessionNotes — annotating on the session page (11 Nov 2026). Runs are gone from Trace Lab: a person reads a
// conversation on its session page and, on the turn in front of them, writes notes on a span's input or output (the same
// notes the Labels, Notes, Learning and Loop pages read) and marks each failure mode present or absent in that turn.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';
import { modeRows, needsRead } from '../logic/sessions.js';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const P = await bundle('../lab/SessionPage.jsx');
const L = await bundle('../lab/LabApp.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));
const src = (name) => readFileSync(new URL('../' + name, import.meta.url), 'utf-8');

const LABELS = { trace_id: 'turn-1', me: 'me',
  modes: [{ lineage_id: 'm1', name: 'Wrong building', definition: 'The answer is about another building.' },
    { lineage_id: 'm2', name: 'Raw error shown', definition: null }],
  cells: { m1: [{ value: 'positive', author_email: 'reviewer@example.com', mine: true, at: '2026-11-11T10:00:00Z' },
    { value: 'negative', author_email: 'sme@example.com', mine: false, at: '2026-11-11T09:00:00Z' }] } };

test('each failure mode as the page reads it: the reader\'s value, everyone else\'s, and the counts', () => {
  assert.deepEqual(modeRows(LABELS), [
    { lineage: 'm1', name: 'Wrong building', definition: 'The answer is about another building.', mine: 'positive',
      others: [{ who: 'sme@example.com', value: 'negative' }], pos: 1, neg: 1 },
    { lineage: 'm2', name: 'Raw error shown', definition: '', mine: null, others: [], pos: 0, neg: 0 }]);
  assert.deepEqual(modeRows(null), []);
});

test('the failure modes panel: present or absent per mode, what others said, and a plain empty state', () => {
  const out = html(P.FailureModes, { data: LABELS, onSet() {} });
  assert.match(out, /aria-label="Failure modes in this turn"/);
  assert.match(out, /<button[^>]*aria-pressed="true"[^>]*>Present<\/button>/, 'the reader marked Wrong building present');
  assert.equal((out.match(/>Absent<\/button>/g) || []).length, 2);
  assert.ok(out.includes('sme@example.com: absent'));
  assert.ok(out.includes('1 present · 1 absent'));
  assert.ok(html(P.FailureModes, { data: { modes: [], cells: {} }, onSet() {} })
    .includes('No failure modes yet. The platform team or the consultant adds them on the Labels page.'));
  assert.ok(html(P.FailureModes, { data: null, onSet() {} }).includes('Reading the failure modes'));
});

const TREE = { turn: { turn_id: 'turn-1' }, purged: false, nodes: [
  { id: 'root', parent_id: null, depth: 0, seq: 0, type: 'agent', name: 'chat', started_at: '2026-11-11T09:00:00Z', ended_at: '2026-11-11T09:00:05Z' },
  { id: 's1', parent_id: 'root', depth: 1, seq: 1, type: 'tool', name: 'list_certificates', started_at: '2026-11-11T09:00:01Z', ended_at: '2026-11-11T09:00:02Z' }] };
const NOTES = { trace_id: 'turn-1', organization_id: 'org-1', me: 'me',
  record: { id: 'turn-1', messages: [{ id: 'm-u', role: 'user', span_id: 'root' }, { id: 'm-a', role: 'assistant', span_id: 'root' }] },
  labels: [{ id: 'n1', lineage_id: 'n1', step_key: 'm-u', note: 'Asked about B-301, answered about B-302.', reviewer_email: 'sme@example.com',
    reviewer_id: 'u2', reviewer_role: 'domain_expert', at: '2026-11-11T10:00:00Z' },
  { id: 'n2', lineage_id: 'n2', step_key: 'turn-1:scenario', note: 'The whole turn missed the point.', reviewer_email: 'sme@example.com',
    reviewer_id: 'u2', reviewer_role: 'domain_expert', at: '2026-11-11T10:00:00Z' }] };
const DATA = { session: { id: 'scn__baseline_v0__MT-001__1', run_tag: 'baseline_v0' }, scenario: null,
  traces: [{ id: 'turn-1', turn_index: 1, input: 'Which certificates expire?', output: 'Two do.', status: 'ok', metadata: {}, scores: {} }] };

test('a turn on the session page carries notes on its spans and a comment box for the turn; its failure modes are hidden for now', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: { 'turn-1': { tree: TREE } },
    notes: { 'turn-1': NOTES }, labels: { 'turn-1': LABELS }, onTab() {}, me: { user_id: 'me' } });
  assert.ok(out.includes('Asked about B-301, answered about B-302.'), 'a note on the question message shows under chat');
  assert.match(out, /aria-label="Add note on Input · chat"/);
  assert.ok(out.includes('Comment on this turn as a whole') && out.includes('The whole turn missed the point.'));
  assert.ok(!out.includes('Failure modes in this turn') && !out.includes('>Present<') && !out.includes('>Absent<'),
    'the failure-mode block is hidden on the session page for now (Hussain, 13 Nov 2026)');
  assert.ok(out.includes('Pick a span, then press Add note under its input or output'));
});

test('while a turn\'s notes are read the trace still shows, and says so', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: { 'turn-1': { tree: TREE } }, notes: {}, labels: {},
    onTab() {}, me: { user_id: 'me' } });
  assert.ok(out.includes('Reading the notes'));
  assert.ok(out.includes('list_certificates'));
});

test('a note saved on the session page pops up the next step: go to Loop', () => {
  assert.ok(src('TraceNotes.jsx').includes("nextStepFor('note_saved')"));
  assert.ok(src('SessionPage.jsx').includes('<NextStep '));
  // The session page opens inside Runs now (14 Nov 2026): Runs hands it the way to Loop.
  assert.ok(/<RunsPage [^]*?onOpenLoop=\{\(\) => onView\('loop'\)\}/.test(src('LabApp.jsx')));
  assert.ok(/<SessionPage [^>]*onOpenLoop=\{onOpenLoop\}/.test(src('Runs.jsx')));
});

test('Runs is the first tab and the home page (Sessions moved into it, 14 Nov 2026): its page and the question links', () => {
  assert.ok(existsSync(new URL('../Runs.jsx', import.meta.url)) && existsSync(new URL('../QuestionPage.jsx', import.meta.url)));
  assert.deepEqual(L.navFor({ manages: false }).map(([k]) => k).slice(0, 2), ['runs', 'labels']);
  assert.equal(L.HOME_VIEW, 'runs');
  const app = src('LabApp.jsx');
  assert.ok(/^import RunsPage\b[^\n]*from '\.\/Runs\.jsx';$/m.test(app) && app.includes("current === 'runs' ? <RunsPage"));
  ['LabelsView', 'Loop', 'Notes'].forEach((page) => {
    const line = app.split('\n').find((l) => l.includes('<' + page + ' ')) || '';
    assert.ok(line.includes('onOpenQuestion={openQuestion}'), page + ' links a question code to its page again: ' + line);
  });
});

test('a turn\'s tree and labels are read once, its notes every time it is opened, and a failed read is tried again', () => {
  assert.equal(needsRead(undefined), true);
  assert.equal(needsRead({ tree: {} }), false);
  assert.equal(needsRead({ error: 'timed out' }), true, 'a failed read is not kept for ever');
  assert.equal(needsRead({ labels: [] }, true), true, 'notes are read again each time the turn opens');
  assert.equal(needsRead({ loading: true }, true), false, 'never two reads at once');
  const page = src('SessionPage.jsx');
  assert.ok(page.includes('<Trace key={current.id}'), 'each turn has its own notes state, so a late save never lands on another turn');
  assert.ok(/needsRead\(notes\[id\], true\)/.test(page));
});

test('a turn the chat no longer has offers no comment box, and says why', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: { 'turn-1': { tree: TREE } },
    notes: { 'turn-1': { ...NOTES, record: null, labels: [] } }, labels: { 'turn-1': LABELS }, onTab() {}, me: { user_id: 'me' } });
  assert.ok(!out.includes('Comment on this turn as a whole'));
  assert.ok(out.includes('The chat no longer has this turn, so notes cannot be added here.'));
});

test('a label error or busy mode belongs to the turn it happened on', () => {
  // The failure-mode block is hidden on the session page for now (Hussain, 13 Nov 2026), so nothing about a label
  // shows there, for this turn or another. The block itself still shows its own error and busy mode, and the page
  // still hands each turn only its own, so the block comes back whole.
  const at = (label) => html(P.SessionBody, { data: DATA, active: 'turn-1', trees: { 'turn-1': { tree: TREE } },
    notes: { 'turn-1': NOTES }, labels: { 'turn-1': LABELS }, label, onTab() {}, me: { user_id: 'me' } });
  assert.ok(!at({ traceId: 'turn-9', busy: null, error: 'Could not save.' }).includes('Could not save.'));
  assert.ok(!at({ traceId: 'turn-1', busy: null, error: 'Could not save.' }).includes('Could not save.'));
  assert.ok(!at({ traceId: 'turn-1', busy: 'm1', error: '' }).includes('>Present<'));
  assert.ok(html(P.FailureModes, { data: LABELS, error: 'Could not save.', onSet() {} }).includes('Could not save.'));
  assert.ok(/disabled=""[^>]*>Present</.test(html(P.FailureModes, { data: LABELS, busy: 'm1', onSet() {} })));
  const page = src('SessionPage.jsx');
  assert.ok(page.includes('labelBusy={label && label.traceId === current.id ? label.busy : null}'));
  assert.ok(page.includes("labelError={label && label.traceId === current.id ? label.error : ''}"));
});

test('an older link to a run or a question opens Runs, and a session is part of Runs\' address', () => {
  assert.equal(L.startView({ view: null, run: 'R3', question: 'R3-12' }), 'runs');
  assert.equal(L.startView({ view: null, run: null, question: null }), 'runs');
  assert.equal(L.startView({ view: 'labels', run: 'R3', question: null }), 'labels');
  assert.equal(L.placeQuery('runs', null, null, null), '');
  assert.equal(L.placeQuery('runs', 'R3', null, 'scn__a__SD-1__1'), 'view=runs&run=R3&session=scn__a__SD-1__1');
  assert.equal(L.placeQuery('runs', 'R3', 'R3-12', null), 'view=runs&run=R3&q=R3-12');
  assert.equal(L.startView({ view: null, run: 'R3', question: null }), 'runs', 'what placeQuery writes reads back the same');
});
