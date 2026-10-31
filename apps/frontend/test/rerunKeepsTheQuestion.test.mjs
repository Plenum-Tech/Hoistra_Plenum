// rerunKeepsTheQuestion — "Re-run the steps" re-asks the ORIGINAL question with the correction
// applied; it does not post the correction as a new question.
//
// Seen 5 Oct 2026: correcting the analyst stage of "Which vendors are blocked right now?" with
// "hi" put a new bubble "Re-run with correction: For the compliance analyst stage: hi." under
// the old answer, the rail headed the run with that sentence, and the chat read as if a new
// query had been typed. The server was already replaying the original question
// (orchestrator.rerun_turn); only the transcript told it otherwise. Now the question bubble
// stays the original question with the correction as a note beneath it, and the corrected
// answer takes the old answer's place, as Edit does. A failed re-run puts the old answer back.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { rerunTranscript, correctionMethods } from '../src/logic/corrections.js';
import { deepAgentsApi } from '../src/api/deepAgents.js';
import { turnsFromThread, attachTurns } from '../src/logic/sessions.js';

const Q1 = 'Which certificates expire this month?';
const Q2 = 'Which vendors are blocked right now?';
const CHAT = [
  { role: 'you', text: Q1 }, { role: 'bot', text: 'Two.', turnId: 't1' },
  { role: 'you', text: Q2 }, { role: 'bot', text: 'Five vendors.', turnId: 't2' }
];

test('correcting the last answer keeps its question and drops only that answer', () => {
  const out = rerunTranscript(CHAT, 't2', 'ignored', 'For the compliance analyst stage: hi.');
  assert.equal(out.length, 3);
  assert.deepEqual(out.slice(0, 2), CHAT.slice(0, 2));
  assert.equal(out[2].role, 'you');
  assert.equal(out[2].text, Q2);                     // the original question, verbatim
  assert.equal(out[2].correction, 'For the compliance analyst stage: hi.');
  assert.ok(!/Re-run with correction/.test(out[2].text));
});

test('correcting an earlier answer replaces from its question on, as Edit does', () => {
  const out = rerunTranscript(CHAT, 't1', 'ignored', 'Count DEC certificates too.');
  assert.equal(out.length, 1);
  assert.equal(out[0].text, Q1);
  assert.equal(out[0].correction, 'Count DEC certificates too.');
});

test('an answer not in the transcript falls back to the run\'s own question, appended', () => {
  const out = rerunTranscript(CHAT, 'gone', Q2, 'x');
  assert.equal(out.length, CHAT.length + 1);
  assert.equal(out[out.length - 1].text, Q2);
});

function controller(chat) {
  const c = Object.assign({}, correctionMethods);
  c.state = { ccChat: chat.slice(), sessionId: 's1', crOpen: { turnId: 't2', spanId: 'sp', mode: 'model' }, crNote: 'hi', crExclude: [],
    crRuns: { t2: { question: Q2, spans: [{ id: 'sp', kind: 'llm', name: 'analyst', agent: 'compliance' }] } } };
  c.setState = (u) => { c.state = Object.assign({}, c.state, typeof u === 'function' ? u(c.state) : u); };
  c.flash = () => {}; c.crLoadRun = () => {};
  return c;
}

test('Re-run the steps: the original question, then the corrected answer in the old one\'s place', async () => {
  const real = deepAgentsApi.traceRerun;
  let seen = null;
  deepAgentsApi.traceRerun = async (turnId, body) => { seen = { turnId, body }; return { ok: true, turn_id: 't3', rerun_of: 't2', answer: 'Four vendors.', tool_calls: [], applied: [], taught: [] }; };
  try {
    const c = controller(CHAT);
    await c.crRerun();
    assert.equal(seen.turnId, 't2');                  // the server replays the original turn
    const chat = c.state.ccChat;
    assert.equal(chat.length, 4);
    assert.equal(chat[2].text, Q2);
    assert.match(chat[2].correction, /hi/);
    assert.equal(chat[3].text, 'Four vendors.');
    assert.equal(chat[3].rerunOf, 't2');
    assert.ok(!chat.some((m) => m.text === 'Five vendors.'));
  } finally { deepAgentsApi.traceRerun = real; }
});

