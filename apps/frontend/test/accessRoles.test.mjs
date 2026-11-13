// Access roles (12 Nov 2026). An admin may give a user a role and tick the pages that user
// may open; from then on only those pages - and only their data - reach them. The server is
// what enforces it (every service refuses data owned by a page the caller does not hold);
// these are the client's own rules, so that what is not held is absent rather than broken:
// which pages a signed-in account holds, which loaders may run, and which requests are never
// sent at all.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const A = await import('../src/logic/accessRoles.js');

const NORMAL = { id: 'u1', role: 'user', access_role: null, pages: null };
const ADMIN = { id: 'u2', role: 'admin', access_role: null, pages: null };
const role = (pages, key) => ({ id: 'u3', role: 'user', access_role: key || 'compliance_officer', pages });
const OFFICER = role(['compliance', 'buildings']);

test('the four roles and the six pages are exactly these, in this order', () => {
  assert.deepEqual(A.ACCESS_ROLES.map((r) => [r.key, r.label]), [
    ['portfolio_management', 'Portfolio Management'], ['property_manager', 'Property Manager'],
    ['general_user', 'General User'], ['compliance_officer', 'Compliance Officer']]);
  assert.deepEqual(A.PAGE_OPTIONS.map((p) => [p.key, p.label, p.hint || '']), [
    ['buildings', 'Buildings', ''], ['compliance', 'Compliance', ''], ['energy', 'Energy', ''],
    ['vendors', 'Vendors', 'Vendor performance'], ['ops', 'Maintenance', 'Vendor operations'],
    ['assets', 'Assets', '']]);
});

test('a role user is a plain user with an access role', () => {
  assert.equal(A.isRoleUser(OFFICER), true);
  assert.equal(A.isRoleUser(NORMAL), false);
  assert.equal(A.isRoleUser(ADMIN), false);
  assert.equal(A.isRoleUser(null), false);
  // An admin is never held to pages, whatever the record says.
  assert.equal(A.isRoleUser({ role: 'admin', access_role: 'general_user', pages: ['energy'] }), false);
  assert.equal(A.isRoleUser({ role: 'superadmin', access_role: 'general_user', pages: ['energy'] }), false);
});

test('the pages of an account: null is every page, a list is only those', () => {
  assert.equal(A.pagesOf(NORMAL), null);
  assert.equal(A.pagesOf(ADMIN), null);
  assert.equal(A.pagesOf(null), null);
  assert.deepEqual(A.pagesOf(OFFICER), ['buildings', 'compliance']);
});

test('reading the pages fails closed', () => {
  // A role with nothing readable behind it holds nothing; it is never read as every page.
  assert.deepEqual(A.pagesOf(role(null)), []);
  assert.deepEqual(A.pagesOf(role(undefined)), []);
  assert.deepEqual(A.pagesOf(role('compliance')), []);
  assert.deepEqual(A.pagesOf(role(['compliance', 'payroll', 7])), ['compliance']);
});

test('the reports a role user sees: their pages, and the Compliance Graph with Compliance', () => {
  assert.equal(A.roleReports(NORMAL), null);
  assert.equal(A.roleReports(ADMIN), null);
  assert.deepEqual(A.roleReports(OFFICER), ['buildings', 'compliance', 'graph']);
  assert.deepEqual(A.roleReports(role(['energy', 'assets'])), ['energy', 'assets']);
  assert.deepEqual(A.roleReports(role([])), []);
});

test('holding a page', () => {
  assert.equal(A.holdsPage(NORMAL, 'energy'), true);
  assert.equal(A.holdsPage(ADMIN, 'energy'), true);
  assert.equal(A.holdsPage(null, 'energy'), true);
  assert.equal(A.holdsPage(OFFICER, 'compliance'), true);
  assert.equal(A.holdsPage(OFFICER, 'energy'), false);
  assert.equal(A.holdsAny(OFFICER, ['vendors', 'compliance']), true);
  assert.equal(A.holdsAny(OFFICER, ['vendors', 'energy']), false);
  assert.equal(A.holdsAll(OFFICER, ['buildings', 'compliance']), true);
  assert.equal(A.holdsAll(OFFICER, ['buildings', 'ops']), false);
  assert.equal(A.holdsAll(NORMAL, ['buildings', 'ops']), true);
});

test('the role label', () => {
  assert.equal(A.roleLabel(OFFICER), 'Compliance Officer');
  assert.equal(A.roleLabel(role(['energy'], 'property_manager')), 'Property Manager');
  assert.equal(A.roleLabel(NORMAL), null);
  // A role this build has not heard of still restricts; it is shown by its key.
  assert.equal(A.roleLabel(role(['energy'], 'night_porter')), 'night_porter');
});

