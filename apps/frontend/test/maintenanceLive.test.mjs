// shapeLiveWorkOrder / shapeLiveDecisions / shapeDashboardTiles — the Maintenance page's
// live decisions grid and KPI tiles, shaped from svc-work-order-management's real responses
// (GET /api/work-orders/, GET /api/dashboard/stats). Fixtures mirror WorkOrderResponse and
// DashboardStats exactly as the service defines them (src/api/schemas/work_order.py,
// src/api/schemas/journey.py) — WorkOrderResponse carries no estimated_cost field even
// though the underlying table column exists, so a live row's estimate is always "—".
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveWorkOrder, shapeLiveDecisions, shapeDashboardTiles } from '../src/logic/maintenanceLive.js';

test('a pending-approval work order shapes into an Awaiting approval decision row', () => {
  const wo = {
    work_order_id: 'WO-20260911120000000000', source: 'tenant', status: 'pending_approval',
    priority: 'high', asset: 'AHU-04', location: 'Marina Heights — Plant Room 2',
    issue_description: 'Belt slipping, audible squeal', vendor: null, created_at: '2026-09-11T08:00:00Z'
  };
  const d = shapeLiveWorkOrder(wo);
  assert.equal(d.id, 'WO-20260911120000000000');
  assert.equal(d.asset, 'AHU-04');
  assert.equal(d.b, 'Marina Heights — Plant Room 2');
  assert.equal(d.state, 'Awaiting approval');
  assert.equal(d.vendor, '—');
  assert.equal(d.est, '—');
  assert.equal(d.src, 'Work order');
  assert.equal(d.detail, 'Belt slipping, audible squeal');
  assert.deepEqual(d.actions, []);
});

test('an unrecognised status still renders a readable label instead of throwing', () => {
  const d = shapeLiveWorkOrder({ work_order_id: 'WO-1', status: 'some_new_status', asset: null, location: null });
  assert.equal(d.state, 'some_new_status');
  assert.equal(d.asset, '—');
  assert.equal(d.b, '—');
});

test('shapeLiveDecisions drops completed and closed work orders', () => {
  const rows = shapeLiveDecisions([
    { work_order_id: 'WO-1', status: 'pending_approval', created_at: '2026-09-10T00:00:00Z' },
    { work_order_id: 'WO-2', status: 'completed', created_at: '2026-09-09T00:00:00Z' },
    { work_order_id: 'WO-3', status: 'closed', created_at: '2026-09-08T00:00:00Z' },
    { work_order_id: 'WO-4', status: 'active', created_at: '2026-09-11T00:00:00Z' }
  ]);
  assert.deepEqual(rows.map((r) => r.id), ['WO-4', 'WO-1']);
});

test('shapeLiveDecisions on an empty or missing list returns an empty array, not a crash', () => {
  assert.deepEqual(shapeLiveDecisions([]), []);
  assert.deepEqual(shapeLiveDecisions(null), []);
});

test('shapeDashboardTiles turns raw counts into the four KPI tiles', () => {
  const tiles = shapeDashboardTiles({
    total: 42,
    by_status: { pending_approval: 5, preparing: 2, prepared: 1, active: 3, completed: 20, closed: 11 },
    by_priority: { low: 10, medium: 20, high: 8, urgent: 3, critical: 1 },
    by_source: { tenant: 30, ppm: 12 },
    created_today: 4
  });
  assert.equal(tiles.length, 4);
  assert.equal(tiles[0].v, '11'); // open = pending_approval + preparing + prepared + active
  assert.equal(tiles[1].v, '5');  // pending_approval
  assert.equal(tiles[2].v, '4');  // urgent + critical
  assert.equal(tiles[2].tone, 'risk');
  assert.equal(tiles[3].v, '4');  // created_today
});

test('shapeDashboardTiles on no urgent/critical work uses an ok tone, not risk', () => {
  const tiles = shapeDashboardTiles({ total: 5, by_status: {}, by_priority: { low: 5 }, by_source: {}, created_today: 0 });
  assert.equal(tiles[2].v, '0');
  assert.equal(tiles[2].tone, 'ok');
});

test('shapeDashboardTiles returns null when there is nothing to show', () => {
  assert.equal(shapeDashboardTiles(null), null);
});
