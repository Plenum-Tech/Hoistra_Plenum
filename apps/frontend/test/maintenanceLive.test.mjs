// shapeLiveMaintenance — the Maintenance page shaped from svc-work-order-management's
// /api/maintenance reads. Fixtures are the response shapes the service documents in
// docs/api/assets-and-maintenance-api.md and docs/api/inspection-intelligence-and-ppm-api.md
// (routes/maintenance.py, 15 Sep 2026).
//
// The rules this file exists to hold:
//   - nothing answered is not zero, and an unallocated caller is not an empty queue;
//   - `answerable: false` is not a count of none;
//   - a group with no estimate on any row costs "—", not £0;
//   - the state on a PPM row is the backend's, never re-derived here.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveMaintenance, shapeDecision, money, fmtDay } from '../src/logic/maintenanceLive.js';

const ADMIN = { id: 'u1', email: 'ada@example.com', role: 'admin', all_buildings: true, organization_name: 'Plenum Tech LLC' };
const USER = { id: 'u2', email: 'ur@example.com', role: 'user', all_buildings: false, building_ids: ['b1'] };
const NOBODY = { id: 'u3', email: 'no@example.com', role: 'user', all_buildings: false, building_ids: [] };

const OVERVIEW = { ok: true, cards: {
  decisions_owed: { value: 10, blocked: 2, to_raise: 4, deviating: 2, awaiting_approval: 2, caption: '2 blocked · 4 to raise · 2 deviating' },
  statutory: { value: 3, of_decisions: 10, window_days: 30, caption: 'certificate lapsed or inside 30 days' },
  recommendations_unconverted: { value: 7, answerable: true, now_flagged_by_energy: 5, reports: 12, caption: 'from 12 inspection reports since 2026-03-10' },
  ppm_to_plan: { value: 91.0, done: 134, plan: 148, missed: 7, reports: 127, deferred: 6, year_to_date: true, caption: '134 of 148 visits · 7 missed · 127 reports' }
} };

const DECISIONS = { ok: true, count: 2, total: 10, filtered: false,
  by_state: { Blocked: 2, 'To raise': 4 }, by_source: { Vendors: 2 },
  available: { state: ['Blocked', 'To raise'], source: ['Vendors'], group_by: ['state'] },
  group_by: 'state',
  groups: [{ key: 'Blocked', count: 2, blocked: 2, to_raise: 0, deviating: 0, awaiting_approval: 0,
    estimated_cost: 1020, priced: 1, decisions: [
      { work_order: 'WO-4512', state: 'Blocked', source: 'Vendors', trigger: 'Vendor blocked',
        detail: 'Statutory CP12 due.', asset: 'Boiler-22', building: 'Town Hall',
        vendor: 'Meridian Heating Ltd', estimated_cost: 640, currency: 'GBP', due: '2026-09-20',
        asset_id: 'a1', vendor_id: 'v1', building_id: 'b1',
        statutory_certificate: { name: 'Gas Safe registration', expiry_date: '2026-03-31', matched_on: 'vendor' } },
      { work_order: null, state: 'Blocked', source: 'Energy', trigger: 'Anomaly priced',
        detail: 'No order exists.', asset: 'AHU-3', building: null, vendor: null, estimated_cost: null }
    ] }],
  decisions: [] };

const INTELLIGENCE = { ok: true,
  corpus: { reports: 12, assets: 10, since: '2026-03-10', latest: '2026-08-22' },
  cards: {
    unconverted_recommendations: { answerable: true, count: 7, now_flagged_by_energy: 5,
      headline: '7 recommendations never converted to orders — 5 on assets now flagged by energy, so the inspector saw it first',
      method: 'a recommendation counts as converted if the report names the order' },
    corroborated_anomalies: { answerable: true, count: 6, open_anomalies: 8,
      headline: '6 of 8 open anomalies corroborated by an earlier finding' },
    warranted_findings: { answerable: false, count: null, reason: 'Nothing on record carries a warranty term' },
    poorly_graded: { answerable: true, count: 3, end_of_life: 0, install_year_range: [2004, 2009],
      headline: '3 assets graded poor (4 of 5) by inspectors — all 2004–2009 — none graded end of life' }
  },
  unanswerable: ['warranted_findings'] };

