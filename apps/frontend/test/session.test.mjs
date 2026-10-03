// session — the reload slice now carries the refresh token and the account. The access
// token never goes to storage, and a slice from the old demo gate (signedIn but no token)
// must land on the sign-in panel, not pretend to be authenticated.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

// Two SEPARATE backing objects — localStorage (shared across every tab of the origin) and
// sessionStorage (private to this one tab) — the same way a real browser keeps them apart.
const mem = {};
const tabMem = {};
globalThis.window = {
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};

const { loadSession, loadSharedSession, saveSession, SESSION_KEY } = await import('../src/logic/session.js');
const { getActingOrg, setActingOrg } = await import('../src/api/client.js');

const ACCOUNT = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', organization_name: 'TechCorp Facilities LLC', status: 'active', email_verified: true, role: 'user', role_label: 'Facilities manager', last_login_at: null };
const OTHER_ACCOUNT = { id: 'u-2', email: 'plenum-admin@plenum-tech.com', full_name: 'P Admin', organization_id: 'org-2', organization_name: 'Plenum Tech LLC', status: 'active', email_verified: true, role: 'admin', role_label: 'Admin', last_login_at: null };

beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
});

test('a legacy slice with signedIn but no refresh token restores nothing', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, email: 'a@b.c', role: 'admin', view: 'buildings' });
  assert.deepEqual(loadSession(), {});
});

test('refresh token and account restore; the account is reduced to the fields the shell needs', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'home', role: 'user', email: 'sam@example.com' });
  const s = loadSession();
  assert.equal(s.signedIn, true);
  assert.equal(s.refreshToken, 'ref-1');
  assert.deepEqual(s.account, {
    id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', role: 'user', status: 'active',
    organization_id: 'org-1', organization_name: 'TechCorp Facilities LLC', email_verified: true,
    building_ids: null, all_buildings: false, selected_building_id: null, buildings: []
  });
  assert.equal(s.view, 'home');
});

test('a malformed account is dropped but the session survives', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: { id: 1, email: 'x' }, view: 'home' });
  const s = loadSession();
  assert.equal(s.signedIn, true);
  assert.equal(s.account, undefined);
});

// ── building scope (docs/api/building-scope-api.md) — carried only once GET /api/auth/me
//    has answered; login/refresh never send it, so a fresh account is null/false/[]. ──

test('a restricted user\'s allocation and selection round-trip through the account', () => {
  const B1 = '11111111-1111-4111-8111-111111111111', B2 = '22222222-2222-4222-8222-222222222222';
  const restricted = Object.assign({}, ACCOUNT, {
    building_ids: [B1, B2], all_buildings: false, selected_building_id: B1,
    buildings: [{ id: B1, name: 'Riverside Court', building_code: 'B-001' }, { id: B2, name: 'Bishopsgate Tower' }]
  });
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: restricted, view: 'home' });
  const s = loadSession();
  assert.deepEqual(s.account.building_ids, [B1, B2]);
  assert.equal(s.account.selected_building_id, B1);
  assert.deepEqual(s.account.buildings, [{ id: B1, name: 'Riverside Court', building_code: 'B-001' }, { id: B2, name: 'Bishopsgate Tower' }]);
});

test('null building_ids (admin/superadmin — unrestricted) is kept as null, not coerced to []', () => {
  const admin = Object.assign({}, ACCOUNT, { role: 'admin', building_ids: null, all_buildings: true });
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: admin, view: 'home' });
  const s = loadSession();
  assert.equal(s.account.building_ids, null);
  assert.equal(s.account.all_buildings, true);
});

test('an empty building_ids ([], allocated to nothing) is a real answer, not dropped like null', () => {
  const nobody = Object.assign({}, ACCOUNT, { building_ids: [] });
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: nobody, view: 'home' });
  assert.deepEqual(loadSession().account.building_ids, []);
});

test('a malformed buildings list or selected_building_id falls back to defaults, not a dropped account', () => {
  const bad = Object.assign({}, ACCOUNT, { buildings: [{ id: 'b-1' }], selected_building_id: 42 });
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: bad, view: 'home' });
  const s = loadSession();
  assert.equal(s.signedIn, true);
  assert.deepEqual(s.account.buildings, [], 'a building row missing name is not a valid row');
  assert.equal(s.account.selected_building_id, null);
});

