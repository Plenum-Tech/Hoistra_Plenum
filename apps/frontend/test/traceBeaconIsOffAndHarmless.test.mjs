// What the browser saw of an answer goes to Hoist Trace Lab (3 Nov 2026): the ONE approved exception to the eval golden
// rule (Hussain, 3 Nov 2026). It is off unless the build has VITE_TRACE_LAB=1; on, it sends one POST with keepalive and
// the bearer, after the caller has moved on; and nothing it does can reach the chat: a refused, failed or throwing send is
// dropped, a 401 calls no refresh and no sign-out, and nothing is retried.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdirSync, mkdtempSync, writeFileSync, rmSync, readFileSync } from 'node:fs';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = resolve(HERE, '..');
const CACHE = join(FRONTEND, 'node_modules', '.cache', 'beacon-tests');
const made = [];
after(() => made.splice(0).forEach((d) => rmSync(d, { recursive: true, force: true })));

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', href: 'http://test.local/', search: '' },
  addEventListener: () => {}, removeEventListener: () => {} };

// The beacon and the request client's auth hooks from ONE bundle, built as a VITE_TRACE_LAB=1 build sees it (or not).
async function load(flagOn) {
  const res = await build({
    stdin: { contents: "export * from './src/api/traceBeacon.js'; export { configureAuth } from './src/api/client.js';",
             resolveDir: FRONTEND, loader: 'js' },
    bundle: true, write: false, format: 'esm', platform: 'node', logLevel: 'silent',
    define: flagOn ? { 'import.meta.env': JSON.stringify({ VITE_TRACE_LAB: '1' }) } : {}
  });
  mkdirSync(CACHE, { recursive: true });
  const dir = mkdtempSync(join(CACHE, 'b-'));
  made.push(dir);
  const file = join(dir, 'bundle.mjs');
  writeFileSync(file, res.outputFiles[0].text);
  return import(pathToFileURL(file).href);
}

const tick = () => new Promise((r) => setTimeout(r, 5));
const EVT = { kind: 'answer_shown', turn_id: 'turn-1', session_id: 's-1', question: 'How many floors?', view: 'home',
  instance: 'comp', ms_total: 1200, ms_first_text: null, client_at: '2026-11-03T10:00:00.000Z' };

test('off by default: nothing is sent', async () => {
  const B = await load(false);
  B.configureAuth({ getToken: () => 'tok' });
  const sent = [];
  globalThis.fetch = async (...a) => { sent.push(a); return { ok: true }; };
  B.traceBeacon(EVT);
  await tick();
  assert.equal(sent.length, 0);
});

test('on: one POST to Trace Lab, keepalive, with the bearer, after the caller moved on', async () => {
  const B = await load(true);
  B.configureAuth({ getToken: () => 'tok-1' });
  const sent = [];
  globalThis.fetch = async (url, init) => { sent.push([url, init]); return { ok: true, status: 202 }; };
  const out = B.traceBeacon(EVT);
  assert.equal(out, undefined);
  assert.equal(sent.length, 0, 'nothing is sent while the caller is still running');
  await tick();
  assert.equal(sent.length, 1);
  const [url, init] = sent[0];
  assert.equal(url, '/backend/trace-lab/api/trace-lab/lab/frontend-events');
  assert.equal(init.method, 'POST');
  assert.equal(init.keepalive, true);
  assert.equal(init.headers.Authorization, 'Bearer tok-1');
  const body = JSON.parse(init.body);
  assert.equal(body.kind, 'answer_shown');
  assert.equal(body.turn_id, 'turn-1');
  assert.equal(body.ms_total, 1200);
});

test('a signed-out browser sends nothing', async () => {
  const B = await load(true);
  B.configureAuth({ getToken: () => null });
  const sent = [];
  globalThis.fetch = async (...a) => { sent.push(a); return { ok: true }; };
  B.traceBeacon(EVT);
  await tick();
  assert.equal(sent.length, 0);
});

