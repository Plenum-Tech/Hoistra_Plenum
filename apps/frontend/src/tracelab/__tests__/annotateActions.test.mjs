// annotateActions — open coding against a mocked service: Right saves at once with the head the reviewer last
// saw as base; Wrong needs a quote and a note before anything is sent; a failed save keeps the draft and offers
// Retry; a stale save shows the current version and Keep mine resends on top of it; a second press while one is
// saving sends nothing; reads never touch drafts; a reply after a company switch is dropped; r and w act only in
// the Conversations tab; Save & next opens the next answer of the same batch; comments keep their text on failure.
import { test, beforeEach, afterEach, mock } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} } };
globalThis.document = { visibilityState: 'visible' };
let handlers = {};
let requests = [];
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const o = opts || {};
  requests.push({ method: o.method || 'GET', url: u, body: typeof o.body === 'string' ? JSON.parse(o.body) : undefined });
  const h = handlers[(o.method || 'GET') + ' ' + u.pathname] || handlers[u.pathname];
  const out = typeof h === 'function' ? await h(u, o) : (h || [404, { detail: { reason: 'not_mocked', error: 'not mocked' } }]);
  return { ok: out[0] >= 200 && out[0] < 300, status: out[0], statusText: String(out[0]), text: async () => JSON.stringify(out[1]), headers: { get: () => null } };
};

const A = await import('../annotateActions.js');
const { reviewView, commentsView, blockView, reviewInitial, reviewOf, NO_FAILURE_NOTE } = await import('../logic/annotate.js');
const { tlStore, initialState, resetForCompany } = await import('../store.js');
const { setActingOrg } = await import('../../api/client.js');
const P = '/backend/trace-lab/api/trace-lab';
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
const S = () => tlStore.get();
const R = () => reviewOf(tlStore.get());
const fresh = (review, over) => {
  const seq = S().seq + 1;
  tlStore.set(Object.assign(initialState(), { seq, tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' }, over || {},
    { review: Object.assign(reviewInitial(seq), { key: 's:abc' }, review || {}) }));
};
const posted = (method, path) => requests.filter((r) => r.method === method && r.url.pathname === P + path);

const ANSWER = 'There were 758 work orders in August, 758 in total.';
const MINE = { id: 'v1', lineage_id: 'v1', version: 1, reviewer_id: 'me', verdict: 'right', note: NO_FAILURE_NOTE, anchor: null, tombstone: false };
const SAVED = (over) => Object.assign({}, MINE, { id: 'v2', version: 2 }, over || {});
const QUOTE = { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' };
const NOTHING = { ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: null };

beforeEach(() => { handlers = {}; requests = []; setActingOrg(null); A.stopAnnotationPoll(); fresh(); });
afterEach(() => { A.stopAnnotationPoll(); mock.timers.reset(); });

test('Right saves "no failure observed" with no anchor and the head I last saw as base, then reads again', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: SAVED() }];
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: MINE } } });
  assert.equal(await A.pickVerdict('turn-1', 'right'), true);
  assert.deepEqual(requests[0].body, { verdict: 'right', note: null, anchor: null, base: 'v1' });
  assert.equal(R().ann['turn-1'].mine.id, 'v2');
  assert.deepEqual(R().drafts, {});
  await settle();
  const read = posted('GET', '/annotations').at(-1).url.searchParams;
  assert.deepEqual([read.get('conversation_key'), read.get('turn_id')], ['s:abc', 'turn-1']);
  assert.equal(R().ann['turn-1'].mine.id, 'v2', 'a read with nothing new keeps what was saved');
});

test('a first verdict has no head to build on: base is null', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: SAVED() }];
  handlers[P + '/annotations'] = [200, NOTHING];
  await A.saveVerdict('turn-1', 'right');
  assert.equal(requests[0].body.base, null);
});

