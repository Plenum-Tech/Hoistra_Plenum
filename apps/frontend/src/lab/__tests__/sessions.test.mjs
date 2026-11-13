// sessions — the eval store's pages (11 Nov 2026, task plan B1): the list of sessions and one session opened as a
// tab per turn. The logic first (turn tabs in turn order, the filters as a query, which score is the current one, the
// expected plan as lines a person reads), then the screens rendered to markup: Langfuse's words on screen (Session,
// Trace, Score), a tab per turn with the first one selected, and honest empty states.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';
import { LEVELS, evaluateLevels, evaluateStep, evaluateText, ingestText, planLines, scoreSummary, sessionQuery, sessionRow,
  sessionTitle, turnTabs, verdictText, failureReasons } from '../logic/sessions.js';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const S = await bundle('../lab/Sessions.jsx');
const P = await bundle('../lab/SessionPage.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const SESSION = { id: 'scn__baseline_v0__MT-001__1', run_tag: 'baseline_v0', scenario_id: 'MT-001', scenario_version: 2,
  attempt: 1, origin: 'aasim-vps', source: 'chat', type: 'multi_turn', role: 'property_manager', turns: 2,
  status: 'failed', started_at: '2026-11-11T09:00:00.000Z', scores: { logic: null, ux: null, domain: null } };
const EXPECTED = { plan: [{ call_id: 'c1', domain: 'wo_engine', tool: null, params: {}, depends_on: [] },
  { call_id: 'c2', domain: 'compliance', tool: 'list_certificates', params: {}, depends_on: ['c1'] }],
failure_handling: { state: 'partial_failure', failed: ['c2'] }, answer_format: 'partial_success',
must_include: ['WO-B-301-4551'], must_not_include: [], answer: null };
const DATA = { session: SESSION,
  scenario: { scenario_id: 'MT-001', version: 2, type: 'multi_turn', spec_sections: ['multi_turn_continuity'], status: 'approved',
    stale: [], turns: [{ message: 'Show the overdue work orders at B-301.', expected: EXPECTED },
      { message: 'For each one, who is the vendor?', expected: { ...EXPECTED, plan: [], failure_handling: null, answer_format: 'success', must_include: [] } }] },
  traces: [{ id: 'turn-2', turn_index: 2, input: 'For each one, who is the vendor?', output: 'Apex Mechanical.', status: 'timeout', metadata: {}, scores: {} },
    { id: 'turn-1', turn_index: 1, input: 'Show the overdue work orders at B-301.', output: 'Three are overdue.', status: 'ok', metadata: {}, scores: {} }] };

test('turn tabs are numbered in turn order, whatever order the traces arrive in', () => {
  const tabs = turnTabs(DATA.traces);
  assert.deepEqual(tabs.map((t) => [t.label, t.traceId, t.status]), [['Turn 1', 'turn-1', 'ok'], ['Turn 2', 'turn-2', 'timeout']]);
  assert.deepEqual(turnTabs(null), []);
});

test('empty filters are left out of the query, and switches are sent only when on', () => {
  assert.deepEqual(sessionQuery({ runTag: 'baseline_v0', type: '', status: null }), { run_tag: 'baseline_v0' });
  assert.deepEqual(sessionQuery({ type: 'failure', status: 'failed', level: 'ux', verdict: 'fail', needsSme: true, stale: false, allAttempts: true }),
    { type: 'failure', status: 'failed', level: 'ux', verdict: 'fail', needs_sme: 'true', all_attempts: 'true' });
  assert.deepEqual(sessionQuery({ level: 'ux' }), {}, 'a level without a verdict filters nothing');
  assert.deepEqual(sessionQuery(null), {});
});

test('a person\'s score is the current one, however new the automatic score is', () => {
  const s = scoreSummary([
    { name: 'ux', source: 'EVAL', string_value: 'pass', value: 1, created_at: '2026-11-12T10:00:00Z' },
    { name: 'ux', source: 'ANNOTATION', string_value: 'fail', value: 0.25, comment: 'Too long.', author_email: 'a@b.c', created_at: '2026-11-12T09:00:00Z' },
    { name: 'logic', source: 'EVAL', string_value: 'fail', value: 0.5, created_at: '2026-11-12T08:00:00Z' },
    { name: 'logic', source: 'EVAL', string_value: 'pass', value: 1, created_at: '2026-11-12T09:30:00Z' }]);
  assert.equal(s.ux.verdict, 'fail');
  assert.equal(s.ux.source, 'ANNOTATION');
  assert.equal(s.ux.comment, 'Too long.');
  assert.equal(s.logic.verdict, 'pass', 'the newest automatic score when no person has scored');
  assert.equal(s.domain, null);
  assert.deepEqual(Object.keys(s), LEVELS);
  assert.deepEqual(scoreSummary(null), { logic: null, ux: null, domain: null });
});

