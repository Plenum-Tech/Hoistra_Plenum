// planQueueAction — what a Decision-queue card does when it is clicked (Hussain, 7 Oct 2026):
// the page the card is about opens, and beside it the dock holds the email that resolves it,
// written and ready for Approve & send. "Compliance was only an example — do it for all":
// compliance, maintenance, energy, vendors and the sample cards alike.
//
// The draft is the compliance engine's own vendor email where it wrote one, and otherwise written
// here from the record the card stands for. The address is only ever read from the record of whoever
// is written to — never the engine draft's `to`, which is a configured PM default for its building
// notices and the vendor's first contact by id for its vendor ones — because Approve & send emails
// for real (review, 7 Oct 2026). A card that nothing outside Hoistra can resolve gets an unaddressed
// internal note, never a vendor email with the engine's internal sentence in it.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { planQueueAction, addressLine } = await import('../src/logic/queueDraft.js');
const { shapeLiveCompliance } = await import('../src/logic/complianceLive.js');
const { shapeDecision } = await import('../src/logic/maintenanceLive.js');

const REG = shapeLiveCompliance({ certificates: [
  { id: 'c-bafe', certificate_type_code: 'BAFE_SP203_1', certificate_type_name: 'BAFE SP203-1', cert_scope: 'Vendor',
    vendor_name: 'Pennard Fire Services', vendor_id: 'v-pennard', expiry_date: '2026-09-30T12:00:00Z',
    days_to_expiry: -7, certificate_number: 'SP203-4471', country_code: 'UK', status: 'Lapsed' },
  { id: 'c-cp12', certificate_type_code: 'GAS_CP12', certificate_type_name: 'Gas Safety (CP12)', cert_scope: 'Building',
    building_name: 'Bishopsgate Tower', vendor_name: 'Meridian Heating', vendor_id: 'v-meridian',
    expiry_date: '2026-10-20T12:00:00Z', days_to_expiry: 13, certificate_number: 'CP12-0091', country_code: 'UK',
    status: 'Due for Renewal' },
  { id: 'c-gas', certificate_type_code: 'GAS_SAFE_REG', certificate_type_name: 'Gas Safe registration', cert_scope: 'Vendor',
    vendor_name: 'Pennard Fire Services', vendor_id: 'v-pennard', expiry_date: '2026-11-30T12:00:00Z',
    days_to_expiry: 54, certificate_number: 'GS-604412', country_code: 'UK', status: 'Expiring Soon' }
] }).certs;

const ENGINE = {
  to: 'ops@pennardfire.co.uk',
  subject: '[URGENT] Accreditation renewal — BAFE SP203-1 — Pennard Fire Services',
  body: 'Dear Pennard Fire Services,\n\nYour accreditation (BAFE SP203-1) has lapsed and requires renewal.\n'
};
const approval = (over) => Object.assign({
  id: 'q1', source_feature: 'A', item_type: 'vendor_email', summary: 'Lapsed: Pennard Fire Services — BAFE SP203-1',
  severity: 'Critical', status: 'pending', created_at: '2026-10-06T12:00:00Z',
  related_entity_type: 'vendor', related_entity_id: 'v-pennard',
  payload: { certificate_id: 'c-bafe', risk: 'Lapsed', accreditation_type: 'BAFE SP203-1' },
  email_draft: ENGINE
}, over || {});
const card = (it) => ({ kind: 'approval', module: { A: 'Compliance', B: 'Vendors', C: 'Energy' }[it.source_feature],
  title: it.summary, item: it });
const decisionCard = (raw) => ({ kind: 'decision', module: 'Maintenance', d: shapeDecision(raw) });
const ctx = (over) => Object.assign({ approvals: [], certs: REG, vendors: {}, sender: 'Northbridge Estates' }, over || {});
const SIGN = '\n\nRegards,\nNorthbridge Estates · Hoistra';

// ── compliance ────────────────────────────────────────────────────────────────────────────

