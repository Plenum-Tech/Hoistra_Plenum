// views — pure view models for the Trace Lab tabs: each takes the store's state (and the time, for "today
// 11:00") and returns plain data the screens draw. No callbacks here: the screens call actions.js by id.
import { fmtMs, fmtTok, fmtUsd, fmtWhen } from '../../logic/tracesPage.js';
import { PAGE_REASONS, PAGE_SIZE, channelLabel, noticeFor } from './shell.js';
import { labelFor, modelCallCard, PURPOSES, toCards, UNAVAILABLE } from './timeline.js';

const nil = (v) => v === null || v === undefined;
const count = (v) => Number(v || 0).toLocaleString('en-GB');

// Synthetic and inferred groupings are always labelled, never shown as a conversation someone held.
export function groupingChip(conv) {
  if (!conv || conv.session_kind === 'observed') return null;
  const key = String(conv.key || '');
  const sid = String(conv.session_id || '');
  if (key.startsWith('r:')) return 'Grouped by re-run';
  if (key.startsWith('t:')) return 'Single answer, no conversation recorded';
  if (sid.startsWith('report-')) return 'Grouped by report run';
  if (sid.startsWith('golden-')) return 'Grouped by golden run';
  if (sid.startsWith('docs-') || sid.startsWith('ing-')) return 'Grouped by document upload';
  if (sid.startsWith('skilllab-')) return 'Grouped by skill lab replay';
  return 'Legacy grouping';
}

// Page one read again and merged by conversation key: fresh rows first (a row in both takes the fresh copy), the
// rows still on screen kept after them, and never more than a page (`limit`, PAGE_SIZE by default).
export function mergeByKey(oldRows, freshRows, limit) {
  const fresh = freshRows || [];
  const old = oldRows || [];
  const keys = new Set(fresh.map((r) => r.key));
  const cap = Math.max(typeof limit === 'number' ? limit : PAGE_SIZE, fresh.length);
  return fresh.concat(old.filter((r) => !keys.has(r.key))).slice(0, cap);
}

export const FLAG_NOTES = Object.freeze({
  spans_purged: 'The step-by-step record of this answer was deleted after the retention period; only the question and the answer remain.',
  duplicate_trees: 'This answer was recorded twice; the record that matches the answer is shown.',
  orphan_spans: 'Some steps lost their parent step and are shown at the top level.',
  truncated: 'Some recorded content was cut to fit the record.',
  question_repaired: "The question shown is the asker's own words, recovered from the record.",
  question_wrapped: 'The question shown still includes the page context it was sent with.',
  cost_incomplete: 'The cost is partial: some model calls have no price.',
  legacy_provenance: 'Where this question came from is inferred from the record.',
  inferred_rewrite: "The stored answer differs from the model's last output, so it was probably changed by a check after the model finished (inferred).",
  view_as: 'A platform admin asked this while viewing as this company.',
  thread_deleted: 'The asker deleted this conversation.',
  unexecuted_tool_requests: 'The model asked for a tool that did not run.',
  concurrent_same_name_tools: 'Two calls of the same tool ran at once; their results may be swapped.',
  failed: 'This answer failed.',
  workflow_stop: 'This answer stopped at an approval gate.'
});

export function flagNotes(flags) {
  const out = [];
  (flags || []).forEach((f) => {
    const note = FLAG_NOTES[f];
    if (note && out.indexOf(note) < 0) out.push(note);
  });
  return out;
}

// A whole-page notice when the service refused the reader (for example a superadmin who is not viewing as a
// company), else ''.
export function pageNotice(s) {
  for (const part of [s.convs, s.runs, s.calls]) {
    if (part && PAGE_REASONS.indexOf(part.reason) > -1) return noticeFor(part.reason, part.error);
  }
  return '';
}

export function isOpen(s, id, dflt) {
  const open = (s && s.open) || {};
  return Object.prototype.hasOwnProperty.call(open, id) ? !!open[id] : !!dflt;
}

export function listView(s, now) {
  const c = s.convs;
  const rows = c.rows || [];
  const total = typeof c.total === 'number' ? c.total : null;
  const pages = total !== null ? Math.max(1, Math.ceil(total / PAGE_SIZE)) : null;
  return {
    rows: rows.map((r) => ({
      key: r.key, question: r.first_question || '—',
      answers: r.turns_in_window + (r.turns_in_window === 1 ? ' answer' : ' answers'),
      who: (r.asker && r.asker.email) || '—', when: fmtWhen(r.last_turn_at, now), chip: groupingChip(r),
      failed: Number(r.failed_turns) > 0, on: r.key === s.convKey
    })),
    loading: !!c.loading && !c.loadedAt,
    error: c.error || '',
    empty: !!c.loadedAt && !rows.length && !c.error,
    totalLabel: total !== null ? total + (total === 1 ? ' conversation' : ' conversations') : '',
    pageLabel: pages ? 'Page ' + (c.page + 1) + ' of ' + pages : 'Page ' + (c.page + 1),
    canPrev: c.page > 0, canNext: !!(c.cursors || [])[c.page + 1]
  };
}

