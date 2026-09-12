// shapeLiveUser / lastActiveLabel — the Users & access screen's live rows, shaped from
// GET /api/admin/users and GET /api/admin/buildings. Fixtures mirror the exact response
// shapes transcribed from the admin router into scratchpad contract.md (12 Sep 2026):
// lowercase status, usage counters under `usage`, buildings as {id, name} pairs, and the
// invite receipt with accept_url when the email was dry-run/undelivered.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveUser, lastActiveLabel, statusLabel, usersLiveMethods } from '../src/logic/usersLive.js';

const B1 = '11111111-1111-4111-8111-111111111111';
const B2 = '22222222-2222-4222-8222-222222222222';
const U1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
const U2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const U3 = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';

// Local-time timestamps, so the humanizer's same-day/weekday reads are the same in any
// timezone the test runs in.
const local = (y, mo, d, h, mi) => new Date(y, mo, d, h, mi, 0).toISOString();
const NOW = local(2026, 8, 12, 14, 0); // 14:00 local, Sat 12 Sep 2026

test('an active server row shapes to exactly the seed shape, under the SERVER id', () => {
  const u = shapeLiveUser({
    id: U1, full_name: 'Amara Osei', email: 'amara@plenum.co', job_title: 'Facilities lead',
    status: 'active', role: 'user', can_ingest: true,
    buildings: [{ id: B1, name: 'Riverside Court' }, { id: B2, name: 'Bishopsgate Tower' }],
    building_count: 2, all_buildings: false,
    usage: { queries: 214, ingests: 38, last_active: local(2026, 8, 12, 13, 48) },
    last_login_at: local(2026, 8, 12, 13, 48), invited_at: null,
    activated_at: '2026-01-05T09:00:00Z', created_at: '2026-01-05T09:00:00Z'
  }, {}, NOW);
  assert.deepEqual(u, {
    id: U1, name: 'Amara Osei', email: 'amara@plenum.co', title: 'Facilities lead',
    buildings: ['Riverside Court', 'Bishopsgate Tower'], allB: false, buildingCount: 2,
    ingest: true, status: 'Active',
    queries: 214, ingests: 38, last: '12 min ago', live: true
  });
});

test('an invited row with nulls falls back cleanly: empty title, dash last-active', () => {
  const u = shapeLiveUser({
    id: U2, full_name: 'Jo Kim', email: 'jo@co.com', job_title: null,
    status: 'invited', role: 'user', can_ingest: false,
    buildings: [{ id: B1, name: 'Riverside Court' }], building_count: 1, all_buildings: false,
    usage: { queries: 0, ingests: 0, last_active: null },
    last_login_at: null, invited_at: NOW, activated_at: null, created_at: NOW
  }, {}, NOW);
  assert.equal(u.title, '');
  assert.equal(u.status, 'Invited');
  assert.equal(u.last, '—');
  assert.equal(u.ingest, false);
});

test('a suspended row keeps its status word and an admin keeps its empty allocation', () => {
  const susp = shapeLiveUser({ id: U3, full_name: 'Lena Ruiz', email: 'l@co.com', status: 'suspended', can_ingest: false, buildings: [], usage: {} }, {}, NOW);
  assert.equal(susp.status, 'Suspended');
  const admin = shapeLiveUser({ id: U1, full_name: 'Boss', email: 'b@co.com', status: 'active', role: 'admin', can_ingest: true, buildings: [], building_count: 8, all_buildings: true, usage: {} }, {}, NOW);
  assert.deepEqual(admin.buildings, []);
  assert.equal(admin.allB, true, 'all_buildings rides along so the table does not read "0 buildings"');
  assert.equal(admin.buildingCount, 8);
});

test('an id-only building resolves through the buildings list; an unknown id is dropped, not guessed', () => {
  const u = shapeLiveUser({
    id: U1, full_name: 'A', email: 'a@co.com', status: 'active', can_ingest: false,
    buildings: [{ id: B1 }, { id: 'not-on-record' }], usage: {}
  }, { [B1]: 'Riverside Court' }, NOW);
  assert.deepEqual(u.buildings, ['Riverside Court']);
});

test('statusLabel maps the wire vocabulary and humanises an unknown token', () => {
  assert.equal(statusLabel('active'), 'Active');
  assert.equal(statusLabel('invited'), 'Invited');
  assert.equal(statusLabel('suspended'), 'Suspended');
  assert.equal(statusLabel('locked'), 'Locked');
  assert.equal(statusLabel(null), 'Active');
});

