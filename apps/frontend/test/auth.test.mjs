// auth — the real controller in Node, fetch mocked per METHOD + path so nothing reaches a
// host. This stack's backend points at production and every auth route except /config
// writes (accounts, sessions, last_login_at), so these tests must prove the requests and
// the state transitions without ever performing one.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {}; // sessionStorage: private to this "tab", separate from the shared mem/localStorage
let calls, handlers;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push({ method, path: u.pathname, headers: (opts && opts.headers) || {}, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { SESSION_KEY } = await import('../src/logic/session.js');
const { apiFetch, BASES, getActingOrg, setActingOrg } = await import('../src/api/client.js');

const A = '/backend/ops-intelligence/api/auth';
const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', status: 'active', email_verified: true, role: 'user', role_label: 'Facilities manager — the default for a new account', last_login_at: null };
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin' });
const SUPERADMIN = Object.assign({}, USER, { id: 'u-3', email: 'sadie@example.com', full_name: 'Sadie Superadmin', role: 'superadmin' });
const CONFIG = { ok: true, self_registration: true, password: { min_length: 12 }, otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }, secrets_configured: { jwt: true, otp_pepper: true } };
const tokens = (n) => ({ access_token: 'acc-' + n, refresh_token: 'ref-' + n, token_type: 'Bearer', expires_in: 1800 });
const session = (user, n, message) => [200, Object.assign({ ok: true, user, tokens: tokens(n) }, message ? { message } : {})];
const accepted = (status, message) => [202, { ok: true, status, message, email: 'sam@example.com', otp: CONFIG.otp }];
const fail = (status, reason, error, extra) => [status, { detail: Object.assign({ ok: false, error, reason }, extra || {}) }];
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 10));
const stored = () => JSON.parse(mem[SESSION_KEY] || '{}');
const requests = (path) => calls.filter((c) => c.path === path);

const OI = '/backend/ops-intelligence';
const WO = '/backend/work-order';
const UDR = '/backend/udr';
// Every route loadLiveData() touches (core.js's mount reads, re-fired by authEnter on a
// fresh sign-in), so a login in these tests never hits an unmocked route: an unmocked
// route either throws (armed retry timers cleanup() would have to clear one more of, on
// every test in the file) or, worse, races the specific request a test itself is
// asserting on. Bodies are shaped only as far as each loader actually destructures.
const liveMock = () => {
  Object.assign(handlers, {
    ['GET ' + OI + '/api/approvals']: [200, { ok: true, items: [] }],
    ['GET ' + OI + '/api/compliance/saved-space/summary']: [200, { ok: true }],
    ['GET ' + OI + '/api/contract-performance/contracts']: [200, { ok: true, contracts: [] }],
    ['GET ' + OI + '/api/energy/meters']: [200, { ok: true, meters: [] }],
    ['GET ' + OI + '/api/energy/anomalies']: [200, { ok: true, anomalies: [] }],
    ['GET ' + OI + '/api/compliance/certificates']: [200, { ok: true, certificates: [] }],
    ['GET ' + OI + '/api/compliance/coverage/buildings']: [200, { ok: true, buildings: [] }],
    ['GET ' + OI + '/api/compliance/coverage/vendors']: [200, { ok: true, vendors: [] }],
    ['GET ' + OI + '/api/compliance/country-pack']: [200, { ok: true, types: [] }],
    ['GET ' + OI + '/api/contract-performance/saved-space/summary']: [200, { ok: true }],
    ['GET ' + OI + '/api/contract-performance/admin/weights']: [200, { ok: true }],
    ['GET ' + OI + '/api/contract-performance/approvals']: [200, { ok: true, items: [] }],
    ['GET ' + OI + '/api/energy/buildings']: [200, { ok: true, buildings: [] }],
    ['GET ' + OI + '/api/energy/graph/shape']: [200, { ok: true }],
    ['GET ' + OI + '/api/energy/graph/tables']: [200, { ok: true, tables: [] }],
    ['GET ' + OI + '/api/energy/ratings/position']: [200, { ok: true, tiles: [] }],
    ['GET ' + WO + '/api/assets']: [200, []],
    ['GET ' + WO + '/api/work-orders/']: [200, []],
    ['GET ' + WO + '/api/dashboard/stats']: [200, {}],
    ['GET ' + UDR + '/api/spaces']: [200, { spaces: [] }]
  });
};

let c;
const fresh = () => { const x = new HoistraLogic(); x.authBoot(); return x; };
// authEnter kicks the admin reads (usLiveLoad/auLiveLoad) for an admin account, and — on
// every fresh sign-in — resetLiveData()/loadLiveData() for the account-scoped registers
// (compliance, home, vendors, buildings, energy, assets, maintenance, spaces); liveMock()
// keeps all of those clean, but usLiveLoad/auLiveLoad/saLiveLoad are only mocked by the
// tests that need an admin/superadmin account (saMock()), so their retry timers still
// need clearing like _tt.
const cleanup = (x) => {
  const k = x || c;
  k.authStop(); clearTimeout(k._tt);
  clearTimeout(k._usLiveRetry); clearTimeout(k._usLiveRefresh); clearTimeout(k._auLiveRetry);
  clearTimeout(k._saLiveRetry); clearTimeout(k._saLiveRefresh);
  clearTimeout(k._ccRetry); clearTimeout(k._homeRetry); clearTimeout(k._homeRefresh);
  clearTimeout(k._vpRetry); clearTimeout(k._vpRefresh); clearTimeout(k._bldRetry);
  clearTimeout(k._enRetry); clearTimeout(k._enPosRetry);
  clearTimeout(k._asLiveRetry); clearTimeout(k._mxLiveRetry); clearTimeout(k._spRetry);
};
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  calls = []; handlers = { ['GET ' + A + '/config']: [200, CONFIG] };
  liveMock();
  c = fresh();
});
// loadLiveData()'s loaders are fired-and-forgotten by authEnter, so a test that returns
// (and whose synchronous afterEach then runs) before they settle can lose the race: a
// timer field set just after cleanup() reads it is never cleared, and homeLoad/vpLoad arm
// a 15-minute refresh handle on SUCCESS too, not only a retry on failure — that one keeps
// the whole process alive long after every assertion has passed. One settle() first lets
// every loader a test kicked off reach its own setState/setTimeout before cleanup sweeps.
afterEach(async () => { await settle(); cleanup(); });

// ── sign in ──────────────────────────────────────────────────────────────────

test('login 200: account, both tokens, signed in on home; only the refresh token is stored', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: ' sam@example.com ', password: 'correct-horse-battery-staple' });
  await c.authSignIn();
  const r = requests(A + '/login')[0];
  assert.deepEqual(r.body, { email: 'sam@example.com', password: 'correct-horse-battery-staple' });
  assert.equal(r.headers.Authorization, undefined, 'login never carries a bearer');
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.navOpen, true);
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.accessToken, 'acc-1');
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.password, '', 'the password leaves memory');
  assert.equal(stored().refreshToken, 'ref-1');
  assert.equal(stored().account.full_name, 'Sam Okafor');
  assert.equal(JSON.stringify(stored()).includes('acc-1'), false, 'the access token is never stored');
});

