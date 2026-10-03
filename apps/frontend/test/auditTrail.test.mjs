// The Ingestion audit trail's filter bar, tiles and day grouping.
//
// The page fetches one page of the trail (limit 100) and narrows it in the browser, so
// everything here is client-side over `s.audit`. Two numbers must not drift apart: what the
// tiles claim and what the rows beneath them show. The tiles describe the FILTERED set —
// "IN VIEW 2 · CLEAN RATE 50% · NEEDS REVIEW 1" over two rows, one accepted — so a tile that
// kept describing the whole trail while the list narrowed would be quietly wrong every time
// a filter was on.
//
// The status chips are the exception: their counts come from the set narrowed by every
// OTHER filter, not by the chip you are standing on. A chip whose own count changed when you
// pressed it could never show you what you would get by pressing a different one.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
const { shapeLiveUser } = await import('../src/logic/usersLive.js');

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async () => ({ ok: true, status: 200, statusText: '200', text: async () => JSON.stringify({ ok: true }) });

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const DAY = 86400000;
const NOW = new Date('2026-09-17T11:30:00');
const iso = (msAgo) => new Date(NOW.getTime() - msAgo).toISOString();

// Two rows today (one accepted, one overridden), one three days back, one three weeks back.
const ROWS = [
  { id: 'a1', at: iso(30 * 60000), when: 'Today 11:00', who: 'Clara Novak', role: 'User',
    doc: 'Lift LOLER examination report — Apex Lifts.pdf', building: 'Northgate Mall',
    finalB: 'Northgate Mall', outcome: 'Accepted', tone: 'ok' },
  { id: 'a2', at: iso(56 * 60000), when: 'Today 10:34', who: 'Clara Novak', role: 'User',
    doc: 'Asbestos re-inspection survey — EnviroCheck.pdf', building: 'Bishopsgate Tower',
    finalB: 'Bishopsgate Tower', outcome: 'Overridden', tone: 'warn' },
  { id: 'a3', at: iso(3 * DAY), when: 'Mon 09:15', who: 'Daniel Reyes', role: 'Admin',
    doc: 'Fire alarm certificate — SafeGuard.pdf', building: 'Riverside Court',
    finalB: 'Riverside Court', outcome: 'Rejected', tone: 'risk' },
  { id: 'a4', at: iso(21 * DAY), when: '27 Aug 14:47', who: 'Priya Shah', role: 'User',
    doc: 'Gas safety record — Northern Heating.pdf', building: 'Northgate Mall',
    finalB: 'Riverside Court', outcome: 'Reassigned', tone: 'accent' }
];

let c;
beforeEach(() => {
  c = new HoistraLogic();
  // Every control now asks the register again (auApplyFilter -> auLiveLoad). What goes on
  // the wire is auditQuery.test.mjs's subject; these tests are about the view model, so the
  // read is stubbed out — left live it would retry a stub payload forever and hang the run.
  c.auLiveLoad = async () => {};
  c.setState({ signedIn: true, view: 'audit', audit: ROWS.slice(), auTotal: 888, auNow: NOW.toISOString(), auRange: 'All' });
});
const v = () => c.renderVals();

// ── the three tiles ──────────────────────────────────────────────────────────────────

test('the tiles describe what is in view, not the whole trail', () => {
  c.renderVals().auRangePicks.find((r) => r.label === 'Today').pick();
  const t = v();
  assert.equal(t.auInView, '2', 'two rows are from today');
  assert.equal(t.auCleanRate, '50%', 'one of the two is accepted');
  assert.equal(t.auNeedsReview, '1', 'the overridden one still wants a person');
});

test('an empty view reports no rate rather than dividing by zero', () => {
  c.setState({ auQuery: 'nothing matches this' });
  const t = v();
  assert.equal(t.auInView, '0');
  assert.equal(t.auCleanRate, '—', 'a rate over nothing is not 0% and not NaN');
  assert.equal(t.auNeedsReview, '0');
});

