// A failed GET /api/reports is not "No report cards yet". reports.js stored the error in
// reportsError and, until 28 Sep 2026, nothing showed it: the grid invited the reader to pin
// their first card over cards that were there and simply not read.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

test('a failed reports read says so instead of "no report cards yet"', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, reports: [], reportsError: '502 Bad Gateway', reportsLoading: false });
  const v = c.renderVals();
  assert.equal(v.reportGridFailed, true);
  assert.match(v.reportGridFailText, /502 Bad Gateway/);
  clearInterval(c._orchTick); clearTimeout(c._tt);
});

test('an answered read with no cards is still the plain empty state', () => {
  const c = new HoistraLogic();
  c.setState({ signedIn: true, reports: [], reportsError: '', reportsLoading: false });
  const v = c.renderVals();
  assert.equal(v.reportGridFailed, false);
  assert.equal(v.reportGridEmpty, true);
  clearInterval(c._orchTick); clearTimeout(c._tt);
});