test('Wrong needs a quote and a note before anything is sent', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: MINE }];
  await A.pickVerdict('turn-1', 'wrong');
  assert.equal(R().drafts['turn-1'].focus, true);
  assert.equal(await A.saveVerdict('turn-1', 'wrong'), false);
  assert.equal(R().drafts['turn-1'].error, 'Select the text where it first went wrong.');
  A.selectQuote('turn-1', QUOTE);
  assert.equal(await A.saveVerdict('turn-1', 'wrong'), false);
  assert.equal(R().drafts['turn-1'].error, 'Say what went wrong first.');
  assert.equal(requests.length, 0, 'nothing reached the service');
  A.setNote('turn-1', 'Counted July, not August.');
  assert.equal(R().drafts['turn-1'].note, 'Counted July, not August.');
});

test('editing my own Wrong verdict opens its note and quote, and saves a changed note on top of it', async () => {
  const wrong = Object.assign({}, MINE, { id: 'w1', verdict: 'wrong', note: 'Old note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1 } });
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, wrong, { id: 'w2', note: 'New note' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: wrong } } });
  await A.pickVerdict('turn-1', 'wrong');
  assert.deepEqual([R().drafts['turn-1'].note, R().drafts['turn-1'].anchor.quote], ['Old note', '758']);
  A.setNote('turn-1', 'New note');
  assert.equal(await A.saveNote('turn-1'), true);
  assert.deepEqual(requests[0].body, { verdict: 'wrong', note: 'New note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1 }, base: 'w1' });
  assert.equal(R().ann['turn-1'].mine.id, 'w2');
});

test('a note typed on my saved Wrong verdict, without reopening it, keeps its saved quote', async () => {
  const wrong = Object.assign({}, MINE, { id: 'w1', verdict: 'wrong', note: 'Old note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1, span_name: 'turn', span_kind: 'turn' } });
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, wrong, { id: 'w2', note: 'New note' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: wrong } } });
  A.setNote('turn-1', 'New note');                       // typed straight into the box: no pickVerdict, no selection
  assert.equal(await A.saveNote('turn-1'), true);
  assert.deepEqual(requests[0].body, { verdict: 'wrong', note: 'New note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1 }, base: 'w1' });
  assert.equal(R().ann['turn-1'].mine.id, 'w2');
  assert.deepEqual(R().drafts, {});
});

test('a new selection beats the saved quote, and a saved quote is only borrowed from my live Wrong verdict', async () => {
  const wrong = Object.assign({}, MINE, { id: 'w1', verdict: 'wrong', note: 'Old note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1 } });
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, wrong, { id: 'w2' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: wrong } } });
  A.selectQuote('turn-1', { span_id: null, field: 'answer', quote: 'August', occurrence: 1, key: ':answer' });
  A.setNote('turn-1', 'Wrong month');
  assert.equal(await A.saveNote('turn-1'), true);
  assert.equal(requests[0].body.anchor.quote, 'August', 'the quote the reviewer just selected wins');
  // A saved Right has no quote to lend, and neither has a cleared Wrong: each still needs a selection.
  for (const mine of [MINE, Object.assign({}, MINE, { anchor: wrong.anchor }), Object.assign({}, wrong, { tombstone: true })]) {
    requests = [];
    fresh({ ann: { 'turn-1': { mine } } });
    A.setNote('turn-1', 'Wrong month');
    assert.equal(await A.saveNote('turn-1'), false);
    assert.equal(R().drafts['turn-1'].error, 'Select the text where it first went wrong.');
    assert.equal(requests.length, 0, 'nothing reached the service');
  }
});

test('a failed save keeps the draft and Retry resends it', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [502, { detail: { error: 'Unreachable' } }];
  A.selectQuote('turn-1', QUOTE);
  A.setNote('turn-1', 'Counted July, not August.');
  assert.equal(await A.saveNote('turn-1'), false);
  const d = R().drafts['turn-1'];
  assert.deepEqual([d.note, d.anchor.quote, d.error, d.saving], ['Counted July, not August.', '758', 'Not saved: Unreachable', false]);
  assert.equal(reviewView(S()).error, 'Not saved: Unreachable');
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: SAVED({ verdict: 'wrong' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  assert.equal(await A.retryVerdict('turn-1'), true);
  assert.equal(posted('PUT', '/turns/turn-1/verdict').at(-1).body.note, 'Counted July, not August.');
  assert.equal(R().drafts['turn-1'], undefined);
  assert.equal(await A.retryVerdict('turn-1'), false, 'nothing to retry once saved');
});

const THEIRS = Object.assign({}, MINE, { id: 'v7', version: 7, verdict: 'wrong', note: 'Wrong building', anchor: { quote: 'August', occurrence: 1 } });
const STALE = [409, { detail: { reason: 'stale', error: 'Changed since you opened it.', current: THEIRS } }];

test('a 409 shows the current version beside the draft; Keep mine resends on top of it', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = STALE;
  fresh({ ann: { 'turn-1': { mine: MINE } } });
  A.selectQuote('turn-1', Object.assign({}, QUOTE, { occurrence: 1 }));
  A.setNote('turn-1', 'Counted July.');
  assert.equal(await A.saveNote('turn-1'), false);
  const v = reviewView(S());
  assert.deepEqual([v.conflict.message, v.conflict.theirs.note, v.note, v.error], ['Changed since you opened it.', 'Wrong building', 'Counted July.', '']);
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, THEIRS, { id: 'v8' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  assert.equal(await A.resolveConflict('turn-1', true), true);
  const resent = posted('PUT', '/turns/turn-1/verdict').at(-1).body;
  assert.deepEqual([resent.base, resent.note], ['v7', 'Counted July.']);
  assert.equal(R().drafts['turn-1'], undefined);
});

test('Use theirs drops my draft, takes the current version and reads again', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = STALE;
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: MINE } } });
  A.selectQuote('turn-1', QUOTE);
  A.setNote('turn-1', 'Mine.');
  await A.saveNote('turn-1');
  assert.equal(await A.resolveConflict('turn-1', false), true);
  assert.equal(R().drafts['turn-1'], undefined);
  assert.equal(R().ann['turn-1'].mine.id, 'v7');
  await settle();
  assert.equal(posted('GET', '/annotations').length, 1, 'the read that settles any doubt about their version');
  assert.equal(await A.resolveConflict('turn-1', true), false, 'nothing to resolve once resolved');
});

