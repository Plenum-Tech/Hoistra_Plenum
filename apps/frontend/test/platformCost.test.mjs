// platformCost — the Super Admin console's Platform cost page (logic/platformCost.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pcVals, pcPanelSheet, fmtUsd, fmtPct, shiftMonth, monthName } from '../src/logic/platformCost.js';
import { superAdminMethods } from '../src/logic/superAdmin.js';

// 5 Oct 2026 10:12 UTC: "this month" is October. A fixed instant, not a local date, so the
// suite reads the same in every time zone; the server counts months and days in UTC.
const NOW = new Date(Date.UTC(2026, 9, 5, 10, 12));
const hhmm = (d) => String(d.getHours()).padStart(2, '0') + ':' + String(d.getMinutes()).padStart(2, '0');

// The month the console showed on 5 Oct 2026, figures as the API returned them.
const DATA = {
  month: '2026-10',
  totals: { total_cost_usd: 595.86, chat_cost_usd: 5.86, migration_cost_usd: 0, infra_share_usd: 590, revenue_usd: 47.12, credits: 152,
    profit_usd: -548.74, margin: -11.646, cost_per_query_usd: 0.0652, chat_turns: 90, active_companies: 2 },
  companies: [
    { organization_id: 'p', name: 'Plenum Technologies', active: true, chat_turns: 67, chat_cost_usd: 5.08, cost_per_query_usd: 0.0759,
      compliance_turns: 13, compliance_cost_usd: 1.56, migration_runs: 0, migration_cost_usd: 0, infra_share_usd: 295,
      total_cost_usd: 300.08, credits: 42, revenue_usd: 13.02, profit_usd: -287.06, margin: -22.0476 },
    { organization_id: 'y', name: 'Your Organisation', active: true, chat_turns: 23, chat_cost_usd: 0.78, cost_per_query_usd: 0.0339,
      compliance_turns: 0, compliance_cost_usd: 0, migration_runs: 1, migration_cost_usd: 0.5, infra_share_usd: 295,
      total_cost_usd: 296.28, credits: 110, revenue_usd: 34.1, profit_usd: -262.18, margin: -7.6886 },
    { organization_id: 'n', name: 'Northbridge Estates Ltd', active: false, total_cost_usd: 0 },
    { organization_id: 'r', name: 'Retired scratch tenant 38ed', active: false, total_cost_usd: 0 }],
  by_model: [{ model: 'gpt-5.6-terra', calls: 138, cost_usd: 3.0 }, { model: 'claude-sonnet-5', calls: 26, cost_usd: 2.34 }],
  assumptions: { infra_monthly_usd: 590, credit_usd: 0.31, infra_split: 'equally among companies active in the month' },
  not_recorded: ['document ingest model cost (not traced yet)', 'migration model cost (no run recorded yet - the ledger table is created on the first call)'],
  unreadable: ['migration']
};

function ctl(state) {
  const calls = { load: [], open: [] };
  const c = {
    state: Object.assign({ pcData: DATA, pcMonth: 'this', pcLoadedAt: NOW.getTime() }, state),
    pcLoad: (m) => calls.load.push(m), pcOpen: (id) => calls.open.push(id), pcClose() {}, pcSetTab() {}
  };
  return { c, calls };
}

test('the formatters show small sums to four places and nothing as a dash', () => {
  assert.equal(fmtUsd(0.092), '$0.0920');
  assert.equal(fmtUsd(304.6), '$304.60');
  assert.equal(fmtUsd(-109.6, 2), '-$109.60');
  assert.equal(fmtUsd(null), '—');
  assert.equal(fmtPct(0.3449), '34.5%');
  assert.equal(fmtPct(null), '—');
});

test('months step across a year boundary and read as words', () => {
  assert.equal(shiftMonth('2026-01', -1), '2025-12');
  assert.equal(shiftMonth('2025-12', 1), '2026-01');
  assert.equal(shiftMonth('2026-10', -13), '2025-09');
  assert.equal(monthName('2026-10'), 'October 2026');
});

