// sessions — every conversation with the orchestrator, kept so the navigator can reopen it.
//
// Not to be confused with session.js, which is the reload slice (signed in, current view).
// A *session* here is what the Plenum AI shell calls one: a thread with svc-deepagents, keyed
// by the `session_id` the server keeps its LangGraph state under. The server has no route
// that lists threads, so — like the Plenum shell — the list and the transcripts live in this
// browser's localStorage. Reopening a session restores its transcript and continues the same
// server thread; the orchestrator sees the earlier turns through its checkpointer.
//
// Orchestrator tasks (`orch()` in core.js) are sessions too, of kind "task": they reopen the
// dock on the step chain they played.
//
// The pure functions are tested in test/sessions.test.mjs; the methods at the bottom are mixed
// into HoistraLogic.prototype and `this` is the controller.
import { domainOf } from './chat.js';
import { dayLabel } from './homeLive.js';

export const SESSIONS_KEY = 'hoistra.sessions.v1';
export const MAX_SESSIONS = 60;
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
    spaceId: null
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
  return (list || []).filter(Boolean).slice().sort((a, b) => (b.at || 0) - (a.at || 0)).slice(0, MAX_SESSIONS);
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
    const rec = makeSession({ id: r.id, title: r.title, page: r.page, kind: r.kind, at: Number(r.at) || Date.now(), task: r.task, ctx: r.ctx, steps: r.steps });
    rec.createdAt = Number(r.createdAt) || rec.at;
    rec.turns = Array.isArray(r.turns) ? r.turns.filter((m) => m && typeof m === 'object' && typeof m.role === 'string') : [];
    rec.calls = Array.isArray(r.calls) ? r.calls.filter((x) => typeof x === 'string') : [];
    rec.spaceId = typeof r.spaceId === 'string' && r.spaceId ? r.spaceId : null;
    rec.domain = typeof r.domain === 'string' && r.domain ? r.domain : sessionDomain(rec);
    out.push(rec);
  });
  return trimSessions(out);
}

// Shrinks in stages when the browser refuses the size: traces go first (they are the bulk of
// a turn), then the transcripts of everything but the newest ten, then the oldest sessions.
const shrink = (list, stage) => {
  if (stage === 0) return list.map((r, i) => (i < 5 ? r : Object.assign({}, r, {
    turns: (r.turns || []).map((m) => { if (!m || !m.trace) return m; const c = Object.assign({}, m); delete c.trace; return c; })
  })));
  if (stage === 1) return list.map((r, i) => (i < 10 ? r : Object.assign({}, r, { turns: [] })));
  if (stage === 2) return list.slice(0, 20);
  return list.slice(0, 5).map((r) => Object.assign({}, r, { turns: [] }));
};

export function saveSessions(list, storage) {
  const st = storage || store();
  if (!st) return false;
  let l = trimSessions(list);
  for (let stage = 0; stage <= 4; stage++) {
    try {
      st.setItem(SESSIONS_KEY, JSON.stringify(l));
      return true;
    } catch (e) {
      if (stage === 4) return false;
      l = shrink(l, stage);
    }
  }
  return false;
}

// ── the list ─────────────────────────────────────────────────────────────────
// Grouped by day, newest first. `space` narrows to a built-in key (matched on the engine)
// or a saved space id (matched on where the session was filed). Handlers are added by the
// view model; this only shapes.
export function shapeSessionList(sessions, opts) {
  const o = opts || {};
  const now = o.nowMs === undefined ? Date.now() : o.nowMs;
  const q = String(o.query || '').trim().toLowerCase();
  const space = o.space || null;
  const rows = trimSessions(sessions).filter((r) => {
    if (space) {
      const builtin = spaceKeyOf(r.domain) === space && r.kind === 'chat';
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
    domain: r.kind === 'task' ? 'Task' : (r.domain || 'Orchestrator'),
    icon: sessionIcon(r),
    turns: r.kind === 'task' ? 0 : (r.turns || []).filter((m) => m && m.role === 'you').length,
    spaceId: r.spaceId || null
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
    const rec = makeSession({ id: id, title: q, page: this.ctxLabel(), at: Date.now() });
    // Asked from inside a saved space: the session is filed there from the start.
    if (s.view === 'space' && s.spaceKey && BUILTIN_SPACE_KEYS.indexOf(s.spaceKey) < 0) rec.spaceId = s.spaceKey;
    this.setState((p) => ({ sessionId: id, sessions: trimSessions([rec].concat((p.sessions || []).filter((x) => x.id !== id))) }));
    return id;
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
    if (this.state.ccBusy) return this.flash('Still answering — stop it first, or wait for it to finish.');
    this.setState({
      sessionId: id, ccChat: rec.turns || [], ccTraceIdx: null, ccStepsOpen: {}, ccEditIdx: null, ccEditText: '',
      ccStream: null, flow: null, flowDone: ''
    });
    this.openChat();
  },

  deleteSession(id) {
    const active = this.state.sessionId === id;
    if (active && this._ccAbort) this._ccAbort.abort();
    this.setState((p) => Object.assign(
      { sessions: (p.sessions || []).filter((x) => x.id !== id) },
      active ? { sessionId: null, ccChat: [], ccBusy: false, ccStream: null } : {}
    ));
  },

  // Files a session in a saved space (null unfiles it). Client-side: svc-udr has no route
  // for saved_space_item, so the membership lives with the record.
  fileSession(id, spaceId) {
    this.setState((p) => ({ sessions: (p.sessions || []).map((x) => (x.id === id ? Object.assign({}, x, { spaceId: spaceId || null }) : x)) }));
  },

  openSessions(filter) {
    this.setState((p) => ({
      view: 'sessions', sessionsFilter: filter === undefined ? (p.sessionsFilter || null) : filter,
      navOpen: true, detail: null, queueOpen: false, paletteOpen: false
    }));
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  },

  // A fresh thread: the next question starts a new session on the server too.
  // "+ New query": a fresh thread on the chat page — the main orchestrator — with its
  // composer ready and the side dock closed. The dock is for asking beside a page; a new
  // query is the page.
  newQuery() {
    if (this._ccAbort) this._ccAbort.abort();
    clearInterval(this._orchTick);
    this.setState({
      sessionId: null, ccChat: [], ccBusy: false, ccStream: null, ccTraceIdx: null, ccStepsOpen: {},
      view: 'chat', query: '', detail: null, flow: null, flowDone: '', queueOpen: false, paletteOpen: false,
      orchOpen: false, orchTask: null, orchDone: 0, orchQuery: ''
    });
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    if (typeof this.chatConnect === 'function') this.chatConnect();
    if (typeof document === 'undefined') return;
    setTimeout(() => { const el = document.getElementById('chat-composer'); if (el) el.focus(); }, 80);
  }
};
