// shapeLiveVendors — the Vendors page shaped from svc-operations-intelligence reads.
// Fixtures are trimmed copies of real responses captured from the running service
// (07 Sep 2026): contract-performance scorecards / contracts / weights / approvals,
// and the compliance register's vendor certificates and per-vendor coverage.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveVendors, trendOf, evidenceRows } from '../src/logic/vendorsLive.js';

const NOW = new Date(2026, 8, 7, 14, 0, 0);

const A1 = '00000000-0000-0000-0000-0000000000a1';
const A3 = '00000000-0000-0000-0000-0000000000a3';
const GK = 'e56473ed-2592-4512-a1b4-e7d92ac40842';

const WEIGHTS = { sla_response_pct: 25, sla_completion_pct: 25, first_fix_pct: 20, recall_pct: 15, accreditation_pct: 15, blocked_score_cap: 60 };

const card = (vendor_id, vendor_name, month, overall, delta, breakdown, extra) => Object.assign({
  id: vendor_id + '-' + month, vendor_id, vendor_name, score_month: month, overall_score: overall, trend_delta: delta,
  component_breakdown: Object.assign({ weights_snapshot: WEIGHTS, invoice_match_signal: 65, exclusions: [] }, breakdown),
  ppm_compliance_pct: null, matched_flagged_ratio: 0.65, block_capped: false, wo_score_ids: ['w1', 'w2', 'w3']
}, extra || {});

const summary = {
  ok: true,
  scorecards: [
    card(A1, 'Apex Mechanical Services Ltd', '2026-08-01', 46.58, -38.73,
      { recall: 15, first_fix: 13.33, sla_response: 25, accreditation: 15, sla_completion: 8.33, parameter_source: 'Contract-sourced',
        contract_confirmed: true, contract_parameters_id: 'fa65bf58-gone', wo_avg_before_invoice_blend: 43.33 }),
    card(A3, 'SafeLift Engineering Ltd', '2026-08-01', 20.38, -21.03,
      { recall: 0, first_fix: 0, sla_response: 25, accreditation: 0, sla_completion: 0, parameter_source: 'Contract-sourced', wo_avg_before_invoice_blend: 12.5 },
      { wo_score_ids: ['w1', 'w2'] }),
    card(A1, 'Apex Mechanical Services Ltd', '2026-07-01', 85.61, 8.79,
      { recall: 14.78, first_fix: 17.31, sla_response: 23.51, accreditation: 15, sla_completion: 22.76, wo_avg_before_invoice_blend: 89.25 },
      { ppm_compliance_pct: 100, wo_score_ids: Array.from({ length: 67 }, (_, i) => 'j' + i) }),
    card(A3, 'SafeLift Engineering Ltd', '2026-07-01', 40.87, -6.42,
      { recall: 0, first_fix: 5, sla_response: 20, accreditation: 0, sla_completion: 10, wo_avg_before_invoice_blend: 35 },
      { block_capped: true }),
    card(GK, 'Gough and Kelly Ltd.', '2024-01-01', 73.53, -12.22,
      { recall: 15, first_fix: 18, sla_response: 20, accreditation: 15, sla_completion: 10, parameter_source: 'Contract-sourced', wo_avg_before_invoice_blend: 78 },
      { wo_score_ids: Array.from({ length: 9 }, (_, i) => 'g' + i) })
  ],
  weights: Object.assign({ id: 'w', organization_id: null }, WEIGHTS),
  pending_approvals: 25,
  approvals: [],
  unapproved_criticalities: [{ id: 'c1' }, { id: 'c2' }],
  kpis: { scorecards_count: 29, avg_overall: 69.42, pending_approvals: 25, unapproved_asset_criticalities: 2 }
};

const contracts = {
  ok: true, count: 1,
  parameters: [{
    id: 'd723efa2', vendor_id: GK, vendor_name: 'Gough and Kelly Ltd.', document_id: 'doc1', document_name: 'UKRI_2938_Framework.pdf',
    contract_ref: 'UKRI-2938', signed_date: '2023-06-28', status: 'draft',
    sla_response_p1_hours: 1, sla_response_p2_hours: 4, sla_response_p3_hours: 24, sla_response_p4_hours: 72,
    sla_completion_p1_hours: 4, sla_completion_p2_hours: 24, sla_completion_p3_hours: 72, sla_completion_p4_hours: 168,
    labour_day_rate: 350, labour_hour_rate: null, overtime_rate: 525, call_out_rate: 150, parts_pricing_json: {}, payment_terms: '30',
    kpi_clauses_json: { kpi_1: {}, kpi_2: {}, kpi_6: {}, kpi_7: {} }, ppm_obligations_json: { guaranteed: true }, task_criticality_json: { P1: 'x', P2: 'y', P3: 'z', P4: 'w' },
    defaults_used: ['sla_response_p1_hours: Default — not contract-sourced'],
    field_sources: {
      contract_ref: 'contract', call_out_rate: 'default', overtime_rate: 'default', payment_terms: 'contract', labour_day_rate: 'default',
      kpi_clauses_json: 'contract', parts_pricing_json: 'default', ppm_obligations_json: 'contract',
      sla_response_p1_hours: 'default', sla_response_p2_hours: 'default', sla_response_p3_hours: 'default', sla_response_p4_hours: 'default',
      task_criticality_json: 'contract',
      sla_completion_p1_hours: 'default', sla_completion_p2_hours: 'default', sla_completion_p3_hours: 'default', sla_completion_p4_hours: 'default'
    }
  }]
};

