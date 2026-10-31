// timeline — pure: one normalized Trace Lab record (the service's normalize.normalize_turn output) becomes the
// cards the Conversations timeline draws. No state and no requests. A card's title never carries an internal
// agent, tool or table name: titles go through labelFor(), and the raw values reach only views/RawPanel.jsx,
// collapsed. Reuses the app's own formatters and scrubber by import (logic/tracesPage.js, logic/publicText.js).
import { scrubInternal } from '../../logic/publicText.js';
import { fmtMs, fmtTok, fmtUsd } from '../../logic/tracesPage.js';

export const COLLAPSE_CHARS = 240;
export const DB_ROWS_SHOWN = 10;
export const UNAVAILABLE = 'unavailable';
export const PURPOSES = ['tool decision', 'answer synthesis', 'routing decision'];

// Names the trace records, as a reviewer reads them. Anything else falls back to humanize().
const NAME_LABELS = {
  orchestrator: 'Assistant', 'orchestrator loop': 'Assistant', 'sub-agent': 'Helper', turn: 'Answer',
  compliance: 'Compliance', compliance_router: 'Compliance routing', agent_router: 'Routing', router: 'Routing decision',
  udr_agent: 'Data lookup', el_udr: 'Answer check', contract_compose: 'Vendor answer rewrite',
  contract_analyst: 'Vendor answer rewrite', answer_from_records: 'Records query', task: 'Hand-off to a helper',
  analyst: 'Analyst', planner: 'Planner', plan: 'Planning', review: 'Review', summary: 'Summary', judge: 'Answer check',
  recheck: 'Answer re-check', fetch: 'Fetching records', 'prompt assembled': 'What the model was given'
};
const KIND_LABELS = { db: 'Database query', tool: 'Tool', llm: 'Model call', router: 'Routing decision', plan: 'Plan',
  step: 'Plan step', stage: 'Step', agent: 'Helper', turn: 'Answer' };
const ALWAYS_ROWS = ['Provider', 'Finish reason', 'Response id', 'Time to first token', 'Tool call ids', 'Cache write tokens', 'Reasoning tokens'];

const has = (o, k) => Object.prototype.hasOwnProperty.call(o, k);
const nil = (v) => v === null || v === undefined;
const two = (n) => String(n).padStart(2, '0');
function timeOf(iso) {
  const t = Date.parse(iso || '');
  if (isNaN(t)) return '';
  const d = new Date(t);
  return two(d.getHours()) + ':' + two(d.getMinutes()) + ':' + two(d.getSeconds());
}

// A recorded name as words, with the platform's own names taken out (logic/publicText.js, plus the bare forms a
// name can take once its underscores are spaces).
export function humanize(name) {
  let s = scrubInternal(String(name || '').replace(/^(?:tool|agent|engine):/i, ''));
  s = s.replace(/[_-]+/g, ' ')
    .replace(/\bdoc rag\b/gi, 'document search')
    .replace(/\bdeep ?agents\b/gi, 'assistant')
    .replace(/\budr\b/gi, 'data')
    .replace(/\brag\b/gi, 'search')
    .replace(/\bsvc\b/gi, '')
    .replace(/\s+/g, ' ')
    .trim();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : '';
}

export function labelFor(kind, name) {
  const key = String(name || '').trim();
  if (key && has(NAME_LABELS, key)) return NAME_LABELS[key];
  if (key && has(NAME_LABELS, key.toLowerCase())) return NAME_LABELS[key.toLowerCase()];
  return humanize(key) || KIND_LABELS[kind] || 'Step';
}

// Text cut at n characters (240 by default) with the whole kept beside it.
export function collapse(text, n) {
  const max = typeof n === 'number' ? n : COLLAPSE_CHARS;
  const s = nil(text) ? '' : String(text);
  return s.length > max ? { text: s.slice(0, max) + '…', full: s, long: true, chars: s.length }
    : { text: s, full: s, long: false, chars: s.length };
}

