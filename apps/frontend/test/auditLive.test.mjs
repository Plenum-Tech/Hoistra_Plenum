// shapeLiveAudit — the Ingestion audit trail's rows, shaped from svc-operations-intelligence's
// GET /api/admin/ingestion-audit into exactly the AU_SEED shape auditVals renders. Fixtures
// mirror the response transcribed in contract.md from the route handlers (12 Sep 2026):
// snake_case outcomes, findings under detail.checks (steps) or detail.findings (decisions),
// building names on the row, suggested_building_id id-only.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveAudit, whenLabel, checksSummary, buildingNames, OUTCOME_LABEL, auditLiveMethods } from '../src/logic/auditLive.js';

const B1 = '5b1f8e6a-0d5c-4f2b-9a6e-2f4f4b3c1a10'; // Riverside Court
const B2 = '9c2d7f1b-4e6a-4c3d-8b5f-7a1e2d3c4b50'; // Bishopsgate Tower
const NAMES = buildingNames([
  { id: B1, name: 'Riverside Court', building_code: 'B-002' },
  { id: B2, name: 'Bishopsgate Tower', building_code: 'B-001' }
], null);

const F = (check, direction) => ({ check, direction, weight: 3.0, message: check + ' ' + direction, claimed: 'x', known: 'y' });
const SIX_OK = ['building_name', 'building_code', 'country', 'vendor', 'assets', 'building_evidence'].map((c) => F(c, 'supports'));
const NOW = new Date('2026-09-12T10:00:00');

test('a clean accepted decision shapes to the seed vocabulary: ok tone, all checks passed, no approval needed', () => {
  const a = shapeLiveAudit({
    id: 'e-1', occurred_at: '2026-09-12T09:41:00', outcome: 'accepted',
    document_name: '8f14e45f-ceea-4a42-9902-4c9a0f2b1de7_Legionella risk assessment.pdf',
    document_id: 'd-1', warning: null, explanation: null,
    detail: { case_id: 'c-1', verdict: 'matched', assessment: {}, note: null, findings: SIX_OK },
    actor_user_id: 'u-1', actor_role: 'user', actor_name: 'Daniel Reyes', actor_email: 'daniel@co.com',
    building_id: B1, building_name: 'Riverside Court',
    reassigned_to_building_id: null, reassigned_to_building_name: null,
    approved_by: null, approved_by_name: null
  }, NAMES, NOW);
  assert.equal(a.live, true);
  assert.equal(a.when, 'Today 09:41');
  assert.equal(a.who, 'Daniel Reyes');
  assert.equal(a.role, 'User');
  assert.equal(a.building, 'Riverside Court');
  assert.equal(a.finalB, 'Riverside Court');
  assert.equal(a.doc, 'Legionella risk assessment.pdf', 'the uuid_ prefix is not for people');
  assert.equal(a.outcome, 'Accepted');
  assert.equal(a.tone, 'ok');
  assert.equal(a.checks, '6 of 6 passed');
  assert.match(a.issue, /^None/);
  assert.equal(a.suggested, '—');
  assert.equal(a.explanation, '—');
  assert.equal(a.approval, 'Not required — clean match');
});

