// The Evidence tab, one work order per row.
//
// 28 Sep 2026: once Apex Lifts' contract was confirmed and Rebuild scorecards ran, the tab
// listed 242 rows in one unbroken column — a row per check per work order, and every work
// order twice, because the read carried two score rows for each job in the month. Nobody
// could tell which jobs failed, or how many jobs there were. These hold the shape that
// replaced it: each work order once, its four checks side by side, the ones that missed
// something first, a filter that counts, a search, and ten jobs a page. It opens on All
// (Hussain, 28 Sep): the misses lead that list anyway, and a reader who lands on a filtered
// view reads its count as the vendor's whole month.
//
// Runs the real controller against the real renderVals, the way vendorsNoSeed.test.mjs does.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { shapeLiveVendors, uniqueScores, evidenceJobs } = await import('../src/logic/vendorsLive.js');

const VID = '00000000-0000-0000-0000-0000000000b1';
const OTHER = '00000000-0000-0000-0000-0000000000b2';
const MONTH = '2026-09-01';
const WEIGHTS = { sla_response_pct: 25, sla_completion_pct: 25, first_fix_pct: 20, recall_pct: 15, accreditation_pct: 15, blocked_score_cap: 60 };

//: One scored work order as GET /wo-scores returns it: met on every check unless told otherwise.
const score = (code, extra) => Object.assign({
  id: 'sc-' + code, vendor_id: VID, wo_code: code, score_month: MONTH, priority: 'P2',
  asset_name: 'Lift Asset-4471', building_name: 'Bishopsgate Tower', criticality: 'L2',
  reported_at: '2026-09-12T09:00:00+00:00', attended_at: '2026-09-12T12:00:00+00:00',
  completed_at: '2026-09-12T20:00:00+00:00',
  sla_response_met: true, sla_completion_met: true, response_hours: 3, response_target_hours: 4,
  completion_hours: 11, completion_target_hours: 24, first_fix: true, recall: false,
  overall_score: 100, contract_parameters_id: 'cp-1'
}, extra || {});
//: The same job, written a second time by the scorer.
const copyOf = (s) => Object.assign({}, s, { id: s.id + '-copy' });
const missResponse = { sla_response_met: false, response_hours: 5.1 };

const card = (vendor_id, name) => ({
  id: 'card-' + vendor_id, vendor_id, vendor_name: name, score_month: MONTH, overall_score: 93, trend_delta: 0,
  component_breakdown: { weights_snapshot: WEIGHTS, sla_response: 25, sla_completion: 25, first_fix: 18, recall: 15, accreditation: 10 },
  ppm_compliance_pct: null, matched_flagged_ratio: 1, block_capped: false, wo_score_ids: []
});
const raw = (scores) => ({
  fetchedAt: '2026-09-28T07:00:00Z',
  errors: {},
  summary: { ok: true, pending_approvals: 0, weights: WEIGHTS, scorecards: [card(VID, 'Apex Lifts'), card(OTHER, 'Apex Mechanical')] },
  contracts: { ok: true, parameters: [] },
  weights: { ok: true, weights: WEIGHTS },
  approvals: { ok: true, count: 0, items: [] },
  certificates: [],
  coverage: {}, packs: {},
  woScores: { ok: true, wo_scores: scores }
});
const shape = (scores) => shapeLiveVendors(raw(scores), new Date(2026, 8, 28, 9, 0, 0));

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'vp' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._vpRefresh); };
const open = (scores, vendor) => {
  c.setState({ vpRaw: raw(scores), vpVendor: vendor || VID, vpTab: 2 });
  return c.renderVals();
};
const vp = () => c.renderVals().vp;
const many = (n, extra) => Array.from({ length: n }, (_, i) => score('WO-B-301-' + (6000 + i), extra));

// ── the jobs, as data ───────────────────────────────────────────────────────────────────

test('a work order scored twice in one month is one job, and the copy is counted', () => {
  const a = score('WO-1');
  const u = uniqueScores([a, copyOf(a), score('WO-2')]);
  assert.deepEqual(u.scores.map((s) => s.wo_code), ['WO-1', 'WO-2']);
  assert.equal(u.dupes, 1);
});

test('the same code in two months is two jobs, not a duplicate', () => {
  const u = uniqueScores([score('WO-1'), score('WO-1', { id: 'sc-aug', score_month: '2026-08-01' })]);
  assert.equal(u.scores.length, 2);
  assert.equal(u.dupes, 0);
});

