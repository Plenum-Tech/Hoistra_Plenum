// reopenedAnswersKeepTheirLayout — a chat reopened from the server renders its answers the way
// they rendered live: the compliance dashboard when the answer had one, markdown otherwise.
//
// The server's thread kept only tool names, so a conversation this browser never saw (asked on
// another device, or shed from local storage to make room) came back as plain markdown while the
// same answers rendered as cards live (5 Oct 2026). The thread now keeps the two presentation
// payloads beside the names; names stay strings, so older rows read exactly as before.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { turnsFromThread } from '../src/logic/sessions.js';

const RESPONSE = { narrative: 'Five vendors are blocked.', kpis: [{ value: 5, label: 'Vendors blocked' }], groups: [], actions: [] };
const PIPELINE = { engine: 'compliance', steps: [{ stage: 'data', label: 'list_vendor_accreditations' }], cost: null };

test('an answer stored with its card payloads reopens as the dashboard', () => {
  const msgs = turnsFromThread({ turns: [{ question: 'Which vendors are blocked?', answer: 'Five.', route: 'compliance',
    tools: ['list_vendor_accreditations', 'compliance_pipeline', 'compliance_response',
      { tool: 'compliance_pipeline', output: PIPELINE }, { tool: 'compliance_response', output: RESPONSE }] }] });
  const bot = msgs[1];
  assert.deepEqual(bot.calls, ['list_vendor_accreditations', 'compliance_pipeline', 'compliance_response']);
  assert.ok(bot.rich);
  assert.equal(bot.rich.narrative, 'Five vendors are blocked.');
  assert.equal(bot.rich.kpis.length, 1);
});

test('an older row with names only reopens as markdown, as before', () => {
  const msgs = turnsFromThread({ turns: [{ question: 'q', answer: 'a', tools: ['compliance_pipeline', 'compliance_response'] }] });
  assert.equal(msgs[1].rich, undefined);
  assert.deepEqual(msgs[1].calls, ['compliance_pipeline', 'compliance_response']);
});

test('a re-run reopens with its dashboard too', () => {
  const msgs = turnsFromThread({ turns: [
    { question: 'Which vendors are blocked?', answer: 'Five.', tools: ['x'] },
    { question: 'Re-run with corrections (as marked): Which vendors are blocked?', answer: 'Four.', route: 'rerun',
      tools: ['compliance_response', { tool: 'compliance_response', output: RESPONSE }] }
  ] });
  assert.equal(msgs.length, 2);
  assert.ok(msgs[1].rich);
});
