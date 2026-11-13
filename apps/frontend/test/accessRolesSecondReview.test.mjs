// Access roles: the second review's findings (13 Nov 2026), each pinned before its fix.
//
// The real controller in Node. `fetch` records what would have gone out and answers from a
// small table; nothing reaches a network.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
const listeners = {};
globalThis.window = {
  location: { origin: 'http://test.local', search: '', pathname: '/' }, scrollTo: () => {},
  addEventListener: (k, fn) => { (listeners[k] = listeners[k] || []).push(fn); },
  removeEventListener: (k, fn) => { listeners[k] = (listeners[k] || []).filter((f) => f !== fn); },
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
const asked = [];
const json = (body) => new Response(JSON.stringify(body), { status: 200, headers: { 'content-type': 'application/json' } });
globalThis.fetch = async (url) => {
  const u = String(url);
  asked.push(u.split('?')[0].replace(/^.*\/backend/, ''));
  if (/\/work-order\/api\/assets/.test(u)) return json([{ asset_id: 'a1', asset_name: 'Chiller 1', building_id: 'b1' }, { asset_id: 'a2', asset_name: 'Boiler 2', building_id: 'b1' }]);
  if (/\/work-order\/api\/work-orders/.test(u)) return json([]);
  return json({});
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { configureAuth, getActiveSpace } = await import('../src/api/client.js');
const { adminApi } = await import('../src/api/admin.js');
const A = await import('../src/logic/accessRoles.js');

const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', email_verified: true, role: 'user' };
const NORMAL = Object.assign({}, USER, { access_role: null, pages: null, pages_known: true });
const role = (pages, over) => Object.assign({}, USER, { access_role: 'general_user', access_role_label: 'General User', pages, pages_known: true }, over || {});
const OFFICER = role(['buildings', 'compliance'], { access_role: 'compliance_officer', access_role_label: 'Compliance Officer' });
const ENERGY = role(['energy']);
const resp = (user) => ({ ok: true, user, tokens: { access_token: 'acc-1', refresh_token: 'ref-1' } });
const QUIET = ['authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun'];
const calm = ['loadLiveData', 'resetLiveData'];
const fresh = (alsoQuiet) => {
  const x = new HoistraLogic();
  QUIET.concat(alsoQuiet || []).forEach((k) => { x[k] = () => {}; });
  configureAuth(x.authHooks());
  return x;
};
const signIn = (x, user) => { x.authEnter(resp(user)); return x; };
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 60));
const REFUSAL = /not part of the pages/;

beforeEach(() => {
  Object.keys(mem).forEach((k) => delete mem[k]);
  Object.keys(tabMem).forEach((k) => delete tabMem[k]);
  Object.keys(listeners).forEach((k) => delete listeners[k]);
  asked.length = 0;
});

// ── 1 ───────────────────────────────────────────────────────────────────────────────────
test('1: Assets without Maintenance still loads the asset register, without its work orders', async () => {
  const x = signIn(fresh(calm), role(['assets']));
  await x.asLiveLoad();
  clearTimeout(x._asLiveRetry);
  assert.equal(x.state.asLiveError, '');
  assert.equal((x.state.asLive || []).length, 2);
  assert.deepEqual(x.state.asLiveWos, []);
  assert.equal(asked.some((a) => /work-orders/.test(a)), false, 'the work orders are not asked for');
  assert.equal(REFUSAL.test(x.renderVals().asEmptyText || ''), false);
});

test('1: with Maintenance, and for a normal user, the work orders are read as before', async () => {
  for (const who of [role(['assets', 'ops']), NORMAL]) {
    asked.length = 0;
    const x = signIn(fresh(calm), who);
    await x.asLiveLoad();
    assert.equal(asked.some((a) => /work-orders/.test(a)), true, JSON.stringify(who.pages));
  }
});

// ── 2 ───────────────────────────────────────────────────────────────────────────────────
test('2: the Compliance Graph is drawn for a role user who holds Compliance', () => {
  const x = signIn(fresh(calm), OFFICER);
  x.renderVals().navSections.find((n) => n.key === 'hoist_graph').click();
  assert.equal(x.state.view, 'hg');
  assert.equal(x.renderVals().isHG, true);
});

