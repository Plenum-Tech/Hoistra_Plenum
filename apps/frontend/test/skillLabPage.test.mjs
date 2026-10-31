// skillLabPage — Administration › Skill lab: what a run found, in words and figures.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { skillLabVals, runHeadline } from '../src/logic/skillLabPage.js';

const NOW = Date.parse('2026-10-06T12:00:00Z');
const COMPARE = {
  id: 'r1', kind: 'compare', status: 'done', started_at: '2026-10-06T11:00:00Z', created_by: 'admin@x.test', sample: 6,
  summary: {
    reference: { questions: 6, score: 1, input_tokens: 40000, peak_prompt_tokens: 30000, usd: 0.12 },
    current: { questions: 6, score: 0.95, input_tokens: 26000, peak_prompt_tokens: 14000, usd: 0.08, compactions: 9, cut_to_fit: 1 }
  }
};

test('a measurement reads as how much was kept and what it saved', () => {
  assert.equal(runHeadline(COMPARE),
    'Self-managed context kept 95% of what the full-context answers said, on 35% fewer tokens (6 questions; 9 compactions, 1 cut to fit).');
});

test('a tuning run says whether it proposed a rewrite and why', () => {
  assert.equal(runHeadline({ kind: 'optimise', status: 'done', proposal_id: 'p1', summary: { verdict: 'as complete (0.95 vs 0.94) on 20% fewer tokens' } }),
    'Rewrite proposed - as complete (0.95 vs 0.94) on 20% fewer tokens');
  assert.equal(runHeadline({ kind: 'optimise', status: 'done', summary: { outcome: 'no change proposed' } }), 'No change proposed.');
  assert.equal(runHeadline({ kind: 'compare', status: 'failed', error: 'tables unavailable' }), 'Failed: tables unavailable');
});

test('the tiles come from the latest measurement, set against the full-context answers', () => {
  const v = skillLabVals({ state: { signedIn: true, view: 'skilllab', slRuns: [COMPARE], slProposals: [] }, slOpenRun() {}, slStart() {}, slLoad() {}, slReview() {}, slSetSample() {} }, NOW);
  assert.equal(v.isSkillLab, true);
  assert.deepEqual(v.slTiles.map((t) => t.value), ['95%', '26,000', '14,000', '$0.080', 'Shipped']);
  assert.equal(v.slTiles[1].hint, 'self-managed, vs 40,000 full');
  assert.equal(v.slRuns[0].kind, 'Measure');
});

test('a rewrite waiting for approval shows beside the text it would replace, with both decisions', () => {
  const calls = [];
  const c = { state: { signedIn: true, view: 'skilllab', slRuns: [], slCurrent: { 'query-builder/context-budget': 'OLD' },
    slProposals: [{ id: 'p1', status: 'proposed', skill: 'query-builder', doc: 'context-budget', content: 'NEW', created_at: '2026-10-06T11:30:00Z',
      evidence: { verdict: 'as complete on fewer tokens', rationale: 'compact earlier' } }] },
    slReview: (id, d) => calls.push([id, d]) };
  const v = skillLabVals(c, NOW);
  assert.equal(v.slProposals.length, 1);
  assert.equal(v.slProposals[0].current, 'OLD');
  assert.equal(v.slProposals[0].content, 'NEW');
  v.slProposals[0].approve();
  v.slProposals[0].reject();
  assert.deepEqual(calls, [['p1', 'approve'], ['p1', 'reject']]);
});

test('an approved rewrite can be reverted to the shipped text', () => {
  const calls = [];
  const v = skillLabVals({ state: { signedIn: true, view: 'skilllab', slRuns: [],
    slProposals: [{ id: 'a1', status: 'active', skill: 'query-builder', doc: 'context-budget', reviewed_at: '2026-10-06T10:00:00Z', reviewed_by: 'admin@x.test' }] },
    slReview: (id, d) => calls.push([id, d]) }, NOW);
  assert.equal(v.slTiles[4].value, 'Tuned');
  v.slActive.revert();
  assert.deepEqual(calls, [['a1', 'revert']]);
});
