// api/reports — svc-operations-intelligence's /api/reports: a person's pinned report
// cards, refreshed on a cadence by the server's own scheduler (engines/reports).
// Routes: apps/backend/.../svc-operations-intelligence/src/api/routes/reports.py.
// Mounted behind the gateway at /backend/ops-intelligence/. Every row is the caller's
// own — no org scoping query needed, unlike admin.js.
import { BASES, apiFetch } from './client.js';

const B = BASES.opsIntelligence;

export const reportsApi = {
  // The caller's reports, each with its cards and their latest runs.
  list: () => apiFetch(B, '/api/reports'),
  create: (name) => apiFetch(B, '/api/reports', { method: 'POST', body: { name } }),
  rename: (reportId, name) => apiFetch(B, '/api/reports/' + encodeURIComponent(reportId), { method: 'PATCH', body: { name } }),
  remove: (reportId) => apiFetch(B, '/api/reports/' + encodeURIComponent(reportId), { method: 'DELETE' }),

  // The cadence presets a client can offer — server-authoritative labels/badges.
  refreshOptions: () => apiFetch(B, '/api/reports/refresh-options'),

  // Pin a question as a card. body: {prompt, name?, report_id?, refresh, timezone?,
  // source_session_id?, source_page?, source_message_id?, seed?, run_now}.
  createCard: (body) => apiFetch(B, '/api/reports/cards', { method: 'POST', body }),
  getCard: (cardId) => apiFetch(B, '/api/reports/cards/' + encodeURIComponent(cardId)),
  patchCard: (cardId, body) => apiFetch(B, '/api/reports/cards/' + encodeURIComponent(cardId), { method: 'PATCH', body }),
  deleteCard: (cardId) => apiFetch(B, '/api/reports/cards/' + encodeURIComponent(cardId), { method: 'DELETE' }),
  // Refresh now — this one BLOCKS on the orchestrator, which reads the graph, runs its
  // pipeline and writes the run row before answering. Observed real runs: 33s, 55s, 58s.
  // apiFetch's default timeout is 20s, so every genuine refresh reported "timed out after
  // 20s" to the user while the server went on to succeed, and the poll then quietly filled
  // the answer in — a failure message on a request that worked. Given the same headroom
  // the compliance work endpoints get (api/compliance.js), and the same the browser-side
  // scheduler used before the server owned this.
  runCard: (cardId) => apiFetch(B, '/api/reports/cards/' + encodeURIComponent(cardId) + '/run',
    { method: 'POST', timeoutMs: 180000 }),
  cardRuns: (cardId) => apiFetch(B, '/api/reports/cards/' + encodeURIComponent(cardId) + '/runs')
};