const flag = (line_id, wo_code, delta, discrepancy, checks, item_type) => ({
  id: 'ap-' + line_id, source_feature: 'B', item_type: item_type || 'invoice_flag', severity: item_type ? 'high' : 'medium', status: 'pending',
  summary: 'Invoice 8495_B3_invoice_UKRI-2938_Sept-2023 line flagged £' + delta,
  payload: { line: { checks: checks || { wo_exists: true, labour_rate: 62, contracted_hourly: 43.75, attendance_hours: 3.02 }, status: 'flagged', line_id, wo_code, decision: null, delta_gbp: delta, discrepancy },
    invoice_ref: '84957388-6d79-42f8-b537-5ea7ebbcc5a7_B3_invoice_UKRI-2938_Sept-2023' },
  created_at: '2026-08-31T11:16:33+00:00'
});
const approvals = {
  ok: true, count: 5,
  items: [
    flag(2, 'WO-UKRI-2938-202309-0159', 55.12, 'Labour rate £62.00/h exceeds contracted £43.75/h; ≈£55.12.'),
    flag(21, 'WO-UKRI-2938-202309-9001', 186.0, 'No ingested completed work order found for WO-UKRI-2938-202309-9001.', { wo_exists: false }),
    flag(23, 'WO-UKRI-2938-202309-0131', 1644.46, 'Labour hours 12.71 vs attendance 0.71 (>10% tolerance); ≈£525.00.', undefined, 'invoice_flag_high_value'),
    { id: 'ap-cp', source_feature: 'B', item_type: 'contract_params_review', severity: 'medium', status: 'pending',
      summary: 'Review contract SLA parameters — SPELL-TEST-1', payload: { defaults_used: [] }, created_at: '2026-08-31T11:50:20+00:00' },
    { id: 'ap-tie', source_feature: 'B', item_type: 'overlapping_contracts_tie', severity: 'high', status: 'pending',
      summary: 'Two confirmed contracts for vendor share the same signed_date', payload: {}, created_at: '2026-08-31T11:50:20+00:00' }
  ]
};

const cert = (vendor_id, vendor_name, code, name, status, expiry, days, trade, extra) => Object.assign({
  id: vendor_id.slice(-2) + '-' + code, cert_scope: 'Vendor', vendor_id, vendor_name, certificate_type_code: code, certificate_type_name: name,
  status, expiry_date: expiry, days_to_expiry: days, issuer: 'Issuer', country_code: 'UK', trade_category: trade, raw_metadata: {}
}, extra || {});
const certificates = [
  cert(A1, 'Apex Mechanical Services Ltd', 'GAS_SAFE', 'Gas Safe Registration Certificate (Company)', 'Lapsed', '2026-08-31', -7, 'Gas',
    { issuer: 'Gas Safe Register', raw_metadata: { verification: { status: 'needs_partner' } }, vendor_block_state: 'Blocked' }),
  cert(A1, 'Apex Mechanical Services Ltd', 'PUBLIC_LIABILITY', 'Public Liability Insurance', 'Due for Renewal', '2026-10-31', 54, null, { issuer: 'Zurich UK' }),
  cert(A1, 'Apex Mechanical Services Ltd', 'SAFE_CONTRACTOR', 'SafeContractor', 'Current', '2027-01-09', 124, null, { issuer: 'Alcumus' }),
  cert(A3, 'SafeLift Engineering Ltd', 'LEIA', 'LEIA Membership', 'Overdue', '2026-08-01', -37, 'LOLER'),
  cert(A3, 'SafeLift Engineering Ltd', 'CONTRACTOR_PL_INSURANCE', 'Public Liability Insurance', 'Current', '2027-03-01', 175, null)
];
const coverage = {
  UK: [
    { vendor_id: A1, vendor_name: 'Apex Mechanical Services Ltd', block_state: 'Blocked', blocked_accreditation_type: 'Gas Safe Registration Certificate (Company)',
      required: 2, on_record: 1, gaps: ['ACS_CARD'], coverage_pct: 50, certificates_total: 3, cleared: false },
    { vendor_id: A3, vendor_name: 'SafeLift Engineering Ltd', block_state: 'Clear', blocked_accreditation_type: null,
      required: 3, on_record: 2, gaps: ['CHAS_SSIP'], coverage_pct: 66.7, certificates_total: 2, cleared: true },
    { vendor_id: 'other', vendor_name: 'C&H Fire Protection Ltd', block_state: 'Clear', required: 4, on_record: 2, gaps: ['BAFE_SP203_1', 'NSI_GOLD_FIRE'], coverage_pct: 50 }
  ]
};
const packs = {
  UK: [
    { certificate_type_code: 'ACS_CARD', certificate_type_name: 'ACS Gas Competency Card', certificate_scope: 'Vendor' },
    { certificate_type_code: 'GAS_SAFE', certificate_type_name: 'Gas Safe Registration Certificate (Company)', certificate_scope: 'Vendor' },
    { certificate_type_code: 'CHAS_SSIP', certificate_type_name: 'CHAS / SSIP Accreditation', certificate_scope: 'Vendor' },
    { certificate_type_code: 'LEIA', certificate_type_name: 'LEIA Membership', certificate_scope: 'Vendor' },
    { certificate_type_code: 'EICR', certificate_type_name: 'Electrical Installation Condition Report', certificate_scope: 'Building' }
  ]
};