test('a verdict in words, and a dash when there is none', () => {
  assert.equal(verdictText('pass'), 'Pass');
  assert.equal(verdictText('fail'), 'Fail');
  assert.equal(verdictText('not_evaluated'), 'Not evaluated');
  assert.equal(verdictText(null), '—');
});

test('the expected plan as lines: the call, what answers it, and what it waits for', () => {
  assert.deepEqual(planLines(EXPECTED), ['c1 · wo_engine', 'c2 · list_certificates (compliance) · after c1']);
  assert.deepEqual(planLines({ plan: [] }), []);
  assert.deepEqual(planLines(null), []);
});

test('a session is titled by its scenario, or by its own id when it has none', () => {
  assert.equal(sessionTitle(SESSION), 'MT-001');
  assert.equal(sessionTitle({ id: 'chat-session-41', scenario_id: null }), 'chat-session-41');
});

test('one row of the list: what it is, where it ran, how it ended', () => {
  const r = sessionRow(SESSION);
  assert.equal(r.title, 'MT-001');
  assert.equal(r.type, 'Multi-turn');
  assert.equal(r.turns, '2 turns');
  assert.equal(r.status, 'Failed');
  assert.equal(r.failed, true);
  assert.deepEqual(r.levels.map((l) => [l.name, l.text]), [['Logic', '—'], ['User experience', '—'], ['Domain', '—']]);
  const one = sessionRow({ ...SESSION, turns: 1, status: 'ok', type: null, scores: { logic: { verdict: 'pass', source: 'EVAL' }, ux: null, domain: null } });
  assert.equal(one.turns, '1 turn');
  assert.equal(one.status, 'OK');
  assert.equal(one.type, '—');
  assert.equal(one.levels[0].text, 'Pass');
});

test('the list: one row per session with its run tag, type, turns and status, in Langfuse\'s words', () => {
  const out = html(S.SessionsTable, { rows: [SESSION, { ...SESSION, id: 'scn__baseline_v0__SD-001__1', scenario_id: 'SD-001', type: 'single_domain', turns: 1, status: 'ok' }], onOpen() {} });
  assert.match(out, /<th[^>]*>Session<\/th>/);
  assert.ok(out.includes('MT-001') && out.includes('SD-001'));
  assert.ok(out.includes('baseline_v0') && out.includes('aasim-vps'));
  assert.ok(out.includes('Multi-turn') && out.includes('Single domain'));
  assert.ok(out.includes('2 turns') && out.includes('1 turn'));
  assert.ok(out.includes('Failed') && out.includes('OK'));
  assert.equal((out.match(/<button/g) || []).length, 2, 'each session opens from its own button');
  assert.ok(!/<th[^>]*>(Logic|User experience|Domain)<\/th>/.test(out), 'the score columns are hidden until scores come from annotations');
});

test('the list says so when there is nothing, and names the run tag asked for', () => {
  assert.ok(html(S.SessionsTable, { rows: [], runTag: 'baseline_v0', onOpen() {} }).includes('No sessions for baseline_v0 yet.'));
  assert.ok(html(S.SessionsTable, { rows: [], onOpen() {} }).includes('No sessions yet.'));
  assert.ok(html(S.SessionsTable, { rows: null, onOpen() {} }).includes('Reading the sessions'));
});

test('the filters: run tag, scenario type and failure status, each a labelled select', () => {
  const out = html(S.Filters, { tags: [{ tag: 'baseline_v0', sessions: 250 }, { tag: 'postfix_v1', sessions: 0 }],
    value: { runTag: 'baseline_v0', type: '', status: '' }, onChange() {} });
  assert.match(out, /<label[^>]*><span>Run tag<\/span><select/);
  assert.match(out, /<label[^>]*><span>Scenario type<\/span><select/);
  assert.match(out, /<label[^>]*><span>Status<\/span><select/);
  assert.ok(out.includes('baseline_v0 (250)') && out.includes('postfix_v1 (0)'));
  assert.ok(out.includes('All run tags') && out.includes('Multi-turn') && out.includes('Failed'));
});

test('a session opens as a tab per turn, the first turn selected', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: {}, onTab() {} });
  assert.match(out, /role="tablist" aria-label="Turns of this session"/);
  const tabs = [...out.matchAll(/role="tab"[^>]*aria-selected="(true|false)"[^>]*>([^<]*)</g)].map((m) => [m[2], m[1]]);
  assert.deepEqual(tabs, [['Turn 1', 'true'], ['Turn 2', 'false']]);
  assert.match(out, /role="tabpanel"/);
  // 13 Nov 2026 (Hussain): the Input / Output boxes are gone; the trace view below shows the question and the answer
  assert.ok(!out.includes('lab-ses-io') && !out.includes('>Input<') && !out.includes('>Output<'), 'no Input / Output boxes');
  assert.ok(!out.includes('Apex Mechanical.'), 'nothing of the other turn');
});

