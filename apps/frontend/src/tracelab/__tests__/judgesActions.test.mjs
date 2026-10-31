// judgesActions — what the Judges tab does (judgesActions.js): the loaders (the table and the code checks for everyone;
// everything else for the platform team only), the run watch, the company-switch guard, and the writes (split, new
// version, preview, runs, triage, freeze, decision, threshold, the failure report) with their inline errors, the
// server's own words for a refused rule, and a reload offer for a stale base. No backend: fetch is stubbed per route.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import { net, C, S, Jx, P, route, reqs, flush, jfresh, installDefaults, setActingOrg, tlStore,
  SUMMARY_BRIEF, SUMMARY_FULL, DRAFT, RUN_DEV, RUN_ROW_DEV, RUN_TEST, DETAIL, DETAIL_TRIAGED, FROZEN, DETAIL_FROZEN, DETAIL_TESTED, SPLIT, READY, READY_THIN,
  CHECKS, REPORT } from './judges-helper.mjs';
const A = await import('../judgesActions.js');
const T = await import('../logic/judgesText.js');
const V = await import('../logic/judges.js');
const { resetForCompany } = await import('../store.js');

beforeEach(installDefaults);
afterEach(() => A.stopWatch());

const h = (method, path, reply) => { net.handlers[route(method, path)] = reply; };
const down = [503, { detail: { ok: false, available: false, error: 'Trace Lab is not available right now. The chat is not affected.' } }];

// ── Task 15: loaders, the run watch and the company-switch guard ─────────────────────────────────────────────────

test('a company admin opening the tab reads the judges and the code checks, and nothing only the platform team may read', async () => {
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_BRIEF], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeId: 'j-2', tlJudgeModeId: 'm-1' }, { owner: false });
  A.openTab();
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /checks', 'GET /judges']);
  assert.equal(Jx().tlJudgesRows.length, 1);
  assert.equal(Jx().tlJudgesChecks.length, 2);
  assert.ok(Jx().tlJudgesLoadedAt);
  await A.openJudge('j-2');
  await A.openMode('m-1');
  await A.loadSplit('m-1');
  await A.openRun('r-dev');
  await A.loadReport();
  A.reopen();
  await flush();
  assert.equal(net.requests.length, 2, 'no owner-only read was sent');
});

test('a company admin can start none of the platform team\'s writes either: nothing is sent', async () => {
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeModeId: 'm-1', tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeFreeze: { reason: 'Stable on dev twice.' },
    tlJudgeEstimate: { split: 'dev', loading: false, error: '', cap: '', data: { items: 1 } }, tlJudgeDecisionDraft: { decision: 'use', note: '' },
    tlJudgeThresholdDraft: '0.15', tlJudgeDraft: { kind: 'code', check_key: 'no_answer', change_note: '', examples: {} } }, { owner: false });
  const results = [await A.createSplit(), await A.register(), await A.preview('td-1'), await A.startRun(), await A.triage('turn-9', 'judge_wrong'),
    await A.freeze(), await A.decide(), await A.saveThreshold(), await A.downloadReport()];
  assert.deepEqual(results, results.map(() => ({ ok: false })));
  A.freezeOpen();
  A.startDraft('llm');
  A.revise();
  await A.estimate('dev');
  assert.equal(Jx().tlJudgeFreeze.reason, 'Stable on dev twice.', 'the draft state is not touched either');
  assert.equal(net.requests.length, 0);
});

test('opening the tab when nobody knows who the person is also reads the failure-mode list, once', async () => {
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: [] }]);
  h('GET', '/modes', [200, { ok: true, modes: [], is_owner: true, agents: [], levels: [] }]);
  jfresh({}, { owner: null });
  assert.equal(C.isOwner(S()), false);
  A.openTab();
  A.openTab();
  await flush();
  assert.equal(reqs().filter((r) => r === 'GET /modes').length, 1, 'the second open finds it already being read');
  assert.equal(C.isOwner(S()), true, 'the platform team once the service has said so');
});

test('the platform team opening a judge reads it and then its latest dev run', async () => {
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  jfresh();
  await A.openJudge('j-2');
  await flush();
  assert.deepEqual(net.requests.map((r) => r.url.pathname), [P + '/judges/j-2', P + '/judge-runs/r-dev']);
  assert.equal(Jx().tlJudge.judge.id, 'j-2');
  assert.equal(Jx().tlJudgeRunId, 'r-dev');
  assert.equal(Jx().tlJudgeRun.disagreements.length, 1);
  assert.equal(A.watching(), false);
  A.closeJudge();
  assert.equal(Jx().tlJudgeId, null);
  assert.equal(Jx().tlJudgeRun, null);
});

test('a run still going is the one a judge opens on, ahead of the latest dev run', async () => {
  const live = { id: 'r-live', judge_id: 'j-2', split: 'dev', status: 'running', rev: 1 };
  h('GET', '/judges/j-2', [200, Object.assign({}, DETAIL, { runs: [{ id: 'r-test', split: 'test', status: 'done' }, live, DETAIL.runs[0]] })]);
  h('GET', '/judge-runs/r-live', [200, { ok: true, run: Object.assign({}, RUN_DEV, live, { disagreements: [], job: { status: 'running', progress: { done: 1, total: 24 } } }) }]);
  jfresh();
  await A.openJudge('j-2');
  await flush();
  assert.equal(Jx().tlJudgeRunId, 'r-live');
  assert.equal(A.watching(), true);
  const asked = await (async () => { A.closeJudge(); await A.openJudge('j-2', { runId: 'r-test' }); await flush(); return Jx().tlJudgeRunId; })();
  assert.equal(asked, 'r-test', 'the run asked for wins');
  A.stopWatch();
});

test('a reply that lands after a company switch is dropped', async () => {
  let release;
  h('GET', '/judges', () => new Promise((r) => { release = () => r([200, { ok: true, judges: [SUMMARY_FULL], modes: [] }]); }));
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh();
  const pending = A.load();
  await flush();
  resetForCompany();
  release();
  await pending;
  assert.equal(Jx().tlJudgesRows, null);
  assert.equal(Jx().tlJudgesChecks, null);
});

test('a read overtaken by a newer read of the same judge is dropped', async () => {
  const releases = [];
  h('GET', '/judges/j-2', () => new Promise((r) => { releases.push((d) => r([200, d])); }));
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  jfresh();
  const first = A.openJudge('j-2');
  const second = A.openJudge('j-2');
  await flush();
  assert.equal(releases.length, 2);
  releases[1](Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { change_note: 'the newer read' }) }));
  await second;
  releases[0](Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { change_note: 'the older read' }) }));
  await first;
  assert.equal(Jx().tlJudge.judge.change_note, 'the newer read');
});

