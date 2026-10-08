// Notifications carry the platform's own actions too (Aasim, 8 Oct 2026): not only what the
// records need — approvals, anomalies, maintenance — but what the platform needs from you. A
// low Hoist Score takes you to ingest what is missing; a cron that failed is a cron to look at;
// nothing scheduled for you is a cron you could have; and what the platform is costing you.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { platformNotices } = await import('../src/logic/platformNotices.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c = null;
afterEach(() => {
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

const ME = 'hussain@plenum.example';
const ago = (min) => new Date(Date.now() - min * 60000).toISOString();
const SCORE = { value: 31, answered: true, band: 'Early', gap: 'Contracts lowest at 0% — Harbour Point, Ashgrove have none on record' };
const FAILED = { id: 'j2', name: 'Work-order SLA watch', enabled: true, created_by: { email: 'bala@plenum.example' },
  runs: [{ ok: false, finished_at: ago(30), error: 'work-order service did not answer' }] };
const OK = { id: 'j1', name: 'Compliance expiry scan', enabled: true, created_by: { email: 'bala@plenum.example' }, runs: [{ ok: true, finished_at: ago(60) }] };
const base = (o) => Object.assign({ score: SCORE, cronJobs: [OK, FAILED], me: ME, canManageCrons: true, canIngest: true, isAdmin: true,
  usage: { credits_this_month: 1240, queries: 980, ingests: 52 } }, o);

test('a low Hoist Score is a notice that takes you to ingest what is missing', () => {
  const n = platformNotices(base()).find((i) => i.key === 'platform:score');
  assert.equal(n.module, 'Platform');
  assert.equal(n.tone, 'risk', 'under 40');
  assert.match(n.title, /Hoist Score is 31\/100/);
  assert.match(n.meta, /Contracts lowest at 0%/);
  assert.deepEqual(n.action, { type: 'ingest' });
  assert.equal(n.info, false, 'an action, so it counts');
  assert.equal(platformNotices(base({ score: Object.assign({}, SCORE, { value: 55 }) })).find((i) => i.key === 'platform:score').tone, 'warn');
  assert.equal(platformNotices(base({ score: Object.assign({}, SCORE, { value: 82 }) })).some((i) => i.key === 'platform:score'), false);
  assert.deepEqual(platformNotices(base({ canIngest: false })).find((i) => i.key === 'platform:score').action, { type: 'buildings' },
    'someone who cannot ingest is sent to the buildings, not to an upload they cannot make');
});

test('no building hoisted yet is a notice too', () => {
  const n = platformNotices(base({ score: { value: null, answered: true, band: 'Nothing hoisted' } })).find((i) => i.key === 'platform:score');
  assert.match(n.title, /No buildings hoisted yet/);
  assert.deepEqual(n.action, { type: 'buildings' });
});

test('a cron whose last run failed is a notice, whoever scheduled it', () => {
  const n = platformNotices(base()).find((i) => i.key === 'platform:cron:j2');
  assert.equal(n.tone, 'risk');
  assert.match(n.title, /Work-order SLA watch failed/);
  assert.match(n.meta, /work-order service did not answer/);
  assert.deepEqual(n.action, { type: 'cron', id: 'j2' });
  assert.equal(platformNotices(base()).some((i) => i.key === 'platform:cron:j1'), false, 'a healthy one is not');
  const paused = Object.assign({}, FAILED, { enabled: false });
  assert.equal(platformNotices(base({ cronJobs: [paused] })).some((i) => i.key === 'platform:cron:j2'), false, 'nor a paused one');
});

test('nothing scheduled for you is a suggestion, not a pending item', () => {
  const n = platformNotices(base()).find((i) => i.key === 'platform:schedule');
  assert.match(n.title, /Nothing is scheduled for you/);
  assert.deepEqual(n.action, { type: 'schedule' });
  assert.equal(n.info, true);
  const mine = Object.assign({}, OK, { created_by: { email: ME } });
  assert.equal(platformNotices(base({ cronJobs: [mine] })).some((i) => i.key === 'platform:schedule'), false);
  assert.equal(platformNotices(base({ cronJobs: null })).some((i) => i.key === 'platform:schedule'), false, 'not before the jobs are read');
  assert.equal(platformNotices(base({ canManageCrons: false })).some((i) => i.key === 'platform:schedule'), false, 'not for someone who cannot schedule');
});

test("this month's usage is said in credits, for an admin, and is not counted as pending", () => {
  const n = platformNotices(base()).find((i) => i.key === 'platform:usage');
  assert.match(n.title, /1,240 credits/);
  assert.match(n.meta, /a question is 1 credit, an ingest 5/i);
  assert.deepEqual(n.action, { type: 'usage' });
  assert.equal(n.info, true);
  assert.equal(platformNotices(base({ isAdmin: false })).some((i) => i.key === 'platform:usage'), false);
  assert.equal(platformNotices(base({ usage: { credits_this_month: 0 } })).some((i) => i.key === 'platform:usage'), false);
});

test('the drawer has a Platform filter, counts only actions, and each notice opens its place', () => {
  c = new HoistraLogic({});
  const job = Object.assign({}, FAILED);
  c.setState({ signedIn: true, bldLive: [], account: { email: ME, role: 'admin', can_ingest: true }, role: 'admin', cronJobs: [OK, job], cronLoadedAt: 1, cronCanManage: true,
    usageRaw: { totals: { credits_this_month: 1240, queries: 980, ingests: 52 } },
    homeRaw: { approvals: { items: [] }, anomalies: { anomalies: [] }, coverage: { buildings: 4, domains: [
      { key: 'contracts', covered: 0, of: 4 }, { key: 'assets', covered: 1, of: 4 }, { key: 'energy', covered: 1, of: 4 }, { key: 'compliance', covered: 3, of: 4 }] } } });
  const v = c.renderVals();
  const platform = v.queueFilterOpts.find((o) => o.label === 'Platform');
  assert.ok(platform, 'a Platform filter');
  assert.equal(platform.n, '4', 'score, failed cron, schedule suggestion, usage');
  assert.equal(c.queueModel().count, 2, 'the badge counts what needs acting on: the score and the failed cron');
  platform.pick();
  const items = c.renderVals().queueItems;
  const open = (re) => items.find((i) => re.test(i.title)).click();
  open(/Hoist Score/);
  assert.equal(c.state.flow, 'ingest');
  assert.equal(c.state.queueOpen, false);
  open(/failed/);
  assert.equal(c.state.view, 'crons');
  assert.equal(c.state.cpJobId, 'j2');
  open(/credits/);
  assert.equal(c.state.view, 'users');
});

// ── the 8 Oct 2026 review ─────────────────────────────────────────────────────────────
test("a failed cron's notice does not show the platform's internal names", () => {
  const bad = Object.assign({}, FAILED, { runs: [{ ok: false, finished_at: ago(5), error: '/api/compliance/scan answered 500: svc-operations-intelligence plenum_cafm.compliance_certificates' }] });
  const n = platformNotices(base({ cronJobs: [bad] })).find((i) => i.key === 'platform:cron:j2');
  assert.doesNotMatch(n.meta, /svc-|plenum_cafm|\/api\//);
});

test('a figure or a suggestion appearing is not announced as a new decision', async () => {
  const { opsApi } = await import('../src/api/opsIntelligence.js');
  const real = { ap: opsApi.approvals, an: opsApi.anomalies };
  opsApi.approvals = async () => ({ ok: true, items: [] });
  opsApi.anomalies = async () => ({ ok: true, anomalies: [] });
  try {
    c = new HoistraLogic({});
    c.setState({ signedIn: true, bldLive: [], account: { email: ME, role: 'admin' }, cronJobs: [OK], cronLoadedAt: 1, cronCanManage: true,
      channels: ['In-platform'], homeRaw: { approvals: { items: [] }, anomalies: { anomalies: [] } } });
    await c.queueRefresh();
    const said = [];
    c.flash = (m) => said.push(m);
    c.setState({ usageRaw: { totals: { credits_this_month: 900 } } });
    await c.queueRefresh();
    assert.deepEqual(said, []);
  } finally { Object.assign(opsApi, { approvals: real.ap, anomalies: real.an }); }
});

test("a company switch drops the previous company's crons and usage", () => {
  c = new HoistraLogic({});
  c.setState({ signedIn: true, bldLive: [], account: { email: ME, role: 'admin' }, cronJobs: [FAILED], cronLoadedAt: 1, usageRaw: { totals: { credits_this_month: 5 } } });
  c._cronLoading = true;
  c.resetLiveData();
  assert.deepEqual(c.state.cronJobs, []);
  assert.equal(c.state.cronLoadedAt, null);
  assert.equal(c.state.usageRaw, null);
  assert.equal(c._cronLoading, false, 'the cron read is released, so the new company is read');
});