const full = () => shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage, packs }, NOW);

test('with nothing loaded, nothing is asserted', () => {
  const m = shapeLiveVendors(null, NOW);
  assert.equal(m.live, false);
  assert.deepEqual(m.vendors, []);
  assert.deepEqual(m.V, {});
  assert.equal(m.month, null);
});

test('the trend word is improving / declining / stable, from the card delta', () => {
  assert.equal(trendOf(8.79), 'improving');
  assert.equal(trendOf(-38.73), 'declining');
  assert.equal(trendOf(0.4), 'stable');
  assert.equal(trendOf(null), 'first month');
});

test('directory is the union of scored and contracted vendors, one row each, newest card first', () => {
  const m = full();
  assert.equal(m.live, true);
  assert.deepEqual(m.vendors.map((v) => v.id), [A3, A1, GK]); // Aug cards first, lowest score first; Gough (Jan 2024) last
  const apex = m.vendors.find((v) => v.id === A1);
  assert.equal(apex.name, 'Apex Mechanical Services Ltd');
  assert.equal(apex.score, 47);       // 46.58 rounded — the published score, never recomputed
  assert.equal(apex.trend, 'declining');
  assert.equal(apex.spend, null);     // no spend ledger in any backend
  assert.equal(m.month, 'Aug 2026');
});

test('the directory coverage column is the engine\'s CountryPack figure when it has one', () => {
  const m = full();
  const apex = m.vendors.find((v) => v.id === A1);
  assert.equal(apex.cov, 50);
  assert.equal(apex.covOn, 1);
  assert.equal(apex.covReq, 2);
  const gk = m.vendors.find((v) => v.id === GK);
  assert.equal(gk.cov, undefined);         // no coverage row → renderVals derives it from the certificates
  assert.match(gk.meta, /no accreditation on record/);
  assert.match(m.vendors.find((v) => v.id === A3).meta, /lapsed accreditation · Aug 2026 card/);
});

test('a vendor returned under several countries takes the coverage row for the country its own accreditations are in', () => {
  // Apex's certificates are UK; the US pack reports the same vendor as fully covered against US types.
  const cov2 = { UK: coverage.UK, US: [
    { vendor_id: A1, vendor_name: 'Apex Mechanical Services Ltd', block_state: 'Blocked', blocked_accreditation_type: 'Gas Safe Registration Certificate (Company)', required: 3, on_record: 3, gaps: [], coverage_pct: 100 }
  ] };
  const m = shapeLiveVendors({ summary, contracts, approvals, certificates, coverage: cov2, packs }, NOW);
  const apex = m.vendors.find((v) => v.id === A1);
  assert.equal(apex.cov, 50);
  assert.equal(apex.covReq, 2);
  assert.ok(m.V[A1].certs.some((c) => c.name === 'ACS Gas Competency Card'));   // the UK gap is still shown
  // A vendor with no certificates at all falls back to the first country that reports it.
  const m2 = shapeLiveVendors({ summary, contracts, approvals, certificates: [], coverage: { US: cov2.US, UK: coverage.UK }, packs }, NOW);
  assert.equal(m2.vendors.find((v) => v.id === A1).cov, 100);
});

