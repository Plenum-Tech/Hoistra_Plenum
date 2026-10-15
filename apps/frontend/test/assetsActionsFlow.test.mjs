// The three asset-row buttons, driven through the real controller and renderVals the way the
// Assets screen drives them. On 28 Sep 2026 all three were dead ends: the per-asset read went
// out without the company in view and 404'd, the failure was cached for good, the drafts gave
// up with "No vendor on this asset record", and Investigate opened a four-line drawer. These
// hold the working shape: the dock opens, the four agents run, a draft addressed from the
// vendor record or a real investigation fills it, and nothing is written but the email the
// reader approves (Hussain, 28 Sep: email only, real sending).
import { test, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { energyApi } = await import('../src/api/energy.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');

const BLD = { buildingId: 'b1', id: 'b1', uuid: 'b1', name: 'Bishopsgate Tower', euiN: 214, benchN: 180, deviation: 19,
  std: 'CIBSE TM46', mix: [], route: 'sub-meter', granularity: 'sub-metered', metersActive: 4, cc: 'UK' };
const AHU = { asset_id: 'a1', asset_name: 'AHU-3', asset_code: 'AS-1042', category_name: 'Air handling', active: true,
  health_score: 70, building_id: 'b1', installation_date: '2009-04-01' };

const seed = (c, extra) => c.setState(Object.assign({
  signedIn: true, view: 'module', module: 'assets', filter: 'All', account: {},
  bldLive: [BLD], asLive: [AHU], asLiveWos: [], asLocations: [],
  asSections: [{ section_id: 's1', building_id: 'b1', name: 'L4 East', section_type: 'tenant floor', eui_kwh_per_m2: 231,
    reference_eui_kwh_m2: 180, deviation_pct: 28, measured: true, meters: 1 }],
  asCondBands: [{ asset_id: 'a1', band: 'threat', reasons: ['section_over_reference', 'anomaly_open'], section_id: 's1',
    section: 'L4 East', vendor: 'Apex Mechanical', section_deviation_pct: 28 }],
  asAnoms: [{ id: 'x1', asset_id: 'a1', status: 'open', anomaly_type: 'Non-occupancy spike',
    detected_at: new Date(Date.now() - 21 * 86400000).toISOString(), financial_gbp: 38400 }],
  asInspections: [{ asset_id: 'a1', wo_code: 'WO-4472', inspection_date: '2026-06-14', vendor: 'Apex Mechanical', observations: 'Filter ΔP 210 Pa' }],
  asOpenB: ['b1'], asOpenS: []
}, extra || {}));

const row = (c) => {
  for (const g of c.renderVals().asGroups || []) {
    for (const sec of g.sections || []) for (const r of sec.rows || []) if (r.name === 'AHU-3') return r;
    for (const r of g.rows || []) if (r.name === 'AHU-3') return r;
  }
  throw new Error('AHU-3 row not rendered');
};
const E = { stopPropagation() {} };

const real = { intel: energyApi.assetIntelligence, inv: energyApi.assetInvestigate, send: opsApi.sendEmail,
  bms: energyApi.assetBmsTrend, bill: energyApi.assetUtilityBill, dd: energyApi.degreeDays, sum: deepAgentsApi.investigationSummary };
// The datasets and the summary are answered by default, so no test reaches a network; the
// tests that are about them replace these.
const DS = {
  bms: { ok: true, written: false, asset: { id: 'a1' }, status: 'found', kind: 'asset_readings', points: 192, out_of_band: [] },
  bill: { ok: true, written: false, status: 'found', total: { comparable: false, now_days: 56, last_year_days: 1 } },
  wx: { ok: true, written: false, status: 'found', source: 'open-meteo', total: { hdd: 180.4, comparable: true } }
};
const SUMMARY = { ok: true, lines: [{ source: 'bms_trend', text: 'All 8 graded readings are within their bands.' },
  { source: 'weather', text: 'Heating degree days are flat on last year.' }], overall: 'Nothing in the readings explains the cost.', dropped: [] };
let c;
beforeEach(() => {
  c = new HoistraLogic(); seed(c);
  energyApi.assetBmsTrend = async () => DS.bms; energyApi.assetUtilityBill = async () => DS.bill;
  energyApi.degreeDays = async () => DS.wx; deepAgentsApi.investigationSummary = async () => SUMMARY;
});
afterEach(() => {
  // Anything still pending from the test (a dataset read, a summary) drops out the way it does
  // when a newer investigation begins, instead of reaching a real network after the restore.
  c._invToken = (c._invToken || 0) + 1;
  energyApi.assetIntelligence = real.intel; energyApi.assetInvestigate = real.inv; opsApi.sendEmail = real.send;
  energyApi.assetBmsTrend = real.bms; energyApi.assetUtilityBill = real.bill; energyApi.degreeDays = real.dd;
  deepAgentsApi.investigationSummary = real.sum;
  clearInterval(c._orchTick); clearInterval(c._invTick); clearTimeout(c._tt);
  try { mock.timers.reset(); } catch (e) { /* not enabled */ }
});
const notFound = () => Promise.reject(Object.assign(new Error('No such asset in your buildings.'), { status: 404 }));

// ── Raise work order / Request inspection ──────────────────────────────────────────────

test('Raise work order opens the dock with the four agents and a draft, even when the per-asset read 404s', async () => {
  energyApi.assetIntelligence = notFound;
  await row(c).wo(E);
  assert.equal(c.state.orchOpen, true);
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.orchTask.label, 'Raise work order — AHU-3 · Bishopsgate Tower');
  assert.deepEqual(c.state.orchTask.steps.map((s) => s.a), ['Orchestrator', 'Planner', 'Worker', 'Quality']);
  assert.equal(c.state.emKind, 'wo');
  assert.equal(c.state.emSubject, 'Work order request — AHU-3 · Bishopsgate Tower');
  assert.match(c.state.emBody, /^Hello Apex Mechanical team,/, 'the vendor the condition read names, when the per-asset read has none');
  assert.match(c.state.emBody, /L4 East · tenant floor at 231 kWh\/m²\/yr against a reference of 180 \(\+28%\)/);
  assert.match(c.state.emBody, /Non-occupancy spike on AHU-3 · £38,400 annualised · active 21 days/);
  assert.equal(c.state.emTo, '', 'no address on record, so none is invented');
  assert.equal(c.state.fSpec, null, 'no stale action spec can reword the outcome');
  assert.equal(c.state.sessions[0].title, 'Raise work order — AHU-3 · Bishopsgate Tower', 'the task is a session');
});