// ── 3 ───────────────────────────────────────────────────────────────────────────────────
test('3: a reload keeps the role and pages: no picker, no bounce, no reads for other pages', async () => {
  const x = signIn(fresh(calm), OFFICER);
  x.setState({ view: 'cc' });
  const y = fresh(calm);
  assert.deepEqual(y.state.account.pages, ['buildings', 'compliance']);
  assert.equal(y.state.account.access_role, 'compliance_officer');
  assert.equal(y.state.view, 'cc');
  assert.equal(y.renderVals().isPicker, false);
  y.authEnter(resp(OFFICER), { keepView: true });
  assert.equal(y.state.view, 'cc');
});

test('3: a reloaded role user\'s mount reads ask for no other page\'s register', async () => {
  signIn(fresh(calm), ENERGY);
  asked.length = 0;
  const y = fresh(calm);
  // The page loaders the mount runs; each is held back for an account without its page.
  await Promise.all(['ccLoad', 'vpLoad', 'vmLoad', 'vbLoad', 'asLiveLoad', 'asCondLoad', 'mxLiveLoad'].map((k) => y[k]()));
  const other = asked.filter((a) => /\/api\/compliance\/|contract-performance|\/work-order\/|\/connector\/|energy\/condition|energy\/assets/.test(a));
  assert.deepEqual(other, []);
});

test('3: a stored view whose page is not held opens as Home on reload', () => {
  const x = signIn(fresh(calm), ENERGY);
  x.setState({ view: 'module', module: 'energy' });
  // A slice written before the role was given: the view is the Compliance page.
  const k = Object.keys(tabMem)[0];
  const slice = JSON.parse(tabMem[k]);
  tabMem[k] = JSON.stringify(Object.assign(slice, { view: 'cc', module: null }));
  const y = fresh(calm);
  assert.equal(y.state.view, 'home');
});

test('3: a normal user\'s stored account is the shape it was', () => {
  signIn(fresh(calm), USER);
  const y = fresh(calm);
  assert.equal('pages' in y.state.account, false);
  assert.equal('access_role' in y.state.account, false);
  assert.equal(y.state.view, 'instances');
});

// ── 4 ───────────────────────────────────────────────────────────────────────────────────
test('4: an account given a role mid-session leaves its instance and space behind', () => {
  const x = signIn(fresh(calm), NORMAL);
  x.instPick('ops');
  assert.equal(getActiveSpace(), 'ops');
  x.authEnter(resp(OFFICER), { keepView: true });
  assert.equal(x.state.instance, null);
  assert.equal(getActiveSpace(), null);
});

test('4: a normal user keeps the instance they picked', () => {
  const x = signIn(fresh(calm), NORMAL);
  x.instPick('ops');
  x.authEnter(resp(NORMAL), { keepView: true });
  assert.equal(x.state.instance, 'ops');
  assert.equal(getActiveSpace(), 'ops');
});

// ── 5 ───────────────────────────────────────────────────────────────────────────────────
test('5: the navigator lists only the spaces of held pages, and Support', () => {
  const x = signIn(fresh(calm), ENERGY);
  assert.deepEqual((x.renderVals().navSpaces || []).map((s) => s.key), ['energy', 'support']);
});

test('5: the space of a page not held is Home; a held one, and Support, open', () => {
  const x = signIn(fresh(calm), ENERGY);
  x.setState({ view: 'space', spaceKey: 'compliance' });
  assert.equal(x.state.view, 'home');
  x.setState({ view: 'space', spaceKey: 'energy' });
  assert.equal(x.state.view, 'space');
  x.setState({ view: 'space', spaceKey: 'support' });
  assert.equal(x.state.view, 'space');
});

test('5: a normal user\'s spaces are as before', () => {
  const x = signIn(fresh(calm), NORMAL);
  x.instPick('full');
  assert.deepEqual((x.renderVals().navSpaces || []).map((s) => s.key), ['compliance', 'energy', 'vendors', 'ops', 'support']);
});

