// A deleted vendor is gone everywhere a vendor is listed (Hussain, 12 Oct 2026) — the mirror of
// test/hoistedVendorEverywhere.test.mjs. A vendor with history is RETIRED, not removed: its
// certificates, scorecards and contract terms stay, as history, and still carry its id. The
// page's lists are built as a union of those registers, so without a rule each of them would
// put the retired vendor straight back — in the Vendors directory, the Compliance vendor lists,
// Hoist vendors and Home's counts. The service marks what it knows: certificates of a retired
// vendor carry vendor_deleted: true (vendor_on_record stays true, so Hoist vendors never offers
// it again), and a vendor list row carries status 'deleted'. History rows keep the name the
// service gives them.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };
globalThis.fetch = async () => { throw new TypeError('no network'); };

const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { shapeLiveVendors } = await import('../src/logic/vendorsLive.js');
const { shapeVendorList } = await import('../src/logic/vendorMaster.js');
const { detectedVendors, masterVendors } = await import('../src/logic/vendorHoist.js');

const ACME = 'V-01';
const LARK = '7d0b6f1e-1c2a-4d8e-9a61-3f2b8c9e0a11';
const certs = [
  // The retired vendor's accreditation and a building certificate it is named on.
  { id: 'c1', cert_scope: 'Vendor', certificate_type_code: 'GAS_SAFE', vendor_name: 'Acme Ltd (deleted)', vendor_id: ACME,
    vendor_on_record: true, vendor_deleted: true, expiry_date: '2027-03-01', country_code: 'UK' },
  { id: 'c2', cert_scope: 'Building', certificate_type_code: 'CP17', certificate_type_name: 'Gas Safety Certificate', building_name: 'Bishopsgate Tower',
    vendor_name: 'Acme Ltd (deleted)', vendor_id: ACME, vendor_on_record: true, vendor_deleted: true, expiry_date: '2027-10-07', country_code: 'UK' },
  // A live vendor beside it.
  { id: 'c3', cert_scope: 'Building', certificate_type_code: 'EICR', building_name: 'Bishopsgate Tower',
    vendor_name: 'Larkfield Combustion Services Ltd', vendor_id: LARK, vendor_on_record: true, expiry_date: '2027-01-01', country_code: 'UK' }
];
const coverage = { UK: { buildings: [], vendors: [
  { vendor_id: ACME, vendor_name: 'Acme Ltd (deleted)', coverage_pct: 50, on_record: 1, required: 2, certificates_total: 1, gaps: [] }
] } };

// ── the compliance register ───────────────────────────────────────────────────

test('a retired vendor is in no compliance vendor list and serves no building', () => {
  const cc = shapeLiveCompliance({ certificates: certs, coverage: coverage, packs: { UK: [] } });
  assert.deepEqual(cc.vendors.map((v) => v.name), ['Larkfield Combustion Services Ltd']);
  assert.equal(cc.vendors.some((v) => v.id === ACME), false, 'not from its accreditation, its building certificate or a coverage row');
});

test('its certificates stay on the register as history, under the name the service gives them', () => {
  const cc = shapeLiveCompliance({ certificates: certs, coverage: coverage, packs: { UK: [] } });
  const c2 = cc.certs.find((c) => c.id === 'c2');
  assert.equal(c2.vendor, 'Acme Ltd (deleted)');
  assert.equal(c2.vendorDeleted, true);
  assert.equal(c2.vendorOnRecord, true, 'on record — so Hoist vendors never offers it as new');
});

test('a certificate with no deleted flag reads as before', () => {
  const cc = shapeLiveCompliance({ certificates: [certs[2]], coverage: { UK: { buildings: [], vendors: [] } }, packs: { UK: [] } });
  assert.equal(cc.certs[0].vendorDeleted, false);
  assert.deepEqual(cc.vendors.map((v) => v.id), [LARK]);
});

// ── the Vendors directory ─────────────────────────────────────────────────────

const card = (id, name, score) => ({ vendor_id: id, vendor_name: name, score_month: '2026-09-01', overall_score: score, component_breakdown: {} });
const base = (over) => Object.assign({ summary: { scorecards: [card(ACME, 'Acme Ltd', 72), card(LARK, 'Larkfield Combustion Services Ltd', 88)] },
  contracts: { parameters: [] }, certificates: [] }, over || {});

