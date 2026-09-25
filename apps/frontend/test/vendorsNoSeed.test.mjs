// The Vendors page shows what svc-operations-intelligence returned, and nothing else.
// There is no seed fallback behind it: with no read answered the directory is empty, every
// tile and stat reads "—", and the page says which service did not answer. With a read
// answered, a figure the response did not carry still reads "—" rather than a zero.
//
// Runs the real controller against the real renderVals, the way chat.test.mjs does.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const A1 = '00000000-0000-0000-0000-0000000000a1';
const GK = 'e56473ed-2592-4512-a1b4-e7d92ac40842';
const WEIGHTS = { sla_response_pct: 25, sla_completion_pct: 25, first_fix_pct: 20, recall_pct: 15, accreditation_pct: 15, blocked_score_cap: 60 };

// One scored vendor and one with a contract but no card — the second is the case a seed
// would have filled in and a zero would have mis-stated.
const raw = () => ({
  fetchedAt: '2026-09-07T14:00:00Z',
  errors: {},
  summary: {
    ok: true,
    pending_approvals: 2,
    weights: WEIGHTS,
    scorecards: [{
      id: 'c1', vendor_id: A1, vendor_name: 'Apex Mechanical Services Ltd', score_month: '2026-08-01',
      overall_score: 46.58, trend_delta: -38.73,
      component_breakdown: {
        weights_snapshot: WEIGHTS, invoice_match_signal: 65,
        sla_response: 25, sla_completion: 8.33, first_fix: 13.33, recall: 15, accreditation: 15,
        wo_avg_before_invoice_blend: 43.33
      },
      ppm_compliance_pct: null, matched_flagged_ratio: 0.65, block_capped: false, wo_score_ids: ['w1', 'w2', 'w3']
    }]
  },
  contracts: { ok: true, parameters: [{ id: 'p1', vendor_id: GK, vendor_name: 'Gough and Kelly Ltd.', contract_ref: 'UKRI-2938', payment_terms: '30', field_sources: { payment_terms: 'contract' } }] },
  weights: { ok: true, weights: WEIGHTS },
  approvals: { ok: true, count: 0, items: [] },
  certificates: [],
  coverage: {}, packs: {}
});

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'vp' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._vpRefresh); };

test('nothing answered: no vendors, no counts, and the page says which service is missing', () => {
  const v = c.renderVals();
  assert.deepEqual(v.vpList, []);                       // no seed directory behind the read
  assert.equal(v.vpEmptyShow, 'block');
  assert.equal(v.vpBodyShow, 'none');
  assert.match(v.vpEmptyNote, /svc-operations-intelligence/);
  assert.match(v.vpSourceLabel, /No data|Reading/);
  v.vpTiles.forEach((t) => assert.equal(t.value, '—', t.label + ' is not asserted'));
  v.vpStats.forEach((st) => {
    assert.equal(st.value, '—', st.label + ' is not asserted');
    st.rows.forEach((r) => assert.equal(r.n, '—', st.label + ' · ' + r.label + ' is not asserted'));
  });
  assert.equal(v.vpLastRebuild, '—');                   // no read returns when the cards were cut
  assert.equal(v.vpTabs[0].n, '0');                     // not the five components of a card that is not there
  cleanup();
});

