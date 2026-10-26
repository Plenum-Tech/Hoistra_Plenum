// taxonomySuggest — AI suggestions on the open answer in Conversations (logic/suggest.js, taxonomyActions.js): a dashed
// highlight found in the server's own block text, cards in the margin or the list, Accept / Dismiss / the other value,
// hidden until the reviewer has judged the answer; and the one view model every taxonomy screen reads.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync } from 'node:fs';
import { taxonomyApi, reviewApi, A, C, S, X, tick, fresh, loaded, setActingOrg, installDefaults, restoreApis } from './tax-helper.mjs';
const G = await import('../logic/suggest.js');
const V = await import('../logic/taxonomyVals.js');

beforeEach(installDefaults);
afterEach(restoreApis);

const ANSWER = 'There are 12 open work orders, including 3 cancelled ones.';
const SUGS = [
  { id: 'p-1', kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', mode_definition: 'Present when cancelled jobs count as open.',
    suggestion: 'present', quote: '3 cancelled ones', field: 'answer', span_id: null, evidence: 'Counts cancelled jobs among the open ones.', base: null, turn_id: 't1' },
  { id: 'p-2', kind: 'cell', mode_id: 'm-2', mode_name: 'invents_asset_codes', mode_definition: 'Present when an asset code is made up.',
    suggestion: 'absent', quote: '', field: null, span_id: null, evidence: 'Names no asset code.', base: 'L-2', turn_id: 't1' }];
// The open answer t1 of conversation s:chat-1, read by a company admin (the default) or the platform team (owner).
const open = (tax, owner, review) => fresh(loaded(tax || {}, !!owner), { tab: 'conversations', convKey: 's:chat-1', turnId: 't1', review: review || {} });
const suggest = () => G.taxonomySuggestVals(S(), A).tlTaxSuggest;

test('a suggestion’s dashed highlight is found in the server’s own block text', () => {
  assert.deepEqual(G.suggestionMarks(SUGS, ':answer', ANSWER), [{ id: 'sug:p-1', start: 41, end: 57, kind: 'suggestion' }]);
  assert.deepEqual(G.suggestionMarks(SUGS, ':question', 'How many open?'), []);
  assert.deepEqual(G.suggestionMarks(SUGS, ':answer', 'No such text here.'), []);
  assert.deepEqual(G.suggestionMarks(SUGS, ':answer', undefined), []);
  open({ tlSugTurnId: 't1', tlSug: SUGS });
  assert.deepEqual(A.sugMarks('t1', ':answer', ANSWER).map((m) => m.id), ['sug:p-1']);
  assert.deepEqual(A.sugMarks('t9', ':answer', ANSWER), []);
});

test('the open answer’s suggestions are read once, stay hidden until it is judged, then appear', async () => {
  const asked = [];
  let hidden = true;
  taxonomyApi.proposals = async (q) => { asked.push(['proposals', q]); return { ok: true, served_at: 's', total: hidden ? 0 : 2, hidden: hidden ? 2 : 0, proposals: hidden ? [] : SUGS }; };
  taxonomyApi.turnLabels = async (t) => { asked.push(['labels', t]); return { ok: true, hidden, cells: hidden ? [] : [
    { mode_id: 'm-1', name: 'counts_cancelled_jobs_as_open', state: 'unchecked', proposal: null },
    { mode_id: 'm-2', name: 'invents_asset_codes', state: 'unchecked', proposal: { id: 'p-2', suggestion: 'absent', evidence: 'Names no asset code.' } },
    { mode_id: 'm-3', name: 'wrong_period', state: 'present', source: 'human_open_code', approval: 'single', decided_by: 'ada@example.com' }] }; };
  open();
  A.sugEnsure('t1', false);
  await tick();
  A.sugEnsure('t1', false);
  assert.equal(asked.length, 2, 'read once per answer');
  let v = suggest();
  assert.equal(v.hidden, true);
  assert.equal(v.hiddenText, 'AI suggestions and failure modes for this answer stay hidden until you judge it yourself.');
  hidden = false;
  A.sugEnsure('t1', true);
  await tick();
  assert.deepEqual(asked[2], ['proposals', { turn_id: 't1' }]);
  v = suggest();
  assert.equal(v.hidden, false);
  assert.deepEqual(v.chips.map((ch) => [ch.kind, ch.text]), [['ai', 'AI: absent · invents_asset_codes'], ['present', 'wrong_period: present']]);
  A.sugEnsure('t1', true);
  assert.equal(asked.length, 4, 'a judged answer that is no longer hidden is not read again');
  A.sugEnsure('t2', false);
  assert.deepEqual([X().tlSugTurnId, X().tlSug, X().tlSugLoading], ['t2', null, true], 'another answer starts over, without the last one’s cards');
  await tick();
  assert.equal(asked.at(-1)[1], 't2');
});

