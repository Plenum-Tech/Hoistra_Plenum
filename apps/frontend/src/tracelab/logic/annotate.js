// annotate — pure helpers and view models for open coding inside Trace Lab's Conversations tab: a reviewer's
// verdict on one answer (Right, or Wrong with a note on the first failure pinned to a verbatim quote), comments
// on the conversation, and the margin notes beside the timeline. No requests and no state of its own: the
// screens draw what these return and call annotateActions.js.
//
// The service decides what may be shown. Other reviewers' verdicts arrive as null until the caller has saved one
// (blind review), and every annotatable block's text arrives with the annotations as `blocks`, so a selection is
// always a substring of the text the service checks. The reviewer's unsaved work lives in `drafts`, keyed by
// turn; reads never touch it. The state is the `review` slice of the store. The store merges patches and
// resetForCompany() only resets the keys store.js lists, so the slice carries the store's `seq` it was made
// under (reviewInitial(seq)); reviewOf(s) hands back a slice from an earlier seq, or none, as an untouched one.
// A company switch therefore empties it without store.js having to know it exists.
import { labelFor } from './timeline.js';

export const NO_FAILURE_NOTE = 'no failure observed';
export const NOTE_MAX = 2000;
export const ANN_POLL_MS = 30000;
const TURN_FIELDS = ['question', 'answer'];
const FIELDS = ['question', 'answer', 'input', 'output'];
const NOTE_HEIGHT = (note) => 46 + 16 * Math.ceil(Math.min(400, (note || '').length) / 34);
const clip = (s, n) => { const t = String(s || ''); return t.length > n ? t.slice(0, n - 1) + '…' : t; };

// ── pure helpers ─────────────────────────────────────────────────────────────────────────────────────────────

// '<span id>:<field>' for a step, ':question' / ':answer' for the turn's own text (the service's block key).
export function blockKey(anchor) {
  if (!anchor || FIELDS.indexOf(anchor.field) < 0) return null;
  return (TURN_FIELDS.indexOf(anchor.field) >= 0 ? '' : (anchor.span_id || '')) + ':' + anchor.field;
}

// Start of the n-th (1-based) occurrence, overlapping matches counted: the service's numbering.
export function findOccurrence(text, quote, occurrence) {
  if (!quote || !(occurrence >= 1)) return -1;
  let pos = -1;
  for (let i = 0; i < occurrence; i += 1) {
    pos = String(text).indexOf(quote, pos + 1);
    if (pos < 0) return -1;
  }
  return pos;
}

// Which occurrence of `quote` starts at `start` (1-based), or 0 when none does.
export function occurrenceAt(text, quote, start) {
  if (!quote) return 0;
  let pos = -1;
  for (let n = 1; ; n += 1) {
    pos = String(text).indexOf(quote, pos + 1);
    if (pos < 0 || pos > start) return 0;
    if (pos === start) return n;
  }
}

// A browser selection as an anchor, or a sentence saying why not. `block` = {key, span_id, field, text};
// startKey/endKey = the data-tl-block of each end of the selection; start/end = offsets in block.text.
export function anchorFromSelection(block, sel) {
  if (!block || !sel || sel.startKey !== block.key || sel.endKey !== block.key) return { ok: false, error: 'Select text inside one step.' };
  const start = Math.min(sel.start, sel.end);
  const end = Math.max(sel.start, sel.end);
  const quote = String(block.text).slice(start, end);
  if (!quote.trim()) return { ok: false, error: 'Select some text first.' };
  if (quote.length > NOTE_MAX) return { ok: false, error: 'Select at most 2,000 characters.' };
  const occurrence = occurrenceAt(block.text, quote, start);
  if (!occurrence) return { ok: false, error: 'Select text inside one step.' };
  const turnText = TURN_FIELDS.indexOf(block.field) >= 0;
  return { ok: true, anchor: { span_id: turnText ? null : block.span_id, field: block.field, quote, occurrence, key: block.key } };
}

