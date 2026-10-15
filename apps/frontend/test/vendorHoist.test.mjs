// Hoist vendors (12 Oct 2026). In User view, an admin (or a superadmin) can open Hoist vendors
// from the Vendors page: the vendors named on certificates that are not in the vendor master,
// and a one-question-at-a-time conversation in the orchestrator that prepares the record.
// A plain user is never offered it, and the admin view does not carry it.
//
// Saving to the vendor master is not switched on (Hussain, 12 Oct 2026): the hoist prepares
// the record in this browser and sends nothing. These tests hold that to account — no request
// other than a GET leaves the controller.
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
// Every request is recorded; only the routes a test answers get an answer, the rest fail as an
// unreachable network would. `sent` is "METHOD /path", `calls` carries the parsed body.
const sent = [];
const calls = [];
let handlers = {};
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  sent.push(key);
  // A file upload goes as FormData, kept as it is; everything else is JSON.
  calls.push({ key: key, body: opts && typeof opts.body === 'string' ? JSON.parse(opts.body) : (opts && opts.body) || null });
  const h = handlers[key];
  if (!h) throw new TypeError('no network in tests: ' + key);
  const [status, body] = typeof h === 'function' ? h(opts) : h;
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => JSON.stringify(body) };
};
const settle = () => new Promise((r) => setTimeout(r, 30));
const OI = '/backend/ops-intelligence/api/compliance/certificates/';
const CN = '/backend/connector/api/v1/plenum/';
const writes = () => calls.filter((x) => !/^GET /.test(x.key));

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { detectedVendors, masterVendors, VH_STORE_KEY } = await import('../src/logic/vendorHoist.js');

const ORG = { organization_id: 'org-1', organization_name: 'Northbridge' };
const USER = Object.assign({ id: 'u-1', email: 'sam@example.com', full_name: 'Sam', status: 'active', role: 'user' }, ORG);
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin' });
const SUPER = Object.assign({}, USER, { id: 'u-3', email: 'sadie@example.com', full_name: 'Sadie', role: 'superadmin' });

// Certificate rows as complianceLive shapes them into ccLive.certs.
const CERTS = [
  { id: 'c1', kind: 'building', nm: 'Gas safety record', building: 'Kingsway House', holder: 'Kingsway House', vendor: 'Crown Gas Services', vendorId: null, vendorOnRecord: false, trade: 'Gas', exp: '12 Mar 2027' },
  { id: 'c2', kind: 'building', nm: 'Boiler service', building: 'Harbour Point', holder: 'Harbour Point', vendor: 'crown gas services ', vendorId: null, vendorOnRecord: false, trade: null, exp: '01 Feb 2027' },
  { id: 'c3', kind: 'vendor', nm: 'Pest control licence', building: null, holder: 'Kestrel Pest', vendor: 'Kestrel Pest', vendorId: null, vendorOnRecord: false, trade: 'Pest control', exp: '09 Sep 2026' },
  { id: 'c4', kind: 'building', nm: 'EICR', building: 'Kingsway House', holder: 'Kingsway House', vendor: 'Sparks Ltd', vendorId: 'v-9', vendorOnRecord: true, trade: 'Electrical', exp: '01 Jan 2028' },
  // Named on a certificate, not linked, but the vendor master has the name: not a new vendor.
  { id: 'c5', kind: 'building', nm: 'LOLER', building: 'Harbour Point', holder: 'Harbour Point', vendor: 'LiftCo', vendorId: null, vendorOnRecord: false, trade: 'Lifts', exp: '01 May 2027' },
  { id: 'c6', kind: 'building', nm: 'Fire alarm', building: 'Harbour Point', holder: 'Harbour Point', vendor: null, vendorId: null, vendorOnRecord: false, trade: null, exp: '01 May 2027' }
];
const VENDORS = [
  { name: 'Sparks Ltd', id: 'v-9', block: 'Clear', worst: 'OK', spec: 'Electrical', serves: [], gaps: [] },
  { name: 'LiftCo', id: 'v-4', block: 'Clear', worst: '<30d', spec: 'Lifts', serves: [], gaps: [] },
  { name: 'AquaSafe', id: 'v-5', block: 'Blocked', worst: 'Blocked', spec: '—', serves: [], gaps: [] },
  { name: 'Quiet Co', id: 'v-6', block: 'Clear', worst: 'None on file', spec: '—', serves: [], gaps: [] },
  // A holder named only on a certificate is not in the master.
  { name: 'Kestrel Pest', id: null, block: 'Clear', worst: '<90d', spec: 'Pest control', serves: [], gaps: [] }
];

const quiet = (x) => {
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun', 'ccLoad', 'vpLoad']
    .forEach((k) => { x[k] = () => {}; });
  return x;
};
const resp = (user) => ({ ok: true, user, tokens: { access_token: 'a', refresh_token: 'r-' + user.id } });
const register = (x) => Object.assign({}, x.ccData(), { certs: CERTS, vendors: VENDORS });
const labels = (list) => (list || []).map((x) => x.label || x.name || x.l);

