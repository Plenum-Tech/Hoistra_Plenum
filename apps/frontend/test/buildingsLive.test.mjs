// buildingsLive — the Buildings table's search filter (name, building ID, country, state)
// and its pagination (20 rows a page), both computed client-side from whatever the register
// already loaded. The real controller in Node, with a dead backend, since neither feature
// makes a network call of its own.
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
const { shapeLiveBuildings, filterBuildings } = await import('../src/logic/buildingsLive.js');

const ROWS = shapeLiveBuildings({ buildings: [
  { site_id: 'B-001', code: 'B-001', name: 'Bishopsgate Tower', country_code: 'UK', region: 'Greater London', city: 'London' },
  { site_id: 'B-002', code: 'B-002', name: 'Kingsway House', country_code: 'UK', region: 'Greater London', city: 'London' },
  { site_id: 'B-009', code: 'B-009', name: 'Raffles Link', country_code: 'SG', region: 'Central Region', city: 'Singapore' },
  { site_id: 'SITE-77', code: 'MH-2', name: 'Mill House', country_code: 'AE', region: 'Dubai', city: 'Dubai' }
] });

// ── filterBuildings (pure) ──

test('a blank query matches everything, in the same order', () => {
  assert.deepEqual(filterBuildings(ROWS, ''), ROWS);
  assert.deepEqual(filterBuildings(ROWS, '   '), ROWS);
});

test('matches by name, case-insensitive and by substring', () => {
  const hit = filterBuildings(ROWS, 'tower');
  assert.equal(hit.length, 1);
  assert.equal(hit[0].name, 'Bishopsgate Tower');
  assert.deepEqual(filterBuildings(ROWS, 'TOWER'), hit);
});

test('matches by building ID or code', () => {
  assert.equal(filterBuildings(ROWS, 'b-002').length, 1);
  assert.equal(filterBuildings(ROWS, 'mh-2')[0].name, 'Mill House');
});

test('matches by country and by state or city', () => {
  // "sg" also occurs inside "Bishop-sg-ate" by name, so this picks a country code that is
  // not a substring of any fixture's other fields — the match still has to come from cc.
  assert.equal(filterBuildings(ROWS, 'ae').length, 1);
  assert.equal(filterBuildings(ROWS, 'ae')[0].name, 'Mill House');
  assert.equal(filterBuildings(ROWS, 'dubai').length, 1);
  assert.equal(filterBuildings(ROWS, 'london').length, 2);
});

test('no match returns an empty list, not everything', () => {
  assert.deepEqual(filterBuildings(ROWS, 'nonexistent-building-xyz'), []);
});

// ── pagination + search, through the controller ──

let c;
beforeEach(() => { Object.keys(mem).forEach((k) => { delete mem[k]; }); c = new HoistraLogic(); });
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._bldRetry); };

// 45 synthetic rows so pagination has three full-ish pages at 20/page.
const many = () => Array.from({ length: 45 }, (_, i) => ({
  site_id: 'B-' + String(i + 1).padStart(3, '0'), code: 'B-' + String(i + 1).padStart(3, '0'),
  name: 'Building ' + (i + 1), country_code: i % 2 ? 'UK' : 'US', region: 'Region ' + (i % 5)
}));

test('with nothing loaded, the pager is hidden and the search bar does not show', () => {
  c.setState({ signedIn: true, view: 'buildings' });
  const v = c.renderVals();
  assert.equal(v.bldQueryShow, 'none');
  assert.equal(v.bldPagerShow, 'none');
  assert.equal(v.bldPageCount, 1);
  cleanup();
});

test('45 rows page at 20, with a correct page label and Prev/Next availability', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  let v = c.renderVals();
  assert.equal(v.bldQueryShow, 'flex');
  assert.equal(v.bldPagerShow, 'flex');
  assert.equal(v.buildingRows.length, 20);
  assert.equal(v.bldPageCount, 3);
  assert.equal(v.bldPage, 0);
  assert.equal(v.bldPageLabel, '1–20 of 45');
  assert.equal(v.bldPagePrevShow, false);
  assert.equal(v.bldPageNextShow, true);

  v.bldPageNext();
  v = c.renderVals();
  assert.equal(v.bldPage, 1);
  assert.equal(v.buildingRows.length, 20);
  assert.equal(v.bldPageLabel, '21–40 of 45');
  assert.equal(v.bldPagePrevShow, true);
  assert.equal(v.bldPageNextShow, true);

  v.bldPageNext();
  v = c.renderVals();
  assert.equal(v.bldPage, 2);
  assert.equal(v.buildingRows.length, 5, 'the last page carries the remainder');
  assert.equal(v.bldPageLabel, '41–45 of 45');
  assert.equal(v.bldPageNextShow, false);

  // Next past the end is a no-op; Prev walks back.
  v.bldPageNext();
  assert.equal(c.renderVals().bldPage, 2);
  v.bldPagePrev();
  assert.equal(c.renderVals().bldPage, 1);
  cleanup();
});

