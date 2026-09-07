// api/deepAgents — svc-deepagents (the single-door orchestrator), incl. the activity log.
// Routes: apps/backend/.../svc-deepagents/src/api/routes/workflow.py
import { BASES, apiFetch } from './client.js';

const B = BASES.deepAgents;

// One browser session id per page load, so the UI's own compliance actions land in the same
// trail as the server-side stages when troubleshooting.
let _sessionId = null;
export function activitySessionId() {
  if (_sessionId) return _sessionId;
  try {
    _sessionId = sessionStorage.getItem('hoistra_activity_session') || '';
  } catch (e) { _sessionId = ''; }
  if (!_sessionId) {
    _sessionId = 'ui-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 8);
    try { sessionStorage.setItem('hoistra_activity_session', _sessionId); } catch (e) { /* private mode */ }
  }
  return _sessionId;
}

// One id per UI action (scan, verify, renewal) so its input and output rows group as a turn.
export function newTurn() { return 'ui-turn-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6); }

export const deepAgentsApi = {
  // Append one client-side entry to plenum_cafm.agent_activity_log. Fire-and-forget: a
  // logging failure is never surfaced to the user.
  logActivity: (entry) =>
    apiFetch(B, '/api/workflow/activity', {
      method: 'POST', timeoutMs: 8000,
      body: Object.assign({ session_id: activitySessionId(), agent: 'frontend' }, entry)
    }).catch(() => null),

  activityFor: (sessionId, query) =>
    apiFetch(B, '/api/workflow/activity/' + encodeURIComponent(sessionId), { query: query || {} }),
  activitySessions: (limit) => apiFetch(B, '/api/workflow/activity', { query: { limit: limit || 50 } })
};