test('the vendor\'s address on record fills the To line', async () => {
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { id: 'a1', vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk', section: 'L4 East' } });
  await row(c).inspect(E);
  assert.equal(c.state.emKind, 'inspect');
  assert.equal(c.state.emTo, 'ops@apexmechanical.co.uk');
  assert.match(c.state.orchTask.steps[2].t, /Apex Mechanical at ops@apexmechanical\.co\.uk/);
});

test('a failed per-asset read is tried again on the next click, and two clicks share one read', async () => {
  let n = 0;
  energyApi.assetIntelligence = () => { n += 1; return n === 1 ? notFound() : Promise.resolve({ ok: true, asset: { vendor: 'Apex Mechanical', vendor_email: 'a@b.co' } }); };
  await row(c).wo(E);
  assert.equal(c.state.emTo, '');
  await Promise.all([c.asCondLoadIntel('a1'), c.asCondLoadIntel('a1')]);
  assert.equal(n, 2, 'the 404 was not cached forever, and the two calls shared one request');
  await row(c).wo(E);
  assert.equal(c.state.emTo, 'a@b.co');
  assert.equal(n, 2, 'an answered read is not repeated');
});

test('the per-asset reads name the company in view', async () => {
  const { setActingOrg } = await import('../src/api/client.js');
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => { seen.push(String(url)); return { ok: true, status: 200, text: async () => '{"ok":true}' }; };
  setActingOrg('ad88d1ed-4376-447c-8196-e4e5b81d5af6');
  try {
    await real.intel('a1');
    await real.inv('a1');
  } finally { globalThis.fetch = realFetch; setActingOrg(null); }
  assert.match(seen[0], /\/api\/energy\/assets\/a1\/intelligence\?.*organization_id=ad88d1ed/);
  assert.match(seen[1], /\/api\/energy\/assets\/a1\/investigate\?.*organization_id=ad88d1ed/);
});

test('the dock shows the four agents ticking through', async () => {
  energyApi.assetIntelligence = notFound;
  await row(c).wo(E);
  const v = c.renderVals();
  assert.equal(v.orchStepsShow, 'flex');
  assert.equal(v.orchSteps.length, 4);
  assert.equal(v.orchSteps[0].state, 'live');
  assert.equal(v.fEmail, true);
});

test('the row the dock is working on is marked while it does', async () => {
  energyApi.assetIntelligence = notFound;
  assert.equal(row(c).active, false);
  await row(c).wo(E);
  assert.equal(row(c).active, true);
  assert.equal(row(c).rowBg, 'var(--marker-tint)');
  c.closeOrch();
  assert.equal(row(c).active, false);
});

// ── sending ──────────────────────────────────────────────────────────────────────────

test('an approved work-order request says it was emailed, and that nothing else was written', async () => {
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' } });
  const sent = [];
  opsApi.sendEmail = async (b) => { sent.push(b); return { ok: true, status: 'sent' }; };
  await row(c).wo(E);
  await c.renderVals().em.send();
  assert.equal(sent.length, 1);
  assert.equal(sent[0].to, 'ops@apexmechanical.co.uk');
  assert.equal(sent[0].subject, 'Work order request — AHU-3 · Bishopsgate Tower');
  assert.equal(c.state.flow, null);
  assert.match(c.state.flowDone, /^Work order request sent to ops@apexmechanical\.co\.uk about AHU-3 · Bishopsgate Tower\./);
  assert.match(c.state.flowDone, /No work-order record was created in Hoistra/);
  assert.doesNotMatch(c.state.flowDone, /Extension request/);
});

test('an approved inspection request reads as one', async () => {
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' } });
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  await row(c).inspect(E);
  await c.renderVals().em.send();
  assert.match(c.state.flowDone, /^Inspection request sent to ops@apexmechanical\.co\.uk about AHU-3 · Bishopsgate Tower\./);
});

test('a dry run still says it was not delivered', async () => {
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' } });
  opsApi.sendEmail = async () => ({ ok: true, status: 'dry_run' });
  await row(c).wo(E);
  await c.renderVals().em.send();
  assert.match(c.state.flowDone, /NOT delivered/);
});

// ── Investigate ──────────────────────────────────────────────────────────────────────

const RES = {
  ok: true, written: false,
  asset: { asset_id: 'a1', asset_name: 'AHU-3', building: 'Bishopsgate Tower', vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' },
  plan: 'I will walk 3 sources — the readings first, then the maintenance record around the asset, then the documents that should exist for it.',
  sources: [
    { source: 'meter_reading', question: 'sub-metered plant, 8 weeks', status: 'found', badge: 'sub-metered' },
    { source: 'work_order', question: 'planned maintenance by Apex Mechanical', status: 'partial', badge: 'closed, no report' },
    { source: 'document', question: 'records filed against this asset', status: 'not_found', badge: 'not found' }
  ],
  evidence: [{ statement: 'The PPM visit closed with no report, and no service or treatment log is filed against the asset.',
    sources: ['work_order', 'document'], confidence: 1, kind: 'missing record' }],
  conclusion: { cause: 'The records this asset should carry are not on file.', cost_to_date: 15508, cost_annualised: 38400,
    confirmation_required: true, caveat: 'A record the contract requires is missing, so the cause rests on inference.' },
  actions: [
    { id: 'request_records', label: 'Request PPM report and treatment log', detail: 'draft to Apex Mechanical',
      endpoint: 'POST /api/work-orders/', body: { request_type: 'inspection', issue_description: 'Request the report and log for the closed planned visit — neither is on file' } },
    { id: 'resequence', label: 'Re-sequence to favour the healthier unit', detail: 'BMS change', endpoint: null, body: null, note: 'a BMS change; this platform proposes it but does not make it' }
  ]
};

test('Investigate asks the real engine and plays its walk in the dock', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let asked = null;
  energyApi.assetInvestigate = async (id) => { asked = id; return RES; };
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  assert.equal(asked, 'a1');
  assert.equal(c.state.flow, 'investigate');
  assert.equal(c.state.orchTask.label, 'Investigate — AHU-3 · Bishopsgate Tower');
  assert.equal(c.state.orchTask.steps[1].t, 'Walk 3 sources: meter_reading, work_order, document', 'the plan names what was actually walked');
  let v = c.renderVals();
  assert.equal(v.fInvestigate, true);
  assert.equal(v.invQuery, 'Why is AHU-3 at Bishopsgate Tower showing a non-occupancy spike worth £38,400 a year, and what should I do about it?');
  assert.equal(v.invPlan, RES.plan);
  assert.equal(v.invStage1, false, 'the evidence waits for the walk');
  mock.timers.tick(420 * 3);
  v = c.renderVals();
  assert.deepEqual(v.invSources.map((s) => s.icon), ['ph-check-circle', 'ph-check-circle', 'ph-x-circle'], 'a source that gave nothing back says so');
  assert.equal(v.invSources[2].gap, 'var(--st-risk)');
  mock.timers.tick(420 * 3);
  v = c.renderVals();
  assert.equal(v.invStage3, true);
  assert.equal(v.invStatus, 'Actions ready — nothing has been written yet');
  assert.deepEqual(v.invFindings.map((f) => [f.conf, f.gapShow]), [['100%', 'inline-flex']]);
  assert.equal(v.inv.costLine, '£15,508 to date · £38,400 annualised if left');
  assert.equal(v.invEscShow, 'flex');
  assert.equal(v.invApproveShow, 'none', 'every action here is an email to be read first, so there is no approve-all');
});

test('a source whose query failed reads as unreadable, not as found or as absent', async () => {
  // The weather walk failed on every call for as long as its SQL did not parse, and said
  // "no degree days". The engine now returns `unreadable`; the dock must not dress it as the
  // amber tick a partial answer gets.
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => ({ ...RES, sources: [...RES.sources,
    { source: 'weather', question: 'cooling degree days vs last year', status: 'unreadable', badge: 'could not be read' }] });
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  mock.timers.tick(420 * 4);
  const w = c.renderVals().invSources[3];
  assert.equal(w.icon, 'ph-warning-circle');
  assert.equal(w.fg, 'var(--st-risk)');
  assert.equal(w.gap, 'var(--st-risk)');
  assert.equal(w.n, 'could not be read');
});

test('an investigation action opens its draft; a proposal the platform cannot carry out does nothing', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  let v = c.renderVals();
  const [records, reseq] = v.invActions;
  assert.equal(reseq.cursor, 'default');
  reseq.click();
  assert.equal(c.state.flow, 'investigate', 'a BMS change is not something this page can raise');
  records.click();
  assert.equal(c.state.flow, 'email');
  assert.equal(c.state.emKind, 'records', 'a request for records is not an inspection booking');
  assert.equal(c.state.emFromInv, true);
  assert.equal(c.state.emTo, 'ops@apexmechanical.co.uk', 'the engine\'s own read of the vendor record addresses it');
  assert.match(c.state.emBody, /Request the report and log for the closed planned visit — neither is on file\./);
  assert.doesNotMatch(c.state.emBody, /Please inspect|propose a date/);
  assert.equal(c.state.orchTask.label, 'Investigate — AHU-3 · Bishopsgate Tower', 'the draft opens inside the investigation, not as a new task');
});

