// auth — accounts and sessions against /api/auth (api/auth.js). Methods are mixed into
// HoistraLogic.prototype; `this` is the controller.
//
// `signedIn` stays the one boolean the shell keys off. `role` stays the view mode
// (user | admin); whether it may ever be admin is decided by the account's real role.
// The access token lives in state only; the refresh token is the persisted credential.
import { authApi } from '../api/auth.js';
import { adminApi } from '../api/admin.js';
import { ApiError, configureAuth, setActingOrg } from '../api/client.js';
import { forgetIssuedToken, forgetSharedSession, isSupersededToken, issuedTokenKey, latestIssuedRecord, loadSharedSession, recordIssuedToken, SESSION_KEY } from './session.js';

// Resolves on the next storage event for this account's session slot or token record, or
// after `ms` — whichever is first. Never rejects; without a window it simply waits.
function storageSettles(accountId, ms) {
  return new Promise((resolve) => {
    let done = false;
    let timer = null;
    const keys = [SESSION_KEY, issuedTokenKey(accountId)];
    const onStorage = (e) => { if (!e || !e.key || keys.indexOf(e.key) >= 0) finish(); };
    function finish() {
      if (done) return;
      done = true;
      clearTimeout(timer);
      try { window.removeEventListener('storage', onStorage); } catch (e) { /* no window */ }
      resolve();
    }
    try { window.addEventListener('storage', onStorage); } catch (e) { /* no window */ }
    timer = setTimeout(finish, ms);
  });
}

// One refresh at a time per account, across every tab of this origin (the Web Locks API:
// every browser this ships to has it, and so does node 24). The server accepts the token a
// rotation replaced for only 30 s and answers anything later by ending every session, so
// two tabs whose access tokens expire together must not both exchange the same refresh
// token: that forked the session, and whichever tab's copy was saved last left the other
// holding the replaced one — the replay thirty minutes on (30 Sep 2026). The second tab
// now waits, then reads the token the first was just issued. Where the lock machinery
// itself is missing or refuses, the exchange runs as it always did.
// The token a refresh (or a sign-out) presents: the NEWEST this browser holds for the tab's
// account, by the time it was issued — among the account's own record (which no other
// company's tab can overwrite), the shared slot while it is this account's, and the tab's own
// copy. Newest, not "whichever is in the shared slot": a tab can save an older token back from
// a view of storage a few milliseconds behind (traced in Chrome, 1 Oct 2026). A shared token
// with no issue time was written by a build from before them; it is followed as that build
// did, because it is how an old tab's legitimate rotation reaches this one.
function newestRefresh(state) {
  const mine = state && state.account && state.account.id;
  const shared = loadSharedSession();
  const sameAccount = !!(shared.account && mine && shared.account.id === mine);
  const own = state && state.refreshToken ? { token: state.refreshToken, at: state.refreshTokenAt || 0 } : null;
  const fromShared = sameAccount && shared.refreshToken ? { token: shared.refreshToken, at: shared.refreshTokenAt || 0 } : null;
  if (fromShared && !fromShared.at && (!own || fromShared.token !== own.token) && !isSupersededToken(mine, fromShared.token)) return fromShared;
  let best = null;
  [latestIssuedRecord(mine), fromShared, own].forEach((x) => { if (x && x.token && (!best || x.at > best.at)) best = x; });
  return best;
}
function newestRefreshToken(state) {
  const n = newestRefresh(state);
  return n ? n.token : null;
}

// The issue time a newly issued token is stamped with: now, and never earlier than any this
// browser already knows for the account, so "newer" holds even inside one millisecond.
function issueStamp(accountId, state) {
  const rec = latestIssuedRecord(accountId);
  const shared = loadSharedSession();
  const sharedAt = shared.account && shared.account.id === accountId ? (shared.refreshTokenAt || 0) : 0;
  const known = Math.max(rec ? rec.at : 0, sharedAt, (state && state.refreshTokenAt) || 0);
  return Math.max(Date.now(), known + 1);
}

//
// The wait is bounded: a tab that stopped while holding the lock (frozen, or its exchange hung
// past the 20 s request timeout) must not leave every other tab of the account — and, through
// client.js's wait for a refresh in flight, every read they make — waiting behind it.
export const REFRESH_LOCK_WAIT_MS = 25000;
export function withRefreshLock(accountId, fn, waitMs) {
  let locks = null;
  try { locks = typeof navigator !== 'undefined' && navigator ? navigator.locks : null; } catch (e) { locks = null; }
  if (!locks || typeof locks.request !== 'function') return Promise.resolve().then(fn);
  return new Promise((resolve, reject) => {
    let ran = false;
    let held;
    try {
      const opts = typeof AbortSignal !== 'undefined' && typeof AbortSignal.timeout === 'function'
        ? { signal: AbortSignal.timeout(waitMs || REFRESH_LOCK_WAIT_MS) } : {};
      held = locks.request('hoistra.refresh.' + (accountId || 'signed-out'), opts, () => { ran = true; return fn(); });
    } catch (e) {
      resolve(Promise.resolve().then(fn));
      return;
    }
    Promise.resolve(held).then(resolve, (e) => (ran ? reject(e) : resolve(Promise.resolve().then(fn))));
  });
}

