// steps — one recorded answer as the steps a reviewer annotates (22 Oct 2026 feedback, item 1).
//
// * The question comes first, then the orchestrator step: its routing model call, the general loop's first decision
//   (did it answer directly or call a tool?) or its plan. When the trace holds none of those (Hoistra routed by code,
//   e.g. the compliance shortcut), the step says so: routing ran as code and no model call was recorded.
// * After that a new step starts only where a different model role takes over: consecutive calls of one role (the same
//   agent and purpose, e.g. the compliance "plan" call recorded twice) are one step with parts. Hoistra uses one model id
//   for most OpenAI roles, so "the same model" means the same role, not the same id.
// * Whatever ran between two model calls (the fetch stage, database reads, tool calls and their results) is one code step,
//   its parts in the order they ran.
// * The final answer closes the trace.
// Every step has an input block (key) and an output block (outKey = key + '#output'); both can carry notes.
import { toCards, pretty } from '../../tracelab/logic/timeline.js';
import { fmtMs, fmtUsd } from '../../logic/tracesPage.js';

export const OUTPUT = '#output';
export const SCENARIO = ':scenario';
export const ORCHESTRATOR = ':orchestrator';

const UNKNOWN = 'unavailable';
const known = (x) => !!x && x !== UNKNOWN && x !== '—';
const ORCH_GROUPS = ['', 'orchestrator', 'orchestrator loop'];

function groupOf(msg) {
  return String((msg && msg.group) || '').trim();
}

function roleKey(card, msg) {
  return (groupOf(msg) || 'orchestrator') + '|' + String(card.purpose || '');
}

function isPromptNote(card) {
  return card.kind === 'note' && card.role === 'What the model was given';
}

function isOrchestratorModel(card, msg) {
  if (card.kind !== 'model') return false;
  const kind = String(((msg && msg.model_call) || {}).call_kind || '').toLowerCase();
  if (card.purpose === 'routing decision' || kind === 'router' || kind === 'routing') return true;
  return ORCH_GROUPS.indexOf(groupOf(msg).toLowerCase()) > -1;
}

// The column a read matched its records on, from the first join of its SQL ("by asset_code"): two reads that share a
// recorded name are different queries, and this tells them apart.
function matchedOn(sql) {
  const m = /\bON\s+"?\w+"?\."?(\w+)"?\s*=/i.exec(String(sql || ''));
  return m ? 'by ' + m[1] : '';
}

// The name Hoistra recorded for a part (plan, fetch, count, include ...), as its Run trace shows it.
function rawName(card) {
  const raw = card.raw || {};
  if (card.kind === 'model') return card.recorded || card.purpose || 'Model call';
  if (card.kind === 'db') return raw.label || card.title || 'Database query';
  return raw.name || card.title || card.role || 'Step';
}

// Parts of one step sharing a name get what tells them apart: the column each read matched on, or "1 of 2".
function nameParts(parts) {
  const by = {};
  parts.forEach((c) => { const n = rawName(c); (by[n] || (by[n] = [])).push(c); });
  Object.keys(by).forEach((n) => {
    const same = by[n];
    if (same.length < 2) { same.forEach((c) => { c.shown = n; }); return; }
    const tags = same.map((c) => (c.kind === 'db' ? matchedOn((c.raw || {}).sql) : ''));
    const unique = tags.every(Boolean) && new Set(tags).size === tags.length;
    same.forEach((c, k) => { c.shown = n + (unique ? ' · ' + tags[k] : ' (' + (k + 1) + ' of ' + same.length + ')'); });
  });
}

