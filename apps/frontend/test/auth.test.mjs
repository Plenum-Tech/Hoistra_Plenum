// auth — the real controller in Node, fetch mocked per METHOD + path so nothing reaches a
// host. This stack's backend points at production and every auth route except /config
// writes (accounts, sessions, last_login_at), so these tests must prove the requests and
// the state transitions without ever performing one.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls, handlers;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push({ method, path: u.pathname, headers: (opts && opts.headers) || {}, body: opts && opts.body ? JSON.parse(opts.body) : null });
  const h = handlers[method + ' ' + u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status), text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { SESSION_KEY } = await import('../src/logic/session.js');
const { apiFetch, BASES } = await import('../src/api/client.js');

const A = '/backend/ops-intelligence/api/auth';
const USER = { id: 'u-1', email: 'sam@example.com', full_name: 'Sam Okafor', organization_id: 'org-1', status: 'active', email_verified: true, role: 'user', role_label: 'Facilities manager — the default for a new account', last_login_at: null };
const ADMIN = Object.assign({}, USER, { id: 'u-2', email: 'ada@example.com', full_name: 'Ada Admin', role: 'admin' });
const CONFIG = { ok: true, self_registration: true, password: { min_length: 12 }, otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }, secrets_configured: { jwt: true, otp_pepper: true } };
const tokens = (n) => ({ access_token: 'acc-' + n, refresh_token: 'ref-' + n, token_type: 'Bearer', expires_in: 1800 });
const session = (user, n, message) => [200, Object.assign({ ok: true, user, tokens: tokens(n) }, message ? { message } : {})];
const accepted = (status, message) => [202, { ok: true, status, message, email: 'sam@example.com', otp: CONFIG.otp }];
const fail = (status, reason, error, extra) => [status, { detail: Object.assign({ ok: false, error, reason }, extra || {}) }];
const settle = (ms) => new Promise((r) => setTimeout(r, ms || 10));
const stored = () => JSON.parse(mem[SESSION_KEY] || '{}');
const requests = (path) => calls.filter((c) => c.path === path);

let c;
const fresh = () => { const x = new HoistraLogic(); x.authBoot(); return x; };
const cleanup = (x) => { const k = x || c; k.authStop(); clearTimeout(k._tt); };
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = []; handlers = { ['GET ' + A + '/config']: [200, CONFIG] };
  c = fresh();
});
afterEach(() => cleanup());

// ── sign in ──────────────────────────────────────────────────────────────────

test('login 200: account, both tokens, signed in on home; only the refresh token is stored', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: ' sam@example.com ', password: 'correct-horse-battery-staple' });
  await c.authSignIn();
  const r = requests(A + '/login')[0];
  assert.deepEqual(r.body, { email: 'sam@example.com', password: 'correct-horse-battery-staple' });
  assert.equal(r.headers.Authorization, undefined, 'login never carries a bearer');
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.navOpen, true);
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.accessToken, 'acc-1');
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.password, '', 'the password leaves memory');
  assert.equal(stored().refreshToken, 'ref-1');
  assert.equal(stored().account.full_name, 'Sam Okafor');
  assert.equal(JSON.stringify(stored()).includes('acc-1'), false, 'the access token is never stored');
});

test('after login every backend call carries the bearer', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  handlers['GET /backend/udr/api/anything'] = [200, { ok: true }];
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await apiFetch(BASES.udr, '/api/anything');
  assert.equal(requests('/backend/udr/api/anything')[0].headers.Authorization, 'Bearer acc-1');
});

test('a user-role account gets User view only: role clamped, no Admin view row', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), role: 'admin' });
  await c.authSignIn();
  assert.equal(c.state.role, 'user');
  const v = c.renderVals();
  assert.equal(v.canAdmin, false);
  assert.equal(v.acctItems.some((i) => i.label === 'Admin view' || i.label === 'User view'), false);
  assert.ok(v.acctItems.some((i) => i.label === 'Change password'));
  assert.ok(v.acctItems.some((i) => i.label === 'Sign out everywhere'));
  assert.equal(v.acctName, 'Sam Okafor');
  assert.equal(v.acctEmail, 'sam@example.com');
  assert.equal(v.acctInitial, 'S');
});

