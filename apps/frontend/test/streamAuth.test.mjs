// The ask bar's streaming socket carries the signed-in caller, like every other request.
//
// A browser WebSocket cannot take a header — the constructor accepts a URL and a list of
// subprotocols and nothing else — so the access token rides in the subprotocol list behind
// a marker, and svc-deepagents reads it back out of the handshake. Until it did, every
// streamed turn reached the backend anonymously: the agent's tool calls were refused for
// want of a bearer and the answer came back explaining it had no data.
//
// The token must NOT go in the query string: a URL is written to every proxy and access log
// between the browser and the service, and this one is a credential.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' } };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

const MARKER = 'hoistra.auth.bearer';
let dialled = [];

globalThis.WebSocket = class {
  constructor(url, protocols) {
    dialled.push({ url: String(url), protocols });
    this.readyState = 0;
  }
  send() {}
  close() {}
};

const { configureAuth } = await import('../src/api/client.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');

beforeEach(() => { dialled = []; });

test('the streaming socket offers the marker and the access token as its subprotocols', () => {
  configureAuth({ getToken: () => 'tok-abc123' });

  deepAgentsApi.stream('s-1', 'which certificates are lapsed', null, {});

  assert.equal(dialled.length, 1);
  assert.deepEqual(dialled[0].protocols, [MARKER, 'tok-abc123']);
});

test('the token stays out of the URL, where proxies and access logs would keep it', () => {
  configureAuth({ getToken: () => 'tok-abc123' });

  deepAgentsApi.stream('s-1', 'which certificates are lapsed', null, {});

  assert.ok(!dialled[0].url.includes('tok-abc123'), dialled[0].url);
  assert.ok(dialled[0].url.startsWith('ws://test.local/backend/deep-agents/api/workflow/ws/s-1'),
    dialled[0].url);
});

test('with no token yet, no socket is dialled and the caller is told, so it falls back to the POST', () => {
  // The access token is never persisted — it is rebuilt by authBoot()'s refresh — so the
  // very first moments of a session have none. A socket dialled then would be refused by
  // the server anyway; the non-streaming path refreshes and retries on its own.
  configureAuth({ getToken: () => null });
  let failed = null;

  const handle = deepAgentsApi.stream('s-1', 'which certificates are lapsed', null, {
    onError: (e) => { failed = e; }
  });

  assert.equal(dialled.length, 0);
  assert.ok(failed instanceof Error);
  assert.equal(typeof handle.close, 'function');
});