// What a part took in and gave back, ready to show: each side has a title and either text or rows.
export function partIO(card) {
  if (card.kind === 'model') {
    const [request, response] = card.panels || [];
    return { name: card.shown || rawName(card), meta: [card.model, card.latency, card.tokens, card.cost].filter(known),
      input: { title: 'Model input', text: (request && request.body) || '', note: (request && request.note) || '' },
      output: { title: 'Model output', text: (response && response.body) || '' } };
  }
  if (card.kind === 'tool') {
    return { name: card.shown || rawName(card), meta: [card.latency].filter(known),
      input: { title: 'Arguments', text: card.args || '' },
      output: { title: 'Result', text: (card.result && card.result.full) || '' } };
  }
  if (card.kind === 'db') {
    const raw = card.raw || {};
    const params = raw.params && Object.keys(raw.params).length ? '\n\n-- params\n' + pretty(raw.params) : '';
    return { name: card.shown || rawName(card),
      meta: [card.rowCount === null || card.rowCount === undefined ? 'rows not recorded' : card.rowCount + (card.rowCount === 1 ? ' row' : ' rows')],
      input: { title: 'SQL', text: (raw.sql || '') + params },
      output: { title: 'Rows', rows: card.rows || [], more: card.moreRows || 0, count: card.rowCount } };
  }
  if (card.kind === 'plan') {
    return { name: card.rejected ? 'Rejected plan' : 'Plan', meta: [((card.raw || {}).source) || ''].filter(Boolean),
      input: { title: 'Input', text: '' },
      output: { title: 'Plan', text: (card.steps || []).map((s) => s.n + '. ' + s.text).join('\n') || pretty((card.raw || {}).plan) } };
  }
  const raw = card.raw || {};
  return { name: card.shown || rawName(card), meta: [],
    input: { title: isPromptNote(card) ? 'What the model was given' : 'Input', text: pretty(raw.input) },
    output: { title: 'Output', text: isPromptNote(card) ? '' : pretty(raw.output) || ((card.body && card.body.full) || '') } };
}

function sumMoney(parts) {
  let total = 0;
  let any = false;
  parts.forEach((c) => {
    const n = c.cost && /^\$/.test(c.cost) ? Number(String(c.cost).replace(/[^0-9.]/g, '')) : NaN;
    if (Number.isFinite(n)) { total += n; any = true; }
  });
  return any ? fmtUsd(total) : '';
}

function codeTitle(parts) {
  const stage = parts.find((c) => c.kind === 'note' && !isPromptNote(c) && rawName(c));
  if (stage) return rawName(stage);
  const tool = parts.find((c) => c.kind === 'tool');
  if (tool) return rawName(tool);
  const dbs = parts.filter((c) => c.kind === 'db').length;
  if (dbs) return dbs === 1 ? 'Database read' : 'Database reads';
  return (parts[0] && (parts[0].title || parts[0].role)) || 'Code';
}

function codeFacts(parts) {
  const dbs = parts.filter((c) => c.kind === 'db');
  const tools = parts.filter((c) => c.kind === 'tool');
  const bits = [];
  if (tools.length) bits.push(tools.length + (tools.length === 1 ? ' tool call' : ' tool calls'));
  if (dbs.length) {
    const rows = dbs.reduce((n, c) => n + (Number(c.rowCount) || 0), 0);
    bits.push(dbs.length + (dbs.length === 1 ? ' query' : ' queries') + ' · ' + rows + (rows === 1 ? ' row' : ' rows'));
  }
  return bits.join(' · ');
}

function modelFacts(parts) {
  const last = parts[parts.length - 1] || {};
  const ms = parts.reduce((n, c) => {
    const m = /^([\d.]+)\s*(ms|s)$/.exec(String(c.latency || ''));
    return m ? n + (m[2] === 's' ? Number(m[1]) * 1000 : Number(m[1])) : n;
  }, 0);
  return [known(last.model) ? last.model : '', ms ? fmtMs(ms) : '', sumMoney(parts), parts.length > 1 ? parts.length + ' calls' : '']
    .filter(Boolean).join(' · ');
}

function agentsOf(record) {
  return (record && Array.isArray(record.groups) ? record.groups : []).map((g) => g.name).filter(Boolean);
}