const PPM = { ok: true, source: 'ppm_visits', window: { year_to_date: true },
  summary: { contracts: 2, done: 134, plan: 148, completion_pct: 90.5, missed: 7, late: 9, deferred: 6, reports: 127, behind_plan: 1, watch: 1, to_plan: 0 },
  rule: 'behind plan at 3+ missed visits or under 80% complete',
  contracts: [
    { contract: 'Heating and gas', vendor: 'Meridian Heating Ltd', country_code: 'UK',
      service_scope: 'Boilers, DHW, gas safety', visits_to_plan: { done: 12, plan: 18, plan_is_committed: true },
      missed: 4, late: 2, deferred: 2, reports: 10, reports_to_done: { filed: 10, done: 12 },
      completion_pct: 66.7, next_due: null, buildings: 3, state: 'behind plan' },
    { contract: 'Lifts · portfolio UK', vendor: 'Apex Lifts', country_code: 'UK',
      service_scope: 'LOLER and monthly service', visits_to_plan: { done: 24, plan: 24, plan_is_committed: false },
      missed: 0, late: 0, deferred: 0, reports: 24, reports_to_done: { filed: 24, done: 24 },
      completion_pct: 100, next_due: '2026-09-16', buildings: 8, state: 'to plan' }
  ] };

const full = (acct) => shapeLiveMaintenance({
  overview: OVERVIEW, decisions: DECISIONS, intelligence: INTELLIGENCE, ppm: PPM,
  lastRead: { ok: true, last_read: { started_at: '2026-09-15T02:14:07+00:00', reports_read: 12 } },
  chips: { ok: true, suggestions: [{ id: 'statutory_decisions', question: 'Which decisions are statutory?' }] },
  errors: {}
}, acct || ADMIN);

test('with nothing loaded, nothing is asserted', () => {
  const m = shapeLiveMaintenance(null, ADMIN);
  assert.equal(m.live, false);
  assert.deepEqual(m.decisions, []);
  assert.deepEqual(m.ppm, []);
  assert.equal(m.total, null);
  assert.equal(m.lastRead, null);
  m.cards.forEach((c) => assert.equal(c.v, null, c.l + ' is null, not 0'));
});

test('the four cards are the backend values and its own captions', () => {
  const m = full();
  assert.equal(m.cards[0].v, 10);
  assert.equal(m.cards[0].s, '2 blocked · 4 to raise · 2 deviating');
  assert.equal(m.cards[1].v, 3);
  assert.equal(m.cards[3].v, 91);
  assert.equal(m.cards[3].unit, '%');
});

test('a decision names the certificate that makes it statutory, and how it matched', () => {
  const d = shapeDecision(DECISIONS.groups[0].decisions[0]);
  assert.equal(d.id, 'WO-4512');
  assert.equal(d.state, 'Blocked');
  assert.equal(d.est, '£640');
  assert.equal(d.due, '20 Sep 2026');
  assert.equal(d.statutory, true);
  assert.match(d.statutoryNote, /Gas Safe registration/);
  assert.match(d.statutoryNote, /matched on vendor/);
});

test('a decision that does not exist yet has no work order, and no estimate is not a free one', () => {
  const d = shapeDecision(DECISIONS.groups[0].decisions[1]);
  assert.equal(d.id, null, 'To raise means no order exists');
  assert.equal(d.est, '—');
  assert.equal(d.estimated, null);
  assert.equal(d.b, '—');
  assert.equal(d.statutory, false);
});

test('a group carries the backend total and how much of it is priced', () => {
  const g = full().groups[0];
  assert.equal(g.name, 'Blocked');
  assert.equal(g.n, 2);
  assert.equal(g.blocked, 2);
  assert.equal(g.total, '~£1k');
  assert.equal(g.priced, 1, 'one of the two rows carries an estimate');
});

test('a group nothing in it is priced costs "—", not nothing', () => {
  const m = shapeLiveMaintenance({ decisions: Object.assign({}, DECISIONS, {
    groups: [Object.assign({}, DECISIONS.groups[0], { estimated_cost: null, priced: 0 })]
  }) }, ADMIN);
  assert.equal(m.groups[0].total, '—');
  assert.equal(money(null), '—');
  assert.equal(money(0), '£0', 'a real zero is still a zero');
});

test('answerable:false is a reason, never a count of none', () => {
  const cards = full().intelligence;
  const warr = cards.find((c) => c.key === 'warranted_findings');
  assert.equal(warr.answerable, false);
  assert.equal(warr.n, null, 'not 0 — "we checked and there are none" is a different claim');
  assert.match(warr.s, /Nothing on record carries a warranty term/);
  assert.deepEqual(full().unanswerable, ['warranted_findings']);
});

test('the corroborated card counts against the anomalies open, and the sub-line is what the headline adds', () => {
  const cards = full().intelligence;
  assert.equal(cards.find((c) => c.key === 'corroborated_anomalies').n, '6 of 8');
  const rec = cards.find((c) => c.key === 'unconverted_recommendations');
  assert.equal(rec.n, '7');
  assert.match(rec.s, /5 on assets now flagged by energy/);
  assert.ok(!/^7 recommendations/.test(rec.s), 'the count is not repeated into its own sub-line');
});