test('saveSession writes the refresh token and account and never the access token', () => {
  saveSession({ signedIn: true, email: 'sam@example.com', role: 'user', navOpen: true, view: 'home', refreshToken: 'ref-9', accessToken: 'acc-9', account: ACCOUNT });
  const d = JSON.parse(mem[SESSION_KEY]);
  assert.equal(d.refreshToken, 'ref-9');
  assert.equal(d.account.email, 'sam@example.com');
  assert.equal(d.account.role, 'user');
  assert.equal('accessToken' in d, false);
  assert.equal(JSON.stringify(d).includes('acc-9'), false);
});

test('signing out removes the key', () => {
  mem[SESSION_KEY] = '{"signedIn":true,"refreshToken":"ref-1"}';
  saveSession({ signedIn: false });
  assert.equal(mem[SESSION_KEY], undefined);
});

// ── a reload lands back on the page it was on, not always Home ──

test('users, audit and insp all restore — the three views missing from the allow-list', () => {
  for (const view of ['users', 'audit', 'insp']) {
    mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view, role: 'user' });
    assert.equal(loadSession().view, view, view + ' must restore, not silently fall back to home');
  }
});

test('an unrecognised view still falls back to nothing restored, same as before', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'not-a-real-view', role: 'user' });
  assert.equal(loadSession().view, undefined);
});

const SUPER = Object.assign({}, ACCOUNT, { role: 'superadmin' });

test('saOn restores for a stored superadmin account', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: SUPER, view: 'home', role: 'admin', saOn: true });
  assert.equal(loadSession().saOn, true);
});

test('saOn does NOT restore for a plain account, even if the stored slice claims it', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'home', role: 'user', saOn: true });
  assert.equal(loadSession().saOn, undefined, 'a hand-edited or stale slice must not open the platform console for a non-superadmin');
});

test('saveSession round-trips saOn', () => {
  saveSession({ signedIn: true, email: 'sadie@example.com', role: 'admin', navOpen: true, view: 'home', refreshToken: 'ref-9', account: SUPER, saOn: true });
  const d = JSON.parse(mem[SESSION_KEY]);
  assert.equal(d.saOn, true);
});

// ── viewOrgId: a superadmin's "View as this company" was never persisted at all — a
//    reload silently dropped it and fell back to the account's own company. Reported as:
//    viewing Plenum Tech, reload, back on TechCorp (the account's own company). ──

test('viewOrgId/viewOrgName round-trip through saveSession for a superadmin', () => {
  saveSession({ signedIn: true, email: 'sadie@example.com', role: 'admin', navOpen: true, view: 'home', refreshToken: 'ref-9', account: SUPER, viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  const d = JSON.parse(mem[SESSION_KEY]);
  assert.equal(d.viewOrgId, 'org-plenum');
  assert.equal(d.viewOrgName, 'Plenum Tech LLC');
});

test('a restored viewOrgId survives a reload for a superadmin account', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: SUPER, view: 'home', role: 'admin', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  const s = loadSession();
  assert.equal(s.viewOrgId, 'org-plenum');
  assert.equal(s.viewOrgName, 'Plenum Tech LLC');
});

test('a restored view-as also scopes the API, not just the header label', () => {
  // Reported from the Azure deployment on 17 Sep 2026: the top bar read "Plenum Tech LLC"
  // while the chat answered out of TechCorp's register — a contract (UKRI-2938) belonging
  // to the account's OWN company, not the one named on screen.
  //
  // Two halves have to move together. `viewOrgId`/`viewOrgName` are what the top bar reads;
  // client.js's `actingOrgId` is what actually puts `organization_id` on a request, and it
  // is the only thing workflow.py's _resolve_acting_org ever sees. The reload restored the
  // first and not the second, so the label said one company and every answer came from the
  // other. The bug this file already records was the same split the other way round — the
  // label was dropped and the scope was right. Fixing one half and not the other left them
  // disagreeing, which is worse than both being wrong together: nothing on screen is
  // untrue-looking, so nobody checks.
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: SUPER, view: 'home', role: 'admin', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  loadSession();
  assert.equal(getActingOrg(), 'org-plenum', 'the header says Plenum Tech; every API call must too');
});

test('no stored view-as leaves the API scoped to the account itself', () => {
  setActingOrg('org-stale');
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: SUPER, view: 'home', role: 'admin' });
  loadSession();
  assert.equal(getActingOrg(), null, 'a slice with no view-as must clear a stale override, not inherit it');
});