// ── the filter bar ───────────────────────────────────────────────────────────────────

test('the search box matches document, person and building alike', () => {
  c.setState({ auQuery: 'asbestos' });
  assert.deepEqual(v().auRows.map((r) => r.id), ['a2'], 'by document');
  c.setState({ auQuery: 'daniel' });
  assert.deepEqual(v().auRows.map((r) => r.id), ['a3'], 'by person');
  c.setState({ auQuery: 'northgate' });
  assert.deepEqual(v().auRows.map((r) => r.id).sort(), ['a1', 'a4'], 'by building');
});

test('the building select narrows to one building and offers every building in the trail', () => {
  const opts = v().auBuildingOpts.map((o) => o.label);
  assert.equal(opts[0], 'All buildings');
  assert.deepEqual(opts.slice(1), ['Bishopsgate Tower', 'Northgate Mall', 'Riverside Court']);
  c.setState({ auBuilding: 'Northgate Mall' });
  assert.deepEqual(v().auRows.map((r) => r.id).sort(), ['a1', 'a4']);
});

// ── the building picker ──
// The register runs to hundreds of buildings, so the picker is a type-ahead with a cap
// rather than a list to scroll: what it shows must be the buildings being reached for.
const REGISTER = [
  { id: 'b-1', name: 'Northgate Mall', building_code: 'NGM-01' },
  { id: 'b-2', name: 'Bishopsgate Tower', building_code: 'BGT-04' },
  { id: 'b-3', name: 'North Quay Wharf', building_code: 'NQW-02' },
  { id: 'b-4', name: 'Old Northern Depot', building_code: 'OND-11' }
];

test('the building picker types ahead over the register, and a name that STARTS with it leads', () => {
  c.setState({ axBldsLive: REGISTER, auBldOpen: true, auBldQuery: 'north' });
  assert.deepEqual(v().auBuildings.map((b) => b.label),
    ['North Quay Wharf', 'Northgate Mall', 'Old Northern Depot'],
    'the two that start with it first, the one that merely contains it last');
  assert.equal(v().auBuildingsLine, '3 of 4');
});

test('the picker reaches a building by its code, because that is how some people know it', () => {
  c.setState({ axBldsLive: REGISTER, auBldOpen: true, auBldQuery: 'bgt' });
  assert.deepEqual(v().auBuildings.map((b) => [b.label, b.hint]), [['Bishopsgate Tower', 'BGT-04']]);
  c.setState({ auBldQuery: 'nothing by that name' });
  assert.deepEqual(v().auBuildings, [], 'and says nothing rather than offering everything');
});

test('a register of hundreds is capped, and the footer says what the cap is holding back', () => {
  const many = Array.from({ length: 200 }, (_, i) => ({ id: 'b' + i, name: 'Site ' + String(i).padStart(3, '0'), building_code: 'S' + i }));
  c.setState({ axBldsLive: many, auBldOpen: true, auBldQuery: '' });
  assert.equal(v().auBuildings.length, 60, 'the popover never renders two hundred rows');
  assert.equal(v().auBuildingsLine, '60 of 200 — keep typing to narrow');
  c.setState({ auBldQuery: 'site 01' });
  assert.equal(v().auBuildings.length, 10, 'typing is what reaches the rest');
  assert.equal(v().auBuildingsLine, '10 of 200');
});

test('picking a building files it by id, closes the picker and forgets what was typed', () => {
  c.setState({ axBldsLive: REGISTER, auBldOpen: true, auBldQuery: 'bishop' });
  v().auBuildings[0].pick();
  assert.equal(c.state.auBuilding, 'b-2', 'valued as the id the server filters on');
  assert.equal(c.state.auBldOpen, false);
  assert.equal(c.state.auBldQuery, '', 'a leftover search would reopen on a list already narrowed');
  assert.equal(v().auBuildingLabel, 'Bishopsgate Tower', 'and the trigger names it');
  v().auBuildingChip.clear();
  assert.equal(c.state.auBuilding, '');
  assert.equal(v().auBuildingLabel, 'All buildings');
  assert.equal(v().auBuildingAll.on, true);
});

