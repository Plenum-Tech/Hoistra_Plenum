// emailFlowOffers — "Draft renewal email" under an answer, through the real controller and
// renderVals (Hussain, 28 Oct 2026: "draft a mail should work").
//
// On the Hoistra chat page the button drafted the email (and the compliance service queued it on
// the Approvals card), then showed nothing: the email card was drawn only in the dock, which is
// not open there, while a note said "It is open above". Every further click queued another
// Approvals item. Now the page draws the draft under the answer that asked for it (emChatAt),
// a certificate's draft is made once and reopened after that, the send is recorded against the
// Approvals item the draft made, and what the send did stays where the draft was.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { complianceApi } = await import('../src/api/compliance.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { emChatPlace, emDoneTone } = await import('../src/logic/renderVals.js');

const real = { sent: opsApi.sentEmails, send: opsApi.sendEmail, renewal: complianceApi.draftRenewalEmail, verify: complianceApi.verifyCertificate };
let c = null;
let posts = [];
let sends = [];
beforeEach(() => {
  posts = [];
  sends = [];
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
  opsApi.sendEmail = async (body) => { sends.push(body); return { ok: true, status: 'sent' }; };
  complianceApi.draftRenewalEmail = async (id) => {
    posts.push(id);
    return { ok: true, certificate_id: id, queue_item_id: 'qi-' + id,
      email_draft: { to: 'renewals@' + id + '.example', subject: 'Renewal required — ' + id, body: 'Please renew ' + id + '.' } };
  };
});
afterEach(() => {
  Object.assign(opsApi, { sentEmails: real.sent, sendEmail: real.send });
  Object.assign(complianceApi, { draftRenewalEmail: real.renewal, verifyCertificate: real.verify });
  if (c) { clearInterval(c._orchTick); clearInterval(c._ccTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

// Two F-Gas certificates (one type, two plants at one building) and a LOLER one.
const REG = shapeLiveCompliance({ certificates: [
  { id: 'c-fgas-1', certificate_type_code: 'FGAS', certificate_type_name: 'F-Gas Certificate', cert_scope: 'Building', building_name: 'Bishopsgate Tower',
    vendor_name: 'Apex Mechanical', days_to_expiry: 1, expiry_date: '2026-10-29', country_code: 'UK', certificate_number: 'FG-101' },
  { id: 'c-fgas-2', certificate_type_code: 'FGAS', certificate_type_name: 'F-Gas Certificate', cert_scope: 'Building', building_name: 'Bishopsgate Tower',
    vendor_name: 'Apex Mechanical', days_to_expiry: -3, expiry_date: '2026-10-25', country_code: 'UK', certificate_number: 'FG-102' },
  { id: 'c-loler', certificate_type_code: 'LOLER', certificate_type_name: 'LOLER Thorough Examination', cert_scope: 'Vendor', vendor_name: 'Apex Lifts',
    vendor_id: 'v-apex', days_to_expiry: -10, expiry_date: '2026-10-18', country_code: 'UK' }
] });
const OFFERS = [
  { cert_id: 'c-fgas-1', cert_name: 'FGAS', owner: 'Bishopsgate Tower', kind: 'renewal_email', label: 'Draft renewal email', reason: 'Expires tomorrow' },
  { cert_id: 'c-fgas-2', cert_name: 'FGAS', owner: 'Bishopsgate Tower', kind: 'renewal_email', label: 'Draft renewal email', reason: 'Lapsed' },
  { cert_id: 'c-loler', cert_name: 'LOLER', owner: 'Apex Lifts', kind: 'verify_now', label: 'Verify with the issuer', reason: 'Lapsed' },
  { cert_id: '', cert_name: 'EICR', owner: 'Bishopsgate Tower', owner_kind: 'building', kind: 'attach_certificate', label: 'Attach certificate' },
  { cert_id: '', cert_name: 'EICR', owner: 'Bishopsgate Tower', owner_kind: 'building', kind: 'log_outstanding', label: 'Log as outstanding' }
];
const RICH = { narrative: 'Two F-Gas certificates at Bishopsgate Tower need renewing.', sections: [], groups: [], kpis: [], actions: [], insights: [],
  certificates: [], pending: [], offers: OFFERS, validation: null, steps: [], cost: null };
const CHAT = [{ role: 'you', text: 'Which certificates need renewing?' }, { role: 'bot', text: RICH.narrative, rich: RICH }];

const boot = (state) => {
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, view: 'chat', orchOpen: false, ccLive: REG, ccChat: CHAT.slice(),
    account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates', role: 'admin', can_ingest: true,
      buildings: [{ id: 'bld-1', name: 'Bishopsgate Tower' }] } }, state));
  return c;
};
const offers = () => c.renderVals().orchChat[1].rich.offers;
const offer = (id, kind) => offers().find((o) => o.cert_id === id && o.kind === (kind || 'renewal_email'));