test('a failed re-run puts the original answer back, with the error after it', async () => {
  const real = deepAgentsApi.traceRerun;
  deepAgentsApi.traceRerun = async () => { throw new Error('boom'); };
  try {
    const c = controller(CHAT);
    await c.crRerun();
    const chat = c.state.ccChat;
    assert.deepEqual(chat.slice(0, 4), CHAT);
    assert.equal(chat.length, 5);
    assert.match(chat[4].text, /Could not re-run: boom/);
    assert.equal(c.state.ccBusy, false);
  } finally { deepAgentsApi.traceRerun = real; }
});

test('a reopened thread reads a re-run as the original question with the corrected answer', () => {
  const RQ = 'Re-run with corrections (pinned: {"exclude": ["cancelled"]}): ' + Q2;
  const msgs = turnsFromThread({ turns: [
    { question: Q1, answer: 'Two.', route: 'records' },
    { question: Q2, answer: 'Five vendors.', route: 'compliance' },
    { question: RQ, answer: 'Four vendors.', route: 'rerun' }
  ] });
  assert.deepEqual(msgs.map((m) => [m.role, m.text]), [['you', Q1], ['bot', 'Two.'], ['you', Q2], ['bot', 'Four vendors.']]);
  assert.equal(msgs[2].correction, 'Re-run with your correction');
  // the corrected answer gets the re-run's own run, not the original's
  const out = attachTurns(msgs, [
    { turn_id: 't1', question: Q1, started_at: '2026-10-05T09:00:00Z' },
    { turn_id: 't2', question: Q2, started_at: '2026-10-05T09:05:00Z' },
    { turn_id: 't3', question: RQ, started_at: '2026-10-05T09:10:00Z' }
  ]);
  assert.equal(out[1].turnId, 't1');
  assert.equal(out[3].turnId, 't3');
});

// Seen 5 Oct 2026: a corrected answer came back as plain markdown (a table, then bullet lists)
// while every first answer in the same chat rendered as the compliance dashboard. The re-run
// endpoint returns the same compliance_pipeline / compliance_response outputs a planned turn
// does; crRerun kept only the tool names, so the rich view never had anything to render.
test('a corrected answer renders like the first one: the structured outputs are kept', async () => {
  const real = deepAgentsApi.traceRerun;
  deepAgentsApi.traceRerun = async () => ({ ok: true, turn_id: 't3', rerun_of: 't2', answer: 'Four vendors.', applied: [], taught: [], tool_calls: [
    { tool: 'list_vendor_accreditations', input: {}, output: { ok: true, rows: [] } },
    { tool: 'planner', input: {}, output: { steps: ['compliance'] } },
    { tool: 'compliance_pipeline', input: {}, output: { engine: 'orchestrator', steps: [{ stage: 'data', label: 'list_vendor_accreditations' }], cost: null } },
    { tool: 'compliance_response', input: {}, output: { narrative: 'Four vendors are blocked.', kpis: [{ value: 4, label: 'Vendors blocked' }], groups: [], actions: [] } }
  ] });
  try {
    const c = controller(CHAT);
    await c.crRerun();
    const bot = c.state.ccChat[3];
    assert.ok(bot.rich, 'the rich payload is kept');
    assert.equal(bot.rich.narrative, 'Four vendors are blocked.');
    assert.equal(bot.rich.kpis.length, 1);
    assert.deepEqual(bot.calls, ['list_vendor_accreditations', 'planner', 'compliance_pipeline', 'compliance_response']);
  } finally { deepAgentsApi.traceRerun = real; }
});

test('a corrected answer with no structured outputs stays markdown', async () => {
  const real = deepAgentsApi.traceRerun;
  deepAgentsApi.traceRerun = async () => ({ ok: true, turn_id: 't3', answer: 'Four.', applied: [], taught: [], tool_calls: [{ tool: 'answer_from_records', output: { ok: true } }] });
  try {
    const c = controller(CHAT);
    await c.crRerun();
    assert.equal(c.state.ccChat[3].rich, null);
  } finally { deepAgentsApi.traceRerun = real; }
});
