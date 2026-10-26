// watchVals — the Watch tab's view models: watchVals(s, a, now, company) turns the store state into plain data the screen
// draws. Pinned: with no batches the tab says so and draws nothing; the Due marker; the chart of each failure mode as geometry
// from the server's points; the automatic-vs-people shares; who sees what (a company admin sees no Run now, the platform team
// sees no eval-case actions and is told why, a superadmin viewing a company reads that company's cases and starts, resumes
// and reviews that company's own monitoring batches, a company admin never does); the review-batch
// action; the rules for each case's buttons; the errors of the writes; and that a missing number reads "—", never zero or
// "undefined". The buttons the models carry call the real actions, with every request stubbed.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

import { net, S, Wx, route, reqs, flush, wfresh, installDefaults, setActingOrg,
  BATCHES, BATCH_ROW, BATCH_DETAIL, BATCH_INTERRUPTED, CHART_M1, CHART_M2, CHECKS, AUTO, PLAN, CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE, CASE_CAPABILITY,
  RELEASES, DUE } from './watch-helper.mjs';

const A = await import('../watchActions.js');
const V = await import('../logic/watch.js');
const { fmtWhen } = await import('../../logic/tracesPage.js');

beforeEach(installDefaults);
const NOW = Date.parse('2026-10-15T12:00:00Z');
const h = (method, path, reply) => { net.handlers[route(method, path)] = reply; };
const vals = (company) => V.watchVals(S(), A, NOW, company);
const admin = (watch, opts) => wfresh(watch, Object.assign({ owner: false }, opts));
const platform = (watch, opts) => wfresh(watch, Object.assign({ owner: true }, opts));
const loaded = { tlMonitorLoadedAt: 1, tlMonitorBatches: BATCHES, tlMonitorCharts: { m1: { chart: CHART_M1 }, m2: { chart: CHART_M2 } }, tlMonitorChecks: CHECKS.checks, tlAuto: AUTO, tlAutoLoadedAt: 1 };

test('with no batches the tab says so and draws nothing', () => {
  admin({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } });
  const v = vals();
  assert.equal(v.tlwBatchesEmpty, true);
  assert.equal(v.tlwEmptyText, 'No batches yet. Monitoring has not run for this scope.');
  assert.deepEqual(v.tlwBatches, []);
  assert.deepEqual(v.tlwCharts, []);
  assert.deepEqual(v.tlwChecks, []);
  assert.equal(v.tlwLoading, false);
  assert.equal(v.tlwBatch, null);
  assert.equal(v.tlwPlan, null);
});

test('before the first read the tab is loading, not empty, and after a failed read it says what failed', () => {
  admin({ tlMonitorLoading: true });
  assert.equal(vals().tlwLoading, true);
  assert.equal(vals().tlwBatchesEmpty, false);
  admin({ tlMonitorLoading: true, tlMonitorLoadedAt: 5, tlMonitorBatches: BATCHES });
  assert.equal(vals().tlwLoading, false, 'a refresh keeps the rows on screen');
  admin({ tlMonitorErr: 'monitoring is unavailable', tlMonitorChecksErr: 'checks are down' });
  assert.equal(vals().tlwError, 'monitoring is unavailable');
  assert.equal(vals().tlwChecksError, 'checks are down');
  assert.equal(vals().tlwBatchesEmpty, false, 'a failed read is not "no batches"');
  admin({ tlMonitorLoadedAt: 3, tlMonitorBatches: { batches: [] }, tlMonitorErr: 'a refresh failed' });
  assert.equal(vals().tlwBatchesEmpty, false, 'nor is a failed refresh after an empty read');
  admin({ tlMonitorLoadedAt: 3, tlMonitorBatches: { batches: [] } });
  assert.equal(vals().tlwBatchesEmpty, true);
});

test('the Due marker names the week that still needs a batch', () => {
  admin({ tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } });
  assert.equal(vals().tlwDue.isDue, true);
  assert.equal(vals().tlwDue.text, 'Due: 2026-W41 has not been checked yet');
  admin({});
  assert.equal(vals().tlwDue, null);
});