test('after login every backend call carries the bearer', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET /backend/udr/api/anything'] = [200, { ok: true }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await apiFetch(BASES.udr, '/api/anything');
  assert.equal(requests('/backend/udr/api/anything')[0].headers.Authorization, 'Bearer acc-1');
});

test('a user-role account gets User view only: role clamped, no Admin view row', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), role: 'admin' });
  await c.authSignIn();
  assert.equal(c.state.role, 'user');
  const v = c.renderVals();
  assert.equal(v.canAdmin, false);
  assert.equal(v.acctItems.some((i) => i.label === 'Admin view' || i.label === 'User view'), false);
  assert.ok(v.acctItems.some((i) => i.label === 'Change password'));
  assert.ok(v.acctItems.some((i) => i.label === 'Sign out everywhere'));
  assert.equal(v.acctName, 'Sam Okafor');
  assert.equal(v.acctEmail, 'sam@example.com');
  assert.equal(v.acctInitial, 'S');
});

test('an admin account lands in admin view on sign-in, with a toggle back to user view', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.role, 'admin');
  const v = c.renderVals();
  assert.equal(v.canAdmin, true);
  const toggle = v.acctItems.find((i) => i.label === 'User view');
  assert.ok(toggle);
  toggle.click();
  assert.equal(c.state.role, 'user');
});

const SA = '/backend/ops-intelligence/api/superadmin';
const ADM = '/backend/ops-intelligence/api/admin';
// A superadmin (like an admin) is canAdmin, so authEnter also fires usLiveLoad/auLiveLoad
// alongside saLiveLoad — all three must be mocked clean, or the two unmocked ones fail and
// arm a 30s retry timer that afterEach's cleanup can lose the race to clear (the retry is
// only armed once the failed fetch's rejection actually runs, which can land after
// cleanup already fired), hanging the whole file on a real 30-second timeout.
const saMock = () => {
  handlers['GET ' + SA + '/companies'] = [200, { ok: true, companies: [] }];
  handlers['GET ' + SA + '/credits'] = [200, { ok: true, month_total: 0, companies: [] }];
  handlers['GET ' + ADM + '/users'] = [200, { ok: true, count: 0, summary: { users: 0, can_ingest: 0, pending_invites: 0, access_boundary: 'building' }, users: [] }];
  handlers['GET ' + ADM + '/buildings'] = [200, { ok: true, count: 0, buildings: [] }];
  handlers['GET ' + ADM + '/ingestion-audit'] = [200, { ok: true, count: 0, entries: [] }];
};

test('a superadmin account opens straight into the Super Admin console on sign-in', async () => {
  saMock();
  handlers['POST ' + A + '/login'] = session(SUPERADMIN, 1);
  c.setState({ email: 'sadie@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.equal(c.state.saOn, true);
  const v = c.renderVals();
  assert.equal(v.canAdmin, true);
  assert.equal(v.acctRole, 'Super Admin', 'never demoted to plain "Admin view"');
  assert.equal(v.acctOrgName, null, 'the console spans every company, so none is named here');
  assert.ok(requests(SA + '/companies').length > 0);
});

test('signing out from inside the Super Admin console actually closes it, not just the session underneath', async () => {
  saMock();
  handlers['POST ' + A + '/login'] = session(SUPERADMIN, 1);
  c.setState({ email: 'sadie@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.equal(c.state.saOn, true, 'lands in the console on sign-in');
  c.renderVals().signOut();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.saOn, false, 'the fixed full-screen overlay must not outlive the session it belongs to');
});

// ── building scope (docs/api/building-scope-api.md) — carried on GET /me, never on
//    login/refresh's own reply, so authEnter fetches it separately (authLoadScope) and
//    merges it onto the account already in state. ─────────────────────────────────────

const BSB1 = '11111111-1111-4111-8111-111111111111';
const BSB2 = '22222222-2222-4222-8222-222222222222';

test('authLoadScope fetches GET /me after a fresh sign-in and merges building scope onto the account', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, USER, {
    building_ids: [BSB1], all_buildings: false, selected_building_id: BSB1,
    buildings: [{ id: BSB1, name: 'Riverside Court' }, { id: BSB2, name: 'Bishopsgate Tower' }]
  }) }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.deepEqual(c.state.account.building_ids, [BSB1]);
  assert.equal(c.state.account.selected_building_id, BSB1);
  assert.equal(c.state.account.buildings.length, 2);
});

test('an unrestricted account (building_ids null) offers no switcher — nothing to switch between', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, USER, {
    building_ids: null, all_buildings: false, selected_building_id: null, buildings: []
  }) }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.equal(c.state.account.building_ids, null);
  const v = c.renderVals();
  assert.equal(v.bldShow, false, 'no picker at all with nothing to switch between');
  assert.equal(v.acctItems.some((i) => i.label === 'All buildings'), false, 'and never as rows in the account menu');
});

test('a live GET /me failure leaves scope alone rather than breaking sign-in', async () => {
  // liveMock() gives every register loader a handler, but not GET /me here — the default
  // "no handler" throw must be swallowed, not surface as an unhandled rejection or a
  // failed sign-in.
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.account.building_ids, undefined, 'login never carried this field; the failed /me left it exactly as login sent it');
});

test('two or more buildings offer a switcher; selecting one PATCHes, re-reads scope and re-fetches live data', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  // Stateful, like a real backend: /me reflects whatever the PATCH below last set, so this
  // also proves authSelectBuilding re-reads scope rather than trusting the optimistic value.
  let selected = null;
  handlers['GET ' + A + '/me'] = () => [200, { ok: true, user: Object.assign({}, USER, {
    building_ids: [BSB1, BSB2], all_buildings: false, selected_building_id: selected,
    buildings: [{ id: BSB1, name: 'Riverside Court' }, { id: BSB2, name: 'Bishopsgate Tower' }]
  }) }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  let v = c.renderVals();
  assert.equal(v.bldShow, true);
  assert.equal(v.bldLabel, 'All buildings');
  assert.equal(v.bldScoped, false, 'the resting state stays quiet');
  assert.equal(v.acctItems.some((i) => i.label === 'Bishopsgate Tower'), false, 'buildings never crowd the account menu');
  const row = v.bldRows.find((i) => i.label === 'Bishopsgate Tower');
  assert.ok(row, 'the second building is offered in the picker');
  handlers['PATCH ' + A + '/me/selected-building'] = (u, o) => {
    assert.deepEqual(JSON.parse(o.body), { building_id: BSB2 });
    selected = BSB2;
    return [200, { ok: true, selected_building_id: BSB2 }];
  };
  row.click();
  assert.equal(c.state.account.selected_building_id, BSB2, 'applied optimistically before the PATCH settles');
  await settle();
  v = c.renderVals();
  assert.equal(v.bldLabel, 'Bishopsgate Tower');
  assert.equal(v.bldScoped, true, 'a narrowed scope is the state worth noticing');
});

