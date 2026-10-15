// The status of the platform's read of a finished migration's contract terms, invoices and
// telemetry, as the Migration page's chip and the Vendors page's highlight say it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { extrasStatus, extrasCounts, vendorsExtrasBanner } from '../src/logic/workbookExtras.js';

const done = { status: 'done', summary: { found: true, contracts: 15, invoices: 40, lines_held: 47,
  telemetry: { chiller_readings: 960, degree_days: 25, bms_samples: 8070 } } };

test('a finished read says what it read', () => {
  assert.equal(extrasCounts(done.summary),
    '15 contracts read into draft terms · 40 invoices verified, 47 lines held for your decision · telemetry: 960 chiller readings, 25 months of degree days, 8,070 BMS samples');
  const c = extrasStatus(done);
  assert.equal(c.tone, 'ok'); assert.equal(c.label, 'Done'); assert.equal(c.action, 'Read again');
});

test('each state has its own words, tone and action', () => {
  assert.deepEqual([extrasStatus({ status: 'waiting' }).label, extrasStatus({ status: 'waiting' }).action], ['Waiting', 'Read now']);
  assert.equal(extrasStatus({ status: 'running' }).action, '');
  const f = extrasStatus({ status: 'failed', error: 'HTTP 502', attempts: 2 });
  assert.equal(f.tone, 'risk'); assert.match(f.detail, /HTTP 502 · attempt 2 of 3/); assert.equal(f.action, 'Try again');
  assert.equal(extrasStatus({ status: 'none', summary: { found: false } }).action, '');
  assert.equal(extrasStatus({ status: 'predates' }).action, 'Read now');
  assert.equal(extrasStatus(null).show, false);
});

test('the Vendors highlight names what is left for a person', () => {
  const b = vendorsExtrasBanner({ run: { ...done, source_filename: 'bishopsgate-single.xlsx' }, drafts_awaiting: 15, confirmed: 0 });
  assert.equal(b.title, 'Latest migration: Done · bishopsgate-single.xlsx');
  assert.equal(b.tone, 'warn');
  assert.match(b.next, /^15 contracts wait for a named person to confirm/);
  assert.match(vendorsExtrasBanner({ run: done, drafts_awaiting: 0, confirmed: 15 }).next, /15 contracts confirmed/);
  assert.equal(vendorsExtrasBanner({ run: null }).show, false);
});
