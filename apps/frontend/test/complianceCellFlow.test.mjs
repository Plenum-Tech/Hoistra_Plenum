// A requirement-matrix cell, clicked, through the real controller and renderVals (Aasim, 8 Oct
// 2026): the dock opens a fresh conversation asking what is behind the cell, the orchestrator is
// given the cell's facts, and its answer is followed by the next steps as buttons — each one a
// draft the reader checks and sends, or skips.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { complianceApi } = await import('../src/api/compliance.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { validChoices } = await import('../src/logic/choiceCards.js');

const real = { contact: opsApi.vendorContact, sent: opsApi.sentEmails, send: opsApi.sendEmail, renewal: complianceApi.draftRenewalEmail };
let c = null;
const asked = [];
beforeEach(() => {
  asked.length = 0;
  opsApi.vendorContact = async (id) => { asked.push(id); return { ok: true, vendor: { id: id }, email: id + '@vendor.example', candidates: [] }; };
  opsApi.sentEmails = async () => ({ ok: true, count: 0, items: [] });
});
afterEach(() => {
  Object.assign(opsApi, { vendorContact: real.contact, sentEmails: real.sent, sendEmail: real.send });
  complianceApi.draftRenewalEmail = real.renewal;
  if (c) { clearInterval(c._orchTick); clearInterval(c._ccTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});

const pt = (code, name, scope, trade, requires) => ({
  certificate_type_code: code, certificate_type_name: name, certificate_scope: scope, trade_category: trade, required_contractor_accreditation: requires || null
});
const REG = shapeLiveCompliance({
  certificates: [
    { id: 'c-survey', certificate_type_code: 'ASBESTOS_SURVEY', cert_scope: 'Building', building_name: 'Bishopsgate Tower', vendor_name: 'Apex Asbestos',
      days_to_expiry: -8, expiry_date: '2026-09-30', country_code: 'UK', certificate_number: 'AS-118' },
    { id: 'c-register', certificate_type_code: 'ASBESTOS_REGISTER', cert_scope: 'Building', building_name: 'Bishopsgate Tower', vendor_name: 'Clearwater Surveys',
      days_to_expiry: 200, expiry_date: '2027-04-26', country_code: 'UK' },
    { id: 'v-ukas', certificate_type_code: 'UKAS_ASBESTOS', cert_scope: 'Vendor', vendor_name: 'Apex Asbestos', vendor_id: 'v-apex',
      days_to_expiry: -20, expiry_date: '2026-09-18', country_code: 'UK' },
    { id: 'v-hse', certificate_type_code: 'HSE_ASBESTOS_LICENCE', cert_scope: 'Vendor', vendor_name: 'Clearwater Surveys', vendor_id: 'v-clearwater',
      days_to_expiry: 300, expiry_date: '2027-08-04', country_code: 'UK' }
  ],
  coverage: {},
  packs: { UK: [
    pt('ASBESTOS_SURVEY', 'Asbestos Survey', 'Building', 'Asbestos', 'UKAS'),
    pt('ASBESTOS_REGISTER', 'Asbestos Register', 'Building', 'Asbestos'),
    pt('FRA', 'Fire Risk Assessment', 'Building', 'Fire'),
    pt('UKAS_ASBESTOS', 'UKAS Asbestos Accreditation', 'Vendor', 'Asbestos'),
    pt('HSE_ASBESTOS_LICENCE', 'HSE Asbestos Licence', 'Vendor', 'Asbestos')
  ] }
});
const COL = (code) => REG.mxCodes.indexOf(code);
const QUESTION = 'Why is the Asbestos Survey at Bishopsgate Tower lapsed or blocked, and what should I do next?';

let seen = [];
const boot = (state, reply) => {
  seen = [];
  c = new HoistraLogic({});
  c.setState(Object.assign({ signedIn: true, view: 'cc', ccPivot: 'matrix', ccLive: REG,
    account: { email: 'pm@northbridge.co.uk', organization_name: 'Northbridge Estates', role: 'admin', can_ingest: true,
      buildings: [{ id: 'bld-1', name: 'Bishopsgate Tower' }] } }, state));
  c.ccStreamTurn = async (q, context) => {
    seen.push({ q: q, context: context });
    if (reply instanceof Error) throw reply;
    return reply || { answer: 'Apex Asbestos did the survey and its UKAS accreditation lapsed on 18 Sep 2026.', tool_calls: [] };
  };
  return c;
};
const cell = (code, building) => {
  const row = c.renderVals().ccMxRows.find((r) => r.name === (building || 'Bishopsgate Tower'));
  return row.cells[COL(code)];
};
const settle = () => new Promise((r) => setTimeout(r, 20));
const steps = () => c.renderVals().orchChat[c.state.ccChat.length - 1];
const pick = (title) => {
  const card = steps().choices.find((x) => x.title === title);
  assert.ok(card, 'no "' + title + '" card in ' + JSON.stringify(steps().choices.map((x) => x.title)));
  return card.pick();
};

test('a matrix cell is a button; a "Not required" cell is not', () => {
  boot({ ccLive: Object.assign({}, REG, { mx: { 'Bishopsgate Tower': REG.mx['Bishopsgate Tower'].map((st, i) => (i === COL('FRA') ? 'na' : st)) } }) });
  assert.equal(typeof cell('ASBESTOS_SURVEY').click, 'function');
  assert.match(cell('ASBESTOS_SURVEY').tip, /Asbestos Survey — Lapsed or blocked/);
  assert.equal(cell('FRA').click, null);
});

test('clicking a cell opens a fresh conversation in the dock asking about it, with the facts behind it', async () => {
  boot({ sessionId: 'old-thread', ccChat: [{ role: 'you', text: 'an older question' }, { role: 'bot', text: 'an older answer' }] });
  await cell('ASBESTOS_SURVEY').click();
  assert.equal(c.state.orchOpen, true);
  assert.notEqual(c.state.sessionId, 'old-thread', 'a new thread, not the last one');
  assert.equal(c.state.ccChat[0].role, 'you');
  assert.equal(c.state.ccChat[0].text, QUESTION);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].q, QUESTION);
  assert.match(seen[0].context, /compliance console/);
  assert.match(seen[0].context, /Asbestos Survey \(ASBESTOS_SURVEY\) at Bishopsgate Tower/);
  assert.match(seen[0].context, /Apex Asbestos — UKAS Asbestos Accreditation lapsed/);
  assert.equal(c.state.sessions[0].title, QUESTION, 'the session is named by the question');
});