test('a refused selection (403) reverts and flashes why, never widening what is shown', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, USER, {
    building_ids: [BSB1, BSB2], all_buildings: false, selected_building_id: BSB1,
    buildings: [{ id: BSB1, name: 'Riverside Court' }, { id: BSB2, name: 'Bishopsgate Tower' }]
  }) }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  handlers['PATCH ' + A + '/me/selected-building'] = () => fail(403, 'building_not_allocated', 'Not your building.');
  await c.authSelectBuilding(BSB2);
  assert.equal(c.state.account.selected_building_id, BSB1, 'reverted to what the server last agreed to');
  assert.match(c.state.toast, /aren't allocated/);
});

// GET /me only ever populates `buildings` for a RESTRICTED caller (a non-null,
// non-empty principal.building_ids) — the backend builds that list off it. An
// admin/superadmin's building_ids is null, so /me's own list is [] for them even with a
// whole company of buildings to pick from; authLoadScope must fall back to
// GET /api/admin/buildings, the same admin-only route the invite form's chips use.
test('an admin/superadmin gets their switcher from GET /api/admin/buildings, since /me never lists buildings for an unrestricted caller', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, ADMIN, {
    building_ids: null, all_buildings: true, selected_building_id: null, buildings: []
  }) }];
  handlers['GET ' + ADM + '/buildings'] = [200, { ok: true, count: 2, buildings: [
    { id: BSB1, name: 'Riverside Court', building_code: 'B-001' },
    { id: BSB2, name: 'Bishopsgate Tower', building_code: 'B-002' }
  ] }];
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.equal(c.state.account.building_ids, null, 'still unrestricted — the fallback fills the picker, not the allocation');
  assert.deepEqual(c.state.account.buildings, [
    { id: BSB1, name: 'Riverside Court', building_code: 'B-001' },
    { id: BSB2, name: 'Bishopsgate Tower', building_code: 'B-002' }
  ]);
  const v = c.renderVals();
  assert.equal(v.bldShow, true, 'the picker is offered to an admin too');
  assert.equal(v.bldLabel, 'All buildings');
  assert.ok(v.bldRows.some((i) => i.label === 'Bishopsgate Tower'));
});

test('a plain user never calls the admin-only buildings route — GET /me already answers for them, empty or not', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, USER, {
    building_ids: [], all_buildings: false, selected_building_id: null, buildings: []
  }) }];
  handlers['GET ' + ADM + '/buildings'] = () => { throw new Error('a plain user must never call the admin-only buildings route'); };
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  assert.deepEqual(c.state.account.building_ids, [], 'allocated to nothing is a real answer, not an error');
  assert.deepEqual(c.state.account.buildings, []);
});

// PATCH /api/auth/me/selected-building validates a choice against the CALLER'S OWN
// company with no acting-as override, so selecting there while viewing as a different
// company (superAdmin.js's viewAsCompany) can only ever 403 — the switcher is withheld
// rather than offering rows that cannot work.
test('the switcher is withheld while viewing as another company — selecting there would only 403', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, ADMIN, {
    building_ids: null, all_buildings: true, selected_building_id: null,
    buildings: [{ id: BSB1, name: 'Riverside Court' }, { id: BSB2, name: 'Bishopsgate Tower' }]
  }) }];
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  let v = c.renderVals();
  assert.ok(v.bldRows.some((i) => i.label === 'Bishopsgate Tower'), 'offered normally, acting as no one');
  c.setState({ viewOrgId: 'org-2', viewOrgName: 'Another Co' });
  v = c.renderVals();
  assert.equal(v.bldShow, false, 'withheld while viewing as another company');
});

// An admin's list is every building in the company, which on this deployment is hundreds,
// and the names are NOT unique — three rows called "MixedUse 004" is normal. So the picker
// searches name AND code, and carries the code so duplicates can be told apart.
test('the picker searches on name and on code, and keeps duplicates distinguishable', async () => {
  const B3 = '33333333-3333-4333-8333-333333333333';
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, ADMIN, {
    building_ids: null, all_buildings: true, selected_building_id: null,
    buildings: [
      { id: BSB1, name: 'MixedUse 004', building_code: 'B-004-A' },
      { id: BSB2, name: 'MixedUse 004', building_code: 'B-004-B' },
      { id: B3, name: 'Harbour View', building_code: 'B-011' }
    ]
  }) }];
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();

  let v = c.renderVals();
  assert.equal(v.bldRows.length, 3, 'unfiltered, every building is offered');
  assert.equal(v.bldPlaceholder, 'Search 3 buildings');
  const dupes = v.bldRows.filter((r) => r.label === 'MixedUse 004');
  assert.deepEqual(dupes.map((r) => r.code), ['B-004-A', 'B-004-B'], 'two rows with one name are still told apart by code');
  assert.notEqual(dupes[0].id, dupes[1].id, 'and they select different buildings');

  c.setState({ bldQuery: 'harbour' });
  v = c.renderVals();
  assert.deepEqual(v.bldRows.map((r) => r.label), ['Harbour View'], 'searched by name');
  assert.equal(v.bldNoMatch, false);

  c.setState({ bldQuery: 'B-004-B' });
  v = c.renderVals();
  assert.deepEqual(v.bldRows.map((r) => r.code), ['B-004-B'], 'searched by code — the only way to name one of the duplicates');

  c.setState({ bldQuery: 'nothing here' });
  v = c.renderVals();
  assert.deepEqual(v.bldRows, []);
  assert.equal(v.bldNoMatch, true, 'an empty result says so rather than rendering a blank menu');

  // "All buildings" is the way back to the whole portfolio, not a search result.
  assert.equal(v.bldAllRow.label, 'All buildings');
  assert.equal(v.bldAllRow.tick, true);
});

test('opening the picker closes the account menu, and picking a building clears the search', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  handlers['GET ' + A + '/me'] = [200, { ok: true, user: Object.assign({}, ADMIN, {
    building_ids: null, all_buildings: true, selected_building_id: null,
    buildings: [{ id: BSB1, name: 'Riverside Court' }, { id: BSB2, name: 'Bishopsgate Tower' }]
  }) }];
  handlers['PATCH ' + A + '/me/selected-building'] = [200, { ok: true, selected_building_id: BSB2 }];
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();

  c.setState({ acctOpen: true });
  c.renderVals().bldToggle();
  assert.equal(c.state.bldOpen, true);
  assert.equal(c.state.acctOpen, false, 'two menus must never be open over each other');

  c.setState({ bldQuery: 'bishop' });
  c.renderVals().bldRows[0].click();
  assert.equal(c.state.bldOpen, false, 'the menu closes on pick');
  assert.equal(c.state.bldQuery, '', 'and never reopens holding the last search');
  await settle();
});

