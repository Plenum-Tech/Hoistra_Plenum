// The Assets page's three row actions — Raise work order, Request inspection, Investigate — as
// data. On 28 Sep 2026 none of them did anything a person could use: the two drafts stopped
// at "No vendor on this asset record" whenever the per-asset read 404'd, and Investigate
// opened a four-line drawer. They now follow the design (the Hoistra_1 prototype): the dock
// opens, the four agents run, and a draft or an investigation fills it.
//
// What differs from the prototype is where the words come from. The prototype's findings
// were typed into a table per anomaly type; here every figure is one a read returned — the
// condition read, the per-asset read, GET /api/energy/assets/{id}/investigate — and anything
// no read returned is left out rather than filled. These drafts are sent to real vendors.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  actionCtx, actionSteps, mailDraft, investigationQuestion, investigationSteps,
  shapeInvestigation, investigationError, invCostLine,
  summaryReply, investigationContext, datasetOf, datasetFailure, CONTEXT_CAP, mailDraft as mailDraftR } from '../src/logic/assetsActions.js';

const F = {
  assetId: 'a-1042', assetName: 'AHU-3', assetCode: 'AS-1042', category: 'Air handling',
  installed: '2009-04-01', building: 'Bishopsgate Tower',
  section: { name: 'L4 East · tenant floor', eui: 231, ref: 180, deviation: 28, meters: 'sub-meter' },
  anomaly: { type: 'Non-occupancy spike', annualCost: 38400, days: 21, status: 'open' },
  vendor: 'Apex Mechanical', vendorEmail: 'ops@apexmechanical.co.uk',
  latestNote: { id: 'WO-4472', when: '14 Jun 2026' }, sender: 'Plenum Technologies'
};
const clean = (s) => assert.doesNotMatch(s, /\bnull\b|\bundefined\b|NaN/, 'a vendor must never read null/undefined/NaN: ' + s);

// ── the two drafts ─────────────────────────────────────────────────────────────────────

test('a work order request is addressed to the vendor on record, about this asset', () => {
  const d = mailDraft('wo', F);
  assert.equal(d.emKind, 'wo');
  assert.equal(d.emKicker, 'Work order request · draft');
  assert.equal(d.emTo, 'ops@apexmechanical.co.uk');
  assert.equal(d.emSubject, 'Work order request — AHU-3 · Bishopsgate Tower');
  assert.equal(d.fSubject, 'AHU-3 · Bishopsgate Tower');
  assert.equal(d.fVendor, 'Apex Mechanical');
  assert.match(d.emBody, /^Hello Apex Mechanical team,/);
  assert.match(d.emBody, /Please raise a predictive work order on AHU-3 at Bishopsgate Tower\./);
  clean(d.emBody);
});

test('the body states what the graph shows, in the readings\' own numbers', () => {
  const b = mailDraft('wo', F).emBody;
  assert.match(b, /• Section L4 East · tenant floor at 231 kWh\/m²\/yr against a reference of 180 \(\+28%\) · sub-meter/);
  assert.match(b, /• Non-occupancy spike on AHU-3 · £38,400 annualised · active 21 days · status open/);
  assert.match(b, /• Asset AS-1042 · Air handling · installed 2009 · latest inspection note WO-4472 · 14 Jun 2026/);
  assert.match(b, /Scope: diagnose and rectify the cause of the non-occupancy spike/);
  assert.match(b, /Regards,\nPlenum Technologies · Hoistra$/);
});

test('an inspection request asks for an inspection, not a repair', () => {
  const d = mailDraft('inspect', F);
  assert.equal(d.emKicker, 'Inspection request · draft');
  assert.equal(d.emSubject, 'Inspection request — AHU-3 · Bishopsgate Tower');
  assert.match(d.emBody, /Please inspect AHU-3 at Bishopsgate Tower\./);
  assert.match(d.emBody, /Scope: condition inspection, controls and schedule check/);
  assert.doesNotMatch(d.emBody, /raise a predictive work order/);
  clean(d.emBody);
});

