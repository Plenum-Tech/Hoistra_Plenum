// Delete vendor (Hussain, 12 Oct 2026). From the selected vendor's panel on the Vendors page an
// admin — or a superadmin viewing as a company — deletes a vendor. A vendor with no history is
// removed completely; one with history is retired: hidden everywhere, its past records kept and
// marked (deleted). It cannot be undone, so the dialog reads what the delete would change first
// and the vendor's name must be typed before Delete vendor does anything. The server checks
// the name and the mode again; these tests hold the page to its side of that.
import { test, beforeEach, afterEach } from 'node:test';
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
// Every request is recorded; only the routes a test answers get an answer, the rest fail the
// way an unreachable network does.
const calls = [];
let handlers = {};
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  calls.push({ key: key, query: Object.fromEntries(u.searchParams), body: opts && opts.body ? JSON.parse(opts.body) : null });
  const h = handlers[key];
  if (!h) throw new TypeError('Failed to fetch');
  const [status, body] = typeof h === 'function' ? await h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(body) };
};
const settle = () => new Promise((r) => setTimeout(r, 30));
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const client = await import('../src/api/client.js');
const { VH_STORE_KEY } = await import('../src/logic/vendorHoist.js');

const ORG = { organization_id: 'org-1', organization_name: 'Northbridge' };
const USER = Object.assign({ id: 'u-1', email: 'sam@example.com', full_name: 'Sam', status: 'active', role: 'user' }, ORG);
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin' });
const SUPER = Object.assign({}, USER, { id: 'u-3', email: 'sadie@example.com', full_name: 'Sadie', role: 'superadmin' });

// A legacy id, as half of production's vendors carry, and two uuids.
const ACME = 'V-01';
const BETA = '5c1e2d3f-0000-4000-8000-0000000000b2';
const GAMMA = '5c1e2d3f-0000-4000-8000-0000000000c3';
const OI = '/backend/ops-intelligence/api/admin/vendors/';
const PLAN = 'GET ' + OI + ACME + '/delete';
const DEL = 'POST ' + OI + ACME + '/delete';

// The directory, newest month first and lowest score first: Acme, Beta, Gamma.
const card = (id, name, score) => ({ vendor_id: id, vendor_name: name, score_month: '2026-09-01', overall_score: score, component_breakdown: {} });
const RAW = {
  fetchedAt: '2026-10-12T08:00:00Z', errors: {},
  summary: { scorecards: [card(ACME, 'Acme Ltd', 72), card(BETA, 'Beta Lifts', 88), card(GAMMA, 'Gamma Gas', 91)] },
  contracts: { parameters: [] }, certificates: []
};

const asset = (n) => ({ id: 'a' + n, name: 'Boiler ' + n, code: 'B-30' + n });
const RETIRE = {
  ok: true, applied: false,
  vendor: { id: ACME, name: 'Acme Ltd', code: 'ACME', trade: 'Mechanical', block_state: 'Clear' },
  mode: 'retire', confirm_with: 'Acme Ltd',
  groups: {
    removed: [{ key: 'vendor_contacts', label: 'Contacts', rows: 2 }],
    unassigned: [
      { key: 'assets', label: 'Assets', rows: 7, items: [1, 2, 3, 4, 5, 6].map(asset) },
      { key: 'work_orders', label: 'Open work orders', rows: 1, items: [{ id: 'w1', code: 'WO-17', title: 'Leak on level 3' }] }
    ],
    closed: [{ key: 'approvals', label: 'Pending decisions', rows: 3 }],
    revoked: [], ended: [],
    archived: [{ key: 'compliance_certificates', label: 'Accreditations', rows: 4 }],
    kept: [{ key: 'invoice_verifications', label: 'Invoices', rows: 12 }],
    other: []
  },
  unavailable: []
};
const REMOVE = {
  ok: true, applied: false,
  vendor: { id: ACME, name: 'Acme Ltd', code: null, trade: null, block_state: 'Clear' },
  mode: 'remove', confirm_with: 'Acme Ltd',
  groups: { removed: [{ key: 'vendor_contacts', label: 'Contacts', rows: 1 }], unassigned: [], closed: [], revoked: [], ended: [], archived: [], kept: [], other: [] },
  unavailable: []
};
const done = (plan) => [200, Object.assign({}, plan, { applied: true, audit_id: 'au-1' })];
const refusal = (status, reason, extra) => [status, { detail: Object.assign({ ok: false, error: 'refused', reason: reason }, extra || {}) }];
const posts = () => calls.filter((x) => x.key === DEL);
const plans = () => calls.filter((x) => x.key === PLAN);

