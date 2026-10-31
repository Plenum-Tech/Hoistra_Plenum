// watchActions — everything the Watch tab does: read the batches, the code checks, the automatic-vs-people trend and one
// chart per failure mode (everyone's), a company reader's eval cases and release checks, and the platform team's plan,
// start, resume and review batch (a superadmin viewing a company does them for that company); the eval-case and release-check writes, the report download, and the way a started run
// opens on Runs, claimed by this page to step. Every request is stubbed; nothing here reaches a network.
//
// Pinned: who may read and write what (nothing owner-only or company-only is asked for before the service has said who the
// person is), a reply from before a company switch or overtaken by a newer read is dropped, the writes' states
// (Could not save with Retry, Changed in another tab, the server's own sentence), Retry runs the whole press again, and
// no write is sent without what it needs written down.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

import { net, S, Wx, route, reqs, flush, wfresh, installDefaults, setActingOrg, tlStore, initialState,
  BATCHES, BATCHES_EMPTY, BATCH_DETAIL, CHART_M1, CHART_M2, CHECKS, AUTO, PLAN, CASES, CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE,
  RELEASES, RUN_ID, DUE } from './watch-helper.mjs';

const A = await import('../watchActions.js');
const G = await import('../goldenActions.js');
const C = await import('../logic/watchCommon.js');

beforeEach(installDefaults);
afterEach(() => { G.stopLoop(); });

const h = (method, path, reply) => { net.handlers[route(method, path)] = reply; };
const admin = (watch, opts) => wfresh(watch, Object.assign({ owner: false }, opts));
const platform = (watch, opts) => wfresh(watch, Object.assign({ owner: true }, opts));
const sentBody = (method, path) => { const r = net.requests.filter((x) => x.method + ' ' + x.url.pathname.slice('/backend/trace-lab/api/trace-lab'.length) === method + ' ' + path); return r.map((x) => x.body); };
const writeOf = (key) => Wx().tlWatchWrites[key];
function readsOk() {
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', (u) => [200, u.searchParams.get('mode_id') === 'm1' ? CHART_M1 : CHART_M2]);
  h('GET', '/eval-cases', [200, CASES]);
  h('GET', '/release-checks', [200, RELEASES]);
}

// ── reading ──────────────────────────────────────────────────────────────────────────────────────────────────────
test('opening the tab reads the batches, the code checks and the trend, then one chart per failure mode', async () => {
  readsOk();
  platform({});
  await A.openTab();
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /monitor/automation', 'GET /monitor/batches', 'GET /monitor/chart', 'GET /monitor/chart', 'GET /monitor/code-checks']);
  assert.deepEqual(net.requests.filter((r) => r.url.pathname.endsWith('/chart')).map((r) => r.url.searchParams.get('mode_id')).sort(), ['m1', 'm2']);
  assert.equal(net.requests.find((r) => r.url.pathname.endsWith('/monitor/batches')).url.searchParams.get('limit'), '20');
  const w = Wx();
  assert.equal(w.tlMonitorBatches.batches.length, 2);
  assert.deepEqual(w.tlMonitorBatches.due, DUE);
  assert.equal(w.tlMonitorChecks.length, 2);
  assert.equal(w.tlAuto.weeks.length, 2);
  assert.equal(w.tlMonitorCharts.m1.chart.points.length, 2);
  assert.equal(w.tlMonitorCharts.m2.chart.points.length, 1);
  assert.equal(w.tlMonitorCharts.m1.loading, false);
  assert.ok(w.tlMonitorLoadedAt > 0 && w.tlAutoLoadedAt > 0);
  assert.equal(w.tlMonitorLoading, false);
  assert.equal(w.tlMonitorErr, '');
});

test('the platform team reads no eval cases and no release checks; a company reader reads both', async () => {
  readsOk();
  platform({});
  await A.openTab();
  await A.loadCompany();
  await flush();
  assert.equal(reqs().some((r) => /eval-cases|release-checks/.test(r)), false, 'they belong to one company');
  net.requests = [];
  admin({});
  await A.loadCompany();
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /eval-cases', 'GET /release-checks']);
  assert.equal(Wx().tlCases.length, 3);
  assert.equal(Wx().tlRelease.checks.length, 3);
  assert.ok(Wx().tlCasesLoadedAt > 0 && Wx().tlReleaseLoadedAt > 0);
  net.requests = [];
  wfresh({}, { owner: true, over: {} });
  setActingOrg('org-viewed');
  await A.loadCompany();
  assert.deepEqual(reqs().sort(), ['GET /eval-cases', 'GET /release-checks'], 'a superadmin viewing a company reads that company\'s');
});

test('the cases and the release checks are not read, and no report is downloaded, by anyone but a company reader, whoever calls', async () => {
  readsOk();
  platform({});
  await A.loadCases();
  await A.loadReleases();
  assert.equal(await A.downloadRelease('r1', 'md', 'x'), false);
  wfresh({}, { owner: null });
  await A.loadCases();
  await A.loadReleases();
  assert.equal(await A.downloadRelease('r1', 'md', 'x'), false);
  assert.deepEqual(reqs(), []);
  admin({});
  assert.equal(await A.downloadRelease('', 'md', 'x'), false, 'no run named, nothing to download');
  assert.deepEqual(reqs(), []);
});

test('nothing company-only is asked for before the service has said who the person is, and the failure-mode list is read once', async () => {
  readsOk();
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: false }]);
  wfresh({}, { owner: null });
  await A.loadCompany();
  assert.deepEqual(reqs(), [], 'not known yet: no cases, no releases');
  await A.openTab();
  await flush();
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1, 'the list is read, once');
  assert.equal(S().taxonomy.tlModesMeta.isOwner, false);
  net.requests = [];
  await A.loadCompany();
  assert.deepEqual(reqs().sort(), ['GET /eval-cases', 'GET /release-checks'], 'now a company admin is known');
});