test('a building filter held from a register that has not loaded still names itself', () => {
  c.setState({ axBldsLive: null, auBuilding: 'Northgate Mall' });
  assert.equal(v().auBuildingLabel, 'Northgate Mall', 'never "All buildings" while it narrows the page');
});

test('clicking a person filters to them, and the chip clears it again', () => {
  assert.equal(v().auPersonChip, null, 'no chip until someone is picked');
  v().auRows.find((r) => r.id === 'a1').pickPerson();
  const withChip = v();
  assert.equal(withChip.auPersonChip.label, 'Clara Novak');
  assert.deepEqual(withChip.auRows.map((r) => r.id).sort(), ['a1', 'a2']);
  withChip.auPersonChip.clear();
  assert.equal(v().auPersonChip, null);
  assert.equal(v().auRows.length, 4, 'clearing the chip brings everyone back');
});

test('the date range narrows by when it actually happened', () => {
  const pick = (label) => v().auRangePicks.find((r) => r.label === label).pick();
  pick('Today');
  assert.deepEqual(v().auRows.map((r) => r.id).sort(), ['a1', 'a2']);
  pick('7 days');
  assert.deepEqual(v().auRows.map((r) => r.id).sort(), ['a1', 'a2', 'a3']);
  pick('14 days');
  assert.equal(v().auRows.length, 3, 'the 27 Aug row is still outside a fortnight');
  pick('All');
  assert.equal(v().auRows.length, 4);
});

// ── the chips, and the count beside them ─────────────────────────────────────────────

test('each status chip carries its own count', () => {
  const by = Object.fromEntries(v().auFilters.map((f) => [f.label, f.count]));
  assert.equal(by.All, '4');
  assert.equal(by.Accepted, '1');
  assert.equal(by.Overridden, '1');
  assert.equal(by.Rejected, '1');
  assert.equal(by.Reassigned, '1');
});

test('a chip count reflects the other filters but never the chip you are standing on', () => {
  v().auRangePicks.find((r) => r.label === 'Today').pick();
  const by = Object.fromEntries(v().auFilters.map((f) => [f.label, f.count]));
  assert.equal(by.All, '2', 'the range narrowed every count');
  assert.equal(by.Rejected, '0');
  v().auFilters.find((f) => f.label === 'Accepted').pick();
  const after = Object.fromEntries(v().auFilters.map((f) => [f.label, f.count]));
  assert.deepEqual(after, by, 'pressing a status chip must not rewrite the counts beside it');
  assert.deepEqual(v().auRows.map((r) => r.id), ['a1'], 'though it does narrow the rows');
});

test('the entries line counts what is shown against the whole trail on the server', () => {
  assert.equal(v().auShownLine, '4 of 888 entries');
  c.setState({ auQuery: 'asbestos' });
  assert.equal(v().auShownLine, '1 of 888 entries');
});

// ── day grouping ─────────────────────────────────────────────────────────────────────

test('rows are grouped by day, newest first, each group counted and its outcomes tallied', () => {
  v().auRangePicks.find((r) => r.label === 'All').pick();
  const g = v().auGroups;
  assert.deepEqual(g.map((x) => x.label), ['Today', 'Monday', '27 August'],
    'today is named, this week by weekday, older by date');
  assert.equal(g[0].count, '2');
  assert.deepEqual(g[0].legend.map((l) => l.label), ['1 accepted', '1 overridden'],
    'the legend tallies the outcomes actually in that day');
  assert.deepEqual(g[0].rows.map((r) => r.id), ['a1', 'a2']);
});

test('a group shows only the rows that survived the filters', () => {
  c.setState({ auQuery: 'asbestos' });
  const g = v().auGroups;
  assert.equal(g.length, 1);
  assert.equal(g[0].count, '1');
  assert.deepEqual(g[0].rows.map((r) => r.id), ['a2']);
});


