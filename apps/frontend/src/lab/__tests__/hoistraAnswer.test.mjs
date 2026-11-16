// hoistraAnswer — the answer and its steps in Trace Lab exactly as Hoistra showed them (16 Nov 2026, Hussain: "make sure
// u show me the exact trace steps and exact same ans in the trace lab"). The answer is drawn from the turn's stored
// chat row (plenum_cafm.chat_turns.tools: the cards and the OpenUI view) with Hoistra's own components; each step's
// line under its name follows Hoistra's run trace (logic/corrections.js).
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const HA = await bundle('../lab/HoistraAnswer.jsx');
const TT = await bundle('../lab/TraceTab.jsx');

test('each step says what Hoistra’s run trace says under it', () => {
  assert.equal(HA.stepDetail({ kind: 'db', output: '{"rows": [{"n": 13}], "row_count": 13}' }), '13 rows');
  assert.equal(HA.stepDetail({ kind: 'db', output: { rows: [], row_count: 1 } }), '1 row');
  assert.equal(HA.stepDetail({ kind: 'plan', output: { plan: { goal: null, mode: 'single', steps: [{ target: 'wo_engine' }] }, rejected: null } }), 'One step: wo_engine');
  assert.equal(HA.stepDetail({ kind: 'plan', output: { plan: { mode: 'single', steps: [] }, rejected: 'too broad' } }), 'Rejected: too broad');
  assert.equal(HA.stepDetail({ kind: 'llm', output: { content: '', tool_calls: [{ name: 'answer_from_records' }] } }), 'decided: answer_from_records');
  assert.equal(HA.stepDetail({ kind: 'llm', output: { content: 'x'.repeat(200) } }), 'x'.repeat(120));
  assert.equal(HA.stepDetail({ kind: 'step', input: { ask: 'Which building?' } }), 'Which building?');
  assert.equal(HA.stepDetail({ kind: 'db', ok: false, error: 'timeout', output: { row_count: 0 } }), 'Failed: timeout');
  assert.equal(HA.stepDetail({ kind: 'tool', output: 'not json' }), '');
});

test('the tree shows each step’s line and model, as in Hoistra', () => {
  const tree = { turn: { turn_id: 't1' }, nodes: [
    { id: 'a', parent_id: null, kind: 'turn', type: 'agent', name: 'chat', latency_ms: 24600, output: 'Answer.' },
    { id: 'b', parent_id: 'a', kind: 'llm', type: 'generation', name: 'model', model: 'gpt-5.6-terra', latency_ms: 1900, cost_usd: 0.034,
      output: { content: '', tool_calls: [{ name: 'answer_from_records' }] } },
    { id: 'c', parent_id: 'a', kind: 'db', type: 'span', name: 'focus WorkOrder', latency_ms: 205, output: '{"rows": [], "row_count": 13}' }] };
  const out = renderToStaticMarkup(React.createElement(TT.TraceView, { tree }));
  assert.ok(out.includes('decided: answer_from_records'));
  assert.ok(out.includes('13 rows'));
  assert.ok(out.includes('gpt-5.6-terra'), 'the model beside the step, as in Hoistra');
});

const CARDS = { tool: 'compliance_response', output: {
  narrative: '**Bishopsgate Tower has the most delayed work orders: 13 overdue, incomplete jobs.**',
  kpis: [{ count: 48353, label: '£/yr at stake', sublabel: 'energy waste behind 8 open jobs', severity: 'warning', unit: 'other' },
    { count: 15, label: 'Replace or fix', sublabel: 'of 15 repeat-failure assets', severity: 'critical', unit: 'other' }],
  groups: [{ owner: 'Open jobs with money at stake', headline: '£48,353 a year of identified waste', points: ['WO-B-301-4527 — AHU-3 supply fan'], severity: 'warning', scope: 'Mixed' }],
  actions: [{ title: 'Approve WO-B-301-4527', severity: 'warning', tags: ['cost saving'], scope: 'Mixed' }] } };

