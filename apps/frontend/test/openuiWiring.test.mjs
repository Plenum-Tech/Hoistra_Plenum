// openuiWiring — the chat around an OpenUI answer: where the view comes from (a finished turn,
// a reopened thread), how the stream paints without lagging, and which bubble draws what.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { extractOpenUIView, callNames } from '../src/logic/openuiView.js';
import { createFramePainter } from '../src/logic/framePainter.js';
import { turnsFromThread } from '../src/logic/sessions.js';
import { chatMethods } from '../src/logic/chat.js';

const src = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8');
const PROGRAM = 'root = Answer([h])\nh = Headline("Seven are overdue")';
const VIEW = { tool: 'openui_view', input: {}, output: { v: 1, lang: PROGRAM } };

test('the view comes off the turn\'s tool calls, with its tail', () => {
  assert.deepEqual(extractOpenUIView([{ tool: 'list_work_orders' }, VIEW]), { lang: PROGRAM, tail: '' });
  assert.deepEqual(extractOpenUIView([{ tool: 'openui_view', output: { v: 1, lang: PROGRAM, tail: '_Note._' } }]), { lang: PROGRAM, tail: '_Note._' });
});

test('no view, or a malformed one, is no view', () => {
  for (const calls of [null, [], [{ tool: 'list_work_orders' }], [{ tool: 'openui_view' }],
    [{ tool: 'openui_view', output: 'x' }], [{ tool: 'openui_view', output: { lang: '' } }],
    [{ tool: 'openui_view', output: { lang: 42 } }], [{ tool: 'openui_view', output: { v: 2, lang: PROGRAM } }]]) {
    assert.equal(extractOpenUIView(calls), null, JSON.stringify(calls));
  }
});

test('the view is presentation, not a tool the reader should see under the answer', () => {
  assert.deepEqual(callNames([{ tool: 'list_work_orders' }, { tool: 'compliance_response' }, VIEW, {}]), ['list_work_orders', 'compliance_response']);
  assert.deepEqual(callNames(['list_work_orders', 'openui_view']), ['list_work_orders']);
});

test('a reopened thread draws its answers with the view they streamed with', () => {
  const turns = turnsFromThread({ turns: [{ question: 'What is overdue?', answer: 'Seven are overdue', tools: ['list_work_orders', 'openui_view', VIEW] }] });
  const bot = turns.find((m) => m.role === 'bot');
  assert.deepEqual(bot.view, { lang: PROGRAM, tail: '' });
  assert.deepEqual(bot.calls, ['list_work_orders']);
  const old = turnsFromThread({ turns: [{ question: 'q', answer: 'a', tools: ['list_work_orders'] }] }).find((m) => m.role === 'bot');
  assert.equal(old.view, undefined);
});

function clock() {
  let t = 0;
  const frames = [];
  return {
    now: () => t, at: (ms) => { t = ms; },
    raf: (cb) => { frames.push(cb); return frames.length; },
    caf: (id) => { frames[id - 1] = null; },
    run: () => { const due = frames.splice(0); due.forEach((cb) => cb && cb()); },
    pending: () => frames.filter(Boolean).length
  };
}

test('a burst of streamed words paints once per frame, not once per word', () => {
  const c = clock();
  let paints = 0;
  const p = createFramePainter(() => { paints += 1; }, { raf: c.raf, caf: c.caf, now: c.now, minGap: 40 });
  for (let i = 0; i < 50; i += 1) p.request();
  assert.equal(paints, 0);
  assert.equal(c.pending(), 1);
  c.run();
  assert.equal(paints, 1);
});

test('frames closer than the gap wait for it, so a fast stream paints about 25 times a second', () => {
  const c = clock();
  let paints = 0;
  const p = createFramePainter(() => { paints += 1; }, { raf: c.raf, caf: c.caf, now: c.now, minGap: 40 });
  p.request(); c.run();
  c.at(16); p.request(); c.run();
  assert.equal(paints, 1, 'a frame 16 ms after a paint waits');
  c.at(41); c.run();
  assert.equal(paints, 2);
});

test('flush paints what is pending at once and cancels the waiting frame', () => {
  const c = clock();
  let paints = 0;
  const p = createFramePainter(() => { paints += 1; }, { raf: c.raf, caf: c.caf, now: c.now, minGap: 40 });
  p.request();
  p.flush();
  assert.equal(paints, 1);
  c.run();
  assert.equal(paints, 1);
  p.flush();
  assert.equal(paints, 1, 'nothing pending, nothing painted');
});

