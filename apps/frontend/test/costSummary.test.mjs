// costSummary — shapes one turn's llm_cost ledger (svc-deepagents `compliance_pipeline.output.cost`)
// into the answer panel's header figures and "Cost by role" bar. The fixture is the real
// payload a live orchestrator call returned for "Which fire-safety certificates does the pack
// require for a building, and how often?" (captured 09 Sep 2026, over the WebSocket stream,
// after wiring llm_cost.begin_turn into the compliance posture shortcut) — not synthesised.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { costSummary } from '../src/logic/complianceLive.js';

const LIVE_COST = {
  calls: 3, usd: 0.5223, usd_complete: true,
  input_tokens: 60198, output_tokens: 3788, cache_read: 12368, wall_ms: 54734,
  by_role: { analyst: 0.449984, reviewer: 0.072328 },
  models: ['claude-opus-5']
};

test('the real captured ledger summary shapes into calls, total, seconds and models', () => {
  const s = costSummary(LIVE_COST);
  assert.equal(s.calls, 3);
  assert.equal(s.usd, 0.5223);
  assert.equal(s.usdLabel, '$0.522');
  assert.equal(s.usdComplete, true);
  assert.equal(s.secsLabel, '55 s');
  assert.deepEqual(s.models, ['claude-opus-5']);
  assert.equal(s.inputTokens, 60198);
  assert.equal(s.outputTokens, 3788);
  assert.equal(s.cacheReadLabel, '12,368 tok');
});

test('roles are sorted highest spend first, each with a 2-significant-figure label and its share of the total', () => {
  const s = costSummary(LIVE_COST);
  assert.deepEqual(s.roles.map((r) => r.role), ['analyst', 'reviewer']);
  assert.equal(s.roles[0].usdLabel, '$0.45');
  assert.equal(s.roles[1].usdLabel, '$0.072');
  // 0.449984 / 0.5223 and 0.072328 / 0.5223
  assert.ok(Math.abs(s.roles[0].pct - 86.15) < 0.1);
  assert.ok(Math.abs(s.roles[1].pct - 13.85) < 0.1);
  assert.ok(Math.abs(s.roles[0].pct + s.roles[1].pct - 100) < 0.01);
});

test('small amounts keep 2 significant figures rather than rounding to zero or to 3 decimals', () => {
  assert.equal(costSummary({ calls: 1, usd: 0.130, by_role: { analyst: 0.130 } }).usdLabel, '$0.130');
  assert.equal(costSummary({ calls: 1, usd: 0.0037, by_role: { routing: 0.0037 } }).roles[0].usdLabel, '$0.0037');
  assert.equal(costSummary({ calls: 1, usd: 0.043, by_role: { reviewer: 0.043 } }).roles[0].usdLabel, '$0.043');
});

test('a turn priced with an unknown model is marked incomplete rather than shown as free', () => {
  const s = costSummary({ calls: 1, usd: 0, usd_complete: false, by_role: {}, wall_ms: 900, models: ['some-untracked-model'] });
  assert.equal(s.usdComplete, false);
});

test('a role that recorded no cost (a pure lookup, no LLM call) still gets a zero-width row, not a divide-by-zero', () => {
  const s = costSummary({ calls: 2, usd: 0.02, by_role: { analyst: 0.02, fetch: 0 } });
  const fetch = s.roles.find((r) => r.role === 'fetch');
  assert.equal(fetch.pct, 0);
  assert.equal(fetch.usdLabel, '$0');
});

test('no cost object at all (the ledger never ran for this turn) shapes to null, not a fabricated zero', () => {
  assert.equal(costSummary(null), null);
  assert.equal(costSummary(undefined), null);
  assert.equal(costSummary({}), null);
  assert.equal(costSummary({ calls: 0, by_role: {} }), null);
});

import { stepCostLabel } from '../src/logic/complianceLive.js';

test('a step with a real ledger entry attached shows its own time, cost and model, joined on one line', () => {
  const badge = stepCostLabel({ stage: 'analyse', label: 'Compliance analyst reasoning', ms: 8300, usd: 0.043, model: 'claude-sonnet-5', effort: 'medium' });
  assert.equal(badge.secsLabel, '8.3 s');
  assert.equal(badge.usdLabel, '$0.043');
  assert.equal(badge.model, 'claude-sonnet-5');
  assert.equal(badge.effort, 'medium');
  assert.equal(badge.cacheHit, false);
  assert.equal(badge.line, '8.3 s · $0.043 · claude-sonnet-5 · medium');
});

test('a step whose call hit the cache says so, appended after the effort level', () => {
  const badge = stepCostLabel({ ms: 23000, usd: 0.028, model: 'claude-sonnet-5', effort: 'medium', cache_hit: true });
  assert.equal(badge.line, '23 s · $0.028 · claude-sonnet-5 · medium · cache hit');
});

test('a step with no ledger entry at all (a database read, not a model call) has no badge', () => {
  assert.equal(stepCostLabel({ stage: 'data', label: 'Read the certificate register' }), null);
  assert.equal(stepCostLabel(null), null);
  assert.equal(stepCostLabel({}), null);
});

test('a step whose model has no price on file shows its time but never a fabricated $ figure', () => {
  const badge = stepCostLabel({ ms: 1200, model: 'some-new-model', usd: null });
  assert.equal(badge.secsLabel, '1.2 s');
  assert.equal(badge.usdLabel, null);
  assert.equal(badge.line, '1.2 s · some-new-model');
});