test('a reassigned decision routes building → finalB and resolves the suggested id against axBldsLive', () => {
  const a = shapeLiveAudit({
    id: 'e-2', occurred_at: '2026-09-12T08:12:00', outcome: 'reassigned',
    document_name: 'Fire alarm service certificate.pdf', document_id: 'd-2',
    warning: 'Vendor and referenced asset FA-2201 not associated with Riverside Court',
    explanation: null,
    detail: { case_id: 'c-2', verdict: 'matched', assessment: {}, note: null,
      findings: SIX_OK.slice(0, 4).concat([F('vendor', 'conflicts'), F('assets', 'conflicts')]),
      suggested_building_id: B2 },
    actor_user_id: 'u-2', actor_role: 'user', actor_name: 'Amara Osei', actor_email: 'amara@co.com',
    building_id: B1, building_name: 'Riverside Court',
    reassigned_to_building_id: B2, reassigned_to_building_name: 'Bishopsgate Tower',
    approved_by: 'u-2', approved_by_name: 'Amara Osei'
  }, NAMES, NOW);
  assert.equal(a.outcome, 'Reassigned');
  assert.equal(a.tone, 'accent');
  assert.equal(a.building, 'Riverside Court');
  assert.equal(a.finalB, 'Bishopsgate Tower');
  assert.equal(a.suggested, 'Bishopsgate Tower');
  assert.equal(a.checks, '6 run · 2 mismatched');
  assert.equal(a.issue, 'Vendor and referenced asset FA-2201 not associated with Riverside Court');
  assert.equal(a.approval, 'Yes — after switch');
});

test('an overridden decision keeps the uploader explanation and the agent assessment reason, warn tone', () => {
  const a = shapeLiveAudit({
    id: 'e-3', occurred_at: '2026-09-08T16:20:00', outcome: 'overridden',
    document_name: 'Contract addendum — NovaClean FM.pdf', document_id: 'd-3',
    warning: 'NovaClean FM is not an approved vendor for Town Hall',
    explanation: 'New vendor — contract onboarding completes next week',
    detail: { case_id: 'c-3', verdict: 'mismatch',
      assessment: { resolves: false, addressed: [], unaddressed: ['vendor'], reason: 'Plausible: vendor record missing rather than wrong', method: 'llm:x' },
      note: 'go ahead', findings: SIX_OK.slice(0, 5).concat([F('vendor', 'conflicts')]) },
    actor_user_id: 'u-3', actor_role: 'admin', actor_name: 'Marcus Hale', actor_email: 'marcus@co.com',
    building_id: B1, building_name: 'Town Hall',
    reassigned_to_building_id: null, reassigned_to_building_name: null,
    approved_by: 'u-3', approved_by_name: 'Marcus Hale'
  }, NAMES, NOW);
  assert.equal(a.outcome, 'Overridden');
  assert.equal(a.tone, 'warn');
  assert.equal(a.role, 'Admin');
  assert.equal(a.building, 'Town Hall', 'the row-borne name wins even when the id is not in the register');
  assert.equal(a.checks, '6 run · 1 mismatched');
  assert.equal(a.explanation, 'New vendor — contract onboarding completes next week');
  assert.equal(a.assessment, 'Plausible: vendor record missing rather than wrong');
  assert.equal(a.approval, 'Yes — explicit override');
});

test('a rejected decision has no final building', () => {
  const a = shapeLiveAudit({
    id: 'e-4', occurred_at: '2026-09-08T11:03:00', outcome: 'rejected',
    document_name: 'Vendor invoice — HVAC quarterly service.pdf', document_id: null,
    warning: 'Invoice lines reference AHU assets on record elsewhere', explanation: null,
    detail: { case_id: 'c-4', verdict: 'mismatch', assessment: {}, note: null,
      findings: SIX_OK.slice(0, 3).concat([F('assets', 'conflicts'), F('vendor', 'conflicts'), F('building_name', 'conflicts')]) },
    actor_user_id: 'u-4', actor_role: 'user', actor_name: null, actor_email: 'tom@co.com',
    building_id: B1, building_name: 'Riverside Court',
    reassigned_to_building_id: null, reassigned_to_building_name: null,
    approved_by: null, approved_by_name: null
  }, NAMES, NOW);
  assert.equal(a.outcome, 'Rejected');
  assert.equal(a.tone, 'risk');
  assert.equal(a.finalB, '—');
  assert.equal(a.who, 'tom@co.com', 'the email stands in when the actor has no name');
  assert.equal(a.checks, '6 run · 3 mismatched');
  assert.equal(a.approval, 'No');
});