// The steps of one answer (the conversation's active record), in order. Pure: the same record gives the same steps.
export function buildSteps(record) {
  if (!record) return [];
  const msgs = {};
  (record.messages || []).forEach((m) => { if (m && m.id) msgs[m.id] = m; });
  const cards = toCards(record).filter((c) => c.kind !== 'cot');
  cards.forEach((c) => { if (c.kind === 'model') c.recorded = String((msgs[c.id] || {}).label || '') || c.purpose; });
  const tid = String(record.id || 'turn');
  const steps = [];
  const user = cards.find((c) => c.kind === 'user');
  const answers = cards.filter((c) => c.kind === 'answer');
  const middle = cards.filter((c) => c.kind !== 'user' && c.kind !== 'answer');

  if (user) {
    steps.push({ key: user.id, outKey: null, kind: 'user', kindText: 'User', title: '', agent: '', facts: '', parts: [user],
      text: user.text || '', isEnd: true });
  }

  // The orchestrator step: the leading routing / decision call or the orchestrator's plan, with the prompt it was given.
  let i = 0;
  const lead = [];
  while (i < middle.length) {
    const c = middle[i];
    const m = msgs[c.id] || {};
    if (isPromptNote(c) || (c.kind === 'plan' && !groupOf(m))) { lead.push(c); i += 1; continue; }
    if (isOrchestratorModel(c, m) && !lead.some((x) => x.kind === 'model')) { lead.push(c); i += 1; continue; }
    break;
  }
  const leadModel = lead.find((c) => c.kind === 'model');
  if (leadModel) {
    steps.push({ key: leadModel.id, outKey: leadModel.id + OUTPUT, kind: 'orchestrator', kindText: 'Orchestrator',
      title: rawName(leadModel) || 'routing decision', agent: 'orchestrator', facts: modelFacts([leadModel]), parts: lead,
      byCode: false, isEnd: false });
  } else {
    const agents = agentsOf(record);
    const plan = lead.find((c) => c.kind === 'plan');
    const said = agents.length ? 'Handed to the ' + agents.join(', ') + (agents.length === 1 ? ' agent.' : ' agents.')
      : (plan ? 'Followed the plan below.' : (middle.length ? 'Went straight to the steps below.' : 'Answered without any recorded step.'));
    steps.push({ key: tid + ORCHESTRATOR, outKey: tid + ORCHESTRATOR + OUTPUT, kind: 'orchestrator', kindText: 'Orchestrator',
      title: 'routing by code', agent: 'orchestrator', facts: 'no orchestrator model call was recorded', parts: lead,
      byCode: true, routed: said, isEnd: false });
  }

  // Model roles and the code between them.
  let pendingPrompt = null;
  for (; i < middle.length; i += 1) {
    const c = middle[i];
    const m = msgs[c.id] || {};
    if (isPromptNote(c)) { pendingPrompt = c; continue; }
    const prev = steps[steps.length - 1];
    if (c.kind === 'model') {
      const key = roleKey(c, m);
      if (prev && prev.kind === 'model' && prev.role === key) {
        prev.parts.push(c);
        prev.facts = modelFacts(prev.parts.filter((p) => p.kind === 'model'));
        continue;
      }
      const parts = pendingPrompt ? [pendingPrompt, c] : [c];
      pendingPrompt = null;
      steps.push({ key: (parts[0]).id, outKey: (parts[0]).id + OUTPUT, kind: 'model', kindText: 'Model', role: key,
        title: rawName(c), agent: groupOf(m), facts: modelFacts([c]), parts, isEnd: false });
      continue;
    }
    if (prev && prev.kind === 'code') {
      prev.parts.push(c);
      prev.title = codeTitle(prev.parts);
      prev.facts = codeFacts(prev.parts);
      continue;
    }
    steps.push({ key: c.id, outKey: c.id + OUTPUT, kind: 'code', kindText: 'Code', title: codeTitle([c]), agent: groupOf(m),
      facts: codeFacts([c]), parts: [c], isEnd: false });
  }

  answers.forEach((a) => {
    steps.push({ key: a.id, outKey: null, kind: 'answer', kindText: a.role || 'Final answer', title: '', agent: '', facts: '',
      parts: [a], text: a.text || '', error: a.error || '', isEnd: true });
  });
  steps.forEach((s, n) => { s.n = n + 1; nameParts(s.parts || []); });
  return steps;
}

// The block a note belongs to: a step's input block, its output block, the question as a whole ("scenario"), or "other"
// for a note on a step this layout no longer shows separately (older notes keyed by an agent).
export function blockIndex(steps, turnId) {
  const map = {};
  (steps || []).forEach((s) => {
    map[s.key] = s.key;
    if (s.outKey) map[s.outKey] = s.outKey;
    (s.parts || []).forEach((p) => {
      if (!map[p.id]) map[p.id] = s.key;
      if (s.outKey && !map[p.id + OUTPUT]) map[p.id + OUTPUT] = s.outKey;
    });
  });
  if (turnId) map[turnId + SCENARIO] = 'scenario';
  return map;
}

export function blockOf(index, stepKey) {
  return index[stepKey] || 'other';
}

// What was asked and what came back for a step as a whole: the input side lists each part's input, the output side the
// last model part's output (a model step) or every part's output (a code step).
export function stepIO(step) {
  const parts = (step.parts || []).map((c) => ({ card: c, io: partIO(c) }));
  if (step.kind === 'model' || step.kind === 'orchestrator') {
    const models = parts.filter((p) => p.card.kind === 'model');
    const last = models[models.length - 1];
    return { inputs: parts, outputs: last ? [last] : [] };
  }
  return { inputs: parts, outputs: parts };
}