test('lastActiveLabel speaks the seed vocabulary at every distance', () => {
  assert.equal(lastActiveLabel(null, NOW), '—');
  assert.equal(lastActiveLabel('not a date', NOW), '—');
  assert.equal(lastActiveLabel(local(2026, 8, 12, 13, 59), NOW), '1 min ago');
  assert.equal(lastActiveLabel(local(2026, 8, 12, 13, 48), NOW), '12 min ago');
  assert.equal(lastActiveLabel(local(2026, 8, 12, 13, 0), NOW), '1 hour ago');
  assert.equal(lastActiveLabel(local(2026, 8, 12, 9, 0), NOW), '5 hours ago');
  assert.equal(lastActiveLabel(local(2026, 8, 11, 16, 20), NOW), 'Yesterday');
  assert.equal(lastActiveLabel(local(2026, 8, 8, 16, 20), NOW), 'Tue'); // 8 Sep 2026
  assert.equal(lastActiveLabel(local(2026, 7, 28, 14, 47), NOW), '28 Aug 14:47');
});

// ── controller-level: the loader and the optimistic mutations ────────────
// usersLiveMethods is not yet assigned onto HoistraLogic.prototype by the integrator,
// so the tests mix it in themselves — exactly what HoistraLogic.js will do.
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
let routes = {};
const reply = (status, body) => ({
  ok: status < 300, status, statusText: String(status),
  text: () => Promise.resolve(JSON.stringify(body))
});
globalThis.fetch = (url, init) => {
  const key = ((init && init.method) || 'GET') + ' ' + new URL(url).pathname;
  const h = routes[key];
  if (!h) return Promise.reject(new TypeError('unexpected ' + key));
  return Promise.resolve(h(url, init));
};
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
Object.assign(HoistraLogic.prototype, usersLiveMethods);

// GET /api/admin/users — contract.md §1, verbatim fields.
const USERS_RES = {
  ok: true, count: 3,
  summary: { users: 3, can_ingest: 1, pending_invites: 1, access_boundary: 'building' },
  users: [
    { id: U1, full_name: 'Amara Osei', email: 'amara@plenum.co', job_title: 'Facilities lead',
      status: 'active', role: 'user', can_ingest: true,
      buildings: [{ id: B1, name: 'Riverside Court' }], building_count: 1, all_buildings: false,
      usage: { queries: 214, ingests: 38, last_active: local(2026, 8, 12, 13, 48) },
      last_login_at: local(2026, 8, 12, 13, 48), invited_at: null, activated_at: '2026-01-05T09:00:00Z', created_at: '2026-01-05T09:00:00Z' },
    { id: U2, full_name: 'Jo Kim', email: 'jo@co.com', job_title: null,
      status: 'invited', role: 'user', can_ingest: false,
      buildings: [{ id: B2, name: 'Bishopsgate Tower' }], building_count: 1, all_buildings: false,
      usage: { queries: 0, ingests: 0, last_active: null },
      last_login_at: null, invited_at: NOW, activated_at: null, created_at: NOW },
    { id: U3, full_name: 'Lena Ruiz', email: 'lena@plenum.co', job_title: 'Contractor',
      status: 'suspended', role: 'user', can_ingest: false,
      buildings: [], building_count: 0, all_buildings: false,
      usage: { queries: 12, ingests: 0, last_active: local(2026, 7, 28, 14, 47) },
      last_login_at: null, invited_at: null, activated_at: null, created_at: NOW }
  ]
};
// GET /api/admin/buildings — contract.md §2.
const BLDS_RES = {
  ok: true, count: 2,
  buildings: [
    { id: B1, name: 'Riverside Court', building_code: 'B-001' },
    { id: B2, name: 'Bishopsgate Tower', building_code: null }
  ]
};

const liveRow = () => ({
  id: U1, name: 'Amara Osei', email: 'amara@plenum.co', title: 'Facilities lead',
  buildings: ['Riverside Court'], ingest: false, status: 'Active',
  queries: 214, ingests: 38, last: '12 min ago', live: true
});

