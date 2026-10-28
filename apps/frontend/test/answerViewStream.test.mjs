// answerViewStream — the turn after its answer: the layout that arrives on the same socket.
//
// svc-deepagents (orchestrator.stream, 27 Oct 2026) sends the finished answer as
// workflow_completed the moment the turn has recorded it, and only then lays the answer out:
// a `reasoning` {label: 'Layout'} event, then one `answer_view` {view, cost}, then it closes the
// socket. The answer must show and the composer unlock at the completion, as before; the layout
// is applied afterwards to THAT message, wherever the reader has gone since, and kept on the
// session record so a reopened conversation draws it the same way.
//
// Also the turn cut short before its completion (findings 21 and 22): a Stop or a dropped
// socket after the answer had streamed keeps what had arrived, marked as unfinished, instead of
// "Stopped before the orchestrator answered." or "The orchestrator returned an empty answer."
//
// The real controller in Node, with a WebSocket the test drives event by event.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
// The non-streaming fallback's POST. Replaced per test where the answer comes that way.
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

const sockets = [];
// A socket the test plays the server for. `refuse` makes the next one fail before it opens,
// which is how a refused upgrade looks to the browser.
let refuse = false;
class FakeSocket {
  constructor(url, protocols) {
    this.url = String(url); this.protocols = protocols; this.closed = false; this.sent = [];
    sockets.push(this);
    const fail = refuse;
    setTimeout(() => {
      if (fail) { this.closed = true; if (this.onerror) this.onerror({}); if (this.onclose) this.onclose(); return; }
      if (this.onopen) this.onopen();
    }, 0);
  }
  send(raw) { this.sent.push(raw); }
  // The server's side: an event, delivered only while the socket is open, as a real one is.
  emit(e) { if (!this.closed && this.onmessage) this.onmessage({ data: JSON.stringify(e) }); }
  serverClose() { if (this.closed) return; this.closed = true; if (this.onclose) this.onclose(); }
  close() {
    if (this.closed) return;
    this.closed = true; this.closedByClient = true;
    setTimeout(() => { if (this.onclose) this.onclose(); }, 0);
  }
}
globalThis.WebSocket = FakeSocket;

const { configureAuth } = await import('../src/api/client.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { costSummary } = await import('../src/logic/complianceLive.js');

const SESSION = 'sess-layout-1';
const ANSWER = 'Apex Mechanical is slipping most on SLA in September 2026: response fell 0.73 points to 23.44/25.';
const PROGRAM = 'root = Answer([h])\nh = Headline("Apex Mechanical is slipping most on SLA")';
const COST1 = { calls: 2, usd: 0.05, usd_complete: true, wall_ms: 30000, by_role: { analyst: 0.04, plan: 0.01 }, models: ['gpt-5.6-terra'] };
const COST2 = { calls: 3, usd: 0.06, usd_complete: true, wall_ms: 34000, by_role: { analyst: 0.04, plan: 0.01, presenter: 0.01 }, models: ['gpt-5.6-terra'] };
// What the vendor engine attaches: its tools, the pipeline panel (with the cost so far) and the
// engine wrapper.
const VENDOR_CALLS = [
  { tool: 'get_vendor_scorecards', input: {}, output: {} },
  { tool: 'compliance_pipeline', input: {}, output: { engine: 'contract_performance', steps: [{ stage: 'route', label: 'Routed to vendors' }], cost: COST1 } },
  { tool: 'phase2_engine:contract_performance', input: {}, output: {} }
];

const tick = (ms = 5) => new Promise((r) => setTimeout(r, ms));
let c;
beforeEach(() => {
  configureAuth({ getToken: () => 'tok-abc123' });
  sockets.length = 0;
  refuse = false;
  globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'chat', sessionId: SESSION });
});
afterEach(() => {
  clearInterval(c._ccTick); clearInterval(c._orchTick); clearTimeout(c._ccRetry);
  // Any layout still waiting is let go, so its 60 s timer does not hold the run open.
  if (typeof c.ccLayoutStopAll === 'function') c.ccLayoutStopAll();
});

