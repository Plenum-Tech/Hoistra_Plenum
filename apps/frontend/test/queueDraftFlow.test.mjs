// A Decision-queue card, clicked, through the real controller and renderVals (Hussain, 7 Oct
// 2026): the page it is about opens, the dock opens beside it with the email that resolves it,
// and one press of Approve & send sends it. Before this a live card opened a read-only drawer
// whose one button went to the page, and the draft — the compliance engine had usually written
// one — was never on screen: "the solution is lost and the user has to go and search for it".
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { energyApi } = await import('../src/api/energy.js');
const { complianceApi } = await import('../src/api/compliance.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { shapeLiveQueue } = await import('../src/logic/queueLive.js');

const real = { contact: opsApi.vendorContact, send: opsApi.sendEmail, sent: opsApi.sentEmails, approvals: opsApi.approvals,
  anomalies: opsApi.anomalies, assetVendor: energyApi.assetVendor, renewal: complianceApi.draftRenewalEmail };
let c = null;
// The vendor's record answers with its primary contact unless a test says otherwise; the engine
// draft's own `to` is deliberately different, so a test that sees it has caught it being used.
const PENNARD = { ok: true, vendor: { id: 'v-pennard', name: 'Pennard Fire Services' }, email: 'ops@pennardfire.co.uk', candidates: [] };
beforeEach(() => {
  opsApi.vendorContact = async () => PENNARD;
  // No history by default: a draft that was never sent stays the original request.
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
});
afterEach(() => {
  Object.assign(opsApi, { vendorContact: real.contact, sendEmail: real.send, sentEmails: real.sent,
    approvals: real.approvals, anomalies: real.anomalies });
  energyApi.assetVendor = real.assetVendor;
  complianceApi.draftRenewalEmail = real.renewal;
  if (c) {
    clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry);
    clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh);
  }
  c = null;
});

const REG = shapeLiveCompliance({ certificates: [
  { id: 'c-bafe', certificate_type_code: 'BAFE_SP203_1', certificate_type_name: 'BAFE SP203-1', cert_scope: 'Vendor',
    vendor_name: 'Pennard Fire Services', vendor_id: 'v-pennard', expiry_date: '2026-09-30T12:00:00Z',
    days_to_expiry: -7, certificate_number: 'SP203-4471', country_code: 'UK', status: 'Lapsed' }
] });
const ENGINE = { to: 'first-contact@pennard.example', subject: '[URGENT] Accreditation renewal — BAFE SP203-1 — Pennard Fire Services',
  body: 'Dear Pennard Fire Services,\n\nYour accreditation (BAFE SP203-1) has lapsed and requires renewal.\n' };
const approval = (over) => Object.assign({
  id: 'q1', source_feature: 'A', item_type: 'vendor_email', summary: 'Lapsed: Pennard Fire Services — BAFE SP203-1',
  severity: 'Critical', status: 'pending', created_at: '2026-10-06T12:00:00Z',
  related_entity_type: 'vendor', related_entity_id: 'v-pennard',
  payload: { certificate_id: 'c-bafe', risk: 'Lapsed' }, email_draft: ENGINE
}, over || {});
const LAPSED_WO = { work_order: 'WO-B-301-4562', asset: 'Fire alarm panel — ground', building: 'Bishopsgate Tower',
  vendor: 'Pennard Fire Services', vendor_id: 'v-pennard', estimated_cost: 650, currency: 'GBP', state: 'Blocked',
  source: 'Vendors', trigger: "Work order held at status 'Blocked'",
  detail: "Blocked — accreditation: Pennard Fire Services's BAFE SP203-1 has lapsed; the loop 2 fault waits for an accredited contractor" };

const boot = (state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates' } }, state));
  return c;
};
const firstCard = () => c.renderVals().queueItems[0];

test('a compliance card opens the Vendors page and the dock with the engine\'s email, ready to send', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG, queueOpen: true });
  await firstCard().click();
  // A vendor's accreditation opens on the Vendors page now, not a Compliance vendor view (7 Oct 2026).
  assert.equal(c.state.view, 'vp');
  assert.equal(c.state.queueOpen, false);
  assert.equal(c.state.detail, null, 'no drawer laid over the page');
  assert.equal(c.state.orchOpen, true);
  assert.equal(c.state.emTo, 'ops@pennardfire.co.uk');
  assert.equal(c.state.emSubject, ENGINE.subject);
  assert.equal(c.state.emQueueItemId, 'q1');
  const v = c.renderVals();
  assert.equal(v.isVP, true);
  assert.equal(v.fEmail, true, 'the email card is on screen');
  assert.equal(c.state.sessions[0].title, 'Renewal request — BAFE SP203-1 — Pennard Fire Services');
});

