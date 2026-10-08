// railRunAfterReload — the run behind the answer the rail shows comes back after a reload.
//
// Seen 5 Oct 2026: after a reload the rail still listed the answer's steps but no row offered
// "Correct", and a re-run answer showed only its "Re-run" and "Taught" notes, no steps at all.
// The stored runs (crRuns) live in memory; the transcript is restored from the browser, and
// nothing fetched the run again: only a click on "In the trace" or a server-hydrated session
// did. A re-run's own trace is just those two notes; every step it shows comes from the run.
// The run was also written ~0.7 s after the re-run's response, so two fetch attempts 2.3 s
// apart could miss it on a slower database even without a reload.
import { test, mock } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local', href: 'http://test.local/', search: '' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {} };
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const RUNS = {
  'turn-first': { turn_id: 'turn-first', question: 'What needs my approval today?', spans: [
    { id: 'r0', kind: 'turn', name: 'turn', seq: 0, parent_id: null },
    { id: 'r1', kind: 'router', name: 'router', seq: 1, parent_id: 'r0', output: { model_output: { agent: 'compliance', reason: 'approvals' } } },
    { id: 'r2', kind: 'tool', name: 'list_compliance_approvals', seq: 2, parent_id: 'r0', latency_ms: 200 }
  ] },
  'turn-rerun': { turn_id: 'turn-rerun', question: 'Re-run with corrections (s1): all of them', spans: [
    { id: 'q0', kind: 'turn', name: 'turn', seq: 0, parent_id: null },
    { id: 'q1', kind: 'plan', name: 'plan: 1 step', seq: 1, parent_id: 'q0', output: { plan: { mode: 'single', steps: [{ target: 'energy_intelligence' }] } } },
    { id: 'q2', kind: 'agent', name: 'energy_intelligence', seq: 2, parent_id: 'q0' },
    { id: 'q3', kind: 'tool', name: 'get_asset_condition_summary', seq: 3, parent_id: 'q2', latency_ms: 4200 }
  ] }
};

