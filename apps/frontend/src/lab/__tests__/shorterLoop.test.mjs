// A shorter correction loop (Bala, 6 Nov 2026): ask -> annotate -> the agent proposes -> approve (the rule goes into
// SPEC.md and the labels are accepted in the same click) -> add the expected answer on the Loop page -> the agent drafts
// the fix -> approve for testing -> Test the fix (regression run + ask again + compare in one click) -> make live. After
// every step a "Next step" pop-up says what to do next and offers a button that does it.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const LP = await bundle('../lab/Loop.jsx');
const NS = await bundle('../lab/NextStep.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

test('every step says what comes next, with a button that does it', () => {
  const s = (event, data) => NS.nextStepFor(event, data);
  assert.equal(s('note_saved').act, 'open_loop');
  assert.equal(s('proposed', { proposed_modes: [{ name: 'x' }], labels: 1, notes_read: 1 }).act, 'show_waiting');
  assert.equal(s('proposed', { proposed_modes: [], labels: 1, notes_read: 1 }).act, 'show_waiting');
  assert.equal(s('proposed', { proposed_modes: [], labels: 0, notes_read: 0, message: 'No new notes.' }).act, null);
  const approved = s('mode_approved', { spec: { code: 'S-10' }, labels_accepted: 1, published: 'testing' });
  assert.ok(approved.title.includes('S-10') && approved.act === 'expected');
  assert.equal(s('label_accepted', { hasRule: true }).act, 'expected');
  assert.equal(s('label_accepted', { hasRule: false }).act, 'add_rule');
  assert.equal(s('rule_added', { code: 'S-11' }).act, 'expected');
  assert.equal(s('expected_saved').act, 'draft_fix');
  assert.equal(s('fix_drafted', { path: 'docs/compliance/planner.md' }).act, 'show_changes');
  assert.equal(s('change_approved').act, 'test_fix');
  assert.equal(s('tested', { fixed: 1, still_failing: 0, regressed: 0, no_answer: 0 }).act, 'show_changes');
  assert.equal(s('tested', { fixed: 0, still_failing: 0, regressed: 0, no_answer: 1 }).act, 'test_fix');
  assert.equal(s('tested', { fixed: 0, still_failing: 1, regressed: 0, no_answer: 0 }).act, 'show_compare');
  assert.equal(s('made_live').act, 'export');
});

test('the pop-up shows the step and its button, and Later closes it', () => {
  const out = html(NS.NextStep, { step: { title: 'Rule S-10 added to SPEC.md', text: 'Next: add the expected answer.', actionLabel: 'Add expected answer', act: 'expected' },
    onAct() {}, onClose() {} });
  assert.ok(out.includes('role="dialog"') && out.includes('Next step') && out.includes('Rule S-10 added to SPEC.md'));
  assert.ok(out.includes('Add expected answer') && out.includes('Later'));
  assert.equal(html(NS.NextStep, { step: null }), '');
  const info = html(NS.NextStep, { step: { title: 'Nothing new to propose', act: null }, onClose() {} });
  assert.ok(info.includes('OK') && !info.includes('Later'));
});

test('a suggested label on a confirmed mode is accepted from Waiting, and approving a mode says what it does', () => {
  const data = { modes: [{ id: 'p1', lineage_id: 'p1', kind: 'new', name: 'stale_status_field_trusted', definition: 'Trusts a stored status.' }],
    labels: [{ id: 'c1', question: 'R44-2', question_text: 'Which certificates are expiring soon?', mode: 'm1', mode_name: 'grouping_read_as_filter' }],
    spec: [], expected: [], can_decide_modes: true, can_decide_expected: true };
  const out = html(LP.Waiting, { data, onDecide() {} });
  assert.ok(out.includes('Waiting for a decision (2)'));
  assert.ok(out.includes('R44-2') && out.includes('grouping_read_as_filter') && out.includes('Accept'));
  assert.ok(/its rule is added to SPEC\.md/i.test(out), 'the approver is told the rule and labels come with it');
  assert.equal(LP.waitingCount(data), 2);
});

test('a mode row: one-click rule, its questions with the expected answer, and Test the fix', () => {
  const base = { lineage_id: 'm1', name: 'stale_status_field_trusted', positives: 1, corrections: 0, steps: { annotated: true }, next: 'spec',
    regression_runs: [], companies: ['org-1'],
    questions: [{ code: 'R44-2', text: 'Which certificates are expiring soon?', has_expected: false, company: 'org-1' }] };
  const norule = html(LP.ModeRow, { mode: Object.assign({}, base, { rule: null }), canTaxonomy: true, canCurate: true, onAddRule() {},
    onSaveExpected() {} });
  assert.ok(norule.includes('Add to SPEC.md'));
  assert.ok(norule.includes('R44-2') && norule.includes('Add expected answer'));
  const ruled = html(LP.ModeRow, { mode: Object.assign({}, base, { rule: { code: 'S-10' } }), canTaxonomy: true, canCurate: true,
    onAddRule() {}, onTestFix() {}, onSaveExpected() {} });
  assert.ok(!ruled.includes('Add to SPEC.md') && ruled.includes('Test the fix'));
  const done = html(LP.ModeRow, { mode: Object.assign({}, base, { rule: { code: 'S-10' },
    questions: [{ code: 'R44-2', text: 'q', has_expected: true }] }), canCurate: true, onSaveExpected() {} });
  assert.ok(done.includes('Expected answer ✓') && !done.includes('Add expected answer'));
  const open = html(LP.ModeRow, { mode: Object.assign({}, base, { rule: { code: 'S-10' } }), canCurate: true, onSaveExpected() {},
    openExpected: 'R44-2' });
  assert.ok(open.includes('Values to check') && open.includes('Company records it was read from') && open.includes('S-10'));
});

test('the expected answer from the Loop page names its rule, its records and its values', () => {
  const body = LP.expectedBody({ expected: ' 3 certificates. ', records: ' register ', values: 'Hamworthy Heating\n\n!2026-12-08 \n2026-11-19' }, 'S-10');
  assert.deepEqual(body, { expected: '3 certificates.', evidence: { spec_rule: 'S-10', data: 'register',
    values: ['Hamworthy Heating', '!2026-12-08', '2026-11-19'] } });
  assert.deepEqual(LP.expectedBody({ expected: 'x', records: 'y', values: '' }, 'S-1').evidence, { spec_rule: 'S-1', data: 'y' });
});

test('Test the fix makes a fresh regression run, asks it again and compares, in one go', async () => {
  const calls = [];
  const api = {
    makeRegression: async (lin, body) => { calls.push(['make', lin, body]); return { run: 'R46' }; },
    rerun: async (code, body) => { calls.push(['rerun', code, body.start]); return { asked: 1, next_start: null, total: 1, results: [{ ok: true }], under_test: ['docs/compliance/planner.md'] }; },
    compare: async (code) => { calls.push(['compare', code]); return { run: code, summary: { fixed: 1 } }; }
  };
  const out = await LP.testFix(api, { lineage_id: 'm1', companies: ['org-1'] }, { wide: true });
  assert.deepEqual(calls[0], ['make', 'm1', { space: 'full', company: 'org-1' }]);
  assert.deepEqual(calls.slice(1).map((c) => c[0]), ['rerun', 'compare']);
  assert.equal(out.compare.run, 'R46');
  await assert.rejects(LP.testFix(api, { lineage_id: 'm1', companies: ['a', 'b'] }, { wide: true }), /choose the company/i);
});

test('saving a note on a question pops up the next step: go to Loop', () => {
  const qp = readFileSync(new URL('../QuestionPage.jsx', import.meta.url), 'utf-8');
  assert.ok(qp.includes("nextStepFor('note_saved')") && qp.includes('<NextStep '), 'no pop-up after a note');
  const runsSrc = readFileSync(new URL('../Runs.jsx', import.meta.url), 'utf-8');
  assert.ok(runsSrc.includes('onOpenLoop={onOpenLoop}'));
  const app = readFileSync(new URL('../LabApp.jsx', import.meta.url), 'utf-8');
  assert.ok(/<RunsPage [^>]*onOpenLoop=\{\(\) => onView\('loop'\)\}/.test(app));
});
