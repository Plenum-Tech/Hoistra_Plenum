// session — the slice of state that survives a page reload.
//
// The controller holds everything in memory, so a refresh used to drop you back on the
// sign-in gate having lost the page you were on. This persists just enough to land you
// back where you were: the session credential, who is signed in, the current view, and
// the compliance scope.
//
// The refresh token is stored; the access token is not. The refresh token is what "stay
// signed in" means (14 days, revocable, rotated on every use); the access token lives 30
// minutes and cannot be revoked on its own, so it stays in memory and a reload costs one
// refresh. The account is a summary so the shell can render the avatar and menu before
// that refresh answers; the server's copy replaces it as soon as it does.
//
// Deliberately NOT persisted here: staged files (File objects do not serialise), the live
// register and any in-flight flags (better re-fetched than restored stale), and every
// flow/modal state (a half-open dialog restored from last week is worse than a clean page).
// The transcript itself lives with its session record (logic/sessions.js); only the id of
// the active session is kept here so the record can be found again.
//
// TWO stores, not one. localStorage is shared by every tab of this origin, which is right
// for "open a brand new tab and it is already signed in" — but with company-switchable
// accounts, two tabs are routinely on two DIFFERENT accounts at once (this one TechCorp,
// that one Plenum Tech), and localStorage has room for exactly one. Whichever tab wrote to
// it most recently silently became what every OTHER tab's next reload restored — a Plenum
// tab reloading and landing on TechCorp, having done nothing but sit there while the other
// tab was used. sessionStorage is private to this one tab and survives its reloads, so it
// is checked first and is authoritative once it holds anything; localStorage is now only
// the fallback a genuinely fresh tab (empty sessionStorage) inherits.
import { getActingOrg, setActingOrg } from '../api/client.js';

export const SESSION_KEY = 'hoistra.session.v1';
const KEY = SESSION_KEY;

// 'chat' restores to the conversation page with an empty transcript (the transcript itself
// is not persisted), which is a better landing than the sign-in gate.
//
// 'users', 'audit' and 'insp' were missing here — reloading on any of the three silently
// failed this allow-list and fell back to view: 'home' below, same as landing on an
// unrecognised value would. They take no companion id (same as 'home'/'cc'/'vp'), so
// nothing else needs restoring alongside them.
const VIEWS = ['home', 'answer', 'module', 'cc', 'vp', 'buildings', 'integ', 'report', 'chat', 'sessions', 'space', 'users', 'crons', 'audit', 'insp'];

const isStrArray = (v) => Array.isArray(v) && v.every((x) => typeof x === 'string');
const isStr = (v) => typeof v === 'string';
const ROLES = ['user', 'admin', 'superadmin'];
// A row off GET /api/auth/me's buildings[] — building_code is display-only and optional.
const isBuildingRow = (b) => b && typeof b === 'object' && isStr(b.id) && isStr(b.name);
const isBuildingList = (v) => Array.isArray(v) && v.every(isBuildingRow);

// The account as the shell needs it. Anything else the server sent (role_label,
// last_login_at) is display-only and comes back fresh with the next refresh.
function cleanAccount(a) {
  if (!a || typeof a !== 'object') return null;
  if (!isStr(a.id) || !isStr(a.email) || !isStr(a.full_name) || !isStr(a.status)) return null;
  if (!isStr(a.role) || ROLES.indexOf(a.role) < 0) return null;
  return {
    id: a.id, email: a.email, full_name: a.full_name, role: a.role, status: a.status,
    organization_id: isStr(a.organization_id) ? a.organization_id : null,
    // Comes back fresh on the reload refresh too, but kept here so the header shows the
    // real company immediately from the stored copy rather than flashing blank until then.
    organization_name: isStr(a.organization_name) ? a.organization_name : null,
    email_verified: a.email_verified === true,
    // Building scope (docs/api/building-scope-api.md), from GET /api/auth/me only — login
    // and refresh never send it, so this is null/false/[] until authLoadScope's first
    // resolve, same as organization_name was blank before its first load. null here means
    // "not restricted by allocation" (admin/superadmin), NOT "not loaded yet" — a caller
    // that must tell those apart reads selected_building_id/buildings alongside it.
    building_ids: a.building_ids === null ? null : (isStrArray(a.building_ids) ? a.building_ids.slice() : null),
    all_buildings: a.all_buildings === true,
    selected_building_id: isStr(a.selected_building_id) ? a.selected_building_id : null,
    buildings: isBuildingList(a.buildings) ? a.buildings.slice() : []
  };
}

