// memoriesPage — Administration › Chat memory: what the page shows from the service's rows,
// the filters, and that Forget takes two clicks.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { memoriesPageVals, fmtAgo, MEMORY_FILTERS } from '../src/logic/memoriesPage.js';

const NOW = Date.parse('2026-10-02T12:00:00Z');
const ROWS = [
  { id: 'm1', kind: 'fact', text: 'At Bishopsgate Tower the plant room is on level 3.', subject: 'Bishopsgate Tower', user_id: null,
    created_by_email: 'ops@example.com', created_at: '2026-10-01T12:00:00Z', last_used_at: '2026-10-02T11:30:00Z', use_count: 3 },
  { id: 'm2', kind: 'correction', text: 'Apex Mechanical, not Apex Lifts, services the AHUs.', subject: null, user_id: null,
    created_by_email: 'fm@example.com', created_at: '2026-10-02T11:58:00Z', last_used_at: null, use_count: 0 },
  { id: 'm3', kind: 'preference', text: 'The user prefers costs in AED.', subject: null, user_id: 'u1',
    created_by_email: 'fm@example.com', created_at: '2026-09-30T12:00:00Z', last_used_at: null, use_count: 1 }
];

function ctl(state) {
  const calls = [];
  const c = { state: Object.assign({ signedIn: true, view: 'memories', mpRows: ROWS, mpLoadedAt: 1 }, state) };
  ['mpLoad', 'mpSetFilter', 'mpSetSearch', 'mpForget'].forEach((m) => { c[m] = (...a) => calls.push([m, ...a]); });
  return { c, calls };
}

test('the tiles count company memories, the admin\'s own preferences and recalls', () => {
  const { c } = ctl();
  const v = memoriesPageVals(c, NOW);
  assert.equal(v.isMemories, true);
  assert.deepEqual(v.mpTiles.map((t) => [t.label, t.value]), [
    ['Company memories', '2'], ['Your preferences', '1'], ['Times recalled', '4'], ['Contributors', '2'], ['Awaiting approval', '0']]);
  assert.equal(v.mpTiles[0].hint, '1 facts · 1 corrections');
});

test('rows read as the page shows them, and Forget arms before it removes', () => {
  const { c, calls } = ctl({ mpArmed: 'm2' });
  const v = memoriesPageVals(c, NOW);
  const r = v.mpRows.find((x) => x.id === 'm1');
  assert.equal(r.kind, 'Fact');
  assert.equal(r.scope, 'Company');
  assert.equal(r.who, 'ops@example.com');
  assert.equal(r.when, '1 day ago');
  assert.equal(r.used, 3);
  assert.equal(r.lastUsed, '30 min ago');
  assert.equal(r.forgetLabel, 'Forget');
  const mine = v.mpRows.find((x) => x.id === 'm3');
  assert.equal(mine.scope, 'You');
  assert.equal(mine.who, 'you');
  const armed = v.mpRows.find((x) => x.id === 'm2');
  assert.equal(armed.armed, true);
  assert.equal(armed.forgetLabel, 'Click again to forget');
  assert.equal(armed.when, '2 min ago');
  armed.forget({ stopPropagation() {} });
  assert.deepEqual(calls, [['mpForget', 'm2']]);
});

test('the filters narrow by kind and the search by text, subject or who said it', () => {
  assert.deepEqual(MEMORY_FILTERS, ['All', 'Facts', 'Corrections', 'My preferences']);
  assert.deepEqual(memoriesPageVals(ctl({ mpFilter: 'Corrections' }).c, NOW).mpRows.map((r) => r.id), ['m2']);
  assert.deepEqual(memoriesPageVals(ctl({ mpFilter: 'My preferences' }).c, NOW).mpRows.map((r) => r.id), ['m3']);
  assert.deepEqual(memoriesPageVals(ctl({ mpSearch: 'bishopsgate' }).c, NOW).mpRows.map((r) => r.id), ['m1']);
  assert.deepEqual(memoriesPageVals(ctl({ mpSearch: 'ops@' }).c, NOW).mpRows.map((r) => r.id), ['m1']);
  const none = memoriesPageVals(ctl({ mpSearch: 'zzz' }).c, NOW);
  assert.equal(none.mpNoMatch, true);
  assert.equal(none.mpEmpty, false);
  const empty = memoriesPageVals(ctl({ mpRows: [] }).c, NOW);
  assert.equal(empty.mpEmpty, true);
  assert.equal(empty.mpTiles[2].value, '0');
});

test('elapsed time lands in the page\'s bands', () => {
  assert.equal(fmtAgo('2026-10-02T11:59:30Z', NOW), 'just now');
  assert.equal(fmtAgo('2026-10-02T09:00:00Z', NOW), '3 hours ago');
  assert.equal(fmtAgo('2026-09-25T12:00:00Z', NOW), '7 days ago');
  assert.equal(fmtAgo(null, NOW), '');
});


test('pending teachings sit above the table with the decision beside each, and count on a tile', async () => {
  const { memoriesPageVals } = await import('../src/logic/memoriesPage.js');
  const reviewed = [];
  const c = { state: { signedIn: true, view: 'memories', mpLoadedAt: 1, mpCanManage: true, mpRows: [
    { id: 'p1', kind: 'correction', text: 'Repurchase means parts below reorder level.', status: 'pending', created_by_email: 'aasim@x', created_at: '2026-10-05T08:00:00Z' },
    { id: 'a1', kind: 'correction', text: 'Count only open jobs.', status: 'active', created_by_email: 'bala@x', reviewed_by_email: 'admin@x', created_at: '2026-10-05T08:00:00Z' },
    { id: 'm1', kind: 'preference', text: 'Costs in AED.', status: 'active', user_id: 'u', created_at: '2026-10-05T08:00:00Z' }
  ] }, mpReview: (id, d) => reviewed.push([id, d]), mpForget: () => {}, mpLoad: () => {}, mpSetFilter: () => {}, mpSetSearch: () => {} };
  const v = memoriesPageVals(c, Date.parse('2026-10-05T09:00:00Z'));
  assert.equal(v.mpPending.length, 1);
  assert.equal(v.mpPending[0].who, 'aasim@x');
  assert.deepEqual(v.mpRows.map((r) => r.id), ['a1', 'm1']);
  assert.equal(v.mpRows[0].approvedBy, 'approved by admin@x');
  assert.equal(v.mpTiles.find((t) => t.label === 'Awaiting approval').value, '1');
  assert.equal(v.mpTiles.find((t) => t.label === 'Company memories').value, '1');
  v.mpPending[0].approve();
  v.mpPending[0].reject();
  assert.deepEqual(reviewed, [['p1', 'approve'], ['p1', 'reject']]);
  assert.equal(v.mpCanManage, true);
});