let c;
const as = (user, role) => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(user));
  c.setState({ instance: 'full', view: 'vp', role: role, ccLive: register(c), bldLive: null });
  return c;
};
// Every question answered or passed, to the confirm card (six questions, or seven with the
// certificates question for an account that may add data).
const toConfirm = (x) => { for (let i = 0; i < 10 && !x.renderVals().vhcConfirm; i += 1) x.renderVals().vhcNext(); };

beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  Object.keys(tabMem).forEach((k) => { delete tabMem[k]; });
  sent.length = 0;
  calls.length = 0;
  handlers = {};
});
afterEach(() => { if (c) { clearTimeout(c._tt); clearInterval(c._orchTick); clearTimeout(c._mgTimer); } });

// ── the shapers ───────────────────────────────────────────────────────────────

test('detected vendors: named on a certificate, not linked, not in the master — grouped by name', () => {
  const d = detectedVendors(CERTS, masterVendors(VENDORS));
  assert.deepEqual(d.map((x) => x.name), ['Crown Gas Services', 'Kestrel Pest']);
  const crown = d[0];
  assert.equal(crown.certs.length, 2, 'two spellings of one name are one vendor');
  assert.deepEqual(crown.buildings, ['Kingsway House', 'Harbour Point']);
  assert.equal(crown.trade, 'Gas');
  assert.equal(crown.sources[0], 'Gas safety record · Kingsway House · expires 12 Mar 2027');
  assert.equal(d[1].sources[0], 'Pest control licence · vendor accreditation · expires 09 Sep 2026');
});

test('a draft certificate says so on the card — it is not evidence until confirmed', () => {
  const d = detectedVendors([Object.assign({}, CERTS[2], { draft: true })], []);
  assert.equal(d[0].sources[0], 'Pest control licence · vendor accreditation · expires 09 Sep 2026 · draft, awaiting confirmation');
});

test('the vendor master: only vendors with a record, with their eligibility', () => {
  const m = masterVendors(VENDORS);
  assert.deepEqual(m.map((v) => v.name), ['AquaSafe', 'LiftCo', 'Quiet Co', 'Sparks Ltd']);
  const by = Object.fromEntries(m.map((v) => [v.name, v.status]));
  assert.equal(by['Sparks Ltd'].label, 'Eligible');
  assert.equal(by['LiftCo'].label, 'Expiring');
  assert.equal(by['AquaSafe'].label, 'Not eligible');
  assert.equal(by['Quiet Co'].label, 'No certificates');
});

// ── who is offered it ─────────────────────────────────────────────────────────

test('a plain user is never offered Hoist vendors, and cannot reach the page', () => {
  as(USER, 'user');
  let v = c.renderVals();
  assert.equal(v.vhShow, false);
  c.setState({ view: 'vh' });
  v = c.renderVals();
  assert.equal(v.isVH, false);
  assert.equal(v.isVP, true, 'a stored Hoist vendors view falls back to Vendors');
});

test('the admin view does not carry Hoist vendors', () => {
  as(ADMIN, 'admin');
  assert.equal(c.renderVals().vhShow, false);
});

test('an admin in User view gets Hoist vendors on the Vendors page, with the detected count', () => {
  as(ADMIN, 'user');
  const v = c.renderVals();
  assert.equal(v.vhShow, true);
  assert.equal(v.vhDetN, 2);
  v.vhOpen();
  const p = c.renderVals();
  assert.equal(c.state.view, 'vh');
  assert.equal(p.isVH, true);
  assert.equal(p.isVP, false);
  assert.deepEqual(p.vhDet.map((x) => x.name), ['Crown Gas Services', 'Kestrel Pest']);
  assert.deepEqual(p.vhHoisted.map((x) => x.name), ['AquaSafe', 'LiftCo', 'Quiet Co', 'Sparks Ltd']);
  assert.equal(labels(p.navSections).indexOf('Vendors') > -1, true);
  assert.equal(p.navSections.find((n) => n.label === 'Vendors').color, 'var(--color-accent)', 'Vendors stays lit');
});

test('a superadmin viewing as a company gets it in User view too', () => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(SUPER));
  c.viewAsCompany('org-9', 'TechCorp');
  c.setState({ role: 'user', instance: 'full', view: 'vp', ccLive: register(c) });
  assert.equal(c.renderVals().vhShow, true);
});

test('before the register answers, nothing is detected from the sample one', () => {
  as(ADMIN, 'user');
  c.setState({ ccLive: null, ccLoading: true, view: 'vh' });
  const v = c.renderVals();
  assert.equal(v.vhDet.length, 0);
  assert.match(v.vhDetNote, /Reading the certificate register/);
  assert.equal(v.vhHoisted.length, 0);
});

// ── the conversation ──────────────────────────────────────────────────────────

