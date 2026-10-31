// framePainter — paint a streaming answer at most once a frame, and no faster than minGap.
//
// Every setState re-renders the whole app (logic/useHoistra.js), and the orchestrator streams its
// answer a few characters at a time: painting per delta meant a full render per token, which is
// what made a long answer stutter as it arrived. Deltas now only mark the draft dirty; one
// animation frame paints whatever has arrived, and frames closer together than minGap (40 ms,
// about 25 paints a second, smooth for text) wait for the next one.
//
// flush() paints anything pending at once; cancel() drops it. A turn that ends must cancel, or a
// frame already queued would paint the draft back after the answer replaced it.

export function createFramePainter(paint, opts = {}) {
  const raf = opts.raf || ((cb) => (typeof requestAnimationFrame === 'function' ? requestAnimationFrame(cb) : setTimeout(cb, 16)));
  const caf = opts.caf || ((id) => (typeof cancelAnimationFrame === 'function' ? cancelAnimationFrame(id) : clearTimeout(id)));
  const now = opts.now || (() => (typeof performance !== 'undefined' ? performance.now() : Date.now()));
  const gap = opts.minGap == null ? 40 : opts.minGap;
  let id = 0;
  let dirty = false;
  let last = -Infinity;

  const tick = () => {
    id = 0;
    if (!dirty) return;
    if (now() - last < gap) { id = raf(tick); return; }
    dirty = false;
    last = now();
    paint();
  };

  return {
    request() {
      dirty = true;
      if (!id) id = raf(tick);
    },
    flush() {
      if (id) { caf(id); id = 0; }
      if (dirty) { dirty = false; last = now(); paint(); }
    },
    cancel() {
      if (id) { caf(id); id = 0; }
      dirty = false;
    }
  };
}
