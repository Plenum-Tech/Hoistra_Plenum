// useTraceSpy — which answered turn the run-trace rail should show, driven by scroll.
//
// The rail is a sticky sidebar; the conversation scrolls past it. As the reader scrolls,
// whichever traced turn's bubble has crossed a line just under the rail's own top becomes
// the one the rail shows — the same state a manual "Show the run" click writes, so the two
// can never disagree. Elements are looked up by a `data-trace-turn` marker rather than kept
// in a ref list, since the conversation's turns come and go with the transcript itself.
//
//   follow   (idx: number) => void — called whenever the focused turn changes
//   deps     recompute immediately when any of these change (a new turn landing, busy
//            toggling) — scrolling alone would eventually catch up, but a fresh answer
//            appearing above the fold should update the rail without waiting for one
import { useEffect } from 'react';

// Distance from the top of the viewport the sticky rail's own header sits at (see
// `.rt { top: 78px; }` in runtrace.css), plus a little room to read into a bubble before
// treating it as "in view" rather than "about to scroll past".
const FOCUS_LINE = 130;

export function useTraceSpy(follow, deps) {
  useEffect(() => {
    if (typeof window === 'undefined' || typeof follow !== 'function') return undefined;
    let raf = 0;
    const compute = () => {
      raf = 0;
      const nodes = document.querySelectorAll('[data-trace-turn]');
      if (!nodes.length) return;
      // The last node whose top has already crossed the focus line — i.e. the turn the
      // reader has scrolled down into. Above the first one yet, default to it anyway: an
      // empty rail while sitting just above the first question reads as broken, not idle.
      let active = nodes[0];
      for (let i = 0; i < nodes.length; i += 1) {
        if (nodes[i].getBoundingClientRect().top <= FOCUS_LINE) active = nodes[i];
        else break;
      }
      const idx = Number(active.getAttribute('data-trace-turn'));
      if (Number.isFinite(idx)) follow(idx);
    };
    const onScroll = () => { if (!raf) raf = requestAnimationFrame(compute); };
    window.addEventListener('scroll', onScroll, { passive: true });
    window.addEventListener('resize', onScroll, { passive: true });
    compute();
    return () => {
      window.removeEventListener('scroll', onScroll);
      window.removeEventListener('resize', onScroll);
      if (raf) cancelAnimationFrame(raf);
    };
    // `deps` is the caller's explicit recompute trigger; `follow` itself is stable in effect
    // (it only ever closes over the controller, never per-render locals).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
}