test('an admin account keeps the Admin view toggle', async () => {
  handlers['POST ' + A + '/login'] = session(ADMIN, 1);
  c.setState({ email: 'ada@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  const v = c.renderVals();
  assert.equal(v.canAdmin, true);
  const toggle = v.acctItems.find((i) => i.label === 'Admin view');
  assert.ok(toggle);
  toggle.click();
  assert.equal(c.state.role, 'admin');
  assert.equal(c.state.view, 'buildings');
});

test('login 401 shows the message verbatim and stays gated', async () => {
  handlers['POST ' + A + '/login'] = fail(401, 'invalid_credentials', 'That email address and password do not match an account.');
  c.setState({ email: 'sam@example.com', password: 'wrong-wrong-wrong' });
  await c.authSignIn();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authError, 'That email address and password do not match an account.');
  assert.equal(c.state.authReason, 'invalid_credentials');
  assert.equal(c.state.authBusy, false);
  assert.equal(requests(A + '/refresh').length, 0, 'a failing login never refreshes');
});

test('login 429 locked arms a countdown from retry_after_seconds and offers the reset link', async () => {
  handlers['POST ' + A + '/login'] = fail(429, 'locked', 'Too many failed attempts. Try again in 15 minutes, or reset your password.', { retry_after_seconds: 899 });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authReason, 'locked');
  assert.ok(c.state.authRetryAt > Date.now() + 890 * 1000);
  const v = c.renderVals();
  assert.equal(v.authLocked, true);
  assert.match(v.authCountdown, /^14:5\d$/);
  v.authGoForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.email, 'sam@example.com', 'the email is kept for the reset');
});

test('login 403 email_not_verified goes straight to the code screen without a resend', async () => {
  handlers['POST ' + A + '/login'] = fail(403, 'email_not_verified', 'Confirm your email address first. We have sent a new code to sam@example.com.', { email: 'sam@example.com' });
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, 'Confirm your email address first. We have sent a new code to sam@example.com.');
  assert.equal(c.state.authError, '');
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.ok(c.state.authRetryAt > Date.now(), 'the resend cooldown is armed — a code is already in the inbox');
});

test('login 422 and a network failure get the gate\'s own one-liners', async () => {
  handlers['POST ' + A + '/login'] = [422, { detail: [{ loc: ['body', 'email'], msg: 'value is not a valid email address', type: 'value_error' }] }];
  c.setState({ email: 'nope', password: 'x' });
  await c.authSignIn();
  assert.equal(c.state.authError, 'Enter a valid email address and password.');
  delete handlers['POST ' + A + '/login'];
  await c.authSignIn();
  assert.equal(c.state.authError, "Couldn't reach the sign-in service. Check the backend is running.");
});

test('the SSO button is inert: a toast, no sign-in', () => {
  c.renderVals().authSSO();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Single sign-on is not available yet — sign in with your email and password.');
});

test('the animated page chips no longer fake a sign-in', () => {
  c.renderVals().f4items[0].click();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Sign in to open NABERS data pack.');
});

test('sign out posts the refresh token and clears everything even when logout fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ view: 'buildings', navOpen: true, acctOpen: true });
  // no /logout handler → the mocked fetch rejects, exactly like an unreachable backend
  c.renderVals().signOut();
  await settle();
  assert.equal(requests(A + '/logout')[0].body.refresh_token, 'ref-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.account, null);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.refreshToken, null);
  assert.equal(c.state.view, 'home');
  assert.equal(c.state.acctOpen, false);
  assert.equal(c.state.role, 'user');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('a restored role:admin on a user account is clamped in the constructor', () => {
  cleanup();
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: USER, role: 'admin', view: 'buildings' });
  c = new HoistraLogic();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.role, 'user');
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-1', account: ADMIN, role: 'admin', view: 'buildings' });
  const d = new HoistraLogic();
  assert.equal(d.state.role, 'admin');
});

test('/config is read once at boot and drives the register link and the length hint', async () => {
  await settle();
  assert.equal(requests(A + '/config').length, 1);
  let v = c.renderVals();
  assert.equal(v.authCanRegister, true);
  assert.equal(v.authMinLength, 12);
  assert.equal(v.authCodeLength, 6);
  c.setState({ authConfig: Object.assign({}, CONFIG, { self_registration: false, password: { min_length: 16 } }) });
  v = c.renderVals();
  assert.equal(v.authCanRegister, false);
  assert.equal(v.authMinLength, 16);
});

// ── create an account ────────────────────────────────────────────────────────

const REG_MSG = 'Check sam@example.com for a 6-digit code and enter it to finish setting up your account. The code lasts 10 minutes.';