test('batches read as rows with their status, scope, cost and the Above-threshold flag; missing counts read as a dash', () => {
  platform(loaded);
  const rows = vals().tlwBatches;
  assert.deepEqual(rows.map((r) => [r.id, r.period, r.statusText, r.scope, r.cost, r.crossed]),
    [['b1', '2026-W41', 'Done', 'All companies', '$1.23', true], ['b0', '2026-W40', 'Interrupted, resume it', 'One company', '$0.40', false]]);
  assert.deepEqual([rows[0].nWindow, rows[0].nRandom], ['412', '100']);
  assert.deepEqual([rows[1].nWindow, rows[1].nRandom], ['—', '—'], 'an interrupted batch has no counts yet');
  platform({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [{ id: 'b2', status: 'weird', scope: 'company' }] } });
  const odd = vals().tlwBatches[0];
  assert.deepEqual([odd.statusText, odd.cost, odd.period], ['weird', '—', '—']);
});

const PLATFORM_INTERRUPTED = Object.assign({}, BATCH_INTERRUPTED, { id: 'b5', scope: 'platform' });
const MIXED = { batches: [BATCH_ROW, BATCH_INTERRUPTED, PLATFORM_INTERRUPTED], due: DUE };

test('only the platform team can resume an interrupted batch, only an interrupted one, and from the view it was started in', async () => {
  platform(Object.assign({}, loaded, { tlMonitorBatches: MIXED }));
  const rows = vals().tlwBatches;
  assert.deepEqual(rows.map((r) => [r.id, r.scope, typeof r.resume]),
    [['b1', 'All companies', 'object'], ['b0', 'One company', 'object'], ['b5', 'All companies', 'function']],
    'across every company, only a platform-wide batch is resumed (a company\'s is resumed from that company)');
  admin(Object.assign({}, loaded, { tlMonitorBatches: MIXED }));
  assert.deepEqual(vals().tlwBatches.map((r) => r.resume), [null, null, null], 'a company admin never resumes a batch');
  wfresh(Object.assign({}, loaded, { tlMonitorBatches: MIXED }), { owner: null });
  assert.deepEqual(vals().tlwBatches.map((r) => r.resume), [null, null, null], 'not before the service has said who the person is');
  setActingOrg('org-a');
  admin(Object.assign({}, loaded, { tlMonitorBatches: { batches: [Object.assign({}, BATCH_ROW, { scope: 'company' }), BATCH_INTERRUPTED], due: DUE } }));
  const mine = vals().tlwBatches;
  assert.deepEqual(mine.map((r) => [r.id, typeof r.resume]), [['b1', 'object'], ['b0', 'function']],
    'a superadmin viewing a company resumes that company\'s interrupted batch');
  h('POST', '/monitor/batches/b0/resume', [202, { ok: true }]);
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  await mine[1].resume();
  const sent = net.requests.find((r) => r.method === 'POST' && r.url.pathname.endsWith('/b0/resume'));
  assert.equal(sent.url.searchParams.get('organization_id'), 'org-a', 'the company the person views is the one asked for');
  setActingOrg(null);
  platform(Object.assign({}, loaded, { tlMonitorBatches: MIXED, tlWatchWrites: { 'watch:resume:b5': { busy: true, error: '', conflict: false, canRetry: false } } }));
  assert.deepEqual(vals().tlwBatches.map((r) => r.resuming), [false, false, true]);
  net.requests = [];
  platform(Object.assign({}, loaded, { tlMonitorBatches: MIXED }));
  await vals().tlwBatches[2].resume();
  assert.ok(reqs().includes('POST /monitor/batches/b5/resume'));
  assert.equal(net.requests[0].url.searchParams.has('organization_id'), false, 'viewing no company names none');
});

test('each failure mode gets a chart drawn from the server\'s points, with a state for reading and for failing', () => {
  platform(Object.assign({}, loaded, { tlMonitorCharts: { m1: { chart: CHART_M1 }, m2: { loading: true } } }));
  const [one, two] = vals().tlwCharts;
  assert.deepEqual([one.modeId, one.name, one.crossed, one.loading, one.error], ['m1', 'wrong_period', true, false, '']);
  assert.equal(one.geometry.empty, false);
  assert.equal(one.geometry.points.length, 2);
  assert.equal(one.geometry.thresholdLabel, 'threshold 0.15');
  assert.deepEqual([two.modeId, two.loading, two.geometry.empty], ['m2', true, true]);
  platform(Object.assign({}, loaded, { tlMonitorCharts: { m1: { error: 'no chart' } } }));
  const [failed, absent] = vals().tlwCharts;
  assert.equal(failed.error, 'no chart');
  assert.equal(failed.geometry.empty, true);
  assert.deepEqual([absent.loading, absent.error, absent.geometry.empty], [false, '', true], 'a chart not asked for yet is empty, not an error');
});

