// labelsViews — the screens of the 22 Oct 2026 feedback, rendered to markup: Labels (a run's questions x failure modes,
// the caller's own two-way toggle per cell, everyone else's values counted with who and when, the platform team's and
// consultant's column tools), Progress (review completion tiles, a card per failure mode with its trend over runs and
// example notes), Notes (free text only: no Right / Wrong / Not sure anywhere) and the platform team's consultants on People.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const LV = await bundle('../lab/LabelsView.jsx');
const PV = await bundle('../lab/ProgressView.jsx');
const N = await bundle('../lab/Notes.jsx');
const P = await bundle('../lab/People.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));
const count = (s, re) => (s.match(re) || []).length;

const MODES = [
  { lineage_id: 'm1', id: 'm1v2', name: 'wrong_company_scope', definition: 'The answer counts rows from another company.' },
  { lineage_id: 'm2', id: 'm2v1', name: 'stale_status', definition: 'The answer reports a status that has since changed.' }];
const MATRIX = {
  run: { code: 'R3', title: 'Compliance, week 42', kind: 'synthetic' },
  questions: [
    { id: 'q1', code: 'R3-1', question: 'Which vendors are blocked right now?', turn_ids: ['t-7f3a9c21d04e5b'] },
    { id: 'q2', code: 'R3-2', question: 'How many certificates expire this month?', turn_ids: [] }],
  modes: MODES,
  cells: {
    q1: { m1: [
      { value: 'positive', author_id: 'me', author_email: 'me@x.test', author_role: 'tester', at: '2026-10-22T09:00:00Z', mine: true },
      { value: 'positive', author_id: 'u2', author_email: 'exp@x.test', author_role: 'domain_expert', at: '2026-10-22T09:10:00Z', mine: false },
      { value: 'negative', author_id: 'u3', author_email: 'qa@x.test', author_role: 'tester', at: '2026-10-22T09:20:00Z', mine: false }] },
    q2: { m2: [
      { value: 'negative', author_id: 'u2', author_email: 'exp@x.test', author_role: 'domain_expert', at: '2026-10-22T09:30:00Z', mine: false }] } },
  last_updated: '2026-10-22T09:30:00Z', me: 'me' };
const TESTER = { user_id: 'me', email: 'me@x.test', role: 'tester', all_companies: false };
const CONSULTANT = { user_id: 'me', email: 'me@x.test', role: 'consultant', all_companies: true };
const matrix = (me, data) => html(LV.LabelsMatrix, { run: 'R3', data: data || MATRIX, me, onCell() {}, onOpenQuestion() {},
  onAddMode() {}, onEditMode() {}, onRetireMode() {} });

test('labels: one row per question (code, text, first answer), one column per failure mode with its definition', () => {
  const out = matrix(TESTER);
  assert.match(out, /<h1>Labels<\/h1>/);
  assert.ok(out.includes('Failure modes for R3 · Compliance, week 42. Positive means the failure is in the question’s answer; negative means it is not.'));
  assert.equal(count(out, /<button type="button" class="lab-lv-code"/g), 2, 'each question code opens it');
  assert.ok(out.includes('>R3-1<') && out.includes('>R3-2<'));
  assert.ok(out.includes('Which vendors are blocked right now?') && out.includes('How many certificates expire this month?'));
  assert.match(out, /title="t-7f3a9c21d04e5b">Answer t-7f3a9c21…</, 'the first answer, short, the whole id on hover');
  assert.ok(out.includes('No answer yet'));
  assert.match(out, /title="The answer counts rows from another company\.">wrong_company_scope</);
  assert.match(out, /title="The answer reports a status that has since changed\.">stale_status</);
  assert.ok(out.includes('Last updated '), 'when the table last changed');
  assert.ok(out.includes('You have labelled 1 of 4 cells'));
});

test('labels: my value is a two-way toggle pressed where I said so; everyone else is counted, with who and when on hover', () => {
  const out = matrix(TESTER);
  assert.equal(count(out, /role="group" aria-label="Your label for /g), 4, 'a toggle in every cell');
  assert.equal(count(out, /aria-pressed="true"/g), 1, 'only my one value is pressed');
  assert.match(out, /class="lab-lv-opt lab-lv-opt-positive lab-on" aria-pressed="true"[^>]*>positive</);
  assert.match(out, /<td class="lab-lv-cell lab-lv-pos">/, 'my positive cell wears the risk tint');
  assert.ok(out.includes('>1 positive<') && out.includes('>1 negative<'), 'the others on R3-1');
  assert.ok(out.includes('Disagree'), 'a split cell says so');
  assert.match(out, /title="You: positive, annotated at [^"]+\nexp@x\.test \(Domain expert\): positive, annotated at [^"]+\nqa@x\.test \(Tester\): negative, annotated at [^"]+"/);
  assert.equal(count(out, /Disagree/g), 1, 'R3-2 has one other person and no split');
});

test('labels: cell values come out of the newest cells, and a click is applied (or undone) on a copy', () => {
  const c = LV.cellView(MATRIX.cells.q1.m1, 'me');
  assert.deepEqual([c.mine, c.pos, c.neg, c.others, c.disagree], ['positive', 1, 1, 2, true]);
  assert.deepEqual(LV.cellView(undefined, 'me'), { mine: null, mineAt: null, pos: 0, neg: 0, others: 0, disagree: false, who: '' });
  const cleared = LV.withMine(MATRIX, 'q1', 'm1', 'me', null);
  assert.equal(LV.cellView(cleared.cells.q1.m1, 'me').mine, null);
  assert.equal(cleared.cells.q1.m1.length, 2, 'the others stay');
  assert.equal(MATRIX.cells.q1.m1.length, 3, 'the data read from the service is not changed');
  const set = LV.withMine(MATRIX, 'q2', 'm1', 'me', { value: 'negative', author_id: 'me', mine: true });
  assert.equal(LV.cellView(set.cells.q2.m1, 'me').mine, 'negative');
  assert.equal(set.cells.q2.m2, MATRIX.cells.q2.m2, 'the other cells are the same objects');
  const back = LV.withMine(set, 'q2', 'm1', 'me', null);
  assert.deepEqual(back.cells.q2.m1, []);
  assert.equal(LV.withMine(null, 'q1', 'm1', 'me', null), null);
});

test('labels: the platform team and the consultant add, edit and retire failure modes; everyone else only labels', () => {
  const wide = matrix(CONSULTANT);
  assert.ok(wide.includes('>Add failure mode<'));
  assert.equal(count(wide, />Edit</g), 2);
  assert.equal(count(wide, />Retire</g), 2);
  const own = matrix(TESTER);
  assert.ok(!own.includes('Add failure mode') && !own.includes('>Edit<') && !own.includes('>Retire<'));
  assert.equal(count(own, /role="group" aria-label="Your label for /g), 4, 'but still sets their own cells');
});

test('labels: no run asks for one; no failure modes says where they come from, with the form for who may add them', () => {
  assert.ok(html(LV.LabelsMatrix, { run: null, me: TESTER }).includes('Pick a run'));
  const bare = Object.assign({}, MATRIX, { modes: [], cells: {}, last_updated: null });
  const own = matrix(TESTER, bare);
  assert.ok(own.includes('No failure modes yet') && own.includes('coding agent’s taxonomy'));
  assert.ok(!own.includes('Add failure mode') && !own.includes('<table'));
  const wide = matrix(CONSULTANT, bare);
  assert.ok(wide.includes('coding agent’s taxonomy') && wide.includes('>Add failure mode<'));
  assert.match(wide, /<input[^>]*maxLength="120"/, 'the name field is open straight away');
  assert.ok(!wide.includes('>Cancel<'), 'nothing to cancel back to');
  assert.ok(html(LV.LabelsMatrix, { run: 'R3', me: TESTER, loading: true }).includes('Reading…'));
});

const PROG = {
  run: { code: 'R3', title: 'Compliance, week 42' },
  tiles: { questions: 12, reviewed: 8, traced: 10, modes: 2, cells_filled: 15, cells_total: 24, notes: 31 },
  modes: [
    { lineage_id: 'm2', name: 'stale_status', definition: null, positives: 0, negatives: 4, trend: [{ run: 'R3', positives: 0 }], examples: [] },
    { lineage_id: 'm1', name: 'wrong_company_scope', definition: 'The answer counts rows from another company.', positives: 2, negatives: 6,
      trend: [{ run: 'R1', positives: 3 }, { run: 'R2', positives: 5 }, { run: 'R3', positives: 2 }],
      examples: [{ note: 'Counted Northbridge rows for Ashgrove.', by: 'exp@x.test', at: '2026-10-22T09:10:00Z' }] }],
  runs: [{ code: 'R1', title: 'a' }, { code: 'R2', title: 'b' }, { code: 'R3', title: 'Compliance, week 42' }] };

test('progress: review completion tiles, then a card per failure mode with its count, trend over runs and examples', () => {
  const out = html(PV.ProgressBody, { data: PROG });
  assert.ok(out.includes('Review completion') && out.includes('R3 · Compliance, week 42'));
  assert.match(out, /lab-lv-tile-n">8<span class="lab-lv-tile-of"> \/ 12<\/span><\/span><span class="lab-lv-tile-text">reviewed questions</);
  assert.match(out, /lab-lv-tile-n">10<\/span><span class="lab-lv-tile-text">with answers</);
  assert.match(out, /lab-lv-tile-n">2<\/span><span class="lab-lv-tile-text">failure modes</);
  assert.match(out, /lab-lv-tile-n">15<span class="lab-lv-tile-of"> \/ 24<\/span><\/span><span class="lab-lv-tile-text">label cells</);
  assert.match(out, /lab-lv-tile-n">31<\/span><span class="lab-lv-tile-text">notes</);
  const names = [...out.matchAll(/class="lab-lv-mcard-name">([^<]*)</g)].map((m) => m[1].replace(/​/g, ''));
  assert.deepEqual(names, ['wrong_company_scope', 'stale_status'], 'the mode found most often first');
  assert.match(out, /lab-lv-mcard-n">2</);
  assert.ok(out.includes('negative 6') && out.includes('negative 4'));
  assert.ok(out.includes('aria-label="Positive questions by run: R1 3 · R2 5 · R3 2"'));
  assert.ok(out.includes('3 fewer than R2'), 'the movement since the run before');
  assert.ok(out.includes('Counted Northbridge rows for Ashgrove.') && out.includes('exp@x.test'));
  assert.ok(out.includes('No positive questions in R3.'), 'a mode nobody found says so');
  assert.equal(PV.moveOf([{ run: 'R1', positives: 1 }, { run: 'R2', positives: 4 }]).text, '3 more than R1');
  assert.equal(PV.moveOf([{ run: 'R1', positives: 2 }, { run: 'R2', positives: 2 }]).text, 'Same as R1');
  assert.equal(PV.moveOf([{ run: 'R1', positives: 2 }]), null);
});

test('progress: no run yet, no failure modes yet, and still reading each say so', () => {
  assert.ok(html(PV.ProgressBody, { data: { run: null, tiles: null, modes: [], runs: [] } }).includes('No runs yet'));
  const bare = html(PV.ProgressBody, { data: Object.assign({}, PROG, { modes: [] }) });
  assert.ok(bare.includes('No failure modes yet') && bare.includes('reviewed questions'));
  assert.ok(html(PV.ProgressBody, { data: null, loading: true }).includes('Reading…'));
  assert.ok(html(PV.ProgressBody, { data: null, error: 'The service did not answer.', onRetry() {} }).includes('Try again'));
});

const NOTE = { id: 'n1', lineage_id: 'n1', step_key: 't9:q1:query', step_title: 'Records query', step_kind: 'db', question: 'Which vendors are blocked?',
  quote: 'Northgate', note: 'Counted another company.', expected: 'Filter to this company', only_if: 'a vendor is named', reviewer_id: 'me',
  reviewer_email: 'me@x.test', reviewer_role: 'consultant', at: '2026-10-22T09:00:00Z', turn_id: 't9', conversation_key: 'c1', group_id: null,
  label: 'wrong', question_code: 'R3-1' };
const THEIRS = Object.assign({}, NOTE, { id: 'n2', lineage_id: 'n2', reviewer_id: 'u2', reviewer_email: 'exp@x.test', reviewer_role: 'domain_expert',
  label: 'right', question_code: null, quote: null, expected: null, only_if: null, note: 'Fine as it is.', edited: true });
const notes = (extra) => html(N.NotesTable, Object.assign({ rows: [NOTE, THEIRS], myId: 'me', confirming: null, busy: false, onAsk() {},
  onDelete() {}, onOpenQuestion() {} }, extra || {}));

test('notes: free text only, never Right / Wrong / Not sure; annotated at, question code and step, selected text, note, who', () => {
  const out = notes();
  assert.ok(!/Right|Wrong|Not sure|>Label</.test(out), 'no label anywhere');
  assert.deepEqual([...out.matchAll(/<th scope="col">([^<]*)<\/th>/g)].map((m) => m[1]),
    ['Annotated at', 'Question and step', 'Selected text', 'Note', 'Who', '']);
  assert.match(out, /<span class="lab-lv-code">R3-1<\/span>/);
  assert.ok(out.includes('Which vendors are blocked?') && out.includes('Records query'));
  assert.ok(out.includes('“Northgate”') && out.includes('Whole step'));
  assert.match(out, /Counted another company\.[\s\S]*Should: <\/strong>Filter to this company[\s\S]*Only if: <\/strong>a vendor is named/);
  assert.ok(out.includes('me@x.test') && out.includes('Consultant') && out.includes('Domain expert · edited'));
});

test('notes: Open only where the note names its question; Delete only on my notes, a group alone or whole', () => {
  const opened = [];
  const out = notes({ onOpenQuestion: (code) => opened.push(code) });
  assert.equal(count(out, />Open</g), 1, 'only R3-1 has a question code');
  assert.ok(out.includes('aria-label="Open question R3-1"'));
  assert.equal(count(out, />Delete</g), 1, 'only my own note');
  const grouped = Object.assign({}, NOTE, { group_id: 'grp1' });
  const asking = notes({ rows: [grouped], confirming: 'n1' });
  assert.ok(asking.includes('>This step<') && asking.includes('>Whole group<') && asking.includes('>Cancel<'));
  assert.ok(asking.includes('Written with other steps'));
  const single = notes({ rows: [NOTE], confirming: 'n1' });
  assert.ok(single.includes('Delete it?') && single.includes('>Yes<') && single.includes('>No<') && !single.includes('Whole group'));
});

test('notes page: titled Notes, filters without a label choice (roles include Consultant and Platform team), a CSV', () => {
  const lead = html(N.default, { me: { user_id: 'me', role: 'admin', manages: true }, onOpenQuestion() {} });
  assert.match(lead, /<h1>Notes<\/h1>/);
  assert.ok(lead.includes('Every note, with who wrote it and when.'));
  assert.ok(lead.includes('>Person<') && lead.includes('>Role<') && lead.includes('>Words<') && lead.includes('>From<') && lead.includes('>To<'));
  assert.ok(lead.includes('<option value="consultant">Consultant</option>') && lead.includes('<option value="platform">Platform team</option>'));
  assert.ok(lead.includes('Download CSV'));
  assert.ok(!/Right|Wrong|Not sure|All labels|lab-tile/.test(lead), 'no tiles and no label filter');
  const own = html(N.default, { me: { user_id: 'me', role: 'tester', manages: false }, onOpenQuestion() {} });
  assert.ok(own.includes('Your notes, with when you wrote them.') && !own.includes('>Person<'));
  const none = html(N.NoNotes, { filtered: false, everyone: true, onClear() {} });
  assert.ok(none.includes('No notes yet') && !none.includes('Clear filters'));
  assert.ok(html(N.NoNotes, { filtered: true, everyone: false, onClear() {} }).includes('Clear filters'));
});

test('people: the platform team alone sees platform-wide access, with consultants, their status, and Remove', () => {
  const plat = html(P.default, { me: { role: 'platform', manages: true, all_companies: true } });
  assert.ok(plat.includes('Platform-wide access') && plat.includes('Give consultant access'));
  assert.ok(plat.includes('A consultant sees every company’s runs, can annotate and label, but cannot manage people. Create their Hoistra account first.'));
  ['admin', 'consultant', 'tester'].forEach((role) => {
    const out = html(P.default, { me: { role, manages: role === 'admin', all_companies: role === 'consultant' } });
    assert.ok(!out.includes('Platform-wide access') && !out.includes('Give consultant access'), role);
  });
  assert.ok(!html(P.default).includes('Platform-wide access'), 'nobody known yet');
  const rows = [
    { id: 'p1', user_id: 'u5', email: 'consultant@x.test', role: 'consultant', status: 'active', decided_by_email: 'ops@hoistra.test', since: '2026-10-22T08:00:00Z' },
    { id: 'p2', user_id: 'u6', email: 'former@x.test', role: 'consultant', status: 'removed', decided_by_email: null, since: '2026-10-21T08:00:00Z' }];
  const table = html(P.PlatformAccess, { rows, busy: {}, giving: false, error: '', notice: '', onGive() {}, onRemove() {} });
  assert.ok(table.includes('consultant@x.test') && table.includes('former@x.test') && table.includes('by ops@hoistra.test'));
  assert.equal(count(table, />Consultant</g), 2);
  assert.ok(table.includes('>Active<') && table.includes('>Removed<'));
  assert.equal(count(table, />Remove</g), 1, 'only an active consultant can be removed');
  assert.match(table, /<input type="email"[^>]*placeholder="name@company\.com"/);
  assert.ok(html(P.PlatformAccess, { rows: [], busy: {} }).includes('No consultants yet.'));
});
