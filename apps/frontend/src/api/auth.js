// api/auth — svc-operations-intelligence's /api/auth: accounts, codes, sessions, passwords.
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/auth.py. Mounted behind
// the gateway at /backend/ops-intelligence/. Every failure is {detail: {ok, error, reason}}.
//
// The open endpoints pass `auth: false` — no bearer, and a 401 from /login or /refresh must
// never start a refresh of its own. Only logout-everywhere, password/change, me and
// selectBuilding need the caller's token.
//
// me()/selectBuilding() carry the building-scope fields (docs/api/building-scope-api.md):
// building_ids, all_buildings, selected_building_id, buildings[]. login and refresh do NOT
// return these — the server only attaches them inside GET /me — so a caller who needs them
// (the building switcher, the shell on boot) must call me() itself; auth.js's authLoadScope
// does this after every fresh sign-in and every silent token refresh.
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
  // Public: the token from an invitation link is the credential — single use, hashed at
  // rest. Sets the password and activates the account; nothing is minted, so the person
  // then signs in normally. → {ok, user_id, email, role, organization_id}.
  acceptInvitation: (token, password, fullName) =>
    open('/api/auth/invitations/accept', { token, password, full_name: fullName || null }),
  changePassword: (current, next) => apiFetch(B, '/api/auth/password/change', { method: 'POST', body: { current_password: current, new_password: next } }),
  // → {ok, user: {..., building_ids, all_buildings, selected_building_id, buildings[]}}.
  // building_ids is null (unrestricted — admin/superadmin) or a tuple, already narrowed to
  // one element when selected_building_id is set; [] is a real answer (allocated to nothing).
  me: () => apiFetch(B, '/api/auth/me'),
  // Selects the building this account works in from here on, or clears it back to every
  // building they hold (buildingId: null). Narrows only: a plain user naming a building they
  // are not allocated to, or an admin naming one outside their company, is 403 (reason
  // building_not_allocated / building_not_in_company) and nothing changes server-side.
  selectBuilding: (buildingId) =>
    apiFetch(B, '/api/auth/me/selected-building', { method: 'PATCH', body: { building_id: buildingId || null } })
};