test('a running run is read again every 5 seconds until it finishes; then the judge and the table are read again', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let status = 'running';
  h('GET', '/judge-runs/r-dev', () => [200, { ok: true, run: Object.assign({}, RUN_DEV, { status,
    job: { status, progress: { done: 12, total: 24, errors: 0, usd: 0.15 }, usd_spent: 0.15, usd_cap: 5 } }) }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_FULL], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL });
  await A.openRun('r-dev');
  assert.equal(Jx().tlJudgeRun.status, 'running');
  assert.equal(A.watching(), true);
  net.requests = [];
  t.mock.timers.tick(T.TLJ_POLL_MS - 1);
  await flush();
  assert.equal(net.requests.length, 0);
  t.mock.timers.tick(1);
  await flush();
  assert.deepEqual(net.requests.map((r) => r.url.pathname), [P + '/judge-runs/r-dev']);
  assert.equal(A.watching(), true);
  status = 'done';
  net.requests = [];
  t.mock.timers.tick(T.TLJ_POLL_MS);
  await flush();
  const paths = net.requests.map((r) => r.url.pathname);
  for (const p of [P + '/judge-runs/r-dev', P + '/judges/j-2', P + '/judges', P + '/checks']) assert.ok(paths.includes(p), paths.join(' '));
  assert.equal(Jx().tlJudgeRun.status, 'done');
  assert.equal(A.watching(), false);
});

test('the watch stops when the reader leaves the Judges tab, closes the judge, or the company changes', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: Object.assign({}, RUN_DEV, { status: 'running' }) }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: Object.assign({}, RUN_DEV, { status: 'running' }) });
  A.watchRun();
  assert.equal(A.watching(), true);
  tlStore.set({ tab: 'runs' });
  net.requests = [];
  t.mock.timers.tick(T.TLJ_POLL_MS);
  await flush();
  assert.equal(net.requests.length, 0, 'another tab is open: no read');
  tlStore.set({ tab: 'judges' });
  A.watchRun();
  A.closeJudge();
  assert.equal(A.watching(), false, 'closing the judge stops it');
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: Object.assign({}, RUN_DEV, { status: 'running' }) });
  A.watchRun();
  resetForCompany();
  t.mock.timers.tick(T.TLJ_POLL_MS);
  await flush();
  assert.equal(net.requests.length, 0, 'a timer from before the switch reads nothing');
  A.watchRun();
  assert.equal(A.watching(), false, 'nothing to watch in the new company');
});

test('a failed quiet read of a running run keeps the run on screen and keeps watching', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  let fail = true;
  h('GET', '/judge-runs/r-dev', () => (fail ? down : [200, { ok: true, run: Object.assign({}, RUN_DEV, { status: 'running' }) }]));
  const running = Object.assign({}, RUN_DEV, { status: 'running' });
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: running, tlJudgeRunLoadedAt: 1 });
  await A.openRun('r-dev', { quiet: true });
  assert.equal(Jx().tlJudgeRunErr, 'Trace Lab is not available right now. The chat is not affected.');
  assert.equal(Jx().tlJudgeRun.status, 'running');
  assert.equal(Jx().tlJudgeRunLoading, false);
  assert.equal(A.watching(), true);
  fail = false;
  t.mock.timers.tick(T.TLJ_POLL_MS);
  await flush();
  assert.equal(Jx().tlJudgeRunErr, '');
});

test('a failed read shows its error and keeps the rows already on screen', async () => {
  h('GET', '/judges', down);
  h('GET', '/checks', down);
  jfresh({ tlJudgesRows: [SUMMARY_FULL], tlJudgesLoadedAt: 1, tlJudgesChecks: CHECKS });
  await A.load();
  assert.equal(Jx().tlJudgesErr, 'Trace Lab is not available right now. The chat is not affected.');
  assert.equal(Jx().tlJudgesChecksErr, 'Trace Lab is not available right now. The chat is not affected.');
  assert.equal(Jx().tlJudgesRows.length, 1);
  assert.equal(Jx().tlJudgesChecks.length, 2);
  assert.equal(Jx().tlJudgesLoading, false);
});

test('one failing read does not take the other with it', async () => {
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_BRIEF], modes: [{ id: 'm-1', name: 'invented_codes', status: 'confirmed' }] }]);
  h('GET', '/checks', down);
  jfresh();
  await A.load();
  assert.equal(Jx().tlJudgesRows.length, 1);
  assert.equal(Jx().tlJudgesModes.length, 1);
  assert.equal(Jx().tlJudgesErr, '');
  assert.equal(Jx().tlJudgesChecks, null);
  assert.ok(Jx().tlJudgesChecksErr);
});

test('opening a failure mode reads its readiness; another mode starts from an empty card; closing empties it', async () => {
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/modes/m-2/judge-readiness', [200, Object.assign({}, READY_THIN, { mode: Object.assign({}, READY.mode, { id: 'm-2', name: 'other' }) })]);
  h('GET', '/modes/m-1/splits/active', [200, { ok: true, split: SPLIT }]);
  jfresh();
  await A.openMode('m-1');
  assert.equal(Jx().tlJudgeReady.next_step, 'triage');
  assert.ok(Jx().tlJudgeReadyLoadedAt);
  await A.loadSplit('m-1');
  assert.equal(Jx().tlJudgeSplit.id, 's-1');
  A.setSplitForm({ override: true, reason: 'x' });
  await A.openMode('m-1');
  assert.equal(Jx().tlJudgeSplitForm.override, true, 'the same mode keeps what was typed');
  await A.openMode('m-2');
  assert.equal(Jx().tlJudgeModeId, 'm-2');
  assert.equal(Jx().tlJudgeSplit, null, 'another mode: an empty card');
  assert.deepEqual(Jx().tlJudgeSplitForm, { override: false, reason: '' });
  assert.equal(Jx().tlJudgeReady.next_step, 'label_more');
  A.closeMode();
  assert.equal(Jx().tlJudgeModeId, null);
  assert.equal(Jx().tlJudgeReady, null);
});

test('a readiness read that fails shows its error on the card', async () => {
  h('GET', '/modes/m-1/judge-readiness', down);
  jfresh();
  await A.openMode('m-1');
  assert.equal(Jx().tlJudgeReadyErr, 'Trace Lab is not available right now. The chat is not affected.');
  assert.equal(Jx().tlJudgeReadyLoading, false);
});

test('the open mode, judge and run are read again after a stale base; a company admin reads none of them', async () => {
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV, tlJudgeModeId: 'm-1' });
  A.reloadOpen();
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /judge-runs/r-dev', 'GET /judges/j-2', 'GET /modes/m-1/judge-readiness']);
  net.requests = [];
  A.reopen();
  await flush();
  assert.deepEqual(reqs().sort(), ['GET /judges/j-2', 'GET /modes/m-1/judge-readiness'], 'coming back to the tab reads what was open');
});

