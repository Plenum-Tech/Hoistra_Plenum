// Pinning a report card against the real /api/reports contract, transcribed from
// apps/backend/.../svc-operations-intelligence/src/api/routes/reports.py (14 Sep 2026):
//
//   GET  /api/reports                  → {ok, count, reports: [{id, name, cards: [...]}]}
//   GET  /api/reports/refresh-options  → {ok, options, days, min_every_minutes, max_every_minutes}
//   POST /api/reports/cards            → 201 {ok, report, card}
//   POST /api/reports/cards/{id}/run   → {ok, card, run}   · 409 {reason: already_running}
//
// Three real bugs are locked in here. The envelope keys are `options` and `card`, not
// `presets` and the body itself. And the server validates the timezone with ZoneInfo against
// a tzdata that may carry no backward-compatibility links, so the legacy alias every browser
// on an Indian Mac reports — "Asia/Calcutta" — was refused and 422'd the whole create.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers, sent;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const body = opts && opts.body ? JSON.parse(opts.body) : null;
  sent.push({ route: method + ' ' + u.pathname, body });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const [status, out] = h(body);
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { browserTimezone, cadenceIsClockBound, refreshBadge, cardStatusBadge, FALLBACK_PRESETS } = await import('../src/logic/reports.js');

const CARD = { id: 'card-1', report_id: 'rep-1', name: 'Risky buildings', prompt: 'Which buildings put me at risk?', refresh_label: 'Refresh every 1 hour', status: 'pending', runs: [] };
const REPORT = { id: 'rep-1', name: 'My report', cards: [CARD] };
const SERVER_PRESETS = [
  { key: '30m', label: 'Refresh every 30 minutes', badge: '30 min', refresh: { every_minutes: 30 } },
  { key: '1h', label: 'Refresh every 1 hour', badge: '1 hr', refresh: { every_minutes: 60 } },
  { key: 'days', label: 'Refresh on chosen days', badge: 'Days', refresh: { days: [], time: '14:00' }, pick_days: true }
];

const OWNER = 'ada@example.com';

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  sent = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({
    signedIn: true, refreshToken: 'ref-test', view: 'home',
    // Reports are personal, so a signed-in account is part of the harness now: rpLoad
    // stamps reportsOwner on the read and renderVals draws a report only for the account
    // it was read for. Seeding reports[] by hand means seeding that stamp too.
    account: { id: 'u-1', email: OWNER, full_name: 'Ada Admin', role: 'user' },
    sessions: [{ id: 'sess-1', kind: 'chat', title: 'Which buildings put me at risk?', page: 'Home', turns: [], owner: OWNER }]
  });
});
const cleanup = () => { c.rpStop(); clearTimeout(c._tt); };

test('a legacy browser timezone is canonicalised before it ever reaches the server', () => {
  assert.equal(browserTimezone('Asia/Calcutta'), 'Asia/Kolkata', 'the exact alias Chrome reports on an Indian Mac');
  assert.equal(browserTimezone('Europe/Kiev'), 'Europe/Kyiv');
  assert.equal(browserTimezone('America/Buenos_Aires'), 'America/Argentina/Buenos_Aires');
  assert.equal(browserTimezone('Asia/Saigon'), 'Asia/Ho_Chi_Minh');
  assert.equal(browserTimezone('Europe/London'), 'Europe/London', 'a canonical zone passes straight through');
  assert.equal(browserTimezone(''), 'UTC');
  assert.equal(browserTimezone(null), 'UTC');
});

test('only a clock cadence actually depends on the zone', () => {
  assert.equal(cadenceIsClockBound('1h'), false);
  assert.equal(cadenceIsClockBound('30m'), false);
  assert.equal(cadenceIsClockBound('daily'), true);
  assert.equal(cadenceIsClockBound({ days: [1, 4], time: '14:00' }), true);
  assert.equal(cadenceIsClockBound({ every_minutes: 60 }), false);
});

