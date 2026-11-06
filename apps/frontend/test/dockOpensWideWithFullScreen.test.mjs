// The side dock opens wide, and can be made full screen (Hussain, 2 Nov 2026: "the side chat open
// this much by default and there should be an option to make it full screen").
//
// "This much" was the dock dragged to about half of the space right of the navigator — 611px on
// a 1470px-wide window with the navigator open. It used to open at the flow's narrow designed
// width (280px for a conversation) and had to be dragged wider every time. Now it opens at half
// of that space; the flow's own width stays the floor, a drag still sets a width of the reader's
// own, and a double-click goes back to the half. The header's expand button fills the content
// area with the dock and puts it back beside the page; closing the dock, leaving the page or a
// new task raised from the page also puts it back.
import { test, beforeEach } from 'node:test';
import assert from 'node:assert/strict';

globalThis.window = { location: { origin: 'http://test.local' }, scrollTo: () => {}, innerWidth: 1470 };
globalThis.fetch = () => Promise.reject(new TypeError('Failed to fetch'));
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };
globalThis.window.addEventListener = () => {};
globalThis.window.removeEventListener = () => {};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');

let c;
beforeEach(() => {
  globalThis.window.innerWidth = 1470;
  c = new HoistraLogic();
  c.setState({ signedIn: true, view: 'cc', role: 'admin', navOpen: true, orchW: null });
});
const cleanup = () => { clearInterval(c._orchTick); clearTimeout(c._tt); clearTimeout(c._homeRetry); clearTimeout(c._ccRetry); };

test('the dock opens at half the space right of the navigator', () => {
  c.setState({ orchOpen: true });
  const v = c.renderVals();
  assert.equal(v.orchWidth, '611px', '(1470 - 248) / 2: the width the reader dragged it to');
  assert.equal(v.shellPad, (248 + 611) + 'px', 'the page sits beside it, not under it');
  assert.equal(v.orchHandleLeft, (248 + 611 - 3) + 'px');
  cleanup();
});

test('with the navigator folded to its rail the half is of the wider space', () => {
  c.setState({ orchOpen: true, navOpen: false });
  assert.equal(c.renderVals().orchWidth, '709px', '(1470 - 52) / 2');
  cleanup();
});

test('a window resized follows the half (vw is the width the resize listener stores)', () => {
  c.setState({ orchOpen: true, vw: 1000 });
  assert.equal(c.renderVals().orchWidth, '376px', '(1000 - 248) / 2');
  cleanup();
});

test("a flow's own width stays the floor on a narrow window", () => {
  c.setState({ orchOpen: true, vw: 900, flow: 'investigate' });
  assert.equal(c.renderVals().orchWidth, '420px', 'half of 652 is 326, under the investigation panel\'s 420');
  cleanup();
});

test("a width the reader dragged to is kept, and a double-click goes back to the half", () => {
  c.setState({ orchOpen: true, orchW: 450 });
  let v = c.renderVals();
  assert.equal(v.orchWidth, '450px', 'a drag can now narrow it as well as widen it');
  assert.equal(v.orchHandleTint, 'var(--color-accent)', 'the handle marks a width of the reader\'s own');
  v.orchResizeReset();
  v = c.renderVals();
  assert.equal(v.orchWidth, '611px');
  assert.equal(v.orchHandleTint, 'transparent');
  cleanup();
});

test('the expand button fills the content area and puts the dock back beside the page', () => {
  c.setState({ orchOpen: true });
  let v = c.renderVals();
  assert.equal(v.orchFull, false);
  assert.equal(v.orchFullIcon, 'ph-arrows-out-simple');
  assert.equal(v.orchFullTitle, 'Expand to full screen');
  v.orchFullToggle();
  v = c.renderVals();
  assert.equal(v.orchFull, true, 'a dock opened with no task in it goes full too');
  assert.equal(v.orchWidth, 'calc(100vw - 248px)');
  assert.equal(v.shellPad, '248px', 'the page behind is not pushed over by a dock it sits under');
  assert.equal(v.orchResizeShow, 'none', 'no drag handle on a full dock');
  assert.equal(v.orchFullIcon, 'ph-arrows-in-simple');
  assert.equal(v.orchFullTitle, 'Back to the side panel');
  v.orchFullToggle();
  v = c.renderVals();
  assert.equal(v.orchFull, false);
  assert.equal(v.orchWidth, '611px');
  cleanup();
});

test('a conversation made full stays full while it is answered', () => {
  c.orch('What certificates does Bishopsgate Tower hold?', 'Compliance', [], { record: false });
  c.renderVals().orchFullToggle();
  c.setState((p) => ({ ccChat: [{ role: 'you', text: 'And which have lapsed?' }], orchDone: p.orchDone }));
  assert.equal(c.renderVals().orchFull, true);
  cleanup();
});

test('full screen asked for by the reader stays until they put it back or close the dock', () => {
  // The expand control is the reader's choice (upstream 9cd5ef5, orchFullManual): it holds across
  // a page change and a new task, and closing the dock forgets it.
  c.orch('Approve booking', 'Fire Alarm System Service Certificate', []);
  c.renderVals().orchFullToggle();
  assert.equal(c.renderVals().orchFull, true);

  c.setState({ view: 'vp' });
  assert.equal(c.renderVals().orchFull, true, 'another page keeps the reader\'s choice');
  c.setState({ view: 'cc' });

  c.orch('Request evidence', 'Bishopsgate Tower', []);
  assert.equal(c.renderVals().orchFull, true, 'so does a new task');

  c.closeOrch();
  c.setState({ orchOpen: true });
  assert.equal(c.renderVals().orchFull, false, 'a dock reopened by the page is beside the page');
  cleanup();
});