test('a compliance card the engine drafted opens the vendor\'s accreditations on the Vendors page with that draft, tied to the item', () => {
  const it = approval();
  // Vendors live on the Vendors page, not in a Compliance vendor view (7 Oct 2026).
  const p = planQueueAction(card(it), ctx({ approvals: [it], vendors: { 'v-pennard': 'Pennard Fire Services Ltd' } }));
  assert.equal(p.page, 'vp');
  assert.deepEqual(p.focus, { vpVendor: 'v-pennard', vpTab: 5 });
  assert.deepEqual(planQueueAction(card(it), ctx({ approvals: [it] })).focus, {}, 'not in the directory: the Vendors page, no vendor picked');
  assert.equal(p.draft.emSubject, ENGINE.subject);
  assert.equal(p.draft.emBody, ENGINE.body);
  assert.equal(p.draft.emQueueItemId, 'q1', 'the send is recorded on the item the engine raised');
  assert.equal(p.task, 'Renewal request', 'named for what the email is, in the dock and the sessions list');
  assert.match(p.steps[1].t, /compliance engine wrote this email/i);
  assert.match(p.steps[3].t, /recorded on this queue item/);
});

test('the engine draft\'s own address is never used — the vendor\'s record is read instead', () => {
  // The engine addresses its vendor email to the vendor's first contact by id (or to the PM default
  // when it has none) — not the primary contact the Assets drafts are held to.
  for (const to of ['ops@pennardfire.co.uk', 'pm@example.com']) {
    const it = approval({ email_draft: Object.assign({}, ENGINE, { to: to }) });
    const p = planQueueAction(card(it), ctx({ approvals: [it] }));
    assert.equal(p.draft.emTo, '');
    assert.deepEqual(p.lookup, { vendorId: 'v-pennard', vendor: 'Pennard Fire Services' });
    assert.match(p.steps[2].t, /Looking up Pennard Fire Services's contact on record/);
  }
});

test('a card with no draft of its own takes the engine\'s vendor email about the same certificate', () => {
  const block = approval({ id: 'q2', item_type: 'block_ack', summary: 'Vendor blocked — Pennard Fire Services (BAFE SP203-1)',
    email_draft: null });
  const sibling = approval({ id: 'q1' });
  const p = planQueueAction(card(block), ctx({ approvals: [block, sibling] }));
  assert.equal(p.draft.emSubject, ENGINE.subject);
  assert.equal(p.draft.emQueueItemId, 'q1', 'recorded on the item whose draft it is');
  assert.match(p.steps[1].t, /about the same certificate, from its vendor email item/i);
});

test('the engine\'s email about another of the vendor\'s certificates is not taken', () => {
  const risk = approval({ id: 'q5', item_type: 'vendor_risk', summary: 'Medium Risk: Pennard Fire Services — Gas Safe registration (54d)',
    payload: { certificate_id: 'c-gas', risk: 'Medium Risk' }, email_draft: null });
  const lapsedEmail = approval({ id: 'q1' });                       // about c-bafe
  const p = planQueueAction(card(risk), ctx({ approvals: [risk, lapsedEmail] }));
  assert.equal(p.draft.emQueueItemId, null);
  assert.equal(p.draft.emSubject, 'Accreditation renewal — Gas Safe registration — Pennard Fire Services');
  assert.match(p.draft.emBody, /your Gas Safe registration expires on 30 Nov 2026 \(in 54 days\)\./);
});

test('with no engine draft anywhere, a lapsed accreditation gets a renewal request written from the register', () => {
  const it = approval({ item_type: 'block_ack', email_draft: null });
  const p = planQueueAction(card(it), ctx({ approvals: [it] }));
  assert.equal(p.draft.emKind, 'renewal');
  assert.equal(p.draft.emTo, '', 'no address is invented');
  assert.equal(p.draft.emQueueItemId, null, 'a draft written here is not the item\'s draft');
  assert.equal(p.draft.emSubject, 'Accreditation lapsed — BAFE SP203-1 — Pennard Fire Services');
  assert.equal(p.draft.emBody,
    'Hello Pennard Fire Services team,\n\n'
    + 'Our compliance register shows your BAFE SP203-1 lapsed on 30 Sep 2026 (7 days ago). '
    + 'Until a current certificate is on record, regulated work of this type cannot be assigned to you.\n\n'
    + 'Certificate: BAFE SP203-1\nReference: SP203-4471\nExpiry: 30 Sep 2026\n\n'
    + 'Please reply with the renewed certificate attached as a PDF.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-pennard', vendor: 'Pennard Fire Services' });
  assert.match(p.steps[2].t, /Looking up Pennard Fire Services's contact on record/);
});

test('a building certificate is an inspection booking to the contractor on it, never the engine\'s note to the PM', () => {
  const pmNote = { to: 'senior.pm@example.com', subject: '[URGENT] Gas Safety (CP12) — Bishopsgate Tower',
    body: 'Dear Property Manager,\n\nThis is an automated Building Certificate Alert Ladder notification…' };
  const it = approval({ id: 'q3', item_type: 'booking_request', summary: 'Due for Renewal: Gas Safety (CP12) — 13 days remaining',
    related_entity_type: 'compliance_certificate', related_entity_id: 'c-cp12', payload: { certificate_id: 'c-cp12' },
    email_draft: pmNote });
  const p = planQueueAction(card(it), ctx({ approvals: [it] }));
  assert.deepEqual(p.focus.ccFocus, { kind: 'building', name: 'Bishopsgate Tower' });
  assert.equal(p.focus.ccPivot, 'buildings');
  assert.equal(p.draft.emTo, '');
  assert.equal(p.draft.emQueueItemId, null);
  assert.equal(p.draft.emSubject, 'Inspection booking — Gas Safety (CP12) — Bishopsgate Tower');
  assert.equal(p.draft.emBody,
    'Hello Meridian Heating team,\n\n'
    + 'The Gas Safety (CP12) at Bishopsgate Tower expires on 20 Oct 2026 (in 13 days).\n\n'
    + 'Certificate: Gas Safety (CP12)\nReference: CP12-0091\nExpiry: 20 Oct 2026\n\n'
    + 'Please propose an inspection date within the next 10 working days, and send the certificate once it is issued.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-meridian', vendor: 'Meridian Heating' });
});

