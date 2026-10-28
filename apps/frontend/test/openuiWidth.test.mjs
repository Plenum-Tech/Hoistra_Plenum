// openuiWidth — a chart's box is measured whenever it appears, not only when the chart first
// mounts (src/openui/useWidth.js).
//
// Found in the browser pass, 26 Oct 2026: a LineChart streams in before its series do, so its
// first render has no plot box to measure. The width was read once, on mount, and the chart
// stayed drawn at the 520px default inside a 680px card for the rest of the answer. The hook now
// hands out a callback ref, which measures the box whenever it is attached; observeWidth is that
// measuring, without React.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { observeWidth } from '../src/openui/useWidth.js';

function env() {
  const frames = [];
  let observer = null;
  class RO {
    constructor(cb) { this.cb = cb; observer = this; this.disconnected = false; }
    observe(el) { this.el = el; }
    disconnect() { this.disconnected = true; }
  }
  return {
    ResizeObserver: RO,
    raf: (cb) => { frames.push(cb); return frames.length; },
    caf: (id) => { if (frames[id - 1]) frames[id - 1] = null; },
    flush: () => frames.splice(0).forEach((f) => f && f()),
    resize: (w) => observer.cb([{ contentRect: { width: w } }]),
    observer: () => observer
  };
}
const box = (w) => ({ getBoundingClientRect: () => ({ width: w }) });

test('the box is measured the moment it is attached', () => {
  const seen = [];
  observeWidth(box(680.4), (w) => seen.push(w), env());
  assert.deepEqual(seen, [680]);
});

test('a later resize is reported on the next frame, once per frame', () => {
  const e = env();
  const seen = [];
  observeWidth(box(680), (w) => seen.push(w), e);
  e.resize(400);
  e.resize(410);
  assert.deepEqual(seen, [680]);
  e.flush();
  assert.deepEqual(seen, [680, 410]);
});

test('stopping disconnects the observer and drops a pending frame', () => {
  const e = env();
  const seen = [];
  const stop = observeWidth(box(680), (w) => seen.push(w), e);
  e.resize(300);
  stop();
  e.flush();
  assert.deepEqual(seen, [680]);
  assert.equal(e.observer().disconnected, true);
});

test('without ResizeObserver the first width is still reported', () => {
  const seen = [];
  const stop = observeWidth(box(512), (w) => seen.push(w), { ResizeObserver: null });
  assert.deepEqual(seen, [512]);
  assert.doesNotThrow(stop);
});

test('a box with no width yet (not laid out) reports nothing rather than zero', () => {
  const seen = [];
  observeWidth(box(0), (w) => seen.push(w), env());
  assert.deepEqual(seen, []);
});