test('the session header and the scenario: Langfuse\'s words, the run tag, and the expected outcome of each turn', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: {}, onTab() {} });
  assert.ok(out.includes('Session') && out.includes('Trace'));
  assert.ok(!out.includes('Your score'), 'the scores panel is hidden on the session page for now');
  assert.ok(out.includes('scn__baseline_v0__MT-001__1') && out.includes('baseline_v0') && out.includes('aasim-vps'));
  assert.ok(out.includes('MT-001') && out.includes('version 2'));
  assert.ok(out.includes('Expected outcome'));
  assert.ok(out.includes('c2 · list_certificates (compliance) · after c1'));
  assert.ok(!out.includes('Answer format') && !out.includes('Partial success'), 'the answer format is hidden (13 Nov 2026)');
  assert.ok(!out.includes('Failure handling') && !out.includes('Partial failure'), 'the failure handling is hidden (13 Nov 2026)');
  assert.ok(out.includes('WO-B-301-4551'), 'what the answer must include');
});

test('a failure scenario is not labelled "Failure" on the session page: that word is only for a turn that failed', () => {
  const data = { ...DATA, session: { ...SESSION, type: 'failure' }, scenario: { ...DATA.scenario, type: 'failure' } };
  const out = html(P.SessionBody, { data, active: 'turn-1', trees: {}, onTab() {} });
  assert.ok(!out.includes('>Failure<'), 'no Failure chip in the header');
  assert.ok(!out.includes('· Failure'), 'no Failure in the scenario line');
  assert.ok(!out.includes('Why it failed'), 'turn 1 ended well, so nothing failed');
});

test('why a turn failed: how the turn ended and each failed step with its reason, never an expected answer', () => {
  assert.deepEqual(failureReasons({ status: 'ok' }, [{ name: 'plan', ok: true }]), []);
  assert.deepEqual(failureReasons({ status: 'ok' }, [{ name: 'plan', ok: true }, { name: 'energy_engine', ok: false, error: 'Connection error.' }]),
    [{ step: 'energy_engine', reason: 'Connection error.' }]);
  assert.deepEqual(failureReasons({ status: 'timeout' }, null), [{ step: null, reason: 'The turn ended: Timed out' }]);
  assert.deepEqual(failureReasons({ status: 'ok' }, [{ name: 'lookup', ok: false, error: null }]), [{ step: 'lookup', reason: 'Failed, no reason recorded.' }]);
  const long = failureReasons({ status: 'ok' }, [{ name: 'db', ok: false, error: 'x'.repeat(500) }])[0].reason;
  assert.equal(long.length, 301, 'a long error is cut to 300 characters and an ellipsis');
});

test('a failed turn shows why it failed under its answer, and only the reason', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-2', trees: {}, onTab() {} });
  assert.match(out, /aria-label="Why it failed"/);
  assert.ok(out.includes('The turn ended: Timed out'));
  assert.ok(!out.includes('A proper answer'), 'no expected answer inside the failure box');
  const ok = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: {}, onTab() {} });
  assert.ok(!ok.includes('Why it failed'), 'a turn that ended well shows no failure box');
});

test('the session page does not show the specification section tags (hidden 13 Nov 2026)', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-1', trees: {}, onTab() {} });
  assert.ok(!out.includes('multi_turn_continuity'), 'the scenario\'s section names are not shown');
  assert.ok(!out.includes('Section of the specification this scenario tests'));
  const draft = html(P.SessionBody, { data: { ...DATA, scenario: { ...DATA.scenario, status: 'draft' } }, active: 'turn-1', trees: {}, onTab() {} });
  assert.ok(draft.includes('Expected outcome not approved yet'), 'the approval chip stays');
});

test('a turn that did not end well says how it ended, and a trace still loading says so', () => {
  const out = html(P.SessionBody, { data: DATA, active: 'turn-2', trees: {}, onTab() {} });
  assert.ok(out.includes('Timed out'));
  assert.ok(out.includes('Reading the trace'));
  const failed = html(P.SessionBody, { data: DATA, active: 'turn-2', trees: { 'turn-2': { error: 'Trace Lab is not available right now.' } }, onTab() {} });
  assert.match(failed, /role="alert"[^>]*>Trace Lab is not available right now\./);
});

test('a session with no scenario and no traces says so plainly', () => {
  const out = html(P.SessionBody, { data: { session: { ...SESSION, scenario_id: null, type: null }, scenario: null, traces: [] }, active: null, trees: {}, onTab() {} });
  assert.ok(out.includes('This session is not linked to a scenario'));
  assert.ok(out.includes('This session has no traces.'));
});

// ── pushing to the dev instance (task plan B5.3: a Sync button that says how many records went) ──────────────

const SYNC = { enabled: true, peer: 'db.example.com/hoist_tracelab',
  last: { status: 'done', dry_run: false, finished_at: '2026-11-11T18:30:00.000Z', pushed: { sessions: 250 }, error: null } };

test('the sync bar: where a push goes, a button to push and one to check first', () => {
  const out = html(S.SyncBar, { status: SYNC, onPush() {}, onCheck() {} });
  assert.ok(out.includes('db.example.com/hoist_tracelab'), 'the host and database, never the address');
  assert.match(out, /<button[^>]*>Sync to dev<\/button>/);
  assert.match(out, /<button[^>]*>Check what would go<\/button>/);
  assert.ok(out.includes('Last push'));
});