test('coming back to the tab with a run still going reads it again at once and watches it again', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const running = Object.assign({}, RUN_DEV, { status: 'running', job: { status: 'running', progress: { done: 3, total: 24 } } });
  h('GET', '/judges/j-2', [200, Object.assign({}, DETAIL, { runs: [{ id: 'r-dev', split: 'dev', status: 'running' }] })]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: Object.assign({}, running, { job: { status: 'running', progress: { done: 9, total: 24 } } }) }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: running, tlJudgeRunLoadedAt: 1 });
  assert.equal(A.watching(), false, 'leaving the tab stopped it');
  A.reopen();
  await flush();
  assert.ok(reqs().includes('GET /judge-runs/r-dev'), 'the run is read again without waiting 5 seconds');
  assert.equal(Jx().tlJudgeRun.job.progress.done, 9);
  assert.equal(A.watching(), true, 'and watched again');
});

test('coming back to the tab keeps the run the reader had open, not the latest dev run', async () => {
  const test = { id: 'r-test', split: 'test', status: 'done' };
  h('GET', '/judges/j-2', [200, Object.assign({}, DETAIL_TESTED, { runs: [test].concat(DETAIL_TESTED.runs.slice(1)) })]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  h('GET', '/judge-runs/r-test', [200, { ok: true, run: RUN_TEST }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeRunId: 'r-test', tlJudgeRun: RUN_TEST, tlJudgeRunLoadedAt: 1 });
  A.reopen();
  await flush();
  assert.equal(Jx().tlJudgeRunId, 'r-test');
  assert.equal(reqs().includes('GET /judge-runs/r-dev'), false);
});

test('a refused write\'s error goes when the dialog, the panel or the form that showed it is closed or opened again, and Retry cannot bring it back', async () => {
  h('POST', '/judges/j-2/freeze', [409, { detail: { ok: false, error: 'Triage every disagreement first (1 left).', reason: 'untriaged_disagreements' } }]);
  ownJudge();
  A.freezeOpen();
  A.setFreezeReason('Dev agreement is high; no third revision.');
  await A.freeze();
  assert.ok(writes()['judges:freeze:j-2'].error);
  A.freezeClose();
  assert.equal(writes()['judges:freeze:j-2'], undefined, 'closing the dialog drops its error');
  assert.deepEqual(await A.retry('judges:freeze:j-2'), { ok: false }, 'and there is nothing left to retry');
  A.freezeOpen();
  A.setFreezeReason('Dev agreement is high; no third revision.');
  await A.freeze();
  A.freezeOpen();
  assert.equal(writes()['judges:freeze:j-2'], undefined, 'opening it again starts clean');
  h('POST', '/judges/j-2/runs', [409, { detail: { ok: false, error: 'The held-out test has already run for this version.', reason: 'test_already_run' } }]);
  h('GET', '/judges/j-2/estimate', [200, { ok: true, items: 1, cached: 0, to_call: 1, usd_estimate: 0.01 }]);
  await A.estimate('dev');
  await A.startRun();
  assert.ok(writes()['judges:run:j-2'].error);
  A.closeEstimate();
  assert.equal(writes()['judges:run:j-2'], undefined);
  await A.estimate('dev');
  await A.startRun();
  await A.estimate('dev');
  assert.equal(writes()['judges:run:j-2'], undefined, 'a new estimate starts clean');
  h('POST', '/judges', [422, { detail: { ok: false, error: 'Say what changed.', reason: 'change_note_required' } }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1 });
  A.startDraft('code');
  A.setDraft({ check_key: 'no_answer', change_note: 'x' });
  await A.register();
  assert.ok(writes()['judges:register:m-1'].error);
  A.cancelDraft();
  assert.equal(writes()['judges:register:m-1'], undefined);
  A.startDraft('code');
  A.setDraft({ check_key: 'no_answer', change_note: 'x' });
  await A.register();
  assert.ok(writes()['judges:register:m-1'].error);
  A.startDraft('llm');
  assert.equal(writes()['judges:register:m-1'], undefined, 'a new draft starts clean');
});

test('a write still in flight keeps its busy state when its dialog is closed, so it cannot be sent twice', async () => {
  let release;
  h('POST', '/judges/j-2/freeze', () => new Promise((r) => { release = () => r([200, { ok: true, judge: FROZEN }]); }));
  h('GET', '/judges/j-2', [200, DETAIL_FROZEN]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  ownJudge({ tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const pending = A.freeze();
  await flush();
  assert.equal(writes()['judges:freeze:j-2'].busy, true);
  A.freezeClose();
  assert.equal(writes()['judges:freeze:j-2'].busy, true, 'closing the dialog does not forget a freeze that is on its way');
  release();
  assert.equal((await pending).ok, true);
  assert.equal(writes()['judges:freeze:j-2'], undefined);
});

// ── Task 16: the writes ──────────────────────────────────────────────────────────────────────────────────────────

const ownJudge = (extra) => jfresh(Object.assign({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeRunId: 'r-dev', tlJudgeRun: RUN_DEV }, extra || {}));
const writes = () => Jx().tlJudgeWrites;

test('triage writes the choice and the note with the run base, and takes the run the server returns', async () => {
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', (u, body) => [200, { ok: true, run: Object.assign({}, RUN_DEV, { rev: 3,
    disagreements: [Object.assign({}, RUN_DEV.disagreements[0], { triage: body.triage, note: body.note, by_email: 'team@plenum.example' })] }) }]);
  h('GET', '/judges/j-2', [200, DETAIL_TRIAGED]);
  ownJudge();
  A.setTriageNote('turn-9', 'The label missed the implied code.');
  const res = await A.triage('turn-9', 'label_wrong');
  await flush();
  assert.equal(res.ok, true);
  assert.deepEqual(net.requests.find((r) => r.method === 'PATCH').body, { triage: 'label_wrong', note: 'The label missed the implied code.', base: 2 });
  assert.equal(Jx().tlJudgeRun.rev, 3);
  assert.equal(Jx().tlJudgeRun.disagreements[0].triage, 'label_wrong');
  assert.equal(Jx().tlJudgeTriageNotes['turn-9'], undefined);
  assert.equal(Jx().tlJudge.runs[0].untriaged, 0);
  assert.equal(writes()['judges:triage:r-dev:turn-9'], undefined);
  assert.deepEqual(await A.triage('turn-9', 'made_up'), { ok: false });
});

test('a note left empty is sent as null', async () => {
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [200, { ok: true, run: RUN_DEV }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  ownJudge();
  await A.triage('turn-9', 'judge_wrong');
  assert.deepEqual(net.requests.find((r) => r.method === 'PATCH').body, { triage: 'judge_wrong', note: null, base: 2 });
});

test('a stale base says another tab changed it and offers a reload, not a retry', async () => {
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [409, { detail: { ok: false, error: 'This run changed in another tab; reload it.', reason: 'stale_base' } }]);
  ownJudge();
  A.setTriageNote('turn-9', 'Kept after the conflict.');
  const res = await A.triage('turn-9', 'judge_wrong');
  assert.deepEqual(res, { ok: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, reason: 'stale_base' });
  assert.deepEqual(writes()['judges:triage:r-dev:turn-9'], { busy: false, error: 'Changed in another tab — reload to see the latest.', conflict: true, canRetry: false });
  assert.equal(Jx().tlJudgeTriageNotes['turn-9'], 'Kept after the conflict.');
});

