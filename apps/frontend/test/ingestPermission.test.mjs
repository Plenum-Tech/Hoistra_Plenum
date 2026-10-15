// ingestPermission — the "Can ingest" toggle on Users & access, honoured by the screens.
//
// The toggle wrote `can_ingest` on the account and the page never read it back for the
// person signed in: `can_ingest` appears in the frontend only inside the admin's own user
// list, as a column about OTHER people. So an account with the toggle off still saw every
// ingest affordance — the Ingest chip on Home, the paperclip in both composers, the
// dropzone in the orchestrator's Ingest documents card — and used them.
//
// GET /api/auth/me has always returned the flag ("what the client needs to shape itself:
// whether this person may ingest"); authLoadScope() was dropping it on the floor beside
// the building scope it does keep. These pin the flag from that read to one `canIngest`
// that every surface hides behind.
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
  const [status, body] = typeof h === 'function' ? await h(u, opts) : h;
  return { ok: status >= 200 && status < 300, status, statusText: String(status),
           text: async () => JSON.stringify(body) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const ME = '/backend/ops-intelligence/api/auth/me';
const ACCOUNT = { id: 'u1', email: 'df@example.com', full_name: 'DF', role: 'user',
                  organization_id: 'o1' };

// One file, as an <input type="file"> hands it over.
const FILES = [{ name: 'contract.pdf', size: 1024, type: 'application/pdf' }];
const pick = (v) => v.orchPickFiles({ target: { files: FILES, value: 'x' } });

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  calls = [];
  handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'user', account: ACCOUNT });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); };

// ── the flag has to arrive at all ──

test('GET /me’s can_ingest lands on the signed-in account, beside the building scope', async () => {
  handlers[ME] = [200, { ok: true, user: { ...ACCOUNT, can_ingest: false,
                                           building_ids: ['b1'], all_buildings: false } }];
  await c.authLoadScope();
  assert.equal(c.state.account.can_ingest, false);
  assert.deepEqual(c.state.account.building_ids, ['b1'], 'the scope it already kept still lands');
  cleanup();
});

test('a later read turning it back on is picked up the same way', async () => {
  handlers[ME] = [200, { ok: true, user: { ...ACCOUNT, can_ingest: false } }];
  await c.authLoadScope();
  assert.equal(c.renderVals().canIngest, false);
  handlers[ME] = [200, { ok: true, user: { ...ACCOUNT, can_ingest: true } }];
  await c.authLoadScope();
  assert.equal(c.renderVals().canIngest, true, 'switching the toggle back on brings it back');
  cleanup();
});

// ── what the screens read ──

test('canIngest follows the account’s flag', () => {
  c.setState({ account: { ...ACCOUNT, can_ingest: false } });
  assert.equal(c.renderVals().canIngest, false);
  c.setState({ account: { ...ACCOUNT, can_ingest: true } });
  assert.equal(c.renderVals().canIngest, true);
  cleanup();
});

test('an admin follows the same flag, because the server has already folded the role into it', () => {
  // tokens.py: can_ingest = row.can_ingest OR role in (admin, superadmin). The page must
  // not OR the role in a SECOND time — that would let an admin who was explicitly turned
  // off keep the affordance the server would honour.
  c.setState({ role: 'admin', account: { ...ACCOUNT, role: 'admin', can_ingest: false } });
  assert.equal(c.renderVals().canIngest, false);
  cleanup();
});

test('before anything has said, the affordance stays — an unanswered /me is not a No', () => {
  // authLoadScope() is best-effort and silent. Reading "not yet known" as "not allowed"
  // would blink the control off on every sign-in, and hide it for good whenever that read
  // fails, for people the server would let ingest.
  c.setState({ account: { ...ACCOUNT } });          // no can_ingest key at all
  assert.equal(c.renderVals().canIngest, true);
  cleanup();
});

test('signed in with no account object yet is also not a No', () => {
  // The shell renders between sign-in and the account landing in state, and several of
  // this suite's own harnesses set signedIn without one. Same rule as a missing key.
  c.setState({ account: null });
  assert.equal(c.renderVals().canIngest, true);
  cleanup();
});

test('signed out, there is nothing to ingest with', () => {
  c.setState({ signedIn: false, account: null });
  assert.equal(c.renderVals().canIngest, false);
  cleanup();
});

test('the dock composer’s attach button needs both the chat view and the permission', () => {
  c.setState({ view: 'chat', account: { ...ACCOUNT, can_ingest: true } });
  assert.equal(c.renderVals().orchAttachPickShow, 'flex');
  c.setState({ account: { ...ACCOUNT, can_ingest: false } });
  assert.equal(c.renderVals().orchAttachPickShow, 'none');
  // The tray of already-staged files is a different question and keeps its own flag.
  assert.equal(c.renderVals().orchAttachShow, 'flex');
  cleanup();
});

test('the dock’s Ingest documents card does not come back after a reload', () => {
  // `flow` survives a reload, so an account whose toggle was turned off while the card was
  // open would otherwise find it waiting for them — a building picker with nothing to
  // attach beneath it.
  c.setState({ flow: 'ingest', account: { ...ACCOUNT, can_ingest: true } });
  assert.equal(c.renderVals().fIngest, true);
  c.setState({ account: { ...ACCOUNT, can_ingest: false } });
  assert.equal(c.renderVals().fIngest, false);
  cleanup();
});

test('the ingestion agent modal cannot be opened at all', () => {
  c.setState({ account: { ...ACCOUNT, can_ingest: false } });
  c.ingStart();
  assert.notEqual(c.state.ingOn, true, 'the modal stays shut');
  c.setState({ account: { ...ACCOUNT, can_ingest: true } });
  c.ingStart();
  assert.equal(c.state.ingOn, true, 'and opens again once the toggle is back on');
  clearInterval(c._ingT);
  cleanup();
});

// ── and the handler behind them ──

test('with the toggle off no file can be staged, even if a control is still on screen', () => {
  c.setState({ account: { ...ACCOUNT, can_ingest: false } });
  pick(c.renderVals());
  assert.equal((c.state.ccFiles || []).length, 0, 'nothing staged');
  cleanup();
});

test('with the toggle on a file stages as it always did', () => {
  c.setState({ account: { ...ACCOUNT, can_ingest: true } });
  pick(c.renderVals());
  assert.equal((c.state.ccFiles || []).length, 1);
  cleanup();
});
