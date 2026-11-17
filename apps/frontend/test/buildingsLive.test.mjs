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

// The register as it reads on screen: the Country column says "United Kingdom", the Use
// column "Commercial", so those words have to find the rows too — not only the stored codes.
const RICH = shapeLiveBuildings({ buildings: [
  { building_id: 'B-012', name: 'ABC', country_code: 'GB', region: 'London', city: 'London', use_type: 'commercial', postcode: 'EC2M 4AA' },
  { building_id: 'B-301', name: 'Bishopsgate Tower', country_code: 'UK', region: 'Greater London', city: 'London', use_type: 'commercial', metering_granularity: 'sub-metered' },
  { building_id: 'B-04', name: 'LPS Office', country_code: 'UAE', region: 'Dubai Silicon Oasis', city: 'Dubai', use_type: 'mixed' },
  { building_id: 'B-303', name: 'Manchester Town Hall', country_code: 'UK', region: 'North West', city: 'Manchester', use_type: 'other' },
  { building_id: 'S-77', name: 'Raffles Link', country_code: 'SG', region: 'Central Region', city: 'Singapore', use_type: 'residential' }
] });
const names = (rows) => rows.map((b) => b.name);

test('the country as the table spells it, and its everyday aliases, find the rows', () => {
  const uk = ['ABC', 'Bishopsgate Tower', 'Manchester Town Hall'];
  for (const q of ['united kingdom', 'United Kingdom', 'uk', 'GB', 'britain', 'great britain']) {
    assert.deepEqual(names(filterBuildings(RICH, q)).sort(), uk, q);
  }
  for (const q of ['uae', 'united arab emirates', 'emirates']) {
    assert.deepEqual(names(filterBuildings(RICH, q)), ['LPS Office'], q);
  }
  assert.deepEqual(names(filterBuildings(RICH, 'singapore')), ['Raffles Link']);
});

test('every word must match some field, in any order — words narrow, they do not widen', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'commercial london')).sort(), ['ABC', 'Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(RICH, 'london commercial')).sort(), ['ABC', 'Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(RICH, 'tower london')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(RICH, 'uk north west')), ['Manchester Town Hall']);
  assert.deepEqual(filterBuildings(RICH, 'london dubai'), []);
});

test('filler words in a sentence-style search do not stop a match', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'commercial buildings in london')).sort(), ['ABC', 'Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(RICH, 'show me the sites in the uae')), ['LPS Office']);
});

test('use type and its synonyms match the Use column', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'commercial')).sort(), ['ABC', 'Bishopsgate Tower']);
  assert.ok(names(filterBuildings(RICH, 'offices')).includes('Bishopsgate Tower'), 'office reads as commercial');
  assert.deepEqual(names(filterBuildings(RICH, 'mixed use')), ['LPS Office']);
  assert.deepEqual(names(filterBuildings(RICH, 'flats')), ['Raffles Link'], 'flats reads as residential');
});

test('building IDs match with or without the dash, and postcodes with or without the space', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'b012')), ['ABC']);
  assert.deepEqual(names(filterBuildings(RICH, 'B-012')), ['ABC']);
  assert.deepEqual(names(filterBuildings(RICH, 'b–301')), ['Bishopsgate Tower'], 'an en dash too');
  assert.deepEqual(names(filterBuildings(RICH, 'ec2m 4aa')), ['ABC']);
  assert.deepEqual(names(filterBuildings(RICH, 'EC2M4AA')), ['ABC']);
});

test('state and city match on their own words, partial or whole', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'silicon oasis')), ['LPS Office']);
  assert.deepEqual(names(filterBuildings(RICH, 'manch')), ['Manchester Town Hall']);
  assert.deepEqual(names(filterBuildings(RICH, 'greater')), ['Bishopsgate Tower']);
});