// The text split at every highlight boundary. Overlapping highlights share a segment that carries both ids, so
// no note loses its highlight. items: [{id, start, end}]; one with start < 0 (quote not found) is left out.
export function highlightSegments(text, items) {
  const t = String(text || '');
  const spans = (items || []).filter((i) => i.start >= 0 && i.end > i.start && i.start < t.length);
  const cuts = new Set([0, t.length]);
  spans.forEach((i) => { cuts.add(i.start); cuts.add(Math.min(i.end, t.length)); });
  const points = Array.from(cuts).sort((a, b) => a - b);
  const out = [];
  for (let k = 0; k < points.length - 1; k += 1) {
    const a = points[k];
    const b = points[k + 1];
    if (b <= a) continue;
    const ids = spans.filter((i) => i.start <= a && i.end >= b).map((i) => i.id);
    out.push({ start: a, end: b, text: t.slice(a, b), ids });
  }
  return out;
}

// Margin placement: notes sorted by the top of their highlight, stacked with at least `gap` px; a note that would
// land more than `maxDrift` px below its highlight joins a "+k more notes here" stub on the last placed note,
// unless that note's group is expanded. items: [{id, top, height}].
export function placeMarginNotes(items, opts) {
  const o = opts || {};
  const gap = o.gap === undefined ? 8 : o.gap;
  const maxDrift = o.maxDrift === undefined ? 240 : o.maxDrift;
  const expanded = new Set(o.expanded || []);
  const sorted = (items || []).slice().sort((a, b) => (a.top - b.top) || String(a.id).localeCompare(String(b.id)));
  const placed = [];
  let cursor = -Infinity;
  let last = null;
  sorted.forEach((it) => {
    const top = Math.max(it.top, cursor);
    if (last && top - it.top > maxDrift && !expanded.has(last.id)) { last.more.push(it.id); return; }
    const p = { id: it.id, top, height: it.height, anchorTop: it.top, more: [] };
    placed.push(p);
    last = p;
    cursor = top + it.height + gap;
  });
  return placed;
}

// Notes whose quote is not in the text on screen (a purged step, a payload cut at 16,000 characters, changed
// text): listed at the end of the turn with the copied quote and step name.
export function unplacedNotes(notes, blocks) {
  return (notes || []).filter((n) => {
    const text = blocks ? blocks[n.blockKey] : undefined;
    return text === undefined || text === null || findOccurrence(text, n.quote, n.occurrence) < 0;
  });
}

// The first problem with a Wrong verdict, or ''.
export function wrongProblem(note, anchor) {
  if (!anchor || !anchor.quote) return 'Select the text where it first went wrong.';
  if (!note || !String(note).trim()) return 'Say what went wrong first.';
  if (String(note).length > NOTE_MAX) return 'Keep the note under 2,000 characters.';
  return '';
}

// A reviewer's unsaved work on one answer.
export const blankDraft = () => ({ verdict: null, note: '', anchor: null, saving: false, error: '', conflict: null, popover: false, focus: false });

// The `review` slice of the store, at its default, made under the store's `seq`. A fresh object every call.
export function reviewInitial(seq) {
  return {
    seq: seq || 0,
    ann: {},               // turn id -> { mine, others, others_count, others_hidden, reviews_required, sample_item }
    blocks: {},            // turn id -> { block key -> the text the service checks a quote against }
    comments: [], commentsHidden: 0,
    key: null,             // the conversation the three above belong to
    loading: false, loadedAt: null, error: '', selError: '',
    drafts: {},            // turn id -> blankDraft()
    commentDraft: '', commentError: '', commentBusy: false,
    me: null,              // my id, learned from a comment I wrote
    queue: [], queueRemaining: null, queueLoadedAt: null
  };
}

// ── view models ──────────────────────────────────────────────────────────────────────────────────────────────

// The slice in force for this state: the one made under the store's current seq, else an untouched one.
export function reviewOf(s) {
  const seq = (s && s.seq) || 0;
  return s && s.review && s.review.seq === seq ? s.review : reviewInitial(seq);
}
const rv = reviewOf;