test('the sync bar says what went, in the service\'s own words, and says a failure aloud', () => {
  const line = 'Pushed to db.example.com/hoist_tracelab: 250 sessions, 312 traces, 9,804 spans, 0 scores, 250 scenarios, 1 run tag, 0 spec sections.';
  assert.match(html(S.SyncBar, { status: SYNC, said: line, onPush() {}, onCheck() {} }), /role="status"[^>]*>Pushed to db\.example\.com\/hoist_tracelab: 250 sessions/);
  assert.match(html(S.SyncBar, { status: SYNC, error: 'The dev database is on schema 13 and this one is on 14.', onPush() {}, onCheck() {} }),
    /role="alert"[^>]*>The dev database is on schema 13 and this one is on 14\./);
  assert.match(html(S.SyncBar, { status: SYNC, busy: true, onPush() {}, onCheck() {} }), /<button[^>]*disabled=""[^>]*>Syncing…<\/button>/);
});

test('no sync bar when there is nowhere to push to', () => {
  assert.equal(html(S.SyncBar, { status: { enabled: false, peer: null, last: null }, onPush() {}, onCheck() {} }), '');
  assert.equal(html(S.SyncBar, { status: null, onPush() {}, onCheck() {} }), '');
});

// ── scoring a trace, the remaining filters, compare and scenarios (task plan B2, B3, B4) ─────────────────────

const { CHANGE_TEXT, canScoreDomain, compareView, scenarioRow, scoreLines } = await import('../logic/sessions.js');
const C = await bundle('../lab/Compare.jsx');
const N = await bundle('../lab/Scenarios.jsx');

const LOGIC = { verdict: 'fail', value: 0.5, source: 'EVAL', comment: 'No call was made for c2 (compliance).',
  details: { checks: [{ key: 'completed', result: 'pass', note: 'The turn ended normally.' },
    { key: 'plan_coverage', result: 'fail', note: 'No call was made for c2 (compliance).' },
    { key: 'retries', result: 'not_applicable', note: 'Nothing was retried.' },
    { key: 'evidence_state', result: 'not_observable', note: 'No tool reported an evidence state.' }], stale_expected: true } };
const UX = { verdict: 'fail', value: 0.75, source: 'EVAL', comment: 'Leaves out the EICR.',
  details: { criteria: { concise: true, complete: false, template: true, failure_wording: true }, answer_words: 13,
    missing: ['EICR'], forbidden_present: [], template: { expected: 'success', found: 'success' } } };
const DOMAIN = { verdict: 'fail', value: 0, source: 'EVAL', comment: 'One rule broken.',
  details: { violations: [{ rule: 'R-1', why: 'Calls a lapsed certificate current.' }], needs_sme: true } };

test('a logic score is read as its checks: what passed, what failed and why, what could not be seen', () => {
  const lines = scoreLines('logic', LOGIC).map((l) => l.text);
  assert.deepEqual(lines, ['Completed: pass', 'Plan coverage: fail. No call was made for c2 (compliance).',
    'Evidence state: could not be seen. No tool reported an evidence state.',
    'The expected outcome was out of date when this was scored.']);
  assert.equal(scoreLines('logic', LOGIC)[1].tone, 'bad');
  assert.deepEqual(scoreLines('logic', null), []);
});

test('a user-experience score is read as its four criteria and what was missing', () => {
  const lines = scoreLines('ux', UX).map((l) => l.text);
  assert.deepEqual(lines, ['Concise: yes (13 words)', 'Complete: no. Missing: EICR', 'Right template: yes (Success)',
    'Clear wording on failures and denials: yes']);
  const wrong = scoreLines('ux', { ...UX, details: { ...UX.details, criteria: { ...UX.details.criteria, template: false },
    template: { expected: 'partial_success', found: 'success' } } }).map((l) => l.text);
  assert.ok(wrong.includes('Right template: no (expected Partial success, found Success)'));
});

test('a domain score is read as the rules broken, and says when it waits for an expert', () => {
  assert.deepEqual(scoreLines('domain', DOMAIN).map((l) => l.text),
    ['R-1: Calls a lapsed certificate current.', 'Waiting for a domain expert to confirm.']);
  assert.deepEqual(scoreLines('domain', { verdict: 'not_evaluated', source: 'EVAL', details: { reason: 'no_domain_rules' } }).map((l) => l.text),
    ['Not evaluated: this scenario has no domain rules to check against.']);
});

test('who may score the domain level', () => {
  assert.equal(canScoreDomain({ role: 'domain_expert' }), true);
  assert.equal(canScoreDomain({ role: 'platform' }), true);
  assert.equal(canScoreDomain({ role: 'consultant' }), true);
  assert.equal(canScoreDomain({ role: 'tester' }), false);
  assert.equal(canScoreDomain(null), false);
});

