// taxSuggestScreens — AI suggestions on the open answer in Conversations (views/Suggestions.jsx) and the dashed
// highlights, margin cards and mounts they rely on in the Phase 4 annotation layer (logic/annotate.js blockView's marks,
// views/MarginNotes.jsx, views/Conversations.jsx), and the stylesheet block that draws them.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { taxonomyApi, S, settle, fresh, loaded, installDefaults, restoreApis } from './tax-helper.mjs';
import { button, cleanTaxScreens, findAll, taxScreen, text, view } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const SG = await taxScreen('views/Suggestions.jsx');
const MN = await taxScreen('views/MarginNotes.jsx');
const { blockView } = await import('../logic/annotate.js');
const { suggestionMarks } = await import('../logic/suggest.js');

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

const markup = (C, props) => renderToStaticMarkup(React.createElement(C, props));
const here = (rel) => readFileSync(new URL('../' + rel, import.meta.url), 'utf8');

const ANSWER = 'There are 12 open work orders, including 3 cancelled ones.';
const SUGS = [
  { id: 'p-1', kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', mode_definition: 'Present when cancelled jobs count as open.',
    suggestion: 'present', quote: '3 cancelled ones', field: 'answer', span_id: null, evidence: 'Counts cancelled jobs among the open ones.', base: null, turn_id: 't1' },
  { id: 'p-2', kind: 'cell', mode_id: 'm-2', mode_name: 'invents_asset_codes', mode_definition: 'Present when an asset code is made up.',
    suggestion: 'absent', quote: '', field: null, span_id: null, evidence: 'Names no asset code.', base: 'L-2', turn_id: 't1' }];
// The open answer t1 of conversation s:chat-1, read by a company admin (the default) or the platform team (owner).
const open = (tax, owner) => fresh(loaded(Object.assign({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1 }, tax || {}), !!owner),
  { tab: 'conversations', convKey: 's:chat-1', turnId: 't1', review: { blocks: { t1: { ':answer': ANSWER } } } });
const ANCHOR = { span_id: null, field: 'answer' };

test('the annotation layer draws a dashed highlight for a suggestion and a yellow one for a note', () => {
  open();
  const marks = (tid, key, text2) => suggestionMarks(tid === 't1' ? SUGS : [], key, text2);
  const block = blockView(S(), ANCHOR, marks);
  assert.deepEqual(block.segments.filter((x) => x.ids.length).map((x) => [x.text, x.ids]), [['3 cancelled ones', ['sug:p-1']]]);
  assert.deepEqual(blockView(S(), ANCHOR).segments.filter((x) => x.ids.length), [], 'without the hook the block is what it was');
  const html = markup(MN.AnnotatedText, { s: S(), anchor: ANCHOR });
  assert.match(html, /<mark data-tl-hl="sug:p-1" class="tl-hl-sug" aria-describedby="tl-note-sug:p-1">3 cancelled ones<\/mark>/);
  assert.equal(/class="tl-hl[ "]/.test(html), false, 'a suggestion is never painted as a reviewer’s note');
});

test('a stretch shared by a note and a suggestion keeps both classes and both ids', () => {
  const wrong = { id: 'w1', verdict: 'wrong', note: 'Counted them.', reviewer_id: 'me', tombstone: false,
    anchor: { span_id: null, field: 'answer', quote: '3 cancelled', occurrence: 1, span_name: 'turn', span_kind: 'turn' } };
  fresh(loaded({ tlSugTurnId: 't1', tlSug: SUGS, tlSugCells: [], tlSugLoadedAt: 1 }),
    { tab: 'conversations', convKey: 's:chat-1', turnId: 't1', review: { blocks: { t1: { ':answer': ANSWER } }, ann: { t1: { mine: wrong, others: [] } } } });
  const html = markup(MN.AnnotatedText, { s: S(), anchor: ANCHOR });
  assert.match(html, /data-tl-hl="w1 sug:p-1" class="tl-hl tl-hl-sug" aria-describedby="tl-note-w1 tl-note-sug:p-1"/);
  assert.match(html, /<mark data-tl-hl="w1 sug:p-1"[^>]*>3 cancelled<\/mark><mark data-tl-hl="sug:p-1" class="tl-hl-sug"[^>]*> ones<\/mark>/,
    'the rest of the suggestion’s quote keeps its dashed highlight alone');
});