test('bare type codes are named for people and the upload id is stripped off document names', () => {
  const m = shapeLiveVendors({
    summary, contracts: { parameters: [Object.assign({}, contracts.parameters[0], { document_name: '84957388-6d79-42f8-b537-5ea7ebbcc5a7_UKRI-2938_FM_Contract.pdf' })] },
    certificates: [cert(A1, 'Apex Mechanical Services Ltd', 'PUBLIC_LIABILITY', 'PUBLIC_LIABILITY', 'Current', '2027-01-01', 100, null)],
    coverage: { UK: [{ vendor_id: A1, vendor_name: 'Apex Mechanical Services Ltd', block_state: 'Blocked', blocked_accreditation_type: 'PUBLIC_LIABILITY', required: 1, on_record: 1, gaps: [], coverage_pct: 100 }] },
    packs
  }, NOW);
  const pl = m.V[A1].certs[0];
  assert.equal(pl.name, 'Public Liability');
  assert.equal(pl.req, 'Mandatory');     // it is the type the engine blocked on
  assert.equal(m.V[GK].contract.doc, 'UKRI-2938_FM_Contract.pdf');
  assert.match(m.V[GK].contract.line, / from UKRI-2938_FM_Contract\.pdf$/);
});

test('a coverage block is a mandatory lapse: accreditation Lapsed, blocked, ceiling note', () => {
  const m = full();
  const apex = m.vendors.find((v) => v.id === A1);
  assert.equal(apex.accred, 'Lapsed');
  assert.equal(apex.blocked, true);
  // SafeLift is Clear now but its July card was capped; the August card was not.
  const sl = m.vendors.find((v) => v.id === A3);
  assert.equal(sl.blocked, false);
  assert.equal(sl.accred, 'Lapsed');  // LEIA overdue — lapsed accreditation on file, not a block
  assert.equal(m.V[A3].capApplied, false);
  assert.equal(m.V[A1].capApplied, false);
});

test('scorecard rows are the backend components against the weights snapshot', () => {
  const R = full().V[A1];
  assert.equal(R.rows.length, 5);
  const ff = R.rows.find((r) => r.k === 'first_fix');
  assert.equal(ff.label, 'First-time fix');
  assert.equal(ff.w, 20);
  assert.equal(ff.pts, 13.3);
  assert.equal(ff.measured, 67);      // 13.33 / 20
  assert.match(ff.sample, /3 work orders/);
  assert.equal(R.rows.find((r) => r.k === 'accreditation').label, 'Accreditation');
  assert.equal(R.raw, 43.33);         // weighted total before the invoice blend
  assert.equal(R.score, 47);
  assert.match(R.critNote, /0\.85/);  // the blend is disclosed where the total and the published score differ
  assert.match(R.critNote, /65%/);
});

test('contract terms come from the parameter set with their field sources', () => {
  const R = full().V[GK];
  assert.equal(R.contract.ref, 'UKRI-2938');
  assert.equal(R.contract.signed, '28 Jun 2023');
  assert.equal(R.contract.status, 'draft');
  assert.equal(R.contract.fields, 17);   // every field the extractor sources
  assert.equal(R.contract.read, 5);      // contract_ref, payment_terms, kpi, ppm, task criticality
  assert.match(R.contract.line, /UKRI-2938/);
  assert.match(R.contract.line, /5 of 17 terms read/);
  assert.match(R.contract.line, /UKRI_2938_Framework\.pdf/);
  const p1 = R.terms.find((t) => t.label === 'P1 response');
  assert.equal(p1.value, '1 hour');
  assert.equal(p1.src, 'default');
  const pay = R.terms.find((t) => t.label === 'Payment terms');
  assert.equal(pay.value, '30 days');
  assert.equal(pay.src, 'contract');
  assert.equal(R.terms.find((t) => t.label === 'Labour rate — day').value, '£350 / day');
  assert.equal(R.terms.find((t) => t.label === 'KPI clauses').value, '4 clauses');
  const sla = R.rows.find((r) => r.k === 'sla_response');
  assert.match(sla.requires, /P1 1h/);
  assert.match(sla.requires, /P2 4h/);
});

test('a vendor whose parameter row is gone shows no terms and says so', () => {
  const R = full().V[A1];
  assert.equal(R.terms.length, 0);
  assert.equal(R.contract.read, 0);
  assert.equal(R.contract.fields, 0);
  assert.match(R.contract.line, /not on record/i);
  assert.match(R.rows.find((r) => r.k === 'sla_response').srcTag, /contract-sourced/i);
});