// The reads a delete asks for afterwards are counted, not run.
let reads;
const LOADERS = ['vpLoad', 'vmLoad', 'ccLoad', 'homeLoad', 'asCondLoad', 'mxLiveLoad'];
const quiet = (x) => {
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun']
    .forEach((k) => { x[k] = () => {}; });
  LOADERS.forEach((k) => { x[k] = () => { reads[k] = (reads[k] || 0) + 1; }; });
  return x;
};
const resp = (user) => ({ ok: true, user, tokens: { access_token: 'a', refresh_token: 'r-' + user.id } });

let c;
const as = (user, role) => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(user));
  c.setState({ instance: 'full', view: 'vp', role: role, vpRaw: RAW, vpVendor: ACME });
  return c;
};
// Opens the dialog from the panel's own control and waits for the plan.
async function open(plan) {
  if (plan) handlers[PLAN] = [200, plan];
  c.renderVals().vp.deleteVendor();
  await settle();
  return c.renderVals();
}
const type = (text) => c.renderVals().vdlSetTyped({ target: { value: text } });

beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  calls.length = 0;
  handlers = {};
  reads = {};
});
afterEach(() => {
  if (c) { clearTimeout(c._tt); clearInterval(c._orchTick); clearTimeout(c._mgTimer); if (c.vdlReset) c.vdlReset(); }
  client.setActingOrg(null);
});

// ── who is offered it ─────────────────────────────────────────────────────────

test('a plain user is never offered Delete vendor, and calling it anyway sends nothing', async () => {
  as(USER, 'user');
  const v = c.renderVals();
  assert.equal(v.vp.name, 'Acme Ltd');
  assert.equal(v.vp.canDelete, false);
  c.vdlOpen(ACME, 'Acme Ltd');
  await settle();
  assert.equal(calls.length, 0);
  assert.equal(c.renderVals().vdlShow, false);
});

test('the admin view does not carry it', () => {
  as(ADMIN, 'admin');
  assert.equal(c.renderVals().vp.canDelete, false);
});

test('an admin in User view is offered it on the selected vendor', () => {
  as(ADMIN, 'user');
  assert.equal(c.renderVals().vp.canDelete, true);
});

test('a superadmin viewing as a company is offered it too, and the read names that company', async () => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(SUPER));
  c.viewAsCompany('org-9', 'TechCorp');
  c.setState({ role: 'user', instance: 'full', view: 'vp', vpRaw: RAW, vpVendor: ACME });
  assert.equal(c.renderVals().vp.canDelete, true);
  await open(RETIRE);
  assert.equal(plans().length, 1);
  assert.equal(plans()[0].query.organization_id, 'org-9');
});

// ── the dialog reads what would change before anything can be deleted ─────────

test('opening reads the plan for that vendor, and says so — with nothing armed — until it answers', async () => {
  as(ADMIN, 'user');
  let release;
  handlers[PLAN] = () => new Promise((r) => { release = () => r([200, RETIRE]); });
  c.renderVals().vp.deleteVendor();
  let v = c.renderVals();
  assert.equal(v.vdlShow, true);
  assert.equal(v.vdlTitle, 'Delete Acme Ltd?');
  assert.equal(v.vdlReading, 'Reading what this would change…');
  assert.equal(v.vdlMode, '');
  assert.deepEqual(v.vdlGroups, []);
  assert.equal(v.vdlCanDelete, false);
  assert.equal(v.vdlDeleteShow, true, 'shown, off, while it reads');
  assert.equal(v.vdlCancelLabel, 'Cancel');
  release();
  await settle();
  v = c.renderVals();
  assert.equal(v.vdlReading, '');
  assert.equal(plans().length, 1, 'one read, for this vendor');
  assert.equal(plans()[0].query.organization_id, undefined, 'an admin acts for their own company');
});

test('an id the address cannot carry as typed is encoded', async () => {
  as(ADMIN, 'user');
  handlers['GET ' + OI + 'VEN%2FABC%201/delete'] = [200, RETIRE];
  c.vdlOpen('VEN/ABC 1', 'Odd Id Ltd');
  await settle();
  assert.equal(calls[0].key, 'GET ' + OI + 'VEN%2FABC%201/delete');
});

