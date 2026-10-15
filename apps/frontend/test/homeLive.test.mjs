// shapeLiveHome — the Home page's live tiles, shaped from svc-operations-intelligence reads.
// Fixtures are trimmed copies of real responses captured from the running service.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveHome } from '../src/logic/homeLive.js';

// 07 Sep 2026, 14:00 local — every "Today / Yesterday" test is relative to this.
const NOW = new Date(2026, 8, 7, 14, 0, 0);

const compliance = {
  ok: true,
  building_certificates: { total: 8, total_active: 1, compliant: 1, non_compliant: 6, lapsed: 6, not_on_record: 22, drafts: 5 },
  vendor_certificates: { total: 18, total_active: 6, compliant: 6, non_compliant: 9, lapsed: 9, not_on_record: 19, drafts: 8 },
  risk_dashboard: { vendors_blocked: 6, high_risk_lt_30: 1, medium_risk_lt_90: 2 }
};

// GET /api/energy/hoist-score — two buildings hoisted; Ashgrove Court has no contract and no
// certificate yet, neither has a work order.
const AC = { building_id: 'b-102', name: 'Ashgrove Court', building_code: 'B-102' };
const HP = { building_id: 'b-101', name: 'Harbour Point', building_code: 'B-101' };
const coverage = {
  ok: true, root: 'buildings', buildings: 2, score: 60,
  domains: [
    { key: 'assets', covered: 2, of: 2, pct: 100, missing_buildings: [], note: null },
    { key: 'compliance', covered: 1, of: 2, pct: 50, missing_buildings: [AC], note: null },
    { key: 'contracts', covered: 1, of: 2, pct: 50, missing_buildings: [AC], note: null },
    { key: 'energy', covered: 2, of: 2, pct: 100, missing_buildings: [], note: null },
    { key: 'maintenance', covered: 0, of: 2, pct: 0, missing_buildings: [HP, AC], note: null }
  ],
  rows: []
};

// The compliance console's live register (shapeLiveCompliance output), reused for the hero.
const register = {
  certs: [
    { id: 'c1', kind: 'building', cc: 'UK', holder: 'Bishopsgate Tower' },
    { id: 'c2', kind: 'vendor', cc: 'UK', holder: 'Apex Mechanical Services Ltd' },
    { id: 'c3', kind: 'vendor', cc: 'US', holder: 'Joe Facility-User' }
  ],
  buildings: [
    { name: 'Bishopsgate Tower', cc: 'UK', linked: true },
    { name: 'Town Hall', cc: 'UK', linked: true },
    { name: 'No building on certificate', cc: 'UK', linked: false }
  ],
  vendors: Array.from({ length: 11 }, (_, i) => ({ name: 'Vendor ' + i, cc: 'UK' }))
};

const approvals = {
  ok: true, count: 4,
  items: [
    { id: 'a1', source_feature: 'A', item_type: 'vendor_email', severity: 'Critical', status: 'pending',
      summary: 'Lapsed: ProudCastle Solutions Ltd — BAFE SP203-1 Registration Certificate',
      created_at: '2026-09-07T12:16:46+00:00', email_draft: { to: 'pm@example.com', subject: 'Renewal' } },
    { id: 'a2', source_feature: 'A', item_type: 'vendor_risk', severity: 'Info', status: 'pending',
      summary: 'Medium Risk: BrightSpark Electrical Ltd — NICEIC Approved Contractor Certificate (37d)',
      created_at: '2026-09-07T12:16:40+00:00' },
    { id: 'a3', source_feature: 'B', item_type: 'alert', severity: 'Action required', status: 'pending',
      summary: 'Invoice line above contracted rate — WO-APEX-2607-1444',
      created_at: '2026-09-06T09:05:00+00:00' },
    { id: 'a4', source_feature: 'A', item_type: 'block_lift', severity: 'Info', status: 'pending',
      summary: 'Block lifted — Apex Mechanical Services Ltd (SAFE_CONTRACTOR)',
      created_at: '2026-09-02T08:00:00+00:00' }
  ]
};

const anomalies = {
  ok: true, count: 1,
  anomalies: [
    { id: 'an1', anomaly_type: 'weekend_spike', status: 'open', metric_pct: 226.02, financial_gbp: 14967.99,
      site_id: 'site-a', detected_at: '2026-08-31T18:05:54+00:00' }
  ]
};

const full = () => shapeLiveHome({ raw: { compliance, coverage, approvals, anomalies }, register }, NOW);

test('with nothing loaded, nothing is asserted', () => {
  const h = shapeLiveHome({ raw: null, register: null }, NOW);
  assert.equal(h.live, false);
  assert.equal(h.score.value, null);
  assert.equal(h.score.bars.length, 4);
  assert.ok(h.score.bars.every((b) => b.pct === null && b.tone === 'none'));
  assert.deepEqual(h.crons, []);
  assert.equal(h.hero.buildings, null);
  assert.equal(h.pending, null);
});

