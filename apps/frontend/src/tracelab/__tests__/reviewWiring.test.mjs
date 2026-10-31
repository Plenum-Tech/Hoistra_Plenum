// reviewWiring — Phase 4's open coding, sampling, Map and Progress are wired into the Trace Lab page: Map and
// Progress are tabs (nav.js, logic/shell.js, a ?tl= link reaches them), the timeline's step and note cards carry
// their anchor, the page reads annotations when a conversation is on screen and the Map / Progress figures when
// their tab opens, r and w are the verdict keys (never while typing), the page stops its reads when it closes, and
// the Conversations tab carries the verdict bar, the comments and the margin notes around the timeline.
import { test, beforeEach, afterEach, after, mock } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} } };
const listeners = {};
globalThis.document = {
  visibilityState: 'visible',
  addEventListener: (type, fn) => { (listeners[type] = listeners[type] || []).push(fn); },
  removeEventListener: (type, fn) => { listeners[type] = (listeners[type] || []).filter((f) => f !== fn); }
};
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

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const W = await import('../wiring.js');
const A = await import('../annotateActions.js');
const { tlStore, initialState } = await import('../store.js');
const { TABS, linkOpenPatch } = await import('../nav.js');
const { TAB_LABELS, parseLink, tabsView } = await import('../logic/shell.js');
const { toCards } = await import('../logic/timeline.js');
const { reviewInitial, reviewOf } = await import('../logic/annotate.js');
const { samplingInitial } = await import('../logic/sampling.js');
const { Shell } = await bundle('TraceLabApp.jsx');
const { default: Timeline } = await bundle('views/Timeline.jsx');
const P = '/backend/trace-lab/api/trace-lab';
const here = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
const gets = (path) => requests.filter((r) => r.method === 'GET' && r.url.pathname === P + path);

const ANSWER = 'There were 758 work orders in August.';
const REC = { id: 'turn-1', timestamp: '2026-10-15T11:00:00Z', models: [], usage: { cost_usd: 0.01, latency_ms: 4000, tool_calls: 1 },
  meta: { channel: 'chat', turn_index: 1, turn_count: 1, asker: { email: 'fm@example.com' } }, flags: [], feedback: null,
  messages: [
    { id: 'turn-1:root:user', role: 'user', depth: 0, text: 'How many work orders?', anchor: { turn_id: 'turn-1', span_id: 'root', field: 'question' } },
    { id: 'turn-1:t1:call', role: 'tool_call', depth: 1, span_id: 't1', name: 'answer_from_records', arguments: {}, ok: true, latency_ms: 10 },
    { id: 'turn-1:t1:result', role: 'tool_result', depth: 1, span_id: 't1', name: 'answer_from_records', content: '758 rows', ok: true,
      anchor: { turn_id: 'turn-1', span_id: 't1', field: 'output' } },
    { id: 'turn-1:s1:step', role: 'step', depth: 1, span_id: 's1', name: 'fetch', input: { q: 1 }, output: { n: 758 }, ok: true, anchor: { turn_id: 'turn-1', span_id: 's1', field: 'output' } },
    { id: 'turn-1:n1:note', role: 'system_note', depth: 1, span_id: 'n1', name: 'recheck', output: 'checked', anchor: { turn_id: 'turn-1', span_id: 'n1', field: 'output' } },
    { id: 'turn-1:root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: ANSWER, anchor: { turn_id: 'turn-1', span_id: 'root', field: 'answer' } }] };
const CONV = { key: 's:abc', session_kind: 'observed', session_label: 'Work orders', turns: [{ turn_id: 'turn-1', turn_index: 1, question: 'How many?', ok: true }], active: REC };
const BLOCKS = { ':question': 'How many work orders?', 't1:output': '758 rows', 's1:output': '{"n":758}', 'n1:output': 'checked', ':answer': ANSWER };
const WRONG = { id: 'w1', lineage_id: 'w1', version: 1, reviewer_id: 'me', verdict: 'wrong', note: 'Counted July.', tombstone: false,
  anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1, span_name: 'turn', span_kind: 'turn' } };
const open = (over, review) => Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: 'turn-1', conv: { data: CONV, loading: false, error: '', reason: '' } },
  over || {}, { review: Object.assign(reviewInitial(), { key: 's:abc' }, review || {}) });
