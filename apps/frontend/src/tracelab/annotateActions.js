// annotateActions — everything the reviewer does on the Conversations tab to code an answer: read the open
// conversation's verdicts and comments, pick Right or Wrong, pin a note to a quote, save, retry, resolve a stale
// save, clear a verdict, comment, and move on to the next answer of a batch. Every request goes through
// reviewApi.js. State lives in the store's `review` slice, tagged with the store's seq (logic/annotate.js
// reviewOf): a company switch (resetForCompany bumps the seq) leaves the old slice unread. A reply issued before
// the switch is dropped and writes nothing, and the app's stale-company error stays quiet.
//
// What the reviewer has typed lives in `drafts`, keyed by answer. Reads (including the 30-second one) never touch
// a draft. A failed write keeps it with "Not saved: …" for Retry; a stale write (409) keeps it beside the current
// version for Keep mine / Use theirs. Whoever opens a conversation or an answer asks for its annotations
// (loadAnnotations); nothing here hooks into actions.js.
import { tlStore } from './store.js';
import { isStaleScope } from './api.js';
import { reviewApi } from './reviewApi.js';
import { openConversation, setTab } from './actions.js';
import { ANN_POLL_MS, anchorFromSelection, blankDraft, reviewOf, wrongProblem } from './logic/annotate.js';

const get = () => tlStore.get();
const R = () => reviewOf(get());
const setR = (patch) => tlStore.set((s) => {
  const r = reviewOf(s);
  return { review: Object.assign({}, r, typeof patch === 'function' ? patch(r) : patch) };
});
const live = (seq) => seq === get().seq;
const msg = (e) => (e && e.message) || String(e);
const has = (o, k) => !!o && Object.prototype.hasOwnProperty.call(o, k);
const isStale409 = (e) => !!e && e.status === 409 && e.reason === 'stale';
const currentOf = (e) => {
  const detail = e && e.body && e.body.detail;
  return (detail && typeof detail === 'object' && detail.current) || null;
};
const asks = { ann: 0, queue: 0 };

const patchDraft = (turnId, patch) => setR((r) => ({ drafts: Object.assign({}, r.drafts, { [turnId]: Object.assign(blankDraft(), (r.drafts || {})[turnId], patch) }) }));
const withoutDraft = (r, turnId) => { const drafts = Object.assign({}, r.drafts); delete drafts[turnId]; return drafts; };
const withMine = (r, turnId, mine) => Object.assign({}, r.ann, { [turnId]: Object.assign({}, (r.ann || {})[turnId], { mine }) });

// ── reading ─────────────────────────────────────────────────────────────────────────────────────────────────
// The open conversation's verdict states and comments, and the text of the active answer's blocks. Merges into
// what is on screen when it is the same conversation, starts over for another. Never touches `drafts`. A reply
// that lands after the company changed, or after a newer read was issued, is dropped.
export async function loadAnnotations(opts) {
  const s = get();
  const key = (opts && opts.key) || s.convKey;
  const turnId = (opts && opts.turnId) || (key === s.convKey ? s.turnId : null);
  if (!key) return;
  const seq = s.seq;
  const ask = (asks.ann += 1);
  setR({ loading: true, error: '' });
  try {
    const out = await reviewApi.annotations({ conversation_key: key, turn_id: turnId || undefined });
    if (!live(seq) || ask !== asks.ann) return;
    setR((p) => ({
      loading: false, loadedAt: Date.now(), key,
      ann: Object.assign({}, p.key === key ? p.ann : {}, (out && out.turns) || {}),
      blocks: Object.assign({}, p.key === key ? p.blocks : {}, (out && out.blocks) || {}),
      comments: (out && out.comments) || [], commentsHidden: (out && out.comments_hidden) || 0
    }));
  } catch (e) {
    if (isStaleScope(e) || !live(seq) || ask !== asks.ann) return;
    setR({ loading: false, error: msg(e) });
  }
  ensureAnnotationPoll();
}

// Every 30 s, while Conversations is the open tab with a conversation in it and the page is visible: read it again.
let timer = null;
export function ensureAnnotationPoll() {
  if (timer || typeof setInterval !== 'function') return;
  timer = setInterval(() => {
    const s = get();
    const shown = typeof document === 'undefined' || document.visibilityState === 'visible';
    if (s.tab === 'conversations' && s.convKey && shown) loadAnnotations();
  }, ANN_POLL_MS);
  if (timer && timer.unref) timer.unref();
}

export function stopAnnotationPoll() {
  if (timer) clearInterval(timer);
  timer = null;
}

// ── the draft ───────────────────────────────────────────────────────────────────────────────────────────────
export function cancelDraft(turnId) {
  setR((r) => ({ drafts: withoutDraft(r, turnId), selError: '' }));
}

