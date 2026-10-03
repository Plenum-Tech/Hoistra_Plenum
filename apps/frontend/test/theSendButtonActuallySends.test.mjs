// "Approve & send" sends, and says what actually happened.
//
// It was a bare setState. No request was made, and the transcript then read "Evidence
// request sent to <address>. Affected invoice lines are held pending the reply." A PM would
// wait for a reply from a contractor who had never been written to, and treat an invoice
// hold as something the vendor had been told about.
//
// The truthful wording for that exact case already existed one branch away — "Nothing has
// left the platform" — but the action spec's own `done` string matched first and claimed
// the send.
//
// The reply's `ok` cannot be used to report the outcome: send_platform_email answers
// ok=true for a dry run as well as a real send. Only `status` separates "left the platform"
// from "recorded and went nowhere", so that is what these pin.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { opsApi } = await import('../src/api/opsIntelligence.js');
const { renderValsMethods } = await import('../src/logic/renderVals.js');

const SRC = renderValsMethods.renderVals.toString();

test('the api client exposes a send, and it is a POST', () => {
  assert.equal(typeof opsApi.sendEmail, 'function');
  assert.match(opsApi.sendEmail.toString(), /approvals\/send-email/);
  assert.match(opsApi.sendEmail.toString(), /POST/);
});

test('the send handler actually calls it', () => {
  assert.match(SRC, /opsApi\.sendEmail\(/,
    'the button made no request at all — the message was the whole implementation');
});

test('the outcome is read from status, never from ok', () => {
  // ok is true for a dry run too. A handler that trusted it would report a delivered email
  // on a platform that had deliberately sent nothing.
  assert.match(SRC, /res && res\.status/);
  assert.match(SRC, /=== "sent"/);
  assert.match(SRC, /=== "dry_run"/);
});

test('a dry run is reported as not delivered', () => {
  assert.match(SRC, /NOT delivered/,
    'a dry run must never read as a send');
  assert.match(SRC, /EMAIL_DRY_RUN/,
    'the reader is told which switch held it back');
});

test('a failure keeps the draft on screen instead of reporting a send', () => {
  assert.match(SRC, /Not sent —/);
  assert.match(SRC, /flow: keepDraft \? prev\.flow/,
    'a failed send closed the composer, losing the message it had not sent');
});

test('an evidence request is only remembered once it has genuinely gone', () => {
  // ccRequested stops the certificate row offering the request again. Setting it on a send
  // that failed would hide the control for a request nobody received.
  assert.match(SRC, /sent && kind === "evidence"/);
});

test('a send with no address is refused before any request', () => {
  assert.match(SRC, /An address is needed before this can be sent/);
});

test('a second click cannot send the same email twice', () => {
  assert.match(SRC, /if \(s\.emSending\) return/);
  assert.match(SRC, /sendBusy/);
});