test('a vendor with history will be retired, and every group it touches is listed with its items', async () => {
  as(ADMIN, 'user');
  const v = await open(RETIRE);
  assert.equal(v.vdlSub, 'ACME · Mechanical');
  assert.equal(v.vdlMode, 'This vendor has history, so it will be retired: hidden everywhere, its past records kept and marked (deleted).');
  assert.equal(v.vdlScope, 'Applies across every building in the company, including ones you do not manage.');
  assert.deepEqual(v.vdlGroups.map((g) => g.title),
    ['Unassigned', 'Pending decisions closed', 'Removed with it', 'Accreditations archived', 'History kept']);
  const un = v.vdlGroups[0];
  assert.deepEqual(un.rows.map((r) => [r.label, r.n]), [['Assets', '7'], ['Open work orders', '1']]);
  assert.deepEqual(un.rows[0].items, ['Boiler 1 · B-301', 'Boiler 2 · B-302', 'Boiler 3 · B-303', 'Boiler 4 · B-304', 'Boiler 5 · B-305']);
  assert.equal(un.rows[0].more, 'and 2 more', 'seven unassigned, five named');
  assert.deepEqual(un.rows[1].items, ['WO-17 · Leak on level 3']);
  assert.equal(un.rows[1].more, '');
  assert.equal(v.vdlConfirmWith, 'Acme Ltd');
});

test('a vendor with no history will be removed completely', async () => {
  as(ADMIN, 'user');
  const v = await open(REMOVE);
  assert.equal(v.vdlMode, 'This vendor has no history, so it will be removed completely.');
  assert.deepEqual(v.vdlGroups.map((g) => g.title), ['Removed with it']);
  assert.equal(v.vdlSub, '');
});

// ── the typed name ────────────────────────────────────────────────────────────

test('Delete vendor stays off until the exact name is typed — spacing aside, as the server reads it', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  assert.equal(c.renderVals().vdlCanDelete, false);
  type('Acme');
  assert.equal(c.renderVals().vdlCanDelete, false);
  type('acme ltd');
  assert.equal(c.renderVals().vdlCanDelete, false, 'a different case is a different name');
  type('  Acme   Ltd ');
  const v = c.renderVals();
  assert.equal(v.vdlNameOk, true);
  assert.equal(v.vdlCanDelete, true);
  assert.equal(v.vdlDeleteLabel, 'Delete vendor');
});

test('the reason is optional and capped at 500 characters', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  c.renderVals().vdlSetReason({ target: { value: 'x'.repeat(620) } });
  assert.equal(c.renderVals().vdlReasonText.length, 500);
});

// ── confirming ────────────────────────────────────────────────────────────────

test('confirming sends exactly the typed name, the mode that was shown and the reason', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme  Ltd');
  c.renderVals().vdlSetReason({ target: { value: '  Contract ended  ' } });
  await c.renderVals().vdlConfirm();
  assert.equal(posts().length, 1);
  assert.deepEqual(posts()[0].body, { confirm: 'Acme Ltd', expect: 'retire', reason: 'Contract ended' });
});

test('no reason given is sent as none', async () => {
  as(ADMIN, 'user');
  await open(REMOVE);
  handlers[DEL] = done(REMOVE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.deepEqual(posts()[0].body, { confirm: 'Acme Ltd', expect: 'remove', reason: null });
});

test('a second press while deleting sends nothing more, and the dialog cannot be closed under it', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  let release;
  handlers[DEL] = () => new Promise((r) => { release = () => r(done(RETIRE)); });
  type('Acme Ltd');
  const first = c.renderVals().vdlConfirm();
  c.renderVals().vdlConfirm();
  let v = c.renderVals();
  assert.equal(v.vdlBusy, true);
  assert.equal(v.vdlDeleteLabel, 'Deleting…');
  assert.equal(v.vdlCanDelete, false);
  assert.equal(v.vdlCanCancel, false);
  v.vdlClose();
  assert.equal(c.renderVals().vdlShow, true, 'Cancel and Esc do nothing while the delete is on the wire');
  await settle();
  assert.equal(posts().length, 1);
  release();
  await first;
  await settle();
  assert.equal(posts().length, 1);
});