// Review, 28 Sep: folding on the code alone merged two different jobs that share one.
test('two different jobs that share a code are two jobs', () => {
  const u = uniqueScores([score('WO-1'), score('WO-1', { id: 'sc-other', completed_at: '2026-09-20T10:00:00+00:00' })]);
  assert.equal(u.scores.length, 2);
  assert.equal(u.dupes, 0);
});

// Second review, 28 Sep: GET /wo-scores takes completed_at from the work order joined BY CODE,
// so two jobs sharing a code read the same completion; what they scored still tells them apart.
test('two jobs sharing a code and a joined work order stay two when their scores differ', () => {
  const u = uniqueScores([score('WO-1'), score('WO-1', { id: 'sc-other', overall_score: 55, sla_response_met: false })]);
  assert.equal(u.scores.length, 2);
});

test('of two copies, the one that names its building and asset is kept', () => {
  const bare = score('WO-1', { id: 'bare', building_name: null, asset_name: null });
  const named = score('WO-1', { id: 'named' });
  assert.equal(uniqueScores([bare, named]).scores[0].id, 'named');
});

test('a score with no work-order code is never folded into another', () => {
  const u = uniqueScores([score(null, { id: 'x1' }), score(null, { id: 'x2' })]);
  assert.equal(u.scores.length, 2);
});

test('each job carries its four checks side by side', () => {
  const [j] = evidenceJobs([score('WO-1', Object.assign({ first_fix: false }, missResponse))]);
  assert.equal(j.wo, 'WO-1');
  assert.deepEqual(j.response, { target: '4h', actual: '5.1h', met: false });
  assert.deepEqual(j.completion, { target: '24h', actual: '11h', met: true });
  assert.equal(j.firstFix, false);
  assert.equal(j.recall, false);
  assert.deepEqual(j.missed, ['Response', 'First fix']);
  assert.equal(j.weight, '1.5×', 'an SLA miss on an L2 asset is weighted 1.5×');
});

test('a job that only needed a return visit carries no SLA weight', () => {
  const [j] = evidenceJobs([score('WO-1', { first_fix: false })]);
  assert.deepEqual(j.missed, ['First fix']);
  assert.equal(j.weight, null, 'criticality weights SLA misses only');
});

test('a missing timestamp is not measured — neither a pass nor a miss', () => {
  const [j] = evidenceJobs([score('WO-1', { sla_completion_met: null, completion_hours: null })]);
  assert.deepEqual(j.completion, { target: '24h', actual: null, met: null });
  assert.deepEqual(j.missed, []);
});

test('jobs that missed something lead, then work-order order with the numbers read as numbers', () => {
  const jobs = evidenceJobs([score('WO-B-301-100'), score('WO-B-301-99'), score('WO-B-301-7', { recall: true })]);
  assert.deepEqual(jobs.map((j) => j.wo), ['WO-B-301-7', 'WO-B-301-99', 'WO-B-301-100']);
});

test('the vendor model counts each work order once — jobs, the L1/L2/L3 split and the L1 tile', () => {
  const l1 = score('WO-1', Object.assign({ criticality: 'L1' }, missResponse));
  const m = shape([l1, copyOf(l1), score('WO-2')]);
  assert.equal(m.V[VID].jobs.length, 2);
  assert.equal(m.V[VID].evidenceDupes, 1);
  assert.deepEqual(m.V[VID].crit, { L1: 1, L2: 1, L3: 0 });
  assert.equal(m.tiles.L1, 1, 'one L1 response miss, not two');
});

// ── the tab, as the reader sees it ──────────────────────────────────────────────────────

test('the tab counts work orders, not check rows', () => {
  const s = score('WO-1', missResponse);
  const v = open([s, copyOf(s), score('WO-2')]);
  assert.equal(v.vpTabs[2].n, '2');
  cleanup();
});

test('it opens on All with the misses leading, and every filter says how many it holds', () => {
  open([score('WO-3'), score('WO-1', missResponse), score('WO-4'), score('WO-2', { recall: true })]);
  const f = vp().evFilters;
  assert.equal(f.find((x) => x.active).key, 'all');
  assert.equal(f.find((x) => x.key === 'missed').n, '2');
  assert.equal(f.find((x) => x.key === 'all').n, '4');
  assert.equal(f.find((x) => x.key === 'response').n, '1');
  assert.equal(f.find((x) => x.key === 'recall').n, '1');
  assert.equal(f.find((x) => x.key === 'completion'), undefined, 'a check nobody missed is not offered as a filter');
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-1', 'WO-2', 'WO-3', 'WO-4']);
  vp().evFilters.find((x) => x.key === 'missed').click();
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-1', 'WO-2']);
  cleanup();
});