test('clearing my verdict: a 409 keeps the intent to clear, and Keep mine clears on top of the current version', async () => {
  handlers['DELETE ' + P + '/turns/turn-1/verdict'] = STALE;
  handlers[P + '/annotations'] = [200, NOTHING];
  fresh({ ann: { 'turn-1': { mine: MINE } } });
  assert.equal(await A.deleteVerdict('turn-1'), false);
  assert.equal(requests[0].url.searchParams.get('base'), 'v1');
  assert.equal(reviewView(S()).conflict.theirs.note, 'Wrong building');
  const tomb = Object.assign({}, MINE, { id: 'v9', tombstone: true });
  handlers['DELETE ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: tomb }];
  assert.equal(await A.resolveConflict('turn-1', true), true);
  assert.equal(posted('PUT', '/turns/turn-1/verdict').length, 0, 'it clears; it does not save a verdict');
  assert.equal(posted('DELETE', '/turns/turn-1/verdict').at(-1).url.searchParams.get('base'), 'v7');
  assert.equal(R().ann['turn-1'].mine.id, 'v9');
  assert.equal(reviewView(S()).verdict, null);
  assert.equal(await A.deleteVerdict('turn-1'), false, 'a cleared verdict cannot be cleared again');
  await settle();
  assert.equal(posted('GET', '/annotations').length, 1, 'clearing reads again: other reviewers may be hidden again');
  fresh();
  assert.equal(await A.deleteVerdict('turn-1'), false, 'nothing to clear');
});

test('a failed clear says so and keeps the verdict', async () => {
  handlers['DELETE ' + P + '/turns/turn-1/verdict'] = [502, { detail: { error: 'Unreachable' } }];
  fresh({ ann: { 'turn-1': { mine: MINE } } });
  assert.equal(await A.deleteVerdict('turn-1'), false);
  assert.equal(reviewView(S()).error, 'Not cleared: Unreachable');
  assert.equal(R().ann['turn-1'].mine.id, 'v1');
});

test('a second press while a save is in flight sends nothing: it would only collide with the first', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = async () => { await gate; return [200, { ok: true, annotation: SAVED() }]; };
  handlers[P + '/annotations'] = [200, NOTHING];
  const first = A.saveVerdict('turn-1', 'right');
  await tick();
  assert.equal(reviewView(S()).saving, true);
  const second = A.saveVerdict('turn-1', 'right');
  assert.equal(A.shortcut('r'), true, 'the key is taken, and does nothing more');
  await tick();
  assert.equal(posted('PUT', '/turns/turn-1/verdict').length, 1, 'only the first press was sent');
  release();
  assert.equal(await first, true);
  assert.equal(await second, false);
  assert.equal(posted('PUT', '/turns/turn-1/verdict').length, 1);
});

