// The full-page conversation: a question asked from the home bar opens the chat view, not
// the side dock. The compliance console keeps its dock. Runs the real controller in Node
// with a stub window, so the orchestrator call fails and the failure lands in the transcript
// — which is itself the behaviour a dead backend should produce.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {} };
// Node ships fetch and WebSocket; without these stubs the controller would really dial
// test.local and each test would wait on the socket timeout.
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { domainOf, errorFromAnswer } = await import('../src/logic/chat.js');
const { loadSession, saveSession } = await import('../src/logic/session.js');

const settle = () => new Promise((r) => setTimeout(r, 30));
let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'home' });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

test('asking from home opens the chat view — the main orchestrator — and leaves the dock closed', async () => {
  await c.askScoped('What needs my approval today?');
  await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  assert.equal(c.state.ccChat[0].role, 'you');
  assert.equal(c.state.ccChat[0].text, 'What needs my approval today?');
  cleanup();
});

test('a question from the home bar while the dock is open still answers on the chat page, and the dock closes', async () => {
  c.setState({ orchOpen: true });
  await c.askScoped('Which vendors are blocked right now?');
  await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  assert.equal(c.state.ccChat[0].text, 'Which vendors are blocked right now?');
  cleanup();
});

test('a task session opened from the chat page goes back to its own page, and nothing on the chat page itself opens a dock beside it', async () => {
  c.setState({ view: 'buildings', role: 'admin' });
  c.orch('Update the graph', 'Buildings', []);
  const task = c.state.sessions.find((x) => x.kind === 'task');
  assert.equal(task.page, 'Buildings');
  c.closeOrch();
  c.setState({ view: 'home' });      // Home is not a dock view, so the next question is the chat page's
  await c.askScoped('hello'); await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  // Reopening the task from the sessions list returns to the page it was raised on — never
  // stacking the dock beside the chat page itself.
  c.openSession(task.id);
  assert.equal(c.state.view, 'buildings', 'the task reopens beside the page it was raised on');
  assert.equal(c.state.orchOpen, true);
  // Back on the chat page: the top-bar icon (openOrch) does not open a dock there — the page
  // already is the orchestrator — and a follow-up keeps it that way too.
  c.setState({ view: 'chat', orchOpen: false });
  c.renderVals().openOrch();
  assert.equal(c.state.orchOpen, false, 'the top-bar icon on the chat page does not open a dock');
  await c.askScoped('a follow-up'); await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  // On a dock page the dock still carries the conversation, exactly as before.
  c.setState({ view: 'cc', orchOpen: true });
  assert.equal(c.renderVals().orchChatShow, true);
  cleanup();
});

test('asking from a space opens the chat view and leaves the dock closed', async () => {
  c.setState({ view: 'space', spaceKey: 'compliance' });
  await c.askScoped('What needs my approval today?');
  await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  assert.equal(c.state.ccChat[0].text, 'What needs my approval today?');
  cleanup();
});

test('a dead orchestrator answers in the transcript, not with an exception', async () => {
  await c.askScoped('hi');
  await settle();
  const last = c.state.ccChat[c.state.ccChat.length - 1];
  assert.equal(last.role, 'bot');
  assert.equal(last.error, true);
  assert.match(last.text, /^Could not answer/);
  assert.equal(c.state.ccBusy, false);
  cleanup();
});

test('the question is recorded as a chat session the navigator can reopen', async () => {
  await c.askScoped('Which vendors are blocked right now?');
  await settle();
  const s = c.state.sessions[0];
  assert.equal(s.label, 'Which vendors are blocked right now?');
  assert.equal(s.kind, 'chat');
  assert.ok(s.at > 0);
  cleanup();
});

test('a follow-up from the chat view stays on the chat view', async () => {
  c.setState({ view: 'space', spaceKey: 'compliance' });
  await c.askScoped('first');
  await settle();
  assert.equal(c.state.view, 'chat');
  await c.askScoped('second');
  await settle();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.ccChat.filter((m) => m.role === 'you').length, 2);
  cleanup();
});

test('the compliance console still answers in its side dock', async () => {
  c.setState({ view: 'cc' });
  await c.askScoped('Which lapses void insurance?');
  await settle();
  assert.equal(c.state.view, 'cc');
  assert.equal(c.state.orchOpen, true);
  cleanup();
});

test('the vendors and buildings pages answer in their side dock too, with the page described to the orchestrator', async () => {
  for (const view of ['vp', 'buildings']) {
    c = new HoistraLogic();
    c.setState({ signedIn: true, view });
    await c.askScoped('What should I look at first?');
    await settle();
    assert.equal(c.state.view, view, view + ' stays on its page');
    assert.equal(c.state.orchOpen, true, view + ' opens the dock');
    assert.equal(c.state.ccChat[0].text, 'What should I look at first?');
    cleanup();
  }
  c.setState({ view: 'vp', ccChat: [], ccBusy: false, orchOpen: false });
  assert.match(c.chatContext(), /vendors page/);
  assert.match(c.chatContext(), /seed data/);                 // nothing loaded in the test
  c.setState({ view: 'buildings' });
  assert.match(c.chatContext(), /buildings page/);
  assert.match(c.chatContext(), /building table has not loaded/);
  const v = c.renderVals();
  assert.equal(v.orchPlaceholder, 'Ask anything about your buildings…');
  cleanup();
});

