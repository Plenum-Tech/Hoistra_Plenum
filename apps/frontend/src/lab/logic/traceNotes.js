// traceNotes — notes in the trace (9 Nov 2026). The question page shows an answer only as its trace, and every note is
// written on a trace step's input or output. Notes keep the step keys Trace Lab has always used, so the Labels, Notes,
// Learning, Loop and Progress pages read them unchanged and older notes land where they were written:
//
//   chat (the root)      input: the record's question message id      output: its answer message id
//   an agent             '<turn>:<span>:agent'                         + '#output'
//   any other step       the first message of that span                + '#output'
//   the question itself  '<turn>:scenario' (the comment box above the trace)
//
// A note whose key matches no step ('<turn>:orchestrator', a step since purged) goes under chat as Other notes.

export const OUTPUT = '#output';
const sid = (v) => (v == null ? null : String(v));

const rootOf = (nodes) => (nodes || []).find((n) => n && n.parent_id == null) || null;

// Each node's note keys and the titles new notes on it are saved with.
export function nodeKeys(record, nodes) {
  const out = new Map();
  const list = Array.isArray(nodes) ? nodes : [];
  const msgs = (record && Array.isArray(record.messages)) ? record.messages : [];
  const tid = String((record && record.id) || '');
  const root = rootOf(list);
  const first = new Map();
  for (const m of msgs) {
    const s = sid(m && m.span_id);
    if (s !== null && !first.has(s)) first.set(s, String(m.id));
  }
  const user = msgs.find((m) => m && m.role === 'user');
  const answers = msgs.filter((m) => m && m.role === 'assistant');
  // The agent a step ran in (its nearest agent above it, chat aside), named in its notes' titles: "Input · model
  // (maintenance)". Learning files a note against the engine its title names (10 Nov 2026).
  const byId = new Map(list.map((n) => [sid(n.id), n]));
  const agentOf = (n) => {
    const seen = new Set();
    for (let p = byId.get(sid(n.parent_id)); p && !seen.has(sid(p.id)); p = byId.get(sid(p.parent_id))) {
      seen.add(sid(p.id));
      if (root && sid(p.id) === sid(root.id)) return null;
      if (p.type === 'agent') return String(p.name || '') || null;
    }
    return null;
  };
  for (const n of list) {
    const id = sid(n.id);
    const agent = n.type === 'agent' ? null : agentOf(n);
    const name = String(n.name || '') + (agent ? ' (' + agent + ')' : '');
    let k = { in: null, out: null };
    if (root && id === sid(root.id)) {
      k = { in: user ? String(user.id) : null, out: answers.length ? String(answers[answers.length - 1].id) : null };
    } else if (n.type === 'agent' && tid) {
      k = { in: tid + ':' + id + ':agent', out: tid + ':' + id + ':agent' + OUTPUT };
    } else if (first.has(id)) {
      k = { in: first.get(id), out: first.get(id) + OUTPUT };
    }
    out.set(id, Object.assign(k, { inTitle: 'Input · ' + name, outTitle: 'Output · ' + name }));
  }
  return out;
}

// { [note key]: the title a new note on it is saved with }.
export function keyTitles(keys) {
  const out = {};
  for (const k of keys.values()) {
    if (k.in) out[k.in] = k.inTitle;
    if (k.out) out[k.out] = k.outTitle;
  }
  return out;
}

const atOf = (n) => { const t = Date.parse(n.at); return Number.isNaN(t) ? 0 : t; };
const order = (a, b) => (a.mine !== b.mine ? (a.mine ? -1 : 1) : atOf(b) - atOf(a));

// Where a note key sits in the trace: '<node>:in' / '<node>:out', or null when it matches no step. The note's own key
// first, then any message of a step (older notes were left on a step's other messages, e.g. a tool's result).
function placer(record, list, keys) {
  const where = new Map();
  for (const [id, k] of keys) {
    if (k.in) where.set(k.in, id + ':in');
    if (k.out) where.set(k.out, id + ':out');
  }
  const msgs = (record && Array.isArray(record.messages)) ? record.messages : [];
  const known = new Set(list.map((n) => sid(n.id)));
  const spanOf = new Map(msgs.map((m) => [String(m.id), sid(m.span_id)]));
  return (key) => {
    if (where.has(key)) return where.get(key);
    const out_ = key.endsWith(OUTPUT);
    const base = out_ ? key.slice(0, -OUTPUT.length) : key;
    const span = spanOf.get(base);
    if (span && known.has(span)) return span + (out_ ? ':out' : ':in');
    return null;
  };
}

// The step and side a note key belongs to ({ id, side: 'in' | 'out' }), or null for the question comment, a key that
// matches no step, or no key. The Notes page opens a question on the step its note was written on (10 Nov 2026).
export function stepOfKey(record, nodes, key) {
  const k = key == null ? '' : String(key);
  if (!k) return null;
  const list = Array.isArray(nodes) ? nodes : [];
  const slot = placer(record, list, nodeKeys(record, list))(k);
  if (!slot) return null;
  const at = slot.lastIndexOf(':');
  return { id: slot.slice(0, at), side: slot.slice(at + 1) };
}

// Everyone's notes by trace step and side: slots['<node>:in' | '<node>:out'], the question comment (scenario), notes
// that match no step (other, shown under chat), and how many notes each step holds (counts, for the tree's badges).
// `viewOf(label, me)` is the question page's noteView, passed in so this module imports nothing from the page. Each
// note's `block` is the key of the place it shows under ('scenario', 'other', or that side's note key), where the page
// opens its editor and shows its errors.
export function traceNotesIndex(labels, record, nodes, me, viewOf) {
  const out = { slots: {}, scenario: [], other: [], counts: {} };
  const list = Array.isArray(nodes) ? nodes : [];
  const tid = String((record && record.id) || '');
  const root = rootOf(list);
  const keys = nodeKeys(record, list);
  const place = placer(record, list, keys);
  for (const x of Array.isArray(labels) ? labels : []) {
    if (!x || x.cleared) continue;
    const v = viewOf(x, me);
    const key = String(x.step_key || '');
    if (tid && key === tid + ':scenario') { v.block = 'scenario'; out.scenario.push(v); continue; }
    const slot = place(key);
    if (slot) {
      const [nid, side] = [slot.slice(0, slot.lastIndexOf(':')), slot.slice(slot.lastIndexOf(':') + 1)];
      const k = keys.get(nid) || {};
      v.block = (side === 'in' ? k.in : k.out) || key;
      (out.slots[slot] || (out.slots[slot] = [])).push(v);
      const id = slot.slice(0, slot.lastIndexOf(':'));
      out.counts[id] = (out.counts[id] || 0) + 1;
    } else {
      v.block = 'other';
      out.other.push(v);
      if (root) out.counts[sid(root.id)] = (out.counts[sid(root.id)] || 0) + 1;
    }
  }
  Object.values(out.slots).forEach((l) => l.sort(order));
  out.scenario.sort(order);
  out.other.sort(order);
  return out;
}