test('reading again never wipes a draft, and a block\'s text arrives with it', async () => {
  handlers[P + '/annotations'] = [200, { ok: true, turns: { 'turn-1': { mine: MINE, others: [], others_count: 0 } }, comments: [], blocks: { 'turn-1': { ':answer': ANSWER } } }];
  A.selectQuote('turn-1', Object.assign({}, QUOTE, { occurrence: 1 }));
  A.setNote('turn-1', 'half-written');
  await A.loadAnnotations();
  assert.equal(R().drafts['turn-1'].note, 'half-written');
  assert.equal(R().blocks['turn-1'][':answer'], ANSWER);
  assert.equal(R().key, 's:abc');
  assert.equal(R().loading, false);
  const blk = blockView(S(), { span_id: null, field: 'answer' });
  assert.ok(blk.popover);
  assert.deepEqual(blk.segments.filter((x) => x.ids.length).map((x) => [x.text, x.ids]), [['758', ['draft']]]);
});

test('reads merge within a conversation and start over for another one', async () => {
  handlers[P + '/annotations'] = (u) => (u.searchParams.get('conversation_key') === 's:abc'
    ? [200, { ok: true, turns: { 'turn-2': { mine: MINE } }, comments: [{ id: 'c1', lineage_id: 'l1', reviewer_id: 'me', note: 'n' }], comments_hidden: 1, blocks: { 'turn-2': { ':answer': 'two' } } }]
    : [200, { ok: true, turns: { 'turn-9': { mine: null } }, comments: [], comments_hidden: 0, blocks: null }]);
  fresh({ key: 's:abc', ann: { 'turn-1': { mine: MINE } }, blocks: { 'turn-1': { ':answer': 'one' } } });
  await A.loadAnnotations({ key: 's:abc', turnId: 'turn-2' });
  assert.deepEqual(Object.keys(R().ann).sort(), ['turn-1', 'turn-2']);
  assert.deepEqual(Object.keys(R().blocks).sort(), ['turn-1', 'turn-2']);
  assert.deepEqual([R().comments.length, R().commentsHidden], [1, 1]);
  await A.loadAnnotations({ key: 's:xyz' });
  assert.deepEqual(Object.keys(R().ann), ['turn-9'], 'another conversation never shows this one\'s verdicts');
  assert.deepEqual(R().comments, []);
  assert.equal(R().key, 's:xyz');
  const asked = posted('GET', '/annotations').map((r) => r.url.searchParams.get('turn_id'));
  assert.deepEqual(asked, ['turn-2', null]);
});

