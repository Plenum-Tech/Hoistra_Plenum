// sessions — the record behind every conversation with the orchestrator, and the list the
// navigator and the Sessions page read. Pure functions only; the controller methods are
// covered in store.test.mjs.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const {
  ago, makeSession, syncTurns, spaceKeyOf, sessionIcon, trimSessions,
  loadSessions, saveSessions, shapeSessionList, MAX_TURNS, MAX_SESSIONS, SESSIONS_KEY
} = await import('../src/logic/sessions.js');

// 07 Sep 2026, 14:00 local.
const NOW = new Date(2026, 8, 7, 14, 0, 0).getTime();
const MIN = 60000, HOUR = 60 * MIN, DAY = 24 * HOUR;

const memStorage = () => {
  const m = {};
  return { getItem: (k) => (k in m ? m[k] : null), setItem: (k, v) => { m[k] = String(v); }, removeItem: (k) => { delete m[k]; }, _m: m };
};

test('ago reads in the bands the navigator shows', () => {
  assert.equal(ago(NOW - 10000, NOW), 'just now');
  assert.equal(ago(NOW - 1 * MIN, NOW), '1 min ago');
  assert.equal(ago(NOW - 12 * MIN, NOW), '12 mins ago');
  assert.equal(ago(NOW - 2 * HOUR, NOW), '2 hours ago');
  assert.equal(ago(NOW - 3 * DAY, NOW), '3 days ago');
  assert.equal(ago(null, NOW), '');
});

test('a new chat session carries the first question as its title and label', () => {
  const s = makeSession({ id: 'abc', title: 'Which buildings put me at risk?', page: 'Home', at: NOW });
  assert.equal(s.kind, 'chat');
  assert.equal(s.label, 'Which buildings put me at risk?');
  assert.equal(s.task, 'Which buildings put me at risk?');
  assert.equal(s.page, 'Home');
  assert.equal(s.createdAt, NOW);
  assert.deepEqual(s.turns, []);
  assert.equal(s.spaceId, null);
});

test('syncTurns keeps the last 40 messages and reads the engine off the tools used', () => {
  const rec = makeSession({ id: 'abc', title: 'q', page: 'Home', at: NOW - HOUR });
  const turns = [];
  for (let i = 0; i < 50; i++) turns.push({ role: 'you', text: 'q' + i });
  turns.push({ role: 'bot', text: 'a', calls: ['get_energy_anomalies', 'get_energy_anomalies'] });
  const out = syncTurns(rec, turns, NOW);
  assert.equal(out.turns.length, MAX_TURNS);
  assert.equal(out.turns[out.turns.length - 1].role, 'bot');
  assert.deepEqual(out.calls, ['get_energy_anomalies']);
  assert.equal(out.domain, 'Energy');
  assert.equal(out.at, NOW);
  // The input record is not mutated.
  assert.equal(rec.turns.length, 0);
});

test('a structured compliance answer files the session under Compliance', () => {
  const rec = makeSession({ id: 'x', title: 'q', page: 'Compliance', at: NOW });
  const out = syncTurns(rec, [{ role: 'you', text: 'q' }, { role: 'bot', text: '', rich: { narrative: 'n' }, calls: [] }], NOW);
  assert.equal(out.domain, 'Compliance');
  assert.equal(spaceKeyOf(out.domain), 'compliance');
  assert.equal(sessionIcon(out), 'ph-shield-check');
});

test('spaceKeyOf maps engine domains to the built-in spaces and nothing else', () => {
  assert.equal(spaceKeyOf('Compliance'), 'compliance');
  assert.equal(spaceKeyOf('Energy'), 'energy');
  assert.equal(spaceKeyOf('Vendors'), 'vendors');
  assert.equal(spaceKeyOf('Work orders'), 'ops');
  assert.equal(spaceKeyOf('Documents'), null);
  assert.equal(spaceKeyOf('Orchestrator'), null);
});

test('trimSessions keeps the newest 60', () => {
  const list = [];
  for (let i = 0; i < 70; i++) list.push(makeSession({ id: 's' + i, title: 't', page: 'Home', at: NOW - i * MIN }));
  const out = trimSessions(list.slice().reverse());
  assert.equal(out.length, MAX_SESSIONS);
  assert.equal(out[0].id, 's0');
});