test('a 409 with no reason, or the reason "stale", is also another tab\'s change; any other reason is the server\'s own rule', async () => {
  ownJudge();
  for (const detail of [{ ok: false, error: 'Conflict.' }, { ok: false, error: 'Old.', reason: 'stale' }]) {
    h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [409, { detail }]);
    const res = await A.triage('turn-9', 'judge_wrong');
    assert.equal(res.conflict, true, JSON.stringify(detail));
  }
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [409, { detail: { ok: false, error: 'This run is not a dev run.', reason: 'not_dev_run' } }]);
  const rule = await A.triage('turn-9', 'judge_wrong');
  assert.deepEqual(rule, { ok: false, error: 'This run is not a dev run.', conflict: false, reason: 'not_dev_run' });
});

test('a network failure keeps the work and offers Retry, and Retry does everything the press did, not just the request', async () => {
  let up = false;
  h('POST', '/judges', () => { if (!up) throw new Error('network down'); return [201, { ok: true, judge: Object.assign({}, DRAFT, { id: 'j-3', version: 3 }), warnings: [] }]; });
  h('GET', '/judges/j-3', [200, Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { id: 'j-3', version: 3 }), runs: [] })]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_FULL, DRAFT], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeDraft: { kind: 'code', task: '', fail_rule: '', pass_rule: '', edge_cases: '', change_note: 'Use the check.', check_key: 'no_answer', examples: {} } });
  const first = await A.register();
  assert.equal(first.ok, false);
  assert.equal(first.error, 'Could not save — network down');
  assert.deepEqual(writes()['judges:register:m-1'], { busy: false, error: 'Could not save — network down', conflict: false, canRetry: true });
  assert.ok(Jx().tlJudgeDraft, 'the draft is kept');
  up = true;
  const again = await A.retry('judges:register:m-1');
  await flush();
  assert.equal(again.ok, true);
  assert.equal(Jx().tlJudgeDraft, null, 'the draft closed, as a first success would have');
  assert.equal(Jx().tlJudgeId, 'j-3', 'and the new version opened');
  assert.equal(writes()['judges:register:m-1'], undefined);
  assert.deepEqual(await A.retry('judges:register:m-1'), { ok: false }, 'nothing left to retry');
});

test('a write whose company changed before the reply is dropped and shows nothing', async () => {
  let release;
  h('POST', '/judges/j-2/freeze', () => new Promise((r) => { release = () => r([200, { ok: true, judge: FROZEN }]); }));
  ownJudge({ tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const pending = A.freeze();
  await flush();
  resetForCompany();
  release();
  const res = await pending;
  assert.deepEqual(res, { ok: false, stale: true });
  assert.deepEqual(writes(), {});
});

test('a write that fails after the company changed shows nothing, and so does the app\'s own stale-company error', async () => {
  let fail;
  h('POST', '/judges/j-2/freeze', () => new Promise((r) => { fail = () => r([503, { detail: { ok: false, error: 'Trace Lab is not available right now.' } }]); }));
  ownJudge({ tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const pending = A.freeze();
  await flush();
  resetForCompany();
  fail();
  assert.deepEqual(await pending, { ok: false, stale: true });
  assert.deepEqual(writes(), {}, 'no error is drawn for a company that has gone');
  // The company changing while the request was out: the app's client throws its stale-scope error, which stays quiet.
  ownJudge({ tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  const quiet = await A.save('judges:probe', () => { throw Object.assign(new Error('the company changed while this request was in flight'), { staleScope: true }); });
  assert.deepEqual(quiet, { ok: false, stale: true });
  assert.deepEqual(writes(), {});
});

test('freeze needs a stop reason, sends the judge base, and closes its dialog when done', async () => {
  h('POST', '/judges/j-2/freeze', [200, { ok: true, judge: FROZEN }]);
  h('GET', '/judges/j-2', [200, DETAIL_FROZEN]);
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_FULL, FROZEN], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  ownJudge({ tlJudge: DETAIL_TRIAGED });
  A.freezeOpen();
  assert.deepEqual(Jx().tlJudgeFreeze, { reason: '' });
  A.setFreezeReason('too short');
  assert.deepEqual(await A.freeze(), { ok: false });
  assert.equal(net.requests.length, 0);
  A.setFreezeReason('Dev agreement is high; no third revision.');
  const res = await A.freeze();
  await flush();
  assert.equal(res.ok, true);
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { stop_reason: 'Dev agreement is high; no third revision.', base: 3 });
  assert.equal(Jx().tlJudgeFreeze, null);
  assert.equal(Jx().tlJudge.judge.status, 'frozen');
});

test('a refused freeze keeps the dialog and its reason, and shows the server words with no retry', async () => {
  h('POST', '/judges/j-2/freeze', [409, { detail: { ok: false, error: 'Triage every disagreement first (1 left).', reason: 'untriaged_disagreements', untriaged: 1 } }]);
  ownJudge();
  A.freezeOpen();
  A.setFreezeReason('Dev agreement is high; no third revision.');
  const res = await A.freeze();
  assert.equal(res.ok, false);
  assert.equal(res.reason, 'untriaged_disagreements');
  assert.deepEqual(Jx().tlJudgeFreeze, { reason: 'Dev agreement is high; no third revision.' });
  assert.deepEqual(writes()['judges:freeze:j-2'], { busy: false, error: 'Triage every disagreement first (1 left).', conflict: false, canRetry: false });
});

test('the freeze dialog opens only on an open judge, trims nothing the reader typed, and closes', () => {
  jfresh();
  A.freezeOpen();
  assert.equal(Jx().tlJudgeFreeze, null, 'no judge open');
  ownJudge();
  A.freezeOpen();
  A.setFreezeReason('x'.repeat(5000));
  assert.equal(Jx().tlJudgeFreeze.reason.length, T.TLJ_STOP_REASON_MAX + 100, 'long text is cut, but not before the check can see it is too long');
  assert.equal(T.tljStopReasonCheck(Jx().tlJudgeFreeze.reason).ok, false);
  A.freezeClose();
  assert.equal(Jx().tlJudgeFreeze, null);
  A.setFreezeReason('ignored with no dialog open');
  assert.equal(Jx().tlJudgeFreeze, null);
});

test('the held-out test: the estimate first, then one run; a second start is refused in the server words', async () => {
  h('GET', '/judges/j-2/estimate', [200, { ok: true, items: 24, cached: 0, to_call: 24, usd_estimate: 0.288, model: 'claude-sonnet-5' }]);
  h('POST', '/judges/j-2/runs', [202, { ok: true, run: RUN_TEST, job: { id: 'job-1', status: 'queued' } }]);
  h('GET', '/judges/j-2', [200, DETAIL_TESTED]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_FROZEN });
  await A.estimate('test');
  assert.equal(net.requests[0].url.searchParams.get('split'), 'test');
  assert.deepEqual(Jx().tlJudgeEstimate, { split: 'test', loading: false, error: '', cap: '',
    data: { ok: true, items: 24, cached: 0, to_call: 24, usd_estimate: 0.288, model: 'claude-sonnet-5' } });
  A.setCap('2.5');
  const res = await A.startRun();
  await flush();
  assert.equal(res.ok, true);
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { split: 'test', usd_cap: 2.5 });
  assert.equal(Jx().tlJudgeEstimate, null);
  assert.equal(Jx().tlJudgeRunId, 'r-test');
  assert.equal(Jx().tlJudge.runs[0].split, 'test');
  h('POST', '/judges/j-2/runs', [409, { detail: { ok: false, error: 'The held-out test has already run for this version.', reason: 'test_already_run', run_id: 'r-test' } }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeEstimate: { split: 'test', loading: false, error: '', cap: '', data: { items: 24, cached: 24, to_call: 0, usd_estimate: 0 } } });
  const again = await A.startRun();
  assert.equal(again.ok, false);
  assert.equal(again.reason, 'test_already_run');
  assert.deepEqual(writes()['judges:run:j-2'], { busy: false, error: 'The held-out test has already run for this version.', conflict: false, canRetry: false });
});

test('a run starts for dev when the estimate says dev; a run going is watched', async () => {
  h('GET', '/judges/j-2/estimate', [200, { ok: true, items: 24, cached: 0, to_call: 24, usd_estimate: 0.29 }]);
  const queued = Object.assign({}, RUN_DEV, { id: 'r-new', status: 'queued', disagreements: [], rev: 1 });
  h('POST', '/judges/j-2/runs', [202, { ok: true, run: queued, job: { id: 'job-2', status: 'queued' } }]);
  h('GET', '/judges/j-2', [200, Object.assign({}, DETAIL, { runs: [{ id: 'r-new', split: 'dev', status: 'queued' }] })]);
  h('GET', '/judge-runs/r-new', [200, { ok: true, run: queued }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL });
  await A.estimate();
  assert.equal(net.requests[0].url.searchParams.get('split'), 'dev');
  const res = await A.startRun();
  assert.equal(res.ok, true);
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { split: 'dev' });
  assert.equal(A.watching(), true, 'a queued run is read again every 5 seconds');
  A.stopWatch();
  assert.deepEqual(await A.startRun(), { ok: false }, 'with no estimate open there is nothing to start');
});

test('the cost cap is sent only as a positive number; what is typed into it is cut at 12 characters', async () => {
  h('POST', '/judges/j-2/runs', [202, { ok: true, run: Object.assign({}, RUN_DEV, { id: 'r-new', status: 'done' }), job: { id: 'job-2', status: 'done' } }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  const data = { items: 24, cached: 0, to_call: 24, usd_estimate: 0.29 };
  const sent = async (cap) => {
    jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeEstimate: { split: 'dev', loading: false, error: '', cap, data } });
    net.requests = [];
    await A.startRun();
    return net.requests.find((r) => r.method === 'POST').body;
  };
  assert.deepEqual(await sent('2.5'), { split: 'dev', usd_cap: 2.5 });
  assert.deepEqual(await sent(' 3 '), { split: 'dev', usd_cap: 3 });
  for (const cap of ['0', '-3', 'abc', '   ', '', 'Infinity']) assert.deepEqual(await sent(cap), { split: 'dev' }, JSON.stringify(cap));
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL, tlJudgeEstimate: { split: 'dev', loading: false, error: '', cap: '', data } });
  A.setCap('x'.repeat(40));
  assert.equal(Jx().tlJudgeEstimate.cap.length, 12);
  A.setCap(undefined);
  assert.equal(Jx().tlJudgeEstimate.cap, '');
});