test('Approve & send posts the draft with its queue item, says what happened, and re-reads the queue', async () => {
  const sent = [];
  let reread = 0;
  opsApi.approvals = async () => { reread += 1; return { ok: true, items: [] }; };
  opsApi.anomalies = async () => ({ ok: true, anomalies: [] });
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  opsApi.sendEmail = async (b) => { sent.push(b); return { ok: true, status: 'sent' }; };
  await c.renderVals().em.send();
  assert.deepEqual(sent, [{ to: 'ops@pennardfire.co.uk', subject: ENGINE.subject, body: ENGINE.body, queue_item_id: 'q1' }]);
  assert.equal(c.state.flow, null);
  assert.equal(c.state.flowDone,
    'Renewal request sent to ops@pennardfire.co.uk about BAFE SP203-1 — Pennard Fire Services. It is recorded in the sent-email log and on the queue item.');
  assert.equal(c.state.emSentLabel, '', 'the next draft confirms in its own words');
  assert.equal(c.state.emQueueItemId, null, 'the next draft starts clean');
  await new Promise((r) => setImmediate(r));
  assert.equal(reread, 1, 'the card can show its email went out');
});

test('a dry run is never reported as sent, and keeps nothing tied to the item', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  opsApi.sendEmail = async () => ({ ok: true, status: 'dry_run' });
  await c.renderVals().em.send();
  assert.match(c.state.flowDone, /NOT delivered/);
  assert.equal(c.state.emQueueItemId, null);
});

test('a send that failed keeps the draft and its queue item, so the retry is recorded against it', async () => {
  const sent = [];
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  opsApi.sendEmail = async (b) => { sent.push(b); return sent.length === 1 ? { ok: false, error: 'mailbox refused' } : { ok: true, status: 'sent' }; };
  await c.renderVals().em.send();
  assert.equal(c.state.flow, 'email');
  assert.match(c.state.flowDone, /Not sent — mailbox refused/);
  assert.equal(c.state.emQueueItemId, 'q1');
  await c.renderVals().em.send();
  assert.equal(sent[1].queue_item_id, 'q1');
});

test('a maintenance card held on a lapsed accreditation opens the Vendors page, and the vendor\'s address fills in from its record', async () => {
  const asked = [];
  opsApi.vendorContact = async (id) => { asked.push(id); return { ok: true, vendor: { id: id, name: 'Pennard Fire Services' }, email: 'ops@pennardfire.co.uk', candidates: [] }; };
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  await firstCard().click();
  assert.equal(c.state.view, 'vp');
  assert.deepEqual(asked, ['v-pennard']);
  assert.equal(c.state.emTo, 'ops@pennardfire.co.uk');
  assert.equal(c.state.emSubject, 'Accreditation lapsed — BAFE SP203-1 — Pennard Fire Services');
  assert.equal(c.state.orchTask.steps[2].t,
    "Vendors opened on Pennard Fire Services. Addressed to ops@pennardfire.co.uk — Pennard Fire Services's contact on record.");
  assert.equal(c.state.sessions[0].steps[2].t, c.state.orchTask.steps[2].t, 'the session record says the same');
});

test('an address typed while the read was out is kept', async () => {
  let release;
  opsApi.vendorContact = () => new Promise((r) => { release = r; });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  const opening = firstCard().click();
  c.renderVals().em.setTo({ target: { value: 'fire@pennard.example' } });
  release({ ok: true, vendor: { id: 'v-pennard', name: 'Pennard Fire Services' }, email: 'ops@pennardfire.co.uk', candidates: [] });
  await opening;
  assert.equal(c.state.emTo, 'fire@pennard.example');
});

test('a read that answers after the reader moved on leaves the new task alone', async () => {
  let release;
  opsApi.vendorContact = () => new Promise((r) => { release = r; });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  const opening = firstCard().click();
  c.orch('Ask something else', 'Home');
  c.setState({ flow: 'email', emTo: '', emSubject: 'Another draft', emBody: 'Hello,\n\nSomething else.' });
  const steps = c.state.orchTask.steps;
  release({ ok: true, vendor: { id: 'v-pennard', name: 'Pennard Fire Services' }, email: 'ops@pennardfire.co.uk', candidates: [] });
  await opening;
  assert.equal(c.state.emTo, '');
  assert.equal(c.state.orchTask.steps, steps);
});

