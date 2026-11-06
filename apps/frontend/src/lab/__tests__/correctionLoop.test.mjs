// The correction loop on the lab page (3 Nov 2026): traces -> notes -> failure modes -> SPEC.md -> correction ->
// regression -> rerun. What a coding agent proposes waits for a person (Approve / Reject; an expected answer not derived
// from the approved specification and the company's records cannot be approved); an agent's label is a suggestion a person
// accepts, never a person's value; the specification in force downloads as SPEC.md; a regression run is asked again five
// questions at a time and compared; the question page shows how its expected answer was derived and what the browser saw;
// the top bar says the service syncs new answers by itself.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' }, history: { replaceState: () => {} } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const LP = await bundle('../lab/Loop.jsx');
const LV = await bundle('../lab/LabelsView.jsx');
const QP = await bundle('../lab/QuestionPage.jsx');
const L = await bundle('../lab/LabApp.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const PROPOSALS = {
  modes: [{ lineage_id: 'm9', id: 'm9v1', kind: 'new', name: 'lapsed_read_as_valid', definition: 'A certificate past expiry is called current.',
    by: 'agent@plenum-tech.com', at: '2026-11-03T10:00:00Z', current: null }],
  spec: [{ id: 'v2', version: 2, rules: [{ code: 'S-1', title: 'lapsed_read_as_valid', text: 'An answer must not call a lapsed certificate current.' }],
    by: 'hussain@plenum-tech.com', at: '2026-11-03T11:00:00Z', status: 'proposed' }],
  expected: [
    { id: 'e1', question: 'R4-2', question_text: 'Which certificates have lapsed?', expected: 'LOLER-B-301-4471 and FGAS-B-301-CH101 are lapsed.',
      derivation: { derived: true, spec_rule: 'S-1', data: 'SELECT certificate_number FROM compliance_certificates WHERE expiry_date < now()', problems: [] } },
    { id: 'e2', question: 'R4-3', question_text: 'How many floors?', expected: '22 floors.',
      derivation: { derived: false, spec_rule: null, data: null, problems: ['names no rule of the specification', 'names no company records it was read from'] } }],
  can_decide_modes: true, can_decide_expected: true
};

test('waiting: each proposal with Approve and Reject; an expected answer not derived cannot be approved, and says why', () => {
  const out = html(LP.Waiting, { data: PROPOSALS, onDecide() {} });
  assert.ok(out.includes('Waiting for a decision (4)'));
  assert.ok(out.includes('New failure mode') && out.includes('lapsed_read_as_valid'));
  assert.ok(out.includes('Version 2') && out.includes('S-1'));
  assert.ok(out.includes('Derived from S-1'));
  assert.ok(out.includes('Not derived from the specification') && out.includes('names no rule of the specification'));
  assert.equal((out.match(/>Approve</g) || []).length, 4);
  assert.equal((out.match(/disabled=""[^>]*>Approve</g) || []).length, 1, 'only the underived expected answer is blocked');
  assert.ok(out.includes('Nothing it proposes counts until a person approves it'));
});

test('waiting: someone who may not decide sees who does, and no buttons', () => {
  const out = html(LP.Waiting, { data: Object.assign({}, PROPOSALS, { can_decide_modes: false, can_decide_expected: false }), onDecide() {} });
  assert.equal((out.match(/>Approve</g) || []).length, 0);
  assert.ok(out.includes('The platform team or the consultant decides failure modes.'));
  assert.ok(out.includes('A company admin, the platform team or the consultant decides expected answers.'));
  assert.equal(LP.waitingCount(PROPOSALS), 4);
  assert.equal(LP.waitingCount(null), 0);
});