test('code-check rates show the last six weeks, each with its rate and the number of answers, and say if the rate is validated', () => {
  const weeks = [1, 2, 3, 4, 5, 6, 7, 8].map((i) => ({ period_label: '2026-W' + (30 + i), n: i * 10, flagged: i, rate: i / 100 }));
  platform({ tlMonitorChecks: [{ evaluator_key: 'code:a@v1', label: 'Gives no answer', validated: true, weeks }, CHECKS.checks[1]] });
  const [a, b] = vals().tlwChecks;
  assert.equal(a.weeks.length, 6);
  assert.deepEqual(a.weeks[0], { period: '2026-W33', rate: '3%', n: 30 }, 'the first two of eight are dropped');
  assert.equal(a.weeks[5].period, '2026-W38');
  assert.deepEqual([a.tag, b.tag], ['validated', 'raw rate only']);
  assert.equal(b.weeks[0].rate, '—', 'no decided answers: a dash, not 0%');
});

test('the automatic and people shares of each week, with an empty week as a dash and the server\'s definition', () => {
  platform(loaded);
  const t = vals().tlwAuto;
  assert.deepEqual([t.weeks[0].autoText, t.weeks[0].peopleText, t.weeks[0].golden, t.weeks[0].live], ['40%', '60%', '50% of 2 (10% to 90%)', '—']);
  assert.equal(t.weeks[1].empty, true);
  assert.equal(t.empty, false);
  assert.equal(t.note, 'Automatic: golden-case checks and judges that passed their held-out test.');
  admin({ tlAuto: { weeks: [{ week: 'w', checked_any: 0 }] }, tlAutoLoadedAt: 1 });
  assert.equal(vals().tlwAuto.empty, true);
  assert.equal(vals().tlwAuto.note, '');
  admin({ tlAutoLoading: true });
  assert.equal(vals().tlwAuto.loading, true);
  admin({ tlAutoErr: 'trend is down' });
  assert.equal(vals().tlwAuto.error, 'trend is down');
});

test('a company admin sees no Run now, and the platform team sees no eval-case actions and is told why', () => {
  admin({});
  const a = vals('Northbridge Estates Ltd');
  assert.equal(a.tlwCanStart, false);
  assert.equal(a.tlwCanCases, true);
  assert.equal(a.tlwCasesHint, '');
  assert.equal(a.tlwScope, 'Company: Northbridge Estates Ltd');
  platform({});
  const p = vals('Plenum');
  assert.equal(p.tlwCanStart, true);
  assert.equal(p.tlwCanCases, false);
  assert.match(p.tlwCasesHint, /view as a company/);
  assert.equal(p.tlwScope, 'All companies');
  assert.equal(typeof p.tlwRunNow, 'function');
});

test('a superadmin viewing a company works that company\'s cases and starts monitoring for it; a company admin only reads', () => {
  setActingOrg('org-a');
  admin({});                                          // the service answers is_owner false for a company it was told to act for
  const v = vals('Northbridge');
  assert.equal(v.tlwCanStart, true);
  assert.equal(typeof v.tlwRunNow, 'function');
  assert.equal(v.tlwCanCases, true);
  assert.equal(v.tlwScope, 'Company: Northbridge');
  assert.equal(v.tlwCasesHint, '');
  platform({});                                       // a list read before the View as began does not change that
  assert.equal(vals('Northbridge').tlwCanStart, true);
  setActingOrg(null);
  admin({});
  assert.equal(vals('Northbridge').tlwCanStart, false, 'a company admin');
  platform({});
  assert.equal(vals('Plenum').tlwCanStart, true, 'the platform team viewing every company');
});

test('the plan says whose batch it would run: the company viewed, or every company', () => {
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN });
  assert.equal(vals('Plenum').tlwPlan.scopeText, 'Covers every company.');
  setActingOrg('org-a');
  admin({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN });
  assert.equal(vals('Northbridge').tlwPlan.scopeText, 'Covers Northbridge only.');
  assert.equal(vals('').tlwPlan.scopeText, 'Covers this company only.', 'a company with no name on record');
  assert.equal(vals('  ').tlwPlan.scopeText, 'Covers this company only.');
});