test('a typo still finds the row when nothing matches exactly', () => {
  assert.deepEqual(names(filterBuildings(RICH, 'bishopgate')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(RICH, 'manchster')), ['Manchester Town Hall']);
  assert.deepEqual(names(filterBuildings(RICH, 'singapor')), ['Raffles Link']);
});

test('typo tolerance never adds rows when the words match something exactly', () => {
  const rows = shapeLiveBuildings({ buildings: [
    { building_id: 'B-1', name: 'Mill House', country_code: 'UK' },
    { building_id: 'B-2', name: 'Hill House', country_code: 'UK' }
  ] });
  assert.deepEqual(names(filterBuildings(rows, 'mill')), ['Mill House']);
});

test('the strongest match ranks first: an exact name or ID beats a passing mention', () => {
  const rows = shapeLiveBuildings({ buildings: [
    { building_id: 'B-1', name: 'Tower Court Annex', country_code: 'UK', region: 'London' },
    { building_id: 'B-2', name: 'London', country_code: 'UK', region: 'Kent' },
    { building_id: 'B-3', name: 'Elm Yard', country_code: 'UK', region: 'Greater London' }
  ] });
  assert.equal(filterBuildings(rows, 'london')[0].name, 'London', 'exact name first');
  assert.equal(filterBuildings(rows, 'b-3')[0].name, 'Elm Yard', 'exact ID first');
});

test('accents and case are ignored on both sides', () => {
  const rows = shapeLiveBuildings({ buildings: [
    { building_id: 'B-1', name: 'Café Royal', country_code: 'UK', region: 'Île Street' }
  ] });
  assert.equal(filterBuildings(rows, 'CAFE').length, 1);
  assert.equal(filterBuildings(rows, 'ile').length, 1);
});

// Hussain, 17 Nov 2026: "22" returned only a building whose UUID happened to contain "22", and
// not the 22-storey tower. A number is a floor count, an area or an EUI, or the start of an ID
// part — never digits buried in the middle of an opaque key.
const NUMS = shapeLiveBuildings({ buildings: [
  { building_id: '936a5b02-2271-4c22-9f0e-8822aa01b4c7', name: 'Vasiliki House', country_code: 'UK', city: 'Cardiff' },
  { building_id: 'B-301', name: 'Bishopsgate Tower', country_code: 'UK', region: 'Greater London', floors: 22, gfa_sqm: 49400, eui_kwh_per_m2: 215 },
  { building_id: 'B-012', name: 'ABC', country_code: 'UK', region: 'London', floors: 25, gfa_sqm: 10000 },
  { building_id: 'B-2201', name: 'Quay Point', country_code: 'UK', region: 'Bristol', floors: 8, gfa_sqm: 2200 }
] });

test('a number finds the floor count, never digits inside an opaque UUID', () => {
  const hit = names(filterBuildings(NUMS, '22'));
  assert.equal(hit[0], 'Bishopsgate Tower', 'the 22-storey building ranks first');
  assert.ok(!hit.includes('Vasiliki House'), '"22" inside a UUID is not a match');
  assert.ok(hit.includes('Quay Point'), 'an ID part that starts with 22 (B-2201) still matches');
});

test('"22 floors" and "25 storeys" read as a floor count', () => {
  assert.deepEqual(names(filterBuildings(NUMS, '22 floors')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(NUMS, '25 storeys')), ['ABC']);
});

test('floor area and EUI match as whole numbers', () => {
  assert.deepEqual(names(filterBuildings(NUMS, '49400')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(NUMS, '215')), ['Bishopsgate Tower']);
});

// Hussain, 17 Nov 2026: "i cant search through floor area". The table shows "10,000 m²"; typed
// that way the comma split the number in two and the ² matched nothing.
test('floor area typed the way the table shows it', () => {
  for (const q of ['10,000', '10,000 m²', '10000 m2', '10000m2', '10,000m²', '10000 sqm', '10 000']) {
    assert.deepEqual(names(filterBuildings(NUMS, q)), ['ABC'], q);
  }
  assert.deepEqual(names(filterBuildings(NUMS, '49,400')), ['Bishopsgate Tower']);
});

