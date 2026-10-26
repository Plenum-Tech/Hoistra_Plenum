// session — Trace Lab's own stored session, kept apart from Hoistra's. The lab signs in with the same Hoistra
// account but holds its own refresh token: the login service rotates refresh tokens and revokes every session when
// one is presented twice, so the two apps must never share one. Only the refresh token and the account are stored;
// the access token stays in memory (authFlow.js).

export const KEY = 'tracelab.session.v1';

// The refresh lock is per account and per app: two lab tabs refresh one at a time; Hoistra's own lock is separate.
export const lockName = (userId) => 'tracelab.refresh.' + (userId || 'signed-out');

export function loadSession(storage) {
  try {
    const raw = storage && storage.getItem(KEY);
    if (!raw) return null;
    const s = JSON.parse(raw);
    return s && typeof s.refreshToken === 'string' && s.refreshToken ? { refreshToken: s.refreshToken, user: s.user || null } : null;
  } catch (e) {
    return null;
  }
}

export function saveSession(storage, s) {
  try { storage.setItem(KEY, JSON.stringify({ refreshToken: s.refreshToken, user: s.user || null })); } catch (e) { /* storage blocked: memory only */ }
}

export function clearSession(storage) {
  try { storage.removeItem(KEY); } catch (e) { /* nothing to clear */ }
}

// Who the person is to the app: the platform team (a superadmin) picks a company; a company admin works in their own;
// anyone else is a member of their company, let in once a company admin gives them a Trace Lab role (the service's
// /lab/me says whether they are active yet).
export function access(user) {
  if (!user) return 'denied';
  if (user.role === 'superadmin') return 'platform';
  if (user.role === 'admin') return 'company';
  return 'member';
}
