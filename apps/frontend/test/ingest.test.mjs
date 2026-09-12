// Ingest documents — a real file upload against the orchestrator (deepAgentsApi.runStatefulWithFiles),
// not the decorative "Start ingestion" button this flow used to be. Two things this covers:
// starting a task (Hoist a building, or Ingest documents from the page) opens a fresh dock —
// any stale conversation is cleared, not shown underneath the new task — and the ingest card
// itself only ever fires a real POST when it actually holds a file and a chosen building.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let calls;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  calls.push(method + ' ' + u.pathname);
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  if (!h) throw new TypeError('Failed to fetch: no handler for ' + method + ' ' + u.pathname);
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  return {
    ok: status >= 200 && status < 300, status: status, statusText: String(status),
    text: async () => JSON.stringify(body)
  };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const RUN_WITH_FILES = 'POST /backend/deep-agents/api/workflow/run-stateful-with-files';

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'buildings', role: 'user', bldLive: [] });
});
const cleanup = () => { clearInterval(c._orchTick); clearInterval(c._ccTick); clearTimeout(c._tt); clearTimeout(c._bldRetry); if (c._ccAbort) c._ccAbort.abort(); };

const aFile = (name) => new File(['x'.repeat(10)], name, { type: 'application/pdf' });

// ── starting a task opens a fresh dock ───────────────────────────────────────

test('Hoist a building clears a stale conversation already in the dock', () => {
  c.setState({ orchOpen: true, ccChat: [{ role: 'you', text: 'an old question' }], ccBusy: false, sessionId: 'old-session' });
  c.renderVals().addBuilding();
  const v = c.renderVals();
  assert.deepEqual(v.orchChat, [], 'the old transcript is gone from the panel');
  assert.equal(c.state.sessionId, null, 'the next question starts a fresh thread, not the old one');
  assert.equal(v.bcOpen, true, 'the hoist card still opens as normal');
  cleanup();
});

test('editing a building also clears a stale conversation', () => {
  c.setState({ orchOpen: true, ccChat: [{ role: 'you', text: 'an old question' }] });
  c.bcOpenEdit({ buildingId: 'b-1', name: 'Bishopsgate Tower', updatedAt: null });
  assert.deepEqual(c.renderVals().orchChat, []);
  cleanup();
});

test('Ingest documents from the page also opens a fresh dock, with no building preselected', () => {
  c.setState({ orchOpen: true, ccChat: [{ role: 'you', text: 'an old question' }] });
  c.renderVals().ingestDocuments();
  const v = c.renderVals();
  assert.equal(c.state.flow, 'ingest');
  assert.equal(v.fIngest, true);
  assert.deepEqual(v.orchChat, []);
  assert.equal(v.iBuilding, '', 'nothing is guessed — the person picks one');
  cleanup();
});

// ── the ingest card is a real upload, not a canned message ───────────────────

test('starting ingestion with no file attached is refused, not silently accepted', () => {
  c.renderVals().ingestDocuments();
  c.setState({ declFor: 'Bishopsgate Tower' });
  const v = c.renderVals();
  assert.equal(v.iCanRun, false);
  v.iRun();
  assert.equal(calls.length, 0, 'nothing was sent');
  assert.equal(c.state.flow, 'ingest', 'the card stays open');
  assert.match(c.state.toast, /attach/i);
  cleanup();
});

test('starting ingestion with no building chosen is refused', () => {
  c.renderVals().ingestDocuments();
  c.renderVals().orchPickFiles({ target: { files: [aFile('EICR.pdf')], value: '' } });
  const v = c.renderVals();
  assert.equal(v.iCanRun, false);
  v.iRun();
  assert.equal(calls.length, 0);
  assert.match(c.state.toast, /building/i);
  cleanup();
});

test('starting ingestion with a file and a building sends a real request and closes the card', async () => {
  handlers[RUN_WITH_FILES] = (u, opts) => {
    assert.ok(opts.body instanceof FormData, 'a real multipart upload, not JSON');
    assert.match(opts.body.get('message'), /Bishopsgate Tower/);
    assert.equal(opts.body.getAll('files').length, 1);
    assert.equal(opts.body.get('files').name, 'EICR.pdf');
    return [200, { session_id: 's-1', answer: 'Queued EICR.pdf for indexing against Bishopsgate Tower.', tool_calls: [], success: true }];
  };
  c.renderVals().ingestDocuments();
  c.renderVals().setIBuilding({ target: { value: 'Bishopsgate Tower' } });
  c.renderVals().orchPickFiles({ target: { files: [aFile('EICR.pdf')], value: '' } });
  assert.equal(c.renderVals().iCanRun, true);
  c.renderVals().iRun();
  assert.equal(c.state.flow, null, 'the card closes immediately — the answer streams into the transcript instead');
  await new Promise((r) => setTimeout(r, 0));
  await new Promise((r) => setTimeout(r, 0));
  assert.deepEqual(calls, [RUN_WITH_FILES]);
  const v = c.renderVals();
  assert.ok(v.orchChat.some((m) => /Queued EICR\.pdf/.test(m.text)), 'the real answer landed in the transcript');
  cleanup();
});

test('the file tray is shared with the composer — attaching from either shows up in both', () => {
  c.setState({ ccFiles: [aFile('contract.pdf')] });
  const v = c.renderVals();
  assert.equal(v.orchFiles.length, 1);
  c.renderVals().ingestDocuments();
  assert.equal(c.renderVals().orchFiles.length, 1, 'the ingest card sees the same staged file');
  cleanup();
});