test('a read that fails says so on its own part, and the others still arrive', async () => {
  readsOk();
  h('GET', '/monitor/code-checks', [500, { detail: { error: 'checks are down' } }]);
  h('GET', '/monitor/automation', [500, { detail: { error: 'trend is down' } }]);
  platform({});
  await A.openTab();
  await flush();
  const w = Wx();
  assert.equal(w.tlMonitorBatches.batches.length, 2, 'the batches are not lost to a failed code-check read');
  assert.match(w.tlMonitorChecksErr, /checks are down/);
  assert.equal(w.tlMonitorErr, '');
  assert.match(w.tlAutoErr, /trend is down/);
  assert.equal(w.tlAutoLoading, false);
  h('GET', '/monitor/batches', [503, { detail: { error: 'monitoring is unavailable' } }]);
  platform({});
  await A.load();
  await flush();
  assert.match(Wx().tlMonitorErr, /monitoring is unavailable/);
  assert.equal(Wx().tlMonitorLoading, false);
  assert.equal(Wx().tlMonitorBatches, null);
  h('GET', '/eval-cases', [500, { detail: { error: 'cases are down' } }]);
  h('GET', '/release-checks', [500, { detail: { error: 'releases are down' } }]);
  admin({});
  await A.loadCompany();
  assert.match(Wx().tlCasesErr, /cases are down/);
  assert.match(Wx().tlReleaseErr, /releases are down/);
  assert.equal(Wx().tlCasesLoading, false);
});

test('a chart that fails says so for its mode only', async () => {
  readsOk();
  h('GET', '/monitor/chart', (u) => (u.searchParams.get('mode_id') === 'm1' ? [500, { detail: { error: 'no chart' } }] : [200, CHART_M2]));
  platform({});
  await A.openTab();
  await flush();
  assert.match(Wx().tlMonitorCharts.m1.error, /no chart/);
  assert.equal(Wx().tlMonitorCharts.m2.chart.points.length, 1);
  assert.equal(Wx().tlMonitorCharts.m2.error, '');
});

test('a reply issued before a company switch is dropped, and the new company starts from nothing', async () => {
  let release;
  h('GET', '/monitor/batches', () => new Promise((resolve) => { release = () => resolve([200, BATCHES]); }));
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  platform({});
  const pending = A.load();
  await flush();
  tlStore.set((s) => Object.assign(initialState(), { seq: s.seq + 1, tab: s.tab }));
  release();
  await pending;
  await flush();
  assert.equal(Wx().tlMonitorBatches, null, 'nothing of the last company is kept');
  assert.equal(Wx().tlMonitorLoading, false);
  assert.equal(Wx().tlAuto, null);
});

test('a read overtaken by a newer read of the same thing is dropped', async () => {
  const waits = [];
  h('GET', '/monitor/batches', () => new Promise((resolve) => { waits.push(resolve); }));
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  platform({});
  const first = A.load();
  const second = A.load();
  await flush();
  waits[1]([200, Object.assign({}, BATCHES, { batches: [BATCHES.batches[0]] })]);
  await second;
  waits[0]([200, BATCHES_EMPTY]);
  await first;
  await flush();
  assert.equal(Wx().tlMonitorBatches.batches.length, 1, 'the older reply, arriving last, does not win');
});

test('a request the app marks as from another company is dropped without an error', async () => {
  h('GET', '/monitor/batches', () => { setActingOrg('org-other'); return [200, BATCHES]; });
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  platform({});
  await A.load();
  await flush();
  assert.equal(Wx().tlMonitorErr, '');
  assert.equal(Wx().tlMonitorChecksErr, '');
});

test('Refresh reads everything the person can see again, the open batch too', async () => {
  readsOk();
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  admin({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  await A.reload();
  await flush();
  for (const r of ['GET /monitor/batches', 'GET /monitor/code-checks', 'GET /monitor/automation', 'GET /eval-cases', 'GET /release-checks', 'GET /monitor/batches/b1']) {
    assert.ok(reqs().includes(r), r);
  }
  net.requests = [];
  platform({});
  await A.reload();
  await flush();
  assert.equal(reqs().some((r) => /eval-cases|release-checks/.test(r)), false);
  assert.equal(reqs().includes('GET /modes'), false, 'who the person is, once read, is not read again by Refresh');
});

test('Refresh reads who the person is again when the first read failed, and a company reader\'s cases follow', async () => {
  readsOk();
  h('GET', '/modes', [500, { detail: { error: 'modes are down' } }]);
  wfresh({}, { owner: null });
  await A.openTab();
  await flush();
  assert.match(S().taxonomy.tlModesErr, /modes are down/, 'the failed read is kept for the page to show');
  assert.equal(C.known(S()), false);
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1);
  net.requests = [];
  await A.reload();
  await flush();
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1, 'Refresh tries the identity read again');
  assert.equal(C.known(S()), false, 'it failed again');
  assert.match(S().taxonomy.tlModesErr, /modes are down/);
  assert.equal(reqs().some((r) => /eval-cases|release-checks/.test(r)), false, 'nothing company-only is asked for while the person is not known');
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: false }]);
  net.requests = [];
  await A.reload();
  await flush();
  assert.equal(S().taxonomy.tlModesErr, '', 'the error is cleared by a good read');
  assert.equal(C.known(S()), true);
  assert.equal(C.inCompany(S()), true);
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1);
  net.requests = [];
  await A.loadCompany();
  assert.deepEqual(reqs().sort(), ['GET /eval-cases', 'GET /release-checks'], 'now a company admin is known and the cases can be read');
});

test('Refresh with a batch open still reads who the person is again', async () => {
  readsOk();
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: true }]);
  wfresh({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL }, { owner: null, tax: { tlModesErr: 'modes are down' } });
  await A.reload();
  await flush();
  assert.ok(reqs().includes('GET /modes'));
  assert.ok(reqs().includes('GET /monitor/batches/b1'));
  assert.equal(C.isPlatform(S()), true);
});