test('on the chat page the draft opens under the answer that asked for it, not in a dock that is closed', async () => {
  boot({});
  await offer('c-fgas-1').run();
  const v = c.renderVals();
  assert.equal(c.state.flow, 'email');
  assert.equal(v.emChatAt, 1, 'drawn under message 1, the answer');
  assert.equal(v.fEmail, false, 'the dock does not draw it a second time');
  assert.equal(v.em.to, 'renewals@c-fgas-1.example');
  assert.equal(v.em.subject, 'Renewal required — c-fgas-1');
  assert.deepEqual(posts, ['c-fgas-1']);
});

test('the draft keeps the Approvals item the service queued, so the send is recorded on it', async () => {
  boot({});
  await offer('c-fgas-1').run();
  assert.equal(c.state.emQueueItemId, 'qi-c-fgas-1');
  assert.equal(c.renderVals().em.queued, true);
  await c.renderVals().em.send();
  assert.equal(sends.length, 1);
  assert.equal(sends[0].queue_item_id, 'qi-c-fgas-1');
  assert.equal(sends[0].to, 'renewals@c-fgas-1.example');
});

test('the note says what happened and where — not "open above", not "approve to queue it"', async () => {
  boot({});
  await offer('c-fgas-1').run();
  const note = c.state.ccChat[c.state.ccChat.length - 1];
  assert.equal(note.note, true);
  assert.doesNotMatch(note.text, /open above/);
  assert.doesNotMatch(note.text, /queue it/);
  assert.match(note.text, /^Renewal email drafted for F-Gas Certificate — Bishopsgate Tower and added to the Approvals card\. Check the address, then press Approve & send\.$/);
});

test('a second click opens the same draft again and queues nothing more', async () => {
  boot({});
  await offer('c-fgas-1').run();
  const notes = c.state.ccChat.length;
  c.renderVals().fCancel();
  assert.equal(c.state.flow, null);
  await offer('c-fgas-1').run();
  assert.deepEqual(posts, ['c-fgas-1'], 'one POST for two clicks');
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emQueueItemId, 'qi-c-fgas-1', 'still that item');
  assert.equal(c.state.ccChat.length, notes, 'no second note');
  assert.equal(c.renderVals().emChatAt, 1);
  // Another certificate is its own draft.
  await offer('c-fgas-2').run();
  assert.deepEqual(posts, ['c-fgas-1', 'c-fgas-2']);
  assert.equal(c.state.emQueueItemId, 'qi-c-fgas-2');
});

test('two offers with one label say which certificate each is for; the label stays the offer\'s own', () => {
  boot({});
  const [a, b, v] = offers();
  assert.equal(a.label, 'Draft renewal email');
  assert.equal(b.label, 'Draft renewal email');
  assert.notEqual(a.forName, b.forName);
  assert.equal(a.forName, 'F-Gas Certificate · Bishopsgate Tower · FG-101');
  assert.equal(b.forName, 'F-Gas Certificate · Bishopsgate Tower · FG-102');
  assert.equal(v.forName, 'LOLER Thorough Examination', 'a name no other offer shares is left alone');
  assert.match(a.aria, /^Draft renewal email — F-Gas Certificate, Bishopsgate Tower/);
  assert.match(v.aria, /^Verify with the issuer — LOLER Thorough Examination, Apex Lifts$/);
});

