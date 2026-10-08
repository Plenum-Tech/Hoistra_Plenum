// complianceCell — a requirement-matrix cell, clicked, asks the orchestrator what is behind it
// and offers the next steps as buttons (Aasim, 8 Oct 2026): "it's a combination of information
// plus actions". The question, the facts it carries and the buttons are all worked out here from
// the register the page already holds; the orchestrator only explains.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveCompliance } from '../src/logic/complianceLive.js';
import { cellSubject, cellQuestion, cellContext, cellActions, cellStepsIntro, visitRequestDraft, withoutCellDuplicates } from '../src/logic/complianceCell.js';

const pt = (code, name, scope, trade, requires) => ({
  certificate_type_code: code, certificate_type_name: name, certificate_scope: scope,
  trade_category: trade, required_contractor_accreditation: requires || null
});
const PACK = [
  pt('ASBESTOS_SURVEY', 'Asbestos Survey', 'Building', 'Asbestos', 'UKAS'),
  pt('ASBESTOS_REGISTER', 'Asbestos Register', 'Building', 'Asbestos'),
  pt('EICR', 'EICR', 'Building', 'Electrical', 'NICEIC'),
  pt('FRA', 'Fire Risk Assessment', 'Building', 'Fire', 'BAFE SP205'),
  pt('EPC', 'Energy Performance Certificate', 'Building', 'Energy'),
  pt('L8_RISK', 'Legionella Risk Assessment', 'Building', 'Water/Legionella', 'LCA'),
  pt('CP17', 'Gas Safety (CP17)', 'Building', 'Gas', 'Gas Safe'),
  pt('UKAS_ASBESTOS', 'UKAS Asbestos Accreditation', 'Vendor', 'Asbestos'),
  pt('HSE_ASBESTOS_LICENCE', 'HSE Asbestos Licence', 'Vendor', 'Asbestos'),
  pt('NICEIC', 'NICEIC Approved Contractor', 'Vendor', 'Electrical'),
  pt('BAFE_SP203_1', 'BAFE SP203-1', 'Vendor', 'Fire'),
  pt('LCA', 'Legionella Control Association', 'Vendor', 'Water'),
  pt('ISO_9001', 'ISO 9001', 'Vendor', 'General')
];
const bc = (id, code, building, vendor, days, extra) => Object.assign({
  id: id, certificate_type_code: code, cert_scope: 'Building', building_name: building, vendor_name: vendor,
  days_to_expiry: days, expiry_date: days < 0 ? '2026-09-30' : '2027-03-01', country_code: 'UK', certificate_number: id.toUpperCase()
}, extra || {});
const vc = (id, code, vendor, days, extra) => Object.assign({
  id: id, certificate_type_code: code, cert_scope: 'Vendor', vendor_name: vendor, vendor_id: 'v-' + vendor.split(' ')[0].toLowerCase(),
  days_to_expiry: days, expiry_date: days < 0 ? '2026-09-20' : '2027-05-01', country_code: 'UK'
}, extra || {});

const REG = shapeLiveCompliance({
  certificates: [
    // Two asbestos vendors at Bishopsgate: Apex did the survey and its UKAS has lapsed;
    // Clearwater keeps the register and its HSE licence is current.
    bc('c-survey', 'ASBESTOS_SURVEY', 'Bishopsgate Tower', 'Apex Asbestos', -8),
    bc('c-register', 'ASBESTOS_REGISTER', 'Bishopsgate Tower', 'Clearwater Surveys', 200),
    vc('v-ukas', 'UKAS_ASBESTOS', 'Apex Asbestos', -20),
    vc('v-hse', 'HSE_ASBESTOS_LICENCE', 'Clearwater Surveys', 300),
    // A current EICR whose electrician holds no NICEIC on record.
    bc('c-eicr', 'EICR', 'Bishopsgate Tower', 'Volt Electrical', 300),
    // An expiring legionella assessment; the contractor's LCA sits under the "Water" trade.
    bc('c-l8', 'L8_RISK', 'Bishopsgate Tower', 'Aqua Hygiene', 40),
    vc('v-lca', 'LCA', 'Aqua Hygiene', 400),
    // A fire vendor that serves another building only.
    bc('c-fa', 'FRA', 'Manchester Town Hall', 'Pennard Fire Services', 500),
    vc('v-bafe', 'BAFE_SP203_1', 'Pennard Fire Services', 250),
    vc('v-iso', 'ISO_9001', 'Generic Facilities', 250)
  ],
  coverage: {},
  packs: { UK: PACK }
});
const col = (code) => REG.mxCodes.indexOf(code);
const subj = (building, code) => cellSubject(REG, building, col(code));
const titles = (choices) => choices.map((c) => c.title);

