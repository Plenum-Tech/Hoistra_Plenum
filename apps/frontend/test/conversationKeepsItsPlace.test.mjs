// The conversation keeps its place (2 Nov 2026, feedback "Preserve conversation position and move
// user control to the latest conversation end"):
//   - a question edited and run again is ASKED AGAIN AT THE END; the original question, its answer
//     and everything after it stay where they were (it used to cut the transcript at the edit);
//   - opening the chat never throws the reader to the top of the conversation (openChat called
//     window.scrollTo(0, 0), which also switched the follow off);
//   - opening or switching a conversation lands at its end, and that conversation's messages
//     re-engage the follow however many the last one had (useFollowBottom's `conversation`).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const scrolls = [];
globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: (...a) => scrolls.push(a), addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { complianceLiveMethods } = await import('../src/logic/complianceLive.js');
const { chatMethods } = await import('../src/logic/chat.js');
const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');

function host(state) {
  const h = {
    state: Object.assign({}, state),
    asked: [],
    setState(patch) {
      const p = typeof patch === 'function' ? patch(this.state) : patch;
      this.state = Object.assign({}, this.state, p);
    },
    ccAsk(q) { this.asked.push(q); this.state.ccChat = (this.state.ccChat || []).concat([{ role: 'you', text: q }]); }
  };
  return h;
}

const CHAT = [
  { role: 'you', text: 'Which certificates lapse this month?' },
  { role: 'ai', text: 'Two.', turnId: 't1' },
  { role: 'you', text: 'Which vendors are blocked?' },
  { role: 'ai', text: 'Three.', turnId: 't2' }
];

test('a question edited and run again is asked at the end; nothing before it is cut', async () => {
  const h = host({ ccChat: CHAT.slice(), ccEditIdx: 0, ccEditText: 'Which certificates lapse next month?' });
  complianceLiveMethods.ccEditRun.call(h);
  await new Promise((r) => setTimeout(r, 5));
  assert.deepEqual(h.asked, ['Which certificates lapse next month?']);
  assert.deepEqual(h.state.ccChat.slice(0, 4), CHAT, 'the original question, its answer and the later turns stay');
  assert.equal(h.state.ccChat.length, 5);
  assert.equal(h.state.ccChat[4].text, 'Which certificates lapse next month?');
  assert.equal(h.state.ccEditIdx, null);
  assert.equal(h.state.ccEditText, '');
});

test('an empty edit runs nothing and keeps the box open', () => {
  const h = host({ ccChat: CHAT.slice(), ccEditIdx: 2, ccEditText: '   ' });
  complianceLiveMethods.ccEditRun.call(h);
  assert.deepEqual(h.asked, []);
  assert.equal(h.state.ccEditIdx, 2);
  assert.equal(h.state.ccChat.length, 4);
});

test('opening the chat does not throw the reader to the top of the conversation', () => {
  const h = host({ view: 'home', ccChat: CHAT.slice() });
  h.chatConnect = () => {};
  scrolls.length = 0;
  chatMethods.openChat.call(h);
  assert.equal(h.state.view, 'chat');
  assert.deepEqual(scrolls, []);
  assert.doesNotMatch(src('logic/chat.js').slice(src('logic/chat.js').indexOf('openChat()'), src('logic/chat.js').indexOf('chatConnect();')), /scrollTo\(0, 0\)/);
});

test('the chat page follows per conversation: a new one lands at its end and its messages re-engage the follow', () => {
  const chat = src('screens/Chat.jsx');
  assert.match(chat, /useFollowBottom\(\{ container: null, end: endRef, busy: vals\.orchBusy, count: count, conversation: vals\.chatSessionId \}\)/);
  const vals = src('logic/renderVals.js');
  assert.match(vals, /chatSessionId: s\.sessionId \|\| ""/);
  const hook = src('components/shell/useFollowBottom.js');
  assert.match(hook, /export function useFollowBottom\(\{ container, end, busy, count, conversation \}\)/);
  // a changed conversation: follow again, count its messages afresh, and jump straight to its end
  assert.match(hook, /if \(conversation === lastConversation\.current\) return;/);
  assert.match(hook, /stuck\.current = true;\s*\n\s*lastCount\.current = -1;/);
  assert.match(hook, /if \(jumpRef\.current\) jumpRef\.current\(\);/);
  // the jump writes the bottom at once and is marked as ours, so its scroll event is not read as the reader's
  assert.match(hook, /jumpRef\.current = \(\) => \{ const m = read\(\); write\(Math\.max\(0, m\.max\)\); settleUntil = Date\.now\(\) \+ 1500; follow\(\); \};/);
  // and keeps to the end while a just-opened conversation's answers finish drawing
  assert.match(hook, /\|\| Date\.now\(\) < settleUntil\) raf = requestAnimationFrame\(tick\);/);
  // the dock passes no conversation: it behaves as before
  assert.doesNotMatch(src('components/shell/OrchestratorDock.jsx'), /conversation:/);
});

test('the edit box says what running it again does', () => {
  for (const file of ['screens/Chat.jsx', 'components/shell/OrchestratorDock.jsx']) {
    assert.match(src(file), /Runs as a new question at the end; this answer stays\./, file);
    assert.doesNotMatch(src(file), /Re-running replaces this question's answer/, file);
  }
});
