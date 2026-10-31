// annotate (logic) — open coding in the Trace Lab: a quote is found where the service finds it, overlapping
// highlights keep both notes, margin notes never pile up, other reviewers stay hidden until you have saved, a
// failed or stale save keeps the reviewer's draft on screen, and the view models are plain data read from the
// store's state (no callbacks: the screens call annotateActions.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialState } from '../store.js';
import {
  NO_FAILURE_NOTE, NOTE_MAX, blockKey, findOccurrence, occurrenceAt, anchorFromSelection, highlightSegments, placeMarginNotes,
  unplacedNotes, wrongProblem, blankDraft, reviewInitial, reviewOf, knownMe, noticesView, reviewView, blockView, marginView, commentsView, turnChips
} from '../logic/annotate.js';

const ANSWER = 'There were 758 work orders in August, 758 in total.';
const MINE = { id: 'v1', lineage_id: 'v1', version: 1, reviewer_id: 'me', verdict: 'right', note: NO_FAILURE_NOTE, anchor: null, tombstone: false };
const WRONG = { id: 'w1', lineage_id: 'w1', version: 1, reviewer_id: 'me', verdict: 'wrong', note: 'Counted July.', tombstone: false,
  anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2, span_name: 'turn', span_kind: 'turn' } };
const OTHER = { id: 'o1', lineage_id: 'o1', version: 1, reviewer_id: 'her', reviewer_email: 'ana@example.com', verdict: 'wrong', note: 'Wrong month',
  tombstone: false, anchor: { span_id: null, field: 'answer', quote: 'August', occurrence: 1, span_name: 'turn', span_kind: 'turn' } };
const state = (review, over) => Object.assign(initialState(), { tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' }, over || {},
  { review: Object.assign(reviewInitial(), review || {}) });

test('occurrences count overlapping matches, the way the service numbers them', () => {
  assert.equal(findOccurrence('aaaa', 'aa', 1), 0);
  assert.equal(findOccurrence('aaaa', 'aa', 2), 1);
  assert.equal(findOccurrence('aaaa', 'aa', 3), 2);
  assert.equal(findOccurrence('aaaa', 'aa', 4), -1);
  assert.equal(findOccurrence('aaaa', '', 1), -1);
  assert.equal(findOccurrence('aaaa', 'aa', 0), -1);
  assert.equal(occurrenceAt('aaaa', 'aa', 1), 2);
  assert.equal(occurrenceAt(ANSWER, '758', 38), 2);
  assert.equal(occurrenceAt(ANSWER, '758', 12), 0);
  assert.equal(blockKey({ span_id: 'root', field: 'answer' }), ':answer');
  assert.equal(blockKey({ span_id: 's1', field: 'output' }), 's1:output');
  assert.equal(blockKey({ span_id: 'g', field: 'answer_after' }), null);
  assert.equal(blockKey(null), null);
});

test('a selection inside one block becomes an anchor; across blocks, empty or too long it is refused in words', () => {
  const block = { key: ':answer', span_id: null, field: 'answer', text: ANSWER };
  const ok = anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 38, end: 41 });
  assert.deepEqual(ok, { ok: true, anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 2, key: ':answer' } });
  assert.deepEqual(anchorFromSelection(block, { startKey: ':answer', endKey: 's1:output', start: 38, end: 3 }),
    { ok: false, error: 'Select text inside one step.' });
  assert.equal(anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 5, end: 5 }).ok, false);
  assert.equal(anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 5, end: 5 }).error, 'Select some text first.');
  assert.equal(anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 14, end: 15 }).error, 'Select some text first.', 'a space is not text');
  // A backwards selection reads the same as a forwards one.
  assert.equal(anchorFromSelection(block, { startKey: ':answer', endKey: ':answer', start: 41, end: 38 }).anchor.quote, '758');
  const long = { key: 's1:output', span_id: 's1', field: 'output', text: 'x'.repeat(NOTE_MAX + 5) };
  assert.equal(anchorFromSelection(long, { startKey: 's1:output', endKey: 's1:output', start: 0, end: NOTE_MAX + 1 }).error, 'Select at most 2,000 characters.');
  assert.equal(anchorFromSelection(null, { startKey: 'a', endKey: 'a', start: 0, end: 1 }).ok, false);
  // A step's text keeps its step id; the turn's own question and answer carry none.
  assert.equal(anchorFromSelection(long, { startKey: 's1:output', endKey: 's1:output', start: 0, end: 4 }).anchor.span_id, 's1');
});