test('the PPM state is the backend rule, not a second one computed here', () => {
  const m = full();
  assert.equal(m.ppm.length, 2);
  assert.equal(m.ppm[0].state, 'behind plan');
  assert.equal(m.ppm[0].done, '12 / 18');
  assert.equal(m.ppm[0].pct, '67%');
  assert.equal(m.ppm[0].planCommitted, true);
  assert.match(m.ppm[0].planNote, /committed plan/);
  assert.match(m.ppm[0].deferrals, /2 deferred/);
  assert.equal(m.ppm[0].next, 'blocked', 'behind plan with misses and nothing booked');
  assert.equal(m.ppm[1].state, 'to plan');
  assert.equal(m.ppm[1].next, '16 Sep 2026');
  assert.match(m.ppm[1].planNote, /visits booked/, 'an uncommitted denominator says so');
});

test('the scope line names who the records belong to', () => {
  assert.match(full(ADMIN).scope, /Every building in Plenum Tech LLC/);
  assert.equal(full(ADMIN).admin, true);
  assert.match(full(USER).scope, /Your buildings only/);
  assert.equal(full(USER).admin, false);
  const none = full(NOBODY);
  assert.equal(none.unallocated, true);
  assert.match(none.scope, /allocated to no buildings/);
});

test('a superadmin is treated as an admin for the scope line', () => {
  const m = full({ id: 'u9', email: 's@x.y', role: 'superadmin', all_buildings: true, organization_name: 'Plenum Tech LLC' });
  assert.equal(m.admin, true);
  assert.match(m.scope, /Every building/);
});

test('the last read is null before the first one, and that is not zero reports', () => {
  const m = shapeLiveMaintenance({ overview: OVERVIEW, lastRead: { ok: true, last_read: null } }, ADMIN);
  assert.equal(m.lastRead, null);
  assert.equal(full().lastRead.reports_read, 12);
});

test('the chips are the server\'s, so a new skill needs no frontend release', () => {
  assert.deepEqual(full().chips.map((c) => c.question), ['Which decisions are statutory?']);
  assert.deepEqual(shapeLiveMaintenance({ overview: OVERVIEW }, ADMIN).chips, []);
});

test('a calendar date is read as a calendar date, so a UTC midnight never slips a day', () => {
  assert.equal(fmtDay('2026-03-10'), '10 Mar 2026');
  assert.equal(fmtDay('2026-03-10T00:00:00Z'), '10 Mar 2026');
  assert.equal(fmtDay(null), null);
});

test('one read answering is enough to go live — the rest stay null rather than zero', () => {
  const m = shapeLiveMaintenance({ ppm: PPM, errors: { overview: 'boom' } }, ADMIN);
  assert.equal(m.live, true);
  assert.equal(m.ppm.length, 2);
  assert.equal(m.total, null, 'the decisions read failed, so there is no count');
  m.cards.forEach((c) => assert.equal(c.v, null));
});

// ── whose company these figures are ──────────────────────────────────────────
// svc-work-order-management returns every row to a superadmin (services/principal.py:182)
// and takes no acting-company parameter, so "view as company" does not reach it. Two
// companies therefore show identical maintenance data, and the page has to say so — a
// figure captioned with one company's name that is really every company's is worse than
// no caption.

const SUPER = { id: 'u9', email: 's@x.y', role: 'superadmin', all_buildings: true, organization_name: 'Plenum Tech LLC' };

test('a superadmin who has chosen no company is told these are platform totals', () => {
  const m = full(SUPER);   // no acting company
  assert.equal(m.superadmin, true);
  assert.equal(m.crossCompany, true);
  assert.match(m.scope, /Every building in every company/);
});

test('acting as a company narrows the reads, so the page names that company and drops the warning', () => {
  const acting = shapeLiveMaintenance({ overview: OVERVIEW, decisions: DECISIONS, errors: {} }, SUPER, 'TechCorp Facilities LLC');
  assert.equal(acting.actingName, 'TechCorp Facilities LLC');
  assert.equal(acting.crossCompany, false, 'organization_id goes on every read now');
  assert.match(acting.scope, /Every building in TechCorp Facilities LLC/);
  assert.ok(!/every company/.test(acting.scope));
});

test('an admin is scoped to their own company, and is not warned about a boundary they have', () => {
  const m = full(ADMIN);
  assert.equal(m.crossCompany, false);
  assert.match(m.scope, /Every building in Plenum Tech LLC/);
  assert.ok(!/every company/.test(m.scope));
});

test('a user allocated to nothing is not told they read across companies', () => {
  const m = shapeLiveMaintenance({ overview: OVERVIEW, errors: {} },
    { id: 'u8', email: 'n@x.y', role: 'superadmin', all_buildings: false, building_ids: [] }, 'TechCorp');
  assert.equal(m.unallocated, true);
  assert.equal(m.crossCompany, false, 'an empty allocation beats the superadmin read-across');
  assert.match(m.scope, /allocated to no buildings/);
});