test('the specification: the version in force, its rules, Download SPEC.md, and a draft to edit and propose', () => {
  const spec = { approved: { id: 'v1', version: 1, rules: [{ code: 'S-1', title: 'lapsed_read_as_valid', text: 'Never call a lapsed certificate current.' }],
    review: { by: 'aasim@plenum-tech.com', at: '2026-11-03T12:00:00Z' } }, pending: [], history: [] };
  const out = html(LP.SpecPanel, { spec, canEdit: true, onDraft() {}, onPropose() {}, onCancelDraft() {}, onDownload() {} });
  assert.ok(out.includes('Version 1 in force') && out.includes('approved by aasim@plenum-tech.com'));
  assert.ok(out.includes('Download SPEC.md') && out.includes('Draft next version'));
  const none = html(LP.SpecPanel, { spec: { approved: null }, canEdit: false });
  assert.ok(none.includes('No version approved yet.'));
  assert.equal(none.includes('Download SPEC.md'), false);
  assert.equal(none.includes('Draft next version'), false);
});

test('a failure mode in the loop: its steps done and the next one, its rule, corrections and regression runs', () => {
  const mode = { lineage_id: 'm1', name: 'lapsed_read_as_valid', definition: 'A certificate past expiry is called current.',
    positives: 3, corrections: 1, rule: { code: 'S-1' }, steps: { annotated: true, taxonomy: true, spec: true, correction: true, regression: true },
    next: 'rerun', regression_runs: [{ run: 'R12', spec_rule: 'S-1', space: 'comp', at: '2026-11-03T12:30:00Z' }] };
  const chips = LP.stepChips(mode);
  assert.deepEqual(chips.map((c) => c.key), ['traces', 'notes', 'taxonomy', 'spec', 'correction', 'regression', 'rerun']);
  assert.deepEqual(chips.filter((c) => c.done).map((c) => c.key), ['traces', 'notes', 'taxonomy', 'spec', 'correction', 'regression']);
  assert.equal(chips.find((c) => c.next).key, 'rerun');
  const out = html(LP.ModeRow, { mode, canTaxonomy: true, canCurate: true, wide: true, companies: [], onCorrection() {}, onRegression() {},
    onRerun() {}, onCompare() {} });
  assert.ok(out.includes('3 marked positive · 1 correction'));
  assert.ok(out.includes('R12') && out.includes('Compliance Engine') && out.includes('Ask again') && out.includes('Compare'));
  assert.match(out, /aria-current="step"[^>]*>Rerun</);
  const early = html(LP.ModeRow, { mode: Object.assign({}, mode, { rule: null, regression_runs: [], next: 'spec' }), canTaxonomy: false, canCurate: true });
  assert.match(early, /disabled=""[^>]*title="Add this failure mode to the approved specification first"[^>]*>Make regression run/);
  assert.equal(early.includes('Record correction'), false, 'only the platform team and the consultant record corrections');
});

test('asking again runs five at a time until the service says there are no more', async () => {
  const calls = [];
  const api = { rerun: async (code, body) => {
    calls.push(body.start);
    assert.equal(body.count, 1, 'one question a request: a slow answer must not hold one HTTP request past the gateway limit');
    if (body.start === 0) return { asked: 5, next_start: 5, total: 7, results: [{ ok: true }, { ok: true }, { ok: false }, { ok: true }, { ok: true }] };
    return { asked: 2, next_start: null, total: 7, results: [{ ok: true }, { ok: true }], under_test: ['docs/compliance/answering.md'] };
  } };
  const seen = [];
  const out = await LP.rerunAll(api, 'R12', (p) => seen.push(p.asked));
  assert.deepEqual(calls, [0, 5]);
  assert.deepEqual(seen, [5, 7]);
  // underTest: the instruction documents under test that this rerun read (Bala, 5 Nov 2026)
  assert.deepEqual(out, { asked: 7, total: 7, failed: 1, underTest: ['docs/compliance/answering.md'] });
});