test('typing a search resets to page 1 and re-derives the page count from the match', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().bldPageNext(); // land on page 2 first
  assert.equal(c.renderVals().bldPage, 1);

  const v = c.renderVals();
  v.setBldQuery({ target: { value: 'US' } }); // country_code match, half the rows
  const after = c.renderVals();
  assert.equal(after.bldPage, 0, 'a new search always lands back on page 1');
  assert.equal(after.bldPageCount, 2);
  assert.equal(after.buildingRows.length, 20);
  assert.equal(after.bldPageLabel, '1–20 of 23 matching');
  cleanup();
});

test('a search with no match shows the empty state, naming the query, distinct from "not loaded"', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setBldQuery({ target: { value: 'nonexistent-xyz' } });
  const v = c.renderVals();
  assert.equal(v.bldEmptyShow, 'block');
  assert.match(v.bldEmptyText, /No buildings match “nonexistent-xyz”/);
  assert.equal(v.bldPagerShow, 'none');
  cleanup();
});

test('clearing the search restores the full, unpaginated-from-scratch list', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setBldQuery({ target: { value: 'US' } });
  c.renderVals().setBldQuery({ target: { value: '' } });
  const v = c.renderVals();
  assert.equal(v.bldQuery, '');
  assert.equal(v.bldPageCount, 3);
  assert.equal(v.buildingRows.length, 20);
  cleanup();
});

test('a page number surviving a shrink (search or reload) is clamped rather than left past the end', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }), bldPage: 2 });
  // Reload the same rows without going through setBldQuery — bldPage is still 2 (last page
  // of 45), then the register itself shrinks to 5 rows (one page) without resetting bldPage.
  c.setState({ bldLive: shapeLiveBuildings({ buildings: many().slice(0, 5) }) });
  const v = c.renderVals();
  assert.equal(v.bldPageCount, 1);
  assert.equal(v.bldPage, 0, 'clamped into range rather than showing an empty page 3 of 1');
  assert.equal(v.buildingRows.length, 5);
  cleanup();
});

// ── the Documents section's own search + pagination (independent of the table's) ──

test('with nothing loaded, the Documents search bar is hidden and its pager is hidden', () => {
  c.setState({ signedIn: true, view: 'buildings' });
  const v = c.renderVals();
  assert.equal(v.docQueryShow, 'none');
  assert.equal(v.docPagerShow, 'none');
  assert.equal(v.docPageCount, 1);
  cleanup();
});

test('45 buildings page the Documents list at 20, with its own Prev/Next and page label', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  let v = c.renderVals();
  assert.equal(v.docQueryShow, 'flex');
  assert.equal(v.docPagerShow, 'flex');
  assert.equal(v.docBuildings.length, 20);
  assert.equal(v.docPageCount, 3);
  assert.equal(v.docPageLabel, '1–20 of 45');
  assert.equal(v.docPagePrevShow, false);
  assert.equal(v.docPageNextShow, true);

  v.docPageNext();
  v = c.renderVals();
  assert.equal(v.docPage, 1);
  assert.equal(v.docBuildings.length, 20);
  assert.equal(v.docPageLabel, '21–40 of 45');

  v.docPageNext();
  v = c.renderVals();
  assert.equal(v.docPage, 2);
  assert.equal(v.docBuildings.length, 5, 'the last page carries the remainder');
  assert.equal(v.docPageNextShow, false);
  cleanup();
});

test('paging the Documents list never moves the buildings table, and vice versa', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().docPageNext(); // Documents → page 2
  let v = c.renderVals();
  assert.equal(v.docPage, 1);
  assert.equal(v.bldPage, 0, 'the table is untouched');

  v.bldPageNext(); v.bldPageNext(); // table → page 3
  v = c.renderVals();
  assert.equal(v.bldPage, 2);
  assert.equal(v.docPage, 1, 'the Documents list is untouched');
  cleanup();
});

test('a Documents search resets its own page to 1 and re-derives its own page count', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().docPageNext();
  assert.equal(c.renderVals().docPage, 1);

  c.renderVals().setDocQuery({ target: { value: 'US' } });
  const v = c.renderVals();
  assert.equal(v.docPage, 0);
  assert.equal(v.docPageCount, 2);
  assert.equal(v.docPageLabel, '1–20 of 23 matching');
  assert.equal(v.bldQuery, '', 'the table\'s search is untouched by the Documents search');
  cleanup();
});

test('a Documents search with no match shows its own empty state and hides its pager', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setDocQuery({ target: { value: 'nonexistent-xyz' } });
  const v = c.renderVals();
  assert.equal(v.docEmptyShow, 'block');
  assert.match(v.docEmptyText, /No buildings match “nonexistent-xyz”/);
  assert.equal(v.docPagerShow, 'none');
  assert.equal(v.docBuildings.length, 0);
  cleanup();
});
