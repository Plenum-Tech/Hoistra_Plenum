// traceTree — the Trace tab's pure helpers (9 Nov 2026). The service sends one answer as Langfuse's tree: `nodes` in
// depth-first order (parent before children), each with id, parent_id, depth, type (agent | generation | tool | span),
// name, started_at, ended_at, latency_ms, cost_usd. These helpers decide which rows show, where each step's bar sits on
// the timeline, the graph of agents, model calls and tools, and Langfuse's number formats. Nothing here draws.

const sid = (v) => (v == null ? null : String(v));
const listOf = (nodes) => (Array.isArray(nodes) ? nodes : []);
const ms = (iso) => { const t = iso ? Date.parse(iso) : NaN; return Number.isFinite(t) ? t : null; };
const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);

// The rows the tree shows. A collapsed step keeps its row and hides what is under it. A search (on the step's name,
// any case) shows the matching steps with the steps above them, and opens everything so a match is never hidden.
export function visibleRows(nodes, collapsed, query) {
  const list = listOf(nodes);
  const byId = new Map(list.map((x) => [sid(x.id), x]));
  const parents = new Set(list.map((x) => sid(x.parent_id)).filter((p) => p !== null));
  const q = String(query || '').trim().toLowerCase();
  let keep = null;
  if (q) {
    keep = new Set();
    for (const x of list) {
      if (!String(x.name || '').toLowerCase().includes(q)) continue;
      const seen = new Set();
      for (let cur = x; cur && !seen.has(sid(cur.id)); cur = byId.get(sid(cur.parent_id))) {
        seen.add(sid(cur.id));
        keep.add(sid(cur.id));
      }
    }
  }
  const shut = collapsed instanceof Set ? collapsed : new Set(collapsed || []);
  const closed = new Set();
  const out = [];
  for (const x of list) {
    const id = sid(x.id);
    const p = sid(x.parent_id);
    if ((p !== null && closed.has(p)) || (keep && !keep.has(id))) { closed.add(id); continue; }
    const open = q ? true : !shut.has(id);
    if (!open) closed.add(id);
    out.push({ node: x, depth: Number(x.depth) || 0, hasKids: parents.has(id), open });
  }
  return out;
}

// One bar per step on a shared axis from the turn's start to the latest end any step reached. Percentages are kept
// finite and inside the axis even for a step with no times, or one that ends before it starts.
// The shared time axis: from the turn's start to the latest end any step reached (ms), at least 1 ms long.
export function axisOf(nodes, turn) {
  const list = listOf(nodes);
  const starts = list.map((x) => ms(x.started_at)).filter((v) => v !== null);
  const t0 = ms(turn && turn.started_at) ?? (starts.length ? Math.min(...starts) : 0);
  let end = ms(turn && turn.ended_at) ?? t0;
  for (const x of list) {
    for (const v of [ms(x.started_at), ms(x.ended_at)]) if (v !== null && v > end) end = v;
  }
  return { t0, ms: Math.max(end - t0, 0), axis: Math.max(end - t0, 1) };
}

export function timelineBars(nodes, turn) {
  const list = listOf(nodes);
  const { t0, axis } = axisOf(list, turn);
  return list.map((x) => {
    const s = ms(x.started_at);
    const e = ms(x.ended_at);
    const left = clamp(s === null ? 0 : ((s - t0) / axis) * 100, 0, 99.5);
    const width = clamp(s === null || e === null ? 0 : ((e - s) / axis) * 100, 0.5, 100 - left);
    return { id: sid(x.id), depth: Number(x.depth) || 0, leftPct: left, widthPct: width };
  });
}

// The graph Langfuse draws (9 Nov 2026): one node per step name, in the order the steps first ran, each holding the ids of
// its runs in order; an arrow from each node to the next. A step that ran several times is one node, as in Langfuse.
// 10 Nov 2026: a node is one name AND one kind of step. A 'plan' stage and a 'plan' model call are different steps; grouped
// by name alone they drew as one node with the first one's icon. `key` tells the nodes apart.
export function flowOf(nodes) {
  const list = listOf(nodes).map((x, i) => ({ x, i, t: ms(x.started_at) }));
  list.sort((a, b) => (a.t === null || b.t === null ? 0 : a.t - b.t) || a.i - b.i);
  const steps = [];
  const byKey = new Map();
  for (const { x } of list) {
    const name = String(x.name || '');
    const type = x.type;
    const key = String(type || '') + ':' + name;
    if (!byKey.has(key)) { byKey.set(key, { key, name, type, ids: [] }); steps.push(byKey.get(key)); }
    byKey.get(key).ids.push(sid(x.id));
  }
  const edges = steps.slice(1).map((st, k) => ({ from: steps[k].name, to: st.name, fromKey: steps[k].key, toKey: st.key }));
  return { steps, edges };
}