test('the scores panel: each level with its current score, its reasons and a form to score it', () => {
  const trace = { id: 'turn-1', status: 'ok', scores: { logic: LOGIC, ux: UX, domain: null }, score_history: [] };
  const out = html(P.ScoresPanel, { trace, me: { role: 'platform' }, onSave() {} });
  assert.ok(out.includes('Logic') && out.includes('User experience') && out.includes('Domain'));
  assert.ok(out.includes('Plan coverage: fail. No call was made for c2 (compliance).'));
  assert.ok(out.includes('Scored automatically'));
  assert.equal((out.match(/<legend[^>]*>Your score<\/legend>/g) || []).length, 3, 'one form per level');
  assert.equal((out.match(/type="radio"/g) || []).length, 6, 'pass and fail for each level');
  assert.match(out, /<label[^>]*><span>Score from 0 to 1<\/span><input type="number"/);
  assert.match(out, /<label[^>]*><span>Comment<\/span><textarea/);
  assert.ok(out.includes('Not scored yet'), 'the domain level has no score');
});

test('the domain form is closed to someone who is not a domain expert, and says who can', () => {
  const trace = { id: 'turn-1', status: 'ok', scores: { logic: null, ux: null, domain: DOMAIN }, score_history: [] };
  const out = html(P.ScoresPanel, { trace, me: { role: 'tester' }, onSave() {} });
  assert.equal((out.match(/<legend[^>]*>Your score<\/legend>/g) || []).length, 2);
  assert.ok(out.includes('Only a domain expert, the platform team or the consultant scores the domain level.'));
  assert.ok(out.includes('Waiting for a domain expert to confirm.'));
});

test('a person\'s score shows who gave it and what they said, with the other scores still listed', () => {
  const mine = { id: 's2', verdict: 'pass', value: 1, source: 'ANNOTATION', comment: 'I misread it.', author: 'a@b.c', details: {} };
  const trace = { id: 'turn-1', status: 'ok', scores: { logic: mine, ux: null, domain: null },
    score_history: [{ id: 's2', name: 'logic', source: 'ANNOTATION', verdict: 'pass', comment: 'I misread it.', author_email: 'a@b.c', created_at: '2026-11-12T10:00:00Z' },
      { id: 's1', name: 'logic', source: 'EVAL', verdict: 'fail', comment: 'No call was made.', evaluator: 'logic-1', created_at: '2026-11-12T09:00:00Z' }] };
  const out = html(P.ScoresPanel, { trace, me: { role: 'platform' }, onSave() {} });
  assert.ok(out.includes('Scored by a@b.c') && out.includes('I misread it.'));
  assert.ok(out.includes('Other scores (1)') && out.includes('No call was made.'));
});

test('the other scores leave out the current one by its id, even when an automatic score is newer', () => {
  const mine = { id: 's2', verdict: 'pass', value: 1, source: 'ANNOTATION', comment: 'I misread it.', author: 'a@b.c', details: {} };
  const trace = { id: 'turn-1', status: 'ok', scores: { logic: mine, ux: null, domain: null },
    score_history: [{ id: 's3', name: 'logic', source: 'EVAL', verdict: 'fail', comment: 'Rescored after the fix.', evaluator: 'logic-1', created_at: '2026-11-12T11:00:00Z' },
      { id: 's2', name: 'logic', source: 'ANNOTATION', verdict: 'pass', comment: 'I misread it.', author_email: 'a@b.c', created_at: '2026-11-12T10:00:00Z' },
      { id: 's1', name: 'logic', source: 'EVAL', verdict: 'fail', comment: 'No call was made.', evaluator: 'logic-1', created_at: '2026-11-12T09:00:00Z' }] };
  const out = html(P.ScoresPanel, { trace, me: { role: 'platform' }, onSave() {} });
  const other = out.slice(out.indexOf('Other scores'));
  assert.ok(out.includes('Other scores (2)'), out);
  assert.ok(other.includes('Rescored after the fix.') && other.includes('No call was made.') && !other.includes('I misread it.'));
});

test('the filters also take a level with its verdict, and the three switches', () => {
  const out = html(S.Filters, { scores: true, tags: [], value: { runTag: '', type: '', status: '', level: 'ux', verdict: 'fail', needsSme: true, stale: false, allAttempts: false }, onChange() {} });
  assert.match(out, /<label[^>]*><span>Level<\/span><select/);
  assert.match(out, /<label[^>]*><span>Verdict<\/span><select/);
  assert.match(out, /<input type="checkbox"[^>]*checked=""[^>]*\/>\s*<span>Waiting for a domain expert<\/span>|<input type="checkbox"[^>]*checked=""[^>]*\/><span>Waiting for a domain expert<\/span>/);
  assert.ok(out.includes('Expected outcome out of date') && out.includes('Show every attempt'));
});