test('a certificate with defects is a remedial request to its contractor', () => {
  const it = approval({ id: 'q6', item_type: 'remedial', summary: 'Remedial action required — GAS_CP12 result=Fail',
    related_entity_type: 'compliance_certificate', related_entity_id: 'c-cp12',
    payload: { certificate_id: 'c-cp12', defects_found: 'Flue spillage on boiler 2', remedial_actions: 'Replace flue liner' },
    email_draft: { to: 'pm@example.com', subject: 'Remedial', body: 'Dear Property Manager' } });
  const p = planQueueAction(card(it), ctx({ approvals: [it] }));
  assert.equal(p.draft.emSubject, 'Remedial works — Gas Safety (CP12) — Bishopsgate Tower');
  assert.equal(p.draft.emBody,
    'Hello Meridian Heating team,\n\n'
    + 'The Gas Safety (CP12) at Bishopsgate Tower came back with defects: Flue spillage on boiler 2. '
    + 'The remedial action on record is: Replace flue liner.\n\n'
    + 'Certificate: Gas Safety (CP12)\nReference: CP12-0091\nExpiry: 20 Oct 2026\n\n'
    + 'Please quote for the remedial works with your earliest date, and send the completion evidence once they are done.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-meridian', vendor: 'Meridian Heating' });
});

test('a card decided inside Hoistra is an unaddressed internal note — no vendor, no lookup', () => {
  for (const [type, summary] of [['certificate_confirm', 'Confirm extracted certificate CP12-0091'],
    ['block_lift', 'Block lifted — Pennard Fire Services (BAFE SP203-1)'], ['adversary_gate', '[BLOCK] Vendor email blocked']]) {
    const it = approval({ id: 'q-' + type, item_type: type, summary: summary, email_draft: null,
      related_entity_type: type === 'certificate_confirm' ? 'compliance_certificate' : 'vendor',
      related_entity_id: type === 'certificate_confirm' ? 'c-cp12' : 'v-pennard' });
    const sibling = approval({ id: 'q1' });
    const p = planQueueAction(card(it), ctx({ approvals: [it, sibling] }));
    assert.equal(p.page, 'vp', type + ': the item names the vendor\'s accreditation, which opens on the Vendors page');
    assert.equal(p.lookup, null, type + ' looks nobody up');
    assert.equal(p.draft.emQueueItemId, null, type + ' takes no engine email');
    assert.equal(p.draft.emKicker, 'Internal note · draft');
    assert.equal(p.draft.emSubject, summary);
    assert.match(p.draft.emBody, new RegExp('^Hello,\\n\\n'));
    assert.match(p.steps[2].t, /Nothing in this item is for a vendor/);
  }
});

// ── maintenance ───────────────────────────────────────────────────────────────────────────

const LAPSED_WO = {
  work_order: 'WO-B-301-4562', asset: 'Fire alarm panel — ground', building: 'Bishopsgate Tower',
  vendor: 'Pennard Fire Services', vendor_id: 'v-pennard', estimated_cost: 650, currency: 'GBP', state: 'Blocked',
  source: 'Vendors', trigger: "Work order held at status 'Blocked'",
  detail: "Blocked — accreditation: Pennard Fire Services's BAFE SP203-1 has lapsed; the loop 2 fault waits for an accredited contractor"
};