test('several contacts and none primary leave the To line to the reader, and the dock says why', async () => {
  opsApi.vendorContact = async () => ({ ok: true, vendor: { id: 'v-pennard', name: 'Pennard Fire Services' }, email: null,
    candidates: ['a@pennard.example', 'b@pennard.example'] });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  await firstCard().click();
  assert.equal(c.state.emTo, '');
  assert.match(c.state.orchTask.steps[2].t, /none marked primary, so the address is left for you: a@pennard\.example, b@pennard\.example\./);
});

test('a vendor record that cannot be read says so instead of passing for an empty one', async () => {
  opsApi.vendorContact = async () => { throw Object.assign(new Error('Service Unavailable'), { status: 503 }); };
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  await firstCard().click();
  assert.equal(c.state.emTo, '');
  assert.match(c.state.orchTask.steps[2].t, /Pennard Fire Services's record could not be read just now/);
});

test('an overdue work order opens Maintenance with a chaser', async () => {
  opsApi.vendorContact = async () => ({ ok: true, vendor: { id: 'v-mm', name: 'Meridian Mechanical Ltd' }, email: 'jobs@meridian.example', candidates: [] });
  boot({ mxRaw: { decisions: { decisions: [{ work_order: 'WO-B-101-17', asset: 'Chiller 2', building: 'Harbour Point',
    vendor: 'Meridian Mechanical Ltd', vendor_id: 'v-mm', state: 'Deviation', due: '2026-10-03' }] } } });
  await firstCard().click();
  assert.equal(c.state.view, 'module');
  assert.equal(c.state.module, 'ops');
  assert.equal(c.state.emSubject, 'Overdue — WO-B-101-17 · Chiller 2, Harbour Point');
  assert.equal(c.state.emTo, 'jobs@meridian.example');
  assert.equal(c.state.emQueueItemId, null);
});

test('an energy anomaly opens Energy; the asset\'s vendor names the greeting and the address', async () => {
  energyApi.assetVendor = async (id) => ({ ok: true, asset: { id: id }, vendor: { id: 'v-apex', name: 'Apex Mechanical' },
    contacts: { email: 'ops@apexmechanical.example', candidates: [] } });
  boot({ homeRaw: { anomalies: { anomalies: [{ id: 'n1', anomaly_type: 'weekend_spike', status: 'open',
    detected_at: '2026-09-25T12:00:00Z', metric_pct: 303, financial_gbp: 38400, asset_id: 'a1', asset_code: 'AHU-3',
    building_name: 'Bishopsgate Tower' }] } } });
  await firstCard().click();
  assert.equal(c.state.view, 'module');
  assert.equal(c.state.module, 'energy');
  assert.equal(c.state.emTo, 'ops@apexmechanical.example');
  assert.match(c.state.emBody, /^Hello Apex Mechanical team,\n\nThe energy engine has an open weekend spike on AHU-3/);
  assert.equal(c.state.fVendor, 'Apex Mechanical');
});

test('a flagged invoice opens Vendors on that vendor\'s Invoices tab with the query', async () => {
  opsApi.vendorContact = async () => ({ ok: true, vendor: { id: 'v-apex', name: 'Apex Mechanical' }, email: 'accounts@apex.example', candidates: [] });
  boot({
    vpRaw: { contracts: { ok: true, parameters: [{ id: 'p1', vendor_id: 'v-apex', vendor_name: 'Apex Mechanical', contract_ref: 'X-1' }] },
      approvals: { items: [] }, certificates: [], coverage: {}, packs: {} },
    homeRaw: { approvals: { items: [{ id: 'b1', source_feature: 'B', item_type: 'invoice_flag', severity: 'medium',
      summary: 'Invoice INV-2847 line flagged £84.00', created_at: '2026-10-05T12:00:00Z', related_entity_type: 'invoice',
      related_entity_id: 'v-apex', payload: { invoice_ref: 'INV-2847', vendor_id: 'v-apex', line: { line_id: 4, delta_gbp: 84 } } }] } }
  });
  await firstCard().click();
  assert.equal(c.state.view, 'vp');
  assert.equal(c.state.vpVendor, 'v-apex');
  assert.equal(c.state.vpTab, 4);
  assert.equal(c.state.emSubject, 'Invoice query — INV-2847 line 4');
  assert.equal(c.state.emTo, 'accounts@apex.example');
});

test('a sample card opens its page with a draft, reads nothing and fills no address', async () => {
  let reads = 0;
  opsApi.vendorContact = async () => { reads += 1; return {}; };
  boot({});
  const gas = c.renderVals().queueItems.find((i) => /Gas Safe/.test(i.title));
  await gas.click();
  assert.equal(c.state.view, 'cc');
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emTo, '');
  assert.equal(reads, 0);
});