const bots = () => (c.state.ccChat || []).filter((m) => m.role === 'bot');
const lastBot = () => bots().pop();
const record = (id) => (c.state.sessions || []).find((x) => x.id === id);
const rowFor = (m) => c.renderVals().orchChat.find((r) => r.key === c.state.ccChat.indexOf(m));

// Asks, streams a few words, completes. Returns the socket, still open for the layout.
async function askAndComplete(q, opts = {}) {
  const answer = opts.answer || ANSWER;
  const asked = c.ccAsk(q);
  await tick();
  const ws = sockets[sockets.length - 1];
  ws.emit({ type: 'reasoning', label: 'Domain routing', text: 'Read the question', domain: 'contract_performance' });
  ws.emit({ type: 'answer_delta', text: answer.slice(0, 30) });
  ws.emit({ type: 'answer_delta', text: answer.slice(30) });
  ws.emit({ type: 'workflow_completed', session_id: SESSION, answer: answer, tool_calls: opts.calls || VENDOR_CALLS });
  await asked;
  return ws;
}

test('the answer lands at workflow_completed and the socket stays open for its layout', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  assert.equal(c.state.ccBusy, false, 'the composer unlocks at the completion');
  assert.equal(lastBot().text, ANSWER);
  assert.equal(ws.closed, false, 'the completion no longer closes the socket: the layout comes on it');
  assert.ok(lastBot().mid, 'the finished message carries a stable id the layout is applied by');
});

test('the Layout reasoning after the completion shows a quiet cue on that message, not a live turn', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  assert.equal(c.state.ccBusy, false, 'the layout never locks the composer');
  assert.equal(c.state.ccStream, null, 'an event after the completion must not paint a live turn back');
  assert.equal(rowFor(lastBot()).layingOut, true);
  // Every other message has no cue.
  const you = c.renderVals().orchChat.find((r) => r.isYou);
  assert.equal(!!you.layingOut, false);
});

test('answer_view draws the view on that message, clears the cue, closes the socket and is kept on the record', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  ws.emit({ type: 'answer_view', session_id: SESSION, view: { v: 1, lang: PROGRAM, tail: '_Checked against the scorecards._' }, cost: null });
  await tick();
  const bot = lastBot();
  assert.deepEqual(bot.view, { lang: PROGRAM, tail: '_Checked against the scorecards._' });
  assert.equal(bot.text, ANSWER, 'the answer itself is never replaced by its layout');
  const row = rowFor(bot);
  assert.equal(row.view, PROGRAM);
  assert.equal(row.viewTail, '_Checked against the scorecards._');
  assert.equal(row.layingOut, false);
  assert.equal(ws.closed, true);
  const kept = record(SESSION).turns.find((m) => m.mid === bot.mid);
  assert.deepEqual(kept.view, { lang: PROGRAM, tail: '_Checked against the scorecards._' }, 'reopening the session must draw the same view');
});

test('answer_view cost replaces the cost on that message\'s pipeline panel, so the card counts the layout call', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  assert.equal(costSummary(lastBot().rich.cost).calls, 2);
  ws.emit({ type: 'answer_view', session_id: SESSION, view: null, cost: COST2 });
  await tick();
  const bot = lastBot();
  assert.deepEqual(bot.rich.cost, COST2);
  assert.equal(costSummary(rowFor(bot).rich.cost).calls, 3);
  assert.equal(bot.view, null, 'view null: the answer stays as drawn');
  assert.deepEqual(record(SESSION).turns.find((m) => m.mid === bot.mid).rich.cost, COST2);
});

