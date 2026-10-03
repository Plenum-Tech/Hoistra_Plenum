// A failed anomaly read is not an anomaly-free portfolio.
//
// energyLive.js stores the error in enError and, until 28 Sep 2026, nothing rendered it: a
// 502 from GET /api/energy/anomalies read "£0 · 0 anomalies" on the tile and "No open anomalies"
// under every building — the same words the page uses for a building that is genuinely clean.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async () => { throw new TypeError('Failed to fetch'); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', role: 'user' });
});
const tile = (v, label) => v.enScopeCards.find((x) => x.l === label);

test('a failed anomaly read shows a dash and says why, not £0', () => {
  c.setState({ enAnomLive: null, enError: '502 Bad Gateway' });
  const v = c.enVals(c.state);
  const t = tile(v, 'Anomaly cost / year');
  assert.equal(t.v, '—');
  assert.match(t.s, /could not be read/);
  assert.equal(v.enReadFailShow, 'flex');
  assert.match(v.enReadFailText, /502 Bad Gateway/);
});

test('an answered read with no anomalies still says £0 and shows no failure notice', () => {
  c.setState({ enAnomLive: [], enMetersLive: [], enError: '' });
  const v = c.enVals(c.state);
  assert.notEqual(tile(v, 'Anomaly cost / year').v, '—');
  assert.equal(v.enReadFailShow, 'none');
});

test('a refresh that fails after a good read keeps the anomalies it has and says the refresh failed', () => {
  c.setState({ enAnomLive: [], enMetersLive: [], enError: 'timeout' });
  const v = c.enVals(c.state);
  assert.notEqual(tile(v, 'Anomaly cost / year').v, '—', 'the last good read still stands');
  assert.equal(v.enReadFailShow, 'flex');
  assert.match(v.enReadFailText, /refresh/);
});