test('sending a draft from an investigation returns to it with what happened', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  await c.renderVals().em.send();
  assert.equal(c.state.flow, 'investigate');
  const last = c.state.inv.replies[c.state.inv.replies.length - 1];
  assert.match(last.bot, /Records request sent to ops@apexmechanical\.co\.uk/);
  assert.equal(last.tag, 'sent · ');
});

test('an investigation the engine cannot answer says why, and plays nothing', async () => {
  energyApi.assetInvestigate = notFound;
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  const v = c.renderVals();
  assert.equal(v.fInvestigate, true);
  assert.match(v.invStatus, /not in the buildings you can see/);
  assert.equal(v.invStatusFg, 'var(--st-risk)');
  assert.deepEqual(v.invSources, []);
  assert.equal(v.invLive, 'none');
});

test('closing the dock mid-investigation drops the answer that arrives afterwards', async () => {
  let release;
  energyApi.assetInvestigate = () => new Promise((r) => { release = r; });
  energyApi.assetIntelligence = notFound;
  const p = row(c).investigate(E);
  c.closeOrch();
  release(RES);
  await p;
  assert.equal(c.state.inv, null, 'a late answer does not reopen a closed investigation');
  assert.equal(c.state.flow, null);
});

test('Investigate stays on the rows the design gives it: an asset with an anomaly attributed', () => {
  assert.equal(row(c).invShow, 'inline-flex');
  seed(c, { asAnoms: [] });
  assert.equal(row(c).invShow, 'none');
});

