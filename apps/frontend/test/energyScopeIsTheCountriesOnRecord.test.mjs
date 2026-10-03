// The markets in scope are the ones the buildings are in, not the ones a pack exists for.
//
// enScope used to answer with the four packs the platform holds, whatever was on record. Two
// buildings, both in the United Kingdom, reported "GB US AE SG · 4 markets" — and the count
// is not cosmetic. Anything above one market makes the portfolio "mixed", and the page then
// withholds the single ranking, the portfolio-wide half-hourly pattern analysis, the single
// regulatory exposure number and tenant-level detail, on the grounds that they do not survive
// a mix of standards. There was no mix. A pack existing is not a market being in scope.
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
globalThis.fetch = async () => ({
  ok: true, status: 200, statusText: '200', text: async () => JSON.stringify({ ok: true })
});

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const building = (cc, name) => ({
  cc, name, code: name, areaM2: 8600, euiN: 165, benchN: 185, anoms: 0
});

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'module', module: 'energy', role: 'admin' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); };

test('two buildings in one country are one market, not four', () => {
  c.setState({ bldLive: [building('UK', 'Harbour Point'), building('UK', 'Ashgrove Court')] });
  const sc = c.enScope(c.state);
  assert.deepEqual(sc.all, ['UK']);
  assert.deepEqual(sc.sel, ['UK']);
  assert.equal(sc.single, true, 'a single-country portfolio is not mixed');
  cleanup();
});

test('a single market keeps the analysis the page was withholding', () => {
  c.setState({ bldLive: [building('UK', 'Harbour Point'), building('UK', 'Ashgrove Court')] });
  const v = c.enVals(c.state);
  assert.match(v.enFidelity, /Single market/);
  assert.doesNotMatch(v.enFidelityNote, /markets in scope with/);
  assert.match(v.enScopeCards[0].s, /United Kingdom/);
  assert.doesNotMatch(v.enScopeCards[0].s, /4 markets/);
  cleanup();
});

test('a genuinely mixed portfolio still reports every market it holds', () => {
  c.setState({ bldLive: [building('UK', 'A'), building('SG', 'B'), building('AE', 'C')] });
  const sc = c.enScope(c.state);
  assert.deepEqual(sc.all, ['UK', 'AE', 'SG'], 'pack order, so chips do not reshuffle');
  assert.equal(sc.single, false);
  const v = c.enVals(c.state);
  assert.match(v.enFidelity, /Mixed portfolio/);
  assert.match(v.enScopeCards[0].s, /3 markets/);
  cleanup();
});

test('a country with no pack does not invent a market', () => {
  c.setState({ bldLive: [building('UK', 'A'), building('FR', 'B')] });
  assert.deepEqual(c.enScope(c.state).all, ['UK']);
  cleanup();
});

test('buildings with no country attributed are not a market of their own', () => {
  // They stay in scope under "all countries" and drop out when one is picked. That is the
  // filter's job, not the scope's, and counting them as a market would make every portfolio
  // with one unmapped building look mixed.
  c.setState({ bldLive: [building('UK', 'A'), building(null, 'B'), building('—', 'C')] });
  const sc = c.enScope(c.state);
  assert.deepEqual(sc.all, ['UK']);
  assert.equal(sc.single, true);
  cleanup();
});

test('an explicit selection still wins over what is on record', () => {
  c.setState({
    bldLive: [building('UK', 'A'), building('SG', 'B')],
    eScope: ['SG']
  });
  const sc = c.enScope(c.state);
  assert.deepEqual(sc.sel, ['SG']);
  assert.equal(sc.isAll, false);
  cleanup();
});

test('an empty register falls back to the packs rather than reporting no markets', () => {
  // Mid-load is not a portfolio. Answering "0 markets" would divide by a scope that does not
  // exist and read as a finding about the estate rather than about the page.
  c.setState({ bldLive: [] });
  assert.deepEqual(c.enScope(c.state).all, ['UK', 'US', 'AE', 'SG']);
  cleanup();
});

test('the ratings hint counts the same markets as the scope card', () => {
  c.setState({ bldLive: [building('UK', 'A'), building('UK', 'B')] });
  const v = c.enVals(c.state);
  assert.match(v.enRatingHint, /1 market/);
  cleanup();
});

test('the market profile section is singular for one market', () => {
  c.setState({ bldLive: [building('UK', 'A')] });
  assert.match(c.enVals(c.state).enMatrixTitle, /Market profile · United Kingdom/);
  cleanup();
});