test('nothing is sent before the plan has answered, whatever was typed', async () => {
  as(ADMIN, 'user');
  let release;
  handlers[PLAN] = () => new Promise((r) => { release = () => r([200, RETIRE]); });
  c.renderVals().vp.deleteVendor();
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(posts().length, 0);
  release();
  await settle();
});

// ── success ───────────────────────────────────────────────────────────────────

test('a retired vendor leaves the directory at once, the next one is selected, and it is said', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  const v = c.renderVals();
  assert.equal(v.vdlShow, false, 'the dialog closes');
  assert.deepEqual(v.vpList.map((r) => r.name), ['Beta Lifts', 'Gamma Gas']);
  assert.equal(c.state.vpVendor, BETA, 'the next vendor in the directory is selected');
  assert.equal(v.vp.name, 'Beta Lifts');
  assert.equal(c.state.toast, 'Acme Ltd deleted — its history is kept, marked (deleted)');
  assert.equal(v.vdlSaid, 'Acme Ltd deleted — its history is kept, marked (deleted)', 'announced to a screen reader');
});

test('a removed vendor says so', async () => {
  as(ADMIN, 'user');
  await open(REMOVE);
  handlers[DEL] = done(REMOVE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.state.toast, 'Acme Ltd deleted');
});

test('a read that left before the delete cannot bring the vendor back', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  // The 15-minute refresh, issued before the delete, lands now with the vendor still in it.
  c.setState({ vpRaw: Object.assign({}, RAW, { fetchedAt: '2026-10-12T08:15:00Z' }), vmRows: [{ id: ACME, name: 'Acme Ltd' }] });
  assert.deepEqual(c.renderVals().vpList.map((r) => r.name), ['Beta Lifts', 'Gamma Gas']);
});

test('deleting a vendor that is not the selected one leaves the selection alone', async () => {
  as(ADMIN, 'user');
  c.setState({ vpVendor: GAMMA });
  handlers[PLAN] = [200, RETIRE];
  c.vdlOpen(ACME, 'Acme Ltd');
  await settle();
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.state.vpVendor, GAMMA);
});

test('afterwards every page that lists vendors is read again — after any read already in flight', async () => {
  as(ADMIN, 'user');
  c.setState({ asIntel: { a1: { vendor: { id: ACME } } }, vaEvents: [] });
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  // The Vendors page's own read is mid-flight: a call now would be dropped by its guard.
  c._vpLoading = true;
  await c.renderVals().vdlConfirm();
  assert.equal(reads.vpLoad || 0, 0, 'not asked while its read is in flight');
  assert.equal(reads.vmLoad, 1);
  assert.equal(reads.ccLoad, 1);
  assert.equal(reads.homeLoad, 1);
  assert.equal(reads.asCondLoad, 1);
  // Maintenance's open work orders name the vendor they just lost (12 Oct 2026).
  assert.equal(reads.mxLiveLoad, 1, 'Maintenance too');
  assert.deepEqual(c.state.asIntel, {}, 'cached asset reads naming the vendor are dropped');
  assert.equal(c.state.vaEvents, null, 'the vendor trail reads again on its next visit');
  c._vpLoading = false;
  await wait(400);
  assert.equal(reads.vpLoad, 1, 'read once the earlier read has landed');
});

test('a saved hoist of the vendor in this browser never reads "In vendor master" again', async () => {
  mem[VH_STORE_KEY] = JSON.stringify({ 'org-1': { dismissed: [], events: [],
    prepared: [{ key: 'acme ltd', name: 'Acme Ltd', trade: '', saved: true, vendorId: ACME, savedAt: Date.now(), fields: {}, certIds: [] }] } });
  as(ADMIN, 'user');
  c.setState({ ccLive: Object.assign({}, c.ccData(), { certs: [], vendors: [], buildings: [] }) });
  c.setState({ view: 'vh' });
  assert.equal(c.renderVals().vhHoisted.some((x) => x.name === 'Acme Ltd' && x.status === 'In vendor master'), true, 'listed before');
  c.setState({ view: 'vp' });
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  c.setState({ view: 'vh' });
  assert.equal(c.renderVals().vhHoisted.some((x) => x.name === 'Acme Ltd'), false);
  assert.equal(JSON.parse(mem[VH_STORE_KEY])['org-1'].prepared[0].deleted, true, 'marked in the store, so a reload agrees');
});

