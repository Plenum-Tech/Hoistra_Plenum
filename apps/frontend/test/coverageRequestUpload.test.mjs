// Request on a vendor's Coverage row and Upload on a building's "Not on record" type (Hussain,
// 7 Oct 2026). Both used to log a task in the sessions list and do nothing else. Request now
// opens the email asking the vendor for that accreditation, addressed from the vendor's record
// and sent only on Send; Upload opens the ingest card already filing against the building and
// naming the certificate it is for.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { accreditationRequestDraft, canRequest } = await import('../src/logic/accreditationRequest.js');

const real = { contact: opsApi.vendorContact, sent: opsApi.sentEmails, send: opsApi.sendEmail };
let c = null;
const asked = [];
beforeEach(() => {
  asked.length = 0;
  opsApi.vendorContact = async (id) => { asked.push(id); return { ok: true, vendor: { id: id, name: 'Clearwater Compliance' }, email: 'office@clearwater.example', candidates: [] }; };
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
});
afterEach(() => {
  Object.assign(opsApi, { vendorContact: real.contact, sentEmails: real.sent, sendEmail: real.send });
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

const REG = shapeLiveCompliance({ certificates: [
  { id: 'b1', certificate_type_code: 'FRA', certificate_type_name: 'Fire Risk Assessment', cert_scope: 'Building',
    building_name: 'Bishopsgate Tower', vendor_name: 'Clearwater Compliance', vendor_id: 'v-cw', expiry_date: '2027-01-01', country_code: 'UK' },
  { id: 'v1', certificate_type_code: 'ISO_9001', certificate_type_name: 'ISO 9001:2015', cert_scope: 'Vendor',
    vendor_name: 'Clearwater Compliance', vendor_id: 'v-cw', expiry_date: '2027-06-01', country_code: 'UK' }
] });
const boot = (state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, ccLive: REG,
    account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates', role: 'admin', can_ingest: true,
      buildings: [{ id: 'bld-1', name: 'Bishopsgate Tower' }] } }, state));
  return c;
};
const settle = () => new Promise((r) => setTimeout(r, 20));

test('the email names the accreditation, why it is owed and where the vendor works', () => {
  const missing = accreditationRequestDraft('Clearwater Compliance', { name: 'ISO 14001:2015', status: 'Not on record', exp: '—' }, ['Bishopsgate Tower'], 'Northbridge Estates');
  assert.equal(missing.emSubject, 'Accreditation required — ISO 14001:2015 — Clearwater Compliance');
  assert.match(missing.emBody, /^Hello Clearwater Compliance team,/);
  assert.match(missing.emBody, /holds no ISO 14001:2015 for you\. It is a required accreditation for the work you carry out for us at Bishopsgate Tower\./);
  assert.match(missing.emBody, /Regards,\nNorthbridge Estates · Hoistra$/);
  const lapsed = accreditationRequestDraft('Apex', { name: 'Gas Safe', status: 'Lapsed', exp: '30 Sep 2026' }, [], null);
  assert.equal(lapsed.emSubject, 'Accreditation lapsed — Gas Safe — Apex');
  assert.match(lapsed.emBody, /lapsed on 30 Sep 2026/);
  const soon = accreditationRequestDraft('Apex', { name: 'Gas Safe', status: 'Expiring', exp: '20 Oct 2026' }, ['A', 'B', 'C', 'D'], null);
  assert.match(soon.emBody, /expires on 20 Oct 2026/);
  assert.match(soon.emBody, /at A, B and 2 more buildings/);
  assert.equal(canRequest({ status: 'Current' }), false, 'nothing to ask for a current accreditation');
  assert.ok(['Not on record', 'Lapsed', 'Expiring'].every((st) => canRequest({ status: st })));
});

test('Request opens the email to the vendor, addressed from its record, and sends nothing on its own', async () => {
  let sends = 0;
  opsApi.sendEmail = async () => { sends += 1; return { ok: true, status: 'sent' }; };
  boot({});
  await c.vpRequestAccreditation({ id: 'v-cw', name: 'Clearwater Compliance' }, { name: 'ISO 14001:2015', status: 'Not on record', exp: '—' });
  await settle();
  assert.equal(c.state.flow, 'email', 'the email card is open in the dock');
  assert.equal(c.state.emSubject, 'Accreditation required — ISO 14001:2015 — Clearwater Compliance');
  assert.deepEqual(asked, ['v-cw'], 'the vendor\'s own record is read for the address');
  assert.equal(c.state.emTo, 'office@clearwater.example');
  assert.match(c.state.emBody, /at Bishopsgate Tower/, 'the buildings the register says it serves');
  assert.equal(sends, 0, 'nothing goes out until Send');
  assert.equal(c.state.orchTask.title, 'Request ISO 14001:2015 — Clearwater Compliance');
});

test('Upload opens the ingest card filing against the building, naming the certificate', () => {
  boot({});
  c.ccUploadMissing('Asbestos Register (Living Document)', 'Bishopsgate Tower');
  assert.equal(c.state.flow, 'ingest');
  assert.equal(c.state.declFor, 'Bishopsgate Tower');
  assert.equal(c.state.declForId, 'bld-1', 'the building the file is bound to, by id');
  assert.equal(c.state.ingExpect, 'Asbestos Register (Living Document)');
  const v = c.renderVals();
  assert.equal(v.fIngest, true);
  assert.equal(v.iExpect, 'Asbestos Register (Living Document)');
});

test('Upload for a building the ingest cannot file against asks which building instead of guessing', () => {
  boot({});
  c.ccUploadMissing('Boiler Service Record', 'Somewhere Else');
  assert.equal(c.state.flow, 'ingest');
  assert.equal(c.state.declFor, '');
  assert.equal(c.state.declForId, null);
});

test('Upload is refused, with the reason, for an account that cannot ingest', () => {
  boot({ account: { email: 'u@x', role: 'user', can_ingest: false, buildings: [] }, role: 'user' });
  c.ccUploadMissing('Boiler Service Record', 'Bishopsgate Tower');
  assert.notEqual(c.state.flow, 'ingest');
  assert.match(String(c.state.toast), /cannot ingest/);
});
