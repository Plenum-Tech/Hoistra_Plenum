// useWidth — the rendered width of an element, so a chart can be drawn in real pixels.
//
// An SVG scaled by viewBox shrinks its text with it: readable on the chat page, too small to read
// in the narrow dock. Measuring the box and drawing at its true width keeps 10px labels 10px
// everywhere.
//
// The hook hands out a callback ref, so the box is measured whenever it is attached, not only
// when the component mounts: a chart that streams in before its data first renders without a
// plot box, and a width read once on mount stayed at the default for the rest of the answer
// (test/openuiWidth.test.mjs). Ref callbacks run before paint, so there is no frame at the wrong
// width; after that, the width changes only when the box really does, at most once a frame.
import { useCallback, useEffect, useRef, useState } from 'react';

const hasRO = () => typeof ResizeObserver !== 'undefined';

// Report el's width now and on every resize (one report per frame). Returns a stop function.
export function observeWidth(el, onWidth, env = {}) {
  const RO = 'ResizeObserver' in env ? env.ResizeObserver : (hasRO() ? ResizeObserver : null);
  const raf = env.raf || ((cb) => requestAnimationFrame(cb));
  const caf = env.caf || ((id) => cancelAnimationFrame(id));
  const now = Math.round(el.getBoundingClientRect().width);
  if (now > 0) onWidth(now);
  if (!RO) return () => {};
  let frame = 0;
  const ro = new RO((entries) => {
    const w = Math.round(entries[0].contentRect.width);
    caf(frame);
    frame = raf(() => { frame = 0; if (w > 0) onWidth(w); });
  });
  ro.observe(el);
  return () => { caf(frame); ro.disconnect(); };
}

export function useWidth(initial) {
  const [width, setWidth] = useState(initial);
  const stop = useRef(null);
  const ref = useCallback((el) => {
    if (stop.current) { stop.current(); stop.current = null; }
    if (el) stop.current = observeWidth(el, (w) => setWidth((p) => (Math.abs(p - w) > 1 ? w : p)));
  }, []);
  useEffect(() => () => { if (stop.current) stop.current(); }, []);
  return [ref, width];
}