// ── invitation links: /accept-invitation?token=… ─────────────────────────────

const TOKEN = 'inv-token-abcdefghijklmnopqrstuvwxyz';
const withUrl = (pathname, search, fn) => {
  const loc = globalThis.window.location;
  const replaced = [];
  const prev = { pathname: loc.pathname, search: loc.search, history: globalThis.window.history };
  Object.assign(loc, { pathname, search });
  globalThis.window.history = { replaceState: (st, title, url) => replaced.push(url) };
  try { return fn(replaced); } finally { Object.assign(loc, { pathname: prev.pathname, search: prev.search }); globalThis.window.history = prev.history; }
};

test('an invitation link puts the gate straight into invite mode, holds the token, and scrubs it from the address bar', () => {
  withUrl('/accept-invitation', '?token=' + TOKEN, (replaced) => {
    const inv = new HoistraLogic();
    inv.authBoot();
    assert.equal(inv.state.authMode, 'invite');
    assert.equal(inv.state.inviteToken, TOKEN);
    assert.deepEqual(replaced, ['/'], 'the token must not survive in history, a reload or a screenshot');
    cleanup(inv);
  });
});

test('an invitation link opened in a tab that is signed in as someone else signs that session out first', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  withUrl('/accept-invitation', '?token=' + TOKEN, () => {
    assert.equal(c.authInviteFromUrl(), true);
    assert.equal(c.state.signedIn, false, 'the link is for the invitee, not whoever was here');
    assert.equal(c.state.authMode, 'invite');
    assert.equal(c.state.inviteToken, TOKEN);
  });
});

test('?invite= works on any path, for a static host with no /accept-invitation fallback', () => {
  withUrl('/Hoistway Customer Journey.dc.html', '?invite=' + TOKEN, (replaced) => {
    const inv = new HoistraLogic();
    assert.equal(inv.authInviteFromUrl(), true);
    assert.equal(inv.state.inviteToken, TOKEN);
    assert.deepEqual(replaced, ['/Hoistway Customer Journey.dc.html'], 'the page is kept, only the query goes');
    cleanup(inv);
  });
});

test('a plain load with no token is untouched', () => {
  withUrl('/', '', () => {
    const inv = new HoistraLogic();
    assert.equal(inv.authInviteFromUrl(), false);
    assert.equal(inv.state.authMode, 'signin');
    cleanup(inv);
  });
});

test('accepting: posts token + password + name, then goes straight to the verify-code screen (accept() leaves the account pending a confirmation code)', async () => {
  c.setState({ authMode: 'invite', inviteToken: TOKEN, password: 'Correct-Horse-Battery-2026', fullName: ' Dana Reyes ', code: 'stale' });
  handlers['POST ' + A + '/invitations/accept'] = [200, {
    ok: true, user_id: 'u-7', email: 'dana@example.com', role: 'user', organization_id: 'org-1',
    verification_required: true, otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }
  }];
  assert.equal(c.renderVals().authInviteReady, true);
  await c.authAcceptInvite();
  const r = requests(A + '/invitations/accept')[0];
  assert.deepEqual(r.body, { token: TOKEN, password: 'Correct-Horse-Battery-2026', full_name: 'Dana Reyes' });
  assert.equal(r.headers.Authorization, undefined, 'a public route — no bearer');
  assert.equal(c.state.authMode, 'verify', 'not sign-in — the code IS how this account signs in');
  assert.equal(c.state.email, 'dana@example.com', 'so resend/verify target the right address');
  assert.equal(c.state.inviteToken, '', 'one-shot: spent');
  assert.equal(c.state.password, '');
  assert.equal(c.state.code, '', 'no stale code carried over from anywhere else');
  assert.match(c.state.authNotice, /code to confirm/i);
});

test('accepting then entering the code signs the invitee straight in — no re-typing the password', async () => {
  c.setState({ authMode: 'invite', inviteToken: TOKEN, password: 'Correct-Horse-Battery-2026', fullName: 'Dana Reyes' });
  handlers['POST ' + A + '/invitations/accept'] = [200, {
    ok: true, user_id: 'u-7', email: 'dana@example.com', role: 'user', organization_id: 'org-1', verification_required: true
  }];
  await c.authAcceptInvite();
  handlers['POST ' + A + '/verify-email'] = session(Object.assign({}, USER, { id: 'u-7', email: 'dana@example.com' }), 1);
  c.setState({ code: '123456' });
  await c.authVerify();
  const r = requests(A + '/verify-email')[0];
  assert.deepEqual(r.body, { email: 'dana@example.com', code: '123456' });
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.account.email, 'dana@example.com');
});

test('accepting: a password below the configured minimum is refused locally, no request made', async () => {
  c.setState({ authMode: 'invite', inviteToken: TOKEN, password: 'short' });
  assert.equal(c.renderVals().authInviteReady, false);
  await c.authAcceptInvite();
  assert.equal(requests(A + '/invitations/accept').length, 0);
  assert.match(c.state.authError, /at least 12 characters/i);
  assert.equal(c.state.authMode, 'invite');
});

test('accepting: an expired, used or unknown link is not a form to retry — back to sign-in with the server line', async () => {
  c.setState({ authMode: 'invite', inviteToken: TOKEN, password: 'Correct-Horse-Battery-2026' });
  handlers['POST ' + A + '/invitations/accept'] = fail(410, 'expired', 'That invitation has expired. Ask to be re-invited.');
  await c.authAcceptInvite();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.inviteToken, '');
  assert.equal(c.state.authNotice, 'That invitation has expired. Ask to be re-invited.');
  assert.equal(c.state.authError, '');
});

test('accepting: a weak password (server-side) keeps the form up, clears the password, shows the reason', async () => {
  c.setState({ authMode: 'invite', inviteToken: TOKEN, password: 'passwordpassword' });
  handlers['POST ' + A + '/invitations/accept'] = fail(400, 'weak_password', 'Choose a less common password.');
  await c.authAcceptInvite();
  assert.equal(c.state.authMode, 'invite');
  assert.equal(c.state.inviteToken, TOKEN, 'the link is still good');
  assert.equal(c.state.password, '');
  assert.equal(c.state.authError, 'Choose a less common password.');
});

