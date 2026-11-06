// The chat's question carries the instance it is asked in, as every other call does (2 Nov 2026,
// feedback "Keep orchestration consistent ... the same instance context"). The WebSocket the chat
// page asks over sent the company override but not the space (the instance picker's choice), so a
// question asked in Ops Brain was answered from every module; /run and /run-stateful sent both.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const src = readFileSync(new URL('../src/api/deepAgents.js', import.meta.url), 'utf8');

test('the chat socket, /run and /run-stateful all send the company and the space', () => {
  assert.match(src, /ws\.send\(JSON\.stringify\(Object\.assign\(\{ message: message, context: context \|\| null \}, orgOverride\(\), spaceField\(\)\)\)\)/);
  const restBodies = src.match(/body: Object\.assign\(\{ message: message, session_id: sessionId \|\| null, context: context \|\| null \}, orgOverride\(\), spaceField\(\)\)/g) || [];
  assert.equal(restBodies.length, 2, '/run and /run-stateful');
});

test('the space sent is the instance the picker chose', () => {
  assert.match(src, /function spaceField\(\) \{ const s = getActiveSpace\(\); return s \? \{ space: s \} : \{\}; \}/);
  const logic = readFileSync(new URL('../src/logic/instances.js', import.meta.url), 'utf8');
  assert.match(logic, /setActiveSpace\(key\)/);
});
