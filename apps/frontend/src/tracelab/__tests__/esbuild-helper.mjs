// A helper for the Trace Lab tests (not a test file itself: node --test runs only *.test.mjs). It bundles one
// module of src/tracelab with esbuild, the bundler Vite already installs, and imports the result. The bundle is
// written under node_modules/.cache (git-ignored) so `react` resolves from the app's own install. With
// { flagOn: true } the module is built the way a VITE_TRACE_LAB=1 build sees it; without, the flag is off.
import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
export const FRONTEND = resolve(HERE, '../../..');
export const SRC = join(FRONTEND, 'src');
const CACHE = join(FRONTEND, 'node_modules', '.cache', 'tracelab-tests');
const made = [];

export async function bundle(rel, opts) {
  const o = opts || {};
  const res = await build({
    entryPoints: [join(HERE, '..', rel)], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
    external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent',
    define: o.flagOn ? { 'import.meta.env': JSON.stringify({ VITE_TRACE_LAB: '1' }) } : {}
  });
  mkdirSync(CACHE, { recursive: true });
  const dir = mkdtempSync(join(CACHE, 'b-'));
  made.push(dir);
  const file = join(dir, 'bundle.mjs');
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

export function cleanBundles() {
  made.splice(0).forEach((dir) => rmSync(dir, { recursive: true, force: true }));
}