export function convView(s, now) {
  const conv = s.conv.data && s.conv.data.key === s.convKey ? s.conv.data : null;
  const base = { open: !!s.convKey, loading: !!s.conv.loading && !conv, error: s.conv.error || '' };
  if (!conv) return Object.assign(base, { view: null });
  const rec = conv.active || null;
  const meta = (rec && rec.meta) || {};
  const headers = conv.turns || [];
  const usage = (rec && rec.usage) || {};
  const tools = Number(usage.tool_calls) || 0;
  const chips = [channelLabel(meta.channel || 'chat')];
  const chip = groupingChip(conv);
  if (chip) chips.push(chip);
  chips.push('Answer ' + (meta.turn_index || 1) + ' of ' + (meta.turn_count || headers.length || 1));
  if (Number(meta.attempt) > 1) chips.push('Attempt ' + meta.attempt);
  if (meta.channel_source === 'inferred') chips.push('Source inferred');
  ((rec && rec.models) || []).forEach((m) => chips.push(String(m)));
  chips.push(tools + (tools === 1 ? ' tool' : ' tools'));
  const fb = rec && rec.feedback;
  return Object.assign(base, { view: {
    key: conv.key, title: conv.session_label || '—', chips,
    answers: headers.map((h) => ({ id: h.turn_id, label: 'Answer ' + h.turn_index, question: h.question || '—', failed: h.ok === false, on: !!rec && h.turn_id === rec.id })),
    truncatedNote: conv.turns_truncated ? 'Showing the first 500 answers of this conversation.' : '',
    when: rec ? fmtWhen(rec.timestamp, now) : '', who: (meta.asker && meta.asker.email) || '—',
    cost: rec ? fmtUsd(usage.cost_usd) + (usage.cost_complete === false ? ' (partial)' : '') : '—',
    latency: rec ? fmtMs(usage.latency_ms) : '—',
    notes: flagNotes(rec ? rec.flags : []),
    rating: fb ? { text: (fb.by_asker ? 'The asker rated this answer ' : 'Rated, not by the asker: ') + (fb.rating === 'up' ? 'up' : (fb.rating === 'down' ? 'down' : '—')), comment: fb.comment || '' } : null,
    cards: rec ? toCards(rec) : []
  } });
}

export function runsView(s, now) {
  const r = s.runs;
  return {
    rows: (r.rows || []).map((x) => ({
      id: x.run_id, name: x.kind === 'live' ? 'All live traffic' : String(x.name || x.run_id), live: x.kind === 'live',
      period: x.started_at ? fmtWhen(x.started_at, now) + ' – ' + fmtWhen(x.ended_at, now) : '—',
      conversations: nil(x.conversation_count) ? '—' : String(x.conversation_count),
      answers: nil(x.turn_count) ? '—' : String(x.turn_count),
      models: (x.models || []).join(', ') || '—', cost: fmtUsd(x.cost_usd), status: x.kind === 'live' ? 'Live' : String(x.status || '—'),
      on: (s.run || 'live') === x.run_id
    })),
    loading: !!r.loading && !r.loadedAt,
    error: r.error || '',
    goldenNote: r.loadedAt && !r.goldenAvailable ? 'No golden runs yet.' : ''
  };
}

// The Run filter's options: the runs the service listed, or the live pseudo-run before they are read.
export function runOptions(s) {
  const rows = ((s.runs && s.runs.rows) || []).filter((x) => x && x.run_id);
  return (rows.length ? rows : [{ run_id: 'live', kind: 'live' }])
    .map((x) => ({ value: x.run_id, label: x.kind === 'live' ? 'All live traffic' : String(x.name || x.run_id) }));
}

export function callsView(s, now) {
  const c = s.calls;
  const rows = c.rows || [];
  const agg = c.aggregate;
  return {
    header: agg ? [count(agg.calls) + (Number(agg.calls) === 1 ? ' call' : ' calls'), 'p50 ' + fmtMs(agg.latency_p50_ms),
      'p95 ' + fmtMs(agg.latency_p95_ms), fmtTok(agg.input_tokens) + ' in · ' + fmtTok(agg.output_tokens) + ' out', fmtUsd(agg.cost_usd),
      count(agg.errors) + ' failed'].concat(Number(agg.unpriced_calls) ? [count(agg.unpriced_calls) + ' unpriced'] : []).join(' · ') : '',
    note: c.retentionDays ? 'Model calls are kept for ' + c.retentionDays + ' days; older calls are not in these figures.' : '',
    loading: !!c.loading && !c.loadedAt,
    error: c.error || '',
    empty: !!c.loadedAt && !rows.length && !c.error,
    rows: rows.map((r) => ({
      id: r.span_id, when: fmtWhen(r.started_at, now), model: r.model || UNAVAILABLE,
      purpose: PURPOSES.indexOf(r.label) > -1 ? r.label : labelFor('llm', r.label),
      latency: nil(r.latency_ms) ? UNAVAILABLE : fmtMs(r.latency_ms),
      tokens: nil(r.input_tokens) && nil(r.output_tokens) ? UNAVAILABLE : fmtTok(r.input_tokens) + ' in · ' + fmtTok(r.output_tokens) + ' out',
      cost: nil(r.cost_usd) ? (nil(r.input_tokens) ? UNAVAILABLE : 'unpriced') : fmtUsd(r.cost_usd),
      ok: r.ok !== false, question: r.question_excerpt || '—', on: !!(s.call && s.call.spanId === r.span_id)
    })),
    canPrev: c.page > 0, canNext: !!(c.cursors || [])[c.page + 1]
  };
}

export function callPanelView(s) {
  const mc = s.call;
  if (!mc) return null;
  return { spanId: mc.spanId, loading: !!mc.loading, error: mc.error || '', card: mc.data ? modelCallCard(mc.data, 'panel:' + mc.spanId) : null,
    turnId: mc.turnId || null, conversationKey: mc.conversationKey || null };
}
