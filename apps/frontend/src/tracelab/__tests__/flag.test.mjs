// flag — off unless the build says VITE_TRACE_LAB=1. Under node there is no import.meta.env, so every Trace Lab
// test sees the flag off unless it builds a module with { flagOn: true }.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const { ON, enabled } = await import('../flag.js');

test('the flag is off under node', () => {
  assert.equal(ON, false);
  assert.equal(enabled(), false);
});

test('the flag reads VITE_TRACE_LAB with the same guard as the API client', () => {
  const src = readFileSync(new URL('../flag.js', import.meta.url), 'utf8');
  assert.ok(src.includes("const env = (typeof import.meta !== 'undefined' && import.meta.env) || {};"));
  assert.ok(src.includes("export const ON = env.VITE_TRACE_LAB === '1';"));
});

test('a build made with VITE_TRACE_LAB=1 turns it on', async () => {
  const on = await bundle('flag.js', { flagOn: true });
  assert.equal(on.ON, true);
  assert.equal(on.enabled(), true);
  const off = await bundle('flag.js');
  assert.equal(off.ON, false);
});
