// store — Trace Lab's own state: a small external store read with useSyncExternalStore, and the reset a
// company switch performs. Nothing here touches the app controller.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { createStore, useStore, tlStore, initialState, resetForCompany, FILTER_DEFAULTS } = await import('../store.js');

test('get, set with a patch or a function, and a set that returns nothing changes nothing', () => {
  const s = createStore({ n: 1, m: 'a' });
  assert.deepEqual(s.get(), { n: 1, m: 'a' });
  s.set({ n: 2 });
  assert.deepEqual(s.get(), { n: 2, m: 'a' });
  s.set((p) => ({ n: p.n + 1 }));
  assert.equal(s.get().n, 3);
  const before = s.get();
  s.set(() => null);
  assert.equal(s.get(), before);
  assert.deepEqual(createStore(() => ({ fresh: true })).get(), { fresh: true });
});

test('subscribers hear every change until they unsubscribe', () => {
  const s = createStore({ n: 0 });
  let heard = 0;
  const off = s.subscribe(() => { heard += 1; });
  s.set({ n: 1 });
  s.set({ n: 2 });
  off();
  s.set({ n: 3 });
  assert.equal(heard, 2);
});

test('every state change replaces the state object, so React sees it', () => {
  const s = createStore({ n: 0 });
  const a = s.get();
  s.set({ n: 0 });
  assert.notEqual(s.get(), a);
});

test('useStore reads the store through useSyncExternalStore, with a selector', () => {
  const s = createStore({ n: 7, label: 'seven' });
  function Show() { return React.createElement('span', null, useStore(s, (x) => x.label) + ':' + useStore(s).n); }
  assert.equal(renderToStaticMarkup(React.createElement(Show)), '<span>seven:7</span>');
});

test('the initial state is fresh every time and names every Phase 2 key', () => {
  const a = initialState();
  const b = initialState();
  assert.notEqual(a.filters, b.filters);
  assert.notEqual(a.open, b.open);
  assert.notEqual(a.convs, b.convs);
  assert.deepEqual(a.filters, FILTER_DEFAULTS);
  assert.equal(a.tab, 'conversations');
  assert.equal(a.run, 'live');
  assert.deepEqual(Object.keys(a).sort(), ['call', 'calls', 'conv', 'convKey', 'convs', 'facets', 'filters', 'golden', 'open', 'poll', 'qDraft',
    'run', 'runs', 'seq', 'tab', 'turnId'].sort());
});

test('a company switch clears every company-scoped value, keeps the tab, and bumps the sequence', () => {
  tlStore.set({ tab: 'models', run: 'abc', convKey: 's:x', turnId: 't1', open: { a: true }, call: { spanId: 'sp' },
    convs: Object.assign(initialState().convs, { rows: [{ key: 's:x' }] }), filters: { range: '30d', channel: 'chat', status: 'error', q: 'b' } });
  const seq = tlStore.get().seq;
  resetForCompany();
  const s = tlStore.get();
  assert.equal(s.seq, seq + 1);
  assert.equal(s.tab, 'models');
  assert.equal(s.run, 'live');
  assert.equal(s.convKey, null);
  assert.equal(s.turnId, null);
  assert.deepEqual(s.open, {});
  assert.equal(s.call, null);
  assert.equal(s.convs.rows, null);
  assert.deepEqual(s.filters, FILTER_DEFAULTS);
});
