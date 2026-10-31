// A helper for the Trace Lab screen tests (not a test file: node --test runs only *.test.mjs).
//
// esbuild-helper.mjs bundles a screen together with its own copy of the store and the actions, which is right for
// markup tests but means a click in a rendered screen would act on a store the test cannot see. screen() bundles a
// screen the same way but leaves the module's stateful files (the store, the *Actions and *Api files, the app's
// api client) as imports of the real files, so the screen, the actions it calls and the test all share one store.
// view() then expands a component to plain host elements, with the React hooks the screens use stubbed, so a test
// can find a button and call its onClick without a DOM.
import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { basename, dirname, join, resolve, sep } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { FRONTEND, SRC } from './esbuild-helper.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const MODULE = join(SRC, 'tracelab');
const CACHE = join(FRONTEND, 'node_modules', '.cache', 'tracelab-tests');
const SHARED = new Set(['store.js', 'api.js', 'actions.js', 'goldenActions.js', 'annotateActions.js', 'samplingActions.js', 'reviewApi.js', 'goldenApi.js', 'wiring.js']);
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

export async function screen(rel) {
  const res = await build({
    entryPoints: [join(HERE, '..', rel)], bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic', plugins: [shared],
    external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent'
  });
  mkdirSync(CACHE, { recursive: true });
  const dir = mkdtempSync(join(CACHE, 's-'));
  made.push(dir);
  const file = join(dir, 'bundle.mjs');
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

export function cleanScreens() {
  made.splice(0).forEach((dir) => rmSync(dir, { recursive: true, force: true }));
}

// ── expanding a component without a DOM ─────────────────────────────────────────────────────────────────────────
const React = (await import('react')).default;

// Runs `fn` with the hooks the screens call replaced by inert ones (a ref holding its first value, state that never
// changes, effects that never run), so a function component can be called outside a renderer. `states` seeds the
// values the useState calls return, in the order they are called.
function withStubbedHooks(fn, states) {
  const saved = {};
  const seeds = (states || []).slice();
  const stub = {
    useRef: (v) => ({ current: v === undefined ? null : v }),
    useState: (v) => [seeds.length ? seeds.shift() : (typeof v === 'function' ? v() : v), () => {}],
    useEffect: () => {}, useLayoutEffect: () => {}, useMemo: (f) => f(), useCallback: (f) => f
  };
  Object.keys(stub).forEach((k) => { saved[k] = React[k]; React[k] = stub[k]; });
  try { return fn(); } finally { Object.keys(saved).forEach((k) => { React[k] = saved[k]; }); }
}

function expand(node) {
  if (node === null || node === undefined || typeof node === 'boolean') return null;
  if (Array.isArray(node)) return node.map(expand).filter((x) => x !== null);
  if (typeof node !== 'object') return node;
  const { type, props } = node;
  if (typeof type === 'function') return expand(type(props));
  const children = expand(props.children);
  return { type, props: Object.assign({}, props, { children }) };
}

// The element a component returns, expanded to host elements: { type, props } nodes, strings and arrays.
export function view(Component, props, states) {
  return withStubbedHooks(() => expand(React.createElement(Component, props)), states);
}

const kids = (n) => (n && n.props ? [].concat(n.props.children === undefined || n.props.children === null ? [] : n.props.children) : []);

// Every node (depth first) the predicate accepts.
export function findAll(tree, pred) {
  const out = [];
  const visit = (n) => {
    if (Array.isArray(n)) { n.forEach(visit); return; }
    if (!n || typeof n !== 'object') return;
    if (pred(n)) out.push(n);
    kids(n).forEach(visit);
  };
  visit(tree);
  return out;
}

export function text(n) {
  if (n === null || n === undefined) return '';
  if (Array.isArray(n)) return n.map(text).join('');
  if (typeof n !== 'object') return String(n);
  return kids(n).map(text).join('');
}

// The one node the predicate accepts (it must be exactly one).
export function find(tree, pred, what) {
  const hits = findAll(tree, pred);
  if (hits.length !== 1) throw new Error((what || 'node') + ': expected one match, found ' + hits.length);
  return hits[0];
}

export const button = (tree, label) => find(tree, (n) => n.type === 'button' && text(n) === label, 'button "' + label + '"');
export const byLabel = (tree, label) => find(tree, (n) => n.props['aria-label'] === label, 'aria-label "' + label + '"');
