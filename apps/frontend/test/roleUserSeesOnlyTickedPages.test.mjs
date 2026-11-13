// A user with an access role sees only the pages an admin ticked for them (12 Nov 2026).
//
// Hussain: "they should only see compliance and buildings page", "the users with assigned
// role should not have an instance picker". The server refuses the rest; these pin the
// client's half - what is not held is absent, not broken: no picker, only the ticked pages in
// the navigator, no request sent for another page, and Home when a view is not theirs. And
// nothing at all changes for a user with no role, or for an admin.
//
// The real controller in Node. `fetch` records every URL asked for and answers with a network
// error, so "no request is sent" is checked against what would really have gone out.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
const asked = [];
let answer = null;
globalThis.fetch = async (url, init) => {
  asked.push(String(url));
  if (answer) return answer(String(url), init);
  throw new TypeError('no network in tests: ' + url);
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { apiFetch, BASES, configureAuth, ApiError } = await import('../src/api/client.js');

const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', email_verified: true, role: 'user' };
const NORMAL = Object.assign({}, USER, { access_role: null, pages: null, pages_known: true });
const OFFICER = Object.assign({}, USER, { id: 'u-4', access_role: 'compliance_officer', access_role_label: 'Compliance Officer', pages: ['buildings', 'compliance'], pages_known: true });
const ENERGY = Object.assign({}, USER, { id: 'u-5', access_role: 'general_user', access_role_label: 'General User', pages: ['energy'], pages_known: true });
const ADMIN = Object.assign({}, USER, { id: 'u-2', role: 'admin', pages: null, pages_known: true });
const resp = (user) => ({ ok: true, user, tokens: { access_token: 'acc-1', refresh_token: 'ref-1' } });

// The loaders authEnter fires that are not page loaders, stubbed: the page loaders are left
// real where a test wants to see what they ask for.
const QUIET = ['authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun'];
const fresh = (alsoQuiet) => {
  const x = new HoistraLogic();
  QUIET.concat(alsoQuiet || []).forEach((k) => { x[k] = () => {}; });
  // The hooks the controller installs at mount (authBoot), without the rest of the boot.
  configureAuth(x.authHooks());
  return x;
};
const calm = ['loadLiveData', 'resetLiveData'];
const signIn = (x, user) => { x.authEnter(resp(user)); return x; };
const navKeys = (x) => (x.renderVals().navSections || []).map((n) => n.key);
const settle = () => new Promise((r) => setTimeout(r, 60));

beforeEach(() => { Object.keys(mem).forEach((k) => delete mem[k]); Object.keys(tabMem).forEach((k) => delete tabMem[k]); asked.length = 0; answer = null; });

// ── no instance picker ──────────────────────────────────────────────────────────────────
test('a role user is not offered the instance picker: sign-in lands on Home', () => {
  const x = signIn(fresh(calm), OFFICER);
  assert.equal(x.state.view, 'home');
  assert.equal(x.state.instance, null);
  const v = x.renderVals();
  assert.equal(v.isPicker, false);
  assert.equal((v.acctItems || []).filter(Boolean).some((i) => i.label === 'Switch instance'), false);
});

test('a normal user and an admin are offered the picker exactly as before', () => {
  for (const who of [NORMAL, USER, ADMIN]) {
    const x = signIn(fresh(calm), who);
    assert.equal(x.state.view, 'instances', who.role + ' ' + who.id);
    assert.equal(x.renderVals().isPicker, true);
  }
});

test('the picker is never where a role user is left: a stored view of it becomes Home', () => {
  const x = signIn(fresh(calm), OFFICER);
  x.setState({ view: 'instances' });
  assert.equal(x.state.view, 'home');
  assert.equal(x.renderVals().isPicker, false);
});

test('the top bar names the role, and pressing it switches nothing', () => {
  const x = signIn(fresh(calm), OFFICER);
  const v = x.renderVals();
  assert.equal(v.instShow, true);
  assert.equal(v.instName, 'Compliance Officer');
  assert.equal(v.instStatic, true);
  v.instSwitch();
  assert.equal(x.state.view, 'home');
});

// ── only the ticked pages ───────────────────────────────────────────────────────────────
test('the navigator lists the ticked pages only, and the Compliance Graph with Compliance', () => {
  assert.deepEqual(navKeys(signIn(fresh(calm), OFFICER)), ['buildings_user', 'hoist_graph', 'compliance']);
  assert.deepEqual(navKeys(signIn(fresh(calm), ENERGY)), ['energy']);
});

test('a view whose page is not held is Home', () => {
  const x = signIn(fresh(calm), OFFICER);
  for (const patch of [{ view: 'vp' }, { view: 'module', module: 'energy' }, { view: 'module', module: 'assets' }, { view: 'module', module: 'ops' }, { view: 'vh' }, { view: 'insp' }]) {
    x.setState(patch);
    assert.equal(x.state.view, 'home', JSON.stringify(patch));
  }
  for (const view of ['cc', 'hg', 'buildings', 'chat', 'sessions']) {
    x.setState({ view });
    assert.equal(x.state.view, view);
  }
  const y = signIn(fresh(calm), ENERGY);
  y.setState({ view: 'buildings' });
  assert.equal(y.state.view, 'home');
});

test('a normal user and an admin open every view as before', () => {
  for (const who of [NORMAL, ADMIN]) {
    const x = signIn(fresh(calm), who);
    x.setState({ instance: 'full', view: 'home' });
    for (const patch of [{ view: 'vp' }, { view: 'module', module: 'energy' }, { view: 'buildings' }, { view: 'cc' }]) {
      x.setState(patch);
      assert.equal(x.state.view, patch.view);
    }
  }
});

// ── no request for a page that is not held ──────────────────────────────────────────────
const NOT_MINE = /\/api\/energy\/(anomalies|meters|market-profiles|ratings|condition|assets\/)|\/api\/contract-performance\/|\/api\/maintenance\/|\/api\/work-orders|\/backend\/work-order\/api\/assets|\/api\/v1\/plenum\/vendors/;

test('a role user\'s browser asks for nothing owned by a page they do not hold', async () => {
  const x = signIn(fresh(), OFFICER);        // loadLiveData runs for real
  await settle();
  const offending = asked.filter((u) => NOT_MINE.test(u));
  assert.deepEqual(offending, []);
  assert.ok(asked.some((u) => u.indexOf('/api/compliance/certificates') > -1), 'the compliance register is read');
  assert.ok(asked.some((u) => u.indexOf('/api/energy/buildings') > -1), 'building names are read');
  assert.equal(x.state.view, 'home');
});

test('a normal user\'s browser asks for every register as before', async () => {
  signIn(fresh(), NORMAL);
  await settle();
  for (const part of ['/api/energy/anomalies', '/api/contract-performance/', '/api/maintenance/', '/api/compliance/certificates'])
    assert.ok(asked.some((u) => u.indexOf(part) > -1), part);
});

// ── the client answers the refusal itself, and learns from the server's ─────────────────
test('a request for an unheld page is refused without being sent', async () => {
  const x = signIn(fresh(calm), OFFICER);
  asked.length = 0;
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/energy/anomalies'), (e) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 403);
    assert.equal(e.reason, 'page_not_allowed');
    assert.equal(e.pageNotAllowed, true);
    return true;
  });
  assert.deepEqual(asked, []);
  assert.ok(x);
});