test('register 202 → the code screen with the server line, cooldown armed, password gone', async () => {
  handlers['POST ' + A + '/register'] = accepted('verification_sent', REG_MSG);
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'correct-horse-battery-staple', fullName: ' Sam Okafor ', phone: '' });
  await c.authRegister();
  assert.deepEqual(requests(A + '/register')[0].body, { email: 'sam@example.com', password: 'correct-horse-battery-staple', full_name: 'Sam Okafor', phone: null });
  assert.equal(c.state.authMode, 'verify');
  assert.equal(c.state.authNotice, REG_MSG);
  assert.equal(c.state.password, '');
  assert.equal(c.state.signedIn, false, '202 issues no tokens');
  assert.ok(c.state.authRetryAt > Date.now() + 55 * 1000);
  assert.equal(c.renderVals().authCoolingDown, true);
});

test('register 400 password shows the rule verbatim and stays on the form', async () => {
  handlers['POST ' + A + '/register'] = fail(400, 'password', 'Use at least 12 characters. Length is what makes a password hard to guess; a memorable phrase beats a short one with symbols in it.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'short', fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authMode, 'register');
  assert.equal(c.state.authReason, 'password');
  assert.match(c.state.authError, /^Use at least 12 characters/);
});

test('register 403 registration_closed hides the create-account link from then on', async () => {
  handlers['POST ' + A + '/register'] = fail(403, 'registration_closed', 'Self-registration is off on this deployment. Ask an operator to create your account.');
  c.renderVals().authGoRegister();
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12), fullName: 'Sam' });
  await c.authRegister();
  assert.equal(c.state.authError, 'Self-registration is off on this deployment. Ask an operator to create your account.');
  assert.equal(c.renderVals().authCanRegister, false);
});

test('verify: a mismatch keeps the typed code and reports attempts left; the right code signs in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123 456' });
  handlers['POST ' + A + '/verify-email'] = fail(400, 'mismatch', 'That code is not correct. 4 attempts left.', { attempts_remaining: 4 });
  await c.authVerify();
  assert.equal(requests(A + '/verify-email')[0].body.code, '123456', 'spaces are stripped');
  assert.equal(c.state.code, '123 456', 'kept for a retype');
  assert.equal(c.state.authAttemptsLeft, 4);
  assert.equal(c.state.authError, 'That code is not correct. 4 attempts left.');
  handlers['POST ' + A + '/verify-email'] = session(USER, 3, 'Your email address is confirmed and you are signed in.');
  await c.authVerify();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.accessToken, 'acc-3');
  assert.equal(c.state.code, '');
  assert.equal(c.state.toast, 'Your email address is confirmed and you are signed in.');
});

test('verify: a dead code is cleared and resend becomes the emphasised action', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '999999' });
  for (const reason of ['expired', 'too_many_attempts', 'no_code']) {
    handlers['POST ' + A + '/verify-email'] = fail(400, reason, 'dead: ' + reason);
    c.setState({ code: '999999' });
    await c.authVerify();
    assert.equal(c.state.code, '', reason + ' clears the field');
    assert.equal(c.state.authReason, reason);
    assert.equal(c.renderVals().authDeadCode, true);
  }
});

test('resend: 202 clears the typed code and re-arms the cooldown; 429 shows the wait', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '1234' });
  handlers['POST ' + A + '/resend-code'] = accepted('sent', 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  assert.equal(c.state.code, '', 'the code in the older email is dead');
  assert.equal(c.state.authNotice, 'If that address needs a code, one is on its way. It lasts 10 minutes.');
  assert.equal(c.renderVals().authCoolingDown, true);
  // Still cooling down: the button is inert, nothing is sent.
  await c.authResend();
  assert.equal(requests(A + '/resend-code').length, 1);
  c.setState({ authRetryAt: 0 });
  handlers['POST ' + A + '/resend-code'] = fail(429, 'cooldown', 'A code was just sent. Ask for another in 59 seconds.', { retry_after_seconds: 59 });
  await c.authResend();
  assert.equal(c.state.authError, 'A code was just sent. Ask for another in 59 seconds.');
  assert.match(c.renderVals().authCountdown, /^00:5\d$/);
});

test('verify 404 no_account returns to sign in', async () => {
  c.setState({ authMode: 'verify', email: 'sam@example.com', code: '123456' });
  handlers['POST ' + A + '/verify-email'] = fail(404, 'no_account', 'That account no longer exists.');
  await c.authVerify();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authError, 'That account no longer exists.');
});

