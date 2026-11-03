// skillLabPage — Administration › Skill lab: what a run found, in words and figures.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
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


// A rewrite an admin writes (3 Nov 2026). The loop the feedback asked for ends here: annotations
// in Trace Lab become a draft, a person reads it, and what they decide the agents should be told
// becomes a proposal on this page. The risks are all about honesty — a rewrite that cannot take
// effect, and a written rewrite that reads as if it had been measured.

const CTL = (state) => Object.assign({
  state, slOpenRun() {}, slStart() {}, slLoad() {}, slReview() {}, slSetSample() {},
  slSetDoc() {}, slSetAppend() {}, slSetNote() {}, slWrite() {}
}, {});

test('only the documents the service says an overlay reaches can be written to', () => {
  // The page never decides this. A rewrite of a document read into a prompt at import would be
  // approved and change nothing the agents read.
  const v = skillLabVals(CTL({ signedIn: true, view: 'skilllab', slRuns: [], slProposals: [],
    slTunable: ['query-builder/context-budget'] }), NOW);
  assert.deepEqual(v.slTunable, ['query-builder/context-budget']);
  const none = skillLabVals(CTL({ signedIn: true, view: 'skilllab', slRuns: [], slProposals: [] }), NOW);
  assert.deepEqual(none.slTunable, [], 'the form must not offer a document the service did not list');
});

test('a written rewrite is marked as written and carries who wrote it and why', () => {
  const v = skillLabVals(CTL({ signedIn: true, view: 'skilllab', slRuns: [], slProposals: [{
    id: 'p9', skill: 'query-builder', doc: 'context-budget', status: 'proposed',
    created_at: '2026-10-06T11:30:00Z', content: 'new text',
    evidence: { source: 'written', written_by: 'ada@example.com', note: 'R32: the role was never checked' }
  }] }), NOW);
  const p = v.slProposals[0];
  assert.equal(p.written, true);
  assert.equal(p.writtenBy, 'ada@example.com');
  assert.equal(p.note, 'R32: the role was never checked');
});

test('a measured rewrite is not marked as written', () => {
  const v = skillLabVals(CTL({ signedIn: true, view: 'skilllab', slRuns: [], slProposals: [{
    id: 'p8', skill: 'query-builder', doc: 'context-budget', status: 'proposed',
    created_at: '2026-10-06T11:30:00Z', content: 'new text',
    evidence: { verdict: 'as complete on 20% fewer tokens', rationale: 'shorter preamble' }
  }] }), NOW);
  const p = v.slProposals[0];
  assert.equal(p.written, false);
  assert.equal(p.verdict, 'as complete on 20% fewer tokens');
});

test('the screen says which rewrites were written rather than measured', () => {
  const src = readFileSync(new URL('../src/screens/SkillLab.jsx', import.meta.url), 'utf-8');
  assert.ok(src.includes('written, not measured'),
    'a written rewrite sits beside measured ones and would read as if it had a score behind it');
  assert.ok(src.includes('No replay behind it'));
  assert.ok(src.includes('Nothing changes until an admin approves it'));
});

test('the form sends an append, not a replacement', () => {
  // A drafted lesson is a paragraph to add. Replacing the whole document by hand is how
  // instructions get silently dropped.
  const src = readFileSync(new URL('../src/logic/skillLabPage.js', import.meta.url), 'utf-8');
  assert.ok(/skillLabWrite\(\{ skill: [^}]*append: append/.test(src), 'the page replaces the document instead of adding to it');
  assert.ok(src.includes("doc.slice(0, at)") && src.includes("doc.slice(at + 1)"),
    'skill and doc must be sent apart, as the route takes them');
});

test('the proposals path is named twice and no more: one to read them, one to write one', () => {
  // GET and POST share the path, so two is right and three means a third caller drifted in.
  const src = readFileSync(new URL('../src/api/deepAgents.js', import.meta.url), 'utf-8');
  assert.equal(src.split("'/api/skill-lab/proposals'").length - 1, 2);
  assert.ok(/skillLabWrite:.*method: 'POST'/.test(src), 'writing one must be a POST');
  assert.ok(!/skillLabProposals:.*method:/.test(src), 'reading them must stay a GET');
});