test('the answer is followed by the next steps as buttons, one per vendor', async () => {
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  assert.equal(c.state.ccChat.length, 3);
  assert.match(c.state.ccChat[1].text, /UKAS accreditation lapsed/);
  const v = steps();
  assert.match(v.choiceIntro, /^Next steps for the Asbestos Survey at Bishopsgate Tower/);
  assert.deepEqual(v.choices.map((x) => x.title), [
    'Draft renewal email',
    'Ask Clearwater Surveys to book the renewal visit',
    'Email Apex Asbestos for their UKAS Asbestos Accreditation'
  ]);
});

test('the reply\'s own renewal button for the cell\'s certificate is dropped; its other buttons stay', async () => {
  boot({}, { answer: 'x', tool_calls: [], _partial: { steps: [], zones: { narrative: 'x', offers: [
    { cert_id: 'c-survey', kind: 'renewal_email', label: 'Draft renewal email' },
    { cert_id: 'c-survey', kind: 'verify_now', label: 'Verify with the issuer' }
  ] } } });
  await cell('ASBESTOS_SURVEY').click();
  assert.deepEqual(c.state.ccChat[1].rich.offers.map((o) => o.kind), ['verify_now']);
});

test('an answer that failed still leaves the next steps — they come from the register', async () => {
  boot({}, new Error('the orchestrator returned no answer'));
  await cell('ASBESTOS_SURVEY').click();
  assert.equal(c.state.ccChat[1].error, true);
  assert.equal(steps().choices.length, 3);
});

test('a cell clicked while an answer runs waits', async () => {
  boot({ ccBusy: true });
  await cell('ASBESTOS_SURVEY').click();
  assert.equal(seen.length, 0);
  assert.match(String(c.state.toast), /Still answering/);
});