test('a read answered: the engine\'s figures are shown, and what it did not send stays a dash', () => {
  c.setState({ vpRaw: raw(), vpLoadedAt: '2026-09-07T14:00:00Z' });
  const v = c.renderVals();
  assert.equal(v.vpList.length, 2);                     // scored vendor + contracted vendor, no one else
  assert.match(v.vpSourceLabel, /^Live/);

  const apex = v.vpList.find((r) => /Apex/.test(r.name));
  assert.equal(apex.score, '47');                       // the published score, as published
  const gough = v.vpList.find((r) => /Gough/.test(r.name));
  assert.equal(gough.score, '—');                       // a contract but no card is unscored, not zero
  assert.equal(gough.cov, '—');                         // no coverage row and no accreditation on record
  assert.equal(gough.covFrac, '—');

  const avg = v.vpStats.find((s) => s.label === 'Avg score');
  assert.equal(avg.value, '47');                        // the unscored vendor is not averaged in as a 0
  assert.equal(avg.rows.find((r) => r.label === 'Below 70').n, '1');

  const orders = v.vpStats.find((s) => s.label === 'Commercial orders');
  assert.equal(orders.value, '—');                                                  // no read lists invoice lines
  assert.equal(orders.rows.find((r) => r.label === 'Approved as charged').n, '—');
  assert.equal(orders.rows.find((r) => r.label === 'Work orders open').n, '—');
  assert.equal(v.vpTiles.find((t) => t.label === 'L1 breaches').value, '—');         // no read lists breaches
  assert.equal(v.vpLastRebuild, '—');
  cleanup();
});

test('the tabs behind a live vendor count their own rows, and an empty tab offers no total to claim', () => {
  c.setState({ vpRaw: raw(), vpVendor: A1 });
  const v = c.renderVals();
  assert.equal(v.vpTabs[0].n, '5');                     // five components on the card
  assert.equal(v.vpTabs[2].n, '0');                     // evidence: the wo-scores read is absent here
  assert.equal(v.vp.creditTotal, '—');                  // so there is no recoverable total
  assert.equal(v.vp.claimShow, 'none');                 // and nothing to claim
  assert.equal(v.vp.invTotal, '—');
  assert.equal(v.vp.invActionsShow, 'none');
  assert.match(v.vp.breachEmpty, /could not be read/);  // absent read, said as such
  cleanup();
});

test('a vendor with a contract and no card shows dashes down the scorecard, not zeros', () => {
  c.setState({ vpRaw: raw(), vpVendor: GK });
  const v = c.renderVals();
  assert.equal(v.vp.score, '—');
  assert.equal(v.vp.finalScore, '—');
  assert.equal(v.vp.totalPts, '—');                     // "0 of 100 pts" would be a scorecard
  v.vp.metrics.forEach((m) => {
    assert.equal(m.pts, '—', m.label + ' was never scored');
    assert.equal(m.measured, '—', m.label + ' was never measured');
  });
  assert.match(v.vp.covNote, /No mandatory accreditation is on record/);
  cleanup();
});

test('the Evidence tab shows the scored work orders from GET /wo-scores, misses in red, met in green', () => {
  const r = raw();
  const month = r.summary.scorecards.find((x) => x.vendor_id === A1).score_month;
  r.woScores = { ok: true, wo_scores: [{
    id: 's1', vendor_id: A1, wo_code: 'WO-B101-0007', score_month: month, priority: 'P1',
    asset_name: 'AHU-01', building_name: 'Harbour Point', criticality: 'L1',
    sla_response_met: false, sla_completion_met: true, response_hours: 6.5, response_target_hours: 4,
    completion_hours: 20, completion_target_hours: 24, first_fix: true, recall: false, contract_parameters_id: 'cp'
  }] };
  c.setState({ vpRaw: r, vpVendor: A1, vpTab: 2 });
  const v = c.renderVals();
  assert.equal(v.vpTabs[2].n, '2');
  const [miss, met] = v.vp.breaches;
  assert.deepEqual([miss.wo, miss.asset, miss.building, miss.metric, miss.target, miss.actual, miss.mult],
    ['WO-B101-0007', 'AHU-01', 'Harbour Point', 'Response', '4h', '6.5h', '3×']);
  assert.equal(miss.actualFg, 'var(--st-risk)');
  assert.equal(met.actualFg, 'var(--st-ok)');
  assert.equal(v.vp.breachEmptyShow, 'none');
  assert.equal(v.vp.creditTotal, '—');                  // nothing priced, so no total and no claim
  assert.equal(v.vp.claimShow, 'none');
  assert.equal(v.vpTiles.find((t) => t.label === 'L1 breaches').value, '1');
  cleanup();
});
