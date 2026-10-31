// actions — everything the Trace Lab page does: read the open tab, page, filter, open a conversation or a model
// call, and the 45-second update check. Every read goes through api.js (so through the app's apiFetch) and only
// GETs: Phase 2 writes nothing. Each loader keeps what is on screen while it reads, drops a reply issued before a
// company switch (the store's seq, bumped by resetForCompany) or overtaken by a newer read of the same thing,
// and stays quiet on the app's stale-scope error. Nothing here ever clears `open` (the open cards and panels),
// `conv` or `call` except the reader's own action, so a refresh never closes what the reader has open.
import { tl, isStaleScope } from './api.js';
import { tlStore, FILTER_DEFAULTS } from './store.js';
import { TABS } from './nav.js';
import { CALLS_PAGE_SIZE, PAGE_SIZE, POLLED_TABS, isRunId, linkSearch, listQuery, sinceFor } from './logic/shell.js';
import { mergeByKey } from './logic/views.js';
import { loadGoldenRuns, loadSets, pollRun } from './goldenActions.js';

const get = () => tlStore.get();
const set = (patch) => tlStore.set(patch);
const msg = (e) => (e && e.message) || String(e);
const reasonOf = (e) => (e && typeof e.reason === 'string' ? e.reason : '');
const live = (seq) => seq === get().seq;
const patchOf = (key, part) => (p) => ({ [key]: Object.assign({}, p[key], part) });
const asks = { convs: 0, conv: 0, calls: 0, call: 0, runs: 0 };
const PAGING = (p) => ({
  convs: Object.assign({}, p.convs, { cursors: [null], page: 0 }),
  calls: Object.assign({}, p.calls, { cursors: [null], page: 0 }),
  poll: Object.assign({}, p.poll, { newTurns: 0 })
});

// ── the tab, the link and the address bar ──────────────────────────────────────────────────────────────────
export function applyLink(link) {
  if (!link) return;
  set({ tab: link.tab, run: link.run, convKey: link.conv, turnId: link.conv ? link.turn : null });
}

export function syncUrl() {
  try {
    const qs = linkSearch(window.location.search, get());
    if (qs !== (window.location.search || '')) window.history.replaceState(null, '', (window.location.pathname || '/') + qs);
  } catch (e) { /* no history API: the page still works, the link just is not kept */ }
}

export function scrubUrl() {
  try {
    const qs = linkSearch(window.location.search, null);
    if (qs !== (window.location.search || '')) window.history.replaceState(null, '', (window.location.pathname || '/') + qs);
  } catch (e) { /* nothing to scrub */ }
}

// The lists the open tab shows.
export function loadLists() {
  const tab = get().tab;
  if (tab === 'conversations') { loadConversations(); loadFacets(); loadRuns(); } else if (tab === 'runs') { loadRuns(); loadGoldenRuns(); } else if (tab === 'golden') loadSets();
  else if (tab === 'models') loadCalls();
}

// The lists, and on Conversations the conversation the link or the reader had open.
export function loadTab() {
  loadLists();
  const s = get();
  if (s.tab === 'conversations' && s.convKey) openConversation(s.convKey, s.turnId);
}

export function setTab(tab) {
  if (TABS.indexOf(tab) < 0 || tab === get().tab) return;
  set({ tab, call: null });
  loadTab();
}

export function setFilter(patch) {
  const f = Object.assign({}, FILTER_DEFAULTS, get().filters, patch || {});
  set((p) => Object.assign({ filters: f, qDraft: f.q }, PAGING(p)));
  loadLists();
}

export function setQueryDraft(text) { set({ qDraft: String(text || '').slice(0, 200) }); }

export function search() { setFilter({ q: String(get().qDraft || '').trim() }); }

export function setRun(runId) {
  set((p) => Object.assign({ run: isRunId(runId) ? String(runId) : 'live' }, PAGING(p)));
  loadLists();
}

// "Refresh now" (and "Retry"): page one of the open tab again.
export function refresh() {
  set(PAGING);
  loadLists();
}