test('the sidebar badge is the compact cadence, not the server prose label', () => {
  // card_to_dict sends refresh_label as prose ("every 1 hour"); the badge column is ~60px.
  assert.equal(refreshBadge({ every_minutes: 30 }), '30 min');
  assert.equal(refreshBadge({ every_minutes: 60 }), '1 hr');
  assert.equal(refreshBadge({ every_minutes: 360 }), '6 hr');
  assert.equal(refreshBadge({ every_minutes: 1440 }), '24 hr');
  assert.equal(refreshBadge({ every_minutes: 10080 }), '7 d');
  assert.equal(refreshBadge({ daily_at: '02:00' }), 'Daily');
  assert.equal(refreshBadge({ days: [1, 4], time: '14:00' }), 'Mon · Thu');
  assert.equal(refreshBadge({ days: [0, 1, 2, 3, 4, 5, 6], time: '02:00' }), 'Daily');
  assert.equal(refreshBadge(null), '');
  // A card mid-flight says what it is doing; a settled one says how often it repeats.
  assert.equal(cardStatusBadge({ status: 'running', refresh: { every_minutes: 60 } }), 'Running');
  assert.equal(cardStatusBadge({ status: 'ready', refresh: { every_minutes: 60 }, refresh_label: 'every 1 hour' }), '1 hr');
  assert.equal(cardStatusBadge({ status: 'ready', refresh_label: 'every 1 hour' }), 'every 1 hour', 'prose only when there is no object to read');
});

test('the cadence menu is read from `options` — the key the server actually sends', async () => {
  handlers['GET /backend/ops-intelligence/api/reports/refresh-options'] = () => [200, {
    ok: true, options: SERVER_PRESETS, days: ['Sun', 'Mon'], min_every_minutes: 5, max_every_minutes: 10080
  }];
  await c.rpLoadPresets();
  assert.equal(c.state.reportPresets.length, 3);
  assert.equal(c.state.reportPresets[1].key, '1h', 'the server list replaced the hardcoded fallback');
  cleanup();
});

test('a refresh-options reply the client cannot read leaves the fallback menu in place', async () => {
  handlers['GET /backend/ops-intelligence/api/reports/refresh-options'] = () => [200, { ok: true }];
  await c.rpLoadPresets();
  assert.equal(c.state.reportPresets, FALLBACK_PRESETS, 'the menu is never left empty');
  cleanup();
});

test('creating a card reads the card out of the {ok, report, card} envelope and opens it', async () => {
  handlers['POST /backend/ops-intelligence/api/reports/cards'] = () => [201, { ok: true, report: REPORT, card: CARD }];
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  c.setState({ reportName: 'Risky buildings', reportCad: 1 });
  await c.rpCreate();
  const post = sent.find((r) => r.route === 'POST /backend/ops-intelligence/api/reports/cards');
  assert.equal(post.body.prompt, 'Which buildings put me at risk?');
  assert.equal(post.body.refresh, '1h', 'the preset key, which parse_refresh accepts verbatim');
  assert.equal(post.body.run_now, true);
  assert.equal(c.state.view, 'report');
  assert.equal(c.state.reportKey, 'card-1', 'the id came from r.card.id, not from the envelope');
  assert.match(c.state.toast, /Report card created/);
  cleanup();
});

test('the first attempt never carries a legacy alias the server would refuse', async () => {
  handlers['POST /backend/ops-intelligence/api/reports/cards'] = () => [201, { ok: true, report: REPORT, card: CARD }];
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  await c.rpCreate();
  const tz = sent.find((r) => r.route === 'POST /backend/ops-intelligence/api/reports/cards').body.timezone;
  assert.ok(tz, 'a zone is always sent');
  assert.equal(['Asia/Calcutta', 'Europe/Kiev', 'Asia/Saigon', 'America/Buenos_Aires'].includes(tz), false,
    'whatever this machine reports, a legacy alias is canonicalised first');
  cleanup();
});

test('a server that refuses the zone still gets the card made — retried on UTC', async () => {
  let attempt = 0;
  handlers['POST /backend/ops-intelligence/api/reports/cards'] = (body) => {
    attempt += 1;
    if (body.timezone !== 'UTC') {
      return [422, { detail: { ok: false, error: "Unknown timezone 'Asia/Kolkata'; use an IANA name like Europe/London.", reason: 'bad_timezone' } }];
    }
    return [201, { ok: true, report: REPORT, card: CARD }];
  };
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  c.setState({ reportCad: 1 });   // every 1 hour — arithmetic, so UTC changes nothing
  await c.rpCreate();
  assert.equal(attempt, 2, 'one retry, not a loop');
  assert.equal(c.state.reportKey, 'card-1', 'the card exists despite the refused zone');
  assert.match(c.state.toast, /Report card created —/, 'an interval cadence does not bother the user about the zone');
  cleanup();
});

test('a clock cadence that falls back to UTC says so rather than moving the time silently', async () => {
  handlers['POST /backend/ops-intelligence/api/reports/cards'] = (body) => (body.timezone !== 'UTC'
    ? [422, { detail: { ok: false, error: 'Unknown timezone', reason: 'bad_timezone' } }]
    : [201, { ok: true, report: REPORT, card: CARD }]);
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  c.setState({ reportCad: 5 });   // "Refresh daily at 02:00" — 02:00 in WHICH zone matters
  await c.rpCreate();
  assert.match(c.state.toast, /read as UTC/, 'the user is told their 02:00 moved');
  cleanup();
});

