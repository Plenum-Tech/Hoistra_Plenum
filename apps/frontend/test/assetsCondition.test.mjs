// assetsCondition — the Assets page's condition rule and anomaly attribution, on real data.
// The page this replaced computed the same screen from a hardcoded fixture, so it rendered
// nine buildings and nineteen assets for every user of every tenant. Everything here is
// derived from rows a backend returned for the caller.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { seedOrder, conditionOf, anomaliesByAsset, anomalyAgeDays, NOT_METERED, NO_SECTION_LINK, NOT_COMPUTABLE, UNGRADED, RULE_NOT_MODEL } from '../src/logic/assetsCondition.js';

const DAY = 86400000;
const base = { deviation: 0, pct: 10, anomaly: null, anomalyDays: null, weeks: 3, healthScore: 90 };

test('the energy rule: building over reference AND an anomaly on the asset is a threat', () => {
  const r = conditionOf({ ...base, deviation: 19, anomaly: { id: 'a' }, anomalyDays: 5 });
  assert.equal(r.cond, 'threat');
  assert.equal(r.kind, 'threat');
});

test('over reference alone is a watch, and an anomaly alone is a watch only once it persists', () => {
  assert.equal(conditionOf({ ...base, deviation: 19 }).cond, 'watch');
  assert.equal(conditionOf({ ...base, anomaly: { id: 'a' }, anomalyDays: 6 }).cond, 'ok', 'under the persistence threshold');
  assert.equal(conditionOf({ ...base, anomaly: { id: 'a' }, anomalyDays: 21 }).cond, 'watch', '3 weeks meets it');
  assert.equal(conditionOf({ ...base, anomaly: { id: 'a' }, anomalyDays: 21 }).kind, 'persist');
});

test('a building inside the threshold with no anomaly is in control', () => {
  assert.equal(conditionOf({ ...base, deviation: 4 }).cond, 'ok');
});

test('a building with no EUI on record cannot trip the energy rule', () => {
  const r = conditionOf({ ...base, deviation: null, anomaly: { id: 'a' }, anomalyDays: 2 });
  assert.equal(r.over, false);
  assert.equal(r.cond, 'ok', 'no reference to be over');
});

test('health score raises a band but never lowers one', () => {
  // A poor score threatens an asset the energy rule cleared.
  assert.equal(conditionOf({ ...base, healthScore: 20 }).cond, 'threat');
  assert.equal(conditionOf({ ...base, healthScore: 55 }).cond, 'watch');
  // A good score does not rescue an asset the energy rule flagged.
  assert.equal(conditionOf({ ...base, deviation: 19, anomaly: { id: 'a' }, anomalyDays: 1, healthScore: 100 }).cond, 'threat');
  assert.equal(conditionOf({ ...base, deviation: 19, healthScore: 100 }).cond, 'watch');
});

test('an unscored asset is marked unscored and is not treated as healthy', () => {
  const r = conditionOf({ ...base, healthScore: null });
  assert.equal(r.unscored, true);
  assert.equal(r.cond, 'ok', 'no signal either way — but the page labels it Not scored');
});

// energy_anomalies.asset_id is set by svc-operations-intelligence from meters.asset_id, so
// it is populated only where a meter names an asset. A row with no asset_id is dropped
// rather than spread across the building's assets, which would invent an attribution.
test('anomaliesByAsset keys only rows that name an asset, and drops resolved ones', () => {
  const by = anomaliesByAsset([
    { id: '1', asset_id: 'a1', status: 'open', detected_at: '2026-09-01T00:00:00Z' },
    { id: '2', asset_id: null, status: 'open', detected_at: '2026-09-02T00:00:00Z' },
    { id: '3', asset_id: 'a1', status: 'resolved', detected_at: '2026-09-03T00:00:00Z' },
    { id: '4', asset_id: 'a2', status: 'open', detected_at: '2026-09-04T00:00:00Z' }
  ]);
  assert.deepEqual(Object.keys(by).sort(), ['a1', 'a2']);
  assert.equal(by.a1.length, 1, 'the resolved row is not attributed');
  assert.equal(by.a1[0].id, '1');
});

test('anomaliesByAsset on nothing returns nothing, rather than throwing', () => {
  assert.deepEqual(anomaliesByAsset(null), {});
  assert.deepEqual(anomaliesByAsset([]), {});
});

test('anomalyAgeDays counts from detected_at, and is null when there is no date to count from', () => {
  const now = Date.parse('2026-09-15T00:00:00Z');
  assert.equal(anomalyAgeDays({ detected_at: '2026-09-01T00:00:00Z' }, now), 14);
  assert.equal(anomalyAgeDays({ detected_at: null }, now), null);
  assert.equal(anomalyAgeDays({}, now), null);
  assert.equal(anomalyAgeDays({ detected_at: 'not a date' }, now), null);
});

// What is still unknown must say what it is, and must never read as a zero or a pass.
// "not metered" is not "consumed nothing"; "ungraded" is not "in band"; "not computable"
// is not "worth nothing"; and a named rule is not a fitted model.
test('every unknown-state message names the claim rather than showing a bare dash', () => {
  [NOT_METERED, NO_SECTION_LINK, NOT_COMPUTABLE, UNGRADED, RULE_NOT_MODEL].forEach((m) => {
    assert.equal(typeof m, 'string');
    assert.ok(m.length > 10, 'the message explains rather than showing a dash alone');
  });
  assert.match(NOT_METERED, /not the same as no consumption/);
  assert.match(UNGRADED, /no band defined/i);
  assert.match(RULE_NOT_MODEL, /not a fitted model/);
});