test('view null and no cost leaves the answer exactly as drawn, and still ends the wait', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  const before = lastBot();
  ws.emit({ type: 'answer_view', session_id: SESSION, view: null, cost: null });
  await tick();
  assert.equal(lastBot(), before, 'nothing to apply: the message is untouched');
  assert.equal(rowFor(before).layingOut, false);
  assert.equal(ws.closed, true);
});

test('a malformed view is no view: the answer keeps drawing as it was', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'answer_view', session_id: SESSION, view: { v: 2, lang: PROGRAM }, cost: null });
  await tick();
  assert.equal(lastBot().view, null);
  assert.equal(ws.closed, true);
});

test('the layout reaches its own message after the reader asked another question in the same session', async () => {
  const ws1 = await askAndComplete('Which vendors are slipping on SLA?');
  ws1.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  const first = lastBot();
  const ws2 = await askAndComplete('And which one is cheapest?', { answer: 'Brightline is the cheapest per job at £212 on average.' });
  assert.notEqual(ws1, ws2);
  ws1.emit({ type: 'answer_view', session_id: SESSION, view: { v: 1, lang: PROGRAM }, cost: COST2 });
  await tick();
  const [a, b] = bots();
  assert.equal(a.mid, first.mid);
  assert.deepEqual(a.view, { lang: PROGRAM, tail: '' });
  assert.deepEqual(a.rich.cost, COST2);
  assert.equal(b.view, null, 'the newer answer is not the one that was laid out');
  assert.deepEqual(b.rich.cost, COST1);
});

test('the layout reaches its own session after the reader started a new one', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  const mid = lastBot().mid;
  c.newQuery();
  assert.equal(c.state.sessionId, null);
  ws.emit({ type: 'answer_view', session_id: SESSION, view: { v: 1, lang: PROGRAM }, cost: COST2 });
  await tick();
  assert.deepEqual(c.state.ccChat, [], 'nothing lands in the conversation the reader is in now');
  const kept = record(SESSION).turns.find((m) => m.mid === mid);
  assert.deepEqual(kept.view, { lang: PROGRAM, tail: '' });
  assert.deepEqual(kept.rich.cost, COST2);
  assert.equal(ws.closed, true);
  assert.deepEqual(c.state.ccLayout || {}, {}, 'the cue is cleared whichever session is on screen');
});

test('the socket closing before any answer_view clears the cue and keeps the answer', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  ws.serverClose();
  await tick();
  assert.equal(lastBot().text, ANSWER);
  assert.equal(rowFor(lastBot()).layingOut, false);
  assert.equal(bots().length, 1, 'no second message about the layout');
});

test('an error after the completion clears the cue and never turns the answer into an error', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  ws.emit({ type: 'error', error: 'presenter blew up', session_id: SESSION });
  await tick();
  assert.equal(bots().length, 1);
  assert.equal(lastBot().text, ANSWER);
  assert.equal(!!lastBot().error, false);
  assert.equal(rowFor(lastBot()).layingOut, false);
  assert.equal(ws.closed, true);
});

test('a layout that never comes is let go after the wait', async () => {
  c._ccLayoutWaitMs = 30;
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  assert.equal(rowFor(lastBot()).layingOut, true);
  await tick(60);
  assert.equal(rowFor(lastBot()).layingOut, false);
  assert.equal(ws.closed, true);
  // Too late: the socket is closed, and nothing arrives through it.
  ws.emit({ type: 'answer_view', session_id: SESSION, view: { v: 1, lang: PROGRAM }, cost: null });
  await tick();
  assert.equal(lastBot().view, null);
});