test('coverage lists what is on file and what the pack still expects', () => {
  const R = full().V[A1];
  const names = R.certs.map((c) => c.name);
  assert.ok(names.indexOf('Gas Safe Registration Certificate (Company)') > -1);
  assert.ok(names.indexOf('ACS Gas Competency Card') > -1);           // the gap, named from the pack
  const gap = R.certs.find((c) => c.name === 'ACS Gas Competency Card');
  assert.equal(gap.status, 'Not on record');
  assert.equal(gap.req, 'Mandatory');
  assert.equal(gap.exp, '—');
  const pl = R.certs.find((c) => c.name === 'Public Liability Insurance');
  assert.equal(pl.status, 'Expiring');                                   // Due for Renewal → the tab's vocabulary
  assert.equal(pl.exp, '31 Oct 2026');
  assert.equal(pl.req, 'Preferred');                                     // not a UK vendor-pack type
  const gs = R.certs.find((c) => c.name === 'Gas Safe Registration Certificate (Company)');
  assert.equal(gs.status, 'Lapsed');
  assert.equal(gs.req, 'Mandatory');
  assert.match(gs.ver, /needs partner/);
  assert.equal(full().V[A3].certs.find((c) => c.name === 'LEIA Membership').status, 'Lapsed'); // Overdue → Lapsed
});

test('invoice lines held are the flagged lines in the approvals queue, matched to the vendor by contract reference', () => {
  const m = full();
  const R = m.V[GK];
  assert.equal(R.invoices.length, 3);
  const l2 = R.invoices.find((i) => /0159/.test(i.line));
  assert.equal(l2.status, 'Held');
  assert.equal(l2.charged, '£62.00/h');
  assert.equal(l2.should, '£43.75/h');
  // To the penny. This asserted '+£55' while the value was 55.12, which is what the UI
  // showed: a credit note is raised for this figure and reconciled against the invoice line
  // it came from, so a rounded one does not tie out. £27.55 rendering as £28 on 23 Sep 2026
  // is what prompted the change.
  assert.equal(l2.delta, '+£55.12');
  assert.equal(l2.period, 'Sep 2023');
  assert.match(l2.flag, /exceeds contracted/);
  const l21 = R.invoices.find((i) => /9001/.test(i.line));
  assert.equal(l21.charged, '£186');
  assert.equal(l21.should, 'no work order');
  assert.equal(R.invoices.find((i) => /0131/.test(i.line)).status, 'Disputed'); // high-value, adversary-checked
  assert.deepEqual(m.V[A1].invoices, []);
});

test('tiles: blocked vendors, pending queue, critical items, held lines, terms on default; L1 breaches unsourced', () => {
  const t = full().tiles;
  assert.equal(t.blocked, 1);
  assert.equal(t.pending, 25);
  assert.equal(t.critical, 2);      // high-severity Feature B items
  assert.equal(t.held, 3);
  assert.equal(t.defaults, 12);     // 17 − 5 on the one parameter set
  assert.equal(t.L1, null);
});

test('counts the stats read: contracts, terms, and what has no source', () => {
  const c = full().counts;
  assert.equal(c.contracts, 1);
  assert.equal(c.termsRead, 5);
  assert.equal(c.termsDefault, 12);
  assert.equal(c.expiring, null);
  assert.equal(c.approvedLines, null);
  assert.equal(c.workordersOpen, null);
});

test('packages come from the trade on the vendor\'s accreditations', () => {
  const m = full();
  assert.equal(m.pkgOf(A1), 'Gas');
  assert.equal(m.pkgOf(A3), 'LOLER');
  assert.equal(m.pkgOf(GK), 'Unclassified');
});

test('the scorecards list is the fallback when the summary did not answer', () => {
  const m = shapeLiveVendors({ scorecards: { scorecards: summary.scorecards }, weights: { weights: summary.weights } }, NOW);
  assert.equal(m.live, true);
  assert.equal(m.vendors.length, 3);
  assert.equal(m.tiles.pending, null);
  assert.equal(m.V[A1].rows.find((r) => r.k === 'recall').w, 15);
});

// ── the SLA badge describes the contract on screen, not the one the card was scored against ──
//
// Production, 17 Sep 2026: Gough and Kelly's SLA rows carried "contract-sourced · UKRI-2938"
// while the UKRI-2938 row itself said `default` for every SLA field. The badge was read from
// the January 2024 scorecard's `parameter_source`, recorded when a DIFFERENT, since-deleted
// contract (ae5ad29f-…) was confirmed. The card stitched a 2024 provenance onto 2026 default
// hours — true of a contract that is gone, false of the one the reader is looking at.
//
// Rule: when a current contract row is on screen, the badge describes THAT row. The scorecard's
// recorded source is a fallback for when there is no row at all (A1's case, pinned above).

test('a current contract whose SLA terms are defaults is badged default, whatever an old card recorded', () => {
  const R = full().V[GK];
  const sla = R.rows.find((r) => r.k === 'sla_response');
  assert.match(sla.srcTag, /^default · UKRI-2938/i, 'the row on screen says default; the badge must too');
  assert.doesNotMatch(sla.srcTag, /contract-sourced/i, 'that label belonged to a contract that no longer exists');
  assert.match(R.rows.find((r) => r.k === 'sla_completion').srcTag, /^default · UKRI-2938/i);
});

