// platformCost — the Super Admin console's Platform cost page (components/shell/PlatformCostPage.jsx):
// what each company cost to run in a month (chat and migration model spend, its share of the
// infrastructure) against the credits it was billed. GET /api/superadmin/platform-cost and
// /platform-cost/{id} (engines/platform_cost.py).
//
// pcVals() and pcPanelSheet() are pure; the methods are mixed into HoistraLogic.prototype and
// `this` is the controller.
import { superAdminApi } from '../api/superAdmin.js';

export function fmtUsd(v, digits) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  const n = Number(v);
  const d = typeof digits === 'number' ? digits : (Math.abs(n) < 1 ? 4 : 2);
  return (n < 0 ? '-$' : '$') + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: d, maximumFractionDigits: d });
}

export function fmtPct(v) {
  if (v === null || v === undefined || isNaN(Number(v))) return '—';
  return (Number(v) * 100).toFixed(1) + '%';
}

const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];

// Months travel as 'YYYY-MM', the key the API takes.
export function shiftMonth(key, delta) {
  const [y, m] = String(key).split('-').map(Number);
  const i = y * 12 + (m - 1) + delta;
  return Math.floor(i / 12) + '-' + String((i % 12) + 1).padStart(2, '0');
}

export function monthName(key) {
  const [y, m] = String(key).split('-').map(Number);
  return MONTHS[m - 1] + ' ' + y;
}

// The server counts months and days in UTC (engines/platform_cost.py month_bounds), so the page
// does too: in Dubai at 01:30 on 1 November it is still October on the platform.
const keyOf = (d) => d.getUTCFullYear() + '-' + String(d.getUTCMonth() + 1).padStart(2, '0');

function resolveMonth(m, now) {
  const here = keyOf(now);
  if (!m || m === 'this') return here;
  if (m === 'last') return shiftMonth(here, -1);
  return m;
}

const n0 = (v) => Number(v) || 0;
const r1 = (v) => Math.round(v * 10) / 10;
const r2 = (v) => Math.round(v * 100) / 100;
const pctOf = (v, scale) => (scale > 0 ? r1((n0(v) / scale) * 100) : 0);
const count = (v) => Math.round(n0(v)).toLocaleString('en-GB');
const share = (v) => (v === null || v === undefined ? '—' : Math.round(v * 100) + '%');

function tok(v) {
  const x = n0(v);
  return x >= 1e6 ? (x / 1e6).toFixed(2) + 'M' : x >= 1e3 ? (x / 1e3).toFixed(1) + 'k' : String(x);
}

// Margin as a reader judges it. The icon beside the value carries this, not colour alone.
// With nothing billed the margin is undefined; a loss is still a loss.
function tone(m, profit) {
  if (m === null || m === undefined) return n0(profit) < 0 ? 'loss' : null;
  return m < 0 ? 'loss' : m < 0.6 ? 'thin' : 'healthy';
}

function hhmm(d) {
  return String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');
}

