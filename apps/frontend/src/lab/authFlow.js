// authFlow — signing in to Trace Lab with a Hoistra account. The same login service and endpoints Hoistra uses
// (src/api/auth.js), a session of the lab's own (session.js). The access token lives in memory and is handed to
// the shared request client through configureAuth, so every Trace Lab request carries it and refreshes once on an
// expired token, exactly as in Hoistra. A refresh runs under the lab's own Web Lock and always presents the newest
// stored refresh token, so two lab tabs never present the same one twice.
import { configureAuth } from '../api/client.js';
import { clearSession, loadSession, lockName, saveSession } from './session.js';

const BAD_LOGIN = 'That email or password is not right.';

function message(e) {
  if (!e) return 'Could not sign in. Try again.';
  if (e.status === 401) return BAD_LOGIN;
  if (e.status === 403 && e.reason === 'disabled') return 'This account is disabled. Ask your administrator.';
  if (e.status === 429) return 'Too many tries. Wait a minute, then try again.';
  return 'Could not sign in: ' + (e.message || 'the service did not answer') + '.';
}

function withLock(locks, name, fn) {
  if (!locks || typeof locks.request !== 'function') return Promise.resolve().then(fn);
  return Promise.resolve(locks.request(name, {}, fn));
}

export function createLabAuth({ api, storage, locks, onChange }) {
  let accessToken = null;
  let account = null;
  let refreshing = null;
  const tell = () => { if (typeof onChange === 'function') onChange(account); };

  function enter(resp) {
    accessToken = resp.tokens.access_token;
    account = resp.user || account;
    saveSession(storage, { refreshToken: resp.tokens.refresh_token, user: account });
    tell();
  }

  function leave() {
    accessToken = null;
    account = null;
    clearSession(storage);
    tell();
  }

  async function refresh() {
    if (refreshing) return refreshing;
    const held = loadSession(storage);
    if (!held) { leave(); throw Object.assign(new Error('signed out'), { status: 401, reason: 'no_session' }); }
    refreshing = withLock(locks, lockName(held.user && held.user.id), async () => {
      const newest = loadSession(storage);                 // another lab tab may have rotated it meanwhile
      if (!newest) throw Object.assign(new Error('signed out'), { status: 401, reason: 'no_session' });
      const resp = await api.refresh(newest.refreshToken);
      enter(resp);
      return accessToken;
    }).catch((e) => {
      if (e && e.status === 401) leave();
      throw e;
    }).finally(() => { refreshing = null; });
    return refreshing;
  }

  configureAuth({
    getToken: () => accessToken,
    refresh,
    onTerminal: () => leave()
  });

  return {
    token: () => accessToken,
    user: () => account,
    refresh,
    async signIn(email, password) {
      try {
        enter(await api.login(String(email || '').trim(), String(password || '')));
        return { ok: true };
      } catch (e) {
        if (e && e.status === 403 && e.reason === 'email_not_verified') return { needsCode: true };
        return { error: message(e) };
      }
    },
    async verify(email, code) {
      try {
        enter(await api.verifyEmail(String(email || '').trim(), String(code || '').replace(/\s+/g, '')));
        return { ok: true };
      } catch (e) {
        return { error: e && e.status === 400 ? 'That code is not right or has expired.' : message(e) };
      }
    },
    // Opening the lab: a stored session is refreshed once; nothing stored, or a refused refresh, means sign in.
    async boot() {
      if (!loadSession(storage)) return false;
      try {
        await refresh();
        const me = await api.me().catch(() => null);
        if (me && me.user) { account = Object.assign({}, account, me.user); saveSession(storage, { refreshToken: loadSession(storage).refreshToken, user: account }); tell(); }
        return !!accessToken;
      } catch (e) {
        return false;
      }
    },
    async signOut() {
      const held = loadSession(storage);
      try { if (held) await api.logout(held.refreshToken); } catch (e) { /* signed out here whatever the service says */ }
      leave();
    }
  };
}
