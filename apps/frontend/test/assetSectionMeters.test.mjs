// A floor section on the Assets page opened onto nothing: floors hold no assets (the plant sits
// in the zones), and the section's own two sub-meters were never shown. sectionMeterRows is
// what the section now lists when opened.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { sectionMeterRows } from '../src/logic/assetsCondition.js';

const level1 = [
  { fuel: 'gas', supply: 'PT-B-301-G-L01', kwh: 12403.2, share_pct: 4.4, cost: 880.6, open_anomalies: 0 },
  { fuel: 'electricity', supply: 'PT-B-301-E-L01', kwh: 81844.9, share_pct: 4.1, cost: 23244, open_anomalies: 1 }
];

test('a floor with no assets lists its electricity and gas meters and says why', () => {
  const r = sectionMeterRows(level1, 0, 90);
  assert.equal(r.metersShow, 'flex');
  assert.deepEqual(r.meterRows.map((m) => m.name), ['Electricity sub-meter', 'Gas sub-meter']);
  const e = r.meterRows[0];
  assert.equal(e.ref, 'PT-B-301-E-L01');
  assert.equal(e.kwh, '81,845 kWh · last 90 days');
  assert.equal(e.share, "4.1% of the building's electricity");
  assert.equal(e.cost, '£23,244');
  assert.equal(e.anoms, '1 open anomaly');
  assert.match(r.emptyNote, /No assets are placed in this section/);
});

test('a zone with assets keeps its rows and adds no note; nothing on record says so', () => {
  assert.equal(sectionMeterRows(level1, 3, 90).emptyNote, '');
  const none = sectionMeterRows(undefined, 0, 90);
  assert.equal(none.metersShow, 'none');
  assert.match(none.emptyNote, /no sub-meter of its own/);
});