test('approved_on_confirmation folds unknown-direction checks into inconclusive, dormant tone', () => {
  const a = shapeLiveAudit({
    id: 'e-5', occurred_at: '2026-08-28T14:47:00', outcome: 'approved_on_confirmation',
    document_name: 'First UDR pack — Marina Heights.zip', document_id: 'd-5',
    warning: 'New building — insufficient reference data to validate relationships',
    explanation: 'First ingestion for a newly created building',
    detail: { case_id: 'c-5', verdict: 'uncertain',
      assessment: { resolves: true, addressed: ['building_evidence'], unaddressed: [], reason: 'Cannot be logically proven — the building ontology is still empty', method: 'llm:x' },
      note: null, findings: SIX_OK.slice(0, 2).concat(['country', 'vendor', 'assets', 'building_evidence'].map((c) => F(c, 'unknown'))) },
    actor_user_id: 'u-3', actor_role: 'admin', actor_name: 'Marcus Hale', actor_email: 'marcus@co.com',
    building_id: B2, building_name: null,
    reassigned_to_building_id: null, reassigned_to_building_name: null,
    approved_by: 'u-3', approved_by_name: 'Marcus Hale'
  }, NAMES, NOW);
  assert.equal(a.outcome, 'Approved on confirmation');
  assert.equal(a.tone, 'dormant');
  assert.equal(a.when, '28 Aug 14:47');
  assert.equal(a.building, 'Bishopsgate Tower', 'a null building_name resolves through the id');
  assert.equal(a.checks, '6 run · 4 inconclusive', 'unknown is inconclusive, never a mismatch');
  assert.equal(a.approval, 'Yes — explicit confirmation');
});

test('a held step row shows under All with no final building and no approval — nothing was filed', () => {
  const a = shapeLiveAudit({
    id: 'e-6', occurred_at: '2026-09-12T09:59:30', outcome: 'held',
    document_name: 'INV-30412.pdf', document_id: null,
    warning: 'Vendor not on record for this building', explanation: null,
    detail: { case_id: 'c-6', verdict: 'mismatch', confidence: 0.4,
      checks: SIX_OK.slice(0, 5).concat([F('vendor', 'conflicts')]), suggested_building_id: B2 },
    actor_user_id: 'u-1', actor_role: 'user', actor_name: 'Daniel Reyes', actor_email: 'daniel@co.com',
    building_id: B1, building_name: 'Riverside Court',
    reassigned_to_building_id: null, reassigned_to_building_name: null,
    approved_by: null, approved_by_name: null
  }, NAMES, NOW);
  assert.equal(a.outcome, 'Held');
  assert.equal(a.finalB, '—');
  assert.equal(a.approval, '—');
  assert.equal(a.suggested, 'Bishopsgate Tower');
  assert.equal(a.checks, '6 run · 1 mismatched', 'step rows carry findings as detail.checks');
});

test('an empty or unknown entry degrades to dashes and a readable label instead of throwing', () => {
  const a = shapeLiveAudit({ id: 'e-7', outcome: 'quarantined', detail: {} }, {}, NOW);
  assert.equal(a.when, '—');
  assert.equal(a.who, '—');
  assert.equal(a.building, '—');
  assert.equal(a.outcome, 'Quarantined');
  assert.equal(a.checks, '—');
  assert.equal(a.approval, '—');
});

test('whenLabel speaks the seed vocabulary: Just now / Today / weekday / date, year only when it differs', () => {
  const now = new Date('2026-09-12T10:00:00'); // a Saturday
  assert.equal(whenLabel('2026-09-12T09:59:30', now), 'Just now');
  assert.equal(whenLabel('2026-09-12T09:41:00', now), 'Today 09:41');
  assert.equal(whenLabel('2026-09-07T16:20:00', now), 'Mon 16:20');
  assert.equal(whenLabel('2026-08-28T14:47:00', now), '28 Aug 14:47');
  assert.equal(whenLabel('2025-12-31T23:59:00', now), '31 Dec 2025 23:59');
  assert.equal(whenLabel(null, now), '—');
});

