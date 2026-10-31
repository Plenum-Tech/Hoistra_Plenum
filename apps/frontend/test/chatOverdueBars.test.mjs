// chatOverdueBars — the "Days overdue" bars under a compliance answer say which certificate each
// bar is, and how many were left out (finding 30).
//
// One bar per certificate, labelled only by its owner, drew two identical "Building — Bishopsgate
// Tower" bars for its FRA and its EICR; and twelve lapsed certificates drew eight bars with nothing
// to say four more existed. The chart's title already says buildings or vendors, so the label is
// owner and certificate.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { overdueBars } from '../src/logic/complianceLive.js';

const REGISTER = { certs: [{ id: 'c1', days: -120 }, { id: 'c2', days: -40 }, { id: 'c3', days: -9 }, { id: 'c4', days: -9 }] };

test('two certificates of one owner are two bars that say which certificate each is', () => {
  const bars = overdueBars([
    { id: 'c1', scope: 'building', company: 'Bishopsgate Tower', name: 'Fire Risk Assessment', status: 'lapsed' },
    { id: 'c2', scope: 'building', company: 'Bishopsgate Tower', name: 'EICR', status: 'lapsed' },
    { id: 'c3', scope: 'vendor', company: 'Clearwater Compliance', name: 'Public liability insurance', status: 'lapsed' },
    { id: 'c4', scope: 'vendor', company: 'Clearwater Compliance', name: 'Employers liability insurance', status: 'lapsed' }
  ], REGISTER);
  assert.deepEqual(bars.buildings.map((b) => b.label), ['Bishopsgate Tower · Fire Risk Assessment', 'Bishopsgate Tower · EICR']);
  assert.deepEqual(bars.vendors.map((b) => b.label), ['Clearwater Compliance · Public liability insurance', 'Clearwater Compliance · Employers liability insurance']);
  assert.deepEqual(bars.more, { buildings: 0, vendors: 0 });
});

test('a certificate with no owner on record is labelled by its own name', () => {
  const bars = overdueBars([{ id: 'c1', scope: 'building', company: '', name: 'Gas safety', status: 'lapsed' }], REGISTER);
  assert.equal(bars.buildings[0].label, 'Gas safety');
});

test('ten lapsed certificates of one scope draw eight bars and say two more', () => {
  const certs = Array.from({ length: 10 }, (_, i) => ({ id: 'x' + i, scope: 'building', company: 'B' + i, name: 'FRA', status: 'lapsed', reason: 'lapsed ' + (i + 1) + ' days ago' }));
  const bars = overdueBars(certs, { certs: [] });
  assert.equal(bars.buildings.length, 8);
  assert.equal(bars.more.buildings, 2);
  assert.equal(bars.more.vendors, 0);
  // The longest overdue are the ones drawn.
  assert.equal(bars.buildings[0].days, 10);
});
