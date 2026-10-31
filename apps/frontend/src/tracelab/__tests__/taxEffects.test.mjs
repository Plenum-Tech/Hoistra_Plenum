// taxEffects — what the taxonomy screens do when they mount and when a key is pressed, which markup tests cannot see:
// each screen reads what it shows once, when it opens (never writes), the dialogs close on Escape one at a time (the
// dialog on top first, not the drawer under it), and unmounting takes the key listener away. The effects are
// captured while the screen renders and then run against a stand-in for `document`.
import { test, beforeEach, afterEach, after } from 'node:test';
import assert from 'node:assert/strict';
import { taxonomyApi, A, S, X, settle, fresh, loaded, MODE, net, installDefaults, restoreApis } from './tax-helper.mjs';
import { cleanTaxScreens, taxScreen } from './tax-screen-helper.mjs';

const React = (await import('react')).default;
const { renderToStaticMarkup } = await import('react-dom/server');
const LB = await taxScreen('views/Labels.jsx');
const MD = await taxScreen('views/ModeDrawer.jsx');
const MG = await taxScreen('views/ModeDialogs.jsx');
const PT = await taxScreen('views/ProgressTaxonomy.jsx');
const RQ = await taxScreen('views/Requirements.jsx');
const SG = await taxScreen('views/Suggestions.jsx');

after(cleanTaxScreens);
beforeEach(installDefaults);
afterEach(restoreApis);

// Renders the screen once with its effects held back, then runs them against a document that records its listeners.
function mount(Comp, props) {
  const saved = React.useEffect;
  const effects = [];
  const deps = [];
  React.useEffect = (fn, d) => { effects.push(fn); deps.push(d); };
  try { renderToStaticMarkup(React.createElement(Comp, props)); } finally { React.useEffect = saved; }
  const listeners = [];
  const removed = [];
  const before = globalThis.document;
  const stand = { visibilityState: 'visible', addEventListener: (t, h) => listeners.push([t, h]), removeEventListener: (t, h) => removed.push([t, h]) };
  globalThis.document = stand;
  const cleanups = effects.map((fn) => fn());
  globalThis.document = before;
  return {
    deps,
    press: (key) => listeners.filter((l) => l[0] === 'keydown').forEach((l) => l[1]({ key })),
    keyListeners: () => listeners.filter((l) => l[0] === 'keydown').length,
    unmount: () => {
      const keep = globalThis.document;
      globalThis.document = stand;
      try { cleanups.forEach((c) => { if (typeof c === 'function') c(); }); } finally { globalThis.document = keep; }
    },
    removed: () => removed.filter((r) => r[0] === 'keydown').length
  };
}
const spy = (name) => { const calls = []; const real = taxonomyApi[name]; taxonomyApi[name] = async (...a) => { calls.push(a); return real(...a); }; return calls; };

test('Labels reads the grid once when it opens, and again for another company', async () => {
  const reads = spy('labelsMatrix');
  fresh(loaded({}, false), { tab: 'labels' });
  mount(LB.default, { s: S() });
  await settle();
  assert.equal(reads.length, 1);
  assert.deepEqual(net.requests, [], 'a read through the stubbed service only; nothing else is sent');
  assert.deepEqual(mount(LB.default, { s: S() }).deps[0], [S().seq], 'the read is tied to the company: a new seq reads again');
  mount(LB.default, { s: S() });
  await settle();
  assert.equal(reads.length, 1, 'already read: opening it again does not read it again');
  fresh(loaded({}, false), { tab: 'labels' });
  mount(LB.default, { s: S() });
  await settle();
  assert.equal(reads.length, 2, 'a new company starts with nothing read');
});