export const platformCostMethods = {
  pcValsFor() { return pcVals(this); },
  async pcLoad(month) {
    const m = month || this.state.pcMonth || 'this';
    // Stepping through months quickly: only the last month asked for may land.
    const seq = (this._pcSeq = (this._pcSeq || 0) + 1);
    this.setState({ pcLoading: true, pcErr: '', pcMonth: m });
    try {
      const out = await superAdminApi.platformCost(m);
      if (seq !== this._pcSeq) return;
      this.setState({ pcLoading: false, pcData: out || null, pcLoadedAt: Date.now() });
      if (this.state.pcSel) this.pcOpen(this.state.pcSel, true);
    } catch (e) {
      if (seq !== this._pcSeq) return;
      this.setState({ pcLoading: false, pcErr: (e && e.message) || String(e) });
    }
  },
  // Click a company: its month split every way, in a side panel. A second click on the open
  // company closes it; `keep` re-reads the open one (a new month, or a retry).
  // It reads the month the table is showing (not one that failed to read), and only the
  // newest read may land: stepping months with the panel open sends a read per month.
  async pcOpen(id, keep) {
    if (!keep && this.state.pcSel === id) { this.pcClose(); return; }
    const seq = (this._pcDetailSeq = (this._pcDetailSeq || 0) + 1);
    const month = (this.state.pcData && this.state.pcData.month) || this.state.pcMonth || 'this';
    this.setState({ pcSel: id, pcDetailLoading: true, pcDetailErr: '', pcTab: keep ? (this.state.pcTab || 'overview') : 'overview' });
    try {
      const out = await superAdminApi.platformCostCompany(id, month);
      if (seq !== this._pcDetailSeq || this.state.pcSel !== id) return;
      this.setState({ pcDetailLoading: false, pcDetail: out || null });
    } catch (e) {
      if (seq !== this._pcDetailSeq || this.state.pcSel !== id) return;
      this.setState({ pcDetailLoading: false, pcDetailErr: (e && e.message) || String(e) });
    }
  },
  pcClose() { this._pcDetailSeq = (this._pcDetailSeq || 0) + 1; this.setState({ pcSel: null, pcDetail: null, pcDetailErr: '', pcDetailLoading: false }); },
  pcSetTab(t) { this.setState({ pcTab: t }); }
};

const TABS = [['overview', 'Overview'], ['queries', 'Queries'], ['models', 'Models & tools'], ['migrations', 'Migrations'], ['pricing', 'Pricing']];

// The one sentence the page leads with: what running the platform cost, what it billed, and
// the difference — in the data's own month, so it never contradicts the numbers under it.
function sentenceFor(t, month, now) {
  const cost = r2(n0(t.total_cost_usd));
  const billed = r2(n0(t.revenue_usd));
  // From the two figures printed beside it, not the engine's sum of rounded row profits.
  const profit = r2(billed - cost);
  const current = month === keyOf(now);
  const when = current ? MONTHS[Number(month.split('-')[1]) - 1] + ' so far' : 'In ' + monthName(month);
  const result = profit < 0 ? 'loss' : profit > 0 ? 'profit' : 'even';
  const parts = [
    { text: when + ', running the platform cost ' }, { text: fmtUsd(cost, 2), strong: true },
    { text: ' and it billed ' }, { text: fmtUsd(billed, 2), strong: true }];
  if (result === 'even') parts.push({ text: ', breaking even.' });
  else parts.push({ text: result === 'loss' ? ', a loss of ' : ', a profit of ' }, { text: fmtUsd(Math.abs(profit), 2), strong: true, tone: result }, { text: '.' });
  return { parts, result };
}

// How a cost source reads when the engine could not read it: chat is a real failure; the
// migration ledger not existing yet is expected and explained under "Not recorded yet".
function sourceValue(key, v, unreadable) {
  if (key === 'chat' && unreadable.includes('chat')) return 'not read';
  if (key === 'migration' && unreadable.includes('migration')) return 'not recorded yet';
  return fmtUsd(n0(v), 2);
}

function heroFor(d, now) {
  const t = d.totals || {};
  const unreadable = d.unreadable || [];
  const cost = n0(t.total_cost_usd);
  const billed = n0(t.revenue_usd);
  if (!cost && !billed) return null;
  const scale = Math.max(cost, billed);
  const { parts, result } = sentenceFor(t, d.month, now);
  const segs = [['infra', 'Infrastructure', t.infra_share_usd], ['chat', 'Chat models', t.chat_cost_usd], ['migration', 'Migration models', t.migration_cost_usd]];
  return {
    sentence: parts, result,
    bars: {
      cost: { value: fmtUsd(cost, 2), pct: pctOf(cost, scale), segments: segs.map(([key, label, v]) => ({ key, label, value: fmtUsd(n0(v), 2), pct: pctOf(v, scale) })) },
      billed: { value: fmtUsd(billed, 2), pct: pctOf(billed, scale) }
    },
    legend: segs.map(([key, label, v]) => ({ key, label, value: sourceValue(key, v, unreadable) })).concat([{ key: 'billed', label: 'Billed', value: fmtUsd(billed, 2) }])
  };
}