// A selection inside one block of the active answer (the screen passes blockView(...).block and the browser's
// selection). A selection that cannot be an anchor says why and leaves the draft alone.
export function selectFromBlock(turnId, block, sel) {
  const res = anchorFromSelection(block, sel);
  if (!res.ok) { setR({ selError: res.error }); return false; }
  setR({ selError: '' });
  selectQuote(turnId, res.anchor);
  return true;
}

export function selectQuote(turnId, anchor) {
  const d = (R().drafts || {})[turnId];
  patchDraft(turnId, { verdict: 'wrong', anchor, popover: true, error: '', note: d ? d.note : '' });
}

export function setNote(turnId, note) { patchDraft(turnId, { note }); }

// Right saves at once. Wrong opens the note, prefilled from my current Wrong verdict when I am editing it.
export function pickVerdict(turnId, verdict) {
  if (verdict === 'right') return saveVerdict(turnId, 'right');
  const r = R();
  const d = (r.drafts || {})[turnId];
  const mine = ((r.ann || {})[turnId] || {}).mine;
  const prior = !d && mine && !mine.tombstone && mine.verdict === 'wrong' ? { note: mine.note || '', anchor: mine.anchor ? Object.assign({}, mine.anchor) : null } : {};
  patchDraft(turnId, Object.assign({ verdict: 'wrong', error: '', focus: true }, prior));
  return Promise.resolve(false);
}

export function saveNote(turnId) { return saveVerdict(turnId, 'wrong'); }

// ── saving ──────────────────────────────────────────────────────────────────────────────────────────────────
// PUT the verdict with `base` = the head I last saw. true when saved. A second call while one is in flight does
// nothing (it would only collide with the first).
export async function saveVerdict(turnId, verdict, opts) {
  const r = R();
  const d = (r.drafts || {})[turnId] || blankDraft();
  if (d.saving) return false;
  const st = (r.ann || {})[turnId] || {};
  // A note typed on my saved Wrong verdict (no new selection, no reopening) starts a draft with no anchor of its own;
  // the bar shows the saved quote as pinned, so that is the quote it saves. A selection made since always wins, and a
  // saved Right or a cleared Wrong has no quote to lend.
  const lent = st.mine && !st.mine.tombstone && st.mine.verdict === 'wrong' ? st.mine.anchor : null;
  const a = d.anchor || lent || null;
  const body = verdict === 'right' ? { verdict: 'right', note: null, anchor: null }
    : { verdict: 'wrong', note: d.note || '', anchor: a ? { span_id: a.span_id || null, field: a.field, quote: a.quote, occurrence: a.occurrence || 1 } : null };
  if (verdict === 'wrong') {
    const problem = wrongProblem(body.note, body.anchor);
    if (problem) { patchDraft(turnId, { verdict: 'wrong', error: problem }); return false; }
  }
  body.base = has(opts, 'base') ? opts.base : (st.mine ? st.mine.id : null);
  const seq = get().seq;
  patchDraft(turnId, { verdict, saving: true, error: '', conflict: null, popover: false });
  try {
    const out = await reviewApi.saveVerdict(turnId, body);
    if (!live(seq)) return false;
    setR((p) => ({ drafts: withoutDraft(p, turnId), ann: withMine(p, turnId, (out && out.annotation) || null) }));
    loadAnnotations({ turnId });
    return true;
  } catch (e) {
    if (!live(seq)) return false;
    if (isStaleScope(e)) { patchDraft(turnId, { saving: false }); return false; }
    if (isStale409(e)) { patchDraft(turnId, { saving: false, conflict: { current: currentOf(e), action: 'save' } }); return false; }
    patchDraft(turnId, { saving: false, error: 'Not saved: ' + msg(e) });
    return false;
  }
}

export function retryVerdict(turnId) {
  const d = (R().drafts || {})[turnId];
  return d && d.verdict ? saveVerdict(turnId, d.verdict) : Promise.resolve(false);
}

// After a 409: Keep mine repeats what I was doing (save the draft, or clear the verdict) on top of the current
// version; Use theirs drops my draft and takes theirs.
export function resolveConflict(turnId, keep) {
  const d = (R().drafts || {})[turnId];
  if (!d || !d.conflict) return Promise.resolve(false);
  const current = d.conflict.current;
  const base = current ? current.id : null;
  if (keep) return d.conflict.action === 'delete' ? deleteVerdict(turnId, { base }) : saveVerdict(turnId, d.verdict, { base });
  setR((p) => ({ drafts: withoutDraft(p, turnId), ann: withMine(p, turnId, current) }));
  loadAnnotations({ turnId });
  return Promise.resolve(true);
}