// ── conversations ──────────────────────────────────────────────────────────────────────────────────────────
export async function loadConversations(opts) {
  const merge = !!(opts && opts.merge);
  const s = get();
  const page = merge ? 0 : s.convs.page;
  const cursor = s.convs.cursors[page] || null;
  const seq = s.seq;
  const ask = (asks.convs += 1);
  if (!merge) set(patchOf('convs', { loading: true, error: '', reason: '' }));
  try {
    const out = await tl.get('/conversations', Object.assign(listQuery(s), { limit: PAGE_SIZE }, cursor ? { cursor } : {}));
    if (!live(seq) || ask !== asks.convs) return;
    const rows = (out && out.conversations) || [];
    const next = (out && out.next_cursor) || null;
    set((p) => {
      const cursors = p.convs.cursors.slice(0, page + 1);
      if (next) cursors.push(next);
      return {
        convs: Object.assign({}, p.convs, {
          rows: merge ? mergeByKey(p.convs.rows, rows) : rows,
          total: out && typeof out.total === 'number' ? out.total : (merge ? p.convs.total : null),
          cursors, page, loading: false, error: '', reason: '', loadedAt: Date.now(),
          watermark: (out && out.watermark) || p.convs.watermark
        }),
        poll: merge ? p.poll : { updatedAt: Date.now(), newTurns: 0, error: '' }
      };
    });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.convs) return;
    if (merge) set(patchOf('poll', { error: msg(e) }));
    else set(patchOf('convs', { loading: false, error: msg(e), reason: reasonOf(e) }));
  }
}

export function pageConversations(delta) {
  const c = get().convs;
  const page = c.page + delta;
  if (page < 0 || (page > 0 && !c.cursors[page])) return;
  set(patchOf('convs', { page }));
  loadConversations();
}

export async function loadFacets() {
  const s = get();
  const seq = s.seq;
  try {
    const out = await tl.get('/conversations/facets', { since: sinceFor(s.filters.range), run: s.run || 'live' }, { timeoutMs: 15000 });
    if (!live(seq)) return;
    set({ facets: out || null });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    set({ facets: null });
  }
}

export async function openConversation(key, turnId) {
  if (!key) return;
  const s = get();
  const seq = s.seq;
  const ask = (asks.conv += 1);
  const same = !!(s.conv.data && s.conv.data.key === key);
  set((p) => Object.assign({ convKey: key, turnId: turnId || null, conv: Object.assign({}, p.conv, { loading: true, error: '', reason: '' }, same ? {} : { data: null }) },
    same ? {} : { open: {} }));
  try {
    const out = await tl.get('/conversations/' + encodeURIComponent(key), turnId ? { turn: turnId } : {});
    if (!live(seq) || ask !== asks.conv) return;
    const data = (out && out.conversation) || null;
    set({ conv: { data, loading: false, error: '', reason: '' }, turnId: data && data.active ? data.active.id : null });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.conv) return;
    set(patchOf('conv', { loading: false, error: msg(e), reason: reasonOf(e) }));
  }
}

export function openTurn(turnId) {
  const key = get().convKey;
  if (turnId && key) openConversation(key, turnId);
}

// From a model call's panel: the call names its conversation; open it on Conversations with that answer.
export function openInConversations(conversationKey, turnId) {
  if (!conversationKey) return;
  set({ tab: 'conversations', call: null });
  loadLists();
  openConversation(conversationKey, turnId || null);
}

// Which cards and panels are open lives in the store, so a refresh never closes one.
export function setOpen(id, open) {
  if (!id) return;
  set((p) => ({ open: Object.assign({}, p.open, { [id]: !!open }) }));
}

// ── runs ───────────────────────────────────────────────────────────────────────────────────────────────────
export async function loadRuns() {
  const s = get();
  const seq = s.seq;
  const ask = (asks.runs += 1);
  set(patchOf('runs', { loading: true, error: '', reason: '' }));
  try {
    const out = await tl.get('/runs', { since: sinceFor(s.filters.range) }, { timeoutMs: 15000 });
    if (!live(seq) || ask !== asks.runs) return;
    set({ runs: { rows: (out && out.runs) || [], goldenAvailable: !!(out && out.golden_available), loading: false, error: '', reason: '', loadedAt: Date.now() } });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.runs) return;
    set(patchOf('runs', { loading: false, error: msg(e), reason: reasonOf(e) }));
  }
}

// Picking a run on the Runs tab opens its conversations.
export function pickRun(runId) {
  set((p) => Object.assign({ tab: 'conversations', convKey: null, turnId: null, conv: { data: null, loading: false, error: '', reason: '' },
    run: isRunId(runId) ? String(runId) : 'live' }, PAGING(p)));
  loadLists();
}