test('a floor area still being typed finds it from its first digits', () => {
  assert.ok(names(filterBuildings(NUMS, '49')).includes('Bishopsgate Tower'), '49 → 49,400 m²');
  assert.deepEqual(names(filterBuildings(NUMS, '49,4')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(NUMS, '494')), ['Bishopsgate Tower']);
});

test('a floor count is never matched by its first digit — "2 floors" is not 22 or 25', () => {
  assert.deepEqual(filterBuildings(NUMS, '2 floors'), []);
});

test('an ID number still finds its building, by its whole part or its start', () => {
  assert.deepEqual(names(filterBuildings(NUMS, '301')), ['Bishopsgate Tower']);
  assert.deepEqual(names(filterBuildings(NUMS, '012')), ['ABC']);
  assert.deepEqual(names(filterBuildings(NUMS, '936a')), ['Vasiliki House'], 'a UUID still matches from its start');
});

test('punctuation-only input is treated as blank, not as a query that matches nothing', () => {
  assert.deepEqual(filterBuildings(RICH, ' - , '), RICH);
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

test('the page search and the TopBar building picker keep separate queries', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setBldSearch({ target: { value: 'US' } });
  c.renderVals().bldToggle(); // opening the picker clears ITS search
  const v = c.renderVals();
  assert.equal(v.bldSearch, 'US', 'the table keeps its search when the picker opens');
  assert.equal(v.bldPageLabel, '1–20 of 23 matching');
  assert.equal(c.state.bldQuery, '', 'and the picker opens on an empty search of its own');
  cleanup();
});

test('with nothing loaded, the pager is hidden and the search bar does not show', () => {
  c.setState({ signedIn: true, view: 'buildings' });
  const v = c.renderVals();
  assert.equal(v.bldSearchShow, 'none');
  assert.equal(v.bldPagerShow, 'none');
  assert.equal(v.bldPageCount, 1);
  cleanup();
});

test('45 rows page at 20, with a correct page label and Prev/Next availability', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  let v = c.renderVals();
  assert.equal(v.bldSearchShow, 'flex');
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
  v.setBldSearch({ target: { value: 'US' } }); // country_code match, half the rows
  const after = c.renderVals();
  assert.equal(after.bldPage, 0, 'a new search always lands back on page 1');
  assert.equal(after.bldPageCount, 2);
  assert.equal(after.buildingRows.length, 20);
  assert.equal(after.bldPageLabel, '1–20 of 23 matching');
  cleanup();
});

test('a search with no match shows the empty state, naming the query, distinct from "not loaded"', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setBldSearch({ target: { value: 'nonexistent-xyz' } });
  const v = c.renderVals();
  assert.equal(v.bldEmptyShow, 'block');
  assert.match(v.bldEmptyText, /No buildings match “nonexistent-xyz”/);
  assert.equal(v.bldPagerShow, 'none');
  cleanup();
});

test('clearing the search restores the full, unpaginated-from-scratch list', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }) });
  c.renderVals().setBldSearch({ target: { value: 'US' } });
  c.renderVals().setBldSearch({ target: { value: '' } });
  const v = c.renderVals();
  assert.equal(v.bldSearch, '');
  assert.equal(v.bldPageCount, 3);
  assert.equal(v.buildingRows.length, 20);
  cleanup();
});

test('a page number surviving a shrink (search or reload) is clamped rather than left past the end', () => {
  c.setState({ signedIn: true, view: 'buildings', bldLive: shapeLiveBuildings({ buildings: many() }), bldPage: 2 });
  // Reload the same rows without going through setBldSearch — bldPage is still 2 (last page
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
  assert.equal(v.bldSearch, '', 'the table\'s search is untouched by the Documents search');
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
