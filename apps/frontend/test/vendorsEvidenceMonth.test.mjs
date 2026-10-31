// The Evidence tab's month and year picker.
//
// 28 Sep 2026: the tab showed only the jobs behind the newest card. Apex Mechanical had
// cards for Jan and Apr–Sep 2026 and 202 scored work orders in the database, and the page
// offered 32 of them with no way to reach the rest. Hussain asked to choose the month and
// the year. These hold the picker: it offers the months this vendor has a card for, opens on
// the newest, reads another month's work orders only when that month is chosen (a GET, one
// vendor and one month), and says so plainly while it reads or when the read fails.
//
// Runs the real controller against the real renderVals, the way vendorsEvidence.test.mjs does.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');
const { complianceApi } = await import('../src/api/compliance.js');

const VID = '00000000-0000-0000-0000-0000000000b1';
const OTHER = '00000000-0000-0000-0000-0000000000b2';
const SEP = '2026-09-01', AUG = '2026-08-01', JUL = '2026-07-01', DEC25 = '2025-12-01', JUN = '2026-06-01';
const WEIGHTS = { sla_response_pct: 25, sla_completion_pct: 25, first_fix_pct: 20, recall_pct: 15, accreditation_pct: 15, blocked_score_cap: 60 };

//: One scored work order as GET /wo-scores returns it: met on every check unless told otherwise.
const score = (code, month, extra) => Object.assign({
  id: 'sc-' + code + '-' + month, vendor_id: VID, wo_code: code, score_month: month, priority: 'P2',
  asset_name: 'CHILLER-101', building_name: 'Bishopsgate Tower', criticality: 'L2',
  reported_at: month.slice(0, 8) + '12T09:00:00+00:00', attended_at: month.slice(0, 8) + '12T12:00:00+00:00',
  completed_at: month.slice(0, 8) + '12T20:00:00+00:00',
  sla_response_met: true, sla_completion_met: true, response_hours: 3, response_target_hours: 24,
  completion_hours: 11, completion_target_hours: 24, first_fix: true, recall: false,
  overall_score: 100, contract_parameters_id: 'cp-1'
}, extra || {});
const card = (vendor_id, name, month, overall) => ({
  id: 'card-' + vendor_id + '-' + month, vendor_id, vendor_name: name, score_month: month, overall_score: overall, trend_delta: 0,
  component_breakdown: { weights_snapshot: WEIGHTS, sla_response: 25, sla_completion: 25, first_fix: 18, recall: 15, accreditation: 10 },
  ppm_compliance_pct: null, matched_flagged_ratio: 1, block_capped: false, wo_score_ids: []
});
// Apex Mechanical: cards for Sep, Aug and Jul 2026 and Dec 2025. The other vendor's only card
// is Jun 2026, which must never be offered for Apex. The page's first read carries each
// vendor's newest month only — `latest: true` — so Sep is on hand and the rest are not.
const raw = () => ({
  fetchedAt: '2026-09-28T07:00:00Z',
  errors: {},
  summary: {
    ok: true, pending_approvals: 0, weights: WEIGHTS,
    scorecards: [card(VID, 'Apex Mechanical', SEP, 86), card(VID, 'Apex Mechanical', AUG, 88),
      card(VID, 'Apex Mechanical', JUL, 89), card(VID, 'Apex Mechanical', DEC25, 95),
      card(OTHER, 'Apex Lifts', JUN, 93)]
  },
  contracts: { ok: true, parameters: [] },
  weights: { ok: true, weights: WEIGHTS },
  approvals: { ok: true, count: 0, items: [] },
  certificates: [],
  coverage: {}, packs: {},
  woScores: { ok: true, wo_scores: [score('WO-B-301-6001', SEP), score('WO-B-301-6002', SEP, { recall: true })] }
});
const augRows = [score('WO-B-301-5001', AUG), score('WO-B-301-5002', AUG), score('WO-B-301-5003', AUG, { sla_response_met: false, response_hours: 30 })];

//: A GET /wo-scores the test answers by hand, so the reading state can be seen.
let calls, pending;
const realWoScores = opsApi.woScores;
const answerWith = (rows) => pending.shift().resolve({ ok: true, count: rows.length, limit: 1000, truncated: false, wo_scores: rows });
const failWith = (msg) => pending.shift().reject(new Error(msg));
const settle = () => new Promise((r) => setTimeout(r, 0));

let c;
beforeEach(() => {
  calls = []; pending = [];
  opsApi.woScores = (q) => {
    calls.push(q);
    return new Promise((resolve, reject) => pending.push({ resolve, reject }));
  };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'vp', vpRaw: raw(), vpVendor: VID, vpTab: 2 });
});
afterEach(() => {
  opsApi.woScores = realWoScores;
  clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._vpRefresh);
});
const vp = () => c.renderVals().vp;
const pickMonth = (iso) => vp().evPickMonth({ target: { value: iso } });
const pickYear = (y) => vp().evPickYear({ target: { value: y } });
const shownCodes = () => vp().evRows.map((r) => r.wo);