test('Run on an empty vendors bar opens the dock, not the chat page', () => {
  c.setState({ view: 'vp' });
  c.ccOpenChat();
  assert.equal(c.state.view, 'vp');
  assert.equal(c.state.orchOpen, true);
  cleanup();
});

test('Run on an empty home bar opens the chat view without asking anything', () => {
  c.ccOpenChat();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  assert.deepEqual(c.state.ccChat, []);
  cleanup();
});

test('Run on an empty space bar opens the chat view without asking anything', () => {
  c.setState({ view: 'space', spaceKey: 'compliance' });
  c.ccOpenChat();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false);
  assert.deepEqual(c.state.ccChat, []);
  cleanup();
});

test('new thread clears the transcript and stays on the chat page', async () => {
  await c.askScoped('hello');
  await settle();
  c.ccChatReset();
  assert.equal(c.state.view, 'chat');
  assert.equal(c.state.orchOpen, false, 'the main orchestrator stays the page; no dock');
  assert.deepEqual(c.state.ccChat, []);
  cleanup();
});

test('the view model exposes the chat page and its connection state', async () => {
  c.setState({ view: 'space', spaceKey: 'compliance' });
  await c.askScoped('hello');
  await settle();
  const v = c.renderVals();
  assert.equal(v.isChat, true);
  assert.equal(v.isHome, false);
  assert.equal(typeof v.chatLinkLabel, 'string');
  assert.ok(v.chatIntro.length > 20);
  assert.equal(v.orchChat.length, 2);
  assert.equal(v.orchChat[0].isYou, true);
  assert.equal(typeof v.orchChat[1].domain, 'string');
  cleanup();
});

test('sending while a turn is running is refused and the draft is kept', () => {
  c.setState({ view: 'chat', ccBusy: true, ccChat: [{ role: 'you', text: 'first' }], orchQuery: 'second' });
  c.orchSubmitNow();
  assert.equal(c.state.ccChat.length, 1);
  assert.equal(c.state.orchQuery, 'second');
  assert.match(c.state.toast, /still answering/i);
  cleanup();
});

test('an engine that answers with a JSON error is shown as an error, not as prose', () => {
  const raw = '{"error": "Error code: 401 - {\'error\': {\'message\': \'You do not have access to the organization tied to the API key.\'}}"}';
  assert.match(errorFromAnswer(raw), /^Error code: 401/);
  assert.equal(errorFromAnswer('Six vendors hold an expired accreditation.'), null);
  assert.equal(errorFromAnswer('{"ok": true, "count": 3}'), null);
  assert.equal(errorFromAnswer(''), null);
  assert.equal(errorFromAnswer({ error: 'boom' }), 'boom');
});

test('a reload onto the chat page re-checks the orchestrator connection', () => {
  c.setState({ view: 'chat' });
  assert.equal(c.state.chatLink, 'idle');
  // The page loads also fire here; they retry on a 30s timer, which would pin the test
  // process open. They have their own tests.
  c.ccLoad = async () => {};
  c.homeLoad = async () => {};
  c.vpLoad = async () => {};
  c.bldLoad = async () => {};
  c.gphLoad = async () => {};
  c.spLoad = async () => {};
  c.energyLoad = async () => {};
  c.enPositionLoad = async () => {};
  c.asLiveLoad = async () => {};
  c.mxLiveLoad = async () => {};
  c.usLiveLoad = async () => {};
  c.auLiveLoad = async () => {};
  c.componentDidMount();
  assert.notEqual(c.state.chatLink, 'idle');
  c.componentWillUnmount();
  cleanup();
});

test('the chat view survives a reload', () => {
  const store = {};
  globalThis.window.localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = v; },
    removeItem: (k) => { delete store[k]; }
  };
  saveSession({ signedIn: true, refreshToken: 'ref-test', email: 'a@b.c', role: 'user', view: 'chat' });
  assert.equal(loadSession().view, 'chat');
  delete globalThis.window.localStorage;
});

test('the domain label is read off the tools behind a reply', () => {
  assert.equal(domainOf(['compliance_response', 'compliance_pipeline']), 'Compliance');
  assert.equal(domainOf(['list_energy_anomalies']), 'Energy');
  assert.equal(domainOf(['get_vendor_scorecards']), 'Vendors');
  assert.equal(domainOf(['create_work_order']), 'Work orders');
  assert.equal(domainOf(['search_documents']), 'Documents');
  assert.equal(domainOf([]), 'Orchestrator');
  assert.equal(domainOf(undefined), 'Orchestrator');
});