test('Refresh does not start a second identity read while one is in flight', async () => {
  readsOk();
  let release;
  h('GET', '/modes', () => new Promise((r) => { release = () => r([200, { ok: true, modes: [], is_owner: true }]); }));
  wfresh({}, { owner: null });
  A.openTab();
  await flush();
  A.reload();
  await flush();
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1, 'one read in flight is enough');
  release();
  await flush();
  assert.equal(C.known(S()), true);
  assert.equal(C.isPlatform(S()), true);
});

// ── the plan and the start (platform team only) ──────────────────────────────────────────────────────────────────
test('Run now reads the plan with the typed rate; only the platform team asks', async () => {
  h('GET', '/monitor/plan', [200, PLAN]);
  platform({ tlMonitorPlanDraft: { rate: '0.5' } });
  await A.openPlan();
  assert.equal(Wx().tlMonitorPlanOpen, true);
  assert.equal(net.requests[0].url.searchParams.get('rate'), '0.5');
  assert.equal(Wx().tlMonitorPlan.n_window, 412);
  assert.equal(Wx().tlMonitorPlanLoading, false);
  net.requests = [];
  platform({});
  await A.openPlan();
  assert.equal(net.requests[0].url.searchParams.has('rate'), false, 'no rate typed, none sent');
  net.requests = [];
  admin({});
  await A.openPlan();
  assert.deepEqual(reqs(), [], 'a company admin does not run monitoring');
  assert.equal(Wx().tlMonitorPlanOpen, false);
  wfresh({}, { owner: null });
  await A.openPlan();
  assert.deepEqual(reqs(), []);
});

test('a plan that cannot be read says so and can be read again', async () => {
  h('GET', '/monitor/plan', [500, { detail: { error: 'plan failed' } }]);
  platform({});
  await A.openPlan();
  assert.match(Wx().tlMonitorPlanErr, /plan failed/);
  assert.equal(Wx().tlMonitorPlanLoading, false);
  h('GET', '/monitor/plan', [200, PLAN]);
  await A.openPlan();
  assert.equal(Wx().tlMonitorPlanErr, '');
  assert.equal(Wx().tlMonitorPlan.period_label, '2026-W41');
});

test('a sample rate or cost cap that is not a number in range is refused here, and nothing is sent', async () => {
  h('GET', '/monitor/plan', [200, PLAN]);
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  for (const bad of ['abc', '0', '-0.2', '1.5']) {
    platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { rate: bad } });
    net.requests = [];
    await A.start();
    await A.openPlan();
    assert.deepEqual(reqs(), [], 'rate ' + bad);
    assert.match(Wx().tlMonitorPlanErr, /Sample rate/, bad);
    assert.equal(Wx().tlMonitorPlanOpen, true);
  }
  for (const bad of ['abc', '0', '-1', '101']) {
    platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { cap: bad } });
    net.requests = [];
    await A.start();
    assert.deepEqual(reqs(), [], 'cap ' + bad);
    assert.match(Wx().tlMonitorPlanErr, /Cost cap/, bad);
  }
});

test('a refused start leaves the plan open with the error and Retry; a start that succeeds closes it and reads the batches again', async () => {
  readsOk();
  platform({ tlMonitorPlanOpen: true, tlMonitorPlan: PLAN, tlMonitorPlanDraft: { rate: '0.5', cap: '3' } });
  h('POST', '/monitor/batches', [500, { detail: { error: 'over the cost cap' } }]);
  const res = await A.start();
  assert.equal(res.ok, false);
  assert.equal(Wx().tlMonitorPlanOpen, true, 'a failed start must not read as a success');
  assert.match(writeOf('watch:start').error, /^Could not save — over the cost cap/);
  assert.equal(writeOf('watch:start').canRetry, true);
  assert.equal(writeOf('watch:start').busy, false);
  assert.equal(reqs().includes('GET /monitor/batches'), false, 'no reload after a failure');
  assert.deepEqual(sentBody('POST', '/monitor/batches'), [{ sample_rate: 0.5, usd_cap: 3 }]);
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  const retried = await A.retry('watch:start');
  assert.equal(retried.ok, true, 'Retry runs the press again');
  assert.equal(Wx().tlMonitorPlanOpen, false);
  assert.equal(writeOf('watch:start'), undefined, 'the error is gone with the success');
  await flush();
  assert.ok(reqs().includes('GET /monitor/batches'));
});

test('a start with nothing typed sends an empty body, and only the platform team can start', async () => {
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  platform({ tlMonitorPlanOpen: true });
  await A.start();
  assert.deepEqual(sentBody('POST', '/monitor/batches'), [{}]);
  net.requests = [];
  admin({ tlMonitorPlanOpen: true });
  const res = await A.start();
  assert.equal(res.ok, false);
  assert.deepEqual(reqs(), []);
});

test('the plan form keeps what is typed, and closing the plan keeps the draft', () => {
  platform({});
  A.setPlanDraft('rate', '0.25');
  A.setPlanDraft('cap', '4');
  assert.deepEqual(Wx().tlMonitorPlanDraft, { rate: '0.25', cap: '4' });
  platform({ tlMonitorPlanOpen: true, tlMonitorPlanDraft: { rate: '0.3' } });
  A.closePlan();
  assert.equal(Wx().tlMonitorPlanOpen, false);
  assert.deepEqual(Wx().tlMonitorPlanDraft, { rate: '0.3' });
});

test('resuming an interrupted batch is the platform team\'s, names its batch in its key, and reads the batches again', async () => {
  readsOk();
  h('POST', '/monitor/batches/b0/resume', [202, { ok: true }]);
  platform({});
  const res = await A.resume('b0');
  assert.equal(res.ok, true);
  assert.deepEqual(sentBody('POST', '/monitor/batches/b0/resume'), [{}]);
  await flush();
  assert.ok(reqs().includes('GET /monitor/batches'));
  net.requests = [];
  admin({});
  assert.equal((await A.resume('b0')).ok, false);
  assert.deepEqual(reqs(), []);
  h('POST', '/monitor/batches/b0/resume', [409, { detail: { reason: 'not_interrupted', error: 'That batch is not interrupted.' } }]);
  platform({});
  await A.resume('b0');
  assert.equal(writeOf('watch:resume:b0').error, 'That batch is not interrupted.', 'the server\'s own sentence for a rule it refused');
  assert.equal(writeOf('watch:resume:b0').canRetry, false);
});