test('overlapping highlights share a segment so both notes keep their highlight', () => {
  const segs = highlightSegments('abcdefghij', [{ id: 'a', start: 2, end: 6 }, { id: 'b', start: 4, end: 8 }]);
  assert.deepEqual(segs.map((x) => [x.text, x.ids]), [['ab', []], ['cd', ['a']], ['ef', ['a', 'b']], ['gh', ['b']], ['ij', []]]);
  assert.deepEqual(highlightSegments('abc', [{ id: 'x', start: -1, end: 2 }]).map((x) => x.ids), [[]]);
  assert.deepEqual(highlightSegments('abc', [{ id: 'x', start: 1, end: 99 }]).map((x) => [x.text, x.ids]), [['a', []], ['bc', ['x']]], 'a highlight past the end is clipped');
  assert.deepEqual(highlightSegments('', []), [], 'no text, no segments');
});

test('margin notes stack with an 8 px gap, never overlap, and collapse past 240 px into a stub', () => {
  const items = [{ id: 'n1', top: 100, height: 60 }, { id: 'n2', top: 110, height: 60 }, { id: 'n3', top: 120, height: 60 },
    { id: 'n4', top: 130, height: 60 }, { id: 'n5', top: 131, height: 60 }, { id: 'n6', top: 900, height: 60 }];
  const placed = placeMarginNotes(items, { gap: 8, maxDrift: 240 });
  for (let i = 1; i < placed.length; i += 1) assert.ok(placed[i].top >= placed[i - 1].top + placed[i - 1].height + 8);
  assert.deepEqual(placed.map((p) => p.id), ['n1', 'n2', 'n3', 'n4', 'n6']);
  assert.deepEqual(placed[3].more, ['n5']);                        // n5 would land 241 px below its highlight
  assert.equal(placed[4].top, 900);                               // a far note keeps its own place
  const open = placeMarginNotes(items, { expanded: ['n4'] });
  assert.ok(open.some((p) => p.id === 'n5'));
  assert.ok(placed.every((p) => p.top >= 100));                   // nothing piles up at the top
  assert.deepEqual(placeMarginNotes([]), []);
  assert.deepEqual(placeMarginNotes([{ id: 'b', top: 50, height: 10 }, { id: 'a', top: 50, height: 10 }]).map((p) => p.id), ['a', 'b'], 'ties keep a stable order');
});

test('notes whose quote is not on screen go to the list at the end of the turn', () => {
  const notes = [{ id: 'a', blockKey: ':answer', quote: '758', occurrence: 2 }, { id: 'b', blockKey: 's9:output', quote: 'x', occurrence: 1 },
    { id: 'c', blockKey: ':answer', quote: '758', occurrence: 3 }];
  assert.deepEqual(unplacedNotes(notes, { ':answer': ANSWER }).map((n) => n.id), ['b', 'c']);
  assert.deepEqual(unplacedNotes(notes, null).map((n) => n.id), ['a', 'b', 'c']);
});

test('a Wrong verdict needs a quote, then a note under the limit', () => {
  assert.equal(wrongProblem('why', null), 'Select the text where it first went wrong.');
  assert.equal(wrongProblem('why', { quote: '' }), 'Select the text where it first went wrong.');
  assert.equal(wrongProblem('  ', { quote: '758' }), 'Say what went wrong first.');
  assert.equal(wrongProblem('x'.repeat(NOTE_MAX + 1), { quote: '758' }), 'Keep the note under 2,000 characters.');
  assert.equal(wrongProblem('Counted July.', { quote: '758' }), '');
});

test('nothing is open until a turn is: no review view, and a store without the review slice reads as untouched', () => {
  const s = initialState();
  assert.equal(s.review, undefined, 'Phase 3 state has no review slice; the view models default it');
  assert.equal(reviewView(s), null);
  assert.deepEqual(noticesView(s), { error: '', selectionError: '' });
  assert.deepEqual(commentsView(s), { rows: [], hiddenCopy: '', draft: '', busy: false, error: '' });
  assert.deepEqual(turnChips(s, 'turn-1'), []);
  assert.equal(blockView(s, { span_id: null, field: 'answer' }), null);
  const open = Object.assign(initialState(), { convKey: 's:abc', turnId: 'turn-1' });
  assert.equal(reviewView(open).verdict, null);
  assert.equal(reviewView(open).saving, false);
});

