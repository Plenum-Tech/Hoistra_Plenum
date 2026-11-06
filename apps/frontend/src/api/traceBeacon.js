// traceBeacon — what the browser saw of an orchestrator answer, sent to Hoist Trace Lab (3 Nov 2026), so the lab keeps
// it beside the backend's recorded trace: when the answer finished, how long it took, a stop, or the error the person saw.
//
// The ONE approved exception to the eval golden rule (Hussain, 3 Nov 2026). It is additive and inert:
// * off unless the build was made with VITE_TRACE_LAB=1 (the same build switch as the lab page);
// * fire-and-forget: the caller never waits on it, and nothing it does can throw into the chat;
// * a plain fetch with keepalive and the current bearer: no token refresh, no 401 handling, no sign-out and no retry.
//   A refused or failed send is dropped.
// It does not import the Trace Lab module (src/tracelab): the two path constants are repeated here on purpose.
import { accessToken } from './client.js';

const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};
const ON = env.VITE_TRACE_LAB === '1';
// src/tracelab/api.js BASE + PREFIX, and the lab's route (svc-trace-lab routes_loop.py).
const URL_PATH = '/backend/trace-lab' + '/api/trace-lab' + '/lab/frontend-events';
const KINDS = ['answer_shown', 'error', 'stopped'];

const text = (v, most) => {
  if (v === null || v === undefined) return null;
  const s = String(v).trim();
  return s ? s.slice(0, most) : null;
};
const ms = (v) => {
  const n = Number(v);
  return v === null || v === undefined || !Number.isFinite(n) || n < 0 ? null : Math.round(n);
};

// The event as it is sent: only a known kind, text capped, times whole and non-negative. null for anything else.
export function beaconBody(evt) {
  if (!evt || KINDS.indexOf(evt.kind) < 0) return null;
  return {
    kind: evt.kind, turn_id: text(evt.turn_id, 200), session_id: text(evt.session_id, 200),
    question: text(evt.question, 4000), view: text(evt.view, 60), instance: text(evt.instance, 40),
    ms_first_text: ms(evt.ms_first_text), ms_total: ms(evt.ms_total), error: text(evt.error, 500),
    client_at: text(evt.client_at, 40) || new Date().toISOString()
  };
}

// The turn the chat has just written (its last message): an answer, a stop or an error. null when the last message is
// not the orchestrator's (nothing was written for this question).
export function beaconOf(state, question, sessionId, startedAt) {
  try {
    const chat = (state && state.ccChat) || [];
    const last = chat[chat.length - 1];
    if (!last || last.role !== 'bot') return null;
    const kind = last.stopped ? 'stopped' : (last.error ? 'error' : 'answer_shown');
    return {
      kind, turn_id: last.turnId || null, session_id: (state && state.sessionId) || sessionId || null, question,
      view: (state && state.view) || null, instance: (state && state.instance) || null,
      ms_first_text: null, ms_total: startedAt ? Date.now() - startedAt : null,
      error: kind === 'error' ? last.text || null : null, client_at: new Date().toISOString()
    };
  } catch (e) {
    return null;
  }
}

function send(body) {
  try {
    const token = accessToken();
    if (!token || typeof fetch !== 'function') return;
    const p = fetch(URL_PATH, {
      method: 'POST', keepalive: true, credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', Accept: 'application/json', Authorization: 'Bearer ' + token },
      body: JSON.stringify(body)
    });
    if (p && typeof p.catch === 'function') p.catch(() => {});
  } catch (e) {
    // dropped: the chat never hears of it
  }
}

// Sends one event after the caller has moved on. Never throws, never waits, never retries.
export function traceBeacon(evt) {
  if (!ON) return;
  try {
    const body = beaconBody(evt);
    if (!body) return;
    setTimeout(() => send(body), 0);
  } catch (e) {
    // dropped
  }
}
