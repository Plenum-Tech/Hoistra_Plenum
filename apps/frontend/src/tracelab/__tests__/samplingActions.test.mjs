// samplingActions — the Map and Progress tabs against a mocked service: the period changes what is read, a batch
// is previewed and then drawn from the same request (window and seed included), a second click while one is
// being drawn does nothing, a failed draw keeps the form and says why, a withdrawal needs a reason, the manifest
// downloads as a file, a map node opens its conversation, an older reply never lands over a newer one, and a
// reply after a company switch is dropped.
import { test, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} } };
globalThis.document = { visibilityState: 'visible' };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const o = opts || {};
  requests.push({ method: o.method || 'GET', url: u, body: typeof o.body === 'string' ? JSON.parse(o.body) : undefined });
  const h = handlers[(o.method || 'GET') + ' ' + u.pathname] || handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u, o) : (h || [404, { detail: { reason: 'not_mocked', error: 'not mocked' } }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const A = await import('../samplingActions.js');
const { samplingInitial, samplingOf, samplingView, mapView, progressView } = await import('../logic/sampling.js');
const { tlStore, initialState, resetForCompany } = await import('../store.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
const S = () => tlStore.get();
const R = () => samplingOf(tlStore.get());
const fresh = (sampling, over) => {
  const seq = S().seq + 1;
  tlStore.set(Object.assign(initialState(), { seq, tab: 'map' }, over || {}, { sampling: Object.assign(samplingInitial(seq), sampling || {}) }));
};
const got = (path, method) => requests.filter((r) => r.method === (method || 'GET') && r.url.pathname === P + path);
const HOUR = 3600000;
const DAY = 24 * HOUR;
const span = (u) => Date.parse(u.searchParams.get('until')) - Date.parse(u.searchParams.get('since'));

const PLAN = { ok: true, next_step: 'cluster_representatives', distinct_sampled: 24, target_total: 100, pool_size: 400, steps: [] };
const FORM = { plan_step: 'initial_uniform', strategy: 'uniform', k: 15, seed: 7, dimension: '', reviews_required: 1, random_share: '' };
const mockReads = () => {
  handlers[P + '/sampling/plan'] = [200, PLAN];
  handlers[P + '/sampling/batches'] = [200, { ok: true, batches: [] }];
  handlers[P + '/map'] = [200, { ok: true, too_few: true, nodes: [], clusters: [] }];
  handlers[P + '/progress'] = [200, { ok: true, open_batches: 0 }];
};

beforeEach(() => { handlers = {}; requests = []; setActingOrg(null); fresh(); });
afterEach(() => { mock.timers.reset(); });

test('the plan, the batches, the map and the progress each read their route for the chosen period', async () => {
  mockReads();
  await A.loadMapTab();
  assert.deepEqual([got('/sampling/plan').length, got('/sampling/batches').length, got('/map').length, got('/progress').length], [1, 1, 1, 0]);
  const w = got('/map')[0].url;
  assert.equal(span(w), 30 * DAY + HOUR, 'thirty days back to an hour ahead');
  assert.equal(got('/sampling/batches')[0].url.searchParams.get('include_withdrawn'), 'true');
  assert.deepEqual([R().plan.next_step, R().batches, R().map.too_few], ['cluster_representatives', [], true]);
  assert.deepEqual([R().planError, R().batchesError, R().mapError, R().mapLoading], ['', '', '', false]);
  assert.ok(R().planLoadedAt && R().batchesLoadedAt && R().mapLoadedAt);
  requests = [];
  await A.loadProgressTab();
  assert.deepEqual([got('/sampling/plan').length, got('/progress').length, got('/map').length], [1, 1, 0]);
  const p = got('/progress')[0].url.searchParams;
  assert.equal(p.get('block'), '10');
  assert.equal(span(got('/progress')[0].url), 30 * DAY + HOUR);
  assert.deepEqual(progressView(S()).tiles.map((t) => t.value), ['—', '—', '—', '0']);
});

test('choosing another period reads again, forgets the preview and the chosen map group, and reads Progress only when it is open', async () => {
  mockReads();
  fresh({ preview: { pool_size: 1, picks: [], request: {} }, mapFocus: 2 });
  A.setRange('7d');
  await settle();
  assert.deepEqual([R().range, R().preview, R().mapFocus], ['7d', null, null]);
  assert.equal(span(got('/map')[0].url), 7 * DAY + HOUR);
  assert.equal(span(got('/sampling/plan')[0].url), 7 * DAY + HOUR);
  assert.equal(got('/progress').length, 0);
  requests = [];
  fresh({}, { tab: 'progress' });
  A.setRange('60d');
  await settle();
  assert.equal(span(got('/progress')[0].url), 60 * DAY + HOUR);
});

test('a read that fails keeps what is on screen and says why; the app\'s stale-company error is kept quiet', async () => {
  fresh({ plan: PLAN, map: { too_few: false, nodes: [], clusters: [] }, mapLoadedAt: 1, batches: [{ id: 'b1' }], progress: { open_batches: 1 }, progressLoadedAt: 1 });
  const down = [502, { detail: { error: 'Unreachable' } }];
  handlers[P + '/sampling/plan'] = down;
  handlers[P + '/sampling/batches'] = down;
  handlers[P + '/map'] = down;
  handlers[P + '/progress'] = down;
  await Promise.all([A.loadPlan(), A.loadBatches(), A.loadMap(), A.loadProgress()]);
  assert.deepEqual([R().planError, R().batchesError, R().mapError, R().progressError], ['Unreachable', 'Unreachable', 'Unreachable', 'Unreachable']);
  assert.deepEqual([R().plan.next_step, R().batches.length, R().map.too_few, R().progress.open_batches, R().mapLoading, R().progressLoading], ['cluster_representatives', 1, false, 1, false, false]);
  assert.equal(mapView(S()).error, 'Unreachable');
  fresh();
  handlers[P + '/map'] = () => { setActingOrg('11111111-1111-1111-1111-111111111111'); return [200, { ok: true, too_few: true }]; };
  handlers[P + '/progress'] = () => { setActingOrg(null); return [200, { ok: true }]; };
  await A.loadMap();
  await A.loadProgress();
  assert.deepEqual([R().mapError, R().mapLoading, R().progressError, R().progressLoading, R().map], ['', false, '', false, null]);
});

test('an older reply never lands over a newer one', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  let n = 0;
  handlers[P + '/map'] = async () => {
    n += 1;
    if (n === 1) { await gate; return [200, { ok: true, too_few: false, nodes: [], clusters: [], truncated: true }]; }
    return [200, { ok: true, too_few: true, nodes: [], clusters: [] }];
  };
  const slow = A.loadMap();
  await tick();
  await A.loadMap();
  release();
  await slow;
  assert.equal(R().map.too_few, true, 'the newer read stays');
});

test('a reply after a company switch is dropped and writes nothing', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  handlers[P + '/map'] = async () => { await gate; return [200, { ok: true, too_few: true }]; };
  handlers[P + '/sampling/preview'] = async () => { await gate; return [200, { ok: true, pool_size: 3, picks: [] }]; };
  const map = A.loadMap();
  const preview = A.previewBatch();
  await tick();
  resetForCompany();
  release();
  await map;
  await preview;
  assert.deepEqual([R().map, R().preview, R().busy, R().mapLoading], [null, null, false, false]);
  assert.equal(mapView(S()).tooFew, false);
});

test('Next batch fills the plan\'s next step; changing the form forgets the preview and the last error', () => {
  fresh({ plan: PLAN, preview: { pool_size: 1, picks: [], request: {} }, error: 'old' });
  A.nextBatch();
  assert.deepEqual([R().form.plan_step, R().form.strategy, R().form.random_share, R().preview, R().error], ['cluster_representatives', 'diversity', 0, null, '']);
  fresh();
  A.nextBatch();
  assert.equal(R().form.plan_step, 'extra', 'with no plan read, another fair batch');
  A.setBatchForm({ k: 40, seed: 11 });
  assert.deepEqual([R().form.k, R().form.seed, R().form.strategy], [40, 11, 'uniform']);
});

test('preview then create sends the same request, with the window and seed, and reads the lot again', async () => {
  mockReads();
  handlers[P + '/sampling/preview'] = [200, { ok: true, pool_size: 80, excluded_sampled: 15, truncated: false, picks: [{ turn_id: 'turn-1', reason: 'uniform pick' }] }];
  handlers['POST ' + P + '/sampling/batches'] = [201, { ok: true, batch: { id: 'b9', selected: 15, seed: 7 }, items: [] }];
  fresh({ form: Object.assign({}, FORM) });
  mock.timers.enable({ apis: ['Date'], now: Date.parse('2026-10-16T12:00:00Z') });
  await A.previewBatch();
  mock.timers.tick(5000);                       // the reader takes their time before clicking Create
  const pv = samplingView(S()).preview;
  assert.deepEqual([pv.poolSize, pv.excluded, pv.picks[0].reason, R().busy], [80, 15, 'uniform pick', false]);
  const q = got('/sampling/preview')[0].url.searchParams;
  assert.deepEqual([q.get('strategy'), q.get('k'), q.get('seed'), q.get('plan_step'), q.get('reviews_required')], ['uniform', '15', '7', 'initial_uniform', '1']);
  assert.equal(q.has('dimension'), false);
  assert.equal(q.has('random_share'), false);
  assert.equal(await A.createBatch(), true);
  const sent = got('/sampling/batches', 'POST')[0].body;
  const asked = Object.fromEntries(q.entries());
  assert.deepEqual(sent, { plan_step: asked.plan_step, strategy: asked.strategy, k: 15, seed: 7, since: asked.since, until: asked.until, reviews_required: 1 },
    'the draw is the previewed request: same period, same seed');
  assert.ok(sent.since < sent.until);
  assert.equal(S().sampling.notice, 'Drew 15 answers (seed 7). They are in the review queue.');
  assert.equal(R().preview, null);
  await settle();
  assert.deepEqual([got('/sampling/plan').length, got('/sampling/batches').length, got('/map').length], [1, 1, 1], 'plan, batches and map read again');
});

test('creating with no preview draws from the form as it stands; a dimension batch names its dimension', async () => {
  mockReads();
  handlers['POST ' + P + '/sampling/batches'] = [201, { ok: true, batch: { id: 'b9', selected: 1, seed: 3 }, items: [] }];
  fresh({ form: { plan_step: 'dimension', strategy: 'dimension', k: 30, seed: 3, dimension: 'page', reviews_required: 2, random_share: '' } });
  assert.equal(await A.createBatch(), true);
  const sent = got('/sampling/batches', 'POST')[0].body;
  assert.deepEqual([sent.strategy, sent.dimension, sent.k, sent.seed, sent.reviews_required], ['dimension', 'page', 30, 3, 2]);
  assert.equal(R().notice, 'Drew 1 answer (seed 3). It is in the review queue.');
});

test('a second click while a batch is being drawn does nothing, and Create waits for a preview that is still running', async () => {
  mockReads();
  let release;
  const gate = new Promise((r) => { release = r; });
  handlers['POST ' + P + '/sampling/batches'] = async () => { await gate; return [201, { ok: true, batch: { id: 'b9', selected: 15, seed: 7 } }]; };
  handlers[P + '/sampling/preview'] = async () => { await gate; return [200, { ok: true, pool_size: 5, picks: [] }]; };
  const first = A.createBatch();
  await tick();
  assert.equal(samplingView(S()).busy, true);
  const again = A.createBatch();
  const preview = A.previewBatch();
  await tick();
  assert.equal(got('/sampling/batches', 'POST').length, 1, 'the second click sent nothing');
  assert.equal(got('/sampling/preview').length, 0, 'and no preview goes over a draw in flight');
  release();
  assert.equal(await first, true);
  assert.equal(await again, false);
  assert.equal(await preview, null);
  assert.equal(got('/sampling/batches', 'POST').length, 1, 'one batch, however often it was clicked');
  assert.equal(got('/sampling/preview').length, 0);
  requests = [];
  fresh();
  let release2;
  const gate2 = new Promise((r) => { release2 = r; });
  handlers[P + '/sampling/preview'] = async () => { await gate2; return [200, { ok: true, pool_size: 5, picks: [] }]; };
  const pending = A.previewBatch();
  await tick();
  const early = A.createBatch();
  await tick();
  assert.equal(got('/sampling/batches', 'POST').length, 0, 'it would draw something other than what the preview is about to show');
  release2();
  await pending;
  assert.equal(await early, false);
  assert.equal(R().preview.pool_size, 5);
});

test('a failed draw keeps the form and shows the reason; a failed preview does too', async () => {
  handlers['POST ' + P + '/sampling/batches'] = [409, { detail: { reason: 'nothing_left', error: 'No answers are left to draw in that period.' } }];
  handlers[P + '/sampling/preview'] = [502, { detail: { error: 'Unreachable' } }];
  fresh({ form: { plan_step: 'extra', strategy: 'outlier', k: 5 }, notice: 'older notice' });
  assert.equal(await A.createBatch(), false);
  assert.deepEqual([R().error, R().form.strategy, R().busy, R().notice], ['No answers are left to draw in that period.', 'outlier', false, '']);
  await A.previewBatch();
  assert.deepEqual([R().error, R().busy, R().preview], ['Unreachable', false, null]);
  assert.equal(samplingView(S()).form.k, 5);
});

test('a withdrawal needs a reason; it sends it, reads the plan and batches again, and a refusal is shown', async () => {
  mockReads();
  handlers['POST ' + P + '/sampling/batches/b1/withdraw'] = [200, { ok: true, batch: { id: 'b1', withdrawn_at: '2026-10-16T10:00:00Z' } }];
  assert.equal(await A.withdrawBatch('b1', '   '), false);
  assert.equal(R().error, 'Say why the batch is withdrawn.');
  assert.equal(requests.length, 0);
  assert.equal(await A.withdrawBatch('b1', '  wrong period  '), true);
  assert.deepEqual(got('/sampling/batches/b1/withdraw', 'POST')[0].body, { reason: 'wrong period' });
  await settle();
  assert.deepEqual([got('/sampling/plan').length, got('/sampling/batches').length, got('/progress').length], [1, 1, 0]);
  requests = [];
  fresh({}, { tab: 'progress' });
  await A.withdrawBatch('b1', 'duplicate');
  await settle();
  assert.equal(got('/progress').length, 1, 'on Progress the open-batch list is read again too');
  handlers['POST ' + P + '/sampling/batches/b2/withdraw'] = [409, { detail: { reason: 'already', error: 'Already withdrawn.' } }];
  assert.equal(await A.withdrawBatch('b2', 'again'), false);
  assert.equal(R().error, 'Already withdrawn.');
});

test('the manifest opens, closes and downloads as a JSON file; with nothing read there is nothing to download', async () => {
  const manifest = { ok: true, batches: [{ id: 'b1', plan_step: 'initial_uniform', seed: 7, counts_for_prevalence: true, items: [{ position: 1, turn_id: 'turn-1', reason: 'uniform pick' }] }] };
  handlers[P + '/sampling/manifest'] = [200, manifest];
  const made = [];
  const clicks = [];
  const realCreate = URL.createObjectURL;
  const realRevoke = URL.revokeObjectURL;
  URL.createObjectURL = (blob) => { made.push(blob); return 'blob:manifest'; };
  URL.revokeObjectURL = () => {};
  globalThis.document = { visibilityState: 'visible', createElement: () => ({ click() { clicks.push(this); }, remove() {} }), body: { appendChild() {} } };
  try {
    A.downloadManifest();
    assert.equal(made.length, 0, 'nothing read, nothing to download');
    await A.loadManifest();
    assert.equal(samplingView(S()).manifest[0].items[0].turnId, 'turn-1');
    A.downloadManifest();
    assert.equal(made.length, 1);
    assert.deepEqual(JSON.parse(await made[0].text()), manifest);
    assert.deepEqual([clicks[0].download, clicks[0].href], ['review-batches-manifest.json', 'blob:manifest']);
    A.closeManifest();
    assert.equal(samplingView(S()).manifest, null);
    handlers[P + '/sampling/manifest'] = [502, { detail: { error: 'Unreachable' } }];
    await A.loadManifest();
    assert.equal(samplingView(S()).manifestError, 'Unreachable');
    assert.equal(R().manifestOpen, false, 'a failed read does not open an empty list');
  } finally {
    URL.createObjectURL = realCreate;
    URL.revokeObjectURL = realRevoke;
    globalThis.document = { visibilityState: 'visible' };
  }
});

test('choosing a map group brings it forward, and choosing it again lets go; a node opens its conversation on Conversations', async () => {
  handlers[P + '/conversations'] = [200, { ok: true, total: 0, conversations: [] }];
  handlers[P + '/conversations/s%3Ab'] = [200, { ok: true, conversation: { key: 's:b', active: { id: 't2' }, turns: [] } }];
  A.focusCluster(2);
  assert.equal(R().mapFocus, 2);
  A.focusCluster(2);
  assert.equal(R().mapFocus, null);
  A.focusCluster(3);
  A.focusCluster(4);
  assert.equal(R().mapFocus, 4);
  fresh({ map: { too_few: false, nodes: [{ turn_id: 't2', conversation_key: 's:b', x: 0, y: 0, cluster: 2 }], clusters: [] }, mapLoadedAt: 1 });
  const node = mapView(S()).nodes[0];
  A.openMapNode(node);
  assert.deepEqual([S().tab, S().convKey, S().turnId], ['conversations', 's:b', 't2']);
  await settle();
  assert.ok(requests.some((r) => r.url.pathname.endsWith('/conversations/s%3Ab') && r.url.searchParams.get('turn') === 't2'), 'the conversation is read with its answer');
  A.openMapNode({ conversation_key: 's:c', turn_id: 't9' });
  assert.deepEqual([S().convKey, S().turnId], ['s:c', 't9'], 'a raw map row opens the same way');
});

test('Progress reads with the chosen block size; only the offered sizes are taken', async () => {
  handlers[P + '/progress'] = [200, { ok: true, open_batches: 1, block: 20, discovery: { block: 20, points: [] } }];
  A.setProgressBlock(20);
  await settle();
  assert.equal(R().progressBlock, 20);
  assert.equal(got('/progress')[0].url.searchParams.get('block'), '20');
  assert.equal(progressView(S()).tiles[3].value, '1');
  A.setProgressBlock(7);
  A.setProgressBlock('20; drop');
  await settle();
  assert.equal(R().progressBlock, 20);
  assert.equal(got('/progress').length, 1, 'a size that is not offered reads nothing');
});
