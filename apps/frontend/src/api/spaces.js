// api/spaces — svc-udr's customer-named Saved Spaces (Plenum WP-3).
// Routes: apps/backend/.../svc-udr/src/api/routes/spaces.py, mounted at /backend/udr/api/spaces.
//
// The one place the navigator writes to the backend: creating, renaming or deleting a space
// changes plenum_cafm.saved_spaces on the user's click. The rows are scoped by organisation
// when VITE_ORGANIZATION_ID is set, otherwise to the service's unscoped (NULL-org) bucket —
// the same rule the Plenum shell applies.
import { BASES, ORG_ID, apiFetch } from './client.js';

const B = BASES.udr;
const enc = encodeURIComponent;

export const spacesApi = {
  // GET /api/spaces → { spaces: [{ id, organization_id, name, kind, created_by, created_at }] }
  list: () => apiFetch(B, '/api/spaces', { query: ORG_ID ? { organization_id: ORG_ID } : {}, timeoutMs: 15000 }),
  // POST /api/spaces → the created row (201).
  create: (name, createdBy) =>
    apiFetch(B, '/api/spaces', {
      method: 'POST',
      body: { name: name, organization_id: ORG_ID || null, created_by: createdBy || null },
      timeoutMs: 15000
    }),
  // PATCH /api/spaces/{id} → the renamed row.
  rename: (id, name) => apiFetch(B, '/api/spaces/' + enc(id), { method: 'PATCH', body: { name: name }, timeoutMs: 15000 }),
  // DELETE /api/spaces/{id} → { deleted: true, id }
  remove: (id) => apiFetch(B, '/api/spaces/' + enc(id), { method: 'DELETE', timeoutMs: 15000 })
};