// Parses and validates one storage's slice. Shared by both stores so a stale or
// hand-edited entry in either one is held to the exact same shape the app expects.
function readSlice(storage) {
  let raw = null;
  try {
    raw = storage.getItem(KEY);
  } catch (e) {
    return {}; // private windows and blocked site data both throw on access
  }
  if (!raw) return {};

  let d = null;
  try { d = JSON.parse(raw); } catch (e) { return {}; }
  if (!d || typeof d !== 'object') return {};

  // Nothing is trusted: a stored value only survives if it is the shape the app expects,
  // so a stale or hand-edited entry cannot wedge the UI.
  const out = {};
  // A session is only a session with the credential that can renew it. A slice from the
  // demo gate — signedIn with no token — restores nothing.
  if (isStr(d.refreshToken) && d.refreshToken) out.refreshToken = d.refreshToken;
  // When this browser was issued that token (auth.js authEnter). Tabs compare it to tell a
  // newer token from an older one written back by a tab that had not heard yet; a slot
  // written by a build from before it has none.
  if (out.refreshToken && typeof d.refreshTokenAt === 'number' && isFinite(d.refreshTokenAt) && d.refreshTokenAt > 0) out.refreshTokenAt = d.refreshTokenAt;
  const account = cleanAccount(d.account);
  if (account) out.account = account;
  if (d.signedIn === true && out.refreshToken) out.signedIn = true;
  if (typeof d.email === 'string') out.email = d.email;
  if (typeof d.role === 'string' && (d.role === 'user' || d.role === 'admin')) out.role = d.role;
  // The navigator's open/closed state is a preference; a reload keeps it.
  if (typeof d.navOpen === 'boolean') out.navOpen = d.navOpen;

  // The Super Admin console is its own overlay, not a `view` — a reload inside it was
  // landing back on the normal app's Home page regardless. Restored only for an account
  // that is actually a superadmin; HoistraLogic's own constructor re-checks this again
  // once the stored account is merged in, the same way it re-checks a stored admin role.
  if (d.saOn === true && account && account.role === 'superadmin') out.saOn = true;

  // A superadmin's "View as this company" override (superAdmin.js's viewAsCompany()) was
  // never persisted at all — every reload silently dropped it and fell back to reading the
  // account's OWN company, with nothing on screen saying so. Reported exactly as: viewing
  // Plenum Tech, reload, and every report is suddenly back to TechCorp — the account's own
  // company, not a different account and not a bug in which account is signed in. Restored
  // only for a superadmin, the only role the feature is ever offered to.
  if (isStr(d.viewOrgId) && account && account.role === 'superadmin') {
    out.viewOrgId = d.viewOrgId;
    if (isStr(d.viewOrgName)) out.viewOrgName = d.viewOrgName;
  }
  // The OTHER half of that restore — client.js's acting company — is applied by loadSession()
  // alone (restoreActingOrg below), never here: this function also serves as a plain peek at
  // the shared slot, for the storage listener, the refresh-token pick and every save, and
  // there it re-scoped THIS tab's reads to whichever company some other tab of the same
  // superadmin was viewing as — while this tab's header still named its own (1 Oct 2026).

  // A session written by a build that still had the Migration page. The page is gone and
  // the run is answered in the Orchestrator, so the stored view is TRANSLATED rather than
  // dropped: dropping it lands a reader who was mid-review on Home, with the run restored,
  // no card rendered and no nav entry left to find it by.
  if (d && d.view === 'migration') d = Object.assign({}, d, { view: 'chat' });

  if (typeof d.view === 'string' && VIEWS.indexOf(d.view) > -1) {
    // A view that needs a companion value is only restored with it.
    const ok = d.view === 'module' ? typeof d.module === 'string' && !!d.module
      : d.view === 'answer' ? typeof d.answerKey === 'string' && !!d.answerKey
      : d.view === 'space' ? typeof d.spaceKey === 'string' && !!d.spaceKey
      : d.view === 'report' ? typeof d.reportKey === 'string' && !!d.reportKey
      : true;
    if (ok) {
      out.view = d.view;
      if (d.view === 'module') out.module = d.module;
      if (d.view === 'space') out.spaceKey = d.spaceKey;
      if (d.view === 'report') out.reportKey = d.reportKey;
      if (d.view === 'answer') {
        out.answerKey = d.answerKey;
        if (typeof d.askedQuery === 'string') out.askedQuery = d.askedQuery;
      }
    }
  }

  // The conversation in progress: its transcript is restored from the sessions store
  // (logic/sessions.js) and the next question continues the same orchestrator thread.
  if (typeof d.sessionId === 'string' && d.sessionId) out.sessionId = d.sessionId;

  // The migration open in the Orchestrator. A gate waits for a person, and the person
  // who reloads mid-review must land back on the same run, not on the upload panel.
  // Restored on its own — it is useful from any page's nav badge, not only from `migration`.
  if (isStr(d.mgId) && /^[0-9a-f-]{8,64}$/i.test(d.mgId)) out.mgId = d.mgId;

  // Compliance scope, so a reload of the console returns to the same slice of the
  // register rather than the whole portfolio.
  if (isStrArray(d.ccCountries)) out.ccCountries = d.ccCountries;
  if (isStrArray(d.ccStates)) out.ccStates = d.ccStates;
  if (isStrArray(d.ccBuildings)) out.ccBuildings = d.ccBuildings;
  if (typeof d.ccPivot === 'string' && ['buildings', 'vendors', 'matrix'].indexOf(d.ccPivot) > -1) out.ccPivot = d.ccPivot;

  // The held document this session is answering. A case only restores WITH its session —
  // it belongs to that conversation, and a case id floating free of one would point the
  // composer at a document the transcript never mentions.
  //
  // This half was missed the first time: buildSlice wrote the three keys and readSlice,
  // which validates every key explicitly and drops anything it does not know, threw them
  // away on the way back. Writing a value is not persisting it.
  if (out.sessionId && typeof d.ccCaseId === 'string' && d.ccCaseId) {
    out.ccCaseId = d.ccCaseId;
    out.ccCaseDoc = typeof d.ccCaseDoc === 'string' ? d.ccCaseDoc : '';
    out.ccCaseQuestion = typeof d.ccCaseQuestion === 'string' ? d.ccCaseQuestion : '';
  }

  // Signing out must not leave a restorable page behind.
  if (!out.signedIn) return {};
  return out;
}