test('hoisting a detected vendor asks one question at a time, prefilled from its certificates', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhDet[0].hoist();
  let v = c.renderVals();
  assert.equal(c.state.flow, 'vhoist');
  assert.equal(v.fVhoist, true);
  assert.equal(v.vhcStep, 'Question 1 of 7');
  assert.match(v.vhcQ, /found this vendor on your certificates/);
  assert.equal(v.vhcFields[0].value, 'Crown Gas Services');
  assert.equal(v.vhcFields[0].tag, 'from certificates');
  assert.match(v.orchStatus, /nothing is written until you press Hoist vendor/);
  v.vhcNext();
  v = c.renderVals();
  assert.equal(v.vhcStep, 'Question 2 of 7');
  assert.equal(v.vhcDone.length, 1);
  assert.equal(v.vhcDone[0].a, 'Crown Gas Services · Gas');
  v.vhcBack();
  assert.equal(c.renderVals().vhcStep, 'Question 1 of 7');
});

test('a new vendor needs its legal name before the next question', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  let v = c.renderVals();
  assert.match(v.vhcQ, /hoist a new vendor/);
  v.vhcNext();
  v = c.renderVals();
  assert.equal(v.vhcStep, 'Question 1 of 7');
  assert.match(v.vhcNote, /legal name/);
  v.vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  c.renderVals().vhcNext();
  assert.equal(c.renderVals().vhcStep, 'Question 2 of 7');
  assert.equal(c.renderVals().vhcSkipShow, true);
});

test('the answers are echoed back, and nothing claims a register check that did not happen', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  c.renderVals().vhcNext();            // name → contact
  c.renderVals().vhcSkip();            // contact skipped
  c.renderVals().vhcFields[0].set({ target: { value: '10933271' } });
  c.renderVals().vhcNext();            // registration given
  const v = c.renderVals();
  assert.equal(v.vhcDone[1].a, 'Skipped');
  assert.match(v.vhcDone[2].reply, /kept in this browser only — not stored or checked/);
  assert.ok(!/active|found on/i.test(v.vhcDone[2].reply), 'no register was consulted');
});

test('the buildings question offers the company\'s own buildings, searchable', () => {
  as(ADMIN, 'user');
  c.vhBuildingNames = () => ['Ashgrove Court', 'Harbour Point', 'Kingsway House'];
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  for (let i = 0; i < 5; i += 1) c.renderVals().vhcNext();
  let v = c.renderVals();
  assert.equal(v.vhcStep, 'Question 6 of 7');
  assert.deepEqual(v.vhcChips.map((x) => x.label), ['Ashgrove Court', 'Harbour Point', 'Kingsway House']);
  v.vhcAreaSet({ target: { value: 'harb' } });
  v = c.renderVals();
  assert.deepEqual(v.vhcChips.map((x) => x.label), ['Harbour Point']);
  v.vhcChips[0].toggle();
  assert.equal(c.state.vhFlow.f.areas, 'Harbour Point');
  assert.equal(c.renderVals().vhcChips[0].on, true);
});

// ── saving ────────────────────────────────────────────────────────────────────
// Saving is switched on (Hussain, 12 Oct 2026). A detected vendor is created through its
// certificate (create-vendor makes the vendor and links that certificate) and its other
// certificates are linked to it; a brand-new vendor goes to the connector's vendor create. Only
// the name is sent to create-vendor: its other profile columns are not on every vendors table.
const saveOk = () => {
  handlers['POST ' + OI + 'c1/create-vendor'] = [200, { ok: true, certificate_id: 'c1', vendor_id: 'v-new', vendor_name: 'Crown Gas Services', created: true }];
  handlers['POST ' + OI + 'c2/create-vendor'] = [200, { ok: true, certificate_id: 'c2', vendor_id: 'v-new', vendor_name: 'Crown Gas Services', created: false }];
  handlers['POST ' + CN + 'vendor-contacts'] = [201, { id: 'ct-1' }];
  handlers['POST ' + CN + 'vendors'] = [201, { id: 'v-new-thames', organization_id: 'org-1', vendor_name: 'Thames Drainage Ltd', address: null, created_at: '2026-10-12T08:00:00Z' }];
  // The vendor list re-read after a save (logic/vendorMaster.js) — a read.
  handlers['GET ' + CN + 'vendors'] = [200, { total: 0, limit: 500, offset: 0, data: [] }];
};
const saveRefused = () => {
  handlers['POST ' + OI + 'c1/create-vendor'] = [200, { ok: false, error: 'certificate not found' }];
};
const hoistDetected = async (x) => { x.renderVals().vhDet[0].hoist(); toConfirm(x); x.renderVals().vhcFinish(); await settle(); };

test('nothing is written before Hoist vendor is pressed', () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  c.renderVals().vhDet[0].hoist();
  toConfirm(c);
  c.renderVals().vhcBack();
  assert.deepEqual(writes(), []);
  assert.match(c.renderVals().orchStatus, /nothing is written until you press Hoist vendor/);
});

