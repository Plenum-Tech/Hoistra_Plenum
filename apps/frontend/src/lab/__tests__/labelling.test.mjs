// labelling — an answer as labellable steps shown in full, each person's labels grouped by step (several per step, on
// selected text or the whole step), the label dialog, the collected table, the people table, and the access control on
// Hoistra's Users & access screen.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const Lg = await import('../logic/labelling.js');
const Q = await bundle('../lab/Questions.jsx');
const A = await bundle('../lab/AllLabels.jsx');
const P = await bundle('../lab/People.jsx');
const T = await bundle('../lab/TraceLabAccess.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const REC = { id: 't9', messages: [
  { id: 't9:root:user', role: 'user', depth: 0, text: 'Which vendor handled most work orders?' },
  { id: 't9:m1:call', role: 'model_call', depth: 1, label: 'tool decision',
    model_call: { model: 'gpt-x', usage: {}, status: 'ok', request: { q: 'vendors' }, response: { content: 'call the tool' } } },
  { id: 't9:q1:query', role: 'db_query', depth: 2, label: 'records: work_orders', row_count: 3, rows: [{ vendor: 'Northgate' }], ok: true,
    sql: 'SELECT vendor FROM work_orders' },
  { id: 't9:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: 'Northgate Electrical.' }] };
const L1 = { id: 'l1', lineage_id: 'g1', step_key: 't9:q1:query', label: 'wrong', note: 'Wrong table.', quote: 'Northgate', reviewer_id: 'me',
  reviewer_email: 'me@x.test', reviewer_role: 'tester', at: '2026-10-20T09:00:00Z', step_title: 'Records query', question: 'Which vendor?' };
const L2 = { id: 'l2', lineage_id: 'g2', step_key: 't9:q1:query', label: 'right', note: '', quote: null, reviewer_id: 'exp',
  reviewer_email: 'exp@x.test', reviewer_role: 'domain_expert', at: '2026-10-20T09:05:00Z', edited: true };
const L3 = Object.assign({}, L1, { id: 'l3', lineage_id: 'g3', label: 'unsure', quote: null });

test('an answer is the question, each model call and step, and the answer, in order, each with its own key', () => {
  const steps = Lg.stepsOf(REC);
  assert.deepEqual(steps.map((s) => [s.key, s.kind]), [['t9:root:user', 'user'], ['t9:m1:call', 'model'], ['t9:q1:query', 'db'],
    ['t9:root:answer', 'answer']]);
  assert.equal(steps[2].facts, '3 rows');
  assert.deepEqual(Lg.stepsOf(null), []);
});

// The shape of the real run "Which vendors are blocked right now?" (20 Oct 2026): a compliance agent holding two plan
// calls, a fetch stage and the analyst, and the orchestrator's own reads, two of which share a name but match on
// different columns.
const RUN = { id: 'tv', groups: [{ span_id: 'g1', name: 'compliance', latency_ms: 38312, cost_usd: null, child_count: 4 }], messages: [
  { id: 'tv:root:user', role: 'user', depth: 0, seq: 0, text: 'Which vendors are blocked right now?' },
  { id: 'tv:p1:call', role: 'model_call', depth: 1, seq: 2, group: 'compliance', parent_span_id: 'g1', label: 'plan', latency_ms: 38319,
    model_call: { usage: {}, status: 'ok', latency_s: 38.319, request: {}, response: {} } },
  { id: 'tv:p2:call', role: 'model_call', depth: 1, seq: 3, group: 'compliance', parent_span_id: 'g1', label: 'plan', latency_ms: 2031,
    model_call: { model: 'gpt-5.6-terra', usage: {}, status: 'ok', latency_s: 2.031, total_cost: 0.0033, request: {}, response: {} } },
  { id: 'tv:f:note', role: 'system_note', subtype: 'stage', depth: 1, seq: 4, group: 'compliance', parent_span_id: 'g1', name: 'fetch', label: 'fetch', latency_ms: 0 },
  { id: 'tv:c:query', role: 'db_query', depth: 1, seq: 5, parent_span_id: 'root', label: 'count', latency_ms: 43, row_count: 1, rows: [], sql: 'SELECT count(*) FROM x' },
  { id: 'tv:a:query', role: 'db_query', depth: 1, seq: 7, parent_span_id: 'root', label: "include linked_assets [the certificate's own asset]", latency_ms: 37, row_count: 0, rows: [],
    sql: 'SELECT * FROM c f JOIN "plenum_scoped"."assets" AS "j1" ON "f"."asset_code" = "j1"."asset_code"' },
  { id: 'tv:b:query', role: 'db_query', depth: 1, seq: 8, parent_span_id: 'root', label: "include linked_assets [the certificate's own asset]", latency_ms: 37, row_count: 0, rows: [],
    sql: 'SELECT * FROM c f JOIN "plenum_scoped"."assets" AS "j1" ON "f"."asset_id" = "j1"."id"' },
  { id: 'tv:an:call', role: 'model_call', depth: 1, seq: 24, group: 'compliance', parent_span_id: 'g1', label: 'analyst', latency_ms: 32709,
    model_call: { model: 'claude-sonnet-5', usage: {}, status: 'ok', latency_s: 32.709, total_cost: 0.087, request: {}, response: {} } },
  { id: 'tv:root:answer', role: 'assistant', depth: 0, seq: 0, subtype: 'answer', text: 'ProudCastle Fire is blocked.' }] };

test('every recorded step, in Run trace order and names: the agent and its steps under it, then the reads, each once', () => {
  const steps = Lg.stepsOf(RUN);
  assert.deepEqual(steps.map((st) => [st.n, st.kind, st.title, st.nested]), [
    [1, 'user', '', false],
    [2, 'agent', 'compliance', false],
    [3, 'model', 'plan', true],
    [4, 'model', 'plan', true],
    [5, 'note', 'fetch', true],
    [6, 'model', 'analyst', true],
    [7, 'db', 'count', false],
    [8, 'db', "include linked_assets [the certificate's own asset] · by asset_code", false],
    [9, 'db', "include linked_assets [the certificate's own asset] · by asset_id", false],
    [10, 'answer', '', false]]);
  assert.equal(new Set(steps.map((st) => st.key)).size, steps.length, 'no step twice');
  assert.equal(steps[1].key, 'tv:g1:agent');
  assert.deepEqual([steps[1].facts, steps[3].facts, steps[4].facts, steps[5].facts, steps[6].facts],
    ['38.3 s', 'gpt-5.6-terra · 2.03 s · $0.0033', '0 ms', 'claude-sonnet-5 · 32.7 s · $0.087', '43 ms · 1 row']);
  assert.equal(steps[1].text, 'The compliance agent ran 4 steps: plan, plan, fetch, analyst.');
  const out = html(Q.Flow, { conv: { active: RUN, turns: [{ turn_id: 'tv' }] }, steps, byStep: {}, busy: false, errors: {},
    onTurn() {}, onSelect() {}, onWhole() {}, onEdit() {}, onRemove() {}, startOpen: true });
  assert.match(out, /class="lab-step lab-step-agent"/);
  assert.equal((out.match(/lab-step-nested/g) || []).length, 4);
  assert.ok(out.includes('The compliance agent ran 4 steps'));
});

test('labels group by step, several per person, the caller\'s first, each with its selected text', () => {
  const by = Lg.labelsByStep([L2, L1, L3, Object.assign({}, L1, { id: 'x', step_key: 'other', cleared: true })], 'me');
  assert.deepEqual(by['t9:q1:query'].map((o) => [o.mine, o.labelText, o.quote, o.lineage]),
    [[true, 'Wrong', 'Northgate', 'g1'], [true, 'Not sure', '', 'g3'], [false, 'Right', '', 'g2']]);
  assert.equal(by.other, undefined);
  assert.equal(Lg.progressLine(Lg.stepsOf(REC), by), '1 of 4 steps labelled by you');
});

test('a selection counts only when it starts and ends inside the step', () => {
  const inside = { contains: (n) => n === 'a' || n === 'b' };
  const sel = (a, f, t, collapsed) => ({ isCollapsed: !!collapsed, rangeCount: 1, anchorNode: a, focusNode: f, toString: () => t });
  assert.equal(Lg.selectedIn(inside, sel('a', 'b', '  Northgate  ')), 'Northgate');
  assert.equal(Lg.selectedIn(inside, sel('a', 'z', 'x')), '', 'reaches outside the step');
  assert.equal(Lg.selectedIn(inside, sel('a', 'a', '', true)), '');
  assert.equal(Lg.selectedIn(null, sel('a', 'b', 'x')), '');
});

test('the question and the answer are open, each step between them is one line that opens; a bar names the question', () => {
  const steps = Lg.stepsOf(REC);
  const props = { conv: { active: REC, turns: [{ turn_id: 't9' }] }, steps, byStep: Lg.labelsByStep([L1, L2], 'me'),
    busy: false, errors: {}, onTurn() {}, onSelect() {}, onWhole() {}, onEdit() {}, onRemove() {} };
  const shut = html(Q.Flow, props);
  assert.match(shut, /class="lab-flow-bar"><div class="lab-flow-q" title="Which vendor handled most work orders\?">/);
  assert.ok(shut.includes('Northgate Electrical.'), 'the final answer is open');
  assert.ok(!shut.includes('SELECT vendor FROM work_orders') && !shut.includes('call the tool'), 'the steps between start closed');
  assert.equal((shut.match(/aria-expanded="false"/g) || []).length, 2, 'the model call and the query open on a click');
  assert.ok(shut.includes('Open all steps') && shut.includes('select any text in an open step'));
  assert.equal((shut.match(/data-step="/g) || []).length, 2, 'only open steps take a selection');
  assert.equal((shut.match(/Label this whole step/g) || []).length, 4, 'a closed step can still be labelled whole');
  assert.ok(shut.includes('exp@x.test') && shut.includes('Domain expert') && shut.includes('Northgate'), 'labels show on a closed step');
  assert.ok(shut.includes('>Edit<') && shut.includes('>Remove<'), 'my own label can be edited and removed');

  const all = html(Q.Flow, Object.assign({}, props, { startOpen: true }));
  assert.ok(!all.includes('Show details') && all.includes('Close all steps'));
  assert.ok(all.includes('vendors') && all.includes('call the tool'), 'an opened model call shows what it was sent and returned');
  assert.ok(all.includes('SELECT vendor FROM work_orders'), 'an opened query shows its technical detail');
  assert.equal((all.match(/aria-expanded="true"/g) || []).length >= 2, true);
  assert.equal((all.match(/data-step="/g) || []).length, 4, 'every open step is selectable');
});

test('the label dialog: what is labelled, the selected text, three choices, a note, Save only with a choice', () => {
  const d = { stepN: 3, stepText: 'Database query · Records query', quote: 'Northgate', label: null, note: '', saving: false, error: '' };
  const out = html(Q.LabelDialog, { d, onPick() {}, onNote() {}, onSave() {}, onCancel() {} });
  assert.match(out, /role="dialog" aria-modal="true" aria-labelledby="lab-dialog-title"/);
  assert.ok(out.includes('Label the selected text') && out.includes('Step 3 · Database query · Records query') && out.includes('Northgate'));
  assert.match(out, /role="radiogroup" aria-label="Your label"/);
  assert.match(out, /<button[^>]*disabled=""[^>]*>Save label</);
  const whole = html(Q.LabelDialog, { d: Object.assign({}, d, { quote: '', label: 'right' }), onPick() {}, onNote() {}, onSave() {}, onCancel() {} });
  assert.ok(whole.includes('Label this step') && !whole.includes('lab-quote'));
  assert.match(whole, /role="radio" aria-checked="true"[^>]*>Right</);
});

test('all labels: one row per label with question and step, selected text, label, note, who and role, and Open', () => {
  const opened = [];
  const out = html(A.LabelsTable, { rows: [Object.assign({ conversation_key: 'c1', turn_id: 't9' }, L1), L2], onOpen: (x) => opened.push(x) });
  ['Which vendor?', 'Records query', '“Northgate”', 'Whole step', 'Wrong', 'Wrong table.', 'me@x.test', 'Tester', 'Domain expert · edited'].forEach((t) => assert.ok(out.includes(t), t));
  assert.equal((out.match(/>Open</g) || []).length, 1, 'Open only where the label names its conversation');
  assert.ok(!html(A.LabelsTable, { rows: [L1] }).includes('>Open<'), 'no Open without somewhere to go');
});

test('all labels: a long selected text or note shows three lines and "Show all"; a short one shows whole', () => {
  const long = 'Columns you may filter on: status, days_to_expiry, expiry_date, issue_date, cert_scope. '.repeat(4);
  const out = html(A.LabelsTable, { rows: [Object.assign({}, L1, { quote: long, note: 'Short note.' })] });
  assert.match(out, /<div class="lab-clamp"><span class="lab-quote-inline">/);
  assert.match(out, /aria-expanded="false">Show all \(\d+ characters\)</);
  assert.equal((out.match(/Show all/g) || []).length, 1, 'the short note is not folded');
  assert.ok(!html(A.LabelsTable, { rows: [L1] }).includes('Show all'));
});

test('all labels: tiles count every label and by label, the chosen one marked; nothing to list says why', () => {
  const tiles = html(A.LabelTiles, { total: 7, byLabel: { right: 4, wrong: 2 }, current: 'wrong', onPick() {} });
  assert.match(tiles, /role="radiogroup" aria-label="Show labels"/);
  assert.match(tiles, /aria-checked="false" class="lab-tile lab-tile-all"><span class="lab-tile-n">7<\/span><span class="lab-tile-text">All labels</);
  assert.match(tiles, /aria-checked="true" class="lab-tile lab-tile-wrong lab-on"><span class="lab-tile-n">2</);
  assert.match(tiles, /lab-tile-unsure"><span class="lab-tile-n">0</);
  const none = html(A.NoLabels, { filtered: false, everyone: true, onClear() {}, onQuestions() {} });
  assert.ok(none.includes('No labels yet') && none.includes('anyone in this company') && none.includes('Go to Questions') && !none.includes('Clear filters'));
  const miss = html(A.NoLabels, { filtered: true, everyone: false, onClear() {} });
  assert.ok(miss.includes('No labels match these filters') && miss.includes('Clear filters') && !miss.includes('Go to Questions'));
});

test('arriving from Open: the named step starts open and marked, the others closed', () => {
  const steps = Lg.stepsOf(REC);
  const out = html(Q.Flow, { conv: { active: REC, turns: [{ turn_id: 't9' }] }, steps, byStep: {}, busy: false, errors: {},
    onTurn() {}, onSelect() {}, onWhole() {}, onEdit() {}, onRemove() {}, focusStep: 't9:q1:query' });
  assert.match(out, /data-key="t9:q1:query" class="lab-step lab-step-db lab-step-focus"/);
  assert.ok(out.includes('SELECT vendor FROM work_orders') && !out.includes('call the tool'));
});

test('people: each person with a Trace Lab role and access to change', () => {
  const rows = [Lg.memberRow({ id: 'm1', user_id: 'u1', email: 'tester@x.test', role: 'tester', status: 'active', since: '2026-10-20T08:00:00Z' })];
  const out = html(P.PeopleTable, { rows, edits: {}, busy: {}, errors: {}, onEdit() {}, onSave() {} });
  ['tester@x.test', 'Domain expert', 'Tester', 'Active', 'No access'].forEach((t) => assert.ok(out.includes(t), t));
});

test('Hoistra Users & access: no access by default, access with a Trace Lab role once chosen, admins always in', () => {
  const none = html(T.AccessView, { admin: false, member: { status: 'none' }, loading: false, saving: false, error: '', onChange() {}, inputId: 'x' });
  assert.ok(none.includes('Trace Lab access') && none.includes('Cannot sign in to Trace Lab'));
  ['No access', 'Access as domain expert', 'Access as tester', 'Access as admin', 'Access as user'].forEach((t) => assert.ok(none.includes(t), t));
  const on = html(T.AccessView, { admin: false, member: { status: 'active', role: 'tester' }, loading: false, saving: false, error: '', onChange() {}, inputId: 'x' });
  assert.ok(on.includes('Can sign in to Trace Lab'));
  assert.equal(Lg.accessValue({ status: 'active', role: 'tester' }), 'tester');
  assert.equal(Lg.accessValue({ status: 'removed', role: 'tester' }), '');
  assert.ok(html(T.AccessView, { admin: true, inputId: 'x' }).includes('Company admins always have Trace Lab access'));
});

// ── 21 Oct 2026: what should happen and when, several steps labelled together, delete on All labels ──────────────────
const G1 = { id: 'g1r', lineage_id: 'g1r', group_id: 'grp1', step_key: 't9:q1:query', label: 'wrong', note: 'Stay in the company.',
  expected: 'Filter to this company\'s vendors', only_if: 'the question names a vendor', reviewer_id: 'me', reviewer_email: 'me@x.test',
  reviewer_role: 'platform', at: '2026-10-21T10:00:00Z', turn_id: 't9', conversation_key: 'c1', question: 'Which vendors are blocked?', step_title: 'count' };
const G2 = Object.assign({}, G1, { id: 'g2r', lineage_id: 'g2r', step_key: 't9:root:answer', expected: 'List only blocked vendors', only_if: null });

test('a group covers its steps by number, said as "Steps 3 and 4"', () => {
  const steps = Lg.stepsOf(REC);
  assert.deepEqual(Lg.groupsOf([G2, G1, L2], steps), { grp1: [3, 4] });
  assert.equal(Lg.stepsText([3, 4]), 'Steps 3 and 4');
  assert.equal(Lg.stepsText([2, 5, 9]), 'Steps 2, 5 and 9');
  assert.equal(Lg.stepsText([7]), 'Step 7');
  const v = Lg.labelView(G1, 'me');
  assert.deepEqual([v.expected, v.onlyIf, v.group], ['Filter to this company\'s vendors', 'the question names a vendor', 'grp1']);
});

test('the label dialog also asks what should happen in the step and when, both optional', () => {
  const d = { stepN: 3, stepText: 'Database query · count', quote: '', label: 'wrong', note: '', expected: 'Count this company only', onlyIf: '', saving: false, error: '' };
  const out = html(Q.LabelDialog, { d, onPick() {}, onNote() {}, onField() {}, onSave() {}, onCancel() {} });
  assert.ok(out.includes('What should happen in this step') && out.includes('Count this company only'));
  assert.ok(out.includes('>Only if<') && out.includes('e.g. the question names a vendor'));
});

test('several steps together: one label and note, and for each step what should happen and when', () => {
  const g = { items: [{ key: 'a', n: 8, text: 'Database query · count', expected: '', onlyIf: '' },
    { key: 'b', n: 9, text: 'Database query · focus', expected: 'Only certificates', onlyIf: 'a vendor is named' }], label: null, note: '', saving: false, error: '' };
  const out = html(Q.GroupDialog, { g, onPick() {}, onNote() {}, onItem() {}, onSave() {}, onCancel() {} });
  assert.ok(out.includes('Label 2 steps together') && out.includes('Steps 8 and 9: one label and note for all of them'));
  assert.equal((out.match(/What should happen in this step/g) || []).length, 2, 'per step');
  assert.ok(out.includes('Step 9 · Database query · focus') && out.includes('Only certificates') && out.includes('a vendor is named'));
  assert.match(out, /<button[^>]*disabled=""[^>]*>Save 2 labels</, 'a label must be chosen first');
});

test('choosing steps: a tick box on every step and a bar that labels the chosen ones together', () => {
  const steps = Lg.stepsOf(REC);
  const base = { conv: { active: REC, turns: [{ turn_id: 't9' }] }, steps, byStep: {}, busy: false, errors: {}, onTurn() {}, onSelect() {},
    onWhole() {}, onEdit() {}, onRemove() {}, onPickMode() {}, onPick() {}, onGroup() {}, groups: {} };
  const idle = html(Q.Flow, Object.assign({}, base, { picking: false, picked: {} }));
  assert.ok(idle.includes('Label several steps together') && !idle.includes('role="checkbox"'));
  const one = html(Q.Flow, Object.assign({}, base, { picking: true, picked: { 't9:q1:query': true } }));
  assert.equal((one.match(/role="checkbox"/g) || []).length, 4);
  assert.match(one, /role="checkbox" aria-checked="true" aria-label="Choose step 3"/);
  assert.match(one, /<button[^>]*disabled=""[^>]*>Choose at least 2 steps</);
  const two = html(Q.Flow, Object.assign({}, base, { picking: true, picked: { 't9:q1:query': true, 't9:m1:call': true } }));
  assert.ok(two.includes('Label 2 steps together') && two.includes('2 of up to 30 steps chosen'));
});

test('under a step: what should happen, when, and the steps it was labelled with; my group can be removed', () => {
  const steps = Lg.stepsOf(REC);
  const groups = Lg.groupsOf([G1, G2], steps);
  const out = html(Q.StepLabels, { items: [Lg.labelView(G1, 'me')], busy: false, onEdit() {}, onRemove() {}, groups, onRemoveGroup() {} });
  assert.ok(out.includes('Should: </strong>Filter to this company') && out.includes('Only if: </strong>the question names a vendor'));
  assert.ok(out.includes('Labelled together: Steps 3 and 4') && out.includes('>Remove the group<'));
  const theirs = html(Q.StepLabels, { items: [Lg.labelView(G1, 'someone-else')], busy: false, onEdit() {}, onRemove() {}, groups, onRemoveGroup() {} });
  assert.ok(!theirs.includes('Remove the group'));
});

test('all labels: one Note column also says what should happen and when; Delete only on my labels, a group alone or whole', () => {
  const base = { rows: [G1, Object.assign({}, L2, { turn_id: 't9' })], onOpen() {}, me: 'me', busy: false, onAsk() {}, onDelete() {} };
  const out = html(A.LabelsTable, Object.assign({}, base, { confirming: null }));
  assert.ok(!out.includes('>What should happen<'), 'no column of its own');
  assert.match(out, /Stay in the company\.[\s\S]*Should: <\/strong>Filter to this company[\s\S]*Only if: <\/strong>the question names a vendor/);
  assert.ok(out.includes('Labelled with other steps'));
  assert.equal((out.match(/>Delete</g) || []).length, 1, 'only on my own label');
  const asking = html(A.LabelsTable, Object.assign({}, base, { confirming: 'g1r' }));
  assert.ok(asking.includes('>This step<') && asking.includes('>Whole group<') && asking.includes('>Cancel<'));
  const single = html(A.LabelsTable, Object.assign({}, base, { rows: [Object.assign({}, L1, { turn_id: 't9' })], confirming: 'l1' }));
  assert.ok(single.includes('Delete it?') && single.includes('>Yes<') && single.includes('>No<') && !single.includes('Whole group'));
});