test('usLiveLoad replaces the seed in place: rows, summary tiles, live building chips', async () => {
  routes = {
    'GET /backend/ops-intelligence/api/admin/users': () => reply(200, USERS_RES),
    'GET /backend/ops-intelligence/api/admin/buildings': () => reply(200, BLDS_RES)
  };
  const c = new HoistraLogic();
  await c.usLiveLoad();
  assert.equal(c.state.users.length, 3);
  assert.equal(c.state.users[0].id, U1, 'the SERVER id, not a client mint');
  assert.ok(c.state.users.every((u) => u.live));
  assert.equal(c.state.usSummary.pending_invites, 1);
  assert.deepEqual(c.state.axBldsLive, BLDS_RES.buildings);
  assert.equal(c.state.usLiveError, '');
  assert.ok(c.state.usLiveLoadedAt);
  const v = c.usersVals(c.state);
  assert.deepEqual(v.usTiles.slice(0, 3).map((t) => t.value), ['3', '1', '1'], 'tiles read the server summary');
  assert.deepEqual(v.usFormBlds.map((b) => b.name), ['Riverside Court', 'Bishopsgate Tower'], 'chips come from axBldsLive');
  const susp = v.axUsers.find((r) => r.status === 'Suspended');
  assert.equal(susp.stFg, 'var(--st-warn)', 'a suspended pill rides the warn pair');
  assert.equal(susp.stBg, 'var(--st-warn-bg)');
});

test('a forbidden users read keeps the seed, records the error and does not arm a retry timer', async () => {
  routes = {
    'GET /backend/ops-intelligence/api/admin/users': () => reply(403, { detail: { ok: false, error: 'Admin role required — your role is user.', reason: 'forbidden', required_role: 'admin', your_role: 'user' } }),
    'GET /backend/ops-intelligence/api/admin/buildings': () => reply(200, BLDS_RES)
  };
  const c = new HoistraLogic();
  const seed = c.state.users;
  await c.usLiveLoad();
  assert.equal(c.state.users, seed, 'the seed rows are untouched');
  assert.match(c.state.usLiveError, /Admin role required/);
  assert.equal(c.state.usLiveLoadedAt, null, 'never loaded — the integrated default');
  assert.equal(c._usLiveRetry, undefined, 'a 403 is not retried on a timer');
  assert.deepEqual(c.state.axBldsLive, BLDS_RES.buildings, 'the buildings read that answered is kept');
});