test('the page opens on Today, the way the screenshot shows it', () => {
  const fresh = new HoistraLogic();
  fresh.auLiveLoad = async () => {};
  fresh.setState({ signedIn: true, view: 'audit', audit: ROWS.slice(), auTotal: 888, auNow: NOW.toISOString() });
  const picked = fresh.renderVals().auRangePicks.find((r) => r.on);
  assert.equal(picked.label, 'Today');
  assert.deepEqual(fresh.renderVals().auRows.map((r) => r.id).sort(), ['a1', 'a2']);
});

// ── the person picker ────────────────────────────────────────────────────────────────
//
// A plain dropdown of every person does not survive a real tenant: at a hundred users it is
// a mile of scrolling to reach a name you already know. So the control is a type-ahead with
// three standing scopes above it — Everyone, All admins, All users — and each name carries
// the role and how many entries it owns, which is what tells you whether a name is worth
// picking before you pick it.

test('the picker opens on Everyone and lists each person once, with role and entry count', () => {
  const p = v().auPeople;
  assert.equal(v().auPersonLabel, 'Everyone');
  assert.deepEqual(p.map((x) => x.name), ['Clara Novak', 'Daniel Reyes', 'Priya Shah']);
  const clara = p.find((x) => x.name === 'Clara Novak');
  assert.equal(clara.role, 'User');
  assert.equal(clara.count, '2', 'she owns both of today\'s rows');
  assert.equal(p.find((x) => x.name === 'Daniel Reyes').role, 'Admin');
});

test('typing a name narrows the list without touching the trail', () => {
  c.setState({ auPeopleQuery: 'nov' });
  assert.deepEqual(v().auPeople.map((x) => x.name), ['Clara Novak']);
  assert.equal(v().auRows.length, 4, 'the trail is untouched until a name is actually picked');
});

test('the scope tabs stand in for picking every admin or every user', () => {
  const tab = (label) => v().auPeopleTabs.find((t) => t.label === label);
  assert.equal(tab('Everyone').on, true);
  tab('All admins').pick();
  assert.equal(v().auPersonLabel, 'All admins');
  assert.deepEqual(v().auRows.map((r) => r.id), ['a3'], 'Daniel Reyes is the only admin');
  tab('All users').pick();
  assert.deepEqual(v().auRows.map((r) => r.id).sort(), ['a1', 'a2', 'a4']);
  tab('Everyone').pick();
  assert.equal(v().auRows.length, 4);
});

test('a tab also narrows the names offered beneath it', () => {
  v().auPeopleTabs.find((t) => t.label === 'All admins').pick();
  assert.deepEqual(v().auPeople.map((x) => x.name), ['Daniel Reyes']);
});

test('picking a name filters the trail, names the button, and the chip clears it', () => {
  v().auPeople.find((x) => x.name === 'Priya Shah').pick();
  assert.equal(v().auPersonLabel, 'Priya Shah');
  assert.deepEqual(v().auRows.map((r) => r.id), ['a4']);
  assert.equal(v().auPersonChip.label, 'Priya Shah');
  v().auPersonChip.clear();
  assert.equal(v().auPersonLabel, 'Everyone');
  assert.equal(v().auRows.length, 4);
});

test('a name count answers to the other filters but not to the person already picked', () => {
  v().auRangePicks.find((r) => r.label === 'Today').pick();
  const before = Object.fromEntries(v().auPeople.map((x) => [x.name, x.count]));
  assert.deepEqual(before, { 'Clara Novak': '2' }, 'only she has entries today');
  v().auPeople.find((x) => x.name === 'Clara Novak').pick();
  const after = Object.fromEntries(v().auPeople.map((x) => [x.name, x.count]));
  assert.deepEqual(after, before, 'picking a name must not rewrite the counts beside it');
});