// My own id, which comment rows carry as reviewer_id: the one learned from a comment I wrote, else the one on my
// own verdict (a cleared verdict is still mine), else, when the service is withholding other reviewers' work from
// me (some answer's `others` is null), the comment list is mine alone. null when none of those applies.
export function knownMe(r) {
  if (r.me) return r.me;
  const turns = Object.keys(r.ann || {}).map((k) => r.ann[k]).filter(Boolean);
  const mine = turns.find((t) => t.mine && t.mine.reviewer_id);
  if (mine) return mine.mine.reviewer_id;
  const first = (r.comments || [])[0];
  return turns.some((t) => t.others === null) && first && first.reviewer_id ? first.reviewer_id : null;
}

function noteView(a, me) {
  if (!a || a.tombstone) return null;
  return { id: a.id, who: me ? 'You' : (a.reviewer_email || 'Another reviewer'), verdict: a.verdict, mark: a.verdict === 'right' ? '✓ Right' : '✗ Wrong',
    note: a.verdict === 'wrong' ? a.note : '', quote: a.anchor ? clip(a.anchor.quote, 80) : '' };
}

// The step a note is pinned to, in plain words (a step's name goes through the timeline's label map).
function stepLabel(anchor) {
  if (!anchor) return '';
  if (anchor.span_name === 'turn' || !anchor.span_name) return TURN_FIELDS.indexOf(anchor.field) >= 0 ? 'the ' + anchor.field : '';
  return labelFor(anchor.span_kind, anchor.span_name);
}

// The wrong verdicts with a quote on one answer: mine first, then those other reviewers' that are visible.
function wrongNotes(st) {
  if (!st) return [];
  const all = [];
  if (st.mine && !st.mine.tombstone && st.mine.verdict === 'wrong' && st.mine.anchor) all.push(Object.assign({ me: true }, st.mine));
  (st.others || []).forEach((o) => { if (o.verdict === 'wrong' && o.anchor) all.push(Object.assign({ me: false }, o)); });
  return all.map((a) => ({ id: a.id, me: a.me, who: a.me ? 'You' : (a.reviewer_email || 'Another reviewer'), note: a.note, quote: a.anchor.quote,
    clipped: clip(a.anchor.quote, 80), occurrence: a.anchor.occurrence || 1, blockKey: blockKey(a.anchor), step: stepLabel(a.anchor),
    height: NOTE_HEIGHT(a.note) }));
}

const openState = (s) => {
  const r = rv(s);
  const tid = s.turnId || null;
  return { r, tid, st: tid ? (r.ann || {})[tid] || null : null, d: tid ? (r.drafts || {})[tid] || null : null,
    blocks: tid ? (r.blocks || {})[tid] || null : null };
};

export function noticesView(s) {
  const r = rv(s);
  return { error: r.error || '', selectionError: r.selError || '' };
}

// The verdict bar for the open answer, or null when no answer is open.
export function reviewView(s) {
  const { tid, st, d } = openState(s);
  if (!tid) return null;
  const mine = st && st.mine && !st.mine.tombstone ? st.mine : null;
  const n = st ? st.others_count || 0 : 0;
  const anchor = d && d.anchor ? d.anchor : null;
  return {
    turnId: tid,
    verdict: d && d.verdict ? d.verdict : (mine ? mine.verdict : null),
    mine: noteView(mine, true),
    note: d ? d.note : (mine && mine.verdict === 'wrong' ? mine.note || '' : ''),
    quote: anchor ? clip(anchor.quote, 80) : (mine && mine.anchor ? clip(mine.anchor.quote, 80) : ''),
    saving: !!(d && d.saving), error: (d && d.error) || '', focusNote: !!(d && d.focus),
    conflict: d && d.conflict ? { theirs: noteView(d.conflict.current, true), message: 'Changed since you opened it.' } : null,
    othersHidden: !!(st && st.others_hidden), othersCount: n,
    blindCopy: st && st.others_hidden ? (n === 1 ? '1 other reviewer has judged this answer. Their note stays hidden until you save yours.'
      : n + ' other reviewers have judged this answer. Their notes stay hidden until you save yours.') : '',
    others: st && st.others ? st.others.map((o) => noteView(o, false)).filter(Boolean) : [],
    inBatch: st && st.sample_item ? { reason: st.sample_item.reason, needs: st.reviews_required || 1, batchId: st.sample_item.batch_id || null } : null,
    canClear: !!mine,
    // The note form is for a draft only. A saved Wrong verdict offers Edit my note, which starts a draft from it, so
    // Save never sends a form that only looked filled in.
    editing: !!(d && d.verdict === 'wrong'),
    canEdit: !(d && d.verdict) && !!(mine && mine.verdict === 'wrong')
  };
}