test('a fetch that rejects or throws never reaches the caller', async () => {
  const B = await load(true);
  B.configureAuth({ getToken: () => 'tok' });
  const unhandled = [];
  const onUnhandled = (e) => unhandled.push(e);
  process.on('unhandledRejection', onUnhandled);
  try {
    globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
    assert.doesNotThrow(() => B.traceBeacon(EVT));
    globalThis.fetch = () => { throw new Error('boom'); };
    assert.doesNotThrow(() => B.traceBeacon(EVT));
    assert.doesNotThrow(() => B.traceBeacon(null));
    assert.doesNotThrow(() => B.traceBeacon({ kind: 'nonsense' }));
    await tick();
    await tick();
  } finally {
    process.off('unhandledRejection', onUnhandled);
  }
  assert.deepEqual(unhandled, []);
});

test('a 401 calls no refresh and no sign-out, and is not retried', async () => {
  const B = await load(true);
  let refreshed = 0, terminal = 0;
  B.configureAuth({ getToken: () => 'tok', refresh: async () => { refreshed += 1; return 'new'; },
    onTerminal: () => { terminal += 1; } });
  const sent = [];
  globalThis.fetch = async (url, init) => { sent.push(init); return { ok: false, status: 401, json: async () => ({ detail: { reason: 'invalid' } }) }; };
  B.traceBeacon(EVT);
  await tick();
  await tick();
  assert.equal(sent.length, 1);
  assert.equal(refreshed, 0);
  assert.equal(terminal, 0);
});

test('the event is cleaned: only known kinds, capped text, whole non-negative times', async () => {
  const B = await load(false);
  assert.equal(B.beaconBody({ kind: 'nonsense' }), null);
  const b = B.beaconBody({ kind: 'error', question: 'x'.repeat(5000), error: 'e'.repeat(900), ms_total: -4, ms_first_text: 12.7,
    turn_id: null, view: 'home', instance: 'ops' });
  assert.equal(b.question.length, 4000);
  assert.equal(b.error.length, 500);
  assert.equal(b.ms_total, null);
  assert.equal(b.ms_first_text, 13);
  assert.ok(b.client_at);
});

test('beaconOf reads the turn the chat just wrote: answer, stop or error', async () => {
  const B = await load(false);
  const t0 = Date.now() - 2000;
  const ok = B.beaconOf({ ccChat: [{ role: 'user', text: 'q' }, { role: 'bot', text: 'a', turnId: 'turn-9' }], sessionId: 's-9',
    view: 'home', instance: 'comp' }, 'q', 's-0', t0);
  assert.equal(ok.kind, 'answer_shown');
  assert.equal(ok.turn_id, 'turn-9');
  assert.equal(ok.session_id, 's-9');
  assert.ok(ok.ms_total >= 2000);
  const stop = B.beaconOf({ ccChat: [{ role: 'bot', stopped: true, text: 'Stopped' }] }, 'q', 's-0', t0);
  assert.equal(stop.kind, 'stopped');
  const err = B.beaconOf({ ccChat: [{ role: 'bot', error: true, text: 'Could not answer: timed out' }] }, 'q', 's-0', t0);
  assert.equal(err.kind, 'error');
  assert.equal(err.error, 'Could not answer: timed out');
  assert.equal(B.beaconOf({ ccChat: [{ role: 'user', text: 'q' }] }, 'q', 's', t0), null);
});

test('the chat calls it once, where a turn ends, and nowhere else in the app imports it', () => {
  const src = readFileSync(join(FRONTEND, 'src', 'logic', 'complianceLive.js'), 'utf8');
  assert.equal((src.match(/traceBeacon\(/g) || []).length, 1);
  assert.match(src, /finally \{[\s\S]{0,400}traceBeacon\(beaconOf\(this\.state, q, sid0, t0\)\)/);
  const beacon = readFileSync(join(FRONTEND, 'src', 'api', 'traceBeacon.js'), 'utf8');
  assert.equal(/from '\.\.\/tracelab/.test(beacon), false, 'the beacon does not import the Trace Lab module');
  assert.equal(/apiFetch|refresh|onTerminal/.test(beacon.replace(/\/\/.*$/gm, '')), false, 'no client retry or sign-out path');
});