test('a company admin reads the company\'s own batches, its Due marker and its charts, and is offered nothing to start', () => {
  const own = Object.assign({}, BATCH_ROW, { scope: 'company' });
  admin({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [own], due: { period_label: '2026-W41', is_due: false } },
    tlMonitorCharts: { m1: { chart: CHART_M1 }, m2: { chart: CHART_M2 } }, tlMonitorChecks: CHECKS.checks });
  const v = vals('Northbridge');
  assert.equal(v.tlwScope, 'Company: Northbridge');
  assert.deepEqual(v.tlwDue, { text: 'Up to date: 2026-W41 checked', isDue: false });
  assert.deepEqual(v.tlwBatches.map((r) => [r.id, r.scope, r.resume]), [['b1', 'One company', null]]);
  assert.equal(v.tlwBatchesEmpty, false);
  assert.deepEqual(v.tlwCharts.map((c) => [c.modeId, c.geometry.empty]), [['m1', false], ['m2', false]]);
  assert.equal(v.tlwChecks.length, 2);
  assert.equal(v.tlwCanStart, false);
  assert.equal(v.tlwPlan, null);
  admin({ tlMonitorLoadedAt: 1, tlMonitorBatches: { batches: [], due: { period_label: '2026-W41', is_due: true } } });
  const none = vals('Northbridge');
  assert.deepEqual([none.tlwBatchesEmpty, none.tlwCharts.length, none.tlwDue.isDue, none.tlwCanStart], [true, 0, true, false]);
});

test('until the service has said who the person is, nothing role-only is offered and the scope is not guessed', () => {
  wfresh({}, { owner: null });
  const v = vals('Northbridge');
  assert.deepEqual([v.tlwKnown, v.tlwCanStart, v.tlwCanCases, v.tlwCompany, v.tlwScope, v.tlwCasesHint], [false, false, false, false, '', '']);
  assert.equal(admin({}) || vals().tlwKnown, true);
  assert.equal(vals().tlwCompany, true, 'a company reader\'s cases and releases are read');
  assert.equal(vals('').tlwScope, 'Company: this company', 'a company with no name on record');
});

test('a failed read of who the person is is said, with the reason, until it succeeds; the page offers Refresh to try again', async () => {
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: false }]);
  wfresh({}, { owner: null });
  assert.equal(vals().tlwIdentityError, '', 'not read yet is not an error');
  wfresh({}, { owner: null, tax: { tlModesErr: 'modes are down' } });
  const v = vals();
  assert.equal(v.tlwKnown, false);
  assert.equal(v.tlwIdentityError, 'Could not tell who you are — modes are down');
  assert.equal(v.tlwScope, '', 'the scope is still not guessed');
  net.requests = [];
  v.tlwReload();
  await flush();
  assert.ok(reqs().includes('GET /modes'), 'the page\'s Refresh reads who the person is again');
  assert.equal(vals().tlwKnown, true);
  assert.equal(vals().tlwIdentityError, '');
  wfresh({}, { owner: false, tax: { tlModesErr: 'a later refresh of the list failed' } });
  assert.equal(vals().tlwIdentityError, '', 'once the person is known, a failed refresh of the list is not this page\'s problem');
});

test('Run now opens the plan with its numbers, the typed rate and cap, and a Start that is busy while it runs', () => {
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { rate: '0.5' } });
  const p = vals().tlwPlan;
  assert.deepEqual(p.data, { period: '2026-W41', nWindow: '412', nRandom: '100', nRiskOnly: '20', nToJudge: '120', cost: '$1.50',
    judges: ['wrong_period', 'invented_codes-v1'], warnings: ['Two modes have no judge yet.'] });
  assert.deepEqual([p.rate, p.cap, p.loading, p.error, p.starting], ['0.5', '', false, '', false]);
  A.setPlanDraft('cap', '2');
  p.setRate({ target: { value: '0.3' } });
  p.setCap({ target: { value: '4' } });
  assert.deepEqual(Wx().tlMonitorPlanDraft, { rate: '0.3', cap: '4' });
  platform({ tlMonitorPlanOpen: true, tlMonitorPlanLoading: true, tlMonitorPlanErr: 'plan failed', tlWatchWrites: { 'watch:start': { busy: true, error: '', conflict: false, canRetry: false } } });
  const q = vals().tlwPlan;
  assert.deepEqual([q.data, q.loading, q.error, q.starting], [null, true, 'plan failed', true]);
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: { ok: true } });
  assert.deepEqual(vals().tlwPlan.data, { period: '—', nWindow: '—', nRandom: '—', nRiskOnly: '—', nToJudge: '—', cost: '—', judges: [], warnings: [] },
    'a plan with nothing in it reads as dashes');
});