test('a superadmin toggled into their own company\'s Admin view still reads as Super Admin', async () => {
  saMock();
  handlers['POST ' + A + '/login'] = session(Object.assign({}, SUPERADMIN, { organization_name: 'TechCorp Facilities LLC' }), 1);
  c.setState({ email: 'sadie@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  c.setState({ saOn: false, role: 'admin' });
  const v = c.renderVals();
  assert.equal(v.acctRole, 'Super Admin', 'the badge names the account, not the admin/user lens on it');
  assert.equal(v.acctOrgName, 'TechCorp Facilities LLC', 'outside the console, their own company is named again');
});

test('viewing as another company overrides the badge, the org name and the TopBar tenant line', async () => {
  saMock();
  handlers['POST ' + A + '/login'] = session(Object.assign({}, SUPERADMIN, { organization_name: 'TechCorp Facilities LLC' }), 1);
  c.setState({ email: 'sadie@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  c.setState({ saOn: false, role: 'admin', viewOrgId: 'org-99', viewOrgName: 'Gulf Estates FZ' });
  const v = c.renderVals();
  assert.equal(v.acctRole, 'Viewing as', 'more specific than either Super Admin or Admin view while this is active');
  assert.equal(v.acctOrgName, 'Gulf Estates FZ', 'not the account\'s own company while viewing as another one');
  assert.equal(v.tenant, 'Gulf Estates FZ', 'the always-visible TopBar line shows it too, not just the account menu');
  assert.ok(v.acctItems.some((i) => i.label === 'Exit — return to my account'), 'a clear way back is offered');
});

test('signing out — and a fresh sign-in — clear any active viewAsCompany override, so the next account never inherits it', async () => {
  setActingOrg('org-99');
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), viewOrgId: 'org-99', viewOrgName: 'Gulf Estates FZ' });
  await c.authSignIn();
  assert.equal(getActingOrg(), null, 'a fresh sign-in drops any inherited override before it loads a thing');
  assert.equal(c.state.viewOrgId, null);
  assert.equal(c.state.viewOrgName, null);

  setActingOrg('org-99');
  c.renderVals().signOut();
  assert.equal(getActingOrg(), null, 'signing out clears it too, not just the next sign-in');
});

test('a reload (keepView) never re-forces role or re-opens the Super Admin console', async () => {
  saMock();
  handlers['POST ' + A + '/login'] = session(SUPERADMIN, 1);
  c.setState({ email: 'sadie@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await settle();
  c.setState({ saOn: false, role: 'user', view: 'buildings', navOpen: false });
  c.authEnter({ ok: true, user: SUPERADMIN, tokens: { access_token: 'a2', refresh_token: 'r2' } }, { keepView: true });
  assert.equal(c.state.saOn, false);
  assert.equal(c.state.role, 'user');
  assert.equal(c.state.view, 'buildings');
});

test('login 401 shows the message verbatim and stays gated', async () => {
  handlers['POST ' + A + '/login'] = fail(401, 'invalid_credentials', 'That email address and password do not match an account.');
  c.setState({ email: 'sam@example.com', password: 'wrong-wrong-wrong' });
  await c.authSignIn();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authError, 'That email address and password do not match an account.');
  assert.equal(c.state.authReason, 'invalid_credentials');
  assert.equal(c.state.authBusy, false);
  assert.equal(requests(A + '/refresh').length, 0, 'a failing login never refreshes');
});

test('login 429 locked arms a countdown from retry_after_seconds and offers the reset link', async () => {
  handlers['POST ' + A + '/login'] = fail(429, 'locked', 'Too many failed attempts. Try again in 15 minutes, or reset your password.', { retry_after_seconds: 899 });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authReason, 'locked');
  assert.ok(c.state.authRetryAt > Date.now() + 890 * 1000);
  const v = c.renderVals();
  assert.equal(v.authLocked, true);
  assert.match(v.authCountdown, /^14:5\d$/);
  v.authGoForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.email, 'sam@example.com', 'the email is kept for the reset');
});

test('login 403 email_not_verified goes straight to the code screen without a resend', async () => {
  handlers['POST ' + A + '/login'] = fail(403, 'email_not_verified', 'Confirm your email address first. We have sent a new code to sam@example.com.', { email: 'sam@example.com' });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, 'Confirm your email address first. We have sent a new code to sam@example.com.');
  assert.equal(c.state.authError, '');
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.ok(c.state.authRetryAt > Date.now(), 'the resend cooldown is armed — a code is already in the inbox');
});

test('login 422 and a network failure get the gate\'s own one-liners', async () => {
  handlers['POST ' + A + '/login'] = [422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' }] }];
  c.setState({ email: 'nope', password: 'x' });
  await c.authSignIn();
  assert.equal(c.state.authError, 'Enter a valid email address and password.');
  delete handlers['POST ' + A + '/login'];
  await c.authSignIn();
  assert.equal(c.state.authError, "Couldn't reach the sign-in service. Check the backend is running.");
});

test('the SSO button is inert: a toast, no sign-in', () => {
  c.renderVals().authSSO();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Single sign-on is not available yet — sign in with your email and password.');
});

test('the animated page chips no longer fake a sign-in', () => {
  c.renderVals().f4items[0].click();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Sign in to open NABERS data pack.');
});

test('sign out posts the refresh token and clears everything even when logout fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ view: 'buildings', navOpen: true, acctOpen: true });
  // no /logout handler → the mocked fetch rejects, exactly like an unreachable backend
  c.renderVals().signOut();
  await settle();
  assert.equal(requests(A + '/logout')[0].body.refresh_token, 'ref-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.account, null);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.refreshToken, null);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.acctOpen, false);
  assert.equal(c.state.role, 'user');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('a restored role:admin on a user account is clamped in the constructor', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: USER, role: 'admin', view: 'buildings' });
  c = new HoistraLogic();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.role, 'user');
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view: 'buildings' });
  const d = new HoistraLogic();
  assert.equal(d.state.role, 'admin');
});

test('a reload restores the page you were on instead of always landing on Home', () => {
  cleanup();
  for (const view of ['users', 'audit', 'insp']) {
    mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view });
    const inst = new HoistraLogic();
    assert.equal(inst.state.view, view);
  }
});

test('a restored saOn opens straight into the Super Admin console for a stored superadmin account', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: SUPERADMIN, role: 'admin', view: 'home', saOn: true });
  c = new HoistraLogic();
  assert.equal(c.state.saOn, true);
});

test('a restored saOn is clamped for an account that is not actually a superadmin', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view: 'home', saOn: true });
  c = new HoistraLogic();
  assert.equal(c.state.saOn, false);
});

test('reloading while viewing as another company stays on that company — the reported bug: Plenum, reload, must not revert to the account\'s own TechCorp', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({
    signedIn: true, refreshToken: 'ref-1', account: Object.assign({}, SUPERADMIN, { organization_name: 'TechCorp Facilities LLC' }),
    role: 'admin', view: 'buildings', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC'
  });
  c = new HoistraLogic();
  assert.equal(c.state.viewOrgId, 'org-plenum');
  assert.equal(c.state.viewOrgName, 'Plenum Tech LLC');
  assert.equal(getActingOrg(), 'org-plenum', 'the in-memory acting-org (every API call reads this, not state) is re-primed on boot');
  const v = c.renderVals();
  assert.equal(v.acctOrgName, 'Plenum Tech LLC', 'the header still names Plenum, not the account\'s own TechCorp');
});