// Clear my verdict (the service keeps it as a cleared version). true when cleared.
export async function deleteVerdict(turnId, opts) {
  const r = R();
  const mine = ((r.ann || {})[turnId] || {}).mine;
  const d = (r.drafts || {})[turnId];
  if (!mine || mine.tombstone || (d && d.saving)) return false;
  const seq = get().seq;
  patchDraft(turnId, { saving: true, error: '', conflict: null });
  try {
    const out = await reviewApi.deleteVerdict(turnId, has(opts, 'base') ? opts.base : mine.id);
    if (!live(seq)) return false;
    setR((p) => ({ drafts: withoutDraft(p, turnId), ann: withMine(p, turnId, (out && out.annotation) || null) }));
    loadAnnotations({ turnId });
    return true;
  } catch (e) {
    if (!live(seq)) return false;
    if (isStaleScope(e)) { patchDraft(turnId, { saving: false }); return false; }
    if (isStale409(e)) { patchDraft(turnId, { saving: false, verdict: mine.verdict, conflict: { current: currentOf(e), action: 'delete' } }); return false; }
    patchDraft(turnId, { saving: false, error: 'Not cleared: ' + msg(e) });
    return false;
  }
}

// Keyboard (the page's key handler already ignores inputs and modifiers): r = Right, w = Wrong. Only on the
// Conversations tab, on the open answer. true when the key was taken.
export function shortcut(key) {
  const s = get();
  if (!s.turnId || s.tab !== 'conversations') return false;
  if (key === 'r') { saveVerdict(s.turnId, 'right'); return true; }
  if (key === 'w') { pickVerdict(s.turnId, 'wrong'); return true; }
  return false;
}

// ── the queue ───────────────────────────────────────────────────────────────────────────────────────────────
export async function loadQueue(batchId) {
  const seq = get().seq;
  const ask = (asks.queue += 1);
  try {
    const out = await reviewApi.reviewQueue({ batch_id: batchId || undefined, limit: 20 });
    if (!live(seq) || ask !== asks.queue) return [];
    const items = (out && out.items) || [];
    setR({ queue: items, queueRemaining: out ? out.remaining : null, queueLoadedAt: Date.now() });
    return items;
  } catch (e) {
    if (!isStaleScope(e) && live(seq)) setR({ error: msg(e) });
    return [];
  }
}

// Save, then open the next answer of the same batch that still needs my verdict (any batch when this answer is in
// none). false when the save failed or nothing is waiting.
export async function saveAndNext(turnId, verdict) {
  const item = ((R().ann || {})[turnId] || {}).sample_item;
  const ok = await saveVerdict(turnId, verdict);
  if (!ok) return false;
  const next = (await loadQueue(item ? item.batch_id : undefined)).find((i) => i.turn_id !== turnId);
  if (next) openConversation(next.conversation_key, next.turn_id);
  return !!next;
}

// Start reviewing a batch from the Map: its first answer that still needs my verdict, on the Conversations tab.
// Save & next then walks the rest of the batch. false when nothing in it is waiting for me.
export async function reviewBatch(batchId) {
  const next = (await loadQueue(batchId))[0];
  if (!next) { setR({ error: 'Nothing in this batch is waiting for your verdict.' }); return false; }
  setTab('conversations');
  openConversation(next.conversation_key, next.turn_id);
  return true;
}

// ── comments ────────────────────────────────────────────────────────────────────────────────────────────────
export function setCommentDraft(text) { setR({ commentDraft: String(text || ''), commentError: '' }); }

// Comment on the open conversation (on the open answer when there is one). A failed comment keeps its text.
export async function addComment() {
  const s = get();
  const note = R().commentDraft || '';
  if (!note.trim() || !s.convKey) { setR({ commentError: 'Write the comment first.' }); return false; }
  const seq = s.seq;
  setR({ commentBusy: true, commentError: '' });
  try {
    const out = await reviewApi.addComment({ conversation_key: s.convKey, turn_id: s.turnId || null, note });
    if (!live(seq)) return false;
    const saved = out && out.annotation;
    setR((p) => ({ commentDraft: '', commentBusy: false, comments: saved ? (p.comments || []).concat([saved]) : p.comments,
      me: saved && saved.reviewer_id ? saved.reviewer_id : p.me }));
    return true;
  } catch (e) {
    if (!live(seq)) return false;
    setR({ commentBusy: false, commentError: isStaleScope(e) ? '' : 'Not saved: ' + msg(e) });
    return false;
  }
}

export async function editComment(lineageId, note, base) {
  const seq = get().seq;
  let ok = true;
  try { await reviewApi.editComment(lineageId, { note, base }); } catch (e) {
    ok = false;
    if (!isStaleScope(e) && live(seq)) setR({ commentError: (e && e.status === 409 ? 'Changed since you opened it: ' : 'Not saved: ') + msg(e) });
  }
  await loadAnnotations();
  return ok;
}

export async function deleteComment(lineageId, base) {
  const seq = get().seq;
  let ok = true;
  try { await reviewApi.deleteComment(lineageId, base); } catch (e) {
    ok = false;
    if (!isStaleScope(e) && live(seq)) setR({ commentError: 'Not removed: ' + msg(e) });
  }
  await loadAnnotations();
  return ok;
}