// ── model calls ────────────────────────────────────────────────────────────────────────────────────────────
export async function loadCalls() {
  const s = get();
  const page = s.calls.page;
  const cursor = s.calls.cursors[page] || null;
  const seq = s.seq;
  const ask = (asks.calls += 1);
  set(patchOf('calls', { loading: true, error: '', reason: '' }));
  try {
    const out = await tl.get('/model-calls', Object.assign(listQuery(s), { limit: CALLS_PAGE_SIZE }, cursor ? { cursor } : {}));
    if (!live(seq) || ask !== asks.calls) return;
    const next = (out && out.next_cursor) || null;
    set((p) => {
      const cursors = p.calls.cursors.slice(0, page + 1);
      if (next) cursors.push(next);
      return { calls: { rows: (out && out.calls) || [], aggregate: (out && out.aggregate) || null,
        retentionDays: out && out.retention_days ? out.retention_days : null, cursors, page, loading: false, error: '', reason: '', loadedAt: Date.now() } };
    });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.calls) return;
    set(patchOf('calls', { loading: false, error: msg(e), reason: reasonOf(e) }));
  }
}

export function pageCalls(delta) {
  const c = get().calls;
  const page = c.page + delta;
  if (page < 0 || (page > 0 && !c.cursors[page])) return;
  set(patchOf('calls', { page }));
  loadCalls();
}

export async function openCall(spanId) {
  if (!spanId) return;
  const seq = get().seq;
  const ask = (asks.call += 1);
  set({ call: { spanId, loading: true, error: '', data: null, turnId: null, conversationKey: null } });
  try {
    const out = await tl.get('/model-calls/' + encodeURIComponent(spanId), {}, { timeoutMs: 15000 });
    if (!live(seq) || ask !== asks.call || !get().call) return;
    set({ call: { spanId, loading: false, error: '', data: (out && out.model_call) || null,
      turnId: (out && out.turn_id) || null, conversationKey: (out && out.conversation_key) || null } });
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.call || !get().call) return;
    set({ call: { spanId, loading: false, error: msg(e), data: null, turnId: null, conversationKey: null } });
  }
}

export function closeCall() { set({ call: null }); }

// ── the 45-second update check ─────────────────────────────────────────────────────────────────────────────
// Conversations: one cheap count since the list's watermark; news merges into page one by key. Runs: the runs
// list read again. Never while the tab is hidden (unless forced), never on Model calls, and never touching the
// open conversation, its cards and panels, or the model-call panel.
export async function poll(force) {
  const s = get();
  if (POLLED_TABS.indexOf(s.tab) < 0) return;
  const hidden = typeof document !== 'undefined' && document.visibilityState && document.visibilityState !== 'visible';
  if (hidden && !force) return;
  if (s.tab === 'runs') {
    await Promise.all([loadRuns(), loadGoldenRuns(), pollRun()]);
    if (live(s.seq) && !get().runs.error) set(patchOf('poll', { updatedAt: Date.now(), error: '' }));
    return;
  }
  if (!s.convs.watermark) return;
  const seq = s.seq;
  try {
    const out = await tl.get('/conversations/updates', Object.assign(listQuery(s), { watermark: s.convs.watermark }), { timeoutMs: 12000 });
    if (!live(seq)) return;
    const fresh = Number(out && out.new_turns) || 0;
    set({ poll: { updatedAt: Date.now(), newTurns: fresh, error: '' } });
    if (fresh && get().tab === 'conversations' && !(get().convs.page > 0)) await loadConversations({ merge: true });
  } catch (e) {
    if (isStaleScope(e) || !live(seq)) return;
    set(patchOf('poll', { error: msg(e) }));
  }
}

// ── golden runs (Phase 3) ──────────────────────────────────────────────────────────────────────────────────────
// From a golden run's detail or a comparison: that case's conversation on Conversations, filtered to the run, on
// the answer named (a golden session is `golden-<run>-<case>-r<rep>`; its conversation key is `s:` + session id).
export function openGoldenAnswer(runId, sessionId, turnId) {
  if (!sessionId) return;
  pickRun(runId);
  openConversation('s:' + sessionId, turnId || null);
}
