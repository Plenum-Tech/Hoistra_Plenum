// auth — accounts and sessions against /api/auth (api/auth.js). Methods are mixed into
// HoistraLogic.prototype; `this` is the controller.
//
// `signedIn` stays the one boolean the shell keys off. `role` stays the view mode
// (user | admin); whether it may ever be admin is decided by the account's real role.
// The access token lives in state only; the refresh token is the persisted credential.
import { authApi } from '../api/auth.js';
import { configureAuth } from '../api/client.js';
import { loadSession, SESSION_KEY } from './session.js';

export const ADMIN_ROLES = new Set(['admin', 'superadmin']);
export const canAdmin = (account) => !!account && ADMIN_ROLES.has(account.role);

// The reference's published defaults, used until GET /config answers (or if it never does).
export const AUTH_FALLBACK_CONFIG = {
  self_registration: true,
  password: { min_length: 12 },
  otp: { code_length: 6, ttl_minutes: 10, max_attempts: 5, resend_cooldown_seconds: 60, max_per_hour: 5 }
};

// "123 456" and "123-456" are the same code.
export const normaliseCode = (s) => String(s || '').replace(/[\s-]/g, '');

// After these the code is dead — the right digits will not work either.
export const DEAD_CODE = new Set(['expired', 'too_many_attempts', 'no_code']);

export const AUTH_DEFAULTS = {
  account: null, accessToken: null, refreshToken: null, authBooting: false,
  authMode: 'signin', authBusy: false, authError: '', authNotice: '', authReason: '',
  authAttemptsLeft: null, authRetryAt: 0, authTick: 0, authConfig: null,
  password: '', fullName: '', phone: '', code: '', newPassword: '',
  pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: ''
};

const NETWORK_MSG = "Couldn't reach the sign-in service. Check the backend is running.";
const SCHEMA_MSG = 'Enter a valid email address and password.';
const SSO_MSG = 'Single sign-on is not available yet — sign in with your email and password.';

// One ApiError → what the gate shows. A 422 is FastAPI's schema array, written for
// developers; the gate says one plain thing instead. Anything without a status is the network.
export function failurePatch(e) {
  if (!e || typeof e.status !== 'number') return { authError: NETWORK_MSG, authReason: '' };
  if (e.status === 422) return { authError: SCHEMA_MSG, authReason: '' };
  return { authError: e.message || String(e.status), authReason: e.reason || '' };
}
const detailOf = (e) => { const d = e && e.body && e.body.detail; return d && typeof d === 'object' && !Array.isArray(d) ? d : {}; };
const retryAfter = (e) => { const n = Number(detailOf(e).retry_after_seconds); return Number.isFinite(n) && n > 0 ? n : 0; };
const attemptsLeft = (e) => { const n = detailOf(e).attempts_remaining; return typeof n === 'number' ? n : null; };
const trimmed = (v) => String(v || '').trim();