test('a current contract whose SLA terms were read from the document is badged as the contract', () => {
  // The same fixture, with GK's response hours now read from the document. The card still says
  // "Contract-sourced" from 2024 — irrelevant: the row on screen decides.
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => p.vendor_id !== GK ? p : Object.assign({}, p, {
      field_sources: Object.assign({}, p.field_sources, {
        sla_response_p1_hours: 'contract', sla_response_p2_hours: 'contract',
        sla_response_p3_hours: 'contract', sla_response_p4_hours: 'contract'
      })
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage, packs }, NOW).V[GK];
  assert.match(R.rows.find((r) => r.k === 'sla_response').srcTag, /^contract · UKRI-2938/i);
  assert.match(R.rows.find((r) => r.k === 'sla_completion').srcTag, /^default · UKRI-2938/i,
    'each SLA family is judged on its own fields');
});

// ── what the Contract terms panel needs in order to write, not just read ──────────────
// The panel gained a Confirm button and per-term editing. Both address the parameter SET by
// id and a term by its COLUMN NAME, neither of which the view model used to carry: the
// panel knew a row was called "P1 response" and had no idea it was sla_response_p1_hours.

test('the contract carries the id the confirm and patch routes address', () => {
  const R = shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.id, 'd723efa2',
    'without this the panel can only describe the contract, never act on it');
});

test('a vendor with no contract has no id to act on, and says so rather than sending null', () => {
  const R = shapeLiveVendors({ summary, contracts: { ok: true, count: 0, parameters: [] },
    weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[A1];
  assert.equal(R.contract.id, null);
});

test('every term names the column it would be patched into', () => {
  const R = shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.terms.find((t) => t.label === 'P1 response').field, 'sla_response_p1_hours');
  assert.equal(R.terms.find((t) => t.label === 'Labour rate — day').field, 'labour_day_rate');
  assert.equal(R.terms.find((t) => t.label === 'Payment terms').field, 'payment_terms');
  assert.ok(R.terms.every((t) => typeof t.field === 'string' && t.field.length),
    'a row with no field is a row the editor cannot save');
});

test('structured terms are marked not editable, scalar ones are', () => {
  const R = shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  // KPI clauses, PPM obligations, parts pricing and the criticality ladder are objects.
  // A single-line box would flatten them, so the panel must not offer one.
  assert.equal(R.terms.find((t) => t.label === 'KPI clauses').editable, false);
  assert.equal(R.terms.find((t) => t.label === 'PPM obligations').editable, false);
  assert.equal(R.terms.find((t) => t.label === 'Task criticality').editable, false);
  assert.equal(R.terms.find((t) => t.label === 'P1 response').editable, true);
  assert.equal(R.terms.find((t) => t.label === 'Labour rate — day').editable, true);
});

test('the contract counts its platform defaults, because that is what confirming makes binding', () => {
  const R = shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  // 17 terms, 5 read from the document — so 12 are platform defaults, and confirming turns
  // all 12 into agreed values. The confirmation step has to be able to say the number.
  assert.equal(R.contract.defaults, 12);
  assert.equal(R.contract.defaults, R.contract.fields - R.contract.read);
});

test('a confirmed contract is marked confirmed so the panel can refuse to re-confirm it', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, { status: 'confirmed' }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.confirmed, true);
  assert.equal(R.contract.status, 'confirmed');
});

// ── (1) who confirmed, (2) a contract that read nothing, (3) counting a clause ──────────

test('a confirmed contract carries the person and the date, not just the flag', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      status: 'confirmed',
      confirmed_by: '00000000-0000-0000-0001-000000000017',
      confirmed_by_name: 'Aasim Shaik',
      confirmed_at: '2026-09-18T07:01:51+00:00'
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.confirmedBy, 'Aasim Shaik',
    'CONFIRMED with nobody attached is not a record of a decision');
  assert.equal(R.contract.confirmedOn, '18 Sep 2026');
});

test('an unresolvable confirmer leaves the name empty rather than showing a uuid', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      status: 'confirmed', confirmed_by: '00000000-0000-0000-0001-000000000017',
      confirmed_by_name: null, confirmed_at: '2026-09-18T07:01:51+00:00'
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.confirmedBy, null, 'a uuid on screen reads as data and answers nothing');
  assert.equal(R.contract.confirmedOn, '18 Sep 2026', 'the date is still worth showing');
});