test('All shows every job, and a check filter shows only the jobs that missed it', () => {
  open([score('WO-1', missResponse), score('WO-2', { recall: true }), score('WO-3')]);
  vp().evFilters.find((x) => x.key === 'all').click();
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-1', 'WO-2', 'WO-3']);
  vp().evFilters.find((x) => x.key === 'recall').click();
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-2']);
  cleanup();
});

test('a month where nothing was missed opens on every job and says so', () => {
  open([score('WO-1'), score('WO-2')]);
  assert.equal(vp().evFilters.find((x) => x.active).key, 'all');
  assert.equal(vp().evRows.length, 2);
  assert.match(vp().evSummary, /every work order met every check/i);
  cleanup();
});

test('the summary names the month, the job count and how many missed', () => {
  open([score('WO-1', missResponse), score('WO-2'), score('WO-3')]);
  const s = vp().evSummary;
  assert.match(s, /3 work orders/);
  assert.match(s, /Sep 2026/);
  assert.match(s, /1 missed at least one check/);
  cleanup();
});

test('a long month is shown ten jobs a page, with numbered pages', () => {
  open(many(40, missResponse));
  assert.equal(vp().evRows.length, 10);
  assert.equal(vp().evPagerShow, 'flex');
  assert.equal(vp().evPageLabel, '1–10 of 40');
  assert.deepEqual(vp().evPages.map((p) => p.label), ['1', '2', '3', '4']);
  assert.equal(vp().evPages.find((p) => p.current).label, '1');
  assert.equal(vp().evPrevOn, false);
  assert.equal(vp().evNextOn, true);
  cleanup();
});

test('Next, Prev and a page number each move to that page, and the ends stop', () => {
  open(many(40, missResponse));
  vp().evNext();
  assert.equal(vp().evPageLabel, '11–20 of 40');
  assert.equal(vp().evRows[0].wo, 'WO-B-301-6010');
  vp().evPages.find((p) => p.label === '4').click();
  assert.equal(vp().evRows.length, 10);
  assert.equal(vp().evNextOn, false);
  vp().evNext();
  assert.equal(vp().evPageLabel, '31–40 of 40', 'Next on the last page stays put');
  vp().evPrev();
  assert.equal(vp().evPageLabel, '21–30 of 40');
  vp().evPrev();
  vp().evPrev();
  vp().evPrev();
  assert.equal(vp().evPageLabel, '1–10 of 40', 'Prev on the first page stays put');
  cleanup();
});

test('many pages collapse to the first, the last and the ones around the current page', () => {
  open(many(150, missResponse));                                     // 15 pages
  const labels = () => vp().evPages.map((p) => p.label);
  assert.deepEqual(labels(), ['1', '2', '3', '4', '5', '…', '15']);
  vp().evPages.find((p) => p.label === '5').click();
  vp().evNext();                                                     // page 6
  assert.deepEqual(labels(), ['1', '…', '5', '6', '7', '…', '15']);
  vp().evPages.find((p) => p.label === '15').click();
  assert.deepEqual(labels(), ['1', '…', '11', '12', '13', '14', '15']);
  assert.equal(vp().evPages.find((p) => p.label === '…').click, undefined, 'a gap is not a page');
  cleanup();
});

test('one page needs no pager', () => {
  open(many(10, missResponse));
  assert.equal(vp().evPagerShow, 'none');
  assert.equal(vp().evRows.length, 10);
  cleanup();
});

test('changing the filter starts again from the first page', () => {
  open(many(40, missResponse));
  vp().evNext();
  vp().evFilters.find((x) => x.key === 'missed').click();
  assert.equal(vp().evPageLabel, '1–10 of 40');
  cleanup();
});

test('a page past the end — the list shrank under it — shows the last page instead', () => {
  open(many(40, missResponse));
  vp().evPages.find((p) => p.label === '4').click();
  c.setState({ vpRaw: raw(many(15, missResponse)) });
  assert.equal(vp().evPageLabel, '11–15 of 15');
  cleanup();
});

test('turning the page closes the row that was open', () => {
  open(many(40, missResponse));
  vp().evRows[0].toggle();
  vp().evNext();
  vp().evPrev();
  assert.equal(vp().evRows[0].open, false);
  cleanup();
});