test('the Threat card never reports an engine count the summary did not carry', () => {
  seed(c, { asCondSummary: {} });
  const card = c.renderVals().asCards.find((k) => k.l === 'Threat');
  assert.doesNotMatch(card.s, /undefined/);
  seed(c, { asCondSummary: { threat: 4 } });
  assert.match(c.renderVals().asCards.find((k) => k.l === 'Threat').s, /engine reports 4/);
});

// Review, 28 Sep: a draft opened from an investigation left emFromInv set after Cancel, so the
// next, unrelated email returned into the stale investigation and lost its own confirmation.
test('cancelling a draft opened from an investigation goes back to it, and leaves nothing armed', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  assert.equal(c.state.flow, 'email');
  c.renderVals().fCancel();
  assert.equal(c.state.flow, 'investigate', 'Cancel returns to the investigation the draft came from');
  assert.equal(c.state.emFromInv, false);
  // A new task is a new conversation: nothing from the last one carries into it.
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { vendor: 'Apex Mechanical', vendor_email: 'ops@apexmechanical.co.uk' } });
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  await row(c).wo(E);
  await c.renderVals().em.send();
  assert.equal(c.state.flow, null);
  assert.match(c.state.flowDone, /^Work order request sent to/, 'its own confirmation, not a reply in the old thread');
});

