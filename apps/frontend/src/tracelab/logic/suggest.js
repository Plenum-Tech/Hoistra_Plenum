// suggest — pure helpers and the view model for AI suggestions on the open answer in Conversations: dashed highlights
// on the quoted text, cards in the margin with Accept / Dismiss (or the other value), and the answer's
// failure-mode chips. The quote is found in the server's own block text with the annotation layer's helpers, so a
// highlight sits where the server put the evidence. The server keeps suggestions and failure modes hidden on an
// answer the reviewer has not judged. No requests and no state of its own: taxonomyActions.js reads and decides.
import { blockKey, findOccurrence, reviewOf } from './annotate.js';
import { sourceText } from './labels.js';
import { clip, hasCompany, taxOf } from './taxCommon.js';

export const SUGGEST_HIDDEN_TEXT = 'AI suggestions and failure modes for this answer stay hidden until you judge it yourself.';
const CARD_BASE = 132;

// Highlight items for one block of the open answer: [{id: 'sug:<proposal id>', start, end, kind}] for each suggestion
// whose quote is in that block's text (first occurrence).
export function suggestionMarks(items, key, text) {
  const out = [];
  (items || []).forEach((p) => {
    if (!p || !p.quote || typeof text !== 'string' || blockKey({ span_id: p.span_id, field: p.field }) !== key) return;
    const start = findOccurrence(text, p.quote, 1);
    if (start >= 0) out.push({ id: 'sug:' + p.id, start, end: start + p.quote.length, kind: 'suggestion' });
  });
  return out;
}

function chipOf(x) {
  if (x.state === 'unchecked' && x.proposal) {
    return { key: x.mode_id, kind: 'ai', text: 'AI: ' + (x.proposal.suggestion === 'present' ? 'present' : 'absent') + ' · ' + x.name,
      title: 'AI suggestion, not a judgment. ' + (x.proposal.evidence || '') };
  }
  return { key: x.mode_id, kind: x.state, text: x.name + ': ' + x.state + (x.via ? ' (via ' + (x.via_name || 'a merged failure mode') + ')' : ''),
    title: [sourceText(x.source, x.approval), x.decided_by ? 'by ' + x.decided_by : ''].filter(Boolean).join(' · ') };
}

// The open answer's suggestions: `s` is the store state, `a` the actions the cards call. tlTaxSuggest is null without
// an open answer on Conversations and for the platform team (no company to review in).
export function taxonomySuggestVals(s, a) {
  const t = taxOf(s);
  const tid = s.tab === 'conversations' ? s.turnId || null : null;
  if (!tid || !hasCompany(s)) return { tlTaxSuggest: null };
  const mine = t.tlSugTurnId === tid;
  const items = mine ? t.tlSug || [] : [];
  const blocks = reviewOf(s).blocks[tid] || null;
  const writes = t.tlWrites;
  const st = reviewOf(s).ann[tid];
  const judged = !!(st && st.mine && !st.mine.tombstone);
  const placed = (p) => {
    if (!blocks || !p.quote) return false;
    const text = blocks[blockKey({ span_id: p.span_id, field: p.field })];
    return typeof text === 'string' && findOccurrence(text, p.quote, 1) >= 0;
  };
  const cards = items.map((p) => {
    const v = p.kind === 'depth_scan' || p.suggestion === 'present' ? 'present' : 'absent';
    const w = writes['suggest:' + p.id] || {};
    return {
      id: p.id, markId: 'sug:' + p.id, placed: placed(p), kicker: 'AI suggestion', modeName: p.mode_name || 'A failure mode',
      definition: p.mode_definition || '', suggestion: v === 'present' ? 'Present in this answer' : 'Absent from this answer',
      evidence: p.evidence || '', quote: clip(p.quote || '', 160), busy: !!w.busy, error: w.error || '',
      accept: () => a.sugDecide(p.id, 'accept'), acceptLabel: p.kind === 'depth_scan' ? 'Accept' : 'Accept: ' + v,
      other: p.kind === 'depth_scan' ? () => a.sugDecide(p.id, 'dismiss') : () => a.sugDecide(p.id, 'override', v !== 'present'),
      otherLabel: p.kind === 'depth_scan' ? 'Dismiss' : 'Mark ' + (v === 'present' ? 'absent' : 'present'),
      otherHint: p.kind === 'depth_scan' ? 'Dismissing records this answer as a close example where the failure is absent.' : '',
      height: CARD_BASE + 16 * Math.ceil(Math.min(400, (p.evidence || '').length) / 34)
    };
  });
  const cells = mine ? t.tlSugCells || [] : [];
  return {
    tlTaxSuggest: {
      turnId: tid, judged, ensure: () => a.sugEnsure(tid, judged), reload: () => a.sugLoad(tid),
      loading: !!t.tlSugLoading && !(mine && t.tlSugLoadedAt), error: mine ? t.tlSugErr || '' : '',
      hidden: mine && !!t.tlSugHidden, hiddenText: SUGGEST_HIDDEN_TEXT,
      chips: cells.filter((x) => x.state !== 'unchecked' || x.proposal).map(chipOf),
      emptyText: 'No failure mode is marked on this answer yet.',
      cards: cards.filter((k) => k.placed), unplaced: cards.filter((k) => !k.placed)
    }
  };
}