test('a failed read shows its reason and keeps what is on screen; an overtaken read is dropped', async () => {
  handlers[P + '/annotations'] = [502, { detail: { error: 'Unreachable' } }];
  fresh({ key: 's:abc', ann: { 'turn-1': { mine: MINE } } });
  await A.loadAnnotations();
  assert.equal(R().error, 'Unreachable');
  assert.equal(R().loading, false);
  assert.equal(R().ann['turn-1'].mine.id, 'v1');
  const before = requests.length;
  fresh({ key: 's:abc', ann: { 'turn-1': { mine: MINE } } }, { convKey: null });
  await A.loadAnnotations();
  assert.equal(requests.length, before, 'no conversation open, nothing to read');
  fresh({ key: 's:abc', ann: { 'turn-1': { mine: MINE } } });
  let release;
  const gate = new Promise((r) => { release = r; });
  let n = 0;
  handlers[P + '/annotations'] = async () => {
    n += 1;
    if (n === 1) { await gate; return [200, { ok: true, turns: { late: { mine: null } }, comments: [] }]; }
    return [200, { ok: true, turns: { fresh: { mine: null } }, comments: [] }];
  };
  const slow = A.loadAnnotations();
  await tick();
  await A.loadAnnotations();
  release();
  await slow;
  assert.deepEqual(Object.keys(R().ann).sort(), ['fresh', 'turn-1'], 'the older read never lands over the newer one');
});

test('a reply after a company switch is dropped, and a write that outlives it recreates nothing', async () => {
  let release;
  const gate = new Promise((r) => { release = r; });
  handlers[P + '/annotations'] = async () => { await gate; return [200, { ok: true, turns: { 'turn-1': { mine: MINE } }, comments: [] }]; };
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = async () => { await gate; return [200, { ok: true, annotation: SAVED() }]; };
  const read = A.loadAnnotations();
  const save = A.saveVerdict('turn-1', 'right');
  await tick();
  resetForCompany();
  release();
  await read;
  assert.equal(await save, false);
  const r = R();
  assert.deepEqual([r.ann, r.drafts, r.loading, r.error], [{}, {}, false, ''], 'the old company\'s drafts and reads are gone, and nothing late wrote to the new one');
  assert.equal(reviewView(S()), null, 'the page is back to nothing open');
});

test('the app\'s stale-company error is kept quiet: no error line, no draft error', async () => {
  handlers[P + '/annotations'] = () => { setActingOrg('11111111-1111-1111-1111-111111111111'); return [200, NOTHING]; };
  await A.loadAnnotations();
  assert.equal(R().error, '');
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = () => { setActingOrg(null); return [200, { ok: true, annotation: SAVED() }]; };
  A.selectQuote('turn-1', QUOTE);
  A.setNote('turn-1', 'n');
  assert.equal(await A.saveNote('turn-1'), false);
  assert.deepEqual([R().drafts['turn-1'].error, R().drafts['turn-1'].saving], ['', false], 'the draft stays, ready to send again');
});

test('a selection becomes the draft\'s quote; a refused one says why and leaves the draft alone', () => {
  const block = { key: ':answer', span_id: null, field: 'answer', text: ANSWER };
  A.selectFromBlock('turn-1', block, { startKey: ':answer', endKey: 's1:output', start: 0, end: 3 });
  assert.equal(R().selError, 'Select text inside one step.');
  assert.equal(R().drafts['turn-1'], undefined);
  A.selectFromBlock('turn-1', block, { startKey: ':answer', endKey: ':answer', start: 38, end: 41 });
  assert.equal(R().selError, '');
  assert.deepEqual([R().drafts['turn-1'].verdict, R().drafts['turn-1'].popover, R().drafts['turn-1'].anchor.occurrence], ['wrong', true, 2]);
  A.cancelDraft('turn-1');
  assert.equal(R().drafts['turn-1'], undefined);
});

test('keyboard r and w act on the open answer only in the Conversations tab', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: MINE }];
  handlers[P + '/annotations'] = [200, NOTHING];
  assert.equal(A.shortcut('w'), true);
  assert.equal(R().drafts['turn-1'].focus, true);
  assert.equal(A.shortcut('r'), true);
  await settle();
  assert.deepEqual(posted('PUT', '/turns/turn-1/verdict').map((r) => r.body.verdict), ['right']);
  assert.equal(A.shortcut('x'), false);
  fresh({}, { tab: 'runs' });
  assert.equal(A.shortcut('r'), false);
  assert.equal(A.shortcut('w'), false);
  fresh({}, { turnId: null });
  assert.equal(A.shortcut('r'), false, 'no open answer');
});

