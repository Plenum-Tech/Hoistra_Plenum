// The expected outcome and the fidelity caveats, rendered to markup: on a golden conversation the panel is closed
// (nothing of the expected answer or the grade in the page) until revealed; once revealed it shows the case, the
// grade, the value checks and the refused steps (in a closed technical panel); Hide closes it without a second
// reveal; a live conversation has neither panel nor caveats; every golden run shows the four caveats.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from './esbuild-helper.mjs';

after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const { initialState, goldenInitial } = await import('../store.js');
const { FIDELITY_CAVEATS } = await import('../logic/golden.js');
const { default: Conversations } = await bundle('views/Conversations.jsx');
const { default: Runs } = await bundle('views/Runs.jsx');

const NOW = Date.parse('2026-10-15T12:00:00Z');
const R1 = '6f1c2d3e-0000-4000-8000-000000000001';
const SID = 'golden-6f1c2d3e-21-r1';
const REC = { id: 't9', timestamp: '2026-10-15T11:00:00Z', models: [], usage: {}, meta: {}, flags: [], cot_boundary: 'B',
  messages: [{ id: 't9:r:user', role: 'user', text: 'How many work orders?' }, { id: 't9:r:answer', role: 'assistant', subtype: 'answer', text: '758 work orders.' }] };
const conv = (sid) => ({ key: 's:' + sid, session_id: sid, session_kind: 'synthetic', session_label: 'Golden case 21', turns: [{ turn_id: 't9', turn_index: 1, question: 'How many?', ok: true }], active: REC });
const BLIND = { ok: true, available: true, revealed: false, golden: { run: { id: R1, label: 'run-a' }, case_key: '21', rep: 1 } };
const FULL = { ok: true, available: true, revealed: true, golden: { run: { id: R1, label: 'run-a' }, case_key: '21', rep: 1, msg_idx: 0, messages_total: 1, is_final: true,
  case: { question: 'How many work orders?', expected_answer: 'Seven hundred and fifty-nine.', expected_values: [{ raw: '759' }], evaluation: 'objective', source: 'Tower.xlsx', verified_on: '2026-10-13' },
  result: { outcome: 'fail', grade: { critique: 'The answer says 758; the records say 759.', value_check: { status: 'mismatch', items: [{ raw: '759', ok: false, nearest: '758' }] } },
    refused: [{ service: 'oi', method: 'POST', path: '/x', reason: 'refused_by_trace_lab' }] } } };
const page = (expected, sid, over) => renderToStaticMarkup(React.createElement(Conversations, { s: Object.assign(initialState(), {
  tab: 'conversations', run: R1, convKey: 's:' + (sid || SID), turnId: 't9', conv: { data: conv(sid || SID), loading: false, error: '', reason: '' },
  golden: Object.assign(goldenInitial(), { expected: expected ? { t9: expected } : {} }) }, over || {}), now: NOW }));

test('a golden answer shows the caveats and a closed expected outcome, with nothing of the answer or grade in the page', () => {
  let html = page(null);
  assert.match(html, /role="note" aria-label="What a golden run cannot measure"/);
  for (const c of FIDELITY_CAVEATS) assert.ok(html.includes(c.replace(/'/g, '&#x27;')), c);
  assert.match(html, /aria-label="Expected outcome"/);
  assert.ok(html.includes('Hidden until you reveal it'));
  assert.match(html, />Reveal the expected outcome</);
  html = page({ loading: false, data: BLIND });
  assert.ok(html.includes('Golden run run-a · case 21 · rep 1'));
  assert.equal(html.includes('Seven hundred'), false);
  assert.equal(html.includes('758; the records'), false);
  assert.ok(html.indexOf('aria-label="Expected outcome"') < html.indexOf('How this answer was produced'), 'the panel sits above the timeline');
});

test('revealed: the case, the grade, the value checks and the refused steps behind a closed technical panel', () => {
  const html = page({ loading: false, data: FULL });
  assert.ok(html.includes('Seven hundred and fifty-nine.'));
  assert.ok(html.includes('<th scope="row">Expected values</th><td>759</td>'));
  assert.ok(html.includes('<th scope="row">Grade</th><td>Fail</td>'));
  assert.ok(html.includes('The answer says 758; the records say 759.'));
  assert.ok(html.includes('<li>✗ 759 (closest: 758)</li>'));
  assert.ok(html.includes('1 step that would have changed data was refused'));
  assert.match(html, /<details class="tl-raw"><summary>Refused steps<\/summary><\/details>/);
  assert.equal(html.includes('refused_by_trace_lab'), false, 'the technical detail stays closed');
  assert.match(html, />Hide</);
});

test('hidden again after a reveal, the button shows it without saying "Reveal" twice', () => {
  const html = page({ loading: false, data: FULL, hidden: true });
  assert.equal(html.includes('Seven hundred'), false);
  assert.match(html, />Show the expected outcome</);
});

test('an answer Trace Lab has no case for says so; a live conversation has no panel and no caveats', () => {
  assert.ok(page({ loading: false, data: { ok: true, available: false } }).includes('Expected outcome: unavailable for this answer.'));
  const live = page(null, 'chat-1', { run: 'live' });
  assert.equal(live.includes('Expected outcome'), false);
  assert.equal(live.includes('What a golden run cannot measure'), false);
  assert.ok(live.includes('How this answer was produced'));
});

test('a failed read says so in words', () => {
  assert.ok(page({ loading: false, error: 'timed out after 15s' }).includes('Couldn&#x27;t read the expected outcome: timed out after 15s'));
});

test('the caveats are on every golden run: its detail and the start dialog', () => {
  const run = { id: R1, label: 'run-a', status: 'done', k: 1, items_done: 1, items_total: 1 };
  const detail = renderToStaticMarkup(React.createElement(Runs, { s: Object.assign(initialState(), { tab: 'runs',
    golden: Object.assign(goldenInitial(), { detail: { runId: R1, data: { run, cases: [] }, loading: false, error: '', loadedAt: 1 } }) }), now: NOW }));
  assert.ok(detail.includes('What a golden run cannot measure'));
  const dialog = renderToStaticMarkup(React.createElement(Runs, { s: Object.assign(initialState(), { tab: 'runs',
    golden: Object.assign(goldenInitial(), { dialog: { k: 1, purpose: 'adhoc', setId: null, note: '', usdCap: '', estimate: null, estimating: true, starting: false, error: '', problems: [] } }) }), now: NOW }));
  assert.ok(dialog.includes('What a golden run cannot measure'));
});