test('a work order held by a lapsed accreditation opens that vendor on the Vendors page with a renewal request naming it', () => {
  const p = planQueueAction(decisionCard(LAPSED_WO), ctx({ vendors: { 'v-pennard': 'Pennard Fire Services' } }));
  assert.equal(p.page, 'vp');
  assert.deepEqual(p.focus, { vpVendor: 'v-pennard', vpTab: 5 });
  assert.equal(p.draft.emSubject, 'Accreditation lapsed — BAFE SP203-1 — Pennard Fire Services');
  assert.match(p.draft.emBody, /lapsed on 30 Sep 2026 \(7 days ago\)\. Until a current certificate is on record/);
  assert.match(p.draft.emBody, /Work order WO-B-301-4562 \(Fire alarm panel — ground, Bishopsgate Tower\) is on hold until it is\./);
  assert.deepEqual(p.lookup, { vendorId: 'v-pennard', vendor: 'Pennard Fire Services' });
});

test('the engine\'s pending renewal email for that vendor is used when there is one, addressed from the record', () => {
  const it = approval();
  const p = planQueueAction(decisionCard(LAPSED_WO), ctx({ approvals: [it] }));
  assert.equal(p.draft.emSubject, ENGINE.subject);
  assert.equal(p.draft.emTo, '');
  assert.deepEqual(p.lookup, { vendorId: 'v-pennard', vendor: 'Pennard Fire Services' });
  assert.equal(p.draft.emQueueItemId, 'q1');
});

test('a lapse the register does not hold is drafted from the work order\'s own words', () => {
  const p = planQueueAction(decisionCard(Object.assign({}, LAPSED_WO, {
    work_order: 'WO-B-301-4568', asset: 'Standby generator GEN-1', vendor: 'Ostley Power Services', vendor_id: 'v-ostley',
    detail: "Blocked — accreditation: Ostley Power Services' NICEIC registration has lapsed; the monthly run test waits for an accredited contractor"
  })), ctx());
  assert.equal(p.page, 'cc');
  assert.deepEqual(p.focus, { ccQueue: null, ccQueueOpenId: null }, 'no register row to open it on');
  assert.equal(p.draft.emSubject, 'Accreditation lapsed — NICEIC registration — Ostley Power Services');
  assert.equal(p.draft.emBody,
    'Hello Ostley Power Services team,\n\n'
    + 'Work order WO-B-301-4568 for Standby generator GEN-1 at Bishopsgate Tower is on hold: '
    + 'our records show your NICEIC registration has lapsed.\n\n'
    + 'Please reply with the renewed certificate attached as a PDF. Until it is on record, '
    + 'work of this type cannot be allocated to you.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-ostley', vendor: 'Ostley Power Services' });
});

test('an overdue work order opens Maintenance with a chaser to its vendor', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-B-101-17', asset: 'Chiller 2', building: 'Harbour Point',
    vendor: 'Meridian Mechanical Ltd', vendor_id: 'v-mm', state: 'Deviation', source: 'Vendors', trigger: 'Past its due date',
    detail: 'Quarterly service', due: '2026-10-03', estimated_cost: null }), ctx());
  assert.equal(p.page, 'ops');
  assert.deepEqual(p.focus, {});
  assert.equal(p.draft.emSubject, 'Overdue — WO-B-101-17 · Chiller 2, Harbour Point');
  assert.equal(p.draft.emBody,
    'Hello Meridian Mechanical Ltd team,\n\n'
    + 'Work order WO-B-101-17 for Chiller 2 at Harbour Point was due 03 Oct 2026 and is still open.\n\n'
    + 'Please confirm when it will be completed, and send the completion report once it is.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-mm', vendor: 'Meridian Mechanical Ltd' });
  assert.equal(p.draft.emQueueItemId, null);
});

test('an overdue order of a vendor whose accreditation lapsed is still chased, not sent a renewal request', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-B-301-4570', asset: 'Sprinkler valve', building: 'Bishopsgate Tower',
    vendor: 'Pennard Fire Services', vendor_id: 'v-pennard', state: 'Deviation', due: '2026-10-01', detail: 'Annual test' }), ctx());
  assert.equal(p.page, 'ops');
  assert.equal(p.draft.emSubject, 'Overdue — WO-B-301-4570 · Sprinkler valve, Bishopsgate Tower');
});