test('a plain admin\'s hand-edited view-as never reaches the API either', () => {
  setActingOrg(null);
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'home', role: 'admin', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  loadSession();
  assert.equal(getActingOrg(), null, 'view-as is superadmin-only at the API boundary as well as in state');
});

test('viewOrgId does NOT restore for a plain admin, even if the stored slice claims it', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ACCOUNT, view: 'home', role: 'admin', viewOrgId: 'org-plenum', viewOrgName: 'Plenum Tech LLC' });
  const s = loadSession();
  assert.equal(s.viewOrgId, undefined, 'view-as-company is a superadmin-only feature; a hand-edited slice must not grant it');
  assert.equal(s.viewOrgName, undefined);
});

// ── two tabs, two different accounts: the actual bug — a reload must never pick up
//    whichever account some OTHER tab most recently wrote to the shared localStorage ──

test('saveSession writes both stores — this tab\'s own AND the shared one', () => {
  saveSession({ signedIn: true, email: 'sam@example.com', role: 'user', view: 'buildings', refreshToken: 'ref-1', account: ACCOUNT });
  assert.equal(JSON.parse(tabMem[SESSION_KEY]).refreshToken, 'ref-1');
  assert.equal(JSON.parse(mem[SESSION_KEY]).refreshToken, 'ref-1');
});

test('a reload restores THIS tab\'s own account even though a different account owns the shared slot', () => {
  // This tab signed in as ACCOUNT — its own sessionStorage holds it.
  saveSession({ signedIn: true, email: ACCOUNT.email, role: 'user', view: 'buildings', refreshToken: 'ref-1', account: ACCOUNT });
  // A DIFFERENT tab then signed in as OTHER_ACCOUNT and overwrote the shared slot —
  // exactly what a second Hoistra tab on Plenum Tech LLC does today.
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: OTHER_ACCOUNT, view: 'home', role: 'admin' });
  const restored = loadSession();
  assert.equal(restored.account.id, ACCOUNT.id, 'this tab is still ACCOUNT, not whoever last wrote the shared slot');
  assert.equal(restored.refreshToken, 'ref-1');
});

test('a genuinely fresh tab (nothing of its own yet) still inherits whichever account is active elsewhere', () => {
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: OTHER_ACCOUNT, view: 'home', role: 'admin' });
  // tabMem is empty — this tab has never held a session of its own.
  const restored = loadSession();
  assert.equal(restored.account.id, OTHER_ACCOUNT.id);
});

test('loadSharedSession reads the shared slot directly, ignoring this tab\'s own copy', () => {
  saveSession({ signedIn: true, email: ACCOUNT.email, role: 'user', view: 'buildings', refreshToken: 'ref-1', account: ACCOUNT });
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: OTHER_ACCOUNT, view: 'home', role: 'admin' });
  assert.equal(loadSharedSession().account.id, OTHER_ACCOUNT.id);
  assert.equal(loadSession().account.id, ACCOUNT.id, 'loadSession, unlike loadSharedSession, still prefers this tab\'s own copy');
});

test('signing out clears this tab\'s own store always, but leaves the shared slot alone once it belongs to a different account', () => {
  const prev = { signedIn: true, refreshToken: 'ref-1', account: ACCOUNT };
  saveSession(prev, undefined); // this tab was signed in as ACCOUNT, with ref-1 in both stores
  // Meanwhile another tab moved the shared slot on to a different account.
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-9', account: OTHER_ACCOUNT, view: 'home', role: 'admin' });
  saveSession({ signedIn: false }, prev); // this tab signs out
  assert.equal(tabMem[SESSION_KEY], undefined, 'this tab\'s own copy is always cleared on its own sign-out');
  assert.equal(JSON.parse(mem[SESSION_KEY]).account.id, OTHER_ACCOUNT.id, 'the other account\'s active session must survive');
});

test('signing out DOES clear the shared slot when it is still this tab\'s own account', () => {
  const prev = { signedIn: true, refreshToken: 'ref-1', account: ACCOUNT };
  saveSession(prev, undefined);
  saveSession({ signedIn: false }, prev);
  assert.equal(mem[SESSION_KEY], undefined);
});
