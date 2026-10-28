// openuiSpec — the component set the orchestrator answers with, and the prompt that teaches it.
//
// src/openui/spec.js defines the components once, without React, so the same definitions build
// the library the chat renders with AND the "Answer format" section of svc-deepagents' system
// prompt (scripts/openui-prompt.mjs writes it to src/agents/openui_prompt.md). If the two drift,
// the model writes components the chat cannot draw, or never hears of ones it can — so the
// committed prompt has to be exactly what the spec generates.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import * as core from '@openuidev/lang-core';
import { z } from 'zod';
import { makeLibrary, promptOptions, ROOT, BLOCKS, EXAMPLES } from '../src/openui/spec.js';

const library = makeLibrary(core, z);
const prompt = library.prompt(promptOptions) + '\n';
const committed = readFileSync(
  new URL('../../backend/cafm-connector-service-final/svc-deepagents/src/agents/openui_prompt.md', import.meta.url),
  'utf8'
);

const NAMES = [
  'Answer', 'Headline', 'Text', 'Metrics', 'Metric', 'Table', 'BarChart', 'LineChart', 'Series',
  'Callout', 'Finding', 'Details', 'Field', 'Section', 'Steps', 'Step', 'Buttons', 'Button', 'FollowUps', 'EmailDraft'
];

test('the root is Answer and the components are exactly the designed set', () => {
  assert.equal(ROOT, 'Answer');
  assert.equal(library.root, 'Answer');
  assert.deepEqual(Object.keys(library.components).sort(), [...NAMES].sort());
});

test('the block list names what an Answer may hold, in prompt order', () => {
  assert.deepEqual(BLOCKS, [
    'Headline', 'Text', 'Metrics', 'Table', 'BarChart', 'LineChart', 'Callout', 'Finding', 'Details',
    'Section', 'Steps', 'Buttons', 'FollowUps', 'EmailDraft'
  ]);
});

test('no component takes a name the language reserves', () => {
  for (const n of NAMES) assert.ok(!['Query', 'Mutation', 'Action'].includes(n), n);
});

test('the prompt carries every signature with its positional props in order', () => {
  assert.match(prompt, /Answer\(children: \(Headline \| Text \| Metrics/);
  assert.match(prompt, /Metric\(label: string, value: string \| number, note\?: string, tone\?: "risk" \| "warn" \| "ok" \| "neutral"\)/);
  assert.match(prompt, /Table\(columns: \(string \| number\)\[\], rows: /);
  assert.match(prompt, /BarChart\(title: string, labels: \(string \| number\)\[\], values: \(number \| string\)\[\]/);
  assert.match(prompt, /Section\(title: string, children: /);
  assert.match(prompt, /Step\(title: string, detail\?: string, action\?: Button\)/);
  assert.match(prompt, /EmailDraft\(subject: string, to: string, body: string, cc\?: string\)/);
  for (const n of NAMES) assert.ok(prompt.includes(n + '('), n + ' is missing from the prompt');
});

test('the house rules are in the prompt: no fences, figures from tools, \\n escapes, in-app pages', () => {
  assert.match(prompt, /no code fences/i);
  assert.match(prompt, /came from your tools/);
  assert.match(prompt, /\\n inside a string/);
  assert.match(prompt, /@OpenUrl\("\/compliance"\)/);
});

test('tables carry every row asked for: no row cap that would fight the answer check', () => {
  // The database-answer check (EL-UDR) requires a headline count to equal the rows in the
  // answer's own table; a 12-row cap made every longer list fail it (review, 26 Oct 2026).
  // The Table component shows the first rows and opens the rest itself.
  assert.doesNotMatch(prompt, /At most 12 rows|holds at most 12 rows/);
  assert.match(prompt, /every row/i);
});

test('a follow-up question carries everything it needs in its own words', () => {
  // The chat asks a @ToAssistant message as written; hidden context would be lost.
  assert.match(prompt, /whole question in the message/i);
});

test('every worked example parses against the library with nothing dropped', () => {
  const parser = core.createParser(library.toJSONSchema(), ROOT);
  assert.ok(EXAMPLES.length >= 2);
  for (const ex of EXAMPLES) {
    const r = parser.parse(ex);
    assert.ok(r.root, 'example has no root:\n' + ex);
    assert.equal(r.root.typeName, 'Answer');
    assert.deepEqual(r.meta.errors, [], JSON.stringify(r.meta.errors));
    assert.deepEqual(r.meta.unresolved, []);
    assert.deepEqual(r.meta.orphaned, []);
  }
});

test('the committed svc-deepagents prompt is exactly what the spec generates', () => {
  assert.equal(committed, prompt, 'run: node scripts/openui-prompt.mjs');
});