test('Hoist vendor writes a detected vendor through its certificate and links its other certificates', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  c.renderVals().vhDet[0].hoist();
  toConfirm(c);
  let v = c.renderVals();
  assert.match(v.vhcSaveNote, /writes Crown Gas Services to the vendor master, linked to its 2 certificates/);
  v.vhcFinish();
  await settle();
  assert.deepEqual(writes().map((x) => [x.key, x.body]), [
    ['POST ' + OI + 'c1/create-vendor', { vendor_name: 'Crown Gas Services' }],
    // Every certificate through create-vendor: it reuses the vendor just made, by name, and
    // links a certificate with no source document too (/link refuses one).
    ['POST ' + OI + 'c2/create-vendor', { vendor_name: 'Crown Gas Services' }]
  ]);
  v = c.renderVals();
  assert.equal(c.state.flow, null);
  assert.match(c.state.flowDone, /Crown Gas Services is in the vendor master/);
  assert.match(c.state.flowDone, /Not stored \(no field for them yet\): trade/);
  assert.deepEqual(v.vhDet.map((x) => x.name), ['Kestrel Pest']);
  assert.equal(v.vhHoisted[0].name, 'Crown Gas Services');
  assert.equal(v.vhHoisted[0].status, 'In vendor master');
  assert.equal(v.vhHoisted[0].saved, true);
  assert.equal(v.vhHoisted[0].canSave, false);
  const stored = JSON.parse(mem[VH_STORE_KEY]);
  assert.equal(stored['org-1'].prepared[0].vendorId, 'v-new');
});

test('an accounts email or phone is saved as the vendor\'s contact', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  c.renderVals().vhDet[0].hoist();
  c.renderVals().vhcNext();
  c.renderVals().vhcFields[0].set({ target: { value: 'accounts@crowngas.co.uk' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  await settle();
  const ct = writes().find((x) => x.key === 'POST ' + CN + 'vendor-contacts');
  assert.deepEqual(ct.body, { vendor_id: 'v-new', name: 'Crown Gas Services', email: 'accounts@crowngas.co.uk', phone: null, designation: 'Accounts' });
  assert.match(c.state.flowDone, /accounts contact is saved/);
});

test('a brand-new vendor is created in the company\'s vendor master', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  await settle();
  assert.deepEqual(writes().map((x) => [x.key, x.body]), [
    ['POST ' + CN + 'vendors', { organization_id: 'org-1', vendor_name: 'Thames Drainage Ltd' }]
  ]);
  assert.equal(c.renderVals().vhHoisted[0].status, 'In vendor master');
});

test('a superadmin viewing as a company files a new vendor under that company', async () => {
  c = quiet(new HoistraLogic());
  c.authEnter(resp(SUPER));
  c.viewAsCompany('org-9', 'TechCorp');
  c.setState({ role: 'user', instance: 'full', view: 'vh', ccLive: register(c) });
  saveOk();
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  await settle();
  assert.equal(writes().find((x) => x.key === 'POST ' + CN + 'vendors').body.organization_id, 'org-9');
});

test('a refused save says why, keeps the record, and can be saved again from the page', async () => {
  as(ADMIN, 'user');
  saveRefused();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  let v = c.renderVals();
  assert.equal(c.state.flow, 'vhoist', 'the confirm card stays open to try again');
  assert.match(v.vhcError, /certificate not found/);
  assert.equal(v.vhHoisted[0].status, 'Not saved');
  assert.equal(v.vhHoisted[0].canSave, true);
  saveOk();
  v.vhHoisted[0].save();
  await settle();
  v = c.renderVals();
  assert.equal(v.vhHoisted[0].status, 'In vendor master');
  // The refused try, then both certificates through create-vendor.
  assert.equal(writes().filter((x) => /create-vendor/.test(x.key)).length, 3);
});

test('an HTTP error is a refusal too, and names the problem', async () => {
  as(ADMIN, 'user');
  handlers['POST ' + OI + 'c1/create-vendor'] = [500, { detail: 'Internal Server Error' }];
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  assert.equal(c.renderVals().vhHoisted[0].status, 'Not saved');
  assert.ok(c.renderVals().vhcError.length > 0);
});

test('a record prepared before saving existed can be saved from Hoisted vendors', async () => {
  as(ADMIN, 'user');
  saveOk();
  mem[VH_STORE_KEY] = JSON.stringify({ 'org-1': { dismissed: [], events: [], prepared: [
    { key: 'crown gas services', name: 'Crown Gas Services', trade: 'Gas', at: Date.now() - 60000, fields: { name: 'Crown Gas Services', trade: 'Gas' }, certIds: ['c1', 'c2'] }
  ] } });
  c.setState({ view: 'vh', vhLocal: null });
  const row = c.renderVals().vhHoisted[0];
  assert.equal(row.status, 'Not saved');
  row.save();
  await settle();
  assert.equal(c.renderVals().vhHoisted[0].status, 'In vendor master');
});

test('a record that was never saved can be discarded, and the vendor is detected again', async () => {
  as(ADMIN, 'user');
  saveRefused();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  c.closeOrch();
  c.renderVals().vhHoisted[0].discard();
  const v = c.renderVals();
  assert.equal(v.vhHoisted[0].name, 'AquaSafe');
  assert.deepEqual(v.vhDet.map((x) => x.name), ['Crown Gas Services', 'Kestrel Pest']);
});

test('Not a vendor hides it in this browser; Show hidden brings it back', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhDet[1].dismiss();
  let v = c.renderVals();
  assert.deepEqual(v.vhDet.map((x) => x.name), ['Crown Gas Services']);
  assert.equal(v.vhHiddenN, 1);
  v.vhShowHidden();
  v = c.renderVals();
  assert.deepEqual(v.vhDet.map((x) => x.name), ['Crown Gas Services', 'Kestrel Pest']);
  assert.deepEqual(writes(), [], 'Not a vendor is a decision in this browser, not a write');
});