test('a vendor the server reports already deleted is treated as gone', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = [200, { ok: true, already: true, vendor: RETIRE.vendor, mode: 'retire' }];
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.renderVals().vdlShow, false);
  assert.equal(c.renderVals().vpList.some((r) => r.name === 'Acme Ltd'), false);
  assert.equal(c.state.toast, 'Acme Ltd was already deleted.');
});

// ── what can go wrong ─────────────────────────────────────────────────────────

// A 404 vendor_not_found is a vendor this company's record does not hold — a legacy row with no
// company, or one removed outside Hoistra — though older scorecards or certificates still list
// it. "Already gone" would be false, and the next press would say it again (12 Oct 2026).
const NOT_HERE = 'This vendor is not on your company\'s vendor record, so it cannot be deleted here. Nothing was deleted.';

test('a vendor not on the company\'s record says so, offers nothing to delete, and the pages are read again', async () => {
  as(ADMIN, 'user');
  handlers[PLAN] = refusal(404, 'vendor_not_found');
  c.renderVals().vp.deleteVendor();
  await settle();
  const v = c.renderVals();
  assert.equal(v.vdlError, NOT_HERE);
  assert.equal(v.vdlCanDelete, false);
  assert.equal(v.vdlRetryShow, false);
  assert.equal(v.vdlDeleteShow, false, 'nothing to delete');
  assert.equal(v.vdlCancelLabel, 'Close');
  assert.equal(reads.vpLoad, 1);
});

test('gone by the time Delete vendor is pressed: the same', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(404, 'vendor_not_found');
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  const v = c.renderVals();
  assert.equal(v.vdlError, NOT_HERE);
  assert.equal(v.vdlCanDelete, false);
});

test('after that, this tab does not offer Delete vendor for it again — still listed, nothing hidden', async () => {
  as(ADMIN, 'user');
  handlers[PLAN] = refusal(404, 'vendor_not_found');
  c.renderVals().vp.deleteVendor();
  await settle();
  c.renderVals().vdlClose();
  const v = c.renderVals();
  assert.equal(v.vp.name, 'Acme Ltd', 'still the panel\'s vendor');
  assert.equal(v.vp.canDelete, false);
  assert.deepEqual(c.state.vdlGone, [], 'not hidden: the reads still list it');
  calls.length = 0;
  c.vdlOpen(ACME, 'Acme Ltd');
  await settle();
  assert.equal(calls.length, 0, 'nothing read again');
  c.setState({ vpVendor: BETA });
  assert.equal(c.renderVals().vp.canDelete, true, 'another vendor is offered it as before');
});

test('when the plan changed underneath, the new plan is shown, the name is typed again, and the next try expects it', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(409, 'plan_changed', { plan: REMOVE });
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  let v = c.renderVals();
  assert.equal(v.vdlMode, 'This vendor has no history, so it will be removed completely.');
  assert.equal(v.vdlTyped, '');
  assert.equal(v.vdlCanDelete, false);
  assert.equal(v.vdlChanged, 'What this would change moved since you opened it: it no longer has history, so it will be removed completely. Check it again and type the name to confirm.');
  handlers[DEL] = done(REMOVE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.deepEqual(posts()[1].body, { confirm: 'Acme Ltd', expect: 'remove', reason: null });
});

test('the same mode with different counts still says it moved', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  const more = JSON.parse(JSON.stringify(RETIRE));
  more.groups.unassigned[0].rows = 8;
  handlers[DEL] = refusal(409, 'plan_changed', { plan: more });
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.renderVals().vdlChanged, 'What this would change moved since you opened it. Check it again and type the name to confirm.');
  assert.equal(c.renderVals().vdlGroups[0].rows[0].n, '8');
});

test('a vendor busy with another change can be tried again, the name kept', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(409, 'vendor_busy');
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  const v = c.renderVals();
  assert.equal(v.vdlError, 'Something else is changing this vendor right now — it may be an earlier delete still finishing. Wait a moment and press Delete vendor again.');
  assert.equal(v.vdlTyped, 'Acme Ltd');
  assert.equal(v.vdlCanDelete, true);
});

