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
import { BASES, getActingOrg, apiFetch, accessToken, errorMessage } from './client.js';

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
    apiFetch(B, '/api/admin/ingestion-audit', { query: Object.assign({ limit: 100, offset: 0 }, orgQuery(), query || {}) }),

  // What a full export WOULD contain: a row count per table, and a reason per table left
  // out. Costs a count rather than a download, so the dialog can say what is in the file
  // before anyone waits for one — and so "is everything really in there" has an answer
  // that is checkable rather than claimed.
  exportPreview: () =>
    apiFetch(B, '/api/admin/export', { query: Object.assign({ preview: true }, orgQuery()), timeoutMs: 120000 }),

  // What clearing these pages' data would delete — counted in a transaction the service rolls
  // back. areas: any of compliance, contracts, assets, energy, maintenance. → {row_total,
  // areas[{area,label,rows,tables}], links_cleared[], blocked[], confirm_with, …}.
  dataResetPreview: (areas) =>
    apiFetch(B, '/api/admin/data-reset', { query: Object.assign({ areas: (areas || []).join(',') }, orgQuery()), timeoutMs: 120000 }),

  // IRREVERSIBLE. Deletes this company's rows behind those pages. `confirm` must be the
  // company name exactly (the preview's confirm_with). 409 reset_blocked carries
  // detail.blocked when kept rows depend on the ones going; nothing is changed then.
  dataReset: (areas, confirm) =>
    apiFetch(B, '/api/admin/data-reset', { method: 'POST', query: orgQuery(), body: { areas: areas, confirm: confirm }, timeoutMs: 600000 })
};

// The export itself. It cannot go through apiFetch: that parses every response as JSON and
// this one is a zip. So it repeats the two things apiFetch does that matter here — the
// Authorization header and the acting-company query — and nothing else. A 401 is reported
// rather than retried; the preview call above runs first and would have refreshed a stale
// token already.
export async function downloadOrgExport() {
  const org = getActingOrg();
  const url = B + '/api/admin/export' + (org ? '?organization_id=' + encodeURIComponent(org) : '');
  const token = accessToken();
  const res = await fetch(url, {
    method: 'GET',
    headers: token ? { Authorization: 'Bearer ' + token } : {}
  });
  if (!res.ok) {
    // The failure body is JSON even though the success body is not.
    let msg = 'Export failed (' + res.status + ')';
    try { const d = await res.json(); msg = errorMessage(d, res) || msg; } catch (e) { /* not json */ }
    throw new Error(msg);
  }
  // Content-Disposition names the file; falling back to a generated name rather than
  // letting the browser save it as "export".
  const disp = res.headers.get('Content-Disposition') || '';
  const named = /filename="([^"]+)"/.exec(disp);
  const name = named ? named[1] : 'hoistra-export.zip';

  const blob = await res.blob();
  const href = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = href;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  // Revoked on the next tick: revoking synchronously races the click in some browsers and
  // the download silently never starts.
  setTimeout(() => URL.revokeObjectURL(href), 0);
  // Planned figures, not written ones: the zip is streamed, so its real totals are only
  // known once the last byte has gone and headers go out first. manifest.json inside the
  // file carries what was actually written.
  return {
    name: name,
    bytes: blob.size,
    tables: Number(res.headers.get('X-Export-Tables-Planned') || 0),
    rows: Number(res.headers.get('X-Export-Rows-Planned') || 0)
  };
}
