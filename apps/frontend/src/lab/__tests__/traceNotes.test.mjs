// traceNotes — notes in the trace (9 Nov 2026): each trace step's input and output keep today's note keys (a message id,
// + '#output'; an agent's '<turn>:<span>:agent'; the question and answer messages for chat), and every stored note lands
// on the step and side it was written on, or under chat's Other notes when it matches no step.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const N = await import('../logic/traceNotes.js');

const RECORD = { id: 'tq', groups: [{ span_id: 'g1', name: 'compliance' }], messages: [
  { id: 'tq:turn:user', role: 'user', span_id: null },
  { id: 'tq:r:call', role: 'model_call', span_id: 'r' },
  { id: 'tq:p:call', role: 'model_call', span_id: 'p' },
  { id: 'tq:p2:call', role: 'model_call', span_id: 'p2' },
  { id: 'tq:c:query', role: 'db_query', span_id: 'c' },
  { id: 'tq:t:call', role: 'tool_call', span_id: 't' },
  { id: 'tq:t:result', role: 'tool_result', span_id: 't' },
  { id: 'tq:turn:answer', role: 'assistant', subtype: 'answer', span_id: null }] };
const node = (id, parent, type, name) => ({ id, parent_id: parent, type, name });
const NODES = [node('s0', null, 'agent', 'chat'), node('r', 's0', 'generation', 'router'), node('g1', 's0', 'agent', 'compliance'),
  node('p', 'g1', 'generation', 'plan'), node('p2', 'g1', 'generation', 'plan'), node('c', 'g1', 'span', 'count'),
  node('t', 'g1', 'tool', 'lookup'), node('f', 'g1', 'span', 'fetch')];
const view = (x, me) => ({ id: x.id, key: x.step_key, mine: !!me && x.reviewer_id === me, at: x.at || '' });
const label = (id, key, o) => Object.assign({ id, step_key: key, reviewer_id: 'other', at: '2026-10-22T10:00:00Z' }, o || {});

test('every node has keys', () => {
  const k = N.nodeKeys(RECORD, NODES);
  assert.deepEqual(k.get('s0'), { in: 'tq:turn:user', out: 'tq:turn:answer', inTitle: 'Input · chat', outTitle: 'Output · chat' });
  assert.deepEqual(k.get('g1'), { in: 'tq:g1:agent', out: 'tq:g1:agent#output', inTitle: 'Input · compliance', outTitle: 'Output · compliance' });
  assert.equal(k.get('t').in, 'tq:t:call');
  assert.equal(k.get('t').out, 'tq:t:call#output');
  assert.equal(k.get('p2').in, 'tq:p2:call');
  assert.deepEqual([k.get('f').in, k.get('f').out], [null, null], 'a step with no message of its own takes no notes');
  for (const n of NODES) assert.ok(k.has(n.id), n.id);
  const titles = N.keyTitles(k);
  assert.equal(titles['tq:t:call#output'], 'Output · lookup');
  assert.equal(titles['tq:turn:user'], 'Input · chat');
});

test('old and new notes land on their step and side', () => {
  const ix = N.traceNotesIndex([
    label('n1', 'tq:c:query#output', { reviewer_id: 'me', at: '2026-10-22T09:30:00Z' }),
    label('n3', 'tq:p2:call'), label('n4', 'tq:scenario'), label('n5', 'tq:g1:agent'),
    label('n6', 'tq:turn:user'), label('n7', 'tq:turn:answer'), label('n8', 'tq:t:result#output')], RECORD, NODES, 'me', view);
  assert.deepEqual(ix.slots['c:out'].map((n) => n.id), ['n1']);
  assert.deepEqual(ix.slots['p2:in'].map((n) => n.id), ['n3'], 'a note on a merged part now sits on its own step');
  assert.deepEqual(ix.scenario.map((n) => n.id), ['n4']);
  assert.deepEqual(ix.slots['g1:in'].map((n) => n.id), ['n5']);
  assert.deepEqual([ix.slots['s0:in'][0].id, ix.slots['s0:out'][0].id], ['n6', 'n7']);
  assert.deepEqual(ix.slots['t:out'].map((n) => n.id), ['n8'], 'any message of a step places its note');
  assert.deepEqual(ix.other, []);
  // Each note knows the key of the place it shows under (edit, delete and their errors land there).
  assert.deepEqual([ix.slots['t:out'][0].block, ix.slots['c:out'][0].block, ix.scenario[0].block, ix.slots['s0:in'][0].block],
    ['tq:t:call#output', 'tq:c:query#output', 'scenario', 'tq:turn:user']);
});

test('unmatched notes go to other', () => {
  const ix = N.traceNotesIndex([label('o1', 'tq:orchestrator'), label('o2', 'tq:gone:agent'), label('o3', 'tq:x9:call#output')],
    RECORD, NODES, 'me', view);
  assert.deepEqual(ix.other.map((n) => n.id).sort(), ['o1', 'o2', 'o3']);
  assert.deepEqual(Object.keys(ix.slots), []);
  assert.ok(ix.other.every((n) => n.block === 'other'));
});

test('cleared notes are dropped and mine come first', () => {
  const ix = N.traceNotesIndex([
    label('a', 'tq:c:query#output', { at: '2026-10-22T08:00:00Z' }),
    label('b', 'tq:c:query#output', { at: '2026-10-22T11:00:00Z' }),
    label('m', 'tq:c:query#output', { reviewer_id: 'me', at: '2026-10-21T08:00:00Z' }),
    label('x', 'tq:c:query#output', { cleared: true }), null], RECORD, NODES, 'me', view);
  assert.deepEqual(ix.slots['c:out'].map((n) => n.id), ['m', 'b', 'a']);
});

test('counts per node', () => {
  const ix = N.traceNotesIndex([label('1', 'tq:c:query'), label('2', 'tq:c:query#output'), label('3', 'tq:turn:answer'),
    label('4', 'tq:orchestrator'), label('5', 'tq:scenario')], RECORD, NODES, 'me', view);
  assert.deepEqual(ix.counts, { c: 2, s0: 2 }, 'chat counts its own notes and the other notes it holds; the question comment is not a step');
  assert.deepEqual(N.traceNotesIndex([], null, [], 'me', view), { slots: {}, scenario: [], other: [], counts: {} });
});
