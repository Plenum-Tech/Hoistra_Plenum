// nav — the Administration entry. Flag off (every node run): no item, never active, nothing scheduled, so the
// admin navigator is exactly what it was. Flag on (the module built as a VITE_TRACE_LAB=1 build): one item for
// Admin view, in Navigator.jsx's shape, that opens the page; and a ?tl= link reopens the page once after a reload.
import { test, after, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const scrolled = [];
globalThis.window = { location: { origin: 'http://test.local', search: '' }, scrollTo: (x, y) => scrolled.push([x, y]) };
const tick = (ms) => new Promise((r) => setTimeout(r, ms || 5));
const ctl = (state) => { const c = { state: Object.assign({}, state), patches: [] }; c.setState = (p) => { c.patches.push(p); c.state = Object.assign({}, c.state, p); }; return c; };
const ADMIN = { signedIn: true, role: 'admin', view: 'home' };

const off = await import('../nav.js');

beforeEach(() => { window.location.search = ''; scrolled.length = 0; });

test('flag off: no item and never active, whatever the state and the address bar say', async () => {
  window.location.search = '?tl=conversations';
  const c = ctl(ADMIN);
  assert.deepEqual(off.traceLabNav(ADMIN, c), []);
  assert.deepEqual(off.traceLabNav({ signedIn: true, role: 'admin', view: 'tracelab' }, c), []);
  assert.equal(off.traceLabActive({ signedIn: true, view: 'tracelab' }), false);
  await tick();
  assert.deepEqual(c.patches, [], 'nothing is scheduled with the flag off');
});

test('the item has exactly the navigator\'s shape and opens the page', () => {
  const c = ctl(ADMIN);
  const item = off.navItem(ADMIN, c);
  assert.deepEqual(Object.keys(item).sort(), ['badge', 'chip', 'click', 'color', 'icon', 'label']);
  assert.equal(item.label, 'Trace Lab');
  assert.equal(item.icon, 'ph-flask');
  assert.equal(item.color, 'var(--color-neutral-300)');
  assert.equal(item.chip, 'transparent');
  item.click();
  assert.deepEqual(c.patches, [{ view: 'tracelab', role: 'admin', navOpen: true, detail: null }]);
  assert.deepEqual(scrolled, [[0, 0]]);
  const on = off.navItem({ view: 'tracelab' }, c);
  assert.equal(on.color, 'var(--color-accent)');
  assert.equal(on.chip, 'var(--color-accent-900)');
});

test('a ?tl= link reopens the page only for Admin view on Home, and only for a known tab', () => {
  const want = { view: 'tracelab', role: 'admin', navOpen: true, detail: null };
  assert.deepEqual(off.linkOpenPatch(ADMIN, '?tl=conversations&conv=s%3Aa'), want);
  assert.deepEqual(off.linkOpenPatch(ADMIN, '?x=1&tl=models'), want);
  assert.equal(off.linkOpenPatch(ADMIN, '?tl=nowhere'), null);
  assert.equal(off.linkOpenPatch(ADMIN, ''), null);
  assert.equal(off.linkOpenPatch(Object.assign({}, ADMIN, { role: 'user' }), '?tl=runs'), null);
  assert.equal(off.linkOpenPatch(Object.assign({}, ADMIN, { view: 'chat' }), '?tl=runs'), null);
  assert.equal(off.linkOpenPatch(Object.assign({}, ADMIN, { signedIn: false }), '?tl=runs'), null);
  assert.deepEqual(off.TABS, ['conversations', 'runs', 'golden', 'models', 'map', 'progress', 'labels', 'judges', 'watch']);
});

test('flag on: one item in Admin view, none in User view; active only on the page while signed in', async () => {
  const on = await bundle('nav.js', { flagOn: true });
  const c = ctl(ADMIN);
  const items = on.traceLabNav(ADMIN, c);
  assert.equal(items.length, 1);
  assert.equal(items[0].label, 'Trace Lab');
  assert.deepEqual(on.traceLabNav({ signedIn: true, role: 'user', view: 'home' }, c), []);
  assert.equal(on.traceLabActive({ signedIn: true, view: 'tracelab' }), true);
  assert.equal(on.traceLabActive({ signedIn: false, view: 'tracelab' }), false);
  assert.equal(on.traceLabActive({ signedIn: true, view: 'home' }), false);
});

test('flag on: a reload with a ?tl= link reopens the page once, after the render, and only if still on Home', async () => {
  window.location.search = '?tl=runs&run=live';
  const on = await bundle('nav.js', { flagOn: true });
  const c = ctl(ADMIN);
  on.traceLabNav(ADMIN, c);
  assert.deepEqual(c.patches, [], 'never during the render');
  await tick();
  assert.deepEqual(c.patches, [{ view: 'tracelab', role: 'admin', navOpen: true, detail: null }]);
  on.traceLabNav(ADMIN, c);
  await tick();
  assert.equal(c.patches.length, 1, 'once per page load');
  const moved = await bundle('nav.js', { flagOn: true });
  const d = ctl(ADMIN);
  moved.traceLabNav(ADMIN, d);
  d.state.view = 'chat';
  await tick();
  assert.deepEqual(d.patches, [], 'the reader moved on before it fired');
});

test('nav.js imports nothing but the flag', () => {
  const src = readFileSync(new URL('../nav.js', import.meta.url), 'utf8');
  const imports = [...src.matchAll(/^import .* from '([^']+)';$/gm)].map((m) => m[1]);
  assert.deepEqual(imports, ['./flag.js']);
});
