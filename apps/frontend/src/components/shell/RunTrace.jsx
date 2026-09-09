// RunTrace — the orchestrator's sequence of thoughts, as a rail beside the conversation.
//
// One shaft, one landing per completed thought, and while a turn runs, a car at the bottom
// carrying whatever the stream last said it was doing. The shaft above the car is painted
// travelled; below it, hairline. That fill height is measured from the live DOM rather than
// guessed from a row count, because rows differ in height once a step carries its parts.
//
// Reads the orchTrace* keys from renderVals. Styling is in styles/runtrace.css — this is
// the one surface with real state, hover, focus and motion, which inline style objects
// cannot carry.
import React, { useLayoutEffect, useRef, useState } from 'react';

export default function RunTrace({ vals }) {
  const listRef = useRef(null);
  const scrollRef = useRef(null);
  const [travelled, setTravelled] = useState(0);

  const rows = vals.orchTraceRows || [];
  const live = !!vals.orchTraceLive;
  const liveLabel = vals.orchTraceLiveLabel;

  // The shaft stops at the centre of the last marker — the car when a turn is running, the
  // final landing when it has finished. 14px is where the shaft starts in the stylesheet.
  useLayoutEffect(() => {
    const el = listRef.current;
    if (!el) return;
    const marks = el.querySelectorAll('.rt-mark');
    if (!marks.length) { setTravelled(0); return; }
    const last = marks[marks.length - 1];
    const box = last.getBoundingClientRect();
    const top = el.getBoundingClientRect().top;
    setTravelled(Math.max(0, (box.top + box.height / 2) - top - 14));
  }, [rows.length, live, liveLabel]);

  // Keep the newest thought in view without dragging the page with it.
  useLayoutEffect(() => {
    const el = scrollRef.current;
    // Glide to the newest step rather than jumping; reduced-motion readers get the jump.
    if (el && live) {
      const reduce = typeof window !== "undefined" && window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
      if (el.scrollTo) el.scrollTo({ top: el.scrollHeight, behavior: reduce ? "auto" : "smooth" });
      else el.scrollTop = el.scrollHeight;
    }
  }, [rows.length, live, liveLabel]);

  const idle = !live && !rows.length;

  return (
    <aside className={idle ? 'rt is-idle' : 'rt'} aria-label="Run trace">
      <div className="rt-head">
        <span className={live ? 'rt-title is-live' : 'rt-title'}>{vals.orchTraceTitle}</span>
        <span className="rt-clock">
          {vals.orchTraceElapsed}
        </span>
      </div>

      {vals.orchTraceFor ? (
        <div className="rt-for">{vals.orchTraceFor}</div>
      ) : null}

      {idle ? (
        <p className="rt-empty">
          {'Ask a question and the route appears here — every stage the orchestrator ran, every tool it called, in the order it happened.'}
        </p>
      ) : (
        <div className="rt-scroll" ref={scrollRef}>
          <ol className={live ? 'rt-list is-live' : 'rt-list'} ref={listRef} style={{ '--rt-travelled': travelled + 'px' }}>
            {rows.map((r) => (
              <li key={r.key} className={r.mono ? 'rt-row is-tool' : 'rt-row'}>
                <span className="rt-mark" aria-hidden="true">
                  <i className={`ph ${r.icon}`}></i>
                </span>
                <div className="rt-body">
                  <div className="rt-line">
                    <span className="rt-label">{r.title}</span>
                    {r.meta ? <span className="rt-meta">{r.meta}</span> : null}
                  </div>
                  {r.detail ? <div className="rt-detail">{r.detail}</div> : null}
                  {r.parts.length ? (
                    <div className="rt-parts">
                      {r.parts.map((p) => (
                        <div key={p.key} className="rt-part">
                          {p.text}
                          {p.scope ? <span className="rt-part-scope">{' — ' + p.scope}</span> : null}
                        </div>
                      ))}
                    </div>
                  ) : null}
                  {r.issues.map((x, j) => (
                    <div key={j} className="rt-issue">{x}</div>
                  ))}
                </div>
              </li>
            ))}

            {/* The car. Announced politely so a screen reader hears the run move on
                without the whole rail being re-read each time a landing lands. */}
            {live ? (
              <li className="rt-row is-car">
                <span className="rt-mark" aria-hidden="true">
                  <i className="ph ph-caret-double-down"></i>
                </span>
                <div className="rt-body">
                  <div className="rt-line">
                    <span className="rt-label" aria-live="polite">{liveLabel}</span>
                  </div>
                </div>
              </li>
            ) : null}
          </ol>
        </div>
      )}

      {live ? (
        <div className="rt-foot">
          <span className="rt-note">{'Nothing is written back without your decision.'}</span>
          <button type="button" className="rt-btn is-stop" onClick={vals.orchTraceStop}>{'Stop'}</button>
        </div>
      ) : vals.orchTraceUnpinShow ? (
        <div className="rt-foot">
          <span className="rt-note">{'Showing an earlier run.'}</span>
          <button type="button" className="rt-btn" onClick={vals.orchTraceUnpin}>{'Follow latest'}</button>
        </div>
      ) : null}
    </aside>
  );
}