// One annotatable block of the open answer (anchor = {span_id, field}), or null when the service sent no text for
// it (the timeline then shows its own rendering, not selectable). `block` is what annotateActions.selectFromBlock
// takes with the browser's selection. `marks`, when given, is a function (turnId, blockKey, text) -> highlight items
// ({id, start, end, kind}) that others add to the block: the AI suggestions' dashed highlights come in this way.
export function blockView(s, anchor, marks) {
  const { tid, st, d, blocks } = openState(s);
  const key = blockKey(anchor);
  if (!tid || !blocks || !key || typeof blocks[key] !== 'string') return null;
  const text = blocks[key];
  const items = wrongNotes(st).filter((x) => x.blockKey === key).map((x) => {
    const start = findOccurrence(text, x.quote, x.occurrence);
    return { id: x.id, start, end: start + x.quote.length, kind: x.me ? 'mine' : 'other' };
  });
  const draft = d && d.anchor && (d.anchor.key || blockKey(d.anchor)) === key ? d.anchor : null;
  if (draft) {
    const start = findOccurrence(text, draft.quote, draft.occurrence);
    items.push({ id: 'draft', start, end: start + draft.quote.length, kind: 'draft' });
  }
  if (typeof marks === 'function') marks(tid, key, text).forEach((m) => items.push(m));
  return {
    key, text, block: { key, span_id: anchor.span_id || null, field: anchor.field, text }, items, segments: highlightSegments(text, items),
    popover: !!(d && d.popover && draft), note: d ? d.note : ''
  };
}

// The margin beside the open answer: its wrong notes, and those whose quote is no longer in the text on screen.
export function marginView(s) {
  const { st, blocks } = openState(s);
  const notes = wrongNotes(st);
  return { notes, unplaced: blocks ? unplacedNotes(notes, blocks) : [] };
}

// The comment list under the conversation header. `me` is my id when the screen knows it; otherwise knownMe().
export function commentsView(s, me) {
  const r = rv(s);
  const who = me || knownMe(r);
  const hidden = r.commentsHidden || 0;
  return {
    rows: (r.comments || []).map((a) => ({ id: a.lineage_id, base: a.id, who: a.reviewer_email || 'Someone', note: a.note,
      mine: !!who && a.reviewer_id === who })),
    hiddenCopy: hidden ? (hidden === 1 ? '1 comment from another reviewer stays hidden until you have judged every answer here.'
      : hidden + ' comments from other reviewers stay hidden until you have judged every answer here.') : '',
    draft: r.commentDraft || '', busy: !!r.commentBusy, error: r.commentError || ''
  };
}

// Chips for an answer in the timeline's turn list: my verdict, Right ×n (mine plus visible others), the hidden count.
export function turnChips(s, turnId) {
  const t = (rv(s).ann || {})[turnId];
  if (!t) return [];
  const chips = [];
  const m = t.mine && !t.mine.tombstone ? t.mine : null;
  const rights = (m && m.verdict === 'right' ? 1 : 0) + (t.others || []).filter((o) => o.verdict === 'right').length;
  if (m) chips.push(m.verdict === 'right' ? '✓ You: right' : '✗ You: wrong');
  if (rights > 1 || (rights === 1 && !(m && m.verdict === 'right'))) chips.push('Right ×' + rights);
  if (t.others_hidden) chips.push(t.others_count + ' hidden');
  if (t.sample_item) chips.push('In a review batch');
  return chips;
}