test('a draft opened next — even one that does not start a task — is not tied to the queue item', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  assert.equal(c.state.emQueueItemId, 'q1');
  complianceApi.draftRenewalEmail = async () => ({ ok: true, email_draft: { to: 'x@y.example', subject: 'Renewal', body: 'Please renew.' } });
  await c.ccRunOffer({ cert_id: 'c-bafe', kind: 'renewal_email', label: 'Draft renewal email' });
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emQueueItemId, null);
  await firstCard().click();
  c.orch('Raise work order', 'AHU-3 · Bishopsgate Tower');
  assert.equal(c.state.emQueueItemId, null);
});

test('a compliance card whose email already went out says so in the queue', () => {
  const m = shapeLiveQueue({ approvals: { items: [approval({ email_sent: true, email_sent_status: 'sent',
    email_sent_at: '2026-10-07T12:00:00Z', email_sent_to: 'ops@pennardfire.co.uk' })] } });
  assert.match(m.items[0].meta, / · email sent \d{2} Oct 2026 \d{2}:\d{2} to ops@pennardfire\.co\.uk$/);
});

test('a dry run recorded on the item is never shown as sent', () => {
  // The backend marks the item email_sent for a dry run too (its `ok` is true); only the status says.
  const m = shapeLiveQueue({ approvals: { items: [approval({ email_sent: true, email_sent_status: 'dry_run',
    email_sent_at: '2026-10-07T12:00:00Z', email_sent_to: 'ops@pennardfire.co.uk' })] } });
  assert.doesNotMatch(m.items[0].meta, /email sent/);
  assert.match(m.items[0].meta, / · email recorded, not delivered \(dry run\)$/);
});

test('a previous draft\'s "already sent" note is not shown over the new one while its address is read', async () => {
  let release;
  opsApi.vendorContact = () => new Promise((r) => { release = r; });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG,
    emPrev: { count: 1, items: [] }, emOrig: { subject: 'Old request' }, emReminder: true });
  const opening = firstCard().click();
  assert.equal(c.state.emPrev, null);
  assert.equal(c.state.emOrig, null);
  assert.equal(c.state.emReminder, false);
  release(PENNARD);
  await opening;
});

test('an address read that answers after another draft opened in the same task leaves that draft alone', async () => {
  let release;
  opsApi.vendorContact = () => new Promise((r) => { release = r; });
  complianceApi.draftRenewalEmail = async () => ({ ok: true, email_draft: { to: '', subject: 'Renewal required — EICR', body: 'Please renew.' } });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  const opening = firstCard().click();
  await c.ccRunOffer({ cert_id: 'c-bafe', kind: 'renewal_email', label: 'Draft renewal email' });
  release(PENNARD);
  await opening;
  assert.equal(c.state.emSubject, 'Renewal required — EICR');
  assert.equal(c.state.emTo, '', 'the queue vendor\'s address went to no other draft');
});

test('Cancel clears what the queue draft armed', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  c.renderVals().fCancel();
  assert.equal(c.state.flow, null);
  assert.equal(c.state.emQueueItemId, null);
  assert.equal(c.state.emSentLabel, '');
  assert.equal(c.state.emSentNote, '');
});

test('a new task starts with no queue item and no queue wording', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  c.orch('Raise work order', 'AHU-3 · Bishopsgate Tower');
  assert.equal(c.state.emQueueItemId, null);
  assert.equal(c.state.emSentLabel, '');
  assert.equal(c.state.emSentNote, '');
  assert.equal(c.state.emSample, false);
});

test('a vendor record the asset read could not open is said to be unreadable, not missing', async () => {
  energyApi.assetVendor = async () => ({ ok: true, asset: { id: 'a1' }, vendor: null, contacts: { email: null, candidates: [] },
    unreadable: ['the vendor record'] });
  boot({ homeRaw: { anomalies: { anomalies: [{ id: 'n1', anomaly_type: 'weekend_spike', status: 'open', asset_id: 'a1',
    asset_code: 'AHU-3', building_name: 'Bishopsgate Tower' }] } } });
  await firstCard().click();
  assert.match(c.state.orchTask.steps[2].t, /could not be read just now/);
  assert.doesNotMatch(c.state.orchTask.steps[2].t, /No vendor on the asset record/);
});

