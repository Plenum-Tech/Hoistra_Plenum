// A staged document is sent and shown — two ways an attachment used to go nowhere.
//
//   ON HOME the tray that names the staged file was gated on orchAttachShow, which is
//   "is this the chat view". Home with the chat closed is not, so the file was staged, the
//   building card appeared under it, and its name was never drawn.
//
//   IN THE CHAT an empty send with a PDF or photo staged did nothing. Only a spreadsheet
//   was treated as an instruction on its own; the Home bar already sent a bare document as
//   "Validate and ingest this document.", the composer did not, so every document after the
//   first sat in the tray with a send button that did nothing.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let calls, handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  calls.push({ key: key, body: opts && opts.body });
  const h = handlers[key];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const out = typeof h === 'function' ? h(opts) : h;
  return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const RUN_FILES = 'POST /backend/deep-agents/api/workflow/run-stateful-with-files';
const settle = () => new Promise((r) => setTimeout(r, 40));
const pdf = (name) => new File(['%PDF-1.4'], name, { type: 'application/pdf' });

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = []; handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
});
afterEach(() => {
  clearTimeout(c._mgTimer); clearTimeout(c._tt);
  clearInterval(c._orchTick); clearInterval(c._ccTick);
});

test('Home with the chat closed names the staged document', () => {
  c.setState({ view: 'home', orchOpen: false });
  c.ccAddFiles([pdf('gas-safety.pdf')]);
  const v = c.renderVals();
  assert.equal(v.orchFiles[0].name, 'gas-safety.pdf');
  assert.equal(v.orchTrayShow, 'flex', 'the tray that carries the name is drawn');
});

test('the tray stays hidden when nothing is staged', () => {
  c.setState({ view: 'home', orchOpen: false });
  assert.equal(c.renderVals().orchTrayShow, 'none');
});

test('an empty send in the chat with a document staged ingests it', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Filed.', tool_calls: [], success: true };
  c.setState({ view: 'chat' });
  c.ccAddFiles([pdf('second.pdf')]);
  await c.orchSubmitNow();
  await settle();
  assert.ok(calls.some((k) => k.key === RUN_FILES), 'the upload route was called');
  const asked = c.state.ccChat.find((m) => m.role === 'you');
  assert.equal(asked.text, 'Validate and ingest this document.');
  assert.deepEqual(asked.files, ['second.pdf']);
  assert.deepEqual(c.state.ccFiles, [], 'the tray is cleared once sent');
});

test('a second document after the first has finished goes too', async () => {
  handlers[RUN_FILES] = { session_id: 's1', answer: 'Filed.', tool_calls: [], success: true };
  c.setState({ view: 'chat' });
  c.ccAddFiles([pdf('first.pdf')]);
  await c.orchSubmitNow();
  await settle();
  assert.equal(c.state.ccBusy, false, 'the first turn is done');
  c.ccAddFiles([pdf('second.pdf')]);
  await c.orchSubmitNow();
  await settle();
  assert.equal(calls.filter((k) => k.key === RUN_FILES).length, 2);
  assert.deepEqual(c.state.ccChat.filter((m) => m.role === 'you').map((m) => m.files[0]), ['first.pdf', 'second.pdf']);
});

test('an empty send with nothing staged is still nothing', async () => {
  c.setState({ view: 'chat' });
  await c.orchSubmitNow();
  assert.equal(calls.length, 0);
  assert.equal((c.state.ccChat || []).length, 0);
});
