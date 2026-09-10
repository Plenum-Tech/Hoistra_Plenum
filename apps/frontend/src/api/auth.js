// api/auth — svc-operations-intelligence's /api/auth: accounts, codes, sessions, passwords.
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/auth.py. Mounted behind
// the gateway at /backend/ops-intelligence/. Every failure is {detail: {ok, error, reason}}.
//
// The open endpoints pass `auth: false` — no bearer, and a 401 from /login or /refresh must
// never start a refresh of its own. Only logout-everywhere and password/change need the
// caller's token. GET /me is deliberately not wrapped: this client never stores the access
// token, so a reload exchanges the refresh token instead and reads the user off that reply.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const open = (path, body) => apiFetch(B, path, body === undefined ? { auth: false } : { auth: false, method: 'POST', body });

export const authApi = {
  config: () => open('/api/auth/config'),
  // 202 whether or not the address is already registered — the owner of the mailbox is told.
  register: (f) => open('/api/auth/register', Object.assign(
    { email: f.email, password: f.password, full_name: f.full_name, phone: f.phone || null },
    ORG_ID ? { organization_id: ORG_ID } : {}
  )),
  verifyEmail: (email, code) => open('/api/auth/verify-email', { email, code }),
  resendCode: (email) => open('/api/auth/resend-code', { email }),
  login: (email, password) => open('/api/auth/login', { email, password }),
  // Rotates both tokens; presenting one already exchanged is 401 replayed and revokes all.
  refresh: (refreshToken) => open('/api/auth/refresh', { refresh_token: refreshToken }),
  logout: (refreshToken) => open('/api/auth/logout', { refresh_token: refreshToken }),
  logoutEverywhere: () => apiFetch(B, '/api/auth/logout', { method: 'POST', body: { everywhere: true } }),
  forgot: (email) => open('/api/auth/password/forgot', { email }),
  reset: (email, code, newPassword) => open('/api/auth/password/reset', { email, code, new_password: newPassword }),
  changePassword: (current, next) => apiFetch(B, '/api/auth/password/change', { method: 'POST', body: { current_password: current, new_password: next } })
};
