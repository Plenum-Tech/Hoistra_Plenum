// timelineCompact — a database query card stays short on the conversation page. With the service's text for the
// block (review mode), the card still shows the compact table of the first rows, and the full text, which can be
// thousands of lines of JSON, waits behind "Show all". A card that holds a reviewer's note starts open, so the
// highlight and its margin note stay on screen.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { cleanScreens, screen } from './screen-helper.mjs';

globalThis.window = { location: { origin: 'http://test.local', pathname: '/', search: '' }, history: { replaceState: () => {} } };
globalThis.document = { visibilityState: 'visible' };

after(cleanScreens);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { tlStore, initialState } = await import('../store.js');
const { reviewInitial } = await import('../logic/annotate.js');
const TL = await screen('views/Timeline.jsx');

const ROWS = Array.from({ length: 20 }, (_, i) => ({ id: 'row-' + i, status: 'active' }));
const TEXT = JSON.stringify(ROWS, null, 2);
const ANCHOR = { span_id: 'q1', field: 'output' };
const CARD = { id: 't1:q1:query', kind: 'db', role: 'Database query', depth: 1, title: 'Include asset maintenance plans', rowCount: 20,
  rows: ROWS.slice(0, 10), moreRows: 10, ok: true, error: '', raw: { sql: 'SELECT 1', params: {}, label: null }, anchor: ANCHOR };
const NOTE = { id: 'w1', lineage_id: 'w1', version: 1, reviewer_id: 'me', verdict: 'wrong', note: 'Wrong row.', tombstone: false,
  anchor: { span_id: 'q1', field: 'output', quote: 'row-3', occurrence: 1, span_name: 'query', span_kind: 'db' } };

function state(review) {
  const seq = tlStore.get().seq + 1;
  tlStore.set(Object.assign(initialState(), { seq, tab: 'conversations', convKey: 's:abc', turnId: 'turn-1' },
    { review: Object.assign(reviewInitial(seq), { key: 's:abc', blocks: { 'turn-1': { 'q1:output': TEXT } } }, review || {}) }));
  return tlStore.get();
}
// The query's step row is opened, as a reviewer would, unless a test says otherwise.
const render = (s, more, card) => {
  const opens = Object.assign({ 't1:q1:query:step': true }, more || {});
  return renderToStaticMarkup(React.createElement(TL.default, {
    cards: [card || CARD], s, isOpen: (k, d) => (Object.prototype.hasOwnProperty.call(opens, k) ? opens[k] : d), setOpen: () => {} }));
};

test('with the service text, a query card shows the compact table and keeps the full text behind Show all', () => {
  const html = render(state());
  assert.ok(html.includes('<table'), 'the first rows as a table');
  assert.ok(!html.includes('tl-block'), 'not the full text');
  assert.ok(html.includes('10 more rows not shown'));
  assert.ok(html.includes('Show all as text (20 rows)'));
});

test('Show all opens the full text, selectable for a note, and offers Show less', () => {
  const html = render(state(), { 't1:q1:query:all': true });
  assert.ok(html.includes('tl-block'), 'the full text block');
  assert.ok(html.includes('row-19'), 'every row');
  assert.ok(!html.includes('<table'), 'the table gives way to the text');
  assert.ok(html.includes('Show less'));
});

test('a card holding a reviewer note starts open so its highlight stays on screen', () => {
  const opens = {};
  const html = renderToStaticMarkup(React.createElement(TL.default, { cards: [CARD], setOpen: () => {},
    s: state({ ann: { 'turn-1': { mine: NOTE, others: [], others_count: 0, others_hidden: false } } }),
    isOpen: (k, d) => (k in opens ? opens[k] : d) }));
  assert.ok(html.includes('tl-block'));
  assert.ok(html.includes('data-tl-hl="w1"'), 'the highlight');
});

test('without the service text there is no Show all and the table is the whole card', () => {
  const html = render(undefined);
  assert.ok(html.includes('<table'));
  assert.ok(!html.includes('Show all'));
});

test('closed, the query is one line with its name and row count; a query with no rows offers no Show all', () => {
  const closed = render(state(), { 't1:q1:query:step': false });
  assert.ok(closed.includes('class="tl-step-row" aria-expanded="false"'));
  assert.ok(closed.includes('Include asset maintenance plans'));
  assert.ok(closed.includes('20 rows'));
  assert.ok(!closed.includes('<table'), 'no body while closed');
  const none = render(state(), {}, Object.assign({}, CARD, { rowCount: 0, rows: [], moreRows: 0 }));
  assert.ok(none.includes('No rows came back.'));
  assert.ok(!none.includes('Show all'));
});
