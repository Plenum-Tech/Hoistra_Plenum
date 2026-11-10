// traceTab — the Trace tab on the question page (9 Nov 2026): one answer as Langfuse shows it. The header (time, cost,
// tokens, session, user, route, models), the Tree / Timeline / Graph views, and the selected step's Preview (input and
// output, Formatted or Raw) and Metadata. Only the selected step's input and output are on the page.
import { test, after } from 'node:test';
import assert from 'node:assert/strict';
import { bundle, cleanBundles } from '../../tracelab/__tests__/esbuild-helper.mjs';

globalThis.window = globalThis.window || { location: { origin: 'http://test.local', pathname: '/lab/', search: '' } };
after(cleanBundles);
const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const TT = await bundle('../lab/TraceTab.jsx');
const html = (C, p) => renderToStaticMarkup(React.createElement(C, p || {}));

const at = (ms) => new Date(Date.UTC(2026, 10, 9, 12, 8, 22, 650) + ms).toISOString();
const node = (id, parent, depth, type, name, start, end, extra) => Object.assign({ id, parent_id: parent, depth, type,
  name, kind: type === 'generation' ? 'llm' : type, seq: depth, started_at: at(start), ended_at: at(end),
  latency_ms: end - start, cost_usd: null, model: null, input_tokens: null, output_tokens: null, ok: true, error: null,
  level: 'DEFAULT', filled: {}, truncated: false }, extra || {});
const Q = 'which maintenance work orders are breached the SLA ?';
const ANSWER = '**13 open maintenance work orders are past their SLA due time.**\n\n| WO | ISSUE |\n|---|---|\n| WO-B-301-4562 | Fire-alarm Loop 2 fault |';
const TREE = {
  purged: false,
  turn: { turn_id: 'turn-tt', langfuse_trace_id: '488a0bdaa7c95bd0ba5b1fbcae9e8dc7', session_id: 'sess-9',
    email: 'aasim.shaik@plenum-tech.com', route: 'single:maintenance', models: ['claude-sonnet-5', 'gpt-5.6-terra'],
    started_at: at(0), ended_at: at(18790), latency_ms: 18790, cost_usd: 0.11312, cost_complete: true,
    input_tokens: 40000, output_tokens: 2000, cache_read_tokens: 8210, total_tokens: 50210, ok: true, error: null },
  nodes: [
    node('r', null, 0, 'agent', 'chat', 0, 18790, { input: Q, output: ANSWER, cost_usd: 0.11312 }),
    node('a', 'r', 1, 'generation', 'router', 10, 2720, { cost_usd: 0.00769, model: 'claude-sonnet-5', input_tokens: 900,
      output_tokens: 20, input: { q: 'ROUTER-IN' }, output: { model_output: { route: 'maintenance' } } }),
    node('b', 'r', 1, 'agent', 'maintenance', 2750, 17610, { input: Q, output: 'MAINT-OUT', filled: { input_from: 'the question' } }),
    node('c', 'b', 2, 'generation', 'model', 2760, 5150, { cost_usd: 0.034392, model: 'gpt-5.6-terra',
      input: { messages: ['MODEL-IN'] }, output: { model_output: 'MODEL-OUT' } }),
    node('d', 'b', 2, 'tool', 'answer_from_records', 5160, 10920, { input: { question: 'TOOL-IN' }, output: { rows: 13 } }),
    node('e', 'd', 3, 'span', 'count', 5170, 5180, { input: { sql: 'COUNT-SQL' }, output: { row_count: 13 } }),
    node('h', 'b', 2, 'tool', 'list_vendor_accreditations', 11000, 41000, { ok: false, level: 'ERROR',
      error: 'timeout after 30 s', input: { vendor: 'Pennard' }, output: { error: 'timeout after 30 s' },
      filled: { output_from: 'its error' } }),
  ],
};
const order = (s, names) => names.map((n) => s.indexOf(n)).every((v, i, a) => v >= 0 && (i === 0 || v > a[i - 1]));

