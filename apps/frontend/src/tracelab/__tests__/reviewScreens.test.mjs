// reviewScreens — the Phase 4 screens: the verdict bar and comments, the annotated text and margin notes, the Map
// and Progress tabs. Each is checked three ways: its source carries its accessible structure and is scanned by the
// internal-names guard; rendered to markup it says what the view model says (a radio group for the verdict, labelled
// SVG charts, a dash where nothing is known); and with the screen sharing the test's store (screen-helper.mjs) every
// control calls the action it is for, which a mocked service then shows (the right request, the right store change).
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { transformSync } from 'esbuild';
import { button, byLabel, cleanScreens, find, findAll, screen, text, view } from './screen-helper.mjs';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} } };
globalThis.document = { visibilityState: 'visible' };
globalThis.NodeFilter = { SHOW_TEXT: 4 };
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

after(cleanScreens);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { tlStore, initialState } = await import('../store.js');
const A = await import('../annotateActions.js');
const { reviewInitial, reviewOf, blankDraft, NO_FAILURE_NOTE } = await import('../logic/annotate.js');
const { samplingInitial, samplingOf } = await import('../logic/sampling.js');
const VB = await screen('views/VerdictBar.jsx');
const MN = await screen('views/MarginNotes.jsx');
const MP = await screen('views/Map.jsx');
const PR = await screen('views/Progress.jsx');
const P = '/backend/trace-lab/api/trace-lab';
const here = (rel) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf8');
const tick = () => new Promise((r) => setTimeout(r, 0));
const settle = async () => { for (let i = 0; i < 8; i += 1) await tick(); };
const markup = (C, props) => renderToStaticMarkup(React.createElement(C, props));
const R = () => reviewOf(tlStore.get());
const SM = () => samplingOf(tlStore.get());
const sent = (method, path) => requests.filter((r) => r.method === method && r.url.pathname === P + path);

// The shared store with a review slice (and a sampling slice), and the state to hand to a screen.
function put(review, sampling, over) {
  const seq = tlStore.get().seq + 1;
  tlStore.set(Object.assign(initialState(), { seq, tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' }, over || {},
    { review: Object.assign(reviewInitial(seq), { key: 's:abc' }, review || {}), sampling: Object.assign(samplingInitial(seq), sampling || {}) }));
  return tlStore.get();
}
beforeEach(() => { handlers = {}; requests = []; A.stopAnnotationPoll(); put(); });
afterEach(() => { A.stopAnnotationPoll(); });

const ANSWER = 'There were 758 work orders in August, 758 in total.';
const MINE_RIGHT = { id: 'v1', lineage_id: 'v1', version: 1, reviewer_id: 'me', verdict: 'right', note: NO_FAILURE_NOTE, anchor: null, tombstone: false };
const MINE_WRONG = { id: 'w1', lineage_id: 'w1', version: 1, reviewer_id: 'me', verdict: 'wrong', note: 'Counted July.', tombstone: false,
  anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2, span_name: 'turn', span_kind: 'turn' } };
const OTHER = { id: 'o1', lineage_id: 'o1', version: 1, reviewer_id: 'her', reviewer_email: 'ana@example.com', verdict: 'wrong', note: 'Wrong month', tombstone: false,
  anchor: { span_id: null, field: 'answer', quote: 'August', occurrence: 1, span_name: 'turn', span_kind: 'turn' } };
const QUOTE = { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' };
const turn = (extra) => ({ 'turn-1': Object.assign({ mine: null, others: [], others_count: 0, others_hidden: false }, extra || {}) });
const NOTHING = { ok: true, turns: {}, comments: [], comments_hidden: 0, blocks: {} };

// ── structure ────────────────────────────────────────────────────────────────────────────────────────────────────
const SCREENS = {
  'views/VerdictBar.jsx': ['role="radiogroup"', 'Keep mine', 'Use theirs', 'Save & next', 'Retry', 'aria-label="Note on the first failure"'],
  'views/MarginNotes.jsx': ['data-tl-block', 'data-tl-hl', 'aria-describedby', 'more notes here', 'Notes whose text is not on screen',
    'What went wrong first? (Enter to save)', 'First failure only, in your own words.'],
  'views/Map.jsx': ['role="img"', 'Draw a review batch', 'Download the manifest', 'Show the manifest'],
  'views/Progress.jsx': ['role="img"', 'Wrong rate', 'Coverage', 'Discovery', 'Agreement between reviewers']
};

test('the Phase 4 screens parse and carry their accessible structure', () => {
  for (const [rel, marks] of Object.entries(SCREENS)) {
    const src = here('../' + rel);
    assert.doesNotThrow(() => transformSync(src, { loader: 'jsx', jsx: 'preserve' }), rel);
    marks.forEach((m) => assert.ok(src.includes(m), rel + ' lacks ' + m));
  }
});

test('the Phase 4 screens, logic and requests are scanned by the internal-names guard, none on its skip list', () => {
  const guard = here('../../../test/noInternalNames.test.mjs');
  const files = Object.keys(SCREENS).map((r) => 'src/tracelab/' + r).concat(['src/tracelab/wiring.js', 'src/tracelab/reviewApi.js', 'src/tracelab/logic/annotate.js', 'src/tracelab/logic/sampling.js']);
  files.forEach((rel) => assert.ok(!guard.includes("'" + rel + "'"), rel + ' is on the guard\'s skip list'));
});

// ── the verdict bar ──────────────────────────────────────────────────────────────────────────────────────────────
test('with no answer open there is no verdict bar', () => {
  assert.equal(markup(VB.default, { s: put({}, {}, { turnId: null }) }), '');
});

test('the bar offers Right and Wrong as a radio group, shows what I saved, and offers to clear it', () => {
  const html = markup(VB.default, { s: put({ ann: turn({ mine: MINE_RIGHT }) }) });
  assert.match(html, /<section aria-label="Your verdict on this answer"/);
  assert.match(html, /role="radiogroup" aria-label="Verdict"/);
  assert.match(html, /<input type="radio" name="tl-verdict-turn-1" checked=""[^>]*\/>✓ Right/);
  assert.match(html, /<input type="radio" name="tl-verdict-turn-1"(?! checked)[^>]*\/>✗ Wrong/);
  assert.ok(html.includes('Saved: ✓ Right'));
  assert.ok(html.includes('Clear my verdict'));
  assert.equal(html.includes('Note on the first failure'), false, 'Right needs no note');
  const none = markup(VB.default, { s: put({ ann: turn() }) });
  assert.equal(none.includes('Clear my verdict'), false, 'nothing saved, nothing to clear');
  assert.equal(none.includes('Saved:'), false);
});

test('blind review is said plainly, a batch is named, and other reviewers appear once the service shows them', () => {
  const blind = markup(VB.default, { s: put({ ann: turn({ others_hidden: true, others_count: 2 }) }) });
  assert.ok(blind.includes('2 other reviewers have judged this answer. Their notes stay hidden until you save yours.'));
  const batch = markup(VB.default, { s: put({ ann: turn({ sample_item: { reason: 'uniform pick', batch_id: 'b1' }, reviews_required: 2 }) }) });
  assert.ok(batch.includes('In a review batch: uniform pick'));
  const shown = markup(VB.default, { s: put({ ann: turn({ mine: MINE_RIGHT, others: [OTHER], others_count: 1 }) }) });
  assert.ok(shown.includes('Other reviewers'));
  assert.ok(shown.includes('ana@example.com: ✗ Wrong — Wrong month (“August”)'));
});

test('Wrong shows the quote, the note box with its guidance, Save, Save & next and Cancel', () => {
  const html = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'Counted July.' }) } }) });
  assert.ok(html.includes('First failure at: “758”'));
  assert.match(html, /<textarea aria-label="Note on the first failure" placeholder="What went wrong first\? First failure only, in your own words\."[^>]*maxLength="2000"[^>]*>Counted July\.<\/textarea>/);
  for (const label of ['Save', 'Save &amp; next', 'Cancel']) assert.ok(html.includes('>' + label + '</button>'), label);
  const noQuote = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong' }) } }) });
  assert.ok(noQuote.includes('Select the text where it first went wrong, inside one step of the answer above.'));
  const saving = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'x', saving: true }) } }) });
  assert.ok(saving.includes('Saving…'));
  assert.equal((saving.match(/disabled=""/g) || []).length >= 5, true, 'radios and buttons wait for the save');
});

