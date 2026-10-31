// A session opened from the navigator's Sessions list opens in full (29 Oct 2026, "why is the
// orchestrator opening in left when i am clicking in sessions — it should always open in full").
// A conversation already opened the full chat page; a task (an orchestrator run raised from a
// page) reopened as the narrow side dock beside that page. The dock is the only thing that can
// draw a task — its steps, its flow, its follow-ups — so opened from the list it now fills the
// content area. A task raised by an action on a page keeps the dock beside the page, and leaving
// the page for another puts a full dock back beside it.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'buildings', role: 'admin', navOpen: true });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

function raisedTask() {
  c.orch('Hoist building', 'Buildings', []);
  const task = c.state.sessions.find((x) => x.kind === 'task');
  c.closeOrch();
  return task;
}

test('a task opened from the sessions list fills the content area', () => {
  const task = raisedTask();
  c.openSession(task.id);
  const v = c.renderVals();
  assert.equal(c.state.orchOpen, true);
  assert.equal(v.orchFull, true);
  assert.equal(v.orchWidth, 'calc(100vw - 248px)', 'the dock spans everything right of the navigator');
  assert.equal(v.shellPad, '248px', 'the page behind is not pushed over by a dock it sits under');
  assert.equal(v.orchResizeShow, 'none', 'no drag handle on a full dock');
  cleanup();
});

test('a task raised by an action on a page keeps the dock beside the page', () => {
  c.orch('Hoist building', 'Buildings', []);
  const v = c.renderVals();
  assert.equal(v.orchFull, false);
  assert.match(v.orchWidth, /^\d+px$/);
  assert.equal(v.orchResizeShow, 'block');
  cleanup();
});

test('leaving for another page, or closing it, puts a full dock back beside the page', () => {
  const task = raisedTask();
  c.openSession(task.id);
  c.setState({ view: 'cc' });
  assert.equal(c.renderVals().orchFull, false, 'another page shows the dock beside it');
  c.setState({ view: 'buildings' });
  c.closeOrch();
  c.setState({ orchOpen: true });  // the top-bar icon reopens the same task
  assert.equal(c.renderVals().orchFull, false, 'a dock reopened by the page is beside the page');
  cleanup();
});

test('an action on the page while the full dock is open opens its task beside the page', () => {
  const task = raisedTask();
  c.openSession(task.id);
  c.orch('Approve booking', 'Bishopsgate Tower', []);
  assert.equal(c.renderVals().orchFull, false);
  cleanup();
});