test('a restored viewOrgId is clamped for an account that is not actually a superadmin', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view: 'home', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  c = new HoistraLogic();
  assert.equal(c.state.viewOrgId, null);
  assert.equal(getActingOrg(), null);
});

test('/config is read once at boot and drives the register link and the length hint', async () => {
  await settle();
  assert.equal(requests(A + '/config').length, 1);
  let v = c.renderVals();
  assert.equal(v.authCanRegister, true);
  assert.equal(v.authMinLength, 12);
  assert.equal(v.authCodeLength, 6);
  c.setState({ authConfig: Object.assign({}, CONFIG, { self_registration: false, password: { min_length: 16 } }) });
  v = c.renderVals();
  assert.equal(v.authCanRegister, false);
  assert.equal(v.authMinLength, 16);
});

// ── create an account ────────────────────────────────────────────────────────

const REG_MSG = 'Check sam@example.com for a 6-digit code and enter it to finish setting up your account. The code lasts 10 minutes.';

test('register 202 → the code screen with the server line, cooldown armed, password gone', async () => {
  handlers['POST ' + A + '/register'] = accepted('verification_sent', REG_MSG);
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'correct-horse-battery-staple', fullName: ' Sam Okafor ', phone: '' });
  await c.authRegister();
  assert.deepEqual(requests(A + '/register')[0].body, { email: 'sam@example.com', password: 'correct-horse-battery-staple', full_name: 'Sam Okafor', phone: null });
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, REG_MSG);
  assert.equal(c.state.password, '');
  assert.equal(c.state.signedIn, false, '202 issues no tokens');
  assert.ok(c.state.authRetryAt > Date.now() + 55 * 1000);
  assert.equal(c.renderVals().authCoolingDown, true);
});

test('register 400 password shows the rule verbatim and stays on the form', async () => {
  handlers['POST ' + A + '/register'] = fail(400, 'password', 'Use at least 12 characters. Length is what makes a password hard to guess; a memorable phrase beats a short one with symbols in it.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'short', fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authMode, 'register');
  assert.equal(c.state.authReason, 'password');
  assert.match(c.state.authError, /^Use at least 12 characters/);
});

test('register 403 registration_closed hides the create-account link from then on', async () => {
  handlers['POST ' + A + '/register'] = fail(403, 'registration_closed', 'Self-registration is off on this deployment. Ask an operator to create your account.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authError, 'Self-registration is off on this deployment. Ask an operator to create your account.');
  assert.equal(c.renderVals().authCanRegister, false);
});

test('verify: a mismatch keeps the typed code and reports attempts left; the right code signs in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123 456' });
  handlers['POST ' + A + '/verify-email'] = fail(400, 'mismatch', 'That code is not correct. 4 attempts left.', { attempts_remaining: 4 });
  await c.authVerify();
  assert.equal(requests(A + '/verify-email')[0].body.code, '123456', 'spaces are stripped');
  assert.equal(c.state.code, '123 456', 'kept for a retype');
  assert.equal(c.state.authAttemptsLeft, 4);
  assert.equal(c.state.authError, 'That code is not correct. 4 attempts left.');
  handlers['POST ' + A + '/verify-email'] = session(USER, 3, 'Your email address is confirmed and you are signed in.');
  await c.authVerify();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.accessToken, 'acc-3');
  assert.equal(c.state.code, '');
  assert.equal(c.state.toast, 'Your email address is confirmed and you are signed in.');
});

test('verify: a dead code is cleared and resend becomes the emphasised action', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '999999' });
  for (const reason of ['expired', 'too_many_attempts', 'no_code']) {
    handlers['POST ' + A + '/verify-email'] = fail(400, reason, 'dead: ' + reason);
    c.setState({ code: '999999' });
    await c.authVerify();
    assert.equal(c.state.code, '', reason + ' clears the field');
    assert.equal(c.state.authReason, reason);
    assert.equal(c.renderVals().authDeadCode, true);
  }
});

test('resend: 202 clears the typed code and re-arms the cooldown; 429 shows the wait', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '1234' });
  handlers['POST ' + A + '/resend-code'] = accepted('sent', 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  assert.equal(c.state.code, '', 'the code in the older email is dead');
  assert.equal(c.state.authNotice, 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  assert.equal(c.renderVals().authCoolingDown, true);
  // Still cooling down: the button is inert, nothing is sent.
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  c.setState({ authRetryAt: 0 });
  handlers['POST ' + A + '/resend-code'] = fail(429, 'cooldown', 'A code was just sent. Ask for another in 59 seconds.', { retry_after_seconds: 59 });
  await c.authResend();
  assert.equal(c.state.authError, 'A code was just sent. Ask for another in 59 seconds.');
  assert.match(c.renderVals().authCountdown, /^00:5\d$/);
});

test('verify 404 no_account returns to sign in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123456' });
  handlers['POST ' + A + '/verify-email'] = fail(404, 'no_account', 'That account no longer exists.');
  await c.authVerify();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authError, 'That account no longer exists.');
});

// ── forgotten password ───────────────────────────────────────────────────────

const FORGOT_MSG = 'If that address has an account, a reset code is on its way. It is valid for 10 minutes.';

test('forgot 202 → reset screen with the server line; 429 shows the wait', async () => {
  c.renderVals().authGoForgot();
  c.setState({ email: 'sam@example.com' });
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authForgot();
  assert.deepEqual(requests(A + '/password/forgot')[0].body, { email: 'sam@example.com' });
  assert.equal(c.state.authMode, 'reset');
  assert.equal(c.state.authNotice, FORGOT_MSG);
  assert.equal(c.renderVals().authCoolingDown, true);
  c.setState({ authMode: 'forgot', authRetryAt: 0 });
  handlers['POST ' + A + '/password/forgot'] = fail(429, 'hourly_cap', 'Too many codes for that address this hour.', { retry_after_seconds: 1800 });
  await c.authForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.authError, 'Too many codes for that address this hour.');
  // ceil() of the remaining 1799.9 s reads 30:00 until a whole second has passed.
  assert.match(c.renderVals().authCountdown, /^(30:00|29:5\d)$/);
});

