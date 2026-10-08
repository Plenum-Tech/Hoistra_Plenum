// The three checks on a Compliance vault row — Document, Register, Expiry — each a status and
// the line that explains it, with when the register was last checked (7 Oct 2026). Display only:
// every value comes from fields the row already carried.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { certChecks } from '../src/logic/compliance.js';

const row = (o) => Object.assign({ auth: 'genuine', authSev: 'ok', doc: true, verChip: { label: 'Not checked', sev: 'none' }, verState: { state: 'open' }, risk: 'OK', sev: 'ok', exp: '25 Apr 2027', days: 200 }, o);
const byKey = (c) => Object.fromEntries(certChecks(c).map((k) => [k.key, k]));

test('always the three checks, in the order Document, Register, Expiry', () => {
  assert.deepEqual(certChecks(row()).map((k) => k.label), ['Document', 'Register', 'Expiry']);
});

test('the register says when it was last checked, and by whom', () => {
  const k = byKey(row({ verChip: { label: 'Confirmed on register', sev: 'ok' }, verState: { state: 'human', ok: true }, verWhen: '05 Oct 2026', verBy: 'Hussain Kalabhai' }));
  assert.equal(k.reg.status, 'Confirmed on register');
  assert.equal(k.reg.detail, 'Last checked 05 Oct 2026 by Hussain Kalabhai');
  assert.equal(byKey(row({ verChip: { label: 'Verified', sev: 'ok' }, verState: { state: 'system', ok: true }, verWhen: '07 Sep 2026' })).reg.detail, 'Last checked 07 Sep 2026 by the platform');
  assert.equal(byKey(row()).reg.detail, 'Never checked');
});

test('expiry reads as words, with the date and how far away it is', () => {
  assert.equal(byKey(row()).exp.status, 'Active');
  assert.equal(byKey(row({ risk: '<30d', sev: 'warn', exp: '13 Oct 2026', days: 6 })).exp.status, 'Expiring soon');
  const lapsed = byKey(row({ risk: 'Lapsed', sev: 'risk', exp: '18 Sep 2026', days: -19 })).exp;
  assert.equal(lapsed.status, 'Lapsed');
  assert.match(lapsed.detail, /^18 Sep 2026 · /);
  assert.equal(lapsed.icon, 'ph-x-circle', 'warn and risk share a colour, so the icon tells them apart');
});

test('the document check never contradicts itself when there is no file', () => {
  assert.deepEqual([byKey(row({ doc: false, auth: 'not checked', authSev: 'none' })).doc.status, byKey(row({ doc: false, auth: 'not checked', authSev: 'none' })).doc.detail], ['No file', 'Only its fields are on record']);
  assert.equal(byKey(row({ doc: false })).doc.detail, 'Forensic check passed · no file linked here');
  const sus = byKey(row({ auth: 'suspect · 72', authSev: 'warn' })).doc;
  assert.equal(sus.status, 'Suspect');
  assert.equal(sus.detail, 'Forensic check flagged the file · risk 72');
});

test('a vendor reads Clear, At risk or Blocked — at risk while an accreditation lapses within 30 days', async () => {
  const { vendorStatus } = await import('../src/logic/compliance.js');
  assert.equal(vendorStatus({ block: 'Clear', worst: 'OK' }).label, 'Clear');
  assert.equal(vendorStatus({ block: 'Clear', worst: '<90d' }).label, 'Clear', 'two months out is not yet a risk');
  assert.equal(vendorStatus({ block: 'Clear', worst: '<30d' }).label, 'At risk');
  assert.equal(vendorStatus({ block: 'Clear', worst: 'Lapsed' }).label, 'At risk');
  assert.equal(vendorStatus({ block: 'Blocked', worst: 'Blocked' }).label, 'Blocked');
  assert.equal(vendorStatus({ block: 'Clear', worst: 'None on file' }).label, 'Clear');
});