// Which assets are worth a per-asset intelligence call. Seeding only the flagged ones left
// the instrumented block empty on a portfolio that is entirely under reference — the common
// case, and the one where a person still wants to see what their plant is doing. Flagged
// first, then the ones a person would look at anyway: most critical, then worst scored.
test('seedOrder puts flagged assets first', () => {
  const rows = [
    { cond: 'ok', a: { asset_id: 'ok1', criticality: 'Low', health_score: 95 } },
    { cond: 'threat', a: { asset_id: 't1', criticality: 'Low', health_score: 95 } },
    { cond: 'watch', a: { asset_id: 'w1', criticality: 'Low', health_score: 95 } }
  ];
  assert.deepEqual(seedOrder(rows, 3), ['t1', 'w1', 'ok1']);
});

test('seedOrder falls back to criticality then health when nothing is flagged', () => {
  const rows = [
    { cond: 'ok', a: { asset_id: 'low', criticality: 'Low', health_score: 50 } },
    { cond: 'ok', a: { asset_id: 'high-worse', criticality: 'High', health_score: 60 } },
    { cond: 'ok', a: { asset_id: 'high-better', criticality: 'High', health_score: 90 } },
    { cond: 'ok', a: { asset_id: 'med', criticality: 'Medium', health_score: 10 } }
  ];
  assert.deepEqual(seedOrder(rows, 4), ['high-worse', 'high-better', 'med', 'low']);
});

test('seedOrder is capped, so a big portfolio is not one call per asset', () => {
  const rows = Array.from({ length: 200 }, (_, i) => ({ cond: 'ok', a: { asset_id: 'a' + i, criticality: null, health_score: 80 } }));
  assert.equal(seedOrder(rows, 8).length, 8);
});

test('seedOrder treats an unscored asset as unknown, not as worst', () => {
  const rows = [
    { cond: 'ok', a: { asset_id: 'unscored', criticality: 'Low', health_score: null } },
    { cond: 'ok', a: { asset_id: 'bad', criticality: 'Low', health_score: 20 } }
  ];
  assert.deepEqual(seedOrder(rows, 2), ['bad', 'unscored']);
});

// ── The band now comes from GET /api/energy/condition/assets ────────────────────────────
// This page used to decide Threat / Watch / In control itself, from the BUILDING's deviation.
// Measured against condition_engine.assess() over hoistra_test on 15 Sep 2026, the two agreed
// on 44 of 54 assets and every one of the ten disagreements ran the same way: this page banded
// LOWER than the server — two Threats shown as Watch, eight Watches shown as In control. The
// cause was the figure, not the rule: no building on that data was over its reference (as low
// as -51.3%) while ten assets sat in sections over by 18-46%. A building average hides an
// over-consuming plant room among the floors around it.
import { bandFromServer } from '../src/logic/assetsCondition.js';

const svRow = (band, reasons) => ({ asset_id: 'a1', band, reasons: reasons || [] });

test('the server decides the band, and its reasons come through as given', () => {
  const r = bandFromServer(svRow('threat', ['section_over_reference', 'anomaly_attributed']), 90);
  assert.equal(r.cond, 'threat');
  assert.equal(r.over, true);
  assert.deepEqual(r.reasons, ['section_over_reference', 'anomaly_attributed']);
});

test('in_control maps to the page vocabulary rather than being passed through raw', () => {
  assert.equal(bandFromServer(svRow('in_control', []), 90).cond, 'ok');
});

test('a Watch the server reached on a persistent anomaly is marked as that, not as a zone', () => {
  assert.equal(bandFromServer(svRow('watch', ['anomaly_persistent']), 90).kind, 'persist');
  assert.equal(bandFromServer(svRow('watch', ['section_over_reference']), 90).kind, 'zone');
});

// The exact case the old rule got wrong: section over, building under. The server sees the
// section; the page no longer gets a say in it.
test('an asset whose section is over reference is banded on that, whatever the building reads', () => {
  const r = bandFromServer(svRow('watch', ['section_over_reference']), 90);
  assert.equal(r.cond, 'watch', 'the building being 51% UNDER its reference cannot clear this');
});

test('health score still raises a band the server set, and still never lowers one', () => {
  assert.equal(bandFromServer(svRow('in_control', []), 20).cond, 'threat');
  assert.equal(bandFromServer(svRow('in_control', []), 55).cond, 'watch');
  assert.equal(bandFromServer(svRow('threat', ['section_over_reference', 'anomaly_attributed']), 100).cond,
    'threat', 'a perfect score does not rescue an asset the server flagged');
  assert.equal(bandFromServer(svRow('watch', ['section_over_reference']), 100).cond, 'watch');
});

test('an unscored asset is still marked unscored, and is not treated as healthy', () => {
  const r = bandFromServer(svRow('in_control', []), null);
  assert.equal(r.unscored, true);
  assert.equal(r.cond, 'ok');
});

// When the condition read fails, soft() hands back {__err}, the list is empty and no asset has
// a row. The page must fall back to its own rule rather than banding everything In control —
// which is what returning a default here would have done.
test('no server row means no server band, so the page falls back instead of inventing one', () => {
  assert.equal(bandFromServer(null, 90), null);
  assert.equal(bandFromServer(undefined, 90), null);
  assert.equal(bandFromServer({ asset_id: 'a1', band: 'nonsense' }, 90), null);
});