test('the picker opens on the newest card, and that month costs no extra read', () => {
  const v = vp();
  assert.equal(v.evMonthShow, 'flex');
  assert.equal(v.evMonth, SEP);
  assert.equal(v.evYear, '2026');
  assert.deepEqual(shownCodes().sort(), ['WO-B-301-6001', 'WO-B-301-6002']);
  assert.equal(calls.length, 0, 'the first read already carries the newest month');
  assert.match(v.evSummary, /Sep 2026/);
});

test('it offers the years and months this vendor has a card for — and no other vendor\'s', () => {
  const v = vp();
  assert.deepEqual(v.evYears.map((y) => y.value), ['2026', '2025'], 'newest year first');
  const enabled = v.evMonthOpts.filter((o) => !o.disabled).map((o) => o.value);
  assert.deepEqual(enabled, [JUL, AUG, SEP], 'Jun is the other vendor\'s card, not this one\'s');
  assert.equal(v.evMonthOpts.length, 12, 'every month of the year is listed, the unscored ones disabled');
  assert.equal(v.evMonthOpts.find((o) => o.value === JUN).disabled, true);
});

test('choosing another month reads that vendor and month only, says it is reading, then shows them', async () => {
  pickMonth(AUG);
  assert.deepEqual(calls, [{ vendor_id: VID, score_month: AUG, limit: 1000 }]);
  let v = vp();
  assert.equal(v.evMonth, AUG);
  assert.equal(v.breachEmptyShow, 'block');
  assert.match(v.breachEmpty, /Reading the work orders scored for Aug 2026/);
  assert.deepEqual(v.evRows, [], 'Sep\'s jobs are not shown under Aug while it reads');

  answerWith(augRows);
  await settle();
  v = vp();
  assert.equal(v.breachEmptyShow, 'none');
  assert.equal(v.evRows.length, 3);
  assert.equal(v.evRows[0].wo, 'WO-B-301-5003', 'the miss leads, as in every month');
  assert.match(v.evSummary, /3 work orders scored for the Aug 2026 card/);
  assert.equal(v.evFilters.find((f) => f.key === 'response').n, '1');
});

test('a month already read is not read again, and the newest month never is', async () => {
  pickMonth(AUG);
  answerWith(augRows);
  await settle();
  pickMonth(SEP);
  assert.deepEqual(shownCodes().sort(), ['WO-B-301-6001', 'WO-B-301-6002']);
  pickMonth(AUG);
  assert.equal(calls.length, 1);
  assert.equal(vp().evRows.length, 3);
});

test('"Back to" returns to the newest card', async () => {
  assert.equal(vp().evLatestShow, 'none');
  pickMonth(AUG);
  answerWith(augRows);
  await settle();
  const v = vp();
  assert.equal(v.evLatestShow, 'inline-flex');
  assert.equal(v.evLatestLabel, 'Back to Sep 2026');
  v.evLatest();
  assert.equal(vp().evMonth, SEP);
  assert.equal(vp().evRows.length, 2);
});

test('a failed read names the month and the error, and Retry reads it again', async () => {
  pickMonth(AUG);
  failWith('502 Bad Gateway');
  await settle();
  let v = vp();
  assert.equal(v.evMonthShow, 'flex', 'the picker stays so another month can be chosen');
  assert.equal(v.breachEmptyShow, 'block');
  assert.match(v.breachEmpty, /Aug 2026/);
  assert.match(v.breachEmpty, /502 Bad Gateway/);
  // The select is already on August and re-choosing it fires no change event, so the retry
  // is a button, not an instruction to choose the month again.
  assert.equal(v.evRetryShow, 'inline-block');
  v.evRetry();
  assert.equal(calls.length, 2, 'Retry reads the failed month again');
  answerWith(augRows);
  await settle();
  assert.equal(vp().evRows.length, 3);
});

test('a month the engine returned nothing for says so, rather than looking unread', async () => {
  pickMonth(JUL);
  answerWith([]);
  await settle();
  const v = vp();
  assert.equal(v.breachEmptyShow, 'block');
  assert.match(v.breachEmpty, /No scored work order came back for Jul 2026/);
});

test('changing the year keeps the month when that year has it, else takes the year\'s newest', async () => {
  pickYear('2025');
  assert.equal(vp().evMonth, DEC25, 'Sep 2025 has no card, so the newest 2025 month is taken');
  assert.deepEqual(calls.map((q) => q.score_month), [DEC25]);
  answerWith([score('WO-B-301-0901', DEC25)]);
  await settle();
  pickYear('2026');
  assert.equal(vp().evMonth, SEP, 'back in 2026, Dec is not carded, so the newest — Sep — is shown');
});