test('the plan\'s buttons act: refresh reads it again, start sends the batch, close closes it', async () => {
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { rate: '0.5' } });
  h('GET', '/monitor/plan', [200, PLAN]);
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  await vals().tlwPlan.refresh();
  assert.ok(reqs().includes('GET /monitor/plan'));
  await vals().tlwPlan.start();
  assert.ok(reqs().includes('POST /monitor/batches'));
  assert.equal(Wx().tlMonitorPlanOpen, false);
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN });
  vals().tlwPlan.close();
  assert.equal(Wx().tlMonitorPlanOpen, false);
  platform({});
  await vals().tlwRunNow();
  assert.equal(Wx().tlMonitorPlanOpen, true);
});

test('an open batch lists its failure modes with rates and intervals, its code checks and its flagged answers', () => {
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const b = vals().tlwBatch;
  assert.equal(b.period, '2026-W41');
  assert.deepEqual(b.modes[0], Object.assign({}, b.modes[0], { name: 'wrong_period', crossed: true, corrected: '17%', interval: '6% to 28%', raw: '19%',
    nSample: '100', threshold: 'threshold 0.15', warning: '', reason: '', canReview: true, reviewing: false, reviewIds: [] }));
  assert.deepEqual([b.modes[1].canReview, b.modes[1].crossed], [false, false]);
  assert.deepEqual(b.checks, [{ label: 'Gives no answer', rate: '2.2%', flagged: 9, decided: 412, tag: 'validated' }]);
  assert.deepEqual(b.flagged.map((f) => [f.turnId, f.question, f.mode, f.group, f.critique]),
    [['t1', 'How many work orders in July?', 'wrong_period', 'uniform sample', 'Used June.'], ['t2', 'List the overdue AHUs', 'wrong_period', 'risk group', '']]);
  assert.equal(b.loading, false);
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatchLoading: true });
  assert.deepEqual([vals().tlwBatch.loading, vals().tlwBatch.modes, vals().tlwBatch.flagged], [true, [], []]);
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatchErr: 'No such batch.' });
  assert.equal(vals().tlwBatch.error, 'No such batch.');
});

test('a batch mode shows the server\'s warning and reason, and missing numbers read as a dash', () => {
  const row = { mode_id: 'm9', mode_name: 'odd', crossed: false, flagged: 0, validity_warning: 'Few labelled answers.', reason: 'No judge yet.' };
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: { batch: Object.assign({}, BATCH_ROW, { results: { modes: [row] } }), flagged: [{ turn_id: 't9' }] } });
  const b = vals().tlwBatch;
  assert.deepEqual([b.modes[0].warning, b.modes[0].reason, b.modes[0].corrected, b.modes[0].interval, b.modes[0].raw, b.modes[0].nSample, b.modes[0].threshold],
    ['Few labelled answers.', 'No judge yet.', '—', '—', '—', '—', '—']);
  assert.equal(b.checks.length, 0);
  assert.equal(b.flagged[0].question, '(no question recorded)');
  assert.equal(b.flagged[0].mode, 'failure mode');
});

