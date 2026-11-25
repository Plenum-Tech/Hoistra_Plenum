// A consultant's Home is the question box only (25 Nov 2026, Hussain): "remove hoist score, P&L,
// Hoist Cron, Platform value for the consultant. just keep it query only ... no one else change".
//
// The flag is set in instVals' consultant branch (logic/instances.js) and Home.jsx leaves the
// four-tile row out when it is set. Everyone else - administrators, plain users, other access
// roles - has no flag and keeps the row exactly as before.
//
// The real controller in Node, as consultantRole.test.mjs runs it, and Home.jsx bundled with
// esbuild (as Vite would) and rendered to markup.
import { test, beforeEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { writeFileSync, rmSync } from 'node:fs';
import { fileURLToPath, pathToFileURL } from 'node:url';
import path from 'node:path';

const mem = {};
const tabMem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: (k) => (k in tabMem ? tabMem[k] : null), setItem: (k, v) => { tabMem[k] = String(v); }, removeItem: (k) => { delete tabMem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url) => { throw new TypeError('no network in tests: ' + url); };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { configureAuth } = await import('../src/api/client.js');

const USER = { id: 'u-9', email: 'cara@example.com', full_name: 'Cara Mensah', organization_id: 'org-1', organization_name: 'Northbridge', status: 'active', email_verified: true, role: 'user' };
const CONSULTANT = Object.assign({}, USER, { access_role: 'consultant', access_role_label: 'Consultant', pages: null, screens: ['buildings'], pages_known: true });
const ADMIN = Object.assign({}, USER, { id: 'u-1', role: 'admin' });
const ROLE_USER = Object.assign({}, USER, { id: 'u-3', access_role: 'general_user', access_role_label: 'General user', pages: ['compliance', 'vendors'], pages_known: true });

const QUIET = ['authLoadScope', 'usLiveLoad', 'auLiveLoad', 'saLiveLoad', 'ccCaseSync', 'cpOpenLinked', 'authStop', 'crEnsureRun', 'loadLiveData', 'resetLiveData'];
const valsFor = (user) => {
  const x = new HoistraLogic();
  QUIET.forEach((k) => { x[k] = () => {}; });
  configureAuth(x.authHooks());
  x.authEnter({ ok: true, user, tokens: { access_token: 'acc-1', refresh_token: 'ref-1' } });
  return x.renderVals();
};

beforeEach(() => { Object.keys(mem).forEach((k) => delete mem[k]); Object.keys(tabMem).forEach((k) => delete tabMem[k]); });

// ── Home.jsx, bundled and rendered ──────────────────────────────────────────────────────────
const here = path.dirname(fileURLToPath(import.meta.url));
const out = path.join(here, '.consultant-home.bundle.mjs');
const res = await build({
  stdin: { contents: "export { default as Home } from '../src/screens/Home.jsx';", resolveDir: here, loader: 'js' },
  bundle: true, write: false, format: 'esm', platform: 'node', jsx: 'automatic',
  external: ['react', 'react/*', 'react-dom', 'react-dom/*'], loader: { '.css': 'empty' }, logLevel: 'silent'
});
writeFileSync(out, res.outputFiles[0].text);
after(() => rmSync(out, { force: true }));
const { Home } = await import(pathToFileURL(out).href);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const words = (vals) => renderToStaticMarkup(React.createElement(Home, { vals }))
  .replace(/<[^>]+>/g, ' ').replace(/&amp;/g, '&').replace(/\s+/g, ' ');

const TILES = ['Hoist Score', 'Portfolio P&L', 'Hoist Crons', 'Platform value'];

// ── the flag ────────────────────────────────────────────────────────────────────────────────
test('a consultant gets the query-only Home', () => {
  assert.equal(valsFor(CONSULTANT).homeQueryOnly, true);
});

for (const [name, user] of [['an administrator', ADMIN], ['a plain user', USER], ['a user with another access role', ROLE_USER]]) {
  test(name + ' keeps the full Home', () => {
    assert.ok(!valsFor(user).homeQueryOnly);
  });
}

// ── what is drawn ───────────────────────────────────────────────────────────────────────────
test("a consultant's Home draws none of the four tiles", () => {
  const w = words(valsFor(CONSULTANT));
  for (const t of TILES) assert.ok(!w.includes(t), t + ' is still drawn');
});

test("a consultant's Home still has the question box", () => {
  const vals = valsFor(CONSULTANT);
  const html = renderToStaticMarkup(React.createElement(Home, { vals }));
  assert.ok(html.includes('placeholder="' + vals.homePh + '"'), 'no question box');
});

test("an administrator's Home still draws all four tiles", () => {
  const w = words(valsFor(ADMIN));
  for (const t of TILES) assert.ok(w.includes(t), t + ' is missing');
});