test('Escape closes the Labels cell panel and the listener goes away with it', () => {
  fresh(loaded({ tlMatrix: { ok: true, modes: [{ id: 'm-1', name: 'a_b_c', status: 'confirmed', level: 'technical' }], rows: [], totals: null }, tlMatrixLoadedAt: 1,
    tlMatrixCell: { turnId: 't1', modeId: 'm-1' }, tlMatrixHistory: [] }, false), { tab: 'labels' });
  const m = mount(LB.default, { s: S() });
  assert.equal(m.keyListeners(), 1);
  m.press('Enter');
  assert.deepEqual(X().tlMatrixCell, { turnId: 't1', modeId: 'm-1' });
  m.press('Escape');
  assert.equal(X().tlMatrixCell, null);
  m.unmount();
  assert.equal(m.removed(), 1);
});

const DRAWER = { mode: MODE, contract: { missing: [] }, events: [], positives: [], close_negatives: [], close_negative_candidates: [], notes: [] };

test('Escape closes the mode dialog first and the drawer under it only on the next press', () => {
  fresh(loaded({ tlModesDrawerId: 'm-1', tlModesDrawer: DRAWER }), { tab: 'progress' });
  A.modeDialog('rename', { modeId: 'm-1' });
  const drawer = mount(MD.default, { s: S() });
  const dialog = mount(MG.default, { s: S() });
  drawer.press('Escape');
  assert.equal(X().tlModesDrawerId, 'm-1', 'the drawer ignores Escape while a dialog is on top of it');
  assert.ok(X().tlModesDialog);
  dialog.press('Escape');
  assert.equal(X().tlModesDialog, null);
  const next = mount(MD.default, { s: S() });
  next.press('Escape');
  assert.equal(X().tlModesDrawerId, null);
});

test('a screen with nothing open listens for no key', () => {
  fresh(loaded(), { tab: 'progress' });
  assert.equal(mount(MD.default, { s: S() }).keyListeners(), 0);
  assert.equal(mount(MG.default, { s: S() }).keyListeners(), 0);
});

test('Progress reads the failure modes, the notes and the queue once when it opens, and the runs for the platform team only', async () => {
  const modes = spy('modes'); const notes = spy('unfiledNotes'); const queue = spy('proposals'); const runs = spy('proposalRuns');
  const real = taxonomyApi.modes;
  taxonomyApi.modes = async (...a) => Object.assign({}, await real(...a), { is_owner: false });
  fresh({}, { tab: 'progress' });
  const first = mount(PT.default, { s: S() });
  await settle();
  assert.deepEqual([modes.length, notes.length, queue.length, runs.length], [1, 1, 1, 0], 'a company admin: no proposal runs');
  assert.deepEqual(first.deps[0], [S().seq], 'tied to the company, like the Labels grid');
  mount(PT.default, { s: S() });
  await settle();
  assert.deepEqual([modes.length, notes.length, queue.length], [1, 1, 1], 'already read: opening it again reads nothing again');
  taxonomyApi.modes = real;
  fresh({}, { tab: 'progress' });
  mount(PT.default, { s: S() });
  await settle();
  assert.equal(runs.length, 1, 'the platform team’s runs are read once the list says it is the platform team');
  A.stopPolls();
});

test('Requirements reads the register when its section opens', async () => {
  const reads = spy('requirements');
  fresh(loaded({}, true), { tab: 'progress' });
  const m = mount(RQ.default, { s: S() });
  await settle();
  assert.equal(reads.length, 1);
  assert.deepEqual(m.deps[0], [S().seq]);
});

test('the open answer’s chips read its suggestions and labels once, for a reviewer, and nothing for the platform team', async () => {
  const props = spy('proposals'); const labels = spy('turnLabels');
  fresh(loaded({}, true), { tab: 'conversations', convKey: 's:c', turnId: 't1' });
  mount(SG.TurnLabels, { s: S() });
  await settle();
  assert.deepEqual([props.length, labels.length], [0, 0]);
  fresh(loaded({}, false), { tab: 'conversations', convKey: 's:c', turnId: 't1' });
  mount(SG.TurnLabels, { s: S() });
  await settle();
  assert.deepEqual([props.length, labels.length], [1, 1]);
  assert.deepEqual(props[0][0], { turn_id: 't1' });
});