test('a delete the audit trail could not record is not a delete, and says so', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(503, 'not_recorded');
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  const v = c.renderVals();
  assert.equal(v.vdlError, 'Nothing was deleted — the change could not be recorded in the audit trail. Try again.');
  assert.equal(v.vdlCanDelete, true);
  assert.equal(c.state.vdlGone.length, 0);
});

test('a refused delete changed nothing and says so', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(409, 'delete_refused', { step: 'unassign' });
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.renderVals().vdlError, 'Nothing was changed — the delete could not be completed. Try again, and contact support if it keeps happening.');
});

test('a name the server does not accept reads the plan again and asks for the name again', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = refusal(400, 'confirm_mismatch', { confirm_with: 'Acme Ltd' });
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  await settle();
  const v = c.renderVals();
  assert.equal(plans().length, 2, 'the plan is read again');
  assert.equal(v.vdlTyped, '');
  assert.equal(v.vdlError, 'The name did not match the one on record. It has been read again — type it exactly as shown.');
  assert.equal(v.vdlCanDelete, false);
});

test('no answer to the delete is said honestly, and Try again sends it again', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();  // no handler: the network fails
  let v = c.renderVals();
  assert.equal(v.vdlError, 'No answer came back, so it is not known whether the vendor was deleted. Try again — if it was, you will be told it is already gone.');
  assert.equal(v.vdlRetryShow, true);
  handlers[DEL] = done(RETIRE);
  await v.vdlRetry();
  assert.equal(posts().length, 2);
  assert.equal(c.renderVals().vdlShow, false);
  assert.equal(c.state.toast, 'Acme Ltd deleted — its history is kept, marked (deleted)');
});

test('a plan that could not be read can be read again', async () => {
  as(ADMIN, 'user');
  c.renderVals().vp.deleteVendor();
  await settle();
  let v = c.renderVals();
  assert.equal(v.vdlError, 'What this would change could not be read. Nothing has been deleted — try again.');
  assert.equal(v.vdlRetryShow, true);
  handlers[PLAN] = [200, RETIRE];
  await v.vdlRetry();
  v = c.renderVals();
  assert.equal(v.vdlError, '');
  assert.equal(v.vdlMode.startsWith('This vendor has history'), true);
});

// ── replies that arrive too late ───────────────────────────────────────────────

test('closing while the plan is read drops its answer', async () => {
  as(ADMIN, 'user');
  let release;
  handlers[PLAN] = () => new Promise((r) => { release = () => r([200, RETIRE]); });
  c.renderVals().vp.deleteVendor();
  c.renderVals().vdlClose();
  release();
  await settle();
  assert.equal(c.state.vdl, null);
  assert.equal(c.renderVals().vdlShow, false);
});

test('a company switch while the delete is on the wire drops its answer', async () => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(SUPER));
  c.viewAsCompany('org-9', 'TechCorp');
  c.setState({ role: 'user', instance: 'full', view: 'vp', vpRaw: RAW, vpVendor: ACME });
  c.resetLiveData = HoistraLogic.prototype.resetLiveData;
  await open(RETIRE);
  let release;
  handlers[DEL] = () => new Promise((r) => { release = () => r(done(RETIRE)); });
  type('Acme Ltd');
  const p = c.renderVals().vdlConfirm();
  await settle();
  c.viewAsCompany('org-10', 'Other Co');
  assert.equal(c.state.vdl, null, 'the switch closes the dialog');
  release();
  await p;
  await settle();
  assert.equal(c.state.vdl, null);
  assert.deepEqual(c.state.vdlGone, []);
  assert.notEqual(c.state.toast, 'Acme Ltd deleted — its history is kept, marked (deleted)');
});

test('signing out hides the dialog, drops an answer that lands afterwards, and the next sign-in starts clean', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  let release;
  handlers[DEL] = () => new Promise((r) => { release = () => r(done(RETIRE)); });
  type('Acme Ltd');
  const p = c.renderVals().vdlConfirm();
  await settle();
  c.authSignedOut();
  assert.equal(c.renderVals().vdlShow, false);
  release();
  await p;
  await settle();
  assert.deepEqual(c.state.vdlGone, []);
  assert.notEqual(c.state.toast, 'Acme Ltd deleted — its history is kept, marked (deleted)');
  c.resetLiveData = HoistraLogic.prototype.resetLiveData;
  c.authEnter(resp(ADMIN));
  assert.equal(c.state.vdl, null);
  assert.deepEqual(c.state.vdlGone, []);
});

