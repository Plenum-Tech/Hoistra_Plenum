// platformCost — the Super Admin console's "Platform cost" section: what each company cost to run
// in a month (chat and migration model spend, its share of the infrastructure) against the
// credits it was billed. GET /api/superadmin/platform-cost (engines/platform_cost.py).
//
// pcVals() is pure; the methods are mixed into HoistraLogic.prototype and `this` is the controller.
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

function tone(m) {
  if (m === null || m === undefined) return 'var(--color-neutral-500)';
  return m >= 0.6 ? 'var(--st-ok)' : m >= 0 ? 'var(--st-warn)' : 'var(--st-risk)';
}

export const platformCostMethods = {
  pcValsFor() { return pcVals(this); },
  async pcLoad(month) {
    const m = month || this.state.pcMonth || 'this';
    this.setState({ pcLoading: true, pcErr: '', pcMonth: m });
    try {
      const out = await superAdminApi.platformCost(m);
      this.setState({ pcLoading: false, pcData: out || null });
      if (this.state.pcSel) this.pcOpen(this.state.pcSel, true);
    } catch (e) {
      this.setState({ pcLoading: false, pcErr: (e && e.message) || String(e) });
    }
  },
  // Click a company: its month split every way, in tabs (like the cost model's sheets). A second
  // click on the open company closes it.
  async pcOpen(id, keep) {
    if (!keep && this.state.pcSel === id) { this.setState({ pcSel: null, pcDetail: null }); return; }
    this.setState({ pcSel: id, pcDetailLoading: true, pcDetailErr: '', pcTab: keep ? (this.state.pcTab || 'overview') : 'overview' });
    try {
      const out = await superAdminApi.platformCostCompany(id, this.state.pcMonth || 'this');
      if (this.state.pcSel !== id) return;
      this.setState({ pcDetailLoading: false, pcDetail: out || null });
    } catch (e) {
      this.setState({ pcDetailLoading: false, pcDetailErr: (e && e.message) || String(e) });
    }
  },
  pcSetTab(t) { this.setState({ pcTab: t }); }
};

const TABS = [['overview', 'Overview'], ['queries', 'Query types'], ['agents', 'Agents & models'], ['tools', 'Tools'],
  ['migrations', 'Migrations'], ['billing', 'Billing'], ['unit', 'Unit economics']];

function tok(n) {
  const v = Number(n) || 0;
  return v >= 1e6 ? (v / 1e6).toFixed(2) + 'M' : v >= 1e3 ? (v / 1e3).toFixed(1) + 'k' : String(v);
}