test('each bar is hoisted buildings with that record over hoisted buildings', () => {
  const bars = full().score.bars;
  const by = (k) => bars.find((b) => b.key === k);
  assert.equal(by('assets').pct, 100);
  assert.equal(by('assets').val, '100%');
  assert.match(by('assets').note, /2 of 2 hoisted buildings with an asset register on record/);
  assert.equal(by('assets').tone, 'ok');
  assert.equal(by('contracts').pct, 50);
  assert.match(by('contracts').note, /1 of 2 hoisted buildings with a contract on record/);
  assert.equal(by('contracts').tone, 'risk');
  assert.equal(by('meters').pct, 100);
  assert.match(by('meters').note, /with a meter on record/);
  assert.equal(by('certificates').pct, 50);
  assert.match(by('certificates').note, /with a certificate on record/);
});

test('score is the mean of the four bars, with the band and the gap naming the building', () => {
  const s = full().score;
  assert.equal(s.value, 75); // (50 + 100 + 100 + 50) / 4
  assert.equal(s.band, 'Supervised autonomy');
  assert.equal(s.sourced, 4);
  assert.equal(s.total, 4);
  assert.match(s.gap, /^Contracts lowest at 50% — Ashgrove Court has none on record$/);
  assert.match(s.note, /Live · 4 of 4 sources · 2 buildings hoisted/);
});

test('the gap line lists up to three buildings and counts the rest', () => {
  const many = Array.from({ length: 5 }, (_, i) => ({ building_id: 'b' + i, name: 'Building ' + i, building_code: null }));
  const cov = { ok: true, root: 'buildings', buildings: 6, domains: [
    { key: 'assets', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'compliance', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'contracts', covered: 1, of: 6, pct: 17, missing_buildings: many, missing_count: 30 },
    { key: 'energy', covered: 6, of: 6, pct: 100, missing_buildings: [] },
    { key: 'maintenance', covered: 0, of: 6, pct: 0, missing_buildings: [] }
  ] };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  // The backend caps the names it sends and says how many there really are.
  assert.equal(s.gap, 'Contracts lowest at 17% — Building 0, Building 1, Building 2 and 27 more have none on record');
});

test('a coverage read rooted on sites leaves every bar unsourced and says why', () => {
  const cov = { ok: true, root: 'sites', buildings: 3, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: null, of: 3, pct: null, missing_buildings: [], note: 'the building graph has no rows yet, so nothing is counted against a building' }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, null);
  assert.equal(s.answered, true);
  assert.equal(s.band, 'Not counted');
  assert.ok(s.bars.every((b) => b.pct === null && /building graph has no rows/.test(b.note)));
  // The tile's own line says what the bars say, not that nothing answered.
  assert.equal(s.note, 'The building graph has no rows yet, so nothing is counted against a building');
});

test('no buildings hoisted is said in words, not as 0%', () => {
  const cov = { ok: true, root: 'buildings', buildings: 0, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: null, of: 0, pct: null, missing_buildings: [], note: 'no buildings hoisted yet' }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, null);
  assert.equal(s.answered, true);
  assert.equal(s.band, 'Nothing hoisted');
  assert.equal(s.bars[0].val, '—');
  assert.equal(s.note, 'No buildings hoisted yet — hoist one and ingest against it');
});

test('when the coverage read fails the bars say so and the other tiles still render', () => {
  const h = shapeLiveHome({ raw: { compliance, approvals, anomalies, coverage: null }, register }, NOW);
  assert.equal(h.live, true);
  assert.equal(h.score.value, null);
  assert.equal(h.score.answered, false);
  assert.ok(h.score.bars.every((b) => b.pct === null && b.note === 'coverage read did not answer'));
  assert.ok(h.crons.length > 0);
});

test('score bands: delegated at 85, supervised at 60', () => {
  const at = (pct) => shapeLiveHome({
    raw: { coverage: { ok: true, root: 'buildings', buildings: 100, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
      { key: k, covered: pct, of: 100, pct: pct, missing_buildings: [] }
    )) } },
    register: null
  }, NOW).score;
  assert.equal(at(85).band, 'Delegated autonomy');
  assert.equal(at(60).band, 'Supervised autonomy');
  assert.equal(at(59).band, 'Ingestion in progress');
});