test('the month stepper goes back freely and forward only as far as this month', () => {
  const { c, calls } = ctl();
  const v = pcVals(c, NOW);
  assert.equal(v.pcMonth.label, 'October 2026');
  assert.equal(v.pcMonth.current, true);
  assert.equal(v.pcMonth.next, null, 'there is no month after this one to read');
  v.pcMonth.prev();
  assert.deepEqual(calls.load, ['2026-09']);

  const past = ctl({ pcMonth: '2026-09' });
  const pv = pcVals(past.c, NOW);
  assert.equal(pv.pcMonth.label, 'September 2026');
  assert.equal(pv.pcMonth.current, false);
  pv.pcMonth.next();
  assert.deepEqual(past.calls.load, ['this'], 'stepping forward into this month asks for "this", as the server counts it');
});

test('the answer is one sentence: what it cost, what it billed, and the loss', () => {
  const v = pcVals(ctl().c, NOW);
  assert.equal(v.pcHero.sentence.map((p) => p.text).join(''),
    'October so far, running the platform cost $595.86 and it billed $47.12, a loss of $548.74.');
  assert.deepEqual(v.pcHero.sentence.filter((p) => p.strong).map((p) => p.text), ['$595.86', '$47.12', '$548.74']);
  assert.equal(v.pcHero.result, 'loss');

  const sept = pcVals(ctl({ pcMonth: '2026-09', pcData: Object.assign({}, DATA, { month: '2026-09',
    totals: Object.assign({}, DATA.totals, { revenue_usd: 700, profit_usd: 104.14 }) }) }).c, NOW);
  assert.equal(sept.pcHero.sentence.map((p) => p.text).join(''),
    'In September 2026, running the platform cost $595.86 and it billed $700.00, a profit of $104.14.');
  assert.equal(sept.pcHero.result, 'profit');
});

test('a month with nothing in it says so instead of drawing empty bars', () => {
  const empty = Object.assign({}, DATA, { totals: { total_cost_usd: 0, revenue_usd: 0, profit_usd: 0, chat_turns: 0, active_companies: 0, credits: 0 },
    companies: DATA.companies.map((r) => Object.assign({}, r, { active: false })), by_model: [] });
  const v = pcVals(ctl({ pcData: empty }).c, NOW);
  assert.equal(v.pcHero, null);
  assert.equal(v.pcRows.length, 0);
  assert.match(v.pcEmpty, /^Nothing has been recorded for October 2026 yet\./);
});

test('cost and billed are drawn on one dollar scale, the cost split by where it comes from', () => {
  const { bars, legend } = pcVals(ctl().c, NOW).pcHero;
  assert.equal(bars.cost.value, '$595.86');
  assert.equal(bars.cost.pct, 100);
  assert.deepEqual(bars.cost.segments.map((s) => [s.key, s.pct]), [['infra', 99], ['chat', 1], ['migration', 0]]);
  assert.equal(bars.billed.value, '$47.12');
  assert.equal(bars.billed.pct, 7.9);
  assert.deepEqual(legend.map((l) => [l.label, l.value]), [
    ['Infrastructure', '$590.00'], ['Chat models', '$5.86'], ['Migration models', 'not recorded yet'], ['Billed', '$47.12']]);
});

test('four cards lead the page: cost, billed, margin and model cost per query, each with what it is made of', () => {
  const v = pcVals(ctl().c, NOW);
  assert.deepEqual(v.pcCards.map((k) => [k.key, k.label, k.value, k.hint]), [
    ['cost', 'Platform cost', '$595.86', 'Chat $5.86, migration not recorded yet, infrastructure $590'],
    ['billed', 'Billed', '$47.12', '152 credits at $0.31'],
    ['margin', 'Margin', '-1164.6%', '$548.74 loss'],
    ['perQuery', 'Model cost per query', '$0.0652', '90 queries, 2 of 4 companies active']]);
  assert.equal(v.pcCards[2].tone, 'loss');
  assert.equal(v.pcHero.facts, undefined, 'the cards replace the facts row; nothing is shown twice');
  const profit = pcVals(ctl({ pcData: Object.assign({}, DATA, { totals: Object.assign({}, DATA.totals, { revenue_usd: 608.36, profit_usd: 12.5, margin: 0.0205 }) }) }).c, NOW);
  assert.equal(profit.pcCards[2].hint, '$12.50 profit');
  assert.equal(profit.pcCards[2].tone, 'thin');
});