test('reset: a rejected password keeps the code, a wrong code keeps the code minus an attempt, success returns to sign in', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '418902', newPassword: 'correct-horse-battery-staple' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'password_unchanged', 'That is the password the account already has. If you are resetting it because someone else may know it, choose a different one. Your code is still valid.');
  await c.authReset();
  assert.deepEqual(requests(A + '/password/reset')[0].body, { email: 'sam@example.com', code: '418902', new_password: 'correct-horse-battery-staple' });
  assert.equal(c.state.code, '418902', 'the code survives a rejected password');
  assert.equal(c.state.newPassword, '', 'only the password is retyped');
  assert.equal(c.state.authReason, 'password_unchanged');

  c.setState({ newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'mismatch', 'That code is not correct. 3 attempts left.', { attempts_remaining: 3 });
  await c.authReset();
  assert.equal(c.state.code, '418902');
  assert.equal(c.state.authAttemptsLeft, 3);

  handlers['POST ' + A + '/password/reset'] = [200, { ok: true, message: 'Your password is set. Sign in with it — every other session has been signed out.', sessions_ended: 1 }];
  await c.authReset();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Your password is set. Sign in with it — every other session has been signed out.');
  assert.equal(c.state.email, 'sam@example.com', 'kept for the sign-in');
  assert.equal(c.state.code, '');
  assert.equal(c.state.newPassword, '');
  assert.equal(c.state.signedIn, false, 'a reset deliberately does not sign in');
});

test('reset: a dead code is cleared; resend in reset mode asks /password/forgot', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '000000', newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'expired', 'That code has expired. Ask for a new one.');
  await c.authReset();
  assert.equal(c.state.code, '');
  assert.equal(c.renderVals().authDeadCode, true);
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authResend();
  assert.equal(requests(A + '/password/forgot').length, 1);
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.equal(c.state.authMode, 'reset');
});

// ── reload, refresh, and other tabs ──────────────────────────────────────────

const seedSession = (user, n) => { mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-' + n, account: user, view: 'buildings', role: 'user', navOpen: true }); };

test('reload: the shell renders on the stored identity, one refresh rotates both tokens and keeps the page', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = session(Object.assign({}, USER, { full_name: 'Sam O.' }), 2);
  c = new HoistraLogic();
  calls = []; // beforeEach's fresh() already read /config once; measure only the reload
  assert.equal(c.state.signedIn, true, 'optimistic — no gate flash');
  assert.equal(c.state.account.full_name, 'Sam Okafor');
  assert.equal(c.state.accessToken, null);
  await c.authBoot();
  const r = requests(A + '/refresh');
  assert.equal(r.length, 1);
  assert.deepEqual(r[0].body, { refresh_token: 'ref-1' });
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.refreshToken, 'ref-2');
  assert.equal(stored().refreshToken, 'ref-2');
  assert.equal(c.state.account.full_name, 'Sam O.', 'the server copy replaces the summary');
  assert.equal(c.state.view, 'buildings', 'a refresh does not move the page');
  assert.equal(c.state.authBooting, false);
  assert.equal(requests(A + '/config').length, 0, 'config is only read for the gate');
});

test('reload: a replayed refresh token drops to the gate with the server line and clears storage', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = fail(401, 'replayed', 'That session was already used. Every session has been ended as a precaution — sign in again.');
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'That session was already used. Every session has been ended as a precaution — sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('reload: a network failure keeps the person signed in on the stored account', async () => {
  cleanup();
  seedSession(USER, 1);
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.authBooting, false);
});

test('a bearer call that returns 401 expired is refreshed once and retried once', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  // An arbitrary path, not one of loadLiveData()'s own routes (liveMock() covers those) —
  // this proves the generic bearer-refresh-retry mechanism, not any one endpoint.
  handlers['GET /backend/ops-intelligence/api/whatever'] = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true, items: [] }] : fail(401, 'expired', 'Token expired'));
  const r = await apiFetch(BASES.opsIntelligence, '/api/whatever');
  assert.equal(r.ok, true);
  assert.equal(requests(A + '/refresh').length, 1);
  assert.equal(requests('/backend/ops-intelligence/api/whatever').length, 2);
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.signedIn, true);
});

test('two calls expiring together share one refresh', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  const guard = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true }] : fail(401, 'expired', 'Token expired'));
  handlers['GET /backend/ops-intelligence/api/a'] = guard;
  handlers['GET /backend/ops-intelligence/api/b'] = guard;
  await Promise.all([apiFetch(BASES.opsIntelligence, '/api/a'), apiFetch(BASES.opsIntelligence, '/api/b')]);
  assert.equal(requests(A + '/refresh').length, 1, 'single-flight');
});

test('a terminal 401 on any call signs out with the server line', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['GET /backend/ops-intelligence/api/a'] = fail(401, 'session_revoked', 'That session has ended. Sign in again.');
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/a'));
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authNotice, 'That session has ended. Sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
  assert.equal(requests(A + '/refresh').length, 0);
});

test('a refresh presents the token from storage, not a stale copy in memory', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // Another tab rotated to ref-7 and wrote it.
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-7' }));
  handlers['POST ' + A + '/refresh'] = session(USER, 8);
  await c.authRefresh();
  assert.equal(requests(A + '/refresh')[0].body.refresh_token, 'ref-7');
  assert.equal(c.state.refreshToken, 'ref-8');
});

test('another tab rotating the token is adopted; another tab signing out signs this one out', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-5', account: Object.assign({}, USER, { full_name: 'Sam Five' }) }));
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.refreshToken, 'ref-5');
  assert.equal(c.state.account.full_name, 'Sam Five');
  assert.equal(c.state.signedIn, true);
  c.authStorageChanged({ key: 'something.else' });
  assert.equal(c.state.signedIn, true, 'other keys are ignored');
  delete mem[SESSION_KEY];
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, false);
});

test('a DIFFERENT account writing the shared slot is not adopted — no live hijack of this tab\'s own session', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // A second tab signs into a DIFFERENT account (ADMIN, a different id) and, in doing so,
  // overwrites the one shared localStorage slot — this is what two tabs on two different
  // companies do today, and it used to fire this exact listener in every other tab.
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: ADMIN, view: 'home', role: 'admin' });
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.refreshToken, 'ref-1', 'this tab keeps its own token');
  assert.equal(c.state.account.id, USER.id, 'this tab keeps its own account, not the other tab\'s');
  assert.equal(c.state.signedIn, true);
});

test('a DIFFERENT account\'s tab signing out does not sign this tab out too', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // The shared slot currently belongs to a different account (as above), and that
  // account's own tab now signs out, clearing the shared slot entirely.
  const oldShared = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: ADMIN, view: 'home', role: 'admin' });
  delete mem[SESSION_KEY];
  c.authStorageChanged({ key: SESSION_KEY, oldValue: oldShared });
  assert.equal(c.state.signedIn, true, 'the account that left was never this tab\'s own');
  assert.equal(c.state.refreshToken, 'ref-1');
});

test('authRefresh never presents a different account\'s token from the shared slot', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // Another tab's different account owns the shared slot now.
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: ADMIN, view: 'home', role: 'admin' });
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  await c.authRefresh();
  assert.equal(requests(A + '/refresh')[0].body.refresh_token, 'ref-1', 'this tab\'s own token, never the other account\'s');
});

