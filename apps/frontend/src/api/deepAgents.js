// api/deepAgents — svc-deepagents, the orchestrator behind the ask bar.
// Routes: apps/backend/.../svc-deepagents/src/api/routes/workflow.py (prefix /api/workflow)
// Mounted behind the gateway at /backend/deep-agents/.
//
// The orchestrator reads the graph through its own sub-agents (compliance, UDR, work
// order, doc-rag), so a question can take a while and the server rate-limits to
// 20/minute per IP. `run` is stateless — a fresh thread per call; `runStateful` keeps a
// session so a human-in-the-loop gate can be resumed.
//
// Also the activity log (plenum_cafm.agent_activity_log): the UI's own actions are appended
// as turns so they sit in the same trail as the server-side stages when troubleshooting.
import { BASES, apiFetch } from './client.js';

const B = BASES.deepAgents;

// LLM round-trips plus tool calls: the ask bar is not a 20-second request.
const T_ASK = 180000;

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
  health: () => apiFetch(B, '/health', { timeoutMs: 6000 }),

  // GET /api/workflow/tools → [{ name, description, domain }] — every tool the
  // orchestrator can route to. The chat page shows the count as its connection line.
  tools: () => apiFetch(B, '/api/workflow/tools', { timeoutMs: 8000 }),

  // POST /api/workflow/run → { session_id, answer, tool_calls[], success, error, interrupted }
  run: (message, sessionId, context) =>
    apiFetch(B, '/api/workflow/run', {
      method: 'POST',
      body: { message: message, session_id: sessionId || null, context: context || null },
      timeoutMs: T_ASK
    }),

  // Same contract, but the session survives an interrupt() gate so it can be resumed.
  // This is the path the compliance console uses: the structured compliance answer is
  // produced by a preflight that only the stateful route reaches.
  runStateful: (message, sessionId, context, signal) =>
    apiFetch(B, '/api/workflow/run-stateful', {
      method: 'POST',
      body: { message: message, session_id: sessionId || null, context: context || null },
      timeoutMs: T_ASK,
      signal: signal
    }),

  // Chat plus document ingestion in one turn. The backend routes by file type —
  // CSV/Excel into the migration flow, PDF/Word/images into doc-rag indexing — then
  // continues the conversation in the same session. Multipart, so it carries a FormData.
  //
  // `buildingId` is the one thing here that is not conversation. Named in the message and
  // mentioned in the context, a building is something an agent has to read and choose to
  // act on; sent as a field it is a foreign key the endpoint can bind. Optional, because a
  // question with an attachment is not always a filing.
  runStatefulWithFiles: (message, sessionId, context, files, signal, buildingId) => {
    const form = new FormData();
    form.append('message', message || '');
    form.append('session_id', sessionId || '');
    if (context) form.append('context', context);
    if (buildingId) form.append('building_id', buildingId);
    (files || []).forEach((f) => form.append('files', f, f.name));
    return apiFetch(B, '/api/workflow/run-stateful-with-files', {
      method: 'POST',
      form: form,
      timeoutMs: T_ASK,
      signal: signal
    });
  },

  resume: (sessionId, body) =>
    apiFetch(B, '/api/workflow/resume/' + encodeURIComponent(sessionId), {
      method: 'POST',
      body: body || {},
      timeoutMs: T_ASK
    }),

  workspace: (sessionId) =>
    apiFetch(B, '/api/workflow/workspace/' + encodeURIComponent(sessionId), { timeoutMs: 30000 }),

  // ── streaming ───────────────────────────────────────────────────────────
  // WS /api/workflow/ws/{session_id}. Connect, send {message, context} once, then read
  // typed events until the server closes:
  //
  //   reasoning          { label, text, domain }        chain-of-thought line
  //   compliance_step    { step: {stage,label,detail…} } one pipeline step, as it happens
  //   compliance_zone    { zone, data }                 one zone of the answer
  //   tool_started       { tool, domain, input }
  //   tool_completed     { tool, domain, output }
  //   agent_switch       { from_domain, to_domain }
  //   gate_interrupt     { payload, session_id }
  //   workflow_completed { answer, session_id, … }
  //   error              { error, session_id }
  //
  // This is the same orchestrator path as run-stateful — stream() calls the compliance
  // preflight too — so the structured answer arrives here as well, painted zone by zone
  // instead of all at the end. Returns a handle with close().
  stream(sessionId, message, context, on) {
    const h = on || {};
    const base = new URL(B + '/api/workflow/ws/' + encodeURIComponent(sessionId), window.location.origin);
    base.protocol = base.protocol === 'https:' ? 'wss:' : 'ws:';

    let ws;
    try {
      ws = new WebSocket(base.toString());
    } catch (e) {
      if (h.onError) h.onError(e);
      return { close: () => {} };
    }

    let settled = false;
    const finish = (err) => {
      if (settled) return;
      settled = true;
      if (err && h.onError) h.onError(err);
      else if (h.onClose) h.onClose();
    };

    ws.onopen = () => {
      try {
        ws.send(JSON.stringify({ message: message, context: context || null }));
        if (h.onOpen) h.onOpen();
      } catch (e) { finish(e); }
    };
    ws.onmessage = (ev) => {
      let d = null;
      try { d = JSON.parse(ev.data); } catch (e) { return; }
      if (!d || !d.type) return;
      if (d.type === 'error') {
        settled = true;
        if (h.onError) h.onError(new Error(d.error || 'stream error'));
        return;
      }
      if (h.onEvent) h.onEvent(d);
      if (d.type === 'workflow_completed' || d.type === 'gate_interrupt') {
        settled = true;
        if (h.onDone) h.onDone(d);
      }
    };
    // A socket that never opens is the normal case when the service is down; the caller
    // falls back to the non-streaming POST rather than showing an error.
    ws.onerror = () => finish(new Error('stream unavailable'));
    ws.onclose = () => finish(null);

    return {
      close: () => {
        settled = true;
        try { ws.close(); } catch (e) { /* already closed */ }
      }
    };
  },

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
