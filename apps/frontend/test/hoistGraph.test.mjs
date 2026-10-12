// The Compliance Graph report (12 Oct 2026). A permanent report in the Compliance Engine: in
// the navigator of the user view (an admin or a user) and of the admin view. It is the
// prototype's hospital "Building & Hoist Graph" page, card for card, drawn from the live
// compliance register: a row per building where the prototype had a floor, a card per
// regulated area (the pack's trade: Fire, Gas, Electrical…) where it had a hospital area, and
// in each card the obligations, the certificate's state and the vendor who holds it. Nothing
// from the prototype's hospital reaches it, and it has no "Hoist a building".
//
// Laid out to be read without scrolling (Hussain, 12 Oct 2026: "I have to scroll too much"): an
// overview table, a row per building and a column per area, then the picked building's cards.
//
// Why areas are the pack's regulated areas: a certificate names a building and nothing below
// it — no floor, no ward, no plant room — so there is no physical area to draw from.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
const tabMem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url) => { throw new TypeError('no network in tests: ' + url); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { hoistGraphModel } = await import('../src/logic/hoistGraph.js');

const day = (n) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
const pack = (code, name, scope, trade, reg) => ({ certificate_type_code: code, certificate_type_name: name, certificate_scope: scope, regulation_reference: reg, issuing_body: 'Body ' + code, trade_category: trade });
const cert = (id, scope, code, building, vendor, onRecord, expiry, extra) => Object.assign({
  id, cert_scope: scope, certificate_type_code: code, building_name: building, vendor_name: vendor,
  vendor_id: onRecord ? 'v-' + vendor : null, vendor_on_record: onRecord, expiry_date: expiry, country_code: 'UK', status: 'active'
}, extra || {});

// The UK pack, cut to four building obligations in three areas, and one vendor accreditation.
const PACK = [
  pack('GAS_SAFETY', 'Gas safety record', 'Building', 'Gas', 'Gas Safety (Installation and Use) Regulations 1998'),
  pack('EICR', 'Electrical installation condition report', 'Building', 'Electrical', 'BS 7671'),
  pack('FRA', 'Fire risk assessment', 'Building', 'Fire', 'Regulatory Reform (Fire Safety) Order 2005'),
  pack('FIRE_ALARM', 'Fire alarm service', 'Building', 'Fire', 'BS 5839-1'),
  pack('GAS_SAFE', 'Gas Safe registration', 'Vendor', 'Gas', 'Gas Safe Register')
];
const CERTS = [
  cert('c1', 'Building', 'GAS_SAFETY', 'Kingsway House', 'Crown Gas Services', true, day(300)),
  cert('c2', 'Building', 'EICR', 'Kingsway House', 'Sparks Electrical Ltd', true, day(10)),
  cert('c3', 'Building', 'GAS_SAFETY', 'Harbour Point', 'AquaSafe Hygiene', true, day(200)),
  cert('c4', 'Vendor', 'GAS_SAFE', null, 'AquaSafe Hygiene', true, day(-5)),
  cert('c5', 'Vendor', 'GAS_SAFE', null, 'Crown Gas Services', true, day(400)),
  cert('c6', 'Building', 'EICR', null, 'Sparks Electrical Ltd', true, day(100))
];
const covB = (name, extra) => Object.assign({ site_name: name, country_code: 'UK', state: 'London', coverage_pct: 0, on_record: 0, required: 4, certificates_total: 0, gaps: [], linked: true }, extra || {});
const COVERAGE = { UK: { buildings: [covB('Kingsway House'), covB('Harbour Point', { linked: false })], vendors: [] } };
const register = (over) => shapeLiveCompliance(Object.assign({ certificates: CERTS, coverage: COVERAGE, packs: { UK: PACK } }, over || {}));
const building = (m, name) => m.buildings.find((b) => b.name === name);
const area = (b, name) => b.areas.find((a) => a.name === name);

// ── the model ─────────────────────────────────────────────────────────────────

test('no register yet: nothing answered, no figure claimed', () => {
  assert.equal(hoistGraphModel(null).answered, false);
});

test('the five figures across the top are counted from the register', () => {
  assert.deepEqual(hoistGraphModel(register()).stats.map((x) => [x.l, x.v]), [
    ['Areas', '6'], ['Obligations', '8'], ['Certificates', '3'], ['Vendors', '3'], ['Accreditations', '2']
  ]);
});

test("a building's obligations sit in its regulated areas, each with the vendor who discharges it", () => {
  const kw = building(hoistGraphModel(register()), 'Kingsway House');
  assert.deepEqual(kw.areas.map((a) => [a.name, a.state]), [['Fire', 'risk'], ['Electrical', 'warn'], ['Gas', 'ok']]);
  const fire = area(kw, 'Fire');
  assert.deepEqual(fire.rows.map((r) => [r.code, r.tone, r.v]), [['FRA', 'risk', 'No certificate'], ['FIRE_ALARM', 'risk', 'No certificate']]);
  assert.equal(fire.label, 'Out of compliance');
  assert.equal(fire.sub, '2 obligations · 0 vendors');
  assert.equal(fire.use, '0 of 2 on record');
  const gas = area(kw, 'Gas');
  assert.equal(gas.rows[0].t, 'Gas safety record · Gas Safety (Installation and Use) Regulations 1998');
  assert.equal(gas.rows[0].v, 'Crown Gas Services');
  assert.equal(gas.label, 'Compliant');
  assert.equal(area(kw, 'Electrical').label, 'At risk');
});

test('a current certificate held by a vendor who is not eligible is at risk, not compliant', () => {
  const gas = area(building(hoistGraphModel(register()), 'Harbour Point'), 'Gas');
  assert.equal(gas.rows[0].tone, 'warn');
  assert.match(gas.rows[0].title, /AquaSafe Hygiene/);
  assert.match(gas.rows[0].title, /not eligible/i);
});

test('every obligation discharged, in date, by an eligible vendor: every area compliant', () => {
  const m = hoistGraphModel(register({
    certificates: ['GAS_SAFETY', 'EICR', 'FRA', 'FIRE_ALARM'].map((k, i) => cert('k' + i, 'Building', k, 'Kingsway House', 'Crown Gas Services', true, day(300)))
      .concat([cert('k9', 'Vendor', 'GAS_SAFE', null, 'Crown Gas Services', true, day(400))]),
    coverage: { UK: { buildings: [covB('Kingsway House')], vendors: [] } }
  }));
  assert.deepEqual(m.buildings[0].areas.map((a) => a.label), ['Compliant', 'Compliant', 'Compliant']);
});

test('certificates that name no building are counted outside the graph, not drawn as one', () => {
  const m = hoistGraphModel(register());
  assert.ok(!m.buildings.some((b) => /no building/i.test(b.name)));
  assert.equal(m.orphans, 1);
});

test("with no pack loaded for a building's country, its obligations are the certificates it holds", () => {
  const m = hoistGraphModel(register({ packs: {}, coverage: {}, certificates: CERTS.map((c) => Object.assign({ trade_category: c.certificate_type_code === 'EICR' ? 'Electrical' : 'Gas' }, c)) }));
  const kw = building(m, 'Kingsway House');
  assert.deepEqual(kw.areas.map((a) => a.name).sort(), ['Electrical', 'Gas']);
  assert.equal(kw.packMissing, true);
});

// ── the report in the app ─────────────────────────────────────────────────────

const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', role: 'user' };
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin' });
let c;
const make = (account, role, instance) => {
  c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad', 'vmLoad'].forEach((k) => { c[k] = () => {}; });
  c.setState({ signedIn: true, account: account, role: role, instance: instance, view: 'home', accessToken: 'tok' });
  return c;
};
const navLabels = () => c.renderVals().navSections.map((n) => n.label);
const openGraph = () => c.renderVals().navSections.find((n) => n.label === 'Compliance Graph').click();
beforeEach(() => { [mem, tabMem].forEach((m) => Object.keys(m).forEach((k) => { delete m[k]; })); });
afterEach(() => { if (c) clearInterval(c._orchTick); });

test('the Compliance Engine lists the report in User view, for a user and for an admin', () => {
  make(USER, 'user', 'comp');
  assert.deepEqual(navLabels(), ['Buildings', 'Compliance Graph', 'Compliance', 'Vendors']);
  make(ADMIN, 'user', 'comp');
  assert.deepEqual(navLabels(), ['Buildings', 'Compliance Graph', 'Compliance', 'Vendors']);
  openGraph();
  assert.equal(c.state.view, 'hg');
  const v = c.renderVals();
  assert.equal(v.isHG, true);
  assert.equal(v.navSections.find((n) => n.label === 'Compliance Graph').color, 'var(--color-accent)');
});

test("the Compliance Engine's admin view lists it too, and nothing else of the user view", () => {
  make(ADMIN, 'admin', 'comp');
  assert.deepEqual(navLabels(), ['Compliance Graph']);
  openGraph();
  assert.equal(c.state.view, 'hg');
  assert.equal(c.state.role, 'admin');
  assert.equal(c.renderVals().isHG, true);
});

test('no other instance has it, in either view', () => {
  make(USER, 'user', 'ops');
  assert.equal(navLabels().indexOf('Compliance Graph'), -1);
  make(USER, 'user', 'full');
  assert.equal(navLabels().indexOf('Compliance Graph'), -1);
  c.setState({ view: 'hg' });
  assert.equal(c.renderVals().isHG, false);
  make(ADMIN, 'admin', 'assets');
  assert.equal(c.renderVals().navSections.length, 0);
  c.setState({ view: 'hg' });
  assert.equal(c.renderVals().isHG, false);
});

test('before the register answers: dashes, and a line saying it is being read', () => {
  make(USER, 'user', 'comp');
  openGraph();
  c.setState({ ccLive: null, ccLoading: true });
  const v = c.renderVals();
  assert.deepEqual(v.hgStats.map((x) => x.v), ['—', '—', '—', '—', '—']);
  assert.match(v.hgStatus, /Reading the compliance register/);
  assert.equal(v.hgMatrix, null);
  assert.equal(v.hgPicked, null);
});

test('the overview: a row per building, a column per area, each cell held of required', () => {
  make(USER, 'user', 'comp');
  openGraph();
  c.setState({ ccLive: register() });
  const v = c.renderVals();
  assert.equal(v.hgTitle, 'Compliance Graph');
  assert.equal(v.hgStatus, '');
  assert.deepEqual(v.hgMatrix.cols, ['Gas', 'Electrical', 'Fire']);
  assert.deepEqual(v.hgMatrix.rows.map((r) => r.label), ['Harbour Point', 'Kingsway House']);
  const kw = v.hgMatrix.rows[1];
  assert.deepEqual(kw.cells.map((x) => x.v), ['1/1', '1/1', '0/2']);
  assert.equal(kw.out, '1 out');
  assert.equal(v.hgMatrix.rows[0].on, true, 'the first building is open to start with');
});

test("the picked building's areas are drawn as the prototype's cards; no Hoist a building", () => {
  make(USER, 'user', 'comp');
  openGraph();
  c.setState({ ccLive: register() });
  c.renderVals().hgMatrix.rows[1].pick();
  const v = c.renderVals();
  assert.equal(v.hgMatrix.rows[1].on, true);
  assert.equal(v.hgPicked.label, 'Kingsway House');
  assert.equal(v.hgPicked.line, '3 areas · 1 out of compliance');
  assert.deepEqual(v.hgPicked.cards.map((x) => [x.name, x.label]), [['Fire', 'Out of compliance'], ['Electrical', 'At risk'], ['Gas', 'Compliant']]);
  assert.deepEqual(v.hgPicked.cards[2].rows.map((r) => r.v), ['Crown Gas Services']);
  assert.deepEqual(v.hgActions.map((a) => a.label), ['Update the graph', 'Map an area to its obligations']);
  assert.ok(!JSON.stringify(v.hgActions).match(/Hoist a building/));
});

test('a cell opens its building and marks that area', () => {
  make(USER, 'user', 'comp');
  openGraph();
  c.setState({ ccLive: register() });
  c.renderVals().hgMatrix.rows[1].cells[2].pick();
  const v = c.renderVals();
  assert.equal(v.hgPicked.label, 'Kingsway House');
  assert.deepEqual(v.hgPicked.cards.filter((x) => x.focus).map((x) => x.name), ['Fire']);
});

test('an account that may not add data is not offered Update the graph', () => {
  make(Object.assign({}, USER, { can_ingest: false }), 'user', 'comp');
  openGraph();
  c.setState({ ccLive: register() });
  assert.deepEqual(c.renderVals().hgActions.map((a) => a.label), ['Map an area to its obligations']);
});

test('Update the graph opens the real document upload in the dock', () => {
  make(USER, 'user', 'comp');
  openGraph();
  c.setState({ ccLive: register() });
  c.renderVals().hgActions[0].run();
  assert.equal(c.state.orchOpen, true);
  assert.equal(c.state.flow, 'ingest');
});

test('the page survives a reload', async () => {
  const { saveSession, loadSession } = await import('../src/logic/session.js');
  saveSession({ signedIn: true, view: 'hg', role: 'user', instance: 'comp', account: USER, accessToken: 'a', refreshToken: 'r' });
  assert.equal(loadSession().view, 'hg');
});

test('a vendor deleted from this tab is not shown as fine before the register is read again', () => {
  const reg = register();
  // vendorDelete.js vdlDone drops the vendor from the register's vendors at once.
  const after = Object.assign({}, reg, { vendors: reg.vendors.filter((v) => v.name !== 'Crown Gas Services') });
  const gas = area(building(hoistGraphModel(after), 'Kingsway House'), 'Gas');
  assert.equal(gas.rows[0].tone, 'warn');
  assert.match(gas.rows[0].title, /no longer in your vendor list/);
});