// The timeline's step rows, all opened (a step's body, and so its annotatable text, shows only while it is open).
const STEPS_OPEN = Object.fromEntries(toCards(REC).filter((c) => ['user', 'answer', 'cot'].indexOf(c.kind) < 0).map((c) => [c.id + ':step', true]));
const page = (s) => renderToStaticMarkup(React.createElement(Shell, { s, now: Date.parse('2026-10-15T12:00:00Z') }));

function fresh(over) {
  const seq = tlStore.get().seq + 1;
  tlStore.set(Object.assign(initialState(), { seq }, over || {}));
  return seq;
}
beforeEach(() => { handlers = {}; requests = []; Object.keys(listeners).forEach((k) => { listeners[k] = []; }); A.stopAnnotationPoll(); fresh(); });
afterEach(() => { A.stopAnnotationPoll(); mock.timers.reset(); });

// ── the tabs ─────────────────────────────────────────────────────────────────────────────────────────────────────
test('Map and Progress are tabs after Model calls: in the tab list, the labels, the link parser and the reopen link', () => {
  assert.deepEqual(TABS.slice(0, 6), ['conversations', 'runs', 'golden', 'models', 'map', 'progress']);
  assert.deepEqual(tabsView(Object.assign(initialState(), { tab: 'map' })).slice(4, 6), [{ key: 'map', label: 'Map', on: true }, { key: 'progress', label: 'Progress', on: false }]);
  assert.equal(TAB_LABELS.map, 'Map');
  assert.equal(TAB_LABELS.progress, 'Progress');
  assert.equal(parseLink('?tl=map').tab, 'map');
  assert.equal(parseLink('?tl=progress&run=live').tab, 'progress');
  assert.deepEqual(linkOpenPatch({ signedIn: true, role: 'admin', view: 'home' }, '?tl=progress'), { view: 'tracelab', role: 'admin', navOpen: true, detail: null });
});

test('the timeline gives step and note cards their anchor, so their text can be annotated', () => {
  const cards = toCards(REC);
  assert.deepEqual(cards.filter((c) => c.kind === 'step' || c.kind === 'note').map((c) => c.anchor && c.anchor.span_id), ['s1', 'n1']);
  assert.equal(cards.find((c) => c.kind === 'user').anchor.field, 'question');
  const bare = toCards({ messages: [{ id: 'x', role: 'step', name: 'fetch', output: 1 }, { id: 'y', role: 'system_note', name: 'z', output: 1 }] });
  assert.deepEqual(bare.map((c) => c.anchor), [null, null], 'a message with no anchor stays unanchored');
  const anchor = { turn_id: 'turn-1', span_id: 'p1', field: 'output' };
  const shownInput = toCards({ messages: [{ id: 'p', role: 'system_note', subtype: 'prompt', name: 'prompt assembled', input: { s: 1 }, anchor },
    { id: 'q', role: 'system_note', name: 'stage', input: { a: 1 }, anchor }] });
  assert.deepEqual(shownInput.map((c) => c.anchor), [null, null], 'a card that shows the input is not the block its anchor names');
});

test('Map and Progress open without the conversation filters, and each shows its own page', () => {
  const map = page(Object.assign(initialState(), { tab: 'map' }));
  assert.equal(map.includes('tl-filters'), false);
  assert.equal(map.includes('Refresh now'), false, 'the shared refresh row is for the tabs it reads');
  assert.ok(map.includes('Draw a review batch'));
  assert.match(map, /aria-selected="true"[^>]*>Map</);
  const prog = page(Object.assign(initialState(), { tab: 'progress' }));
  assert.equal(prog.includes('tl-filters'), false);
  assert.ok(prog.includes('Agreement between reviewers'));
  assert.match(prog, /aria-selected="true"[^>]*>Progress</);
  const conv = page(Object.assign(initialState(), { tab: 'conversations' }));
  assert.ok(conv.includes('tl-filters'), 'Conversations keeps its filters');
  assert.equal(conv.includes('Draw a review batch'), false);
});

// ── the keys ─────────────────────────────────────────────────────────────────────────────────────────────────────
const key = (k, over) => {
  const e = Object.assign({ key: k, target: { tagName: 'DIV' }, defaultPrevented: false, repeat: false, ctrlKey: false, metaKey: false, altKey: false,
    preventDefault() { this.defaultPrevented = true; } }, over || {});
  return e;
};