test('opening flagged answers as a review batch calls the route with the batch and the mode, and then shows the batches it opened', async () => {
  h('POST', '/monitor/batches/b1/review-batch', [201, { ok: true, review_batch_ids: ['rb-1', 'rb-2'] }]);
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  assert.equal(vals().tlwBatch.modes[0].canReview, true);
  await vals().tlwBatch.modes[0].review();
  assert.deepEqual(net.requests.filter((r) => r.method === 'POST').map((r) => r.body), [{ mode_id: 'm1' }]);
  assert.deepEqual(vals().tlwBatch.modes[0].reviewIds, ['rb-1', 'rb-2']);
  assert.equal(vals().tlwBatch.modes[0].canReview, true, 'the screen decides to show the opened count instead of the button');
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL, tlWatchWrites: { 'watch:review:b1:m1': { busy: true, error: '', conflict: false, canRetry: false } } });
  assert.equal(vals().tlwBatch.modes[0].reviewing, true);
  assert.equal(vals().tlwBatch.modes[1].reviewing, false);
  admin({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  assert.equal(vals().tlwBatch.modes[0].canReview, false, 'a company admin does not open review batches');
  const withIds = { batch: Object.assign({}, BATCH_ROW, { results: { modes: [{ mode_id: 'm1', mode_name: 'x', flagged: 2, review_batch_ids: ['rb-9'] }] } }), flagged: [] };
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: withIds });
  assert.deepEqual(vals().tlwBatch.modes[0].reviewIds, ['rb-9'], 'batches opened earlier come from the server');
});

test('a flagged answer opens in its conversation for a company reader; for the platform team it is plain text with the reason', async () => {
  h('GET', '/turns/t1', [200, { ok: true, trace: {}, conversation_key: 's:abc' }]);
  admin({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const f = vals().tlwBatch.flagged[0];
  assert.equal(typeof f.openAnswer, 'function');
  assert.equal(f.hint, '');
  await f.openAnswer();
  await flush();
  assert.equal(S().tab, 'conversations');
  assert.equal(S().convKey, 's:abc');
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const p = vals().tlwBatch.flagged[0];
  assert.equal(p.openAnswer, null);
  assert.match(p.hint, /View as a company/);
});

test('the batch\'s close and the page\'s reload act', async () => {
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  vals().tlwBatch.close();
  assert.equal(Wx().tlMonitorBatchId, null);
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  await vals().tlwReload();
  assert.ok(reqs().includes('GET /monitor/batches'));
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  platform(loaded);
  await vals().tlwBatches[0].open();
  assert.equal(Wx().tlMonitorBatchId, 'b1');
});

test('each case\'s buttons follow its status, and the pass rates are the server\'s', () => {
  admin({ tlCasesLoadedAt: 1, tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE, CASE_CAPABILITY, Object.assign({}, CASE_ACTIVE, { id: 'c5', status: 'retired' }),
    Object.assign({}, CASE_ACTIVE, { id: 'c6', golden_case_id: null })] });
  const [proposed, draft, active, capability, retired, noTwin] = vals().tlwCases;
  assert.deepEqual([proposed.canConfirm, proposed.canBaseline, proposed.canRepeat, proposed.canRetire], [true, false, false, true]);
  assert.deepEqual([draft.canConfirm, draft.canBaseline, draft.canRepeat, draft.canRetire], [false, true, true, true]);
  assert.deepEqual([active.canConfirm, active.canBaseline, active.canRepeat, active.canRetire], [false, false, true, true]);
  assert.deepEqual([retired.canConfirm, retired.canBaseline, retired.canRepeat, retired.canRetire], [false, false, false, false]);
  assert.equal(noTwin.canRepeat, false, 'a case with no golden twin cannot be repeated');
  assert.deepEqual([proposed.key, proposed.mode, proposed.kindText, proposed.statusText, proposed.question],
    ['e-7f3a19c2 v1', 'wrong_period', 'Not baselined', 'Proposed: confirm the failure', 'How many work orders in July?']);
  assert.equal(active.kindText, 'Regression');
  assert.equal(capability.kindText, 'Capability (baseline 40%)');
  assert.deepEqual([active.passAt1, active.passAt3, active.passAt5, active.passHat], ['100%', '100%', '90%', '60%']);
  assert.deepEqual([proposed.passAt1, proposed.passAt5, proposed.passHat], ['—', '—', '—'], 'never run: dashes');
  assert.equal(active.passHatTitle, '');
  admin({ tlCases: [Object.assign({}, CASE_ACTIVE, { latest_run: { pass_at_1: 1, pass_hat_k: 0.5, k: 10 } })] });
  assert.equal(vals().tlwCases[0].passHatTitle, 'pass^10: every one of 10 runs right');
  assert.equal(vals().tlwCases[0].passAt3, '—', 'a rate the server did not compute is a dash');
});

test('the case form shows what was typed, with defaults, and its setters keep it per case', () => {
  admin({ tlCases: [CASE_PROPOSED, CASE_ACTIVE], tlCasesDrafts: { c1: { expected_answer: 'There were 786.', evaluation: 'judgment', n: 7, reason: 'dup' } } });
  const [one, three] = vals().tlwCases;
  assert.deepEqual([one.answer, one.evaluation, one.n, one.reason], ['There were 786.', 'judgment', '7', 'dup']);
  assert.deepEqual([three.answer, three.evaluation, three.n, three.reason], ['', 'objective', '10', '']);
  three.setAnswer({ target: { value: 'ans' } });
  three.setEvaluation({ target: { value: 'judgment' } });
  three.setN({ target: { value: '4' } });
  three.setReason({ target: { value: 'why' } });
  assert.deepEqual(Wx().tlCasesDrafts.c3, { expected_answer: 'ans', evaluation: 'judgment', n: '4', reason: 'why' });
});

test('a case\'s buttons act, and are busy only while their own write is in flight', async () => {
  h('POST', '/eval-cases/c1/confirm', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [CASE_PROPOSED] }]);
  admin({ tlCases: [CASE_PROPOSED, CASE_DRAFT], tlCasesDrafts: { c1: { expected_answer: 'ans' } } });
  await vals().tlwCases[0].confirm();
  assert.ok(reqs().includes('POST /eval-cases/c1/confirm'));
  h('POST', '/eval-cases/c2/retire', [200, { ok: true }]);
  admin({ tlCases: [CASE_DRAFT], tlCasesDrafts: { c2: { reason: 'duplicate' } } });
  await vals().tlwCases[0].retire();
  assert.ok(reqs().includes('POST /eval-cases/c2/retire'));
  admin({ tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE], tlWatchWrites: { 'watch:baseline:c2': { busy: true, error: '', conflict: false, canRetry: false } } });
  assert.deepEqual(vals().tlwCases.map((k) => k.busy), [false, true, false]);
  admin({ tlCases: [CASE_ACTIVE], tlWatchWrites: { 'watch:repeat:c3': { busy: false, error: 'boom', conflict: false, canRetry: true } } });
  assert.equal(vals().tlwCases[0].busy, false, 'a failed write is not busy');
});