test('usSend goes to POST /api/admin/users/invite when live and appends the server row', async () => {
  let sent = null;
  routes = {
    'GET /backend/ops-intelligence/api/admin/users': () => reply(200, USERS_RES),
    'GET /backend/ops-intelligence/api/admin/buildings': () => reply(200, BLDS_RES),
    'POST /backend/ops-intelligence/api/admin/users/invite': (url, init) => {
      sent = JSON.parse(init.body);
      // contract.md §3 — the dry-run receipt: accept_url + note ride back.
      return reply(201, {
        ok: true, invitation_id: 'dddddddd-dddd-4ddd-8ddd-dddddddddddd', user_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
        email: 'new@co.com', role: 'user', can_ingest: true, building_ids: [B1],
        expires_at: '2026-09-19T14:00:00Z',
        email_sent: { ok: true, status: 'dry_run', email_id: 'f0f0', to: 'new@co.com', cc: null, subject: 'You are invited', message: 'dry run' },
        accept_url: '/accept-invitation?token=tok-123', note: 'Email delivery is in dry-run — share the link.',
        buildings: [{ id: B1, name: 'Riverside Court', building_code: 'B-001' }]
      });
    }
  };
  const c = new HoistraLogic();
  await c.usLiveLoad();
  c.setState({ usInviteOpen: true, usName: 'New Person', usEmail: 'new@co.com', usBlds: ['Riverside Court'], usIngest: true });
  await c.usersVals(c.state).usSend();
  assert.deepEqual(sent, { full_name: 'New Person', email: 'new@co.com', building_ids: [B1], can_ingest: true }, 'names resolved to ids through axBldsLive');
  const added = c.state.users.find((u) => u.id === 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee');
  assert.ok(added, 'the appended row carries the SERVER user_id');
  assert.equal(added.status, 'Invited');
  assert.deepEqual(added.buildings, ['Riverside Court']);
  assert.equal(added.live, true);
  assert.equal(c.state.usInviteOpen, false, 'the form closed and cleared');
  assert.equal(c.state.usName, '');
  assert.match(c.state.toast, /accept-invitation\?token=tok-123/, 'the undelivered-email link is surfaced');
  clearTimeout(c._usLiveRefresh);
  clearTimeout(c._tt);
});

test('a failed can_ingest PATCH reverts the optimistic flip and flashes the ApiError message', async () => {
  routes = {
    ['PATCH /backend/ops-intelligence/api/admin/users/' + U1]: () => reply(500, { detail: { ok: false, error: 'database unavailable', reason: 'db_down' } })
  };
  const c = new HoistraLogic();
  c.setState({ users: [liveRow()], usLiveLoadedAt: NOW, axBldsLive: BLDS_RES.buildings });
  const p = c.usersVals(c.state).axUsers[0].toggleIngest(null);
  assert.equal(c.state.users[0].ingest, true, 'flipped before the call answers');
  await p;
  assert.equal(c.state.users[0].ingest, false, 'reverted on failure');
  assert.match(c.state.toast, /Could not change ingest .* database unavailable/);
  clearTimeout(c._tt);
});

test('rapid allocation clicks are latest-wins and serialised: full id lists in click order, stale settles ignored, newest failure reverts to the acknowledged list', async () => {
  const sent = [];
  const pending = [];
  routes = {
    ['PATCH /backend/ops-intelligence/api/admin/users/' + U1]: (url, init) => {
      sent.push(JSON.parse(init.body).building_ids);
      return new Promise((resolve) => pending.push(resolve));
    }
  };
  const c = new HoistraLogic();
  c.setState({ users: [liveRow()], usLiveLoadedAt: NOW, axBldsLive: BLDS_RES.buildings });
  // Click 1 adds Bishopsgate, click 2 removes Riverside — before anything settles.
  const p1 = c.usersVals(c.state).axUsers[0].alloc.find((b) => b.name === 'Bishopsgate Tower').pick();
  const p2 = c.usersVals(c.state).axUsers[0].alloc.find((b) => b.name === 'Riverside Court').pick();
  assert.deepEqual(c.state.users[0].buildings, ['Bishopsgate Tower'], 'both toggles applied optimistically');
  // Sends are serialised — the server applies full-replacement lists in ARRIVAL order,
  // so only one PATCH is in flight and dispatch order is click order.
  await Promise.resolve();
  assert.deepEqual(sent, [[B1, B2]], 'the second PATCH waits for the first to settle');
  // The stale first call succeeds — nothing moves, but the server acknowledged [B1, B2].
  pending[0](reply(200, { ok: true, user_id: U1, changed: { building_ids: [B1, B2] } }));
  await p1;
  assert.deepEqual(sent, [[B1, B2], [B2]], 'every click sends the COMPLETE list it produced, in order');
  assert.deepEqual(c.state.users[0].buildings, ['Bishopsgate Tower'], 'a stale success changes nothing on screen');
  // The newest call fails — revert to the last list the server acknowledged.
  pending[1](reply(400, { detail: { ok: false, error: 'foreign_buildings', reason: 'foreign_buildings', building_ids: [B2] } }));
  await p2;
  assert.deepEqual(c.state.users[0].buildings, ['Riverside Court', 'Bishopsgate Tower'], 'reverted to what the server holds, not a half-applied list');
  assert.match(c.state.toast, /Allocation not saved/);
  clearTimeout(c._tt);
});

test('without a live load the screen stays the offline demo: local invite, local toggles, seed chips', async () => {
  routes = {}; // any network call would throw 'unexpected'
  const c = new HoistraLogic();
  const v = c.usersVals(c.state);
  assert.deepEqual(v.usFormBlds.map((b) => b.name).slice(0, 2), ['Bishopsgate Tower', 'Riverside Court'], 'AX_BUILDINGS still feeds the chips');
  assert.equal(v.usLiveSourceLabel, 'Sample data — backend not read yet');
  const before = c.state.users.length;
  c.setState({ usName: 'Demo', usEmail: 'demo@co.com', usBlds: ['Town Hall'] });
  c.usersVals(c.state).usSend();
  assert.equal(c.state.users.length, before + 1, 'the local append still works');
  assert.equal(c.state.users[before].status, 'Invited');
  c.usersVals(c.state).axUsers[0].toggleIngest(null);
  assert.notEqual(c.state.users[0].ingest, undefined, 'the seed toggle flips locally without a network call');
  clearTimeout(c._tt);
});