// ── forgotten password ───────────────────────────────────────────────────────

const FORGOT_MSG = 'If that address has an account, a reset code is on its way. It is valid for 10 minutes.';

test('forgot 202 → reset screen with the server line; 429 shows the wait', async () => {
  c.renderVals().authGoForgot();
  c.setState({ email: 'sam@example.com' });
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authForgot();
  assert.deepEqual(requests(A + '/password/forgot')[0].body, { email: 'sam@example.com' });
  assert.equal(c.state.authMode, 'reset');
  assert.equal(c.state.authNotice, FORGOT_MSG);
  assert.equal(c.renderVals().authCoolingDown, true);
  c.setState({ authMode: 'forgot', authRetryAt: 0 });
  handlers['POST ' + A + '/password/forgot'] = fail(429, 'hourly_cap', 'Too many codes for that address this hour.', { retry_after_seconds: 1800 });
  await c.authForgot();
  assert.equal(c.state.authMode, 'forgot');
  assert.equal(c.state.authError, 'Too many codes for that address this hour.');
  // ceil() of the remaining 1799.9 s reads 30:00 until a whole second has passed.
  assert.match(c.renderVals().authCountdown, /^(30:00|29:5\d)$/);
});

test('reset: a rejected password keeps the code, a wrong code keeps the code minus an attempt, success returns to sign in', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '418902', newPassword: 'correct-horse-battery-staple' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'password_unchanged', 'That is the password the account already has. If you are resetting it because someone else may know it, choose a different one. Your code is still valid.');
  await c.authReset();
  assert.deepEqual(requests(A + '/password/reset')[0].body, { email: 'sam@example.com', code: '418902', new_password: 'correct-horse-battery-staple' });
  assert.equal(c.state.code, '418902', 'the code survives a rejected password');
  assert.equal(c.state.newPassword, '', 'only the password is retyped');
  assert.equal(c.state.authReason, 'password_unchanged');

  c.setState({ newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'mismatch', 'That code is not correct. 3 attempts left.', { attempts_remaining: 3 });
  await c.authReset();
  assert.equal(c.state.code, '418902');
  assert.equal(c.state.authAttemptsLeft, 3);

  handlers['POST ' + A + '/password/reset'] = [200, { ok: true, message: 'Your password is set. Sign in with it — every other session has been signed out.', sessions_ended: 1 }];
  await c.authReset();
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'Your password is set. Sign in with it — every other session has been signed out.');
  assert.equal(c.state.email, 'sam@example.com', 'kept for the sign-in');
  assert.equal(c.state.code, '');
  assert.equal(c.state.newPassword, '');
  assert.equal(c.state.signedIn, false, 'a reset deliberately does not sign in');
});

test('reset: a dead code is cleared; resend in reset mode asks /password/forgot', async () => {
  c.setState({ authMode: 'reset', email: 'sam@example.com', code: '000000', newPassword: 'a-brand-new-long-passphrase' });
  handlers['POST ' + A + '/password/reset'] = fail(400, 'expired', 'That code has expired. Ask for a new one.');
  await c.authReset();
  assert.equal(c.state.code, '');
  assert.equal(c.renderVals().authDeadCode, true);
  handlers['POST ' + A + '/password/forgot'] = accepted('accepted', FORGOT_MSG);
  await c.authResend();
  assert.equal(requests(A + '/password/forgot').length, 1);
  assert.equal(requests(A + '/resend-code').length, 0);
  assert.equal(c.state.authMode, 'reset');
});

// ── reload, refresh, and other tabs ──────────────────────────────────────────

const seedSession = (user, n) => { mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-' + n, account: user, view: 'buildings', role: 'user', navOpen: true }); };

test('reload: the shell renders on the stored identity, one refresh rotates both tokens and keeps the page', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = session(Object.assign({}, USER, { full_name: 'Sam O.' }), 2);
  c = new HoistraLogic();
  calls = []; // beforeEach's fresh() already read /config once; measure only the reload
  assert.equal(c.state.signedIn, true, 'optimistic — no gate flash');
  assert.equal(c.state.account.full_name, 'Sam Okafor');
  assert.equal(c.state.accessToken, null);
  await c.authBoot();
  const r = requests(A + '/refresh');
  assert.equal(r.length, 1);
  assert.deepEqual(r[0].body, { refresh_token: 'ref-1' });
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.refreshToken, 'ref-2');
  assert.equal(stored().refreshToken, 'ref-2');
  assert.equal(c.state.account.full_name, 'Sam O.', 'the server copy replaces the summary');
  assert.equal(c.state.view, 'buildings', 'a refresh does not move the page');
  assert.equal(c.state.authBooting, false);
  assert.equal(requests(A + '/config').length, 0, 'config is only read for the gate');
});