// ── monitoring for the company a superadmin views (the platform team's, Phase 8) ─────────────────────────────────────
const org = (method, path) => net.requests.filter((x) => x.method === method && x.url.pathname.endsWith(path)).map((x) => x.url.searchParams.get('organization_id'));

test('a superadmin viewing a company previews, starts and resumes monitoring and opens review batches for that company', async () => {
  readsOk();
  h('GET', '/monitor/plan', [200, PLAN]);
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  h('POST', '/monitor/batches/b0/resume', [202, { ok: true }]);
  h('POST', '/monitor/batches/b1/review-batch', [201, { ok: true, review_batch_ids: ['rb-1'] }]);
  setActingOrg('org-a');
  admin({ tlMonitorPlanOpen: true });                 // the service answers is_owner false for a company it was told to act for
  await A.openPlan();
  assert.equal(Wx().tlMonitorPlan.n_window, 412);
  assert.equal(Wx().tlMonitorPlanErr, '');
  assert.deepEqual(org('GET', '/monitor/plan'), ['org-a']);
  const started = await A.start();
  assert.equal(started.ok, true);
  assert.deepEqual(org('POST', '/monitor/batches'), ['org-a']);
  assert.equal(Wx().tlMonitorPlanOpen, false, 'the plan closes once the batch is started');
  assert.equal((await A.resume('b0')).ok, true);
  assert.deepEqual(org('POST', '/monitor/batches/b0/resume'), ['org-a']);
  assert.equal((await A.openReviewBatch('b1', 'm1')).ok, true);
  assert.deepEqual(org('POST', '/monitor/batches/b1/review-batch'), ['org-a']);
  assert.deepEqual(Wx().tlMonitorReviewIds['b1:m1'], ['rb-1']);
  await flush();
  assert.ok(org('GET', '/monitor/batches').every((o) => o === 'org-a'), 'and the batches read afterwards are that company\'s');
});

test('viewing no company names none: the platform team\'s start is platform-wide', async () => {
  readsOk();
  h('POST', '/monitor/batches', [202, { ok: true, batch: { id: 'b9' } }]);
  platform({ tlMonitorPlanOpen: true });
  assert.equal((await A.start()).ok, true);
  assert.deepEqual(org('POST', '/monitor/batches'), [null]);
});

test('a company admin cannot preview, start, resume or open a review batch, and nobody can before the service has said who they are', async () => {
  for (const [who, make] of [['company admin', () => admin({ tlMonitorPlanOpen: true })], ['unknown', () => wfresh({ tlMonitorPlanOpen: true }, { owner: null })]]) {
    make();
    net.requests = [];
    await A.openPlan();
    assert.equal((await A.start()).ok, false, who);
    assert.equal((await A.resume('b0')).ok, false, who);
    assert.equal((await A.openReviewBatch('b1', 'm1')).ok, false, who);
    assert.deepEqual(reqs(), [], who + ': nothing is sent');
  }
  setActingOrg('org-a');
  wfresh({ tlMonitorPlanOpen: true }, { owner: null });
  net.requests = [];
  assert.equal((await A.start()).ok, false, 'a View as before the service has said who the person is');
  assert.deepEqual(reqs(), []);
});

// ── one batch ────────────────────────────────────────────────────────────────────────────────────────────────────
test('opening a batch reads it; closing it empties it; a reply for a batch the reader has left is dropped', async () => {
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  admin({});
  await A.openBatch('b1');
  assert.equal(Wx().tlMonitorBatchId, 'b1');
  assert.equal(Wx().tlMonitorBatch.flagged.length, 2);
  assert.equal(Wx().tlMonitorBatchLoading, false);
  A.closeBatch();
  assert.equal(Wx().tlMonitorBatchId, null);
  assert.equal(Wx().tlMonitorBatch, null);
  let release;
  h('GET', '/monitor/batches/b2', () => new Promise((resolve) => { release = () => resolve([200, BATCH_DETAIL]); }));
  const pending = A.openBatch('b2');
  await flush();
  A.closeBatch();
  release();
  await pending;
  assert.equal(Wx().tlMonitorBatch, null, 'the reader had left it');
  assert.equal(Wx().tlMonitorBatchLoading, false);
  h('GET', '/monitor/batches/b3', [404, { detail: { error: 'No such batch.' } }]);
  await A.openBatch('b3');
  assert.match(Wx().tlMonitorBatchErr, /No such batch/);
  assert.equal(Wx().tlMonitorBatchLoading, false);
});

