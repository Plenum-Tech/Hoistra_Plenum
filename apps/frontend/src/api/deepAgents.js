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
import { BASES, apiFetch, accessToken, getActingOrg } from './client.js';

const B = BASES.deepAgents;

// How the caller reaches a socket. A WebSocket handshake has no Authorization header to put
// a bearer in, so the token is offered as a subprotocol behind this marker — the client
// sends [marker, token], the server agrees to the marker alone and reads the token off the
// handshake. Matches WS_AUTH_SUBPROTOCOL in svc-deepagents/src/services/principal.py;
// changing one without the other signs everybody out of the streaming path.
export const WS_AUTH_SUBPROTOCOL = 'hoistra.auth.bearer';

// LLM round-trips plus tool calls: the ask bar is not a 20-second request.
const T_ASK = 180000;
// Case actions forward to ops-intelligence (60s there); clarify runs an LLM assessment.
const T_CASE = 60000;

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

// A superadmin's viewAsCompany() override, and ONLY that — deliberately raw getActingOrg(),
// never client.js's currentOrgId() (which falls back to the build's own default org for
// every ordinary caller). The compliance/vendor/energy GETs can send that fallback because
// operations-intelligence there is lenient about a caller naming their own org;
// workflow.py's _resolve_acting_org is not — a normal caller naming ANY org, own default
// included, that is not their real one is a 403. null (nobody viewing as anyone) is the
// only value every ordinary caller may ever send, so this must stay null for them.
function orgOverride() { const o = getActingOrg(); return o ? { organization_id: o } : {}; }

export const deepAgentsApi = {
  health: () => apiFetch(B, '/health', { timeoutMs: 6000 }),

  // GET /api/workflow/tools → [{ name, description, domain }] — every tool the
  // orchestrator can route to. The chat page shows the count as its connection line.
  tools: () => apiFetch(B, '/api/workflow/tools', { timeoutMs: 8000 }),

  // POST /api/workflow/run → { session_id, answer, tool_calls[], success, error, interrupted }
  run: (message, sessionId, context) =>
    apiFetch(B, '/api/workflow/run', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, orgOverride()),
      timeoutMs: T_ASK
    }),

  // Same contract, but the session survives an interrupt() gate so it can be resumed.
  // This is the path the compliance console uses: the structured compliance answer is
  // produced by a preflight that only the stateful route reaches.
  runStateful: (message, sessionId, context, signal) =>
    apiFetch(B, '/api/workflow/run-stateful', {
      method: 'POST',
      body: Object.assign({ message: message, session_id: sessionId || null, context: context || null }, orgOverride()),
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
  //
  // `opts.interactiveMigration` sets the route's interactive_migration flag: a CSV/Excel
  // attachment then stops at its first human gate (answered in the Orchestrator, which
  // ingested_migration_ids in the reply points at) instead of every gate being approved
  // server-side as proposed.
  runStatefulWithFiles: (message, sessionId, context, files, signal, buildingId, opts) => {
    const form = new FormData();
    form.append('message', message || '');
    form.append('session_id', sessionId || '');
    if (context) form.append('context', context);
    if (buildingId) form.append('building_id', buildingId);
    if (opts && opts.interactiveMigration) form.append('interactive_migration', 'true');
    // The source system the spreadsheets came out of. The route has always taken it
    // (workflow.py's `cmms_name: str = Form("Custom")`) and this client never sent it, so
    // every run started from the chat was labelled Custom however the composer was filled
    // in. It picks the mapper's alias pack, so it is not just a caption.
    if (opts && opts.cmmsName) form.append('cmms_name', opts.cmmsName);
    const org = getActingOrg();
    if (org) form.append('organization_id', org);
    (files || []).forEach((f) => form.append('files', f, f.name));
    return apiFetch(B, '/api/workflow/run-stateful-with-files', {
      method: 'POST',
      form: form,
      timeoutMs: T_ASK,
      signal: signal
    });
  },

  // Submits the human's decision for an interrupt() gate — mapping_approval:
  // {approved, corrections}, rollback_confirmation: {confirmed}. The route's ResumeRequest
  // wants that decision nested under a `decision` key, not posted as the body itself.
  resume: (sessionId, decision) =>
    apiFetch(B, '/api/workflow/resume/' + encodeURIComponent(sessionId), {
      method: 'POST',
      body: { decision: decision || {} },
      timeoutMs: T_ASK
    }),

  // ── ingestion validation cases ──────────────────────────────────────────
  // Routes: .../svc-deepagents/src/api/routes/ingestion_cases.py (prefix /api/ingestion).
  // A held upload comes back from runStatefulWithFiles as validation_cases +
  // validation_held; this is the conversation that decides those cases. Each call forwards
  // to ops-intelligence (the authority that ran the check) with the caller's own bearer,
  // and decide is HERE and not there because on a yes this service performs the bind it
  // withheld — the response then carries bound: {documents, certificates, created}.
  // Ops-intelligence unreachable is 503 {reason: 'validation_unavailable'}: the document
  // stays held, nothing is lost.

  // Cases waiting on somebody (open_only defaults true on the server). Rows are the case
  // object minus its heavy fields (claims, ontology, events) — getCase returns those.
  listCases: (opts) => {
    const o = opts || {};
    return apiFetch(B, '/api/ingestion/cases', {
      query: { open_only: o.openOnly, building_id: o.buildingId }
    });
  },

  // One case in full: verdict, findings, claims, ontology, candidates, question, the
  // conversation (events) and may_ingest — the permission a decision grants.
  getCase: (caseId) =>
    apiFetch(B, '/api/ingestion/cases/' + encodeURIComponent(caseId)),

  // The uploader's reason → the agent's assessment. Always ends in a question
  // (requires_confirmation: true) — nothing is filed by this call, whatever it makes of
  // the explanation. The assessment is an LLM read, so it gets the case timeout.
  clarifyCase: (caseId, explanation) =>
    apiFetch(B, '/api/ingestion/cases/' + encodeURIComponent(caseId) + '/clarify', {
      method: 'POST', body: { explanation: explanation }, timeoutMs: T_CASE
    }),

  // Move the case to another building — and the check RE-RUNS there before agreeing, so a
  // document that does not match the new building either says so rather than inheriting
  // an approval.
  reassignCase: (caseId, buildingId) =>
    apiFetch(B, '/api/ingestion/cases/' + encodeURIComponent(caseId) + '/reassign', {
      method: 'POST', body: { building_id: buildingId }, timeoutMs: T_CASE
    }),

  // The explicit yes or no — approve is required, there is no default. On a yes the
  // withheld filing happens and the response carries bound; a bind that fails after the
  // decision is reported in bound.error and can be retried, the decision stands.
  decideCase: (caseId, d) =>
    apiFetch(B, '/api/ingestion/cases/' + encodeURIComponent(caseId) + '/decide', {
      method: 'POST', body: { approve: d.approve, note: d.note || null }, timeoutMs: T_CASE
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

    // The signed-in caller, carried in the handshake. Without one the server closes the
    // socket, so there is nothing to gain by dialling: report it and let the caller take
    // the non-streaming path, which refreshes the access token and retries on its own.
    // That is the normal state for the first moments of a session — the access token is
    // never persisted, only rebuilt by authBoot()'s refresh.
    const token = accessToken();
    if (!token) {
      if (h.onError) h.onError(new Error('not signed in yet'));
      return { close: () => {} };
    }

    let ws;
    try {
      ws = new WebSocket(base.toString(), [WS_AUTH_SUBPROTOCOL, token]);
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
        ws.send(JSON.stringify(Object.assign({ message: message, context: context || null }, orgOverride())));
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
