// Pressing an action opens a new conversation, not the last one.
//
// orch() made a session record and opened the dock, and left ccChat exactly as it was. So
// "Approve booking" on a lapsed fire-alarm certificate logged a fresh session in the
// sidebar and then rendered the PREVIOUS one underneath it — a completed migration and an
// unrelated question about an electricity meter, sitting above the approval that had just
// been asked for. Two sessions listed, one body, and nothing to say which reply belonged
// to which.
//
// It also has to drop sessionId: that is the orchestrator thread, and keeping it makes the
// next message a follow-up to whatever the last conversation was about.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { coreMethods } = await import('../src/logic/core.js');
// The real one, not a copy. orch() lives in core and ccChatReset in complianceLive; they
// only meet on the prototype, so a test that reimplemented the reset would keep passing
// after the real one stopped clearing something.
const { complianceLiveMethods } = await import('../src/logic/complianceLive.js');

// Enough of the store for orch() to run, recording what it set rather than rendering it.
function harness(state) {
  const ctx = {
    state: Object.assign({ sessions: [], account: { email: 'a@b.c' }, view: 'cc' }, state || {}),
    _ccAbort: null,
    setState(patch) {
      const next = typeof patch === 'function' ? patch(this.state) : patch;
      this.state = Object.assign({}, this.state, next);
    },
    ctxLabel: () => 'Compliance',
    // The console, vendors and buildings pages: the dock is the conversation surface.
    dockAnswers: () => true,
    ccChatReset: complianceLiveMethods.ccChatReset,
  };
  ctx.orch = coreMethods.orch.bind(ctx);
  ctx.ccChatReset = ctx.ccChatReset.bind(ctx);
  return ctx;
}

const PRIOR = [
  { role: 'you', text: 'Why is electricity meter NB-B-101-E0 spiking?' },
  { role: 'bot', text: 'Ingestion complete. Migration 530f1d20 …' },
];

test('an action clears the previous transcript', () => {
  const ctx = harness({ ccChat: PRIOR.slice(), sessionId: 'thread-from-the-migration' });
  ctx.orch('Approve booking', 'Fire Alarm System Service Certificate');
  clearInterval(ctx._orchTick);
  assert.deepEqual(ctx.state.ccChat, [],
    'the dock opened showing the last conversation under a new session heading');
});

test('an action starts a new orchestrator thread', () => {
  const ctx = harness({ ccChat: PRIOR.slice(), sessionId: 'thread-from-the-migration' });
  ctx.orch('Approve booking', 'Fire Alarm System Service Certificate');
  clearInterval(ctx._orchTick);
  assert.equal(ctx.state.sessionId, null,
    'keeping the thread makes the approval a follow-up to whatever came before it');
});

test('the action still opens the dock and records its own session', () => {
  const ctx = harness({ ccChat: PRIOR.slice() });
  ctx.orch('Approve booking', 'Fire Alarm System Service Certificate');
  clearInterval(ctx._orchTick);
  assert.equal(ctx.state.orchOpen, true);
  assert.equal(ctx.state.sessions.length, 1, 'the new task is recorded');
  assert.match(ctx.state.sessions[0].title, /Approve booking/);
});

test('a question that opens the dock is not itself cleared', () => {
  // ccAsk calls orch() BEFORE appending the reader's message, and only when the dock is
  // closed or the transcript already empty. The reset must therefore never be the thing
  // that eats the question — this pins that ordering.
  const ctx = harness({ ccChat: [] });
  ctx.orch('Why is this lapsed?', 'Compliance', [], { record: false });
  clearInterval(ctx._orchTick);
  ctx.setState((p) => ({ ccChat: (p.ccChat || []).concat([{ role: 'you', text: 'Why is this lapsed?' }]) }));
  assert.equal(ctx.state.ccChat.length, 1);
  assert.equal(ctx.state.ccChat[0].text, 'Why is this lapsed?');
  assert.equal(ctx.state.sessions.length, 0, 'record:false still suppresses the record');
});

test('an in-flight reply is abandoned rather than answering into the new session', () => {
  const ctx = harness({ ccChat: PRIOR.slice(), ccBusy: true });
  let aborted = 0;
  ctx._ccAbort = { abort() { aborted += 1; } };
  ctx.orch('Approve booking', 'Fire Alarm System Service Certificate');
  clearInterval(ctx._orchTick);
  assert.equal(aborted, 1);
  assert.equal(ctx.state.ccBusy, false);
});

test('a question continues the conversation it was asked in — transcript and thread kept', () => {
  // ccAsk reaches orch() with record:false when the dock is closed. Closing the dock does
  // not end the conversation (closeOrch leaves ccChat and sessionId alone), so a follow-up
  // typed afterwards must reopen it with its history and answer in the same thread — not
  // wipe six turns because the reader pressed × between them.
  const ctx = harness({ ccChat: PRIOR.slice(), sessionId: 'thread-from-the-migration' });
  ctx.orch('And the gas meter?', 'Compliance', [], { record: false });
  clearInterval(ctx._orchTick);
  assert.deepEqual(ctx.state.ccChat, PRIOR, 'a follow-up question wiped the transcript it follows');
  assert.equal(ctx.state.sessionId, 'thread-from-the-migration', 'a follow-up question dropped its thread');
});

test('an action while the dock is already open continues the conversation in it', () => {
  // The stale-transcript failure was a CLOSED dock opening onto the last conversation. A
  // dock that is open is a conversation the reader is in; an action from a row beside it
  // (refinement chip, cron action, "Request evidence") joins that conversation rather than
  // deleting it and killing whatever was still streaming.
  const ctx = harness({ ccChat: PRIOR.slice(), sessionId: 'thread-1', orchOpen: true, ccBusy: true });
  let aborted = 0;
  ctx._ccAbort = { abort() { aborted += 1; } };
  ctx.orch('Narrow to three worst buildings', 'Query');
  clearInterval(ctx._orchTick);
  assert.deepEqual(ctx.state.ccChat, PRIOR, 'an action inside an open dock wiped its transcript');
  assert.equal(ctx.state.sessionId, 'thread-1');
  assert.equal(aborted, 0, 'an action inside an open dock killed the reply being streamed');
});

test('on the chat page, where the conversation is the page, an action never wipes it', () => {
  const ctx = harness({ ccChat: PRIOR.slice(), sessionId: 'thread-1', view: 'chat' });
  ctx.dockAnswers = () => false;
  ctx.orch('Pin as weekly run', 'Query');
  clearInterval(ctx._orchTick);
  assert.deepEqual(ctx.state.ccChat, PRIOR, 'an action wiped the chat page');
  assert.equal(ctx.state.sessionId, 'thread-1');
});
