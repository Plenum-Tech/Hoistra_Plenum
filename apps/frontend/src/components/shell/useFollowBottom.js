// useFollowBottom — keep the newest part of a growing transcript in view, smoothly.
//
// While an answer streams in, the page (or the dock's scroll region) grows below the fold.
// This follows the growth the way a chat client does: an animation-frame loop eases the
// scroll position toward the bottom a little each frame, so the transcript glides rather
// than jumping in steps with every token. It runs only while there is distance to cover or
// an answer is still being produced, and it is a single read and a single write per frame.
//
// The reader stays in charge. A wheel or key upward, or a touch drag, stops the following at
// once; scrolling back to the bottom, a new message, or the next question resumes it. Nothing
// here is React state — scrolling never causes a re-render — so it cannot loop. Reduced-motion
// users get an instant snap instead of the glide.
//
//   container  ref to the element that scrolls, or null when the window scrolls
//   end        kept for callers that place a marker after the last message; the follow
//              targets the bottom of the scroll area, so the marker is not required
//   busy       true while a turn is being answered — a new turn re-engages following
//   count      number of messages in the transcript — a new message re-engages following
import { useEffect, useRef } from 'react';

// "Near the bottom": within `threshold` pixels of the end of the scrollable content.
export function nearBottom(scrollTop, clientHeight, scrollHeight, threshold) {
  return scrollHeight - (scrollTop + clientHeight) <= (threshold === undefined ? 140 : threshold);
}

// One frame of the glide: a fifth of the remaining distance, never less than a pixel, and a
// snap once a pixel is all that is left. Exponential approach — fast when far, gentle when close.
export function easeStep(top, target, factor) {
  const f = factor === undefined ? 0.2 : factor;
  const dist = target - top;
  if (Math.abs(dist) <= 1) return target;
  const step = dist * f;
  return top + (Math.abs(step) < 1 ? (dist > 0 ? 1 : -1) : step);
}

export function useFollowBottom({ container, end, busy, count }) { // eslint-disable-line no-unused-vars
  const stuck = useRef(true);
  const busyRef = useRef(false);
  const followRef = useRef(null);
  const lastBusy = useRef(false);
  const lastCount = useRef(-1);
  busyRef.current = !!busy;

  useEffect(() => {
    if (typeof window === "undefined") return undefined;
    const el = container ? container.current : null;
    const target = el || window;
    const reduce = !!(window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches);
    let raf = 0;
    let animating = false;
    let wrote = -1;

    const read = () => (el
      ? { top: el.scrollTop, max: el.scrollHeight - el.clientHeight, ch: el.clientHeight, sh: el.scrollHeight }
      : { top: window.scrollY || document.documentElement.scrollTop || 0, max: document.documentElement.scrollHeight - window.innerHeight, ch: window.innerHeight, sh: document.documentElement.scrollHeight });
    const write = (v) => { animating = true; wrote = v; if (el) el.scrollTop = v; else window.scrollTo(0, v); };

    const tick = () => {
      raf = 0;
      if (!stuck.current) { animating = false; return; }
      const m = read();
      const max = Math.max(0, m.max);
      const dist = max - m.top;
      if (Math.abs(dist) > 0.5) write(reduce ? max : easeStep(m.top, max));
      else animating = false;
      // Keep gliding while the answer is still growing; otherwise come to rest at the bottom.
      if (busyRef.current || Math.abs(dist) > 0.5) raf = requestAnimationFrame(tick);
    };
    const follow = () => { if (stuck.current && !raf) raf = requestAnimationFrame(tick); };
    followRef.current = follow;

    // Reader intent. Upward wheel, key or touch drag → stop following now. Downward wheel
    // that lands near the bottom → follow again.
    const onWheel = (e) => {
      if (e.deltaY < 0) { stuck.current = false; return; }
      const m = read();
      if (nearBottom(m.top, m.ch, m.sh)) { stuck.current = true; follow(); }
    };
    const onKey = (e) => { if (e.key === "ArrowUp" || e.key === "PageUp" || e.key === "Home") stuck.current = false; };
    let touchY = null;
    const onTouchStart = (e) => { touchY = e.touches && e.touches[0] ? e.touches[0].clientY : null; };
    const onTouchMove = (e) => {
      const y = e.touches && e.touches[0] ? e.touches[0].clientY : null;
      if (touchY !== null && y !== null && y > touchY + 4) stuck.current = false;   // finger moving down = content scrolling up
      touchY = y;
    };
    // Scroll events: ours land where we wrote them and are ignored; anyone else's (scrollbar
    // drag, momentum) decides by where it ended up.
    const onScroll = () => {
      const m = read();
      if (animating && Math.abs(m.top - wrote) < 2) { animating = false; return; }
      animating = false;
      if (nearBottom(m.top, m.ch, m.sh)) { if (!stuck.current) { stuck.current = true; follow(); } }
      else stuck.current = false;
    };
    target.addEventListener("wheel", onWheel, { passive: true });
    target.addEventListener("touchstart", onTouchStart, { passive: true });
    target.addEventListener("touchmove", onTouchMove, { passive: true });
    target.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("keydown", onKey);

    // Content that grows between renders (streamed text, late layout) starts the glide too.
    const watched = el || document.body;
    const mo = typeof MutationObserver !== "undefined" && watched
      ? new MutationObserver(() => follow()) : null;
    if (mo) mo.observe(watched, { childList: true, subtree: true, characterData: true });

    follow();
    return () => {
      if (raf) cancelAnimationFrame(raf);
      if (mo) mo.disconnect();
      target.removeEventListener("wheel", onWheel);
      target.removeEventListener("touchstart", onTouchStart);
      target.removeEventListener("touchmove", onTouchMove);
      target.removeEventListener("scroll", onScroll);
      window.removeEventListener("keydown", onKey);
      followRef.current = null;
    };
    // The dock's region and the window both exist for the life of the component.
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // A new turn, or a new message, means the reader wants to see what arrives next.
  useEffect(() => {
    if ((busy && !lastBusy.current) || count > lastCount.current) {
      stuck.current = true;
      if (followRef.current) followRef.current();
    }
    lastBusy.current = !!busy;
    lastCount.current = count;
  }, [busy, count]);

  // After every render, nudge the glide if it has stopped and there is new distance to cover.
  useEffect(() => { if (followRef.current) followRef.current(); });
}
