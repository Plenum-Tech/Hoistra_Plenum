// The Golden Rule (G0) on the frontend: Trace Lab is a separate module, and since 19 Oct 2026 a separate place: the
// lab page (src/lab, /lab/) hosts it and Hoistra no longer shows it. Hoistra reaches the module only through the one
// remaining approved attachment (A3 components/shell/PlatformCostPage.jsx, the Evaluation spend line); A1 App.jsx and
// A2 logic/integrations.js are gone, and nothing in Hoistra imports the lab page; with the flag off the admin navigator and
// the page list are what they were; and the module's own source passes the app's internal-names scan, whose
// skip list may not be edited.
import { test, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, extname } from 'node:path';
import { transformSync } from 'esbuild';
import { FRONTEND, SRC } from './esbuild-helper.mjs';

const MODULE = join(SRC, 'tracelab');
const LAB = join(SRC, 'lab');
const LOADER = { '.js': 'jsx', '.jsx': 'jsx', '.ts': 'ts', '.tsx': 'tsx', '.mjs': 'js' };
const rel = (p) => relative(FRONTEND, p).split('\\').join('/');
function walk(dir, out) {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name);
    if (statSync(p).isDirectory()) walk(p, out);
    else if (LOADER[extname(p)]) out.push(p);
  }
  return out;
}
// Comments out, as test/noInternalNames.test.mjs strips them.
const stripComments = (file) => transformSync(readFileSync(file, 'utf8'),
  { loader: LOADER[extname(file)], legalComments: 'none', minifyWhitespace: true, jsx: 'preserve', format: 'esm' }).code;

// The approved import sites and the one specifier each may use.
const ALLOWED = {
  'src/components/shell/PlatformCostPage.jsx': '../../tracelab/EvalSpendLine.jsx'
};
const REQUIRED_NOW = ['src/components/shell/PlatformCostPage.jsx'];
const SPEC = /(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*)(["'])([^"']+)\1/g;
const intoModule = (spec) => /(^|\/)tracelab(\/|$)/.test(spec);

function moduleImports() {
  const found = [];
  for (const file of walk(SRC, [])) {
    if (file === MODULE || file.startsWith(MODULE + '/') || file.startsWith(LAB + '/')) continue;
    const code = stripComments(file);
    let m;
    SPEC.lastIndex = 0;
    while ((m = SPEC.exec(code))) if (intoModule(m[2])) found.push([rel(file), m[2]]);
  }
  return found;
}

test('(a) only the approved attachments import the Trace Lab module, each with its approved path', () => {
  const found = moduleImports();
  const bad = found.filter(([file, spec]) => ALLOWED[file] !== spec);
  assert.deepEqual(bad, [], 'Golden Rule G0: only approved attachments may import src/tracelab:\n' + bad.map((b) => b.join(' imports ')).join('\n'));
  for (const file of REQUIRED_NOW) assert.ok(found.some(([f]) => f === file), file + ' carries its attachment');
  assert.equal(found.filter(([f]) => f === 'src/App.jsx').length, 0, 'Hoistra no longer mounts Trace Lab');
  assert.equal(found.filter(([f]) => f === 'src/logic/integrations.js').length, 0, 'Hoistra\'s menu no longer lists Trace Lab');
  assert.equal(found.filter(([f]) => f === 'src/components/shell/PlatformCostPage.jsx').length, 1);
});

test('(a) the import scan is not vacuous', () => {
  const code = 'import X from "./tracelab/x.js";const y=import("../../tracelab/z.jsx");export{a}from"../tracelab";import "./tracelabs/n.js";';
  const hits = [];
  let m;
  SPEC.lastIndex = 0;
  while ((m = SPEC.exec(code))) if (intoModule(m[2])) hits.push(m[2]);
  assert.deepEqual(hits, ['./tracelab/x.js', '../../tracelab/z.jsx', '../tracelab']);
});

test('(a) Hoistra\'s app and menu name Trace Lab nowhere, and only Users & access imports the lab\'s access control', () => {
  const app = readFileSync(join(SRC, 'App.jsx'), 'utf8');
  const integ = readFileSync(join(SRC, 'logic', 'integrations.js'), 'utf8');
  assert.equal(/tracelab|TraceLab|traceLab/.test(app), false);
  assert.equal(/tracelab|TraceLab|traceLab/.test(integ), false);
  const intoLab = /(^|\/)lab(\/|$)/;
  const bad = [];
  for (const file of walk(SRC, [])) {
    if (file.startsWith(LAB + '/') || file.startsWith(MODULE + '/')) continue;
    const code = stripComments(file);
    let m;
    SPEC.lastIndex = 0;
    while ((m = SPEC.exec(code))) if (intoLab.test(m[2]) && m[2].startsWith('.')) bad.push(rel(file) + ' imports ' + m[2]);
  }
  // The one approved exception (20 Oct 2026): Users & access shows each person's Trace Lab access control.
  assert.deepEqual(bad, ['src/screens/Users.jsx imports ../lab/TraceLabAccess.jsx']);
  const users = readFileSync(join(SRC, 'screens', 'Users.jsx'), 'utf8');
  assert.equal((users.match(/import TraceLabAccess from '\.\.\/lab\/TraceLabAccess\.jsx';/g) || []).length, 1, 'the one import');
  assert.equal((users.match(/<TraceLabAccess /g) || []).length, 1, 'the one mount');
  assert.equal((users.match(/TraceLabAccess/g) || []).length, 3, 'the import and the one mount, nothing else');
});

// ── (b) flag off: the admin navigator and the page list are unchanged ──────────────────────────────────────
const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local', pathname: '/', search: '?tl=conversations' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
const sent = [];
globalThis.fetch = async (url, opts) => {
  sent.push(((opts && opts.method) || 'GET') + ' ' + new URL(String(url)).pathname);
  return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}', headers: { get: () => null } };
};
const { traceLabNav, traceLabActive } = await import('../nav.js');
const { HoistraLogic } = await import('../../logic/HoistraLogic.js');
let c = null;
afterEach(() => {
  if (!c) return;
  clearTimeout(c._mgTimer); clearTimeout(c._tt);
  clearInterval(c._orchTick); clearInterval(c._ccTick);
  c = null;
});
const ADMIN_LABELS = ['Integrations', 'Users & access', 'Hoist Crons', 'Chat memory', 'Hoist Traces', 'Skill lab', 'Audit trail'];

test('(b) flag off: traceLabNav adds nothing and the page is never active', () => {
  const ctl = { state: {}, setState: () => { throw new Error('nothing may be set with the flag off'); } };
  assert.deepEqual(traceLabNav({ signedIn: true, role: 'admin', view: 'home' }, ctl), []);
  assert.deepEqual(traceLabNav({ signedIn: true, role: 'admin', view: 'tracelab' }, ctl), []);
  assert.equal(traceLabActive({ signedIn: true, view: 'tracelab' }), false);
});

test('(b) Hoistra\'s admin navigator lists exactly its seven entries and opens no Trace Lab page', async () => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
  const v = c.renderVals();
  assert.deepEqual(v.navAdmin.map((a) => a.label), ADMIN_LABELS);
  assert.equal(v.isTraceLab, undefined);
  c.setState({ role: 'user', view: 'home' });
  assert.deepEqual(c.renderVals().navAdmin, []);
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(c.state.view, 'home', 'a ?tl= link opens nothing with the flag off');
  assert.equal(sent.some((s) => s.includes('/trace-lab')), false, 'no Trace Lab request with the flag off');
});