test('a card sits in the margin when its quote is on screen, in the list otherwise; Accept, Dismiss and the other value', async () => {
  const sent = [];
  taxonomyApi.decideProposal = async (id, body) => { sent.push([id, body]); return { ok: true, proposal: { id }, label: {} }; };
  taxonomyApi.proposals = async () => ({ ok: true, proposals: SUGS, total: 2, hidden: 0 });
  let grid = 0;
  taxonomyApi.labelsMatrix = async () => { grid += 1; return { ok: true, modes: [], rows: [] }; };
  open({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1 }, false, { blocks: { t1: { ':answer': ANSWER } } });
  const v = suggest();
  assert.deepEqual(v.cards.map((k) => [k.markId, k.kicker, k.acceptLabel, k.otherLabel]), [['sug:p-1', 'AI suggestion', 'Accept', 'Dismiss']]);
  assert.match(v.cards[0].otherHint, /close example where the failure is absent/);
  assert.deepEqual(v.unplaced.map((k) => [k.id, k.acceptLabel, k.otherLabel]), [['p-2', 'Accept: absent', 'Mark present']]);
  await v.cards[0].other();
  await v.unplaced[0].other();
  assert.deepEqual(sent, [['p-1', { decision: 'dismiss', present: null, base: null }], ['p-2', { decision: 'override', present: true, base: 'L-2' }]]);
  assert.equal(grid, 0, 'the grid is not loaded, so it is not read');
  await v.cards[0].accept();
  assert.deepEqual(sent.at(-1), ['p-1', { decision: 'accept', present: null, base: null }]);
});

test('a decision reads the answer’s suggestions again, and the grid, the modes and Progress when they are on screen', async () => {
  let grid = 0;
  let modes = 0;
  let progress = 0;
  taxonomyApi.decideProposal = async (id) => ({ ok: true, proposal: { id }, label: {} });
  taxonomyApi.labelsMatrix = async () => { grid += 1; return { ok: true, modes: [], rows: [] }; };
  taxonomyApi.modes = async () => { modes += 1; return { ok: true, is_owner: false, modes: [], agents: [], levels: [] }; };
  reviewApi.progress = async () => { progress += 1; return { ok: true }; };
  let reread = 0;
  taxonomyApi.proposals = async () => { reread += 1; return { ok: true, proposals: [], total: 0, hidden: 0 }; };
  fresh(loaded({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1, tlMatrixLoadedAt: 1 }), { tab: 'progress', turnId: 't1' });
  assert.equal(await A.sugDecide('p-2', 'accept'), true);
  await tick();
  assert.equal(reread, 1);
  assert.deepEqual([grid, modes, progress], [1, 1, 1]);
  assert.equal(await A.sugDecide('p-none', 'accept'), false);
});

test('a refused suggestion keeps the cards and shows the server’s sentence', async () => {
  taxonomyApi.decideProposal = async () => { throw Object.assign(new Error('That suggestion was already decided.'), { status: 409, reason: 'not_pending',
    body: { detail: { ok: false, reason: 'not_pending', error: 'That suggestion was already decided.' } } }); };
  open({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1 });
  assert.equal(await A.sugDecide('p-1', 'dismiss'), false);
  assert.equal(X().tlSugErr, 'That suggestion was already decided.');
  assert.equal(suggest().error, 'That suggestion was already decided.');
  assert.equal(X().tlSug.length, 2);
});

