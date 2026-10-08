// sessions — every conversation with the orchestrator, kept so the navigator can reopen it.
//
// Not to be confused with session.js, which is the reload slice (signed in, current view).
// A *session* here is what the Plenum AI shell calls one: a thread with svc-deepagents, keyed
// by the `session_id` the server keeps its LangGraph state under. Since 2 Oct 2026 the server
// keeps every thread too (GET /api/threads: title, turns, a running summary - the chat's
// memory phase A), so this browser's localStorage is a cache of it: the list is merged from
// the server on each sign-in (mergeServerThreads), a thread this browser never saw is
// hydrated from the server when opened, and a delete here hides it there. Reopening a
// session restores its transcript and continues the same server thread; the orchestrator
// sees the earlier turns through its checkpointer and the server's own record.
//
// Orchestrator tasks (`orch()` in core.js) are sessions too, of kind "task": they reopen the
// dock on the step chain they played.
//
// The pure functions are tested in test/sessions.test.mjs; the methods at the bottom are mixed
// into HoistraLogic.prototype and `this` is the controller.
import { domainOf } from './chat.js';
import { dayLabel } from './homeLive.js';
import { deepAgentsApi } from '../api/deepAgents.js';
import { extractComplianceAnswer } from './complianceLive.js';
import { readSupport, supportRef, supportStatus, SUPPORT_SPACE_KEY } from './support.js';

export const SESSIONS_KEY = 'hoistra.sessions.v1';
// Every session is kept (up to MAX_SESSIONS); only the newest FULL_SESSIONS keep their
// transcript in this browser. Older conversations are a title and a time, and their turns come
// back from the server when opened (sessionHydrate) — the list used to stop at 60, and every
// orchestrator task pushed an older conversation out of it (7 Oct 2026).
export const MAX_SESSIONS = 1000;
export const FULL_SESSIONS = 60;
// A page of the server's thread list; the server allows 200.
export const THREAD_PAGE = 200;
export const MAX_TURNS = 40;

// Real elapsed time, in the bands the navigator shows.
export function ago(at, now) {
  if (!at) return '';
  const s = Math.max(0, Math.round(((now === undefined ? Date.now() : now) - at) / 1000));
  if (s < 45) return 'just now';
  const m = Math.round(s / 60);
  if (m < 60) return m + (m === 1 ? ' min ago' : ' mins ago');
  const h = Math.round(m / 60);
  if (h < 24) return h + (h === 1 ? ' hour ago' : ' hours ago');
  const d = Math.round(h / 24);
  return d + (d === 1 ? ' day ago' : ' days ago');
}

export function newSessionId() {
  const c = typeof globalThis !== 'undefined' ? globalThis.crypto : null;
  if (c && typeof c.randomUUID === 'function') return c.randomUUID();
  return 'hs-' + Date.now().toString(36) + '-' + Math.random().toString(16).slice(2, 10);
}

// The record. `label` and `task` duplicate `title` because the dock's Recent tasks and the
// report menu already read those names.
//
// `owner` is the signed-in account's email at the moment the session was created — this
// store is one shared browser localStorage array, not per-account, and more than one
// account has always been able to sign in and out of the same tab (superadmin's own
// view-as-company mode makes that routine, not exotic). Without an owner tag every
// session ever created in this browser — by every account that ever tested from it —
// shows up in everyone's navigator. shapeSessionList() below is what actually hides a
// session whose owner does not match who is looking; this just stamps it at birth.
//
// `viewOrgId` is the SAME owner's acting-company scope at that moment (superAdmin.js's
// viewAsCompany — null when not viewing as anyone). owner alone does not separate a
// superadmin's TechCorp questions from their Plenum Tech questions: it is the same email
// both times, but each is a live thread against that company's own data via a different
// organization_id, so reopening a TechCorp session while viewing as Plenum Tech would
// resume the wrong company's thread. Stamped at birth, same as owner.
export function makeSession(o) {
  const at = o.at === undefined ? Date.now() : o.at;
  const kind = o.kind === 'task' ? 'task' : 'chat';
  return {
    id: String(o.id),
    kind: kind,
    title: String(o.title || ''),
    label: String(o.title || ''),
    task: o.task !== undefined ? o.task : String(o.title || ''),
    ctx: o.ctx || null,
    k: null,
    steps: Array.isArray(o.steps) ? o.steps : [],
    page: o.page || 'Home',
    at: at,
    createdAt: at,
    turns: [],
    calls: [],
    domain: 'Orchestrator',
    spaceId: null,
    owner: typeof o.owner === 'string' && o.owner ? o.owner.trim().toLowerCase() : null,
    viewOrgId: typeof o.viewOrgId === 'string' && o.viewOrgId ? o.viewOrgId : null,
    // The migration run this conversation started or opened (logic/migration.js). A run is
    // answered in the conversation that started it — its card leaves with that conversation
    // and comes back with it. Held on the record because mgId alone is one controller-wide
    // field: left as that, "+ New query" after an ingestion put the next question up with
    // the previous conversation's run still running beneath it (30 Sep 2026).
    migrationId: typeof o.migrationId === 'string' && o.migrationId ? o.migrationId : null,
    // A support request (logic/support.js): { status: 'open' | 'resolved', openedAt, resolvedAt,
    // from }. null for every other conversation.
    support: readSupport(o.support)
  };
}