test('a failed save shows why with Retry; a stale save shows the current version beside mine with Keep mine and Use theirs', () => {
  const failed = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'x', error: 'Not saved: offline' }) } }) });
  assert.match(failed, /role="alert"[^>]*>Not saved: offline<button type="button"[^>]*>Retry<\/button>/);
  const stale = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'Counted July.',
    conflict: { current: MINE_RIGHT, action: 'save' } }) } }) });
  assert.ok(stale.includes('Changed since you opened it.'));
  assert.ok(stale.includes('Now saved: ✓ Right'));
  assert.ok(stale.includes('Yours: ✗ Wrong — Counted July.'));
  assert.ok(stale.includes('>Keep mine</button>') && stale.includes('>Use theirs</button>'));
  const cleared = markup(VB.default, { s: put({ drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'right', conflict: { current: null, action: 'save' } }) } }) });
  assert.ok(cleared.includes('Your verdict was cleared in another tab.'));
});

test('Right and Wrong radios, the note box and the buttons each call their action', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_RIGHT, { id: 'v2' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  let s = put({ ann: turn({ mine: MINE_RIGHT }) });
  let tree = view(VB.default, { s });
  const radios = findAll(tree, (n) => n.type === 'input' && n.props.type === 'radio');
  assert.equal(radios.length, 2);
  radios[1].props.onChange();                                          // Wrong: opens the note, saves nothing
  assert.equal(R().drafts['turn-1'].verdict, 'wrong');
  assert.equal(sent('PUT', '/turns/turn-1/verdict').length, 0);
  assert.equal(R().drafts['turn-1'].note, '', 'a fresh Wrong starts with no note');
  s = tlStore.get();
  tree = view(VB.default, { s });
  byLabel(tree, 'Note on the first failure').props.onChange({ target: { value: 'Counted July.' } });
  assert.equal(R().drafts['turn-1'].note, 'Counted July.');
  tlStore.set((x) => ({ review: Object.assign({}, reviewOf(x), { drafts: { 'turn-1': Object.assign({}, reviewOf(x).drafts['turn-1'], { anchor: QUOTE }) } }) }));
  tree = view(VB.default, { s: tlStore.get() });
  await button(tree, 'Save').props.onClick();
  const put1 = sent('PUT', '/turns/turn-1/verdict')[0];
  assert.deepEqual(put1.body, { verdict: 'wrong', note: 'Counted July.', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2 }, base: 'v1' });
  await settle();
  radios[0].props.onChange();                                          // Right saves at once
  await settle();
  assert.deepEqual(sent('PUT', '/turns/turn-1/verdict')[1].body.verdict, 'right');
});

test('a saved Wrong verdict can have its note edited in the bar: Edit my note, typing, then Save, sends the saved quote with the new note', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w2', note: 'Counted June.' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  let tree = view(VB.default, { s: put({ ann: turn({ mine: MINE_WRONG }) }) });
  assert.equal(markup(VB.default, { s: tlStore.get() }).includes('Note on the first failure'), false, 'saved, there is no form that only looks filled in');
  await button(tree, 'Edit my note').props.onClick();
  tree = view(VB.default, { s: tlStore.get() });
  assert.ok(markup(VB.default, { s: tlStore.get() }).includes('First failure at: “758”'), 'the saved quote is shown as pinned');
  assert.equal(byLabel(tree, 'Note on the first failure').props.value, 'Counted July.', 'the saved note is in the box');
  byLabel(tree, 'Note on the first failure').props.onChange({ target: { value: 'Counted June.' } });   // no Wrong click, no w key
  tree = view(VB.default, { s: tlStore.get() });
  assert.equal(await button(tree, 'Save').props.onClick(), true, 'the save went through');
  assert.equal(sent('PUT', '/turns/turn-1/verdict').length, 1);
  assert.deepEqual(sent('PUT', '/turns/turn-1/verdict')[0].body,
    { verdict: 'wrong', note: 'Counted June.', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2 }, base: 'w1' });
  assert.equal(R().ann['turn-1'].mine.id, 'w2');
  assert.deepEqual(R().drafts, {}, 'nothing left unsaved');
});

test('a saved Wrong verdict with its note edited also goes through Save & next', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w2', note: 'Counted June.' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  handlers[P + '/queue'] = [200, { ok: true, items: [], remaining: 0 }];
  let tree = view(VB.default, { s: put({ ann: turn({ mine: MINE_WRONG }) }) });
  await button(tree, 'Edit my note').props.onClick();
  tree = view(VB.default, { s: tlStore.get() });
  byLabel(tree, 'Note on the first failure').props.onChange({ target: { value: 'Counted June.' } });
  tree = view(VB.default, { s: tlStore.get() });
  assert.equal(await button(tree, 'Save & next').props.onClick(), false, 'nothing is waiting, but the verdict was saved');
  assert.equal(sent('PUT', '/turns/turn-1/verdict')[0].body.anchor.quote, '758');
  assert.equal(sent('PUT', '/turns/turn-1/verdict')[0].body.note, 'Counted June.');
});

test('Cancel drops the draft; Clear my verdict clears; Retry sends the draft again', async () => {
  handlers['DELETE ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_RIGHT, { id: 'v2', tombstone: true }) }];
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w2' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  let s = put({ ann: turn({ mine: MINE_RIGHT }), drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'x' }) } });
  button(view(VB.default, { s }), 'Cancel').props.onClick();
  assert.deepEqual(R().drafts, {});
  s = put({ ann: turn({ mine: MINE_RIGHT }) });
  await button(view(VB.default, { s }), 'Clear my verdict').props.onClick();
  assert.equal(sent('DELETE', '/turns/turn-1/verdict')[0].url.searchParams.get('base'), 'v1');
  await settle();
  s = put({ ann: turn(), drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'Counted July.', error: 'Not saved: offline' }) } });
  await button(view(VB.default, { s }), 'Retry').props.onClick();
  assert.equal(sent('PUT', '/turns/turn-1/verdict').length, 1);
  assert.equal(sent('PUT', '/turns/turn-1/verdict')[0].body.note, 'Counted July.');
});