test('the evaluate bar: which run tag, what it does and how far it has got', () => {
  const idle = html(S.EvaluateBar, { runTag: 'baseline_v0', onRun() {} });
  assert.match(idle, /<button[^>]*>Evaluate baseline_v0<\/button>/);
  const busy = html(S.EvaluateBar, { runTag: 'baseline_v0', busy: true, said: 'Scored 50 traces so far; 400 scores to go.', onRun() {} });
  assert.match(busy, /disabled=""/);
  assert.match(busy, /role="status"[^>]*>Scored 50 traces so far; 400 scores to go\./);
  assert.ok(html(S.EvaluateBar, { runTag: '', onRun() {} }).includes('Choose a run tag to evaluate it.'));
});

test('the evaluate bar names the levels the person may run', () => {
  assert.ok(html(S.EvaluateBar, { runTag: 'baseline_v0', levels: ['logic'], onRun() {} }).includes('at the logic level.'));
  assert.ok(html(S.EvaluateBar, { runTag: 'baseline_v0', onRun() {} }).includes('at the logic, user experience and domain levels.'));
});

test('the model judges are run by the platform team and the consultant; a company admin runs the logic checks', () => {
  assert.deepEqual(evaluateLevels({ all_companies: true }), ['logic', 'ux', 'domain']);
  assert.deepEqual(evaluateLevels({ all_companies: false, role: 'admin' }), ['logic']);
  assert.deepEqual(evaluateLevels(null), ['logic']);
});

test('evaluating goes on past traces whose judge failed, and stops when done, capped or stuck', () => {
  const pass = (o) => ({ traces: 0, scored: {}, not_evaluated: {}, failed_traces: [], remaining: 0, left_for_later: 0, capped: false, ...o });
  let st = evaluateStep(null, pass({ traces: 2, scored: { ux: 1 }, not_evaluated: { ux: 0 }, failed_traces: ['t1'], remaining: 9 }));
  assert.deepEqual([st.scores, st.skip, st.left, st.stop], [1, ['t1'], 9, null]);
  st = evaluateStep(st, pass({ traces: 2, scored: { ux: 2 }, failed_traces: ['t2', 't1'], remaining: 7, left_for_later: 1 }));
  assert.deepEqual([st.scores, st.skip, st.left, st.stop], [3, ['t1', 't2'], 6, null], 'a trace is passed over once, however often it fails');
  assert.equal(evaluateStep(st, pass({ remaining: 2, left_for_later: 2 })).stop, 'done', 'only the passed-over traces are left');
  assert.equal(evaluateStep(st, pass({ traces: 1, scored: { ux: 1 }, remaining: 4, capped: true })).stop, 'capped');
  assert.equal(evaluateStep(st, pass({ remaining: 4 })).stop, 'stuck', 'a pass that got nowhere never spins');
});

test('what the page says while evaluating and after', () => {
  assert.equal(evaluateText({ tag: 'baseline_v0', scores: 40, skip: [], left: 10, stop: null }), 'Scored 40 so far; 10 to go.');
  assert.equal(evaluateText({ tag: 'baseline_v0', scores: 750, skip: [], left: 0, stop: 'done' }), 'Done: 750 scores written under baseline_v0.');
  assert.equal(evaluateText({ tag: 'baseline_v0', scores: 700, skip: ['t1', 't2'], left: 0, stop: 'done' }),
    'Done: 700 scores written under baseline_v0. The judge could not score 2 traces; evaluate again later to retry them.');
  assert.equal(evaluateText({ tag: 'baseline_v0', scores: 300, skip: [], left: 450, stop: 'capped' }),
    'Stopped: baseline_v0 has used its spending limit for the model judges, with 450 scores still to do.');
  assert.equal(evaluateText({ tag: 'baseline_v0', scores: 0, skip: [], left: 5, stop: 'stuck' }),
    'Nothing more could be scored; 5 scores are still to do. Try again later.');
});

test('reading conversations in says what waited, what belonged to another company and what failed', () => {
  assert.equal(ingestText({ sessions: 0, traces: 0 }), 'Nothing new to read.');
  assert.equal(ingestText({ sessions: 2, traces: 3, waiting: 0, other_company: 0, failed: 0 }), 'Read in 2 new sessions and 3 new traces.');
  assert.equal(ingestText({ sessions: 1, traces: 1, waiting: 2, other_company: 1, failed: 1 }),
    'Read in 1 new session and 1 new trace. 2 conversations wait for their scenarios to be stored; read again once they are. '
    + '1 conversation uses a session id another company\'s session already has, and was not read. '
    + '1 conversation could not be read; read again to retry.');
  assert.equal(ingestText({ waiting: 1 }), 'Nothing new to read. 1 conversation waits for its scenario to be stored; read again once it is.');
});

test('the sync bar says how many rows the last push left out', () => {
  const last = { status: 'done', dry_run: false, finished_at: '2026-11-11T10:00:00Z', error: null,
    pushed: { sessions: 2 }, conflicts: { traces: 1, observations: 4, scores: 0 } };
  const out = html(S.SyncBar, { status: { ...SYNC, last }, onPush() {}, onCheck() {} });
  assert.ok(out.includes('5 rows were left out because the dev database holds something else in their place.'), out);
  assert.ok(!html(S.SyncBar, { status: { ...SYNC, last: { ...last, conflicts: {} } }, onPush() {}, onCheck() {} }).includes('left out'));
});