// An answer's "Open Compliance" / "Open Vendors" button goes where the navigator goes: the live
// console and vendor pages. openModule('compliance') is the prototype's seed table (demo data
// shown as if it were the customer's) — found in review, 26 Oct 2026.
function page(target) {
  const c = { state: {}, opened: null, setState(p) { Object.assign(this.state, p); }, openModule(m) { this.opened = m; } };
  chatMethods.openuiOpen.call(c, target);
  return c;
}

test('an answer\'s Open Compliance and Open Vendors buttons open the live pages, never the seed module', () => {
  assert.equal(page({ page: 'compliance' }).state.view, 'cc');
  assert.equal(page({ page: 'compliance' }).opened, null);
  assert.equal(page({ page: 'vendors' }).state.view, 'vp');
  assert.equal(page({ page: 'vendors' }).opened, null);
});

test('the other pages open their module as the navigator does', () => {
  assert.equal(page({ page: 'energy' }).opened, 'energy');
  assert.equal(page({ page: 'assets' }).opened, 'assets');
  assert.equal(page({ page: 'ops' }).opened, 'ops');
});

test('the chat page and the dock draw a view with the lazy OpenUI answer inside the host', () => {
  for (const file of ['screens/Chat.jsx', 'components/shell/OrchestratorDock.jsx']) {
    const s = src(file);
    assert.match(s, /import LazyOpenUIAnswer from '[./]+openui\/LazyOpenUIAnswer\.jsx'/, file);
    assert.match(s, /m\.view\s*\?\s*<LazyOpenUIAnswer text=\{m\.view\} tail=\{m\.viewTail\} fallbackText=\{m\.text\} \/>/, file);
    assert.match(s, /<OpenUIHostContext\.Provider value=\{vals\.openuiHost\}>/, file);
    assert.match(s, /<ComplianceAnswer rich=\{vals\.orchLiveRich\}[^\n]*\slive \/>/, file);
  }
});

test('a dashboard answer hands its narrative to the view, and the live draft streams as OpenUI', () => {
  const s = src('components/shell/ComplianceAnswer.jsx');
  assert.match(s, /looksLikeOpenUIStart\(rich\.narrative\)/);
  assert.match(s, /<LazyOpenUIAnswer text=\{rich\.narrative\} fallbackText=\{fallbackText\} streaming \/>/);
  assert.match(s, /<LazyOpenUIAnswer text=\{view\} tail=\{viewTail\} fallbackText=\{fallbackText\} \/>/);
  // Without a view of its own, the cards are written as one and drawn the same way (27 Oct 2026),
  // with the whole answer beside them: the narrative of planner and loop cards is only its lead.
  assert.match(s, /programFromCompliance\([^;]*, live \? '' : \(fallbackText \|\| ''\)\)/);
  assert.match(s, /const drawn = !!\(view \|\| auto\)/);
  assert.match(s, /\{!drawn && rich\.kpis\.length/);
  assert.match(s, /<LazyOpenUIAnswer text=\{auto\} fallbackText=\{fallbackText \|\| rich\.narrative\} streaming=\{!!live\} \/>/);
  // What runs something stays a real button.
  assert.match(s, /looseOffers\.map/);
  assert.match(s, /rich\.pending\.length \? \(/);
});

test('every Markdown answer is drawn with the OpenUI components, in the page and the dock', () => {
  for (const file of ['screens/Chat.jsx', 'components/shell/OrchestratorDock.jsx']) {
    const s = src(file);
    assert.match(s, /m\.view \? <LazyOpenUIAnswer [^\n]*\/> : <AnyAnswer text=\{m\.text\} \/>/, file);
    assert.ok(!/<AnswerCards /.test(s), file);
  }
  assert.match(src('openui/AnyAnswer.jsx'), /VITE_OPENUI_ALL_ANSWERS/);
});

test('finished answers do not re-render on every keystroke', () => {
  assert.match(src('components/shell/AnswerCards.jsx'), /export default memo\(AnswerCards/);
});

test('the stream paints through the frame painter and warms the renderer up on send', () => {
  const s = src('logic/complianceLive.js');
  assert.match(s, /createFramePainter\(paint/);
  assert.match(s, /if \(d\.type === "answer_delta"\) \{\s*if \(typeof d\.text === "string" && d\.text\) \{ partial\.answer \+= d\.text; painter\.request\(\); \}/);
  assert.match(s, /preloadOpenUI\(\)/);
  assert.match(src('App.jsx'), /preloadOpenUI/);
});