test('Save & next saves, then opens the next answer waiting in the same batch', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w2' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  handlers[P + '/queue'] = [200, { ok: true, items: [{ turn_id: 'turn-1', conversation_key: 's:abc', batch_id: 'b1' }, { turn_id: 'turn-2', conversation_key: 's:xyz', batch_id: 'b1' }], remaining: 2 }];
  handlers[P + '/conversations/s%3Axyz'] = [200, { ok: true, conversation: { key: 's:xyz', turns: [], active: { id: 'turn-2' } } }];
  const s = put({ ann: turn({ sample_item: { reason: 'uniform pick', batch_id: 'b1' } }), drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'Counted July.' }) } });
  assert.equal(await button(view(VB.default, { s }), 'Save & next').props.onClick(), true, 'a next answer was waiting');
  await settle();
  assert.equal(sent('GET', '/queue')[0].url.searchParams.get('batch_id'), 'b1');
  assert.equal(tlStore.get().convKey, 's:xyz');
  assert.equal(tlStore.get().turnId, 'turn-2');
});

test('a stale save: Keep mine saves on top of theirs, Use theirs takes theirs and drops mine', async () => {
  const THEIRS = Object.assign({}, MINE_RIGHT, { id: 'v9', version: 2 });
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w3' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  const draft = () => ({ 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, note: 'Counted July.', conflict: { current: THEIRS, action: 'save' } }) });
  await button(view(VB.default, { s: put({ ann: turn({ mine: MINE_RIGHT }), drafts: draft() }) }), 'Keep mine').props.onClick();
  assert.equal(sent('PUT', '/turns/turn-1/verdict')[0].body.base, 'v9');
  await settle();
  const s = put({ ann: turn({ mine: MINE_RIGHT }), drafts: draft() });
  await button(view(VB.default, { s }), 'Use theirs').props.onClick();
  assert.deepEqual(R().drafts, {});
  assert.equal(R().ann['turn-1'].mine.id, 'v9');
  assert.equal(sent('PUT', '/turns/turn-1/verdict').length, 1, 'Use theirs sends no write');
});

// ── comments ─────────────────────────────────────────────────────────────────────────────────────────────────────
const CMT = (over) => Object.assign({ id: 'c1', lineage_id: 'c1', version: 1, reviewer_id: 'me', reviewer_email: 'me@example.com', note: 'Worth a second look', tombstone: false }, over || {});

test('comments list who said what, count the hidden ones, and say so when there are none', () => {
  const none = markup(VB.ConversationComments, { s: put() });
  assert.match(none, /<section aria-label="Comments on this conversation"/);
  assert.ok(none.includes('No comments yet.'));
  const html = markup(VB.ConversationComments, { s: put({ comments: [CMT(), CMT({ id: 'c2', lineage_id: 'c2', reviewer_id: 'her', reviewer_email: 'ana@example.com', note: 'Agree' })], commentsHidden: 1,
    commentDraft: 'half a thought', commentError: 'Not saved: offline' }, {}, {}) });
  assert.ok(html.includes('me@example.com: ') && html.includes('Worth a second look'));
  assert.ok(html.includes('ana@example.com: ') && html.includes('Agree'));
  assert.ok(html.includes('1 comment from another reviewer stays hidden until you have judged every answer here.'));
  assert.match(html, /<input[^>]*aria-label="Add a comment"[^>]*value="half a thought"/);
  assert.match(html, /role="alert"[^>]*>Not saved: offline</);
  assert.equal((html.match(/aria-label="Remove my comment"/g) || []).length, 0, 'whose comment is mine is not known here, so none can be removed');
});

test('Remove appears on my own comment only, once my identity is known', () => {
  const s = put({ me: 'me', comments: [CMT(), CMT({ id: 'c2', lineage_id: 'c2', reviewer_id: 'her', reviewer_email: 'ana@example.com', note: 'Agree' })] });
  assert.equal((markup(VB.ConversationComments, { s }).match(/aria-label="Remove my comment"/g) || []).length, 1);
});

test('typing, Comment and Remove call their actions', async () => {
  handlers['POST ' + P + '/comments'] = [200, { ok: true, annotation: CMT({ id: 'c9', lineage_id: 'c9', note: 'Hello' }) }];
  handlers['DELETE ' + P + '/comments/c1'] = [200, { ok: true }];
  handlers[P + '/annotations'] = [200, NOTHING];
  let s = put({ me: 'me', comments: [CMT()] });
  let tree = view(VB.ConversationComments, { s });
  byLabel(tree, 'Add a comment').props.onChange({ target: { value: 'Hello' } });
  assert.equal(R().commentDraft, 'Hello');
  tree = view(VB.ConversationComments, { s: tlStore.get() });
  const form = find(tree, (n) => n.type === 'form');
  let prevented = false;
  await form.props.onSubmit({ preventDefault: () => { prevented = true; } });
  assert.equal(prevented, true);
  assert.deepEqual(sent('POST', '/comments')[0].body, { conversation_key: 's:abc', turn_id: 'turn-1', note: 'Hello' });
  s = put({ me: 'me', comments: [CMT()] });
  await byLabel(view(VB.ConversationComments, { s }), 'Remove my comment').props.onClick();
  assert.equal(sent('DELETE', '/comments/c1')[0].url.searchParams.get('base'), 'c1');
});

// ── the annotated text ───────────────────────────────────────────────────────────────────────────────────────────
const BLOCKS = { ':answer': ANSWER };
const ANCHOR = { span_id: 'root', field: 'answer' };
const annotated = (review, props) => view(MN.AnnotatedText, Object.assign({ s: put(Object.assign({ blocks: { 'turn-1': BLOCKS } }, review || {})), anchor: ANCHOR }, props || {}));

test('without the service\'s text for a block the card keeps its own body', () => {
  const body = React.createElement('p', null, 'the cards own text');
  assert.equal(markup(MN.AnnotatedText, { s: put(), anchor: ANCHOR, children: body }), '<p>the cards own text</p>');
  assert.equal(markup(MN.AnnotatedText, { s: undefined, anchor: ANCHOR, children: body }), '<p>the cards own text</p>', 'no state: the model-call panel');
  assert.equal(markup(MN.AnnotatedText, { s: put({ blocks: { 'turn-1': BLOCKS } }), anchor: null, children: body }), '<p>the cards own text</p>', 'no anchor');
  assert.equal(markup(MN.AnnotatedText, { s: put({ blocks: { 'turn-1': BLOCKS } }), anchor: { span_id: 'x', field: 'output' }, children: body }), '<p>the cards own text</p>', 'no text for this step');
  assert.equal(markup(MN.AnnotatedText, { s: put({ blocks: { 'turn-1': { 'x:output': '' } } }), anchor: { span_id: 'x', field: 'output' }, children: body }), '<p>the cards own text</p>', 'an empty block has nothing to annotate');
});