test('a month with nothing recorded draws no cards', () => {
  const empty = Object.assign({}, DATA, { totals: { total_cost_usd: 0, revenue_usd: 0, profit_usd: 0, chat_turns: 0, active_companies: 0, credits: 0 } });
  assert.equal(pcVals(ctl({ pcData: empty }).c, NOW).pcCards, null);
  assert.equal(pcVals(ctl({ pcData: null }).c, NOW).pcCards, null);
});

test('each active company is a row; the bars beside it share one scale across the table', () => {
  const { c, calls } = ctl({ pcSel: 'y' });
  const v = pcVals(c, NOW);
  assert.deepEqual(v.pcRows.map((r) => r.name), ['Plenum Technologies', 'Your Organisation']);
  const [p, y] = v.pcRows;
  assert.deepEqual([p.queries, p.model, p.infra, p.cost, p.billed, p.profit, p.margin],
    ['67', '$5.08', '$295', '$300.08', '$13.02', '-$287.06', '-2204.8%']);
  assert.equal(y.model, '$1.28', 'model cost is chat plus migration model spend');
  assert.equal(p.tone, 'loss');
  assert.equal(p.bars.infraPct, 98.3);
  assert.equal(p.bars.modelPct, 1.7);
  assert.equal(p.bars.billedPct, 4.3);
  assert.equal(y.selected, true);
  assert.equal(p.selected, false);
  p.open();
  assert.deepEqual(calls.open, ['p']);
});

test('companies with no activity are named once, not listed as zero rows', () => {
  const v = pcVals(ctl().c, NOW);
  assert.deepEqual(v.pcQuiet.names, ['Northbridge Estates Ltd', 'Retired scratch tenant 38ed']);
  assert.match(v.pcQuiet.note, /no infrastructure share/i);
});

test('model spend lists each model against the largest, with its share of chat spend', () => {
  const v = pcVals(ctl().c, NOW);
  assert.deepEqual(v.pcModels.map((m) => [m.model, m.value, m.pct, m.share, m.calls]), [
    ['gpt-5.6-terra', '$3.00', 100, '51%', '138'], ['claude-sonnet-5', '$2.34', 78, '40%', '26']]);
});

test('how it is counted names the assumptions and what is not recorded; only a real read failure warns', () => {
  const v = pcVals(ctl().c, NOW);
  assert.match(v.pcCounting.lines[0], /Infrastructure is \$590 a month, shared equally among companies active in the month/);
  assert.match(v.pcCounting.lines[1], /\$0\.31/);
  assert.equal(v.pcCounting.notRecorded.length, 2);
  assert.equal(v.pcCounting.warning, '', 'a migration ledger that does not exist yet is already explained under not recorded');
  const broken = pcVals(ctl({ pcData: Object.assign({}, DATA, { unreadable: ['migration', 'chat'] }) }).c, NOW);
  assert.match(broken.pcCounting.warning, /chat/);
  assert.match(broken.pcCounting.warning, /not counted as zero/);
});

test('a read that fails before any data shows the error with a retry; a re-read keeps the old frame', () => {
  const { c, calls } = ctl({ pcData: null, pcErr: 'Not Found' });
  const v = pcVals(c, NOW);
  assert.equal(v.pcStatus.error, 'Not Found');
  assert.equal(v.pcStatus.loading, false, 'a failed read shows the error, not a skeleton');
  v.pcStatus.retry();
  assert.deepEqual(calls.load, ['this']);
  const r = pcVals(ctl({ pcLoading: true }).c, NOW);
  assert.equal(r.pcStatus.loading, false, 'data is on screen, so no skeleton');
  assert.equal(r.pcStatus.refreshing, true);
  assert.equal(pcVals(ctl({ pcData: null, pcLoading: true }).c, NOW).pcStatus.loading, true);
  assert.equal(pcVals(ctl().c, NOW).pcReadAt, 'Read at ' + hhmm(NOW), 'the reading time is the viewer\'s own clock');
});

test('when another month fails to read, the page says which month the figures on screen are for', () => {
  const v = pcVals(ctl({ pcMonth: '2026-08', pcErr: 'Gateway Timeout' }).c, NOW);
  assert.equal(v.pcMonth.label, 'August 2026');
  assert.equal(v.pcStatus.showing, 'October 2026');
  assert.equal(pcVals(ctl({ pcData: null }).c, NOW).pcStatus.showing, '');
});

