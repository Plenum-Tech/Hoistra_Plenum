// The send button tells the truth about every reply the backend can give.
//
// The handler treated only a thrown error as failure. send_approval_email_draft answers
// HTTP 200 with {ok:false, error} when it refuses a draft (no subject, an address without
// "@"), and send_platform_email answers {status:"failed"} when Graph or SMTP throws. Both
// arrived with err === null, so the outcome read "Sent to <address>, and the mail service
// reported "failed"…" and the composer closed on a draft that had gone nowhere — the very
// untruth the send was wired to remove. The real controller in Node; fetch is a stub that
// answers 200 with whatever body each test names.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local', href: 'http://test.local/' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
let reply = null;
globalThis.fetch = async () => ({ ok: true, status: 200, headers: { get: () => 'application/json' }, text: async () => JSON.stringify(reply) });

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { opsApi } = await import('../src/api/opsIntelligence.js');

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  globalThis.window.location.href = 'http://test.local/';
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'cc', flow: 'email', emKind: 'renewal', emTo: 'ops@vendor.example', emSubject: 'Renewal', emBody: 'Please renew.' });
});

const send = async () => { await c.renderVals().em.send(); return c.state; };

test('a refusal (200, ok:false) is "Not sent" and the draft stays open', async () => {
  reply = { ok: false, error: 'Subject is required' };
  const s = await send();
  assert.equal(s.flow, 'email', 'the composer closed on a refused draft');
  assert.match(s.flowDone, /^Not sent — Subject is required/);
  assert.equal(s.emSending, false);
});

test('a failed transport (status "failed") is "Not sent" and the draft stays open', async () => {
  reply = { ok: false, status: 'failed', error: 'Graph: 401 Unauthorized' };
  const s = await send();
  assert.equal(s.flow, 'email');
  assert.match(s.flowDone, /^Not sent — Graph: 401 Unauthorized/);
});

test('a status nobody recognises is not reported as delivery', async () => {
  reply = { ok: true, status: 'queued' };
  const s = await send();
  assert.equal(s.flow, 'email');
  assert.match(s.flowDone, /^Not confirmed — the mail service reported "queued"/);
});

test('a real send closes the composer and says so', async () => {
  reply = { ok: true, status: 'sent', email_id: 'e1' };
  const s = await send();
  assert.equal(s.flow, null);
  assert.match(s.flowDone, /^Renewal email sent to ops@vendor\.example\./);
});

test('a dry run is recorded, not delivered, and says which', async () => {
  reply = { ok: true, status: 'dry_run', email_id: 'e2' };
  const s = await send();
  assert.equal(s.flow, null);
  assert.match(s.flowDone, /NOT delivered/);
});

test('handoff mode opens the reader\'s own mail client and sends nothing itself', async () => {
  reply = { ok: true, status: 'handoff', handoff: { mailto_uri: 'mailto:ops@vendor.example?subject=Renewal&body=Please%20renew.' } };
  const s = await send();
  assert.equal(globalThis.window.location.href, 'mailto:ops@vendor.example?subject=Renewal&body=Please%20renew.');
  // A mailto is a URL, and mail clients cut long ones short; the composer stays so the body
  // can be copied across if that happens. Nothing has been confirmed sent either.
  assert.equal(s.flow, 'email', 'the composer closed on a handoff nobody has pressed send on');
  assert.match(s.flowDone, /your mail client/);
  assert.match(s.flowDone, /nothing itself|sends nothing/);
  assert.match(s.flowDone, /draft stays/i);
});

test('the mail timeout is the one the client actually reads', () => {
  // apiFetch reads opts.timeoutMs. The wrapper passed `timeout`, so the 60s the comment
  // argues for was ignored and Graph sends were cut off at the 20s default.
  assert.match(opsApi.sendEmail.toString(), /timeoutMs: T_MAIL/);
  assert.doesNotMatch(opsApi.sendEmail.toString(), /[^a-zA-Z]timeout: T_MAIL/);
});