test('header shows time, cost, tokens, session and user', () => {
  const s = html(TT.TraceView, { tree: TREE });
  for (const want of ['turn-tt', '18.79s', '$0.113', '50k tokens', 'sess-9', 'aasim.shaik@plenum-tech.com',
    'single:maintenance', 'claude-sonnet-5', 'gpt-5.6-terra']) assert.ok(s.includes(want), want);
  assert.match(html(TT.TraceView, { tree: Object.assign({}, TREE, { purged: true, nodes: TREE.nodes.slice(0, 1) }) }),
    /no longer keeps this answer&#x27;s steps/);
});

test('tree lists every step with its time and cost, failed step marked', () => {
  const s = html(TT.TraceView, { tree: TREE });
  assert.ok(order(s, ['>chat<', '>router<', '>maintenance<', '>model<', '>answer_from_records<', '>count<',
    '>list_vendor_accreditations<']), 'depth-first order');
  for (const want of ['2.71s', '$0.00769', '$0.034392', '5.76s', '0.01s']) assert.ok(s.includes(want), want);
  assert.match(s, /class="[^"]*lab-tt-error[^"]*"(?:(?!<\/li>)[\s\S])*list_vendor_accreditations/);
  assert.equal((s.match(/role="treeitem"/g) || []).length, 7);
  assert.ok(s.includes('aria-label="Search steps"'));
});

test("only the selected step's input and output are rendered", () => {
  const s = html(TT.TraceView, { tree: TREE });
  assert.ok(s.includes(Q) && s.includes('WO-B-301-4562'), 'the root is selected on open');
  for (const other of ['ROUTER-IN', 'MODEL-IN', 'MODEL-OUT', 'TOOL-IN', 'COUNT-SQL', 'MAINT-OUT']) assert.ok(!s.includes(other), other);
});

test('selecting a step shows its input, output and metadata', () => {
  const p = html(TT.TraceView, { tree: TREE, selectedId: 'd' });
  assert.ok(p.includes('TOOL-IN') && p.includes('&quot;rows&quot;: 13') && !p.includes(Q + '<'));
  const m = html(TT.TraceView, { tree: TREE, selectedId: 'h', pane: 'metadata' });
  for (const want of ['its error', 'timeout after 30 s', '488a0bdaa7c95bd0ba5b1fbcae9e8dc7', 'turn-tt', 'tool'])
    assert.ok(m.includes(want), want);
  assert.match(m, /role="alert"[^>]*>timeout after 30 s/);
  const b = html(TT.TraceView, { tree: TREE, selectedId: 'b' });
  assert.ok(b.includes('Filled from the question'), 'a filled input says where it came from');
});

test('raw mode shows JSON', () => {
  const s = html(TT.TraceView, { tree: TREE, selectedId: 'd', mode: 'raw' });
  assert.match(s, /<pre[^>]*>\{\n {2}&quot;question&quot;: &quot;TOOL-IN&quot;\n\}<\/pre>/);
  const root = html(TT.TraceView, { tree: TREE, mode: 'raw' });
  assert.ok(root.includes('|---|---|') && !root.includes('<table'), 'raw shows the markdown as written');
});

test('timeline: a ruler, one bar per step labelled with its time and rolled-up cost', () => {
  const t = html(TT.TraceView, { tree: TREE, view: 'timeline' });
  assert.equal((t.match(/class="lab-tt-bar[^"]*"/g) || []).length, 7);
  assert.match(t, /style="left:0%;width:45\.8\d*%"/);
  for (const tick of ['0ms', '10.00s', '20.00s', '30.00s']) assert.ok(t.includes('>' + tick + '<'), tick);
  assert.ok(t.includes('5.76s') && t.includes('10ms'), 'durations as Langfuse writes them');
  assert.ok(t.includes('$0.034392'), 'the agent rolls up its model calls');
  assert.ok(!t.includes('lab-tt-tname'), 'names stay off the bars until Show labels');
  assert.ok(t.includes('>Show labels<') && t.includes('aria-label="Zoom in"') && t.includes('aria-label="Zoom out"'));
  assert.ok(t.includes('answer_from_records'), 'each bar still names its step for hover and screen readers');
});

test('graph: a start node, then every step in the order it ran, repeats counted', () => {
  const g = html(TT.TraceView, { tree: TREE, view: 'graph' });
  assert.ok(g.includes('<svg') && g.includes('>__start__<'));
  assert.ok(order(g, ['>__start__<', '>chat<', '>router<', '>maintenance<', '>model<', '>answer_from_records<', '>count<',
    '>list_vendor_accreditations<']), 'one chain in run order');
  assert.ok(g.includes('aria-label="Zoom in"') && g.includes('aria-label="Fit"'));
  const twice = Object.assign({}, TREE, { nodes: TREE.nodes.concat([Object.assign({}, TREE.nodes[3], { id: 'c2', started_at: TREE.nodes[6].ended_at })]) });
  assert.ok(html(TT.TraceView, { tree: twice, view: 'graph' }).includes('(2/2)'), 'a repeated step says which of its runs is in view');
  assert.ok(html(TT.TraceView, { tree: twice, view: 'graph', selectedId: 'c' }).includes('(1/2)'));
});

test('graph: its step buttons are reachable by screen readers (10 Nov 2026)', () => {
  // role="img" makes everything inside one picture, so the step buttons in it were hidden from assistive technology.
  const g = html(TT.TraceView, { tree: TREE, view: 'graph' });
  const svg = g.slice(g.indexOf('<svg class="lab-tt-graph"'), g.indexOf('>', g.indexOf('<svg class="lab-tt-graph"')));
  assert.ok(svg.includes('role="group"') && !svg.includes('role="img"'), svg);
  assert.ok(g.includes('role="button"') && g.includes('aria-label="Model call router"'));
  assert.ok(/<g[^>]*class="lab-tt-gstart"[^>]*aria-hidden="true"/.test(g), 'the start marker is decoration');
});

test('formatted output renders the markdown table', () => {
  const s = html(TT.TraceView, { tree: TREE });
  assert.match(s, /<table[\s\S]*<th[^>]*>WO<\/th>[\s\S]*<td[^>]*>WO-B-301-4562<\/td>/);
  assert.match(s, /<strong>13 open maintenance work orders are past their SLA due time\.<\/strong>/);
});


// Notes in the trace (9 Nov 2026): the page passes `annotate`; the view shows note counts on rows, each side's notes slot
// under its value, and marks each value with its note key for selection and double-click.
const annotate = (o) => Object.assign({
  keyOf: (id, side) => (id === 'e' ? null : 'KEY-' + id + '-' + side),
  count: (id) => ({ d: 2, a: 1 })[id] || 0,
  render: (id, side) => React.createElement('div', { className: 'slot' }, 'SLOT-' + id + '-' + side),
  up() {}, double() {},
  other: () => React.createElement('div', { className: 'slot' }, 'OTHER-NOTES'),
}, o || {});

test('note counts show on rows', () => {
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate() });
  assert.match(s, />answer_from_records<(?:(?!<\/li>)[\s\S])*aria-label="2 notes"[^>]*>2</);
  assert.match(s, />router<(?:(?!<\/li>)[\s\S])*aria-label="1 note"[^>]*>1</);
  assert.equal((s.match(/lab-tt-ncount/g) || []).length, 2);
  const t = html(TT.TraceView, { tree: TREE, annotate: annotate(), view: 'timeline' });
  assert.equal((t.match(/lab-tt-ncount/g) || []).length, 2);
});

test('each side renders its notes slot and wires selection', () => {
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(), selectedId: 'd' });
  assert.ok(order(s, ['TOOL-IN', 'SLOT-d-in', '&quot;rows&quot;: 13', 'SLOT-d-out']), 'each slot under its own value');
  assert.match(s, /data-note-key="KEY-d-in"/);
  assert.match(s, /data-note-key="KEY-d-out"/);
  const root = html(TT.TraceView, { tree: TREE, annotate: annotate() });
  assert.ok(order(root, ['SLOT-r-in', 'WO-B-301-4562', 'SLOT-r-out', 'OTHER-NOTES']), 'chat holds the other notes');
  const none = html(TT.TraceView, { tree: TREE, annotate: annotate(), selectedId: 'e' });
  assert.ok(!none.includes('SLOT-e') && !none.includes('data-note-key'), 'a step with no key takes no notes');
  assert.ok(!html(TT.TraceView, { tree: TREE, annotate: annotate(), selectedId: 'd' }).includes('OTHER-NOTES'));
});

test('notes stay under their side in raw mode', () => {
  const s = html(TT.TraceView, { tree: TREE, annotate: annotate(), selectedId: 'd', mode: 'raw' });
  assert.ok(order(s, ['&quot;question&quot;: &quot;TOOL-IN&quot;', 'SLOT-d-in', '&quot;rows&quot;: 13', 'SLOT-d-out']));
});

test('without annotate nothing changes', () => {
  const s = html(TT.TraceView, { tree: TREE, selectedId: 'd' });
  assert.ok(!s.includes('lab-tt-ncount') && !s.includes('data-note-key') && !s.includes('SLOT-'));
});