test('a plain user cannot run the conversation even with it forced into state', () => {
  as(USER, 'user');
  c.setState({ flow: 'vhoist', vhFlow: { from: null, step: 0, f: { name: 'X' }, prepared: [] } });
  assert.equal(c.renderVals().fVhoist, false);
  assert.deepEqual(writes(), []);
});

test('what one company hoisted is not shown under another', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  c.setState({ account: Object.assign({}, c.state.account, { organization_id: 'org-2' }) });
  assert.equal(c.renderVals().vhHoisted.filter((x) => x.local).length, 0);
});

// ── the vendor audit trail ────────────────────────────────────────────────────
// The server records no hoist, so these entries are this browser's own record, shown in the
// admin's Vendor audit trail beside the server's and said to be exactly that.

const trail = (x) => { x.setState({ role: 'admin', view: 'audit', auTab: 'vendors', vaEvents: [] }); return x.renderVals(); };

test('a hoist is in the vendor trail, by whom, with the vendor id it was saved under', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  const v = trail(c);
  const rows = v.vaGroups.flatMap((g) => g.rows);
  assert.equal(rows.length, 1);
  const r = rows[0];
  assert.equal(r.badge, 'Hoisted');
  assert.equal(r.vendor, 'Crown Gas Services');
  assert.equal(r.who, 'Ada');
  assert.equal(r.role, 'Admin · this browser');
  assert.match(r.change, /Saved to the vendor master/);
  assert.match(r.fields.find((f) => f.l === 'In the vendor master').v, /^Yes — created/);
  assert.equal(r.fields.find((f) => f.l === 'Vendor id').v, 'v-new');
  assert.equal(r.fields.find((f) => f.l === 'Certificates linked').v, '2');
  assert.match(v.vaLocalNote, /1 hoisting entry is recorded in this browser/);
});

test('a failed save, Not a vendor and Discard are in the trail too, under the Hoisting filter', async () => {
  as(ADMIN, 'user');
  saveRefused();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  c.closeOrch();
  c.renderVals().vhDet[0].dismiss();
  c.renderVals().vhHoisted[0].discard();
  let v = trail(c);
  const rows = v.vaGroups.flatMap((g) => g.rows);
  assert.deepEqual(rows.map((r) => r.badge).sort(), ['Hoist discarded', 'Hoist failed', 'Not a vendor']);
  assert.match(rows.find((r) => r.badge === 'Hoist failed').fields.find((f) => f.l === 'Why it failed').v, /certificate not found/);
  const chip = v.vaFilters.find((f) => f.label === 'Hoisting');
  assert.equal(chip.count, 3);
  chip.pick();
  v = c.renderVals();
  assert.equal(v.vaInView, 3);
});

test('the trail merges the server\'s changes with this browser\'s, newest first', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  c.setState({ role: 'admin', view: 'audit', auTab: 'vendors', vaEvents: [
    { id: 'srv-1', kind: 'blocked', source: 'compliance_scan', at: '2020-01-01T09:00:00Z', vendor: { name: 'AquaSafe' }, reason: 'Lapsed' }
  ] });
  const rows = c.renderVals().vaGroups.flatMap((g) => g.rows);
  assert.deepEqual(rows.map((r) => r.badge), ['Hoisted', 'Blocked']);
});

test('another company\'s hoisting is not in this company\'s trail', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  await hoistDetected(c);
  c.setState({ account: Object.assign({}, c.state.account, { organization_id: 'org-2' }) });
  const v = trail(c);
  assert.equal(v.vaGroups.length, 0);
  assert.equal(v.vaLocalNote, '');
});

test('a new vendor whose name is already in the vendor list is not created twice', async () => {
  as(ADMIN, 'user');
  saveOk();
  handlers['GET ' + CN + 'vendors'] = [200, { total: 1, limit: 500, offset: 0, data: [{ id: 'v-7', vendor_name: 'Thames  drainage ltd' }] }];
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  await settle();
  assert.deepEqual(writes().map((x) => x.key), [], 'the list was re-read and the name found: nothing posted');
  assert.match(c.state.vhFlow.error, /already in your vendor list/);
});

test('the replies never claim a check that is not made', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  c.renderVals().vhcNext();
  c.renderVals().vhcSkip();
  c.renderVals().vhcFields[0].set({ target: { value: '10933271' } }); c.renderVals().vhcNext();
  c.renderVals().vhcFields[0].set({ target: { value: 'FM-2041' } }); c.renderVals().vhcNext();
  c.renderVals().vhcFields[0].set({ target: { value: 'Gas Safe · NICEIC' } }); c.renderVals().vhcNext();
  const replies = c.renderVals().vhcDone.map((d) => d.reply).join(' ');
  assert.ok(!/register|SLA|penalty|verified|unallocated|eligible only/i.test(replies), replies);
});