test('Stop pressed after the completion only ends the layout: the answer stays, nothing is added', async () => {
  const asked = c.ccAsk('Which vendors are slipping on SLA?');
  await tick();
  const ws = sockets[sockets.length - 1];
  // The Stop button's controller, as it was when the reader pressed it.
  const ctrl = c._ccAbort;
  ws.emit({ type: 'answer_delta', text: ANSWER });
  ws.emit({ type: 'workflow_completed', session_id: SESSION, answer: ANSWER, tool_calls: VENDOR_CALLS });
  await asked;
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  ctrl.abort();
  await tick();
  assert.equal(bots().length, 1);
  assert.equal(lastBot().text, ANSWER);
  assert.equal(!!lastBot().stopped, false, 'the answer was finished: it is not a stopped one');
  assert.equal(rowFor(lastBot()).layingOut, false);
  assert.equal(ws.closed, true);
});

test('the Stop button with no turn running lets any waiting layout go and leaves the answers alone', async () => {
  const ws = await askAndComplete('Which vendors are slipping on SLA?');
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  await tick();
  c.ccStop();
  await tick();
  assert.equal(ws.closed, true);
  assert.equal(lastBot().text, ANSWER);
  assert.equal(rowFor(lastBot()).layingOut, false);
});

test('Stop pressed on a follow-up leaves the earlier answer and its layout alone', async () => {
  const ws1 = await askAndComplete('Which vendors are slipping on SLA?');
  ws1.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.', session_id: SESSION });
  const asked = c.ccAsk('And which one is cheapest?');
  await tick();
  c.ccStop();
  await asked;
  assert.equal(ws1.closed, false, 'the earlier answer is still being laid out');
  ws1.emit({ type: 'answer_view', session_id: SESSION, view: { v: 1, lang: PROGRAM }, cost: null });
  await tick();
  assert.equal(bots()[0].text, ANSWER);
  assert.deepEqual(bots()[0].view, { lang: PROGRAM, tail: '' });
});

// ── a turn cut short before its completion (findings 21, 22) ─────────────────

const DRAFT1 = 'Kingsway House is 38% over its own pack, ';
const DRAFT2 = 'driven by out-of-hours gas.';
async function streamDraft(q) {
  const asked = c.ccAsk(q);
  await tick();
  const ws = sockets[sockets.length - 1];
  ws.emit({ type: 'reasoning', label: 'Domain routing', text: 'Read the question → energy engine', domain: 'energy_intelligence' });
  ws.emit({ type: 'tool_started', tool: 'get_building_cost_drivers', domain: 'energy_intelligence', input: {} });
  ws.emit({ type: 'tool_completed', tool: 'get_building_cost_drivers', domain: 'energy_intelligence', output: {} });
  ws.emit({ type: 'answer_delta', text: DRAFT1 });
  ws.emit({ type: 'answer_delta', text: DRAFT2 });
  await tick(40);
  return { asked, ws };
}

test('Stop after the answer had streamed keeps the draft as the answer, marked as stopped before it finished', async () => {
  const { asked } = await streamDraft('Why is Kingsway House over benchmark?');
  c.ccStop();
  await asked;
  const bot = lastBot();
  assert.equal(bot.text, DRAFT1 + DRAFT2);
  assert.notEqual(bot.text, 'Stopped before the orchestrator answered.');
  assert.equal(bot.stopped, true);
  assert.equal(bot.incomplete, true);
  const row = rowFor(bot);
  assert.equal(row.stoppedShow, 'block');
  assert.match(row.stopNote, /^Stopped/);
  assert.match(row.stopNote, /finished/);
  // The tools that ran still name the engine.
  assert.equal(row.domain, 'Energy');
  assert.equal(c.state.ccBusy, false);
  assert.ok(record(SESSION).turns.some((m) => m.role === 'bot' && m.text === DRAFT1 + DRAFT2));
});

test('Stop before anything was written still says it stopped before the answer', async () => {
  const asked = c.ccAsk('Why is Kingsway House over benchmark?');
  await tick();
  c.ccStop();
  await asked;
  assert.equal(lastBot().text, 'Stopped before the orchestrator answered.');
  assert.equal(rowFor(lastBot()).stopNote, 'Stopped.');
});