export function pretty(v) {
  if (nil(v)) return '';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
}

// One model call (the record's model_call) as a card: "Model call · model · latency · tokens · cost" and four
// panels that start closed. A missing field reads "unavailable", never 0 or a guess.
export function modelCallCard(rec, id) {
  const r = rec || {};
  const u = r.usage || {};
  const model = r.model || UNAVAILABLE;
  const latency = typeof r.latency_s === 'number' ? fmtMs(r.latency_s * 1000) : UNAVAILABLE;
  const tokens = nil(u.input_tokens) && nil(u.output_tokens) ? UNAVAILABLE : fmtTok(u.input_tokens) + ' in · ' + fmtTok(u.output_tokens) + ' out';
  const cost = typeof r.total_cost === 'number' ? fmtUsd(r.total_cost) : (r.cost_status === 'unpriced' ? 'unpriced' : UNAVAILABLE);
  const withheld = Array.isArray(r.withheld) && r.withheld.length > 0;
  const val = (v) => (nil(v) ? UNAVAILABLE : String(v));
  return {
    id, kind: 'model', role: 'Model call', purpose: PURPOSES.indexOf(r.label) > -1 ? r.label : labelFor('llm', r.label),
    model, latency, tokens, cost, header: 'Model call · ' + model + ' · ' + latency + ' · ' + tokens + ' · ' + cost,
    ok: r.status !== 'error', error: r.status_message || '', cotNote: r.cot_note || '', withheld,
    panels: [
      { id: id + ':request', title: 'Request', body: nil(r.request) ? UNAVAILABLE : pretty(r.request),
        note: r.request_kind === 'loop_summary' ? 'Summary only: the full request is not recorded. "What the model was given" above shows the prompt.' : '' },
      { id: id + ':response', title: 'Response', note: '',
        body: withheld ? 'Hidden so it does not shape your review.' : (nil(r.response) ? UNAVAILABLE : pretty(r.response)) },
      { id: id + ':usage', title: 'Usage', note: '',
        rows: [['Input tokens', val(u.input_tokens)], ['Output tokens', val(u.output_tokens)], ['Cache read tokens', val(u.cache_read_tokens)],
          ['Total tokens', val(u.total_tokens)], ['Cost', cost], ['Latency', latency],
          ['Model settings', r.model_parameters ? pretty(r.model_parameters) : UNAVAILABLE]].concat(ALWAYS_ROWS.map((label) => [label, UNAVAILABLE])) },
      { id: id + ':raw', title: 'Raw metadata', raw: { operation: r.operation || null, request_kind: r.request_kind || null, raw: withheld ? null : (r.raw || null) } }
    ]
  };
}

function planSteps(plan) {
  const steps = plan && Array.isArray(plan.steps) ? plan.steps : [];
  return steps.map((s, i) => ({ n: i + 1, text: typeof s === 'string' ? s
    : String((s && (s.question || s.why || s.purpose)) || labelFor('step', s && (s.target || s.name)) || 'Step') }));
}