// ── one company's panel ────────────────────────────────────────────────────────────────

const DETAIL = { month: '2026-10', organization_id: 'p',
  company: DATA.companies[0],
  sources: [{ source: 'Chat', turns: 55, cost_usd: 4.39 }, { source: 'Scheduled (Hoist Crons, report cards)', turns: 12, cost_usd: 0.69 }],
  days: [{ day: '2026-10-02', turns: 20, cost_usd: 1.5 }, { day: '2026-10-05', turns: 47, cost_usd: 3.58 }],
  query_types: [{ type: 'Compliance engine', turns: 13, cost_usd: 1.56, share: 0.307, avg_usd: 0.12, p90_usd: 0.38, llm_calls: 6, tool_calls: 2, latency_s: 58, price_at_target_usd: 0.4 },
    { type: 'General question', turns: 54, cost_usd: 3.52, share: 0.693, avg_usd: 0.0652, p90_usd: 0.1, llm_calls: 3, tool_calls: 1, latency_s: 12, price_at_target_usd: 0.2173 }],
  top_turns: [{ type: 'Compliance engine', cost_usd: 0.6228, question: 'which certificates expired?', started_at: '2026-10-05T10:12:00+00:00', email: 'sam@plenum-tech.com' }],
  agents: [{ agent: 'compliance', calls: 20, cost_usd: 2.83, share: 0.58, input_tokens: 400000, output_tokens: 30000 }],
  models: [{ model: 'claude-sonnet-5', calls: 20, cost_usd: 2.34, input_tokens: 437021, output_tokens: 59280, cache_read_tokens: 71628 }],
  tools: [{ tool: 'list_vendor_accreditations', calls: 14, latency_ms: 812, failed: 1 }],
  migration_runs: [], migration_stages: [],
  billing: [{ kind: 'ingest', events: 8, credits: 40 }, { kind: 'query', events: 2, credits: 2 }],
  unit_economics: { target_margin: 0.7, model_cost_per_query_usd: 0.0759, infra_per_query_usd: 4.403, all_in_cost_per_query_usd: 4.4789,
    price_per_query_at_target_usd: 0.253, credit_value_usd: 0.31, credits_billed: 42, credits_to_cover_cost: 968, breakeven_queries_per_month: 1255 },
  assumptions: { credit_usd: 0.31, target_margin: 0.7 } };

test('opening a company gives a panel with its figures and five tabs', () => {
  const v = pcVals(ctl({ pcSel: 'p', pcDetail: DETAIL, pcTab: 'overview' }).c, NOW);
  assert.equal(v.pcPanel.name, 'Plenum Technologies');
  assert.deepEqual(v.pcPanel.tabs.map((t) => t.label), ['Overview', 'Queries', 'Models & tools', 'Migrations', 'Pricing']);
  assert.deepEqual(v.pcPanel.figures.map((f) => [f.label, f.value]), [
    ['Total cost', '$300.08'], ['Billed', '$13.02'], ['Profit', '-$287.06'], ['Margin', '-2204.8%']]);
  assert.equal(pcVals(ctl().c, NOW).pcPanel, null);
  const loading = pcVals(ctl({ pcSel: 'p', pcDetail: null, pcDetailLoading: true }).c, NOW).pcPanel;
  assert.equal(loading.loading, true);
  assert.equal(loading.sheet, null, 'no sheet is drawn from another company\'s data while this one loads');
});