test('the server\'s text is drawn under its block key, with my wrong verdict\'s quote and another reviewer\'s highlighted', () => {
  const html = markup(MN.AnnotatedText, { s: put({ blocks: { 'turn-1': BLOCKS }, ann: turn({ mine: MINE_WRONG, others: [OTHER], others_count: 1 }) }), anchor: ANCHOR, className: 'tl-text' });
  assert.match(html, /^<div><div class="tl-block tl-text" data-tl-block=":answer">/);
  assert.match(html, /<mark data-tl-hl="o1" class="tl-hl" aria-describedby="tl-note-o1">August<\/mark>/);
  assert.match(html, /<mark data-tl-hl="w1" class="tl-hl" aria-describedby="tl-note-w1">758<\/mark><span> in total\.<\/span>/);
  assert.equal(html.includes('role="dialog"'), false);
});

test('a selection being noted shows a dashed draft highlight and the note box with its hints', () => {
  const html = markup(MN.AnnotatedText, { s: put({ blocks: { 'turn-1': BLOCKS }, drafts: { 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, popover: true, note: 'x' }) } }), anchor: ANCHOR });
  assert.match(html, /<mark data-tl-hl="draft" class="tl-hl tl-hl-draft">758<\/mark><span> in total\.<\/span>/);
  assert.ok(html.includes('role="dialog" aria-label="Note on the first failure"'));
  assert.match(html, /<textarea[^>]*aria-label="What went wrong first\?"[^>]*placeholder="What went wrong first\? \(Enter to save\)"/);
  assert.ok(html.includes('First failure only, in your own words. Enter saves, Shift+Enter adds a line, Escape cancels.'));
});

// A tiny DOM: a block element with the text nodes inside it, as the browser's selection sees them.
function fakeBlock(key, parts) {
  const root = { nodeType: 1, parentNode: null, getAttribute: (n) => (n === 'data-tl-block' ? key : null), texts: [] };
  parts.forEach((t) => { root.texts.push({ nodeType: 3, nodeValue: t, parentNode: root }); });
  return root;
}
const sel = (a, ao, b, bo) => ({ isCollapsed: false, rangeCount: 1, getRangeAt: () => ({ startContainer: a, startOffset: ao, endContainer: b, endOffset: bo }) });

test('a browser selection becomes offsets inside its block, counting every text node before it', () => {
  globalThis.document.createTreeWalker = (root) => { const list = root.texts.slice(); return { nextNode: () => list.shift() || null }; };
  const root = fakeBlock(':answer', ['There were ', '758', ' work orders in August, ', '758', ' in total.']);
  assert.deepEqual(MN.readSelection(root, sel(root.texts[3], 0, root.texts[3], 3)), { startKey: ':answer', endKey: ':answer', start: 38, end: 41 });
  assert.deepEqual(MN.readSelection(root, sel(root.texts[0], 6, root.texts[2], 5)), { startKey: ':answer', endKey: ':answer', start: 6, end: 19 });
  const other = fakeBlock('s1:output', ['rows']);
  const across = MN.readSelection(root, sel(root.texts[1], 0, other.texts[0], 2));
  assert.deepEqual([across.startKey, across.endKey, across.start, across.end], [':answer', 's1:output', 11, -1], 'ending in another block cannot be pinned');
  assert.equal(MN.readSelection(root, { isCollapsed: true, rangeCount: 1 }), null);
  assert.equal(MN.readSelection(root, { isCollapsed: false, rangeCount: 0 }), null);
  assert.equal(MN.readSelection(root, null), null);
  delete globalThis.document.createTreeWalker;
});

test('releasing the mouse after selecting inside the block starts a note on that quote; across blocks it says why not', () => {
  globalThis.document.createTreeWalker = (root) => { const list = root.texts.slice(); return { nextNode: () => list.shift() || null }; };
  const root = fakeBlock(':answer', [ANSWER]);
  const tree = annotated();
  const block = find(tree, (n) => n.props && n.props['data-tl-block'] === ':answer');
  globalThis.window.getSelection = () => sel(root.texts[0], 38, root.texts[0], 41);
  block.props.onMouseUp({ currentTarget: root });
  const d = R().drafts['turn-1'];
  assert.deepEqual([d.verdict, d.popover, d.anchor.quote, d.anchor.occurrence, d.anchor.key], ['wrong', true, '758', 2, ':answer']);
  const other = fakeBlock('s1:output', ['rows']);
  globalThis.window.getSelection = () => sel(root.texts[0], 3, other.texts[0], 2);
  block.props.onMouseUp({ currentTarget: root });
  assert.equal(R().selError, 'Select text inside one step.');
  const before = JSON.stringify(R());
  globalThis.window.getSelection = () => ({ isCollapsed: true, rangeCount: 0 });
  block.props.onMouseUp({ currentTarget: root });
  assert.equal(JSON.stringify(R()), before, 'a click with nothing selected changes nothing');
  delete globalThis.window.getSelection;
  delete globalThis.document.createTreeWalker;
});

test('in the note box Enter saves, Shift+Enter does not, Escape cancels, and typing keeps the draft', async () => {
  handlers['PUT ' + P + '/turns/turn-1/verdict'] = [200, { ok: true, annotation: Object.assign({}, MINE_WRONG, { id: 'w2' }) }];
  handlers[P + '/annotations'] = [200, NOTHING];
  const draft = () => ({ 'turn-1': Object.assign(blankDraft(), { verdict: 'wrong', anchor: QUOTE, popover: true, note: 'Counted July.' }) });
  const box = (tree) => byLabel(tree, 'What went wrong first?');
  const ev = (key, shiftKey) => ({ key, shiftKey: !!shiftKey, prevented: false, preventDefault() { this.prevented = true; } });
  let tree = annotated({ drafts: draft() });
  box(tree).props.onChange({ target: { value: 'Counted July!' } });
  assert.equal(R().drafts['turn-1'].note, 'Counted July!');
  const shift = ev('Enter', true);
  box(tree).props.onKeyDown(shift);
  assert.equal(shift.prevented, false);
  assert.equal(sent('PUT', '/turns/turn-1/verdict').length, 0);
  const esc = ev('Escape');
  box(tree).props.onKeyDown(esc);
  assert.equal(esc.prevented, true);
  assert.deepEqual(R().drafts, {});
  tree = annotated({ drafts: draft() });
  const enter = ev('Enter');
  await box(tree).props.onKeyDown(enter);
  assert.equal(enter.prevented, true);
  await settle();
  assert.equal(sent('PUT', '/turns/turn-1/verdict')[0].body.note, 'Counted July.');
});