test('a 422 that is not about the timezone is reported, not retried', async () => {
  let attempt = 0;
  handlers['POST /backend/ops-intelligence/api/reports/cards'] = () => {
    attempt += 1;
    return [422, { detail: { ok: false, error: 'Pick at least one day, 0 (Sunday) to 6 (Saturday).', reason: 'no_days' } }];
  };
  await c.rpCreate();
  assert.equal(attempt, 1, 'no pointless second attempt');
  assert.match(c.state.toast, /Could not create the report card — Pick at least one day/);
  cleanup();
});

test('deleting a card from the sidebar takes two clicks — the first only arms it', async () => {
  let deleted = 0;
  handlers['DELETE /backend/ops-intelligence/api/reports/cards/card-1'] = () => { deleted += 1; return [200, { ok: true, card_id: 'card-1', removed: true }]; };
  c.setState({ reports: [REPORT], reportsOwner: OWNER });
  const row = () => c.renderVals().navReports.find((r) => r.name === 'Risky buildings');
  assert.equal(row().armed, false);
  row().remove();
  assert.equal(deleted, 0, 'the first click sends nothing');
  assert.equal(row().armed, true);
  assert.equal(row().badge, 'Delete?', 'and the row says what the next click does');
  await row().remove();
  assert.equal(deleted, 1);
  assert.equal(c.state.reports[0].cards.length, 0, 'gone from the list');
  assert.equal(c.state.rpArmed, null, 'and the confirm window closes with it');
  cleanup();
});

test('the arm window belongs to one control — arming a second disarms the first', () => {
  const CARD2 = Object.assign({}, CARD, { id: 'card-2', name: 'Second' });
  c.setState({ reports: [Object.assign({}, REPORT, { cards: [CARD, CARD2] })], reportsOwner: OWNER });
  c.renderVals().navReports[0].remove();
  assert.equal(c.state.rpArmed, 'nav:card-1');
  c.renderVals().navReports[1].remove();
  assert.equal(c.state.rpArmed, 'nav:card-2', 'only the newest control is armed');
  cleanup();
});

test('arming one control does not confirm a delete on a different one', () => {
  // The same card is deletable from three places. They shared one arm key, so a single
  // click on the detail button followed by a single click on the sidebar bin fired the
  // DELETE with no confirmation anywhere — the two-click guard defeated by using two
  // different buttons.
  let deleted = 0;
  handlers['DELETE /backend/ops-intelligence/api/reports/cards/card-1'] = () => { deleted += 1; return [200, { ok: true }]; };
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  c.setState({ reports: [REPORT], reportsOwner: OWNER, view: 'report', reportKey: 'card-1' });
  c.renderVals().deleteReport();                 // arms the detail button only
  assert.equal(c.state.rpArmed, 'detail:card-1');
  c.renderVals().navReports[0].remove();         // a different control: must arm, not fire
  assert.equal(deleted, 0, 'the sidebar bin must not inherit the detail button\'s confirmation');
  assert.equal(c.state.rpArmed, 'nav:card-1');
  cleanup();
});

test('deleting the whole report takes its cards with it, and says how many first', async () => {
  let deleted = null;
  handlers['DELETE /backend/ops-intelligence/api/reports/rep-1'] = () => { deleted = 'rep-1'; return [200, { ok: true, report_id: 'rep-1', cards_removed: 1 }]; };
  c.setState({ reports: [REPORT], reportsOwner: OWNER, view: 'report', reportKey: 'card-1' });
  const group = () => c.renderVals().reportGroups[0];
  assert.equal(group().deleteLabel, 'Delete report');
  assert.equal(group().count, '1 card');
  group().remove();
  assert.equal(deleted, null, 'armed, not fired');
  assert.match(group().deleteLabel, /Click again — deletes 1 card/);
  await group().remove();
  assert.equal(deleted, 'rep-1');
  assert.deepEqual(c.state.reports, []);
  assert.equal(c.state.reportKey, null, 'the open card went with it, so the page falls back to the grid');
  assert.equal(c.state.view, 'report', 'and stays in reports rather than bouncing to Home');
  assert.match(c.state.toast, /Report deleted, with 1 card on it/);
  cleanup();
});