export function loadSession() {
  let win;
  try { win = window; } catch (e) { return {}; }
  // This tab's own last-known identity, if it has one, wins outright — a different
  // account being active in some other tab must never override it.
  const mine = readSlice(win.sessionStorage);
  // A genuinely fresh tab (nothing of its own yet) inherits whichever account is
  // currently active elsewhere, same as it always has — a reasonable default for the
  // common case of one account across many tabs.
  const slice = mine.signedIn ? mine : readSlice(win.localStorage);
  restoreActingOrg(slice);
  return slice;
}

// The OTHER half of a restored view-as, and the half that was once missing. viewOrgId is only
// what the top bar reads; client.js's actingOrgId is what actually puts organization_id on a
// request, and it is the only thing svc-deepagents' _resolve_acting_org ever sees. Restoring
// the label alone produced the Azure report of 17 Sep 2026: the bar read "Plenum Tech LLC"
// while the chat answered out of TechCorp's register — the account's OWN company. Two halves
// of one fact disagreeing is worse than both being wrong: the screen looks right, so nobody
// checks it. Set unconditionally, so a slice with no view-as also CLEARS an override left over
// from before the reload. Only when it actually differs: setActingOrg bumps client.js's
// orgEpoch, which is how a request already in flight learns the company changed under it and
// discards its answer — so calling it on a restore that changes nothing aborts a token
// refresh that was already in the air. (Caught by auth.test.mjs's two refresh tests.)
function restoreActingOrg(slice) {
  const want = (slice && slice.viewOrgId) || null;
  if ((getActingOrg() || null) !== want) setActingOrg(want);
}

// The shared slot only, bypassing this tab's own sessionStorage copy — for
// authStorageChanged (auth.js), which exists specifically to react to what some OTHER
// tab just did to that shared slot, not to re-read this tab's own unrelated identity.
export function loadSharedSession() {
  let win;
  try { win = window; } catch (e) { return {}; }
  return readSlice(win.localStorage);
}