test('an estimate that fails says so on the panel and starts nothing; a closed panel is empty', async () => {
  h('GET', '/judges/j-2/estimate', [409, { detail: { ok: false, error: 'This judge is frozen.', reason: 'judge_frozen' } }]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL });
  await A.estimate('dev');
  assert.equal(Jx().tlJudgeEstimate.error, 'This judge is frozen.');
  assert.equal(Jx().tlJudgeEstimate.loading, false);
  assert.deepEqual(await A.startRun(), { ok: false });
  A.closeEstimate();
  assert.equal(Jx().tlJudgeEstimate, null);
  A.setCap('3');
  assert.equal(Jx().tlJudgeEstimate, null);
});

test('a new version sends its rules and three train examples, each verdict taken from its role or its label', async () => {
  h('GET', '/modes/m-1/splits/active', [200, { ok: true, split: SPLIT }]);
  h('POST', '/judges', [201, { ok: true, judge: Object.assign({}, DRAFT, { id: 'j-3', version: 3 }),
    warnings: [{ code: 'beyond_revision_limit', message: 'At most two revisions are expected; say why another one is needed.' }] }]);
  h('GET', '/judges/j-3', [200, Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { id: 'j-3', version: 3 }), runs: [] })]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [SUMMARY_FULL, DRAFT], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1 });
  A.startDraft('llm');
  await flush();
  assert.equal(Jx().tlJudgeSplit.id, 's-1');
  A.setDraft({ fail_rule: 'The answer names a code that no step returned.', pass_rule: 'Every code in the answer was returned by a step.',
    change_note: 'Third revision: a tighter borderline example.' });
  A.setExample('clear_fail', { turn_id: 'tr-f', critique: 'Names AHU-09, which no step returned.' });
  A.setExample('clear_pass', { turn_id: 'tr-p', critique: 'Every code came from the query.' });
  A.setExample('borderline', { turn_id: 'tr-b', critique: 'The code is only implied by the answer.' });
  const res = await A.register();
  await flush();
  assert.equal(res.ok, true);
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { mode_id: 'm-1', kind: 'llm', change_note: 'Third revision: a tighter borderline example.',
    parts: { task: null, fail_rule: 'The answer names a code that no step returned.', pass_rule: 'Every code in the answer was returned by a step.', edge_cases: null },
    examples: [
      { turn_id: 'tr-f', role: 'clear_fail', critique: 'Names AHU-09, which no step returned.', verdict: 'Fail' },
      { turn_id: 'tr-p', role: 'clear_pass', critique: 'Every code came from the query.', verdict: 'Pass' },
      { turn_id: 'tr-b', role: 'borderline', critique: 'The code is only implied by the answer.', verdict: 'Fail' }] });
  assert.equal(Jx().tlJudgeDraft, null);
  assert.deepEqual(Jx().tlJudgeWarnings.map((w) => w.code), ['beyond_revision_limit']);
  assert.equal(Jx().tlJudgeId, 'j-3');
});

