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

const meters = {
  ok: true, count: 5,
  meters: [
    { id: 'm1', site_id: null, meter_type: 'gas', mprn: '9130847302', active: true },
    { id: 'm2', site_id: null, meter_type: 'electricity', mpan: '1200023306013', active: true },
    { id: 'm3', site_id: 'site-a', meter_type: 'electricity', mpan: '1200023305687', active: true },
    { id: 'm4', site_id: 'site-a', meter_type: 'gas', mprn: '9130847265', active: true },
    { id: 'm5', site_id: 'site-a', meter_type: 'electricity', mpan: '1200023305688', active: true }
  ]
};

const contracts = {
  ok: true, count: 1,
  parameters: [{ id: 'p1', vendor_id: 'v-apex', contract_ref: 'UKRI-2938', status: 'draft' }]
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

const full = () => shapeLiveHome({ raw: { compliance, meters, contracts, approvals, anomalies }, register }, NOW);

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

test('certificates bar is certificates on record over what the packs expect', () => {
  const bar = full().score.bars.find((b) => b.key === 'certificates');
  // 8 + 18 on record; 22 + 19 types not on record → 26 of 67.
  assert.equal(bar.pct, 39);
  assert.equal(bar.val, '39%');
  assert.match(bar.note, /26 of 67/);
  assert.equal(bar.tone, 'risk');
});

test('meter bar is active meters linked to a site', () => {
  const bar = full().score.bars.find((b) => b.key === 'meters');
  assert.equal(bar.pct, 60);
  assert.match(bar.note, /3 of 5/);
  assert.equal(bar.tone, 'warn');
});

test('contracts bar is vendors with contract terms on record over vendors in the register', () => {
  const bar = full().score.bars.find((b) => b.key === 'contracts');
  assert.equal(bar.pct, 9);
  assert.match(bar.note, /1 of 11/);
});

test('contracts bar falls back to confirmed parameter sets when there is no register', () => {
  const h = shapeLiveHome({ raw: { contracts }, register: null }, NOW);
  const bar = h.score.bars.find((b) => b.key === 'contracts');
  assert.equal(bar.pct, 0);
  assert.match(bar.note, /0 of 1 confirmed/);
});

test('assets bar has no source yet and says so', () => {
  const bar = full().score.bars.find((b) => b.key === 'assets');
  assert.equal(bar.pct, null);
  assert.equal(bar.val, '—');
  assert.equal(bar.tone, 'none');
  assert.match(bar.note, /connector/i);
});

test('score is the mean of the sourced bars, with the band and the gap named', () => {
  const s = full().score;
  assert.equal(s.value, 36); // (39 + 9 + 60) / 3
  assert.equal(s.band, 'Ingestion in progress');
  assert.equal(s.sourced, 3);
  assert.equal(s.total, 4);
  assert.match(s.gap, /Contracts lowest at 9%/);
  assert.match(s.note, /3 of 4 sources/);
});

test('score bands: delegated at 85, supervised at 60', () => {
  const at = (pct) => shapeLiveHome({
    raw: { meters: { meters: Array.from({ length: 100 }, (_, i) => ({ id: String(i), active: true, site_id: i < pct ? 's' : null })) } },
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