// The newest refresh token the server has issued to each account in this browser, in a key
// of its own, written only at the moment one is issued — sign-in and every rotation
// (auth.js authEnter). The shared slot above cannot be that record: it holds one account at
// a time, and whichever tab of whichever company saved last owns it. A tab that fell behind
// — frozen in the background while another tab of its account rotated, then the slot taken
// by a tab on another company — refreshed with its own stale copy, which the server treats
// as a stolen token and answers by ending every session the account has (30 Sep 2026: nine
// sessions, every open tab's data gone until a reload and a fresh sign-in).
const ISSUED_PREFIX = 'hoistra.rt.v1.';
export function issuedTokenKey(accountId) { return ISSUED_PREFIX + String(accountId || ''); }

export function latestIssuedRecord(accountId) {
  if (!accountId) return null;
  try {
    const raw = window.localStorage.getItem(issuedTokenKey(accountId));
    const d = raw ? JSON.parse(raw) : null;
    if (!d || !isStr(d.token) || !d.token) return null;
    return {
      token: d.token,
      at: typeof d.at === 'number' && isFinite(d.at) ? d.at : 0,
      superseded: Array.isArray(d.superseded) ? d.superseded.filter((t) => isStr(t) && t) : []
    };
  } catch (e) {
    return null;
  }
}

export function latestIssuedToken(accountId) {
  const r = latestIssuedRecord(accountId);
  return r ? r.token : null;
}

// The record also keeps the last few tokens it replaced: a token the account has moved past
// is never followed again, whoever writes it back — a tab reloaded from its own stale copy,
// or one still on the build before issue times (review, 1 Oct 2026).
// `replaced`: the token this one was exchanged for, remembered even when there was no record
// yet — the first refresh after this build arrives replaces a token no record ever held, and a
// tab still booting from it saved it back as if it were a rotation (traced in Chrome, 1 Oct).
export function recordIssuedToken(accountId, token, at, replaced) {
  if (!accountId || !isStr(token) || !token) return;
  const prev = latestIssuedRecord(accountId);
  const superseded = [replaced].concat(prev ? [prev.token].concat(prev.superseded) : [])
    .filter((t, i, all) => isStr(t) && t && t !== token && all.indexOf(t) === i).slice(0, 8);
  try { window.localStorage.setItem(issuedTokenKey(accountId), JSON.stringify({ token, at: at || Date.now(), superseded })); } catch (e) { /* no storage: this tab alone still works */ }
}

export function isSupersededToken(accountId, token) {
  const r = latestIssuedRecord(accountId);
  return !!(r && token && r.token !== token && r.superseded.indexOf(token) >= 0);
}

// Clears the shared slot when it still holds one of `tokens` for this account — the session a
// tab is ending. Compared by token, not by this tab's own copy, which can be a rotation behind
// the slot (logout presents the newest), and never touching another company's slot.
export function forgetSharedSession(accountId, tokens) {
  if (!accountId) return;
  try {
    const shared = readSlice(window.localStorage);
    if (shared.account && shared.account.id === accountId && shared.refreshToken && (tokens || []).indexOf(shared.refreshToken) >= 0) {
      window.localStorage.removeItem(KEY);
    }
  } catch (e) { /* nothing stored, nothing to clear */ }
}

// Only while it still holds one of `tokens` — a token this tab never held belongs to a
// session some other tab of the account signed into since, and is not this tab's to end.
export function forgetIssuedToken(accountId, tokens) {
  if (!accountId) return;
  try {
    const held = latestIssuedToken(accountId);
    if (held && (tokens || []).indexOf(held) >= 0) window.localStorage.removeItem(issuedTokenKey(accountId));
  } catch (e) { /* nothing stored, nothing to forget */ }
}

function buildSlice(state) {
  return {
    signedIn: true,
    email: state.email || '',
    role: state.role || 'user',
    refreshToken: state.refreshToken || null,
    refreshTokenAt: (state.refreshToken && state.refreshTokenAt) || null,
    account: cleanAccount(state.account),
    navOpen: !!state.navOpen,
    saOn: !!state.saOn,
    viewOrgId: state.viewOrgId || null,
    viewOrgName: state.viewOrgName || null,
    view: state.view || 'home',
    module: state.module || null,
    spaceKey: state.spaceKey || null,
    reportKey: state.reportKey || null,
    sessionId: state.sessionId || null,
    mgId: state.mgId || null,
    answerKey: state.answerKey || null,
    askedQuery: state.askedQuery || '',
    ccCountries: state.ccCountries || [],
    ccStates: state.ccStates || [],
    ccBuildings: state.ccBuildings || [],
    ccPivot: state.ccPivot || 'buildings',
    // The held document this session is answering. sessionId is persisted, so the case that
    // belongs to it must be too: a hard refresh — the very thing needed to pick up new code
    // — otherwise drops it, and the document stays held on the server with nothing in the
    // UI pointing at it.
    ccCaseId: state.ccCaseId || null,
    ccCaseDoc: state.ccCaseDoc || '',
    ccCaseQuestion: state.ccCaseQuestion || ''
  };
}