test('a code check is a version too: it names the check and loads no split', async () => {
  h('POST', '/judges', [201, { ok: true, judge: Object.assign({}, DRAFT, { id: 'j-4', kind: 'code' }), warnings: [] }]);
  h('GET', '/judges/j-4', [200, Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { id: 'j-4', kind: 'code' }), runs: [] })]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY, tlJudgeReadyLoadedAt: 1 });
  A.startDraft('code');
  assert.equal(Jx().tlJudgeDraft.kind, 'code');
  assert.equal(net.requests.length, 0, 'a code check needs no train answers');
  A.setDraft({ check_key: 'no_answer', change_note: '  Switch to the code check.  ' });
  await A.register();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { mode_id: 'm-1', kind: 'code', change_note: 'Switch to the code check.', check_key: 'no_answer' });
  A.startDraft('anything else');
  assert.equal(Jx().tlJudgeDraft.kind, 'llm');
  A.cancelDraft();
  assert.equal(Jx().tlJudgeDraft, null);
  A.setDraft({ fail_rule: 'ignored with no draft open' });
  A.setExample('clear_fail', { turn_id: 'x' });
  assert.equal(Jx().tlJudgeDraft, null);
});

test('a revision starts from the open judge\'s rules and examples, and opens its failure mode', async () => {
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/modes/m-1/splits/active', [200, { ok: true, split: SPLIT }]);
  ownJudge();
  A.revise();
  await flush();
  const d = Jx().tlJudgeDraft;
  assert.equal(Jx().tlJudgeModeId, 'm-1');
  assert.equal(d.kind, 'llm');
  assert.equal(d.fail_rule, 'The answer names a code that no step returned.');
  assert.equal(d.edge_cases, 'A code the asker typed counts as returned.');
  assert.deepEqual(d.examples.borderline, { turn_id: 'tr-b', critique: 'The code is only implied.' });
  assert.equal(d.change_note, '');
  assert.equal(Jx().tlJudgeSplit.id, 's-1');
  jfresh();
  A.revise();
  assert.equal(Jx().tlJudgeDraft, null, 'no judge open: nothing to revise');
});

test('a split below the minimum goes with its reason; the decision and the threshold go with the judge base', async () => {
  h('POST', '/modes/m-1/splits', [201, { ok: true, split: SPLIT }]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY_THIN, tlJudgeReadyLoadedAt: 1 });
  A.setSplitForm({ override: true, reason: '  Pilot volume; approved by Hussain.  ' });
  assert.equal((await A.createSplit()).ok, true);
  await flush();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, { override_min_labels: true, override_reason: 'Pilot volume; approved by Hussain.' });
  assert.deepEqual(Jx().tlJudgeSplitForm, { override: false, reason: '' });
  assert.equal(Jx().tlJudgeReady.next_step, 'triage');
  net.requests = [];
  h('PATCH', '/judges/j-2/decision', [200, { ok: true, judge: FROZEN }]);
  h('PATCH', '/judges/j-2/threshold', [200, { ok: true, judge: FROZEN }]);
  h('GET', '/judges/j-2', [200, DETAIL_TESTED]);
  jfresh({ tlJudgeId: 'j-2', tlJudge: DETAIL_TESTED, tlJudgeRunId: 'r-test', tlJudgeRun: RUN_TEST });
  assert.deepEqual(await A.decide(), { ok: false });
  A.setDecisionDraft({ decision: 'use', note: ' Held-out rates are good. ' });
  assert.equal((await A.decide()).ok, true);
  A.setThresholdDraft('1.5');
  assert.deepEqual(await A.saveThreshold(), { ok: false });
  A.setThresholdDraft('0.15');
  assert.equal((await A.saveThreshold()).ok, true);
  await flush();
  assert.deepEqual(net.requests.filter((r) => r.method === 'PATCH').map((r) => [r.url.pathname, r.body]), [
    [P + '/judges/j-2/decision', { decision: 'use', note: 'Held-out rates are good.', base: 4 }],
    [P + '/judges/j-2/threshold', { threshold: 0.15, base: 4 }]]);
  assert.deepEqual(Jx().tlJudgeDecisionDraft, { decision: '', note: '' });
  assert.equal(Jx().tlJudgeThresholdDraft, '');
});

test('a split with no override sends an empty body; an override with no reason still goes (the service refuses it in its words)', async () => {
  h('POST', '/modes/m-1/splits', [201, { ok: true, split: SPLIT }]);
  h('GET', '/modes/m-1/judge-readiness', [200, READY]);
  h('GET', '/judges', [200, { ok: true, judges: [], modes: [] }]);
  h('GET', '/checks', [200, { ok: true, checks: CHECKS }]);
  jfresh({ tlJudgeModeId: 'm-1', tlJudgeReady: READY });
  await A.createSplit();
  assert.deepEqual(net.requests.find((r) => r.method === 'POST').body, {});
  net.requests = [];
  h('POST', '/modes/m-1/splits', [422, { detail: { ok: false, error: 'Say why this split is built below the minimum.', reason: 'override_reason_required' } }]);
  A.setSplitForm({ override: true, reason: '' });
  const res = await A.createSplit();
  assert.equal(res.ok, false);
  assert.equal(Jx().tlJudgeSplitForm.override, true, 'the form is kept');
  assert.match(writes()['judges:split:m-1'].error, /Could not save — Say why this split/);
});

test('preview keeps each answer verdict; the failure report downloads as Markdown', async () => {
  h('POST', '/judges/j-2/preview', [200, { ok: true, failure_present: 0, verdict: 'Pass', critique: 'Every code came from a step.', truncated: false, usd: 0, status: 'decided' }]);
  ownJudge();
  assert.equal((await A.preview('td-1')).ok, true);
  assert.equal(Jx().tlJudgePreview['td-1'].verdict, 'Pass');
  assert.deepEqual(net.requests[0].body, { turn_id: 'td-1' });
  h('GET', '/failure-report', [200, '# Failure report\n']);
  net.requests = [];
  assert.equal((await A.downloadReport()).ok, true);
  assert.equal(net.requests[0].url.searchParams.get('format'), 'md');
});

test('the failure report is saved through a link when the page can; the report read keeps what is on screen on a failure', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  h('GET', '/failure-report', (u) => (u.searchParams.get('format') === 'md' ? [200, '# Failure report\n'] : [200, REPORT]));
  jfresh();
  await A.loadReport();
  assert.equal(Jx().tlJudgeReport.modes.length, 1);
  assert.ok(Jx().tlJudgeReportLoadedAt);
  const realDoc = globalThis.document;
  const realCreate = URL.createObjectURL;
  const clicks = [];
  const link = { click: () => clicks.push(link.download), remove: () => {} };
  globalThis.document = { visibilityState: 'visible', createElement: () => link, body: { appendChild: () => {} } };
  URL.createObjectURL = () => 'blob:report';
  try {
    assert.equal((await A.downloadReport()).ok, true);
    assert.deepEqual(clicks, ['failure-report.md'], 'the fallback name when the service sent none');
  } finally {
    globalThis.document = realDoc;
    URL.createObjectURL = realCreate;
  }
  h('GET', '/failure-report', down);
  await A.loadReport();
  assert.equal(Jx().tlJudgeReportErr, 'Trace Lab is not available right now. The chat is not affected.');
  assert.equal(Jx().tlJudgeReport.modes.length, 1, 'the report already on screen stays');
});

