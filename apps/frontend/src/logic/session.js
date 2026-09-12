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
export const SESSION_KEY = 'hoistra.session.v1';
const KEY = SESSION_KEY;

// 'chat' restores to the conversation page with an empty transcript (the transcript itself
// is not persisted), which is a better landing than the sign-in gate.
const VIEWS = ['home', 'answer', 'module', 'cc', 'vp', 'buildings', 'integ', 'report', 'chat', 'sessions', 'space'];

const isStrArray = (v) => Array.isArray(v) && v.every((x) => typeof x === 'string');
const isStr = (v) => typeof v === 'string';
const ROLES = ['user', 'admin', 'superadmin'];

// The account as the shell needs it. Anything else the server sent (role_label,
// last_login_at) is display-only and comes back fresh with the next refresh.
function cleanAccount(a) {
  if (!a || typeof a !== 'object') return null;
  if (!isStr(a.id) || !isStr(a.email) || !isStr(a.full_name) || !isStr(a.status)) return null;
  if (!isStr(a.role) || ROLES.indexOf(a.role) < 0) return null;
  return {
    id: a.id, email: a.email, full_name: a.full_name, role: a.role, status: a.status,
    organization_id: isStr(a.organization_id) ? a.organization_id : null,
    email_verified: a.email_verified === true
  };
}

export function loadSession() {
  let raw = null;
  try {
    raw = window.localStorage.getItem(KEY);
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

  // Compliance scope, so a reload of the console returns to the same slice of the
  // register rather than the whole portfolio.
  if (isStrArray(d.ccCountries)) out.ccCountries = d.ccCountries;
  if (isStrArray(d.ccStates)) out.ccStates = d.ccStates;
  if (isStrArray(d.ccBuildings)) out.ccBuildings = d.ccBuildings;
  if (typeof d.ccPivot === 'string' && ['buildings', 'vendors', 'matrix'].indexOf(d.ccPivot) > -1) out.ccPivot = d.ccPivot;

  // Signing out must not leave a restorable page behind.
  if (!out.signedIn) return {};
  return out;
}

export function saveSession(state) {
  if (!state) return;
  try {
    if (!state.signedIn) {
      window.localStorage.removeItem(KEY);
      return;
    }
    window.localStorage.setItem(KEY, JSON.stringify({
      signedIn: true,
      email: state.email || '',
      role: state.role || 'user',
      refreshToken: state.refreshToken || null,
      account: cleanAccount(state.account),
      navOpen: !!state.navOpen,
      view: state.view || 'home',
      module: state.module || null,
      spaceKey: state.spaceKey || null,
      reportKey: state.reportKey || null,
      sessionId: state.sessionId || null,
      answerKey: state.answerKey || null,
      askedQuery: state.askedQuery || '',
      ccCountries: state.ccCountries || [],
      ccStates: state.ccStates || [],
      ccBuildings: state.ccBuildings || [],
      ccPivot: state.ccPivot || 'buildings'
    }));
  } catch (e) {
    /* storage unavailable or full — the app must still work, just without restore */
  }
}