test('a report emptied of cards still renders, so it can still be deleted', () => {
  c.setState({ reports: [Object.assign({}, REPORT, { cards: [] })], reportsOwner: OWNER });
  const v = c.renderVals();
  assert.equal(v.reportGridEmpty, false, 'an empty report is not an empty grid');
  assert.equal(v.reportGroups.length, 1);
  assert.equal(v.reportGroups[0].count, '0 cards');
  assert.equal(v.reportGroups[0].deleteLabel, 'Delete report');
  cleanup();
});

test('a failed report delete leaves the report exactly where it was', async () => {
  handlers['DELETE /backend/ops-intelligence/api/reports/rep-1'] = () => [500, { detail: 'boom' }];
  c.setState({ reports: [REPORT], reportsOwner: OWNER });
  c.renderVals().reportGroups[0].remove();
  await c.renderVals().reportGroups[0].remove();
  assert.equal(c.state.reports.length, 1, 'nothing removed on a server failure');
  assert.match(c.state.toast, /Could not delete the report/);
  cleanup();
});

test('a manual refresh that collides with the server scheduler is not shown as a failure', async () => {
  handlers['POST /backend/ops-intelligence/api/reports/cards/card-1/run'] = () => [409, {
    detail: { ok: false, error: 'This card is refreshing already.', reason: 'already_running' }
  }];
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  c.setState({ toast: '' });
  await c.rpRunCard('card-1');
  assert.equal(c.state.toast, '', 'the server having claimed it first is not an error the user needs');
  cleanup();
});

// ── whose reports these are ──────────────────────────────────────────────────
// A report card is personal: /api/reports filters on the caller's user_id, so the array in
// state holds the rows of whoever was signed in when it was READ. One tab routinely sees
// more than one account — sign out, sign in as someone else — and the previous person's
// pinned cards stayed in the navigator, on the home page's pinned runs and open on the
// Reports page until rpStart's 30-second poll happened to come round and correct it.

test('a report is drawn only for the account it was read for', () => {
  c.setState({ reports: [REPORT], reportsOwner: OWNER });
  assert.equal(c.renderVals().navReports.length, 1);
  // The same rows in state, somebody else looking at them: not theirs, not shown.
  c.setState({ account: { id: 'u-2', email: 'bo@example.com', full_name: 'Bo', role: 'user' } });
  const v = c.renderVals();
  assert.deepEqual(v.navReports, []);
  // Home's pinned runs are the saved cards followed by the standing suggestions; only the
  // cards are somebody's, and only they go.
  assert.equal(v.pinned.filter((x) => x.label === 'Risky buildings').length, 0);
  assert.equal(v.reportGridEmpty, true);
  assert.deepEqual(v.reportGroups, []);
  cleanup();
});

test('the account switch clears the cards, and the card left open with them', () => {
  c.setState({ reports: [REPORT], reportsOwner: OWNER, view: 'report', reportKey: 'card-1', reportSelected: ['card-1'] });
  c.resetLiveData();
  assert.deepEqual(c.state.reports, []);
  assert.equal(c.state.reportsOwner, null);
  assert.equal(c.state.reportKey, null);
  assert.deepEqual(c.state.reportSelected, []);
  cleanup();
});

test('the switch re-reads the reports itself rather than waiting for the poll', () => {
  const fired = [];
  ['ccLoad', 'homeLoad', 'vpLoad', 'bldLoad', 'energyLoad', 'enPositionLoad',
   'asLiveLoad', 'asCondLoad', 'mxLiveLoad', 'spLoad', 'rpLoad'].forEach((m) => {
    c[m] = () => { fired.push(m); };
  });
  c.loadLiveData();
  assert.ok(fired.indexOf('rpLoad') > -1, 'loadLiveData reads the reports with every other register');
  cleanup();
});

test('signing out takes the cards with it', () => {
  handlers['GET /backend/ops-intelligence/api/auth/config'] = () => [200, { ok: true }];
  c.setState({ reports: [REPORT], reportsOwner: OWNER, view: 'report', reportKey: 'card-1' });
  c.authSignedOut('');
  assert.deepEqual(c.state.reports, []);
  assert.equal(c.state.reportsOwner, null);
  assert.equal(c.state.reportKey, null);
  cleanup();
});

test('a read issued for the previous account never lands on the next one', async () => {
  handlers['GET /backend/ops-intelligence/api/reports'] = () => [200, { ok: true, count: 1, reports: [REPORT] }];
  const pending = c.rpLoad();
  // The account changes while that read is in flight.
  c.setState({ account: { id: 'u-2', email: 'bo@example.com', full_name: 'Bo', role: 'user' } });
  await pending;
  assert.deepEqual(c.state.reports, [], 'the answer to the previous account\'s question is dropped');
  assert.equal(c.state.reportsOwner, null);
  cleanup();
});