test('a held order of a vendor the register shows lapsed is a renewal request, even when its words do not say so', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-B-301-4571', asset: 'Fire door', building: 'Bishopsgate Tower',
    vendor: 'Pennard Fire Services', vendor_id: 'v-pennard', state: 'Blocked', detail: "Work order held at status 'Blocked'" }), ctx());
  assert.equal(p.page, 'vp');
  assert.equal(p.draft.emSubject, 'Accreditation lapsed — BAFE SP203-1 — Pennard Fire Services');
  // Nothing on the order says the lapse is why it is held, so the email does not say so either.
  assert.match(p.draft.emBody, /Work order WO-B-301-4571 \(Fire door, Bishopsgate Tower\) is currently on hold\./);
  assert.doesNotMatch(p.draft.emBody, /until it is/);
});

test('a work order that should exist is a work-order request', () => {
  const p = planQueueAction(decisionCard({ work_order: null, asset: 'AHU-3', building: 'Bishopsgate Tower',
    vendor: 'Apex Mechanical', vendor_id: 'v-apex', state: 'To raise', source: 'Assets',
    trigger: 'Condition below threshold', detail: 'Supply fan bearing signature for 72 hours.' }), ctx());
  assert.equal(p.page, 'ops');
  assert.equal(p.draft.emSubject, 'Work order request — AHU-3 · Bishopsgate Tower');
  assert.equal(p.draft.emBody,
    'Hello Apex Mechanical team,\n\n'
    + 'Please raise a work order on AHU-3 at Bishopsgate Tower. Supply fan bearing signature for 72 hours.\n\n'
    + 'Please confirm your attendance date.' + SIGN);
});

test('a work order awaiting approval asks its vendor to stand by the estimate and give a date — it approves nothing', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-7', asset: 'Boiler 1', building: 'Town Hall',
    vendor: 'Meridian Heating', vendor_id: 'v-meridian', state: 'Awaiting approval', estimated_cost: 640,
    detail: 'Burner service' }), ctx());
  assert.equal(p.draft.emSubject, 'Estimate and date — WO-7 · Boiler 1, Town Hall');
  assert.equal(p.draft.emBody,
    'Hello Meridian Heating team,\n\n'
    + 'Work order WO-7 for Boiler 1 at Town Hall is awaiting our approval at the estimate of £640.\n\n'
    + 'Before we approve it, please confirm the estimate still stands and give your earliest attendance date.' + SIGN);
  assert.doesNotMatch(p.draft.emBody, /approved to proceed/);
});

test('a held work order asks its vendor what it needs, and a statutory one says so', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-B-101-32', asset: 'Chiller 2', building: 'Harbour Point',
    vendor: 'Meridian Mechanical Ltd', vendor_id: 'v-mm', state: 'Blocked', detail: "Work order held at status 'Blocked'.",
    statutory_certificate: { name: 'F-Gas leak check', expiry_date: '2026-11-01' } }), ctx());
  assert.equal(p.page, 'ops');
  assert.equal(p.draft.emSubject, 'On hold — WO-B-101-32 · Chiller 2, Harbour Point');
  assert.equal(p.draft.emBody,
    'Hello Meridian Mechanical Ltd team,\n\n'
    + "Work order WO-B-101-32 for Chiller 2 at Harbour Point is on hold: Work order held at status 'Blocked'.\n\n"
    + 'This is statutory: F-Gas leak check · 01 Nov 2026.\n\n'
    + 'Please tell us what is needed for it to go ahead — or that you cannot attend, so it can be reassigned.' + SIGN);
});

test('a decision with no vendor on it is drafted unaddressed and looks nothing up', () => {
  const p = planQueueAction(decisionCard({ work_order: 'WO-9', asset: 'Pump P1', building: 'Riverside Court',
    vendor: null, vendor_id: null, state: 'Deviation', due: null }), ctx());
  assert.equal(p.lookup, null);
  assert.match(p.draft.emBody, /^Hello,\n\nWork order WO-9 for Pump P1 at Riverside Court is past its due date and still open\./);
  assert.match(p.steps[2].t, /No vendor on the work order/);
});

// ── energy ────────────────────────────────────────────────────────────────────────────────