test('signing out clears the delete state in the same write, not at the next sign-in', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  await open(RETIRE);
  assert.notEqual(c.state.vdl, null);
  assert.deepEqual(c.state.vdlGone, [ACME]);
  c.authSignedOut();
  assert.equal(c.state.vdl, null);
  assert.deepEqual(c.state.vdlGone, []);
});

test('a company switch forgets the vendors deleted under the previous one', async () => {
  as(ADMIN, 'user');
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.deepEqual(c.state.vdlGone, [ACME]);
  HoistraLogic.prototype.resetLiveData.call(c);
  assert.deepEqual(c.state.vdlGone, []);
  assert.equal(c.state.vdl, null);
});

test('a Compliance read that left before the delete cannot bring the vendor back into Compliance or Home', async () => {
  as(ADMIN, 'user');
  c.ccLoad = HoistraLogic.prototype.ccLoad;
  const CC = '/backend/ops-intelligence/api/compliance/';
  // The register as it stood when the read left: the vendor still on record, nothing marked.
  const rows = [
    { id: 'c1', cert_scope: 'Vendor', certificate_type_code: 'GAS_SAFE', vendor_name: 'Acme Ltd', vendor_id: ACME, vendor_on_record: true, expiry_date: '2027-03-01', country_code: 'UK' },
    { id: 'c2', cert_scope: 'Vendor', certificate_type_code: 'NICEIC', vendor_name: 'Beta Lifts', vendor_id: BETA, vendor_on_record: true, expiry_date: '2027-03-01', country_code: 'UK' }
  ];
  let first = true, release;
  handlers['GET ' + CC + 'certificates'] = () => {
    if (!first) return [200, { certificates: rows }];
    first = false;
    return new Promise((r) => { release = () => r([200, { certificates: rows }]); });
  };
  handlers['GET ' + CC + 'coverage/buildings'] = [200, { buildings: [] }];
  handlers['GET ' + CC + 'coverage/vendors'] = [200, { vendors: [] }];
  handlers['GET ' + CC + 'country-pack'] = [200, { types: [] }];
  const reading = c.ccLoad();
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  release();
  await reading;
  assert.deepEqual(c.state.ccLive.vendors.map((v) => v.name), ['Beta Lifts']);
  assert.equal(c.homeModel().hero.vendors, 1);
  await wait(300);
});