const COMPARE = { base: 'baseline_v0', head: 'postfix_v1',
  summary: { logic: { fixed: 12, regressed: 1, same: 230, changed: 0, new: 2, missing: 5 },
    ux: { fixed: 40, regressed: 3, same: 200, changed: 0, new: 2, missing: 5 },
    domain: { fixed: 0, regressed: 0, same: 243, changed: 0, new: 2, missing: 5 },
    sessions: { base: 248, head: 245 }, answer_words: { base: 412, head: 96 } },
  rows: [{ scenario_id: 'SD-7-001', type: 'single_domain',
    base: { session_id: 'scn__baseline_v0__SD-7-001__1', verdicts: { logic: 'fail', ux: 'fail', domain: 'not_evaluated' }, answer_words: 420, status: 'ok', turns: 1 },
    head: { session_id: 'scn__postfix_v1__SD-7-001__1', verdicts: { logic: 'pass', ux: 'pass', domain: 'not_evaluated' }, answer_words: 90, status: 'ok', turns: 1 },
    change: { logic: 'fixed', ux: 'fixed', domain: 'same' } },
  { scenario_id: 'SD-7-002', type: 'single_domain', base: { session_id: 'b2', verdicts: { logic: 'pass', ux: 'pass', domain: 'pass' }, answer_words: 80, status: 'ok', turns: 1 },
    head: null, change: { logic: 'missing', ux: 'missing', domain: 'missing' } }] };

test('compare as it is read: what changed per level, and how long the answers are on each side', () => {
  const v = compareView(COMPARE);
  assert.deepEqual(v.summary, ['Logic: 12 fixed, 1 regressed, 230 the same', 'User experience: 40 fixed, 3 regressed, 200 the same',
    'Domain: 0 fixed, 0 regressed, 243 the same', 'Median answer length: 412 words in baseline_v0, 96 words in postfix_v1',
    '248 scenarios in baseline_v0, 245 in postfix_v1; 2 only in postfix_v1, 5 only in baseline_v0']);
  assert.deepEqual(v.rows[0].levels.map((l) => [l.name, l.base, l.head, l.change]),
    [['Logic', 'Fail', 'Pass', 'Fixed'], ['User experience', 'Fail', 'Pass', 'Fixed'], ['Domain', 'Not evaluated', 'Not evaluated', 'Same']]);
  assert.deepEqual(v.rows[0].words, { base: 420, head: 90 });
  assert.equal(v.rows[1].levels[0].head, '—');
  assert.equal(v.rows[1].levels[0].change, 'Not asked');
  assert.equal(CHANGE_TEXT.regressed, 'Regressed');
  assert.deepEqual(compareView(null), { summary: [], rows: [] });
});

test('the compare table: the two runs side by side for each scenario, and a way to open its sessions', () => {
  const out = html(C.CompareTable, { data: COMPARE, onOpen() {} });
  assert.ok(out.includes('baseline_v0') && out.includes('postfix_v1'));
  assert.ok(out.includes('SD-7-001') && out.includes('Fixed') && out.includes('Not asked'));
  assert.ok(out.includes('Median answer length: 412 words in baseline_v0, 96 words in postfix_v1'));
  assert.match(out, /<button[^>]*>Open side by side<\/button>/, 'asked under both run tags');
  assert.match(out, /<button[^>]*>Open the session<\/button>/, 'asked under one of them only');
  assert.equal((out.match(/<button/g) || []).length, 2);
  assert.ok(html(C.CompareTable, { data: { ...COMPARE, rows: [] }, onOpen() {} }).includes('No scenario was asked under both run tags.'));
});

test('one scenario as the list reads it', () => {
  const r = scenarioRow({ scenario_id: 'FL-7-001', version: 2, type: 'failure', spec_sections: ['partial_failure', 'output_format'],
    status: 'draft', turns: [{}, {}], stale: ['partial_failure'] });
  assert.deepEqual([r.id, r.version, r.type, r.status, r.turns, r.stale], ['FL-7-001', 2, 'Failure', 'Draft', '2 turns', true]);
  assert.equal(r.staleText, 'Out of date: partial_failure changed');
  assert.equal(scenarioRow({ scenario_id: 'SD-1', version: 1, type: 'single_domain', spec_sections: [], status: 'approved', turns: [{}], stale: [] }).staleText, '');
});