const ANOM = { id: 'n1', anomaly_type: 'weekend_spike', status: 'open', detected_at: '2026-09-25T12:00:00Z',
  metric_pct: 303.4, financial_gbp: 38400, currency: 'GBP', annualised_excess_kwh: 2412.4, meter_id: 'm1',
  meter_ref: 'MPAN-1', asset_id: 'a1', asset_code: 'AHU-3', building_id: 'b1', building_name: 'Bishopsgate Tower',
  simulated: false };

test('an anomaly on an asset opens Energy with an inspection request; the vendor is read from the asset', () => {
  const p = planQueueAction({ kind: 'anomaly', module: 'Energy', title: 'Weekend spike — meter m1', item: ANOM }, ctx());
  assert.equal(p.page, 'energy');
  assert.equal(p.draft.emKind, 'inspect');
  assert.equal(p.draft.emSubject, 'Inspection request — Weekend spike — AHU-3 at Bishopsgate Tower');
  assert.equal(p.draft.emBody,
    'Hello,\n\n'
    + "The energy engine has an open weekend spike on AHU-3 at Bishopsgate Tower: 303% above its baseline since 25 Sep 2026, £38,400 a year at the meter's tariff. "
    + 'That is 2,412 kWh a year above baseline.\n\n'
    + 'Please inspect AHU-3 and report what you find, with a recommendation. A work order follows if the report supports one.' + SIGN);
  assert.deepEqual(p.lookup, { assetId: 'a1' });
});

test('a building-level anomaly is drafted for whoever runs the supply, unaddressed', () => {
  const p = planQueueAction({ kind: 'anomaly', module: 'Energy', item: Object.assign({}, ANOM, {
    asset_id: null, asset_code: null, currency: 'AED', simulated: true }) }, ctx());
  assert.equal(p.draft.emSubject, 'Energy anomaly — Weekend spike — meter MPAN-1 at Bishopsgate Tower');
  assert.match(p.draft.emBody, /AED 38,400 a year at the meter's tariff/);
  assert.match(p.draft.emBody, /contains simulated readings/);
  assert.equal(p.lookup, null);
});

// ── vendors ───────────────────────────────────────────────────────────────────────────────

const FLAG = { id: 'b1', source_feature: 'B', item_type: 'invoice_flag_high_value', severity: 'high',
  summary: 'Invoice INV-2847 line 7 flagged £230.25 (>200)', created_at: '2026-10-05T12:00:00Z',
  related_entity_type: 'invoice', related_entity_id: null,
  payload: { invoice_ref: 'INV-2847', vendor_id: 'v-apex',
    line: { line_id: 7, delta_gbp: 230.25, discrepancy: 'Labour charged 5.0 hrs against 2.75 hrs on the attendance record.' } } };

test('a flagged invoice line opens Vendors on that vendor\'s invoices with an invoice query', () => {
  const p = planQueueAction(card(FLAG), ctx({ vendors: { 'v-apex': 'Apex Mechanical' } }));
  assert.equal(p.page, 'vp');
  assert.deepEqual(p.focus, { vpVendor: 'v-apex', vpTab: 4 });
  assert.equal(p.draft.emSubject, 'Invoice query — INV-2847 line 7');
  assert.equal(p.draft.emBody,
    'Hello Apex Mechanical team,\n\n'
    + 'We have held line 7 of invoice INV-2847 for review: it differs from what the contract supports by £230.25. '
    + 'Labour charged 5.0 hrs against 2.75 hrs on the attendance record.\n\n'
    + 'Please send the evidence behind it — attendance records, approved parts — or a credit note for the difference. '
    + 'The rest of the invoice is not affected.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-apex', vendor: 'Apex Mechanical' });
  assert.equal(p.draft.emQueueItemId, null, 'only compliance items are recorded on the queue item');
});

test('a flagged line with no invoice reference is named by the item, not by a blank', () => {
  const p = planQueueAction(card(Object.assign({}, FLAG, { summary: 'Invoice INV-2847 — 2 of 12 lines flagged',
    payload: { line: { delta_gbp: 230.25 } } })), ctx());
  assert.equal(p.draft.emSubject, 'Invoice query — Invoice INV-2847 — 2 of 12 lines flagged');
  assert.match(p.draft.emBody, /^Hello,\n\nWe have held a line of the invoice for review: it differs from what the contract supports by £230\.25\./);
  assert.equal(p.lookup, null, 'no vendor on the item, so nothing to look up');
});