// fetch answers the trace store and refuses everything else, so only the runs are live.
let asked = [];
globalThis.fetch = async (url) => {
  const m = /\/api\/traces\/turns\/([^/?]+)(\?|$)/.exec(String(url));
  if (!m) throw new TypeError('Failed to fetch');
  asked.push(decodeURIComponent(m[1]));
  const run = RUNS[decodeURIComponent(m[1])];
  const body = run ? { ok: true, turn: run } : { detail: { ok: false, error: 'Turn not found' } };
  return { ok: !!run, status: run ? 200 : 404, statusText: run ? 'OK' : 'Not Found', headers: { get: () => 'application/json' },
    text: async () => JSON.stringify(body), json: async () => body };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { railTurnIndex, correctionMethods } = await import('../src/logic/corrections.js');
const { deepAgentsApi } = await import('../src/api/deepAgents.js');

const settle = () => new Promise((r) => setTimeout(r, 40));
const FIRST = [
  { role: 'you', text: 'What needs my approval today?' },
  { role: 'bot', text: 'You have 100 pending approvals.', turnId: 'turn-first',
    trace: [{ at: 0, kind: 'reasoning', label: 'Query understanding', text: 'General query' }, { at: 1, kind: 'tool', tool: 'list_compliance_approvals', ranMs: 200 }] }
];
const RERUN = [
  { role: 'you', text: 'all of them', correction: 'For the sub-agent model stage: sc.', rerun: true },
  { role: 'bot', text: 'Here are the current insights.', turnId: 'turn-rerun', rerunOf: 'turn-orig',
    trace: [{ at: 0, kind: 'reasoning', label: 'Re-run', text: 'Steps replayed with your correction: s1' },
      { at: 0, kind: 'reasoning', label: 'Taught', text: 'Remembered for similar questions' }] }
];

// A controller as a reload leaves it: signed in, on the chat page, the transcript put back
// straight into state by the constructor (no setState), and no run in memory.
function reloaded(chat) {
  const c = new HoistraLogic();
  c.state = Object.assign({}, c.state, { signedIn: true, view: 'chat', sessionId: 's1', ccChat: chat, ccTraceIdx: null, crRuns: {} });
  // The page loads, the sign-in refresh and the reports tick also start at mount; their retry
  // timers would pin the test process open, and they have tests of their own.
  ['loadLiveData', 'usLiveLoad', 'auLiveLoad', 'authBoot', 'rpStart', 'chatConnect'].forEach((k) => { c[k] = () => {}; });
  return c;
}
// Registered with t.after so it runs even when an assertion fails: componentDidMount starts
// interval timers, and a test that throws before stopping them hangs the run instead of failing.
const stop = (c) => { try { c.componentWillUnmount(); } catch (e) { /* timers only */ } };

test('the rail picks the pinned answer, else the newest traced one, and an answer on record counts', () => {
  const chat = [{ role: 'you', text: 'a' }, { role: 'bot', trace: [{}], turnId: 't1' }, { role: 'you', text: 'b' }, { role: 'bot', turnId: 't2' }, { role: 'you', text: 'c' }];
  assert.equal(railTurnIndex(chat, 1), 1);
  assert.equal(railTurnIndex(chat, null), 3);        // no stream trace (shed to fit storage), but its run is on record
  assert.equal(railTurnIndex(chat, 9), 3);           // a pin past the end falls back
  assert.equal(railTurnIndex([{ role: 'you', text: 'a' }, { role: 'bot', text: 'x' }], null), -1);
});

test('after a reload the rail\'s answer gets its run back, and every row offers Correct', async (t) => {
  asked = [];
  const c = reloaded(FIRST);
  t.after(() => stop(c));
  c.componentDidMount();
  await settle();
  assert.deepEqual(asked.filter((x) => x === 'turn-first'), ['turn-first'], 'the run behind the rail\'s answer is fetched once');
  const rows = c.renderVals().orchTraceRows;
  assert.ok(rows.length >= 2);
  assert.ok(rows.every((r) => typeof r.correct === 'function'), 'every row of the stored run is correctable');
});

test('a re-run answer shows the steps of its own run after a reload, not just its notes', async (t) => {
  asked = [];
  const c = reloaded(RERUN);
  t.after(() => stop(c));
  c.componentDidMount();
  await settle();
  const rows = c.renderVals().orchTraceRows;
  const titles = rows.map((r) => r.title);
  assert.ok(titles.includes('get_asset_condition_summary'), 'the re-run\'s tool call is in the rail: ' + titles.join(' | '));
  assert.ok(titles.includes('energy_intelligence'));
  assert.ok(rows.every((r) => typeof r.correct === 'function'));
});

test('an answer whose stream trace was shed to fit storage still gets its run in the rail and its "In the trace" link', async (t) => {
  asked = [];
  const c = reloaded([{ role: 'you', text: 'What needs my approval today?' }, { role: 'bot', text: 'You have 100 pending approvals.', turnId: 'turn-first' }]);
  t.after(() => stop(c));
  c.componentDidMount();
  await settle();
  assert.deepEqual(asked, ['turn-first'], 'the run is fetched although no stream trace was kept');
  const v = c.renderVals();
  assert.ok(v.orchTraceRows.length >= 2, 'the rail draws the stored run');
  assert.ok(v.orchTraceRows.every((r) => typeof r.correct === 'function'), 'every row offers Correct');
  assert.equal(v.orchChat[1].traceShow, 'inline-flex', 'the answer offers its run to the rail');
});

test('the scroll-spy moving the rail to an older answer loads that answer\'s run', async (t) => {
  asked = [];
  const c = reloaded(FIRST.concat(RERUN));
  t.after(() => stop(c));
  c.componentDidMount();
  await settle();
  assert.ok(asked.includes('turn-rerun'));
  assert.ok(!asked.includes('turn-first'), 'only the answer on screen is fetched');
  c.renderVals().orchTraceFollow(1);
  await settle();
  assert.ok(asked.includes('turn-first'));
  assert.ok(c.renderVals().orchTraceRows.every((r) => typeof r.correct === 'function'));
});

test('a run is fetched once: not again while loading, on every state change, or while a turn runs', async (t) => {
  asked = [];
  const c = reloaded(FIRST);
  t.after(() => stop(c));
  c.setState({ ccBusy: true });
  await settle();
  assert.deepEqual(asked, [], 'nothing is fetched while a turn is answering');
  c.setState({ ccBusy: false });
  c.setState({ ccTick: 1 }); c.setState({ ccTraceIdx: 1 }); c.setState({ ccTraceIdx: null });
  await settle();
  assert.deepEqual(asked, ['turn-first']);
});

test('a run written after the answer arrives is still picked up: the load retries past the first misses', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const c = Object.assign({}, correctionMethods);
  c.state = { crRuns: {} };
  c.setState = (u) => { c.state = Object.assign({}, c.state, typeof u === 'function' ? u(c.state) : u); };
  const real = deepAgentsApi.traceTurn;
  let calls = 0;
  // Missing for the first three reads (about 5 s), then on record.
  deepAgentsApi.traceTurn = async () => { calls += 1; if (calls <= 3) throw new Error('Turn not found'); return { ok: true, turn: RUNS['turn-rerun'] }; };
  try {
    const flush = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };
    await c.crLoadRun('turn-rerun');
    for (let i = 0; i < 6 && !c.state.crRuns['turn-rerun']; i += 1) { t.mock.timers.tick(6000); await flush(); }
    assert.ok(c.state.crRuns['turn-rerun'], 'the run is loaded once it is on record');
    assert.equal(calls, 4);
  } finally {
    deepAgentsApi.traceTurn = real;
  }
});