test('a preview that fails shows its error under that answer, with Retry, and keeps the other answers', async () => {
  let ok = false;
  h('POST', '/judges/j-2/preview', () => (ok ? [200, { ok: true, verdict: 'Fail', critique: 'x' }] : [503, { detail: { ok: false, error: 'The judge model did not answer.' } }]));
  ownJudge({ tlJudgePreview: { 'td-2': { verdict: 'Pass' } } });
  const res = await A.preview('td-1');
  assert.equal(res.ok, false);
  assert.deepEqual(writes()['judges:preview:j-2:td-1'], { busy: false, error: 'Could not save — The judge model did not answer.', conflict: false, canRetry: true });
  assert.equal(Jx().tlJudgePreview['td-2'].verdict, 'Pass');
  ok = true;
  assert.equal((await A.retry('judges:preview:j-2:td-1')).ok, true);
  assert.equal(Jx().tlJudgePreview['td-1'].verdict, 'Fail');
});

// ── fix round 1: a write's error, busy state and Retry belong to the judge it was made on ─────────────────────────
// Judges of one failure mode share the same dev split, so the same preview and triage turn ids appear on every version.

const NOW = Date.parse('2026-10-15T12:00:00Z');
const pane = () => V.judgesVals(S(), A, NOW);
const asJudge = (detail, id, runs) => Object.assign({}, detail, { judge: Object.assign({}, detail.judge, { id }), runs: runs || [] });
const IN_USE = Object.assign({}, DETAIL_TESTED, { judge: Object.assign({}, DETAIL_TESTED.judge, { in_use: true, decision: 'use', monitor_threshold: 0.2 }) });
const stale409 = [409, { detail: { ok: false, error: 'This changed in another tab; reload it.', reason: 'stale_base' } }];

test('a refused preview on one judge shows nothing on another judge, and its Retry cannot run on the old one', async () => {
  h('POST', '/judges/j-2/preview', [503, { detail: { ok: false, error: 'The judge model did not answer.' } }]);
  h('GET', '/judges/j-9', [200, asJudge(DETAIL, 'j-9')]);
  ownJudge();
  assert.equal((await A.preview('td-1')).ok, false);
  const before = pane().tljJudge.preview[0];
  assert.deepEqual([before.label, before.error, typeof before.retry], ['Dev answer 1', 'Could not save — The judge model did not answer.', 'function']);
  await A.openJudge('j-9');
  const after = pane().tljJudge.preview[0];
  assert.deepEqual([after.label, after.error, after.retry, after.busy], ['Dev answer 1', '', null, false], 'j-9 starts clean');
  assert.deepEqual(await A.retry('judges:preview:j-2:td-1'), { ok: false }, 'nothing is left that could run on j-2');
  assert.equal(net.requests.filter((r) => r.method === 'POST').length, 1, 'no second model call was paid for');
  assert.deepEqual(writes(), {});
});