test('r saves Right and w opens the Wrong note, on the open answer of the Conversations tab, and the key is taken', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: { id: 'v1', verdict: 'right' } }];
  handlers[P + '/annotations'] = [200, { ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: {} }];
  const seq = fresh({ tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' });
  tlStore.set({ review: Object.assign(reviewInitial(seq), { key: 's:abc' }) });
  const r = key('r');
  assert.equal(W.reviewKeyHandler(r), true);
  assert.equal(r.defaultPrevented, true);
  await settle();
  assert.deepEqual(requests.find((x) => x.method === 'PUT').body, { verdict: 'right', note: null, anchor: null, base: null });
  const w = key('w');
  assert.equal(W.reviewKeyHandler(w), true);
  assert.equal(reviewOf(tlStore.get()).drafts['turn-1'].verdict, 'wrong');
});

test('the keys are left alone while typing, with a modifier or on repeat, on other tabs and for other keys', () => {
  fresh({ tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' });
  for (const target of [{ tagName: 'TEXTAREA' }, { tagName: 'INPUT', type: 'text' }, { tagName: 'INPUT' }, { tagName: 'SELECT' }, { tagName: 'DIV', isContentEditable: true }]) {
    const e = key('r', { target });
    assert.equal(W.reviewKeyHandler(e), false, JSON.stringify(target));
    assert.equal(e.defaultPrevented, false);
  }
  for (const over of [{ ctrlKey: true }, { metaKey: true }, { altKey: true }, { repeat: true }, { defaultPrevented: true }]) {
    assert.equal(W.reviewKeyHandler(key('r', over)), false, JSON.stringify(over));
  }
  assert.equal(W.reviewKeyHandler(key('R')), false, 'a capital letter is another key');
  assert.equal(W.reviewKeyHandler(key('x')), false);
  assert.equal(W.reviewKeyHandler(null), false);
  fresh({ tab: 'runs', convKey: 's:abc', turnId: 'turn-1' });
  assert.equal(W.reviewKeyHandler(key('r')), false, 'not on Runs');
  fresh({ tab: 'conversations', convKey: 's:abc', turnId: null });
  assert.equal(W.reviewKeyHandler(key('w')), false, 'no open answer, nothing to judge');
  assert.deepEqual(requests, []);
});

test('a radio button or a checkbox does not stop the keys: the reviewer has just clicked Right or Wrong', () => {
  fresh({ tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' });
  assert.equal(W.reviewKeyHandler(key('w', { target: { tagName: 'INPUT', type: 'radio' } })), true);
  assert.equal(W.reviewKeyHandler(key('w', { target: { tagName: 'INPUT', type: 'checkbox' } })), true);
});

// ── what the page reads, and when ───────────────────────────────────────────────────────────────────────────────
test('opening the Map reads the plan, the batches and the map; Progress reads the plan and the figures; nothing else reads them', async () => {
  handlers[P + '/sampling/plan'] = [200, { ok: true, steps: [] }];
  handlers[P + '/sampling/batches'] = [200, { ok: true, batches: [] }];
  handlers[P + '/map'] = [200, { ok: true, nodes: [], too_few: true }];
  handlers[P + '/progress'] = [200, { ok: true }];
  W.loadSamplingFor(initialState());
  await settle();
  assert.deepEqual(requests.map((r) => r.url.pathname.slice(P.length)), []);
  W.loadSamplingFor(Object.assign(initialState(), { tab: 'map' }));
  await settle();
  assert.deepEqual(requests.map((r) => r.url.pathname.slice(P.length)).sort(), ['/map', '/sampling/batches', '/sampling/plan']);
  requests = [];
  W.loadSamplingFor(Object.assign(initialState(), { tab: 'progress' }));
  await settle();
  assert.deepEqual(requests.map((r) => r.url.pathname.slice(P.length)).sort(), ['/progress', '/sampling/plan']);
});

test('annotations are read once the conversation is on screen and settled, for the answer shown, and not before, while it is read again, or elsewhere', async () => {
  handlers[P + '/annotations'] = [200, { ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: { 'turn-1': BLOCKS } }];
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: null }));
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: 'turn-1', conv: { data: null, loading: true, error: '', reason: '' } }));
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: 'turn-1', conv: { data: { key: 's:other' }, loading: false, error: '', reason: '' } }));
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: 'turn-1', conv: { data: CONV, loading: true, error: '', reason: '' } }));
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'runs', convKey: 's:abc', turnId: 'turn-1', conv: { data: CONV, loading: false, error: '', reason: '' } }));
  W.loadAnnotationsFor(Object.assign(initialState(), { tab: 'conversations', convKey: null, turnId: null }));
  await settle();
  assert.deepEqual(requests, []);
  fresh({ tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' });
  W.loadAnnotationsFor(open());
  await settle();
  assert.equal(gets('/annotations').length, 1);
  const q = gets('/annotations')[0].url.searchParams;
  assert.deepEqual([q.get('conversation_key'), q.get('turn_id')], ['s:abc', 'turn-1']);
});