test('a run that never turns up stops being asked for, and a click asks again', async (t) => {
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const c = Object.assign({}, correctionMethods);
  c.state = { crRuns: {}, signedIn: true, ccChat: FIRST, ccTraceIdx: null };
  c.setState = (u) => { c.state = Object.assign({}, c.state, typeof u === 'function' ? u(c.state) : u); };
  const real = deepAgentsApi.traceTurn;
  let calls = 0;
  deepAgentsApi.traceTurn = async () => { calls += 1; throw new Error('Turn not found'); };
  try {
    const flush = async () => { for (let i = 0; i < 5; i += 1) await Promise.resolve(); };
    c.crEnsureRun();
    for (let i = 0; i < 10; i += 1) { t.mock.timers.tick(10000); await flush(); }
    const settled = calls;
    assert.ok(settled > 1 && settled <= 6, 'retried a bounded number of times: ' + settled);
    c.crEnsureRun(); c.crEnsureRun();
    await flush();
    assert.equal(calls, settled, 'the rail does not keep asking for a run the store does not have');
    c.crLoadRun('turn-first');            // "In the trace" clicked: an explicit ask goes out again
    await flush();
    assert.equal(calls, settled + 1);
  } finally {
    deepAgentsApi.traceTurn = real;
  }
});

test('a reload whose open conversation was shed from storage reads its turns back', async (t) => {
  // Older than the newest 60, the open conversation is stored without its transcript; the
  // constructor put back an empty chat and nothing re-read it (8 Oct 2026 review).
  const c = reloaded([]);
  c.state.sessions = [{ id: 's1', kind: 'chat', title: 'Old question', turns: [], remote: true, turnCount: 2, at: 1 }];
  const hydrated = [];
  c.sessionHydrate = async (id) => { hydrated.push(id); };
  t.after(() => stop(c));
  c.componentDidMount();
  assert.deepEqual(hydrated, ['s1']);
  // A transcript that came back whole is not read again.
  const d = reloaded(FIRST);
  d.state.sessions = [{ id: 's1', kind: 'chat', title: 'Q', turns: FIRST, at: 1 }];
  const again = [];
  d.sessionHydrate = async (id) => { again.push(id); };
  t.after(() => stop(d));
  d.componentDidMount();
  assert.deepEqual(again, []);
});
