// Resolving the building a person picked to the key it is stored under.
//
// The ingest dropdown lists names, because that is what someone picks from. What gets posted
// has to be the id — the whole point of the change is that the building stops travelling as
// a word for an agent to interpret and starts travelling as a foreign key.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { buildingIdByName } = await import('../src/logic/renderVals.js');

const ROWS = [
  { name: 'Riverside Court', buildingId: 'f12e9629-95ed-5722-80cc-a1397f627850' },
  { name: 'Town Hall', buildingId: '425f5590-404f-5d25-ac68-cac5c9cdebcc' },
  { name: 'Marina Heights', building_id: '8f4a8708-a87c-5887-99b5-865c3a98b511' },
];

test('a name resolves to its id', () => {
  assert.equal(buildingIdByName(ROWS, 'Riverside Court'), 'f12e9629-95ed-5722-80cc-a1397f627850');
});

test('both spellings of the key are read', () => {
  // The live rows use buildingId; a raw API row uses building_id. Reading one and not the
  // other would resolve some buildings and silently fail on others.
  assert.equal(buildingIdByName(ROWS, 'Marina Heights'), '8f4a8708-a87c-5887-99b5-865c3a98b511');
});

test('case and surrounding space do not matter', () => {
  assert.equal(buildingIdByName(ROWS, '  town hall '), '425f5590-404f-5d25-ac68-cac5c9cdebcc');
});

test('an unknown name is null, never a guess', () => {
  // A name with no row behind it means the building is not in the live set — a seed-only
  // name, or a table that has not loaded. Sending nothing is right: the endpoint rejects an
  // unknown id and accepts an absent one, so a wrong guess is the only outcome that files a
  // document against the wrong building.
  assert.equal(buildingIdByName(ROWS, 'Somewhere Else'), null);
  assert.equal(buildingIdByName(ROWS, ''), null);
  assert.equal(buildingIdByName(ROWS, null), null);
  assert.equal(buildingIdByName([], 'Riverside Court'), null);
  assert.equal(buildingIdByName(null, 'Riverside Court'), null);
});

test('a row with a name and no id resolves to null', () => {
  // Seed rows have names and no keys. Returning the name, or an empty string, would post a
  // building_id the endpoint has to reject.
  assert.equal(buildingIdByName([{ name: 'Seed House' }], 'Seed House'), null);
});