const uniq = (xs) => xs.filter((x, i, a) => x && a.indexOf(x) === i);

// Which engine a session belongs to: a structured compliance answer is Compliance outright,
// otherwise the tools the thread used decide (domainOf, the same rule the chat page applies
// per reply).
export function sessionDomain(rec) {
  if (!rec) return 'Orchestrator';
  if ((rec.turns || []).some((m) => m && m.role !== 'you' && m.rich)) return 'Compliance';
  return domainOf(rec.calls || []);
}

// Engine → built-in space. Documents and migration have no space of their own. The keys are
// the navigator's four built-in spaces (spacesLive.js reads them from here).
export const SPACE_OF_DOMAIN = { Compliance: 'compliance', Energy: 'energy', Vendors: 'vendors', 'Work orders': 'ops' };
export const BUILTIN_SPACE_KEYS = Object.keys(SPACE_OF_DOMAIN).map((d) => SPACE_OF_DOMAIN[d]);
export function spaceKeyOf(domain) {
  return SPACE_OF_DOMAIN[domain] || null;
}

export function sessionIcon(rec) {
  if (!rec) return 'ph-magnifying-glass';
  if (rec.kind === 'task') return 'ph-cpu';
  if (supportStatus(rec)) return 'ph-lifebuoy';
  return {
    Compliance: 'ph-shield-check', Energy: 'ph-lightning', Vendors: 'ph-chart-line-up',
    'Work orders': 'ph-wrench', Documents: 'ph-file-text', Migration: 'ph-swap'
  }[rec.domain] || 'ph-magnifying-glass';
}

// The transcript mirrored into the record: the last 40 messages, the tools seen across the
// thread, the engine they imply, and the time. Returns a new record.
export function syncTurns(rec, turns, now) {
  const t = (turns || []).slice(-MAX_TURNS);
  const calls = uniq(t.reduce((a, m) => (m && m.role !== 'you' ? a.concat(m.calls || []) : a), []));
  const out = Object.assign({}, rec, { turns: t, calls: calls, at: now === undefined ? Date.now() : now });
  if (!out.title) {
    const you = t.find((m) => m && m.role === 'you' && m.text);
    if (you) { out.title = you.text; out.label = you.text; out.task = you.text; }
  }
  out.domain = sessionDomain(out);
  return out;
}

export function trimSessions(list) {
  return (list || []).filter(Boolean).slice().sort((a, b) => (b.at || 0) - (a.at || 0)).slice(0, MAX_SESSIONS)
    .map((r, i) => (i < FULL_SESSIONS ? r : shed(r)));
}

// An older conversation without its transcript: how many questions it held is kept for the
// list, and `remote` makes opening it read the turns back from the server. A task has no
// server copy, and its steps are small, so it is kept whole.
function shed(r) {
  if (r.kind === 'task' || !(r.turns || []).length) return r;
  const asked = r.turns.filter((m) => m && m.role === 'you').length;
  return Object.assign({}, r, { turns: [], remote: true, turnCount: asked || r.turnCount || 0 });
}

// How many questions a session holds, whether or not its transcript is in this browser.
export function questionCount(r) {
  if (!r || r.kind === 'task') return 0;
  const here = (r.turns || []).filter((m) => m && m.role === 'you').length;
  return here || Number(r.turnCount) || Number(r.serverTurns) || 0;
}

