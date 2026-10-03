// A finished migration of a single end-to-end workbook says what the post-write engines did
// with its Contract_Terms and Invoice_Lines sheets; an ordinary workbook says nothing more.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extrasLine } from '../src/logic/migration.js';

test('contracts and invoices are reported, with the lines held', () => {
  const line = extrasLine({ found: true,
    contracts: [{ contract_ref: 'AM-2024-HVAC-07' }, { contract_ref: 'AL-2024-LIFT-03' }],
    invoices: [{ invoice_no: 'INV-8841', held: 1 }, { invoice_no: 'INV-8744', held: 0 }], skipped: [] });
  assert.equal(line, '2 contracts read into draft terms — confirm each on the Vendors page · 2 invoices verified, 1 line held for your decision');
});

test('plant telemetry is reported and points at the energy scan', () => {
  const line = extrasLine({ found: true, contracts: [], invoices: [], skipped: [],
    telemetry: { chiller_specs: 2, chiller_readings: 800, degree_days: 25, bms_samples: 8064 } });
  assert.equal(line, 'plant telemetry stored (800 chiller readings, 25 months of degree days, 8064 BMS samples) — run the energy scan to assess them');
});

test('a workbook without the sheets, a read in flight, a failure and skips', () => {
  assert.equal(extrasLine({ ok: true, found: false }), '');
  assert.equal(extrasLine(null), '');
  assert.match(extrasLine({ loading: true }), /Reading the workbook/);
  assert.match(extrasLine({ error: 'HTTP 500' }), /could not be read — HTTP 500/);
  assert.match(extrasLine({ found: true, contracts: [], invoices: [], skipped: [{ reason: 'already verified' }, { reason: 'already verified' }] }),
    /2 skipped \(already verified\)/);
});