test('cases read as loading until the first read, empty only after one that found none, and carry the form\'s own error', () => {
  admin({ tlCasesLoading: true });
  assert.deepEqual([vals().tlwCasesLoading, vals().tlwCasesEmpty], [true, false]);
  admin({ tlCasesLoadedAt: 3, tlCases: [] });
  assert.deepEqual([vals().tlwCasesLoading, vals().tlwCasesEmpty], [false, true]);
  admin({ tlCasesLoading: true, tlCasesLoadedAt: 3, tlCases: [CASE_ACTIVE], tlCasesErr: 'Repeat an answer between 2 and 20 times.' });
  assert.deepEqual([vals().tlwCasesLoading, vals().tlwCasesEmpty, vals().tlwCasesError], [false, false, 'Repeat an answer between 2 and 20 times.']);
});

test('release checks read as a decision with its tone and reason, with the report and the run one click away', async () => {
  admin({ tlReleaseLoadedAt: 1, tlRelease: RELEASES });
  const r = vals('Northbridge Estates Ltd').tlwRelease;
  assert.deepEqual(r.list.map((x) => [x.label, x.decisionText, x.tone, x.reason, x.k]),
    [['release-1', '✓ Pass', 'ok', '', '5'], ['release-2', '✗ Blocked', 'risk', 'Two regression cases failed a run.', '5'], ['release-3', '… Incomplete', 'warn', 'Still grading.', '3']]);
  assert.equal(r.list[0].when, fmtWhen('2026-10-14T10:00:00+00:00', NOW));
  assert.equal(r.list[2].when, '', 'no start time, nothing said');
  assert.deepEqual([r.loading, r.empty, r.error, r.k, r.includeCapability, r.starting], [false, false, '', '5', true, false]);
  h('GET', '/release-checks/11111111-1111-4111-8111-111111111111', [200, '{"ok":true}']);
  const keep = globalThis.document;
  globalThis.document = { visibilityState: 'visible', body: { appendChild() {} }, createElement: () => ({ click() {}, remove() {} }) };
  try {
    await r.list[0].downloadMd();
    await r.list[0].downloadJson();
  } finally {
    globalThis.document = keep;
  }
  const asked = net.requests.filter((x) => x.url.pathname.endsWith('/11111111-1111-4111-8111-111111111111'));
  assert.deepEqual(asked.map((x) => x.url.searchParams.get('format')), ['md', 'json']);
  assert.equal(asked[0].url.searchParams.get('company_name'), 'Northbridge Estates Ltd');
  h('GET', '/golden-runs/11111111-1111-4111-8111-111111111111', [200, { ok: true, run: { id: '11111111-1111-4111-8111-111111111111', status: 'done' } }]);
  await r.list[0].openRun();
  assert.equal(S().tab, 'runs');
});

