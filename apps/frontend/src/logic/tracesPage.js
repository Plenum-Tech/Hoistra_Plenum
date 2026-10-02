// tracesPage — Administration › Hoist Traces: every turn the orchestrator answered, as a run
// list with cost, latency and status, and each run as a span tree with a waterfall — what went
// into every model and tool call and what came back (svc-deepagents agents/trace.py).
//
// Two uses: seeing how an answer was produced (observability, cost, delay, failures), and
// labelling turns (thumbs up/down) so the export is training data with a verdict on it.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller. The pure functions
// (buildTree, tracesPageVals, formatters) are tested in test/tracesPage.test.mjs.
import { deepAgentsApi } from '../api/deepAgents.js';
import { isStaleScope } from '../api/client.js';

export const RANGES = [{ key: 'today', label: 'Today', days: 1 }, { key: '7d', label: '7 days', days: 7 }, { key: '30d', label: '30 days', days: 30 }, { key: '90d', label: '90 days', days: 90 }];
const KIND_LABEL = { turn: 'Turn', agent: 'Agent', router: 'Router', llm: 'Model', tool: 'Tool', stage: 'Stage', db: 'Query', plan: 'Plan', step: 'Plan step' };

export const fmtUsd = (v) => {
  const n = Number(v);
  if (!isFinite(n)) return '—';
  if (n === 0) return '$0';
  return n < 0.01 ? '$' + n.toFixed(4) : n < 1 ? '$' + n.toFixed(3) : '$' + n.toFixed(2);
};
export const fmtMs = (v) => {
  const n = Number(v);
  if (!isFinite(n) || v === null || v === undefined) return '—';
  return n < 1000 ? Math.round(n) + ' ms' : (n / 1000).toFixed(n < 10000 ? 2 : 1) + ' s';
};
export const fmtTok = (v) => { const n = Number(v) || 0; return n >= 1000 ? (n / 1000).toFixed(n < 10000 ? 1 : 0) + 'k' : String(n); };
export const fmtWhen = (iso, now) => {
  const t = iso ? Date.parse(iso) : NaN;
  if (isNaN(t)) return '';
  const d = new Date(t); const n = new Date(now === undefined ? Date.now() : now);
  const hm = String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
  if (d.toDateString() === n.toDateString()) return 'today ' + hm;
  const y = new Date(n); y.setDate(n.getDate() - 1);
  if (d.toDateString() === y.toDateString()) return 'yesterday ' + hm;
  return d.getDate() + ' ' + ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][d.getMonth()] + ' ' + hm;
};