test('checksSummary counts conflicts and unknowns separately', () => {
  assert.equal(checksSummary(SIX_OK), '6 of 6 passed');
  assert.equal(checksSummary(SIX_OK.slice(0, 4).concat([F('vendor', 'conflicts'), F('country', 'unknown')])), '6 run · 1 mismatched · 1 inconclusive');
  assert.equal(checksSummary([]), '—');
  assert.equal(checksSummary(null), '—');
});

test('every wire outcome token has a display label', () => {
  ['accepted', 'approved_on_confirmation', 'clarified', 'held', 'overridden', 'reassigned', 'rejected', 'validated'].forEach((t) => {
    assert.ok(OUTCOME_LABEL[t], t + ' should be labelled');
  });
});

// auLiveLoad — the loader on the real controller: the read replaces the seed in `audit`
// in place, and auditVals' untouched prefix-match filter folds approved_on_confirmation
// under the Accepted chip on live rows exactly as it did on the seed. The mixin is
// assigned manually here so these pass before the integrator touches HoistraLogic.js.
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.fetch = () => Promise.reject(new TypeError('no network in this test'));
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
Object.assign(HoistraLogic.prototype, auditLiveMethods);

const PAYLOAD = {
  ok: true, count: 3,
  by_outcome: { accepted: 1, approved_on_confirmation: 1, rejected: 1 },
  outcomes: ['accepted', 'approved_on_confirmation', 'clarified', 'held', 'overridden', 'reassigned', 'rejected', 'validated'],
  entries: [
    // deliberately oldest-first to prove the shaper re-sorts newest-first
    { id: 'e-11', occurred_at: '2026-09-10T08:00:00', outcome: 'rejected',
      document_name: 'wrong-building.pdf', document_id: null, warning: 'Assets elsewhere', explanation: null,
      detail: { case_id: 'c-11', verdict: 'mismatch', assessment: {}, note: null, findings: [F('assets', 'conflicts')] },
      actor_user_id: 'u-1', actor_role: 'user', actor_name: 'Daniel Reyes', actor_email: 'daniel@co.com',
      building_id: B1, building_name: 'Riverside Court',
      reassigned_to_building_id: null, reassigned_to_building_name: null, approved_by: null, approved_by_name: null },
    { id: 'e-12', occurred_at: '2026-09-11T09:00:00', outcome: 'approved_on_confirmation',
      document_name: 'first-pack.zip', document_id: 'd-12', warning: 'New building', explanation: 'First ingestion',
      detail: { case_id: 'c-12', verdict: 'uncertain', assessment: { resolves: true, reason: 'Ontology empty' }, note: null, findings: [F('building_evidence', 'unknown')] },
      actor_user_id: 'u-3', actor_role: 'admin', actor_name: 'Marcus Hale', actor_email: 'marcus@co.com',
      building_id: B2, building_name: 'Bishopsgate Tower',
      reassigned_to_building_id: null, reassigned_to_building_name: null, approved_by: 'u-3', approved_by_name: 'Marcus Hale' },
    { id: 'e-13', occurred_at: '2026-09-12T09:41:00', outcome: 'accepted',
      document_name: 'clean.pdf', document_id: 'd-13', warning: null, explanation: null,
      detail: { case_id: 'c-13', verdict: 'matched', assessment: {}, note: null, findings: SIX_OK },
      actor_user_id: 'u-1', actor_role: 'user', actor_name: 'Daniel Reyes', actor_email: 'daniel@co.com',
      building_id: B1, building_name: 'Riverside Court',
      reassigned_to_building_id: null, reassigned_to_building_name: null, approved_by: null, approved_by_name: null }
  ]
};

