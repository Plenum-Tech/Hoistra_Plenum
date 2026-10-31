// rerunKeepsTheQuestion — "Re-run the steps" re-asks the ORIGINAL question with the correction
// applied; it does not post the correction as a new question.
//
// Seen 5 Oct 2026: correcting the analyst stage of "Which vendors are blocked right now?" with
// "hi" put a new bubble "Re-run with correction: For the compliance analyst stage: hi." under
// the old answer, the rail headed the run with that sentence, and the chat read as if a new
// query had been typed. The server was already replaying the original question
// (orchestrator.rerun_turn); only the transcript told it otherwise. Now the question bubble
// stays the original question with the correction as a note beneath it.
//
// It used to replace from that question on, as Edit does. That discarded every turn after the one
// being corrected and put the reader back up the page, so a re-run now APPENDS to the end of the
// conversation (1 Nov 2026): a correction is not an edit of history, and the reader is already at
// the bottom. A failed re-run leaves everything in place and says so.
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

test('correcting the last answer appends its question, verbatim, to the end', () => {
  const out = rerunTranscript(CHAT, 't2', 'ignored', 'For the compliance analyst stage: hi.');
  assert.equal(out.length, CHAT.length + 1);
  assert.deepEqual(out.slice(0, CHAT.length), CHAT);   // nothing above it moved
  const last = out[out.length - 1];
  assert.equal(last.role, 'you');
  assert.equal(last.text, Q2);                         // the original question, verbatim
  assert.equal(last.correction, 'For the compliance analyst stage: hi.');
  assert.ok(!/Re-run with correction/.test(last.text));
});

test('correcting an EARLIER answer keeps every turn since and appends to the end', () => {
  const out = rerunTranscript(CHAT, 't1', 'ignored', 'Count DEC certificates too.');
  assert.equal(out.length, CHAT.length + 1);
  assert.deepEqual(out.slice(0, CHAT.length), CHAT);   // Q2 and its answer survive
  const last = out[out.length - 1];
  assert.equal(last.text, Q1);                         // the corrected answer's own question
  assert.equal(last.correction, 'Count DEC certificates too.');
});

test('a re-run never discards a turn', () => {
  for (const id of ['t1', 't2', 'gone']) {
    const out = rerunTranscript(CHAT, id, Q2, 'x');
    assert.equal(out.length, CHAT.length + 1, id);
    assert.deepEqual(out.slice(0, CHAT.length), CHAT, id);
  }
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

test('Re-run the steps: the original question and its corrected answer, both at the end', async () => {
  const real = deepAgentsApi.traceRerun;
  let seen = null;
  deepAgentsApi.traceRerun = async (turnId, body) => { seen = { turnId, body }; return { ok: true, turn_id: 't3', rerun_of: 't2', answer: 'Four vendors.', tool_calls: [], applied: [], taught: [] }; };
  try {
    const c = controller(CHAT);
    await c.crRerun();
    assert.equal(seen.turnId, 't2');                  // the server replays the original turn
    const chat = c.state.ccChat;
    assert.equal(chat.length, CHAT.length + 2);         // the question, then its new answer
    assert.deepEqual(chat.slice(0, CHAT.length), CHAT); // nothing above either of them moved
    assert.equal(chat[CHAT.length].text, Q2);
    assert.match(chat[CHAT.length].correction, /hi/);
    assert.equal(chat[CHAT.length + 1].text, 'Four vendors.');
    assert.equal(chat[CHAT.length + 1].rerunOf, 't2');
    // The answer being corrected stays where it was said: a correction is not an edit of history.
    assert.ok(chat.some((m) => m.text === 'Five vendors.'));
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
    const chat = c.state.ccChat;
    const bot = chat[chat.length - 1];   // the re-run appends; the answer is last
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
    const chat2 = c.state.ccChat;
    assert.equal(chat2[chat2.length - 1].rich, null);   // the re-run appends; the answer is last
  } finally { deepAgentsApi.traceRerun = real; }
});