test('the picker opens and closes, and picking closes it', () => {
  assert.equal(v().auPeopleOpen, false);
  v().auPeopleToggle();
  assert.equal(v().auPeopleOpen, true);
  v().auPeople[0].pick();
  assert.equal(v().auPeopleOpen, false, 'a pick is a decision — the popover has done its job');
});

// ── an empty trail is an answer, not a failure to read one ───────────────────────────

test('a live read that found nothing says so, rather than claiming sample data', () => {
  // `live` was inferred from the rows themselves — some(a => a.live) — so a successful read
  // of an EMPTY trail left nothing to infer from and the page fell back to "Sample data".
  // Reported against production on 17 Sep 2026, where ingestion_audit_events holds no rows
  // at all: the screen read "Sample data · 0 of 0 entries", which says the read never
  // happened. It had; there is simply nothing to show yet.
  c.setState({ audit: [], auLiveLoadedAt: '2026-09-17T11:00:00Z', auLiveError: '', auLiveLoading: false });
  assert.equal(v().auLiveSourceLabel, 'Live · svc-operations-intelligence');
  assert.equal(v().auLiveRetryShow, 'none', 'nothing to retry — the read worked');
});

test('before any read answers, the seed is still named as sample data', () => {
  assert.equal(v().auLiveSourceLabel, 'Sample data');
});

// ── once the register is answering, it is the one doing the narrowing ────────────────
//
// The client pipeline above is what makes the sample trail usable before any read answers.
// On live data it must stand down entirely: the server has already applied these filters,
// and a second pass with predicates that are merely SIMILAR — the search looks at an email
// the row does not carry, a date lands either side of midnight — would hide rows the server
// deliberately returned, while the counts beside them still described the server's set.

const LIVE = {
  auLiveLoadedAt: '2026-09-17T11:30:00Z', auLiveError: '', auLiveLoading: false,
  auTotal: 919,
  auByOutcome: { accepted: 336, reassigned: 61, overridden: 5, rejected: 7 },
  auActors: [
    { user_id: 'u-1', name: 'Clara Novak', role: 'user', count: 58 },
    { user_id: 'u-2', name: 'Marcus Hale', role: 'admin', count: 42 }
  ]
};

test('on live data the rows are rendered as they came, not filtered a second time', () => {
  c.setState(Object.assign({ auQuery: 'matches nothing at all', auRange: 'Today' }, LIVE));
  assert.equal(v().auRows.length, 4, 'the server already answered this question');
});

test('the chips count from the register, not from the page in hand', () => {
  c.setState(LIVE);
  const by = Object.fromEntries(v().auFilters.map((f) => [f.label, f.count]));
  assert.equal(by.Accepted, '336');
  assert.equal(by.Reassigned, '61');
  assert.equal(by.All, '409', 'All is the sum of the register\'s own tallies');
});

test('the tiles describe the register once it has answered', () => {
  c.setState(LIVE);
  assert.equal(v().auInView, '919');
  assert.equal(v().auCleanRate, '82%', '336 accepted of 409 outcomes');
  assert.equal(v().auShownLine, '4 of 919 entries', 'what is on screen, against what is held');
});

test('the picker offers the register\'s people, with the counts it sent', () => {
  c.setState(LIVE);
  const p = v().auPeople;
  assert.deepEqual(p.map((x) => x.name), ['Clara Novak', 'Marcus Hale']);
  assert.equal(p[0].count, '58');
  assert.equal(p[1].role, 'Admin', 'the role is rendered the way the rows render it');
});

test('picking a person sends their id, because two people may share a name', () => {
  c.setState(LIVE);
  v().auPeople.find((x) => x.name === 'Marcus Hale').pick();
  assert.equal(c.state.auPersonId, 'u-2');
  assert.equal(c.state.auPerson, 'Marcus Hale');
});