// The four cards the page opens with, each saying what its figure is made of. None for a
// month with nothing recorded — the page says so in words instead.
function cardsFor(d) {
  const t = d.totals || {};
  if (!n0(t.total_cost_usd) && !n0(t.revenue_usd)) return null;
  const a = d.assumptions || {};
  const unreadable = d.unreadable || [];
  const profit = r2(r2(n0(t.revenue_usd)) - r2(n0(t.total_cost_usd)));
  return [
    { key: 'cost', label: 'Platform cost', value: fmtUsd(t.total_cost_usd, 2),
      hint: 'Chat ' + sourceValue('chat', t.chat_cost_usd, unreadable) + ', migration ' + sourceValue('migration', t.migration_cost_usd, unreadable) + ', infrastructure ' + fmtUsd(t.infra_share_usd, 0) },
    { key: 'billed', label: 'Billed', value: fmtUsd(t.revenue_usd, 2), hint: count(t.credits) + ' credits at ' + fmtUsd(a.credit_usd, 2) },
    { key: 'margin', label: 'Margin', value: fmtPct(t.margin), tone: tone(t.margin, profit),
      hint: profit === 0 ? 'Breaking even' : fmtUsd(Math.abs(profit), 2) + (profit < 0 ? ' loss' : ' profit') },
    { key: 'perQuery', label: 'Model cost per query', value: fmtUsd(t.cost_per_query_usd),
      hint: count(t.chat_turns) + ' queries, ' + (t.active_companies || 0) + ' of ' + ((d.companies || []).length) + ' companies active' }
  ];
}

