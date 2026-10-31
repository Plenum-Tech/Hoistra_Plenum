// energyAnomalyCostIsNotAdded — the Anomaly cost card stops adding the same energy twice.
//
// Two rules fire on one meter in one window and each annualises the excess it sees, but they
// read the SAME consumption from different angles. An overnight floor that has drifted up
// shows as baseline drift, as a non-occupancy spike and as a weekend spike, and every hour the
// weekend rule can fire on is an hour the non-occupancy rule already covers. The card added
// all of them.
//
// Measured on hoistra_test on 23 Sep 2026: Harbour Point's five priced electricity findings
// summed to GBP371,465 against a supply that used GBP359,279 of electricity all year — the
// page reported more waste than the meter consumed. Across the portfolio the card read
// GBP3.27M against two buildings whose combined annual energy bill is about GBP640k.
//
// The API had the answer already: engines/energy/anomaly_rollup.py, method
// "rule:largest-single-finding/v1" — a meter contributes its largest single finding, a
// building its largest meter (a parent and a sub-meter on one supply are the same mistake a
// level up), and buildings add because they are genuinely separate supplies. The page now
// applies the same rule, and these tests hold it to the figures the engine produces.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';

// The helper is a module-private const, so it is read out of the source rather than imported.
// That also pins it to the file: if the rule is deleted or renamed, this stops compiling.
const src = fs.readFileSync(new URL('../src/logic/energy.js', import.meta.url), 'utf8');
const impactNum = (a) => (typeof a.impactN === 'number' ? a.impactN : 0);
const body = src.slice(src.indexOf('const anomalyTotal ='));
const anomalyTotal = new Function('impactNum',
  body.slice(0, body.indexOf('\n};') + 3) + '\nreturn anomalyTotal;')(impactNum);

const HP = 'b-101', AG = 'b-102';
const HPE = 'hp-elec', HPG = 'hp-gas', AGE = 'ag-elec', AGG = 'ag-gas';

// The twenty open findings on hoistra_test after the recurring ones were collapsed.
const FINDINGS = [
  { buildingUuid: HP, meterId: HPE, impactN: 119175 },   // non-occupancy spike, the largest
  { buildingUuid: HP, meterId: HPE, impactN: 101725 },   // post-works regression
  { buildingUuid: HP, meterId: HPE, impactN: 101316 },   // baseline drift
  { buildingUuid: HP, meterId: HPE, impactN: 44472 },    // weekend spike — inside non-occupancy
  { buildingUuid: HP, meterId: HPE, impactN: 4776 },
  { buildingUuid: HP, meterId: HPE, impactN: 0 },
  { buildingUuid: HP, meterId: HPE, impactN: 0 },
  { buildingUuid: HP, meterId: HPG, impactN: 7803 },
  { buildingUuid: HP, meterId: HPG, impactN: 7630 },
  { buildingUuid: HP, meterId: HPG, impactN: 1 },
  { buildingUuid: AG, meterId: AGE, impactN: 50738 },
  { buildingUuid: AG, meterId: AGE, impactN: 43762 },
  { buildingUuid: AG, meterId: AGE, impactN: 42011 },
  { buildingUuid: AG, meterId: AGE, impactN: 19432 },
  { buildingUuid: AG, meterId: AGE, impactN: 2046 },
  { buildingUuid: AG, meterId: AGE, impactN: 0 },
  { buildingUuid: AG, meterId: AGE, impactN: 0 },
  { buildingUuid: AG, meterId: AGG, impactN: 19860 },
  { buildingUuid: AG, meterId: AGG, impactN: 18273 },
  { buildingUuid: AG, meterId: AGG, impactN: 2 },
];

test('the portfolio total matches what the API rollup computes', () => {
  // anomaly_rollup.py returns GBP169,914 for exactly this data; it rounds, the page does not.
  assert.equal(Math.round(anomalyTotal(FINDINGS)), 169913);
});

test('adding every finding is what produced the impossible number', () => {
  const added = FINDINGS.reduce((q, a) => q + impactNum(a), 0);
  assert.equal(added, 583022);
  assert.ok(added > anomalyTotal(FINDINGS) * 3, 'adding overstates by more than threefold here');
});

test('a building reports its largest meter, not its meters added', () => {
  const hp = FINDINGS.filter((f) => f.buildingUuid === HP);
  assert.equal(anomalyTotal(hp), 119175, 'the electricity headline, with gas not added on top');
});

test('a meter reports its largest finding, not its findings added', () => {
  const hpe = FINDINGS.filter((f) => f.meterId === HPE);
  assert.equal(anomalyTotal(hpe), 119175);
});

test('buildings do add, being genuinely separate supplies', () => {
  assert.equal(anomalyTotal(FINDINGS), 119175 + 50738);
});

test('a total can never exceed what the worst supply is claimed to waste', () => {
  const worst = Math.max(...FINDINGS.map(impactNum));
  assert.ok(anomalyTotal(FINDINGS) >= worst, 'the largest finding is always represented');
});

test('unpriced findings are left out rather than counted as zero', () => {
  const priced = FINDINGS.filter((f) => f.impactN > 0);
  assert.equal(anomalyTotal(FINDINGS), anomalyTotal(priced));
  assert.equal(anomalyTotal([{ buildingUuid: HP, meterId: HPE, impactN: 0 }]), 0);
});

test('a finding with no meter is its own group, not folded into a metered one', () => {
  // "Whole building" findings carry no meter. Collapsing them into a meter's group would hide
  // one behind the other; they are separate claims about separate things.
  const rows = [
    { buildingUuid: HP, meterId: HPE, impactN: 1000 },
    { buildingUuid: HP, meterId: null, impactN: 4000 },
  ];
  assert.equal(anomalyTotal(rows), 4000, 'the larger of the two groups on that building');
});

test('nothing at all totals nothing', () => {
  assert.equal(anomalyTotal([]), 0);
  assert.equal(anomalyTotal(null), 0);
  assert.equal(anomalyTotal(undefined), 0);
});

test('the page names the rule the API uses, so the two cannot drift silently', () => {
  assert.ok(src.includes('rule:largest-single-finding/v1'),
    'the comment must cite anomaly_rollup.py by method name');
  assert.ok(!/anoms\.reduce\(\(q, a\) => q \+ impactNum\(a\), 0\)/.test(src),
    'no naive sum of findings may remain');
});
