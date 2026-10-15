// The Vendors page's insight cards open their lists in place, like Compliance's Needs-you
// cards (Hussain, 7 Oct 2026) — instead of jumping to whichever single vendor came first.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');

let c = null;
const real = { contact: opsApi.vendorContact, sent: opsApi.sentEmails };
afterEach(() => {
  Object.assign(opsApi, { vendorContact: real.contact, sentEmails: real.sent });
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); clearTimeout(c._vpFlashT); clearTimeout(c._vpJumpT); }
  c = null;
});

const FLAG = { id: 'q-inv', item_type: 'invoice_flag', severity: 'medium', status: 'pending', created_at: '2026-10-05T10:00:00Z',
  related_entity_type: 'vendor', related_entity_id: 'v-apex', summary: 'Invoice INV-2847 line 3 over the contracted rate',
  payload: { invoice_ref: 'INV-2847', vendor_id: 'v-apex', line: { line_id: 3, wo_code: 'WO-118', delta_gbp: 42.5, discrepancy: 'Labour rate above contract' } } };
const CRIT = { id: 'q-crit', item_type: 'fm_report_staleness', severity: 'critical', status: 'pending', created_at: '2026-10-06T10:00:00Z',
  related_entity_type: 'vendor', related_entity_id: 'v-pennard', summary: 'FM reports overdue — Pennard Fire Services', payload: { data_as_of: '2026-08-31' } };
const RAW = {
  fetchedAt: '2026-10-07T10:00:00Z', errors: {},
  contracts: { parameters: [{ id: 'p-apex', vendor_id: 'v-apex', vendor_name: 'Apex Mechanical', contract_ref: 'AM-2024-07', field_sources: { contract_ref: 'contract', sla_response_p1_hours: 'default', sla_response_p2_hours: 'default' } }, { id: 'p-pen', vendor_id: 'v-pennard', vendor_name: 'Pennard Fire Services' }] },
  approvals: { items: [FLAG, CRIT] },
  certificates: [
    { id: 'c-bafe', cert_scope: 'vendor', vendor_id: 'v-pennard', vendor_name: 'Pennard Fire Services', certificate_type_code: 'BAFE', certificate_type_name: 'BAFE SP203-1',
      status: 'Lapsed', expiry_date: '2026-09-30', country_code: 'UK', vendor_block_state: 'blocked' }
  ],
  coverage: { UK: [] }, packs: { UK: [] }, woScores: { wo_scores: [] }
};
const boot = () => {
  c = new HoistraLogic({});
  c.setState({ signedIn: true, view: 'vp', vpRaw: RAW, account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates' } });
  return c;
};
const tile = (label) => c.renderVals().vpTiles.find((t) => t.label === label);

test('a card opens its own list in place, and the overview steps aside', () => {
  boot();
  assert.equal(c.renderVals().vpQueueShow, false);
  tile('Vendors blocked').click();
  const v = c.renderVals();
  assert.equal(v.vpQueueShow, true);
  assert.equal(v.vpBrowse, false, 'stats and the directory make way for the list');
  assert.equal(v.vpQTitle, 'Vendors blocked');
  assert.deepEqual(v.vpQRows.map((r) => r.title), ['Pennard Fire Services']);
  assert.equal(tile('Vendors blocked').active, true);
  tile('Vendors blocked').click();
  assert.equal(c.renderVals().vpQueueShow, false, 'the same card again closes it');
});

test('a blocked vendor\'s first action is the request for what it is short of', () => {
  boot();
  opsApi.vendorContact = async () => ({ ok: true, vendor: { id: 'v-pennard', name: 'Pennard Fire Services' }, email: 'ops@pennard.example', candidates: [] });
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
  tile('Vendors blocked').click();
  const r = c.renderVals().vpQRows[0];
  assert.match(r.sub, /1 lapsed/);
  r.toggle();
  const open = c.renderVals().vpQRows[0];
  assert.equal(open.open, true);
  assert.equal(open.actions[0].label, 'Request BAFE SP203-1');
  open.actions[0].run();
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emSubject, 'Accreditation lapsed — BAFE SP203-1 — Pennard Fire Services');
  assert.equal(c.state.vpQueue, null, 'the list closes once an action is taken');
});

test('pending tasks list every item, critical first; Pending critical keeps only those', () => {
  boot();
  tile('Pending tasks').click();
  assert.deepEqual(c.renderVals().vpQRows.map((r) => r.title), ['FM reports overdue — Pennard Fire Services', 'Invoice INV-2847 line 3 over the contracted rate']);
  tile('Pending critical').click();
  const v = c.renderVals();
  assert.equal(v.vpQTitle, 'Pending critical');
  assert.deepEqual(v.vpQRows.map((r) => r.title), ['FM reports overdue — Pennard Fire Services']);
  assert.equal(v.vpQRows[0].chips[0].label, 'Critical');
});

test('acting on a pending task opens it the way the Decision queue does', () => {
  boot();
  tile('Pending critical').click();
  let opened = null;
  c.queueOpenItem = (card) => { opened = card; };
  const r = c.renderVals().vpQRows[0];
  r.toggle();
  c.renderVals().vpQRows[0].actions[0].run();
  assert.equal(opened.kind, 'approval');
  assert.equal(opened.item.id, 'q-crit');
});

test('a held invoice line can be queried with the vendor or opened on its invoices tab', () => {
  boot();
  tile('Invoice lines held').click();
  const v = c.renderVals();
  assert.equal(v.vpQRows.length, 1);
  assert.match(v.vpQRows[0].title, /INV-2847/);
  assert.match(v.vpQRows[0].meta, /Labour rate above contract/);
  v.vpQRows[0].toggle();
  const acts = c.renderVals().vpQRows[0].actions.map((a) => a.label);
  assert.deepEqual(acts, ['Query the invoice', 'Open invoices']);
  c.renderVals().vpQRows[0].actions[1].run();
  assert.equal(c.state.vpTab, 4);
  assert.equal(c.state.vpVendor, 'v-apex');
  assert.equal(c.state.vpQueue, null);
});

test('terms on default list the vendors with defaults, each with its confirm action', () => {
  boot();
  tile('Terms on default').click();
  const v = c.renderVals();
  assert.ok(v.vpQRows.length >= 1);
  assert.match(v.vpQRows[0].sub, /terms on platform default/);
  v.vpQRows[0].toggle();
  assert.equal(c.renderVals().vpQRows[0].actions[0].label, 'Review and confirm terms');
});

test('Back closes the list', () => {
  boot();
  tile('L1 breaches').click();
  assert.equal(c.renderVals().vpQueueShow, true);
  c.renderVals().vpQClose();
  assert.equal(c.renderVals().vpQueueShow, false);
});

test('terms on default says how many terms and across how many vendors — the card counts terms', () => {
  boot();
  tile('Terms on default').click();
  const v = c.renderVals();
  assert.match(v.vpQCount, /^\d+ terms? across 1 vendor$/);
  assert.equal(v.vpQNote, '', 'no "more counted than read" note comparing terms with vendors');
});