export function pcVals(c, now) {
  const at = now || new Date();
  const s = c.state;
  const d = s.pcData || null;
  const key = resolveMonth(s.pcMonth, at);
  const here = keyOf(at);
  const all = (d && d.companies) || [];
  const rows = all.filter((r) => r.active);
  const scale = Math.max(0, ...rows.map((r) => Math.max(n0(r.total_cost_usd), n0(r.revenue_usd))));
  const quiet = all.filter((r) => !r.active).map((r) => r.name);
  const t = (d && d.totals) || {};
  const a = (d && d.assumptions) || {};
  const unread = ((d && d.unreadable) || []).filter((u) => u !== 'migration');
  const dataMonth = (d && d.month) || key;
  // An empty month is a finding only when its activity was actually read.
  const activityUnread = unread.some((u) => u === 'chat' || u === 'billed' || u === 'companies');
  const sel = s.pcSel || null;
  // Only this company's detail for the month on screen: while another month's read is in
  // flight the panel shows the table's figures and a skeleton, never the old month's tabs.
  const detail = sel && s.pcDetail && s.pcDetail.organization_id === sel && s.pcDetail.month === dataMonth ? s.pcDetail : null;
  const detailUnread = ((detail && detail.unreadable) || []).filter((u) => u !== 'migrations');
  const selRow = sel ? (detail && detail.company) || all.find((r) => r.organization_id === sel) || null : null;
  const tab = TABS.some(([k]) => k === s.pcTab) ? s.pcTab : 'overview';
  return {
    // `showing`: the month the figures on screen belong to — not the month asked for, when that read failed.
    pcStatus: { loading: !d && !s.pcErr, refreshing: !!s.pcLoading && !!d, error: s.pcErr || '', retry: () => c.pcLoad(s.pcMonth || 'this'),
      showing: d ? monthName(dataMonth) : '' },
    pcMonth: {
      label: monthName(key), current: key === here,
      prev: () => c.pcLoad(shiftMonth(key, -1)),
      next: key >= here ? null : () => { const n = shiftMonth(key, 1); c.pcLoad(n >= here ? 'this' : n); }
    },
    pcReadAt: s.pcLoadedAt ? 'Read at ' + hhmm(new Date(s.pcLoadedAt)) : '',
    pcReload: () => c.pcLoad(s.pcMonth || 'this'),
    pcHero: d ? heroFor(d, at) : null,
    pcCards: d ? cardsFor(d) : null,
    pcEmpty: d && !rows.length && !activityUnread
      ? (dataMonth === here
        ? 'Nothing has been recorded for ' + monthName(dataMonth) + ' yet. Costs appear here once a company asks a question or runs a migration.'
        : 'Nothing was recorded for ' + monthName(dataMonth) + '. No company asked a question or ran a migration that month.')
      : '',
    pcRows: rows.map((r) => ({
      id: r.organization_id, name: r.name, selected: sel === r.organization_id, open: () => c.pcOpen(r.organization_id),
      queries: count(r.chat_turns),
      model: fmtUsd(n0(r.chat_cost_usd) + n0(r.migration_cost_usd), 2),
      infra: fmtUsd(r.infra_share_usd, 0), cost: fmtUsd(r.total_cost_usd, 2), billed: fmtUsd(r.revenue_usd, 2),
      profit: fmtUsd(r.profit_usd, 2), margin: fmtPct(r.margin), tone: tone(r.margin, r.profit_usd),
      bars: { infraPct: pctOf(r.infra_share_usd, scale), modelPct: pctOf(n0(r.chat_cost_usd) + n0(r.migration_cost_usd), scale), billedPct: pctOf(r.revenue_usd, scale) }
    })),
    pcQuiet: quiet.length ? { names: quiet, note: 'No infrastructure share is charged to a company with no activity.' } : null,
    pcModels: (() => {
      const ms = (d && d.by_model) || [];
      const max = Math.max(0, ...ms.map((m) => n0(m.cost_usd)));
      return ms.map((m) => ({ model: m.model, value: fmtUsd(m.cost_usd, 2), pct: pctOf(m.cost_usd, max), calls: count(m.calls),
        share: t.chat_cost_usd ? Math.round((n0(m.cost_usd) / t.chat_cost_usd) * 100) + '%' : '—' }));
    })(),
    pcModelsEmpty: ((d && d.unreadable) || []).includes('models') ? 'Model spend could not be read.' : 'No model calls were recorded in ' + monthName(dataMonth) + '.',
    pcCounting: d ? {
      lines: [
        'Infrastructure is ' + fmtUsd(a.infra_monthly_usd, 0) + ' a month, shared ' + (a.infra_split || 'equally among companies active in the month') + '.',
        'Billed is the credits each company used, at ' + fmtUsd(a.credit_usd, 2) + ' a credit.',
        'Months and days are counted in UTC, as the platform records them.'
      ],
      notRecorded: d.not_recorded || [],
      warning: unread.length ? 'Some costs could not be read: ' + unread.join(', ') + '. They are left out of the totals, not counted as zero.' : ''
    } : null,
    pcPanel: sel ? {
      id: sel,
      name: (selRow && selRow.name) || (all.find((r) => r.organization_id === sel) || {}).name || '',
      monthLabel: monthName(dataMonth),
      loading: !!s.pcDetailLoading, error: s.pcDetailErr || '',
      warning: detailUnread.length ? 'Some of this company\'s figures could not be read: ' + detailUnread.join(', ') + '. They are left out, not counted as zero.' : '',
      retry: () => c.pcOpen(sel, true), close: () => c.pcClose(),
      tab, tabs: TABS.map(([k, label]) => ({ key: k, label, on: k === tab, pick: () => c.pcSetTab(k) })),
      figures: selRow ? [
        { label: 'Total cost', value: fmtUsd(selRow.total_cost_usd, 2) },
        { label: 'Billed', value: fmtUsd(selRow.revenue_usd, 2) },
        { label: 'Profit', value: fmtUsd(selRow.profit_usd, 2), tone: tone(selRow.margin, selRow.profit_usd) },
        { label: 'Margin', value: fmtPct(selRow.margin), tone: tone(selRow.margin, selRow.profit_usd) }
      ] : [],
      sheet: pcPanelSheet(detail, tab, at)
    } : null
  };
}

const KINDS = { ingest: 'Document ingests', query: 'Queries' };

function whenOf(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  return d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3) + ', ' + hhmm(d);
}