// ── 6 ───────────────────────────────────────────────────────────────────────────────────
test('6: a page refusal is never printed: the Assets page without Energy shows no refusal', async () => {
  const x = signIn(fresh(calm), role(['assets', 'ops']));
  await x.asLiveLoad();
  await x.asCondLoad();
  ['asAssetMetersError', 'asAnomsError', 'asInspectionsError', 'asSectionsError', 'asLocationsError', 'asVarError'].forEach((k) => {
    assert.equal(REFUSAL.test(String(x.state[k] || '')), false, k);
  });
  x.setState({ view: 'module', module: 'assets' });
  const v = x.renderVals();
  const shown = Object.keys(v).filter((k) => typeof v[k] !== 'function' && REFUSAL.test(JSON.stringify(v[k]) || ''));
  assert.deepEqual(shown, []);
});

test('6: client.js names a page refusal so a caller can leave the part out', async () => {
  const { isPageRefusal, ApiError } = await import('../src/api/client.js');
  const e = new ApiError('x', 403, { detail: { ok: false, reason: 'page_not_allowed', page: 'energy' } });
  assert.equal(isPageRefusal(e), true);
  assert.equal(isPageRefusal(new ApiError('x', 403, { detail: { reason: 'forbidden' } })), false);
  assert.equal(isPageRefusal(new Error('timeout')), false);
});

// ── 7 ───────────────────────────────────────────────────────────────────────────────────
const ADMIN = { id: 'adm', email: 'ada@example.com', full_name: 'Ada Admin', organization_id: 'org-1', role: 'admin', status: 'active', pages: null, pages_known: true };
const U1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const row = (over) => Object.assign({ id: U1, name: 'Sam Okafor', email: 'sam@example.com', title: 'FM', buildings: ['Harbour Point'], allB: false, ingest: false, status: 'Active', role: 'user', queries: 0, ingests: 0, last: '—', live: true, accessRole: null, accessRoleLabel: null, pages: null }, over || {});
function usersPage(users) {
  const x = fresh(calm.concat(['usLiveLoad']));
  x.flashes = [];
  x.flash = (m) => { x.flashes.push(String(m)); };
  x.setState({ signedIn: true, account: ADMIN, role: 'admin', view: 'users', usLiveLoadedAt: Date.now(),
    axBldsLive: [{ id: 'b1', name: 'Harbour Point' }], users: users || [row()], usInviteOpen: true });
  return x;
}
const person = (x) => x.renderVals().axUsers.find((u) => u.id === U1);

test('7: a server that did not record the role is said to have not recorded it', async () => {
  // The build before access roles answers 200 and changes nothing.
  adminApi.patchUser = async (id) => ({ ok: true, user_id: id, changed: {} });
  const x = usersPage();
  await x.usLiveSetAccess(row(), 'general_user', ['energy']);
  const u = x.state.users.find((r) => r.id === U1);
  assert.equal(u.accessRole, null);
  assert.equal(u.pages, null);
  assert.match(x.flashes.join(' | '), /not recorded/i);
  assert.doesNotMatch(x.flashes.join(' | '), /now opens only/);
});

test('7: a server that recorded it is believed', async () => {
  adminApi.patchUser = async (id, body) => ({ ok: true, user_id: id, changed: { access_role: body.access_role, pages: body.pages } });
  const x = usersPage();
  await x.usLiveSetAccess(row(), 'general_user', ['energy']);
  const u = x.state.users.find((r) => r.id === U1);
  assert.equal(u.accessRole, 'general_user');
  assert.deepEqual(u.pages, ['energy']);
  assert.match(x.flashes.join(' | '), /now opens only: Energy/);
});

test('7: an invitation the server recorded without its role says so', async () => {
  adminApi.inviteUser = async (body) => ({ ok: true, user_id: 'new-1', email: body.email, can_ingest: false, buildings: [] });
  const x = usersPage();
  x.setState({ usName: 'Kim', usEmail: 'kim@example.com', usBlds: [], usRole: 'general_user', usPages: ['energy'] });
  await x.usLiveInvite();
  assert.match(x.flashes.join(' | '), /role was not recorded/i);
});

