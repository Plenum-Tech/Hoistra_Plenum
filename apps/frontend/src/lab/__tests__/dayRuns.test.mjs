// dayRuns — one live run per company per day, shown as one run per day, with who asked on every row (19 Nov 2026,
// Hussain: "on 17 nov i asked question through two accounts it should be stored in the same run as it was asked on the
// same date make some ui so i can differentiate btw it", then "it should stay as one run only"). The service lists a live
// day once (its first run's code, `codes` all of its runs, `people` who asked); the Runs card says so, the day's page has
// an "Asked by" column and a filter by person, and the top bar's picker has one option for the day.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const L = await bundle('../lab/LabApp.jsx');
const R = await bundle('../lab/Runs.jsx');
const SES = await bundle('../lab/Sessions.jsx');
const LP = await bundle('../lab/Loop.jsx');
const LV = await bundle('../lab/LabelsView.jsx');
const PV = await bundle('../lab/ProgressView.jsx');
const S = await import('../logic/sessions.js');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const ANN = 'ann@a.test';
const BOB = 'bob@a.test';
const DAY = { code: 'R60', title: 'ann@a.test · 2026-11-17', kind: 'production', source: 'sync', owner_email: ANN, run_day: '2026-11-17',
  codes: ['R60', 'R61'], people: [ANN, BOB], n_questions: 5, n_traced: 5, n_expected: 0, n_notes: 0, n_reviewed: 0 };
const ONE = { code: 'R62', title: 'Live questions · 2026-11-18', kind: 'production', source: 'sync', run_day: '2026-11-18', codes: ['R62'],
  people: [ANN], n_questions: 1 };
const row = (id, askers, extra) => Object.assign({ id, run_tag: 'chat', scenario_id: null, attempt: 1, origin: 'hoistra-prod', source: 'chat',
  type: null, turns: 1, status: 'ok', started_at: '2026-11-17T09:00:00.000Z', stale: false, expected_flagged: 0, scores: {},
  codes: ['R60-1'], turn_ids: ['t1'], askers, asked_by: askers[0] || null, organization_id: 'org-a' }, extra || {});
const A_ROW = row('11111111-aaaa-4aaa-8aaa-111111111111', [ANN], { first_question: 'Which vendors are blocked?' });
const B_ROW = row('22222222-bbbb-4bbb-8bbb-222222222222', [BOB], { first_question: 'How many work orders?', codes: ['R61-1'] });
const B_WAIT = { code: 'R61-2', question: 'And which are lapsed?', asked_at: '2026-11-17T11:00:00.000Z', turn_id: 'tb2', asked_by: BOB, organization_id: 'org-a' };
const A_WAIT = { code: 'R60-3', question: 'What is the EPC rating?', asked_at: '2026-11-17T12:00:00.000Z', turn_id: 'ta3', asked_by: ANN, organization_id: 'org-a' };

test('a live day card: the title is the day, who asked, and every run it is made of', () => {
  const v = R.runView(DAY);
  assert.equal(v.title, 'Live questions · 2026-11-17', 'old per-person runs are titled by the day too');
  assert.equal(v.kindText, 'live · 17 Nov 2026');
  assert.equal(v.asked, 'Asked by: ann@a.test, bob@a.test');
  assert.equal(v.runsText, 'Runs R60 + R61');
  const one = R.runView(ONE);
  assert.equal(one.title, 'Live questions · 2026-11-18');
  assert.equal(one.asked, 'Asked by: ann@a.test');
  assert.equal(one.runsText, '', 'one run: nothing to say');
  const old = R.runView({ code: 'R7', title: 'ana@x.test · 2026-10-21', kind: 'production', owner_email: 'ana@x.test', run_day: '2026-10-21' });
  assert.equal(old.asked, 'Asked by: ana@x.test', 'a list without people still says whose the old run is');
  assert.equal(old.runsText, '');
  const synth = R.runView({ code: 'R3', title: 'Certificates batch', kind: 'synthetic', source: 'excel', n_questions: 2 });
  assert.equal(synth.title, 'Certificates batch');
  assert.equal(synth.asked, '');
  assert.equal(synth.runsText, '');
});

test('the Runs list draws the day once with Asked by and Runs lines', () => {
  const out = html(R.RunsList, { runs: [DAY, ONE], onOpen() {} });
  ['Live questions · 2026-11-17', 'Asked by: ann@a.test, bob@a.test', 'Runs R60 + R61', '5 questions', '>R60<', 'Live questions · 2026-11-18']
    .forEach((t) => assert.ok(out.includes(t), t));
  assert.ok(!out.includes('ann@a.test · 2026-11-17'), 'the old per-person title is not drawn');
  assert.equal((out.match(/Runs R/g) || []).length, 1, 'only the day with several runs says it');
  assert.ok(html(R.RunsList, { runs: [], loading: false, curator: true }).includes('one run per day'));
});