export const authMethods = {
  // ── lifecycle ──────────────────────────────────────────────────────────
  // Installs the client hooks, then renews a stored session. The stored account already
  // rendered the shell (loadSession restored signedIn), so the refresh only has to swap in
  // fresh tokens; a 401 here drops to the gate, a network failure leaves things as they are.
  authBoot() {
    configureAuth({
      getToken: () => this.state.accessToken,
      refresh: () => this.authRefresh(),
      onTerminal: (reason, message) => this.authSignedOut(message)
    });
    this._authStorage = (e) => this.authStorageChanged(e);
    window.addEventListener('storage', this._authStorage);
    if (!this.state.refreshToken) {
      this.authLoadConfig();
      return Promise.resolve();
    }
    this.setState({ authBooting: true });
    return this.authRefresh().catch(() => {}).then(() => this.setState({ authBooting: false }));
  },

  authStop() {
    window.removeEventListener('storage', this._authStorage);
    clearInterval(this._authTimer); this._authTimer = null;
  },

  authLoadConfig() {
    if (this._authConfigAsked) return;
    this._authConfigAsked = true;
    authApi.config().then((cfg) => { if (cfg && cfg.ok) this.setState({ authConfig: cfg }); }).catch(() => {});
  },

  // Two tabs share the stored refresh token but each keeps its own access token. When the
  // other tab rotates, this one adopts the new token so its next refresh is not a replay;
  // when the other tab signs out, this one follows. Same-tab writes never fire this event.
  authStorageChanged(e) {
    if (e && e.key && e.key !== SESSION_KEY) return;
    // Only a signed-in tab follows the store. A tab on the gate is not part of the session
    // another tab opened; adopting its token there would only leave a stale credential behind.
    if (!this.state.signedIn) return;
    const s = loadSession();
    if (!s.refreshToken) { this.authSignedOut(''); return; }
    if (s.refreshToken !== this.state.refreshToken) {
      this.setState({ refreshToken: s.refreshToken, account: s.account || this.state.account });
    }
  },

  // Single-flight: the 401 interceptor, boot and a second caller all await one promise.
  // The token is re-read from storage because another tab may have rotated it; presenting
  // the stale copy is a replay, and a replay revokes every session on the account.
  authRefresh() {
    if (this._refreshing) return this._refreshing;
    const token = loadSession().refreshToken || this.state.refreshToken;
    if (!token) return Promise.reject(new Error('no refresh token'));
    this._refreshing = authApi.refresh(token)
      .then((resp) => { this.authEnter(resp, { keepView: true }); return resp.tokens.access_token; })
      .catch((e) => {
        // Only a 401 means the token is finished. A 5xx from the gateway mid-deploy is the
        // network's problem, and the credential is kept for the next try.
        if (e && e.status === 401) this.authSignedOut(e.message || '');
        throw e;
      })
      .finally(() => { this._refreshing = null; });
    return this._refreshing;
  },

  // ── entering and leaving ───────────────────────────────────────────────
  // Apply a SessionResponse. `keepView` is the reload refresh, which must not move the page.
  authEnter(resp, opts) {
    const o = opts || {};
    const admin = canAdmin(resp.user);
    this.setState((p) => ({
      account: resp.user, accessToken: resp.tokens.access_token, refreshToken: resp.tokens.refresh_token,
      signedIn: true,
      role: admin ? p.role : 'user',
      view: o.keepView ? p.view : 'home', navOpen: o.keepView ? p.navOpen : true,
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null, authRetryAt: 0,
      password: '', code: '', newPassword: '', fullName: '', phone: ''
    }));
    if (resp.message && !o.keepView) this.flash(resp.message);
  },

  // Local sign-out. `notice` is what the gate shows — the server's own line when it ended
  // the session, nothing when the person chose to leave.
  authSignedOut(notice) {
    this.setState({
      account: null, accessToken: null, refreshToken: null, signedIn: false, role: 'user',
      view: 'home', navOpen: false, queueOpen: false, detail: null, acctOpen: false,
      pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: '',
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null,
      authNotice: notice || '', password: '', code: '', newPassword: ''
    });
    this.authLoadConfig();
  },

  authSignOut() {
    const token = this.state.refreshToken;
    if (token) authApi.logout(token).catch(() => {});
    this.authSignedOut('');
  },

  async authSignOutEverywhere() {
    let ended = null;
    try {
      const r = await authApi.logoutEverywhere();
      ended = typeof r.sessions_ended === 'number' ? r.sessions_ended : null;
    } catch (e) { /* signing out locally is the point; the server side is best effort */ }
    // A terminal 401 inside the call has already signed out with the server's line; keep it.
    if (this.state.signedIn) this.authSignedOut('');
    this.flash(ended === null ? 'Signed out.' : 'Signed out of ' + ended + ' session' + (ended === 1 ? '' : 's') + '.');
  },

  // ── change password ────────────────────────────────────────────────────
  pwOpenModal() { this.setState({ pwOpen: true, acctOpen: false, pwCurrent: '', pwNext: '', pwError: '', pwBusy: false }); },
  pwClose() { this.setState({ pwOpen: false, pwCurrent: '', pwNext: '', pwError: '', pwBusy: false }); },

  async pwSubmit() {
    const s = this.state;
    if (s.pwBusy) return;
    this.setState({ pwBusy: true, pwError: '' });
    try {
      const resp = await authApi.changePassword(s.pwCurrent, s.pwNext);
      // Every session is ended, this one included: the next call would be 401 password_changed.
      this.authSignedOut((resp && resp.message) || 'Your password is changed. Sign in again with the new one.');
    } catch (e) {
      if (!this.state.signedIn) return; // a terminal 401 already signed us out
      const msg = !e || typeof e.status !== 'number' ? NETWORK_MSG
        : e.status === 422 ? 'Enter your current password and a new one.'
        : e.message;
      this.setState({ pwBusy: false, pwError: msg });
    }
  },

  // ── the gate ───────────────────────────────────────────────────────────
  authGo(mode) {
    this.setState({ authMode: mode, authError: '', authReason: '', authAttemptsLeft: null, code: '' });
  },

  authSSO() { this.flash(SSO_MSG); },

  async authSignIn() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      this.authEnter(await authApi.login(trimmed(s.email), s.password));
    } catch (e) {
      const patch = Object.assign({ authBusy: false }, failurePatch(e));
      if (e && e.status === 403 && e.reason === 'email_not_verified') {
        // The server has already sent a fresh code: go to the code screen, ask for nothing.
        Object.assign(patch, { authMode: 'verify', authNotice: e.message, authError: '', code: '' });
        this.authArm(this.authOtp().resend_cooldown_seconds);
      } else if (e && e.status === 429) {
        this.authArm(retryAfter(e));
      }
      this.setState(patch);
    }
  },

  async authRegister() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      const resp = await authApi.register({ email: trimmed(s.email), password: s.password, full_name: trimmed(s.fullName), phone: trimmed(s.phone) || null });
      this.authAccepted(resp, 'verify');
    } catch (e) {
      const patch = Object.assign({ authBusy: false }, failurePatch(e));
      if (e && e.reason === 'registration_closed') patch.authConfig = Object.assign({}, s.authConfig || AUTH_FALLBACK_CONFIG, { self_registration: false });
      this.setState(patch);
    }
  },

  // A 202 that sent a code: the code screen, the server's line, and the resend cooldown
  // armed from the limits it published. Any earlier code is dead, so whatever was typed goes.
  // The password is not needed again — verify-email signs the person in — so it leaves memory.
  authAccepted(resp, mode) {
    const otp = Object.assign({}, this.authOtp(), (resp && resp.otp) || {});
    // The server's own wording, plus the standing fact that this deployment drops mail.
    // Without the second half the screen says a code is on its way and then asks for it,
    // and the only way to learn otherwise is to wait for an email that was never sent.
    const notice = (resp && resp.message) || '';
    this.setState({ authBusy: false, authMode: mode, authNotice: notice + this.authDeliveryNote(), authError: '', authReason: '', authAttemptsLeft: null, code: '', password: '' });
    this.authArm(otp.resend_cooldown_seconds);
  },

  async authVerify() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      this.authEnter(await authApi.verifyEmail(trimmed(s.email), normaliseCode(s.code)));
    } catch (e) {
      this.setState(Object.assign({ authBusy: false }, this.authCodeFailure(e)));
    }
  },

  // A refused code, for verify-email and password/reset alike. A dead code is cleared so the
  // emphasis moves to Resend; a wrong one is kept for a retype.
  authCodeFailure(e) {
    const patch = failurePatch(e);
    patch.authAttemptsLeft = attemptsLeft(e);
    if (e && DEAD_CODE.has(e.reason)) patch.code = '';
    if (e && e.status === 404) patch.authMode = 'signin';
    return patch;
  },

  // Verify mode asks for another confirmation code; reset mode asks for another reset code.
  // The two purposes are separate on the server and a confirmation code will not reset.
  async authResend() {
    const s = this.state;
    if (s.authBusy || this.authCooling()) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      const email = trimmed(s.email);
      const resp = s.authMode === 'reset' ? await authApi.forgot(email) : await authApi.resendCode(email);
      this.authAccepted(resp, s.authMode);
    } catch (e) {
      if (e && e.status === 429) this.authArm(retryAfter(e));
      this.setState(Object.assign({ authBusy: false }, failurePatch(e)));
    }
  },

  async authForgot() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authNotice: '', authReason: '' });
    try {
      this.authAccepted(await authApi.forgot(trimmed(s.email)), 'reset');
    } catch (e) {
      if (e && e.status === 429) this.authArm(retryAfter(e));
      this.setState(Object.assign({ authBusy: false }, failurePatch(e)));
    }
  },

  // The server verifies the code without consuming it and spends it only when the write is
  // certain, so a rejected password leaves the code good: only the password is retyped.
  async authReset() {
    const s = this.state;
    if (s.authBusy) return;
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      const resp = await authApi.reset(trimmed(s.email), normaliseCode(s.code), s.newPassword);
      this.setState({ authBusy: false, authMode: 'signin', authNotice: (resp && resp.message) || '', authError: '', authReason: '', authAttemptsLeft: null, authRetryAt: 0, code: '', newPassword: '', password: '' });
    } catch (e) {
      const patch = this.authCodeFailure(e);
      if (e && (e.reason === 'password' || e.reason === 'password_unchanged')) patch.newPassword = '';
      this.setState(Object.assign({ authBusy: false }, patch));
    }
  },

  // ── cooldowns and lockouts ─────────────────────────────────────────────
  authArm(seconds) {
    const n = Number(seconds) || 0;
    if (n <= 0) return;
    this.setState({ authRetryAt: Date.now() + n * 1000 });
    if (this._authTimer) return;
    this._authTimer = setInterval(() => {
      this.setState((p) => ({ authTick: p.authTick + 1 }));
      if (this.state.authRetryAt <= Date.now()) { clearInterval(this._authTimer); this._authTimer = null; }
    }, 1000);
  },
  authCooling() { return this.state.authRetryAt > Date.now(); },
  authCountdown() {
    const ms = this.state.authRetryAt - Date.now();
    if (ms <= 0) return '';
    const t = Math.ceil(ms / 1000);
    const m = Math.floor(t / 60), sec = t % 60;
    return (m < 10 ? '0' : '') + m + ':' + (sec < 10 ? '0' : '') + sec;
  },
  // Appended wherever the UI says a code has been sent. Empty on a deployment that sends
  // them, which is every deployment that matters — this exists so the ones that do not say
  // so out loud instead of looking broken.
  authDeliveryNote() {
    const d = (this.state.authConfig || {}).email_delivery;
    if (!d || d.live !== false) return '';
    return d.dry_run
      ? ' This environment has email delivery turned off (EMAIL_DRY_RUN), so the code was '
        + 'written to the log rather than sent.'
      : ' This environment has no mail transport configured, so the code was written to the '
        + 'log rather than sent.';
  },

  authOtp() {
    const cfg = this.state.authConfig || AUTH_FALLBACK_CONFIG;
    return Object.assign({}, AUTH_FALLBACK_CONFIG.otp, cfg.otp || {});
  },

  // ── view model ─────────────────────────────────────────────────────────
  authVals(s) {
    const admin = canAdmin(s.account);
    const cfg = s.authConfig || AUTH_FALLBACK_CONFIG;
    const cooling = this.authCooling();
    const primary = { signin: 'authSignIn', register: 'authRegister', verify: 'authVerify', forgot: 'authForgot', reset: 'authReset' }[s.authMode] || 'authSignIn';
    const field = (k) => (e) => this.setState({ [k]: e.target.value });
    const a = s.account || {};
    const name = a.full_name || a.email || '';
    return {
      // the gate
      email: s.email, setEmail: field('email'),
      password: s.password, setPassword: field('password'),
      fullName: s.fullName, setFullName: field('fullName'),
      phone: s.phone, setPhone: field('phone'),
      code: s.code, setCode: field('code'),
      newPassword: s.newPassword, setNewPassword: field('newPassword'),
      authMode: s.authMode, authBusy: s.authBusy, authBooting: s.authBooting,
      authError: s.authError, authNotice: s.authNotice, authReason: s.authReason,
      authAttemptsLeft: s.authAttemptsLeft,
      authCountdown: this.authCountdown(), authCoolingDown: cooling,
      authLocked: s.authMode === 'signin' && s.authReason === 'locked' && cooling,
      authDeadCode: DEAD_CODE.has(s.authReason),
      authCanRegister: cfg.self_registration !== false,
      authMinLength: (cfg.password && cfg.password.min_length) || AUTH_FALLBACK_CONFIG.password.min_length,
      authCodeLength: this.authOtp().code_length,
      authKey: (e) => { if (e.key === 'Enter') this[primary](); },
      authSignIn: () => this.authSignIn(), authSSO: () => this.authSSO(),
      authRegister: () => this.authRegister(), authVerify: () => this.authVerify(), authResend: () => this.authResend(),
      authForgot: () => this.authForgot(), authReset: () => this.authReset(),
      authGoSignin: () => this.authGo('signin'), authGoRegister: () => this.authGo('register'), authGoForgot: () => this.authGo('forgot'),
      signOut: () => this.authSignOut(),

      /* Account menu. The admin view is a mode, not a page: switching into it leaves only
         the admin surfaces in the navigator, so a configuration session cannot be confused
         with reading a report. Only an account whose real role allows it is offered the
         switch; everyone else has User view and nothing to toggle. */
      acctOpen: s.acctOpen,
      toggleAcct: () => this.setState((p) => ({ acctOpen: !p.acctOpen })),
      closeAcct: () => this.setState({ acctOpen: false }),
      acctName: name || 'Account', acctEmail: a.email || '',
      acctInitial: (name || '?').trim().charAt(0).toUpperCase(),
      canAdmin: admin,
      acctRole: s.role === 'admin' ? 'Admin view' : 'User view',
      acctBg: s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-neutral-900)',
      acctFg: s.role === 'admin' ? 'var(--accent-ink)' : 'var(--color-neutral-300)',
      acctEdge: s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-divider)',
      acctItems: [
        { label: 'Pricing', icon: 'ph-tag', click: () => this.setState({ acctOpen: false }, () => this.flash('Pricing and plan usage open in the billing workspace — seats, buildings hoisted and ingest volume.')) },
        { label: 'Support', icon: 'ph-lifebuoy', click: () => this.setState({ acctOpen: false }, () => this.flash('Support: a Hoister is on call for this portfolio. Every request carries the page and the graph state you were on.')) },
        admin ? {
          label: s.role === 'admin' ? 'User view' : 'Admin view',
          icon: s.role === 'admin' ? 'ph-user-focus' : 'ph-shield-star', tick: false,
          click: () => this.setState((p) => ({
            role: p.role === 'admin' ? 'user' : 'admin',
            acctOpen: false,
            view: p.role === 'admin' ? 'home' : 'buildings',
            navOpen: true, detail: null
          }))
        } : null,
        { label: 'Change password', icon: 'ph-key', click: () => this.pwOpenModal() },
        { label: 'Sign out everywhere', icon: 'ph-power', click: () => this.authSignOutEverywhere() }
      ].filter(Boolean).map((i) => ({
        label: i.label, icon: i.icon, click: i.click,
        fg: i.tick ? 'var(--color-accent)' : 'var(--color-text)',
        iconFg: i.tick ? 'var(--color-accent)' : 'var(--color-neutral-500)',
        tickShow: i.tick ? 'block' : 'none'
      })),

      // change-password modal
      pwOpen: s.pwOpen, pwCurrent: s.pwCurrent, pwNext: s.pwNext, pwBusy: s.pwBusy, pwError: s.pwError,
      pwSetCurrent: field('pwCurrent'), pwSetNext: field('pwNext'),
      pwClose: () => this.pwClose(), pwSubmit: () => this.pwSubmit(),
      pwKey: (e) => { if (e.key === 'Enter') this.pwSubmit(); if (e.key === 'Escape') this.pwClose(); }
    };
  }
};