test('the register keeps each matrix column\'s code, and each type\'s trade and required accreditation', () => {
  assert.ok(col('ASBESTOS_SURVEY') > -1);
  assert.equal(REG.mxTypes[col('ASBESTOS_SURVEY')], 'Asbestos Survey');
  assert.deepEqual(REG.packTypes.ASBESTOS_SURVEY, { name: 'Asbestos Survey', scope: 'building', trade: 'Asbestos', requires: 'UKAS' });
});

test('a "Not required" cell is not a question', () => {
  // Every UK pack type is required of a UK building, so the fixture has no "na" cell; mark one.
  const CC = Object.assign({}, REG, { mx: { 'Bishopsgate Tower': REG.mxCodes.map(() => 'na') } });
  assert.equal(cellSubject(CC, 'Bishopsgate Tower', col('EPC')), null);
});

test('each state asks its own question about the building and the type', () => {
  assert.equal(cellQuestion(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY')),
    'Why is the Asbestos Survey at Bishopsgate Tower lapsed or blocked, and what should I do next?');
  assert.equal(cellQuestion(subj('Bishopsgate Tower', 'L8_RISK')),
    'The Legionella Risk Assessment at Bishopsgate Tower is expiring soon — what is the situation, and what should I do next?');
  assert.equal(cellQuestion(subj('Bishopsgate Tower', 'FRA')),
    'Why is there no Fire Risk Assessment on record for Bishopsgate Tower, and what should I do next?');
  assert.equal(cellQuestion(subj('Bishopsgate Tower', 'EICR')),
    'What is the status of the EICR at Bishopsgate Tower, and who services it?');
});

test('the vendors behind a cell: the one named on the certificate and those serving the building in that trade', () => {
  const s = subj('Bishopsgate Tower', 'ASBESTOS_SURVEY');
  assert.equal(s.state, 'risk');
  assert.deepEqual(s.vendors.map((v) => v.name).sort(), ['Apex Asbestos', 'Clearwater Surveys']);
  assert.equal(s.unlinked, false);
  const apex = s.vendors.find((v) => v.name === 'Apex Asbestos');
  assert.deepEqual(apex.accreditations.map((a) => [a.name, a.status]), [['UKAS Asbestos Accreditation', 'Lapsed']]);
});

test('two asbestos vendors, one lapsed: one email button, for the lapsed one only', () => {
  const t = titles(cellActions(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY')));
  assert.ok(t.includes('Email Apex Asbestos for their UKAS Asbestos Accreditation'));
  assert.ok(!t.some((x) => /Email Clearwater/.test(x)), 'Clearwater\'s licence is current — nothing to ask for');
});

test('a lapsed building certificate offers its renewal draft, and a visit only from a vendor in good standing', () => {
  const acts = cellActions(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY'));
  const renewal = acts.find((a) => a.action.draft === 'renewal');
  assert.deepEqual(renewal.action, { kind: 'draft', draft: 'renewal', certId: 'c-survey', certName: 'Asbestos Survey', owner: 'Bishopsgate Tower' });
  assert.equal(acts[0], renewal, 'the cell\'s own certificate comes first');
  const visits = acts.filter((a) => a.action.draft === 'visit').map((a) => a.action.vendor);
  assert.deepEqual(visits, ['Clearwater Surveys'], 'Apex\'s accreditation has lapsed, so it is not asked to do the work');
});

test('a current certificate whose contractor lacks the required accreditation offers to ask for it', () => {
  const acts = cellActions(subj('Bishopsgate Tower', 'EICR'));
  assert.deepEqual(titles(acts), ['Email Volt Electrical for their NICEIC Approved Contractor']);
  assert.deepEqual(acts[0].action.row, { name: 'NICEIC Approved Contractor', status: 'Not on record', exp: '—' });
});

test('trade names that differ between packs still match (Water/Legionella → Water)', () => {
  const s = subj('Bishopsgate Tower', 'L8_RISK');
  assert.deepEqual(s.vendors.map((v) => v.name), ['Aqua Hygiene']);
  assert.deepEqual(s.vendors[0].accreditations.map((a) => a.status), ['Current']);
  const t = titles(cellActions(s));
  assert.deepEqual(t, ['Draft renewal email', 'Ask Aqua Hygiene to book the renewal visit']);
});

test('a type no contractor holds offers the upload only', () => {
  const acts = cellActions(subj('Bishopsgate Tower', 'EPC'));
  assert.deepEqual(acts.map((a) => a.action), [{ kind: 'draft', draft: 'upload', type: 'Energy Performance Certificate', building: 'Bishopsgate Tower' }]);
});

test('no vendor linked to the building: trade vendors are offered for the visit and said to be unlinked', () => {
  const s = subj('Bishopsgate Tower', 'FRA');
  assert.equal(s.unlinked, true);
  assert.deepEqual(s.vendors.map((v) => v.name), ['Pennard Fire Services']);
  const visit = cellActions(s).find((a) => a.action.draft === 'visit');
  assert.equal(visit.title, 'Ask Pennard Fire Services to carry out the Fire Risk Assessment');
  assert.match(visit.detail, /not linked to Bishopsgate Tower/i);
});

test('the general trade (ISO, insurance) never stands in for a contractor', () => {
  const s = subj('Bishopsgate Tower', 'EPC');
  assert.deepEqual(s.vendors, []);
});

test('every button is a card the chat can draw, numbered in order, with an action to run', () => {
  const acts = cellActions(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY'));
  acts.forEach((a, i) => {
    assert.equal(a.n, i + 1);
    assert.ok(a.title && a.cta && a.icon && a.detail);
    assert.equal(a.action.kind, 'draft');
  });
});

test('the orchestrator is told the facts behind the cell and the buttons under its answer', () => {
  const s = subj('Bishopsgate Tower', 'ASBESTOS_SURVEY');
  const ctx = cellContext(s, cellActions(s));
  assert.match(ctx, /Asbestos Survey \(ASBESTOS_SURVEY\) at Bishopsgate Tower/);
  assert.match(ctx, /Lapsed or blocked/);
  assert.match(ctx, /C-SURVEY/);
  assert.match(ctx, /lapsed 8 days ago/);
  assert.match(ctx, /Apex Asbestos — UKAS Asbestos Accreditation lapsed/);
  assert.match(ctx, /Clearwater Surveys — HSE Asbestos Licence current/);
  assert.match(ctx, /Email Apex Asbestos for their UKAS Asbestos Accreditation/);
  assert.match(ctx, /nothing has been sent/i);
});

test('the visit request says what is wrong and asks for a date and the certificate', () => {
  const lapsed = visitRequestDraft('Clearwater Surveys', { type: 'Asbestos Survey', building: 'Bishopsgate Tower', status: 'Lapsed', exp: '30 Sep 2026' }, 'Northbridge Estates');
  assert.equal(lapsed.emSubject, 'Renewal visit — Asbestos Survey — Bishopsgate Tower');
  assert.match(lapsed.emBody, /^Hello Clearwater Surveys team,/);
  assert.match(lapsed.emBody, /lapsed on 30 Sep 2026/);
  assert.match(lapsed.emBody, /earliest date/);
  assert.match(lapsed.emBody, /Northbridge Estates · Hoistra$/);
  const soon = visitRequestDraft('Aqua', { type: 'L8', building: 'B', status: 'Expiring', exp: '17 Nov 2026' }, null);
  assert.match(soon.emBody, /expires on 17 Nov 2026/);
  assert.match(soon.emBody, /before that date/);
  const none = visitRequestDraft('Pennard', { type: 'Fire Risk Assessment', building: 'B', status: 'Not on record', exp: '—' }, null);
  assert.equal(none.emSubject, 'Fire Risk Assessment required — B');
  assert.match(none.emBody, /holds no Fire Risk Assessment for B/);
  assert.match(none.emBody, /already carried it out/);
});

test('the reply\'s own renewal and attach buttons for the cell are dropped, the rest kept', () => {
  const acts = cellActions(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY'));
  const rich = { narrative: 'x', offers: [
    { cert_id: 'c-survey', kind: 'renewal_email', label: 'Draft renewal email' },
    { cert_id: 'c-survey', kind: 'verify_now', label: 'Verify with the issuer' },
    { cert_id: 'c-other', kind: 'renewal_email', label: 'Draft renewal email' }
  ] };
  assert.deepEqual(withoutCellDuplicates(rich, acts).offers.map((o) => o.cert_id + ':' + o.kind),
    ['c-survey:verify_now', 'c-other:renewal_email']);
  const gap = cellActions(subj('Bishopsgate Tower', 'EPC'));
  const r2 = { offers: [{ cert_id: '', certificate_type_code: 'EPC', kind: 'attach_certificate' }, { cert_id: '', certificate_type_code: 'EPC', kind: 'log_outstanding' }] };
  assert.deepEqual(withoutCellDuplicates(r2, gap, 'EPC').offers.map((o) => o.kind), ['log_outstanding']);
  assert.equal(withoutCellDuplicates(null, acts), null);
});

test('on seed data the orchestrator is told the register has not loaded, not that nothing is on record', async () => {
  const { HOISTRA_CC } = await import('../src/data/hoistra-compliance.js');
  const name = Object.keys(HOISTRA_CC.mx)[0];
  const i = HOISTRA_CC.mx[name].findIndex((st) => st === 'risk');
  const s = cellSubject(HOISTRA_CC, name, i);
  assert.ok(s, 'a seed cell is still a question');
  const ctx = cellContext(s, cellActions(s));
  assert.match(ctx, /has not loaded/);
  assert.doesNotMatch(ctx, /No certificate of this type is on record/);
});

test('the next steps open with what they are for, and that nothing is sent on a click', () => {
  assert.equal(cellStepsIntro(subj('Bishopsgate Tower', 'ASBESTOS_SURVEY')),
    'Next steps for the Asbestos Survey at Bishopsgate Tower. Each opens a draft for you to check — nothing is sent until you send it. Skip any that do not apply.');
});
