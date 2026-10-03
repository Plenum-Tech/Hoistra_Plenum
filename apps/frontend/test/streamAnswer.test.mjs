// A streamed turn must arrive at the same answer a posted one does.
//
// The socket path and the POST path produce the same turn, so they must produce the same
// rendered answer: the structured compliance cards, the cost-by-role ledger, and a run trace
// the rail can replay. Nothing drove the streaming path before this file, which is why it
// could differ from the POST path without anything failing.
//
// The payload below is the shape svc-deepagents actually sends — see
// session_workspace.workflow_stream_completion_payload (type/answer/session_id/tool_calls/…)
// replayed by orchestrator._stream_compliance_progressive, which emits tool_started and
// tool_completed for every tool EXCEPT compliance_response and compliance_pipeline, then one
// compliance_zone per zone, then the completion carrying the full tool_calls.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));

const SESSION = 'sess-stream-1';

// One turn's real ledger, as llm_cost.Ledger.summary() writes it.
const COST = {
  calls: 4, usd: 0.7262, usd_complete: true,
  input_tokens: 61721, output_tokens: 9202, cache_read: 1024, wall_ms: 117180,
  by_role: { plan: 0.000143, analyst: 0.616271, reviewer: 0.109797 },
  models: ['claude-opus-5', 'gpt-4o-mini']
};

const TOOL_CALLS = [
  { tool: 'list_building_certificates', input: {}, output: { rows: [] } },
  { tool: 'list_vendor_accreditations', input: {}, output: { rows: [] } },
  { tool: 'get_compliance_saved_space_summary', input: {}, output: {} },
  { tool: 'compliance_response', input: { question: 'which vendors are blocked' },
    output: { narrative: 'Six vendors are currently blocked.', sections: [], groups: [],
              kpis: [{ label: 'Blocked', value: '6' }], actions: [], insights: [],
              certificates: [], pending: [], offers: [] } },
  { tool: 'compliance_pipeline', input: { question: 'which vendors are blocked' },
    output: { steps: [{ stage: 'plan', label: 'Planned the read' }], cost: COST } }
];

const EVENTS = [
  ...TOOL_CALLS
    .filter((t) => t.tool !== 'compliance_response' && t.tool !== 'compliance_pipeline')
    .flatMap((t) => ([
      { type: 'tool_started', tool: t.tool, domain: 'compliance', input: t.input },
      { type: 'tool_completed', tool: t.tool, domain: 'compliance', output: t.output }
    ])),
  // The pipeline's steps, which the replay path emits as well as the live one.
  { type: 'compliance_step', domain: 'compliance', step: { stage: 'plan', label: 'Planned the read' } },
  { type: 'compliance_zone', zone: 'narrative', domain: 'compliance', data: 'Six vendors are currently blocked.' },
  { type: 'compliance_zone', zone: 'kpis', domain: 'compliance', data: [{ label: 'Blocked', value: '6' }] },
  { type: 'workflow_completed', answer: 'Six vendors are currently blocked.',
    session_id: SESSION, tool_calls: TOOL_CALLS, workspace_status: {}, ingested_schema_mapping_ids: [] }
];

globalThis.WebSocket = class {
  constructor(url, protocols) {
    this.url = String(url);
    this.protocols = protocols;
    // onopen/onmessage are attached after the constructor returns, as in the browser.
    setTimeout(() => { if (this.onopen) this.onopen(); }, 0);
  }
  send() {
    let i = 0;
    const pump = () => {
      if (i >= EVENTS.length) { if (this.onclose) this.onclose(); return; }
      const e = EVENTS[i++];
      if (this.onmessage) this.onmessage({ data: JSON.stringify(e) });
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
});
const cleanup = () => { clearInterval(c._ccTick); clearInterval(c._orchTick); clearTimeout(c._ccRetry); };

const answerOf = () => (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();

test('a streamed turn keeps the cost ledger the backend sent', async () => {
  await c.ccAsk('which vendors are blocked right now?');
  await settle();

  const bot = answerOf();
  assert.ok(bot, 'no bot message was appended');
  assert.ok(bot.rich, 'the structured answer was dropped');
  assert.ok(bot.rich.cost, 'the cost ledger was dropped on the streaming path');
  assert.equal(bot.rich.cost.calls, 4);
  assert.equal(Object.keys(bot.rich.cost.by_role).length, 3);
  cleanup();
});

test('a streamed turn carries a run trace, so the rail can be offered', async () => {
  await c.ccAsk('which vendors are blocked right now?');
  await settle();

  const bot = answerOf();
  // traceShow in renderVals.js is gated on this being non-empty — an empty trace means no
  // "Show the run" button on the answer at all.
  assert.ok((bot.trace || []).length > 0, 'the turn recorded no trace');
  cleanup();
});

test("the run's pipeline steps reach the trace, not just its tool calls", async () => {
  // A trace holding only tool rows cannot say how the answer was composed. The steps come
  // from compliance_step events, which BOTH streaming paths now emit — the replay one
  // (orchestrator._stream_compliance_progressive) reads them off the finished turn's
  // compliance_pipeline call rather than leaving them out.
  await c.ccAsk('which vendors are blocked right now?');
  await settle();

  const kinds = (answerOf().trace || []).map((e) => e.kind);
  assert.ok(kinds.includes('step'), `trace carried no pipeline step: ${kinds.join(', ')}`);
  cleanup();
});