test('hovering a highlight or a note outlines its partner, and leaving takes the outline away', () => {
  const log = [];
  const el = (name) => ({ classList: { toggle: (c, on) => log.push([name, c, on]) } });
  const root = { querySelectorAll: (q) => (q === '[data-tl-hl~="a"], [data-tl-note="a"]' ? [el('hl'), el('note')] : []) };
  MN.outline(root, ['a'], true);
  MN.outline(root, ['a'], false);
  assert.deepEqual(log, [['hl', 'tl-hover', true], ['note', 'tl-hover', true], ['hl', 'tl-hover', false], ['note', 'tl-hover', false]]);
  assert.doesNotThrow(() => MN.outline(null, ['a'], true));
  const tree = annotated({ ann: turn({ mine: MINE_WRONG }) });
  const mark = find(tree, (n) => n.type === 'mark');
  const seen = [];
  const closest = { querySelectorAll: (q) => { seen.push(q); return []; } };
  mark.props.onMouseEnter({ currentTarget: { closest: (q) => (q === '[data-tl-timeline]' ? closest : null) } });
  mark.props.onMouseLeave({ currentTarget: { closest: () => closest } });
  assert.equal(seen.length, 2);
});

// ── the margin ───────────────────────────────────────────────────────────────────────────────────────────────────
const NOTE = (over) => Object.assign({ id: 'w1', me: true, who: 'You', note: 'Counted July.', quote: '758', clipped: '758', occurrence: 1, blockKey: ':answer', step: 'the answer', height: 62 }, over || {});