// One tab = {title, columns, rows (arrays of cells), note}. A table per tab keeps the overlay simple.
export function pcDetailVals(d, tab) {
  if (!d) return null;
  const co = d.company || {};
  const u = d.unit_economics || {};
  const pct = (v) => (v === null || v === undefined ? '—' : Math.round(v * 100) + '%');
  const t = tab || 'overview';
  let sheet;
  if (t === 'overview') {
    sheet = { title: 'Cost and billing, ' + d.month, columns: ['Line', 'Amount'], rows: [
      ['Chat queries', (co.chat_turns || 0).toLocaleString('en-GB')],
      ['Chat model cost', fmtUsd(co.chat_cost_usd, 2)],
      ['  of which compliance engine', fmtUsd(co.compliance_cost_usd, 2) + ' (' + (co.compliance_turns || 0) + ' queries)'],
      ['Migration model cost', co.migration_runs ? fmtUsd(co.migration_cost_usd, 2) + ' (' + co.migration_runs + ' runs)' : '—'],
      ['Infrastructure share', fmtUsd(co.infra_share_usd, 2)],
      ['Total cost', fmtUsd(co.total_cost_usd, 2)],
      ['Credits billed', Math.round(co.credits || 0).toLocaleString('en-GB')],
      ['Billed (credits x ' + fmtUsd(d.assumptions && d.assumptions.credit_usd, 2) + ')', fmtUsd(co.revenue_usd, 2)],
      ['Profit', fmtUsd(co.profit_usd, 2)], ['Margin', fmtPct(co.margin)]
    ].concat((d.sources || []).map((s) => ['Source: ' + s.source, s.turns + ' queries · ' + fmtUsd(s.cost_usd, 2)])) };
  } else if (t === 'queries') {
    sheet = { title: 'By query type', columns: ['Query type', 'Queries', 'Model $', 'Share', 'Avg $ / query', 'p90 $', 'Model calls', 'Tool calls', 'Time', 'Price @ ' + pct(u.target_margin)],
      rows: (d.query_types || []).map((q) => [q.type, q.turns, fmtUsd(q.cost_usd, 2), pct(q.share), fmtUsd(q.avg_usd), fmtUsd(q.p90_usd), q.llm_calls, q.tool_calls, q.latency_s + ' s', fmtUsd(q.price_at_target_usd)]),
      note: 'Most expensive queries: ' + ((d.top_turns || []).slice(0, 5).map((x) => x.type + ' ' + fmtUsd(x.cost_usd) + ' — "' + String(x.question || '').slice(0, 60) + '"').join(' · ') || 'none') };
  } else if (t === 'agents') {
    sheet = { title: 'By agent, then by model', columns: ['Agent / model', 'Calls', 'Model $', 'Share', 'Input tokens', 'Output tokens'],
      rows: (d.agents || []).map((a) => ['Agent: ' + a.agent, a.calls, fmtUsd(a.cost_usd, 2), pct(a.share), tok(a.input_tokens), tok(a.output_tokens)])
        .concat((d.models || []).map((m) => ['Model: ' + m.model, m.calls, fmtUsd(m.cost_usd, 2), '', tok(m.input_tokens), tok(m.output_tokens) + ' · ' + tok(m.cache_read_tokens) + ' cached'])) };
  } else if (t === 'tools') {
    sheet = { title: 'Tool calls (no model cost of their own; their results feed the model calls)', columns: ['Tool', 'Calls', 'Avg time', 'Failed'],
      rows: (d.tools || []).map((x) => [x.tool, x.calls, Math.round(x.latency_ms || 0) + ' ms', x.failed || 0]) };
  } else if (t === 'migrations') {
    sheet = { title: 'Migration runs and their stages', columns: ['Run / stage', 'Status / model', 'Model calls', 'Model $'],
      rows: (d.migration_runs || []).map((r) => ['Run ' + String(r.migration_id).slice(0, 8), r.status, r.calls, fmtUsd(r.cost_usd)])
        .concat((d.migration_stages || []).map((s) => ['Stage: ' + s.stage, s.model, s.calls, fmtUsd(s.cost_usd)])),
      note: (d.migration_runs || []).length ? '' : 'No migration run recorded for this company this month. Runs are recorded from 5 Oct 2026 onwards.' };
  } else if (t === 'billing') {
    sheet = { title: 'Credits billed', columns: ['Kind', 'Events', 'Credits', 'Billed'],
      rows: (d.billing || []).map((b) => [b.kind, b.events, Math.round(b.credits), fmtUsd(b.credits * ((d.assumptions && d.assumptions.credit_usd) || 0), 2)]),
      note: (co.chat_turns || 0) > 3 * ((d.billing || []).filter((b) => b.kind === 'query').reduce((a, b) => a + b.events, 0) || 0)
        ? 'Fewer queries are billed than were asked (' + (co.chat_turns || 0) + ' traced): chat over the live connection is not recorded as a billed query yet.' : '' };
  } else {
    sheet = { title: 'Unit economics at ' + pct(u.target_margin) + ' target margin', columns: ['Measure', 'Value'], rows: [
      ['Model cost per query', fmtUsd(u.model_cost_per_query_usd)],
      ['Infrastructure per query (this month\'s volume)', fmtUsd(u.infra_per_query_usd)],
      ['All-in cost per query', fmtUsd(u.all_in_cost_per_query_usd)],
      ['Price per query at target margin (model cost only)', fmtUsd(u.price_per_query_at_target_usd)],
      ['Credit value', fmtUsd(u.credit_value_usd, 2)],
      ['Credits needed to cover this month\'s cost', u.credits_to_cover_cost === null || u.credits_to_cover_cost === undefined ? '—' : Math.round(u.credits_to_cover_cost).toLocaleString('en-GB')],
      ['Credits billed', Math.round(u.credits_billed || 0).toLocaleString('en-GB')],
      ['Queries a month to cover its infrastructure share', u.breakeven_queries_per_month === null || u.breakeven_queries_per_month === undefined ? '—' : u.breakeven_queries_per_month.toLocaleString('en-GB')]
    ] };
  }
  return Object.assign({ tabs: TABS.map(([k, label]) => ({ key: k, label: label, on: k === t })) }, sheet);
}

