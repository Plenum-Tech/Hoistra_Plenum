// Mount — the only Trace Lab component the app renders. Flag off: nothing, and the page's chunk is never asked
// for. Flag on: the page loads lazily behind "Reading…", inside a boundary that keeps a Trace Lab failure off
// the rest of the app.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');

test('flag off: Mount renders nothing', async () => {
  const { default: Mount } = await bundle('Mount.jsx');
  assert.equal(renderToStaticMarkup(React.createElement(Mount, { vals: {} })), '');
});

test('flag on: the page is lazy, behind "Reading…"', async () => {
  const { default: Mount } = await bundle('Mount.jsx', { flagOn: true });
  const html = renderToStaticMarkup(React.createElement(Mount, { vals: {} }));
  assert.ok(html.includes('Reading…'), html);
});

test('the page is imported lazily and only when the flag is on, inside an error boundary', () => {
  const src = readFileSync(new URL('../Mount.jsx', import.meta.url), 'utf8');
  assert.ok(src.includes("const TraceLabApp = ON ? React.lazy(() => import('./TraceLabApp.jsx')) : null;"));
  assert.equal(/^import .*TraceLabApp/m.test(src), false, 'never a static import of the page');
  assert.ok(src.includes('getDerivedStateFromError'));
  assert.ok(src.includes('if (!ON || !TraceLabApp) return null;'));
});
