// A helper for the taxonomy screen tests (not a test file: node --test runs only *.test.mjs).
//
// screen-helper.mjs keeps the store, the actions and the api files shared between a bundled screen and the test, but
// its list stops at the Phase 3 and 4 files. The taxonomy screens also call taxonomyActions.js and taxonomyApi.js,
// and those hold state too (the store's taxonomy slice, the reads in flight), so a click in a rendered screen must
// reach the very copy the test sees. taxScreen() is screen() with those two files shared as well; the expansion
// helpers (view, find, button, text …) are screen-helper.mjs's own and are re-exported here.
import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { FRONTEND, SRC } from './esbuild-helper.mjs';

export { button, byLabel, find, findAll, text, view } from './screen-helper.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const MODULE = join(SRC, 'tracelab');
const CACHE = join(FRONTEND, 'node_modules', '.cache', 'tracelab-tests');
const SHARED = new Set(['store.js', 'api.js', 'actions.js', 'goldenActions.js', 'annotateActions.js', 'samplingActions.js', 'taxonomyActions.js',
  'reviewApi.js', 'goldenApi.js', 'taxonomyApi.js', 'wiring.js']);
const made = [];

const shared = {
  name: 'tracelab-shared-state',
  setup(b) {
    b.onResolve({ filter: /^\.{1,2}\// }, (args) => {
      const abs = resolve(args.resolveDir, args.path);
      const mine = abs.startsWith(MODULE + sep) && SHARED.has(basename(abs));
      const client = abs === join(SRC, 'api', 'client.js');
      return mine || client ? { path: pathToFileURL(abs).href, external: true } : null;
    });
  }
};

export async function taxScreen(rel) {
  const res = await build({
    entryPoints: [join(HERE, '..', rel)], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic', plugins: [shared],
    external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent'
  });
  mkdirSync(CACHE, { recursive: true });
  const dir = mkdtempSync(join(CACHE, 'x-'));
  made.push(dir);
  const file = join(dir, 'bundle.mjs');
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

export function cleanTaxScreens() {
  made.splice(0).forEach((dir) => rmSync(dir, { recursive: true, force: true }));
}