test('auLiveLoad replaces the seed in place, newest first, and sends no organization_id', async () => {
  let seen = null;
  globalThis.fetch = async (url) => {
    seen = String(url);
    return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(PAYLOAD) };
  };
  const c = new HoistraLogic();
  const seedLen = c.state.audit.length;
  assert.ok(seedLen > 0, 'the seed renders before the read answers');
  await c.auLiveLoad();
  assert.match(seen, /\/api\/admin\/ingestion-audit\?/);
  assert.match(seen, /limit=200/);
  assert.ok(!/organization_id/.test(seen), 'the admin wrapper never sends the org — it is the superadmin override');
  assert.equal(c.state.audit.length, 3);
  assert.ok(c.state.audit.every((a) => a.live));
  assert.deepEqual(c.state.audit.map((a) => a.id), ['e-13', 'e-12', 'e-11'], 'newest first');
  assert.equal(c.state.auLiveError, '');
  assert.ok(c.state.auLiveLoadedAt);
  assert.equal(c.state.auLiveRaw.count, 3);
  // The untouched auditVals machinery over live rows: count, fold, and the 8-field record.
  c.setState({ signedIn: true, view: 'audit' });
  const v = c.auditVals(c.state);
  assert.equal(v.auCount, '3');
  assert.equal(v.auLiveSourceLabel, 'Live · svc-operations-intelligence');
  assert.equal(v.auLiveRetryShow, 'none');
  assert.equal(v.auRows.length, 3);
  c.setState({ auFilter: 'Accepted' });
  const folded = c.auditVals(c.state).auRows.map((r) => r.id);
  assert.deepEqual(folded, ['e-13', 'e-12'], 'the Accepted chip folds approved_on_confirmation in client-side');
  c.setState({ auFilter: 'Rejected' });
  assert.deepEqual(c.auditVals(c.state).auRows.map((r) => r.id), ['e-11']);
  const rejected = c.auditVals(c.state).auRows[0];
  assert.equal(rejected.fields.length, 8, 'the 8-field expanded record is intact on live rows');
  assert.equal(rejected.route, 'Riverside Court', 'a rejected row routes to nowhere');
  clearTimeout(c._auLiveRetry);
});

test('a failed read keeps the seed, surfaces the error quietly and arms a retry', async () => {
  globalThis.fetch = () => Promise.reject(new TypeError('fetch failed'));
  const c = new HoistraLogic();
  const seed = c.state.audit;
  await c.auLiveLoad();
  assert.equal(c.state.audit, seed, 'the seed is never eaten by a failure');
  assert.match(c.state.auLiveError, /fetch failed/);
  assert.equal(c.state.auLiveLoading, false);
  assert.ok(c._auLiveRetry, 'a retry timer is armed');
  clearTimeout(c._auLiveRetry);
  const v = c.auditVals(c.state);
  assert.match(v.auLiveSourceLabel, /^Unreachable — /);
  assert.equal(v.auLiveRetryShow, 'inline');
  assert.equal(v.auLiveSourceDot, 'var(--st-risk)');
});

test('an empty-200 body is a malformed answer, not an empty trail — the seed stays', async () => {
  globalThis.fetch = async () => ({ ok: true, status: 200, statusText: 'OK', text: async () => '' });
  const c = new HoistraLogic();
  const seedLen = c.state.audit.length;
  await c.auLiveLoad();
  assert.equal(c.state.audit.length, seedLen);
  assert.match(c.state.auLiveError, /empty response/);
  clearTimeout(c._auLiveRetry);
});

test('a 403 does not arm the retry timer — the caller is not an admin and a timer will not change that', async () => {
  globalThis.fetch = async () => ({
    ok: false, status: 403, statusText: 'Forbidden',
    text: async () => JSON.stringify({ detail: { ok: false, error: 'Admin role required.', reason: 'forbidden', required_role: 'admin', your_role: 'user' } })
  });
  const c = new HoistraLogic();
  await c.auLiveLoad();
  assert.equal(c.state.auLiveError, 'Admin role required.');
  assert.ok(!c._auLiveRetry, 'no timer armed on a role refusal');
});