test('notes are placed at the top of their highlight, stacked 8 px apart, and the ones with no highlight are reported', () => {
  const notes = [NOTE(), NOTE({ id: 'w2', height: 62 }), NOTE({ id: 'w3' })];
  const l = MN.layoutNotes(notes, { w1: 40, w2: 50 }, []);
  assert.deepEqual(l.placed.map((p) => [p.id, p.top]), [['w1', 40], ['w2', 110]]);
  assert.deepEqual(l.missing, ['w3']);
  assert.equal(l.height, 172);
  const empty = MN.layoutNotes([], {}, []);
  assert.deepEqual([empty.placed, empty.missing, empty.height], [[], [], 0]);
  const crowd = MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' }), NOTE({ id: 'c' })], { a: 0, b: 0, c: 1 }, []);
  assert.deepEqual(crowd.placed.map((p) => p.id), ['a'], 'notes that would drift too far fold into the first');
  assert.deepEqual(crowd.placed[0].more, ['b', 'c']);
  const some = MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' }), NOTE({ id: 'c' })], { a: 0, b: 0, c: 1 }, ['a']);
  assert.deepEqual(some.placed.map((p) => [p.id, p.more]), [['a', []], ['b', ['c']]], 'unfolding a note shows the next one, which may fold the rest');
  assert.deepEqual(MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' }), NOTE({ id: 'c' })], { a: 0, b: 0, c: 1 }, ['a', 'b']).placed.map((p) => p.id), ['a', 'b', 'c']);
});

test('the tops are read from the highlights on screen, relative to the timeline, and a note with no highlight has none', () => {
  const els = { w1: { getBoundingClientRect: () => ({ top: 160.4 }) }, w2: { getBoundingClientRect: () => ({ top: 400 }) } };
  const root = { getBoundingClientRect: () => ({ top: 100 }), querySelector: (q) => { const m = /data-tl-hl~="([^"]+)"/.exec(q); return m ? els[m[1]] || null : null; } };
  assert.deepEqual(MN.measureTops(root, [NOTE(), NOTE({ id: 'w2' }), NOTE({ id: 'w3' })]), { w1: 60, w2: 300 });
  assert.deepEqual(MN.measureTops(null, [NOTE()]), {});
});

test('the notes column is an aside of absolutely placed notes, each linked by id to its highlight, with a stub for the folded ones', () => {
  const notes = [NOTE(), NOTE({ id: 'w2', who: 'ana@example.com', note: 'Wrong month', clipped: 'August', me: false })];
  const layout = MN.layoutNotes(notes, { w1: 40, w2: 50 }, []);
  const html = markup(MN.NoteColumn, { notes, layout, onExpand: () => {} });
  assert.match(html, /<aside aria-label="Notes on this answer" class="tl-margin" style="min-height:172px">/);
  assert.match(html, /<div id="tl-note-w1" data-tl-note="w1" class="tl-mnote" style="top:40px;min-height:62px">/);
  assert.ok(html.includes('✎ Counted July.') && html.includes('“758” — You'));
  assert.ok(html.includes('“August” — ana@example.com'));
  const folded = MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' }), NOTE({ id: 'c' })], { a: 0, b: 0, c: 1 }, []);
  const stub = markup(MN.NoteColumn, { notes: [NOTE({ id: 'a' }), NOTE({ id: 'b' }), NOTE({ id: 'c' })], layout: folded, onExpand: () => {} });
  assert.ok(stub.includes('+2 more notes here'));
  const one = MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' })], { a: 0, b: 0 }, []);
  assert.ok(markup(MN.NoteColumn, { notes: [NOTE({ id: 'a' }), NOTE({ id: 'b' })], layout: one, onExpand: () => {} }).includes('+1 more note here'));
  assert.match(markup(MN.NoteColumn, { notes: [], layout: MN.layoutNotes([], {}, []), onExpand: () => {} }), /^<aside aria-label="Notes on this answer" class="tl-margin"( style="min-height:0px")?><\/aside>$/);
});

test('the stub unfolds the notes it hides, and a note\'s hover outlines its highlight', () => {
  const notes = [NOTE({ id: 'a' }), NOTE({ id: 'b' })];
  const layout = MN.layoutNotes([NOTE({ id: 'a', height: 300 }), NOTE({ id: 'b' })], { a: 0, b: 0 }, []);
  const opened = [];
  const tree = view(MN.NoteColumn, { notes, layout, onExpand: (id) => opened.push(id) });
  button(tree, '+1 more note here').props.onClick();
  assert.deepEqual(opened, ['a']);
  const seen = [];
  const note = find(tree, (n) => n.props && n.props['data-tl-note'] === 'a');
  note.props.onMouseEnter({ currentTarget: { closest: () => ({ querySelectorAll: (q) => { seen.push(q); return []; } }) } });
  assert.deepEqual(seen, ['[data-tl-hl~="a"], [data-tl-note="a"]']);
});

test('the margin component renders an empty aside before anything is measured', () => {
  assert.match(markup(MN.default, { s: put({ ann: turn({ mine: MINE_WRONG }), blocks: { 'turn-1': BLOCKS } }), rootRef: { current: null } }), /^<aside aria-label="Notes on this answer"/);
});

// ── notes whose text is not on screen ────────────────────────────────────────────────────────────────────────────
test('notes whose quote is no longer in the text are listed with the quote and the step, and so are notes in a closed card', () => {
  assert.equal(markup(MN.UnplacedNotes, { s: put({ ann: turn({ mine: MINE_WRONG }), blocks: { 'turn-1': BLOCKS } }) }), '', 'a note whose text is there is not listed');
  const gone = Object.assign({}, MINE_WRONG, { anchor: Object.assign({}, MINE_WRONG.anchor, { quote: 'July' }) });
  const html = markup(MN.UnplacedNotes, { s: put({ ann: turn({ mine: gone }), blocks: { 'turn-1': BLOCKS } }) });
  assert.match(html, /<section aria-label="Notes whose text is not on screen"/);
  assert.ok(html.includes('✎ Counted July. — “July” (step: the answer) — You'));
  const closed = markup(MN.UnplacedNotes, { s: put({ ann: turn({ mine: MINE_WRONG }), blocks: { 'turn-1': BLOCKS } }), also: ['w1'] });
  assert.ok(closed.includes('✎ Counted July. — “758” (step: the answer) — You'));
  const both = markup(MN.UnplacedNotes, { s: put({ ann: turn({ mine: gone }), blocks: { 'turn-1': BLOCKS } }), also: ['w1'] });
  assert.equal((both.match(/✎/g) || []).length, 1, 'a note is listed once');
  assert.equal(markup(MN.UnplacedNotes, { s: put(), also: ['nope'] }), '');
});

// ── the Map ──────────────────────────────────────────────────────────────────────────────────────────────────────
const PLAN = { ok: true, next_step: 'cluster_representatives', distinct_sampled: 24, target_total: 100, pool_size: 40, steps: [
  { step: 'initial_uniform', strategy: 'uniform', target: 15, selected: 15, reviewed: 15, batch_ids: ['b1'] },
  { step: 'cluster_representatives', strategy: 'diversity', target: 15, selected: 9, reviewed: 2, batch_ids: ['b2'] },
  { step: 'dimension', strategy: 'dimension', target: 30, selected: 0, reviewed: 0, batch_ids: [] }] };
const BATCHES = [{ id: 'b1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15, reviewed: 15, missing: 0, seed: 7, created_by_email: 'me@example.com' },
  { id: 'b0', plan_step: 'dimension', strategy: 'dimension', counts_for_prevalence: false, selected: 30, requested: 30, reviewed: 4, missing: 26, seed: 3, withdrawn_at: '2026-10-14T10:00:00Z' }];
const MAPDATA = { ok: true, too_few: false, explained_variance: [0.612, 0.18], feature_names: [{ key: 'tokens', label: 'Tokens' }],
  nodes: [{ turn_id: 't1', conversation_key: 's:a', x: 0, y: 0, cluster: 1, reviewed: true, batch_id: 'b1', question: 'Boilers?' },
    { turn_id: 't2', conversation_key: 's:b', x: 1, y: 1, cluster: 2, reviewed: false, batch_id: null, question: 'Lifts?' },
    { turn_id: 't3', conversation_key: 's:c', x: 2, y: 0, cluster: 2, reviewed: true, batch_id: null, question: 'Pumps?' }],
  clusters: [{ id: 1, size: 1, reviewed: 1, centroid: { tokens: 900 } }, { id: 2, size: 2, reviewed: 1, centroid: { tokens: 1200 } }] };
const mapState = (sampling, over) => put({}, Object.assign({ plan: PLAN, batches: BATCHES, map: MAPDATA, mapLoadedAt: 1 }, sampling || {}), Object.assign({ tab: 'map' }, over || {}));
const field = (tree, name) => {
  const label = find(tree, (n) => n.type === 'label' && n.props.children && text(n.props.children[0]) === name, 'field ' + name);
  return find(label, (n) => n.type === 'select' || n.type === 'input', 'control of ' + name);
};

test('the Map says it has not read anything yet before it has, and offers the period and the draw form', () => {
  const html = markup(MP.default, { s: put({}, {}, { tab: 'map' }) });
  assert.match(html, /role="group" aria-label="Period"/);
  assert.match(html, /aria-pressed="true"[^>]*>30 days</);
  assert.ok(html.includes('Review plan') && html.includes('Reading…'));
  assert.equal(html.includes('No answers in this period.'), false, 'unread is not empty');
  assert.ok(html.includes('Draw a review batch'));
  assert.ok(html.includes('Counts toward rates.'));
  assert.ok(html.includes('No batches yet.') === false, 'batches not read yet');
  assert.ok(html.includes('A cluster is a prompt to read answers, never evidence that they failed.'));
});

test('the plan is a table of steps with the next one marked, and a button for the next batch', () => {
  const html = markup(MP.default, { s: mapState() });
  assert.ok(html.includes('24 of 100 answers sampled'));
  assert.ok(html.includes('One from each group · next') && html.includes('First fair batch'));
  assert.ok(html.includes('Fair (uniform) · counts toward rates') && html.includes('Spread across groups · for discovery only'));
  assert.ok(html.includes('9 / 15'));
  assert.ok(html.includes('✓ done'));
  assert.ok(html.includes('Next batch: One from each group'));
  assert.ok(html.includes('Only 40 unsampled answers in this period; the plan cannot reach 100.'), 'the shortfall is stated');
});

test('the batches table tags each batch, strikes a withdrawn one and offers Withdraw only on the others', () => {
  const html = markup(MP.default, { s: mapState() });
  assert.ok(html.includes('First fair batch · Fair (uniform)') && html.includes('counts toward rates'));
  assert.ok(html.includes('Split by a dimension · Split by a dimension · withdrawn'));
  assert.ok(html.includes('15 of 15') && html.includes('4 of 30 · 26 missing'));
  assert.equal((html.match(/aria-label="Why withdraw this batch"/g) || []).length, 1);
  assert.equal((html.match(/>Withdraw<\/button>/g) || []).length, 1);
  assert.ok(html.includes('No batches yet.') === false);
  assert.ok(markup(MP.default, { s: mapState({ batches: [] }) }).includes('No batches yet.'));
});

test('the map is a labelled picture: a square for each reviewed answer, a circle for the others, a ring when in a batch', () => {
  const html = markup(MP.default, { s: mapState() });
  assert.match(html, /<svg viewBox="0 0 640 400" role="img" aria-label="Answers projected onto their two main axes"/);
  assert.equal((html.match(/<rect[^>]*class="tl-pt-sq/g) || []).length, 2, 'two reviewed');
  assert.equal((html.match(/<circle[^>]*class="tl-pt-dot/g) || []).length, 1, 'one not reviewed');
  assert.equal((html.match(/<circle[^>]*class="tl-pt-ring"/g) || []).length, 1, 'one in a batch');
  assert.ok(html.includes('<title>Boilers? · group 1 · reviewed · in a review batch</title>'));
  assert.ok(html.includes('■ reviewed · ○ not reviewed · ring: already in a review batch. The two axes carry 61% and 18% of the spread between answers.'));
  assert.ok(html.includes('Group 2 · 2 answers · 1 reviewed'));
  assert.match(html, /<th[^>]*>Tokens \(average\)<\/th>/);
  assert.match(html, /<td[^>]*>1200<\/td>/);
});

test('a group brought forward keeps its points and dims the rest; empty and short maps say so', () => {
  const html = markup(MP.default, { s: mapState({ mapFocus: 2 }) });
  assert.equal((html.match(/opacity="0.25"/g) || []).length, 1);
  assert.match(html, /aria-pressed="true"[^>]*>Group 2 · 2 answers/);
  assert.ok(markup(MP.default, { s: mapState({ map: { ok: true, too_few: true, nodes: [], clusters: [] } }) }).includes('Not enough answers in this window to draw a map.'));
  assert.ok(markup(MP.default, { s: mapState({ map: { ok: true, too_few: false, nodes: [], clusters: [] } }) }).includes('No answers in this period.'));
  assert.ok(markup(MP.default, { s: mapState({ map: null, mapLoadedAt: null, mapLoading: true }) }).includes('Reading…'));
  assert.ok(markup(MP.default, { s: mapState({ map: Object.assign({}, MAPDATA, { truncated: true }) }) }).includes('Only the newest 3,000 answers are shown.'));
  const bad = markup(MP.default, { s: mapState({ mapError: 'Unreachable', planError: 'no plan', batchesError: 'no batches', error: 'No answers are left', notice: 'Drew 15 answers (seed 7). They are in the review queue.' }) });
  for (const m of ['Could not draw the map: Unreachable', 'Could not read the plan: no plan', 'Could not read the batches: no batches', 'No answers are left']) assert.ok(bad.includes(m), m);
  assert.match(bad, /role="status"[^>]*>Drew 15 answers/);
});

test('a preview says how many answers from how big a pool, with each reason; a manifest opens, downloads and closes', () => {
  const preview = { pool_size: 80, excluded_sampled: 15, truncated: true, picks: [{ turn_id: 't1', reason: 'uniform pick' }, { turn_id: 't2', reason: 'nearest to the centre of group 2' }], request: {} };
  const html = markup(MP.default, { s: mapState({ preview }) });
  assert.ok(html.includes('2 answers from a pool of 80 (15 already sampled, left out); the pool was capped at the newest answers.'));
  assert.ok(html.includes('uniform pick') && html.includes('nearest to the centre of group 2'));
  assert.ok(markup(MP.default, { s: mapState({ preview: { pool_size: 0, excluded_sampled: 0, picks: [] } }) }).includes('No answers are left to draw in this period.'));
  const manifest = { batches: [{ id: 'b1', plan_step: 'initial_uniform', seed: 7, counts_for_prevalence: true, items: [{ position: 1, turn_id: 't1', reason: 'uniform pick' }] }] };
  const closed = markup(MP.default, { s: mapState({ manifest, manifestOpen: false }) });
  assert.ok(closed.includes('Show the manifest') && !closed.includes('Download the manifest'));
  const opened = markup(MP.default, { s: mapState({ manifest, manifestOpen: true }) });
  assert.ok(opened.includes('Download the manifest') && opened.includes('Hide the manifest'));
  assert.ok(opened.includes('First fair batch · seed 7 · counts toward rates · 1 answers') === false);
  assert.ok(opened.includes('First fair batch · seed 7 · counts toward rates · 1 answer'));
  assert.ok(markup(MP.default, { s: mapState({ manifestError: 'x' }) }).includes('Could not read the manifest: x'));
});

test('the period chips, Refresh and the group chips act on the map; a point opens its conversation', async () => {
  handlers[P + '/map'] = [200, MAPDATA];
  handlers[P + '/sampling/plan'] = [200, PLAN];
  const s = mapState({ mapFocus: null }, { tab: 'map' });
  let tree = view(MP.default, { s });
  await button(tree, '60 days').props.onClick();
  assert.equal(SM().range, '60d');
  await settle();
  assert.equal(sent('GET', '/map').length, 1);
  assert.equal(sent('GET', '/sampling/plan').length, 1);
  tree = view(MP.default, { s: tlStore.get() });
  await button(tree, 'Refresh').props.onClick();
  await settle();
  assert.equal(sent('GET', '/map').length, 2);
  button(tree, 'Group 2 · 2 answers · 1 reviewed').props.onClick();
  assert.equal(SM().mapFocus, 2);
  button(view(MP.default, { s: tlStore.get() }), 'Group 2 · 2 answers · 1 reviewed').props.onClick();
  assert.equal(SM().mapFocus, null, 'choosing the group again lets go');
  const point = find(tree, (n) => n.type === 'g' && n.props.children && text(n.props.children).startsWith('Lifts?'), 'the Lifts point');
  point.props.onClick();
  assert.equal(tlStore.get().tab, 'conversations');
  assert.equal(tlStore.get().convKey, 's:b');
  await settle();
});

test('Next batch fills the form from the plan; each field of the form sets itself; Preview and Create send what the form says', async () => {
  handlers[P + '/sampling/preview'] = [200, { ok: true, pool_size: 40, excluded_sampled: 0, truncated: false, picks: [{ turn_id: 't1', reason: 'uniform pick' }] }];
  handlers['POST ' + P + '/sampling/batches'] = [200, { ok: true, batch: { id: 'b9', selected: 15, seed: 11 } }];
  handlers[P + '/sampling/plan'] = [200, PLAN];
  handlers[P + '/sampling/batches'] = [200, { ok: true, batches: BATCHES }];
  handlers[P + '/map'] = [200, MAPDATA];
  let tree = view(MP.default, { s: mapState() });
  button(tree, 'Next batch: One from each group').props.onClick();
  assert.deepEqual([SM().form.plan_step, SM().form.strategy, SM().form.k], ['cluster_representatives', 'diversity', 15]);
  tree = view(MP.default, { s: tlStore.get() });
  field(tree, 'Kind').props.onChange({ target: { value: 'dimension' } });
  assert.equal(SM().form.strategy, 'dimension');
  tree = view(MP.default, { s: tlStore.get() });
  field(tree, 'Split by').props.onChange({ target: { value: 'page' } });
  field(tree, 'Answers').props.onChange({ target: { value: '20' } });
  field(tree, 'Seed').props.onChange({ target: { value: '11' } });
  field(tree, 'Reviews per answer').props.onChange({ target: { value: '2' } });
  field(tree, 'Step').props.onChange({ target: { value: 'dimension' } });
  assert.deepEqual([SM().form.dimension, SM().form.k, SM().form.seed, SM().form.reviews_required, SM().form.plan_step], ['page', 30, '11', 2, 'dimension'], 'choosing a step refills kind and size, as the plan says');
  tree = view(MP.default, { s: tlStore.get() });
  assert.deepEqual(findAll(field(tree, 'Reviews per answer'), (n) => n.type === 'option').map((o) => o.props.value), [1, 2, 3]);
  await button(tree, 'Preview').props.onClick();
  const pv = sent('GET', '/sampling/preview')[0].url.searchParams;
  assert.deepEqual([pv.get('strategy'), pv.get('plan_step'), pv.get('seed'), pv.get('reviews_required'), pv.get('dimension')], ['dimension', 'dimension', '11', '2', 'page']);
  tree = view(MP.default, { s: tlStore.get() });
  await button(tree, 'Create batch').props.onClick();
  const made = sent('POST', '/sampling/batches')[0].body;
  assert.deepEqual([made.strategy, made.plan_step, made.seed], ['dimension', 'dimension', 11], 'Create draws what the preview showed');
  await settle();
  assert.equal(SM().notice, 'Drew 15 answers (seed 11). They are in the review queue.');
});

test('Show the manifest reads it; Download and Hide act on it; Withdraw needs a reason and sends it', async () => {
  handlers[P + '/sampling/manifest'] = [200, { ok: true, batches: [{ id: 'b1', plan_step: 'initial_uniform', seed: 7, counts_for_prevalence: true, items: [] }] }];
  handlers['POST ' + P + '/sampling/batches/b1/withdraw'] = [200, { ok: true }];
  handlers[P + '/sampling/plan'] = [200, PLAN];
  handlers[P + '/sampling/batches'] = [200, { ok: true, batches: BATCHES }];
  let tree = view(MP.default, { s: mapState() });
  await button(tree, 'Show the manifest').props.onClick();
  assert.equal(SM().manifestOpen, true);
  const made = [];
  const realCreate = URL.createObjectURL;
  URL.createObjectURL = (b) => { made.push(b); return 'blob:x'; };
  globalThis.document.createElement = () => ({ click: () => made.push('clicked'), remove: () => {}, set href(v) {}, set download(v) { made.push(v); } });
  globalThis.document.body = { appendChild: () => {} };
  tree = view(MP.default, { s: tlStore.get() });
  button(tree, 'Download the manifest').props.onClick();
  URL.createObjectURL = realCreate;
  delete globalThis.document.createElement;
  delete globalThis.document.body;
  assert.ok(made.includes('review-batches-manifest.json') && made.includes('clicked'));
  button(tree, 'Hide the manifest').props.onClick();
  assert.equal(SM().manifestOpen, false);
  tree = view(MP.default, { s: tlStore.get() });
  await button(tree, 'Withdraw').props.onClick();                      // no reason typed yet
  assert.equal(SM().error, 'Say why the batch is withdrawn.');
  assert.equal(sent('POST', '/sampling/batches/b1/withdraw').length, 0);
  tree = view(MP.default, { s: tlStore.get() }, [{ b1: 'Seed was wrong' }]);
  await button(tree, 'Withdraw').props.onClick();
  assert.deepEqual(sent('POST', '/sampling/batches/b1/withdraw')[0].body, { reason: 'Seed was wrong' });
});

// ── Progress ─────────────────────────────────────────────────────────────────────────────────────────────────────
const PROGRESS = { ok: true, open_batches: 2, batches: [{ id: 'b1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15, reviewed: 12, mine: 5, missing: 3, wrong: 3, right: 8, disputed: 1 }], block: 10,
  open_coding: { reviewed: 12, wrong: 3, right: 9, missing: 37, missing_turns: ['t1', 't2'], missing_more: 12, reviewers: [{ email: 'a@x', reviewed: 12 }, { reviewed: 4 }] },
  fair_sample: { label: 'From fair (uniform) batches only', rate: 0.25, ci_low: 0.0889, ci_high: 0.5323, wrong: 3, right: 9, disputed: 1 },
  agreement: { shared: 8, percent: 0.875, kappa: null, note: 'fewer than 20 shared answers' },
  discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3 }, { block: 2, reviews_through: 12, wrong: 0 }] },
  saturation: { available: false, hint: false },
  coverage: { dimensions: { channel: [{ value: 'chat', pool: 80, reviewed: 20, pool_share: 0.8, reviewed_share: 0.4, under_covered: false }],
    route_target: [{ value: '(none)', pool: 10, reviewed: 0, pool_share: 0.1, reviewed_share: 0, under_covered: true }] }, clusters_unreviewed: [{ id: 3, size: 7 }] } };
const progState = (sampling) => put({}, Object.assign({ progress: PROGRESS, progressLoadedAt: 1 }, sampling || {}), { tab: 'progress' });

test('before anything is read every tile is a dash, never a zero', () => {
  const html = markup(PR.default, { s: put({}, {}, { tab: 'progress' }) });
  assert.equal((html.match(/<div class="tl-bigtile-n">—<\/div>/g) || []).length, 4);
  for (const k of ['Answers reviewed', 'Wrong', 'Missing judgments', 'Open batches']) assert.ok(html.includes(k), k);
  assert.ok(html.includes('No open batches.') && html.includes('No reviews yet.'));
  assert.ok(html.includes('From fair (uniform) batches only'));
  assert.ok(html.includes('Saturation: — (shown once failure modes are named)'));
  const loading = markup(PR.default, { s: put({}, { progressLoading: true }, { tab: 'progress' }) });
  assert.equal((loading.match(/<div class="tl-bigtile-n">…<\/div>/g) || []).length, 4);
});

test('Progress shows the figures, the open batches, the fair-sample rate, agreement, coverage and what is missing', () => {
  const html = markup(PR.default, { s: progState() });
  assert.deepEqual([...html.matchAll(/<div class="tl-bigtile-n">([^<]*)<\/div>/g)].map((m) => m[1]), ['12', '3', '37', '2']);
  assert.ok(html.includes('First fair batch · counts toward rates') && html.includes('12 of 15') && html.includes('3 / 8 / 1'));
  assert.ok(html.includes('Reviewers: a@x (12), — (4)'));
  assert.ok(html.includes('Still missing a verdict: 2 listed …and 12 more'));
  assert.ok(html.includes('25% wrong (95% interval 9%–53%)') && html.includes('3 wrong · 9 right · 1 disputed'));
  assert.ok(html.includes('88% agreement on 8 shared answers · fewer than 20 shared answers') && html.includes('First, blind verdicts only.'));
  assert.ok(html.includes('Shares of the reviewed sample, not rates for all answers.'));
  assert.match(html, /<summary[^>]*>Channel<\/summary>/);
  assert.match(html, /<summary[^>]*>Where it was routed · some values thin<\/summary>/);
  assert.equal(/<summary[^>]*>(Page|Asker)/.test(html), false, 'a dimension the reply has no rows for is not listed');
  assert.ok(html.includes('Not recorded · thin'));
  assert.ok(html.includes('Map groups with no reviewed answer: Group 3 (7 answers)'));
});

test('the discovery curve is a labelled picture with a point for each block, and a block-size choice', () => {
  const html = markup(PR.default, { s: progState() });
  assert.match(html, /<svg viewBox="0 0 560 160" role="img" aria-label="Wrong answers found in each block of 10 reviews\./);
  assert.ok(html.includes('most in a block: 3'));
  assert.equal((html.match(/<circle[^>]*class="tl-curve-dot"/g) || []).length, 2);
  assert.ok(html.includes('<title>Reviews up to 10: 3 wrong</title>'));
  assert.match(html, /role="group" aria-label="Reviews per block"/);
  assert.match(html, /aria-pressed="true"[^>]*>10 per block</);
  const sparse = markup(PR.default, { s: progState({ progress: Object.assign({}, PROGRESS, { fair_sample: null, coverage: null }) }) });
  assert.ok(sparse.includes('From fair (uniform) batches only') && sparse.includes('class="tl-meta">—<'), 'no coverage reads as a dash');
  assert.ok(markup(PR.default, { s: progState({ progressError: 'Unreachable' }) }).includes('Could not read progress: Unreachable'));
});

test('the block-size buttons and Refresh read the figures again', async () => {
  handlers[P + '/progress'] = [200, PROGRESS];
  handlers[P + '/sampling/plan'] = [200, PLAN];
  const tree = view(PR.default, { s: progState() });
  await button(tree, '20 per block').props.onClick();
  assert.equal(SM().progressBlock, 20);
  await settle();
  assert.equal(sent('GET', '/progress')[0].url.searchParams.get('block'), '20');
  await button(view(PR.default, { s: tlStore.get() }), 'Refresh').props.onClick();
  await settle();
  assert.equal(sent('GET', '/progress').length, 2);
});