test('what no read returned is left out, never filled', () => {
  const bare = { assetId: 'a-9', assetName: 'Pump-2', building: 'Town Hall' };
  const d = mailDraft('wo', bare);
  assert.equal(d.emTo, '', 'no address on record means no address — the reader types one');
  assert.match(d.emBody, /^Hello,/, 'no vendor named, so no "Hello undefined team"');
  assert.match(d.emBody, /• No anomaly attributed to this asset/);
  assert.doesNotMatch(d.emBody, /Section/, 'no section on record, so no section line');
  assert.match(d.emBody, /• Asset a-9/);
  assert.match(d.emBody, /Regards,\nHoistra$/);
  clean(d.emBody);
});

test('a section with no metered intensity says so instead of printing a blank figure', () => {
  const d = mailDraft('inspect', Object.assign({}, F, { section: { name: 'Car park', eui: null, ref: 45, measured: false } }));
  assert.match(d.emBody, /• Section Car park — not metered, so its load is not measured/);
  clean(d.emBody);
});

// Review, 28 Sep: "not metered" was said of any section missing EITHER figure — a metered
// section with no reference, and one known only by name, were both told to a vendor as unmetered.
test('a metered section with no reference is reported as metered, without a comparison', () => {
  const d = mailDraft('inspect', Object.assign({}, F, { section: { name: 'Plant room', eui: 210, ref: null, measured: true } }));
  assert.match(d.emBody, /• Section Plant room at 210 kWh\/m²\/yr — no reference on record to compare it with/);
  assert.doesNotMatch(d.emBody, /not metered/);
  clean(d.emBody);
});

test('a section known only by name is named, and nothing is claimed about its metering', () => {
  const d = mailDraft('wo', Object.assign({}, F, { section: { name: 'L4 East' } }));
  assert.match(d.emBody, /• Section L4 East\n/);
  assert.doesNotMatch(d.emBody, /not metered/);
});

// Review, 28 Sep: a read that FAILED was reported as a record that is EMPTY.
test('when the vendor record could not be read, the step says so rather than blaming the record', () => {
  const st = actionSteps('wo', Object.assign({}, F, { vendorEmail: null, vendorReadFailed: true }));
  assert.match(st[2].t, /could not be read just now/);
  assert.doesNotMatch(st[2].t, /no address on the vendor record/);
  const none = actionSteps('wo', Object.assign({}, F, { vendor: null, vendorEmail: null, vendorReadFailed: true }));
  assert.match(none[2].t, /could not be read just now/);
});

test('an investigation can hand the draft its own scope line', () => {
  const d = mailDraft('inspect', F, { scope: 'Request the report and water-treatment log for the closed planned visit — neither is on file' });
  assert.match(d.emBody, /Scope: Request the report and water-treatment log for the closed planned visit — neither is on file\./);
});

test('the four agents say what this action actually does', () => {
  const st = actionSteps('wo', F);
  assert.deepEqual(st.map((x) => x.a), ['Orchestrator', 'Planner', 'Worker', 'Quality']);
  assert.equal(st[0].t, 'Intent: raise work order · scope: AHU-3 · Bishopsgate Tower');
  assert.match(st[2].t, /Apex Mechanical at ops@apexmechanical\.co\.uk/);
  assert.match(st[3].t, /Nothing is sent until you approve/);
  assert.equal(actionSteps('inspect', F)[0].t, 'Intent: request inspection · scope: AHU-3 · Bishopsgate Tower');
  assert.match(actionSteps('wo', Object.assign({}, F, { vendorEmail: null }))[2].t, /no address on the vendor record/);
  assert.equal(actionCtx({ assetName: 'X' }), 'X · Unlinked');
});

// ── the investigation ─────────────────────────────────────────────────────────────────

test('the question is asked in the anomaly\'s own terms', () => {
  assert.equal(investigationQuestion(F),
    'Why is AHU-3 at Bishopsgate Tower showing a non-occupancy spike worth £38,400 a year, and what should I do about it?');
  assert.equal(investigationQuestion(Object.assign({}, F, { anomaly: null })),
    'Why is AHU-3 at Bishopsgate Tower where it is, and what should I do about it?');
});