test('a vendor the Vendors page does not list leaves the page on whoever it shows', () => {
  const p = planQueueAction(card(FLAG), ctx());
  assert.deepEqual(p.focus, {});
  assert.match(p.draft.emBody, /^Hello,\n/);
  assert.deepEqual(p.lookup, { vendorId: 'v-apex', vendor: null });
});

test('a vendor item decided inside Hoistra is an internal note — no lookup, no engine sentence sent to the vendor', () => {
  for (const [type, summary] of [['overlapping_contracts_tie', 'Two contracts tie for vendor 3f2b — PM must resolve which governs'],
    ['cost_variance_alert', 'Cost variance 18% for vendor 3f2b0c1d-0000 in Sep 2026'], ['asset_criticality_review', 'Approve asset criticality AHU-3 → L1']]) {
    const p = planQueueAction(card(Object.assign({}, FLAG, { id: 'b-' + type, item_type: type, summary: summary,
      related_entity_type: 'vendor', related_entity_id: 'v-apex', payload: {} })), ctx({ vendors: { 'v-apex': 'Apex Mechanical' } }));
    assert.equal(p.page, 'vp', type);
    assert.equal(p.lookup, null, type + ' looks nobody up');
    assert.equal(p.draft.emKicker, 'Internal note · draft');
    assert.match(p.draft.emBody, /^Hello,\n\n/, type + ' greets no vendor');
  }
});

test('overdue FM reports are asked for in the vendor email\'s own words, not the engine\'s', () => {
  const p = planQueueAction(card(Object.assign({}, FLAG, { id: 'b-fm', item_type: 'fm_report_staleness',
    summary: 'FM reports stale for vendor v-apex — data as of 2026-09-01', related_entity_type: 'vendor', related_entity_id: 'v-apex',
    payload: { data_as_of: '2026-09-01' } })), ctx({ vendors: { 'v-apex': 'Apex Mechanical' } }));
  assert.equal(p.draft.emSubject, 'FM reports overdue — Apex Mechanical');
  assert.equal(p.draft.emBody, 'Hello Apex Mechanical team,\n\nThe FM reports we hold from you stop at 01 Sep 2026.\n\n'
    + 'Please send the FM reports since then, and keep them coming each month.' + SIGN);
  assert.deepEqual(p.lookup, { vendorId: 'v-apex', vendor: 'Apex Mechanical' });
});

test('an energy engine item is an internal note — its repair-or-replace figures are not mailed to the vendor', () => {
  const p = planQueueAction(card({ id: 'c1', source_feature: 'C', item_type: 'energy_remediation_recommendation',
    summary: 'AHU-3: repair ≈£1,800 vs replace ≈£38,000', created_at: '2026-10-05T12:00:00Z', payload: { asset_id: 'a1' } }), ctx());
  assert.equal(p.page, 'energy');
  assert.equal(p.lookup, null);
  assert.equal(p.draft.emKicker, 'Internal note · draft');
});

// ── the maintenance watch (source M) ────────────────────────────────────────────────────────

const watchCard = (over) => ({ kind: 'approval', module: 'Maintenance', item: Object.assign({ id: 'm1', source_feature: 'M',
  created_at: '2026-10-06T12:00:00Z', severity: 'high' }, over) });

test('a work order past its SLA opens Maintenance with a chaser to the vendor the watch names', () => {
  const p = planQueueAction(watchCard({ item_type: 'wo_sla_breach', related_entity_type: 'work_order', related_entity_id: 'w1',
    summary: 'Work order WO-123 past its SLA by 30 h — Belt slipping · Apex Mechanical',
    payload: { wo_code: 'WO-123', hours_late: 30, vendor: 'Apex Mechanical', asset: 'AHU-3', building_code: 'B-301' } }), ctx());
  assert.equal(p.page, 'ops');
  assert.equal(p.draft.emSubject, 'Past SLA — WO-123 · AHU-3');
  assert.equal(p.draft.emBody, 'Hello Apex Mechanical team,\n\nWork order WO-123 for AHU-3 is 30 hours past its SLA.\n\n'
    + 'Please confirm when it will be completed, and send the completion report once it is.' + SIGN);
  assert.equal(p.lookup, null, 'the watch names the vendor but carries no id to read an address by');
  assert.match(p.steps[2].t, /names Apex Mechanical but not its record/);
});