test('sessions round-trip through storage and bad rows are dropped', () => {
  const st = memStorage();
  const a = makeSession({ id: 'a', title: 'first', page: 'Home', at: NOW });
  assert.equal(saveSessions([a], st), true);
  st._m[SESSIONS_KEY] = JSON.stringify([JSON.parse(st._m[SESSIONS_KEY])[0], { id: 'no-title' }, 'junk', null]);
  const back = loadSessions(st);
  assert.equal(back.length, 1);
  assert.equal(back[0].id, 'a');
  assert.equal(back[0].title, 'first');
  assert.deepEqual(loadSessions({ getItem: () => 'not json' }), []);
  assert.deepEqual(loadSessions({ getItem: () => { throw new Error('blocked'); } }), []);
});

test('saveSessions shrinks what it stores when the browser refuses the size', () => {
  let failures = 2;
  const st = memStorage();
  const strict = {
    getItem: st.getItem,
    setItem: (k, v) => { if (failures > 0) { failures -= 1; throw new Error('QuotaExceededError'); } st.setItem(k, v); },
    removeItem: st.removeItem
  };
  const list = [];
  for (let i = 0; i < 12; i++) {
    const s = makeSession({ id: 's' + i, title: 't' + i, page: 'Home', at: NOW - i * MIN });
    list.push(syncTurns(s, [{ role: 'you', text: 'q' }, { role: 'bot', text: 'a', trace: [{ big: 'x'.repeat(100) }] }], NOW - i * MIN));
  }
  assert.equal(saveSessions(list, strict), true);
  const stored = JSON.parse(st._m[SESSIONS_KEY]);
  assert.equal(stored.length, 12, 'the titles all survive');
  // First shrink drops traces from all but the newest five, the second drops the turns of
  // everything beyond the newest ten.
  assert.ok(stored[0].turns[1].trace.length === 1, 'the newest keeps its trace');
  assert.equal(stored[7].turns[1].trace, undefined, 'older sessions lose their trace');
  assert.deepEqual(stored[11].turns, [], 'the oldest keep only their title');
  // A storage that never accepts reports failure rather than throwing.
  assert.equal(saveSessions(list, { setItem: () => { throw new Error('nope'); } }), false);
});

test('shapeSessionList groups by day, newest first, and filters by text and space', () => {
  const s1 = syncTurns(makeSession({ id: '1', title: 'Which vendors are blocked?', page: 'Vendors', at: NOW - 5 * MIN }),
    [{ role: 'you', text: 'Which vendors are blocked?' }, { role: 'bot', text: 'a', calls: ['get_vendor_scorecards'] }], NOW - 5 * MIN);
  const s2 = syncTurns(makeSession({ id: '2', title: 'Why did Bishopsgate spike?', page: 'Home', at: NOW - DAY }),
    [{ role: 'you', text: 'Why did Bishopsgate spike?' }, { role: 'bot', text: 'a', calls: ['get_energy_anomalies'] }], NOW - DAY);
  const s3 = Object.assign(makeSession({ id: '3', title: 'Tower 3 certificates', page: 'Home', at: NOW - 3 * DAY }), { spaceId: 'space-uuid' });
  const t1 = makeSession({ id: 't', title: 'Create report', page: 'Reports', at: NOW - 2 * MIN, kind: 'task' });

  const all = shapeSessionList([s2, s3, s1, t1], { nowMs: NOW });
  assert.deepEqual(all.map((g) => g.day), ['Today', 'Yesterday', '04 Sep']);
  assert.deepEqual(all[0].rows.map((r) => r.id), ['t', '1']);
  assert.equal(all[0].rows[1].when, '5 mins ago');
  assert.equal(all[0].rows[1].domain, 'Vendors');
  assert.equal(all[0].rows[1].turns, 1);
  assert.equal(all[0].rows[1].page, 'Vendors');

  const q = shapeSessionList([s1, s2, s3], { query: 'bishops', nowMs: NOW });
  assert.deepEqual(q.flatMap((g) => g.rows.map((r) => r.id)), ['2']);

  const byBuiltin = shapeSessionList([s1, s2, s3], { space: 'vendors', nowMs: NOW });
  assert.deepEqual(byBuiltin.flatMap((g) => g.rows.map((r) => r.id)), ['1']);

  const byCustom = shapeSessionList([s1, s2, s3], { space: 'space-uuid', nowMs: NOW });
  assert.deepEqual(byCustom.flatMap((g) => g.rows.map((r) => r.id)), ['3']);
});