test('opening the same batch again keeps what is on screen while it reads', async () => {
  let release;
  h('GET', '/monitor/batches/b1', () => new Promise((resolve) => { release = () => resolve([200, BATCH_DETAIL]); }));
  admin({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const pending = A.openBatch('b1');
  await flush();
  assert.equal(Wx().tlMonitorBatch.flagged.length, 2, 'not blanked');
  release();
  await pending;
  assert.equal(Wx().tlMonitorBatch.flagged.length, 2);
});

test('opening flagged answers as a review batch is the platform team\'s, and remembers the batches it opened', async () => {
  h('POST', '/monitor/batches/b1/review-batch', [201, { ok: true, review_batch_ids: ['rb-1', 'rb-2'] }]);
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  const res = await A.openReviewBatch('b1', 'm1');
  assert.equal(res.ok, true);
  assert.deepEqual(sentBody('POST', '/monitor/batches/b1/review-batch'), [{ mode_id: 'm1' }]);
  assert.deepEqual(Wx().tlMonitorReviewIds['b1:m1'], ['rb-1', 'rb-2']);
  net.requests = [];
  admin({});
  assert.equal((await A.openReviewBatch('b1', 'm1')).ok, false);
  assert.deepEqual(reqs(), []);
  h('POST', '/monitor/batches/b1/review-batch', [500, { detail: { error: 'nope' } }]);
  platform({});
  await A.openReviewBatch('b1', 'm2');
  assert.equal(Wx().tlMonitorReviewIds['b1:m2'], undefined, 'a refused one is not remembered as opened');
  assert.match(writeOf('watch:review:b1:m2').error, /nope/);
});

test('a flagged answer opens in its conversation for a company reader; the platform team, who names no company, cannot', async () => {
  h('GET', '/turns/t1', [200, { ok: true, trace: {}, conversation_key: 's:abc' }]);
  admin({});
  assert.equal(await A.openAnswer('t1'), true);
  await flush();
  assert.equal(S().tab, 'conversations');
  assert.equal(S().convKey, 's:abc');
  assert.ok(reqs().includes('GET /conversations/s%3Aabc'));
  net.requests = [];
  platform({});
  assert.equal(await A.openAnswer('t1'), false);
  assert.deepEqual(reqs(), []);
  assert.equal(S().tab, 'watch');
});

// ── eval cases ───────────────────────────────────────────────────────────────────────────────────────────────────
test('a proposed case is never confirmed without a written expected answer', async () => {
  h('POST', '/eval-cases/c1/confirm', [200, { ok: true, case: CASE_PROPOSED }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [] }]);
  admin({ tlCases: [CASE_PROPOSED] });
  await A.confirmCase('c1');
  assert.deepEqual(sentBody('POST', '/eval-cases/c1/confirm'), []);
  assert.match(Wx().tlCasesErr, /right answer/);
  A.setCaseDraft('c1', 'expected_answer', '   ');
  await A.confirmCase('c1');
  assert.deepEqual(sentBody('POST', '/eval-cases/c1/confirm'), [], 'blanks are not an answer');
  A.setCaseDraft('c1', 'expected_answer', 'There were 786.');
  await A.confirmCase('c1');
  assert.deepEqual(sentBody('POST', '/eval-cases/c1/confirm'), [{ base: 2, expected_answer: 'There were 786.', evaluation: 'objective' }]);
  assert.equal(Wx().tlCasesErr, '', 'the message goes when the press goes through');
  await flush();
  assert.ok(reqs().includes('GET /eval-cases'), 'the cases are read again');
});

test('a case that already has a golden twin is confirmed without a new answer, and the way it is checked is sent as chosen', async () => {
  h('POST', '/eval-cases/c9/confirm', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, CASES]);
  admin({ tlCases: [Object.assign({}, CASE_PROPOSED, { id: 'c9', golden_case_id: 'g-9', rev: 4 })] });
  A.setCaseDraft('c9', 'evaluation', 'judgment');
  await A.confirmCase('c9');
  assert.deepEqual(sentBody('POST', '/eval-cases/c9/confirm'), [{ base: 4, expected_answer: null, evaluation: 'judgment' }]);
});

test('confirming, baselining, repeating and retiring are a company reader\'s: the platform team and an unknown reader send nothing', async () => {
  for (const setup of [() => platform({ tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE] }), () => wfresh({ tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE] }, { owner: null })]) {
    setup();
    net.requests = [];
    A.setCaseDraft('c1', 'expected_answer', 'x');
    A.setCaseDraft('c3', 'reason', 'duplicate');
    for (const r of [await A.confirmCase('c1'), await A.baseline('c2'), await A.repeat('c3'), await A.retire('c3'), await A.startRelease()]) assert.equal(r.ok, false);
    assert.deepEqual(reqs(), []);
  }
});