test('the top bar has one option for the day, named by its first run, and picks it for any run of the day', () => {
  assert.equal(R.runLabel(DAY), 'R60 · Live questions · 2026-11-17 · 5 questions');
  assert.equal(R.runLabel(ONE), 'R62 · Live questions · 2026-11-18 · 1 question');
  assert.equal(R.runEntry([DAY, ONE], 'R61').code, 'R60', 'R61 is a run of the day that R60 stands for');
  assert.equal(R.runEntry([DAY, ONE], 'R62').code, 'R62');
  assert.equal(R.runEntry([DAY, ONE], 'R99'), null);
  const bar = html(L.RunPicker, { runs: [DAY, ONE], run: 'R61', onRun() {}, onSync() {} });
  assert.equal((bar.match(/<option/g) || []).length, 3, 'All runs and one option per day');
  assert.match(bar, /<option value="R60" selected="">/, 'the day is selected when R61 is open');
});

test('a row of a live day says who asked it, and the company when the day spans companies', () => {
  const r = S.sessionRow(A_ROW);
  assert.equal(r.askedBy, ANN);
  assert.deepEqual(r.askers, [ANN]);
  assert.equal(S.sessionRow(Object.assign({}, A_ROW, { askers: [ANN, BOB], asked_by: ANN })).askedBy, 'ann@a.test, bob@a.test');
  assert.equal(S.sessionRow({ id: 'x', asked_by: BOB }).askedBy, BOB, 'a row with only asked_by');
  assert.equal(S.sessionRow({ id: 'y' }).askedBy, '', 'a session no one is known to have asked');
  const one = html(SES.SessionsTable, { rows: [A_ROW, B_ROW], waiting: [B_WAIT], asker: true, onOpen() {} });
  assert.ok(one.includes('<th scope="col">Asked by</th>'), 'the column');
  [ANN, BOB].forEach((e) => assert.ok(one.includes(e), e));
  assert.ok(one.indexOf('<th scope="col">Asked by</th>') < one.indexOf('<th scope="col">Run tag</th>'));
  assert.ok(!one.includes('Alpha Estates'), 'no company when the day is one company');
  const two = html(SES.SessionsTable, { rows: [A_ROW, row('33333333-cccc-4ccc-8ccc-333333333333', ['cat@b.test'], { organization_id: 'org-b' })],
    waiting: [], asker: true, companies: [{ id: 'org-a', name: 'Alpha Estates' }, { id: 'org-b', name: 'Bravo Property' }], onOpen() {} });
  ['Alpha Estates', 'Bravo Property', 'cat@b.test'].forEach((t) => assert.ok(two.includes(t), t));
  const plain = html(SES.SessionsTable, { rows: [A_ROW], onOpen() {} });
  assert.ok(!plain.includes('Asked by'), 'other runs keep their table');
});

test('the people of a day and the filter by person', () => {
  assert.deepEqual(S.peopleOf(DAY, [A_ROW, B_ROW], [B_WAIT]), [ANN, BOB]);
  assert.deepEqual(S.peopleOf(null, [A_ROW, B_ROW], [B_WAIT]), [ANN, BOB], 'from the rows when the run says none');
  assert.deepEqual(S.peopleOf({ people: ['Zed@x.test', 'amy@x.test'] }, [], []), ['amy@x.test', 'Zed@x.test'], 'sorted, ignoring case');
  const all = S.filterByPerson([A_ROW, B_ROW], [B_WAIT, A_WAIT], '');
  assert.equal(all.sessions.length, 2); assert.equal(all.waiting.length, 2);
  const bob = S.filterByPerson([A_ROW, B_ROW], [B_WAIT, A_WAIT], BOB);
  assert.deepEqual(bob.sessions.map((s) => s.id), [B_ROW.id]);
  assert.deepEqual(bob.waiting.map((w) => w.code), ['R61-2']);
  const both = S.filterByPerson([Object.assign({}, A_ROW, { askers: [ANN, BOB] })], [], BOB);
  assert.equal(both.sessions.length, 1, 'a conversation both asked in is theirs too');
  assert.equal(S.filterByPerson(null, null, BOB).sessions.length, 0);
  assert.equal(S.personCounts([A_ROW], [A_WAIT, B_WAIT]), '1 conversation · 2 reading in');
  assert.equal(S.personCounts([A_ROW, B_ROW], []), '2 conversations');
  assert.equal(S.personCounts([], []), 'No conversations');
});

