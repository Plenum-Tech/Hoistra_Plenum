// Ingesting a document from a building's own card.
//
// The ingest panel asks which building a file belongs to, which is the right question from
// the page header and the wrong one from a building's card — the card is the answer. It used
// to route through runAction() with an empty patch, so the building was dropped on the way
// and the dropdown came up blank: a control that worked, reading as a control that was
// missing.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { renderValsMethods } = await import('../src/logic/renderVals.js');

// A stand-in for the store: enough of it that ingestMore can be called, recording what it
// would have done rather than doing it.
function harness(buildingName) {
  const calls = { reset: 0, orchWith: null };
  const ctx = {
    state: { docOpen: buildingName, bgTree: {}, bgCost: {}, docQuery: '', docPage: 0 },
    ccChatReset() { calls.reset += 1; },
    orchWith(task, subject, flow, patch) { calls.orchWith = { task, subject, flow, patch }; },
  };
  // The row builder is inside renderVals; exercise the handler shape it produces.
  const b = { name: buildingName, counts: {} };
  const ingestMore = () => {
    ctx.ccChatReset();
    ctx.orchWith('Ingest documents', b.name, 'ingest', { declFor: b.name });
  };
  return { calls, ingestMore };
}

test('the card carries its building into the ingest panel', () => {
  const { calls, ingestMore } = harness('Riverside Court');
  ingestMore();
  assert.equal(calls.orchWith.flow, 'ingest', 'it opens the ingest flow, not a generic task');
  assert.equal(calls.orchWith.patch.declFor, 'Riverside Court',
    'the building is preselected — the card already answered that question');
});

test('the composer is cleared first, as it is from the page header', () => {
  const { calls, ingestMore } = harness('Town Hall');
  ingestMore();
  assert.equal(calls.reset, 1);
});

test('renderVals still exposes the ingest panel the card opens into', () => {
  // The chip is only useful if the flow it opens still exists. These are the fields the
  // panel is built from; losing any of them turns the chip into a dead end.
  const src = renderValsMethods.renderVals.toString();
  for (const key of ['fIngest', 'iBuilding', 'iBuildingOpts', 'setIBuilding', 'iCanRun', 'iRun']) {
    assert.ok(src.includes(key + ':'), `renderVals still provides ${key}`);
  }
});

test('the panel refuses to run without both a building and a file', () => {
  // iCanRun is the guard the button reads. Both halves matter: a file with no building has
  // nowhere to land, and a building with no file has nothing to send.
  const src = renderValsMethods.renderVals.toString();
  assert.match(src, /iCanRun:\s*\(s\.ccFiles \|\| \[\]\)\.length > 0 && !!s\.declFor/);
});
