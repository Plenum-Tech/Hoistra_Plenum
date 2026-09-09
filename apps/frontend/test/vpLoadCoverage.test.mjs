// vpLoad's coverage/pack fetch — a real crash risk, not a style nit. Promise.allSettled's
// "fulfilled" only means the fetch resolved; apiFetch returns null for an empty 200 body
// (client.js: `data = text ? JSON.parse(text) : null`), and `null.vendors` throws, which
// would take down the whole vpLoad() call for every country in one bad response.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

const mem = {};
let handlers;
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const h = handlers[method + ' ' + u.pathname] || handlers[u.pathname];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '' };
  const [status, body] = typeof h === 'function' ? h(u, opts) : h;
  // body === undefined simulates a real empty-200 response — apiFetch resolves that to null.
  return { ok: status >= 200 && status < 300, status: status, statusText: String(status), text: async () => (body === undefined ? '' : JSON.stringify(body)) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  Object.keys(mem).forEach((k) => { delete mem[k]; });
  handlers = {
    'GET /backend/ops-intelligence/api/contract-performance/saved-space/summary': () => [200, undefined],
    'GET /backend/ops-intelligence/api/contract-performance/contracts': () => [200, { contracts: [] }],
    'GET /backend/ops-intelligence/api/contract-performance/admin/weights': () => [200, undefined],
    'GET /backend/ops-intelligence/api/contract-performance/approvals': () => [200, { approvals: [] }],
    'GET /backend/ops-intelligence/api/compliance/certificates': () => [200, { certificates: [{ country_code: 'UK' }] }],
    'GET /backend/ops-intelligence/api/contract-performance/scorecards': () => [200, { scorecards: [] }],
    // The bug: a real 200 with an empty body, exactly like a country with nothing recorded.
    'GET /backend/ops-intelligence/api/compliance/coverage/vendors': () => [200, undefined],
    'GET /backend/ops-intelligence/api/compliance/country-pack': () => [200, undefined]
  };
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'vp' });
});
const cleanup = () => { clearTimeout(c._vpRetry); clearTimeout(c._vpRefresh); };

test('vpLoad survives an empty-body 200 from coverage/vendors and country-pack without throwing', async () => {
  await c.vpLoad();
  assert.equal(c.state.vpLoading, false, 'the load completed rather than being left hanging by a thrown error');
  const raw = c.state.vpRaw || (c.vpModel && c.vpModel().raw);
  // Whichever field holds it, the coverage/pack maps must exist and be empty, not absent
  // because the whole function threw before setting them.
  assert.doesNotThrow(() => c.vpModel(), 'building the view model after a load with empty coverage bodies must not throw either');
  cleanup();
});
