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

// (28 Oct 2026) Rows from the stored run (logic/corrections.js rowsFromRun) carry their name,
// model, time and cost apart, so each can sit in its own place. Stream rows have no name and
// fall back to the joined title and meta.
const named = (r) => typeof r.name === 'string';
// The landing's shape says what kind of thought it was: a stage is a ring, a model call a ring
// with a core, a tool or query a small square, and a failure a tinted disc with an x.
const shapeOf = (r) => (r.ok === false ? 'bad' : r.kind === 'llm' || r.kind === 'stage' ? 'model' : r.mono ? 'tool' : 'stage');
// A tool name breaks only after its underscores, never inside a word ("asset_open_work_orde/rs").
const breakable = (s) => String(s).split('_').flatMap((p, i, a) => (i < a.length - 1 ? [p, '_', <wbr key={i} />] : [p]));
// A tool's bracket note ("[the certificate's own asset]") goes on a line of its own. The note
// keeps its leading space, so the text read off the row is the title, character for character.
function Label({ r }) {
  const name = named(r) ? r.name : r.title;
  if (!r.mono || typeof name !== 'string') return name;
  const at = name.indexOf(' [');
  return at < 0 ? <span className="rt-tool">{breakable(name)}</span>
    : <><span className="rt-tool">{breakable(name.slice(0, at))}</span><span className="rt-tool-note">{name.slice(at)}</span></>;
}

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

      {idle ? (
        <p className="rt-empty">
          {'Ask a question and the route appears here — every stage the orchestrator ran, every tool it called, in the order it happened.'}
        </p>
      ) : (
        // Keyed on which turn is shown (not on the rows themselves) so switching turns
        // — by scroll or by a "Show the run" click — crossfades in place, while a live
        // run's rows growing under the same key never replay that swap.
        <div className="rt-body-swap" key={vals.orchTraceKey}>
          {/* The two-line clamp is on the inner span, so the box's own padding can never show
              the top of a third line (28 Oct 2026). */}
          {vals.orchTraceFor ? (
            <div className="rt-for"><span className="rt-for-q">{vals.orchTraceFor}</span></div>
          ) : null}
          <div className="rt-scroll" ref={scrollRef}>
            <ol className={live ? 'rt-list is-live' : 'rt-list'} ref={listRef} style={{ '--rt-travelled': travelled + 'px' }}>
              {rows.map((r) => (
                // (28 Oct 2026) Depth indents the body, not the row, so every landing stays on
                // the one shaft. A row margin pushed nested markers off the line.
                <li key={r.key} className={(r.mono ? 'rt-row is-tool' : 'rt-row') + (r.depth ? '' : ' is-root') + (r.ok === false ? ' is-bad' : '')}
                  style={r.depth ? { '--rt-depth': r.depth } : undefined}>
                  <span className="rt-mark" data-shape={shapeOf(r)} aria-hidden="true">
                    {r.ok === false ? <i className="ph ph-x"></i> : null}
                  </span>
                  <div className="rt-body">
                    <div className="rt-line">
                      <span className="rt-label"><Label r={r} /></span>
                      {/* The model and cost read straight after the name, as the joined title did;
                          the stylesheet sets them on a line of their own under it. */}
                      {named(r) && (r.model || r.usd) ? <span className="rt-sub">{[r.model, r.usd].filter(Boolean).join(' · ')}</span> : null}
                      {(named(r) ? r.dur : r.meta) ? <span className="rt-meta">{named(r) ? r.dur : r.meta}</span> : null}
                    </div>
                    {(r.children || []).length ? (
                      <div className="rt-queries">
                        {r.children.map((q) => (
                          <button key={q.key} type="button" className={q.ok ? 'rt-query' : 'rt-query is-bad'} onClick={q.pick} title="Correct this query">
                            <i className="ph ph-database"></i>
                            <span className="rt-query-name">{q.name}</span>
                            {q.meta ? <span className="rt-query-meta">{q.meta}</span> : null}
                            <span className="rt-query-go">{'Correct ›'}</span>
                          </button>
                        ))}
                      </div>
                    ) : null}
                    {r.detail ? <div className={r.ok === false && r.detail.startsWith('Failed: ') ? 'rt-detail is-fail' : 'rt-detail'}>{r.detail}</div> : null}
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
                  {/* (28 Oct 2026) The same two buttons, set over the row's top right. They stay
                      quiet until the row is hovered or focused (styles/runtrace.css), are still
                      reached by Tab, and always show on a touch screen. */}
                  {r.correct || r.editPlan ? (
                    <span className="rt-acts">
                      {r.correct ? <button type="button" className="rt-fix" onClick={r.correct} title="Correct this call"><i className="ph ph-pencil-simple"></i>{'Correct'}</button> : null}
                      {r.editPlan ? <button type="button" className="rt-fix" onClick={r.editPlan} title="Edit the plan"><i className="ph ph-pencil-simple"></i>{'Edit plan'}</button> : null}
                    </span>
                  ) : null}
                </li>
              ))}

              {/* The car. Announced politely so a screen reader hears the run move on
                  without the whole rail being re-read each time a landing lands. */}
              {live ? (
                <li className="rt-row is-car">
                  <span className="rt-mark" data-shape="car" aria-hidden="true"></span>
                  <div className="rt-body">
                    <div className="rt-line">
                      <span className="rt-label" aria-live="polite">{liveLabel}</span>
                    </div>
                  </div>
                </li>
              ) : null}
            </ol>
          </div>
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
