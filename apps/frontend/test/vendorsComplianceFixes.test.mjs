// The review of 8 Oct 2026 on the Compliance ⇄ Vendors changes: what the Vendors page shows of
// the compliance register, where a vendor link lands, and what a draft or an upload carries.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { ApiError } = await import('../src/api/client.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');

const real = { contact: opsApi.vendorContact, sent: opsApi.sentEmails };
let c = null;
beforeEach(() => {
  opsApi.vendorContact = async (id) => ({ ok: true, vendor: { id, name: 'Clearwater Compliance' }, email: 'office@clearwater.example', candidates: [] });
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
});
afterEach(() => {
  Object.assign(opsApi, { vendorContact: real.contact, sentEmails: real.sent });
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); clearTimeout(c._vpFlashT); clearTimeout(c._vpJumpT); }
  c = null;
});

const REG = shapeLiveCompliance({ certificates: [
  { id: 'b1', certificate_type_code: 'FRA', certificate_type_name: 'Fire Risk Assessment', cert_scope: 'Building',
    building_name: 'Harbour Point', vendor_name: 'Clearwater Compliance', vendor_id: 'v-cw', expiry_date: '2027-01-01', country_code: 'UK' },
  { id: 'v1', certificate_type_code: 'ISO_9001', certificate_type_name: 'ISO 9001:2015', cert_scope: 'Vendor',
    vendor_name: 'Clearwater Compliance', vendor_id: 'v-cw', expiry_date: '2027-06-01', country_code: 'UK' }
] });
const RAW = {
  fetchedAt: '2026-10-07T10:00:00Z', errors: {},
  contracts: { parameters: [{ id: 'p-cw', vendor_id: 'v-cw', vendor_name: 'Clearwater Compliance' }, { id: 'p-pen', vendor_id: 'v-pennard', vendor_name: 'Pennard Fire Services' }] },
  approvals: { items: [] },
  certificates: [
    { id: 'c-bafe', cert_scope: 'vendor', vendor_id: 'v-pennard', vendor_name: 'Pennard Fire Services', certificate_type_code: 'BAFE', certificate_type_name: 'BAFE SP203-1',
      status: 'Lapsed', expiry_date: '2026-09-30', country_code: 'UK', vendor_block_state: 'blocked' },
    // Holds an accreditation, has no scorecard and no contract terms.
    { id: 'c-nic', cert_scope: 'vendor', vendor_id: 'v-ostley', vendor_name: 'Ostley Power Services', certificate_type_code: 'NICEIC', certificate_type_name: 'NICEIC Approved Contractor',
      status: 'Lapsed', expiry_date: '2025-09-06', country_code: 'UK', vendor_block_state: 'blocked' }
  ],
  coverage: { UK: [] }, packs: { UK: [] }, woScores: { wo_scores: [] }
};
const boot = (state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, view: 'vp', vpRaw: RAW,
    account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates', role: 'admin', can_ingest: true,
      buildings: [{ id: 'bld-1', name: 'Harbour Point' }] } }, state));
  return c;
};
const tab = (v, label) => v.vpTabs.find((t) => t.label === label);

test('a live vendor is never shown the sample register while the real one has not loaded', () => {
  boot({ ccLive: null, vpVendor: 'v-pennard', vpTab: 5 });
  const v = c.renderVals();
  assert.deepEqual(v.vpAccredRows, [], 'the sample BAFE row is not this vendor\'s');
  assert.deepEqual(v.vpServedRows, [], 'nor are the sample buildings');
  assert.equal(tab(v, 'Accreditations').n, '—');
  c.vpRequestAccreditation({ id: 'v-pennard', name: 'Pennard Fire Services' }, { name: 'BAFE SP203-1', status: 'Lapsed', exp: '30 Sep 2026' });
  assert.doesNotMatch(c.state.emBody, /Bishopsgate Tower|Kingsway House/, 'a real email never names sample buildings');
});

