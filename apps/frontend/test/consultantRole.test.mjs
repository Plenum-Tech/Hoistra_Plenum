// The Consultant role (23 Nov 2026, Hussain): "access to buildings page only but can access the
// entire data of the platform; it needs to have the sessions and reports running perfectly".
//
// The server reads a consultant as a normal user for data (pages null) and tells the client the
// one screen they open (screens ['buildings']). These pin the client's half: Buildings is the
// only page in the navigator and the only page view they can stand on, while every register is
// still read, every session they ran is listed whatever module answered it, and reports stay.
//
// The real controller in Node, as roleUserSeesOnlyTickedPages.test.mjs runs it.
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
globalThis.fetch = async (url) => {
  asked.push(String(url));
  throw new TypeError('no network in tests: ' + url);
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { configureAuth } = await import('../src/api/client.js');
const A = await import('../src/logic/accessRoles.js');

const USER = { id: 'u-9', email: 'cara@example.com', full_name: 'Cara Mensah', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', email_verified: true, role: 'user' };
const CONSULTANT = Object.assign({}, USER, { access_role: 'consultant', access_role_label: 'Consultant', pages: null, screens: ['buildings'], pages_known: true });
const resp = (user) => ({ ok: true, user, tokens: { access_token: 'acc-1', refresh_token: 'ref-1' } });

const QUIET = ['authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun'];
const fresh = (alsoQuiet) => {
  const x = new HoistraLogic();
  QUIET.concat(alsoQuiet || []).forEach((k) => { x[k] = () => {}; });
  configureAuth(x.authHooks());
  return x;
};
const calm = ['loadLiveData', 'resetLiveData'];
const signIn = (x, user) => { x.authEnter(resp(user)); return x; };
const settle = () => new Promise((r) => setTimeout(r, 60));

beforeEach(() => { Object.keys(mem).forEach((k) => delete mem[k]); Object.keys(tabMem).forEach((k) => delete tabMem[k]); asked.length = 0; });

// ── the rules ───────────────────────────────────────────────────────────────────────────
test('Consultant is one of the roles an admin can give', () => {
  assert.deepEqual(A.ACCESS_ROLES.find((r) => r.key === 'consultant'), { key: 'consultant', label: 'Consultant' });
});

test('a consultant holds every page of data and one screen', () => {
  assert.equal(A.isConsultant(CONSULTANT), true);
  assert.equal(A.isRoleUser(CONSULTANT), true);
  assert.equal(A.pagesOf(CONSULTANT), null);
  for (const p of A.PAGE_KEYS) assert.equal(A.holdsPage(CONSULTANT, p), true, p);
  assert.equal(A.roleReports(CONSULTANT), null);
  assert.deepEqual(A.screensOf(CONSULTANT), ['buildings']);
  assert.equal(A.roleLabel(CONSULTANT), 'Consultant');
});

test('a consultant with no screens field from an older server still opens only Buildings', () => {
  const older = Object.assign({}, CONSULTANT);
  delete older.screens;
  assert.deepEqual(A.screensOf(older), ['buildings']);
});

test('a consultant opens Buildings and the pages ticked for them (23 Nov 2026), Buildings always', () => {
  assert.deepEqual(A.screensOf(Object.assign({}, CONSULTANT, { screens: ['buildings', 'compliance'] })), ['buildings', 'compliance']);
  assert.deepEqual(A.screensOf(Object.assign({}, CONSULTANT, { screens: ['vendors', 'ops'] })), ['buildings', 'vendors', 'ops']);
  assert.deepEqual(A.screensOf(Object.assign({}, CONSULTANT, { screens: ['payroll'] })), ['buildings']);
  // their data is every page's whatever is ticked
  assert.equal(A.pagesOf(Object.assign({}, CONSULTANT, { screens: ['vendors'] })), null);
  const withVendors = Object.assign({}, CONSULTANT, { screens: ['vendors'] });
  assert.equal(A.viewOffLimits(withVendors, 'vp'), false);
  assert.equal(A.viewOffLimits(withVendors, 'module', 'energy'), true);
  assert.equal(A.viewOffLimits(Object.assign({}, CONSULTANT, { screens: ['energy'] }), 'module', 'energy'), false);
});

test('an admin with consultant on the record is still an admin', () => {
  const admin = Object.assign({}, CONSULTANT, { role: 'admin' });
  assert.equal(A.isConsultant(admin), false);
  assert.equal(A.screensOf(admin), null);
});

test('screens of everyone else are their pages', () => {
  assert.equal(A.screensOf(Object.assign({}, USER, { pages: null })), null);
  assert.deepEqual(A.screensOf(Object.assign({}, USER, { access_role: 'general_user', pages: ['energy'] })), ['energy']);
});

test('a consultant may send every request and press every button', () => {
  const bases = { ops: 'O', wo: 'W', con: 'C' };
  assert.equal(A.requestHeld(CONSULTANT, 'O', '/api/compliance/certificates', bases), true);
  assert.equal(A.requestHeld(CONSULTANT, 'W', '/api/work-orders', bases), true);
  assert.equal(A.canAct(CONSULTANT, 'assetWorkOrder'), true);
});

// ── the app ─────────────────────────────────────────────────────────────────────────────
test('a consultant is not offered the instance picker and the top bar names the role', () => {
  const x = signIn(fresh(calm), CONSULTANT);
  assert.equal(x.state.view, 'home');
  const v = x.renderVals();
  assert.equal(v.isPicker, false);
  assert.equal(v.instName, 'Consultant');
  assert.equal(v.instStatic, true);
});

test('the navigator lists Buildings only', () => {
  const v = signIn(fresh(calm), CONSULTANT).renderVals();
  assert.deepEqual((v.navSections || []).map((n) => n.key), ['buildings_user']);
});

test('the built-in spaces of other pages are not listed; Support is', () => {
  const v = signIn(fresh(calm), CONSULTANT).renderVals();
  const keys = (v.navSpaces || []).map((sp) => sp.key);
  for (const k of ['compliance', 'energy', 'vendors', 'ops']) assert.equal(keys.indexOf(k), -1, k);
  assert.ok(keys.indexOf('support') > -1, 'support stays: ' + keys.join(','));
});

test('every page view but Buildings is Home; chat, sessions and reports stay', () => {
  const x = signIn(fresh(calm), CONSULTANT);
  for (const patch of [{ view: 'cc' }, { view: 'hg' }, { view: 'vp' }, { view: 'vh' }, { view: 'insp' },
    { view: 'module', module: 'energy' }, { view: 'module', module: 'assets' }, { view: 'module', module: 'ops' },
    { view: 'space', spaceKey: 'compliance' }, { view: 'instances' }]) {
    x.setState(patch);
    assert.equal(x.state.view, 'home', JSON.stringify(patch));
  }
  for (const view of ['buildings', 'chat', 'sessions', 'report']) {
    x.setState({ view });
    assert.equal(x.state.view, view);
  }
});

test('every session the consultant ran is listed, whichever module answered it', () => {
  const x = signIn(fresh(calm), CONSULTANT);
  const at = Date.now();
  x.setState({ sessions: [
    { id: 's-1', kind: 'chat', title: 'Which certificates lapse in March?', owner: 'cara@example.com', domain: 'Compliance', page: 'Home', createdAt: at, at },
    { id: 's-2', kind: 'chat', title: 'Energy spikes at B-101', owner: 'cara@example.com', domain: 'Energy', page: 'Buildings', createdAt: at, at },
    { id: 's-3', kind: 'chat', title: 'Someone else', owner: 'other@example.com', domain: 'Energy', page: 'Home', createdAt: at, at }
  ] });
  const titles = (x.renderVals().navSessions || []).map((q) => q.label);
  assert.ok(titles.indexOf('Which certificates lapse in March?') > -1, titles.join(' | '));
  assert.ok(titles.indexOf('Energy spikes at B-101') > -1, titles.join(' | '));
  assert.equal(titles.indexOf('Someone else'), -1);
});

test('a consultant\'s browser reads every register, as a normal user\'s does', async () => {
  signIn(fresh(), CONSULTANT);       // loadLiveData runs for real
  await settle();
  assert.ok(asked.some((u) => u.indexOf('/api/compliance/certificates') > -1), 'compliance is read');
  assert.ok(asked.some((u) => u.indexOf('/api/energy/buildings') > -1), 'building names are read');
});