test('a baseline starts a run that opens on Runs and is this page\'s to step', async () => {
  h('POST', '/eval-cases/c2/baseline', [202, { ok: true, golden_run: { id: RUN_ID } }]);
  h('GET', '/golden-runs/' + RUN_ID, [200, { ok: true, run: { id: RUN_ID, status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  admin({ tlCases: [CASE_DRAFT] });
  const res = await A.baseline('c2');
  await flush();
  assert.equal(res.ok, true);
  assert.deepEqual(sentBody('POST', '/eval-cases/c2/baseline'), [{}]);
  assert.equal(S().tab, 'runs');
  assert.equal(S().golden.detail.runId, RUN_ID);
  assert.equal(S().golden.own[RUN_ID], true);
});

test('a baseline that is refused stays on the tab with its error under the case\'s own key', async () => {
  h('POST', '/eval-cases/c2/baseline', [409, { detail: { reason: 'over_cap', error: 'This month\'s evaluation spend is at its cap.' } }]);
  admin({ tlCases: [CASE_DRAFT] });
  await A.baseline('c2');
  assert.equal(S().tab, 'watch');
  assert.equal(writeOf('watch:baseline:c2').error, 'This month\'s evaluation spend is at its cap.');
  assert.equal(writeOf('watch:baseline:c2').canRetry, false);
});

test('a repeat runs 2 to 20 times, ten unless the reader typed another, and opens its run', async () => {
  h('POST', '/eval-cases/c3/repeat', [202, { ok: true, golden_run: { id: RUN_ID } }]);
  h('GET', '/golden-runs/' + RUN_ID, [200, { ok: true, run: { id: RUN_ID, status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  admin({ tlCases: [CASE_ACTIVE] });
  await A.repeat('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/repeat'), [{ n: 10 }]);
  assert.equal(S().golden.detail.runId, RUN_ID);
  for (const bad of ['1', '21', '', 'abc', '0']) {
    admin({ tlCases: [CASE_ACTIVE] });
    net.requests = [];
    A.setCaseDraft('c3', 'n', bad);
    await A.repeat('c3');
    assert.deepEqual(sentBody('POST', '/eval-cases/c3/repeat'), [], 'n = ' + JSON.stringify(bad));
    assert.match(Wx().tlCasesErr, /between 2 and 20/, bad);
  }
  admin({ tlCases: [CASE_ACTIVE] });
  net.requests = [];
  A.setCaseDraft('c3', 'n', '3.6');
  await A.repeat('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/repeat'), [{ n: 4 }]);
  net.requests = [];
  A.setCaseDraft('c3', 'n', '2');
  await A.repeat('c3');
  A.setCaseDraft('c3', 'n', '20');
  await A.repeat('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/repeat'), [{ n: 2 }, { n: 20 }]);
});

test('retiring a case needs a reason of at least three characters and sends the case\'s revision', async () => {
  h('POST', '/eval-cases/c3/retire', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [CASE_PROPOSED] }]);
  admin({ tlCases: [CASE_ACTIVE] });
  await A.retire('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/retire'), []);
  assert.match(Wx().tlCasesErr, /why the case is retired/);
  A.setCaseDraft('c3', 'reason', ' x ');
  await A.retire('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/retire'), []);
  A.setCaseDraft('c3', 'reason', '  duplicate of c1  ');
  await A.retire('c3');
  assert.deepEqual(sentBody('POST', '/eval-cases/c3/retire'), [{ base: 5, reason: 'duplicate of c1' }]);
  await flush();
  assert.equal(Wx().tlCases.length, 1, 'the cases are read again');
  assert.equal(Wx().tlCasesErr, '');
});

test('a case that is not on screen is not acted on', async () => {
  admin({ tlCases: [] });
  for (const r of [await A.confirmCase('nope'), await A.retire('nope')]) assert.equal(r.ok, false);
  assert.deepEqual(reqs(), []);
});

test('a stale base on a case says another tab changed it and offers a reload, not a retry', async () => {
  h('POST', '/eval-cases/c3/retire', [409, { detail: { reason: 'stale_base', error: 'stale' } }]);
  admin({ tlCases: [CASE_ACTIVE], tlCasesDrafts: { c3: { reason: 'duplicate' } } });
  const res = await A.retire('c3');
  assert.equal(res.conflict, true);
  assert.deepEqual(writeOf('watch:retire:c3'), { busy: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, canRetry: false });
});

test('the case form keeps what is typed per case', () => {
  admin({});
  A.setCaseDraft('c1', 'expected_answer', 'a');
  A.setCaseDraft('c1', 'evaluation', 'judgment');
  A.setCaseDraft('c2', 'n', '5');
  assert.deepEqual(Wx().tlCasesDrafts, { c1: { expected_answer: 'a', evaluation: 'judgment' }, c2: { n: '5' } });
});

// ── release checks ───────────────────────────────────────────────────────────────────────────────────────────────
test('a release check runs each case 1 to 10 times, with capability cases unless switched off, and opens its run', async () => {
  h('POST', '/release-checks', [202, { ok: true, golden_run: { id: RUN_ID } }]);
  h('GET', '/golden-runs/' + RUN_ID, [200, { ok: true, run: { id: RUN_ID, status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  admin({});
  await A.startRelease();
  assert.deepEqual(sentBody('POST', '/release-checks'), [{ k: 5, include_capability: true }]);
  assert.equal(S().tab, 'runs');
  assert.equal(S().golden.own[RUN_ID], true);
  net.requests = [];
  admin({});
  A.setReleaseDraft('k', '3');
  A.setReleaseDraft('include_capability', false);
  await A.startRelease();
  assert.deepEqual(sentBody('POST', '/release-checks'), [{ k: 3, include_capability: false }]);
  for (const bad of ['0', '11', '', 'abc']) {
    admin({});
    net.requests = [];
    A.setReleaseDraft('k', bad);
    await A.startRelease();
    assert.deepEqual(sentBody('POST', '/release-checks'), [], 'k = ' + JSON.stringify(bad));
    assert.match(Wx().tlReleaseErr, /1 to 10 times/, bad);
  }
});

test('a refused release check stays on the tab with its error', async () => {
  h('POST', '/release-checks', [500, { detail: { error: 'the chat is starting' } }]);
  admin({});
  await A.startRelease();
  assert.equal(S().tab, 'watch');
  assert.match(writeOf('watch:release').error, /the chat is starting/);
  assert.equal(writeOf('watch:release').canRetry, true);
});

test('opening a release check\'s run is plain navigation: Runs, that run, not claimed unless this page started it', async () => {
  h('GET', '/golden-runs/' + RUN_ID, [200, { ok: true, run: { id: RUN_ID, status: 'done' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  admin({});
  await A.openRun(RUN_ID);
  assert.equal(S().tab, 'runs');
  assert.equal(S().golden.detail.runId, RUN_ID);
  assert.equal(S().golden.own[RUN_ID], undefined);
  net.requests = [];
  tlStore.set({ tab: 'watch' });
  assert.equal(await A.openRun('not-a-run'), null, 'a malformed id opens nothing');
  assert.equal(S().tab, 'watch');
});

test('the release report downloads as a file under the company\'s name, and a failed download says so', async () => {
  h('GET', '/release-checks/r1', [200, '# Release check\n', { 'content-disposition': 'attachment; filename="release-check-1.md"', 'content-type': 'text/markdown' }]);
  const anchors = [];
  const keep = globalThis.document;
  globalThis.document = { visibilityState: 'visible', body: { appendChild() {} },
    createElement: () => { const a = { href: '', download: '', click() { anchors.push(this); }, remove() {} }; return a; } };
  try {
    admin({});
    await A.downloadRelease('r1', 'md', 'Northbridge Estates Ltd');
    assert.equal(net.requests[0].url.searchParams.get('format'), 'md');
    assert.equal(net.requests[0].url.searchParams.get('company_name'), 'Northbridge Estates Ltd');
    assert.equal(anchors.length, 1);
    assert.equal(anchors[0].download, 'release-check-1.md');
    h('GET', '/release-checks/r2', [404, { detail: { error: 'No such release check.' } }]);
    await A.downloadRelease('r2', 'json', 'Northbridge Estates Ltd');
    assert.match(Wx().tlReleaseErr, /No such release check/);
    assert.equal(anchors.length, 1, 'nothing saved for a failed download');
  } finally {
    globalThis.document = keep;
  }
});

// ── the writes ───────────────────────────────────────────────────────────────────────────────────────────────────
test('every write names its own key, and the keys are the ones the state module defines', () => {
  const src = readFileSync(new URL('../watchActions.js', import.meta.url), 'utf8');
  const writes = src.match(/(?<!function )\bact\(\s*watchWrite\.\w+/g) || [];
  const all = src.match(/(?<!function )\bact\(/g) || [];
  assert.ok(all.length >= 8, 'a write per button: start, resume, review, confirm, baseline, repeat, retire, release');
  assert.equal(writes.length, all.length, 'every write names a watchWrite key; a write without one would share a key');
  assert.deepEqual([...new Set(writes.map((w) => w.replace(/.*watchWrite\./, '')))].sort(), ['baseline', 'confirm', 'release', 'repeat', 'resume', 'retire', 'review', 'start']);
  for (const k of ['start', 'release', 'resume', 'review', 'confirm', 'baseline', 'repeat', 'retire']) assert.ok(k in C.watchWrite, k);
});

test('a network failure keeps the work and offers Retry, and Retry does everything the press did', async () => {
  h('POST', '/eval-cases/c3/retire', [500, { detail: { error: 'boom' } }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [] }]);
  admin({ tlCases: [CASE_ACTIVE], tlCasesDrafts: { c3: { reason: 'duplicate' } } });
  await A.retire('c3');
  assert.deepEqual(writeOf('watch:retire:c3'), { busy: false, error: 'Could not save — boom', conflict: false, canRetry: true });
  assert.equal(Wx().tlCasesDrafts.c3.reason, 'duplicate', 'what was typed is kept');
  h('POST', '/eval-cases/c3/retire', [200, { ok: true }]);
  const r = await A.retry('watch:retire:c3');
  assert.equal(r.ok, true);
  assert.equal(writeOf('watch:retire:c3'), undefined);
  await flush();
  assert.equal(Wx().tlCases.length, 0, 'what follows a success happened on the retry too');
  assert.deepEqual(await A.retry('watch:never-made'), { ok: false });
});

test('a write is busy while it is in flight, and a write that fails after a company switch shows nothing', async () => {
  let release;
  h('POST', '/eval-cases/c3/retire', () => new Promise((resolve) => { release = () => resolve([500, { detail: { error: 'late failure' } }]); }));
  admin({ tlCases: [CASE_ACTIVE], tlCasesDrafts: { c3: { reason: 'duplicate' } } });
  const pending = A.retire('c3');
  await flush();
  assert.equal(writeOf('watch:retire:c3').busy, true);
  tlStore.set((s) => Object.assign(initialState(), { seq: s.seq + 1, tab: s.tab }));
  release();
  const res = await pending;
  assert.deepEqual(res, { ok: false, stale: true });
  assert.deepEqual(Wx().tlWatchWrites, {}, 'the new company never sees the old one\'s error');
  h('POST', '/eval-cases/c3/retire', [500, { detail: { error: 'must not be asked again' } }]);
  net.requests = [];
  assert.deepEqual(await A.retry('watch:retire:c3'), { ok: false }, 'a press of the company that has gone is not offered again');
  assert.deepEqual(reqs(), []);
});

test('the server\'s own sentence is shown for a rule it refused, with no Retry', async () => {
  h('POST', '/eval-cases/c2/baseline', [409, { detail: { reason: 'baseline_running', error: 'A baseline for that case is already running.' } }]);
  admin({ tlCases: [CASE_DRAFT] });
  const res = await A.baseline('c2');
  assert.equal(res.conflict, false);
  assert.deepEqual(writeOf('watch:baseline:c2'), { busy: false, error: 'A baseline for that case is already running.', conflict: false, canRetry: false });
});

test('a button pressed again while its write is in flight sends nothing more', async () => {
  let release;
  h('POST', '/release-checks', () => new Promise((resolve) => { release = () => resolve([202, { ok: true }]); }));
  admin({});
  const first = A.startRelease();
  await flush();
  const second = await A.startRelease();
  assert.equal(second.ok, false);
  assert.equal(sentBody('POST', '/release-checks').length, 1);
  release();
  await first;
});

test('a failed write\'s error goes when the reader presses the button again and it goes through', async () => {
  h('POST', '/monitor/batches/b0/resume', [500, { detail: { error: 'boom' } }]);
  h('GET', '/monitor/batches', [200, BATCHES]);
  h('GET', '/monitor/code-checks', [200, CHECKS]);
  h('GET', '/monitor/automation', [200, AUTO]);
  h('GET', '/monitor/chart', [200, CHART_M1]);
  platform({});
  await A.resume('b0');
  assert.ok(writeOf('watch:resume:b0').error);
  h('POST', '/monitor/batches/b0/resume', [202, { ok: true }]);
  await A.resume('b0');
  assert.equal(writeOf('watch:resume:b0'), undefined);
});

test('an action the case\'s status does not allow sends nothing: only a proposed case is confirmed, only a draft baselined', async () => {
  admin({ tlCases: [CASE_PROPOSED, CASE_DRAFT, CASE_ACTIVE, Object.assign({}, CASE_ACTIVE, { id: 'c5', status: 'retired' }),
    Object.assign({}, CASE_DRAFT, { id: 'c6', golden_case_id: null })] });
  A.setCaseDraft('c3', 'expected_answer', 'x');
  A.setCaseDraft('c5', 'reason', 'duplicate');
  for (const r of [await A.confirmCase('c3'), await A.confirmCase('c2'), await A.baseline('c1'), await A.baseline('c3'), await A.repeat('c5'),
    await A.repeat('c6'), await A.retire('c5')]) assert.equal(r.ok, false);
  assert.deepEqual(reqs(), []);
});

test('a reason longer than the service takes is cut to its limit', async () => {
  h('POST', '/eval-cases/c3/retire', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [] }]);
  admin({ tlCases: [CASE_ACTIVE] });
  A.setCaseDraft('c3', 'reason', 'r'.repeat(600));
  await A.retire('c3');
  assert.equal(sentBody('POST', '/eval-cases/c3/retire')[0].reason.length, 500);
});

test('opening the tab again reads a batch that was left open', async () => {
  readsOk();
  h('GET', '/monitor/batches/b1', [200, BATCH_DETAIL]);
  platform({ tlMonitorBatchId: 'b1', tlMonitorBatch: BATCH_DETAIL });
  await A.openTab();
  await flush();
  assert.ok(reqs().includes('GET /monitor/batches/b1'));
  assert.equal(Wx().tlMonitorBatch.flagged.length, 2);
});

test('the typed answer, reason and counts are cut to what the service takes', () => {
  admin({});
  A.setCaseDraft('c1', 'expected_answer', 'a'.repeat(7000));
  assert.equal(Wx().tlCasesDrafts.c1.expected_answer.length, 6000);
  A.setReleaseDraft('k', '123456');
  assert.equal(Wx().tlReleaseDraft.k, '1234');
  A.setPlanDraft('rate', 0.25);
  assert.equal(Wx().tlMonitorPlanDraft.rate, '0.25', 'a number typed in is kept as text');
  A.setPlanDraft('cap', '1234567890123456');
  assert.equal(Wx().tlMonitorPlanDraft.cap.length, 12);
  A.setReleaseDraft('include_capability', false);
  assert.equal(Wx().tlReleaseDraft.include_capability, false);
  assert.equal(Wx().tlReleaseDraft.k, '1234', 'a switch does not lose the count');
});

test('a 409 that names no reason is a stale base: another tab changed it, reload, no Retry', async () => {
  h('POST', '/eval-cases/c3/retire', [409, { detail: { error: 'conflict' } }]);
  admin({ tlCases: [CASE_ACTIVE], tlCasesDrafts: { c3: { reason: 'duplicate' } } });
  const res = await A.retire('c3');
  assert.deepEqual([res.conflict, writeOf('watch:retire:c3').canRetry], [true, false]);
  assert.equal(writeOf('watch:retire:c3').error, 'Changed in another tab — reload to see the latest.');
});

test('once a write has gone through there is nothing left to Retry', async () => {
  h('POST', '/eval-cases/c3/retire', [200, { ok: true }]);
  h('GET', '/eval-cases', [200, { ok: true, cases: [] }]);
  admin({ tlCases: [CASE_ACTIVE], tlCasesDrafts: { c3: { reason: 'duplicate' } } });
  assert.equal((await A.retire('c3')).ok, true);
  net.requests = [];
  assert.deepEqual(await A.retry('watch:retire:c3'), { ok: false });
  assert.deepEqual(reqs(), []);
});

test('a download that fails shows its error, and the next one that goes through clears it', async () => {
  h('GET', '/release-checks/r2', [404, { detail: { error: 'No such release check.' } }]);
  h('GET', '/release-checks/r1', [200, '{"ok":true}', { 'content-disposition': 'attachment; filename="r1.json"' }]);
  const keep = globalThis.document;
  globalThis.document = { visibilityState: 'visible', body: { appendChild() {} }, createElement: () => ({ click() {}, remove() {} }) };
  try {
    admin({});
    await A.downloadRelease('r2', 'json', 'x');
    assert.match(Wx().tlReleaseErr, /No such release check/);
    assert.equal(await A.downloadRelease('r1', 'json', 'x'), true);
    assert.equal(Wx().tlReleaseErr, '');
  } finally {
    globalThis.document = keep;
  }
});

test('a chart reply overtaken by a newer read of the same mode is dropped', async () => {
  const waits = [];
  h('GET', '/monitor/chart', () => new Promise((resolve) => { waits.push(resolve); }));
  platform({});
  const first = A.loadChart('m1');
  const second = A.loadChart('m1');
  await flush();
  waits[1]([200, CHART_M2]);
  await second;
  waits[0]([200, CHART_M1]);
  await first;
  assert.equal(Wx().tlMonitorCharts.m1.chart.points.length, 1, 'the older reply, arriving last, does not win');
  const other = A.loadChart('m2');
  await flush();
  waits[2]([200, CHART_M1]);
  await other;
  assert.equal(Wx().tlMonitorCharts.m2.chart.points.length, 2, 'another mode\'s read is its own');
});

test('a write that goes through after a company switch changes nothing for the new company: its follow-up never runs and no run opens', async () => {
  let release;
  h('POST', '/release-checks', () => new Promise((resolve) => { release = () => resolve([202, { ok: true, golden_run: { id: RUN_ID } }]); }));
  h('GET', '/golden-runs/' + RUN_ID, [200, { ok: true, run: { id: RUN_ID, status: 'queued' } }]);
  h('GET', '/runs', [200, { ok: true, runs: [] }]);
  h('GET', '/golden-runs', [200, { ok: true, runs: [] }]);
  admin({});
  const pending = A.startRelease();
  await flush();
  tlStore.set((s) => Object.assign(initialState(), { seq: s.seq + 1, tab: s.tab }));
  net.requests = [];
  release();
  const res = await pending;
  await flush();
  assert.deepEqual(res, { ok: false, stale: true });
  assert.equal(S().tab, 'watch', 'the old company\'s run is not opened in the new company\'s page');
  assert.equal(S().golden.own[RUN_ID], undefined);
  assert.deepEqual(reqs(), []);
  assert.deepEqual(await A.retry('watch:release'), { ok: false }, 'nothing waits to be retried for a company that has gone');
});
