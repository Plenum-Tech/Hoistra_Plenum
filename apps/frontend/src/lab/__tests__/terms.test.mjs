// terms — Langfuse's words where a trace is shown (11 Nov 2026, task plan B1.3: Session, Trace, Span, Generation,
// Score). The eval store's pages and the trace view they share with the question page name things the way Langfuse
// does. The older Runs and Question pages keep their own words (run, question, answer) for the work they describe;
// what is pinned here is that the trace itself is never called an answer with steps.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const T = await bundle('../lab/TraceTab.jsx');

const RETIRED = ['Hoist Traces turn id', 'no recorded steps', "'Turn id'", 'Search steps', 'steps of this answer',
  "this answer's steps", 'Supporting answer', 'supporting answer', 'active answer', "'Model call'", "span: 'Step'",
  "'Step id'", "'Parent step'", 'No step matches', 'aria-label="Steps"', 'Steps on a time axis', 'label="Step detail"',
  "'Step ' + node.name", 'this step recorded'];
const FILES = ['TraceTab.jsx', 'Sessions.jsx', 'SessionPage.jsx', 'Compare.jsx', 'Scenarios.jsx', 'logic/sessions.js'];

test('the trace view and the eval store pages never call a trace an answer with steps', () => {
  FILES.forEach((name) => {
    const src = readFileSync(new URL('../' + name, import.meta.url), 'utf-8');
    const code = src.split('\n').filter((line) => !line.trim().startsWith('//')).join('\n');
    RETIRED.forEach((phrase) => assert.ok(!code.includes(phrase), name + ' still says "' + phrase + '"'));
  });
});

test('a trace with nothing recorded says so in Langfuse\'s words', () => {
  const out = renderToStaticMarkup(React.createElement(T.TraceView, { tree: { turn: { turn_id: 'turn-1' }, nodes: [] } }));
  assert.ok(out.includes('This trace has no spans.'));
  assert.match(out, /title="Trace id"/);
});

test('the metadata of a step names the trace by its trace id', () => {
  const node = { id: 'root', parent_id: null, depth: 0, type: 'agent', name: 'chat', level: 'DEFAULT', input: 'q', output: 'a', filled: {} };
  const out = renderToStaticMarkup(React.createElement(T.TraceView, { tree: { turn: { turn_id: 'turn-1', session_id: 's-1' }, nodes: [node] }, pane: 'metadata' }));
  assert.ok(out.includes('Trace id') && !out.includes('Turn id'));
});

test('the trace view names a span, a generation and a tool as Langfuse does', () => {
  const tree = { turn: { turn_id: 'turn-1' }, nodes: [
    { id: 'a', parent_id: null, depth: 0, seq: 0, type: 'agent', name: 'chat', started_at: '2026-11-11T09:00:00Z', ended_at: '2026-11-11T09:00:05Z' },
    { id: 'b', parent_id: 'a', depth: 1, seq: 1, type: 'generation', name: 'router', started_at: '2026-11-11T09:00:01Z', ended_at: '2026-11-11T09:00:02Z' },
    { id: 'c', parent_id: 'a', depth: 1, seq: 2, type: 'span', name: 'plan', started_at: '2026-11-11T09:00:02Z', ended_at: '2026-11-11T09:00:03Z' }] };
  const out = renderToStaticMarkup(React.createElement(T.TraceView, { tree, selectedId: 'c' }));
  assert.ok(out.includes('title="Generation"') && out.includes('title="Span"'), out.slice(0, 400));
  assert.ok(out.includes('aria-label="Spans"'), 'the tree');
  assert.ok(out.includes('aria-label="Span plan"'), 'the selected span\'s detail');
  const graph = renderToStaticMarkup(React.createElement(T.TraceView, { tree, view: 'graph' }));
  assert.ok(graph.includes('aria-label="Generation router"'));
});