test('search finds a work order by code or asset, and starts again from the first page', () => {
  open(many(30, missResponse).concat([score('WO-X-1', Object.assign({ asset_name: 'Escalator E2' }, missResponse))]));
  vp().evNext();
  vp().evSetQuery({ target: { value: 'escalator' } });
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-X-1']);
  vp().evSetQuery({ target: { value: 'wo-b-301-6012' } });
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-B-301-6012']);
  vp().evSetQuery({ target: { value: '' } });
  assert.equal(vp().evPageLabel, '1–10 of 31', 'a new search is a new list, read from its top');
  cleanup();
});

test('a search that matches nothing says what was searched', () => {
  open([score('WO-1', missResponse)]);
  vp().evSetQuery({ target: { value: 'boiler' } });
  assert.equal(vp().evRows.length, 0);
  assert.equal(vp().evNoMatchShow, 'block');
  assert.match(vp().evNoMatch, /boiler/);
  cleanup();
});

test('the filter and search belong to the vendor they were set on', () => {
  open([score('WO-1', missResponse), score('WO-2'), score('WO-9', { vendor_id: OTHER, recall: true })]);
  vp().evFilters.find((x) => x.key === 'all').click();
  vp().evSetQuery({ target: { value: 'WO-2' } });
  c.setState({ vpVendor: OTHER });
  assert.equal(vp().evQuery, '');
  assert.equal(vp().evFilters.find((x) => x.active).key, 'all');
  assert.deepEqual(vp().evRows.map((r) => r.wo), ['WO-9']);
  cleanup();
});

test('a row opens to the job\'s timeline and closes again', () => {
  open([score('WO-1', missResponse)]);
  assert.equal(vp().evRows[0].open, false);
  vp().evRows[0].toggle();
  const r = vp().evRows[0];
  assert.equal(r.open, true);
  const text = r.detail.join(' ');
  assert.match(text, /Reported 12 Sep 2026 09:00/);
  assert.match(text, /attended 12:00/);
  assert.match(text, /5\.1h against 4h/);
  assert.match(text, /1\.5×/);
  vp().evRows[0].toggle();
  assert.equal(vp().evRows[0].open, false);
  cleanup();
});

test('each cell reads as a verdict: misses in red, passes in green, unmeasured in grey', () => {
  open([score('WO-1', Object.assign({ first_fix: false, recall: true, sla_completion_met: null, completion_hours: null }, missResponse))]);
  const r = vp().evRows[0];
  assert.deepEqual([r.response.value, r.response.of, r.response.fg], ['5.1h', '/ 4h', 'var(--st-risk)']);
  assert.deepEqual([r.completion.value, r.completion.fg], ['—', 'var(--color-neutral-500)']);
  assert.deepEqual([r.firstFix.value, r.firstFix.fg], ['Return visit', 'var(--st-risk)']);
  assert.deepEqual([r.recall.value, r.recall.fg], ['Recalled', 'var(--st-risk)']);
  assert.equal(r.weight, '×1.5');
  cleanup();
});

test('a passing job reads green on the SLA checks and shows no weight', () => {
  open([score('WO-1')]);
  const r = vp().evRows[0];
  assert.deepEqual([r.response.value, r.response.fg], ['3h', 'var(--st-ok)']);
  assert.deepEqual([r.firstFix.value, r.recall.value], ['First visit', 'None']);
  assert.equal(r.weight, '');
  cleanup();
});

test('one building across the month is named once, not on every row', () => {
  open([score('WO-1', missResponse), score('WO-2', missResponse)]);
  assert.equal(vp().evBuilding, 'Bishopsgate Tower');
  assert.doesNotMatch(vp().evRows[0].sub, /Bishopsgate/);
  cleanup();
});

test('jobs across two buildings carry their building on the row', () => {
  open([score('WO-1', missResponse), score('WO-2', Object.assign({ building_name: 'Harbour Point' }, missResponse))]);
  assert.equal(vp().evBuilding, '');
  assert.match(vp().evRows.find((r) => r.wo === 'WO-2').sub, /Harbour Point/);
  cleanup();
});

test('copies in the read are named, so a doubled count is never silent', () => {
  const s = score('WO-1', missResponse);
  open([s, copyOf(s), score('WO-2'), copyOf(score('WO-2'))]);
  assert.match(vp().evDupeNote, /2 work orders were scored more than once/);
  assert.match(vp().evDupeNote, /Rebuild scorecards/);
  open([score('WO-1')]);
  assert.equal(vp().evDupeNote, '');
  cleanup();
});