test('an overdue PPM with no order open is a request to raise it', () => {
  const p = planQueueAction(watchCard({ item_type: 'ppm_overdue', related_entity_type: 'maintenance_plan', related_entity_id: 'p1',
    summary: 'PPM overdue — Filter change (AHU-3), due 01 Oct', payload: { sm_code: 'SM-12', asset_code: 'AHU-3',
      next_due_date: '2026-10-01', days_overdue: 6, vendor: null } }), ctx());
  assert.equal(p.page, 'ops');
  assert.equal(p.draft.emSubject, 'PPM work order request — SM-12 · AHU-3');
  assert.match(p.draft.emBody, /^Hello,\n\nThe planned maintenance SM-12 on AHU-3 was due 01 Oct 2026 and has no work order open\./);
});

test('a parts reorder or a KPI from the watch is an internal note on Maintenance', () => {
  for (const type of ['part_reorder', 'maintenance_kpi']) {
    const p = planQueueAction(watchCard({ item_type: type, summary: 'MOTOR-8HP at 0 — reorder', payload: {} }), ctx());
    assert.equal(p.page, 'ops', type);
    assert.equal(p.draft.emKicker, 'Internal note · draft');
    assert.equal(p.lookup, null);
  }
});

// ── the sample cards ──────────────────────────────────────────────────────────────────────

test('a sample card opens its own page with a draft and never an address', () => {
  const seed = (module, title, fields) => ({ id: 'd', module: module, title: title, fields: fields || [] });
  const gas = planQueueAction(seed('Compliance', 'Gas Safe registration for Meridian Heating Ltd expired 3 days ago',
    [{ l: 'Vendor', v: 'Meridian Heating Ltd' }]), ctx());
  assert.equal(gas.page, 'cc');
  assert.equal(gas.draft.emTo, '');
  assert.equal(gas.lookup, null);
  assert.equal(gas.draft.emSubject, 'Action needed — Gas Safe registration for Meridian Heating Ltd expired 3 days ago');
  assert.match(gas.draft.emBody, /^Hello Meridian Heating Ltd team,\n\nGas Safe registration for Meridian Heating Ltd expired 3 days ago\./);
  assert.match(gas.steps[2].t, /Sample card/);
  assert.equal(gas.draft.emSample, true, 'Approve & send refuses it');
  assert.equal(planQueueAction(seed('Energy', 'Weekend non-occupancy spike — Bishopsgate Tower'), ctx()).page, 'energy');
  assert.equal(planQueueAction(seed('Vendors', 'Invoice INV-2847 — 2 of 12 lines flagged (£1,450)',
    [{ l: 'Vendor', v: 'Apex Mechanical · score 78' }]), ctx()).draft.emBody.split('\n')[0], 'Hello Apex Mechanical team,');
  assert.equal(planQueueAction(seed('Assets', 'AHU-3 supply fan — bearing degradation signature'), ctx()).page, 'assets');
  assert.equal(planQueueAction(seed('Maintenance', 'WO-1 — Pump'), ctx()).page, 'ops');
});

test('no company name signs as Hoistra alone', () => {
  const p = planQueueAction(card(Object.assign({}, FLAG)), ctx({ sender: null }));
  assert.match(p.draft.emBody, /\n\nRegards,\nHoistra$/);
});

test('nothing to act on is left to the drawer', () => {
  assert.equal(planQueueAction(null, ctx()), null);
  assert.equal(planQueueAction({ kind: 'approval', module: 'Compliance' }, ctx()), null);
});

// ── the address line ──────────────────────────────────────────────────────────────────────

test('the address line says where the To came from, or why there is none', () => {
  assert.equal(addressLine({ email: 'ops@apex.co.uk', vendor: 'Apex Mechanical' }),
    "Addressed to ops@apex.co.uk — Apex Mechanical's contact on record.");
  assert.equal(addressLine({ candidates: ['a@x.co', 'b@x.co'], vendor: 'Apex Mechanical' }),
    'Several contacts on record for Apex Mechanical and none marked primary, so the address is left for you: a@x.co, b@x.co.');
  assert.equal(addressLine({ vendor: 'Apex Mechanical' }), "No address on Apex Mechanical's record — add one below.");
  assert.equal(addressLine({ failed: true, vendor: 'Apex Mechanical' }),
    "Apex Mechanical's record could not be read just now — add the address below.");
  assert.equal(addressLine({ notFound: true, vendor: null }),
    'The vendor is not on a record you can see — add the address below.');
  assert.equal(addressLine({ noVendor: true }), 'No vendor on the asset record — add the recipient below.');
});