test('a refused decision or threshold on one judge shows no error and no Reload on another judge', async () => {
  h('PATCH', '/judges/j-2/decision', stale409);
  h('PATCH', '/judges/j-2/threshold', stale409);
  h('GET', '/judges/j-9', [200, asJudge(IN_USE, 'j-9', IN_USE.runs)]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  ownJudge({ tlJudge: IN_USE });
  A.setDecisionDraft({ decision: 'do_not_use' });
  A.setThresholdDraft('0.3');
  await A.decide();
  await A.saveThreshold();
  const own = pane().tljJudge;
  assert.deepEqual([typeof own.decision.reload, typeof own.threshold.reload], ['function', 'function']);
  assert.ok(own.decision.error && own.threshold.error);
  await A.openJudge('j-9');
  await flush();
  const other = pane().tljJudge;
  assert.equal(other.title, 'invented_codes · v2 · LLM judge');
  assert.deepEqual([other.decision.error, other.decision.reload, other.threshold.error, other.threshold.reload], ['', null, '', null]);
  assert.deepEqual(writes(), {});
});

test('a refused triage in one judge\'s run shows nothing on the same answer in another judge\'s run', async () => {
  h('PATCH', '/judge-runs/r-dev/disagreements/turn-9', [503, { detail: { ok: false, error: 'The service is down.' } }]);
  h('GET', '/judges/j-9', [200, asJudge(DETAIL, 'j-9', [Object.assign({}, RUN_ROW_DEV, { id: 'r-9', judge_id: 'j-9' })])]);
  h('GET', '/judge-runs/r-9', [200, { ok: true, run: Object.assign({}, RUN_DEV, { id: 'r-9', judge_id: 'j-9' }) }]);
  ownJudge();
  await A.triage('turn-9', 'judge_wrong');
  const own = pane().tljRun.disagreements[0];
  assert.deepEqual([own.turnId, own.error, typeof own.retry], ['turn-9', 'Could not save — The service is down.', 'function']);
  await A.openJudge('j-9');
  await flush();
  assert.equal(Jx().tlJudgeRunId, 'r-9');
  const other = pane().tljRun.disagreements[0];
  assert.deepEqual([other.turnId, other.error, other.retry, other.busy, other.reload], ['turn-9', '', null, false, null]);
  assert.deepEqual(await A.retry('judges:triage:r-dev:turn-9'), { ok: false });
  assert.equal(net.requests.filter((r) => r.method === 'PATCH').length, 1);
});

test('closing a judge, or coming back to it from another, drops what its page had refused and the Retry for it', async () => {
  h('POST', '/judges/j-2/preview', [503, { detail: { ok: false, error: 'The judge model did not answer.' } }]);
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judges/j-9', [200, asJudge(DETAIL, 'j-9')]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  ownJudge();
  await A.preview('td-1');
  assert.ok(writes()['judges:preview:j-2:td-1']);
  A.closeJudge();
  assert.deepEqual(writes(), {}, 'closing the judge drops it');
  assert.deepEqual(await A.retry('judges:preview:j-2:td-1'), { ok: false });
  await A.openJudge('j-2');
  await flush();
  assert.equal(pane().tljJudge.preview[0].error, '');
  await A.preview('td-1');
  await A.openJudge('j-9');
  await A.openJudge('j-2');
  await flush();
  assert.deepEqual([pane().tljJudge.preview[0].error, pane().tljJudge.preview[0].retry], ['', null], 'back on j-2 the old refusal does not return');
  assert.deepEqual(writes(), {});
  // Writes that are not a judge page's are not dropped by opening another judge.
  tlStore.set((st) => ({ judges: Object.assign({}, C.judgesOf(st), { tlJudgeWrites: { 'judges:register:m-1': { busy: false, error: 'Say what changed.', conflict: false, canRetry: false } } }) }));
  await A.openJudge('j-9');
  assert.equal(writes()['judges:register:m-1'].error, 'Say what changed.', 'the failure mode\'s own write stays with its form');
});

test('a write still on its way when another judge is opened is left alone, and its outcome lands on its own judge only', async () => {
  let fail;
  let failFreeze;
  h('GET', '/judges/j-2/estimate', [200, { ok: true, items: 1, cached: 0, to_call: 1, usd_estimate: 0.01 }]);
  h('GET', '/judges/j-9/estimate', [200, { ok: true, items: 2, cached: 0, to_call: 2, usd_estimate: 0.02 }]);
  h('POST', '/judges/j-2/runs', () => new Promise((r) => { fail = () => r([503, { detail: { ok: false, error: 'The service is down.' } }]); }));
  h('POST', '/judges/j-2/freeze', () => new Promise((r) => { failFreeze = () => r([503, { detail: { ok: false, error: 'The service is down.' } }]); }));
  h('GET', '/judges/j-9', [200, asJudge(DETAIL_TRIAGED, 'j-9')]);
  ownJudge({ tlJudge: DETAIL_TRIAGED, tlJudgeFreeze: { reason: 'Stable on dev twice.' } });
  await A.estimate('dev');
  const run = A.startRun();
  const freeze = A.freeze();
  await flush();
  assert.equal(pane().tljJudge.dev.enabled, false, 'on j-2 the start of a run is on its way');
  await A.openJudge('j-9');
  assert.equal(writes()['judges:run:j-2'].busy, true, 'opening another judge does not forget a write on its way');
  assert.equal(writes()['judges:freeze:j-2'].busy, true);
  assert.equal(pane().tljJudge.dev.enabled, true, 'it does not hold back j-9\'s buttons');
  await A.estimate('dev');
  A.freezeOpen();
  A.setFreezeReason('Stable on dev twice.');
  assert.equal(pane().tljJudge.freezeDialog.canConfirm, true, 'nor j-9\'s Freeze');
  fail();
  failFreeze();
  assert.equal((await run).ok, false);
  assert.equal((await freeze).ok, false);
  const j9 = pane().tljJudge;
  assert.deepEqual([j9.estimate.startError, j9.estimate.retry, j9.estimate.busy], ['', null, false], 'j-2\'s failed run does not show in j-9\'s panel');
  assert.deepEqual([j9.freezeDialog.error, j9.freezeDialog.busy], ['', false], 'nor j-2\'s failed freeze in j-9\'s dialog');
  assert.ok(writes()['judges:run:j-2'].error, 'it is kept under its own judge');
});

test('a preview, triage, decision or threshold on its way when another judge is opened fails into its own judge, not the one now open', async () => {
  const gates = [];
  const gate = (method, path) => h(method, path, () => new Promise((r) => { gates.push(() => r([503, { detail: { ok: false, error: 'The service is down.' } }])); }));
  gate('POST', '/judges/j-2/preview');
  gate('PATCH', '/judge-runs/r-dev/disagreements/turn-9');
  gate('PATCH', '/judges/j-2/decision');
  gate('PATCH', '/judges/j-2/threshold');
  const runs9 = [Object.assign({}, IN_USE.runs[0], { id: 'r-9t' }), Object.assign({}, RUN_ROW_DEV, { id: 'r-9', judge_id: 'j-9' })];
  h('GET', '/judges/j-9', [200, asJudge(IN_USE, 'j-9', runs9)]);
  h('GET', '/judge-runs/r-9', [200, { ok: true, run: Object.assign({}, RUN_DEV, { id: 'r-9', judge_id: 'j-9' }) }]);
  ownJudge({ tlJudge: IN_USE });
  A.setDecisionDraft({ decision: 'use' });
  A.setThresholdDraft('0.3');
  const pending = [A.preview('td-1'), A.triage('turn-9', 'judge_wrong'), A.decide(), A.saveThreshold()];
  await flush();
  assert.equal(gates.length, 4, 'all four are out');
  await A.openJudge('j-9');
  await flush();
  assert.equal(Jx().tlJudgeRunId, 'r-9');
  const shown = () => {
    const v = pane();
    return [v.tljJudge.preview[0], v.tljRun.disagreements[0], v.tljJudge.decision, v.tljJudge.threshold];
  };
  assert.deepEqual(shown().map((x) => x.busy), [false, false, false, false], 'on their way, they hold nothing back on j-9');
  gates.forEach((fail) => fail());
  assert.deepEqual((await Promise.all(pending)).map((r) => r.ok), [false, false, false, false]);
  assert.deepEqual(shown().map((x) => x.error), ['', '', '', ''], 'j-9 shows none of their errors');
  assert.deepEqual(shown().map((x) => x.retry || x.reload || null), [null, null, null, null], 'no Retry or Reload that would act on j-2');
  assert.deepEqual(Object.keys(writes()).sort(), ['judges:decision:j-2', 'judges:preview:j-2:td-1', 'judges:threshold:j-2', 'judges:triage:r-dev:turn-9'],
    'each is kept under its own judge');
});

test('opening another judge starts its page afresh and stops the watch; the same judge keeps what was typed', async () => {
  h('GET', '/judges/j-2', [200, DETAIL]);
  h('GET', '/judges/j-9', [200, Object.assign({}, DETAIL, { judge: Object.assign({}, DRAFT, { id: 'j-9' }), runs: [] })]);
  h('GET', '/judge-runs/r-dev', [200, { ok: true, run: RUN_DEV }]);
  ownJudge({ tlJudgeThresholdDraft: '0.2', tlJudgePreview: { 'td-1': { verdict: 'Pass' } } });
  await A.openJudge('j-2');
  assert.equal(Jx().tlJudgeThresholdDraft, '0.2');
  assert.equal(Jx().tlJudgePreview['td-1'].verdict, 'Pass');
  await A.openJudge('j-9');
  assert.equal(Jx().tlJudgeId, 'j-9');
  assert.equal(Jx().tlJudgeThresholdDraft, '');
  assert.deepEqual(Jx().tlJudgePreview, {});
  assert.equal(Jx().tlJudgeRun, null, 'a judge with no runs has no run open');
});

test('every key the actions write is one the slice starts with, and the page and card resets cover their own', () => {
  const keys = Object.keys(C.judgesInitial(0));
  const src = new URL('../judgesActions.js', import.meta.url);
  return import('node:fs').then(({ readFileSync }) => {
    const code = readFileSync(src, 'utf8');
    const written = new Set([...code.matchAll(/\b(tlJudge[A-Za-z]*)\s*:/g)].map((m) => m[1]));
    for (const k of written) assert.ok(keys.includes(k), k + ' is written but not in the slice');
    for (const k of [...Object.keys(C.judgePage()), ...Object.keys(C.modeCard())]) assert.ok(keys.includes(k), k);
  });
});

test('a superadmin viewing a company is not the platform team: owner-only reads and writes send nothing, even if the list was read before', async () => {
  setActingOrg('org-viewed');
  jfresh({ tlJudgeId: 'j-2', tlJudgeModeId: 'm-1', tlJudgeFreeze: { reason: 'Stable on dev twice.' }, tlJudge: DETAIL }, { owner: true });
  assert.equal(C.isOwner(S()), false);
  await A.openJudge('j-2');
  await A.openMode('m-1');
  assert.deepEqual(await A.createSplit(), { ok: false });
  assert.deepEqual(await A.freeze(), { ok: false });
  assert.equal(net.requests.length, 0);
  setActingOrg(null);
});