test('reload: a replayed refresh token drops to the gate with the server line and clears storage', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = fail(401, 'replayed', 'That session was already used. Every session has been ended as a precaution — sign in again.');
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authMode, 'signin');
  assert.equal(c.state.authNotice, 'That session was already used. Every session has been ended as a precaution — sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('reload: a network failure keeps the person signed in on the stored account', async () => {
  cleanup();
  seedSession(USER, 1);
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.refreshToken, 'ref-1');
  assert.equal(c.state.account.email, 'sam@example.com');
  assert.equal(c.state.authBooting, false);
});

test('a bearer call that returns 401 expired is refreshed once and retried once', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  handlers['GET /backend/ops-intelligence/api/approvals'] = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true, items: [] }] : fail(401, 'expired', 'Token expired'));
  const r = await apiFetch(BASES.opsIntelligence, '/api/approvals');
  assert.equal(r.ok, true);
  assert.equal(requests(A + '/refresh').length, 1);
  assert.equal(requests('/backend/ops-intelligence/api/approvals').length, 2);
  assert.equal(c.state.accessToken, 'acc-2');
  assert.equal(c.state.signedIn, true);
});

test('two calls expiring together share one refresh', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/refresh'] = session(USER, 2);
  const guard = (u, o) => (o.headers.Authorization === 'Bearer acc-2' ? [200, { ok: true }] : fail(401, 'expired', 'Token expired'));
  handlers['GET /backend/ops-intelligence/api/a'] = guard;
  handlers['GET /backend/ops-intelligence/api/b'] = guard;
  await Promise.all([apiFetch(BASES.opsIntelligence, '/api/a'), apiFetch(BASES.opsIntelligence, '/api/b')]);
  assert.equal(requests(A + '/refresh').length, 1, 'single-flight');
});

test('a terminal 401 on any call signs out with the server line', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['GET /backend/ops-intelligence/api/a'] = fail(401, 'session_revoked', 'That session has ended. Sign in again.');
  await assert.rejects(apiFetch(BASES.opsIntelligence, '/api/a'));
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authNotice, 'That session has ended. Sign in again.');
  assert.equal(mem[SESSION_KEY], undefined);
  assert.equal(requests(A + '/refresh').length, 0);
});

test('a refresh presents the token from storage, not a stale copy in memory', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  // Another tab rotated to ref-7 and wrote it.
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-7' }));
  handlers['POST ' + A + '/refresh'] = session(USER, 8);
  await c.authRefresh();
  assert.equal(requests(A + '/refresh')[0].body.refresh_token, 'ref-7');
  assert.equal(c.state.refreshToken, 'ref-8');
});

test('another tab rotating the token is adopted; another tab signing out signs this one out', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  mem[SESSION_KEY] = JSON.stringify(Object.assign(stored(), { refreshToken: 'ref-5', account: Object.assign({}, USER, { full_name: 'Sam Five' }) }));
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.refreshToken, 'ref-5');
  assert.equal(c.state.account.full_name, 'Sam Five');
  assert.equal(c.state.signedIn, true);
  c.authStorageChanged({ key: 'something.else' });
  assert.equal(c.state.signedIn, true, 'other keys are ignored');
  delete mem[SESSION_KEY];
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, false);
});

// ── inside a session ─────────────────────────────────────────────────────────

test('change password 200 signs out with the server line — the API ended this session too', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.setState({ acctOpen: true });
  c.renderVals().acctItems.find((i) => i.label === 'Change password').click();
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.acctOpen, false);
  c.setState({ pwCurrent: 'x'.repeat(12), pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = [200, { ok: true, message: 'Your password is changed. Sign in again with the new one.', sessions_ended: 2 }];
  await c.pwSubmit();
  const r = requests(A + '/password/change')[0];
  assert.deepEqual(r.body, { current_password: 'x'.repeat(12), new_password: 'yet-another-good-passphrase' });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.accessToken, null);
  assert.equal(c.state.authNotice, 'Your password is changed. Sign in again with the new one.');
  assert.equal(c.state.pwCurrent, '');
  assert.equal(c.state.pwNext, '');
});