export const ADMIN_ROLES = new Set(['admin', 'superadmin']);
export const canAdmin = (account) => !!account && ADMIN_ROLES.has(account.role);

// A signed-in plain user allocated to no building at all. GET /api/auth/me reports
// building_ids and all_buildings, so the page can tell this apart from "nothing loaded yet"
// and from "the table really is empty" — three states that look identical on screen and are
// fixed in completely different places. An account like this sees nothing from any
// building-scoped endpoint, and every screen that stays silent about it sends the reader
// looking for a bug in the data instead of asking an admin for an allocation.
// all_buildings (admin/superadmin) is never unallocated, whatever building_ids says.
export const isUnallocated = (account) =>
  !!account && !!account.id && account.all_buildings !== true
  && Array.isArray(account.building_ids) && account.building_ids.length === 0;

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
  account: null, accessToken: null, refreshToken: null, refreshTokenAt: null, authBooting: false,
  authMode: 'signin', authBusy: false, authError: '', authNotice: '', authReason: '',
  authAttemptsLeft: null, authRetryAt: 0, authTick: 0, authConfig: null,
  password: '', fullName: '', phone: '', code: '', newPassword: '',
  // The token off an invitation link, held only while authMode is 'invite'. Never stored:
  // it is a one-shot credential, and the link itself is the only copy that should exist.
  inviteToken: '',
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

// May this person add data at all — the "Can ingest" toggle on Users & access, read back
// for the person signed in rather than only shown as a column about other people. Every
// ingest control on the page hides behind it.
//
// Two deliberate choices. The role is NOT folded in again: the server already does that
// (tokens.py — can_ingest OR admin/superadmin), and doing it twice here would keep the
// affordance for an admin who was explicitly turned off. And a missing key reads as
// ALLOWED, not denied: only GET /me carries the flag, authLoadScope() is best-effort and
// silent, so treating "nothing has said yet" as No would blink the control off on every
// sign-in and hide it for good whenever that one read fails.
export function accountCanIngest(s) {
  // `can_ingest === false` is the ONLY denial. Not an absent account, not an absent key —
  // both of those are "nothing has said", and the paragraph above is why that must read as
  // allowed. Signed out is different: there is no account to ingest on behalf of.
  return !!(s && s.signedIn) && !(s.account && s.account.can_ingest === false);
}