test('the stored cards are read as the reopened chat reads them', () => {
  const parts = HA.answerParts({ answer: 'Answer text.', tools: ['answer_from_records', CARDS, { tool: 'openui_view', output: { v: 1, lang: 'root = Stack([])', tail: '' } }] });
  assert.ok(parts.rich && parts.rich.kpis.length === 2);
  assert.deepEqual(parts.view, { lang: 'root = Stack([])', tail: '' });
  assert.equal(parts.text, 'Answer text.');
  const none = HA.answerParts({ answer: 'Only text.', tools: ['get_cost_savings'] });
  assert.equal(none.rich, null);
  assert.equal(none.view, null);
});

test('the answer with its cards is drawn with Hoistra’s own components', () => {
  // ComplianceAnswer lays the cards out as an OpenUI program, which loads lazily: on the server only its frame and the
  // answer's text are drawn (the cards themselves are checked in the browser).
  const out = renderToStaticMarkup(React.createElement(HA.default, { shown: { found: true, answer: 'Answer text.', tools: [CARDS] } }));
  assert.ok(out.includes('>Overall<') && out.includes('Answer text.'), 'drawn by ComplianceAnswer, as the chat draws a turn with cards');
});

test('a plain answer is drawn as Hoistra draws text', () => {
  const out = renderToStaticMarkup(React.createElement(HA.default, { shown: { found: true, answer: '**Bold** answer.', tools: [] } }));
  assert.ok(out.includes('<strong>Bold</strong>') || out.includes('Bold'));
});

test('without the chat’s record nothing is drawn, so the step keeps its own text', () => {
  const out = renderToStaticMarkup(React.createElement(HA.default, { shown: { found: false }, fallback: 'Answer.' }));
  assert.equal(out, '');
});

test('the Trace Lab page loads what Hoistra’s answers need, as Hoistra’s page does', async () => {
  const { readFileSync } = await import('node:fs');
  const lab = readFileSync(new URL('../../../lab/index.html', import.meta.url), 'utf-8');
  assert.ok(lab.includes('https://unpkg.com/@phosphor-icons/web@2.1.1/src/regular/style.css'), 'the icons on the cards');
  const guard = lab.indexOf('globalThis[Symbol.for("openui.devtools.autoMount")] = true;');
  assert.ok(guard > -1 && guard < lab.indexOf('/src/lab/main.jsx'), 'OpenUI’s devtools panel is off before the app loads');
});

const ROOT = { turn: { turn_id: 't1' }, nodes: [{ id: 'a', parent_id: null, kind: 'turn', type: 'agent', name: 'chat', latency_ms: 1000, output: 'Recorded text.' }] };

test('while the answer as shown is read, the recorded text does not flash first', () => {
  const out = renderToStaticMarkup(React.createElement(TT.TraceView, { tree: ROOT, loadShown: () => new Promise(() => {}) }));
  assert.ok(out.includes('Reading the answer as Hoistra showed it…'));
  assert.ok(!out.includes('Recorded text.'));
});

test('the answer as shown sits in its own frame, without the text note outline', () => {
  const out = renderToStaticMarkup(React.createElement(TT.TraceView, { tree: ROOT, shown: { found: true, answer: 'Answer text.', tools: [] } }));
  assert.match(out, /class="lab-tt-val lab-tt-shown"|class="lab-hoistra-answer"/);
  const none = renderToStaticMarkup(React.createElement(TT.TraceView, { tree: ROOT, shown: { found: false } }));
  assert.ok(none.includes('Recorded text.'), 'no record of it: the recorded text, as before');
  const raw = renderToStaticMarkup(React.createElement(TT.TraceView, { tree: ROOT, mode: 'raw', loadShown: () => new Promise(() => {}) }));
  assert.ok(raw.includes('Recorded text.'), 'Raw never waits');
});

test('an odd step never breaks the tree', () => {
  assert.equal(HA.stepDetail({ kind: 'llm', output: { tool_calls: [null, { name: 'x' }] } }), 'decided: x');
  assert.equal(HA.stepDetail({ kind: 'plan', output: { plan: { mode: 'single', steps: null } } }), '');
});