test('a sample card\'s draft cannot be sent, even with an address typed', async () => {
  let sends = 0;
  opsApi.sendEmail = async () => { sends += 1; return { ok: true, status: 'sent' }; };
  boot({});
  await c.renderVals().queueItems.find((i) => /Gas Safe/.test(i.title)).click();
  c.renderVals().em.setTo({ target: { value: 'someone@example.com' } });
  await c.renderVals().em.send();
  assert.equal(sends, 0);
  assert.equal(c.state.flow, 'email', 'the draft stays on screen');
  assert.match(c.state.flowDone, /sample card/i);
  assert.equal(c.renderVals().em.sample, true);
});

test('a reminder is confirmed as a reminder', async () => {
  opsApi.sentEmails = async () => ({ ok: true, count: 1, reminders: 0, first_sent_at: '2026-10-01T12:00:00Z',
    last_sent_at: '2026-10-01T12:00:00Z', last_to: 'ops@pennardfire.co.uk', items: [{ to: 'ops@pennardfire.co.uk', reminder: false }] });
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  assert.equal(c.state.emReminder, true);
  await c.renderVals().em.send();
  assert.match(c.state.flowDone, /^Reminder sent to ops@pennardfire\.co\.uk/);
});

test('a click while a chat answer is streaming does not cut the answer off', async () => {
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG, view: 'chat', ccBusy: true,
    ccChat: [{ role: 'user', text: 'Which vendors are blocked?' }, { role: 'bot', text: 'Reading…' }] });
  let reset = 0;
  const realReset = c.ccChatReset.bind(c);
  c.ccChatReset = () => { reset += 1; return realReset(); };
  await firstCard().click();
  assert.equal(reset, 0);
  assert.equal(c.state.ccChat.length, 2);
  assert.equal(c.state.view, 'vp');
  assert.equal(c.state.flow, 'email');
});

test('a send that failed does not re-read the queue', async () => {
  let reread = 0;
  opsApi.approvals = async () => { reread += 1; return { ok: true, items: [] }; };
  opsApi.anomalies = async () => ({ ok: true, anomalies: [] });
  opsApi.sendEmail = async () => ({ ok: false, error: 'mailbox refused' });
  boot({ homeRaw: { approvals: { items: [approval()] } }, ccLive: REG });
  await firstCard().click();
  await c.renderVals().em.send();
  await new Promise((r) => setImmediate(r));
  assert.equal(reread, 0);
});

test('the vendor\'s name from the asset read never rewrites a draft the reader has started editing', async () => {
  let release;
  energyApi.assetVendor = () => new Promise((r) => { release = r; });
  boot({ homeRaw: { anomalies: { anomalies: [{ id: 'n1', anomaly_type: 'weekend_spike', status: 'open', asset_id: 'a1',
    asset_code: 'AHU-3', building_name: 'Bishopsgate Tower' }] } } });
  const opening = firstCard().click();
  const edited = 'Hello,\n\nMy own words.';
  c.renderVals().em.setBody({ target: { value: edited } });
  release({ ok: true, vendor: { id: 'v-apex', name: 'Apex Mechanical' }, contacts: { email: 'ops@apex.example', candidates: [] } });
  await opening;
  assert.equal(c.state.emBody, edited);
  assert.equal(c.state.emTo, 'ops@apex.example', 'the empty To is still filled');
});

test('a draft closed while its address was being read is left closed and unaddressed', async () => {
  let release;
  opsApi.vendorContact = () => new Promise((r) => { release = r; });
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  const opening = firstCard().click();
  c.renderVals().fCancel();
  release(PENNARD);
  await opening;
  assert.equal(c.state.flow, null);
  assert.equal(c.state.emTo, '');
});

test('the "already sent" check runs after the address read, not before it', async () => {
  const order = [];
  opsApi.vendorContact = async () => { order.push('contact'); return PENNARD; };
  opsApi.sentEmails = async () => { order.push('history'); return { ok: true, count: 0, items: [] }; };
  boot({ mxRaw: { decisions: { decisions: [LAPSED_WO] } }, ccLive: REG });
  await firstCard().click();
  assert.deepEqual(order, ['contact', 'history']);
});