test('ticks are stored in page order, once each, known pages only', () => {
  assert.deepEqual(A.cleanPages(['assets', 'compliance', 'assets', 'buildings', 'x']), ['buildings', 'compliance', 'assets']);
  assert.deepEqual(A.cleanPages(null), []);
  assert.deepEqual(A.togglePage(['compliance'], 'buildings'), ['buildings', 'compliance']);
  assert.deepEqual(A.togglePage(['buildings', 'compliance'], 'compliance'), ['buildings']);
});

test('whether the account\'s pages are known yet', () => {
  assert.equal(A.pagesKnown({ role: 'user' }), false);          // signed in, scope not read yet
  assert.equal(A.pagesKnown({ role: 'user', pages: null }), true);
  assert.equal(A.pagesKnown({ role: 'user', pages: ['energy'] }), true);
  assert.equal(A.pagesKnown({ role: 'user', pages_known: true }), true);
  assert.equal(A.pagesKnown({ role: 'admin' }), true);          // an admin has every page
  assert.equal(A.pagesKnown(null), true);
});

// ── the view a page lives on ────────────────────────────────────────────────────────────
test('each page view names the page that owns it', () => {
  assert.equal(A.viewPage('cc'), 'compliance');
  assert.equal(A.viewPage('hg'), 'compliance');
  assert.equal(A.viewPage('vp'), 'vendors');
  assert.equal(A.viewPage('vh'), 'vendors');
  assert.equal(A.viewPage('buildings'), 'buildings');
  assert.equal(A.viewPage('insp'), 'ops');
  assert.equal(A.viewPage('module', 'energy'), 'energy');
  assert.equal(A.viewPage('module', 'assets'), 'assets');
  assert.equal(A.viewPage('module', 'ops'), 'ops');
  assert.equal(A.viewPage('module', 'compliance'), 'compliance');
  assert.equal(A.viewPage('home'), null);
  assert.equal(A.viewPage('chat'), null);
  assert.equal(A.viewPage('sessions'), null);
});

test('a role user is sent Home from a view they do not hold, and from the picker', () => {
  assert.equal(A.viewOffLimits(OFFICER, 'vp'), true);
  assert.equal(A.viewOffLimits(OFFICER, 'module', 'energy'), true);
  assert.equal(A.viewOffLimits(OFFICER, 'instances'), true);
  assert.equal(A.viewOffLimits(OFFICER, 'cc'), false);
  assert.equal(A.viewOffLimits(OFFICER, 'hg'), false);
  assert.equal(A.viewOffLimits(OFFICER, 'buildings'), false);
  assert.equal(A.viewOffLimits(OFFICER, 'home'), false);
  assert.equal(A.viewOffLimits(OFFICER, 'chat'), false);
  for (const acct of [NORMAL, ADMIN, null]) {
    assert.equal(A.viewOffLimits(acct, 'vp'), false);
    assert.equal(A.viewOffLimits(acct, 'instances'), false);
  }
});

// ── loaders ─────────────────────────────────────────────────────────────────────────────
test('a guarded loader does not run for a page the account does not hold', async () => {
  const ran = [];
  const proto = {};
  for (const name of Object.keys(A.LOADER_PAGES)) proto[name] = function (x) { ran.push(name); return 'ran:' + (x || ''); };
  proto.homeLoad = function () { ran.push('homeLoad'); return 'home'; };
  A.guardLoaders(proto);
  const me = Object.assign(Object.create(proto), { state: { account: OFFICER } });
  assert.equal(me.ccLoad('a'), 'ran:a');
  assert.equal(await me.energyLoad(), undefined);          // skipped, and still awaitable
  assert.equal(typeof me.energyLoad().then, 'function');
  me.vpLoad(); me.vmLoad(); me.asLiveLoad(); me.asCondLoad(); me.mxLiveLoad(); me.enPositionLoad();
  me.vbLoad();                                              // vendors OR compliance
  me.homeLoad();                                            // not a page loader: untouched
  assert.deepEqual(ran, ['ccLoad', 'vbLoad', 'homeLoad']);
});

test('every loader runs for an account with every page', () => {
  const ran = [];
  const proto = {};
  for (const name of Object.keys(A.LOADER_PAGES)) proto[name] = function () { ran.push(name); };
  A.guardLoaders(proto);
  for (const acct of [NORMAL, ADMIN, null]) {
    ran.length = 0;
    const me = Object.assign(Object.create(proto), { state: { account: acct } });
    for (const name of Object.keys(A.LOADER_PAGES)) me[name]();
    assert.deepEqual(ran, Object.keys(A.LOADER_PAGES));
  }
});

test('guarding twice wraps once', () => {
  let n = 0;
  const proto = { ccLoad() { n += 1; } };
  A.guardLoaders(proto);
  const once = proto.ccLoad;
  A.guardLoaders(proto);
  assert.equal(proto.ccLoad, once);
  Object.assign(Object.create(proto), { state: { account: NORMAL } }).ccLoad();
  assert.equal(n, 1);
});