// ── certificates, attached in the hoist (12 Oct 2026) ──────────────────────────
// The last question takes the vendor's own certificates. Nothing is sent while the
// conversation runs: Hoist vendor saves the vendor first, then the files go through the same
// upload as Ingest documents (read, checked and filed), in the same panel, and the register is
// read again to say how many are now on record for the vendor.
const UPLOAD = 'POST /backend/deep-agents/api/workflow/run-stateful-with-files';
const pdf = (n) => new File(['%PDF-1.4 test'], n, { type: 'application/pdf' });
const toFiles = (x) => { for (let i = 0; i < 10 && !x.renderVals().vhcFilesShow; i += 1) x.renderVals().vhcNext(); };

test('the last question takes the vendor\'s certificates; files can be added and removed', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toFiles(c);
  let v = c.renderVals();
  assert.equal(v.vhcFilesShow, true);
  assert.match(v.vhcQ, /certificates/i);
  assert.equal(v.vhcStep, 'Question 7 of 7');
  v.vhcAddFiles({ target: { files: [pdf('gas-safe.pdf'), pdf('insurance.pdf')], value: '' } });
  v = c.renderVals();
  assert.deepEqual(v.vhcFileList.map((x) => x.name), ['gas-safe.pdf', 'insurance.pdf']);
  v.vhcFileList[1].remove();
  v = c.renderVals();
  assert.deepEqual(v.vhcFileList.map((x) => x.name), ['gas-safe.pdf']);
  v.vhcNext();
  v = c.renderVals();
  assert.equal(v.vhcConfirm, true);
  assert.deepEqual(v.vhcSummary.find((x) => x.l === 'Certificates').v, '1 attached · gas-safe.pdf');
  assert.match(v.vhcSaveNote, /Then the attached certificate is read and filed against Thames Drainage Ltd/);
  assert.deepEqual(writes(), [], 'nothing is sent before Hoist vendor');
});

test('an account that may not add data is not asked for certificates', () => {
  as(Object.assign({}, ADMIN, { can_ingest: false }), 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toConfirm(c);
  assert.equal(c.renderVals().vhcStep, 'Ready to hoist');
  assert.ok(!c.renderVals().vhcSummary.some((x) => x.l === 'Certificates'));
});

test('Hoist vendor saves the vendor, then uploads its certificates, then says how many are on record', async () => {
  as(ADMIN, 'user');
  saveOk();
  handlers[UPLOAD] = [200, { success: true, answer: 'Filed 1 certificate: Gas Safe registration for Thames Drainage Ltd.', session_id: 's-up' }];
  // The register read after the upload holds the new accreditation under the vendor.
  const before = c.state.ccLive.certs;
  let vpReads = 0;
  c.vpLoad = () => { vpReads += 1; };
  c.ccLoad = async () => {
    c.setState({ ccLive: Object.assign({}, c.state.ccLive, { certs: before.concat([
      { id: 'n1', kind: 'vendor', nm: 'Gas Safe registration', holder: 'Thames Drainage Ltd', vendor: 'Thames Drainage Ltd', vendorId: 'v-new-thames', vendorOnRecord: true, exp: '01 Jan 2028' }
    ]) }) });
  };
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('gas-safe.pdf')], value: '' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 20 && !(c.state.ccChat || []).some((t) => /on record for Thames/.test(t.text || '')); i += 1) await settle();
  const keys = writes().map((x) => x.key);
  assert.ok(keys.indexOf('POST ' + CN + 'vendors') > -1 && keys.indexOf(UPLOAD) > keys.indexOf('POST ' + CN + 'vendors'), 'the vendor first, then its certificates: ' + keys.join(', '));
  const up = writes().find((x) => x.key === UPLOAD).body;
  assert.equal(typeof up.getAll, 'function');
  assert.deepEqual(up.getAll('files').map((f) => f.name), ['gas-safe.pdf']);
  assert.match(String(up.get('message')), /for the vendor Thames Drainage Ltd/);
  assert.equal(up.get('building_id'), null, 'a vendor\'s own certificates go up with no building');
  const chat = c.state.ccChat;
  assert.equal(chat[0].role, 'bot');
  assert.match(chat[0].text, /Thames Drainage Ltd is in the vendor master/);
  assert.match(chat[chat.length - 1].text, /1 certificate is now on record for Thames Drainage Ltd/);
  // The Vendors page reads its own data again too, so its directory row is not left stale.
  assert.ok(vpReads > 0, 'the Vendors page data was read again');
});

test('with no certificates attached, Hoist vendor uploads nothing', async () => {
  as(ADMIN, 'user');
  saveOk();
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  await settle();
  assert.ok(!writes().some((x) => x.key === UPLOAD));
});

