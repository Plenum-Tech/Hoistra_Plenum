// shapeSpaces — the four built-in spaces with their live figures, and the customer-named
// saved spaces from svc-udr. Fixtures are trimmed copies of real responses captured from
// the running stack on 08 Sep 2026.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const { shapeSpaces, BUILTIN_SPACES } = await import('../src/logic/spacesLive.js');

const compliance = {
  ok: true,
  building_certificates: { total: 8, total_active: 1, compliant: 1, non_compliant: 6, expiring_lt_30: 0, lapsed: 6, not_on_record: 22, drafts: 5 },
  vendor_certificates: { total: 18, total_active: 6, compliant: 6, non_compliant: 9, expiring_lt_30: 1, lapsed: 9, not_on_record: 19, drafts: 8 },
  risk_dashboard: { vendors_blocked: 6, high_risk_lt_30: 1, medium_risk_lt_90: 2 }
};
const anomalies = {
  ok: true, count: 3,
  anomalies: [
    { id: 'a1', anomaly_type: 'weekend_spike', status: 'open', financial_gbp: 14967.99, site_id: 'e2a5' },
    { id: 'a2', anomaly_type: 'weekend_spike', status: 'open', financial_gbp: 2142.14, site_id: 'e2a5' },
    { id: 'a3', anomaly_type: 'baseline_drift', status: 'open', financial_gbp: 2142.14, site_id: 'b7c1' }
  ]
};
const approvals = { ok: true, count: 25, items: [{ severity: 'Critical' }, { severity: 'Info' }, { severity: 'medium' }, { severity: 'Action required' }, { severity: 'Critical' }] };
const vendors = {
  live: true,
  vendors: [
    { id: 'v1', name: 'Apex', score: 46.58, blocked: true },
    { id: 'v2', name: 'SafeLift', score: 20.38, blocked: false },
    { id: 'v3', name: 'BrightSpark', score: 85.61, blocked: false },
    { id: 'v4', name: 'Unscored', score: null, blocked: false }
  ]
};
const saved = [
  { id: 'sp-1', organization_id: null, name: 'Tower 3 certificates', kind: 'custom', created_by: null, created_at: '2026-09-08T10:00:00+00:00' },
  { id: 'sp-2', organization_id: null, name: 'Vendor X', kind: 'custom', created_by: 'a@b.c', created_at: '2026-09-07T10:00:00+00:00' }
];
const sessions = [
  { id: 's1', kind: 'chat', domain: 'Compliance', spaceId: null },
  { id: 's2', kind: 'chat', domain: 'Compliance', spaceId: 'sp-1' },
  { id: 's3', kind: 'chat', domain: 'Energy', spaceId: null },
  { id: 's4', kind: 'task', domain: 'Orchestrator', spaceId: null }
];

test('the four built-in spaces are always present, in the navigator order', () => {
  const m = shapeSpaces({ home: null, vendors: { live: false, vendors: [] }, saved: null, sessions: [] });
  assert.deepEqual(m.builtin.map((b) => b.key), ['compliance', 'energy', 'vendors', 'ops']);
  assert.deepEqual(BUILTIN_SPACES.map((b) => b.name), ['Compliance', 'Energy', 'Vendor performance', 'Vendor operations']);
  m.builtin.forEach((b) => {
    assert.equal(b.badge, '—', b.key + ' has no figure until its source answers');
    assert.equal(b.count, null);
    assert.equal(b.live, false);
    assert.deepEqual(b.kpis, []);
  });
  assert.equal(m.savedLive, false);
  assert.deepEqual(m.custom, []);
});

test('compliance badge is the lapsed certificates across buildings and vendors', () => {
  const m = shapeSpaces({ home: { compliance }, vendors: { live: false, vendors: [] }, saved: null, sessions });
  const c = m.byKey.compliance;
  assert.equal(c.badge, '15 lapsed');
  assert.equal(c.count, 15);
  assert.equal(c.tone, 'risk');
  assert.equal(c.live, true);
  assert.deepEqual(c.kpis.map((k) => k.label + ' ' + k.value), [
    'Certificates on record 26', 'Lapsed 15', 'Expiring in 30 days 1', 'Drafts 13', 'Vendors blocked 6'
  ]);
  assert.equal(c.sessions, 2, 'both compliance chats, the task is not a chat');
});

test('energy badge is the open anomalies with their annualised exposure', () => {
  const m = shapeSpaces({ home: { anomalies }, vendors: { live: false, vendors: [] }, saved: null, sessions });
  const e = m.byKey.energy;
  assert.equal(e.badge, '3 anomalies');
  assert.equal(e.tone, 'warn');
  assert.deepEqual(e.kpis.map((k) => k.label + ' ' + k.value), ['Open anomalies 3', 'Annualised exposure £19,252', 'Sites affected 2']);
  assert.equal(e.sessions, 1);
  const one = shapeSpaces({ home: { anomalies: { ok: true, count: 1, anomalies: [anomalies.anomalies[0]] } }, vendors: { live: false, vendors: [] }, saved: null, sessions: [] });
  assert.equal(one.byKey.energy.badge, '1 anomaly');
});

test('vendor performance badge counts the vendors scored below 80 on their newest card', () => {
  const m = shapeSpaces({ home: null, vendors, saved: null, sessions: [] });
  const v = m.byKey.vendors;
  assert.equal(v.badge, '2 below 80');
  assert.equal(v.count, 2);
  assert.deepEqual(v.kpis.map((k) => k.label + ' ' + k.value), ['Vendors scored 3', 'Average score 51', 'Below 80 2', 'Blocked 1']);
});

test('vendor operations badge is the pending approvals, marked when the page cap is hit', () => {
  const m = shapeSpaces({ home: { approvals }, vendors: { live: false, vendors: [] }, saved: null, sessions: [] });
  const o = m.byKey.ops;
  assert.equal(o.badge, '25 to approve');
  assert.equal(o.tone, 'risk', 'critical items make it risk');
  assert.deepEqual(o.kpis.map((k) => k.label + ' ' + k.value), ['Pending 25', 'Critical 2', 'Action required 1', 'Medium 1', 'Info 1']);
  const capped = shapeSpaces({ home: { approvals: { ok: true, count: 500, items: [] } }, vendors: { live: false, vendors: [] }, saved: null, sessions: [] });
  assert.equal(capped.byKey.ops.badge, '500+ to approve');
  assert.equal(capped.byKey.ops.count, 500);
});

test('saved spaces from svc-udr list newest first with the sessions filed in them', () => {
  const m = shapeSpaces({ home: null, vendors: { live: false, vendors: [] }, saved, sessions });
  assert.equal(m.savedLive, true);
  assert.deepEqual(m.custom.map((c) => c.name), ['Tower 3 certificates', 'Vendor X']);
  assert.equal(m.custom[0].sessions, 1);
  assert.equal(m.custom[1].sessions, 0);
  assert.equal(m.byKey['sp-1'].name, 'Tower 3 certificates');
  assert.equal(m.byKey['sp-1'].custom, true);
});

test('a svc-udr failure is reported without hiding the built-in spaces', () => {
  const m = shapeSpaces({ home: null, vendors: { live: false, vendors: [] }, saved: null, sessions: [], savedError: 'timed out after 15s' });
  assert.equal(m.builtin.length, 4);
  assert.equal(m.savedLive, false);
  assert.equal(m.savedError, 'timed out after 15s');
});