test('a slice made before a company switch reads as untouched, so the next company never sees its drafts', () => {
  const draft = Object.assign(blankDraft(), { verdict: 'wrong', note: 'company A\'s note', anchor: { span_id: null, field: 'answer', quote: '758', occurrence: 1, key: ':answer' } });
  const before = Object.assign(initialState(), { convKey: 's:abc', turnId: 'turn-1', seq: 0, review: Object.assign(reviewInitial(0), { drafts: { 'turn-1': draft }, ann: { 'turn-1': { mine: MINE } } }) });
  assert.equal(reviewView(before).note, 'company A\'s note');
  const after = Object.assign({}, before, { seq: 1 });
  assert.deepEqual(reviewOf(after), reviewInitial(1));
  assert.deepEqual([reviewView(after).note, reviewView(after).verdict, reviewView(after).mine], ['', null, null]);
  assert.deepEqual(turnChips(after, 'turn-1'), []);
  assert.equal(reviewOf(before), before.review, 'inside one company the slice is the one in the store');
});

test('other reviewers stay hidden until you save, with a count', () => {
  const one = reviewView(state({ ann: { 'turn-1': { mine: null, others: null, others_count: 1, others_hidden: true } } }));
  assert.equal(one.blindCopy, '1 other reviewer has judged this answer. Their note stays hidden until you save yours.');
  assert.deepEqual(one.others, []);
  assert.equal(one.othersHidden, true);
  const two = reviewView(state({ ann: { 'turn-1': { mine: null, others: null, others_count: 2, others_hidden: true } } }));
  assert.match(two.blindCopy, /^2 other reviewers/);
  const open = reviewView(state({ ann: { 'turn-1': { mine: MINE, others: [OTHER], others_count: 1, others_hidden: false } } }));
  assert.equal(open.blindCopy, '');
  assert.deepEqual(open.others.map((o) => [o.who, o.verdict, o.note, o.quote]), [['ana@example.com', 'wrong', 'Wrong month', 'August']]);
});

test('the review view shows my saved verdict, the draft over it, and a draft that failed or is saving', () => {
  const saved = reviewView(state({ ann: { 'turn-1': { mine: WRONG, sample_item: { batch_id: 'b1', reason: 'uniform pick' }, reviews_required: 2 } } }));
  assert.deepEqual([saved.verdict, saved.note, saved.quote, saved.canClear], ['wrong', 'Counted July.', '758', true]);
  assert.deepEqual(saved.mine, { id: 'w1', who: 'You', verdict: 'wrong', mark: '✗ Wrong', note: 'Counted July.', quote: '758' });
  assert.deepEqual(saved.inBatch, { reason: 'uniform pick', needs: 2, batchId: 'b1' });
  const right = reviewView(state({ ann: { 'turn-1': { mine: MINE } } }));
  assert.deepEqual([right.verdict, right.mine.mark, right.mine.note, right.inBatch, right.canClear], ['right', '✓ Right', '', null, true]);
  // A cleared verdict (tombstone) is no verdict.
  const cleared = reviewView(state({ ann: { 'turn-1': { mine: Object.assign({}, MINE, { tombstone: true }) } } }));
  assert.deepEqual([cleared.verdict, cleared.mine, cleared.canClear], [null, null, false]);
  const draft = Object.assign(blankDraft(), { verdict: 'wrong', note: 'half', anchor: { span_id: null, field: 'answer', quote: 'August', occurrence: 1, key: ':answer' },
    error: 'Not saved: Unreachable', saving: false, focus: true });
  const v = reviewView(state({ ann: { 'turn-1': { mine: MINE } }, drafts: { 'turn-1': draft } }));
  assert.deepEqual([v.verdict, v.note, v.quote, v.error, v.focusNote, v.saving], ['wrong', 'half', 'August', 'Not saved: Unreachable', true, false]);
  assert.equal(reviewView(state({ drafts: { 'turn-1': Object.assign({}, draft, { saving: true }) } })).saving, true);
});

test('a saved Wrong verdict shows no note form until I choose to edit it; a draft is what the form edits', () => {
  const saved = reviewView(state({ ann: { 'turn-1': { mine: WRONG } } }));
  assert.deepEqual([saved.editing, saved.canEdit], [false, true], 'saved, the bar offers Edit my note, not an empty form');
  const right = reviewView(state({ ann: { 'turn-1': { mine: MINE } } }));
  assert.deepEqual([right.editing, right.canEdit], [false, false], 'a Right verdict has no note to edit');
  const draft = Object.assign(blankDraft(), { verdict: 'wrong', note: 'Counted July.' });
  const editing = reviewView(state({ ann: { 'turn-1': { mine: WRONG } }, drafts: { 'turn-1': draft } }));
  assert.deepEqual([editing.editing, editing.canEdit], [true, false]);
});