test('change password 401 and 400 stay in the modal with the message', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.pwOpenModal();
  c.setState({ pwCurrent: 'nope', pwNext: 'yet-another-good-passphrase' });
  handlers['POST ' + A + '/password/change'] = fail(401, 'invalid_credentials', 'Your current password is not correct.');
  await c.pwSubmit();
  assert.equal(c.state.signedIn, true, 'a wrong current password is not a dead session');
  assert.equal(c.state.pwOpen, true);
  assert.equal(c.state.pwError, 'Your current password is not correct.');
  assert.equal(c.state.pwBusy, false);
  handlers['POST ' + A + '/password/change'] = fail(400, 'password_unchanged', 'That is the password you already have.');
  await c.pwSubmit();
  assert.equal(c.state.pwError, 'That is the password you already have.');
  c.pwClose();
  assert.equal(c.state.pwOpen, false);
  assert.equal(c.state.pwError, '');
});

test('sign out everywhere sends everywhere:true with the bearer, then signs out locally', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/logout'] = [200, { ok: true, message: 'Signed out.', sessions_ended: 3 }];
  await c.renderVals().acctItems.find((i) => i.label === 'Sign out everywhere').click();
  const r = requests(A + '/logout')[0];
  assert.deepEqual(r.body, { everywhere: true });
  assert.equal(r.headers.Authorization, 'Bearer acc-1');
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out of 3 sessions.');
  assert.equal(mem[SESSION_KEY], undefined);
});

test('sign out everywhere still signs out locally when the call fails', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  await c.authSignOutEverywhere();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.toast, 'Signed out.');
});

// ── two tabs over one store ──────────────────────────────────────────────────

test('a tab on the gate never writes the store, so it cannot erase the session another tab holds', async () => {
  const gated = new HoistraLogic();          // opened first, sits on the gate
  gated.authBoot();
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  assert.equal(stored().refreshToken, 'ref-1');
  gated.setState((p) => ({ frame: (p.frame + 1) % 4 }));   // the gate animation ticks every 3.2 s
  gated.setState({ email: 'someone@else.com' });           // and the person types
  assert.equal(stored().refreshToken, 'ref-1', 'the gated tab left the key alone');
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, true);
  gated.authStop();
});

test('a tab on the gate ignores another tab signing in, and the key survives', async () => {
  const gated = new HoistraLogic();
  gated.authBoot();
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  gated.authStorageChanged({ key: SESSION_KEY });          // what the browser fires in the other tab
  assert.equal(gated.state.signedIn, false);
  assert.equal(gated.state.refreshToken, null, 'no stale credential in a gated tab');
  assert.equal(stored().refreshToken, 'ref-1');
  c.authStorageChanged({ key: SESSION_KEY });
  assert.equal(c.state.signedIn, true);
  gated.authStop();
});

test('signing out removes the key exactly once, on the way out', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  c.renderVals().signOut();
  assert.equal(mem[SESSION_KEY], undefined);
  mem[SESSION_KEY] = JSON.stringify({ signedIn: true, refreshToken: 'ref-other', account: USER, view: 'home' }); // another tab signs in later
  c.setState({ email: 'typing@again.com' });
  assert.equal(JSON.parse(mem[SESSION_KEY]).refreshToken, 'ref-other', 'this gated tab did not touch it');
});

test('reload: a 5xx on the refresh keeps the session — only a 401 means the token is finished', async () => {
  cleanup();
  seedSession(USER, 1);
  handlers['POST ' + A + '/refresh'] = [503, { detail: 'Service Unavailable' }];
  c = new HoistraLogic();
  await c.authBoot();
  assert.equal(c.state.signedIn, true);
  assert.equal(c.state.refreshToken, 'ref-1', 'the credential is kept for the next try');
  assert.equal(c.state.authNotice, '');
});

test('sign out everywhere keeps the server line when the call itself ended the session', async () => {
  handlers['POST ' + A + '/login'] = session(USER, 1);
  c.setState({ email: 'sam@example.com', password: 'x'.repeat(12) });
  await c.authSignIn();
  handlers['POST ' + A + '/logout'] = fail(401, 'session_revoked', 'That session has ended. Sign in again.');
  await c.authSignOutEverywhere();
  assert.equal(c.state.signedIn, false);
  assert.equal(c.state.authNotice, 'That session has ended. Sign in again.');
  assert.equal(c.state.toast, 'Signed out.');
});
