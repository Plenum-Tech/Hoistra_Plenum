// Verify ↗ and "Record result": the register link each certificate carries, and a person's
// finding written back when the platform could not check the register itself.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
let sent = null;
globalThis.fetch = async (url, init) => {
  sent = { url: String(url), init };
  return new Response(JSON.stringify({ ok: true, verification: { status: 'human_verified' } }), { status: 200, headers: { 'content-type': 'application/json' } });
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { shapeLiveCompliance, verStateOf } = await import('../src/logic/complianceLive.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const GOVUK = 'https://find-energy-certificate.service.gov.uk/find-a-certificate/search-by-reference-number';

test('the row carries the register link from verify_link, with the register named', () => {
  const out = shapeLiveCompliance({ certificates: [{
    id: 'c1', certificate_type_code: 'EPC', country_code: 'UK', verification_url: GOVUK,
    verify_link: { url: GOVUK, register: 'GOV.UK Find an energy certificate', note: 'Opens the GOV.UK search.' }
  }] });
  const r = out.certs[0];
  assert.equal(r.verifyUrl, GOVUK);
  assert.equal(r.verifyRegister, 'GOV.UK Find an energy certificate');
  assert.equal(r.verState.state, 'open');
});

test('without verify_link the pack register URL is still a link', () => {
  const out = shapeLiveCompliance({ certificates: [{ id: 'c2', certificate_type_code: 'CP17', country_code: 'UK', verification_url: 'https://www.gassaferegister.co.uk/find-an-engineer/' }] });
  assert.equal(out.certs[0].verifyUrl, 'https://www.gassaferegister.co.uk/find-an-engineer/');
});

test('a person\'s finding reads as what they found, by whom and when', () => {
  const row = { raw_metadata: { verification: { status: 'human_not_found', verified: false, checked_by_label: 'pm@northbridge.test', checked_at: '2026-09-25T07:00:00Z', human_note: 'no such company on BAFE' } } };
  const v = verStateOf(row);
  assert.equal(v.state, 'human');
  assert.equal(v.ok, false);
  assert.match(v.line, /pm@northbridge\.test/);
  assert.match(v.line, /no such company on BAFE/);
  const out = shapeLiveCompliance({ certificates: [{ id: 'c3', certificate_type_code: 'FIRE_ALARM_SERVICE', country_code: 'UK', ...row }] });
  assert.equal(out.certs[0].ver, 'Not on the register');
});

test('a system check is not mistaken for an open one, and needs_human is open', () => {
  assert.equal(verStateOf({ raw_metadata: { verification: { status: 'verified', verified: true } } }).state, 'system');
  assert.equal(verStateOf({ raw_metadata: { verification: { status: 'needs_human', verified: null } } }).state, 'open');
  assert.equal(verStateOf({}).state, 'open');
});

test('recording a result: a negative finding without a note is refused before any request', async () => {
  const c = new HoistraLogic({});
  sent = null;
  c.setState({ ccHvId: 'c1', ccHvOutcome: 'not_found', ccHvNote: '  ' });
  await c.ccHumanVerify({ id: 'c1', nm: 'Fire alarm', verifyUrl: GOVUK });
  assert.equal(sent, null);
  assert.match(c.state.ccHvError, /what the register showed/);
  c.setState({ ccHvOutcome: '' });
  await c.ccHumanVerify({ id: 'c1', nm: 'Fire alarm' });
  assert.equal(sent, null);
});

test('recording a result posts the finding, the register opened and who checked', async () => {
  const c = new HoistraLogic({});
  c.ccLoad = async () => {};
  // The label comes from the signed-in account, not the sign-in form's email field.
  c.setState({ ccHvId: 'c1', ccHvOutcome: 'confirmed', ccHvNote: 'BAFE 12345 matches', email: 'typed-in-gate@x', account: { email: 'pm@northbridge.test' } });
  await c.ccHumanVerify({ id: 'c1', nm: 'Fire alarm', verifyUrl: GOVUK });
  assert.match(sent.url, /\/api\/compliance\/certificates\/c1\/human-verification$/);
  const body = JSON.parse(sent.init.body);
  assert.equal(body.outcome, 'confirmed');
  assert.equal(body.note, 'BAFE 12345 matches');
  assert.equal(body.register_url, GOVUK);
  assert.equal(body.checked_by_label, 'pm@northbridge.test');
  assert.equal(c.state.ccHvId, null);                     // panel closes on success
});


test('a javascript: URL never becomes a Verify link, whatever the stored row says', () => {
  const out = shapeLiveCompliance({ certificates: [{
    id: 'x1', certificate_type_code: 'FIRE_DOOR', country_code: 'UK',
    verify_link: { url: "javascript:alert(document.cookie)" }, verification_url: 'JAVASCRIPT:alert(1)'
  }] });
  assert.equal(out.certs[0].verifyUrl, null);
});