test('the directory drops a vendor whose accreditations say it is deleted, though its scorecard remains', () => {
  const vm = shapeLiveVendors(base({ certificates: [certs[0]] }));
  assert.deepEqual(vm.vendors.map((v) => v.id), [LARK]);
  assert.equal(vm.V[ACME], undefined);
});

test('the directory drops a vendor the vendor list marks deleted', () => {
  const vm = shapeLiveVendors(base({ master: [{ id: ACME, name: 'Acme Ltd (deleted)', status: 'deleted' }] }));
  assert.deepEqual(vm.vendors.map((v) => v.id), [LARK]);
});

test('the directory drops a vendor deleted from this tab, whatever the reads still carry', () => {
  const vm = shapeLiveVendors(base({ gone: [ACME] }));
  assert.deepEqual(vm.vendors.map((v) => v.id), [LARK]);
});

test('a vendor re-onboarded under the old name is still listed, though the retired one\'s card carries that name', () => {
  const vm = shapeLiveVendors(base({ gone: [ACME], master: [{ id: 'v-new', name: 'Acme Ltd' }] }));
  assert.deepEqual(vm.vendors.map((v) => v.id).sort(), [LARK, 'v-new'].sort());
});

test('a building served by a retired vendor does not list it', () => {
  const vm = shapeLiveVendors(base({ summary: { scorecards: [] },
    serving: [{ vendor_id: ACME, vendor_name: 'Acme Ltd (deleted)', building: 'Bishopsgate Tower' }], gone: [ACME] }));
  assert.equal(vm.vendors.length, 0);
});

// ── the vendor list itself ────────────────────────────────────────────────────

test('the vendor list drops a row whose status is deleted', () => {
  assert.deepEqual(shapeVendorList([
    { id: ACME, vendor_name: 'Acme Ltd (deleted)', status: 'deleted' },
    { id: LARK, vendor_name: 'Larkfield Combustion Services Ltd', status: 'Active' },
    { id: 'v-3', vendor_name: 'No Status Ltd' }
  ]).map((r) => r.id), [LARK, 'v-3']);
});

// ── Hoist vendors ─────────────────────────────────────────────────────────────

test('Hoist vendors never detects a retired vendor\'s certificates as a new vendor', () => {
  // Register rows as complianceLive shapes them. Even were a build to send a retired vendor's
  // certificate as not on record, the deleted flag keeps it out; an unlinked name still shows.
  const rows = [
    { id: 'c1', kind: 'building', nm: 'Gas safety record', building: 'Bishopsgate Tower', holder: 'Bishopsgate Tower', vendor: 'Acme Ltd (deleted)', vendorId: ACME, vendorOnRecord: false, vendorDeleted: true, exp: '07 Oct 2027' },
    { id: 'c2', kind: 'building', nm: 'EICR', building: 'Bishopsgate Tower', holder: 'Bishopsgate Tower', vendor: 'Unknown Sparks', vendorId: null, vendorOnRecord: false, vendorDeleted: false, exp: '01 Jan 2027' }
  ];
  assert.deepEqual(detectedVendors(rows, []).map((d) => d.name), ['Unknown Sparks']);
});

test('the vendor master never lists a vendor deleted from this tab', () => {
  const list = [
    { name: 'Acme Ltd', id: ACME, block: 'Clear', worst: 'OK', spec: '—' },
    { name: 'Larkfield Combustion Services Ltd', id: LARK, block: 'Clear', worst: 'OK', spec: '—' }
  ];
  assert.deepEqual(masterVendors(list, [ACME]).map((v) => v.id), [LARK]);
  assert.deepEqual(masterVendors(list).map((v) => v.id), [ACME, LARK], 'nothing gone: as before');
});

