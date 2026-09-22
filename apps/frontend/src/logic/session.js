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
const VIEWS = ['home', 'answer', 'module', 'cc', 'vp', 'buildings', 'integ', 'report', 'chat', 'sessions', 'space', 'users', 'audit', 'insp'];

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
  // The OTHER half of that restore, and the half that was missing. viewOrgId is only what
  // the top bar reads; client.js's actingOrgId is what actually puts organization_id on a
  // request, and it is the only thing svc-deepagents' _resolve_acting_org ever sees.
  // Restoring the label alone produced the Azure report of 17 Sep 2026: the bar read
  // "Plenum Tech LLC" while the chat answered out of TechCorp's register — the account's
  // OWN company. Two halves of one fact disagreeing is worse than both being wrong: the
  // screen looks right, so nobody checks it. Set unconditionally, so a slice with no
  // view-as also CLEARS an override left over from before the reload.
  // Only when it actually differs. setActingOrg bumps client.js's orgEpoch, which is how a
  // request already in flight learns the company changed under it and discards its answer —
  // so calling it on a restore that changes nothing aborts a token refresh that was already
  // in the air. (Caught by auth.test.mjs's two refresh tests, which is what they are for.)
  if ((getActingOrg() || null) !== (out.viewOrgId || null)) setActingOrg(out.viewOrgId || null);

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
  if (mine.signedIn) return mine;
  // A genuinely fresh tab (nothing of its own yet) inherits whichever account is
  // currently active elsewhere, same as it always has — a reasonable default for the
  // common case of one account across many tabs.
  return readSlice(win.localStorage);
}

// The shared slot only, bypassing this tab's own sessionStorage copy — for
// authStorageChanged (auth.js), which exists specifically to react to what some OTHER
// tab just did to that shared slot, not to re-read this tab's own unrelated identity.
export function loadSharedSession() {
  let win;
  try { win = window; } catch (e) { return {}; }
  return readSlice(win.localStorage);
}

function buildSlice(state) {
  return {
    signedIn: true,
    email: state.email || '',
    role: state.role || 'user',
    refreshToken: state.refreshToken || null,
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
  const slice = state.signedIn ? buildSlice(state) : null;
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
      window.localStorage.setItem(KEY, JSON.stringify(slice));
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
