// A question asked from the page opens a new session (Hussain, 2 Nov 2026: "make sure to open a
// new session when question asked").
//
// The page's ask bar, its suggested-question chips and the Home bar ran through askScoped → ccAsk,
// which appends to whatever conversation the dock last held — even one closed with ×, since
// closing the dock keeps ccChat and sessionId. So "What certificates does Bishopsgate Tower
// hold?" asked from the Compliance page landed under the previous question's answers, in the
// previous session, on the previous orchestrator thread. A page question now starts clean
// (askNew): a new session record, an empty transcript, no thread, and no draft, form, held
// document or migration card left over from the conversation being left. The conversation's own
// composer and the buttons drawn inside an answer still carry the conversation on.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470 };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const PRIOR = [
  { role: 'you', text: 'Which vendors are blocked right now?' },
  { role: 'bot', text: 'Two vendors are blocked: …' },
];

let c, asked;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({
    signedIn: true, view: 'cc', role: 'admin', navOpen: true,
    orchOpen: true, sessionId: 'old-thread', ccChat: PRIOR.slice(), ccBusy: false,
    flow: 'booking', flowDone: 'Booked', ccCaseId: 'case-1', ccCaseDoc: 'eicr.pdf',
  });
  asked = [];
  // The turn itself is not under test: record what the conversation looked like when it began.
  c.ccAsk = function (q) {
    asked.push({ q, sessionId: this.state.sessionId, chat: this.state.ccChat, flow: this.state.flow, caseId: this.state.ccCaseId });
  };
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

function assertFresh(q) {
  assert.equal(asked.length, 1, 'the question was asked once');
  assert.equal(asked[0].q, q);
  assert.equal(asked[0].sessionId, null, 'no orchestrator thread carried over: sessionEnsure makes a new session');
  assert.deepEqual(asked[0].chat, [], 'the last conversation is not drawn above it');
  assert.equal(asked[0].flow, null, 'a draft or form belongs to the conversation being left');
  assert.equal(asked[0].caseId, null, 'a held document does not take the new question as its reason');
}

test("the page's ask bar opens a new session", () => {
  c.setState({ pq: 'What certificates does Bishopsgate Tower hold?' });
  c.renderVals().pqRun();
  assertFresh('What certificates does Bishopsgate Tower hold?');
  cleanup();
});

test("Enter in the page's ask bar opens a new session", () => {
  c.setState({ pq: 'What certificates does Bishopsgate Tower hold?' });
  c.renderVals().pqKey({ key: 'Enter', shiftKey: false, preventDefault() {} });
  assertFresh('What certificates does Bishopsgate Tower hold?');
  cleanup();
});

test("a suggested-question chip opens a new session", () => {
  const chip = c.renderVals().abChips[0];
  chip.run();
  assertFresh(chip.label);
  cleanup();
});

test('the Home bar opens a new session', () => {
  c.setState({ view: 'home', orchOpen: false, query: 'How many floors in Bishopsgate Tower?' });
  c.renderVals().runQuery();
  assertFresh('How many floors in Bishopsgate Tower?');
  cleanup();
});

test("a space's ask bar opens a new session", () => {
  c.setState({ view: 'space', spaceKey: 'sp-hus', spaceAsk: 'What lapsed this month?' });
  c.spaceAskRun();
  assertFresh('What lapsed this month?');
  cleanup();
});

test('the conversation\'s own composer is a follow-up in the same session', () => {
  c.setState({ orchQuery: 'And which of those have lapsed?' });
  c.orchSubmitNow();
  assert.equal(asked.length, 1);
  assert.equal(asked[0].sessionId, 'old-thread');
  assert.equal(asked[0].chat.length, 2, 'the conversation it follows stays on screen');
  cleanup();
});

test('a button drawn inside an answer asks within the conversation', () => {
  c.renderVals().openuiHost.ask('Show me the lapsed ones');
  assert.equal(asked.length, 1);
  assert.equal(asked[0].sessionId, 'old-thread');
  cleanup();
});

test('while an answer is still being written a page question waits, and nothing is reset', () => {
  c.setState({ ccBusy: true, pq: 'What certificates does Bishopsgate Tower hold?' });
  c.renderVals().pqRun();
  assert.equal(asked.length, 0, 'the turn in flight is not abandoned by a question from the page');
  assert.equal(c.state.sessionId, 'old-thread');
  assert.equal(c.state.ccChat.length, 2);
  assert.match(c.state.toast, /Still answering/);
  cleanup();
});
