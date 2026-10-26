// labelling — the pure view models of step labelling: an answer as a list of steps (from the timeline cards Trace Lab
// already builds), each person's labels grouped by step, and the wording for labels, roles and people.
import { toCards } from '../../tracelab/logic/timeline.js';
import { fmtMs, fmtUsd } from '../../logic/tracesPage.js';

export const LABEL_CHOICES = Object.freeze([
  { key: 'right', text: 'Right' },
  { key: 'wrong', text: 'Wrong' },
  { key: 'unsure', text: 'Not sure' }
]);
export const LABEL_TEXT = Object.freeze({ right: 'Right', wrong: 'Wrong', unsure: 'Not sure' });
export const ROLE_CHOICES = Object.freeze([
  { key: 'domain_expert', text: 'Domain expert' },
  { key: 'tester', text: 'Tester' },
  { key: 'admin', text: 'Admin' },
  { key: 'user', text: 'User' }
]);
export const ROLE_TEXT = Object.freeze({ domain_expert: 'Domain expert', tester: 'Tester', admin: 'Admin', user: 'User',
  platform: 'Platform team' });
export const STATUS_TEXT = Object.freeze({ pending: 'Waiting for access', active: 'Active', removed: 'No access', none: 'No access' });
export const STATUS_CHOICES = Object.freeze([{ key: 'active', text: 'Active' }, { key: 'removed', text: 'No access' }]);

// The one control Hoistra's Users & access shows per person: no access, or access with a Trace Lab role.
export const ACCESS_CHOICES = Object.freeze([{ key: '', text: 'No access' }].concat(ROLE_CHOICES));
export function accessValue(member) {
  return member && member.status === 'active' ? member.role : '';
}
export const NOTE_MAX = 2000;
export const EXPECTED_MAX = 2000;
export const GROUP_MIN = 2;
export const GROUP_MAX = 30;

const KIND_TEXT = { user: 'Question', answer: 'Answer', model: 'Model call', tool: 'Tool', db: 'Database query', plan: 'Plan',
  step: 'Step', note: 'Step' };

function stepTitle(card) {
  if (card.kind === 'model') return card.purpose || 'Model call';
  if (card.kind === 'plan') return card.rejected ? 'Rejected plan' : 'The plan';
  return card.title && card.title !== '—' ? card.title : '';
}

// A value the trace actually recorded: not "unavailable" and not the "—" placeholder.
const known = (x) => !!x && x !== 'unavailable' && x !== '—';

function stepFacts(card) {
  if (card.kind === 'db') {
    if (card.rowCount === null || card.rowCount === undefined) return 'rows not recorded';
    return card.rowCount + (card.rowCount === 1 ? ' row' : ' rows');
  }
  // A closed step's line names only what the trace recorded: no row of "unavailable".
  if (card.kind === 'model') return [card.model, card.latency, card.cost].filter(known).join(' · ');
  if (card.kind === 'tool') return known(card.latency) ? card.latency : '';
  if (card.kind === 'plan') return card.steps && card.steps.length ? card.steps.length + ' steps' : '';
  return '';
}

// The column a read matched its records on, from the first join of its SQL ("by asset_code"): two reads that share a
// recorded name ("include linked_assets [the certificate's own asset]") are different queries, matching on different
// columns, and this is what tells them apart.
function matchedOn(sql) {
  const m = /\bON\s+"?\w+"?\."?(\w+)"?\s*=/i.exec(String(sql || ''));
  return m ? 'by ' + m[1] : '';
}

// Database reads sharing a name get what tells them apart: the column each matched on, or "1 of 2" when even that is the same.
function distinct(steps, msgs) {
  const byTitle = {};
  steps.forEach((st) => { if (st.title) (byTitle[st.title] || (byTitle[st.title] = [])).push(st); });
  Object.values(byTitle).forEach((same) => {
    if (same.length < 2) return;
    const tags = same.map((st) => matchedOn((msgs[st.key] || {}).sql));
    const unique = tags.every((t) => t) && new Set(tags).size === tags.length;
    same.forEach((st, i) => { st.title += unique ? ' · ' + tags[i] : ' (' + (i + 1) + ' of ' + same.length + ')'; });
  });
}

