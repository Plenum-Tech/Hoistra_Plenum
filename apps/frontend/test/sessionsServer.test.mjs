// sessions — the server's copy of the conversations folded into the browser's cache
// (memory phase A): what gets added, what is left alone, and how a thread's turns read.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { mergeServerThreads, turnsFromThread, makeSession, MAX_TURNS } from '../src/logic/sessions.js';

const T0 = Date.parse('2026-10-02T09:00:00Z');

test('a thread this browser never saw is added as remote; one it has keeps its transcript', () => {
  const mine = makeSession({ id: 'a', title: 'Boilers at B-301', at: T0 - 1000, owner: 'FM@Example.com', viewOrgId: null });
  mine.turns = [{ role: 'you', text: 'Boilers at B-301' }, { role: 'bot', text: 'Three.' }];
  const out = mergeServerThreads([mine], [
    { id: 'a', title: 'Boilers at B-301', turn_count: 1, last_message_at: '2026-10-02T09:00:00Z' },
    { id: 'b', title: 'SLA credits for Apex', turn_count: 4, last_message_at: '2026-10-01T08:00:00Z', created_at: '2026-10-01T07:00:00Z' }
  ], { owner: 'fm@example.com', viewOrgId: null });
  assert.equal(out.length, 2);
  const a = out.find((r) => r.id === 'a');
  assert.equal(a.turns.length, 2);            // the browser's transcript survives
  assert.equal(a.at, T0);                     // the later time wins
  const b = out.find((r) => r.id === 'b');
  assert.equal(b.remote, true);
  assert.equal(b.serverTurns, 4);
  assert.equal(b.owner, 'fm@example.com');
  assert.deepEqual(b.turns, []);
});

test("another account's record with the same id is left alone, and no owner merges nothing", () => {
  const theirs = makeSession({ id: 'a', title: 'Theirs', at: T0 - 5000, owner: 'other@example.com', viewOrgId: null });
  const out = mergeServerThreads([theirs], [{ id: 'a', title: 'Mine', last_message_at: '2026-10-02T09:00:00Z' }], { owner: 'fm@example.com' });
  assert.equal(out.length, 1);
  assert.equal(out[0].owner, 'other@example.com');
  assert.equal(out[0].at, T0 - 5000);
  assert.equal(mergeServerThreads([theirs], [{ id: 'z' }], {}).length, 1);
});

test('a superadmin viewing as a company gets the rows stamped with that scope', () => {
  const out = mergeServerThreads([], [{ id: 'c', title: 'TechCorp chillers' }], { owner: 'sa@example.com', viewOrgId: 'org-techcorp' });
  assert.equal(out[0].viewOrgId, 'org-techcorp');
});

test("a server thread reads back as the chat's own turns, tools kept, capped to the window", () => {
  const turns = turnsFromThread({ turns: [
    { question: 'How many boilers?', answer: 'Three.', tools: ['get_assets', { bad: 1 }] },
    { question: 'Which are overdue?', answer: null }
  ] });
  assert.deepEqual(turns, [
    { role: 'you', text: 'How many boilers?' },
    { role: 'bot', text: 'Three.', calls: ['get_assets'] },
    { role: 'you', text: 'Which are overdue?' }
  ]);
  const many = turnsFromThread({ turns: Array.from({ length: MAX_TURNS }, (_, i) => ({ question: 'q' + i, answer: 'a' + i })) });
  assert.equal(many.length, MAX_TURNS);
  assert.equal(turnsFromThread(null).length, 0);
});