// ── the server's copy ────────────────────────────────────────────────────────
const isoMs = (s) => { const t = s ? Date.parse(s) : NaN; return isNaN(t) ? null : t; };

// The server's list folded into this browser's: a thread this browser has no record of is
// added (turns empty, `remote` - hydrated when opened); one it has keeps its transcript and
// takes the later time. Only the caller's own records in the current company scope are
// touched: every server row belongs to that owner and scope by construction (the route
// filters on both), so they are stamped the same way. Records of other owners/scopes in the
// same browser array pass through untouched.
export function mergeServerThreads(local, threads, opts) {
  const o = opts || {};
  const owner = o.owner ? String(o.owner).trim().toLowerCase() : null;
  if (!owner) return local || [];
  const scope = o.viewOrgId || null;
  // Deleted here while the read was out: the server's page still listed them.
  const skip = o.skip || null;
  const byId = {};
  (local || []).forEach((r) => { if (r) byId[r.id] = r; });
  const out = (local || []).slice();
  (threads || []).forEach((t) => {
    if (!t || typeof t.id !== 'string' || !t.id) return;
    if (skip && skip.has(t.id)) return;
    const at = isoMs(t.last_message_at) || isoMs(t.created_at) || Date.now();
    const have = byId[t.id];
    if (have) {
      if (have.owner !== owner || (have.viewOrgId || null) !== scope) return;
      const patch = {};
      if ((have.at || 0) < at) patch.at = at;
      if (!have.title && t.title) { patch.title = t.title; patch.label = t.title; patch.task = t.title; }
      if (Object.keys(patch).length) out[out.indexOf(have)] = Object.assign({}, have, patch);
      return;
    }
    const rec = makeSession({ id: t.id, title: t.title || 'Conversation', page: 'Home', at: at, owner: owner, viewOrgId: scope });
    rec.createdAt = isoMs(t.created_at) || at;
    rec.remote = true;
    rec.serverTurns = Number(t.turn_count) || 0;
    out.push(rec);
  });
  return trimSessions(out);
}