// Review, 28 Sep: the post-send update restored the investigation from values read before the
// await, so closing the dock while the mail was going brought the closed investigation back.
test('closing the dock while a draft is sending does not bring the investigation back', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  let release;
  opsApi.sendEmail = () => new Promise((r) => { release = r; });
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  const p = c.renderVals().em.send();
  c.closeOrch();
  release({ ok: true, status: 'sent' });
  await p;
  assert.equal(c.state.inv, null);
  assert.equal(c.state.flow, null);
});

test('a read that failed is not reported as an empty vendor record', async () => {
  energyApi.assetIntelligence = () => Promise.reject(Object.assign(new Error('timed out'), { status: 0 }));
  await row(c).wo(E);
  assert.match(c.state.orchTask.steps[2].t, /could not be read just now/);
});

// Second review, 28 Sep.
test('Cancel after a failed send goes back to the investigation without the failure message', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  opsApi.sendEmail = async () => ({ ok: false, error: 'mailbox refused' });
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  await c.renderVals().em.send();
  assert.match(c.state.flowDone, /^Not sent/);
  c.renderVals().fCancel();
  assert.equal(c.state.flow, 'investigate');
  assert.equal(c.state.flowDone, '', 'the failure belonged to the draft that is gone');
});

test('Cancel does nothing while the draft is sending, so the investigation is not lost', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  let release;
  opsApi.sendEmail = () => new Promise((r) => { release = r; });
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  const p = c.renderVals().em.send();
  c.renderVals().fCancel();
  assert.equal(c.state.flow, 'email', 'a send in flight is not cancelled from under itself');
  release({ ok: true, status: 'sent' });
  await p;
  assert.equal(c.state.flow, 'investigate');
  assert.match(c.state.inv.replies[c.state.inv.replies.length - 1].bot, /Records request sent to/);
});


// ── The datasets behind the walk, and the orchestrator's summary of them ─────────────────

const settle = async () => { for (let i = 0; i < 8; i++) await Promise.resolve(); };