test('a day with two people has an Everyone / person filter that narrows the rows and the counts line', () => {
  const props = { run: DAY, mode: 'live', sessions: [A_ROW, B_ROW], waiting: [B_WAIT, A_WAIT], onOpen() {}, onBack() {} };
  const out = html(R.RunConversationsList, props);
  assert.ok(out.includes('role="radiogroup" aria-label="Asked by"'));
  assert.match(out, /aria-checked="true"[^>]*>Everyone</);
  [ANN, BOB].forEach((e) => assert.ok(new RegExp('role="radio"[^>]*>' + e.replace('.', '\\.') + '<').test(out), e));
  assert.ok(out.includes('Asked by: ann@a.test, bob@a.test') && out.includes('Runs R60 + R61'), 'the header says who and which runs');
  assert.ok(out.includes('5 questions · 5 with answers'), 'Everyone: the run\'s own counts');
  assert.equal((out.match(/class="lab-link lab-ses-open"/g) || []).length, 2);
  const bob = html(R.RunConversationsList, Object.assign({}, props, { person: BOB }));
  assert.match(bob, /aria-checked="true"[^>]*>bob@a\.test</);
  assert.equal((bob.match(/class="lab-link lab-ses-open"/g) || []).length, 1, 'only bob\'s conversation');
  assert.ok(bob.includes('Which vendors') === false && bob.includes('How many work orders?'));
  assert.ok(bob.includes('And which are lapsed?') && !bob.includes('What is the EPC rating?'), 'bob\'s waiting row only');
  assert.ok(bob.includes('1 conversation · 1 reading in') && !bob.includes('5 questions · 5 with answers'), 'the counts follow the filter');
  const none = html(R.RunConversationsList, Object.assign({}, props, { run: ONE, sessions: [A_ROW], waiting: [] }));
  assert.ok(!none.includes('aria-label="Asked by"'), 'one person: nothing to filter');
  assert.ok(none.includes('<th scope="col">Asked by</th>'), 'but the column still says who');
  const ses = html(R.RunConversationsList, { run: { code: 'R47', title: 'Sessions · x', kind: 'synthetic', source: 'sync', run_tag: 'x' }, mode: 'sessions',
    sessions: [A_ROW], waiting: [], onOpen() {}, onBack() {} });
  assert.ok(!ses.includes('Asked by'), 'a sessions run has no asker column');
});

test('choosing a person in the filter reports the person, and Everyone clears it', () => {
  const got = [];
  const el = R.RunConversationsList({ run: DAY, mode: 'live', sessions: [A_ROW, B_ROW], waiting: [], person: '', onPerson: (p) => got.push(p), onOpen() {}, onBack() {} });
  const buttons = [];
  (function walk(n) {
    if (!n || typeof n !== 'object') return;
    if (Array.isArray(n)) { n.forEach(walk); return; }
    if (n.props && n.props.role === 'radio') buttons.push(n);
    if (n.props) walk(n.props.children);
  })(el);
  assert.deepEqual(buttons.map((b) => b.props.children), ['Everyone', ANN, BOB]);
  buttons[2].props.onClick(); buttons[0].props.onClick();
  assert.deepEqual(got, [BOB, '']);
});

test('the Loop\'s companies still come from the runs in view when a day merges several companies', () => {
  const day = { code: 'R1', title: 'Live questions · 2026-11-17', kind: 'production', organization_id: 'o1', organization_ids: ['o1', 'o2'],
    people: ['ann@northbridge.test', 'cat@bravo.test'], people_by_company: { o1: ['ann@northbridge.test'], o2: ['cat@bravo.test'] } };
  const own = { code: 'R5', title: 'Live questions · 2026-11-18', kind: 'production', organization_id: 'o1', organization_ids: ['o1'],
    people: ['ann@northbridge.test'], people_by_company: { o1: ['ann@northbridge.test'] } };
  assert.deepEqual(LP.companiesOf([day, own]), [{ id: 'o1', label: 'northbridge.test' }, { id: 'o2', label: 'bravo.test' }]);
  assert.deepEqual(LP.companiesOf([{ organization_id: 'o3', organization_ids: ['o3'], title: 'Live questions · 2026-11-17', people: [] }]),
    [{ id: 'o3', label: 'Live questions · 2026-11-17' }], 'a day nobody is known to have asked in keeps its title');
});

const MODE = { lineage_id: 'm1', id: 'm1v1', name: 'wrong_company_scope', definition: null };
test('Labels and Progress title a live day by its day, whatever its first run was titled', () => {
  assert.equal(R.runTitle(DAY), 'Live questions · 2026-11-17');
  assert.equal(R.runTitle({ kind: 'synthetic', title: 'Certificates batch', run_day: '2026-11-17' }), 'Certificates batch');
  const grid = { all: true, run: null, data: { runs: [{ run: DAY, questions: [{ id: 'q1', code: 'R60-1', question: 'Which vendors are blocked?', turn_ids: ['t1'] }] }],
    modes: [MODE], cells: {}, total: 1, more: false }, me: { user_id: 'me', role: 'tester' }, onCell() {}, onOpenQuestion() {}, onOpenTurn() {} };
  const labels = html(LV.LabelsMatrix, grid);
  assert.ok(labels.includes('R60 · Live questions · 2026-11-17') && !labels.includes('ann@a.test · 2026-11-17'), labels);
  const one = html(LV.LabelsMatrix, { ...grid, all: false, run: 'R60', data: { run: DAY, questions: grid.data.runs[0].questions, modes: [MODE], cells: {}, total: 1, more: false } });
  assert.ok(one.includes('Failure modes for R60 · Live questions · 2026-11-17'), one);
  const prog = html(PV.ProgressBody, { data: { run: DAY, tiles: { questions: 5, reviewed: 0, traced: 5, modes: 0, cells_filled: 0, cells_total: 0, notes: 0 }, modes: [], runs: [] } });
  assert.ok(prog.includes('R60 · Live questions · 2026-11-17'), prog);
});
