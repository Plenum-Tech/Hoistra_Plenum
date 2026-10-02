// corrections — teach the agent from the run trace, in the chat.
//
// After a turn lands, the rail beside the answer links itself to the stored run (the turn_id
// the completion carries; Hoist Traces keeps the spans) and offers "Correct" on the plan, on
// each tool call and on each query beneath it. The drawer shows what ran - the statement, its
// rows - and the common corrections as choices: exclude a status, change the period, date by
// another field, or just say what was wrong. Two things can follow:
//
//   Re-answer     the correction goes back through the orchestrator as a follow-up in the same
//                 thread, so the corrected answer is a new, traced turn and the old one stays
//                 as evidence;
//   Save as teaching  the correction becomes a company `correction` memory (chat_memories):
//                 recalled into the prompt of similar questions from then on.
//
// The before -> after preview for "exclude a status" is computed here from the rows the query
// returned, so a reader sees the effect before anything is sent. Everything else is read-only
// until the reader acts. Pure functions are tested in test/corrections.test.mjs.
import { deepAgentsApi } from '../api/deepAgents.js';

export const PERIODS = [
  { key: '', label: 'Keep the period' }, { key: 'last_month', label: 'Last month' }, { key: 'this_month', label: 'This month' },
  { key: 'last_90_days', label: 'Last 90 days' }, { key: 'this_year', label: 'This year' }
];
export const ENGINES = [
  { key: 'wo_engine', label: 'Maintenance / work orders' }, { key: 'compliance', label: 'Compliance' },
  { key: 'contract_performance', label: 'Contract performance' }, { key: 'energy_intelligence', label: 'Energy' },
  { key: 'planner', label: 'Plan it in steps' }
];
export const DATE_FIELDS = [
  { key: '', label: 'Keep the date field' }, { key: 'raised_at', label: 'Date by raised_at' },
  { key: 'reported_at', label: 'Date by reported_at' }, { key: 'completed_at', label: 'Date by completed_at' }
];

const parse = (v) => { if (v === null || v === undefined) return null; if (typeof v === 'string') { try { return JSON.parse(v); } catch (e) { return v; } } return v; };

// The column of a query's rows that holds a status, if any.
export function statusColumn(rows) {
  if (!rows || !rows.length || typeof rows[0] !== 'object') return null;
  const cols = Object.keys(rows[0]);
  return cols.find((c) => /status$/i.test(c)) || cols.find((c) => /status/i.test(c)) || null;
}

// Before -> after for "exclude these statuses": totals when the rows are counts per status,
// row counts otherwise. Returns null when the rows carry no status to exclude by.
export function previewExclude(rows, excluded) {
  const col = statusColumn(rows);
  if (!col) return null;
  const ex = new Set((excluded || []).map((x) => String(x).toLowerCase()));
  const countCol = Object.keys(rows[0]).find((c) => /^count$|_count$|^n$/i.test(c) && typeof rows[0][c] !== 'object');
  const val = (r) => (countCol ? Number(r[countCol]) || 0 : 1);
  const kept = rows.filter((r) => !ex.has(String(r[col]).toLowerCase()));
  const lines = rows.map((r) => ({ status: String(r[col]), before: val(r), after: ex.has(String(r[col]).toLowerCase()) ? 0 : val(r), excluded: ex.has(String(r[col]).toLowerCase()) }));
  return {
    column: col, statuses: Array.from(new Set(rows.map((r) => String(r[col])))),
    before: rows.reduce((a, r) => a + val(r), 0), after: kept.reduce((a, r) => a + val(r), 0), lines: lines
  };
}

