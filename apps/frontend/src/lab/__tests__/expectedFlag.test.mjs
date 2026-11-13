// expectedFlag — a reviewer flags a turn's expected outcome as wrong while reviewing (12 Nov 2026). The turn is then
// not counted against Hoistra: not scored, left out of compare, and the scenario shows it needs correcting.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';
import { CHANGE_TEXT, compareView, scenarioRow, sessionRow, turnTabs } from '../logic/sessions.js';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const P = await bundle('../lab/SessionPage.jsx');
const S = await bundle('../lab/Sessions.jsx');
const N = await bundle('../lab/Scenarios.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

test('a turn whose expected outcome is flagged is marked on its tab', () => {
  const tabs = turnTabs([{ id: 't2', turn_index: 2, status: 'ok', expected_flag: { flagged: true, count: 1, mine: false } },
    { id: 't1', turn_index: 1, status: 'ok', expected_flag: { flagged: false, count: 0, mine: false } }]);
  assert.deepEqual(tabs.map((t) => [t.label, t.flagged]), [['Turn 1', false], ['Turn 2', true]]);
});

test('the flag box: offer to flag, then say who flagged it and why, the reader first', () => {
  const none = html(P.ExpectedFlag, { data: { flagged: false, mine: false, flags: [] }, onSet() {} });
  assert.match(none, /<button[^>]*>Expected outcome is wrong<\/button>/);
  assert.ok(none.includes('Is the expected outcome for this turn wrong?'));
  const theirs = html(P.ExpectedFlag, { data: { flagged: true, mine: false, flags: [{ author_email: 'sme@example.com', comment: 'Drafts belong in the list.', mine: false }] }, onSet() {} });
  assert.ok(theirs.includes('sme@example.com') && theirs.includes('Drafts belong in the list.'));
  assert.ok(theirs.includes('not counted against Hoistra'));
  assert.match(theirs, /<button[^>]*>Expected outcome is wrong<\/button>/, 'another reviewer can add their own flag');
  const mine = html(P.ExpectedFlag, { data: { flagged: true, mine: true, flags: [{ author_email: 'me@example.com', comment: 'Wrong band.', mine: true }] }, onSet() {} });
  assert.ok(mine.includes('You flagged') && mine.includes('Wrong band.'));
  assert.match(mine, /<button[^>]*>Clear my flag<\/button>/);
  assert.ok(html(P.ExpectedFlag, { data: null, onSet() {} }).includes('Reading'));
});

test('a session page turn carries the flag box', () => {
  const data = { session: { id: 's1' }, scenario: { turns: [{ message: 'q', expected: { answer: 'a' } }] }, traces: [{ id: 'turn-1', turn_index: 1, input: 'q', output: 'a', status: 'ok', metadata: {}, scores: {} }] };
  const out = html(P.SessionBody, { data, active: 'turn-1', trees: {}, notes: {}, labels: {}, flags: { 'turn-1': { flagged: false, mine: false, flags: [] } }, onTab() {}, me: { user_id: 'me' } });
  assert.ok(out.includes('Is the expected outcome for this turn wrong?'));
});

test('the sessions list and the scenarios list say where expected outcomes are flagged', () => {
  assert.equal(sessionRow({ id: 's1', expected_flagged: 2 }).expectedFlagged, 2);
  const list = html(S.SessionsTable, { rows: [{ id: 's1', scenario_id: 'SD-001', run_tag: 'baseline_v0', turns: 2, status: 'ok', expected_flagged: 2, scores: {} }], onOpen() {} });
  assert.ok(list.includes('Expected outcome flagged on 2 turns'));
  assert.equal(scenarioRow({ scenario_id: 'SD-001', version: 1, turns: [], expected_flagged: 1 }).expectedFlagged, 1);
  const scen = html(N.ScenariosTable, { rows: [{ scenario_id: 'SD-001', version: 1, type: 'single_domain', spec_sections: [], status: 'draft', turns: [{ message: 'q' }], stale: [], expected_flagged: 1 }], selected: [], onToggle() {} });
  assert.ok(scen.includes('Expected outcome flagged as wrong on 1 turn: needs correcting'));
});

test('compare says when a scenario was not counted because its expected outcome is flagged', () => {
  assert.equal(CHANGE_TEXT.not_counted, 'Not counted (expected outcome flagged)');
  const v = compareView({ base: 'a', head: 'b', summary: { logic: { fixed: 1, regressed: 0, same: 0, changed: 0, new: 0, missing: 0, not_counted: 2 },
    ux: {}, domain: {}, sessions: { base: 3, head: 3 }, answer_words: {} }, rows: [] });
  assert.ok(v.summary[0].includes('2 not counted (expected outcome flagged)'), v.summary[0]);
});

// 13 Nov 2026 review: a turn with no expected outcome (a chat session, a turn the scenario has no turn for) is never
// offered the flag; one already flagged can still be cleared.
test('the flag is offered only on a turn that has an expected outcome', async () => {
  const { hasExpectedOutcome } = await import('../logic/sessions.js');
  const scenario = { scenario_id: 'EV-1', version: 1, turns: [{ message: 'Q', expected: { answer: 'A' } }] };
  assert.equal(hasExpectedOutcome(scenario, 1), true);
  assert.equal(hasExpectedOutcome(scenario, 2), false);
  assert.equal(hasExpectedOutcome(null, 1), false);
  const trace = { id: 't1', turn_index: 1, status: 'ok', expected_flag: { flagged: false, count: 0, mine: false } };
  const body = (sc, t) => html(P.SessionBody, { data: { session: { id: 's' }, scenario: sc, traces: [t] }, active: 't1', flags: {} });
  assert.ok(!body(null, trace).includes('Is the expected outcome for this turn wrong?'), 'a chat turn has no flag box');
  assert.ok(body(scenario, trace).includes('Is the expected outcome for this turn wrong?'));
  const flagged = Object.assign({}, trace, { expected_flag: { flagged: true, count: 1, mine: true } });
  assert.ok(body(null, flagged).includes('Is the expected outcome for this turn wrong?'), 'a flagged one can be cleared');
});
