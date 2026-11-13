// A turn copied from another machine has no chat record; the server builds one from its stored trace (stored: true, with
// the chat's own message-id shapes). The notes logic must give every step of it note keys and place notes written on them,
// exactly as for a chat-recorded turn, so the session page can annotate it (14 Nov 2026).
import { test } from 'node:test';
import assert from 'node:assert/strict';

const N = await import('../logic/traceNotes.js');

const RECORD = { id: 'tc', stored: true, groups: [{ span_id: 'g1', name: 'compliance' }], messages: [
  { id: 'tc:s0:user', role: 'user', span_id: 's0', text: 'Which certificates expire?' },
  { id: 'tc:t:call', role: 'tool_call', span_id: 't' },
  { id: 'tc:t:result', role: 'tool_result', span_id: 't' },
  { id: 'tc:m:call', role: 'model_call', span_id: 'm' },
  { id: 'tc:s0:answer', role: 'assistant', span_id: 's0', text: 'Two.' }] };
const node = (id, parent, type, name) => ({ id, parent_id: parent, type, name });
const NODES = [node('s0', null, 'agent', 'chat'), node('g1', 's0', 'agent', 'compliance'), node('t', 'g1', 'tool', 'lookup'),
  node('m', 'g1', 'generation', 'analyst')];
const view = (x, me) => ({ id: x.id, key: x.step_key, mine: !!me && x.reviewer_id === me, at: x.at || '' });
const label = (id, key) => ({ id, step_key: key, reviewer_id: 'me', at: '2026-11-14T10:00:00Z' });

test('every step of a stored-only turn has note keys, the root on the question and answer', () => {
  const k = N.nodeKeys(RECORD, NODES);
  assert.deepEqual([k.get('s0').in, k.get('s0').out], ['tc:s0:user', 'tc:s0:answer']);
  assert.deepEqual([k.get('g1').in, k.get('g1').out], ['tc:g1:agent', 'tc:g1:agent#output']);
  assert.deepEqual([k.get('t').in, k.get('t').out], ['tc:t:call', 'tc:t:call#output']);
  assert.deepEqual([k.get('m').in, k.get('m').out], ['tc:m:call', 'tc:m:call#output']);
});

test('notes written on a stored-only turn land on their step and side, and the turn comment on the turn', () => {
  const idx = N.traceNotesIndex([label('a', 'tc:t:call#output'), label('b', 'tc:g1:agent'), label('c', 'tc:scenario'),
    label('d', 'tc:s0:user'), label('e', 'tc:gone:call')], RECORD, NODES, 'me', view);
  assert.equal(idx.slots['t:out'].length, 1);
  assert.equal(idx.slots['g1:in'].length, 1);
  assert.equal(idx.slots['s0:in'].length, 1);
  assert.equal(idx.scenario.length, 1);
  assert.equal(idx.other.length, 1);
  assert.deepEqual(N.stepOfKey(RECORD, NODES, 'tc:t:call#output'), { id: 't', side: 'out' });
});