test('Save & next opens the next answer of the same batch that still needs my verdict', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: MINE }];
  handlers[P + '/annotations'] = [200, NOTHING];
  handlers[P + '/queue'] = [200, { ok: true, remaining: 2, items: [
    { turn_id: 'turn-1', conversation_key: 's:abc', batch_id: 'b1' }, { turn_id: 'turn-9', conversation_key: 's:xyz', batch_id: 'b1' }] }];
  fresh({ ann: { 'turn-1': { mine: null, sample_item: { batch_id: 'b1', reason: 'uniform pick' } } } });
  assert.equal(await A.saveAndNext('turn-1', 'right'), true);
  const q = posted('GET', '/queue')[0].url.searchParams;
  assert.deepEqual([q.get('batch_id'), q.get('limit')], ['b1', '20']);
  assert.deepEqual([S().convKey, S().turnId], ['s:xyz', 'turn-9']);
  assert.deepEqual([R().queue.length, R().queueRemaining], [2, 2]);
});

test('Save & next with nothing left stays put; a save that fails never moves on', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: MINE }];
  handlers[P + '/annotations'] = [200, NOTHING];
  handlers[P + '/queue'] = [200, { ok: true, remaining: 0, items: [{ turn_id: 'turn-1', conversation_key: 's:abc', batch_id: 'b1' }] }];
  fresh({ ann: { 'turn-1': { mine: null } } });
  assert.equal(await A.saveAndNext('turn-1', 'right'), false);
  assert.deepEqual([S().convKey, S().turnId], ['s:abc', 'turn-1']);
  assert.equal(posted('GET', '/queue')[0].url.searchParams.has('batch_id'), false, 'an answer outside a batch asks the whole queue');
  requests = [];
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [502, { detail: { error: 'Unreachable' } }];
  assert.equal(await A.saveAndNext('turn-1', 'right'), false);
  assert.equal(posted('GET', '/queue').length, 0);
});

test('Start reviewing a batch opens its first answer that still needs my verdict, on the Conversations tab', async () => {
  handlers[P + '/queue'] = [200, { ok: true, remaining: 2, items: [
    { turn_id: 'turn-4', conversation_key: 's:four', batch_id: 'b1' }, { turn_id: 'turn-9', conversation_key: 's:xyz', batch_id: 'b1' }] }];
  fresh({}, { tab: 'map', convKey: null, turnId: null });
  assert.equal(await A.reviewBatch('b1'), true);
  assert.equal(posted('GET', '/queue')[0].url.searchParams.get('batch_id'), 'b1');
  assert.deepEqual([S().tab, S().convKey, S().turnId], ['conversations', 's:four', 'turn-4']);
  requests = [];
  handlers[P + '/queue'] = [200, { ok: true, remaining: 0, items: [] }];
  fresh({}, { tab: 'map', convKey: null, turnId: null });
  assert.equal(await A.reviewBatch('b1'), false, 'nothing waiting: it stays on the Map');
  assert.equal(S().tab, 'map');
  assert.match(R().error, /Nothing in this batch is waiting/);
});

test('a queue that cannot be read says so and returns nothing', async () => {
  handlers[P + '/queue'] = [502, { detail: { error: 'Unreachable' } }];
  assert.deepEqual(await A.loadQueue('b1'), []);
  assert.equal(R().error, 'Unreachable');
});

