// A hoisted vendor shows up everywhere a vendor does (12 Oct 2026). Hoisting a vendor named on a
// building certificate links that certificate to it — and then it appeared nowhere: the
// compliance register's vendor list comes from coverage, which only knows vendors holding a
// VENDOR accreditation (or blocked), and the Vendors directory is built from scorecards,
// contracts and vendor accreditations. A vendor on record that serves a building through its
// building certificates is a vendor serving that building, with no accreditation on file yet.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { shapeLiveVendors } = await import('../src/logic/vendorsLive.js');
const { vendorStatus } = await import('../src/logic/compliance.js');

const LARK = '7d0b6f1e-1c2a-4d8e-9a61-3f2b8c9e0a11';
const certs = [
  { id: 'c1', cert_scope: 'Building', certificate_type_code: 'CP17', certificate_type_name: 'Gas Safety Certificate', building_name: 'Bishopsgate Tower',
    vendor_name: 'Larkfield Combustion Services Ltd', vendor_id: LARK, vendor_on_record: true, expiry_date: '2027-10-07', country_code: 'UK' },
  // Named on a certificate but not on record: not a vendor in the master, so not a vendor serving it.
  { id: 'c2', cert_scope: 'Building', certificate_type_code: 'EICR', building_name: 'Bishopsgate Tower',
    vendor_name: 'Unknown Sparks', vendor_id: null, vendor_on_record: false, expiry_date: '2027-01-01', country_code: 'UK' }
];

test('a vendor on record named on a building certificate serves that building, with no accreditation on file', () => {
  const cc = shapeLiveCompliance({ certificates: certs, coverage: { UK: { buildings: [], vendors: [] } }, packs: { UK: [] } });
  const v = cc.vendors.find((x) => x.name === 'Larkfield Combustion Services Ltd');
  assert.ok(v, 'the hoisted vendor is in the register');
  assert.equal(v.id, LARK);
  assert.deepEqual(v.serves, ['Bishopsgate Tower']);
  assert.equal(v.noAccreditations, true);
  assert.equal(cc.vendors.some((x) => x.name === 'Unknown Sparks'), false, 'a name not on record is not a vendor in the master');
});

test('its status says so — never "Clear", which claims every accreditation held is current', () => {
  const s = vendorStatus({ noAccreditations: true, worst: 'None on file', block: 'Clear' });
  assert.equal(s.label, 'No accreditations');
  assert.equal(s.tone, 'warn');
});

test('a vendor that already holds an accreditation is unchanged by this', () => {
  const cc = shapeLiveCompliance({
    certificates: certs.concat([{ id: 'c3', cert_scope: 'Vendor', certificate_type_code: 'GAS_SAFE', vendor_name: 'Larkfield Combustion Services Ltd', vendor_id: LARK, vendor_on_record: true, expiry_date: '2027-03-01', country_code: 'UK' }]),
    coverage: { UK: { buildings: [], vendors: [{ vendor_id: LARK, vendor_name: 'Larkfield Combustion Services Ltd', coverage_pct: 50, on_record: 1, required: 2, certificates_total: 1, gaps: ['ACS'] }] } },
    packs: { UK: [] }
  });
  const v = cc.vendors.find((x) => x.name === 'Larkfield Combustion Services Ltd');
  assert.equal(v.noAccreditations, false);
  assert.equal(v.cov, 50);
  assert.deepEqual(v.serves, ['Bishopsgate Tower']);
});

test('the Vendors directory lists a vendor that serves a building, even with no contract, card or accreditation', () => {
  const vm = shapeLiveVendors({ summary: { scorecards: [] }, contracts: { parameters: [] }, certificates: [],
    serving: [{ vendor_id: LARK, vendor_name: 'Larkfield Combustion Services Ltd', building: 'Bishopsgate Tower' }] });
  const row = vm.vendors.find((x) => x.id === LARK);
  assert.ok(row, 'in the directory');
  assert.equal(row.name, 'Larkfield Combustion Services Ltd');
  assert.equal(row.score, null, 'not scored — no number nobody computed');
  assert.match(row.meta, /no contract terms on record · no accreditation on record/);
  assert.ok(vm.V[LARK], 'its detail opens');
});

test('without the serving list the directory is as it was', () => {
  const vm = shapeLiveVendors({ summary: { scorecards: [] }, contracts: { parameters: [] }, certificates: [] });
  assert.equal(vm.vendors.length, 0);
});

// ── end to end, in the real controller ────────────────────────────────────────
// After a hoist the register is read again; the certificate now names a vendor on record, and
// that one read puts the vendor on the Hoist vendors list, in the Vendors directory and in the
// building's Vendor impact — with nothing seeded.
test('once the register re-reads, the hoisted vendor is in Hoist vendors, the Vendors directory and Vendor impact', async () => {
  const mem = {};
  globalThis.window = {
    location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
    localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
    sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
  };
  globalThis.WebSocket = class { constructor() { throw new Error('no sockets'); } };
  globalThis.fetch = async () => { throw new TypeError('no network'); };
  const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
  const c = new HoistraLogic();
  ['loadLiveData', 'resetLiveData', 'authLoadScope', 'usLiveLoad', 'auLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'crEnsureRun', 'ccLoad', 'vpLoad'].forEach((k) => { c[k] = () => {}; });
  const account = { id: 'u-2', email: 'ada@example.com', full_name: 'Ada', role: 'admin', status: 'active', organization_id: 'org-1', organization_name: 'Northbridge' };
  const cc = shapeLiveCompliance({ certificates: certs, coverage: { UK: { buildings: [{ site_name: 'Bishopsgate Tower', country_code: 'UK', coverage_pct: 26, on_record: 7, required: 27, certificates_total: 9, gaps: [] }], vendors: [] } }, packs: { UK: [] } });
  c.setState({ signedIn: true, account: account, role: 'user', instance: 'full', ccLive: cc,
    vpRaw: { fetchedAt: '2026-10-12T08:00:00Z', errors: {}, summary: { scorecards: [] }, contracts: { parameters: [] }, certificates: [] } });

  c.setState({ view: 'vh' });
  let v = c.renderVals();
  const row = v.vhHoisted.find((x) => x.name === 'Larkfield Combustion Services Ltd');
  assert.ok(row, 'on the Hoist vendors list');
  assert.equal(row.local, false, 'read from the register, not from this browser');
  assert.equal(row.status, 'No certificates');

  c.setState({ view: 'vp' });
  v = c.renderVals();
  assert.ok(v.vpList.some((x) => x.name === 'Larkfield Combustion Services Ltd'), 'in the Vendors directory');

  c.setState({ view: 'cc', ccCountries: ['UK'], ccStates: [], ccBuildings: [], ccFocus: { kind: 'building', name: 'Bishopsgate Tower' }, ccTab: 1 });
  v = c.renderVals();
  const imp = v.ccVendorRows.find((x) => x.name === 'Larkfield Combustion Services Ltd');
  assert.ok(imp, 'in Bishopsgate Tower\'s Vendor impact');
  assert.equal(imp.block, 'No accreditations');
  assert.equal(imp.gaps, 'No accreditations on file');
  clearInterval(c._orchTick);
});