// `prevState` is this tab's own state from just before the change (HoistraLogic's setState
// still has it), used only to decide, on sign-out, whether the SHARED slot is safe to
// clear — see below.
export function saveSession(state, prevState) {
  if (!state) return;
  let slice = state.signedIn ? buildSlice(state) : null;
  // Never anything older than the account's record. The record holds the newest token this
  // browser was issued; a tab whose own copy is behind it — reloaded from a stale, unstamped
  // copy above all — would otherwise write the replaced token out, to its own copy and (with
  // another company holding the shared slot) to the shared one, where an unstamped token reads
  // as a legacy rotation and is followed (review, 1 Oct 2026).
  if (slice && slice.account && slice.account.id && slice.refreshToken) {
    const rec = latestIssuedRecord(slice.account.id);
    if (rec && rec.token !== slice.refreshToken && (!slice.refreshTokenAt || slice.refreshTokenAt < rec.at)) {
      slice = Object.assign({}, slice, { refreshToken: rec.token, refreshTokenAt: rec.at || null });
    }
  }
  // Always this tab's own copy: it is never read by anyone else, so there is no reason to
  // hold back writing or clearing it.
  try {
    if (slice) window.sessionStorage.setItem(KEY, JSON.stringify(slice));
    else window.sessionStorage.removeItem(KEY);
  } catch (e) {
    /* storage unavailable or full — the app must still work, just without restore */
  }
  // The shared slot is a different matter: another tab may have already moved it on to a
  // different account since this tab last touched it. Writing here (signing in, rotating
  // a token) can simply overwrite it — a fresh tab that adopts the wrong account will
  // sort itself out on its own next refresh the same way this one just did. But CLEARING
  // it on sign-out must not destroy a session that has already become someone else's; it
  // is only cleared when it still matches the token this tab is actually signing out.
  try {
    if (slice) {
      // Never this tab's token back over a newer one. A tab's token changes only when it is
      // issued one or adopts one; any OTHER state change that finds the shared slot holding
      // a different token for the SAME account means another tab has rotated since and this
      // tab has not heard yet. Writing its copy back handed every other tab — the one that
      // rotated included — a token the server had already replaced, and thirty minutes
      // later, when an access token expired, the refresh that presented it ended every
      // session on the account (30 Sep 2026).
      //
      // Newer is decided by the issue time each token carries, not by who saved last: the
      // shared slot as THIS tab's process sees it can itself be a few milliseconds behind, so
      // a tab booting next to one that has just rotated saved the old token back from that
      // stale view, and the tab that had rotated then adopted it (traced in Chrome, 1 Oct
      // 2026). A slot written by a build from before issue times carries none; it is kept
      // unless this tab's own token changed, as that build did.
      const shared = readSlice(window.localStorage);
      const sameAccount = !!(shared.account && slice.account && shared.account.id === slice.account.id);
      const changedHere = !prevState || prevState.refreshToken !== state.refreshToken;
      const differs = sameAccount && shared.refreshToken && shared.refreshToken !== slice.refreshToken;
      const sharedIsNewer = differs && (shared.refreshTokenAt
        ? !slice.refreshTokenAt || shared.refreshTokenAt > slice.refreshTokenAt
        : !changedHere);
      const out = sharedIsNewer
        ? Object.assign({}, slice, { refreshToken: shared.refreshToken, refreshTokenAt: shared.refreshTokenAt || null })
        : slice;
      window.localStorage.setItem(KEY, JSON.stringify(out));
    } else {
      const shared = readSlice(window.localStorage);
      const mineToken = prevState && prevState.refreshToken;
      if (!shared.refreshToken || !mineToken || shared.refreshToken === mineToken) {
        window.localStorage.removeItem(KEY);
      }
    }
  } catch (e) {
    /* storage unavailable or full — the app must still work, just without restore */
  }
}

// Exposed for tests: the slice is what survives a reload, and what it omits is lost.
export const buildSliceForTest = buildSlice;
