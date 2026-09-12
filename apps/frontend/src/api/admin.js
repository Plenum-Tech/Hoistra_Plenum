// api/admin — svc-operations-intelligence's /api/admin: the company admin console.
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/admin.py. Mounted
// behind the gateway at /backend/ops-intelligence/. Admin role required (a superadmin
// passes); every failure is {detail: {ok, error, reason}}.
//
// No withOrg here, deliberately. The company is the caller's, derived from the token
// server-side; ?organization_id= exists on these routes only as a superadmin override, and
// a non-superadmin naming any other company is 403 wrong_organization. Sending ORG_ID from
// the client would turn a build arg into a tenancy bug.
import { BASES, apiFetch } from './client.js';

const B = BASES.opsIntelligence;

export const adminApi = {
  // Every account in the company: buildings, can_ingest, usage{queries,ingests,last_active},
  // status (active|invited|suspended) — plus the summary object behind the header tiles.
  listUsers: () => apiFetch(B, '/api/admin/users'),

  // The company's buildings — the allocation chips. Rows are {id, name, building_code}.
  listBuildings: () => apiFetch(B, '/api/admin/buildings'),

  // 201 → the invitation: user_id, expires_at, email_sent, and accept_url when the email
  // was dry-run/undelivered. body: {full_name, email, building_ids[], can_ingest, job_title?, role?}.
  inviteUser: (body) => apiFetch(B, '/api/admin/users/invite', { method: 'POST', body }),

  // Partial update — only the fields present change. building_ids is a FULL replacement of
  // the allocation, so chip toggles must send the complete new list. → {ok, user_id, changed}.
  patchUser: (userId, body) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId), { method: 'PATCH', body }),

  // Suspend, never delete — the audit trail names this person. Revokes their sessions now.
  suspendUser: (userId) =>
    apiFetch(B, '/api/admin/users/' + encodeURIComponent(userId), { method: 'DELETE' }),

  // Company totals plus by_building rows and by_user counters (an object keyed by user id).
  usage: () => apiFetch(B, '/api/admin/usage'),

  // The trail, newest first. `outcome` takes ONE snake_case token (accepted | reassigned |
  // overridden | rejected | approved_on_confirmation | validated | held | clarified) —
  // the UI's "Accepted" chip folds approved_on_confirmation in client-side. `count` is the
  // filtered total (the pagination denominator); by_outcome ignores the filter.
  ingestionAudit: (query) =>
    apiFetch(B, '/api/admin/ingestion-audit', { query: Object.assign({ limit: 100, offset: 0 }, query || {}) })
};
