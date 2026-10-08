// A run that has not finished is read again when the Orchestrator comes back to it.
//
// The panel sat on "Running · Node 8 of 9" for a run the database had recorded as complete ten
// minutes earlier. Only the Refresh button escaped it.
//
// Polling is scheduled by the poll before it, and only while the Orchestrator is the view —
// `if (delay && this.state.view === 'chat')` on success, the same on the error path. The chain
// therefore breaks whenever a poll lands while the person is elsewhere, and on 24 Sep 2026 it
// broke because two polls in a row hit the 60-second timeout while the last node held the
// event loop.
//
// Coming back was meant to restart it, and could not: the resume fired only when the run had
// no document at all, and a run being followed always has one. With a stale document in hand
// the page had a status, so it asked for nothing, and the status it had was the last one
// before the chain broke.
//
// The condition is the run's state, not whether a document exists.
import { test } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = {
  location: { origin: 'http://test.local', href: 'http://test.local/', search: '' },
  scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} },
};
globalThis.fetch = () => Promise.reject(new TypeError('no network in this test'));

const { chatMethods } = await import('../src/logic/chat.js');
const { isTerminal, TERMINAL } = await import('../src/logic/migration.js');

// The shell openChat() touches, with the two things it calls stubbed out.
function shell(mgId, mgStatus) {
  const polled = [];
  const ctx = {
    state: { view: 'buildings', mgId, mgStatus },
    setState(patch) { Object.assign(this.state, typeof patch === 'function' ? patch(this.state) : patch); },
    chatConnect() {},
    mgPoll(immediate) { polled.push(immediate); },
    openChat: chatMethods.openChat,
  };
  return { ctx, polled };
}

test('a run still running is read again on arrival, even though a document is already held', () => {
  const { ctx, polled } = shell('m-1', { status: 'running', current_step: 8 });
  ctx.openChat();
  assert.deepEqual(polled, [true], 'the stale document is exactly what has to be replaced');
});

test('a run awaiting a gate is read again too', () => {
  const { ctx, polled } = shell('m-1', { status: 'awaiting_review' });
  ctx.openChat();
  assert.deepEqual(polled, [true]);
});

test('a run restored from the session with no document is still read — the original case', () => {
  const { ctx, polled } = shell('m-1', null);
  ctx.openChat();
  assert.deepEqual(polled, [true]);
});

test('a finished run is not re-read: its document is the last word', () => {
  for (const status of TERMINAL) {
    const { ctx, polled } = shell('m-1', { status });
    ctx.openChat();
    assert.deepEqual(polled, [], `${status} is terminal and needs no further read`);
  }
});

test('no open run means nothing to read', () => {
  const { ctx, polled } = shell(null, null);
  ctx.openChat();
  assert.deepEqual(polled, []);
});

test('openChat still does the rest of its job', () => {
  const { ctx } = shell('m-1', { status: 'running' });
  ctx.state.orchOpen = true; ctx.state.detail = { a: 1 };
  ctx.openChat();
  assert.equal(ctx.state.view, 'chat');
  assert.equal(ctx.state.orchOpen, false);
  assert.equal(ctx.state.detail, null);
});

test('isTerminal reads a missing status as not finished', () => {
  // openChat passes `mgStatus && mgStatus.status`, which is null when no document is held.
  assert.equal(isTerminal(null), false);
  assert.equal(isTerminal(undefined), false);
  assert.equal(isTerminal('complete'), true);
});
