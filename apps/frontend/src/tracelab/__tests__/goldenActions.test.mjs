// goldenActions — the golden reads and writes against a mocked service: the Golden set tab picks the active
// version and filters its cases, an upload is checked before it is saved, a run starts only after its estimate,
// the page that started a run steps it (step when idle, read while answering, wait out a sign-in renewal, stop on a
// refusal or when done), closing the tab or leaving Trace Lab pauses it with a keepalive request, two runs compare
// older against newer, the expected outcome is revealed on request, and replies after a company switch are dropped.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const listeners = {};
globalThis.window = {
  location: { origin: 'http://test.local', pathname: '/', search: '' },
  history: { replaceState: () => {} },
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  removeEventListener: (type, fn) => { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); }
};
globalThis.document = { visibilityState: 'visible' };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const o = opts || {};
  requests.push({ method: o.method || 'GET', url: u, keepalive: !!o.keepalive, body: typeof o.body === 'string' ? JSON.parse(o.body) : o.body });
  const h = handlers[(o.method || 'GET') + ' ' + u.pathname] || handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u, o) : (h || [404, { detail: { reason: 'not_mocked', error: 'not mocked' } }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const g = await import('../goldenActions.js');
const act = await import('../actions.js');
const { tlStore, initialState, resetForCompany } = await import('../store.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const R1 = '6f1c2d3e-0000-4000-8000-000000000001';
const R2 = '7f1c2d3e-0000-4000-8000-000000000002';
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
const G = () => tlStore.get().golden;
const fresh = (golden, over) => tlStore.set(Object.assign(initialState(), { seq: tlStore.get().seq + 1 }, over || {},
  { golden: Object.assign(initialState().golden, golden || {}) }));

const SETS = { ok: true, active: { id: 's2', version: 2, status: 'active', case_count: 29 },
  versions: [{ id: 's2', version: 2, status: 'active' }, { id: 's1', version: 1, status: 'retired' }] };
const CASES = { ok: true, total: 60, cases: [{ id: 'c1', case_key: '21', question: 'How many?', expected_answer: '759 raised.' }] };
const RUN = (over) => Object.assign({ id: R1, label: 'run-a', status: 'running', mine: true, k: 1, current: null, items_done: 0, items_total: 2 }, over || {});

beforeEach(() => {
  handlers = {}; requests = [];
  setActingOrg(null);
  fresh();
});
afterEach(() => { g.stopLoop(); });

test('the Golden set tab opens on the active version and filters, pages and searches its cases', async () => {
  handlers[P + '/golden-sets'] = [200, SETS];
  handlers[P + '/golden-sets/s2/cases'] = [200, CASES];
  handlers[P + '/golden-sets/s1/cases'] = [200, { ok: true, total: 0, cases: [] }];
  await g.loadSets();
  assert.equal(G().setId, 's2');
  assert.equal(G().cases.data.total, 60);
  let q = requests.at(-1).url.searchParams;
  assert.deepEqual([q.get('limit'), q.get('offset'), q.has('q')], ['50', '0', false]);
  g.setCasesDraft('  reactive  ');
  await g.searchCases();
  assert.equal(requests.at(-1).url.searchParams.get('q'), 'reactive');
  await g.setCasesFlag('stale');
  assert.equal(requests.at(-1).url.searchParams.get('flag'), 'stale');
  g.pageCases(1);
  await settle();
  assert.equal(requests.at(-1).url.searchParams.get('offset'), '50');
  g.pageCases(1);
  assert.equal(G().cases.page, 1, 'no page past the last');
  g.toggleExpected('c1');
  assert.deepEqual(G().shown, { c1: true });
  g.pickSet('s1');
  await settle();
  assert.deepEqual([G().setId, G().shown, G().cases.page], ['s1', {}, 0]);
  await g.loadSets();
  assert.equal(G().setId, 's1', 'a reload keeps the version the reader picked');
});

test('an upload is checked first and saved only on request; row errors are kept for the screen', async () => {
  const file = new File(['x'], 'tower.xlsx');
  handlers['POST ' + P + '/golden-sets'] = (u) => (u.searchParams.get('dry_run') === 'true'
    ? [200, { ok: true, saved: false, cases: 29, header_row: 1, flags: {}, preview: [] }]
    : [201, { ok: true, saved: true, set: { id: 's3', version: 3, case_count: 29 }, diff: { previous_version: 2, added: [], changed: [], removed: [] } }]);
  handlers['GET ' + P + '/golden-sets'] = [200, { ok: true, active: { id: 's3', version: 3 }, versions: [{ id: 's3', version: 3, status: 'active' }] }];
  handlers[P + '/golden-sets/s3/cases'] = [200, CASES];
  await g.upload(file, { sheet: 'Golden set', dryRun: true });
  assert.deepEqual([G().upload.busy, G().upload.dryRun, G().upload.report.cases], [false, true, 29]);
  assert.equal(requests.filter((r) => r.method === 'GET').length, 0, 'a check reads nothing else');
  await g.upload(file, { note: 'v3' });
  assert.equal(G().upload.report.set.version, 3);
  assert.equal(G().setId, 's3');
  handlers['POST ' + P + '/golden-sets'] = [422, { detail: { reason: 'bad_rows', error: 'Some rows need fixing before this file can be saved.',
    errors: [{ row: 4, column: 'Question', message: 'The question is empty.' }], warnings: [] } }];
  await g.upload(file, { dryRun: true });
  assert.equal(G().upload.error, 'Some rows need fixing before this file can be saved.');
  assert.deepEqual(G().upload.errors, [{ row: 4, column: 'Question', message: 'The question is empty.' }]);
});

test('activate and retire reload the sets; a refusal is kept as the action error', async () => {
  handlers['POST ' + P + '/golden-sets/s1/activate'] = [200, { ok: true, set: { id: 's1', status: 'active' } }];
  handlers['GET ' + P + '/golden-sets'] = [200, SETS];
  handlers[P + '/golden-sets/s2/cases'] = [200, CASES];
  await g.activateSet('s1');
  assert.ok(requests.some((r) => r.method === 'GET' && r.url.pathname === P + '/golden-sets'));
  assert.equal(G().setBusy, '');
  handlers['POST ' + P + '/golden-sets/s2/retire'] = [409, { detail: { reason: 'run_in_progress', error: 'A run is using this version.' } }];
  await g.retireSet('s2');
  assert.equal(G().setError, 'A run is using this version.');
});

test('a run starts only after its estimate; the started run is this page\'s and its loop starts', async () => {
  const started = [];
  handlers[P + '/golden-runs/estimate'] = (u, o) => [200, { ok: true, cases: 29, k: JSON.parse(o.body).k, est_total_usd: 3.25, usd_cap_default: 4.88,
    preflight: { ok: true, problems: [] }, building_selected: false }];
  handlers['POST ' + P + '/golden-runs'] = (u, o) => { started.push(JSON.parse(o.body)); return [201, { ok: true, run: { id: R1, status: 'running' } }]; };
  handlers['GET ' + P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  handlers['GET ' + P + '/golden-runs/' + R1] = [200, { ok: true, run: RUN({ current: { case_key: '1', rep: 1 } }), cases: [] }];
  fresh({ sets: { data: SETS, loading: false, error: '', reason: '', loadedAt: 1 } }, { tab: 'runs' });
  assert.equal(await g.startRun(), null, 'no dialog, nothing sent');
  const pending = g.openStart();
  assert.equal(G().dialog.setId, 's2');
  await pending;
  assert.equal(G().dialog.usdCap, '4.88');
  await g.setDialog({ k: 3 });
  assert.equal(G().dialog.estimate.k, 3);
  g.setDialog({ usdCap: '6.5x', note: 'after the fix' });
  await g.startRun();
  assert.deepEqual(started, [{ set_id: 's2', k: 3, purpose: 'adhoc', note: 'after the fix', usd_cap: 6.5 }]);
  assert.equal(G().dialog, null);
  assert.deepEqual(G().own, { [R1]: true });
  assert.equal(G().detail.runId, R1);
  assert.equal(G().loop.runId, R1, 'the page that started the run steps it');
  assert.equal(g.loopActive(), true);
});

test('a refused start keeps the dialog open with the reason and the problems', async () => {
  handlers['POST ' + P + '/golden-runs'] = [503, { detail: { reason: 'not_isolated', error: 'Trace Lab is not isolated.', problems: ['The activity log is on.'] } }];
  fresh({ dialog: { k: 1, purpose: 'adhoc', setId: 's2', note: '', usdCap: '', estimate: { cases: 2 }, estimating: false, starting: false, error: '', problems: [] } });
  await g.startRun();
  assert.deepEqual([G().dialog.error, G().dialog.reason, G().dialog.problems], ['Trace Lab is not isolated.', 'not_isolated', ['The activity log is on.']]);
});

test('the loop steps when the run is idle, reads it while a case is answered, waits out a sign-in renewal, and stops when done', async () => {
  let run = RUN();
  const steps = [];
  handlers['GET ' + P + '/golden-runs/' + R1] = () => [200, { ok: true, run, cases: [] }];
  handlers['GET ' + P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  handlers['POST ' + P + '/golden-runs/' + R1 + '/step'] = () => { steps.push(1); run = RUN({ current: { case_key: '1', rep: 1 } }); return [202, { ok: true, started: true, item: { case_key: '1', rep: 1, attempt: 1 } }]; };
  fresh({ detail: { runId: R1, data: { run }, loading: false, error: '', loadedAt: 1 }, own: { [R1]: true } });
  g.startLoop(R1);
  await settle();
  assert.equal(steps.length, 1);
  await g.tick();
  assert.equal(steps.length, 1, 'a case in flight is read, not stepped again');
  assert.equal(requests.at(-1).method, 'GET');
  run = RUN();
  await g.tick();
  await g.tick();
  assert.equal(steps.length, 2);
  handlers['POST ' + P + '/golden-runs/' + R1 + '/step'] = [409, { detail: { reason: 'token_expiring', error: 'Your sign-in is about to expire.' } }];
  run = RUN();
  await g.pollRun();
  await g.tick();
  assert.equal(g.loopActive(), true, 'a sign-in renewal is waited out');
  assert.equal(G().loop.note, 'Waiting for your sign-in to renew…');
  handlers['POST ' + P + '/golden-runs/' + R1 + '/step'] = () => { run = RUN({ status: 'done' }); return [200, { ok: true, done: true, run }]; };
  await g.tick();
  assert.equal(g.loopActive(), false);
  assert.equal(G().loop.runId, null);
});

test('a refusal stops the loop with its message, and a capped run can be resumed with a new cap', async () => {
  handlers['GET ' + P + '/golden-runs/' + R1] = [200, { ok: true, run: RUN({ status: 'capped' }) }];
  handlers['GET ' + P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  handlers['POST ' + P + '/golden-runs/' + R1 + '/step'] = [409, { detail: { reason: 'run_cap', error: 'This run has reached its spending cap.' } }];
  fresh({ detail: { runId: R1, data: { run: RUN() }, loading: false, error: '', loadedAt: 1 }, own: { [R1]: true } });
  g.startLoop(R1);
  await settle();
  assert.equal(g.loopActive(), false);
  assert.deepEqual(G().loop.error, { reason: 'run_cap', message: 'This run has reached its spending cap.', resumable: true });
  handlers['POST ' + P + '/golden-runs/' + R1 + '/resume'] = (u, o) => { assert.deepEqual(JSON.parse(o.body), { usd_cap: 9 }); return [200, { ok: true, run: RUN() }]; };
  handlers['GET ' + P + '/golden-runs/' + R1] = [200, { ok: true, run: RUN({ current: { case_key: '1', rep: 1 } }) }];
  g.setResumeCap('9');
  await g.resumeRun();
  assert.equal(G().loop.error, null);
  assert.equal(G().loop.runId, R1, 'Resume starts the loop again');
});

test('closing the tab pauses the stepped run with one keepalive request, under the company it was started in', async () => {
  setActingOrg('org-a');
  handlers['GET ' + P + '/golden-runs/' + R1] = [200, { ok: true, run: RUN({ current: { case_key: '1', rep: 1 } }) }];
  handlers['POST ' + P + '/golden-runs/' + R1 + '/pause'] = [200, { ok: true }];
  fresh({ detail: { runId: R1, data: { run: RUN({ current: { case_key: '1', rep: 1 } }) }, loading: false, error: '', loadedAt: 1 }, own: { [R1]: true } });
  g.startLoop(R1);
  assert.equal((listeners.pagehide || []).length, 1);
  listeners.pagehide[0]();
  const pause = requests.find((r) => r.url.pathname === P + '/golden-runs/' + R1 + '/pause');
  assert.deepEqual([pause.method, pause.keepalive, pause.url.searchParams.get('organization_id'), pause.body.reason], ['POST', true, 'org-a', 'page_closed']);
  requests = [];
  setActingOrg('org-b');
  fresh({ detail: { runId: R1, data: { run: RUN() }, loading: false, error: '', loadedAt: 1 }, own: { [R1]: true }, loop: { runId: R1, busy: true, error: null, note: '' } });
  g.leavePage();
  assert.equal(g.loopActive(), false);
  assert.equal((listeners.pagehide || []).length, 0, 'the listener goes with the loop');
  const left = requests.find((r) => r.url.pathname.endsWith('/pause'));
  assert.equal(left.keepalive, true);
  assert.equal(left.url.searchParams.get('organization_id'), 'org-a', 'the run\'s own company, not the one switched to');
});

test('pause and cancel stop the loop first; regrade sends what to grade', async () => {
  const sent = [];
  ['pause', 'cancel', 'regrade'].forEach((k) => { handlers['POST ' + P + '/golden-runs/' + R1 + '/' + k] = (u, o) => { sent.push([k, o.body ? JSON.parse(o.body) : null]); return [200, { ok: true }]; }; });
  handlers['GET ' + P + '/golden-runs/' + R1] = [200, { ok: true, run: RUN({ status: 'paused' }) }];
  handlers['GET ' + P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  fresh({ detail: { runId: R1, data: { run: RUN({ current: { case_key: '1' } }) }, loading: false, error: '', loadedAt: 1 }, own: { [R1]: true } });
  g.startLoop(R1);
  await g.pauseRun();
  assert.equal(g.loopActive(), false);
  await g.cancelRun();
  await g.regradeRun('all');
  assert.deepEqual(sent.map((x) => x[0]), ['pause', 'cancel', 'regrade']);
  assert.deepEqual(sent[2][1], { only: 'all' });
});

test('two runs compare the older against the newer', async () => {
  const asked = [];
  handlers[P + '/golden-runs/compare'] = (u) => { asked.push([u.searchParams.get('base'), u.searchParams.get('head')]); return [200, { ok: true, flips: {} }]; };
  fresh({ runs: { rows: [{ id: R1, started_at: '2026-10-15T10:00:00Z' }, { id: R2, started_at: '2026-10-14T10:00:00Z' }], loading: false, error: '', reason: '', loadedAt: 1 } });
  g.togglePick(R1);
  assert.equal(await g.compareRuns(), null, 'two runs are needed');
  g.togglePick(R2);
  await g.compareRuns();
  assert.deepEqual(asked, [[R2, R1]]);
  assert.deepEqual(G().compare.data, { ok: true, flips: {} });
  g.closeCompare();
  assert.deepEqual([G().compare, G().pick], [null, []]);
});

test('the expected outcome: read blind, revealed on request, hidden and shown again without a second reveal', async () => {
  let reveals = 0;
  handlers['GET ' + P + '/golden/turns/t9'] = [200, { ok: true, available: true, revealed: false, golden: { case_key: '21' } }];
  handlers['POST ' + P + '/golden/turns/t9/reveal'] = () => { reveals += 1; return [200, { ok: true, available: true, revealed: true, golden: { case_key: '21', case: { expected_answer: '759' } } }]; };
  await g.loadExpected('t9');
  assert.equal(G().expected.t9.data.revealed, false);
  await g.loadExpected('t9');
  assert.equal(requests.filter((r) => r.url.pathname.endsWith('/t9')).length, 1, 'read once');
  await g.revealExpected('t9');
  assert.equal(G().expected.t9.data.golden.case.expected_answer, '759');
  g.hideExpected('t9');
  assert.equal(G().expected.t9.hidden, true);
  await g.revealExpected('t9');
  assert.deepEqual([reveals, G().expected.t9.hidden], [1, false]);
});

test('a reply that lands after a company switch is dropped, and the stale-scope error stays quiet', async () => {
  let release;
  handlers[P + '/golden-runs'] = () => new Promise((r) => { release = () => r([200, { ok: true, runs: [{ id: R1 }] }]); });
  const pending = g.loadGoldenRuns();
  await tick();
  resetForCompany();
  release();
  await pending;
  assert.equal(G().runs.rows, null);
  const again = g.loadGoldenRuns();
  await tick();
  setActingOrg('org-2');
  release();
  await again;
  assert.deepEqual([G().runs.rows, G().runs.error], [null, '']);
});

test('the Runs tab also reads golden runs, the Golden set tab reads the sets, and a golden answer opens on Conversations', async () => {
  handlers[P + '/runs'] = [200, { ok: true, runs: [] }];
  handlers[P + '/golden-runs'] = [200, { ok: true, runs: [] }];
  handlers[P + '/golden-sets'] = [200, { ok: true, active: null, versions: [] }];
  handlers[P + '/conversations'] = [200, { ok: true, total: 0, conversations: [], watermark: 'w' }];
  handlers[P + '/conversations/facets'] = [200, { ok: true, channels: [] }];
  handlers[P + '/conversations/' + encodeURIComponent('s:golden-6f1c2d3e-21-r1')] = [200, { ok: true, conversation: { key: 's:golden-6f1c2d3e-21-r1', turns: [], active: { id: 't1' } } }];
  tlStore.set({ tab: 'runs' });
  act.loadLists();
  await settle();
  assert.ok(requests.some((r) => r.url.pathname === P + '/golden-runs'));
  requests = [];
  tlStore.set({ tab: 'golden' });
  act.loadLists();
  await settle();
  assert.deepEqual(requests.map((r) => r.url.pathname), [P + '/golden-sets']);
  act.openGoldenAnswer(R1, 'golden-6f1c2d3e-21-r1', 't1');
  await settle();
  const s = tlStore.get();
  assert.deepEqual([s.tab, s.run, s.convKey, s.turnId], ['conversations', R1, 's:golden-6f1c2d3e-21-r1', 't1']);
});