// A response shaped exactly as svc-operations-intelligence's investigate() returns it.
const RES = {
  ok: true, written: false, method: 'rule:asset-investigation/v1',
  asset: { asset_id: 'a-1042', asset_name: 'AHU-3', building: 'Bishopsgate Tower', vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' },
  plan: 'I will walk 6 sources — the readings first, then the maintenance record around the asset, then the documents that should exist for it.',
  sources: [
    { source: 'bms_trend', question: 'AHU-3 kW per RT against design', status: 'not_found', badge: 'no readings' },
    { source: 'meter_reading', question: 'sub-metered plant, 8 weeks', status: 'found', badge: 'sub-metered' },
    { source: 'utility_bill', question: 'billing-period consumption vs same month last year', status: 'partial', badge: '+12%' },
    { source: 'weather', question: 'cooling degree days vs last year', status: 'found', badge: 'flat' },
    { source: 'work_order', question: 'planned maintenance by Apex Mechanical', status: 'partial', badge: 'closed, no report' },
    { source: 'document', question: 'records filed against this asset', status: 'not_found', badge: 'not found' }
  ],
  evidence: [
    { statement: 'The PPM visit closed on 2026-07-12 with no report, and no service or treatment log is filed against the asset.', sources: ['work_order', 'document'], confidence: 1.0, kind: 'missing record' },
    { statement: 'Consumption is +12% on the same period last year.', sources: ['utility_bill'], confidence: 0.8, kind: 'corroborating total' }
  ],
  conclusion: { cause: 'The records this asset should carry are not on file.', cost_to_date: 15508, cost_annualised: 38400,
    rests_on: 'record', confirmation_required: true,
    caveat: 'A record the contract requires is missing, so the cause rests on inference. Confirm with the people who were on site and request the missing document before anything is claimed.' },
  actions: [
    { id: 'request_records', label: 'Request PPM report and treatment log', detail: 'draft to Apex Mechanical', endpoint: 'POST /api/work-orders/',
      body: { request_type: 'inspection', issue_description: 'Request the report and water-treatment log for the closed planned visit — neither is on file' } },
    { id: 'resequence', label: 'Re-sequence to favour the healthier unit', detail: 'BMS change · interim while this one is cleaned', endpoint: null, body: null,
      note: 'a BMS change; this platform proposes it but does not make it' }
  ]
};

test('every source walked is listed with what it gave back, the empty ones included', () => {
  const inv = shapeInvestigation(RES, F);
  assert.equal(inv.live, true);
  assert.equal(inv.plan, RES.plan);
  assert.deepEqual(inv.sources.map((x) => [x.tbl, x.n, x.status]), [
    ['bms_trend', 'no readings', 'not_found'], ['meter_reading', 'sub-metered', 'found'],
    ['utility_bill', '+12%', 'partial'], ['weather', 'flat', 'found'],
    ['work_order', 'closed, no report', 'partial'], ['document', 'not found', 'not_found']]);
});

test('findings carry their sources and the rule\'s own confidence; a missing record is marked', () => {
  const inv = shapeInvestigation(RES, F);
  assert.deepEqual(inv.findings.map((f) => [f.src, f.conf, f.gap]),
    [['work_order · document', 100, true], ['utility_bill', 80, false]]);
});

test('the conclusion is the engine\'s, with its cost and its caveat', () => {
  const inv = shapeInvestigation(RES, F);
  assert.equal(inv.cause, 'The records this asset should carry are not on file.');
  assert.equal(inv.costLine, '£15,508 to date · £38,400 annualised if left');
  assert.equal(inv.escalate, true);
  assert.match(inv.escText, /A record the contract requires is missing/);
});

test('a cost the engine did not compute is not invented', () => {
  assert.equal(invCostLine({ cost_annualised: 38400 }), '£38,400 annualised if left');
  assert.equal(invCostLine({}), 'No cost is attributed to this asset');
  assert.equal(invCostLine(null), 'No cost is attributed to this asset');
});

test('a proposal becomes an action only where there is a real way to carry it out', () => {
  const inv = shapeInvestigation(RES, F);
  const [records, reseq] = inv.actions;
  assert.equal(records.k, 'records', 'a request for records is its own draft, not an inspection booking');
  assert.equal(records.l, 'Request PPM report and treatment log');
  assert.match(records.scope, /water-treatment log/);
  assert.equal(reseq.k, 'note', 'a BMS change this platform cannot make is shown, not offered');
  assert.match(reseq.s, /proposes it but does not make it/);
  assert.equal(inv.actions.length, 2, 'the inspection the caveat calls for is already there, so none is added');
});

test('when the cause needs confirming and nothing asks for it, an inspection request is added', () => {
  const r = Object.assign({}, RES, { actions: [] });
  const inv = shapeInvestigation(r, F);
  assert.deepEqual(inv.actions.map((a) => a.k), ['inspect']);
  assert.match(inv.actions[0].s, /confirm/);
  const settled = Object.assign({}, RES, { actions: [], conclusion: Object.assign({}, RES.conclusion, { confirmation_required: false }) });
  assert.deepEqual(shapeInvestigation(settled, F).actions, [], 'a settled cause adds nothing of its own');
});

test('a work-order proposal opens a work-order draft', () => {
  const r = Object.assign({}, RES, { actions: [{ id: 'raise_work_order', label: 'Raise WO — water treatment and tower inspection', detail: 'P2 · Apex Mechanical · at contracted rate',
    endpoint: 'POST /api/work-orders/', body: { request_type: 'maintenance', issue_description: 'Water treatment and cooling-tower inspection' } }] });
  const a = shapeInvestigation(r, F).actions[0];
  assert.equal(a.k, 'wo');
  assert.match(a.scope, /Water treatment/);
});

test('nothing in the sources explains it: the engine\'s own statement stands in for a cause', () => {
  const r = Object.assign({}, RES, { evidence: [], conclusion: { cause: null, statement: "Nothing in the sources explains this asset's cost.", confirmation_required: true } });
  const inv = shapeInvestigation(r, F);
  assert.equal(inv.cause, "Nothing in the sources explains this asset's cost.");
  assert.deepEqual(inv.findings, []);
});

test('the chain names the sources actually walked once they are known', () => {
  const before = investigationSteps(F);
  assert.equal(before[0].t, 'Intent: investigate ahu-3 · bishopsgate tower · why is it where it is, and what can be done');
  const after = investigationSteps(F, RES);
  assert.equal(after[1].t, 'Walk 6 sources: bms_trend, meter_reading, utility_bill, weather, work_order, document');
  assert.match(after[3].t, /confirmation armed/);
  const settled = investigationSteps(F, Object.assign({}, RES, { conclusion: { cause: 'x', confirmation_required: false } }));
  assert.match(settled[3].t, /none executed until approved/);
});

test('an asset outside the reader\'s buildings says so; anything else says what failed', () => {
  assert.match(investigationError({ status: 404, message: 'not found' }), /not in the buildings you can see/);
  assert.match(investigationError({ status: 500, message: 'boom' }), /did not answer — boom/);
  assert.match(investigationError(null), /did not answer/);
});

// Second review, 28 Sep: `measured` means an intensity was COMPUTED. A section with meters but
// no area (or too short a window) is not measured and is metered, and must not be told to a
// vendor as unmetered.
test('a metered section whose intensity could not be computed says so, and is not called unmetered', () => {
  const d = mailDraft('inspect', Object.assign({}, F, { section: { name: 'L4 East · tenant floor', eui: null, ref: 180, measured: false, meterCount: 2, meters: '2 sub-meters' } }));
  assert.match(d.emBody, /• Section L4 East · tenant floor — 2 sub-meters on record, but no intensity could be computed from them/);
  assert.doesNotMatch(d.emBody, /not metered/);
});

// ── The summary reply and the follow-up context (28 Sep 2026) ────────────────────────

test('a summary with a withheld line and a flagged overall says both', () => {
  const r = summaryReply({ ok: true, lines: [{ source: 'bms_trend', text: 'In band.' }], overall: 'Up £4,430.',
    dropped: [{ source: 'weather', figures: ['31.77'], kept: false }, { source: 'overall', figures: ['4430'], kept: true }] });
  assert.deepEqual(r.lines, [{ label: 'BMS trend', text: 'In band.' }]);
  assert.equal(r.note, '1 line withheld — it cited a figure not in the data; the overall sentence cites a figure not in the data (4430)');
  assert.equal(r.you, '');
});

test('a failed summary is said plainly', () => {
  assert.match(summaryReply(null, new Error('timed out')).bot, /^Summary unavailable — timed out\./);
  assert.match(summaryReply({ ok: false, reason: 'no anthropic key' }).bot, /no anthropic key/);
});

test('the dataset envelope is removed and a failed read is unreadable, a 404 not on record', () => {
  assert.deepEqual(datasetOf({ ok: true, written: false, asset: {}, status: 'found', points: 3 }), { status: 'found', points: 3 });
  assert.equal(datasetFailure({ status: 502 }).status, 'unreadable');
  assert.equal(datasetFailure({ status: 404, body: { detail: { reason: 'not_found' } } }).status, 'not_found');
});

test('the follow-up context is capped, drops the per-day detail first, and says when it did', () => {
  const days = Array.from({ length: 400 }, (_, i) => ({ day: '2026-08-' + i, min: 1, mean: 2, max: 3, n: 24, out_of_band: 0 }));
  const inv = { live: true, title: 'AHU-3 · Bishopsgate Tower', datasets: {
    bms_trend: { status: 'found', points: 192, types: [{ reading_type: 'fan_current', days: days, latest: { value: 18.4 } }] },
    utility_bill: { status: 'found', total: { comparable: false } }, weather: { status: 'unreadable' } } };
  const ctx = investigationContext(inv);
  assert.match(ctx, /per-day and per-week detail removed to fit/);
  assert.match(ctx, /"value":18.4/);
  assert.ok(ctx.length < CONTEXT_CAP + 1200);
  assert.equal(investigationContext({ live: true }), '', 'no datasets yet, no block');
});


// ── Review fixes, 28 Sep 2026 ───────────────────────────────────────────────────────────

test('a request for records is a records request, not an inspection booking', () => {
  const d = mailDraftR('records', { assetName: 'AHU-3', building: 'Bishopsgate Tower', vendor: 'Apex Mechanical',
    vendorEmail: 'ops@apex.example' }, { scope: 'Request the report and water-treatment log for the closed planned visit — neither is on file' });
  assert.equal(d.emKind, 'records');
  assert.match(d.emSubject, /^Records request — AHU-3/);
  assert.match(d.emBody, /Please send/);
  assert.match(d.emBody, /report and water-treatment log/);
  assert.doesNotMatch(d.emBody, /Please inspect|propose a date/);
});

test('the follow-up context withholds last year when there is no comparison, and says so', () => {
  const inv = { live: true, title: 'Boiler 1 · Bishopsgate Tower', datasets: {
    utility_bill: { status: 'found', total: { now_kwh: 1505169, last_year_kwh: 8383, now_days: 56, last_year_days: 1, comparable: false, change_pct: null },
      fuels: { gas: { now_kwh: 1505169, last_year_kwh: 8383, comparable: false, weeks: [{ now_kwh: 1, last_year_kwh: 8383 }], cost_gbp: { now: 1, last_year: 2347.24 } } } },
    weather: { status: 'found', total: { hdd: 1, last_year_hdd: 26.9, comparable: false, hdd_change_pct: -97.4 }, months: [{ month: '2026-09', last_year_hdd: 26.9 }] },
    bms_trend: { status: 'found', points: 96 } } };
  const ctx = investigationContext(inv);
  assert.doesNotMatch(ctx, /8383|2347\.24|26\.9|-97\.4/);
  assert.match(ctx, /last_year_withheld/);
  assert.match(ctx, /Where comparable is false there is no comparison with last year/);
  const ok = investigationContext({ live: true, datasets: { utility_bill: { status: 'found', total: { last_year_kwh: 1000, comparable: true, change_pct: 10 } } } });
  assert.match(ok, /"last_year_kwh":1000/);
  assert.equal(inv.datasets.utility_bill.total.last_year_kwh, 8383, 'the investigation itself is not changed');
});

test('a route the deployed backend does not have is unreadable, not "not on record"', () => {
  assert.equal(datasetFailure({ status: 404, body: { detail: 'Not Found' } }).status, 'unreadable');
  assert.match(datasetFailure({ status: 404, body: { detail: 'Not Found' } }).detail, /not on the deployed backend/);
  assert.equal(datasetFailure({ status: 404, body: { detail: { ok: false, reason: 'not_found' } } }).status, 'not_found');
});


test('several vendor contacts and none marked primary are named, and none is chosen', () => {
  const steps = actionSteps('inspect', { assetName: 'AHU-3', building: 'Bishopsgate Tower', vendor: 'Apex Mechanical',
    vendorEmail: null, vendorEmailCandidates: ['accounts@apex.co.uk', 'helpdesk@apex.co.uk'] });
  assert.match(steps[2].t, /several contacts on record and none marked primary, so the address is left for you: accounts@apex\.co\.uk, helpdesk@apex\.co\.uk/);
});
