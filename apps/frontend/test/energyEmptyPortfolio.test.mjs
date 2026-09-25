// The Energy page reads the database or it reads nothing.
//
// It used to fall back to a bundled nine-building portfolio (constants.js BUILDINGS, with
// hoistway-data.js anomalies) whenever the live read had not succeeded. An empty database
// therefore rendered a full page — 9 buildings, an area-weighted EUI, a six-figure cost
// above benchmark — which is indistinguishable from real data and was read as exactly that
// after the database had been deliberately emptied. Worse, the drawer then asked the API
// about B-001, an id no database has ever held.
//
// These tests pin the replacement: with nothing loaded the page reports nothing, and every
// figure it does show comes from a row.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: {
    getItem: (k) => (k in mem ? mem[k] : null),
    setItem: (k, v) => { mem[k] = String(v); },
    removeItem: (k) => { delete mem[k]; }
  }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async () => { throw new TypeError('Failed to fetch: the backend is not answering'); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', role: 'user' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); };

test('an empty database reports an empty portfolio, not a demo one', () => {
  const v = c.enVals(c.state);
  const card = (label) => v.enScopeCards.find((x) => x.l === label);

  assert.equal(card('Buildings in scope').v, '0',
    'nine bundled buildings used to appear here against an empty database');
  assert.equal(card('EUI, area weighted').v, '—',
    'an EUI with no readings behind it is a dash, never a number');
  assert.equal(card('EUI, area weighted').s,
    'no EUI reading on record for any building in scope yet');
  cleanup();
});

test('the building list says it is empty rather than listing buildings nobody ingested', () => {
  const v = c.enBuildingVals(c.state);
  assert.equal(v.enListEmpty, 'block');
  assert.match(v.enListSummary, /^0 of 0 buildings/);
  cleanup();
});

test('the market profile shows a dash per cell, not a sample value', () => {
  const v = c.enVals(c.state);
  const cells = v.enMatrixRows.flatMap((r) => r.cells);
  assert.ok(cells.length > 0, 'the table keeps its rows — the row labels are structure');
  assert.ok(cells.every((x) => x.v === '—'),
    'every value must come from the engine; a constant standing in for a measurement is the bug');
  assert.ok(cells.every((x) => x.note === 'not measured in this deployment yet'));
  cleanup();
});

test('figures come from the rows that are loaded, and move when they do', () => {
  // Two buildings, one measured against its reference and one with no reading at all. The
  // headline must count both as in scope and weight the EUI only by the one that has a
  // reading — a building with no meter is not evidence of good performance.
  // bldData() returns state.bldLive itself — the rows ARE the live flag, so an unloaded
  // page and an empty portfolio are the same thing to every reader downstream.
  c.setState({
    bldLive: [
      { id: 'b1', buildingId: 'b1', name: 'Harbour Point', cc: 'UK', euiN: 200, benchN: 100, areaM2: 1000 },
      { id: 'b2', buildingId: 'b2', name: 'Ashgrove Court', cc: 'UK', areaM2: 500 }
    ]
  });
  const v = c.enVals(c.state);
  const card = (label) => v.enScopeCards.find((x) => x.l === label);
  assert.equal(card('Buildings in scope').v, '2');
  assert.equal(card('EUI, area weighted').v, '200', 'weighted over measured area only');
  cleanup();
});