// ── (c) the module passes the app's internal-names scan ──────────────────────────────────────────────────────
function forbiddenPatterns() {
  const src = readFileSync(join(FRONTEND, 'test', 'noInternalNames.test.mjs'), 'utf8');
  const block = src.slice(src.indexOf('const FORBIDDEN = ['), src.indexOf('];', src.indexOf('const FORBIDDEN = [')));
  const out = [];
  const re = /\{\s*name:\s*'([^']+)',\s*re:\s*\/((?:\\.|[^/\\\n])+)\/([a-z]*)\s*\}/g;
  let m;
  while ((m = re.exec(block))) out.push({ name: m[1], re: new RegExp(m[2], m[3].includes('g') ? m[3] : m[3] + 'g') });
  return out;
}

// The hits of every pattern in a piece of code with its comments already stripped; an import path is skipped,
// as test/noInternalNames.test.mjs skips it.
function scan(code, patterns) {
  const hits = [];
  for (const f of patterns) {
    f.re.lastIndex = 0;
    let m;
    while ((m = f.re.exec(code))) {
      if (/(?:import|from)\s*["'][^"']*$/.test(code.slice(Math.max(0, m.index - 80), m.index))) continue;
      hits.push('[' + f.name + '] …' + code.slice(Math.max(0, m.index - 60), m.index + 60).replace(/\s+/g, ' ') + '…');
    }
  }
  return hits;
}

test('(c) the internal-names patterns are read from the app\'s own test, and the scan catches a planted name', () => {
  const patterns = forbiddenPatterns();
  const names = patterns.map((f) => f.name);
  assert.ok(names.length >= 9, names.join(', '));
  for (const n of ['service name', 'database schema', 'raw route', 'agent service', 'document search service']) assert.ok(names.includes(n), n);
  // Built at run time, so this file itself stays clean.
  const planted = ['svc', 'udr'].join('-') + ' ' + ['plenum', 'cafm'].join('_') + ' ' + ['deep', 'agents'].join('-');
  assert.equal(scan('const a = "' + planted + '";', patterns).length, 3);
  assert.deepEqual(scan('import x from "../' + ['svc', 'udr'].join('-') + '/y.js";', patterns), []);
});

test('(c) no file of the module names an internal service, table, route or document', () => {
  const patterns = forbiddenPatterns();
  const found = [];
  for (const file of walk(MODULE, []).concat(walk(LAB, []))) scan(stripComments(file), patterns).forEach((h) => found.push(rel(file) + ' ' + h));
  assert.deepEqual(found, [], 'internal names in Trace Lab source:\n' + found.join('\n'));
});