test('the scenarios table: each scenario with its sections, version and status, and what is out of date', () => {
  const rows = [{ scenario_id: 'FL-7-001', version: 2, type: 'failure', spec_sections: ['partial_failure'], status: 'draft', turns: [{ message: 'q' }], stale: ['partial_failure'] },
    { scenario_id: 'SD-7-001', version: 1, type: 'single_domain', spec_sections: ['output_format'], status: 'approved', turns: [{ message: 'q' }], stale: [] }];
  const out = html(N.ScenariosTable, { rows, selected: ['FL-7-001'], onToggle() {} });
  assert.ok(out.includes('FL-7-001') && out.includes('partial_failure') && out.includes('Draft') && out.includes('Approved'));
  assert.ok(out.includes('Out of date: partial_failure changed'));
  assert.equal((out.match(/type="checkbox"/g) || []).length, 2);
  assert.equal((out.match(/checked=""/g) || []).length, 1);
  assert.ok(html(N.ScenariosTable, { rows: [], selected: [], onToggle() {} }).includes('No scenarios yet.'));
  assert.ok(html(N.ScenariosTable, { rows: null, selected: [], onToggle() {} }).includes('Reading the scenarios'));
});

test('the specification line: which version is current and how many scenarios are out of date', () => {
  assert.ok(html(N.SpecLine, { spec: { spec_version: 'abc123def456', sections: new Array(15).fill({}), stale_scenarios: 3 } })
    .includes('Specification abc123def456 · 15 sections · 3 scenarios out of date'));
  assert.ok(html(N.SpecLine, { spec: { spec_version: null, sections: [], stale_scenarios: 0 } })
    .includes('No specification has been read in yet.'));
});

test('the specification line says when scenarios cite sections the specification does not have', () => {
  const out = html(N.SpecLine, { spec: { spec_version: 'abc123def456', sections: new Array(15).fill({}), stale_scenarios: 0,
    uncovered_scenarios: 23, uncovered_sections: ['retry_rules', 'scope_preservation'] } });
  assert.ok(out.includes('23 scenarios cite sections the specification does not have (retry_rules, scope_preservation), so a change cannot flag them'), out);
  assert.ok(!html(N.SpecLine, { spec: { spec_version: 'abc123def456', sections: [], stale_scenarios: 0, uncovered_scenarios: 0 } }).includes('cite'));
});

test('a level\'s compare line also counts the moves that are neither a fix nor a regression, so it adds up', () => {
  const out = { ...COMPARE, summary: { ...COMPARE.summary, ux: { fixed: 0, regressed: 0, same: 18, changed: 2, new: 0, missing: 0 } } };
  assert.equal(compareView(out).summary[1], 'User experience: 0 fixed, 0 regressed, 18 the same, 2 changed in another way');
  assert.equal(compareView(COMPARE).summary[0], 'Logic: 12 fixed, 1 regressed, 230 the same');
});

test('while scores are hidden (until they come from annotations): no score filters and no Evaluate button', () => {
  assert.equal(S.SHOW_SCORES, false);
  const out = html(S.Filters, { tags: [], value: {}, onChange() {} });
  assert.ok(!out.includes('<span>Level</span>') && !out.includes('<span>Verdict</span>') && !out.includes('Waiting for a domain expert'));
  assert.ok(out.includes('Expected outcome out of date') && out.includes('Show every attempt'), 'the other switches stay');
  const src = readFileSync(new URL('../Sessions.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('{curator && SHOW_SCORES ? <EvaluateBar'), 'the Evaluate button waits for the same switch');
});

test('while scores are hidden the Compare view is hidden too: Sessions and Scenarios only', () => {
  assert.deepEqual(S.VIEWS.map(([key]) => key), ['sessions', 'scenarios']);
});

// 13 Nov 2026: a question typed in the chat was recorded as a trace but never listed in Sessions, which reads
// scenario runs only. A curator can now read the chat's own conversations in as well - asked for, never by default.
test('a curator can read the chat questions in, and the ordinary read does not ask for them', () => {
  const src = readFileSync(new URL('../Sessions.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('labApi.evalIngest(chat ? { chat: true } : {})'));
  assert.ok(src.includes('onClick={readIn(false)}') && src.includes('onClick={readIn(true)}'));
  assert.ok(src.includes("'Read new sessions'") && src.includes("'Read chat questions'"));
});

test('a chat conversation with no scenario is still a row a person can read', () => {
  const row = sessionRow({ id: '2db72d10-e565-46b4-b0b6-5ff51304c586', run_tag: 'chat', scenario_id: null,
    scenario_version: null, attempt: 1, source: 'chat', turns: 1, started_at: '2026-11-12T20:30:11.000Z',
    scores: { logic: null, ux: null, domain: null } });
  assert.ok(row && typeof row === 'object');
  assert.equal(sessionTitle({ id: '2db72d10-e565-46b4-b0b6-5ff51304c586', scenario_id: null }), '2db72d10-e565-46b4-b0b6-5ff51304c586');
});

test('the Sessions list opens on the run tag a Runs row names (?tag=), else on every tag', () => {
  assert.equal(S.startFilters('?view=sessions&tag=frontend_nov13_202718').runTag, 'frontend_nov13_202718');
  assert.equal(S.startFilters('?view=sessions').runTag, '');
  assert.equal(S.startFilters('').status, '', 'the other filters start empty');
});