test('a stale save shows the current version and keeps the draft beside it', () => {
  const theirs = Object.assign({}, MINE, { id: 'v7', version: 7, verdict: 'wrong', note: 'Wrong building', anchor: { quote: 'August', occurrence: 1 } });
  const draft = Object.assign(blankDraft(), { verdict: 'wrong', note: 'Counted July.', conflict: { current: theirs, action: 'save' } });
  const v = reviewView(state({ ann: { 'turn-1': { mine: MINE } }, drafts: { 'turn-1': draft } }));
  assert.equal(v.conflict.message, 'Changed since you opened it.');
  assert.equal(v.conflict.theirs.note, 'Wrong building');
  assert.equal(v.conflict.theirs.quote, 'August');
  assert.equal(v.note, 'Counted July.');
});

test('a block of the open answer: highlights for my note, others\' notes and the draft; none when the service sent no text', () => {
  const answer = { span_id: null, field: 'answer' };
  const blocks = { 'turn-1': { ':answer': ANSWER } };
  const quote = { span_id: null, field: 'answer', quote: '758', occurrence: 1, key: ':answer' };
  const d = Object.assign(blankDraft(), { verdict: 'wrong', note: 'half-written', anchor: quote, popover: true });
  const b = blockView(state({ ann: { 'turn-1': { mine: MINE } }, blocks, drafts: { 'turn-1': d } }), answer);
  assert.ok(b.popover);
  assert.equal(b.key, ':answer');
  assert.equal(b.note, 'half-written');
  assert.deepEqual(b.block, { key: ':answer', span_id: null, field: 'answer', text: ANSWER });
  assert.deepEqual(b.segments.filter((x) => x.ids.length).map((x) => [x.text, x.ids]), [['758', ['draft']]]);
  // My wrong note (second 758) and another reviewer's (August) both highlight; no draft, no popover.
  const both = blockView(state({ ann: { 'turn-1': { mine: WRONG, others: [OTHER] } }, blocks }), answer);
  assert.equal(both.popover, false);
  assert.deepEqual(both.segments.filter((x) => x.ids.length).map((x) => [x.text, x.ids]), [['August', ['o1']], ['758', ['w1']]]);
  assert.deepEqual(both.items.map((i) => [i.id, i.kind]), [['w1', 'mine'], ['o1', 'other']]);
  // A draft restored from my saved verdict carries the service's anchor (no key): it still lands in its block.
  const restored = Object.assign(blankDraft(), { verdict: 'wrong', note: 'edit', anchor: Object.assign({}, WRONG.anchor), popover: true });
  assert.ok(blockView(state({ ann: { 'turn-1': { mine: WRONG } }, blocks, drafts: { 'turn-1': restored } }), answer).popover);
  assert.equal(blockView(state({ ann: { 'turn-1': { mine: MINE } } }), answer), null, 'no text from the service, no annotatable block');
  assert.equal(blockView(state({ blocks }), { span_id: 's9', field: 'output' }), null);
  assert.equal(blockView(state({ blocks }), { span_id: 's9', field: 'answer_after' }), null);
});

test('margin notes are the open answer\'s wrong notes with their step in plain words; ones not on screen are listed apart', () => {
  const step = Object.assign({}, OTHER, { id: 'o2', anchor: { span_id: 's1', field: 'output', quote: 'gone', occurrence: 1, span_name: 'udr_agent', span_kind: 'agent' } });
  const s = state({ ann: { 'turn-1': { mine: WRONG, others: [OTHER, step, Object.assign({}, OTHER, { id: 'o3', verdict: 'right', anchor: null })] } },
    blocks: { 'turn-1': { ':answer': ANSWER, 's1:output': 'nothing like it' } } });
  const m = marginView(s);
  assert.deepEqual(m.notes.map((n) => [n.id, n.me, n.who, n.blockKey, n.step]), [
    ['w1', true, 'You', ':answer', 'the answer'], ['o1', false, 'ana@example.com', ':answer', 'the answer'], ['o2', false, 'ana@example.com', 's1:output', 'Data lookup']]);
  assert.deepEqual(m.unplaced.map((n) => n.id), ['o2']);
  assert.equal(m.unplaced[0].clipped, 'gone');
  assert.ok(m.notes.every((n) => n.height >= 46));
  assert.deepEqual(marginView(state({ ann: { 'turn-1': { mine: WRONG } } })).unplaced, [], 'with no block text nothing is called unplaced');
  assert.deepEqual(marginView(state()).notes, []);
  assert.equal(marginView(state({ ann: { 'turn-1': { mine: Object.assign({}, WRONG, { anchor: { quote: 'x'.repeat(200), occurrence: 1, field: 'answer' } }) } } })).notes[0].clipped.length, 80);
});

