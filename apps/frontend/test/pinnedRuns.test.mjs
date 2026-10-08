// Pinned runs under the Home ask bar run their question (Hussain, 8 Oct 2026). A saved report
// card's chip opened the report page instead, and the same question showed twice — once as the
// card (a page) and once as a suggestion (a query) — with nothing to tell the two apart. Every
// chip now asks its question; the report itself is under Reports in the navigator.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c = null;
afterEach(() => {
  if (c) { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); clearTimeout(c._vpRetry); clearTimeout(c._qTimer); clearTimeout(c._homeRefresh); }
  c = null;
});
const ME = 'hussain@plenum.example';
const boot = (cards) => {
  c = new HoistraLogic({});
  c.setState({ signedIn: true, view: 'home', account: { email: ME }, reportsOwner: ME,
    reports: [{ id: 'r1', name: 'My report', cards: cards }] });
  const asked = [];
  c.askScoped = (q) => { asked.push(q); };
  return asked;
};

test("a saved report card's chip asks its question; it does not open the report", () => {
  const asked = boot([{ id: 'card-1', name: 'Which vendors are blocked right now?', prompt: 'Which vendors are blocked right now?' }]);
  const chip = c.renderVals().pinned.find((p) => /blocked/.test(p.label));
  chip.run();
  assert.deepEqual(asked, ['Which vendors are blocked right now?']);
  assert.equal(c.state.view, 'home', 'no report page');
});

test('a card named differently still asks the question it was saved with', () => {
  const asked = boot([{ id: 'card-2', name: 'hus', prompt: 'Which vendors are blocked right now?' }]);
  c.renderVals().pinned.find((p) => p.label === 'hus').run();
  assert.deepEqual(asked, ['Which vendors are blocked right now?']);
});

test('the same question is offered once', () => {
  boot([{ id: 'card-1', name: 'Which vendors are blocked right now?', prompt: 'Which vendors are blocked right now?' },
        { id: 'card-2', name: 'which vendors are blocked right now', prompt: 'which vendors are blocked right now' }]);
  const labels = c.renderVals().pinned.map((p) => p.label.toLowerCase().replace(/[?.!\s]+$/, ''));
  assert.equal(labels.filter((l) => l === 'which vendors are blocked right now').length, 1);
  assert.equal(labels.length, 3, 'the card plus the two other suggestions');
});