test('Investigate fetches the three datasets beside the walk, for the asset, its building and eight weeks', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  const asked = {};
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  energyApi.assetBmsTrend = async (id, w) => { asked.bms = [id, w]; return DS.bms; };
  energyApi.assetUtilityBill = async (id, w) => { asked.bill = [id, w]; return DS.bill; };
  energyApi.degreeDays = async (b, o) => { asked.wx = [b, o]; return DS.wx; };
  await row(c).investigate(E);
  await settle();
  assert.deepEqual(asked, { bms: ['a1', 8], bill: ['a1', 8], wx: ['b1', { weeks: 8 }] });
  const ds = c.state.inv.datasets;
  assert.equal(ds.bms_trend.points, 192);
  assert.equal(ds.bms_trend.ok, undefined, 'the envelope is not part of the dataset');
  assert.equal(ds.weather.source, 'open-meteo');
});

test('the summary lands once, after the walk, labelled by source, with the datasets it was written from', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  const bodies = [];
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  deepAgentsApi.investigationSummary = async (b) => { bodies.push(b); return SUMMARY; };
  await row(c).investigate(E);
  await settle();
  assert.equal(bodies.length, 0, 'nothing is summarised before the walk has played');
  mock.timers.tick(420 * 10);
  await settle();
  mock.timers.tick(420 * 5);
  await settle();
  assert.equal(bodies.length, 1, 'once per investigation');
  assert.equal(bodies[0].asset.name, 'AHU-3');
  assert.equal(bodies[0].asset.building, 'Bishopsgate Tower');
  assert.equal(bodies[0].sources.utility_bill.total.last_year_days, 1);
  const replies = c.renderVals().invReplies;
  const sum = replies.find((r) => r.id === 'summary');
  assert.equal(sum.you, '', 'no question was asked, so no question bubble');
  assert.equal(sum.tag, 'summary · ');
  assert.deepEqual(sum.lines.map((l) => l.label), ['BMS trend', 'Weather']);
  assert.equal(sum.bot, 'Nothing in the readings explains the cost.');
  assert.equal(sum.note, '');
});

test('a summary that arrives after the dock closed is dropped', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let release;
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  deepAgentsApi.investigationSummary = () => new Promise((r) => { release = r; });
  await row(c).investigate(E);
  await settle();
  mock.timers.tick(420 * 10);
  await settle();
  assert.ok(release, 'the summary was asked for');
  c._invToken += 1; // a newer investigation began
  release(SUMMARY);
  await settle();
  const sum = (c.state.inv.replies || []).find((r) => r.id === 'summary');
  assert.equal(sum.lines.length, 0, 'the late answer did not replace the placeholder of a stale investigation');
});

test('a summary that could not be written says why and leaves the walk standing', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  deepAgentsApi.investigationSummary = async () => ({ ok: false, reason: 'no anthropic key' });
  await row(c).investigate(E);
  await settle();
  mock.timers.tick(420 * 10);
  await settle();
  const v = c.renderVals();
  const sum = v.invReplies.find((r) => r.id === 'summary');
  assert.match(sum.bot, /^Summary unavailable — no anthropic key\./);
  assert.equal(v.invStage3, true);
});

test('a dataset read that fails reaches the summary as unreadable, not as missing', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  const bodies = [];
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  energyApi.degreeDays = () => Promise.reject(Object.assign(new Error('bad gateway'), { status: 502 }));
  deepAgentsApi.investigationSummary = async (b) => { bodies.push(b); return SUMMARY; };
  await row(c).investigate(E);
  await settle();
  mock.timers.tick(420 * 10);
  await settle();
  assert.deepEqual(bodies[0].sources.weather, { status: 'unreadable', detail: 'the endpoint did not answer (HTTP 502)' });
});

test('a follow-up typed during an investigation carries its datasets; with none open it does not', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  assert.doesNotMatch(c.chatContext(), /An investigation of/);
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  await row(c).investigate(E);
  await settle();
  const ctx = c.chatContext();
  assert.match(ctx, /An investigation of AHU-3 · Bishopsgate Tower is open in the dock/);
  assert.match(ctx, /"points":192/);
  assert.match(ctx, /metered consumption standing in for the bill/);
  c.setState({ flow: null });
  assert.doesNotMatch(c.chatContext(), /An investigation of/);
});