export function pcVals(c) {
  const s = c.state;
  const d = s.pcData || null;
  const t = (d && d.totals) || {};
  const rows = ((d && d.companies) || []).filter((r) => r.active);
  const quiet = ((d && d.companies) || []).filter((r) => !r.active).length;
  return {
    pcLoading: !!s.pcLoading && !d,
    pcError: s.pcErr || '',
    pcMonthLabel: d ? d.month : '',
    pcMonths: [['this', 'This month'], ['last', 'Last month']].map(([k, label]) => ({
      label: label, on: (s.pcMonth || 'this') === k, pick: () => c.pcLoad(k)
    })),
    pcReload: () => c.pcLoad(s.pcMonth || 'this'),
    pcTiles: d ? [
      { value: fmtUsd(t.total_cost_usd, 2), label: 'Platform cost', hint: fmtUsd(t.chat_cost_usd, 2) + ' chat · ' + fmtUsd(t.migration_cost_usd, 2) + ' migration · ' + fmtUsd(t.infra_share_usd, 0) + ' infra', color: 'var(--color-text)' },
      { value: fmtUsd(t.revenue_usd, 2), label: 'Billed', hint: Math.round(t.credits || 0).toLocaleString('en-GB') + ' credits at ' + fmtUsd(d.assumptions && d.assumptions.credit_usd, 2), color: 'var(--color-accent)' },
      { value: fmtPct(t.margin), label: 'Margin', hint: fmtUsd(t.profit_usd, 2) + ' profit', color: tone(t.margin) },
      { value: fmtUsd(t.cost_per_query_usd), label: 'Model $ per query', hint: Math.round(t.chat_turns || 0).toLocaleString('en-GB') + ' queries · ' + (t.active_companies || 0) + ' active companies', color: 'var(--color-text)' }
    ] : [],
    pcRows: rows.map((r) => ({
      id: r.organization_id, open: () => c.pcOpen(r.organization_id), selected: s.pcSel === r.organization_id,
      name: r.name,
      queries: (r.chat_turns || 0).toLocaleString('en-GB'),
      chat: fmtUsd(r.chat_cost_usd, 2), perQuery: fmtUsd(r.cost_per_query_usd),
      compliance: r.compliance_turns ? fmtUsd(r.compliance_cost_usd, 2) + ' (' + r.compliance_turns + ')' : '—',
      migrations: r.migration_runs ? r.migration_runs + ' · ' + fmtUsd(r.migration_cost_usd, 2) : '—',
      infra: fmtUsd(r.infra_share_usd, 0), cost: fmtUsd(r.total_cost_usd, 2),
      billed: fmtUsd(r.revenue_usd, 2), margin: fmtPct(r.margin), marginColor: tone(r.margin)
    })),
    pcQuiet: quiet ? quiet + (quiet === 1 ? ' company' : ' companies') + ' with no activity this month (no infrastructure charged)' : '',
    pcModels: ((d && d.by_model) || []).map((m) => ({ model: m.model, calls: (m.calls || 0).toLocaleString('en-GB'), cost: fmtUsd(m.cost_usd, 2),
      share: t.chat_cost_usd ? Math.round((m.cost_usd / t.chat_cost_usd) * 100) + '%' : '—' })),
    pcDetail: s.pcSel ? Object.assign({ loading: !!s.pcDetailLoading, error: s.pcDetailErr || '',
      name: ((d && d.companies) || []).filter((r) => r.organization_id === s.pcSel).map((r) => r.name)[0] || '',
      close: () => c.pcOpen(s.pcSel), pickTab: (k) => c.pcSetTab(k) },
      pcDetailVals(s.pcDetail && s.pcDetail.organization_id === s.pcSel ? s.pcDetail : null, s.pcTab) || {}) : null,
    pcNote: d ? ('Infrastructure ' + fmtUsd(d.assumptions && d.assumptions.infra_monthly_usd, 0) + ' a month, shared ' + ((d.assumptions && d.assumptions.infra_split) || '') +
      '. Not recorded yet: ' + ((d.not_recorded || []).join('; ') || 'nothing') + '.') : ''
  };
}