test('a contract that read nothing at all is flagged as such, not shown as a table of defaults', () => {
  // Moreland: a property management agreement ingested as a service contract. Every term
  // defaulted, and the panel presented it identically to a contract with a few gaps.
  const empty = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      field_sources: Object.keys(p.field_sources || {}).reduce((a, k) => { a[k] = 'default'; return a; }, {})
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: empty, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.read, 0);
  assert.equal(R.contract.readNothing, true);
});

test('a contract with a few gaps is not flagged as having read nothing', () => {
  const R = shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.contract.read, 5);
  assert.equal(R.contract.readNothing, false);
});

test('a clause count counts clauses, not the sentence saying there are none', () => {
  // WKU's stored value, verbatim: two keys, both prose about KPIs being "monitored", and the
  // note itself says no targets are stated. "2 clauses · document" claimed the contract
  // supplied KPI terms when it supplied an aspiration.
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      kpi_clauses_json: {
        note: 'No specific KPI targets or metrics are stated in the contract.',
        description: "Key Performance Indicators (KPI's) are monitored to ensure that the delivery of maintenance services meets desired standards."
      }
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.terms.find((t) => t.label === 'KPI clauses').value, 'mentioned, no targets');
});

test('the heuristic extractor’s placeholder is not counted as clauses either', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      kpi_clauses_json: { detected: true, raw_snippet: 'KPI/penalty language present — PM to confirm' }
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.terms.find((t) => t.label === 'KPI clauses').value, 'mentioned, no targets');
});

test('real clauses are still counted as clauses', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      kpi_clauses_json: { first_fix: '85% minimum', service_credit: '2% per breach', note: 'from schedule 4' }
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.terms.find((t) => t.label === 'KPI clauses').value, '2 clauses',
    'the note beside two real clauses is not a third clause');
});