test('a summary that lands after the reader has acted still sits directly under the walk', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let release;
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  energyApi.degreeDays = () => new Promise((r) => { release = r; });
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  await row(c).investigate(E);
  mock.timers.tick(420 * 10);
  c.renderVals().invActions[0].click();
  await c.renderVals().em.send();
  release(DS.wx);
  await settle();
  const replies = c.state.inv.replies;
  assert.equal(replies[0].id, 'summary');
  assert.match(replies[replies.length - 1].bot, /Records request sent to/);
});


// ── Review fixes, 28 Sep 2026 ───────────────────────────────────────────────────────────

test('switching company drops the open investigation and anything still arriving for it', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let release;
  energyApi.assetInvestigate = async () => RES;
  energyApi.assetIntelligence = notFound;
  deepAgentsApi.investigationSummary = () => new Promise((r) => { release = r; });
  await row(c).investigate(E);
  await settle();
  mock.timers.tick(420 * 10);
  await settle();
  c.resetLiveData();
  assert.equal(c.state.inv, null);
  assert.notEqual(c.state.flow, 'investigate');
  release(SUMMARY);
  await settle();
  assert.equal(c.state.inv, null, "the previous company's summary does not bring it back");
  assert.doesNotMatch(c.chatContext(), /An investigation of/);
});

test('a send that finishes after a new task began does not overwrite that task', async () => {
  mock.timers.enable({ apis: ['setInterval', 'setTimeout'] });
  let deliver;
  energyApi.assetIntelligence = notFound;
  opsApi.sendEmail = () => new Promise((r) => { deliver = r; });
  await row(c).wo(E);
  c.setState({ emTo: 'ops@apex.example' });
  const sending = c.renderVals().em.send();
  energyApi.assetInvestigate = () => new Promise(() => {});
  row(c).investigate(E);
  assert.equal(c.state.flow, 'investigate');
  deliver({ ok: true, status: 'sent' });
  await sending;
  assert.equal(c.state.flow, 'investigate', 'the investigation that took the dock keeps it');
  assert.equal(c.state.flowDone, '', "the earlier draft's confirmation is not written over it");
  assert.equal(c.state.emSending, false);
});

test('a draft reads the anomaly from the asset read when the row carries none', async () => {
  seed(c, { asAnoms: [] });
  energyApi.assetIntelligence = async () => ({ ok: true, asset: { id: 'a1', vendor: 'Apex Mechanical', vendor_email: 'ops@apex.example' },
    anomaly: { open: 2, weeks: 3, worst_pct: 30, annual_cost: 38400, currency: 'GBP' } });
  await row(c).wo(E);
  assert.match(c.state.emBody, /2 open anomalies on AHU-3 · £38,400 annualised/);
});

test('the work-order read names the company in view', async () => {
  const { setActingOrg } = await import('../src/api/client.js');
  const { workOrderApi } = await import('../src/api/workOrder.js');
  const seen = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url) => { seen.push(String(url)); return { ok: true, status: 200, text: async () => '[]' }; };
  setActingOrg('ad88d1ed-4376-447c-8196-e4e5b81d5af6');
  try { await workOrderApi.workOrders({ limit: 200, page: 1 }); } finally { globalThis.fetch = realFetch; setActingOrg(null); }
  assert.match(seen[0], /\/api\/work-orders\/\?.*organization_id=ad88d1ed/);
});

test('a work order matched by name counts only in its own building', () => {
  seed(c, { asLiveWos: [
    { work_order_id: 'w1', status: 'active', asset: 'AHU-3', building_id: 'b2' },
    { work_order_id: 'w2', status: 'active', asset: 'AHU-3', building_id: 'b1' },
    { work_order_id: 'w3', status: 'active', asset: 'AHU-3' } ] });
  assert.equal(row(c).openWorkOrders, 2, 'its own building and the one naming no building — not b2');
});

test('an email sent from an investigation with the dock closed says what was sent', async () => {
  opsApi.sendEmail = async () => ({ ok: true, status: 'sent' });
  c.setState({ flow: 'email', emKind: 'investigate', emTo: 'fm.lead@example.com', emSubject: 'Question', emBody: 'b', orchOpen: false, inv: null });
  await c.renderVals().em.send();
  assert.doesNotMatch(c.state.flowDone, /Extension request/);
  assert.match(c.state.flowDone, /Sent to fm\.lead@example\.com/);
});