// The stored run's tool spans with their queries, in order, so the rail can hang them under
// the tool rows the stream produced. Matched by tool name in sequence: the rail's rows and the
// store's spans are the same calls in the same order, but only the store knows the queries.
export function runChildren(spans) {
  const list = (spans || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  const tools = list.filter((s) => s.kind === 'tool');
  return tools.map((t) => ({
    id: t.id, name: t.name, ok: t.ok !== false, latency: t.latency_ms,
    input: parse(t.input), output: parse(t.output),
    queries: list.filter((q) => q.kind === 'db' && q.parent_id === t.id).map((q) => {
      const inp = parse(q.input) || {}; const out = parse(q.output) || {};
      return { id: q.id, name: q.name, sql: inp.sql || '', params: inp.params || {}, rows: Array.isArray(out.rows) ? out.rows : [], rowCount: out.row_count, ok: q.ok !== false, error: q.error || '' };
    })
  }));
}

// Which drawer a span of each kind opens, and how the rail draws it.
export const KIND_MODE = { router: 'route', plan: 'plan', step: 'plan', agent: 'agent', llm: 'model', stage: 'model', tool: 'tool', db: 'query' };
const KIND_ICON = { router: 'ph-signpost', plan: 'ph-list-checks', step: 'ph-arrow-elbow-down-right', agent: 'ph-tree-structure', llm: 'ph-brain', stage: 'ph-steps', tool: 'ph-wrench', db: 'ph-database' };
const fmtMs = (v) => (v === null || v === undefined ? '' : v < 1000 ? Math.round(v) + ' ms' : (v / 1000).toFixed(v < 10000 ? 1 : 0) + ' s');
const fmtUsd = (v) => { const n = Number(v); return !isFinite(n) || v === null || v === undefined ? '' : n < 0.01 ? '$' + n.toFixed(4) : '$' + n.toFixed(3); };

// The stored run as rail rows: depth-first, every span but the root, each with the drawer it
// opens. This replaces the stream's rows once the run is on record, so the rail shows every
// agent and stage - router, plan and steps, engines, model stages, tools, queries - not only
// what the stream happened to announce. Pure apart from the `open(spanId, mode)` callback.
export function rowsFromRun(spans, open) {
  const list = (spans || []).slice().sort((a, b) => (a.seq || 0) - (b.seq || 0));
  if (!list.length) return [];
  const root = list.find((s) => s.kind === 'turn') || list[0];
  const byParent = {};
  list.forEach((s) => { if (s.id === root.id) return; const p = list.some((x) => x.id === s.parent_id) ? s.parent_id : root.id; (byParent[p] = byParent[p] || []).push(s); });
  const out = [];
  const walk = (s, depth) => {
    (byParent[s.id] || []).forEach((k) => {
      const inp = parse(k.input) || {}; const o = parse(k.output) || {};
      const ok = k.ok !== false;
      let detail = '';
      if (k.kind === 'router') detail = typeof o.model_output === 'object' && o.model_output ? (o.model_output.agent ? '→ ' + o.model_output.agent + (o.model_output.reason ? ' — ' + o.model_output.reason : '') : '') : (typeof o.model_output === 'string' ? o.model_output : '');
      else if (k.kind === 'plan') detail = o.rejected ? 'Rejected: ' + o.rejected : ((o.plan && o.plan.goal) || (o.plan && o.plan.mode === 'single' ? 'One step: ' + ((o.plan.steps || [])[0] || {}).target : ''));
      else if (k.kind === 'step') detail = typeof inp.ask === 'string' ? inp.ask.slice(0, 160) : '';
      else if (k.kind === 'llm' && o && Array.isArray(o.tool_calls) && o.tool_calls.length) detail = 'decided: ' + o.tool_calls.map((t) => t.name).join(', ');
      else if (k.kind === 'llm' && typeof o.content === 'string' && o.content) detail = o.content.slice(0, 120);
      else if (k.kind === 'db') detail = (o.row_count === null || o.row_count === undefined ? '' : o.row_count + (o.row_count === 1 ? ' row' : ' rows'));
      if (!ok && k.error) detail = 'Failed: ' + k.error;
      out.push({
        key: k.id, icon: KIND_ICON[k.kind] || 'ph-circle', mono: k.kind === 'tool' || k.kind === 'db',
        title: k.name + (k.model ? ' · ' + k.model : ''), detail: detail,
        meta: [fmtMs(k.latency_ms), fmtUsd(k.cost_usd)].filter(Boolean).join(' · '),
        depth: depth, ok: ok, parts: [], issues: [],
        correct: () => open(k.id, KIND_MODE[k.kind] || 'suggestion')
      });
      walk(k, depth + 1);
    });
  };
  walk(root, 0);
  return out;
}

export function planSpan(spans) {
  const p = (spans || []).find((s) => s.kind === 'plan');
  if (!p) return null;
  const out = parse(p.output) || {};
  return { id: p.id, plan: out.plan || null, rejected: out.rejected || null };
}

// What the reader asked for, as one sentence the orchestrator (or a memory) can act on.
export function correctionText(edits, context) {
  const parts = [];
  if ((edits.exclude || []).length) parts.push('exclude work orders with status ' + edits.exclude.map((s) => '"' + s + '"').join(', ') + ' from "raised"');
  if (edits.period) parts.push('use the period ' + (PERIODS.find((p) => p.key === edits.period) || {}).label.toLowerCase());
  if (edits.field) parts.push('date work orders by ' + edits.field);
  if (edits.route) parts.push(edits.route === 'planner' ? 'plan questions like this in steps rather than sending them to one engine'
    : 'route questions like this to the ' + ((ENGINES.find((e) => e.key === edits.route) || {}).label || edits.route).toLowerCase() + ' engine');
  if (edits.note && edits.note.trim()) parts.push(edits.note.trim());
  const body = parts.join('; ');
  if (!body) return '';
  const where = context && context.building ? ' at ' + context.building : '';
  const who = context && context.agent ? 'For the ' + context.agent + ': ' : '';
  const text = body.charAt(0).toUpperCase() + body.slice(1);
  return who + (who ? text.charAt(0).toLowerCase() + text.slice(1) : text) + where + (edits.why && edits.why.trim() ? '. Reason: ' + edits.why.trim() : '') + '.';
}

export const correctionMethods = {
  // The stored run for a finished turn; the trace flushes a moment after the answer, so a
  // first miss is retried once.
  async crLoadRun(turnId, attempt) {
    if (!turnId || (this.state.crRuns || {})[turnId]) return;
    try {
      const out = await deepAgentsApi.traceTurn(turnId);
      if (out && out.turn) this.setState((p) => ({ crRuns: Object.assign({}, p.crRuns || {}, { [turnId]: out.turn }) }));
    } catch (e) {
      if (!attempt) setTimeout(() => this.crLoadRun(turnId, 1), 1500);
    }
  },
  crOpen(turnId, spanId, mode) {
    this.setState({ crOpen: { turnId: turnId, spanId: spanId || null, mode: mode || 'query' }, crExclude: [], crPeriod: '', crField: '', crRoute: '', crWhy: '', crNote: '', crMsg: '' });
  },
  crClose() { this.setState({ crOpen: null }); },
  crToggleExclude(status) {
    this.setState((p) => { const ex = (p.crExclude || []).slice(); const i = ex.indexOf(status); if (i < 0) ex.push(status); else ex.splice(i, 1); return { crExclude: ex }; });
  },
  crSetPeriod(e) { this.setState({ crPeriod: e.target.value }); },
  crSetField(e) { this.setState({ crField: e.target.value }); },
  crSetRoute(key) { this.setState((p) => ({ crRoute: p.crRoute === key ? '' : key })); },
  crSetWhy(e) { this.setState({ crWhy: e.target.value }); },
  crSetNote(e) { this.setState({ crNote: e.target.value }); },
  crEdits() { const s = this.state; return { exclude: s.crExclude || [], period: s.crPeriod || '', field: s.crField || '', route: s.crRoute || '', why: s.crWhy || '', note: s.crNote || '' }; },
  crContext() {
    const o = this.state.crOpen; const run = o && (this.state.crRuns || {})[o.turnId];
    const prompt = run && (run.spans || []).find((s) => s.name === 'prompt assembled');
    const ws = prompt ? ((parse(prompt.input) || {}).working_set || null) : null;
    const span = run && o.spanId ? (run.spans || []).find((x) => x.id === o.spanId) : null;
    // An engine, a model stage or the router is addressed by name; a tool or query is about data.
    const agent = span && (o.mode === 'agent' || o.mode === 'model' || o.mode === 'route')
      ? (span.kind === 'agent' ? span.name + ' engine' : span.kind === 'router' ? 'router' : (span.agent && span.agent !== 'orchestrator' ? span.agent + ' ' : '') + span.name + ' stage')
      : '';
    return { question: run ? run.question : '', building: ws && (ws.building_name || ws.building_code) || '', agent: agent };
  },
  // The correction goes back through the orchestrator as a follow-up: a new, traced turn.
  crReanswer() {
    const text = correctionText(this.crEdits(), this.crContext());
    if (!text) { this.setState({ crMsg: 'Choose or write a correction first.' }); return; }
    this.setState({ crOpen: null });
    this.ccAsk('Re-answer the previous question with this correction: ' + text + ' Keep the same building and period unless the correction changes them.');
  },
  // The same run, replayed with the correction applied at the step it was raised on: the steps
  // run again, the answer is rewritten and checked, and it lands as a new turn in this thread.
  async crRerun() {
    const o = this.state.crOpen;
    const text = correctionText(this.crEdits(), this.crContext());
    if (!o || !text) { this.setState({ crMsg: 'Choose or write a correction first.' }); return; }
    if (this.state.ccBusy) { this.setState({ crMsg: 'Still answering — wait for it to finish.' }); return; }
    const edits = this.crEdits();
    const body = { session_id: this.state.sessionId || 'rerun', corrections: [{ span_id: o.spanId || null, mode: o.mode, text: text, route: edits.route || null, args: null,
      exclude: edits.exclude.length ? edits.exclude : null, period: edits.period || null, field: edits.field || null }] };
    this.setState({ crOpen: null, ccBusy: true, ccStream: { steps: [], zones: {}, reasoning: 'Re-running the steps with your correction…', trace: [], answer: '' } });
    this.setState((p) => ({ ccChat: (p.ccChat || []).concat([{ role: 'you', text: 'Re-run with correction: ' + text, rerun: true }]) }));
    const t0 = Date.now();
    try {
      const out = await deepAgentsApi.traceRerun(o.turnId, body);
      this.setState((p) => ({
        ccBusy: false, ccStream: null,
        ccChat: (p.ccChat || []).concat([{ role: 'bot', text: out.answer || '', calls: (out.tool_calls || []).map((t) => t.tool).filter(Boolean),
          trace: [{ at: 0, kind: 'reasoning', label: 'Re-run', text: 'Steps replayed with your correction: ' + (out.applied || []).join('; ') }],
          ms: Date.now() - t0, turnId: out.turn_id || null, rerunOf: out.rerun_of || o.turnId }])
      }));
      if (out.turn_id) setTimeout(() => this.crLoadRun(out.turn_id), 800);
      this.setState((p) => ({ ccTraceIdx: (p.ccChat || []).length - 1 }));
    } catch (e) {
      this.setState((p) => ({ ccBusy: false, ccStream: null, ccChat: (p.ccChat || []).concat([{ role: 'bot', error: true, note: true, text: 'Could not re-run: ' + ((e && e.message) || e) }]) }));
    }
  },
  // The correction becomes a company memory, recalled on similar questions from now on.
  async crTeach() {
    const edits = this.crEdits(); const ctx = this.crContext();
    const text = correctionText(edits, ctx);
    if (!text) { this.setState({ crMsg: 'Choose or write a correction first.' }); return; }
    this.setState({ crBusy: true, crMsg: '' });
    try {
      const lead = ctx.question ? 'For questions like "' + ctx.question.slice(0, 140) + '": ' : '';
      const out = await deepAgentsApi.addMemory({ kind: 'correction', text: lead + text, subject: ctx.building || null, source_thread: this.state.sessionId || null });
      this.setState({ crBusy: false, crMsg: out && out.refreshed ? 'Already known — refreshed.' : 'Saved as a teaching. Colleagues asking similar questions get it from now on.' });
      if (typeof this.mpLoad === 'function' && this.state.mpLoadedAt) this.mpLoad();
    } catch (e) {
      this.setState({ crBusy: false, crMsg: 'Could not save: ' + ((e && e.message) || e) });
    }
  },
  async crRate(turnId, rating) {
    const cur = (this.state.crRatings || {})[turnId] || null;
    const next = cur === rating ? null : rating;
    this.setState((p) => ({ crRatings: Object.assign({}, p.crRatings || {}, { [turnId]: next }) }));
    try { await deepAgentsApi.traceFeedback(turnId, next, null); } catch (e) { this.flash('Could not save the rating: ' + ((e && e.message) || e)); }
  },
  // Rail rows (from the stream) joined to the stored run: queries under each tool, Correct on
  // tools and queries, Edit plan on the plan. Rows come back unchanged when the run is unknown.
  crAugmentRows(rows, msg) {
    const turnId = msg && msg.turnId;
    const run = turnId && (this.state.crRuns || {})[turnId];
    if (!run) return rows;
    // The stored run has every agent and stage; the stream rows had only what was announced.
    const full = rowsFromRun(run.spans, (spanId, mode) => this.crOpen(turnId, spanId, mode));
    if (full.length) return full;
    const tools = runChildren(run.spans);
    const plan = planSpan(run.spans);
    let t = 0;
    return rows.map((r) => {
      if (r.mono) {
        const tool = tools[t] && tools[t].name === r.title ? tools[t++] : tools.find((x) => x.name === r.title);
        if (!tool) return r;
        return Object.assign({}, r, {
          correct: () => this.crOpen(turnId, tool.id, 'tool'),
          children: tool.queries.map((q) => ({ key: q.id, name: q.name, meta: q.rowCount === null || q.rowCount === undefined ? '' : q.rowCount + (q.rowCount === 1 ? ' row' : ' rows'), ok: q.ok, pick: () => this.crOpen(turnId, q.id, 'query') }))
        });
      }
      if (r.title === 'Plan') return Object.assign({}, r, { editPlan: () => this.crOpen(turnId, plan ? plan.id : null, 'plan') });
      // The route the orchestrator took - its understanding of the question and the engine it
      // handed to - is correctable too: say which engine (or a plan) should have had it.
      if (/^Query understanding$|^Domain routing$|^Handed to /.test(r.title || '')) {
        const router = (run.spans || []).find((x) => x.kind === 'router');
        return Object.assign({}, r, { correct: () => this.crOpen(turnId, router ? router.id : null, 'route') });
      }
      return r;
    });
  }
};

export function correctionVals(c) {
  const s = c.state;
  const o = s.crOpen;
  const run = o && (s.crRuns || {})[o.turnId];
  if (!o || !run) return { crShow: false };
  const spans = run.spans || [];
  const span = spans.find((x) => x.id === o.spanId) || null;
  const inp = span ? parse(span.input) || {} : {};
  const out = span ? parse(span.output) || {} : {};
  const rows = Array.isArray(out.rows) ? out.rows : [];
  const preview = o.mode === 'query' ? previewExclude(rows, s.crExclude || []) : null;
  const edits = { exclude: s.crExclude || [], period: s.crPeriod || '', field: s.crField || '', route: s.crRoute || '', why: s.crWhy || '', note: s.crNote || '' };
  const ctx = c.crContext();
  const plan = o.mode === 'plan' ? planSpan(spans) : null;
  return {
    crShow: true,
    crMode: o.mode,
    crTitle: o.mode === 'plan' ? 'Edit the plan' : o.mode === 'route' ? 'Correct the route' : o.mode === 'suggestion' ? 'Suggest a correction'
      : o.mode === 'agent' ? 'Instruct · ' + (span ? span.name : '') + ' engine' : o.mode === 'model' ? 'Instruct · ' + (span ? span.name : '') : 'Correct · ' + (span ? span.name : ''),
    crStageInput: (o.mode === 'agent' || o.mode === 'model') ? (() => {
      const sys = inp.system_prompt; const um = inp.user_message;
      const head = typeof sys === 'string' ? 'SYSTEM PROMPT (' + sys.length + ' chars)\n' + sys.slice(0, 1200) + (sys.length > 1200 ? '\n…' : '') : '';
      const body = um !== undefined ? '\n\nINPUT\n' + (typeof um === 'string' ? um.slice(0, 1500) : JSON.stringify(um, null, 2).slice(0, 1500)) : (Object.keys(inp).length ? JSON.stringify(inp, null, 2).slice(0, 1500) : '');
      return (head + body).trim();
    })() : '',
    crStageOutput: (o.mode === 'agent' || o.mode === 'model') ? JSON.stringify(out, null, 2).slice(0, 2500) : '',
    crQuestion: run.question || '',
    crBuilding: ctx.building,
    crSql: o.mode === 'query' && typeof inp.sql === 'string' ? inp.sql.replace(/\s+/g, ' ').trim() : '',
    crParams: o.mode === 'query' && inp.params && Object.keys(inp.params).length ? JSON.stringify(inp.params) : '',
    crToolInput: o.mode === 'tool' ? JSON.stringify(inp, null, 2) : '',
    crToolOutput: o.mode === 'tool' ? JSON.stringify(out, null, 2).slice(0, 3000) : '',
    crPlanText: plan && plan.plan ? (plan.plan.steps || []).map((st) => st.id + ': ' + st.target + (st.ask ? ' — ' + st.ask : '')).join('\n')
      : (o.mode === 'plan' ? 'This run recorded no plan of its own: the router sent the whole question to one engine.' : ''),
    crRouteText: o.mode === 'route' ? (span ? JSON.stringify(out.model_output !== undefined ? out.model_output : out) : 'No router call recorded for this run.') : '',
    crRoutes: o.mode === 'route' || o.mode === 'plan' ? ENGINES.map((e) => ({ key: e.key, label: e.label, on: (s.crRoute || '') === e.key, pick: () => c.crSetRoute(e.key) })) : [],
    crStatuses: (preview ? preview.statuses : []).map((st) => ({ status: st, on: (s.crExclude || []).map(String).indexOf(st) > -1, toggle: () => c.crToggleExclude(st) })),
    crPreview: preview ? { column: preview.column, before: preview.before, after: preview.after, changed: preview.before !== preview.after, lines: preview.lines } : null,
    crPeriods: PERIODS.map((p) => ({ key: p.key, label: p.label })), crPeriod: s.crPeriod || '', crSetPeriod: (e) => c.crSetPeriod(e),
    crFields: DATE_FIELDS.map((f) => ({ key: f.key, label: f.label })), crField: s.crField || '', crSetField: (e) => c.crSetField(e),
    crWhy: s.crWhy || '', crSetWhy: (e) => c.crSetWhy(e),
    crNote: s.crNote || '', crSetNote: (e) => c.crSetNote(e),
    crText: correctionText(edits, ctx),
    crBusy: !!s.crBusy, crMsg: s.crMsg || '',
    crReanswer: () => c.crReanswer(), crRerun: () => c.crRerun(), crTeach: () => c.crTeach(), crClose: () => c.crClose()
  };
}