test('a reload never lands on a different account\'s session — the exact bug: Plenum tab reloads, must not become TechCorp', () => {
  cleanup();
  const TECHCORP = Object.assign({}, USER, { id: 'u-9', email: 'admin@techcorp.ae', full_name: 'TechCorp Admin', organization_name: 'TechCorp Facilities LLC' });
  const PLENUM = Object.assign({}, USER, { id: 'u-8', email: 'admin@plenum-tech.com', full_name: 'Plenum Admin', organization_name: 'Plenum Tech LLC' });
  // This tab (this browser instance's constructor) is Plenum's own — nothing has written
  // the shared slot from this tab yet in this test, only its own sessionStorage matters.
  // Simulate it directly: HoistraLogic merges loadSession() at construction, and
  // loadSession() prefers sessionStorage, so seed tabMem with Plenum and mem with
  // TechCorp — the shape another, already-open TechCorp tab would have left behind.
  globalThis.window.sessionStorage.setItem(SESSION_KEY, JSON.stringify({
    signedIn: true, refreshToken: 'ref-plenum', account: PLENUM, view: 'buildings', role: 'admin'
  }));
  globalThis.window.localStorage.setItem(SESSION_KEY, JSON.stringify({
    signedIn: true, refreshToken: 'ref-techcorp', account: TECHCORP, view: 'home', role: 'admin'
  }));
  const reloaded = new HoistraLogic();
  assert.equal(reloaded.state.account.id, PLENUM.id, 'reloading the Plenum tab must restore Plenum, not TechCorp');
  assert.equal(reloaded.state.refreshToken, 'ref-plenum');
  assert.equal(reloaded.state.view, 'buildings');
});

// ── inside a session ─────────────────────────────────────────────────────────

test('change password 200 signs out with the server line — the API ended this session too', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ acctOpen: true });
  c.renderVals().acctItems.find((i) => i.label === 'Change password').click();
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.acctOpen, false);
  c.setState({ pwCurrent: 'x'.repeat(12), pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = [200, { ok: true, message: 'Your password is changed. Sign in again with the new one.', sessions_ended: 2 }];
  await c.pwSubmit();
  const r = requests(A + '/password/change')[0];
  assert.deepEqual(r.body, { current_password: 'x'.repeat(12), new_password: 'yet-another-good-passphrase' });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.authNotice, 'Your password is changed. Sign in again with the new one.');
  assert.equal(c.state.pwCurrent, '');
  assert.equal(c.state.pwNext, '');
});

test('change password 401 and 400 stay in the modal with the message', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.pwOpenModal();
  c.setState({ pwCurrent: 'nope', pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = fail(401, 'invalid_credentials', 'Your current password is not correct.');
  await c.pwSubmit();
  assert.equal(c.state.signedIn, true, 'a wrong current password is not a dead session');
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.pwError, 'Your current password is not correct.');
  assert.equal(c.state.pwBusy, false);
  handlers['POST ' + A + '/password/change'] = fail(400, 'password_unchanged', 'That is the password you already have.');
  await c.pwSubmit();
  assert.equal(c.state.pwError, 'That is the password you already have.');
  c.pwClose();
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.pwError, '');
});

test('sign out everywhere sends everywhere:true with the bearer, then signs out locally', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/logout'] = [200, { ok: true, message: 'Signed out.', sessions_ended: 3 }];
  await c.renderVals().acctItems.find((i) => i.label === 'Sign out everywhere').click();
  const r = requests(A + '/logout')[0];
  assert.deepEqual(r.body, { everywhere: true });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out of 3 sessions.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('sign out everywhere still signs out locally when the call fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await c.authSignOutEverywhere();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out.');
});

// ── two tabs over one store ──────────────────────────────────────────────────

test('a tab on the gate never writes the store, so it cannot erase the session another tab holds', async () => {
  const gated = new HoistraLogic();          // opened first, sits on the gate
  gated.authBoot();
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(stored().refreshToken, 'ref-1');
  gated.setState((p) => ({ frame: (p.frame + 1) % 4 }));   // the gate animation ticks every 3.2 s
  gated.setState({ email: 'someone@else.com' });           // and the person types
  assert.equal(stored().refreshToken, 'ref-1', 'the gated tab left the key alone');
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, true);
  gated.authStop();
});

test('a tab on the gate ignores another tab signing in, and the key survives', async () => {
  const gated = new HoistraLogic();
  gated.authBoot();
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  gated.authStorageChanged({ key: SESSION_KEY });          // what the browser fires in the other tab
  assert.equal(gated.state.signedIn, false);
  assert.equal(gated.state.refreshToken, null, 'no stale credential in a gated tab');
  assert.equal(stored().refreshToken, 'ref-1');
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, true);
  gated.authStop();
});

test('signing out removes the key exactly once, on the way out', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.renderVals().signOut();
  assert.equal(mem[SESSION_KEY], undefined);
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-other', account: USER, view: 'home' }); // another tab signs in later
  c.setState({ email: 'typing@again.com' });
  assert.equal(JSON.parse(mem[SESSION_KEY]).refreshToken, 'ref-other', 'this gated tab did not touch it');
});

test('reload: a 5xx on the refresh keeps the session — only a 401 means the token is finished', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = [503, { detail: 'Service Unavailable' }];
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.refreshToken, 'ref-1', 'the credential is kept for the next try');
  assert.equal(c.state.authNotice, '');
});

test('sign out everywhere keeps the server line when the call itself ended the session', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/logout'] = fail(401, 'session_revoked', 'That session has ended. Sign in again.');
  await c.authSignOutEverywhere();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authNotice, 'That session has ended. Sign in again.');
  assert.equal(c.state.toast, 'Signed out.');
});

// isUnallocated — the difference between "you cannot see any building" and "there are no
// buildings". Both render as an empty table; the fix for each is in a different place, so
// the screens branch on this rather than guessing from row count alone.
test('isUnallocated: only a signed-in non-admin with an explicitly empty allocation', async () => {
  const { isUnallocated } = await import('../src/logic/auth.js');
  // The real case this was written for: role fell back to "user", nothing in user_buildings.
  assert.equal(isUnallocated({ id: 'u1', all_buildings: false, building_ids: [] }), true);
  // An admin sees the whole company; building_ids is meaningless for them.
  assert.equal(isUnallocated({ id: 'u1', all_buildings: true, building_ids: [] }), false);
  // Allocated to something is not unallocated.
  assert.equal(isUnallocated({ id: 'u1', all_buildings: false, building_ids: ['b1'] }), false);
  // Not yet loaded (undefined/null list) must NOT read as unallocated — that would put the
  // "ask an admin" message on screen during every cold load.
  assert.equal(isUnallocated({ id: 'u1', all_buildings: false }), false);
  assert.equal(isUnallocated({ id: 'u1', all_buildings: false, building_ids: null }), false);
  // No account at all, or an account with no id yet.
  assert.equal(isUnallocated(null), false);
  assert.equal(isUnallocated({ all_buildings: false, building_ids: [] }), false);
});