// A server thread's turns in the chat's own message shape, so a conversation asked on another
// device reads here as it did there. Structured (rich) compliance answers come back as their
// markdown; the tools behind each reply are kept so the engine badge is right.
//
// A re-run (route "rerun") is stored under "Re-run with corrections (<notes>): <question>". It
// reads as it did live: the original question, a note that it was re-run, and the corrected
// answer in the old one's place. The bot message keeps the stored question as `asked`, so
// attachTurns gives it the re-run's own run rather than the original's.
const RERUN_LEAD = /^Re-run with corrections \(/;
function rerunOriginal(stored, prev) {
  if (prev && stored.endsWith('): ' + prev)) return prev;
  const at = stored.indexOf('): ');
  return at > -1 ? stored.slice(at + 3) : stored;
}
export function turnsFromThread(thread) {
  const out = [];
  ((thread && thread.turns) || []).forEach((t) => {
    if (!t) return;
    const tools = Array.isArray(t.tools) ? t.tools : [];
    const calls = tools.filter((x) => typeof x === 'string');
    // The dashboard payloads the server keeps beside the names (services/chat_threads.py
    // _card_payloads): the answer reopens as it rendered live. Older rows have names only.
    const rich = extractComplianceAnswer(tools.filter((x) => x && typeof x === 'object' && typeof x.tool === 'string'));
    const stored = String(t.question || '');
    if (t.route === 'rerun' && RERUN_LEAD.test(stored)) {
      let qi = -1;
      for (let j = out.length - 1; j >= 0; j -= 1) { if (out[j].role === 'you') { qi = j; break; } }
      const prev = qi > -1 ? String(out[qi].text || '') : '';
      const original = rerunOriginal(stored, prev);
      if (qi > -1 && prev === original) out.splice(qi);
      out.push({ role: 'you', text: original, correction: 'Re-run with your correction', rerun: true });
      if (t.answer) out.push(Object.assign({ role: 'bot', text: String(t.answer), calls: calls, asked: stored, rerunOf: true }, rich ? { rich: rich } : {}));
      return;
    }
    if (t.question) out.push({ role: 'you', text: stored });
    if (t.answer) out.push(Object.assign({ role: 'bot', text: String(t.answer), calls: calls }, rich ? { rich: rich } : {}));
  });
  return out.slice(-MAX_TURNS);
}

// The stored runs of a reopened session, put behind its answers again. The thread keeps the
// question and the answer, not the run; without the turn id the rail had nothing to load and
// "Was this right?" never showed (5 Oct 2026). Runs are matched to answers in order, checked
// against the question each run recorded; a run with no answer to sit behind is left out.
export function attachTurns(messages, turns) {
  const runs = (turns || []).filter((t) => t && t.turn_id).slice()
    .sort((a, b) => String(a.started_at || '').localeCompare(String(b.started_at || '')));
  if (!runs.length) return messages;
  let r = 0;
  return (messages || []).map((m, i) => {
    if (!m || m.role !== 'bot' || m.turnId) return m;
    const asked = m.asked ? String(m.asked).trim()
      : i > 0 && messages[i - 1] && messages[i - 1].role === 'you' ? String(messages[i - 1].text || '').trim() : '';
    // the next run whose question is this answer's question; failing that, the next run in order
    let k = runs.findIndex((t, j) => j >= r && asked && String(t.question || '').trim() === asked);
    if (k < 0) k = r < runs.length ? r : -1;
    if (k < 0) return m;
    r = k + 1;
    const t = runs[k];
    return Object.assign({}, m, {
      turnId: t.turn_id,
      ms: typeof t.latency_ms === 'number' ? t.latency_ms : m.ms,
      trace: (m.trace || []).length ? m.trace : [{ at: 0, kind: 'reasoning', label: 'Stored run', text: 'Restored from the stored run - every stage, tool and query of this answer is in the rail.' }]
    });
  });
}

// ── storage ──────────────────────────────────────────────────────────────────
const store = () => {
  try { return (typeof window !== 'undefined' && window.localStorage) || null; } catch (e) { return null; }
};

export function loadSessions(storage) {
  const st = storage || store();
  if (!st) return [];
  let raw = null;
  try { raw = st.getItem(SESSIONS_KEY); } catch (e) { return []; }
  if (!raw) return [];
  let d = null;
  try { d = JSON.parse(raw); } catch (e) { return []; }
  if (!Array.isArray(d)) return [];
  const out = [];
  d.forEach((r) => {
    if (!r || typeof r !== 'object' || typeof r.id !== 'string' || typeof r.title !== 'string' || !r.title) return;
    const rec = makeSession({ id: r.id, title: r.title, page: r.page, kind: r.kind, at: Number(r.at) || Date.now(), task: r.task, ctx: r.ctx, steps: r.steps, owner: r.owner, viewOrgId: r.viewOrgId, migrationId: r.migrationId, support: r.support });
    rec.createdAt = Number(r.createdAt) || rec.at;
    rec.turns = Array.isArray(r.turns) ? r.turns.filter((m) => m && typeof m === 'object' && typeof m.role === 'string') : [];
    rec.calls = Array.isArray(r.calls) ? r.calls.filter((x) => typeof x === 'string') : [];
    rec.spaceId = typeof r.spaceId === 'string' && r.spaceId ? r.spaceId : null;
    rec.domain = typeof r.domain === 'string' && r.domain ? r.domain : sessionDomain(rec);
    // A conversation whose transcript is on the server, not here, stays one across a reload —
    // without this it reopened empty and never read its turns back.
    if (r.remote === true) rec.remote = true;
    if (Number(r.turnCount) > 0) rec.turnCount = Number(r.turnCount);
    if (Number(r.serverTurns) > 0) rec.serverTurns = Number(r.serverTurns);
    out.push(rec);
  });
  return trimSessions(out);
}

// Shrinks in stages when the browser refuses the size: traces go first (they are the bulk of
// a turn), then the transcripts of everything but the newest ten, then the oldest sessions.
const shrink = (list, stage, keepId) => {
  // The open conversation keeps its transcript at every stage: after a reload it is what is on
  // screen, and nothing reads a shed active conversation back.
  const shedOthers = (r) => (keepId && r.id === keepId ? r : shed(r));
  if (stage === 0) return list.map((r, i) => (i < 5 ? r : Object.assign({}, r, {
    turns: (r.turns || []).map((m) => { if (!m || !m.trace) return m; const c = Object.assign({}, m); delete c.trace; return c; })
  })));
  if (stage === 1) return list.map((r, i) => (i < 10 ? r : shedOthers(r)));
  // Then every transcript, and only then the oldest records — the history is the point.
  if (stage === 2) return list.map(shedOthers);
  return list.slice(0, 300).map(shedOthers);
};

export function saveSessions(list, storage, keepId) {
  const st = storage || store();
  if (!st) return false;
  let l = trimSessions(list);
  for (let stage = 0; stage <= 4; stage++) {
    try {
      st.setItem(SESSIONS_KEY, JSON.stringify(l));
      return true;
    } catch (e) {
      if (stage === 4) return false;
      l = shrink(l, stage, keepId);
    }
  }
  return false;
}

// ── the list ─────────────────────────────────────────────────────────────────
// Grouped by day, newest first. `space` narrows to a built-in key (matched on the engine),
// the Support space (support requests, open or resolved — `status` narrows to one) or a saved
// space id (matched on where the session was filed). A support request is not listed under
// the engine that answered it: it is a request, whatever it touched. `owner` narrows to the
// signed-in account that created the session — this store is one shared browser array
// across every account that has ever signed in in this tab (see makeSession()'s own
// comment), so every caller must pass the current account's email or an unrelated
// account's test sessions leak into view. No owner (not signed in yet) shows nothing,
// same as an owner that matches no session — an empty list is the safe default, never
// "show everyone's". Handlers are added by the view model; this only shapes.
export function shapeSessionList(sessions, opts) {
  const o = opts || {};
  const now = o.nowMs === undefined ? Date.now() : o.nowMs;
  const q = String(o.query || '').trim().toLowerCase();
  const space = o.space || null;
  const owner = o.owner ? String(o.owner).trim().toLowerCase() : null;
  const rows = trimSessions(sessions).filter((r) => {
    if (!owner || r.owner !== owner) return false;
    if (space === SUPPORT_SPACE_KEY) {
      if (!supportStatus(r)) return false;
      if (o.status && supportStatus(r) !== o.status) return false;
    } else if (space) {
      const builtin = spaceKeyOf(r.domain) === space && r.kind === 'chat' && !supportStatus(r);
      if (!builtin && r.spaceId !== space) return false;
    }
    if (!q) return true;
    if ((r.title || '').toLowerCase().indexOf(q) > -1) return true;
    return (r.turns || []).some((m) => m && m.role === 'you' && String(m.text || '').toLowerCase().indexOf(q) > -1);
  }).map((r) => ({
    id: r.id,
    kind: r.kind,
    title: r.title,
    when: ago(r.at, now),
    at: r.at,
    page: r.page,
    domain: r.kind === 'task' ? 'Task' : supportStatus(r) ? 'Support' : (r.domain || 'Orchestrator'),
    icon: sessionIcon(r),
    turns: questionCount(r),
    spaceId: r.spaceId || null,
    status: supportStatus(r),
    ref: supportStatus(r) ? supportRef(r.id) : '',
    emailed: !!(r.support && r.support.emailedAt)
  }));
  const groups = [];
  rows.forEach((row) => {
    const day = dayLabel(row.at, now);
    let g = groups[groups.length - 1];
    if (!g || g.day !== day) { g = { day: day, rows: [] }; groups.push(g); }
    g.rows.push(row);
  });
  return groups;
}

// ── controller ───────────────────────────────────────────────────────────────
// The page a task was raised on (its `page` label, from ctxLabel()) → the view to show it beside.
const TASK_PAGE_VIEW = { Home: 'home', Compliance: 'cc', Vendors: 'vp', Buildings: 'buildings' };

export const sessionsMethods = {
  // The first question of a conversation makes the record; follow-ups find it by the
  // orchestrator session id. Returns the id the turn should run under.
  sessionEnsure(q) {
    const s = this.state;
    const list = s.sessions || [];
    if (s.sessionId && list.some((x) => x.id === s.sessionId)) return s.sessionId;
    const id = s.sessionId || newSessionId();
    // Support pressed and this is its first question: the conversation is a support request,
    // from the page Support was pressed on (logic/support.js). Only on the conversation page —
    // left for another page, the question asked there is not support.
    const asSupport = !!s.supportPending && s.view === 'chat';
    const at = Date.now();
    const rec = makeSession({
      id: id, title: q, page: asSupport && s.supFrom ? s.supFrom : this.ctxLabel(), at: at, owner: s.account && s.account.email, viewOrgId: s.viewOrgId || null,
      support: asSupport ? { status: 'open', openedAt: at, from: s.supFrom || '', topic: s.supAskedTopic || '' } : null
    });
    // Asked from inside a saved space: the session is filed there from the start.
    if (s.view === 'space' && s.spaceKey && BUILTIN_SPACE_KEYS.indexOf(s.spaceKey) < 0) rec.spaceId = s.spaceKey;
    // A run opened before any question was asked — from the recent list, or a Buildings
    // card — is adopted by the conversation the first question creates: that is the
    // conversation it is being answered in.
    if (s.mgId) rec.migrationId = s.mgId;
    this.setState((p) => ({ sessionId: id, supportPending: false, supAskedTopic: '', sessions: trimSessions([rec].concat((p.sessions || []).filter((x) => x.id !== id))) }));
    return id;
  },

  // Stamps the run on the active conversation's record (see makeSession's migrationId).
  // No record yet — the first question has not been asked — means sessionEnsure adopts it.
  sessionBindMigration(id) {
    const sid = this.state.sessionId;
    if (!sid || !id) return;
    this.setState((p) => ({
      sessions: (p.sessions || []).map((x) => (x.id === sid && x.migrationId !== id ? Object.assign({}, x, { migrationId: id }) : x))
    }));
  },

  // Mirrors the transcript into the active record. Called from the controller's setState
  // whenever ccChat changes, so every path that touches the transcript is covered once.
  sessionSync() {
    const s = this.state;
    const id = s.sessionId;
    if (!id) return;
    const list = s.sessions || [];
    const i = list.findIndex((x) => x.id === id);
    if (i < 0) return;
    const rec = list[i];
    const turns = s.ccChat || [];
    // Restoring a session puts its own turns back into ccChat; that is not activity.
    if (rec.turns === turns) return;
    const next = syncTurns(rec, turns, Date.now());
    const rest = list.slice(); rest.splice(i, 1);
    this.setState({ sessions: trimSessions([next].concat(rest)) });
  },

  // The server's list for this account and company, folded into the browser's (see the
  // header). Quiet on failure: the browser's own cache still lists what it saw.
  // One read at a time per account and company: opening the page, a space's "back" and a delete
  // each asked for the whole history, and quick navigation ran several loops over it at once.
  sessionsSyncFromServer() {
    const s = this.state;
    const key = (s.account && s.account.email) + '|' + (s.viewOrgId || '');
    if (this._sessSync && this._sessSync.key === key) return this._sessSync.run;
    const run = this._sessSyncRun().finally(() => { if (this._sessSync && this._sessSync.run === run) this._sessSync = null; });
    this._sessSync = { key: key, run: run };
    return run;
  },

  async _sessSyncRun() {
    const s = this.state;
    const owner = s.account && s.account.email;
    if (!s.signedIn || !owner) return;
    const scope = s.viewOrgId || null;
    // Every page of the server's list, newest first, each one read from where the last ended
    // (inclusive: the boundary rows come again and are dropped). A server that ignores the cursor
    // sends the same page again: nothing new, so it stops - without claiming the whole history.
    const threads = [];
    const seen = new Set();
    let before = null;
    // Whole only when the server ran out of rows: a failed page or the page cap is a part.
    let complete = false;
    for (let page = 0; page < 10; page++) {
      let out = null;
      try { out = await deepAgentsApi.threads(null, before); } catch (e) { if (!page) return; break; }
      const rows = out && Array.isArray(out.threads) ? out.threads : null;
      if (!rows) { if (!page) return; break; }
      const fresh = rows.filter((t) => t && t.id && !seen.has(t.id));
      fresh.forEach((t) => { seen.add(t.id); threads.push(t); });
      const last = rows[rows.length - 1];
      if (rows.length < THREAD_PAGE) { complete = true; break; }
      if (!fresh.length || !last || !last.last_message_at) break;
      before = last.last_message_at;
    }
    this.setState({ sessionsServerCount: threads.length, sessionsServerComplete: complete });
    // The account or scope may have changed while the read was out: stamp with what was asked for.
    this.setState((p) => ({ sessions: mergeServerThreads(p.sessions || [], threads, { owner: owner, viewOrgId: scope, skip: this._sessDeleted || null }) }));
  },

  // A thread this browser never saw (or whose transcript was shed to make room): its turns
  // come from the server once, then it is a record like any other.
  async sessionHydrate(id) {
    let out = null;
    try { out = await deepAgentsApi.thread(id); } catch (e) { return; }
    const thread = out && out.thread;
    if (!thread) return;
    let turns = turnsFromThread(thread);
    // The runs behind the answers, from Hoist Traces, so the rail and "Was this right?" work on
    // a reopened chat. Best effort: the thread shows either way.
    try {
      const tr = await deepAgentsApi.traceTurns({ session_id: id, limit: 100 });
      turns = attachTurns(turns, (tr && tr.turns) || []);
    } catch (e) { /* the thread without its runs */ }
    this.setState((p) => {
      let hydrated = null;
      const list = (p.sessions || []).map((x) => (x.id === id ? (hydrated = Object.assign(syncTurns(x, turns, x.at), { remote: false, title: x.title || thread.title || '' })) : x));
      // Still the open conversation and nothing typed since: show what came back - as the
      // record's own array. A copy reads to sessionSync as new activity: it re-stamped the
      // conversation "just now" and moved it to the top of the history (8 Oct 2026 review).
      const patch = { sessions: list };
      if (p.sessionId === id && !(p.ccChat || []).length) {
        if (hydrated) turns = hydrated.turns;
        patch.ccChat = turns;
        const last = turns.length - 1;
        if (last >= 0 && turns[last].turnId) {
          patch.ccTraceIdx = last;
          if (typeof this.crLoadRun === 'function') setTimeout(() => this.crLoadRun(turns[last].turnId), 0);
        }
      }
      return patch;
    });
  },

  openSession(id) {
    const rec = (this.state.sessions || []).find((x) => x.id === id);
    if (!rec) return;
    if (rec.kind === 'task') {
      clearInterval(this._orchTick);
      // A task lives in the side dock beside a page. The chat page is the conversation itself,
      // so a task opened from there goes back to the page it was raised on — never a dock
      // beside the chat.
      const patch = { orchOpen: true, orchTask: rec, orchDone: (rec.steps || []).length, navOpen: true, flow: null, flowDone: '' };
      if (this.state.view === 'chat') {
        patch.view = TASK_PAGE_VIEW[rec.page] || TASK_PAGE_VIEW[rec.ctx] || 'home';
        if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
      }
      this.setState(patch);
      return;
    }
    // Reopening the session that is already active — including one still streaming, after
    // navigating away to another page and back — just returns to the chat page as it
    // stands. It must NOT fall into the reset below: that would swap the live transcript
    // for the stale snapshot still on the record and drop the in-flight stream.
    if (id === this.state.sessionId) {
      // …unless its transcript was shed (the store ran short, or 60 newer records pushed it out):
      // then it is read back, or it would stay empty for good.
      if (!(this.state.ccChat || []).length && !(rec.turns || []).length && (rec.remote || rec.serverTurns)) this.sessionHydrate(id);
      return this.openChat();
    }
    if (this.state.ccBusy) return this.flash('Still answering — stop it first, or wait for it to finish.');
    this.setState({
      sessionId: id, ccChat: rec.turns || [], ccTraceIdx: null, ccStepsOpen: {}, ccEditIdx: null, ccEditText: '',
      ccStream: null, flow: null, flowDone: '', supportPending: false, supNotYet: null
    });
    // The run this conversation started comes back with it; any other conversation's run
    // does not come along. openChat() below reads the run it finds set.
    if (rec.migrationId) { if (typeof this.mgAttach === 'function') this.mgAttach(rec.migrationId); }
    else if (typeof this.mgDetach === 'function') this.mgDetach();
    if (!(rec.turns || []).length && (rec.remote || rec.serverTurns)) this.sessionHydrate(id);
    this.openChat();
  },

  deleteSession(id) { return this.deleteSessions([id]); },

  // Gone from the list in one write, however many. Hidden on the server too, or the next read of
  // the history would bring them straight back; a task (an orchestrator run) was never a server
  // thread. Remembered for this tab, so a history read already in flight does not re-add them;
  // sent at most four at a time, so a bulk delete of hundreds does not time out its own tail
  // waiting for a connection; and a refusal is said - it used to be swallowed, and the session
  // quietly came back on the next read (8 Oct 2026 review).
  deleteSessions(ids) {
    const want = new Set((ids || []).filter(Boolean).map(String));
    if (!want.size) return;
    const s = this.state;
    const active = !!s.sessionId && want.has(String(s.sessionId));
    if (active && this._ccAbort) this._ccAbort.abort();
    // Its run's card goes with it; the run itself stays in the recent list.
    if (active && typeof this.mgDetach === 'function') this.mgDetach();
    const onServer = s.signedIn ? (s.sessions || []).filter((x) => x && want.has(String(x.id)) && x.kind !== 'task') : [];
    const gone = this._sessDeleted || (this._sessDeleted = new Set());
    want.forEach((id) => gone.add(id));
    this.setState((p) => Object.assign(
      { sessions: (p.sessions || []).filter((x) => !want.has(String(x.id))) },
      active ? { sessionId: null, ccChat: [], ccBusy: false, ccStream: null } : {}
    ));
    if (onServer.length) this._sessDeleteOnServer(onServer);
  },

  async _sessDeleteOnServer(recs) {
    const queue = recs.slice();
    const failed = [];
    const worker = async () => {
      while (queue.length) {
        const r = queue.shift();
        try { await deepAgentsApi.deleteThread(r.id); } catch (e) {
          // 404: the server never stored it, already deleted it, or it is not this caller's -
          // none of which comes back on the next read, so it is gone (8 Oct 2026 review).
          if (e && e.status === 404) continue;
          failed.push(r);
          // Still on the server, so the next read lists it again - and says why it is back.
          if (this._sessDeleted) this._sessDeleted.delete(String(r.id));
        }
      }
    };
    await Promise.all(Array.from({ length: Math.min(4, recs.length) }, worker));
    if (!failed.length) return;
    this.flash(failed.length === 1
      ? '“' + (failed[0].title || 'A session') + '” could not be deleted on the server — it may come back the next time the history is read.'
      : failed.length + ' sessions could not be deleted on the server — they may come back the next time the history is read.');
  },

  // Files a session in a space — a saved space's id or a built-in space's key; null unfiles it.
  // Any session: a conversation or an orchestrator task (7 Oct 2026). Client-side: svc-udr has
  // no route for saved_space_item, so the membership lives with the record.
  fileSession(id, spaceId) { return this.fileSessions([id], spaceId); },
  fileSessions(ids, spaceId) {
    const want = new Set((ids || []).map(String));
    if (!want.size) return;
    this.setState((p) => ({ sessions: (p.sessions || []).map((x) => (want.has(String(x.id)) ? Object.assign({}, x, { spaceId: spaceId || null }) : x)) }));
  },

  openSessions(filter) {
    this.setState((p) => ({
      view: 'sessions', sessionsFilter: filter === undefined ? (p.sessionsFilter || null) : filter,
      navOpen: true, detail: null, queueOpen: false, paletteOpen: false
    }));
    // The whole history, fresh: a conversation asked on another device since sign-in shows too.
    this.sessionsSyncFromServer();
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  },

  // A fresh thread: the next question starts a new session on the server too.
  // "+ New query": drops any conversation in progress and returns to the home ask bar —
  // the chat page only opens once a question is actually asked (askScoped → openChat).
  newQuery() {
    if (this._ccAbort) this._ccAbort.abort();
    clearInterval(this._orchTick);
    // The migration card belongs to the conversation being left, same as the transcript.
    // Without this the next question opened with the previous ingestion's run still
    // running beneath it. The run stays in the recent list and on that conversation's
    // record, so reopening the conversation brings it back.
    if (typeof this.mgDetach === 'function') this.mgDetach();
    this.setState({
      sessionId: null, ccChat: [], ccBusy: false, ccStream: null, ccTraceIdx: null, ccStepsOpen: {},
      view: 'home', query: '', detail: null, flow: null, flowDone: '', queueOpen: false, paletteOpen: false,
      orchOpen: false, orchTask: null, orchDone: 0, orchQuery: '',
      // The building an attachment was being filed against belongs to the conversation that
      // chose it. Carried into the next one it is how a document ends up filed in the wrong
      // place — the reader would have no reason to look at a control they never opened.
      cbBuildingId: null, cbBuildingName: '', cbPickerOpen: false, cbQuery: '', declForId: null, declFor: '',
      // A held document belongs to the conversation that uploaded it.
      ccCaseId: null, ccCaseDoc: '', ccCaseQuestion: '',
      // Support pressed and left unasked is dropped with it (logic/support.js).
      supportPending: false, supNotYet: null
    });
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  }
};