test('a margin card is placed beside its highlight, dashed, with AI suggestion, Accept and Dismiss', () => {
  open();
  const items = SG.suggestionMargin(S());
  assert.deepEqual(items.map((i) => i.id), ['sug:p-1']);
  const html = renderToStaticMarkup(items[0].render({ id: 'sug:p-1', top: 120, height: items[0].height, more: [] }, null, () => {}));
  assert.match(html, /^<div id="tl-note-sug:p-1" data-tl-note="sug:p-1" class="tl-sug-card" style="position:absolute;top:120px/);
  for (const t of ['AI suggestion', 'counts_cancelled_jobs_as_open', 'Present in this answer', '>Accept</button>', '>Dismiss</button>',
    'Dismissing records this answer as a close example where the failure is absent.']) assert.ok(html.includes(t), t);
  const list = markup(SG.SuggestionList, { s: S() });
  assert.ok(list.includes('AI suggestions on this answer') && list.includes('>Accept: absent</button>') && list.includes('>Mark present</button>'));
  assert.ok(list.includes('Nothing here counts until a person decides it.'));
  open({ tlSugHidden: true, tlSug: [] });
  assert.deepEqual(SG.suggestionMargin(S()), []);
  assert.equal(markup(SG.SuggestionList, { s: S() }), '');
});

test('a card with more notes under it offers the expanding stub', () => {
  open();
  const item = SG.suggestionMargin(S())[0];
  const html = renderToStaticMarkup(item.render({ id: 'sug:p-1', top: 0, height: 100, more: ['a', 'b'] }, null, () => {}));
  assert.ok(html.includes('+2 more notes here'));
  assert.equal(renderToStaticMarkup(item.render({ id: 'sug:p-1', top: 0, height: 100, more: [] }, null, () => {})).includes('more note'), false);
});

test('a suggestion whose highlight is not drawn (the margin reports its id) is listed under the timeline, once', () => {
  open();
  const modes = (html) => ['counts_cancelled_jobs_as_open', 'invents_asset_codes'].filter((m) => html.includes(m));
  assert.deepEqual(modes(markup(SG.SuggestionList, { s: S() })), ['invents_asset_codes'], 'a drawn highlight keeps its card in the margin, not the list');
  assert.deepEqual(modes(markup(SG.SuggestionList, { s: S(), also: [] })), ['invents_asset_codes']);
  const both = markup(SG.SuggestionList, { s: S(), also: ['sug:p-1'] });
  assert.deepEqual(modes(both), ['counts_cancelled_jobs_as_open', 'invents_asset_codes'], 'p-1 has a highlight in the stored text but none on screen: it joins the list');
  assert.ok(both.includes('>Accept</button>') && both.includes('>Dismiss</button>') && both.includes('3 cancelled ones'), 'with the same card, Accept and Dismiss included');
  assert.equal(both.split('role="note"').length - 1, 2, 'each suggestion is listed once');
  const named = markup(SG.SuggestionList, { s: S(), also: ['sug:p-1', 'sug:p-2'] });
  assert.equal(named.split('role="note"').length - 1, 2, 'an id that is already in the list adds nothing');
  const other = markup(SG.SuggestionList, { s: S(), also: ['w1', 'sug:nope'] });
  assert.deepEqual(modes(other), ['invents_asset_codes'], 'the ids of notes, and of suggestions that are not on this answer, are ignored');
  open({ tlSugHidden: true });
  assert.equal(markup(SG.SuggestionList, { s: S(), also: ['sug:p-1'] }), '', 'a hidden answer lists nothing, whatever the margin reports');
  open({ tlSugTurnId: 'other-turn' });
  assert.equal(markup(SG.SuggestionList, { s: S(), also: ['sug:p-1'] }), '', 'suggestions read for another answer are not listed');
  open({ tlSug: [SUGS[0]] });
  assert.equal(markup(SG.SuggestionList, { s: S() }), '', 'nothing is listed while every suggestion has its highlight on screen');
  assert.ok(markup(SG.SuggestionList, { s: S(), also: ['sug:p-1'] }).includes('counts_cancelled_jobs_as_open'));
});

test('the margin column reports a card with no highlight on screen under the id the list reads', () => {
  open();
  const extra = SG.suggestionMargin(S());
  const none = MN.layoutNotes(extra, {}, []);
  assert.deepEqual(none.placed, []);
  assert.deepEqual(none.missing, ['sug:p-1']);
  assert.ok(markup(SG.SuggestionList, { s: S(), also: none.missing }).includes('counts_cancelled_jobs_as_open'));
  assert.deepEqual(MN.layoutNotes(extra, { 'sug:p-1': 12 }, []).missing, []);
});

test('the answer’s failure-mode chips are solid for people and dashed for the AI, or say they are hidden', () => {
  const cells = [{ mode_id: 'm-2', name: 'invents_asset_codes', state: 'unchecked', proposal: { suggestion: 'absent', evidence: 'x' } },
    { mode_id: 'm-3', name: 'wrong_period', state: 'present', source: 'human_open_code', approval: 'single' }];
  open({ tlSugCells: cells });
  const html = markup(SG.TurnLabels, { s: S() });
  assert.ok(html.includes('class="tl-chip tl-chip-ai"') && html.includes('AI: absent · invents_asset_codes'));
  assert.ok(html.includes('class="tl-chip tl-chip-present"') && html.includes('wrong_period: present'));
  open({ tlSugHidden: true, tlSug: [], tlSugCells: [] });
  assert.ok(markup(SG.TurnLabels, { s: S() }).includes('AI suggestions and failure modes for this answer stay hidden until you judge it yourself.'));
  open({ tlSugCells: [] });
  assert.ok(markup(SG.TurnLabels, { s: S() }).includes('No failure mode is marked on this answer yet.'));
  open({ tlSugCells: [], tlSugErr: 'The service did not answer.' });
  assert.match(markup(SG.TurnLabels, { s: S() }), /The service did not answer\.<button[^>]*>Retry<\/button>/);
  open({ tlSugLoading: true, tlSugLoadedAt: null, tlSugCells: null, tlSug: null });
  assert.ok(markup(SG.TurnLabels, { s: S() }).includes('Reading…'));
});

test('nothing is drawn without an open answer, or for the platform team, which has no company to review in', () => {
  fresh(loaded(), { tab: 'conversations' });
  assert.equal(markup(SG.TurnLabels, { s: S() }), '');
  assert.equal(markup(SG.SuggestionList, { s: S() }), '');
  assert.deepEqual(SG.suggestionMargin(S()), []);
  open(null, true);
  assert.equal(markup(SG.TurnLabels, { s: S() }), '');
});

test('Accept and Mark present on a card decide through the server with the base the card was read with', async () => {
  open();
  const decided = [];
  taxonomyApi.decideProposal = async (id, body) => { decided.push([id, body]); return { ok: true }; };
  taxonomyApi.proposals = async () => ({ ok: true, served_at: 's', total: 0, hidden: 0, proposals: [] });
  taxonomyApi.turnLabels = async () => ({ ok: true, hidden: false, cells: [] });
  const tree = view(SG.SuggestionList, { s: S() });
  button(tree, 'Accept: absent').props.onClick();
  await settle();
  assert.deepEqual(decided[0], ['p-2', { decision: 'accept', present: null, base: 'L-2' }]);
  open();
  button(view(SG.SuggestionList, { s: S() }), 'Mark present').props.onClick();
  await settle();
  assert.deepEqual(decided[1], ['p-2', { decision: 'override', present: true, base: 'L-2' }]);
  assert.equal(findAll(view(SG.SuggestionList, { s: S() }), (n) => n.props.role === 'alert').length, 0, text(view(SG.SuggestionList, { s: S() })));
});

test('Conversations mounts the failure-mode chips, the margin cards and the list of suggestions that are not on screen', () => {
  const conv = here('views/Conversations.jsx');
  for (const line of ["import { TurnLabels, SuggestionList, suggestionMargin } from './Suggestions.jsx';", '<TurnLabels s={s} />',
    'extra={suggestionMargin(s)}', '<SuggestionList s={s} also={missing} />']) assert.ok(conv.includes(line), line);
  const margin = here('views/MarginNotes.jsx');
  assert.ok(margin.includes('export default function MarginNotes({ s, rootRef, onMissing, extra }) {'));
  assert.ok(margin.includes('if (n.render) return n.render(p, root, () => onExpand(p.id));'));
});

test('the margin column places a card with the notes and hands the timeline to it', () => {
  open();
  const extra = SG.suggestionMargin(S());
  const layout = MN.layoutNotes(extra, { 'sug:p-1': 40 }, []);
  assert.deepEqual(layout.placed.map((p) => [p.id, p.top]), [['sug:p-1', 40]]);
  const html = markup(MN.NoteColumn, { notes: extra, layout, onExpand: () => {}, root: null });
  assert.match(html, /<aside[^>]*aria-label="Notes on this answer"[^>]*><div id="tl-note-sug:p-1"/);
});

test('the stylesheet draws suggestions dashed in the focus colour and keeps reduced motion', () => {
  const css = here('styles.css');
  for (const rule of ['.tl-hl-sug {', '.tl-sug-card {', '.tl-cell-ai .tl-cell-btn {', '.tl-chip-ai {', '.tl-tree-block {', '.tl-overlay {', '.tl-modal {']) assert.ok(css.includes(rule), rule);
  assert.match(css, /\.tl-hl-sug \{[^}]*dashed var\(--tl-focus\)/);
  assert.match(css, /\.tl-sug-card \{[^}]*dashed var\(--tl-focus\)/);
  assert.ok(css.lastIndexOf('prefers-reduced-motion') > css.indexOf('.tl-hl-sug {'));
  assert.equal((css.match(/\.tl-dialog \{/g) || []).length, 1, 'the golden run dialog’s own rule is untouched');
});

// ── the whole Conversations tab ──────────────────────────────────────────────────────────────────────────────
const { Shell } = await taxScreen('TraceLabApp.jsx');
const TURN = 'turn-1';
const TEXT = 'There were 758 work orders in August.';
const REC = { id: TURN, timestamp: '2026-10-15T11:00:00Z', models: [], usage: { cost_usd: 0.01, latency_ms: 4000, tool_calls: 0 },
  meta: { channel: 'chat', turn_index: 1, turn_count: 1, asker: { email: 'fm@example.com' } }, flags: [], feedback: null,
  messages: [
    { id: TURN + ':root:user', role: 'user', depth: 0, text: 'How many work orders?', anchor: { turn_id: TURN, span_id: 'root', field: 'question' } },
    { id: TURN + ':root:answer', role: 'assistant', depth: 0, subtype: 'answer', text: TEXT, anchor: { turn_id: TURN, span_id: 'root', field: 'answer' } }] };
const CONV = { key: 's:abc', session_kind: 'observed', session_label: 'Work orders', turns: [{ turn_id: TURN, turn_index: 1, question: 'How many?', ok: true }], active: REC };
const PROPS = [
  { id: 'p-1', kind: 'depth_scan', mode_id: 'm-1', mode_name: 'counts_cancelled_jobs_as_open', suggestion: 'present', quote: '758 work orders', field: 'answer', span_id: null,
    evidence: 'Counts them all.', base: null, turn_id: TURN },
  { id: 'p-2', kind: 'cell', mode_id: 'm-2', mode_name: 'invents_asset_codes', suggestion: 'absent', quote: '', field: null, span_id: null, evidence: 'Names no code.', base: 'L-2', turn_id: TURN }];

test('the Conversations tab draws the chips, the dashed highlight and the list of suggestions that are not on screen', () => {
  fresh(loaded({ tlSugTurnId: TURN, tlSug: PROPS, tlSugCells: [{ mode_id: 'm-3', name: 'wrong_period', state: 'present', source: 'human_open_code', approval: 'single' }], tlSugLoadedAt: 1 }, false),
    { tab: 'conversations', convKey: 's:abc', turnId: TURN, conv: { data: CONV, loading: false, error: '', reason: '' },
      review: { key: 's:abc', blocks: { [TURN]: { ':question': 'How many work orders?', ':answer': TEXT } } } });
  const html = renderToStaticMarkup(React.createElement(Shell, { s: S(), now: Date.parse('2026-10-15T12:00:00Z') }));
  assert.match(html, /<mark data-tl-hl="sug:p-1" class="tl-hl-sug" aria-describedby="tl-note-sug:p-1">758 work orders<\/mark>/);
  assert.ok(html.includes('Failure modes on this answer') && html.includes('wrong_period: present'));
  assert.ok(html.includes('AI suggestions on this answer') && html.includes('invents_asset_codes'), 'the suggestion with no quote is listed under the timeline');
  assert.equal(html.includes('id="tl-note-sug:p-1"'), false, 'the highlighted suggestion’s card is placed by the browser’s measure, not by the markup');
});

// A suggestion whose quote is in the stored text but whose highlight the page does not draw (its panel is closed, or the
// long result is cut short) is reported by the margin column as missing, and Conversations hands that to the list.
// renderToStaticMarkup runs no effects, so the report is stood in for by the `missing` state it would have set.
const CONVERSATIONS = await taxScreen('views/Conversations.jsx');

function withMissing(ids, fn) {
  const real = React.useState;
  let first = true;
  React.useState = (init) => { if (first) { first = false; return [ids, () => {}]; } return real(init); };
  try { return fn(); } finally { React.useState = real; }
}

test('Conversations lists a suggestion the margin could not place, beside the ones with no quote', () => {
  fresh(loaded({ tlSugTurnId: TURN, tlSug: PROPS, tlSugCells: [], tlSugLoadedAt: 1 }, false),
    { tab: 'conversations', convKey: 's:abc', turnId: TURN, conv: { data: CONV, loading: false, error: '', reason: '' },
      review: { key: 's:abc', blocks: { [TURN]: { ':question': 'How many work orders?', ':answer': TEXT } } } });
  const props = { s: S(), now: Date.parse('2026-10-15T12:00:00Z') };
  const page = (ids) => withMissing(ids, () => renderToStaticMarkup(React.createElement(CONVERSATIONS.default, props)));
  const before = page([]);
  assert.ok(before.includes('invents_asset_codes'), 'the suggestion with no quote is listed');
  assert.equal(before.includes('counts_cancelled_jobs_as_open'), false, 'the one with a drawn highlight waits for the margin');
  const after = page(['sug:p-1']);
  assert.ok(after.includes('counts_cancelled_jobs_as_open') && after.includes('invents_asset_codes'), 'the one the margin could not place is listed too');
  assert.equal(after.split('class="tl-sug-card"').length - 1, 2, 'each suggestion once');
  assert.ok(after.indexOf('AI suggestions on this answer') > after.indexOf('data-tl-timeline'), 'under the timeline');
});