test('a rerun that saved no answer says so, not still failing', () => {
  const data = { run: 'R42', summary: { no_answer: 1 }, questions: [{ question: 'R42-1', text: 'q', expected: 'x', derived: true,
    before: { answer: 'old', check: { status: 'mismatch' } }, after: { answer: '', check: { status: 'mismatch' } }, verdict: 'no_answer', people: {} }] };
  const out = html(LP.CompareView, { data });
  assert.ok(out.includes('No answer (timed out or failed)'));
});

test('compare: the summary, and each question before and after against its expected values', () => {
  const data = { run: 'R12', spec_rule: 'S-1', summary: { fixed: 1, passing: 0, still_failing: 1, regressed: 0, not_rerun: 0, no_expected: 0 },
    questions: [
      { question: 'R12-1', text: 'Which certificates have lapsed?', from: 'R4-2', expected: 'LOLER-B-301-4471', derived: true,
        before: { turn_id: 't1', answer: 'None have lapsed.', check: { status: 'mismatch' } },
        after: { turn_id: 't2', answer: 'LOLER-B-301-4471 lapsed on 18 Oct.', check: { status: 'all_found' } }, verdict: 'fixed', people: { positive: 0, negative: 1 } },
      { question: 'R12-2', text: 'Lapsed at Bishopsgate?', expected: 'FGAS-B-301-CH101', derived: false, before: null, after: null,
        verdict: 'still_failing', people: { positive: 1, negative: 0 } }] };
  const out = html(LP.CompareView, { data });
  assert.ok(out.includes('Compare R12 · S-1'));
  assert.ok(out.includes('An expected value is missing') && out.includes('All expected values found'));
  assert.ok(out.includes('lab-loop-v-fixed') && out.includes('Still failing'));
  assert.ok(out.includes('From R4-2'));
  assert.ok(out.includes('Not derived from the specification'));
  assert.ok(out.includes('0 positive · 1 negative'));
});

test('companies for a regression run come from the runs in view', () => {
  const cos = LP.companiesOf([{ organization_id: 'o1', owner_email: 'ann@northbridge.test', title: 'x' },
    { organization_id: 'o1', owner_email: 'bob@northbridge.test' }, { organization_id: 'o2', title: 'Synthetic · comp' }, { organization_id: null }]);
  assert.deepEqual(cos, [{ id: 'o1', label: 'northbridge.test' }, { id: 'o2', label: 'Synthetic · comp' }]);
});

test('labels: an agent suggestion is shown apart with Accept, and never counted as a person', () => {
  const entries = [{ value: 'positive', source: 'coding_agent', suggestion: true, author_email: 'agent@plenum-tech.com', at: '2026-11-03T10:00:00Z' },
    { value: 'negative', source: 'manual', author_id: 'u2', author_email: 'bob@x.test', at: '2026-11-03T11:00:00Z' }];
  const c = LV.cellView(entries, 'me');
  assert.equal(c.suggested, 'positive');
  assert.equal(c.others, 1);
  assert.equal(c.pos, 0, "the suggestion is not a person's positive");
  assert.equal(c.disagree, false);
  const data = { run: { code: 'R3', title: 'x' }, questions: [{ id: 'q1', code: 'R3-1', question: 'Q?', turn_ids: [] }],
    modes: [{ lineage_id: 'm1', id: 'm1', name: 'lapsed_read_as_valid' }], cells: { q1: { m1: entries } }, me: 'me' };
  const out = html(LV.LabelsMatrix, { run: 'R3', data, me: { user_id: 'me' }, waiting: 2, onOpenLoop() {} });
  assert.ok(out.includes('Suggested by agent: positive'));
  assert.ok(out.includes('Accept the suggestion positive for R3-1, lapsed_read_as_valid'));
  assert.ok(out.includes('2 proposals wait for a person’s decision') && out.includes('Open the loop'));
  const accepted = LV.cellView([entries[0], { value: 'positive', source: 'manual', author_id: 'me', mine: true }], 'me');
  assert.equal(accepted.mine, 'positive');
  assert.equal(accepted.suggested, 'positive');
});