test('an offer is busy while its call runs, open while its draft is on screen, done after', async () => {
  boot({});
  let release;
  complianceApi.draftRenewalEmail = (id) => { posts.push(id); return new Promise((r) => { release = () => r({ ok: true, queue_item_id: 'qi-1', email_draft: { to: 'a@b.example', subject: 'S', body: 'B' } }); }); };
  const running = offer('c-fgas-1').run();
  assert.equal(offer('c-fgas-1').state, 'busy');
  assert.equal(offer('c-fgas-1').run, undefined, 'no second click while it runs');
  assert.equal(offer('c-fgas-1').label, 'Draft renewal email', 'not swapped for "Working…"');
  release();
  await running;
  assert.equal(offer('c-fgas-1').state, 'open');
  assert.match(offer('c-fgas-1').aria, /\(draft open below\)$/);
  assert.equal(offer('c-fgas-2').state, '');
  c.renderVals().fCancel();
  assert.equal(offer('c-fgas-1').state, 'done');
  assert.equal(typeof offer('c-fgas-1').run, 'function', 'a drafted email can be opened again');
  assert.match(offer('c-fgas-1').aria, /\(drafted — opens it again\)$/);
});

test('a check that ran is marked done and can run again', async () => {
  boot({});
  const ran = [];
  complianceApi.verifyCertificate = async (id) => { ran.push(id); return { ok: true, status: 'verified' }; };
  c.ccLoad = () => {};
  await offer('c-loler', 'verify_now').run();
  assert.deepEqual(ran, ['c-loler']);
  assert.equal(offer('c-loler', 'verify_now').state, 'done');
  assert.equal(typeof offer('c-loler', 'verify_now').run, 'function');
});

test('"Log as outstanding" has no route, so it is not offered; "Attach certificate" opens the ingest card', async () => {
  boot({});
  assert.deepEqual(offers().map((o) => o.kind), ['renewal_email', 'renewal_email', 'verify_now', 'attach_certificate']);
  const seen = [];
  c.ccUploadMissing = (type, building, opts) => { seen.push([type, building, opts && opts.keepChat]); };
  await offers().find((o) => o.kind === 'attach_certificate').run();
  assert.deepEqual(seen, [['EICR', 'Bishopsgate Tower', true]]);
});

test('Cancel takes the draft off the page; a sent draft leaves its outcome where it was', async () => {
  boot({});
  await offer('c-fgas-1').run();
  c.renderVals().fCancel();
  assert.equal(c.state.emAt, null);
  assert.equal(c.renderVals().emChatAt, null);
  await offer('c-fgas-1').run();
  await c.renderVals().em.send();
  const v = c.renderVals();
  assert.equal(c.state.flow, null);
  assert.equal(v.emChatAt, null);
  assert.deepEqual(v.emChatReceipt, { at: 1, text: 'Renewal email sent to renewals@c-fgas-1.example.', tone: 'ok' });
  assert.equal(v.emChatReceipt.text, c.state.flowDone);
  // A later flow's result replaces it.
  c.setState({ flowDone: 'Something else happened.' });
  assert.equal(c.renderVals().emChatReceipt, null);
});

test('a refused send keeps the draft open under its answer, with the outcome in it', async () => {
  boot({});
  opsApi.sendEmail = async () => ({ ok: false, error: 'Subject is required' });
  await offer('c-fgas-1').run();
  await c.renderVals().em.send();
  const v = c.renderVals();
  assert.equal(v.emChatAt, 1);
  assert.match(v.fDoneText, /^Not sent — Subject is required/);
  assert.equal(v.fDoneTone, 'risk');
  assert.equal(v.emChatReceipt, null);
});