export function toCards(record) {
  const msgs = record && Array.isArray(record.messages) ? record.messages : [];
  const cards = [];
  let sawResult = false;
  for (let i = 0; i < msgs.length; i += 1) {
    const m = msgs[i] || {};
    const depth = Math.max(0, Number(m.depth) || 0);
    const when = timeOf(m.started_at);
    if (m.role === 'tool_call' || m.role === 'tool_result') {
      const call = m.role === 'tool_call' ? m : null;
      let res = m.role === 'tool_result' ? m : null;
      const next = msgs[i + 1];
      if (call && next && next.role === 'tool_result' && next.span_id === m.span_id) { res = next; i += 1; }
      if (res) sawResult = true;
      const head = call || res;
      const result = collapse(res ? pretty(res.content) : '', COLLAPSE_CHARS);
      cards.push({ id: head.id, kind: 'tool', role: 'Tool', depth, when, title: labelFor('tool', head.name), latency: fmtMs(head.latency_ms),
        args: call ? pretty(call.arguments) : '', result, collapsed: !!(res && res.collapse) || result.long,
        ok: head.ok !== false && (!res || res.ok !== false), error: (res && res.error) || (call && call.error) || '',
        requested: !!(call && call.requested_by),
        raw: { name: head.name || null, arguments: call ? call.arguments : null, content: res ? res.content : null },
        anchor: res ? res.anchor || null : null });
      continue;
    }
    if (m.role === 'model_call') {
      const rec = Object.assign({}, m.model_call || {});
      if (!rec.label) rec.label = m.label || (sawResult ? 'answer synthesis' : 'tool decision');
      cards.push(Object.assign(modelCallCard(rec, m.id), { depth, when, anchor: m.anchor || null }));
      continue;
    }
    if (m.role === 'user') {
      cards.push({ id: m.id, kind: 'user', role: 'Question', depth, when, text: m.text || '', anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'assistant') {
      const role = m.subtype === 'workflow_stop' ? 'Stopped at an approval gate' : (m.subtype === 'error' ? 'Answer (failed)' : 'Answer');
      cards.push({ id: m.id, kind: 'answer', role, depth, when, text: m.text || '', error: m.error || '', anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'db_query') {
      const rows = Array.isArray(m.rows) ? m.rows : [];
      cards.push({ id: m.id, kind: 'db', role: 'Database query', depth, when, title: labelFor('db', m.label || m.name),
        rowCount: nil(m.row_count) ? null : Number(m.row_count), rows: rows.slice(0, DB_ROWS_SHOWN),
        moreRows: Math.max(0, rows.length - DB_ROWS_SHOWN) + (Number(m.rows_truncated) || 0), collapsed: !!m.collapse,
        ok: m.ok !== false, error: m.error || '', raw: { sql: m.sql || '', params: m.params || {}, label: m.label || null }, anchor: m.anchor || null });
      continue;
    }
    if (m.role === 'plan') {
      cards.push({ id: m.id, kind: 'plan', role: 'Plan', depth, when, steps: planSteps(m.plan), rejected: m.rejected || '',
        raw: { source: m.source || null, plan: m.plan || null } });
      continue;
    }
    if (m.role === 'step') {
      const body = collapse(pretty(m.output), COLLAPSE_CHARS);
      cards.push({ id: m.id, kind: 'step', role: m.subtype === 'check' ? 'Check' : 'Plan step', depth, when, title: labelFor('step', m.name),
        body, collapsed: !!m.collapse || body.long, ok: m.ok !== false, error: m.error || '',
        raw: { name: m.name || null, input: m.input, output: m.output }, anchor: m.anchor || null });
      continue;
    }
    // system_note: the prompt note, a stage, or any role this build does not know.
    const prompt = m.subtype === 'prompt';
    const body = collapse(pretty(prompt || nil(m.output) ? m.input : m.output), COLLAPSE_CHARS);
    cards.push({ id: m.id, kind: 'note', role: prompt ? 'What the model was given' : (m.subtype === 'unknown_kind' ? 'Recorded step' : 'Step'),
      depth, when, title: prompt ? '' : labelFor('stage', m.name), body, collapsed: prompt || !!m.collapse || body.long,
      ok: m.ok !== false, error: m.error || '', raw: { name: m.name || null, label: m.label || null, input: m.input, output: m.output },
      anchor: prompt || nil(m.output) ? null : (m.anchor || null) });
  }
  // The record's own chain-of-thought notice follows every group of model calls (the service writes the text).
  const text = record && record.cot_boundary;
  if (!text) return cards;
  const out = [];
  cards.forEach((card, k) => {
    out.push(card);
    if (card.kind === 'model' && !(cards[k + 1] && cards[k + 1].kind === 'model')) {
      out.push({ id: card.id + ':cot', kind: 'cot', role: 'About model calls', depth: card.depth, when: '', text });
    }
  });
  return out;
}
