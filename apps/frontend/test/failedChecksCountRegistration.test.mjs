// "Failed checks" counts a failed authenticity check OR a failed register check (3 Nov 2026).
//
// The Compliance Engine's card says "Failed checks - authenticity or registration" but counted only documents whose
// forensics made them suspect. Plenum Technologies had two certificates that FAILED their register check (EPC
// 0310-4471-2090-6655-2281, TM44-B-301-2022) and the card read 0, while the chat said "None". One rule now, the same in
// the chat (svc-deepagents agents/cert_checks.py): authenticity in doubt, or the register check failed.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} } };

const { complianceEngineCards } = await import('../src/logic/instances.js');
const { authenticityOf } = await import('../src/logic/complianceLive.js');

const cert = (o) => Object.assign({ kind: 'building', holder: 'Bishopsgate Tower', days: 100, auth: 'not checked',
  verChip: { label: 'Not checked', sev: 'none' } }, o);

test('a certificate that failed its register check is a failed check', () => {
  const reg = { certs: [cert({ verChip: { label: 'Failed', sev: 'risk' } }), cert({ verChip: { label: 'Not on register', sev: 'risk' } }),
    cert({ verChip: { label: 'Verified', sev: 'ok' } }), cert({})], buildings: [], vendors: [] };
  const failed = complianceEngineCards(reg).find((c) => c.l === 'Failed checks');
  assert.equal(failed.v, '2');
  assert.equal(failed.tone, 'risk');
});

test('a suspect document still counts, once, even when its register check failed too', () => {
  const reg = { certs: [cert({ auth: 'suspect · 82', verChip: { label: 'Failed', sev: 'risk' } }), cert({ auth: 'genuine' })],
    buildings: [], vendors: [] };
  assert.equal(complianceEngineCards(reg).find((c) => c.l === 'Failed checks').v, '1');
});

test('the authenticity rule is the chat\'s: review and edited verdicts are in doubt too', () => {
  assert.equal(authenticityOf({ forensics_verdict: 'review' }).auth.startsWith('suspect'), true);
  assert.equal(authenticityOf({ forensics_ccc_verdict: 'edited' }).auth.startsWith('suspect'), true);
  assert.equal(authenticityOf({ forensics_verdict: 'PASS', authenticity_warning: 'Confirm via Verify now' }).auth, 'genuine');
  assert.equal(authenticityOf({ authenticity_warning: 'Incremental PDF edits' }).auth.startsWith('suspect'), true);
  assert.equal(authenticityOf({}).auth, 'not checked');
});
