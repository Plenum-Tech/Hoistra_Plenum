// overdueBars — the "Days overdue" bars under a compliance answer (logic/complianceLive.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { overdueBars } from '../src/logic/complianceLive.js';

const register = { certs: [{ id: 'c1', days: -120 }, { id: 'c2', days: 1 }] };

test('a lapsed certificate in the register is drawn with its days past expiry', () => {
  const bars = overdueBars([{ id: 'c1', scope: 'building', company: 'Bishopsgate Tower', status: 'lapsed' }], register);
  // Owner and certificate, without a scope prefix the chart's title already gives (27 Oct 2026).
  assert.deepEqual(bars.buildings.map((b) => [b.label, b.days]), [['Bishopsgate Tower', 120]]);
});

test('a certificate still in date is never drawn as overdue', () => {
  // in the register, one day left
  assert.equal(overdueBars([{ id: 'c2', scope: 'building', company: 'A', status: 'expiring' }], register).buildings.length, 0);
  // not in the register: its "1 day left" / "expires in 5 days" is time left, not days overdue (27 Oct 2026)
  const notHeld = [
    { id: 'x1', scope: 'building', company: 'Bishopsgate Tower', status: 'expiring', reason: '1 day left' },
    { id: 'x2', scope: 'vendor', company: 'Harbourside Lift', status: 'expiring', reason: 'expires in 5 days' }
  ];
  const bars = overdueBars(notHeld, register);
  assert.equal(bars.buildings.length + bars.vendors.length, 0);
});

test('a lapsed certificate the register does not hold reads its days from the reason', () => {
  const bars = overdueBars([{ id: 'x3', scope: 'vendor', company: 'Clearwater', status: 'Lapsed', reason: 'lapsed 32 days ago' }], register);
  assert.deepEqual(bars.vendors.map((b) => [b.label, b.days]), [['Clearwater', 32]]);
});
