// Buildings stays current, and the Home chat knows what is on the register (Hussain, 2 Nov 2026:
// "Buildings is not automatically updated" / "Information for hoisted buildings and ingested
// data is not available on the home page orchestrator chat").
//
// The buildings register was read once, at sign-in, and again only after a hoist, an edit or a
// document delete in this tab. A document ingested through the chat, a migration that wrote
// buildings, or a building another tab or person hoisted never reached the page until a full
// reload. It is now read again when the page is opened (and Home) with a read older than a
// minute, after a chat turn that carried files, and when a migration completes.
//
// The Home chat was told only "Live register: N buildings …" counted off the compliance
// register — no names, no IDs, nothing ingested, and nothing at all until that register had
// loaded (in Ops Brain it now loads last). It is now told the buildings register itself.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('no network in this test'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const ROWS = [
  { buildingId: 'b-1', id: 's-1', code: 'B-090765', name: 'Jasmeet Tower', cc: 'UK', floors: 89, counts: { documents: 12, certificates: 3 }, updatedAt: '2026-11-02T10:00:00Z' },
  { buildingId: 'b-2', id: 's-2', code: 'B-101', name: 'Ashgrove Court', cc: 'UK', floors: 6, counts: { documents: 0 }, updatedAt: '2026-10-01T10:00:00Z' }
];

function make(state) {
  const c = new HoistraLogic();
  const reads = [];
  c.bldLoad = function () { reads.push('bld'); return Promise.resolve(); };
  c.setState(Object.assign({ signedIn: true, account: { id: 'u', email: 'r@x.y', role: 'user' }, role: 'user', view: 'vp', bldLive: ROWS }, state || {}));
  reads.length = 0;
  return { c, reads };
}

test('opening Buildings with a read older than a minute reads the register again', () => {
  const { c, reads } = make({ bldLoadedAt: new Date(Date.now() - 5 * 60000).toISOString(), bgTree: { 'b-1': { branches: [] } } });
  c.setState({ view: 'buildings' });
  assert.deepEqual(reads, ['bld']);
  assert.deepEqual(c.state.bgTree, {}, 'the per-building rows are read again too');
});

test('a fresh read is not repeated on every visit', () => {
  const { c, reads } = make({ bldLoadedAt: new Date().toISOString() });
  c.setState({ view: 'buildings' });
  c.setState({ view: 'home' });
  assert.deepEqual(reads, []);
});

test('Home reads it again too, so the hero line and the chat context are current', () => {
  const { c, reads } = make({ bldLoadedAt: new Date(Date.now() - 2 * 60000).toISOString() });
  c.setState({ view: 'home' });
  assert.deepEqual(reads, ['bld']);
});

test('a chat turn that carried files, or a finished migration, reads the register again', () => {
  const { c, reads } = make({ bldLoadedAt: new Date().toISOString() });
  c.bldAfterChange();
  assert.deepEqual(reads, ['bld'], 'regardless of how fresh the last read was');
});

test('nothing is re-read before the first read has landed', () => {
  const { c, reads } = make({ bldLive: null, bldLoadedAt: null });
  c.bldAfterChange();
  c.setState({ view: 'buildings' });
  assert.deepEqual(reads, [], 'the first read is loadLiveData\'s');
});

test("the Home chat is told the buildings register: names, IDs, floors and what was ingested", () => {
  const { c } = make({ view: 'home', bldLive: ROWS, instance: 'full' });
  const ctx = c.chatContext();
  assert.match(ctx, /Buildings register \(2 buildings/);
  assert.match(ctx, /Jasmeet Tower \(building ID B-090765, UK, 89 floors\) — 12 documents ingested/);
  assert.match(ctx, /Ashgrove Court \(building ID B-101, UK, 6 floors\) — no documents ingested/);
  assert.ok(ctx.indexOf('Jasmeet Tower') < ctx.indexOf('Ashgrove Court'), 'most recently hoisted or changed first');
  assert.match(ctx, /12 documents ingested across the portfolio/);
});

test('a long register is cut short and says the tools read the rest', () => {
  const many = Array.from({ length: 40 }, (_, i) => ({ buildingId: 'b' + i, code: 'B-' + i, name: 'Site ' + i, cc: 'UK', floors: null, counts: {}, updatedAt: null }));
  const { c } = make({ view: 'home', bldLive: many, instance: 'full' });
  assert.match(c.chatContext(), /and 15 more — the tools read the full register/);
});

test('the conversation page is told the same', () => {
  const { c } = make({ view: 'chat', bldLive: ROWS, instance: 'full' });
  assert.match(c.chatContext(), /Jasmeet Tower/);
});