test('the release form: the count as typed, the capability switch, a busy Start, and the loading and empty states', async () => {
  admin({ tlReleaseDraft: { k: '3', include_capability: false } });
  const r = vals().tlwRelease;
  assert.deepEqual([r.k, r.includeCapability], ['3', false]);
  r.setK({ target: { value: '7' } });
  assert.equal(Wx().tlReleaseDraft.k, '7');
  vals().tlwRelease.toggleCapability();
  assert.equal(Wx().tlReleaseDraft.include_capability, true, 'switching it back on');
  vals().tlwRelease.toggleCapability();
  assert.equal(Wx().tlReleaseDraft.include_capability, false);
  admin({ tlWatchWrites: { 'watch:release': { busy: true, error: '', conflict: false, canRetry: false } } });
  assert.equal(vals().tlwRelease.starting, true);
  admin({ tlReleaseLoading: true });
  assert.deepEqual([vals().tlwRelease.loading, vals().tlwRelease.empty], [true, false]);
  admin({ tlReleaseLoadedAt: 1, tlRelease: { checks: [] }, tlReleaseErr: 'bad' });
  assert.deepEqual([vals().tlwRelease.loading, vals().tlwRelease.empty, vals().tlwRelease.error], [false, true, 'bad']);
  h('POST', '/release-checks', [202, { ok: true }]);
  admin({});
  await vals().tlwRelease.start();
  assert.ok(reqs().includes('POST /release-checks'));
});

test('the errors of the writes show on the tab, a refused rule has no Retry, and a stale base offers a reload', async () => {
  const writes = {
    'watch:start': { busy: false, error: 'Could not save — over the cost cap', conflict: false, canRetry: true },
    'watch:retire:c3': { busy: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, canRetry: false },
    'watch:resume:b0': { busy: false, error: 'That batch is not interrupted.', conflict: false, canRetry: false },
    'watch:release': { busy: true, error: '', conflict: false, canRetry: false } };
  platform({ tlWatchWrites: writes });
  const list = vals().tlwWrites;
  assert.deepEqual(list.map((w) => [w.key, w.error, typeof w.retry, typeof w.reload]),
    [['watch:start', 'Could not save — over the cost cap', 'function', 'object'],
      ['watch:retire:c3', 'Changed in another tab — reload to see the latest.', 'object', 'function'],
      ['watch:resume:b0', 'That batch is not interrupted.', 'object', 'object']]);
  assert.equal(list[0].reload, null);
  assert.equal(list[1].retry, null);
  assert.equal(vals().tlwWrites.some((w) => w.key === 'watch:release'), false, 'a write in flight has no error to show');
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  await list[1].reload();
  assert.ok(reqs().includes('GET /monitor/batches'));
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  platform({ tlMonitorPlanOpen: true });
  h('POST', '/monitor/batches', [500, { detail: { error: 'boom' } }]);
  await A.start();
  h('POST', '/monitor/batches', [202, { ok: true }]);
  const res = await vals().tlwWrites[0].retry();
  assert.equal(res.ok, true);
  assert.deepEqual(vals().tlwWrites, []);
});

test('the view model names no internal service, table or route', () => {
  platform(Object.assign({}, loaded, { tlMonitorPlan: PLAN, tlMonitorPlanOpen: true, tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL, tlRelease: RELEASES, tlCases: [CASE_PROPOSED] }));
  const text = JSON.stringify(vals('Northbridge'), (k, v) => (typeof v === 'function' ? 'fn' : v));
  for (const bad of [/\bsvc-[a-z]/i, /plenum_/, /\bUDR\b/, /deep-?agents/, /undefined/, /NaN/]) assert.doesNotMatch(text, bad);
});