test('the overview draws a bar for every day of the month so far, gaps included', () => {
  const s = pcPanelSheet(DETAIL, 'overview', NOW);
  assert.equal(s.days.bars.length, 5, '1 to 5 October');
  assert.deepEqual(s.days.bars.map((b) => b.value), ['$0.00', '$1.50', '$0.00', '$0.00', '$3.58']);
  assert.equal(s.days.bars[4].pct, 100);
  assert.equal(s.days.bars[4].label, '5 Oct');
  assert.equal(s.days.bars[4].queries, 47);
  assert.equal(s.days.total, '$5.08');
  assert.deepEqual(s.split.map((r) => r.label), ['Chat models', 'of which compliance engine', 'Migration models', 'Infrastructure share', 'Total cost']);
  assert.deepEqual(s.sources.map((r) => [r.label, r.value, r.pct]), [['Chat', '$4.39', 100], ['Scheduled (Hoist Crons, report cards)', '$0.69', 15.7]]);
  assert.deepEqual(s.billing.map((b) => [b.kind, b.events, b.credits, b.value]), [['Document ingests', 8, '40', '$12.40'], ['Queries', 2, '2', '$0.62']]);
  assert.match(s.billingNote, /Fewer queries are billed than were asked/);
  const sept = pcPanelSheet(Object.assign({}, DETAIL, { month: '2026-09', days: [] }), 'overview', NOW);
  assert.equal(sept.days.bars.length, 30, 'a past month draws all its days');
});

test('the queries tab ranks query types by cost and lists the costliest questions', () => {
  const s = pcPanelSheet(DETAIL, 'queries', NOW);
  assert.deepEqual(s.types.map((t) => [t.label, t.value, t.pct]), [['Compliance engine', '$1.56', 44.3], ['General question', '$3.52', 100]]);
  assert.equal(s.types[0].price, '$0.4000');
  assert.equal(s.target, '70%');
  assert.deepEqual(s.top.map((t) => [t.question, t.type, t.value, t.who]), [['which certificates expired?', 'Compliance engine', '$0.6228', 'sam@plenum-tech.com']]);
});

test('models, migrations and pricing each have their sheet, and an empty migration month says why', () => {
  const m = pcPanelSheet(DETAIL, 'models', NOW);
  assert.deepEqual(m.agents.map((a) => [a.label, a.value, a.pct]), [['compliance', '$2.83', 100]]);
  assert.deepEqual(m.models[0], { label: 'claude-sonnet-5', calls: 20, value: '$2.34', input: '437.0k', output: '59.3k', cached: '71.6k' });
  assert.deepEqual(m.tools[0], { label: 'list_vendor_accreditations', calls: 14, time: '812 ms', failed: 1 });
  const g = pcPanelSheet(DETAIL, 'migrations', NOW);
  assert.equal(g.empty, 'No migration cost is recorded for this company in October 2026.');
  const p = pcPanelSheet(DETAIL, 'pricing', NOW);
  assert.deepEqual(p.rows.map((r) => r.label), ['Model cost per query', 'Infrastructure per query', 'All-in cost per query',
    'Price per query at 70% margin', 'Value of one credit', 'Credits that would cover this month', 'Credits billed', 'Queries a month to cover its infrastructure']);
  assert.equal(p.rows[3].value, '$0.2530');
  assert.equal(p.rows[7].value, '1,255');
  assert.equal(pcPanelSheet(null, 'overview', NOW), null);
});

// ── reaching the page ──────────────────────────────────────────────────────────────────

test('the console switches between Companies and Platform cost, reading cost the first time it is needed', () => {
  const loads = [];
  const ctx = { state: { saOn: true, saSel: null, saCompanies: [], saLiveLoading: false, saLiveError: '', saLiveLoadedAt: 1,
      saLiveCardError: '', saNew: false, saName: '', saEmail: '', saCc: 'UK', saPage: 'companies', pcData: null, pcLoading: false },
    setState(p) { Object.assign(this.state, p); }, pcLoad(m) { loads.push(m); } };
  const v = superAdminMethods.saVals.call(ctx, ctx.state);
  assert.equal(v.saPage, 'companies');
  v.saGoCost();
  assert.equal(ctx.state.saPage, 'cost');
  assert.deepEqual(loads, ['this']);
  ctx.state.pcData = DATA;
  superAdminMethods.saVals.call(ctx, ctx.state).saGoCompanies();
  assert.equal(ctx.state.saPage, 'companies');
  superAdminMethods.saVals.call(ctx, ctx.state).saGoCost();
  assert.deepEqual(loads, ['this'], 'data already on hand is not read again just for switching tabs');
});

// ── review fixes, 5 Oct 2026 ───────────────────────────────────────────────────────────

test('before the first read starts the page shows its skeleton, not a blank body', () => {
  assert.equal(pcVals(ctl({ pcData: null }).c, NOW).pcStatus.loading, true);
});