test('"Email Apex…" opens the accreditation request beside the conversation, addressed from its record', async () => {
  let sends = 0;
  opsApi.sendEmail = async () => { sends += 1; return { ok: true, status: 'sent' }; };
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  await pick('Email Apex Asbestos for their UKAS Asbestos Accreditation');
  await settle();
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emSubject, 'Accreditation lapsed — UKAS Asbestos Accreditation — Apex Asbestos');
  assert.deepEqual(asked, ['v-apex']);
  assert.equal(c.state.emTo, 'v-apex@vendor.example');
  assert.equal(c.state.ccChat.length, 3, 'the conversation stays');
  assert.equal(sends, 0, 'nothing goes out until Send');
});

test('"Ask Clearwater… to book the renewal visit" opens the visit email to that vendor', async () => {
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  await pick('Ask Clearwater Surveys to book the renewal visit');
  await settle();
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emSubject, 'Renewal visit — Asbestos Survey — Bishopsgate Tower');
  assert.match(c.state.emBody, /^Hello Clearwater Surveys team,/);
  assert.match(c.state.emBody, /Northbridge Estates · Hoistra$/);
  assert.deepEqual(asked, ['v-clearwater']);
  assert.equal(c.state.emTo, 'v-clearwater@vendor.example');
  assert.equal(c.state.ccChat.length, 3);
});

test('"Draft renewal email" asks the compliance service for that certificate\'s draft', async () => {
  const drafted = [];
  complianceApi.draftRenewalEmail = async (id) => { drafted.push(id); return { ok: true, email_draft: { to: 'fm@northbridge.example', subject: 'Renewal — Asbestos Survey', body: 'Please renew.' } }; };
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  await pick('Draft renewal email');
  assert.deepEqual(drafted, ['c-survey']);
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emSubject, 'Renewal — Asbestos Survey');
});

test('"Upload the…" on a missing type opens the ingest card for the building and keeps the conversation', async () => {
  boot({});
  await cell('FRA').click();
  assert.equal(c.state.ccChat[0].text, 'Why is there no Fire Risk Assessment on record for Bishopsgate Tower, and what should I do next?');
  await pick('Upload the Fire Risk Assessment');
  assert.equal(c.state.flow, 'ingest');
  assert.equal(c.state.ingExpect, 'Fire Risk Assessment');
  assert.equal(c.state.declForId, 'bld-1');
  assert.equal(c.state.ccChat.length, 3, 'the conversation stays');
});

test('a follow-up in the same conversation keeps the cell\'s facts; another conversation does not', async () => {
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  await c.ccAsk('Who else could do the survey?');
  assert.match(seen[1].context, /Asbestos Survey \(ASBESTOS_SURVEY\) at Bishopsgate Tower/);
  c.ccChatReset();
  await c.ccAsk('How many buildings are in scope?');
  assert.doesNotMatch(seen[2].context, /ASBESTOS_SURVEY/);
});

test('a next-step card is one the chat can run; an unknown draft is not', () => {
  assert.equal(validChoices([{ title: 'x', action: { kind: 'draft', draft: 'visit' } }]).length, 1);
  assert.equal(validChoices([{ title: 'x', action: { kind: 'draft', draft: 'delete_everything' } }]).length, 0);
});

test('opening a draft gives the dock a new key to bring it into view; typing in it does not', async () => {
  boot({});
  await cell('ASBESTOS_SURVEY').click();
  assert.equal(c.renderVals().orchFlowKey, '', 'no draft open yet');
  await pick('Email Apex Asbestos for their UKAS Asbestos Accreditation');
  await settle();
  const first = c.renderVals().orchFlowKey;
  assert.ok(first);
  c.setState({ emSubject: 'edited by the reader', emBody: 'edited' });
  assert.equal(c.renderVals().orchFlowKey, first, 'editing the draft keeps it where it is');
  await pick('Ask Clearwater Surveys to book the renewal visit');
  await settle();
  assert.notEqual(c.renderVals().orchFlowKey, first, 'another draft is a new one to show');
});