test('comments: a failed comment keeps its text; an empty one is refused; a saved one joins the list and teaches me my id', async () => {
  handlers['POST ' + P + '/comments'] = [502, { detail: { error: 'Unreachable' } }];
  A.setCommentDraft('Same failure as yesterday');
  assert.equal(await A.addComment(), false);
  const v = commentsView(S());
  assert.deepEqual([v.draft, v.error, v.busy], ['Same failure as yesterday', 'Not saved: Unreachable', false]);
  A.setCommentDraft('   ');
  assert.equal(await A.addComment(), false);
  assert.equal(commentsView(S()).error, 'Write the comment first.');
  const requestsBefore = requests.length;
  A.setCommentDraft('Same failure as yesterday');
  handlers['POST ' + P + '/comments'] = [201, { ok: true, annotation: { id: 'c1', lineage_id: 'l1', reviewer_id: 'me', reviewer_email: 'me@example.com', note: 'Same failure as yesterday' } }];
  assert.equal(await A.addComment(), true);
  assert.deepEqual(requests.at(-1).body, { conversation_key: 's:abc', turn_id: 'turn-1', note: 'Same failure as yesterday' });
  assert.equal(requests.length, requestsBefore + 1, 'the empty comment sent nothing');
  const after = commentsView(S());
  assert.deepEqual([after.draft, after.error, after.rows.map((r) => [r.id, r.mine])], ['', '', [['l1', true]]]);
  assert.equal(R().me, 'me');
  fresh({}, { convKey: null });
  A.setCommentDraft('x');
  assert.equal(await A.addComment(), false, 'no open conversation to comment on');
});

test('editing and removing a comment send its base and read again; a stale edit says so', async () => {
  handlers['PUT ' + P + '/comments/l1'] = [200, { ok: true, annotation: { id: 'c2', lineage_id: 'l1', reviewer_id: 'me', note: 'new' } }];
  handlers['DELETE ' + P + '/comments/l1'] = [200, { ok: true }];
  handlers[P + '/annotations'] = [200, NOTHING];
  assert.equal(await A.editComment('l1', 'new', 'c1'), true);
  assert.deepEqual(posted('PUT', '/comments/l1')[0].body, { note: 'new', base: 'c1' });
  assert.equal(await A.deleteComment('l1', 'c2'), true);
  assert.equal(posted('DELETE', '/comments/l1')[0].url.searchParams.get('base'), 'c2');
  assert.equal(posted('GET', '/annotations').length, 2, 'each one reads the list again');
  handlers['PUT ' + P + '/comments/l1'] = [409, { detail: { reason: 'stale', error: 'Changed since you opened it.' } }];
  assert.equal(await A.editComment('l1', 'newer', 'c1'), false);
  assert.equal(commentsView(S()).error, 'Changed since you opened it: Changed since you opened it.');
  handlers['DELETE ' + P + '/comments/l1'] = [502, { detail: { error: 'Unreachable' } }];
  assert.equal(await A.deleteComment('l1', 'c2'), false);
  assert.equal(commentsView(S()).error, 'Not removed: Unreachable');
});

test('the 30-second read runs only on Conversations with a conversation open and the page visible, and stops on request', async () => {
  handlers[P + '/annotations'] = [200, NOTHING];
  mock.timers.enable({ apis: ['setInterval'] });
  A.ensureAnnotationPoll();
  A.ensureAnnotationPoll();
  mock.timers.tick(30000);
  await settle();
  assert.equal(posted('GET', '/annotations').length, 1, 'one timer, however often it is asked for');
  fresh({}, { tab: 'runs' });
  mock.timers.tick(30000);
  fresh({}, { convKey: null });
  mock.timers.tick(30000);
  fresh();
  document.visibilityState = 'hidden';
  mock.timers.tick(30000);
  document.visibilityState = 'visible';
  await settle();
  assert.equal(posted('GET', '/annotations').length, 1, 'wrong tab, no conversation, hidden page: no read');
  A.stopAnnotationPoll();
  mock.timers.tick(60000);
  await settle();
  assert.equal(posted('GET', '/annotations').length, 1, 'stopped');
  A.ensureAnnotationPoll();
  mock.timers.tick(30000);
  await settle();
  assert.equal(posted('GET', '/annotations').length, 2, 'it can start again');
});
