// Energy, Assets and Maintenance answer in the side dock, like Compliance does.
//
// Where an answer lands was decided in two places that had drifted apart. The CONTROLLER
// (complianceLive.dockAnswers) counted the Energy module page, so a question asked there ran
// through ccAsk and opened the dock; the VIEW MODEL (renderVals' own `chatView` list) did
// not, so the dock rendered the legacy task panel — `orchStepsShow` is the exact inverse of
// `orchChatShow` — and the answer, which had run, was never on screen. The same flag gates
// the live stream, the trace, stop and attach, so all of those were dark there too.
//
// These pin the two together and extend the dock to Assets and Maintenance, which are the
// same shape as Energy: all three are view "module", and only Energy had been named.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {} };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const { DOCK_MODULES } = await import('../src/logic/complianceLive.js');
const { MODULES } = await import('../src/logic/constants.js');

let c;
beforeEach(() => {
  c = new HoistraLogic();
  c.setState({ signedIn: true });
});

const onModule = (module) => c.setState({ view: 'module', module: module });
// A turn in flight is what makes the dock show its transcript region at all.
const asking = () => c.setState({ ccBusy: true, orchOpen: true });

// The list is imported, never retyped. Spelling a module by its page title — Maintenance is
// keyed "ops" — matches nothing in state, and every test below would still pass while the
// page went on handing the whole screen to the seed answer view. That is exactly what
// happened once. This is the guard that catches it.
test('every dock module is a real module key, not a page title', () => {
  DOCK_MODULES.forEach((key) => {
    assert.ok(MODULES[key], `"${key}" is not a key of MODULES — check it is not the page title`);
  });
});

test('the dock covers Energy, Assets and Maintenance, by whatever key each is held under', () => {
  const names = DOCK_MODULES.map((k) => MODULES[k].name).sort();
  assert.deepEqual(names, ['Assets', 'Energy', 'Maintenance']);
});

// ── where the answer lands ───────────────────────────────────────────────────────────

DOCK_MODULES.forEach((module) => {
  test(`a question on the ${module} page is answered in the dock, not on the chat page`, () => {
    onModule(module);
    assert.equal(c.dockAnswers(), true, 'should keep its data in view beside the answer');
    assert.equal(c.chatView(), true, 'the ask bar should route to the conversation, not the old ask()');
  });

  test(`the ${module} dock shows the transcript rather than the legacy task panel`, () => {
    onModule(module);
    asking();
    const vals = c.renderVals();
    assert.equal(vals.orchChatShow, true, 'the answer must be on screen');
    assert.equal(vals.orchStepsShow, 'none', 'the legacy step panel is the inverse of the transcript');
  });

  test(`the ${module} dock can paint a streaming answer, stop it and attach to it`, () => {
    onModule(module);
    asking();
    c.setState({ ccStream: { steps: [], reasoning: '', trace: [], rich: {} } });
    const vals = c.renderVals();
    assert.equal(vals.orchLiveShow, 'block', 'the stream should paint as it arrives');
    assert.equal(vals.orchStopShow, 'inline-flex', 'a running turn must be stoppable');
    assert.equal(vals.orchAttachShow, 'flex');
  });
});

// ── the ask bar actually routes there ────────────────────────────────────────────────
//
// The predicates above are what the routing reads, but the page's Ask button goes through
// pqRun → askScoped, and asserting the predicate is not asserting that the button obeys it:
// askScoped sends the question to ccAsk (the conversation) or to ask() (the seed answer
// page, which takes over the whole screen). These pin the button, not the flag.

DOCK_MODULES.forEach((module) => {
  test(`the Ask button on ${module} starts a conversation, not the seed answer page`, () => {
    onModule(module);
    const called = [];
    c.ccAsk = (q) => { called.push(['ccAsk', q]); };
    c.ask = (q) => { called.push(['ask', q]); };

    c.setState({ pq: 'which ones are behind plan?' });
    c.renderVals().pqRun();

    assert.deepEqual(called, [['ccAsk', 'which ones are behind plan?']],
      'the seed answer page must not take the screen over');
  });
});

test('a page with no dock still answers on the seed answer page', () => {
  onModule('something-else');
  const called = [];
  c.ccAsk = (q) => { called.push(['ccAsk', q]); };
  c.ask = (q) => { called.push(['ask', q]); };

  c.setState({ pq: 'anything' });
  c.renderVals().pqRun();

  assert.deepEqual(called, [['ask', 'anything']]);
});

// ── every ask affordance on a page agrees with that page's ask bar ───────────────────
//
// A page offers the same question in several places: the bar, the "Ask this page" chips
// (abChips) and the suggested asks further down (modAsks). They were wired separately, and
// three of them called ask() directly — the seed answer page — so clicking a suggestion took
// over the whole screen on a page whose own bar answered in the dock. Rather than test the
// three by name, this walks every affordance the view model offers and requires all of them
// to route the same way the bar does.

const AFFORDANCES = ['abChips', 'modAsks'];

const routeOf = (controller, fire) => {
  const called = [];
  controller.ccAsk = (q) => { called.push('ccAsk'); return Promise.resolve(); };
  controller.ask = (q) => { called.push('ask'); };
  fire();
  return called[0] || 'none';
};