// ── requests that are never sent ────────────────────────────────────────────────────────
const B = { ops: '/backend/ops-intelligence', wo: '/backend/work-order', con: '/backend/connector', da: '/backend/deep-agents' };
const need = (base, path) => A.pagesForRequest(base, path, B);

test('a request names the page that owns it', () => {
  assert.deepEqual(need(B.ops, '/api/compliance/certificates'), ['compliance']);
  assert.deepEqual(need(B.ops, '/api/compliance/coverage/vendors'), ['vendors', 'compliance']);   // the Blocked state
  assert.deepEqual(need(B.ops, '/api/compliance/coverage/buildings'), ['compliance']);
  assert.deepEqual(need(B.ops, '/api/compliance/vendor-buildings'), ['vendors', 'compliance']);
  assert.deepEqual(need(B.ops, '/api/compliance/vendors/V-1/buildings'), ['vendors', 'compliance']);
  assert.deepEqual(need(B.ops, '/api/contract-performance/contracts'), ['vendors']);
  assert.deepEqual(need(B.ops, '/api/contract-performance/wo-scores'), ['vendors']);
  assert.deepEqual(need(B.ops, '/api/energy/anomalies'), ['energy']);
  assert.deepEqual(need(B.ops, '/api/energy/anomalies/rollup'), ['energy']);
  assert.deepEqual(need(B.ops, '/api/energy/meters/by-floor'), ['energy']);
  assert.deepEqual(need(B.ops, '/api/energy/ratings/position'), ['energy']);
  assert.deepEqual(need(B.ops, '/api/energy/condition/assets'), ['assets']);
  assert.deepEqual(need(B.ops, '/api/energy/assets/a1/intelligence'), ['assets']);
  assert.deepEqual(need(B.ops, '/api/energy/assets/value-at-risk'), ['assets']);
  assert.deepEqual(need(B.ops, '/api/energy/graph/shape'), ['buildings']);
  assert.deepEqual(need(B.ops, '/api/energy/buildings/b1/graph'), ['buildings']);
  assert.deepEqual(need(B.wo, '/api/maintenance/overview'), ['ops']);
  assert.deepEqual(need(B.wo, '/api/work-orders/'), ['ops']);
  assert.deepEqual(need(B.wo, '/api/assets'), ['assets']);
  assert.deepEqual(need(B.con, '/api/v1/plenum/vendors'), ['vendors']);
  assert.deepEqual(need(B.con, '/api/v1/plenum/vendor-contacts'), ['vendors']);
});

test('a request the server opens to everyone, or narrows itself, is not held back', () => {
  for (const [base, path] of [
    [B.ops, '/api/energy/buildings'], [B.ops, '/api/energy/hoist-score'], [B.ops, '/api/energy/buildings/resolve'],
    // A building's cost drivers left this list in review: they are the asset register's
    // (accessRolesReviewFixes.test.mjs).
    [B.ops, '/api/energy/sections'],
    [B.ops, '/api/energy/weather/degree-days'], [B.ops, '/api/energy/readings/ingest'],
    [B.ops, '/api/approvals'], [B.ops, '/api/value/summary'], [B.ops, '/api/reports'], [B.ops, '/api/crons'],
    [B.ops, '/api/documents'], [B.ops, '/api/auth/me'], [B.ops, '/api/admin/users'],
    [B.ops, '/api/compliance/documents/validate'], [B.ops, '/api/compliance/forensics'],
    [B.ops, '/api/contract-performance/migration/m1/workbook-extras'], [B.ops, '/api/contract-performance/contracts/ingest'],
    [B.ops, '/api/contract-performance/asset-criticality'], [B.ops, '/api/ingestion/cases'],
    [B.wo, '/api/locations'], [B.da, '/api/threads'], [B.da, '/api/workflow/run-stateful'],
  ]) assert.equal(need(base, path), null, path);
});

test('whether a request may go out', () => {
  assert.equal(A.requestHeld(OFFICER, B.ops, '/api/compliance/certificates', B), true);
  assert.equal(A.requestHeld(OFFICER, B.ops, '/api/energy/anomalies', B), false);
  assert.equal(A.requestHeld(OFFICER, B.ops, '/api/compliance/vendor-buildings', B), true);
  assert.equal(A.requestHeld(OFFICER, B.ops, '/api/energy/buildings', B), true);
  assert.equal(A.requestHeld(role(['energy']), B.ops, '/api/compliance/vendor-buildings', B), false);
  for (const acct of [NORMAL, ADMIN, null]) assert.equal(A.requestHeld(acct, B.ops, '/api/energy/anomalies', B), true);
});

test('a path with a query string is read by its path', () => {
  assert.deepEqual(need(B.ops, '/api/compliance/certificates?cert_scope=vendor'), ['compliance']);
  assert.equal(need(B.ops, '/api/energy/buildings?limit=5'), null);
});
