// The Home tile when the coverage read ANSWERED but could count nothing — no building graph,
// or no buildings hoisted. renderVals used to gate every Hoist Score field on a non-null
// value, so both of those states fell through to the seed and a brand-new tenant opened Home
// to a fabricated 78% labelled "the operations backend has not answered yet". It had
// answered, and had explained itself. The real controller in Node, with a dead backend.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const DOMAINS = ['assets', 'compliance', 'contracts', 'energy', 'maintenance'];
const NOT_COUNTED = 'the building graph has no rows yet, so nothing is counted against a building';
const answered = (buildings, note) => ({
  ok: true, root: buildings ? 'sites' : 'buildings', buildings: buildings,
  domains: DOMAINS.map((k) => ({ key: k, covered: null, of: buildings, pct: null, missing_buildings: [], missing_count: null, note: note }))
});

let c;
beforeEach(() => { Object.keys(mem).forEach((k) => { delete mem[k]; }); c = new HoistraLogic(); });

test('a graph with no rows shows "—" and the backend\'s reason, not the seed 78', () => {
  c.setState({ signedIn: true, view: 'home', homeRaw: { fetchedAt: '2026-09-23T10:00:00Z', errors: {}, coverage: answered(3, NOT_COUNTED) } });
  const v = c.renderVals();
  assert.equal(v.hoistScore.value, '—');
  assert.equal(v.hoistScore.band, 'Not counted');
  assert.equal(v.hoistBars.length, 4);
  assert.ok(v.hoistBars.every((b) => b.val === '—' && /building graph has no rows/.test(b.note)), JSON.stringify(v.hoistBars));
  assert.match(v.hoistScoreNote, /building graph has no rows/);
});

test('no buildings hoisted shows "—" and says to hoist one', () => {
  c.setState({ signedIn: true, view: 'home', homeRaw: { fetchedAt: '2026-09-23T10:00:00Z', errors: {}, coverage: answered(0, 'no buildings hoisted yet') } });
  const v = c.renderVals();
  assert.equal(v.hoistScore.value, '—');
  assert.equal(v.hoistScore.band, 'Nothing hoisted');
  assert.equal(v.hoistScoreNote, 'No buildings hoisted yet — hoist one and ingest against it');
});

test('a coverage read that did not answer still falls back to the labelled seed', () => {
  c.setState({ signedIn: true, view: 'home', homeRaw: { fetchedAt: '2026-09-23T10:00:00Z', errors: { coverage: 'HTTP 503' }, coverage: null, approvals: { ok: true, count: 0, items: [] } } });
  const v = c.renderVals();
  assert.equal(v.hoistScore.value, '78');
  assert.match(v.hoistScoreNote, /^Seed figures/);
});