test('a vendor that only holds accreditations has its own page', () => {
  boot({ ccLive: REG });
  assert.ok(c.vpModel().vendors.some((x) => x.id === 'v-ostley'), 'listed in the directory');
  let toast = null;
  c.flash = (m) => { toast = m; };
  c.vpOpenVendor('Ostley Power Services', 'v-ostley', 5);
  assert.equal(c.state.vpVendor, 'v-ostley');
  assert.equal(c.state.vpTab, 5);
  assert.equal(toast, null);
});

test('opening a vendor from elsewhere closes an insight list left open', () => {
  boot({ ccLive: REG });
  c.renderVals().vpTiles.find((t) => t.label === 'Vendors blocked').click();
  assert.equal(c.renderVals().vpQueueShow, true);
  c.vpOpenVendor('Pennard Fire Services', 'v-pennard', 5);
  assert.equal(c.renderVals().vpQueueShow, false);
  c.renderVals().vpTiles.find((t) => t.label === 'Vendors blocked').click();
  c.vpOpenVendors();
  assert.equal(c.renderVals().vpQueueShow, false);
});

test('a building served opens that building, not a Compliance list left open', () => {
  boot({ ccLive: REG, vpVendor: 'v-cw', vpTab: 6, ccQueue: 'needs-you', ccQueueOpenId: 'x' });
  const row = c.renderVals().vpServedRows.find((r) => r.name === 'Harbour Point');
  row.open();
  assert.equal(c.state.view, 'cc');
  assert.equal(c.state.ccQueue, null);
  assert.deepEqual(c.state.ccFocus, { kind: 'building', name: 'Harbour Point' });
});

test("an Upload's certificate type does not follow into the next ingest", () => {
  boot({ ccLive: REG, view: 'cc' });
  c.ccUploadMissing('Gas Safety', 'Harbour Point');
  assert.equal(c.state.ingExpect, 'Gas Safety');
  c.closeOrch();
  assert.equal(c.state.ingExpect, '');
  c.ccUploadMissing('Gas Safety', 'Harbour Point');
  c.orchWith('Ingest documents', 'Harbour Point', 'ingest', { declFor: 'Harbour Point' });
  assert.equal(c.state.ingExpect, '');
});

test('Upload joins an open conversation rather than wiping it', () => {
  const turns = [{ q: 'Which certificates lapse this month?', a: 'Two.' }];
  boot({ ccLive: REG, view: 'cc', orchOpen: true, ccChat: turns });
  c.ccUploadMissing('Gas Safety', 'Harbour Point');
  assert.equal(c.state.ccChat, turns);
});

test("the send confirmation is about this draft, not the last one's subject", () => {
  boot({ ccLive: REG, fSubject: 'Booking — boiler service' });
  c.vpRequestAccreditation({ id: 'v-cw', name: 'Clearwater Compliance' }, { name: 'ISO 14001:2015', status: 'Not on record', exp: '—' });
  assert.equal(c.state.fSubject, 'ISO 14001:2015');
});

test('the orchestrator is told the tab the reader is on', () => {
  boot({ ccLive: REG, vpVendor: 'v-cw', vpTab: 5 });
  assert.match(c.chatContext(), /tab: Accreditations/);
  c.setState({ vpTab: 6 });
  assert.match(c.chatContext(), /tab: Buildings served/);
});

test('a route the server does not have yet is not reported as a vendor off the record', async () => {
  boot({ ccLive: REG });
  opsApi.vendorContact = async () => { throw new ApiError('Not Found', 404, { detail: 'Not Found' }); };
  await c.vpRequestAccreditation({ id: 'v-cw', name: 'Clearwater Compliance' }, { name: 'ISO 14001:2015', status: 'Not on record', exp: '—' });
  assert.match(c.state.orchTask.steps[2].t, /could not be read/);
  opsApi.vendorContact = async () => { throw new ApiError('Vendor not found', 404, { detail: { reason: 'not_found', error: 'Vendor not found' } }); };
  await c.vpRequestAccreditation({ id: 'v-cw', name: 'Clearwater Compliance' }, { name: 'ISO 14001:2015', status: 'Not on record', exp: '—' });
  assert.match(c.state.orchTask.steps[2].t, /not on a record you can see/);
});
