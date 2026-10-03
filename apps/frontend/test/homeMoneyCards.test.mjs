// The Home page's money cards — Platform value + P&L — shaped from GET /api/value/summary.
// The server derives the figures from the store; here only formatting and the live/seed
// gate are decided, and a figure nobody derived must never be dressed up as one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { shapeLiveHome, money } from '../src/logic/homeLive.js';

const NOW = new Date(2026, 8, 25, 12, 0, 0);

// A trimmed copy of the /api/value/summary shape (engines/value_ledger.py).
const value = {
  ok: true, year: 2026, currency: 'GBP',
  ledger: {
    counted_modules: 3,
    total_detected: 149250.0,
    total_saved: 109000.0,
    note: 'A line exists only where an engine recorded a priced row…',
    modules: [
      { key: 'energy', name: 'Energy', counted: true, detected: 41250.0, saved: 29000.0,
        note: '14 anomalies this year, 3 of them unpriced…',
        items: [{ what: 'Out of hours spike · Kingsway House', action: 'Resolved',
                  detected: 31000.0, saved: 31000.0,
                  basis: "measured · priced at the meter's tariff", at: null }] },
      { key: 'vendors', name: 'Vendors', counted: true, detected: 13700.0, saved: 1450.0,
        note: '9 flagged invoice lines…', items: [] },
      { key: 'maintenance', name: 'Maintenance', counted: false, detected: null, saved: null,
        note: 'The work-order chain records no priced value yet…', items: [] },
      { key: 'compliance', name: 'Compliance', counted: false, detected: null, saved: null,
        note: 'Exposure at lapse is not priced anywhere in the store…', items: [] },
      { key: 'assets', name: 'Assets', counted: true, detected: 94300.0, saved: 78550.0,
        note: '4 priced recommendations, 3 actioned…',
        items: [{ what: 'CH-2 sequencing', action: 'Actioned', detected: 94300.0,
                  saved: 78550.0, basis: 'estimated · replacement cost less repair cost',
                  at: null }] }
    ]
  },
  pnl: {
    budget_connected: false, saved: null,
    note: 'No budget ledger is connected…',
    heads: [
      { key: 'maintenance', name: 'Maintenance', budget: null, actual: 3860000.0, basis: '812 lines' },
      { key: 'energy', name: 'Energy', budget: null, actual: 3050000.0, basis: '24 meters' },
      { key: 'compliance', name: 'Compliance', budget: null, actual: null, basis: 'nothing billed' },
      { key: 'unplanned', name: 'Unplanned failure', budget: null, actual: 190000.0, basis: '60 lines' }
    ]
  }
};

test('no value read yet: both cards stay unanswered and fall to the seed', () => {
  const m = shapeLiveHome({ raw: {} }, NOW);
  assert.equal(m.value.answered, false);
  assert.equal(m.pnl.answered, false);
});

test('a live ledger formats what the store priced and dashes what it could not', () => {
  const m = shapeLiveHome({ raw: { value } }, NOW);
  assert.equal(m.value.answered, true);
  assert.equal(m.value.year, 2026);
  assert.equal(m.value.total, '£109k');
  const rows = Object.fromEntries(m.value.rows.map((r) => [r.key, r]));
  assert.equal(rows.energy.detected, '£41k');
  assert.equal(rows.energy.saved, '£29k');
  assert.equal(rows.maintenance.detected, '—');   // not counted, and it says why
  assert.ok(rows.maintenance.note.includes('no priced value'));
  assert.equal(rows.energy.items[0].saved, '£31k');
  assert.equal(rows.energy.items[0].est, false);
  assert.equal(rows.assets.items[0].est, true);    // estimated lines stay marked
});

test('the live P&L keeps budgets and the headline honest until a ledger exists', () => {
  const m = shapeLiveHome({ raw: { value } }, NOW);
  assert.equal(m.pnl.answered, true);
  assert.equal(m.pnl.saved, '—');                  // underspend needs a budget to exist
  assert.equal(m.pnl.budgetConnected, false);
  const rows = Object.fromEntries(m.pnl.rows.map((r) => [r.head, r]));
  assert.equal(rows.Maintenance.budget, '—');
  assert.equal(rows.Maintenance.actual, '£3.86m');
  assert.equal(rows.Energy.actual, '£3.05m');
  assert.equal(rows.Compliance.actual, '—');
});

test('the value read alone is enough to mark the home model live', () => {
  const m = shapeLiveHome({ raw: { value } }, NOW);
  assert.equal(m.live, true);
});

test('money: null is a dash, never £0 — and the shorthand matches the cards', () => {
  assert.equal(money(null), '—');
  assert.equal(money(0), '£0');
  assert.equal(money(840), '£840');
  assert.equal(money(4100), '£4.1k');
  assert.equal(money(31000), '£31k');
  assert.equal(money(3050000), '£3.05m');
  assert.equal(money(-4100), '-£4.1k');
});