test('a draft no answer opened goes at the end of the chat page; on a dock page it is the dock\'s', async () => {
  boot({});
  // compliance.js ccCellAction calls ccRunOffer with no answer index.
  await c.ccRunOffer({ cert_id: 'c-fgas-1', kind: 'renewal_email', label: 'Draft renewal email' });
  assert.equal(c.renderVals().emChatAt, -1);
  c.setState({ view: 'cc', orchOpen: true });
  assert.equal(c.renderVals().emChatAt, null);
  assert.equal(c.renderVals().fEmail, true);
});

test('with the dock open over the chat page, each draft is drawn once', async () => {
  boot({ orchOpen: true });
  await offer('c-fgas-1').run();
  let v = c.renderVals();
  assert.equal(v.emChatAt, 1, 'the answer\'s own draft stays under it');
  assert.equal(v.fEmail, false);
  // A draft the dock opened (another kind) is the dock's.
  c.setState({ flow: 'email', emKind: 'evidence', emCertId: 'c-loler' });
  v = c.renderVals();
  assert.equal(v.emChatAt, null);
  assert.equal(v.fEmail, true);
});

test('a new thread drops the draft\'s place; a draft still open moves to the end, never out of sight', async () => {
  boot({});
  await offer('c-fgas-1').run();
  c.ccChatReset();
  assert.equal(c.state.emAt, null);
  assert.equal(c.renderVals().emChatAt, -1);
});

test('the support email is never drawn as this card', () => {
  boot({ flow: 'email', emKind: 'support', emAt: { i: 1, kind: 'support', cert: '' } });
  assert.equal(c.renderVals().emChatAt, null);
  assert.equal(emChatPlace(null), null);
});

test('an outcome line is toned by what the send did', () => {
  assert.equal(emDoneTone('Renewal email sent to a@b.example.'), 'ok');
  assert.equal(emDoneTone('Not sent — mailbox refused. The draft is unchanged; correct it or retry.'), 'risk');
  assert.equal(emDoneTone('Not confirmed — the mail service reported "queued" rather than delivery.'), 'risk');
  assert.equal(emDoneTone('Recorded for a@b.example but NOT delivered: the platform is in dry-run mode.'), 'warn');
  assert.equal(emDoneTone('Opened in your mail client, addressed to a@b.example — the platform is in handoff mode and sends nothing itself.'), 'warn');
});

test('a model-written email opens in the same card to edit and send, with its Cc', async () => {
  boot({});
  c.openuiHost = null;
  const host = c.renderVals().openuiHost;
  assert.equal(typeof host.email, 'function');
  host.email({ to: 'fm@northbridge.example', cc: 'ops@northbridge.example', subject: 'Chase WO-1184', body: 'Please chase.' });
  let v = c.renderVals();
  assert.equal(c.state.emKind, 'draft');
  assert.equal(c.state.emSentLabel, 'Email');
  assert.equal(v.emChatAt, -1);
  assert.equal(v.em.canCc, true);
  assert.equal(v.em.cc, 'ops@northbridge.example');
  await v.em.send();
  assert.equal(sends[0].cc, 'ops@northbridge.example');
  assert.match(c.state.flowDone, /^Email sent to fm@northbridge\.example\. It is recorded in the email log\.$/);
  // Not while an answer is still being written.
  c.setState({ ccBusy: true, flow: null });
  host.email({ to: 'x@y.example', subject: 'S', body: 'B' });
  assert.equal(c.state.flow, null);
  // A vendor renewal never carries a Cc.
  c.setState({ ccBusy: false });
  await offer('c-fgas-2').run();
  c.setState({ emCc: 'leak@else.example' });
  await c.renderVals().em.send();
  assert.equal(sends[1].cc, undefined);
  assert.equal(c.renderVals().em.canCc, false);
});