// Each step's cost as Langfuse's timeline shows it: what its model calls (and its own, if it is one) cost, summed down
// its subtree. A turn's own recorded total is not added again.
export function subtreeCost(nodes) {
  const list = listOf(nodes);
  const kids = new Map();
  for (const x of list) {
    const p = sid(x.parent_id);
    if (p !== null) { if (!kids.has(p)) kids.set(p, []); kids.get(p).push(x); }
  }
  const out = new Map();
  const walk = (x, seen) => {
    const id = sid(x.id);
    if (out.has(id)) return out.get(id);
    if (seen.has(id)) return 0;
    seen.add(id);
    let sum = x.type === 'generation' ? (num(x.cost_usd) || 0) : 0;
    for (const k of kids.get(id) || []) sum += walk(k, seen);
    out.set(id, sum);
    return sum;
  };
  for (const x of list) walk(x, new Set());
  return out;
}

const STEPS_MS = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 15000, 30000, 60000, 120000, 300000, 600000, 1800000, 3600000];

// The ruler above the timeline: round marks (no more than six steps), each with its place on the axis.
export function timeTicks(total) {
  const t = num(total) || 0;
  if (t <= 0) return [{ ms: 0, label: fmtDur(0), pct: 0 }];
  const step = STEPS_MS.find((s) => t / s <= 6) || Math.ceil(t / 6);
  const out = [];
  for (let v = 0; v <= t + 1e-9; v += step) out.push({ ms: v, label: fmtDur(v), pct: (v / t) * 100 });
  return out;
}

const num = (v) => (v == null || v === '' || !Number.isFinite(Number(v)) ? null : Number(v));
const trimZeros = (s) => s.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '');

// 0.01s, 2.71s, 18.79s; a minute or more as 1m 02s.
export function fmtTime(v) {
  const x = num(v);
  if (x === null) return '';
  if (x < 60000) return (x / 1000).toFixed(2) + 's';
  const s = Math.floor(x / 1000);
  return Math.floor(s / 60) + 'm ' + String(s % 60).padStart(2, '0') + 's';
}

// A duration as Langfuse's timeline writes it: 101ms under a second, then 2.05s, then 1m 02s.
export function fmtDur(v) {
  const x = num(v);
  if (x === null) return '';
  return x < 1000 ? Math.round(x) + 'ms' : fmtTime(x);
}

// A step's cost to six decimals, trailing zeros trimmed: $0.00769, $0.034392.
export function fmtCost(v) {
  const x = num(v);
  return x === null ? '' : '$' + trimZeros(x.toFixed(6));
}

// The answer's whole cost, as Langfuse's header shows it: $0.113.
export function fmtTotalCost(v) {
  const x = num(v);
  return x === null ? '' : '$' + x.toFixed(3);
}

// 950 tokens, 2.2k tokens, 50k tokens.
export function fmtTokens(v) {
  const x = num(v);
  if (x === null) return '';
  if (x >= 10000) return Math.round(x / 1000) + 'k tokens';
  if (x >= 1000) return trimZeros((x / 1000).toFixed(1)) + 'k tokens';
  return Math.round(x) + ' tokens';
}

// A step's start in the reader's own time, to the millisecond: 2026-11-09 17:38:22.650.
export function fmtStart(iso) {
  const t = ms(iso);
  if (t === null) return '';
  const d = new Date(t);
  const p = (v, w) => String(v).padStart(w || 2, '0');
  return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + ' ' + p(d.getHours()) + ':'
    + p(d.getMinutes()) + ':' + p(d.getSeconds()) + '.' + p(d.getMilliseconds(), 3);
}
