// platformCost — the Super Admin's per-company cost section.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { pcVals, fmtUsd, fmtPct } from '../src/logic/platformCost.js';

const DATA = {
  month: '2026-10', totals: { total_cost_usd: 605.6, chat_cost_usd: 4.6, migration_cost_usd: 1, infra_share_usd: 600, revenue_usd: 496, credits: 1600,
    profit_usd: -109.6, margin: -0.221, cost_per_query_usd: 0.092, chat_turns: 50, active_companies: 2 },
  companies: [
    { name: 'Plenum Technologies', active: true, chat_turns: 50, chat_cost_usd: 4.6, cost_per_query_usd: 0.092, compliance_turns: 8, compliance_cost_usd: 1.4,
      migration_runs: 0, migration_cost_usd: 0, infra_share_usd: 300, total_cost_usd: 304.6, revenue_usd: 465, margin: 0.3449 },
    { name: 'Northbridge', active: true, chat_turns: 0, chat_cost_usd: 0, cost_per_query_usd: null, compliance_turns: 0, compliance_cost_usd: 0,
      migration_runs: 2, migration_cost_usd: 1, infra_share_usd: 300, total_cost_usd: 301, revenue_usd: 31, margin: -8.7 },
    { name: 'Dormant Ltd', active: false }],
  by_model: [{ model: 'gpt-5.6-terra', calls: 132, cost_usd: 2.9 }],
  assumptions: { infra_monthly_usd: 600, credit_usd: 0.31, infra_split: 'equally among companies active in the month' },
  not_recorded: ['document ingest model cost (not traced yet)']
};

test('the formatters show small sums to four places and nothing as a dash', () => {
  assert.equal(fmtUsd(0.092), '$0.0920');
  assert.equal(fmtUsd(304.6), '$304.60');
  assert.equal(fmtUsd(-109.6, 2), '-$109.60');
  assert.equal(fmtUsd(null), '—');
  assert.equal(fmtPct(0.3449), '34.5%');
  assert.equal(fmtPct(null), '—');
});

test('the section lists active companies with their costs and margin, and names what is not recorded', () => {
  const calls = [];
  const c = { state: { pcData: DATA, pcMonth: 'this' }, pcLoad: (m) => calls.push(m) };
  const v = pcVals(c);
  assert.deepEqual(v.pcTiles.map((t) => t.label), ['Platform cost', 'Billed', 'Margin', 'Model $ per query']);
  assert.equal(v.pcTiles[0].value, '$605.60');
  assert.equal(v.pcRows.length, 2);
  assert.equal(v.pcRows[0].compliance, '$1.40 (8)');
  assert.equal(v.pcRows[1].migrations, '2 · $1.00');
  assert.equal(v.pcRows[1].marginColor, 'var(--st-risk)');
  assert.equal(v.pcQuiet, '1 company with no activity this month (no infrastructure charged)');
  assert.match(v.pcNote, /Not recorded yet: document ingest model cost/);
  v.pcMonths[1].pick();
  assert.deepEqual(calls, ['last']);
});


test('clicking a company opens its split in tabs, each a table like a cost-model sheet', async () => {
  const { pcDetailVals } = await import('../src/logic/platformCost.js');
  const d = { month: '2026-10', organization_id: 'p',
    company: { chat_turns: 56, chat_cost_usd: 4.91, compliance_turns: 13, compliance_cost_usd: 1.56, migration_runs: 0, infra_share_usd: 295,
      total_cost_usd: 299.91, credits: 42, revenue_usd: 13.02, profit_usd: -286.89, margin: -22.03 },
    sources: [{ source: 'Chat', turns: 55, cost_usd: 4.39 }],
    query_types: [{ type: 'Compliance engine', turns: 12, cost_usd: 1.5, share: 0.3, avg_usd: 0.1247, p90_usd: 0.38, llm_calls: 6, tool_calls: 2, latency_s: 58, price_at_target_usd: 0.4156 }],
    top_turns: [{ type: 'Compliance engine', cost_usd: 0.6228, question: 'which certificates expired?' }],
    agents: [{ agent: 'compliance', calls: 20, cost_usd: 2.83, share: 0.58, input_tokens: 400000, output_tokens: 30000 }],
    models: [{ model: 'claude-sonnet-5', calls: 20, cost_usd: 2.34, input_tokens: 437021, output_tokens: 59280, cache_read_tokens: 71628 }],
    tools: [{ tool: 'list_vendor_accreditations', calls: 14, latency_ms: 812, failed: 0 }],
    migration_runs: [], migration_stages: [],
    billing: [{ kind: 'ingest', events: 8, credits: 40 }, { kind: 'query', events: 2, credits: 2 }],
    unit_economics: { target_margin: 0.7, model_cost_per_query_usd: 0.0878, infra_per_query_usd: 5.2679, all_in_cost_per_query_usd: 5.3557,
      price_per_query_at_target_usd: 0.2927, credit_value_usd: 0.31, credits_billed: 42, credits_to_cover_cost: 967.5, breakeven_queries_per_month: 1328 },
    assumptions: { credit_usd: 0.31 } };
  const ov = pcDetailVals(d, 'overview');
  assert.deepEqual(ov.tabs.map((t) => t.label), ['Overview', 'Query types', 'Agents & models', 'Tools', 'Migrations', 'Billing', 'Unit economics']);
  assert.deepEqual(ov.rows[1], ['Chat model cost', '$4.91']);
  assert.deepEqual(ov.rows[ov.rows.length - 1], ['Source: Chat', '55 queries · $4.39']);
  const q = pcDetailVals(d, 'queries');
  assert.deepEqual(q.rows[0], ['Compliance engine', 12, '$1.50', '30%', '$0.1247', '$0.3800', 6, 2, '58 s', '$0.4156']);
  assert.match(q.note, /Compliance engine \$0\.6228/);
  assert.equal(pcDetailVals(d, 'agents').rows[1][0], 'Model: claude-sonnet-5');
  assert.match(pcDetailVals(d, 'migrations').note, /No migration run recorded/);
  assert.match(pcDetailVals(d, 'billing').note, /chat over the live connection is not recorded as a billed query yet/);
  const unit = pcDetailVals(d, 'unit');
  assert.deepEqual(unit.rows[3], ['Price per query at target margin (model cost only)', '$0.2927']);
  assert.deepEqual(unit.rows[7], ['Queries a month to cover its infrastructure share', '1,328']);
  assert.equal(pcDetailVals(null, 'overview'), null);
});