function stepOf(c, m) {
  const kindText = c.role || KIND_TEXT[c.kind] || 'Step';
  const recorded = c.kind === 'user' || c.kind === 'answer' || c.kind === 'plan' ? '' : String(m.label || m.name || '');
  const title = recorded || stepTitle(c);
  const took = fmtMs(m.latency_ms);
  let facts = stepFacts(c);
  if (c.kind === 'db') facts = [took, facts].filter(known).join(' · ');
  else if (c.kind === 'note' || c.kind === 'step') facts = known(took) ? took : '';
  return {
    key: c.id, kind: c.kind, kindText, title: title.toLowerCase() === kindText.toLowerCase() ? '' : title, facts,
    failed: c.ok === false, text: c.kind === 'user' || c.kind === 'answer' ? (c.text || '') : null,
    isEnd: c.kind === 'user' || c.kind === 'answer', nested: false, card: c
  };
}

// One answer (a conversation's active record) as the steps a person can label, in the order and with the names Hoistra's
// Run trace shows: the question; each agent the orchestrator called, followed by the steps that agent ran (nested under
// it); the steps the orchestrator ran itself, such as its database reads; then the answer. Each list keeps the recorded
// order. Nothing recorded is left out and nothing is shown twice. The record's reasoning notice is not a step.
export function stepsOf(record) {
  if (!record) return [];
  const msgs = {};
  (record.messages || []).forEach((m) => { if (m && m.id) msgs[m.id] = m; });
  const groups = Array.isArray(record.groups) ? record.groups : [];
  const cards = toCards(record).filter((c) => c.kind !== 'cot');
  const seq = (c) => Number((msgs[c.id] || {}).seq) || 0;
  // The agent a step ran inside: its own parent span when that is the agent, else the one agent of that name.
  const agentOf = (m) => groups.find((g) => g.span_id && g.span_id === m.parent_span_id)
    || (m.group && groups.filter((g) => g.name === m.group).length === 1 ? groups.find((g) => g.name === m.group) : null);

  const units = [];
  const agentUnits = new Map();
  cards.filter((c) => c.kind !== 'user' && c.kind !== 'answer').forEach((c) => {
    const m = msgs[c.id] || {};
    const st = stepOf(c, m);
    const g = agentOf(m);
    if (!g) { units.push({ at: seq(c), steps: [st] }); return; }
    let u = agentUnits.get(g);
    if (!u) {
      const cost = Number(g.cost_usd);
      u = { at: seq(c), steps: [], agent: {
        key: (record.id || 'turn') + ':' + g.span_id + ':agent', kind: 'agent', kindText: 'Agent', title: String(g.name || 'agent'),
        facts: [fmtMs(g.latency_ms), g.cost_usd !== null && g.cost_usd !== undefined && Number.isFinite(cost) ? fmtUsd(cost) : ''].filter(known).join(' · '),
        failed: false, text: null, isEnd: false, nested: false, card: null } };
      agentUnits.set(g, u);
      units.push(u);
    }
    u.at = Math.min(u.at, seq(c));
    st.nested = true;
    u.steps.push(st);
  });
  agentUnits.forEach((u) => {
    u.agent.text = 'The ' + u.agent.title + ' agent ran ' + u.steps.length + (u.steps.length === 1 ? ' step: ' : ' steps: ')
      + u.steps.map((st) => st.title || st.kindText).join(', ') + '.';
    u.steps.unshift(u.agent);
  });
  units.sort((x, y) => x.at - y.at);
  const steps = [].concat(cards.filter((c) => c.kind === 'user').map((c) => stepOf(c, msgs[c.id] || {})),
    ...units.map((u) => u.steps), cards.filter((c) => c.kind === 'answer').map((c) => stepOf(c, msgs[c.id] || {})));
  distinct(steps.filter((st) => st.kind === 'db'), msgs);
  steps.forEach((st, i) => { st.n = i + 1; });
  return steps;
}

