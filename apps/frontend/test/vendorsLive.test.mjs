// shapeLiveVendors — the Vendors page shaped from svc-operations-intelligence reads.
// Fixtures are trimmed copies of real responses captured from the running service
// (07 Sep 2026): contract-performance scorecards / contracts / weights / approvals,
// and the compliance register's vendor certificates and per-vendor coverage.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveVendors, trendOf } from '../src/logic/vendorsLive.js';

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

test('trend vocabulary follows the seed: improving / declining / stable', () => {
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
  assert.equal(pl.status, 'Expiring');                                   // Due for Renewal → the seed's vocabulary
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
  assert.equal(l2.delta, '+£55');
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
