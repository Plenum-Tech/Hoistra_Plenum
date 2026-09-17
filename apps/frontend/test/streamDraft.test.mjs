// An engine's answer shows as it is written, and the final answer replaces the draft.
//
// Compliance streamed its analyst's zones from the start; energy, maintenance, vendors and the
// general loop delivered their answer whole after a silent minute. svc-deepagents now emits
// `answer_delta` events (meta_tools.answer_delta_event) on the same channel as tool events.
// The interface must paint them as the live narrative — and must let the completion win, since
// the vendor composer rewrites the sub-agent's draft into cards.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

const SESSION = 'sess-draft-1';
const FINAL = 'Kingsway House is 38% over its own pack, driven by out-of-hours gas.';

const EVENTS = [
  { type: 'reasoning', label: 'Domain routing', text: 'Read the question → energy_intelligence engine', domain: 'energy_intelligence' },
  { type: 'tool_started', tool: 'get_building_cost_drivers', domain: 'energy_intelligence', input: {} },
  { type: 'tool_completed', tool: 'get_building_cost_drivers', domain: 'energy_intelligence', output: { drivers: [] } },
  { type: 'answer_delta', text: 'Kingsway House is ', domain: 'energy_intelligence' },
  { type: 'answer_delta', text: '38% over its own pack', domain: 'energy_intelligence' },
  { type: 'answer_delta', text: ', driven by', domain: 'energy_intelligence' },
  { type: 'workflow_completed', answer: FINAL, session_id: SESSION,
    tool_calls: [{ tool: 'get_building_cost_drivers', input: {}, output: { drivers: [] } }],
    workspace_status: {}, ingested_schema_mapping_ids: [] }
];

// Snapshots of the live narrative, taken by the fake socket after each event is delivered —
// the only way to see what the screen showed mid-stream rather than only at the end.
const seenNarratives = [];
globalThis.__snapshot = () => {};

globalThis.WebSocket = class {
  constructor(url, protocols) { this.url = String(url); this.protocols = protocols; setTimeout(() => { if (this.onopen) this.onopen(); }, 0); }
  send() {
    let i = 0;
    const pump = () => {
      if (i >= EVENTS.length) { if (this.onclose) this.onclose(); return; }
      const e = EVENTS[i++];
      if (this.onmessage) this.onmessage({ data: JSON.stringify(e) });
      globalThis.__snapshot();
      setTimeout(pump, 0);
    };
    setTimeout(pump, 0);
  }
  close() {}
};

const { configureAuth } = await import('../src/api/client.js');
const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

const settle = () => new Promise((r) => setTimeout(r, 120));
let c;
beforeEach(() => {
  configureAuth({ getToken: () => 'tok-abc123' });
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'chat', sessionId: SESSION });
  seenNarratives.length = 0;
  globalThis.__snapshot = () => {
    const s = c.state.ccStream;
    seenNarratives.push(s && s.rich ? s.rich.narrative : null);
  };
});
const cleanup = () => { clearInterval(c._ccTick); clearInterval(c._orchTick); clearTimeout(c._ccRetry); };
const answerOf = () => (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();

test('the draft grows on screen as answer_delta events arrive', async () => {
  await c.ccAsk('why is Kingsway House over benchmark?');
  await settle();
  assert.ok(seenNarratives.includes('Kingsway House is '), `first delta never painted: ${JSON.stringify(seenNarratives)}`);
  assert.ok(seenNarratives.includes('Kingsway House is 38% over its own pack'), 'second delta was not appended to the first');
  assert.ok(seenNarratives.includes('Kingsway House is 38% over its own pack, driven by'), 'third delta was not appended');
  cleanup();
});

test('the completion replaces the draft with the final answer', async () => {
  await c.ccAsk('why is Kingsway House over benchmark?');
  await settle();
  const bot = answerOf();
  assert.ok(bot, 'no bot message was appended');
  assert.equal(bot.text || bot.answer || (bot.rich && bot.rich.narrative), FINAL);
  cleanup();
});

test('deltas do not flood the run trace', async () => {
  await c.ccAsk('why is Kingsway House over benchmark?');
  await settle();
  const kinds = (answerOf().trace || []).map((e) => e.kind);
  assert.ok(!kinds.includes('delta'), 'a trace entry per token would bury the rail');
  assert.ok(kinds.includes('tool'), `the tool rows still reach the trace: ${kinds.join(', ')}`);
  cleanup();
});