test('Home\'s vendor count drops at once, and a confirmation armed on the deleted vendor does not carry to the next', async () => {
  as(ADMIN, 'user');
  const reg = Object.assign({}, c.ccData(), { certs: [], buildings: [], vendors: [
    { name: 'Acme Ltd', id: ACME, block: 'Clear', worst: 'OK', spec: '—', serves: [], gaps: [] },
    { name: 'Beta Lifts', id: BETA, block: 'Clear', worst: 'OK', spec: '—', serves: [], gaps: [] }
  ] });
  c.setState({ ccLive: reg, vpConfirmArmed: true, vpEditField: 'p1_response_hours', vpEv: { vendor: ACME, filter: 'all', page: 0, q: '', open: null, month: null } });
  assert.equal(c.homeModel().hero.vendors, 2);
  await open(RETIRE);
  handlers[DEL] = done(RETIRE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();
  assert.equal(c.homeModel().hero.vendors, 1);
  assert.deepEqual(c.state.ccLive.vendors.map((v) => v.name), ['Beta Lifts']);
  assert.equal(c.state.vpConfirmArmed, false);
  assert.equal(c.state.vpEditField, '');
  assert.equal(c.state.vpEv, null);
});

test('a plan that says the vendor was deleted already hides it and says it is gone', async () => {
  as(ADMIN, 'user');
  handlers[PLAN] = [200, { ok: true, already: true, vendor: RETIRE.vendor, mode: 'retire' }];
  c.renderVals().vp.deleteVendor();
  await settle();
  const v = c.renderVals();
  assert.equal(v.vdlError, 'This vendor is already gone.');
  assert.equal(v.vdlDeleteShow, false);
  assert.deepEqual(c.state.vdlGone, [ACME]);
  assert.equal(v.vpList.some((r) => r.name === 'Acme Ltd'), false);
});

test('a service without the delete yet is not mistaken for a vendor already gone', async () => {
  as(ADMIN, 'user');
  handlers[PLAN] = [404, { detail: 'Not Found' }];
  c.renderVals().vp.deleteVendor();
  await settle();
  const v = c.renderVals();
  assert.equal(v.vdlError, 'Deleting a vendor is not available here yet. Nothing was deleted.');
  assert.equal(v.vdlDeleteShow, false);
  assert.deepEqual(c.state.vdlGone, []);
  assert.equal(reads.vpLoad || 0, 0, 'nothing to re-read');
});

test('a refusal of the role says only an admin can, and nothing was deleted', async () => {
  as(ADMIN, 'user');
  handlers[PLAN] = refusal(403, 'forbidden');
  c.renderVals().vp.deleteVendor();
  await settle();
  assert.equal(c.renderVals().vdlError, 'Only an admin can delete a vendor. Nothing was deleted.');
});

// ── the plan as shown (12 Oct 2026) ───────────────────────────────────────────

test('the scope says it reaches every building in the company, not only the ones the admin manages', async () => {
  as(ADMIN, 'user');
  const v = await open(RETIRE);
  assert.equal(v.vdlScope, 'Applies across every building in the company, including ones you do not manage.');
  assert.equal(v.vdlPermanent, 'This cannot be undone.');
});

test('records the service can only call "Other records" read as one row, counted together', async () => {
  as(ADMIN, 'user');
  const plan = JSON.parse(JSON.stringify(RETIRE));
  plan.groups.other = [{ key: 'assets.vendor_id', label: 'Other records', rows: 3 }, { key: 'foo.contractor_id', label: 'Other records', rows: 1 }];
  const v = await open(plan);
  const other = v.vdlGroups.find((g) => g.key === 'other');
  assert.equal(other.rows.length, 1);
  assert.equal(other.rows[0].label, 'Other records');
  assert.equal(other.rows[0].n, '4');
});

// ── a draft about a deleted vendor (12 Oct 2026) ──────────────────────────────
// A Decision-queue draft is sent against its queue item, and the service refuses it once the
// vendor the item names is deleted — whatever address is typed. "Correct it or retry" cannot
// work; the item is dismissed instead.
test('the dock says a send refused for a deleted vendor is dismissed, not retried', async () => {
  as(ADMIN, 'user');
  handlers['POST /backend/ops-intelligence/api/approvals/send-email'] = refusal(409, 'vendor_deleted', { error: 'This vendor has been deleted, so nothing was sent.' });
  c.setState({ flow: 'email', emKind: 'renewal', emTo: 'ops@acme.example', emSubject: 'Renewal', emBody: 'Please renew.', emQueueItemId: 'q1' });
  await c.renderVals().em.send();
  assert.equal(c.state.flowDone, 'Not sent. This vendor was deleted — dismiss the item instead.');
  assert.equal(c.state.flow, 'email', 'the draft stays, to be read');
});

test('any other refusal reads as before, without a doubled full stop', async () => {
  as(ADMIN, 'user');
  handlers['POST /backend/ops-intelligence/api/approvals/send-email'] = [200, { ok: false, error: 'Subject is required.' }];
  c.setState({ flow: 'email', emKind: 'renewal', emTo: 'ops@acme.example', emSubject: 'Renewal', emBody: 'Please renew.' });
  await c.renderVals().em.send();
  assert.equal(c.state.flowDone, 'Not sent — Subject is required. The draft is unchanged; correct it or retry.');
});

// Pre-push review, 12 Oct 2026: a REMOVE whose answer was lost commits anyway, and the retry then
// finds no such vendor. That is the earlier delete having worked — not "Nothing was deleted".
test('a retry after a lost answer that finds the vendor gone says the delete most likely went through', async () => {
  as(ADMIN, 'user');
  await open(REMOVE);
  type('Acme Ltd');
  await c.renderVals().vdlConfirm();  // no handler: the answer is lost
  assert.equal(c.renderVals().vdlRetryShow, true);
  handlers[DEL] = refusal(404, 'vendor_not_found');
  await c.renderVals().vdlRetry();
  const v = c.renderVals();
  assert.match(v.vdlError, /earlier delete most likely went through/);
  assert.ok(!/Nothing was deleted/.test(v.vdlError));
  assert.ok(c.state.vdlGone.indexOf(String(c.state.vdl.id)) > -1, 'it is hidden like any other delete');
});