test('the month is the server\'s: at 01:30 on 1 November in Dubai it is still October, in UTC', () => {
  const late = new Date(Date.UTC(2026, 9, 31, 21, 30));
  const { c } = ctl();
  const v = pcVals(c, late);
  assert.equal(v.pcMonth.label, 'October 2026');
  assert.equal(v.pcMonth.current, true);
  assert.equal(v.pcMonth.next, null);
  assert.match(v.pcHero.sentence.map((p) => p.text).join(''), /^October so far/);
  assert.equal(pcPanelSheet(DETAIL, 'overview', late).days.bars.length, 31, 'every UTC day of October so far');
  assert.match(v.pcCounting.lines[2], /UTC/);
});

test('the sentence\'s loss is the difference of the two figures printed beside it', () => {
  const t = { total_cost_usd: 2.0098, chat_cost_usd: 2.0098, migration_cost_usd: 0, infra_share_usd: 0, revenue_usd: 0, credits: 0, profit_usd: -2.0, margin: null, chat_turns: 2, active_companies: 1 };
  const v = pcVals(ctl({ pcData: Object.assign({}, DATA, { totals: t }) }).c, NOW);
  assert.equal(v.pcHero.sentence.map((p) => p.text).join(''), 'October so far, running the platform cost $2.01 and it billed $0.00, a loss of $2.01.');
  assert.equal(v.pcCards[2].hint, '$2.01 loss');
});

test('with nothing billed the margin is undefined, but a loss is still marked as one', () => {
  const unbilled = Object.assign({}, DATA.companies[0], { revenue_usd: 0, credits: 0, profit_usd: -300.08, margin: null });
  const v = pcVals(ctl({ pcData: Object.assign({}, DATA, { companies: [unbilled].concat(DATA.companies.slice(1)) }) }).c, NOW);
  assert.equal(v.pcRows[0].margin, '—');
  assert.equal(v.pcRows[0].tone, 'loss');
});

test('a source the page could not read is never shown as zero or as an empty month', () => {
  const noChat = Object.assign({}, DATA, { unreadable: ['migration', 'chat', 'billed'], by_model: [],
    totals: Object.assign({}, DATA.totals, { chat_cost_usd: 0 }), companies: DATA.companies.map((r) => Object.assign({}, r, { active: false })) });
  const v = pcVals(ctl({ pcData: noChat }).c, NOW);
  assert.equal(v.pcEmpty, '', 'an unread month is not claimed to be an empty one');
  assert.match(v.pcCounting.warning, /chat, billed/);
  assert.match(v.pcCards[0].hint, /^Chat not read, migration not recorded yet, infrastructure \$590$/);
  assert.equal(v.pcModelsEmpty, 'No model calls were recorded in October 2026.', 'models read fine, there were none');
  const noModels = pcVals(ctl({ pcData: Object.assign({}, DATA, { unreadable: ['migration', 'models'], by_model: [] }) }).c, NOW);
  assert.equal(noModels.pcModelsEmpty, 'Model spend could not be read.');
  const legend = pcVals(ctl().c, NOW).pcHero.legend;
  assert.deepEqual(legend.find((l) => l.key === 'migration'), { key: 'migration', label: 'Migration models', value: 'not recorded yet' });
});

test('the panel names what it could not read and never shows it as nothing', () => {
  const broken = Object.assign({}, DETAIL, { unreadable: ['billing', 'query types', 'migrations'], billing: [], query_types: [] });
  const v = pcVals(ctl({ pcSel: 'p', pcDetail: broken, pcTab: 'overview' }).c, NOW);
  assert.match(v.pcPanel.warning, /billing, query types/);
  assert.doesNotMatch(v.pcPanel.warning, /migrations/);
  assert.equal(pcPanelSheet(broken, 'overview', NOW).billingEmpty, 'Billing could not be read.');
  assert.deepEqual(pcPanelSheet(broken, 'overview', NOW).split.find((r) => r.label === 'Migration models'), { label: 'Migration models', value: 'not recorded yet', hint: '' });
  assert.equal(pcPanelSheet(broken, 'queries', NOW).typesEmpty, 'Query types could not be read.');
  assert.match(pcPanelSheet(broken, 'migrations', NOW).empty, /^No migration cost is on record yet\./);
  const quiet = Object.assign({}, DETAIL, { month: '2026-09', billing: [], query_types: [], top_turns: [], agents: [], tools: [], unreadable: [] });
  assert.equal(pcPanelSheet(quiet, 'overview', NOW).billingEmpty, 'Nothing was billed to this company in September 2026.');
  assert.equal(pcPanelSheet(quiet, 'queries', NOW).typesEmpty, 'No questions were asked in September 2026.');
  assert.equal(pcPanelSheet(quiet, 'models', NOW).agentsEmpty, 'No model calls were recorded in September 2026.');
  assert.equal(pcPanelSheet(quiet, 'migrations', NOW).empty, 'No migration cost is recorded for this company in September 2026.');
  assert.equal(pcVals(ctl({ pcSel: 'p', pcDetail: DETAIL }).c, NOW).pcPanel.warning, '');
});