const when = (iso) => {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
};

export function labelView(x, me) {
  return {
    id: x.id, lineage: x.lineage_id || x.id, label: x.label, labelText: LABEL_TEXT[x.label] || x.label, note: x.note || '',
    quote: x.quote || '', who: x.reviewer_email || 'Someone', role: ROLE_TEXT[x.reviewer_role] || x.reviewer_role,
    when: when(x.at), edited: !!x.edited, mine: !!me && x.reviewer_id === me, raw: x,
    expected: x.expected || '', onlyIf: x.only_if || '', group: x.group_id || ''
  };
}

// Labels grouped by step, the caller's first: { [step_key]: [label view] }. A person may give several labels on a step.
export function labelsByStep(labels, me) {
  const out = {};
  (labels || []).forEach((x) => {
    if (!x || x.cleared) return;
    (out[x.step_key] || (out[x.step_key] = [])).push(labelView(x, me));
  });
  Object.keys(out).forEach((k) => out[k].sort((a, b) => (a.mine === b.mine ? 0 : a.mine ? -1 : 1)));
  return out;
}

// The steps each group label covers on this answer, by step number: { [group_id]: [8, 9, 12] }.
export function groupsOf(labels, steps) {
  const n = {};
  (steps || []).forEach((st) => { n[st.key] = st.n; });
  const out = {};
  (labels || []).forEach((x) => {
    if (!x || x.cleared || !x.group_id || !(x.step_key in n)) return;
    const list = out[x.group_id] || (out[x.group_id] = []);
    if (list.indexOf(n[x.step_key]) < 0) list.push(n[x.step_key]);
  });
  Object.keys(out).forEach((g) => out[g].sort((a, b) => a - b));
  return out;
}

// "Steps 8, 9 and 12" for a list of step numbers.
export function stepsText(ns) {
  const list = (ns || []).slice();
  if (!list.length) return '';
  if (list.length === 1) return 'Step ' + list[0];
  return 'Steps ' + list.slice(0, -1).join(', ') + ' and ' + list[list.length - 1];
}

// How many steps of an answer carry at least one label of the caller's.
export function progressLine(steps, byStep) {
  const n = (steps || []).length;
  const done = (steps || []).filter((s) => (byStep[s.key] || []).some((x) => x.mine)).length;
  return n ? done + ' of ' + n + ' steps labelled by you' : '';
}

export function tallyText(byLabel) {
  const b = byLabel || {};
  return LABEL_CHOICES.map((c) => c.text + ' ' + (b[c.key] || 0)).join(' · ');
}

export function questionRow(r) {
  return { key: r.key, text: r.first_question || r.session_label || r.title || '—', when: when(r.last_turn_at),
    turns: r.turns_in_window || 1, asker: (r.asker && r.asker.email) || '' };
}

export function memberRow(m) {
  return { id: m.id, userId: m.user_id, email: m.email || '—', role: m.role, roleText: ROLE_TEXT[m.role] || m.role,
    status: m.status, statusText: STATUS_TEXT[m.status] || m.status, since: when(m.since), by: m.decided_by_email || '' };
}

export const errText = (e) => (e && e.message) || 'The service did not answer. Try again.';

// The text a person selected inside one step's element, or '' when the selection is empty or reaches outside it.
export function selectedIn(el, sel) {
  if (!el || !sel || sel.isCollapsed || !sel.rangeCount) return '';
  const a = sel.anchorNode;
  const f = sel.focusNode;
  if (!a || !f || !el.contains(a) || !el.contains(f)) return '';
  return String(sel.toString() || '').trim().slice(0, 2000);
}
export const isStale = (e) => !!(e && e.status === 409);
