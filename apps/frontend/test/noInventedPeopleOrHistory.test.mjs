// Users & access and the Audit trail never show invented rows as real ones.
//
// The same fault the Super Admin console had. Both mounted holding demo data and showed it
// until their read answered:
//
//   Users   five people — Amara Osei, Daniel Reyes, Priya Nair, Tom Whitfield, Sofia
//           Lindqvist — with @plenum-tech.com addresses, job titles, building allocations
//           and query counts. A pill beside the table said "Sample data", but a table of
//           named people with email addresses is read as a staff list whatever the pill says.
//
//   Audit   entries attributing decisions to those same people: "Marcus Hale · Overridden ·
//           NovaClean FM is not an approved vendor for Town Hall". Of every screen here this
//           is the worst one to invent rows on, because its whole purpose is to be the
//           account of record.
//
// The demo rows still exist for a console that cannot reach its backend. What changed is
// that they now appear only in that state, where the pill beside them says so.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { AX_USERS, AU_SEED } = await import('../src/data/hoistra-access.js');
const { usersMethods } = await import('../src/logic/users.js');
const { auditMethods } = await import('../src/logic/auditTrail.js');

const PEOPLE = AX_USERS.map((u) => u.name);
const ACTORS = [...new Set(AU_SEED.map((a) => a.who))];

const call = (methods, name, state) =>
  methods[name].call({ state, setState() {} }, state);

// ── Users ──────────────────────────────────────────────────────────────────────
const USERS_FRESH = {
  signedIn: true, view: 'users', users: [], usSummary: null,
  usLiveLoading: true, usLiveError: '', usLiveLoadedAt: null,
  axBldsLive: null, usBlds: [], account: { email: 'me@x.com' },
  usName: '', usEmail: '', usArmed: null, usOpen: null, usInviteOpen: false,
};

test('no invented people before the users read answers', () => {
  const v = call(usersMethods, 'usersVals', USERS_FRESH);
  const rendered = JSON.stringify(v);
  for (const name of PEOPLE) {
    assert.ok(!rendered.includes(name), `${name} is on screen before anything was read`);
  }
});

test('the user tiles do not claim a company with no staff', () => {
  const v = call(usersMethods, 'usersVals', USERS_FRESH);
  assert.equal(v.usTiles[0].value, '…', 'it reported 0 users while the read was in flight');
  assert.match(v.usLiveSourceLabel, /Reading|Not read yet/);
});

test('a real user list is reported as live, with real counts', () => {
  const v = call(usersMethods, 'usersVals', {
    ...USERS_FRESH, usLiveLoading: false, usLiveLoadedAt: '2026-09-23T10:00:00Z',
    users: [{ id: 'u', name: 'A Real Person', email: 'a@b.c', buildings: [], ingest: true, status: 'Active' }],
  });
  assert.equal(v.usTiles[0].value, '1');
  assert.match(v.usLiveSourceLabel, /Live/);
});

test('sample people are never on screen without the pill that explains them', () => {
  const v = call(usersMethods, 'usersVals', {
    ...USERS_FRESH, usLiveLoading: false, usLiveError: 'Failed to fetch',
    users: AX_USERS.map((u) => ({ ...u, buildings: u.buildings.slice() })),
  });
  assert.ok(JSON.stringify(v).includes(PEOPLE[0]), 'the fallback still fills a blank table');
  assert.match(v.usLiveSourceLabel, /showing sample data/,
    'invented staff were listed with nothing saying they were samples');
});

// ── Audit ──────────────────────────────────────────────────────────────────────
const AUDIT_FRESH = {
  signedIn: true, view: 'audit', audit: [], auFilter: 'All', auOpen: null,
  auLiveLoading: true, auLiveError: '', auLiveLoadedAt: null,
  auTotal: 0, auByOutcome: {}, auActors: [], auRange: 'Today', auQuery: '',
  auNow: '2026-09-23T10:00:00Z',
};

test('no invented history before the audit read answers', () => {
  const v = call(auditMethods, 'auditVals', AUDIT_FRESH);
  const rendered = JSON.stringify(v);
  for (const who of ACTORS) {
    assert.ok(!rendered.includes(who), `${who} is credited with a decision nobody made`);
  }
});

test('an unread register is not reported as sample data, nor as an empty one', () => {
  const v = call(auditMethods, 'auditVals', AUDIT_FRESH);
  assert.match(v.auLiveSourceLabel, /Reading|Not read yet/);
});

test('sample entries are never on screen without the pill that explains them', () => {
  const v = call(auditMethods, 'auditVals', {
    ...AUDIT_FRESH, auLiveLoading: false, auLiveError: 'Failed to fetch',
    audit: AU_SEED.slice(), auRange: 'All time',
  });
  assert.match(v.auLiveSourceLabel, /showing sample data/,
    'invented audit entries were listed with nothing saying they were samples');
});

test('a genuinely empty register still reads as live, not as unread', () => {
  // The distinction that was already fixed once here: production held no events at all and
  // the page said "Sample data · 0 of 0", telling the reader the register never answered.
  const v = call(auditMethods, 'auditVals', {
    ...AUDIT_FRESH, auLiveLoading: false, auLiveLoadedAt: '2026-09-23T10:00:00Z',
  });
  assert.match(v.auLiveSourceLabel, /Live/);
});
