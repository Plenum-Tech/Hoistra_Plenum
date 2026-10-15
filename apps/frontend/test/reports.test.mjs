// reports — a custom report card is a pinned prompt, re-run on a cadence by the server
// (svc-operations-intelligence's /api/reports). The pure core here: the fallback preset
// list, the day labels, the exported markdown, the status badge, and flattening reports
// into cards.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local' } };

const { FALLBACK_PRESETS, DAYS, cardStatusBadge, flattenCards } = await import('../src/logic/reports.js');

test('the fallback preset list is what the navigator menu offers before the backend answers', () => {
  assert.deepEqual(FALLBACK_PRESETS.map((c) => c.badge), ['30 min', '1 hr', '6 hr', '12 hr', '24 hr', 'Daily', 'Days']);
  assert.deepEqual(DAYS, ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']);
  assert.equal(FALLBACK_PRESETS.find((c) => c.key === 'days').pick_days, true);
});

test('flattenCards pulls every card out of every report, keeping the parent report name', () => {
  const reports = [
    { id: 'r1', name: 'My report', cards: [{ id: 'c1', name: 'A' }, { id: 'c2', name: 'B' }] },
    { id: 'r2', name: 'Other', cards: [{ id: 'c3', name: 'C' }] }
  ];
  const cards = flattenCards(reports);
  assert.equal(cards.length, 3);
  assert.deepEqual(cards.map((c) => c.id), ['c1', 'c2', 'c3']);
  assert.equal(cards[0].report_name, 'My report');
  assert.equal(cards[2].report_name, 'Other');
  assert.deepEqual(flattenCards(null), []);
  assert.deepEqual(flattenCards([{ id: 'r1', name: 'n' }]), [], 'a report with no cards field contributes nothing');
});

test('cardStatusBadge reads the in-flight states, and falls back to the refresh label once ready', () => {
  assert.equal(cardStatusBadge({ status: 'pending' }), 'Pending');
  assert.equal(cardStatusBadge({ status: 'running' }), 'Running');
  assert.equal(cardStatusBadge({ status: 'error' }), 'Failed');
  assert.equal(cardStatusBadge({ status: 'paused' }), 'Paused');
  assert.equal(cardStatusBadge({ status: 'ready', refresh_label: 'Refresh every 1 hour' }), 'Refresh every 1 hour');
  assert.equal(cardStatusBadge(null), '');
});