test('turn chips: my verdict, how many said right, the hidden count and batch membership', () => {
  const s = state({ ann: {
    t1: { mine: MINE, others: [Object.assign({}, OTHER, { verdict: 'right' })], others_count: 1, sample_item: { batch_id: 'b1' } },
    t2: { mine: null, others: null, others_count: 2, others_hidden: true },
    t3: { mine: WRONG, others: [] },
    t4: { mine: null, others: [Object.assign({}, OTHER, { verdict: 'right' })], others_count: 1 }
  } });
  assert.deepEqual(turnChips(s, 't1'), ['✓ You: right', 'Right ×2', 'In a review batch']);
  assert.deepEqual(turnChips(s, 't2'), ['2 hidden']);
  assert.deepEqual(turnChips(s, 't3'), ['✗ You: wrong']);
  assert.deepEqual(turnChips(s, 't4'), ['Right ×1']);
  assert.deepEqual(turnChips(s, 'unknown'), []);
});

test('comments: hidden ones are counted, never shown; Remove is offered only on mine, known from my own verdicts or writes', () => {
  const rows = [{ id: 'c1', lineage_id: 'l1', reviewer_id: 'me', reviewer_email: 'me@example.com', note: 'mine' },
    { id: 'c2', lineage_id: 'l2', reviewer_id: 'her', reviewer_email: 'ana@example.com', note: 'hers' }];
  const base = { comments: rows, ann: { 'turn-1': { mine: MINE, others: [], others_count: 0 } } };
  const v = commentsView(state(base));
  assert.deepEqual(v.rows.map((r) => [r.id, r.base, r.who, r.note, r.mine]), [['l1', 'c1', 'me@example.com', 'mine', true], ['l2', 'c2', 'ana@example.com', 'hers', false]]);
  assert.equal(commentsView(state(Object.assign({}, base, { ann: {} }))).rows.every((r) => !r.mine), true, 'unknown identity: nothing is called mine');
  assert.deepEqual(commentsView(state(Object.assign({}, base, { ann: {}, me: 'her' }))).rows.map((r) => r.mine), [false, true], 'learned from a comment I wrote');
  assert.deepEqual(commentsView(state(Object.assign({}, base, { ann: {} })), 'me').rows.map((r) => r.mine), [true, false], 'an id the screen knows wins');
  assert.equal(commentsView(state({ commentsHidden: 1 })).hiddenCopy, '1 comment from another reviewer stays hidden until you have judged every answer here.');
  assert.equal(commentsView(state({ commentsHidden: 2 })).hiddenCopy, '2 comments from other reviewers stay hidden until you have judged every answer here.');
  assert.equal(commentsView(state({ commentsHidden: 0 })).hiddenCopy, '');
  const typing = commentsView(state({ commentDraft: 'Same failure as yesterday', commentError: 'Not saved: Unreachable', commentBusy: true }));
  assert.deepEqual([typing.draft, typing.error, typing.busy], ['Same failure as yesterday', 'Not saved: Unreachable', true]);
});

test('knownMe reads my id from what I wrote, from my own verdicts, or from a mine-only comment list', () => {
  assert.equal(knownMe(reviewInitial()), null);
  assert.equal(knownMe(Object.assign(reviewInitial(), { me: 'x' })), 'x');
  assert.equal(knownMe(Object.assign(reviewInitial(), { ann: { a: { mine: null }, b: { mine: Object.assign({}, MINE, { tombstone: true }) } } })), 'me', 'a cleared verdict is still mine');
  // Blind somewhere: the service sent only my own comments, so every comment is mine.
  const blind = Object.assign(reviewInitial(), { ann: { a: { mine: null, others: null, others_count: 1, others_hidden: true } },
    comments: [{ id: 'c1', lineage_id: 'l1', reviewer_id: 'me-too', note: 'n' }] });
  assert.equal(knownMe(blind), 'me-too');
  assert.equal(commentsView(Object.assign(initialState(), { review: blind })).rows[0].mine, true);
});

test('the notices carry the load error and the selection sentence', () => {
  assert.deepEqual(noticesView(state({ error: 'Unreachable', selError: 'Select text inside one step.' })), { error: 'Unreachable', selectionError: 'Select text inside one step.' });
});
