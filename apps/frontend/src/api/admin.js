// api/admin — svc-operations-intelligence's /api/admin: the company admin console.
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/admin.py. Mounted
// behind the gateway at /backend/ops-intelligence/. Admin role required (a superadmin
// passes); every failure is {detail: {ok, error, reason}}.
//
// No ORG_ID here, deliberately — the build's default tenant must never silently redirect
// every admin's own console to a fixed company. What every route here DOES take is
// ?organization_id=, honoured only when the caller is a superadmin (a non-superadmin
// naming any other company is 403 wrong_organization); orgQuery() sends it only when a
// superadmin has explicitly chosen to view/act as another company (superAdmin.js's
// viewAsCompany), never as a standing default.
import { BASES, getActingOrg, apiFetch } from './client.js';

const B = BASES.opsIntelligence;
const orgQuery = () => { const id = getActingOrg(); return id ? { organization_id: id } : {}; };

export const adminApi = {
  // Every account in the company: buildings, can_ingest, usage{queries,ingests,last_active},
  // status (active|invited|suspended) — plus the summary object behind the header tiles.
  listUsers: () => apiFetch(B, '/api/admin/users', { query: orgQuery() }),

  // The company's buildings — the allocation chips. Rows are {id, name, building_code}.
  listBuildings: () => apiFetch(B, '/api/admin/buildings', { query: orgQuery() }),

  // 201 → the invitation: user_id, expires_at, email_sent, and accept_url when the email
  // was dry-run/undelivered. body: {full_name, email, building_ids[], can_ingest, job_title?, role?}.
  inviteUser: (body) => apiFetch(B, '/api/admin/users/invite', { method: 'POST', query: orgQuery(), body }),

  // Partial update — only the fields present change. building_ids is a FULL replacement of
  // the allocation, so chip toggles must send the complete new list. status here accepts
  // only active|inactive (suspended/disabled/deactivated are folded into inactive
  // server-side; deleted is refused — use deleteUser). → {ok, user_id, changed}.
  patchUser: (userId, body) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId), { method: 'PATCH', query: orgQuery(), body }),

  // Reversible. The person can no longer sign in and every live session ends now; their
  // allocation, history and details are kept, so reactivateUser restores exactly what they
  // had. This — not deleteUser below — is what a "Suspend" action in the UI must call.
  deactivateUser: (userId) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId) + '/deactivate', { method: 'POST', query: orgQuery() }),

  // Undoes deactivateUser. Refused (404 user_not_found_or_deleted) once deleteUser has run —
  // a deleted account cannot come back.
  reactivateUser: (userId) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId) + '/reactivate', { method: 'POST', query: orgQuery() }),

  // NOT a suspend — soft but IRREVERSIBLE. The row and its id stay (five tables and the
  // audit log name it with no cascade) but the person is scrubbed: email becomes
  // deleted+<id8>@invalid.local, name becomes "Deleted user", phone/title/password cleared,
  // every session revoked, building allocation dropped. Calling it again is a no-op
  // ({ok, already: true}). Only ever call this from an action explicitly labelled "Delete",
  // separately confirmed from Suspend — never as what a suspend/deactivate button does.
  deleteUser: (userId) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId), { method: 'DELETE', query: orgQuery() }),

  // Company totals plus by_building rows and by_user counters (an object keyed by user id).
  usage: () => apiFetch(B, '/api/admin/usage', { query: orgQuery() }),

  // The trail, newest first. `outcome` takes ONE snake_case token (accepted | reassigned |
  // overridden | rejected | approved_on_confirmation | validated | held | clarified) —
  // the UI's "Accepted" chip folds approved_on_confirmation in client-side. `count` is the
  // filtered total (the pagination denominator); by_outcome ignores the filter.
  ingestionAudit: (query) =>
    apiFetch(B, '/api/admin/ingestion-audit', { query: Object.assign({ limit: 100, offset: 0 }, orgQuery(), query || {}) })
};