// ── 8 ───────────────────────────────────────────────────────────────────────────────────
test('8: conversations from before an access narrowing are not listed for a role user', () => {
  const cut = '2026-11-13T10:00:00Z';
  const x = signIn(fresh(calm), role(['energy'], { access_narrowed_at: cut }));
  const before = Date.parse(cut) - 3600e3, after = Date.parse(cut) + 3600e3;
  const rec = (id, at) => ({ id, kind: 'chat', title: id, label: id, task: id, owner: USER.email, viewOrgId: null, at, createdAt: at, turns: [], calls: [], domain: 'Orchestrator', page: 'Home' });
  x.setState({ sessions: [rec('old', before), rec('new', after)] });
  assert.deepEqual(x.renderVals().navSessions.map((s) => s.label || s.title || s.name).filter(Boolean).length, 1);
  assert.equal(A.sessionVisible(x.state.account, rec('old', before)), false);
  assert.equal(A.sessionVisible(x.state.account, rec('new', after)), true);
  assert.equal(A.sessionVisible(NORMAL, rec('old', before)), true);
});

test('8: /me carries the narrowing time onto the account', async () => {
  const authMod = await import('../src/api/auth.js');
  const real = authMod.authApi.me;
  authMod.authApi.me = async () => ({ ok: true, user: Object.assign({}, ENERGY, { access_narrowed_at: '2026-11-13T10:00:00Z', buildings: [] }) });
  try {
    const x = new HoistraLogic();
    QUIET.filter((k) => k !== 'authLoadScope').concat(calm).forEach((k) => { x[k] = () => {}; });
    configureAuth(x.authHooks());
    x.authEnter(resp(ENERGY));
    await x.authLoadScope();
    await settle();
    assert.equal(x.state.account.access_narrowed_at, '2026-11-13T10:00:00Z');
  } finally { authMod.authApi.me = real; }
});

// ── 9 ───────────────────────────────────────────────────────────────────────────────────
test('9: a role user\'s pages are re-read when the tab comes back into view, at most once a minute', () => {
  const x = signIn(fresh(calm), ENERGY);
  let reads = 0;
  x.authLoadScope = () => { reads += 1; return Promise.resolve(); };
  x.authRecheckPages();
  x.authRecheckPages();
  assert.equal(reads, 1);
  x._pagesCheckedAt = Date.now() - 61000;
  x.authRecheckPages();
  assert.equal(reads, 2);
});

test('9: a normal user is not re-read on focus', () => {
  const x = signIn(fresh(calm), NORMAL);
  let reads = 0;
  x.authLoadScope = () => { reads += 1; return Promise.resolve(); };
  x.authRecheckPages();
  assert.equal(reads, 0);
});

test('9: authBoot listens for the tab coming back; authStop stops', () => {
  const x = fresh(calm.filter((k) => k !== 'authStop').concat([]));
  x.authStop = HoistraLogic.prototype.authStop;
  x.authRefresh = () => Promise.resolve();
  x.authLoadConfig = () => {};
  x.authBoot();
  assert.equal((listeners.focus || []).length, 1);
  x.authStop();
  assert.equal((listeners.focus || []).length, 0);
});

// ── 10 ──────────────────────────────────────────────────────────────────────────────────
test('10: the role chip never reads null or undefined, and takes the server\'s label', () => {
  assert.equal(A.roleLabel(Object.assign({}, USER, { pages: ['energy'] })), 'Limited access');
  assert.equal(A.roleLabel(Object.assign({}, USER, { access_role: null, pages: ['energy'] })), 'Limited access');
  assert.equal(A.roleLabel(Object.assign({}, USER, { access_role: 'estate_lead', access_role_label: 'Estate Lead', pages: ['energy'] })), 'Estate Lead');
  assert.equal(A.roleLabel(OFFICER), 'Compliance Officer');
  assert.equal(A.roleLabel(NORMAL), null);
});

// ── 11 ──────────────────────────────────────────────────────────────────────────────────
test('11: pages without a role are said, and No role lifts them', async () => {
  const sent = [];
  adminApi.patchUser = async (id, body) => { sent.push(body); return { ok: true, user_id: id, changed: { access_role: null, pages: null } }; };
  const x = usersPage([row({ accessRole: null, pages: ['energy'] })]);
  const u = person(x);
  assert.doesNotMatch(u.accessNote, /normal user and opens every page/);
  assert.match(u.accessNote, /Energy/);
  u.roleChips.find((c) => c.key === '').pick();
  await settle();
  assert.deepEqual(sent, [{ access_role: null }]);
  assert.equal(x.state.users[0].pages, null);
});
