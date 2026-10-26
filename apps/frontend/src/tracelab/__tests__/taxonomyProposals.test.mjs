// taxonomyProposals — the AI suggestion queue and proposal runs (logic/proposals.js, taxonomyActions.js): a bulk queue
// that starts with nothing ticked, keeps its ticks across a refetch, selects only the visible page when asked, runs the
// audit before any approval and hides the AI's suggestion during the spot-check; one decision at a time; and proposal
// runs that start only after an estimate the cap covers.
import { test, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { taxonomyApi, A, C, S, X, tick, fresh, META, MODE, loaded, refusal, reads, installDefaults, restoreApis } from './tax-helper.mjs';
const Q = await import('../logic/proposals.js');

beforeEach(installDefaults);
afterEach(() => { mock.timers.reset(); restoreApis(); });

const sug = (i, extra) => Object.assign({ id: 'p-' + i, kind: 'depth_scan', run_id: 'r-1', turn_id: 't' + i, conversation_key: 's:c' + i, mode_id: 'm-1',
  mode_name: 'counts_cancelled_jobs_as_open', mode_definition: 'Present when cancelled jobs count as open.', suggestion: 'present',
  evidence: 'Counts job ' + i + ' although it was cancelled.', quote: 'job ' + i, status: 'pending', base: null, question: 'Question ' + i, notes: [] }, extra || {});
const PAGE = { ok: true, served_at: '2026-10-15T10:00:00+00:00', total: 3, hidden: 0, proposals: [sug(1), sug(2), sug(3)] };
// A company admin (the default) or the platform team (owner) with the queue's page of suggestions loaded by the test.
const qfresh = (extra, owner) => fresh(loaded(extra || {}, !!owner));
const queue = () => Q.taxonomyProposalsVals(S(), A).tlTaxQueue;
const runs = () => Q.taxonomyProposalsVals(S(), A).tlTaxRuns;

test('the bulk queue starts with nothing ticked, and a refetch keeps only the ticks still on the page', async () => {
  let page = PAGE;
  taxonomyApi.proposals = async (q) => { assert.deepEqual(q, { kind: 'depth_scan', status: 'pending', limit: 50, offset: 0 }); return page; };
  qfresh();
  assert.deepEqual(C.taxonomyInitial(1).tlProposalsSelected, []);
  await A.proposalsLoad();
  assert.deepEqual(queue().rows.map((r) => r.checked), [false, false, false]);
  assert.equal(queue().selectedCount, 0);
  queue().rows[0].toggle();
  queue().rows[2].toggle();
  assert.deepEqual(X().tlProposalsSelected, ['p-1', 'p-3']);
  page = Object.assign({}, PAGE, { proposals: [sug(2), sug(3), sug(4)] });
  await A.proposalsLoad();
  assert.deepEqual(X().tlProposalsSelected, ['p-3']);
  assert.deepEqual(queue().rows.map((r) => r.checked), [false, true, false]);
  assert.deepEqual(Q.keepSelected(['a', 'b'], [{ id: 'b' }, { id: 'c' }]), ['b']);
});

test('"Select all" ticks the visible page only, and only when clicked; grouping drafts have no ticks', async () => {
  taxonomyApi.proposals = async (q) => (q.kind === 'group'
    ? { ok: true, served_at: 's', total: 1, hidden: 0, proposals: [sug(9, { kind: 'group', suggestion: 'no_mode_fits', turn_id: null, mode_id: null, notes: [{ id: 'a-1', note: 'vague', step_label: 'Answer' }] })] }
    : Object.assign({}, PAGE, { total: 120 }));
  qfresh({}, true);
  await A.proposalsLoad();
  assert.equal(queue().selectedCount, 0);
  queue().selectPage();
  assert.deepEqual(X().tlProposalsSelected, ['p-1', 'p-2', 'p-3']);
  assert.equal(queue().pager.text, 'Suggestions 1–50 of 120');
  queue().clearSelection();
  assert.deepEqual(X().tlProposalsSelected, []);
  queue().kinds.find((k) => k.key === 'group').pick();
  await tick();
  const g = queue();
  assert.equal(g.bulk, false);
  assert.equal(g.rows[0].checkable, false);
  assert.equal(g.groupingNote, 'Proposed by AI from reviewers’ notes. Read every supporting note before accepting.');
  assert.equal(g.fixTest, 'Does fixing one fix the other?');
  qfresh({});
  assert.deepEqual(queue().kinds.map((k) => k.key), ['depth_scan', 'cell']);
  A.proposalsSetKind('group');
  assert.equal(X().tlProposalsKind, 'depth_scan', 'only the platform team reads grouping drafts');
});

test('Accept selected runs the audit first and shows what it refused; nothing is approved until the person approves', async () => {
  const calls = [];
  taxonomyApi.proposals = async () => PAGE;
  taxonomyApi.bulkCheck = async (body) => { calls.push(['check', body]); return { ok: true, allowed: false, refused: [{ rule: 2, distinct_share: 0.1, detail: 'The suggestions repeat the same evidence; decide them one at a time.' }],
    flagged: [], warnings: [], stats: { n: 2, write: 2 }, spot_check: { seed: 'abc', items: [] } }; };
  taxonomyApi.bulkApprove = async (body) => { calls.push(['approve', body]); return { ok: true, approved: [], skipped: [], labels: [] }; };
  qfresh();
  await A.proposalsLoad();
  queue().rows[0].toggle();
  queue().rows[1].toggle();
  await queue().acceptSelected();
  assert.deepEqual(calls, [['check', { action: 'accept', proposal_ids: ['p-1', 'p-2'], served_at: '2026-10-15T10:00:00+00:00' }]]);
  const k = queue().check;
  assert.deepEqual(k.refused.map((r) => [r.rule, r.title]), [[2, 'The evidence repeats']]);
  assert.equal(k.canApprove, false);
  assert.equal(queue().locked, true);
  queue().rows[2].toggle();
  assert.deepEqual(X().tlProposalsSelected, ['p-1', 'p-2'], 'ticks are frozen while the audit is open');
  assert.equal(calls.filter((x) => x[0] === 'approve').length, 0);
  k.cancel();
  assert.equal(queue().check, null);
});

test('nothing ticked says so, and dismissing in bulk is for depth-scan suggestions only', async () => {
  const calls = [];
  taxonomyApi.proposals = async () => Object.assign({}, PAGE, { proposals: [sug(1), sug(2, { kind: 'cell', suggestion: 'absent' })] });
  taxonomyApi.bulkCheck = async (body) => { calls.push(body); return { ok: true, allowed: true, refused: [], flagged: [], warnings: [], stats: { n: 1, write: 1 }, spot_check: { seed: 's', items: [] } }; };
  qfresh();
  await A.proposalsLoad();
  assert.equal(await queue().acceptSelected(), false);
  assert.equal(queue().error, 'Tick the suggestions to decide first.');
  assert.equal(queue().dismissSelected !== null, true);
  queue().rows[0].toggle();
  queue().rows[1].toggle();
  assert.equal(await A.proposalsBulkCheck('dismiss'), false);
  assert.equal(queue().error, 'Dismiss in bulk works on depth-scan suggestions only.');
  assert.deepEqual(calls, []);
  A.proposalsSetKind('cell');
  await tick();
  assert.equal(queue().dismissSelected, null, 'cell suggestions are accepted or flipped, not dismissed in bulk');
});

test('the spot-check hides the AI’s suggestion, decides with the seed, and only then can the rest be approved', async () => {
  const many = Array.from({ length: 22 }, (_, i) => sug(i + 1, { kind: 'cell', suggestion: 'absent', base: 'L-' + (i + 1) }));
  const sent = [];
  taxonomyApi.proposals = async () => ({ ok: true, served_at: '2026-10-15T09:00:00+00:00', total: 22, hidden: 0, proposals: many });
  taxonomyApi.bulkCheck = async () => ({ ok: true, allowed: true, refused: [], flagged: [], warnings: [{ rule: 8, count: 3, detail: '3 judgments are still missing in these answers.' }],
    stats: { n: 22, write: 17 }, spot_check: { seed: 'seed-1', items: [
      { proposal_id: 'p-4', turn_id: 't4', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', question: 'Question 4', base: 'L-4' },
      { proposal_id: 'p-9', turn_id: 't9', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', question: 'Question 9', base: 'L-9' }] } });
  taxonomyApi.setLabel = async (t, m, body) => { sent.push(['label', t, m, body]); return { ok: true, cell: {}, label: {} }; };
  taxonomyApi.bulkApprove = async (body) => { sent.push(['approve', body]); return { ok: true, approved: many.slice(0, 17).map((p) => p.id), skipped: [{ id: 'p-20', reason: 'stale' }], labels: [] }; };
  qfresh({ tlModes: [{ id: 'm-1', name: 'counts_cancelled_jobs_as_open', definition: 'Present when cancelled jobs count as open.' }] });
  await A.proposalsLoad();
  queue().selectPage();
  await queue().acceptSelected();
  let k = queue().check;
  assert.equal(k.spotText, 'Spot-check: decide these 2 answers yourself first. The AI’s suggestion is hidden.');
  assert.deepEqual(k.warnings, ['3 judgments are still missing in these answers.']);
  assert.deepEqual(Object.keys(k.spot[0]).filter((x) => /suggest|evidence|quote/i.test(x)), []);
  assert.equal(k.spot[0].definition, 'Present when cancelled jobs count as open.');
  const row4 = queue().rows.find((r) => r.id === 'p-4');
  assert.deepEqual([row4.suggestion, row4.evidence, row4.quote], ['Hidden while you do the spot-check', '', '']);
  assert.equal(k.canApprove, false);
  assert.equal(await A.proposalsBulkApprove(), false);
  assert.equal(X().tlProposalsErr, 'Decide every spot-check answer before approving the rest.');
  await k.spot[0].absent();
  await queue().check.spot[1].present();
  k = queue().check;
  assert.equal(k.spotDone, true);
  assert.equal(k.canApprove, true);
  assert.equal(k.approveLabel, 'Approve 17');
  await k.approve();
  assert.deepEqual(sent.slice(0, 2), [['label', 't4', 'm-1', { present: false, base: 'L-4', spot_check_seed: 'seed-1' }],
    ['label', 't9', 'm-1', { present: true, base: 'L-9', spot_check_seed: 'seed-1' }]]);
  const approve = sent[2][1];
  assert.equal(approve.action, 'accept');
  assert.equal(approve.served_at, '2026-10-15T09:00:00+00:00');
  assert.equal(approve.proposal_ids.length, 22);
  assert.equal(approve.bases['p-4'], 'L-4');
  assert.equal(queue().result.text, 'Approved 17. Skipped 1: 1 changed since you loaded them.');
  assert.equal(queue().check, null);
  queue().result.dismiss();
  assert.equal(queue().result, null);
});

test('a refusal at approval time puts the audit’s findings back on screen', async () => {
  taxonomyApi.proposals = async () => PAGE;
  taxonomyApi.bulkCheck = async () => ({ ok: true, allowed: true, refused: [], flagged: [], warnings: [], stats: { n: 1, write: 1 }, spot_check: { seed: 's', items: [] } });
  taxonomyApi.bulkApprove = async () => { throw Object.assign(new Error('The checks refused this bulk approval.'), { status: 422, reason: 'bulk_refused',
    body: { detail: { ok: false, reason: 'bulk_refused', error: 'The checks refused this bulk approval.', refused: [{ rule: 7, retry_after_s: 12, detail: 'Read the suggestions before approving them: 12 more seconds.' }] } } }); };
  qfresh();
  await A.proposalsLoad();
  queue().rows[0].toggle();
  await queue().acceptSelected();
  assert.equal(await queue().check.approve(), false);
  const k = queue().check;
  assert.equal(k.allowed, false);
  assert.deepEqual(k.refused.map((r) => r.rule), [7]);
  assert.equal(k.waitText, 'Read the suggestions first: check again in 12 seconds.');
  assert.equal(queue().error, 'The checks refused this bulk approval.');
  taxonomyApi.bulkCheck = async () => ({ ok: true, allowed: true, refused: [], flagged: [], warnings: [], stats: { n: 1, write: 1 }, spot_check: { seed: 's', items: [] } });
  assert.equal(await queue().check.recheck(), true);
  assert.equal(queue().check.allowed, true, 'checking again asks the audit again');
});

test('one decision at a time: depth scans accept or dismiss, cell proposals take the other value as an override', async () => {
  const sent = [];
  taxonomyApi.proposals = async () => ({ ok: true, served_at: 's', total: 2, hidden: 0, proposals: [sug(1, { base: 'L-1' }), sug(2, { kind: 'cell', suggestion: 'absent' })] });
  taxonomyApi.decideProposal = async (id, body) => { sent.push([id, body]); return { ok: true, proposal: { id, status: 'accepted' }, label: {} }; };
  qfresh();
  await A.proposalsLoad();
  const [depth, cellRow] = queue().rows;
  assert.deepEqual([depth.acceptLabel, depth.otherLabel], ['Accept: it shows this', 'Dismiss: it does not']);
  assert.match(depth.otherHint, /close example where the failure is absent/);
  assert.deepEqual([cellRow.acceptLabel, cellRow.otherLabel], ['Accept: absent', 'Mark present']);
  await depth.other();
  await cellRow.other();
  assert.deepEqual(sent, [['p-1', { decision: 'dismiss', present: null, base: 'L-1' }], ['p-2', { decision: 'override', present: true, base: null }]]);
  assert.equal(reads.progress, 2, 'Progress is read again after each decision');
});

test('a grouping draft is decided by the platform team alone; accepting a new mode sends its edited draft only when it is whole', async () => {
  const sent = [];
  const draft = { name: 'counts_on_hold', definition: 'Present when jobs on hold count as open.', boundary: '', level: 'technical', agent: 'wo_engine' };
  taxonomyApi.proposals = async () => ({ ok: true, served_at: 's', total: 1, hidden: 0, proposals: [sug(7, { kind: 'group', suggestion: 'new_mode', turn_id: null, mode_id: null,
    draft: Object.assign({}, draft, { definition: 'short' }), notes: [{ id: 'a-1', note: 'On hold counted', step_label: 'Answer' }] })] });
  taxonomyApi.decideProposal = async (id, body) => { sent.push([id, body]); return { ok: true, proposal: { id } }; };
  qfresh({ tlProposalsKind: 'group' }, true);
  await A.proposalsLoad();
  const row = queue().rows[0];
  assert.equal(row.suggestion, 'New failure mode: counts_on_hold');
  assert.equal(row.otherHint, 'Dismiss files nothing: the notes stay unfiled.');
  assert.equal(await row.accept(), false);
  assert.match(X().tlProposalsErr, /^Definition:/);
  assert.deepEqual(sent, []);
  row.draft.set('definition')({ target: { value: draft.definition } });
  assert.equal(queue().rows[0].draft.definition, draft.definition);
  assert.equal(await queue().rows[0].accept(), true);
  assert.equal(sent[0][1].decision, 'accept');
  assert.deepEqual(sent[0][1].edits, Object.assign({ boundary: '', nearest_mode_id: null, evaluator_type: null, requirement_ids: [] }, draft));
  // The same draft for a company admin has no Accept or Dismiss.
  qfresh({ tlProposalsKind: 'depth_scan' }, false);
  assert.equal(await A.proposalsDecide('p-7', 'accept'), false, 'a draft this page does not hold is not decided');
});

test('a run starts only after an estimate the cap covers, with the estimated request', async () => {
  const sent = [];
  taxonomyApi.proposalEstimate = async (q) => { sent.push(['estimate', q]); return { ok: true, model: 'claude-sonnet-5', price_known: true, items: 40, canaries: 10,
    expected_usd: 1.2, worst_usd: 3.4, run_cap_max: 25, month_spent: 12, month_cap: 100 }; };
  taxonomyApi.startProposalRun = async (body) => { sent.push(['start', body]); return { ok: true, run: { id: 'r-1' }, job: { id: 'j-1', status: 'queued' } }; };
  qfresh({}, true);
  let r = runs();
  assert.equal(r.startProblem, 'Estimate the cost first.');
  r.form.set('kind')({ target: { value: 'cells' } });
  r.form.set('usd_cap')({ target: { value: '1' } });
  await runs().runEstimate();
  r = runs();
  assert.equal(r.estimate.text, '40 items to read, plus 10 known-answer checks. Expected $1.20, at most $3.40. This month: $12.00 of $100.00 spent on AI proposals; one run may cap at $25.00.');
  assert.equal(r.startProblem, 'The estimate is above the cap you set.');
  assert.equal(await A.proposalsStartRun(), false);
  r.form.set('usd_cap')({ target: { value: '95' } });
  assert.equal(runs().startProblem, 'That cap is above the largest allowed for one run ($25.00).');
  r.form.set('usd_cap')({ target: { value: '5' } });
  assert.equal(runs().startProblem, '');
  assert.equal(await A.proposalsStartRun(), true);
  const est = sent[0][1];
  assert.equal(est.kind, 'cells');
  assert.equal(est.include_candidates, false);
  assert.deepEqual(sent[1][1], Object.assign({}, est, { usd_cap: 5 }));
  r.form.set('days')({ target: { value: '7' } });
  assert.equal(runs().startProblem, 'Estimate the cost first.', 'a changed form needs a new estimate');
  assert.equal(Q.startProblem({ kind: 'cells', usd_cap: 5, days: 30, limit: 200 }, { form: 'cells||30|200|false', price_known: false, items: 3 }),
    'The model’s price is not known, so the run’s spending cannot be capped.');
  assert.equal(Q.startProblem({ kind: 'cells', usd_cap: 5, days: 30, limit: 200 }, { form: 'cells||30|200|false', price_known: true, items: 0 }),
    'There is nothing for this run to read.');
  assert.equal(Q.startProblem({ kind: 'cells', usd_cap: 0, days: 30, limit: 200 }, { form: 'cells||30|200|false', price_known: true, items: 3, expected_usd: 0.5 }),
    'Set a cost cap in dollars.');
  assert.match(Q.startProblem({ kind: 'cells', usd_cap: 20, days: 30, limit: 200 }, { form: 'cells||30|200|false', price_known: true, items: 3, expected_usd: 0.5, month_spent: 90, month_cap: 100 }),
    /^This month’s spending on AI proposals would pass its cap/);
});

test('a depth scan needs a failure mode chosen before it can be estimated, and a refused start keeps the estimate and says why', async () => {
  taxonomyApi.proposalEstimate = async () => ({ ok: true, price_known: true, items: 3, canaries: 0, expected_usd: 0.4, worst_usd: 1, run_cap_max: 25, month_spent: 0, month_cap: 100 });
  taxonomyApi.startProposalRun = async () => { throw refusal(422, 'month_cap', 'This month’s budget for AI proposals is used up.'); };
  qfresh({}, true);
  assert.equal(await A.proposalsEstimateRun(), false);
  assert.equal(X().tlProposalsEstimateErr, 'Choose the failure mode to look for.');
  runs().form.set('mode_id')({ target: { value: 'm-1' } });
  runs().form.set('usd_cap')({ target: { value: '2' } });
  assert.equal(await A.proposalsEstimateRun(), true);
  assert.equal(runs().startProblem, '');
  assert.equal(await A.proposalsStartRun(), false);
  assert.equal(X().tlProposalsEstimateErr, 'This month’s budget for AI proposals is used up.');
  assert.ok(X().tlProposalsEstimate, 'the estimate stays for another try');
});

test('runs list their spend and offer Pause or Resume; the platform team alone sees them', async () => {
  taxonomyApi.proposalRuns = async () => ({ ok: true, runs: [
    { id: 'r-1', kind: 'depth_scan', mode_id: 'm-1', estimate_usd: 1.4, canary_total: 10, canary_agree: 9, created_by_email: 'team@plenum.example', created_at: '2026-10-15T09:00:00Z',
      job: { status: 'running', done: 30, total: 110, usd_spent: 0.41, usd_cap: 3 } },
    { id: 'r-2', kind: 'cells', mode_id: null, estimate_usd: 2, canary_total: 0, canary_agree: 0, created_at: '2026-10-14T09:00:00Z',
      job: { status: 'capped', done: 80, total: 100, usd_spent: 3, usd_cap: 3 } }] });
  const sent = [];
  taxonomyApi.resumeProposalRun = async (id, body) => { sent.push(['resume', id, body]); return { ok: true, run: { id }, job: { status: 'queued' } }; };
  taxonomyApi.pauseProposalRun = async (id) => { sent.push(['pause', id]); return { ok: true, run: { id }, job: { status: 'paused' } }; };
  qfresh({ tlModes: [{ id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed' }], tlModesMeta: META(true) });
  await A.proposalsRunsLoad();
  const list = runs().list;
  assert.deepEqual(list.map((x) => [x.kindText, x.modeName, x.statusText, x.progress, x.spend, !!x.pause, !!x.resume]), [
    ['Find more answers like one failure mode', 'counts_cancelled_jobs_as_open', 'Running', '30 of 110', '$0.41 of $3.00', true, false],
    ['Suggest present or absent for missing judgments', '', 'Stopped at its cost cap', '80 of 100', '$3.00 of $3.00', false, true]]);
  assert.equal(list[0].canary, '9 of 10 known answers agreed');
  await list[0].pause();
  list[1].setResumeCap({ target: { value: '6' } });
  assert.equal(runs().list[1].resumeCap, '6');
  await runs().list[1].resume();
  assert.deepEqual(sent, [['pause', 'r-1'], ['resume', 'r-2', { usd_cap: 6 }]]);
  qfresh({});
  assert.equal(runs(), null);
});

test('a waiting or running run is read again every 5 seconds while Progress is on screen, and a settled one reloads the queue', async () => {
  // Mocked timers also stop the helper's own tick(), so this test waits on microtasks only.
  const flush = async () => { for (let i = 0; i < 12; i += 1) await Promise.resolve(); };
  mock.timers.enable({ apis: ['setTimeout'] });
  let status = 'running';
  let reads2 = 0;
  taxonomyApi.proposalRuns = async () => { reads2 += 1; return { ok: true, runs: [{ id: 'r-1', kind: 'cells', job: { status } }] }; };
  let queueReads = 0;
  taxonomyApi.proposals = async () => { queueReads += 1; return PAGE; };
  qfresh({}, true);
  await A.proposalsRunsLoad();
  assert.equal(reads2, 1);
  mock.timers.tick(5000);
  await flush();
  assert.equal(reads2, 2);
  status = 'done';
  mock.timers.tick(5000);
  await flush();
  assert.equal(reads2, 3);
  assert.ok(queueReads >= 1, 'the run settled, so the queue is read again');
  mock.timers.tick(20000);
  await flush();
  assert.equal(reads2, 3, 'nothing runs, so nothing is polled');
  // Away from Progress the timer reads nothing.
  status = 'running';
  fresh(loaded({}, true), { tab: 'conversations' });
  await A.proposalsRunsLoad();
  assert.equal(reads2, 4);
  mock.timers.tick(5000);
  await flush();
  assert.equal(reads2, 4);
  A.stopPolls();
  fresh(loaded({}, true), { tab: 'progress' });
  await A.proposalsRunsLoad();
  assert.equal(reads2, 5);
  A.stopPolls();
  mock.timers.tick(20000);
  await flush();
  assert.equal(reads2, 5, 'stopping the polls stops the timer');
});

test('what Progress reads when it opens: modes, unfiled notes, the queue and, for the platform team, the runs', async () => {
  const asked = [];
  let owner = false;
  taxonomyApi.modes = async () => { asked.push('modes'); return { ok: true, is_owner: owner, modes: [MODE], agents: [], levels: [] }; };
  taxonomyApi.unfiledNotes = async () => { asked.push('unfiled'); return { ok: true, notes: [], total: 0, hidden: 0 }; };
  taxonomyApi.proposals = async () => { asked.push('proposals'); return PAGE; };
  taxonomyApi.proposalRuns = async () => { asked.push('runs'); return { ok: true, runs: [] }; };
  fresh();
  await A.taxEnsure();
  await tick();
  assert.deepEqual(asked.slice().sort(), ['modes', 'proposals', 'unfiled'], 'a company admin reads no runs');
  asked.length = 0;
  await A.taxEnsure();
  assert.deepEqual(asked, [], 'everything already read is not read again');
  owner = true;
  fresh();
  await A.taxEnsure();
  await tick();
  assert.deepEqual(asked.slice().sort(), ['modes', 'proposals', 'runs', 'unfiled'], 'who the person is arrives with the modes, and the platform team also reads the runs');
  asked.length = 0;
  A.stopPolls();
  await A.taxEnsure();
  assert.deepEqual(asked, [], 'a second visit by the platform team reads nothing it already has');
});

test('the proposals slice keys are all in the reset, and each reset starts with nothing ticked', () => {
  const dir = new URL('..', import.meta.url);
  const src = ['taxonomyActions.js', 'logic/proposals.js', 'logic/taxCommon.js'].map((f) => readFileSync(new URL(f, dir), 'utf8')).join('\n');
  const keys = new Set([...src.matchAll(/\b(tlProposals[A-Za-z]*)\s*:/g)].map((m) => m[1]));
  const patch = C.taxonomyInitial(1);
  assert.ok(keys.size >= 20, [...keys].join(','));
  for (const k of keys) assert.ok(Object.prototype.hasOwnProperty.call(patch, k), k);
  assert.deepEqual(patch.tlProposalsSelected, []);
  assert.notEqual(C.taxonomyInitial(1).tlProposalsSelected, patch.tlProposalsSelected);
  assert.notEqual(C.taxonomyInitial(1).tlProposalsRunForm, patch.tlProposalsRunForm);
});

test('an estimate belongs to the form it was made for: a changed window, mode or candidate choice needs a new one', async () => {
  const sent = [];
  taxonomyApi.proposalEstimate = async (q) => { sent.push(['estimate', q]); return { ok: true, price_known: true, items: 4, canaries: 0, expected_usd: 0.4, worst_usd: 1, run_cap_max: 25, month_spent: 0, month_cap: 100 }; };
  taxonomyApi.startProposalRun = async (body) => { sent.push(['start', body]); return { ok: true, run: { id: 'r-1' }, job: { id: 'j-1', status: 'queued' } }; };
  qfresh({}, true);
  const set = (field, value) => runs().form.set(field)({ target: { value } });
  set('kind', 'cells');
  set('usd_cap', '5');
  await A.proposalsEstimateRun();
  assert.equal(runs().startProblem, '');
  set('days', '7');
  assert.equal(runs().startProblem, 'Estimate the cost first.', 'a different window');
  assert.equal(await A.proposalsStartRun(), false);
  set('days', '30');
  assert.equal(runs().startProblem, '', 'back to the form the estimate was made for');
  runs().form.set('include_candidates')({ target: { type: 'checkbox', checked: true } });
  assert.equal(runs().startProblem, 'Estimate the cost first.', 'candidates are included now');
  runs().form.set('include_candidates')({ target: { type: 'checkbox', checked: false } });
  set('limit', '50');
  assert.equal(runs().startProblem, 'Estimate the cost first.', 'a different limit');
  assert.equal(await A.proposalsStartRun(), false);
  assert.deepEqual(sent.map((x) => x[0]), ['estimate'], 'nothing was started on a stale estimate');
  set('limit', '200');
  set('kind', 'depth_scan');
  set('mode_id', 'm-1');
  await A.proposalsEstimateRun();
  assert.equal(runs().startProblem, '');
  set('mode_id', 'm-2');
  assert.equal(runs().startProblem, 'Estimate the cost first.', 'a different failure mode');
  assert.equal(Q.formKey({ kind: 'cells', mode_id: 'm-1', days: 30, limit: 200, include_candidates: false }),
    Q.formKey({ kind: 'cells', mode_id: 'm-2', days: 30, limit: 200, include_candidates: false }), 'a cells run does not depend on a mode');
});

test('grouping drafts can be neither ticked nor selected, even when asked directly', async () => {
  taxonomyApi.proposals = async () => ({ ok: true, served_at: 's', total: 1, hidden: 0,
    proposals: [sug(9, { kind: 'group', suggestion: 'no_mode_fits', turn_id: null, mode_id: null, notes: [{ id: 'a-1', note: 'vague', step_label: 'Answer' }] })] });
  qfresh({}, true);
  A.proposalsSetKind('group');
  await tick();
  assert.equal(X().tlProposals.length, 1);
  A.proposalsSelectPage();
  A.proposalsToggle('p-9');
  assert.deepEqual(X().tlProposalsSelected, []);
  assert.equal(Q.checkable(X().tlProposals[0]), false);
  assert.equal(Q.checkable(sug(1)), true);
  assert.equal(Q.checkable(sug(1, { kind: 'cell' })), true);
  assert.equal(Q.checkable(null), false);
});

test('checking the same list again keeps the spot-check answers; a list read again at another time starts them over', async () => {
  let served = 's-1';
  taxonomyApi.proposals = async () => ({ ok: true, served_at: served, total: 3, hidden: 0, proposals: [sug(1), sug(2), sug(3)] });
  taxonomyApi.bulkCheck = async () => ({ ok: true, allowed: true, refused: [], flagged: [], warnings: [], stats: { n: 3, write: 3 },
    spot_check: { seed: 'sd', items: [{ proposal_id: 'p-2', turn_id: 't2', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', question: 'Question 2', base: null }] } });
  taxonomyApi.setLabel = async () => ({ ok: true, cell: {}, label: {} });
  qfresh();
  await A.proposalsLoad();
  queue().selectPage();
  assert.equal(await A.proposalsBulkCheck('accept'), true);
  assert.equal(await A.proposalsSpotDecide('p-2', true), true);
  assert.deepEqual(X().tlProposalsSpot, { 'p-2': true });
  assert.equal(await A.proposalsRecheck(), true);
  assert.deepEqual(X().tlProposalsSpot, { 'p-2': true }, 'the same list, so the same spot-check: the answer stands');
  served = 's-2';
  await A.proposalsLoad();
  assert.equal(await A.proposalsRecheck(), true);
  assert.deepEqual(X().tlProposalsSpot, {}, 'a list served at another time draws its own spot-check');
  assert.equal(X().tlProposalsCheckServedAt, 's-2');
  assert.equal(await A.proposalsSpotDecide('p-404', true), false, 'not a spot-check answer');
});

test('paging the queue waits while an audit is open, and never moves past the first or last page', async () => {
  let asks = 0;
  const offsets = [];
  taxonomyApi.proposals = async (q) => { asks += 1; offsets.push(q.offset); return Object.assign({}, PAGE, { total: 120 }); };
  taxonomyApi.bulkCheck = async () => ({ ok: true, allowed: true, refused: [], flagged: [], warnings: [], stats: {}, spot_check: { seed: 's', items: [] } });
  qfresh();
  await A.proposalsLoad();
  A.proposalsPage(-1);
  assert.equal(X().tlProposalsOffset, 0, 'there is no page before the first');
  A.proposalsPage(1);
  await tick();
  assert.equal(X().tlProposalsOffset, 50);
  A.proposalsPage(5);
  await tick();
  assert.equal(X().tlProposalsOffset, 100, 'the last page starts at 100 for 120 suggestions');
  queue().selectPage();
  await A.proposalsBulkCheck('accept');
  const before = asks;
  A.proposalsPage(-1);
  await tick();
  assert.equal(X().tlProposalsOffset, 100, 'ticks and pages are frozen while the audit is open');
  assert.equal(asks, before);
  assert.deepEqual(offsets, [0, 50, 100]);
});