// ── buildings assigned, and certificates filed against them (12 Oct 2026) ──────
// The buildings question is stored now (compliance/vendor_buildings.py): Hoist vendor assigns
// the vendor to the buildings chosen. At the certificates question each file says what it is —
// the vendor's own accreditation, or a certificate for one of those buildings — and each group
// is uploaded against its own building.
const ASSIGN = (vid) => 'POST /backend/ops-intelligence/api/compliance/vendors/' + vid + '/buildings';
const LISTVB = 'GET /backend/ops-intelligence/api/compliance/vendor-buildings';
const ROWS = [{ name: 'Harbour Point', id: 'b-hp' }, { name: 'Kingsway House', id: 'b-kw' }];
const withBuildings = (x) => { x.iBuildingRows = () => ROWS; x.vhBuildingNames = () => ROWS.map((b) => b.name); return x; };
const pickArea = (x, name) => { for (let i = 0; i < 10 && !x.renderVals().vhcChipsShow; i += 1) x.renderVals().vhcNext(); x.renderVals().vhcChips.find((ch) => ch.label === name).toggle(); };

test('Hoist vendor assigns the vendor to the buildings chosen, and says so', async () => {
  as(ADMIN, 'user'); withBuildings(c);
  saveOk();
  handlers[ASSIGN('v-new-thames')] = [200, { ok: true, assigned: [{ id: 'b-kw', name: 'Kingsway House' }], already: [] }];
  handlers[LISTVB] = [200, { ok: true, available: true, assignments: [{ vendor_id: 'v-new-thames', vendor_name: 'Thames Drainage Ltd', building_id: 'b-kw', building_name: 'Kingsway House', source: 'hoist' }] }];
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  pickArea(c, 'Kingsway House');
  toConfirm(c);
  assert.match(c.renderVals().vhcSaveNote, /assigned to Kingsway House/);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 10 && !c.state.flowDone; i += 1) await settle();
  const a = writes().find((x) => x.key === ASSIGN('v-new-thames'));
  assert.deepEqual(a.body, { building_ids: ['b-kw'], source: 'hoist' });
  assert.match(c.state.flowDone, /Assigned to Kingsway House/);
  assert.ok(!/Not stored[^.]*buildings/.test(c.state.flowDone), 'buildings are stored now');
});

test('a database not set up for assignments says the buildings were not assigned', async () => {
  as(ADMIN, 'user'); withBuildings(c);
  saveOk();
  handlers[ASSIGN('v-new-thames')] = [503, { detail: { ok: false, reason: 'not_set_up', error: 'Assigning vendors to buildings is not set up on this database yet.' } }];
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  pickArea(c, 'Kingsway House');
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 10 && !c.state.flowDone; i += 1) await settle();
  assert.match(c.state.flowDone, /Not assigned to Kingsway House/);
});

test('each attached file says what it is, and each group goes up against its own building', async () => {
  as(ADMIN, 'user'); withBuildings(c);
  saveOk();
  handlers[ASSIGN('v-new-thames')] = [200, { ok: true, assigned: [{ id: 'b-kw', name: 'Kingsway House' }], already: [] }];
  handlers[UPLOAD] = [200, { success: true, answer: 'Filed.', session_id: 's-up' }];
  c.ccLoad = async () => {};
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  pickArea(c, 'Kingsway House');
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('gas-safe.pdf'), pdf('drain-survey.pdf')], value: '' } });
  let v = c.renderVals();
  assert.deepEqual(v.vhcFileList.map((f) => f.target), ['', '']);
  assert.deepEqual(v.vhcFileList[0].targets.map((t) => t.label), ["Vendor's own certificate", 'For Kingsway House']);
  v.vhcFileList[1].setTarget({ target: { value: 'Kingsway House' } });
  v = c.renderVals();
  assert.equal(v.vhcFileList[1].target, 'Kingsway House');
  toConfirm(c);
  assert.match(c.renderVals().vhcSummary.find((x) => x.l === 'Certificates').v, /drain-survey\.pdf → Kingsway House/);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 30 && writes().filter((x) => x.key === UPLOAD).length < 2; i += 1) await settle();
  const ups = writes().filter((x) => x.key === UPLOAD).map((x) => x.body);
  assert.equal(ups.length, 2);
  assert.deepEqual(ups[0].getAll('files').map((f) => f.name), ['gas-safe.pdf']);
  assert.equal(ups[0].get('building_id'), null);
  assert.deepEqual(ups[1].getAll('files').map((f) => f.name), ['drain-survey.pdf']);
  assert.equal(ups[1].get('building_id'), 'b-kw');
  assert.match(String(ups[1].get('message')), /for Kingsway House/);
  assert.equal(c.state.cbBuildingId || null, null, 'the chat is not left filing against that building');
});

test('assignments show on the vendor: in the register, on Buildings served, with a way to end one', async () => {
  const { withAssignments } = await import('../src/logic/vendorBuildings.js');
  const reg = { vendors: VENDORS.slice(), buildings: [{ name: 'Kingsway House' }], certs: CERTS };
  const out = withAssignments(reg, [
    { vendor_id: 'v-6', vendor_name: 'Quiet Co', building_id: 'b-kw', building_name: 'Kingsway House' },
    { vendor_id: 'v-new', vendor_name: 'Fresh Vendor Ltd', building_id: 'b-kw', building_name: 'Kingsway House' }
  ]);
  const quiet = out.vendors.find((x) => x.name === 'Quiet Co');
  assert.deepEqual(quiet.serves, ['Kingsway House']);
  assert.deepEqual(quiet.assigned, ['Kingsway House']);
  const fresh = out.vendors.find((x) => x.name === 'Fresh Vendor Ltd');
  assert.ok(fresh && fresh.id === 'v-new' && fresh.noAccreditations, 'a vendor with nothing else on file is listed by its assignment');
  assert.notEqual(out, reg, 'the register itself is not changed');
  assert.deepEqual(VENDORS.find((x) => x.name === 'Quiet Co').serves, []);
});