export const authMethods = {
  // ── lifecycle ──────────────────────────────────────────────────────────
  // Installs the client hooks, then renews a stored session. The stored account already
  // rendered the shell (loadSession restored signedIn), so the refresh only has to swap in
  // fresh tokens; a 401 here drops to the gate, a network failure leaves things as they are.
  authBoot() {
    // An invitation link opened in this tab takes precedence over any stored session:
    // it is for setting up the invited account, so the gate shows that form first.
    this.authInviteFromUrl();
    configureAuth({
      getToken: () => this.state.accessToken,
      refresh: () => this.authRefresh(),
      // The refresh in flight, if any — a read with no token yet waits for it (client.js).
      pending: () => this._refreshing || null,
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

  // Two tabs on the SAME account share the stored refresh token but each keeps its own
  // access token. When the other tab rotates, this one adopts the new token so its next
  // refresh is not a replay; when the other tab signs out, this one follows. Same-tab
  // writes never fire this event.
  //
  // The shared slot now routinely belongs to a DIFFERENT account, though — company-
  // switchable accounts mean one tab is routinely TechCorp while another is Plenum Tech,
  // both writing the one slot localStorage has room for. Without the account check below,
  // one tab signing in (or just rotating its token) fired this listener in every other
  // tab, and a tab reading a different account's token here silently adopted it — a live
  // hijack of an in-use session, worse than the same mix-up surfacing only on reload.
  authStorageChanged(e) {
    const mine = this.state.account && this.state.account.id;
    const issuedHere = !!(mine && e && e.key === issuedTokenKey(mine));
    if (e && e.key && e.key !== SESSION_KEY && !issuedHere) return;
    // Only a signed-in tab follows the store. A tab on the gate is not part of the session
    // another tab opened; adopting its token there would only leave a stale credential behind.
    if (!this.state.signedIn) return;
    // Another tab of this account was issued a token: take it, whoever holds the shared
    // slot now. That record going away is not a sign-out on its own — the shared slot
    // clearing, below, is what says the session ended.
    if (issuedHere) {
      const rec = latestIssuedRecord(mine);
      if (rec && rec.token !== this.state.refreshToken && rec.at > (this.state.refreshTokenAt || 0)) {
        this.setState({ refreshToken: rec.token, refreshTokenAt: rec.at });
        return;
      }
      // The record removed: a tab of this account signed out of the session it held. When
      // that was this tab's session too — its own token, or one at least as new — follow it:
      // a sibling left signed in would refresh on the ended session in a while, and the
      // server answers that by ending every session the account has, on every device. This
      // reaches the sibling even while another company holds the shared slot (review).
      if (!rec) {
        let old = null;
        try { old = e && e.oldValue ? JSON.parse(e.oldValue) : null; } catch (err) { old = null; }
        const oldAt = old && typeof old.at === 'number' ? old.at : 0;
        if (old && old.token && (old.token === this.state.refreshToken || oldAt >= (this.state.refreshTokenAt || 0))) {
          this.authSignedOut('');
        }
      }
      return;
    }
    // Read the shared slot directly — loadSession() now prefers this tab's own
    // sessionStorage copy, which is exactly the wrong thing to consult about a change to
    // the other, shared store this listener exists to react to.
    const s = loadSharedSession();
    // A different account occupying the shared slot is a different tab's own business.
    if (s.account && this.state.account && s.account.id !== this.state.account.id) return;
    if (!s.refreshToken) {
      // The shared slot went empty. A real browser storage event carries what was just
      // cleared in e.oldValue; only treat this as OUR account signing out if that old
      // value was ours (or nothing conclusive is available) — never assume it on a value
      // that was provably a different account's.
      let oldToken = null;
      try { oldToken = e && e.oldValue ? JSON.parse(e.oldValue).refreshToken : null; } catch (err) { oldToken = null; }
      if (!oldToken || oldToken === this.state.refreshToken) this.authSignedOut('');
      return;
    }
    // Only a NEWER token: a tab saving from a view of storage a few milliseconds behind puts
    // an older one here, and adopting it is what handed the tab that had just rotated its own
    // replaced token back (1 Oct 2026). A slot from a build before issue times is followed.
    const newer = s.refreshTokenAt
      ? s.refreshTokenAt > (this.state.refreshTokenAt || 0)
      : !isSupersededToken(mine, s.refreshToken);
    if (s.refreshToken !== this.state.refreshToken && newer) {
      // A token from a tab still on the build before issue times arrives unstamped. It is
      // followed as that build's own tabs would follow it — the only way its rotations reach
      // this tab — so it is stamped now and recorded for the account, or the record this tab
      // stamped earlier would outrank it and the next refresh present the replaced token.
      // Once every open tab runs this build, nothing unstamped is written any more.
      const at = s.refreshTokenAt || issueStamp(mine, this.state);
      if (!s.refreshTokenAt) recordIssuedToken(mine, s.refreshToken, at);
      this.setState({ refreshToken: s.refreshToken, refreshTokenAt: at, account: s.account || this.state.account });
    }
  },

  // Single-flight: the 401 interceptor, boot and a second caller all await one promise.
  // The token is re-read from the SHARED slot, because another tab of the SAME account
  // may have rotated it there; presenting the stale copy is a replay, and a replay
  // revokes every session on the account. Only consulted when that shared slot is still
  // this account's, though — a different account's token sitting there (another company
  // entirely, in another tab) is irrelevant to this refresh and must never be presented.
  authRefresh() {
    if (this._refreshing) return this._refreshing;
    const token = newestRefreshToken(this.state);
    if (!token) {
      // No refresh token and a signed-in shell is a session that cannot be repaired: there
      // is nothing to refresh with, so every read will 401 for as long as the tab is open.
      //
      // This rejected with a plain Error, which carries no `status`, so the catch below —
      // which signs out only on a 401 — never fired. The shell stayed "signed in" holding
      // no usable credential, every panel rendered "Unreachable — 401", and each read went
      // on retrying on its own timer: 117 requests in ten minutes against a session that
      // had been dead for five hours, with no sign-in gate ever offered.
      //
      // Easiest to reach by opening a second origin — the Vite dev server on :5174 and the
      // gateway on :3000 keep separate storage — but any half-written session does it.
      //
      // Signing out is the honest end: it is the same conclusion the 401 branch reaches,
      // for a credential that is just as finished. authBoot never comes through here (it
      // checks for the token first and shows the gate), so this only ever fires mid-session.
      //
      // Only a signed-in shell has a session to end, though. A tab still on the gate reaches
      // here whenever a loader's 401 missing_token asks for a refresh, and signing it out
      // told someone who never signed in that their session had ended — and wiped the
      // password, code and new password they were typing, on every loader retry.
      if (this.state.signedIn) this.authSignedOut('Your session has ended. Sign in again.');
      // Shaped like the 401 the server would have sent, so apiFetch's interceptor treats it
      // as terminal rather than retrying a refresh that can never succeed.
      return Promise.reject(new ApiError('no refresh token', 401, {
        detail: { ok: false, error: 'Your session has ended. Sign in again.', reason: 'invalid' }
      }));
    }
    const accountId = this.state.account && this.state.account.id;
    const askedAt = Date.now();
    this._refreshing = withRefreshLock(accountId, async () => {
      // Picked again once the lock is held: another tab of this account may have been
      // issued a new token while this one waited, and the one it replaced is spent.
      let current = newestRefreshToken(this.state);
      // Waited behind another tab and still see the token from before? Its write may not
      // have reached this tab's view of storage yet — traced in Chrome at a two-tab boot:
      // the second tab presented the replaced token 40 ms later, a grace fork. A moment's
      // wait for the storage event (never more than 300 ms) and a second look.
      if (current && current === token && Date.now() - askedAt > 15) {
        await storageSettles(accountId, 300);
        current = newestRefreshToken(this.state) || current;
      }
      if (!current) {
        return Promise.reject(new ApiError('no refresh token', 401, {
          detail: { ok: false, error: 'Your session has ended. Sign in again.', reason: 'invalid' }
        }));
      }
      // Entered before the lock is let go, so the next tab to take it reads this token —
      // from the shared slot and from the account's own record alike.
      return authApi.refresh(current).then((resp) => { this.authEnter(resp, { keepView: true, replaced: current }); return resp.tokens.access_token; });
    })
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
    // Every token the server issues is recorded against its account first — the one record
    // no other company's tab can overwrite (session.js latestIssuedRecord) — stamped with when.
    const issuedAt = issueStamp(resp.user && resp.user.id, this.state);
    recordIssuedToken(resp.user && resp.user.id, resp.tokens && resp.tokens.refresh_token, issuedAt, o.replaced);
    const admin = canAdmin(resp.user);
    const isSuperadmin = resp.user.role === 'superadmin';
    // Where each role lands, on a FRESH sign-in only (keepView is the reload refresh,
    // which must not move the page — a refresh keeps whatever view/mode was already
    // open). A user is always 'user'; an admin always opens in admin view rather than
    // inheriting whatever mode a previous session left behind; a superadmin goes
    // straight into the platform console instead of the company app underneath it.
    // A fresh sign-in never inherits a previous account's viewAsCompany() override —
    // that is a superadmin-only, explicitly-chosen state, never something the next
    // account to sign in in this tab should find itself silently still scoped to.
    if (!o.keepView) setActingOrg(null);
    // A fresh sign-in swapping the account in this tab must not leave the previous
    // account's orchestrator thread active: sessionId is a LangGraph thread_id the server
    // keeps conversation memory under (logic/sessions.js), so continuing it — asking one
    // more question without first hitting "+ New query" — carries the earlier account's
    // findings (vendor names, figures already discussed) into the new account's answers
    // even though every tool call this turn correctly scopes to the new account's own
    // organization_id. Same reset newQuery() already does for the manual case, applied
    // here for the identity-changed case: an in-flight stream is aborted first, same as
    // that button does, rather than left running against a state that no longer exists.
    if (!o.keepView) {
      if (this._ccAbort) this._ccAbort.abort();
      clearInterval(this._orchTick);
    }
    this.setState((p) => ({
      // The reply's user carries identity and role, never the building scope or can_ingest
      // (public_user() in tokens.py — only GET /me adds those, via authLoadScope below). On
      // the silent refresh, which keeps the same account, the scope already in state stays
      // under it rather than being dropped: replacing the whole account emptied the Ingest
      // tray's building picker on every token rotation until the follow-up /me answered,
      // and for good when that read failed — "No building matches that" over an empty
      // search box, gone on a page reload (Azure, 30 Sep 2026). What the reply does carry
      // still wins. A fresh sign-in can be a different person, so it never inherits.
      account: o.keepView && p.account && p.account.id === resp.user.id
        ? Object.assign({}, p.account, resp.user) : resp.user,
      accessToken: resp.tokens.access_token, refreshToken: resp.tokens.refresh_token, refreshTokenAt: issuedAt,
      signedIn: true,
      role: o.keepView ? p.role : (admin ? 'admin' : 'user'),
      saOn: o.keepView ? p.saOn : isSuperadmin,
      view: o.keepView ? p.view : 'home', navOpen: o.keepView ? p.navOpen : true,
      viewOrgId: o.keepView ? p.viewOrgId : null, viewOrgName: o.keepView ? p.viewOrgName : null,
      sessionId: o.keepView ? p.sessionId : null,
      ccChat: o.keepView ? p.ccChat : [],
      ccBusy: o.keepView ? p.ccBusy : false,
      ccStream: o.keepView ? p.ccStream : null,
      orchOpen: o.keepView ? p.orchOpen : false,
      orchTask: o.keepView ? p.orchTask : null,
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null, authRetryAt: 0,
      password: '', code: '', newPassword: '', fullName: '', phone: ''
    }));
    // A fresh sign-in (not the reload's silent token refresh) can swap the account
    // within the same tab — sign out of one, straight into another. Every register
    // core.js's mount loaded is scoped to whoever was signed in then, so it is cleared
    // and re-fetched now rather than left showing the previous account's buildings,
    // compliance rows and the rest until whatever retry/refresh timer they happened
    // to have armed eventually comes back around.
    if (!o.keepView) {
      if (typeof this.resetLiveData === 'function') this.resetLiveData();
      if (typeof this.loadLiveData === 'function') this.loadLiveData();
    }
    // The eager admin reads (core.js mount) deliberately arm no retry timer on a 403, so
    // an admin landing in a session that first loaded below admin is re-kicked here —
    // fresh rows and navigator badges on sign-in and on every refresh alike.
    if (admin) {
      if (typeof this.usLiveLoad === 'function') this.usLiveLoad();
      if (typeof this.auLiveLoad === 'function') this.auLiveLoad();
    }
    // The held-document sync, for the same reason and one worse: core.js's mount fires it
    // before authBoot() has rebuilt the access token — it is never persisted — so the
    // request goes out with no bearer, comes back 401 `missing_token`, and is not retried
    // (only `expired` is). ccCaseSync swallows failures by design, so the whole thing was
    // invisible except as a pair of 401s in the service log, and a document held in another
    // session never surfaced after a reload. Re-kicked here, where a token exists.
    if (typeof this.ccCaseSync === 'function') this.ccCaseSync();
    // this.state.saOn already covers both cases: true on a fresh superadmin sign-in (line
    // 136 above) and true on a reload that restored an open console (session.js) — either
    // way the overlay is about to be on screen and needs its companies read, same as the
    // admin reads above refresh on every entry, not just the first one.
    if (this.state.saOn && typeof this.saLiveLoad === 'function') this.saLiveLoad();
    if (resp.message && !o.keepView) this.flash(resp.message);
    // Building scope (docs/api/building-scope-api.md) is not in resp.user at all — login
    // and refresh never carry it, only GET /me does — so both a fresh sign-in and a silent
    // reload refresh fetch it separately here. Fire-and-forget: a failure leaves whatever
    // scope the stored/previous account already had rather than blocking entry over it.
    this.authLoadScope();
  },

  // Refreshes can_ingest/building_ids/all_buildings/selected_building_id/buildings from
  // GET /me onto the account already in state (which resp.user alone never carries — see
  // authEnter: public_user() names the fields that may leave the service and neither
  // can_ingest nor the building scope is among them; /me adds both afterwards).
  // Best-effort and silent: called on every sign-in/refresh and after every building
  // switch, so a transient failure here is invisible rather than a flashed error on top of
  // whatever else just succeeded.
  authLoadScope() {
    return authApi.me().then((r) => {
      if (!r || !r.ok || !r.user) return;
      const u = r.user;
      // GET /me only ever populates `buildings` for a RESTRICTED caller — the backend
      // builds that list from principal.building_ids, and an admin/superadmin's
      // building_ids is null (all_buildings: true), so /me's own list is always [] for
      // them even though their company has buildings to pick from. GET /api/admin/buildings
      // is the admin-only route that actually lists those, so an admin/superadmin's
      // switcher is built from that instead — the same list, and the same orgQuery()
      // acting-as company, the invite form's chips already use.
      const unrestrictedNeedsCompanyList = (u.role === 'admin' || u.role === 'superadmin')
        && !(Array.isArray(u.buildings) && u.buildings.length);
      // A refused company list resolves to null, not []: this re-runs on every token refresh
      // and every company switch, and turning one failed read into an empty list replaced a
      // picker that was fine a moment ago with "No building matches that" until the next
      // page reload. null below means "keep what is already there".
      const buildings = unrestrictedNeedsCompanyList
        ? adminApi.listBuildings().then((br) => (br && Array.isArray(br.buildings) ? br.buildings : [])).catch(() => null)
        : Promise.resolve(Array.isArray(u.buildings) ? u.buildings : []);
      return buildings.then((list) => {
        // Guarded by id, not just "an account is signed in": this fires from authEnter and
        // from authSelectBuilding as a fire-and-forget request, so a slow reply can still be
        // in flight after a sign-out and a DIFFERENT sign-in. Applying it unguarded would
        // merge one account's building scope onto whichever account happens to be in state
        // by the time it lands — silently showing someone else's allocation.
        this.setState((p) => (p.account && u.id && p.account.id === u.id ? {
          account: Object.assign({}, p.account, {
            // Whether this person may add data at all — the "Can ingest" toggle on Users &
            // access. This read is the ONLY place the browser learns it for the person
            // signed in, and every ingest control on the page is hidden behind it. Left
            // alone when the answer does not carry the key, so a response from an older
            // build cannot silently revoke it.
            can_ingest: u.can_ingest === undefined ? p.account.can_ingest : !!u.can_ingest,
            building_ids: u.building_ids === undefined ? p.account.building_ids : u.building_ids,
            all_buildings: u.all_buildings === true,
            selected_building_id: u.selected_building_id || null,
            buildings: list === null ? (Array.isArray(p.account.buildings) ? p.account.buildings : []) : list
          })
        } : {}));
      });
    }).catch(() => {});
  },

  // The building switcher in the account menu. Selecting narrows every screen to that one
  // building from here on (resetLiveData + loadLiveData, same re-fetch a fresh sign-in
  // does — every register on the page now means something different); buildingId: null
  // clears it back to every building the account holds. Optimistic, because the PATCH only
  // ever narrows or is refused — there is no state this can move the header to that the
  // server would not also allow — but still reverted with a reason on a 403 or network
  // failure, so the chip never shows a selection that did not actually take.
  authSelectBuilding(buildingId) {
    const prevAccount = this.state.account;
    if (!prevAccount) return Promise.resolve();
    // Captured once, up front — this whole call is a request in flight, and everywhere it
    // touches state below checks against THIS id before applying anything. Someone signing
    // out and into a different account while the PATCH is still on the wire must never have
    // this call's optimistic set, its revert, or its "couldn't switch" flash land on them.
    const accountId = prevAccount.id;
    const stillSameAccount = () => this.state.account && this.state.account.id === accountId;
    const id = buildingId || null;
    this.setState((p) => (p.account && p.account.id === accountId
      ? { account: Object.assign({}, p.account, { selected_building_id: id }) } : {}));
    return authApi.selectBuilding(id).then(() => this.authLoadScope()).then(() => {
      if (!stillSameAccount()) return;
      if (typeof this.resetLiveData === 'function') this.resetLiveData();
      if (typeof this.loadLiveData === 'function') this.loadLiveData();
    }).catch((e) => {
      if (!stillSameAccount()) return;
      this.setState({ account: prevAccount });
      const msg = e && e.reason === 'building_not_allocated' ? "You aren't allocated to that building."
        : e && e.reason === 'building_not_in_company' ? 'That building is outside your company.'
        : (e && e.message) || String(e);
      this.flash("Couldn't switch building — " + msg);
    });
  },

  // Local sign-out. `notice` is what the gate shows — the server's own line when it ended
  // the session, nothing when the person chose to leave.
  authSignedOut(notice) {
    // The account's own token record goes with the session, so no refresh token outlives it
    // in storage — unless another tab of the account has since signed into a session of its
    // own, whose token this tab never held.
    const leaving = this.state.account && this.state.account.id;
    if (leaving) {
      const ending = [this.state.refreshToken, newestRefreshToken(this.state)];
      forgetIssuedToken(leaving, ending);
      // And the shared slot when it holds that session, so this account's other tabs hear of
      // it — matched by token, since this tab's own copy may be a rotation behind the slot.
      forgetSharedSession(leaving, ending);
    }
    this._vdToken = (this._vdToken || 0) + 1;
    // A viewAsCompany() override belongs to the session that chose it — never left
    // armed for whoever signs into this tab next.
    setActingOrg(null);
    if (this._ccAbort) this._ccAbort.abort();
    clearInterval(this._orchTick);
    // The Decision queue's refresh timer reads as the account that armed it; firing on the
    // gate would only collect 401s. queueBoot re-arms it on the next sign-in.
    clearTimeout(this._qTimer);
    this._qTimer = null;
    this._qSeen = null;
    this.setState({
      account: null, accessToken: null, refreshToken: null, refreshTokenAt: null, signedIn: false, role: 'user',
      view: 'home', navOpen: false, queueOpen: false, detail: null, acctOpen: false,
      // The vendor drawer is a fixed overlay too; left open it showed a company's vendor,
      // contacts and change controls over the sign-in gate.
      vdOpen: false, vd: null, vdConfirm: false, vdSaving: false,
      // The Super Admin console is a fixed, full-screen overlay keyed on saOn alone — App.jsx
      // renders it with no signedIn check at all. Without resetting it here, signing out from
      // inside the console (its own Sign out button, a 401, another tab signing out) cleared
      // the session underneath but left the exact same overlay covering the screen, which
      // read as sign-out doing nothing.
      saOn: false,
      viewOrgId: null, viewOrgName: null,
      // Same reason as the pinned report cards below, and as authEnter()'s own reset on
      // the way back in: sessionId is the orchestrator's server-side thread, and leaving
      // it set here is the one gap authEnter()'s reset (armed on the NEXT sign-in) doesn't
      // itself close — a stale thread sitting between sign-out and whoever signs in next.
      sessionId: null, ccChat: [], ccBusy: false, ccStream: null, orchOpen: false, orchTask: null,
      // Pinned report cards are personal. Held past sign-out they are the previous
      // person's list waiting in the navigator for whoever signs in next — the register
      // state resetLiveData() clears on the switch, cleared here for the same reason on
      // the way out, so the cards never outlive the session that read them.
      reports: [], reportsOwner: null, reportsLoading: false, reportsError: '', reportsLoadedAt: null,
      reportKey: null, reportRunIdx: 0, reportSelected: [], rpArmed: null,
      pwOpen: false, pwCurrent: '', pwNext: '', pwBusy: false, pwError: '',
      authMode: 'signin', authBusy: false, authError: '', authReason: '', authAttemptsLeft: null,
      authNotice: notice || '', password: '', code: '', newPassword: ''
    });
    this.authLoadConfig();
  },

  authSignOut() {
    // The newest token, not this tab's copy: logout ends only the session whose current or
    // previous token it is shown, so a copy two rotations behind would leave it running.
    const token = newestRefreshToken(this.state);
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

  // ── invitations ────────────────────────────────────────────────────────
  // The emailed link is {PUBLIC_APP_URL}/accept-invitation?token=…  (engines/auth/
  // invitations.py). Read the token off the URL once at boot, hold it in state, and put
  // the gate straight into 'invite' mode. `?invite=…` is accepted too so a static host with
  // no path fallback can still deliver the SPA at "/". The token is then scrubbed from the
  // address bar so a reload, a screenshot or the browser history never carries it.
  authInviteFromUrl() {
    let loc = null;
    try { loc = window.location; } catch (e) { return false; }
    if (!loc) return false;
    let params;
    try { params = new URLSearchParams(loc.search || ''); } catch (e) { return false; }
    const onPath = /\/accept-invitation\/?$/.test(String(loc.pathname || ''));
    const token = (onPath ? params.get('token') : null) || params.get('invite') || '';
    if (!token) return false;
    // A stored session in this tab belongs to whoever used it last, not to the invitee.
    if (this.state.signedIn) this.authSignedOut('');
    this.setState({ authMode: 'invite', inviteToken: token, password: '', fullName: '', authError: '', authReason: '', authNotice: '' });
    try {
      if (window.history && typeof window.history.replaceState === 'function') {
        window.history.replaceState(null, '', onPath ? '/' : (loc.pathname || '/'));
      }
    } catch (e) { /* history unavailable — the token stays in the bar, nothing else changes */ }
    return true;
  },

  async authAcceptInvite() {
    const s = this.state;
    if (s.authBusy) return;
    if (!s.inviteToken) {
      return this.setState({ authError: 'This invitation link is incomplete. Open the link from the email again.', authReason: 'invalid' });
    }
    const cfg = s.authConfig || AUTH_FALLBACK_CONFIG;
    const min = (cfg.password && cfg.password.min_length) || AUTH_FALLBACK_CONFIG.password.min_length;
    if (!s.password || s.password.length < min) {
      return this.setState({ authError: 'Use at least ' + min + ' characters.', authReason: 'password' });
    }
    this.setState({ authBusy: true, authError: '', authReason: '' });
    try {
      const resp = await authApi.acceptInvitation(s.inviteToken, s.password, (s.fullName || '').trim() || null);
      // The invitation token is spent either way; drop it. The account is not signed in
      // yet — accept() (engines/auth/invitations.py) leaves it pending a confirmation
      // code, the same one register() sends, so this goes straight to the verify screen
      // rather than back to sign-in: entering the code IS what signs the invitee in, no
      // retyping the password they just chose.
      this.setState({
        authBusy: false, authMode: 'verify', inviteToken: '', password: '', fullName: '', code: '',
        email: (resp && resp.email) || s.email,
        authNotice: 'Check your email for a code to confirm this address and finish signing in.',
        authError: '', authReason: ''
      });
    } catch (e) {
      const patch = Object.assign({ authBusy: false }, failurePatch(e));
      // used / expired / invalid: the link cannot be retried — send them to sign in (or to
      // ask for a fresh invitation) rather than leaving a dead form up.
      if (e && (e.status === 404 || e.status === 409 || e.status === 410)) {
        Object.assign(patch, { authMode: 'signin', inviteToken: '', authNotice: e.message || patch.authError, authError: '' });
      }
      if (e && e.reason === 'weak_password') patch.password = '';
      this.setState(patch);
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
    const primary = { signin: 'authSignIn', register: 'authRegister', verify: 'authVerify', forgot: 'authForgot', reset: 'authReset', invite: 'authAcceptInvite' }[s.authMode] || 'authSignIn';
    const field = (k) => (e) => this.setState({ [k]: e.target.value });
    const a = s.account || {};
    const name = a.full_name || a.email || '';
    // The building scope picker (docs/api/building-scope-api.md). Its OWN control in the
    // TopBar, deliberately not rows in the account menu: an admin's list is every building
    // in the company, which here is hundreds, and inlining it buried Sign out and the view
    // toggle under a scrolling wall of names. It also sits better beside the company name,
    // which is the other thing saying what this screen is scoped to.
    //
    // Nothing to switch between with zero or one building, so no control at all then.
    // Withheld inside the Super Admin console (saOn): that overlay spans every company, so
    // a company-scoped selection has no meaning there, the same reason acctOrgName is
    // suppressed below. Withheld while viewing as another company (viewOrgId,
    // superAdmin.js): PATCH /api/auth/me/selected-building validates a choice against the
    // caller's OWN company with no acting-as override, so every choice there would 403.
    const allBuildings = Array.isArray(a.buildings) ? a.buildings : [];
    const bldShow = !s.saOn && !s.viewOrgId && allBuildings.length > 1;
    // Names are not unique in this data — three buildings called "MixedUse 004" is normal —
    // so the code is searched alongside the name and shown beside it, or the list offers
    // rows that cannot be told apart.
    const bldQuery = String(s.bldQuery || '').trim().toLowerCase();
    const bldMatches = bldQuery
      ? allBuildings.filter((b) =>
          String(b.name || '').toLowerCase().includes(bldQuery)
          || String(b.building_code || '').toLowerCase().includes(bldQuery))
      : allBuildings;
    const bldSelected = a.selected_building_id
      ? allBuildings.find((b) => b.id === a.selected_building_id) || null
      : null;
    const bldPick = (id) => () => {
      this.setState({ bldOpen: false, bldQuery: '' });
      this.authSelectBuilding(id);
    };
    const canIngest = accountCanIngest(s);
    return {
      canIngest,
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
      authAcceptInvite: () => this.authAcceptInvite(),
      // The invite form's button is only live once there is a token to spend and a
      // password long enough to be accepted — the same rule the server applies.
      authInviteReady: !!s.inviteToken && !!s.password && s.password.length >= ((cfg.password && cfg.password.min_length) || AUTH_FALLBACK_CONFIG.password.min_length),
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
      // The real company the signed-in account belongs to (from login/refresh — the
      // server names it, the client never guesses it). Null while the account hasn't
      // loaded yet, or the rare row with no organization_id at all; the header omits the
      // suffix rather than showing a placeholder company that isn't this account's.
      // Suppressed while the Super Admin console is open — it spans every company, so
      // naming one here would read as though the console were scoped to it. While a
      // superadmin is viewing as a different company (superAdmin.js's viewAsCompany),
      // that company's name takes over from the account's own.
      acctOrgName: s.saOn ? null : (s.viewOrgId ? s.viewOrgName : (a.organization_name || null)),
      /* The building scope picker, its own TopBar control beside the company name. Always
         says which building the screen is answering for, because that is not something a
         person should have to open a menu to find out. */
      bldShow,
      bldLabel: bldSelected ? bldSelected.name : 'All buildings',
      // A narrowed scope is the state worth noticing, so it takes the accent; "All
      // buildings" is the resting state and stays quiet.
      bldScoped: !!bldSelected,
      bldOpen: !!s.bldOpen,
      bldToggle: () => this.setState((p) => ({ bldOpen: !p.bldOpen, bldQuery: '', acctOpen: false })),
      bldClose: () => this.setState({ bldOpen: false, bldQuery: '' }),
      bldQuery: s.bldQuery || '',
      bldSetQuery: field('bldQuery'),
      bldPlaceholder: 'Search ' + allBuildings.length + ' buildings',
      // Pinned above the searched list rather than inside it: it is the way back to the
      // whole portfolio, not one more result to filter away by typing.
      bldAllRow: {
        label: 'All buildings', tick: !bldSelected,
        tickShow: !bldSelected ? 'block' : 'none',
        fg: !bldSelected ? 'var(--color-accent)' : 'var(--color-text)',
        click: bldPick(null)
      },
      bldRows: bldMatches.map((b) => ({
        id: b.id,
        label: b.name || 'Unnamed building',
        code: b.building_code || '',
        tick: bldSelected ? bldSelected.id === b.id : false,
        tickShow: bldSelected && bldSelected.id === b.id ? 'block' : 'none',
        fg: bldSelected && bldSelected.id === b.id ? 'var(--color-accent)' : 'var(--color-text)',
        click: bldPick(b.id)
      })),
      bldNoMatch: bldShow && bldMatches.length === 0,
      canAdmin: admin,
      // A superadmin's own Admin/User toggle is a lens on their own company (see below),
      // but their badge names what they actually are — never demoted to plain "Admin".
      // Viewing as another company overrides even that, since it is the more specific,
      // more consequential state to be in.
      acctRole: s.viewOrgId ? 'Viewing as' : a.role === 'superadmin' ? 'Super Admin' : (s.role === 'admin' ? 'Admin view' : 'User view'),
      // The warn colour while viewing as another company — the one visual cue that is
      // impossible to miss no matter which page is open, since acctOrgName only shows
      // once the account menu is opened.
      acctBg: s.viewOrgId ? 'var(--st-warn)' : s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-neutral-900)',
      acctFg: s.viewOrgId ? 'var(--accent-ink)' : s.role === 'admin' ? 'var(--accent-ink)' : 'var(--color-neutral-300)',
      acctEdge: s.viewOrgId ? 'var(--st-warn)' : s.role === 'admin' ? 'var(--color-accent)' : 'var(--color-divider)',
      acctItems: [
        // Shown only while superAdmin.js's viewAsCompany() is active — first in the list,
        // since it is the one action that matters most to see while it applies.
        s.viewOrgId ? {
          label: 'Exit — return to my account', icon: 'ph-arrow-u-down-left', tick: false,
          click: () => { this.setState({ acctOpen: false }); if (typeof this.exitViewAsCompany === 'function') this.exitViewAsCompany(); }
        } : null,
        { label: 'Pricing', icon: 'ph-tag', click: () => this.setState({ acctOpen: false }, () => this.flash('Pricing and plan usage open in the billing workspace — seats, buildings hoisted and ingest volume.')) },
        { label: 'Support', icon: 'ph-lifebuoy', click: () => this.setState({ acctOpen: false }, () => this.flash('Support: a Hoister is on call for this portfolio. Every request carries the page and the graph state you were on.')) },
        /* The platform operator's console, not a company surface: superadmin only —
           canAdmin is deliberately not enough. Opening it fires the on-open companies
           load (superAdminLive.js); nothing superadmin reads at mount. Always leaves any
           viewAsCompany() behind first — the console spans every company, so every report
           underneath it must go back to reading the caller's own, not still be scoped to
           whichever one was last viewed. */
        a.role === 'superadmin' ? {
          label: 'Super Admin console', icon: 'ph-lock-key', tick: false,
          click: () => {
            if (this.state.viewOrgId && typeof this.exitViewAsCompany === 'function') this.exitViewAsCompany();
            this.setState({ saOn: true, acctOpen: false }, () => { if (typeof this.saLiveLoad === 'function') this.saLiveLoad(); });
          }
        } : null,
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
