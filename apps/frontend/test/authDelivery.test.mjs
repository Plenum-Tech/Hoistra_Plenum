// authDeliveryNote — the standing fact that this deployment drops mail.
//
// Every code-issuing flow answers 202 and says a code is on its way. A deployment with
// EMAIL_DRY_RUN set writes the message to ops_email_log and drops it, and answers 202 all
// the same, so the screen asks for a code that was never sent and the only way to find out
// is to wait. The server reports delivery on /api/auth/config, about the deployment rather
// than about any address, and this is what the gate does with it.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { authMethods } = await import('../src/logic/auth.js');

const note = (email_delivery) =>
  authMethods.authDeliveryNote.call({ state: { authConfig: email_delivery === undefined ? null : { email_delivery } } });

test('a deployment that sends mail says nothing extra', () => {
  assert.equal(note({ live: true, dry_run: false, transport: 'graph' }), '');
});

test('dry run names the flag, because that is what has to be changed', () => {
  const s = note({ live: false, dry_run: true, transport: 'graph' });
  assert.match(s, /EMAIL_DRY_RUN/);
  assert.match(s, /written to the log/);
});

test('no transport is a different fix and says so', () => {
  const s = note({ live: false, dry_run: false, transport: 'none' });
  assert.match(s, /no mail transport/);
  assert.doesNotMatch(s, /EMAIL_DRY_RUN/, 'the flag is not the problem here');
});

test('an older server that does not report delivery is not accused of dropping mail', () => {
  // live !== false, so nothing is claimed. A frontend talking to a backend without this
  // field must not tell everyone their codes are being discarded.
  assert.equal(note(undefined), '');
  assert.equal(note({}), '');
});
