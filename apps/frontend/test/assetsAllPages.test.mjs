// The Assets page reads every work order and asset, and counts the open ones it finds.
//
// 28 Sep 2026, Plenum Technologies / Bishopsgate: 1,985 work orders in the database, and the
// page read the newest 200 (GET /api/work-orders/ caps a page at 200) and said nothing about
// the rest. Of those it counted a job as open only when its status was exactly one of
// pending_approval / preparing / prepared / active — and a migrated workbook writes "In progress",
// so the one job in progress counted as none. Every asset read "0 open work orders".
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { readAllPages } = await import('../src/logic/assetsLive.js');
const { isOpenWorkOrder } = await import('../src/logic/assetsCondition.js');

const pager = (total, size) => {
  const asked = [];
  const fetchPage = async (page, limit) => {
    asked.push({ page, limit });
    const start = (page - 1) * limit;
    return Array.from({ length: Math.max(0, Math.min(limit, total - start)) }, (_, i) => ({ n: start + i }));
  };
  return { asked, fetchPage };
};

test('every page is read until a short one, not just the first', async () => {
  const p = pager(1985, 200);
  const out = await readAllPages(p.fetchPage, { size: 200 });
  assert.equal(out.rows.length, 1985);
  assert.equal(out.complete, true);
  assert.deepEqual(p.asked.map((a) => a.page), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
});

test('an exact multiple of the page size stops at the empty page after it', async () => {
  const p = pager(400, 200);
  const out = await readAllPages(p.fetchPage, { size: 200 });
  assert.equal(out.rows.length, 400);
  assert.equal(out.complete, true);
  assert.equal(p.asked.length, 3);
});

test('a read past the page ceiling says it is not complete', async () => {
  const p = pager(5000, 200);
  const out = await readAllPages(p.fetchPage, { size: 200, maxPages: 3 });
  assert.equal(out.rows.length, 600);
  assert.equal(out.complete, false);
});

test('a page that fails fails the read, rather than passing off the pages before it as all of them', async () => {
  const fetchPage = async (page) => { if (page === 2) throw new Error('502'); return Array.from({ length: 200 }, () => ({})); };
  await assert.rejects(readAllPages(fetchPage, { size: 200 }), /502/);
});

test('a migrated "In progress" is open, however it is spelt', () => {
  // A draft is awaiting approval in the work-order service's own vocabulary (maintenance.py
  // AWAITING, and GET /api/work-orders/?open=true), so it is open here too — the row's count
  // and the notes listed under it read the same definition.
  ['In progress', 'in_progress', 'IN-PROGRESS', 'open', 'Assigned', 'On hold', 'on_hold', 'active',
    'pending_approval', 'Prepared', 'Draft', 'awaiting_parts']
    .forEach((st) => assert.equal(isOpenWorkOrder(st), true, st));
});

test('finished and cancelled work orders are not open', () => {
  ['Completed', 'complete', 'Closed', 'done', 'Cancelled', 'canceled', 'Rejected', '', null, undefined]
    .forEach((st) => assert.equal(isOpenWorkOrder(st), false, String(st)));
});


test('a row a tied sort repeats across pages is kept once', async () => {
  // Offset pages over a non-unique sort can hand back a row twice and another not at all;
  // the server now breaks ties on the key, and the reader drops any repeat it still sees.
  const pages = { 1: [{ id: 'a' }, { id: 'b' }], 2: [{ id: 'b' }, { id: 'c' }], 3: [{ id: 'd' }] };
  const out = await readAllPages(async (page) => pages[page] || [], { size: 2, key: (r) => r.id });
  assert.deepEqual(out.rows.map((r) => r.id), ['a', 'b', 'c', 'd']);
  assert.equal(out.complete, true);
});