test('question: the expected answer says what it was derived from; a proposal waits with Approve; the browser panel shows', () => {
  const data = { question: { code: 'R4-2', question: 'Which certificates have lapsed?' }, run: { code: 'R4' },
    expected: { id: 'x1', expected: 'LOLER-B-301-4471 lapsed.', source: 'manual', evidence: { spec_rule: 'S-1', data: 'SELECT …' },
      derivation: { derived: true, spec_rule: 'S-1', data: 'SELECT …', problems: [] } },
    expected_pending: { id: 'x2', expected: 'Two have lapsed.', source: 'coding_agent', author_email: 'agent@plenum-tech.com',
      derivation: { derived: false, spec_rule: null, data: null, problems: ['names no rule of the specification'] } },
    spec_rules: [{ code: 'S-1', title: 'lapsed_read_as_valid' }],
    browser: [{ id: 'b1', kind: 'error', ms_total: 86000, error: 'The connection closed before the answer was finished.', view: 'home', instance: 'comp', at: '2026-11-03T10:31:00Z' },
      { id: 'b2', kind: 'answer_shown', ms_first_text: 3100, ms_total: 21000, view: 'home', instance: 'comp', at: '2026-11-03T10:38:00Z' }],
    traces: [{ turn_id: 't-new', source: 'manual' }, { turn_id: 't-old', source: 'sync' }], active: null, labels: [] };
  const out = html(QP.QuestionView, { data, curator: true, openAll: true });
  assert.ok(out.includes('Derived from S-1'));
  assert.ok(out.includes('Proposed by a coding agent') && out.includes('Not derived from the specification'));
  assert.match(out, /disabled=""[^>]*>Approve</);
  assert.ok(out.includes('In the browser') && out.includes('Error shown') && out.includes('took 86.0 s'));
  assert.ok(out.includes('first text after 3.1 s'));
  assert.ok(out.includes('Answer 1 (newest)'));
  const noCurator = html(QP.QuestionView, { data, curator: false, openAll: true });
  assert.equal(/>Approve</.test(noCurator), false);
});

test('question: the evidence saved carries the rule and the records on top of what was typed', () => {
  assert.deepEqual(QP.evidenceWith('', 'S-1', 'SELECT x'), { spec_rule: 'S-1', data: 'SELECT x' });
  assert.deepEqual(QP.evidenceWith('{"type":"sql"}', 'S-2', ''), { type: 'sql', spec_rule: 'S-2' });
  assert.deepEqual(QP.evidenceWith('row 12 of the register', '', ''), { reference: 'row 12 of the register' });
  assert.equal(QP.evidenceWith('', '', ''), null);
});

test('the top bar says the service syncs by itself, how often and when it last did', () => {
  assert.match(L.syncLine({ enabled: true, every_s: 120, last_run_at: '2026-11-03T10:05:00Z', last_ok: true }), /^Synced automatically every 2 min · last \d\d:\d\d$/);
  assert.equal(L.syncLine({ enabled: true, every_s: 120, last_run_at: null }), 'Synced automatically every 2 min · not run yet');
  assert.match(L.syncLine({ enabled: true, every_s: 60, last_run_at: '2026-11-03T10:05:00Z', last_ok: false }), /\(failed\)$/);
  assert.equal(L.syncLine({ enabled: false }), 'Automatic sync is off');
  assert.equal(L.syncLine(null), '');
  const out = html(L.RunPicker, { runs: [], run: null, onRun() {}, onSync() {}, syncing: false, synced: '', auto: 'Synced automatically every 2 min · last 10:05' });
  assert.ok(out.includes('Sync now') && out.includes('Synced automatically every 2 min'));
});

