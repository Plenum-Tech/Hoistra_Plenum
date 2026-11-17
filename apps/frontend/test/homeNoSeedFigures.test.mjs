// Home never shows sample figures as the company's own (Hussain, 17 Nov 2026, from the UI audit:
// with the backend down Home showed Hoist Score 78, £390k saved and £970k detected under a small
// "Seed figures" caption). Before the reads answer every figure is "—", and the note says
// whether it is reading, could not read (in plain words), or has not answered. The tiles keep
// their shape: heads, modules and bars stay, with dashes for values.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => { c = new HoistraLogic({}); c.setState({ signedIn: true, view: 'home' }); });
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

test('no sample money, score or portfolio before the reads answer', () => {
  const v = c.renderVals();
  assert.equal(v.pnlSaved, '—');
  assert.equal(v.pvTotal, '—');
  assert.equal(v.hoistScore.value, '—');
  assert.notEqual(v.hoistScore.band, 'Supervised autonomy');
  assert.ok(v.hoistBars.every((b) => b.val === '—'), 'bars keep their labels, not their sample percentages');
  assert.ok(v.pnlTop.every((r) => r.budget === '—' && r.actual === '—'), 'P&L heads stay, figures are dashes');
  assert.ok(v.pvRows.every((r) => r.detected === '—'), 'value modules stay, figures are dashes');
  assert.deepEqual(v.pvLedger, []);
  const hero = v.heroStats.join(' ');
  assert.doesNotMatch(hero, /24 buildings|1\.84m|Singapore/, 'no sample portfolio line');
  for (const note of [v.pvNote, v.pnlNote, v.hoistScoreNote]) {
    assert.doesNotMatch(note, /Seed figures/);
    assert.match(note, /has not answered yet/);
  }
  cleanup();
});

test('reading, then could-not-read in plain words with no HTTP code', () => {
  c.setState({ homeLoading: true });
  assert.match(c.renderVals().pnlNote, /^Reading/);
  c.setState({ homeLoading: false, homeError: 'HTTP 503' });
  const v = c.renderVals();
  for (const note of [v.pvNote, v.pnlNote, v.hoistScoreNote]) {
    assert.match(note, /could not be read — the server can't be reached right now/);
    assert.doesNotMatch(note, /503/);
  }
  assert.equal(v.pvTotal, '—');
  cleanup();
});