test('attach() listens for keys on the document and its cleanup removes the listener and stops the 30-second read', async () => {
  handlers[P + '/annotations'] = [200, { ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: {} }];
  const detach = W.attach();
  assert.equal(typeof detach, 'function');
  assert.deepEqual(listeners.keydown, [W.reviewKeyHandler]);
  mock.timers.enable({ apis: ['setInterval'] });
  fresh({ tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' });
  await A.loadAnnotations();
  assert.equal(gets('/annotations').length, 1);
  mock.timers.tick(30000);
  await settle();
  assert.equal(gets('/annotations').length, 2, 'the poll is running');
  detach();
  assert.deepEqual(listeners.keydown, []);
  mock.timers.tick(90000);
  await settle();
  assert.equal(gets('/annotations').length, 2, 'the poll stopped with the page');
});

test('the page wires its reads and its keys in effects, and stops them when it closes', () => {
  const src = here('../TraceLabApp.jsx');
  assert.match(src, /from '\.\/wiring\.js'/);
  assert.match(src, /rw\.attach\(\)/);
  assert.match(src, /rw\.loadSamplingFor\(/);
  assert.match(src, /rw\.loadAnnotationsFor\(/);
  assert.match(src, /\[s\.tab, s\.seq, s\.convKey, s\.turnId, s\.conv\.data, s\.conv\.loading\]/, 'the read follows everything it depends on');
  assert.match(src, /import MapTab from '\.\/views\/Map\.jsx'/);
  assert.match(src, /import Progress from '\.\/views\/Progress\.jsx'/);
  assert.match(src, /s\.tab === 'map' \? <MapTab s=\{s\} \/>/);
  assert.match(src, /s\.tab === 'progress' \? <Progress s=\{s\} \/>/);
});

// ── the Conversations tab ───────────────────────────────────────────────────────────────────────────────────────
test('an open answer carries the verdict bar, the comments and the notes column around its timeline', () => {
  const html = page(open());
  assert.match(html, /<div class="tl-annot" data-tl-timeline="">/);
  assert.ok(html.includes('role="radiogroup"'));
  assert.ok(html.includes('aria-label="Your verdict on this answer"'));
  assert.ok(html.includes('aria-label="Comments on this conversation"'));
  assert.ok(html.includes('aria-label="Notes on this answer"'));
  assert.ok(html.includes('How this answer was produced'), 'the timeline itself is unchanged and inside the wrapper');
  const none = page(Object.assign(initialState(), { tab: 'conversations' }));
  assert.equal(none.includes('role="radiogroup"'), false, 'no conversation, no verdict');
  assert.equal(none.includes('data-tl-timeline'), false);
});

test('with the service\'s block text the answer is selectable and a wrong verdict highlights its quote; without it the card is as before', () => {
  const plain = page(open());
  assert.equal(plain.includes('data-tl-block'), false);
  assert.ok(plain.includes('There were 758 work orders in August.'));
  const review = { ann: { 'turn-1': { mine: WRONG, others: [], others_count: 0, others_hidden: false } }, blocks: { 'turn-1': BLOCKS } };
  const closedSteps = page(open({}, review));
  assert.ok(closedSteps.includes('data-tl-block=":answer"'), 'the question and the answer are always read in full');
  assert.equal(closedSteps.includes('data-tl-block="t1:output"'), false, 'a closed step shows no body');
  const html = page(open({ open: STEPS_OPEN }, review));
  assert.ok(html.includes('data-tl-block=":answer"'));
  assert.ok(html.includes('data-tl-block=":question"'));
  assert.ok(html.includes('data-tl-block="t1:output"'));
  assert.ok(html.includes('data-tl-block="s1:output"'), 'a step\'s output is annotatable once its card is open');
  assert.match(html, /<mark data-tl-hl="w1" class="tl-hl" aria-describedby="tl-note-w1">758<\/mark>/);
  assert.ok(html.includes('✗ You: wrong'), 'the turn chip shows the verdict');
  assert.ok(html.includes('Counted July.'));
});

test('a step\'s highlight is on screen only while its card is open (the margin lists the notes of a closed card: reviewScreens.test.mjs)', () => {
  const stepNote = Object.assign({}, WRONG, { id: 'w2', lineage_id: 'w2', note: 'Bad rows', anchor: { span_id: 's1', field: 'output', quote: '758', occurrence: 1, span_name: 'fetch', span_kind: 'step' } });
  const review = { ann: { 'turn-1': { mine: stepNote, others: [], others_count: 0, others_hidden: false } }, blocks: { 'turn-1': BLOCKS } };
  const shown = page(open({}, review));
  assert.match(shown, /data-tl-hl="w2"/, 'the step\'s card is open by default, so its highlight is on screen');
  const closed = page(open({ open: { 'turn-1:s1:step:step': false } }, review));
  assert.equal(closed.includes('data-tl-hl="w2"'), false, 'the step\'s text is not on screen while its card is closed');
});

test('Timeline marks up an annotatable block only when it is given the state, so the model-call panel is unchanged', () => {
  const props = (s) => ({ cards: toCards(REC), isOpen: (id, d) => !!d, setOpen: () => {}, s });
  const review = { ann: { 'turn-1': { mine: WRONG, others: [], others_count: 0, others_hidden: false } }, blocks: { 'turn-1': BLOCKS } };
  assert.ok(renderToStaticMarkup(React.createElement(Timeline, props(open({}, review)))).includes('data-tl-block=":answer"'));
  assert.equal(renderToStaticMarkup(React.createElement(Timeline, props(undefined))).includes('data-tl-block'), false);
});

test('a long tool result is annotatable once shown in full, and a withheld model response is never replaced by its block text', () => {
  const long = 'row\n'.repeat(100);
  const rec = { id: 'turn-1', messages: [
    { id: 'turn-1:t1:result', role: 'tool_result', span_id: 't1', name: 'answer_from_records', content: long, collapse: true, ok: true, anchor: { turn_id: 'turn-1', span_id: 't1', field: 'output' } },
    { id: 'turn-1:m1:call', role: 'model_call', span_id: 'm1', label: 'answer synthesis', anchor: { turn_id: 'turn-1', span_id: 'm1', field: 'output' },
      model_call: { model: 'claude-sonnet-5', withheld: ['response'], response: { content: 'SECRET TEXT' }, usage: {}, status: 'ok' } }] };
  const s = open({}, { blocks: { 'turn-1': { 't1:output': long, 'm1:output': 'SECRET TEXT' } } });
  const draw = (more) => {
    const opened = Object.assign({ 'turn-1:t1:result:step': true, 'turn-1:m1:call:step': true }, more || {});
    return renderToStaticMarkup(React.createElement(Timeline, { cards: toCards(rec), isOpen: (id, d) => (id in opened ? opened[id] : !!d), setOpen: () => {}, s }));
  };
  assert.equal(draw().includes('data-tl-block="t1:output"'), false, 'collapsed, the card keeps its own short text');
  assert.ok(draw({ 'turn-1:t1:result:all': true }).includes('data-tl-block="t1:output"'), 'shown in full, it is the annotatable block');
  const withheld = draw({ 'turn-1:m1:call:response': true });
  assert.ok(withheld.includes('Hidden so it does not shape your review.'));
  assert.equal(withheld.includes('SECRET TEXT'), false);
  assert.equal(withheld.includes('data-tl-block="m1:output"'), false);
  rec.messages[1].model_call.withheld = [];
  assert.ok(draw({ 'turn-1:m1:call:response': true }).includes('data-tl-block="m1:output"'), 'a response that is not withheld is annotatable');
});
