// What the transcript says when a question does not come back.
//
// On 17 Sep 2026 an ingest of a 16-page contract took longer than the browser's own 180s
// budget. The transcript read "svc-deepagents is not reachable at /backend/deep-agents.
// Start that service and give it an LLM key" — while the header beside it said
// "Connected · 172 tools", because the service was up the whole time. It was waiting on a
// Postgres lock; the reader was sent to restart a healthy service.
//
// A timeout and a dead upstream are different events with different next steps, and the
// timeout is the one where the work may still be finishing on the server — so the advice
// that matters is "do not upload it again until you have checked".
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const settle = () => new Promise((r) => setTimeout(r, 30));
let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'home' });
});
const cleanup = () => {
  clearInterval(c._orchTick);
  clearInterval(c._ccTick);
  clearTimeout(c._tt);
  clearTimeout(c._homeRetry);
  clearTimeout(c._ccRetry);
};

const lastBotText = () => {
  const m = (c.state.ccChat || []).filter((x) => x.role === 'bot');
  return (m[m.length - 1] || {}).text || '';
};

test('a timeout is not reported as an unreachable service', async () => {
  // apiFetch turns an AbortError into exactly this message; see src/api/client.js.
  globalThis.fetch = () => Promise.reject(new Error('timed out after 180s'));
  await c.askScoped('ingest this contract');
  await settle();
  const text = lastBotText();
  assert.match(text, /^Could not answer/);
  assert.doesNotMatch(
    text,
    /not reachable/i,
    'the service answered its health check; telling the reader to start it sends them after the wrong thing'
  );
  assert.doesNotMatch(text, /give it an LLM key/i);
  cleanup();
});

test('a timeout says the work may still be running, so the file is not uploaded twice', async () => {
  globalThis.fetch = () => Promise.reject(new Error('timed out after 180s'));
  await c.askScoped('ingest this contract');
  await settle();
  const text = lastBotText();
  assert.match(text, /timed out after 180s/, 'the underlying message is kept');
  assert.match(text, /still be (running|completing|finishing)/i);
  assert.match(text, /audit trail/i, 'names where to check before retrying');
  cleanup();
});

test('a genuinely dead upstream still says so', async () => {
  globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
  await c.askScoped('hello');
  await settle();
  const text = lastBotText();
  assert.match(text, /not reachable at \/backend\/deep-agents/);
  cleanup();
});

test('a gateway 502 still reads as unreachable', async () => {
  globalThis.fetch = () =>
    Promise.resolve({
      ok: false,
      status: 502,
      headers: { get: () => 'text/plain' },
      text: () => Promise.resolve('Bad Gateway'),
      json: () => Promise.resolve({}),
    });
  await c.askScoped('hello');
  await settle();
  assert.match(lastBotText(), /not reachable at \/backend\/deep-agents/);
  cleanup();
});