test('a refusal from the server re-reads the account, once, and raises no banner', async () => {
  const x = fresh(calm.concat([]));
  let reads = 0;
  x.authLoadScope = () => { reads += 1; return Promise.resolve(); };
  signIn(x, NORMAL);
  reads = 0;
  answer = async () => new Response(JSON.stringify({ detail: { ok: false, reason: 'page_not_allowed', page: 'energy', error: 'That is not part of the pages you have access to.' } }), { status: 403, headers: { 'Content-Type': 'application/json' } });
  const before = x.state.toast || x.state.flashMsg || null;
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/energy/anomalies'), (e) => e.reason === 'page_not_allowed' && e.pageNotAllowed === true);
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/energy/meters'), (e) => e.reason === 'page_not_allowed');
  await settle();
  assert.equal(reads, 1, 'the scope is re-read once for a burst of refusals');
  assert.equal(x.state.toast || x.state.flashMsg || null, before);
});

// ── the pages arrive with the account ───────────────────────────────────────────────────
test('GET /api/auth/me sets the role and the pages on the account', async () => {
  const x = new HoistraLogic();
  ['usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun', 'loadLiveData', 'resetLiveData'].forEach((k) => { x[k] = () => {}; });
  configureAuth(x.authHooks());
  answer = async (url) => {
    if (url.indexOf('/api/auth/me') > -1) return new Response(JSON.stringify({ ok: true, user: Object.assign({}, USER, { can_ingest: false, building_ids: [], buildings: [], access_role: 'compliance_officer', access_role_label: 'Compliance Officer', pages: ['buildings', 'compliance'] }) }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    throw new TypeError('no network in tests: ' + url);
  };
  x.authEnter(resp(USER));                    // a sign-in answer from before the pages rode on it
  assert.equal(x.state.view, 'instances');
  await settle();
  assert.equal(x.state.account.access_role, 'compliance_officer');
  assert.deepEqual(x.state.account.pages, ['buildings', 'compliance']);
  assert.equal(x.state.view, 'home', 'and the picker gives way to Home');
});

test('a role taken away makes the account a normal user again', async () => {
  const x = new HoistraLogic();
  ['usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun', 'loadLiveData', 'resetLiveData'].forEach((k) => { x[k] = () => {}; });
  configureAuth(x.authHooks());
  answer = async (url) => {
    if (url.indexOf('/api/auth/me') > -1) return new Response(JSON.stringify({ ok: true, user: Object.assign({}, USER, { id: OFFICER.id, can_ingest: false, building_ids: [], buildings: [], access_role: null, access_role_label: null, pages: null }) }), { status: 200, headers: { 'Content-Type': 'application/json' } });
    throw new TypeError('no network in tests: ' + url);
  };
  x.authEnter(resp(OFFICER));
  await settle();
  assert.equal(x.state.account.access_role, null);
  assert.equal(x.state.account.pages, null);
  assert.equal(x.renderVals().instStatic, false);
});