test('a socket that closes after the answer streamed keeps the draft, never "returned an empty answer"', async () => {
  const { asked, ws } = await streamDraft('Why is Kingsway House over benchmark?');
  ws.serverClose();
  await asked;
  const bot = lastBot();
  assert.equal(bot.text, DRAFT1 + DRAFT2);
  assert.equal(bot.incomplete, true);
  assert.equal(!!bot.stopped, false);
  assert.equal(bot.rich, null, 'an engine draft carries no compliance cards: no empty panel, no Compliance label');
  const row = rowFor(bot);
  assert.equal(row.stoppedShow, 'block');
  assert.match(row.stopNote, /closed before the answer/);
  assert.equal(row.domain, 'Energy');
  const kept = record(SESSION).turns.find((m) => m.role === 'bot');
  assert.equal(kept.text, DRAFT1 + DRAFT2, 'the session caches what was shown, not a false empty answer');
});

test('a socket that closes before anything arrived says the connection closed', async () => {
  const asked = c.ccAsk('Why is Kingsway House over benchmark?');
  await tick();
  sockets[sockets.length - 1].serverClose();
  await asked;
  assert.equal(lastBot().text, 'The connection closed before the orchestrator answered.');
  assert.equal(!!lastBot().incomplete, false);
});

test('a socket that cannot open still answers through the plain POST', async () => {
  refuse = true;
  globalThis.fetch = async () => new Response(JSON.stringify({ session_id: SESSION, answer: 'Seven work orders are overdue.', tool_calls: [{ tool: 'list_work_orders', output: {} }], success: true }),
    { status: 200, headers: { 'content-type': 'application/json' } });
  await c.ccAsk('What is overdue?');
  assert.equal(lastBot().text, 'Seven work orders are overdue.');
  assert.equal(c.state.ccBusy, false);
  assert.ok(lastBot().mid, 'a POST answer has an id too');
});

test('a Layout reasoning before the completion is a live line, as it always was', async () => {
  const asked = c.ccAsk('Which vendors are slipping on SLA?');
  await tick();
  const ws = sockets[sockets.length - 1];
  ws.emit({ type: 'reasoning', label: 'Layout', text: 'Laying the answer out for the chat.' });
  await tick();
  assert.match(c.state.ccStream.reasoning, /^Layout/);
  assert.deepEqual(c.state.ccLayout || {}, {});
  ws.emit({ type: 'workflow_completed', session_id: SESSION, answer: ANSWER, tool_calls: VENDOR_CALLS });
  await asked;
  ws.serverClose();
  await tick();
});

test('Stop during a general-loop answer keeps its program as the view and its words as the text, never code', async () => {
  // 28 Oct 2026: the loop streams its OpenUI program as the draft; kept as text, it showed and was
  // saved as "root = Answer([h, k]) …".
  const asked = c.ccAsk('What needs my attention this week?');
  await tick();
  const ws = sockets[sockets.length - 1];
  ws.emit({ type: 'answer_delta', text: 'Here is the week.\n' });
  ws.emit({ type: 'answer_delta', text: 'root = Answer([h, k])\nh = Headline("Seven work orders are overdue")\n' });
  ws.emit({ type: 'answer_delta', text: 'k = Metrics([m1])\nm1 = Metric("Overdue", "7", null, "risk")' });
  await tick(40);
  c.ccStop();
  await asked;
  const bot = lastBot();
  assert.doesNotMatch(bot.text, /root\s*=\s*Answer|Metric\(/);
  assert.match(bot.text, /Here is the week\./);
  assert.match(bot.text, /Seven work orders are overdue/);
  assert.ok(bot.view && /root = Answer/.test(typeof bot.view === 'string' ? bot.view : bot.view.lang || ''), 'drawn as its view');
  const saved = record(SESSION).turns.find((m) => m.role === 'bot' && m.stopped);
  assert.ok(saved && !/root\s*=\s*Answer/.test(saved.text));
});