// The span rows as the tree shows them: depth-first, each with its depth, children count, and
// its place on the waterfall as a share of the turn (offset and width in percent). A span with
// no parent on record hangs from the root. Pure.
export function buildTree(spans) {
  const list = (spans || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  if (!list.length) return [];
  const root = list.find((s) => s.kind === 'turn') || list[0];
  const t0 = Date.parse(root.started_at) || Math.min(...list.map((s) => Date.parse(s.started_at) || Infinity));
  const t1 = Math.max(Date.parse(root.ended_at) || 0, ...list.map((s) => Date.parse(s.ended_at) || 0)) || t0 + 1;
  const total = Math.max(1, t1 - t0);
  const byParent = {};
  list.forEach((s) => { const p = s.id === root.id ? null : (list.some((x) => x.id === s.parent_id) ? s.parent_id : root.id); (byParent[p] = byParent[p] || []).push(s); });
  const out = [];
  const walk = (s, depth) => {
    const kids = byParent[s.id] || [];
    const st = Date.parse(s.started_at); const en = Date.parse(s.ended_at);
    const start = isNaN(st) ? 0 : Math.max(0, Math.min(100, ((st - t0) / total) * 100));
    const width = isNaN(st) || isNaN(en) ? 0 : Math.max(0.5, Math.min(100 - start, ((en - st) / total) * 100));
    out.push({ id: s.id, depth: depth, kind: s.kind, kindLabel: KIND_LABEL[s.kind] || s.kind, name: s.name, agent: s.agent, model: s.model,
      latency: s.latency_ms, cost: s.cost_usd, ok: s.ok !== false, children: kids.length, start: start, width: width, span: s });
    kids.forEach((k) => walk(k, depth + 1));
  };
  walk(root, 0);
  return out;
}

const sinceFor = (key, now) => {
  const r = RANGES.find((x) => x.key === key) || RANGES[1];
  const d = new Date(now === undefined ? Date.now() : now);
  if (r.key === 'today') { d.setHours(0, 0, 0, 0); return d.toISOString(); }
  d.setDate(d.getDate() - r.days);
  return d.toISOString();
};

export const tracesPageMethods = {
  tpOpen() {
    window.scrollTo(0, 0);
    this.setState({ view: 'traces', role: 'admin', navOpen: true, detail: null, tpTurn: null, tpSpanId: null });
    this.tpLoad();
  },
  async tpLoad() {
    const s = this.state;
    const since = sinceFor(s.tpRange || '7d');
    this.setState({ tpLoading: true, tpErr: '' });
    try {
      const [turns, stats] = await Promise.all([
        deepAgentsApi.traceTurns({ since: since, q: s.tpQuery || undefined, status: s.tpStatus || undefined, limit: 200 }),
        deepAgentsApi.traceStats({ since: since })
      ]);
      this.setState({ tpLoading: false, tpTurns: (turns && turns.turns) || [], tpStats: stats || null, tpAvailable: !turns || turns.available !== false, tpLoadedAt: Date.now(), tpCanManage: !!(turns && turns.can_manage) });
    } catch (e) {
      if (isStaleScope(e)) return;
      this.setState({ tpLoading: false, tpErr: (e && e.message) || String(e) });
    }
  },
  tpSetRange(key) { this.setState({ tpRange: key }); this.tpLoad(); },
  tpSetStatus(v) { this.setState({ tpStatus: v }); this.tpLoad(); },
  tpSetQuery(e) { this.setState({ tpQuery: e.target.value }); },
  tpSearch() { this.tpLoad(); },
  async tpOpenTurn(id) {
    window.scrollTo(0, 0);
    this.setState({ tpTurn: { turn_id: id, loading: true }, tpSpanId: null });
    try {
      const out = await deepAgentsApi.traceTurn(id);
      const turn = out && out.turn;
      const tree = buildTree(turn && turn.spans);
      // The first model call is the most useful thing to land on; else the root.
      const first = tree.find((n) => n.kind === 'llm') || tree[0];
      this.setState({ tpTurn: turn, tpSpanId: first ? first.id : null });
    } catch (e) {
      this.setState({ tpTurn: { turn_id: id, error: (e && e.message) || String(e) } });
    }
  },
  tpBack() { this.setState({ tpTurn: null, tpSpanId: null }); },
  tpSelectSpan(id) { this.setState({ tpSpanId: id }); },
  // The reader's verdict: the label that makes a turn training data. Clicking the same thumb
  // again clears it.
  async tpRate(turnId, rating) {
    const t = this.state.tpTurn;
    const next = t && t.feedback_rating === rating ? null : rating;
    try {
      await deepAgentsApi.traceFeedback(turnId, next, t && t.feedback_comment);
      this.setState((p) => ({
        tpTurn: p.tpTurn && p.tpTurn.turn_id === turnId ? Object.assign({}, p.tpTurn, { feedback_rating: next }) : p.tpTurn,
        tpTurns: (p.tpTurns || []).map((x) => (x.turn_id === turnId ? Object.assign({}, x, { feedback_rating: next }) : x))
      }));
    } catch (e) { this.flash('Could not save the rating: ' + ((e && e.message) || e)); }
  },
  tpSetComment(e) { this.setState((p) => ({ tpTurn: p.tpTurn ? Object.assign({}, p.tpTurn, { feedback_comment: e.target.value, commentDirty: true }) : p.tpTurn })); },
  async tpSaveComment() {
    const t = this.state.tpTurn;
    if (!t || !t.commentDirty) return;
    try {
      await deepAgentsApi.traceFeedback(t.turn_id, t.feedback_rating || null, t.feedback_comment || '');
      this.setState((p) => ({ tpTurn: Object.assign({}, p.tpTurn, { commentDirty: false }) }));
      this.flash('Saved.');
    } catch (e) { this.flash('Could not save the comment: ' + ((e && e.message) || e)); }
  },
  async tpExport(ratedOnly) {
    this.setState({ tpExporting: true });
    try {
      await deepAgentsApi.traceExport({ since: sinceFor(this.state.tpRange || '7d'), rated_only: !!ratedOnly });
    } catch (e) { this.flash('Export failed: ' + ((e && e.message) || e)); }
    this.setState({ tpExporting: false });
  }
};

export function tracesPageVals(c, now) {
  const s = c.state;
  const st = (s.tpStats && s.tpStats.totals) || {};
  const turns = s.tpTurns || [];
  const failed = Number(st.failed) || 0; const n = Number(st.turns) || 0;
  const days = (s.tpStats && s.tpStats.by_day) || [];
  const maxCost = Math.max(0.000001, ...days.map((d) => Number(d.cost_usd) || 0));
  const maxTurns = Math.max(1, ...days.map((d) => Number(d.turns) || 0));
  const t = s.tpTurn && !s.tpTurn.loading && !s.tpTurn.error ? s.tpTurn : null;
  const tree = t ? buildTree(t.spans) : [];
  const sel = tree.find((x) => x.id === s.tpSpanId) || tree[0] || null;
  const pretty = (v) => { if (v === null || v === undefined) return ''; if (typeof v === 'string') return v; try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); } };
  return {
    isTraces: s.signedIn && s.view === 'traces',
    tpLoading: !!s.tpLoading && !s.tpLoadedAt,
    tpError: s.tpErr || '',
    tpUnavailable: s.tpAvailable === false,
    tpCanManage: !!s.tpCanManage,
    tpReload: () => c.tpLoad(),
    tpRanges: RANGES.map((r) => ({ key: r.key, label: r.label, on: (s.tpRange || '7d') === r.key, pick: () => c.tpSetRange(r.key) })),
    tpStatuses: [{ key: '', label: 'All' }, { key: 'ok', label: 'Answered' }, { key: 'error', label: 'Failed' }].map((x) => ({ label: x.label, on: (s.tpStatus || '') === x.key, pick: () => c.tpSetStatus(x.key) })),
    tpQuery: s.tpQuery || '', tpSetQuery: (e) => c.tpSetQuery(e), tpSearch: () => c.tpSearch(),
    tpTiles: [
      { value: String(n), label: 'Turns', hint: (Number(st.users) || 0) + ' people · ' + (Number(st.llm_calls) || 0) + ' model calls · ' + (Number(st.tool_calls) || 0) + ' tool calls', tone: 'var(--color-text)' },
      { value: fmtUsd(st.cost_usd), label: 'LLM cost', hint: n ? fmtUsd((Number(st.cost_usd) || 0) / n) + ' per turn · ' + fmtTok(st.cache_read_tokens) + ' cached tokens' : 'nothing yet', tone: 'var(--color-text)' },
      { value: fmtMs(st.p50_ms), label: 'Latency p50', hint: 'p95 ' + fmtMs(st.p95_ms), tone: Number(st.p95_ms) > 60000 ? 'var(--st-warn)' : 'var(--color-text)' },
      { value: n ? Math.round((failed / n) * 100) + '%' : '—', label: 'Failed', hint: failed + (failed === 1 ? ' turn' : ' turns') + ' errored', tone: failed ? 'var(--st-risk)' : 'var(--st-ok)' },
      { value: (Number(st.thumbs_up) || 0) + ' / ' + (Number(st.thumbs_down) || 0), label: 'Rated up / down', hint: 'labels for the training export', tone: 'var(--color-text)' }
    ],
    tpDays: days.map((d) => ({ day: fmtWhen(d.day, now).replace(/ \d\d:\d\d$/, ''), turns: Number(d.turns) || 0, failed: Number(d.failed) || 0, cost: fmtUsd(d.cost_usd),
      costPct: Math.round(((Number(d.cost_usd) || 0) / maxCost) * 100), turnsPct: Math.round(((Number(d.turns) || 0) / maxTurns) * 100), p95: fmtMs(d.p95_ms) })),
    tpModels: ((s.tpStats && s.tpStats.by_model) || []).map((m) => ({ model: m.model, calls: Number(m.calls) || 0, cost: fmtUsd(m.cost_usd), tokens: fmtTok(m.input_tokens) + ' in · ' + fmtTok(m.output_tokens) + ' out · ' + fmtTok(m.cache_read_tokens) + ' cached', p50: fmtMs(m.p50_ms), failed: Number(m.failed) || 0 })),
    tpTools: ((s.tpStats && s.tpStats.by_tool) || []).map((x) => ({ name: x.name, calls: Number(x.calls) || 0, p50: fmtMs(x.p50_ms), p95: fmtMs(x.p95_ms), failed: Number(x.failed) || 0 })),
    tpRuns: turns.map((r) => ({
      id: r.turn_id, when: fmtWhen(r.started_at, now), who: r.email || '—', question: r.question || '(no question recorded)', answer: r.answer || '',
      llm: Number(r.llm_calls) || 0, tools: Number(r.tool_calls) || 0, cost: fmtUsd(r.cost_usd), costIncomplete: r.cost_complete === false,
      latency: fmtMs(r.latency_ms), ok: r.ok !== false, error: r.error || '', rating: r.feedback_rating || null,
      models: (r.models || []).join(', '), open: () => c.tpOpenTurn(r.turn_id)
    })),
    tpEmpty: !turns.length,
    tpExport: () => c.tpExport(false), tpExportRated: () => c.tpExport(true), tpExporting: !!s.tpExporting,
    // the selected run
    tpTurnLoading: !!(s.tpTurn && s.tpTurn.loading), tpTurnError: (s.tpTurn && s.tpTurn.error) || '',
    tpDetail: t ? {
      id: t.turn_id, when: fmtWhen(t.started_at, now), who: t.email || '—', question: t.question || '', answer: t.answer || '',
      cost: fmtUsd(t.cost_usd), costIncomplete: t.cost_complete === false, latency: fmtMs(t.latency_ms), llm: Number(t.llm_calls) || 0, tools: Number(t.tool_calls) || 0,
      tokens: fmtTok(t.input_tokens) + ' in · ' + fmtTok(t.output_tokens) + ' out · ' + fmtTok(t.cache_read_tokens) + ' cached',
      models: (t.models || []).join(', ') || '—', ok: t.ok !== false, error: t.error || '',
      rating: t.feedback_rating || null, rateUp: () => c.tpRate(t.turn_id, 'up'), rateDown: () => c.tpRate(t.turn_id, 'down'),
      comment: t.feedback_comment || '', setComment: (e) => c.tpSetComment(e), saveComment: () => c.tpSaveComment(), commentDirty: !!t.commentDirty,
      tree: tree.map((nd) => ({
        id: nd.id, depth: nd.depth, kind: nd.kind, kindLabel: nd.kindLabel, name: nd.name, model: nd.model || '', latency: fmtMs(nd.latency), cost: nd.cost !== null && nd.cost !== undefined ? fmtUsd(nd.cost) : '',
        ok: nd.ok, start: nd.start, width: nd.width, children: nd.children, on: sel && sel.id === nd.id, pick: () => c.tpSelectSpan(nd.id)
      })),
      span: sel ? {
        kindLabel: sel.kindLabel, name: sel.name, agent: sel.agent || '', model: sel.model || '', latency: fmtMs(sel.latency), cost: sel.cost !== null && sel.cost !== undefined ? fmtUsd(sel.cost) : '—',
        tokens: sel.span.input_tokens !== null && sel.span.input_tokens !== undefined ? fmtTok(sel.span.input_tokens) + ' in · ' + fmtTok(sel.span.output_tokens) + ' out' + (sel.span.cache_read_tokens ? ' · ' + fmtTok(sel.span.cache_read_tokens) + ' cached' : '') : '—',
        ok: sel.ok, error: sel.span.error || '', input: pretty(sel.span.input), output: pretty(sel.span.output),
        started: sel.span.started_at ? new Date(sel.span.started_at).toLocaleTimeString() : '',
        // A query span: the statement as the tool ran it and the rows the database answered.
        sql: sel.kind === 'db' && sel.span.input && typeof sel.span.input.sql === 'string' ? sel.span.input.sql : '',
        sqlParams: sel.kind === 'db' && sel.span.input && sel.span.input.params && Object.keys(sel.span.input.params).length ? pretty(sel.span.input.params) : '',
        rows: sel.kind === 'db' && sel.span.output && Array.isArray(sel.span.output.rows) ? sel.span.output.rows : null,
        rowCount: sel.kind === 'db' && sel.span.output ? (sel.span.output.row_count === null || sel.span.output.row_count === undefined ? null : Number(sel.span.output.row_count)) : null,
        rowsTruncated: sel.kind === 'db' && sel.span.output ? Number(sel.span.output.truncated) || 0 : 0
      } : null
    } : null,
    tpBack: () => c.tpBack()
  };
}