test('once a regression run was asked again, Rerun is done and nothing is left to point at (4 Nov 2026)', () => {
  const mode = { lineage_id: 'm1', name: 'overdue_reported_as_none', steps: { annotated: true, taxonomy: true, spec: true,
    correction: true, regression: true, rerun: true }, next: null };
  const chips = LP.stepChips(mode);
  assert.equal(chips.every((c) => c.done), true);
  assert.equal(chips.some((c) => c.next), false);
});

// ── the agent inside the container, and instruction changes in one place (Bala, 5 Nov 2026) ─────────────
const CHANGES = {
  proposed: [{ id: 'o1', skill: 'compliance', doc: 'answering', path: 'docs/compliance/answering.md', status: 'proposed',
    mode_name: 'blocked_and_at_risk_conflated', spec_rule: 'S-5', section: '## Blocked and at risk are two lists (S-5)\n\nAnswer two lists.',
    why: 'The answer added the groups.', by: 'boss@plenum.test' }],
  testing: [{ id: 'o2', skill: 'spec', doc: 'SPEC', path: 'docs/spec/SPEC.md', status: 'testing', spec_version: 3 }],
  live: [{ id: 'o3', skill: 'compliance', doc: 'core', path: 'docs/compliance/core.md', status: 'active', spec_rule: 'S-1' }]
};

test('instruction changes: proposed, under test, live, and the download for commit', () => {
  const out = html(LP.ChangesPanel, { changes: CHANGES, busy: false, onApprove() {}, onReject() {}, onLive() {}, onExport() {} });
  assert.ok(out.includes('docs/compliance/answering.md') && out.includes('Blocked and at risk are two lists (S-5)'));
  assert.ok(out.includes('Approve for testing') && out.includes('Reject'));
  assert.ok(out.includes('Under test') && out.includes('Make live') && out.includes('docs/spec/SPEC.md'));
  assert.ok(out.includes('Live') && out.includes('docs/compliance/core.md') && out.includes('Download for commit'));
  assert.ok(/only the reruns/i.test(out), 'says who reads text under test');
  const none = html(LP.ChangesPanel, { changes: { proposed: [], testing: [], live: [] } });
  assert.ok(none.includes('No instruction changes yet'));
  assert.equal(html(LP.ChangesPanel, { changes: null }), '', 'hidden for anyone who cannot change instructions');
});

test('the agent buttons: propose from notes, and draft a fix for a mode with a rule', () => {
  const mode = { lineage_id: 'm1', name: 'blocked_and_at_risk_conflated', positives: 1, corrections: 0, rule: { code: 'S-5' },
    steps: { annotated: true, taxonomy: true, spec: true }, next: 'correction', regression_runs: [] };
  const row = html(LP.ModeRow, { mode, canTaxonomy: true, canCurate: true, wide: true, companies: [], onDraftFix() {} });
  assert.ok(row.includes('Agent: draft fix'));
  const norule = html(LP.ModeRow, { mode: Object.assign({}, mode, { rule: null }), canTaxonomy: true, canCurate: true, onDraftFix() {} });
  assert.match(norule, /disabled=""[^>]*>Agent: draft fix|Agent: draft fix/);
  const page = html(LP.LoopView, { me: { all_companies: true, manages: true }, loop: { modes: [] }, proposals: {}, spec: {}, changes: CHANGES,
    on: { decide() {}, draft() {}, propose() {}, proposeFromNotes() {} } });
  assert.ok(page.includes('Agent: propose from notes'));
});

test('the agent reads only the run that is open, and says which', () => {
  const on = html(LP.Waiting, { data: {}, run: 'R40', onProposeFromNotes() {}, onDecide() {} });
  assert.ok(on.includes('Agent: propose from notes on R40'));
  assert.doesNotMatch(on, /disabled=""[^>]*>Agent: propose/);
  const off = html(LP.Waiting, { data: {}, run: null, onProposeFromNotes() {}, onDecide() {} });
  assert.match(off, /disabled=""[^>]*>Agent: propose from notes</, 'no run chosen: nothing to read');
});