test('a company with no activity that month has nothing to price', () => {
  const idle = Object.assign({}, DETAIL, { month: '2026-09', company: Object.assign({}, DATA.companies[0], { active: false, chat_turns: 0 }) });
  const p = pcPanelSheet(idle, 'pricing', NOW);
  assert.deepEqual(p.rows, []);
  assert.equal(p.empty, 'Nothing to price: this company had no activity in September 2026.');
});

test('the day axis labels the 1st, every fifth day and the last, without crowding the last two', () => {
  const ticks = (month, now) => pcPanelSheet(Object.assign({}, DETAIL, { month, days: [] }), 'overview', now).days.bars.filter((b) => b.tick).map((b) => b.day.slice(8));
  assert.deepEqual(ticks('2026-08', NOW), ['01', '05', '10', '15', '20', '25', '30'], '31 sits next to 30, so it is left to the tooltip');
  assert.deepEqual(ticks('2026-09', NOW), ['01', '05', '10', '15', '20', '25', '30']);
  assert.deepEqual(ticks('2026-10', NOW), ['01', '05']);
  assert.deepEqual(ticks('2026-10', new Date(Date.UTC(2026, 9, 7, 9))), ['01', '05', '07']);
});

test('the panel shows only its own month: a detail from another month is not drawn under this month\'s label', () => {
  const v = pcVals(ctl({ pcSel: 'p', pcDetail: Object.assign({}, DETAIL, { month: '2026-09' }), pcDetailLoading: true }).c, NOW);
  assert.equal(v.pcPanel.monthLabel, 'October 2026');
  assert.equal(v.pcPanel.sheet, null, 'September\'s days and tabs are not drawn as October\'s');
  assert.deepEqual(v.pcPanel.figures.map((f) => f.value), ['$300.08', '$13.02', '-$287.06', '-2204.8%'], 'the figures come from the October table');
});

test('a company read that lands after a newer one is dropped, and the panel reads the month the table shows', async () => {
  const { superAdminApi } = await import('../src/api/superAdmin.js');
  const { platformCostMethods } = await import('../src/logic/platformCost.js');
  const real = superAdminApi.platformCostCompany;
  const pending = [];
  superAdminApi.platformCostCompany = (id, month) => new Promise((resolve) => pending.push({ id, month, resolve }));
  try {
    const c = Object.assign({}, platformCostMethods);
    c.state = { pcData: Object.assign({}, DATA, { month: '2026-09' }), pcMonth: '2026-08', pcErr: 'Gateway Timeout', pcSel: null };
    c.setState = (u) => { c.state = Object.assign({}, c.state, typeof u === 'function' ? u(c.state) : u); };
    const first = c.pcOpen('p');
    assert.equal(pending[0].month, '2026-09', 'the table shows September (August failed to read), so the panel reads September');
    c.state.pcData = Object.assign({}, DATA, { month: '2026-10' });
    const second = c.pcOpen('p', true);
    assert.equal(pending[1].month, '2026-10');
    pending[1].resolve(Object.assign({}, DETAIL, { month: '2026-10' }));
    await second;
    pending[0].resolve(Object.assign({}, DETAIL, { month: '2026-09' }));
    await first;
    assert.equal(c.state.pcDetail.month, '2026-10', 'the older, slower read does not overwrite the newer one');
    assert.equal(c.state.pcDetailLoading, false);
  } finally {
    superAdminApi.platformCostCompany = real;
  }
});
