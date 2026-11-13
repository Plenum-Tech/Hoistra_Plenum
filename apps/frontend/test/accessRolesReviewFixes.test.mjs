// Gaps an independent review found in the client's half of access roles (13 Nov 2026).
//
// A Vendors-only user must still see which vendors are blocked; the question bar belongs to
// Energy and to Assets; pages left on an account with no role still restrict; a burst of role
// edits must not leave the row showing access the server does not hold; and pages learned
// from a token refresh must reload the registers.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url) => { throw new TypeError('no network in tests: ' + url); };

const A = await import('../src/logic/accessRoles.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { adminApi } = await import('../src/api/admin.js');
const { complianceApi } = await import('../src/api/compliance.js');

const B = { ops: '/backend/ops-intelligence', wo: '/backend/work-order', con: '/backend/connector' };
const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam', organization_id: 'org-1', status: 'active', role: 'user' };
const role = (pages) => Object.assign({}, USER, { access_role: 'general_user', pages, pages_known: true });
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 30));
const quiet = (x, names) => { names.forEach((k) => { x[k] = () => {}; }); return x; };
const BOOT = ['authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun'];

beforeEach(() => { Object.keys(mem).forEach((k) => delete mem[k]); });

test('vendor coverage is read by a Vendors holder: it carries the Blocked state', () => {
  assert.deepEqual(A.pagesForRequest(B.ops, '/api/compliance/coverage/vendors', B), ['vendors', 'compliance']);
  assert.equal(A.requestHeld(role(['vendors']), B.ops, '/api/compliance/coverage/vendors', B), true);
  assert.equal(A.requestHeld(role(['energy']), B.ops, '/api/compliance/coverage/vendors', B), false);
  assert.deepEqual(A.pagesForRequest(B.ops, '/api/compliance/coverage/buildings', B), ['compliance']);
});

test('the question bar is the Energy page\'s and the Assets page\'s', () => {
  assert.deepEqual(A.pagesForRequest(B.ops, '/api/energy/ask', B), ['energy', 'assets']);
  assert.deepEqual(A.pagesForRequest(B.ops, '/api/energy/ask/suggestions', B), ['energy', 'assets']);
  assert.equal(A.requestHeld(role(['energy']), B.ops, '/api/energy/ask', B), true);
  assert.equal(A.requestHeld(role(['compliance']), B.ops, '/api/energy/ask', B), false);
});

test('pages left on an account with no role still restrict', () => {
  const odd = Object.assign({}, USER, { access_role: null, pages: ['energy'] });
  assert.equal(A.isRoleUser(odd), true);
  assert.deepEqual(A.pagesOf(odd), ['energy']);
  assert.equal(A.holdsPage(odd, 'compliance'), false);
  assert.equal(A.isRoleUser(Object.assign({}, USER, { access_role: null, pages: null })), false);
  assert.equal(A.isRoleUser(Object.assign({}, USER, { role: 'admin', access_role: null, pages: ['energy'] })), false);
});

test('a Vendors-only user\'s page asks for no certificates, and says so without an error', async () => {
  const x = quiet(new HoistraLogic(), BOOT.concat(['loadLiveData', 'resetLiveData']));
  x.authEnter({ ok: true, user: role(['vendors']), tokens: { access_token: 'a', refresh_token: 'r' } });
  const asked = [];
  const real = complianceApi.listCertificates;
  complianceApi.listCertificates = async (q) => { asked.push(q); return { certificates: [{ id: 'c' }] }; };
  try {
    const out = await x.vpReads().certificates();
    assert.deepEqual(asked, []);
    assert.deepEqual(out, { certificates: [] });
    x.setState({ account: Object.assign({}, USER, { access_role: null, pages: null, pages_known: true }) });
    assert.deepEqual(await x.vpReads().certificates(), { certificates: [{ id: 'c' }] });
    assert.equal(asked.length, 1);
  } finally { complianceApi.listCertificates = real; }
});

test('a burst of role edits leaves the row showing what the server holds', async () => {
  const U1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
  const x = quiet(new HoistraLogic(), BOOT.concat(['loadLiveData', 'resetLiveData']));
  x.flash = () => {};
  x.setState({ signedIn: true, account: Object.assign({}, USER, { role: 'admin' }), role: 'admin', view: 'users', usLiveLoadedAt: Date.now(),
    users: [{ id: U1, name: 'Sam', email: 's@example.com', title: '', buildings: [], allB: false, ingest: false, status: 'Active', role: 'user', queries: 0, ingests: 0, last: '—', live: true, accessRole: 'general_user', accessRoleLabel: 'General User', pages: ['assets'] }] });
  let n = 0;
  const real = adminApi.patchUser;
  // The first change is taken by the server; the second is refused.
  adminApi.patchUser = async () => { n += 1; if (n === 2) throw new Error('refused'); return { ok: true }; };
  try {
    const u = x.state.users[0];
    x.usLiveSetAccess(u, 'general_user', ['energy', 'assets']);
    x.usLiveSetAccess(u, 'general_user', ['energy']);
    await settle(60);
    assert.deepEqual(x.state.users[0].pages, ['energy', 'assets'], 'the change the server took is on the row');
  } finally { adminApi.patchUser = real; }
});

test('pages learned from a token refresh reload the registers', () => {
  const x = quiet(new HoistraLogic(), BOOT);
  let loads = 0, resets = 0;
  x.loadLiveData = () => { loads += 1; };
  x.resetLiveData = () => { resets += 1; };
  x.authEnter({ ok: true, user: role(['compliance']), tokens: { access_token: 'a', refresh_token: 'r' } });
  loads = 0; resets = 0;
  // A refresh keeps the view; this one says the pages changed.
  x.authEnter({ ok: true, user: role(['compliance', 'energy']), tokens: { access_token: 'a2', refresh_token: 'r2' } }, { keepView: true });
  assert.equal(loads, 1);
  assert.equal(resets, 1);
  // A refresh that changes nothing reloads nothing.
  x.authEnter({ ok: true, user: role(['compliance', 'energy']), tokens: { access_token: 'a3', refresh_token: 'r3' } }, { keepView: true });
  assert.equal(loads, 1);
});

test('a building\'s cost drivers are the Assets page\'s: not asked for, and no error, without it', async () => {
  assert.deepEqual(A.pagesForRequest(B.ops, '/api/energy/buildings/b1/cost-drivers', B), ['assets']);
  const { energyApi } = await import('../src/api/energy.js');
  const asked = [];
  const real = energyApi.costDrivers;
  energyApi.costDrivers = async (id) => { asked.push(id); return { drivers: [{ asset: 'AHU-1' }] }; };
  try {
    const x = quiet(new HoistraLogic(), BOOT.concat(['loadLiveData', 'resetLiveData']));
    x.authEnter({ ok: true, user: role(['buildings']), tokens: { access_token: 'a', refresh_token: 'r' } });
    await x.bgLoadCost('b1');
    assert.deepEqual(asked, []);
    assert.equal((x.state.bgCost.b1 || {}).error, undefined);
    assert.equal(!!(x.state.bgCost.b1 || {}).loading, false);
    x.setState({ account: role(['buildings', 'assets']), bgCost: {} });
    await x.bgLoadCost('b1');
    assert.deepEqual(asked, ['b1']);
    assert.deepEqual(x.state.bgCost.b1, { drivers: [{ asset: 'AHU-1' }] });
  } finally { energyApi.costDrivers = real; }
});
