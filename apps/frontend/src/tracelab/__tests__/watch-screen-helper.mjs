// A helper for the Watch screen tests (not a test file: node --test runs only *.test.mjs).
//
// screen-helper.mjs and tax-screen-helper.mjs bundle a screen with its own copy of what is not shared, and share the
// module's stateful files (the store, the actions and api files, the app's api client) as imports of the real files, so
// a click in a rendered screen acts on the store the test sees. The Watch screen also calls watchActions.js and watchApi.js,
// and those hold state too (the reads in flight, the writes to retry), so watchScreen() is the same bundling with those
// two shared as well (and the Judges pair, for a page frame that mounts that tab). The expansion helpers (view, find,
// button, text …) are screen-helper.mjs's own and are re-exported here.
import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { FRONTEND, SRC } from './esbuild-helper.mjs';

export { button, byLabel, find, findAll, text, view } from './screen-helper.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const MODULE = join(SRC, 'tracelab');
const CACHE = join(FRONTEND, 'node_modules', '.cache', 'tracelab-tests');
const SHARED = new Set(['store.js', 'api.js', 'actions.js', 'goldenActions.js', 'annotateActions.js', 'samplingActions.js', 'taxonomyActions.js', 'judgesActions.js', 'watchActions.js',
  'reviewApi.js', 'goldenApi.js', 'taxonomyApi.js', 'judgesApi.js', 'watchApi.js', 'wiring.js']);
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

export async function watchScreen(rel) {
  const res = await build({
    entryPoints: [join(HERE, '..', rel)], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic', plugins: [shared],
    external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent'
  });
  mkdirSync(CACHE, { recursive: true });
  const dir = mkdtempSync(join(CACHE, 'w-'));
  made.push(dir);
  const file = join(dir, 'bundle.mjs');
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

export function cleanWatchScreens() {
  made.splice(0).forEach((dir) => rmSync(dir, { recursive: true, force: true }));
}