test('the platform team, with no company to review in, gets no suggestions in Conversations', () => {
  open({}, true);
  assert.equal(suggest(), null);
  A.sugEnsure('t1', false);
  assert.equal(X().tlSugTurnId, null, 'nothing is read for it');
  setActingOrg('org-1');
  open({}, false);
  assert.equal(suggest().turnId, 't1');
  setActingOrg(null);
  fresh(loaded({}, false), { tab: 'conversations' });
  assert.equal(suggest(), null, 'no answer open, no suggestions');
});

test('judged is read from the reviewer’s own verdict on the open answer', () => {
  open({}, false, { ann: { t1: { mine: { id: 'v1', verdict: 'wrong', tombstone: false } } } });
  assert.equal(suggest().judged, true);
  open({}, false, { ann: { t1: { mine: { id: 'v1', verdict: 'wrong', tombstone: true } } } });
  assert.equal(suggest().judged, false, 'a cleared verdict is not a judgment');
  open({}, false, {});
  assert.equal(suggest().judged, false);
});

test('the view models are one object: every taxonomy screen reads these keys, and the reset covers every key the module writes', () => {
  for (const m of ['modesLoad', 'modeShow', 'modeDialogSubmit', 'noteFile', 'reqsSave', 'matrixToggle', 'matrixShowMissing',
    'proposalsBulkCheck', 'proposalsBulkApprove', 'proposalsStartRun', 'taxEnsure', 'sugEnsure', 'sugMarks', 'retryWrite', 'stopPolls']) {
    assert.equal(typeof A[m], 'function', m);
  }
  open();
  const v = V.taxonomyVals(S(), A, Date.now());
  assert.deepEqual(Object.keys(v).sort(), ['tlTaxCell', 'tlTaxDialog', 'tlTaxDrawer', 'tlTaxLabels', 'tlTaxMap', 'tlTaxModeProposed', 'tlTaxOwner', 'tlTaxOwnerHint',
    'tlTaxProgress', 'tlTaxQueue', 'tlTaxReqs', 'tlTaxRuns', 'tlTaxSuggest', 'tlTaxUnfiled']);
  const dir = new URL('..', import.meta.url);
  const files = ['taxonomyActions.js'].concat(readdirSync(new URL('logic/', dir)).filter((f) => /^(tax|labels|proposals|suggest)/.test(f)).map((f) => 'logic/' + f));
  const src = files.map((f) => readFileSync(new URL(f, dir), 'utf8')).join('\n');
  const keys = new Set([...src.matchAll(/\b(tl(?:Modes|Unfiled|Reqs|Matrix|Proposals|Sug|Writes)[A-Za-z]*)\s*:/g)].map((m) => m[1]));
  const patch = C.taxonomyInitial(1);
  assert.ok(keys.size > 60, String(keys.size));
  for (const k of keys) assert.ok(Object.prototype.hasOwnProperty.call(patch, k), k);
  assert.deepEqual(Object.keys(patch).filter((k) => !keys.has(k) && k !== 'seq'), [], 'every key in the reset is written somewhere');
});

test('suggestions show on the Conversations tab only, even while the store still holds the open answer', () => {
  open({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1 });
  assert.equal(suggest().turnId, 't1');
  assert.equal(suggest().cards.length + suggest().unplaced.length, 2);
  fresh(loaded({ tlSugTurnId: 't1', tlSug: SUGS }, false), { tab: 'progress', convKey: 's:chat-1', turnId: 't1' });
  assert.equal(suggest(), null, 'the store keeps turnId on other tabs');
  fresh(loaded({ tlSugTurnId: 't1', tlSug: SUGS }, false), { tab: 'conversations', convKey: 's:chat-1', turnId: null });
  assert.equal(suggest(), null);
});