test('the building select is the company register, not the buildings that happen to be on this page', () => {
  c.setState(Object.assign({ axBldsLive: [
    { id: 'b-1', name: 'Northgate Mall' }, { id: 'b-2', name: 'Bishopsgate Tower' }
  ] }, LIVE));
  const opts = v().auBuildingOpts;
  assert.equal(opts[0].label, 'All buildings');
  assert.deepEqual(opts.slice(1).map((o) => [o.label, o.value]),
    [['Bishopsgate Tower', 'b-2'], ['Northgate Mall', 'b-1']],
    'named for the reader, valued as the id the server filters on');
});

test('clearing the person clears the id with it', () => {
  c.setState(Object.assign({ auPerson: 'Marcus Hale', auPersonId: 'u-2' }, LIVE));
  v().auPersonChip.clear();
  assert.equal(c.state.auPersonId, '', 'a stale id would keep filtering after the name was dropped');
});

// ── the picker offers the company, not only the people who happen to be in the trail ──
//
// Reported on the empty production register, 17 Sep 2026: "why can't I see the names of
// admins and users of the company I am logged in as". The picker was built from the trail —
// so with nothing in the trail there was nobody to pick, and the control was dead on exactly
// the register where you would most want to go looking for a person.
//
// The company's own roster is already in memory (usersLive fetches it at sign-in), so names
// come from there and counts come from the trail. Somebody with no entries is still
// offered — their zero IS the answer to "has this person ingested anything".

// Shaped the way usersLive shapes a real row, so the fixture cannot drift from what the
// app actually stores — and so the Users screen's own view model can read it too.
const COMPANY = [
  { id: 'u-1', full_name: 'Clara Novak', role: 'user' },
  { id: 'u-2', full_name: 'Marcus Hale', role: 'admin' },
  { id: 'u-9', full_name: 'Priya Nair', role: 'user' }
].map((u) => shapeLiveUser(u, {}));
const ROSTER = { users: COMPANY, usLiveLoadedAt: '2026-09-17T11:00:00Z' };

test('an empty trail still offers every person in the company', () => {
  c.setState(Object.assign({}, LIVE, ROSTER, { audit: [], auActors: [], auByOutcome: {} }));
  const p = v().auPeople;
  assert.deepEqual(p.map((x) => x.name), ['Clara Novak', 'Marcus Hale', 'Priya Nair']);
  assert.deepEqual(p.map((x) => x.count), ['0', '0', '0'], 'nobody has ingested anything yet');
  assert.equal(p.find((x) => x.name === 'Marcus Hale').role, 'Admin');
});

test('the trail supplies the counts, the company supplies the names', () => {
  c.setState(Object.assign({}, LIVE, ROSTER, { audit: [] }));
  const by = Object.fromEntries(v().auPeople.map((x) => [x.name, x.count]));
  assert.equal(by['Clara Novak'], '58', 'she is in the roster and has entries');
  assert.equal(by['Priya Nair'], '0', 'she is in the roster and has none');
});

test('someone who left the company still appears while their entries do', () => {
  // Their rows are still in the trail and still need attributing; dropping the name would
  // leave entries nobody could filter to.
  c.setState(Object.assign({}, LIVE, ROSTER, { audit: [], auActors: [
    { user_id: 'u-gone', name: 'Tom Whitfield', role: 'user', count: 4 }
  ] }));
  const tom = v().auPeople.find((x) => x.name === 'Tom Whitfield');
  assert.equal(tom.count, '4');
});

test('the role tabs narrow the company roster too', () => {
  c.setState(Object.assign({}, LIVE, ROSTER, { audit: [], auPeopleScope: 'admins' }));
  assert.deepEqual(v().auPeople.map((x) => x.name), ['Marcus Hale']);
});

test('picking someone from the roster sends the id the register knows them by', () => {
  c.setState(Object.assign({}, LIVE, ROSTER, { audit: [] }));
  v().auPeople.find((x) => x.name === 'Priya Nair').pick();
  assert.equal(c.state.auPersonId, 'u-9');
});