test('crons come from the approvals queue, newest first, labelled by the engine that raised them', () => {
  const rows = full().crons.filter((c) => c.kind === 'approval');
  assert.equal(rows.length, 4);
  assert.equal(rows[0].id, 'a1');
  assert.equal(rows[0].agent, 'Compliance');
  assert.equal(rows[2].agent, 'Vendor');
  assert.equal(rows[0].text, approvals.items[0].summary);
  assert.equal(rows[0].day, 'Today');
  assert.equal(rows[2].day, 'Yesterday');
  assert.equal(rows[3].day, '02 Sep');
  assert.match(rows[0].t, /^\d\d:\d\d$/);
});

test('cron severity sets the dot and whether there is something to review', () => {
  const rows = full().crons.filter((c) => c.kind === 'approval');
  assert.equal(rows[0].tone, 'risk');   // Critical
  assert.equal(rows[0].action, 'Review');
  assert.equal(rows[1].tone, 'ok');     // Info
  assert.equal(rows[1].action, null);
  assert.equal(rows[2].tone, 'warn');   // Action required
  assert.equal(rows[2].action, 'Review');
});

test('open energy anomalies join the feed as Energy rows priced per year', () => {
  const row = full().crons.find((c) => c.kind === 'anomaly');
  assert.equal(row.agent, 'Energy');
  assert.match(row.text, /Weekend spike/);
  assert.match(row.text, /226%/);
  assert.match(row.text, /£14,968\/yr/);
  assert.equal(row.tone, 'warn');
  assert.equal(row.action, 'Review');
  assert.equal(row.day, '31 Aug');
});

test('the feed is one list ordered by time across both sources', () => {
  const c = full().crons;
  for (let i = 1; i < c.length; i++) assert.ok(c[i - 1].at >= c[i].at, 'row ' + i + ' out of order');
  assert.equal(c[c.length - 1].kind, 'anomaly');
});

test('hero counts hoisted buildings, certificates and countries from the live register', () => {
  const hero = full().hero;
  assert.equal(hero.buildings, 2); // the "no building on certificate" bucket is not a building
  assert.equal(hero.certificates, 3);
  assert.equal(hero.vendors, 11);
  assert.deepEqual(hero.countries, ['UK', 'US']);
});

test('pending is the size of the approvals queue', () => {
  assert.equal(full().pending, 4);
  assert.equal(full().live, true);
});

test('a coverage read that omits the building count does not print "undefined"', () => {
  const cov = { ok: true, root: 'buildings', domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: 1, of: 2, pct: 50, missing_buildings: [] }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, 50);
  assert.doesNotMatch(s.note, /undefined/);
  assert.match(s.note, /Live · 4 of 4 sources$/);
});

test('a restricted user with no allocation is told that, not told to hoist a building', () => {
  const cov = { ok: true, root: 'buildings', buildings: 0, domains: ['assets', 'compliance', 'contracts', 'energy', 'maintenance'].map((k) => (
    { key: k, covered: null, of: 0, pct: null, missing_buildings: [], note: 'no buildings allocated to you' }
  )) };
  const s = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score;
  assert.equal(s.value, null);
  assert.equal(s.band, 'None allocated');
  assert.equal(s.note, 'No buildings allocated to you. Ask an admin to allocate one.');
});

test('the percentage shown is the one the backend computed', () => {
  // One derivation, on the server, shared with the Buildings column. The client recomputes
  // only when a domain arrives without a pct.
  const cov = { ok: true, root: 'buildings', buildings: 3, domains: [
    { key: 'assets', covered: 1, of: 3, pct: 34, missing_buildings: [] },
    { key: 'compliance', covered: 1, of: 3, missing_buildings: [] },
    { key: 'contracts', covered: 3, of: 3, pct: 100, missing_buildings: [] },
    { key: 'energy', covered: 3, of: 3, pct: 100, missing_buildings: [] },
    { key: 'maintenance', covered: 0, of: 3, pct: 0, missing_buildings: [] }
  ] };
  const bars = shapeLiveHome({ raw: { coverage: cov }, register: null }, NOW).score.bars;
  assert.equal(bars.find((b) => b.key === 'assets').pct, 34);
  assert.equal(bars.find((b) => b.key === 'certificates').pct, 33);
});

test('the headline counts every hoisted building, not only the ones with a certificate', () => {
  // Harbour Point has certificates; Ashgrove Court was hoisted with none yet. The register
  // alone sees one building — the coverage read counts the buildings table and sees two.
  const h = shapeLiveHome({
    raw: { coverage: { buildings: 2, domains: [] } },
    register: { buildings: [{ name: 'Harbour Point', cc: 'UK' }], certs: [{ cc: 'UK' }], vendors: [] }
  });
  assert.equal(h.hero.buildings, 2);
  // Without the coverage read the register's count is still used.
  const r = shapeLiveHome({ raw: {}, register: { buildings: [{ name: 'Harbour Point', cc: 'UK' }], certs: [], vendors: [] } });
  assert.equal(r.hero.buildings, 1);
});