// ── end to end, in the real controller ────────────────────────────────────────
// What the service answers after a retire, read once: the vendor is in no list and no count.
test('after the registers re-read, a retired vendor is absent from the directory, Compliance, Hoist vendors and Home', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad'].forEach((k) => { c[k] = () => {}; });
  const account = { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin', status: 'active', organization_id: 'org-1', organization_name: 'Northbridge' };
  const cc = shapeLiveCompliance({ certificates: certs, coverage: coverage, packs: { UK: [] } });
  c.setState({ signedIn: true, account: account, role: 'user', instance: 'full', ccLive: cc,
    // The retired vendor's scorecard and its vendor-scope certificate are history: still read.
    vpRaw: { fetchedAt: '2026-10-12T08:00:00Z', errors: {}, summary: { scorecards: [card(ACME, 'Acme Ltd', 72)] }, contracts: { parameters: [] }, certificates: [certs[0]] },
    vmRows: [{ id: LARK, name: 'Larkfield Combustion Services Ltd' }] });

  c.setState({ view: 'vp' });
  let v = c.renderVals();
  assert.deepEqual(v.vpList.map((x) => x.name), ['Larkfield Combustion Services Ltd'], 'the directory');

  c.setState({ view: 'vh' });
  v = c.renderVals();
  assert.deepEqual(v.vhHoisted.map((x) => x.name), ['Larkfield Combustion Services Ltd'], 'Hoist vendors\' master');
  assert.equal(v.vhDet.length, 0, 'nothing detected');

  c.setState({ view: 'cc', ccCountries: ['UK'], ccStates: [], ccBuildings: [], ccFocus: { kind: 'building', name: 'Bishopsgate Tower' }, ccTab: 1 });
  v = c.renderVals();
  assert.deepEqual(v.ccVendorRows.map((x) => x.name), ['Larkfield Combustion Services Ltd'], 'Bishopsgate Tower\'s Vendor impact');

  assert.equal(c.homeModel().hero.vendors, 1, 'Home counts one vendor');
  clearInterval(c._orchTick);
});

test('a vendor deleted from this tab is gone from Hoist vendors at once, before any read', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad'].forEach((k) => { c[k] = () => {}; });
  const account = { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin', status: 'active', organization_id: 'org-1', organization_name: 'Northbridge' };
  c.setState({ signedIn: true, account: account, role: 'user', instance: 'full', view: 'vh', vdlGone: [ACME],
    ccLive: Object.assign({}, c.ccData(), { certs: [], buildings: [], vendors: [{ name: 'Acme Ltd', id: ACME, block: 'Clear', worst: 'OK', spec: '—', serves: [], gaps: [] }] }),
    vmRows: [{ id: ACME, name: 'Acme Ltd' }, { id: LARK, name: 'Larkfield Combustion Services Ltd' }] });
  assert.deepEqual(c.renderVals().vhHoisted.map((x) => x.name), ['Larkfield Combustion Services Ltd']);
  clearInterval(c._orchTick);
});