test('PPM obligations that are only a mention do not read as "stated"', () => {
  const patched = Object.assign({}, contracts, {
    parameters: contracts.parameters.map((p) => Object.assign({}, p, {
      ppm_obligations_json: { detected: true, note: 'PPM obligations mentioned — PM to confirm schedule' }
    }))
  });
  const R = shapeLiveVendors({ summary, contracts: patched, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];
  assert.equal(R.terms.find((t) => t.label === 'PPM obligations').value, 'mentioned, no detail');
});

// ── the labour rate card a contract states, per trade ────────────────────────────────────
// WKU prices twelve trades by the hour in US dollars. The panel had one row for it —
// "Labour rate — day £350 default" — which is a figure nobody agreed to, in the wrong
// currency, sitting where the real rates should be.

const withCard = (card) => Object.assign({}, contracts, {
  parameters: contracts.parameters.map((p) => Object.assign({}, p, { rate_card: card }))
});
const WKU_CARD = {
  currency: 'USD', basis: 'hour', source: 'Appendix B',
  lines: [
    { trade: 'Heating, Ventilation and Cooling (HVAC)', straight: 51.4, overtime: 77.1 },
    { trade: 'Plumbers', straight: 51.71, overtime: 77.56 },
    { trade: 'Custodial', straight: 18.18, overtime: 27.27 }
  ]
};
const shape = (cs) => shapeLiveVendors({ summary, contracts: cs, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage }, NOW).V[GK];

test('a contract with a rate card gets a labour rates row naming the trades and currency', () => {
  const R = shape(withCard(WKU_CARD));
  const row = R.terms.find((t) => t.label === 'Labour rates');
  assert.ok(row, 'twelve stated rates and no row to show them');
  assert.match(row.value, /3 trades/);
  assert.match(row.value, /USD/);
  assert.match(row.value, /hour/);
  assert.equal(row.src, 'contract');
});

test('the card lines carry each trade with its straight and overtime rate', () => {
  const R = shape(withCard(WKU_CARD));
  const row = R.terms.find((t) => t.label === 'Labour rates');
  assert.equal(row.lines.length, 3);
  assert.equal(row.lines[0].trade, 'Heating, Ventilation and Cooling (HVAC)');
  assert.equal(row.lines[0].straight, '$51.40');
  assert.equal(row.lines[0].overtime, '$77.10');
});

test('a dollar card is not rendered in pounds', () => {
  // The whole point. £51.40 would be a new lie in place of the one this replaces.
  const R = shape(withCard(WKU_CARD));
  const row = R.terms.find((t) => t.label === 'Labour rates');
  assert.ok(row.lines.every((l) => !/£/.test(l.straight)), 'the toggle governs the portfolio, not what one document says');
});

test('an unstated currency shows the number bare rather than guessing a symbol', () => {
  const R = shape(withCard({ basis: 'hour', lines: [{ trade: 'HVAC', straight: 51.4, overtime: null }] }));
  const row = R.terms.find((t) => t.label === 'Labour rates');
  assert.equal(row.lines[0].straight, '51.40');
  assert.equal(row.lines[0].overtime, '—');
});

test('with a rate card present the scalar rate rows stop asserting a default', () => {
  // Two answers to "what does labour cost?" and the wrong one looked authoritative.
  const R = shape(withCard(WKU_CARD));
  const day = R.terms.find((t) => t.label === 'Labour rate — day');
  assert.match(day.value, /per trade/i, 'a £350 default beside a real card is the trap this closes');
  assert.equal(day.editable, false, 'editing it would write a figure the contract contradicts');
});

test('without a rate card nothing about the panel changes', () => {
  const R = shape(contracts);
  assert.equal(R.terms.find((t) => t.label === 'Labour rates'), undefined);
  assert.equal(R.terms.find((t) => t.label === 'Labour rate — day').value, '£350 / day');
});

test('a card with no lines is treated as no card', () => {
  const R = shape(withCard({ currency: 'USD', lines: [] }));
  assert.equal(R.terms.find((t) => t.label === 'Labour rates'), undefined);
});

// GET /wo-scores — the scored work orders behind a card (Evidence tab, L1/L2/L3 split).
const wo = (vendor_id, month, code, extra) => Object.assign({
  id: code, vendor_id, wo_code: code, score_month: month, priority: 'P1', asset_name: 'AHU-01',
  building_name: 'Harbour Point', criticality: 'L1', sla_response_met: false, sla_completion_met: true,
  response_hours: 6.5, response_target_hours: 4, completion_hours: 20, completion_target_hours: 24,
  first_fix: true, recall: false, contract_parameters_id: 'cp-1'
}, extra || {});
const woScores = { ok: true, wo_scores: [
  wo(A1, '2026-08-01', 'WO-7'),
  wo(A1, '2026-08-01', 'WO-8', { criticality: 'L3', sla_response_met: true, response_hours: 2, first_fix: false }),
  wo(A1, '2026-07-01', 'WO-OLD'),                                   // a different month: not this card
  wo(A3, '2026-08-01', 'WO-9', { criticality: 'L2', recall: true, sla_completion_met: null, completion_hours: null })
] };
const withWo = () => shapeLiveVendors({ summary, contracts, weights: { ok: true, weights: summary.weights }, approvals, certificates, coverage, packs, woScores }, NOW);

test('evidence: each scored work order gives response and completion rows, misses first, no invented credit', () => {
  const rows = evidenceRows([wo(A1, '2026-08-01', 'WO-7')]);
  assert.equal(rows.length, 2);
  assert.deepEqual(
    [rows[0].metric, rows[0].target, rows[0].actual, rows[0].met, rows[0].mult, rows[0].crit],
    ['Response', '4h', '6.5h', false, '3×', 'L1']);
  assert.deepEqual([rows[1].metric, rows[1].met, rows[1].mult], ['Completion', true, '—']);
  assert.equal(rows[0].cost, '—');
  assert.equal(rows[0].creditValue, null);
});

test('evidence: a failed first fix and a recall are rows; a missing timestamp is "not measured", not a pass', () => {
  const rows = evidenceRows([wo(A3, '2026-08-01', 'WO-9', { criticality: 'L2', recall: true, first_fix: false, sla_completion_met: null, completion_hours: null })]);
  assert.ok(rows.some((r) => r.metric === 'Recall' && r.met === false));
  assert.ok(rows.some((r) => r.metric === 'First fix' && r.met === false));
  const comp = rows.find((r) => r.metric === 'Completion');
  assert.equal(comp.met, null);
  assert.equal(comp.actual, 'not measured');
});

test('evidence: without a confirmed contract the target says so rather than showing a default', () => {
  const [r] = evidenceRows([wo(A1, '2026-08-01', 'X', { response_target_hours: null, contract_parameters_id: null })]);
  assert.equal(r.target, 'no confirmed target');
});

test('evidence rows reach the vendor for the month of its card only, and the L1 tile counts L1 SLA misses', () => {
  const m = withWo();
  const codes = m.V[A1].breaches.map((b) => b.wo);
  assert.ok(codes.includes('WO-7') && codes.includes('WO-8'));
  assert.ok(!codes.includes('WO-OLD'));
  assert.deepEqual(m.V[A1].crit, { L1: 1, L2: 0, L3: 1 });
  assert.equal(m.V[A1].evidenceRead, true);
  assert.equal(m.tiles.L1, 1);                                       // WO-7 response miss on an L1 asset
});

test('evidence: a failed wo-scores read leaves the split and the L1 tile unsourced, not zero', () => {
  const m = full();
  assert.equal(m.V[A1].crit, null);
  assert.deepEqual(m.V[A1].breaches, []);
  assert.equal(m.V[A1].evidenceRead, false);
  assert.equal(m.tiles.L1, null);
});