test('an upload that fails part-way never leaves the chat filing against a building', async () => {
  as(ADMIN, 'user'); withBuildings(c);
  saveOk();
  handlers[ASSIGN('v-new-thames')] = [200, { ok: true, assigned: [{ id: 'b-kw', name: 'Kingsway House' }], already: [] }];
  c.ccLoad = async () => {};
  const realAsk = c.ccAsk.bind(c);
  let n = 0;
  c.ccAsk = async (q) => { n += 1; if (n === 1) throw new Error('the orchestrator could not be reached'); return realAsk(q); };
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  pickArea(c, 'Kingsway House');
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('drain-survey.pdf')], value: '' } });
  c.renderVals().vhcFileList[0].setTarget({ target: { value: 'Kingsway House' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 20 && !(c.state.ccChat || []).some((t) => /could not be uploaded/.test(t.text || '')); i += 1) await settle();
  assert.equal(c.state.cbBuildingId || null, null);
  assert.equal(c.state.declForId || null, null);
  assert.ok((c.state.ccChat || []).some((t) => /could not be uploaded/.test(t.text || '')), 'the panel says the upload failed');
});

// ── second review, 12 Oct 2026 ─────────────────────────────────────────────────
test('a company switch while the hoist saves uploads nothing into the next company', async () => {
  as(ADMIN, 'user');
  saveOk();
  handlers[UPLOAD] = [200, { success: true, answer: 'Filed.', session_id: 's-up' }];
  const { HoistraLogic: HL } = await import('../src/logic/HoistraLogic.js');
  handlers['POST ' + CN + 'vendors'] = () => { HL.prototype.resetLiveData.call(c); return [201, { id: 'v-new-thames', organization_id: 'org-1', vendor_name: 'Thames Drainage Ltd' }]; };
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('gas-safe.pdf')], value: '' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 10; i += 1) await settle();
  assert.ok(!writes().some((x) => x.key === UPLOAD), 'no upload after the switch');
});

test('nothing in the conversation moves while it saves', () => {
  as(ADMIN, 'user');
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('a.pdf')], value: '' } });
  toConfirm(c);
  c.setState({ vhSaving: true });
  const step = c.state.vhFlow.step;
  c.renderVals().vhcBack();
  assert.equal(c.state.vhFlow.step, step);
});

test('a reply still running past the wait, or an upload that errors, is named as not uploaded', async () => {
  as(ADMIN, 'user');
  saveOk();
  handlers[UPLOAD] = [500, { detail: 'Internal Server Error' }];
  c.ccLoad = async () => {};
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('gas-safe.pdf')], value: '' } });
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 30 && !(c.state.ccChat || []).some((t) => /could not be uploaded/.test(t.text || '')); i += 1) await settle();
  assert.ok((c.state.ccChat || []).some((t) => /gas-safe\.pdf could not be uploaded/.test(t.text || '')));
  assert.deepEqual(c.state.ccFiles || [], [], 'nothing is left in the tray for the next message');
});

test('a file for a building that is no longer chosen goes up as the vendor\'s own', async () => {
  as(ADMIN, 'user'); withBuildings(c);
  saveOk();
  handlers[ASSIGN('v-new-thames')] = [200, { ok: true, assigned: [{ id: 'b-hp', name: 'Harbour Point' }], already: [] }];
  handlers[UPLOAD] = [200, { success: true, answer: 'Filed.', session_id: 's-up' }];
  c.ccLoad = async () => {};
  c.setState({ view: 'vh' });
  c.renderVals().vhNewForm();
  c.renderVals().vhcFields[0].set({ target: { value: 'Thames Drainage Ltd' } });
  pickArea(c, 'Kingsway House');
  toFiles(c);
  c.renderVals().vhcAddFiles({ target: { files: [pdf('survey.pdf')], value: '' } });
  c.renderVals().vhcFileList[0].setTarget({ target: { value: 'Kingsway House' } });
  // Back to the buildings question: Kingsway off, Harbour Point on.
  for (let i = 0; i < 3 && !c.renderVals().vhcChipsShow; i += 1) c.renderVals().vhcBack();
  c.renderVals().vhcChips.find((ch) => ch.label === 'Kingsway House').toggle();
  c.renderVals().vhcChips.find((ch) => ch.label === 'Harbour Point').toggle();
  toFiles(c);
  assert.equal(c.renderVals().vhcFileList[0].target, '');
  toConfirm(c);
  c.renderVals().vhcFinish();
  for (let i = 0; i < 30 && !writes().some((x) => x.key === UPLOAD); i += 1) await settle();
  const up = writes().find((x) => x.key === UPLOAD).body;
  assert.equal(up.get('building_id'), null);
});