test('choosing a month starts the view clean: All, first page, no row open — the search stays', async () => {
  const v = vp();
  v.evFilters.find((f) => f.key === 'recall').click();
  vp().evSetQuery({ target: { value: 'WO-B-301' } });
  vp().evRows[0].toggle();
  pickMonth(AUG);
  answerWith(augRows);
  await settle();
  const after = vp();
  assert.equal(after.evFilters.find((f) => f.active).key, 'all');
  assert.equal(after.evQuery, 'WO-B-301');
  assert.equal(after.evRows.every((r) => !r.open), true);
});

test('another vendor opens on its own newest card, not the month chosen for the last one', async () => {
  pickMonth(AUG);
  answerWith(augRows);
  await settle();
  c.setState({ vpVendor: OTHER });
  const v = vp();
  assert.equal(v.evMonth, JUN);
  assert.equal(calls.length, 1);
});

test('two copies of one job in the chosen month are one row, and the copy is noted', async () => {
  pickMonth(AUG);
  const a = score('WO-B-301-5001', AUG);
  answerWith([a, Object.assign({}, a, { id: a.id + '-copy' })]);
  await settle();
  const v = vp();
  assert.equal(v.evRows.length, 1);
  assert.match(v.evDupeNote, /1 work order was scored more than once/);
});

test('a read that lands after a company switch is dropped', async () => {
  pickMonth(AUG);
  c.resetLiveData();
  answerWith(augRows);
  await settle();
  assert.deepEqual(c.state.vpEvMonths, {});
});

test('a vendor with no card has no picker', () => {
  const r = raw();
  r.summary.scorecards = [card(OTHER, 'Apex Lifts', JUN, 93)];
  r.contracts = { ok: true, parameters: [{ id: 'cp-1', vendor_id: VID, vendor_name: 'Apex Mechanical', status: 'draft' }] };
  r.woScores = { ok: true, wo_scores: [] };
  c.setState({ vpRaw: r, vpVendor: VID });
  assert.equal(vp().evMonthShow, 'none');
});

// A reload — Rebuild scorecards, or the quarter-hour timer — can change any month's scores.
test('a reload keeps the chosen month on screen and reads it again, its jobs staying up meanwhile', async () => {
  pickMonth(AUG);
  answerWith(augRows);
  await settle();
  const saved = {};
  const stub = (api, name, fn) => { saved[name] = [api, api[name]]; api[name] = fn; };
  const r = raw();
  stub(opsApi, 'contractSummary', async () => r.summary);
  stub(opsApi, 'contracts', async () => r.contracts);
  stub(opsApi, 'weights', async () => r.weights);
  stub(opsApi, 'contractApprovals', async () => r.approvals);
  stub(complianceApi, 'listCertificates', async () => ({ certificates: [] }));
  stub(complianceApi, 'vendorCoverage', async () => ({ vendors: [] }));
  stub(complianceApi, 'countryPack', async () => ({ types: [] }));
  const monthRead = opsApi.woScores;
  opsApi.woScores = (q) => (q && q.latest ? Promise.resolve(r.woScores) : monthRead(q));
  try {
    await c.vpLoad();
    assert.deepEqual(calls.map((q) => q.score_month), [AUG, AUG], 'Aug is read again');
    assert.equal(vp().evMonth, AUG);
    assert.equal(vp().evRows.length, 3, 'the jobs already read stay up while it re-reads');
    answerWith(augRows.slice(0, 2));
    await settle();
    assert.equal(vp().evRows.length, 2);
  } finally {
    opsApi.woScores = monthRead;
    Object.keys(saved).forEach((k) => { saved[k][0][k] = saved[k][1]; });
  }
});

test('the Evidence tab counts the month on screen', async () => {
  const count = () => c.renderVals().vpTabs.find((t) => t.label === 'Evidence').n;
  assert.equal(count(), '2');
  pickMonth(AUG);
  assert.equal(count(), '…', 'not Sep\'s count while Aug reads');
  answerWith(augRows);
  await settle();
  assert.equal(count(), '3');
});

// 28 Sep 2026: GET /contract-performance/approvals 500'd for forty minutes and the Invoices tab
// read "No invoice line for this vendor is held" over 21 held lines. A read that failed is not
// a read that found nothing, and the tab must not word the two alike.
test('the Invoices tab says the queue could not be read, not that nothing is held', () => {
  const r = raw();
  r.approvals = null;
  r.errors = { approvals: '500 Internal Server Error' };
  c.setState({ vpRaw: r, vpVendor: VID, vpTab: 4 });
  const v = vp();
  assert.equal(v.invEmptyShow, 'block');
  assert.doesNotMatch(v.invEmpty, /No invoice line for this vendor is held/);
  assert.match(v.invEmpty, /could not be read/);
  assert.match(v.invEmpty, /500 Internal Server Error/);
  assert.equal(c.renderVals().vpTabs.find((t) => t.label === 'Invoices').n, '—', 'not "0" — nothing was counted');
});

test('with the queue read, an empty Invoices tab still says nothing is held', () => {
  c.setState({ vpTab: 4 });
  assert.match(vp().invEmpty, /No invoice line for this vendor is held/);
});
