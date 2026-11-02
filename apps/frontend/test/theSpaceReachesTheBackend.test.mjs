// The role space has to arrive with the question, on EVERY path that sends one.
//
// Reported 2 Nov 2026: in Ops Brain, "is the all compliance certificate on bishops gate tower
// building?" was routed to the compliance engine and answered with eight certificates. Ops Brain
// does not have compliance.
//
// Nothing was wrong with the gate. agents/spaces.py refuses compliance in ops, the router drops
// it from the catalogue, and all three backend routes read body.space into the active_space
// contextvar. The chat simply never sent it: deepAgents.stream() built its first frame from
// {message, context, orgOverride()} and left spaceField() off, so the server read space=None,
// engines_for(None) allowed every engine, and compliance answered.
//
// The backend test for this asserted the READER -- that the contextvar exists and the router
// consults it. A reader with no writer passes that test every time, which is how it shipped. So
// this test is on the senders, and it drives the real code with a fake socket rather than
// reading the source: a frame that does not carry the space fails it.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};

//: Every frame the code under test sends over a socket.
const SENT = [];
globalThis.WebSocket = class {
  constructor() { this.readyState = 1; setTimeout(() => { if (this.onopen) this.onopen(); }, 0); }
  send(text) { SENT.push(JSON.parse(text)); }
  close() {}
};

//: Every request body it sends over HTTP.
const POSTED = [];
globalThis.fetch = async (url, init) => {
  POSTED.push({ url: String(url), body: init && init.body ? JSON.parse(init.body) : null });
  const payload = JSON.stringify({ session_id: 's-1', answer: 'ok', tool_calls: [] });
  return { ok: true, status: 200, headers: { get: () => 'application/json' },
           text: async () => payload, json: async () => JSON.parse(payload) };
};

const { setActiveSpace, getActiveSpace, configureAuth } = await import('../src/api/client.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');

const settle = () => new Promise((r) => setTimeout(r, 5));

beforeEach(() => {
  SENT.length = 0;
  POSTED.length = 0;
  setActiveSpace(null);
  configureAuth({ getToken: () => 'test-token' });
});


// -- the reported failure ------------------------------------------------------------------

test('the streamed chat tells the server which space asked', async () => {
  setActiveSpace('ops');
  deepAgentsApi.stream('s-1', 'is the all compliance certificate on bishops gate tower building?', null, {});
  await settle();

  assert.equal(SENT.length, 1, 'the socket sent no opening frame');
  assert.equal(SENT[0].space, 'ops',
    'the question reached the server with no space, so every engine was allowed and compliance '
    + 'answered a question asked in Ops Brain');
});

test('a caller in no space still sends a question, and sends no space', async () => {
  // None means every module, which is right for the platform console and for anyone who has
  // not picked an instance. It must not become the string "null".
  deepAgentsApi.stream('s-2', 'what is open this week?', null, {});
  await settle();

  assert.equal(SENT.length, 1);
  assert.ok(!('space' in SENT[0]), 'sent space=' + JSON.stringify(SENT[0].space));
});

test('switching space changes what the next question carries', async () => {
  setActiveSpace('ops');
  deepAgentsApi.stream('s-3', 'first', null, {});
  await settle();
  setActiveSpace('comp');
  deepAgentsApi.stream('s-4', 'second', null, {});
  await settle();

  assert.deepEqual(SENT.map((f) => f.space), ['ops', 'comp']);
});


// -- the other ways a question is sent --------------------------------------------------------

test('the non-streaming fallback carries the space too', async () => {
  // The chat falls back to this whenever the socket cannot be dialled -- which is the normal
  // state for the first moments of a session. A fallback that drops the space would reopen the
  // same hole for exactly the questions asked soonest after signing in.
  setActiveSpace('ops');
  await deepAgentsApi.runStateful('is the fire risk assessment current?', 's-5', null);

  const asked = POSTED.filter((p) => p.body && p.body.message);
  assert.ok(asked.length >= 1, 'nothing was posted');
  assert.equal(asked[asked.length - 1].body.space, 'ops');
});


// -- the rule, so the next sender is not written without it -------------------------------------

test('every frame sent over a socket is built with spaceField', () => {
  // A structural guard beside the behavioural ones above: those cover the senders that exist
  // today, this one fails when a new sender is added without the space.
  const src = readFileSync(new URL('../src/api/deepAgents.js', import.meta.url), 'utf-8');
  const sends = src.split('\n').filter((l) => l.includes('ws.send(JSON.stringify('));
  assert.ok(sends.length >= 1, 'no socket sends found; this guard needs re-aiming');
  for (const line of sends) {
    assert.ok(line.includes('spaceField()'),
      'a socket frame is built without the active space: ' + line.trim());
  }
});

test('the space the controller sets is the space that gets sent', async () => {
  // instances.js and HoistraLogic.js both call setActiveSpace; this pins that the api layer
  // reads the same value rather than a copy of its own.
  setActiveSpace('assets');
  assert.equal(getActiveSpace(), 'assets');
  deepAgentsApi.stream('s-6', 'which assets are losing value?', null, {});
  await settle();
  assert.equal(SENT[0].space, 'assets');
});