test('a removed vendor named again on a new, unlinked certificate is detected again, though this browser once hoisted it', async () => {
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const { VH_STORE_KEY } = await import('../src/logic/vendorHoist.js');
  mem[VH_STORE_KEY] = JSON.stringify({ 'org-1': { dismissed: [], events: [],
    prepared: [{ key: 'acme ltd', name: 'Acme Ltd', saved: true, vendorId: ACME, deleted: true, savedAt: 1, fields: {}, certIds: [] }] } });
  const c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad'].forEach((k) => { c[k] = () => {}; });
  const account = { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin', status: 'active', organization_id: 'org-1', organization_name: 'Northbridge' };
  c.setState({ signedIn: true, account: account, role: 'user', instance: 'full', view: 'vh', vhLocal: null,
    ccLive: Object.assign({}, c.ccData(), { buildings: [], vendors: [], certs: [
      { id: 'c9', kind: 'building', nm: 'Gas safety record', building: 'Bishopsgate Tower', holder: 'Bishopsgate Tower', vendor: 'Acme Ltd', vendorId: null, vendorOnRecord: false, vendorDeleted: false, exp: '01 Jan 2028' }
    ] }) });
  const v = c.renderVals();
  assert.deepEqual(v.vhDet.map((d) => d.name), ['Acme Ltd']);
  assert.equal(v.vhHoisted.some((x) => x.name === 'Acme Ltd'), false, 'and the old hoist is not listed');
  delete mem[VH_STORE_KEY];
  clearInterval(c._orchTick);
});

// ── what a retired contractor on a building certificate is still offered for (12 Oct 2026) ──
// A retire keeps a building's certificates as history, naming "<vendor> (deleted)". Nothing may
// offer that vendor as the one to ask: not the Compliance matrix cell's next steps, not a
// Decision-queue booking or remedial draft, not the Compliance Graph's count of vendors.
const { cellSubject, cellActions, cellContext } = await import('../src/logic/complianceCell.js');
const { planQueueAction } = await import('../src/logic/queueDraft.js');
const { hoistGraphModel } = await import('../src/logic/hoistGraph.js');

const gpack = (code, name, scope, trade, requires) => ({ certificate_type_code: code, certificate_type_name: name, certificate_scope: scope,
  trade_category: trade, required_contractor_accreditation: requires || null });
const GPACK = [gpack('CP17', 'Gas Safety (CP17)', 'Building', 'Gas', 'Gas Safe'), gpack('GAS_SAFE', 'Gas Safe registration', 'Vendor', 'Gas')];
const retiredReg = (days) => shapeLiveCompliance({
  certificates: [
    // Acme's building certificate, expiring inside 30 days, and its accreditation — both history.
    { id: 'r1', cert_scope: 'Building', certificate_type_code: 'CP17', building_name: 'Bishopsgate Tower', vendor_name: 'Acme Ltd (deleted)',
      vendor_id: ACME, vendor_on_record: true, vendor_deleted: true, days_to_expiry: days || 20, expiry_date: days ? '2027-04-30' : '2026-11-01', country_code: 'UK', certificate_number: 'CP17-1' },
    { id: 'r2', cert_scope: 'Vendor', certificate_type_code: 'GAS_SAFE', vendor_name: 'Acme Ltd (deleted)', vendor_id: ACME,
      vendor_on_record: true, vendor_deleted: true, days_to_expiry: -3, expiry_date: '2026-10-09', country_code: 'UK' }
  ],
  coverage: { UK: { buildings: [{ site_name: 'Bishopsgate Tower', country_code: 'UK', state: 'London', coverage_pct: 0, on_record: 0, required: 1,
    certificates_total: 1, gaps: [], linked: true }], vendors: [] } },
  packs: { UK: GPACK }
});

test('the matrix cell never asks a retired contractor to book a visit or renew an accreditation', () => {
  const reg = retiredReg();
  const s = cellSubject(reg, 'Bishopsgate Tower', reg.mxCodes.indexOf('CP17'));
  assert.ok(s, 'the cell is there');
  assert.deepEqual(s.vendors, [], 'no vendor to ask');
  const acts = cellActions(s);
  assert.equal(acts.some((a) => /Acme/.test(a.title)), false, acts.map((a) => a.title).join(' | '));
  assert.equal(acts.some((a) => a.action.draft === 'visit' || a.action.draft === 'accreditation'), false);
  assert.doesNotMatch(cellContext(s, acts), /Ask Acme|Email Acme/);
});

const qItem = (type, payload) => ({ id: 'q-' + type, source_feature: 'A', item_type: type, summary: 'Gas Safety (CP17) — Bishopsgate Tower',
  severity: 'High', status: 'pending', created_at: '2026-10-06T12:00:00Z', related_entity_type: 'compliance_certificate',
  related_entity_id: 'r1', payload: Object.assign({ certificate_id: 'r1' }, payload || {}) });
const qPlan = (it) => planQueueAction({ kind: 'approval', module: 'Compliance', title: it.summary, item: it },
  { approvals: [it], certs: retiredReg().certs, vendors: {}, sender: 'Northbridge Estates' });
const GONE_LINE = 'The contractor on this certificate has been deleted — add the recipient below.';

test('a booking for a retired contractor\'s certificate is addressed to nobody, and says why', () => {
  const p = qPlan(qItem('booking_request'));
  assert.equal(p.lookup, null, 'no contact read for the retired vendor');
  assert.equal(p.draft.fVendor, '');
  assert.match(p.draft.emBody, /^Hello,\n/);
  assert.doesNotMatch(p.draft.emBody, /Acme/);
  assert.equal(p.steps[2].t.endsWith(GONE_LINE), true, p.steps[2].t);
});

test('a remedial request for a retired contractor\'s certificate is addressed to nobody, and says why', () => {
  const p = qPlan(qItem('remedial', { defects_found: 'Flue spillage' }));
  assert.equal(p.lookup, null);
  assert.match(p.draft.emBody, /^Hello,\n/);
  assert.equal(p.steps[2].t.endsWith(GONE_LINE), true, p.steps[2].t);
});

test('the Compliance Graph says a retired contractor\'s work has no current vendor, and does not count it', () => {
  const m = hoistGraphModel(retiredReg(200));
  const row = m.buildings[0].areas[0].rows[0];
  assert.equal(row.tone, 'warn', 'in date, but nobody on record can renew it');
  assert.match(row.title, /Acme Ltd \(deleted\) — deleted; no current vendor for this work/);
  assert.equal(m.stats.find((x) => x.l === 'Vendors').v, '0');
  assert.equal(m.stats.find((x) => x.l === 'Accreditations').v, '0');
});