// One tab of a company's panel, from GET /platform-cost/{id}. Pure.
export function pcPanelSheet(d, tab, now) {
  if (!d) return null;
  const at = now || new Date();
  const co = d.company || {};
  const u = d.unit_economics || null;
  const credit = (d.assumptions && d.assumptions.credit_usd) || 0;
  const unread = d.unreadable || [];
  const inMonth = ' in ' + monthName(d.month) + '.';
  if (tab === 'queries') {
    const qt = d.query_types || [];
    const max = Math.max(0, ...qt.map((q) => n0(q.cost_usd)));
    return {
      target: share((u && u.target_margin) || (d.assumptions && d.assumptions.target_margin)),
      types: qt.map((q) => ({ label: q.type, value: fmtUsd(q.cost_usd, 2), pct: pctOf(q.cost_usd, max), queries: q.turns, share: share(q.share),
        avg: fmtUsd(q.avg_usd), p90: fmtUsd(q.p90_usd), calls: q.llm_calls, tools: q.tool_calls, time: q.latency_s + ' s', price: fmtUsd(q.price_at_target_usd) })),
      top: (d.top_turns || []).map((x) => ({ question: String(x.question || ''), type: x.type, value: fmtUsd(x.cost_usd), who: x.email || '', when: whenOf(x.started_at) })),
      typesEmpty: unread.includes('query types') ? 'Query types could not be read.' : 'No questions were asked' + inMonth,
      topEmpty: 'No question with a recorded cost' + inMonth
    };
  }
  if (tab === 'models') {
    const ag = d.agents || [];
    const max = Math.max(0, ...ag.map((x) => n0(x.cost_usd)));
    return {
      agents: ag.map((x) => ({ label: x.agent, value: fmtUsd(x.cost_usd, 2), pct: pctOf(x.cost_usd, max), calls: x.calls, share: share(x.share), input: tok(x.input_tokens), output: tok(x.output_tokens) })),
      models: (d.models || []).map((m) => ({ label: m.model, calls: m.calls, value: fmtUsd(m.cost_usd, 2), input: tok(m.input_tokens), output: tok(m.output_tokens), cached: tok(m.cache_read_tokens) })),
      tools: (d.tools || []).map((x) => ({ label: x.tool, calls: x.calls, time: Math.round(n0(x.latency_ms)) + ' ms', failed: x.failed || 0 })),
      agentsEmpty: unread.includes('agents') ? 'Agents could not be read.' : 'No model calls were recorded' + inMonth,
      toolsEmpty: unread.includes('tools') ? 'Tools could not be read.' : 'No tool calls were recorded' + inMonth
    };
  }
  if (tab === 'migrations') {
    const runs = d.migration_runs || [];
    return {
      runs: runs.map((r) => ({ id: String(r.migration_id).slice(0, 8), status: r.status, calls: r.calls, value: fmtUsd(r.cost_usd), when: whenOf(r.first_call) })),
      stages: (d.migration_stages || []).map((x) => ({ stage: x.stage, model: x.model, calls: x.calls, value: fmtUsd(x.cost_usd) })),
      // The ledger exists from the first migration run on the updated mapper; before that
      // there is no record either way, so the tab does not say none ran.
      empty: runs.length ? '' : unread.includes('migrations')
        ? 'No migration cost is on record yet. Migration model costs are kept from the first migration run on or after 5 October 2026.'
        : 'No migration cost is recorded for this company' + inMonth
    };
  }
  if (tab === 'pricing') {
    const target = share(u && u.target_margin);
    const num = (v) => (v === null || v === undefined ? '—' : Math.round(v).toLocaleString('en-GB'));
    return {
      target,
      rows: u && co.active !== false ? [
        { label: 'Model cost per query', value: fmtUsd(u.model_cost_per_query_usd), hint: 'What the models cost to answer one question, on average.' },
        { label: 'Infrastructure per query', value: fmtUsd(u.infra_per_query_usd), hint: 'This month\'s infrastructure share spread over this month\'s questions.' },
        { label: 'All-in cost per query', value: fmtUsd(u.all_in_cost_per_query_usd), hint: 'The two together.' },
        { label: 'Price per query at ' + target + ' margin', value: fmtUsd(u.price_per_query_at_target_usd), hint: 'Covers model cost only, before infrastructure.' },
        { label: 'Value of one credit', value: fmtUsd(u.credit_value_usd, 2) },
        { label: 'Credits that would cover this month', value: num(u.credits_to_cover_cost), hint: 'Total cost divided by the value of a credit.' },
        { label: 'Credits billed', value: num(u.credits_billed) },
        { label: 'Queries a month to cover its infrastructure', value: num(u.breakeven_queries_per_month), hint: 'At one credit a query, after model cost.' }
      ] : [],
      empty: u && co.active !== false ? '' : 'Nothing to price: this company had no activity' + inMonth
    };
  }
  // overview
  const [y, m] = String(d.month).split('-').map(Number);
  const monthDays = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const days = d.month === keyOf(at) ? at.getUTCDate() : monthDays;
  const byDay = {};
  for (const x of d.days || []) byDay[x.day] = x;
  const series = [];
  for (let i = 1; i <= days; i++) {
    const k = d.month + '-' + String(i).padStart(2, '0');
    const x = byDay[k] || {};
    series.push({ day: k, label: i + ' ' + MONTHS[m - 1].slice(0, 3), cost: n0(x.cost_usd), queries: x.turns || 0 });
  }
  // The 1st, every fifth day, and the last when it is not crowding the fifth before it.
  const lastFifth = Math.floor(days / 5) * 5;
  const tickAt = (i) => i === 1 || i % 5 === 0 || (i === days && days - lastFifth >= 2);
  const dayMax = Math.max(0, ...series.map((x) => x.cost));
  const src = d.sources || [];
  const srcMax = Math.max(0, ...src.map((x) => n0(x.cost_usd)));
  const billing = d.billing || [];
  const billedQueries = billing.filter((b) => b.kind === 'query').reduce((acc, b) => acc + n0(b.events), 0);
  return {
    days: {
      bars: series.map((x, i) => ({ day: x.day, label: x.label, value: fmtUsd(x.cost, 2), pct: pctOf(x.cost, dayMax), queries: x.queries,
        tick: tickAt(i + 1), axis: i === 0 ? x.label : String(i + 1) })),
      max: fmtUsd(dayMax, 2), total: fmtUsd(series.reduce((acc, x) => acc + x.cost, 0), 2)
    },
    split: [
      { label: 'Chat models', value: fmtUsd(co.chat_cost_usd, 2), hint: count(co.chat_turns) + ' queries' },
      { label: 'of which compliance engine', value: fmtUsd(co.compliance_cost_usd, 2), hint: count(co.compliance_turns) + ' queries', indent: true },
      unread.includes('migrations')
        ? { label: 'Migration models', value: 'not recorded yet', hint: '' }
        : { label: 'Migration models', value: fmtUsd(co.migration_cost_usd, 2), hint: co.migration_runs ? co.migration_runs + (co.migration_runs === 1 ? ' run' : ' runs') : 'no runs' },
      { label: 'Infrastructure share', value: fmtUsd(co.infra_share_usd, 2) },
      { label: 'Total cost', value: fmtUsd(co.total_cost_usd, 2), strong: true }
    ],
    sources: src.map((x) => ({ label: x.source, value: fmtUsd(x.cost_usd, 2), pct: pctOf(x.cost_usd, srcMax), hint: count(x.turns) + ' queries' })),
    billing: billing.map((b) => ({ kind: KINDS[b.kind] || b.kind, events: b.events, credits: count(b.credits), value: fmtUsd(n0(b.credits) * credit, 2) })),
    billingEmpty: unread.includes('billing') ? 'Billing could not be read.' : 'Nothing was billed to this company' + inMonth,
    billingNote: n0(co.chat_turns) > 3 * billedQueries
      ? 'Fewer queries are billed than were asked: ' + count(co.chat_turns) + ' traced, ' + count(billedQueries) + ' billed.'
      : ''
  };
}