[...DOCK_MODULES.map((m) => ({ state: { view: 'module', module: m }, what: MODULES[m].name })),
 { state: { view: 'cc' }, what: 'Compliance' },
 { state: { view: 'vp' }, what: 'Vendors' },
 { state: { view: 'buildings' }, what: 'Buildings' }
].forEach(({ state, what }) => {
  test(`every suggested question on ${what} lands where its ask bar lands`, () => {
    c.setState(state);
    const vals = c.renderVals();
    const barRoute = routeOf(c, () => { c.setState({ pq: 'q' }); c.renderVals().pqRun(); });

    AFFORDANCES.forEach((key) => {
      (vals[key] || []).forEach((chip) => {
        assert.equal(routeOf(c, chip.run), barRoute,
          `${what}: ${key} "${chip.label}" goes to ${routeOf(c, chip.run)}, the bar goes to ${barRoute}`);
      });
    });
  });
});

test('the suggestions are actually there to be checked, on every dock page', () => {
  // A page that offered none would pass the agreement test vacuously.
  DOCK_MODULES.forEach((m) => {
    c.setState({ view: 'module', module: m });
    const vals = c.renderVals();
    assert.ok((vals.abChips || []).length > 0, `${MODULES[m].name} offers no "Ask this page" chips`);
    assert.ok((vals.modAsks || []).length > 0, `${MODULES[m].name} offers no suggested asks`);
  });
});

// ── the pages that already worked must not change ────────────────────────────────────

['cc', 'vp', 'buildings', 'insp'].forEach((view) => {
  test(`the ${view} page still answers in its dock`, () => {
    c.setState({ view: view, ccBusy: true, orchOpen: true });
    assert.equal(c.dockAnswers(), true);
    assert.equal(c.renderVals().orchChatShow, true);
  });
});

test('the chat page is still the conversation itself, not a dock', () => {
  c.setState({ view: 'chat', ccBusy: true });
  assert.equal(c.dockAnswers(), false, 'the chat page has no dock beside it');
  assert.equal(c.renderVals().orchChatShow, true, 'but it is still a conversation');
});

test('home chats only once its dock is open', () => {
  c.setState({ view: 'home', ccBusy: true, orchOpen: false });
  assert.equal(c.renderVals().orchChatShow, false, 'the home bar is not a transcript on its own');
  c.setState({ orchOpen: true });
  assert.equal(c.renderVals().orchChatShow, true);
});

test('a module with no dock of its own still opens the chat page', () => {
  onModule('something-else');
  assert.equal(c.dockAnswers(), false);
});

// ── the answer knows which page it was asked from ────────────────────────────────────

test('an assets question tells the orchestrator it came from the assets page', () => {
  onModule('assets');
  assert.match(c.chatContext(), /assets/i);
});

test('a maintenance question tells the orchestrator it came from the maintenance page', () => {
  onModule('ops');  // Maintenance is keyed "ops"
  assert.match(c.chatContext(), /maintenance/i);
});

test('the energy context is still energy, not a generic module line', () => {
  // isEnergyDock stays energy-only: this branch describes EUI and anomaly figures, and
  // firing it for Assets would tell the orchestrator about a page the user is not on.
  onModule('energy');
  assert.match(c.chatContext(), /Energy module page/);
});


// ── the inspection ask box goes to the orchestrator, like every other page's ─────────

test('the Maintenance inspection box asks the orchestrator in the dock, not the phrase matcher', () => {
  onModule('ops');
  const asked = [], matched = [];
  const realAsk = c.ccAsk, realMx = c.mxAsk;
  c.ccAsk = (q) => { asked.push(q); };
  c.mxAsk = (q) => { matched.push(q); };
  try {
    c.setState({ inspDraft: 'Which PPM contracts are behind plan?' });
    c.renderVals().mxInspRun();
    assert.deepEqual(asked, ['Which PPM contracts are behind plan?']);
    assert.deepEqual(matched, [], 'POST /api/maintenance/ask is no longer what answers it');
    assert.equal(c.state.view, 'module', 'the page stays put; the answer lands beside it');
    assert.equal(c.state.inspDraft, '');
    const chip = (c.renderVals().mxInspChips || [])[0];
    if (chip) { chip.run(); assert.equal(asked.length, 2); }
  } finally { c.ccAsk = realAsk; c.mxAsk = realMx; }
});

test('an empty inspection box opens the reports page instead of asking nothing', () => {
  onModule('ops');
  const realAsk = c.ccAsk; const asked = [];
  c.ccAsk = (q) => { asked.push(q); };
  try {
    c.setState({ inspDraft: '   ' });
    c.renderVals().mxInspRun();
    assert.equal(c.state.view, 'insp');
    assert.deepEqual(asked, []);
  } finally { c.ccAsk = realAsk; }
});

test('a question from the inspection-reports page tells the orchestrator where it came from', () => {
  c.setState({ view: 'insp' });
  assert.match(c.chatContext(), /inspection-reports page/);
  assert.equal(c.ctxLabel(), 'Inspection reports');
  onModule('ops');
  assert.match(c.chatContext(), /inspection reports/i);
});
