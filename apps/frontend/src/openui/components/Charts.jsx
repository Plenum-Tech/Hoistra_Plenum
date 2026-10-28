// Charts — a measure across items (bars) and a measure over time (lines), drawn by hand.
//
// No chart library: two shapes are all an answer needs, and each stays a few hundred bytes. The
// bars are plain boxes, so they are crisp at any width. The lines are SVG drawn at the measured
// width of their box (useWidth), so labels keep their size in the narrow dock.
//
// A chart only places figures; it never rewrites them. Every figure it prints is the one the
// answer wrote ("£1,200", "−4%", 0.004), and the numbers it draws to are read from those
// (shared.jsx readFigure, onOneScale).
import React, { memo, useState } from 'react';
import { inline } from '../../components/shell/markdownInline.jsx';
import { scrubInternal } from '../../logic/publicText.js';
import { arr, childProps, fmt, onOneScale, readFigure, refs, sameData } from '../shared.jsx';
import { useWidth } from '../useWidth.js';
import { AskArrow, barQuestion, PlainCodes, rowClick, useAsk } from '../ask.jsx';

// A word unit reads in the singular for exactly one: "1 job", "1 day", "1 hr"; "1 kWh" is left.
// "%" and a denominator ("/25") sit against the figure: "91%", "21.88/25".
const unitFor = (v, unit) => (v === 1 && /^[a-z]{2,}s$/i.test(unit) ? unit.slice(0, -1) : unit);
const withUnit = (v, unit) => (!unit ? fmt(v) : /^[%/]/.test(unit) ? fmt(v) + unit : fmt(v) + ' ' + unitFor(v, unit));

// One bar per item, labelled with its figure as written. Bars run from a zero line, so a negative
// figure is drawn to its left (27 Oct 2026: "-£1,200" was drawn as the longest cost, and plain
// negatives got no bar at all). No bar takes an accent: the chart cannot know whether high is
// good or bad, or which item the answer is about (the longest bar was marked while the headline
// named another). Figures that are not one measure ("23.44 / 25" beside "86.12") are listed with
// no bars rather than drawn against each other. An item with no figure keeps its row with a dash
// and no bar — never a bar at 0.
export const BarChart = memo(function BarChart({ props }) {
  const labels = arr(props.labels);
  const given = arr(props.values);
  const values = labels.map((_, i) => (i < given.length ? given[i] : null));
  const unit = props.unit ? scrubInternal(String(props.unit)) : '';
  const scaled = onOneScale(values);
  const rows = labels.map((l, i) => {
    const raw = values[i];
    // A word in a figure's place ("Exempt", "Not recorded") is printed as written, with no bar
    // (28 Oct 2026: it read "—"); a dash only where nothing was written.
    const blank = raw === null || raw === undefined || (typeof raw === 'string' && !raw.trim());
    const shown = blank ? '—' : typeof raw === 'string' ? scrubInternal(raw.trim())
      : readFigure(raw) === null ? '—' : withUnit(raw, unit);
    return { label: scrubInternal(String(l == null ? '' : l)), n: scaled ? scaled[i] : null, shown };
  });
  const hi = rows.reduce((m, r) => (r.n !== null ? Math.max(m, r.n) : m), 0);
  const lo = rows.reduce((m, r) => (r.n !== null ? Math.min(m, r.n) : m), 0);
  const range = hi - lo;
  // Where zero sits along the track: the left edge unless a figure is negative.
  const zero = range > 0 ? (-lo / range) * 100 : 0;
  const bar = (n) => {
    const w = range > 0 && n !== 0 ? Math.max(2, (Math.abs(n) / range) * 100) : 0;
    return { left: n < 0 ? Math.max(0, zero - w) : zero, width: w };
  };
  // Each bar is a row the reader can ask about: a click on its label, bar or figure asks about the
  // item, with the figure the chart shows for it (ask.jsx barQuestion), and its arrow is the same
  // question for a keyboard (28 Oct 2026). The row is a box-less wrapper (display: contents), so
  // the grid still lines every bar up on one baseline. The item's own code is part of its label
  // (PlainCodes).
  const { ready, streaming, ask } = useAsk();
  // A plain report figure, not a box: title, labels, bars from one baseline, figures (28 Oct 2026).
  return (
    <div className="oui-card oui-figure">
      <div className="oui-card-title">{inline(props.title, 'bt')}</div>
      <div className="oui-bars">
        {rows.map((r, i) => {
          const b = r.n !== null ? bar(r.n) : null;
          const q = ready ? barQuestion(r.label, r.shown) : '';
          return (
            <span key={i} className={q ? 'oui-bar-row is-ask' : 'oui-bar-row'} onClick={q ? rowClick(() => { if (!streaming) ask(q); }) : undefined}>
              <span className="oui-bar-label" title={r.label}><PlainCodes.Provider value={!!q}>{refs(r.label, 'bl' + i)}</PlainCodes.Provider></span>
              <span className="oui-bar">
                {scaled ? (
                  <span className="oui-bar-track">
                    {lo < 0 ? <span className="oui-bar-zero" style={{ left: zero + '%' }}></span> : null}
                    {b ? (
                      <span className={r.n < 0 ? 'oui-bar-fill is-neg' : 'oui-bar-fill'}
                        style={{ left: b.left + '%', width: b.width + '%', animationDelay: Math.min(i, 8) * 30 + 'ms' }}></span>
                    ) : null}
                  </span>
                ) : null}
                <span className={scaled ? 'oui-bar-value' : 'oui-bar-value is-plain'}>{r.shown}</span>
              </span>
              {q ? <AskArrow question={q} className="oui-bar-go" /> : null}
            </span>
          );
        })}
      </div>
    </div>
  );
}, sameData);

// Drawn by LineChart.
export function Series() { return null; }

// Series styles: the first is the subject (ink, solid, the heavier line), the rest step back.
const LINE = [
  { stroke: 'var(--color-text)', width: 2, dash: undefined },
  { stroke: 'var(--color-neutral-500)', width: 1.5, dash: '4 3' },
  { stroke: 'var(--color-accent)', width: 1.5, dash: undefined },
  { stroke: 'var(--color-neutral-400)', width: 1.5, dash: '1 3' }
];
const lineStyle = (k) => LINE[k] || { stroke: 'var(--color-neutral-600)', width: 1.25, dash: '2 2' };

// Two or three round-number gridlines that cover the data.
function scale(lo, hi) {
  if (!(hi > lo)) {
    const pad = Math.abs(lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
  }
  const raw = (hi - lo) / 2;
  const p = Math.pow(10, Math.floor(Math.log10(raw)));
  const f = raw / p;
  const step = (f < 1.5 ? 1 : f < 3 ? 2 : f < 7 ? 5 : 10) * p;
  const a = Math.floor(lo / step) * step;
  const b = Math.ceil(hi / step) * step;
  const ticks = [];
  for (let t = a; t <= b + step / 2; t += step) ticks.push(Number(t.toFixed(10)));
  const digits = Math.max(0, -Math.floor(Math.log10(step)));
  return { lo: a, hi: b, ticks, label: (t) => t.toFixed(digits) };
}

// The decimals a series is written to, so its figures read alike: 5.0 beside 4.1. Padding only;
// a figure is never rounded (27 Oct 2026: at most two decimals turned 0.004 into 0.00).
const decimalsOf = (values) => values.reduce((d, v) => {
  if (typeof v !== 'number' || !Number.isFinite(v)) return d;
  const s = fmt(v);
  const dot = s.indexOf('.');
  return dot > -1 && !/e/i.test(s) ? Math.max(d, s.length - dot - 1) : d;
}, 0);
const padded = (v, d) => {
  const s = fmt(v);
  const dot = s.indexOf('.');
  const have = dot < 0 ? 0 : s.length - dot - 1;
  return !d || have >= d || /e/i.test(s) ? s : (dot < 0 ? s + '.' : s) + '0'.repeat(d - have);
};

// The width a 10px axis label takes, near enough to keep labels apart (measured in Chrome with
// the app's font: "September 2025" is 81px).
const textWidth = (s) => String(s).length * 6.2;
const GAP = 6;
// Where a label at point i of n sits along the plot: the first starts at its point, the last
// ends at its point, the rest are centred on theirs.
const extentAt = (i, n, at, w) => (n > 1 && i === 0 ? [at, at + w] : n > 1 && i === n - 1 ? [at - w, at] : [at - w / 2, at + w / 2]);
const clearOf = (list, n, at, widths) => list.every((i, k) => k === 0
  || extentAt(list[k - 1], n, at(list[k - 1]), widths[list[k - 1]])[1] + GAP <= extentAt(i, n, at(i), widths[i])[0]);

// Which periods get a label on the axis: as many as fit without touching, evenly spaced, and
// always the last, the period the answer's latest figure is for. Each label is sized by its own
// text, so long periods ("September 2025") thin out sooner than short ones (27 Oct 2026: labels
// were budgeted 68px each and overlapped in the dock). When even the first and last do not fit
// side by side, only the last is labelled; the figures list names every period.
export function labelIndexes(n, span, widths) {
  if (n <= 1) return n === 1 ? [0] : [];
  const at = (i) => (i * span) / (n - 1);
  for (let step = 1; step < n; step += 1) {
    const out = [];
    for (let i = 0; i < n - 1; i += step) out.push(i);
    out.push(n - 1);
    // The label before the last gives way to the last.
    while (out.length > 2 && !clearOf(out.slice(-2), n, at, widths)) out.splice(out.length - 2, 1);
    if (clearOf(out, n, at, widths)) return out;
  }
  return [n - 1];
}

// The most periods whose figures are printed on the chart itself; more than that, they are a
// click away (Figures, below the chart).
const PRINT_MAX = 12;

// A measure over periods. Every figure reaches the reader without a pointer (27 Oct 2026: only
// the last value was printed and the rest were in a hover tooltip, out of reach of a keyboard,
// a screen reader or Ctrl+F). With few periods each value is printed by its point, the lower of
// two lines below its points, and a table of the figures sits behind the chart for screen
// readers. With more periods than fit, that table is one click away under the chart. The
// tooltip still follows the pointer.
export const LineChart = memo(function LineChart({ props }) {
  const [box, measured] = useWidth(520);
  const [hover, setHover] = useState(null);
  const labels = arr(props.labels).map((l) => scrubInternal(String(l == null ? '' : l)));
  const n = labels.length;
  const unit = props.unit ? scrubInternal(String(props.unit)) : '';
  const title = scrubInternal(String(props.title == null ? '' : props.title));
  const given = arr(props.series).map(childProps).filter(Boolean).map((s) => {
    const vals = arr(s.values);
    return { name: scrubInternal(String(s.name == null ? '' : s.name)), written: labels.map((_, i) => (i < vals.length ? vals[i] : null)) };
  });
  // Every line on one scale. Figures that are not one measure are listed, never drawn together.
  const scaled = onOneScale(given.flatMap((s) => s.written));
  const series = given.map((s, k) => ({
    ...s,
    values: s.written.map((_, i) => (scaled && Number.isFinite(scaled[k * n + i]) ? scaled[k * n + i] : null))
  }));
  const digits = decimalsOf(given.flatMap((s) => s.written));
  // A figure as written; a number padded to its series' decimals, with the unit when asked.
  const said = (k, i, u) => {
    const raw = series[k].written[i];
    if (typeof raw === 'string' && raw.trim()) return scrubInternal(raw.trim());
    if (readFigure(raw) === null) return '—';
    const p = padded(raw, digits);
    return !u ? p : /^[%/]/.test(u) ? p + u : p + ' ' + unitFor(raw, u);
  };
  const all = series.flatMap((s) => s.values).filter((v) => v !== null);

  const head = (
    <>
      <div className="oui-card-title">{inline(props.title, 'lt')}</div>
      {series.length > 1 || unit ? (
        <div className="oui-legend">
          {series.length > 1 ? series.map((s, k) => {
            const st = lineStyle(k);
            return <span key={k}><span className="oui-swatch" style={{ borderTopColor: st.stroke, borderTopStyle: st.dash ? 'dashed' : 'solid' }}></span>{s.name}</span>;
          }) : null}
          {unit ? <span className="oui-legend-unit">{unit}</span> : null}
        </div>
      ) : null}
    </>
  );
  // Every figure, period by period: for a screen reader behind a chart that prints them all,
  // otherwise in a table under it, open, which the reader may close (28 Oct 2026: closed, a long
  // line showed only its last value).
  const figures = (hidden) => {
    const table = (
      <table className={hidden ? 'oui-sr-only' : undefined}>
        {hidden ? <caption>{title}</caption> : null}
        <thead>
          <tr>
            <td></td>
            {series.map((s, k) => {
              // The legend above a visible table already says the unit; a screen reader gets it here.
              const name = s.name || title;
              return <th key={k} scope="col" className="oui-num">{name + (hidden && unit && !name.includes(unit) ? ' (' + unit + ')' : '')}</th>;
            })}
          </tr>
        </thead>
        <tbody>
          {labels.map((l, i) => (
            <tr key={i} className="oui-row">
              <th scope="row">{l}</th>
              {series.map((s, k) => <td key={k} className="oui-num">{said(k, i, '')}</td>)}
            </tr>
          ))}
        </tbody>
      </table>
    );
    if (hidden) return table;
    return (
      <details className="oui-figures" open>
        <summary><i className="ph ph-caret-right oui-chev" aria-hidden="true"></i>Figures</summary>
        <div className="oui-table"><div className="oui-scroll">{table}</div></div>
      </details>
    );
  };
  if (!n || !series.length) return <div className="oui-card oui-figure">{head}</div>;
  if (!all.length) return <div className="oui-card oui-figure">{head}{figures(false)}</div>;

  const sc = scale(Math.min(...all), Math.max(...all));
  // The box's own width, down to the dock's narrowest; the axis thins its labels to fit.
  const W = Math.max(120, measured);
  const padL = Math.max(...sc.ticks.map((t) => sc.label(t).length)) * 6.2 + 12;
  const padR = 10;
  const padT = 16;
  const span = W - padL - padR;
  const x = (i) => padL + (n === 1 ? span / 2 : (i * span) / (n - 1));

  // Where each printed figure goes: above its point, or below it on the lower of two lines.
  const place = series.map((s, k) => s.values.map((v, i) => {
    if (v === null) return null;
    const o = series.length === 2 ? series[1 - k].values[i] : null;
    return o === null || v > o || (v === o && k === 0) ? 'above' : 'below';
  }));
  const printable = series.length <= 2 && n <= PRINT_MAX && ['above', 'below'].every((row) => {
    const items = [];
    series.forEach((s, k) => place[k].forEach((p, i) => { if (p === row) items.push({ i, w: textWidth(said(k, i, unit === '%' ? '%' : '')) }); }));
    items.sort((a, b) => a.i - b.i);
    return items.every((it, j) => j === 0
      || extentAt(items[j - 1].i, n, x(items[j - 1].i), items[j - 1].w)[1] + 4 <= extentAt(it.i, n, x(it.i), it.w)[0]);
  });
  const below = printable && place.some((row) => row.includes('below'));
  const H = 156 + (below ? 14 : 0);
  const padB = 24 + (below ? 14 : 0);
  const y = (v) => padT + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - padT - padB);
  const anchor = (i) => (n > 1 && i === 0 ? 'start' : n > 1 && i === n - 1 ? 'end' : 'middle');
  const first = series[0];
  const lastIdx = first.values.map((v, i) => (v !== null ? i : -1)).filter((i) => i >= 0).pop();
  const pick = (e) => {
    const r = e.currentTarget.getBoundingClientRect();
    const i = Math.round(((e.clientX - r.left - padL) / Math.max(1, span)) * (n - 1));
    setHover(Math.min(n - 1, Math.max(0, i)));
  };
  const tipLeft = hover === null ? 0 : Math.min(W - 70, Math.max(70, x(hover)));
  return (
    <div className="oui-card oui-figure">
      {head}
      <div className="oui-chart" ref={box} onPointerLeave={() => setHover(null)}>
        <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} role="img"
          aria-label={title + ': ' + series.map((s) => s.name).join(', ') + ', ' + labels[0] + ' to ' + labels[n - 1]}
          onPointerMove={pick} onPointerDown={pick}>
          {sc.ticks.map((t, k) => (
            <g key={k}>
              <line x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} style={{ stroke: 'var(--color-divider)' }} />
              <text x={padL - 8} y={y(t) + 3.5} textAnchor="end" className="oui-axis">{sc.label(t)}</text>
            </g>
          ))}
          {labelIndexes(n, span, labels.map(textWidth)).map((i) => (
            <text key={'x' + i} x={x(i)} y={H - 6} className="oui-axis" textAnchor={anchor(i)}>{labels[i]}</text>
          ))}
          {series.map((s, k) => {
            // A missing value breaks the line: one run either side of the gap, so a period
            // with no reading is never drawn as a fall to zero. A run of one is a dot.
            const st = lineStyle(k);
            const runs = [];
            s.values.forEach((v, i) => {
              if (v === null) { runs.push(null); return; }
              const last = runs[runs.length - 1];
              const pt = [x(i), y(v)];
              if (last) last.push(pt); else runs.push([pt]);
            });
            return runs.filter(Boolean).map((run, j) => (
              <React.Fragment key={k + '-' + j}>
                <polyline points={run.map((p) => p[0].toFixed(1) + ',' + p[1].toFixed(1)).join(' ')} fill="none" strokeLinejoin="round" strokeLinecap="round"
                  style={{ stroke: st.stroke, strokeWidth: st.width, strokeDasharray: st.dash }} />
                {run.length === 1 ? <circle cx={run[0][0]} cy={run[0][1]} r="2.2" style={{ fill: st.stroke }} /> : null}
              </React.Fragment>
            ));
          })}
          {printable ? series.map((s, k) => s.values.map((v, i) => (v === null ? null : (
            <text key={'v' + k + '-' + i} x={x(i)} y={place[k][i] === 'below' ? y(v) + 13 : y(v) - 7} textAnchor={anchor(i)}
              className={k === 0 ? 'oui-axis oui-axis-strong' : 'oui-axis'}>{said(k, i, unit === '%' ? '%' : '')}</text>
          )))) : null}
          {lastIdx !== undefined && hover === null ? (
            <>
              {/* The latest figure: the one live mark on the chart, ringed off the line it ends. */}
              <circle cx={x(lastIdx)} cy={y(first.values[lastIdx])} r="4" style={{ fill: 'var(--color-accent)', stroke: 'var(--color-surface)', strokeWidth: 2 }} />
              {printable ? null : (
                <text x={x(lastIdx)} y={y(first.values[lastIdx]) - 8} textAnchor={lastIdx === n - 1 && n > 1 ? 'end' : 'middle'} className="oui-axis oui-axis-strong">{said(0, lastIdx, unit === '%' ? '%' : '')}</text>
              )}
            </>
          ) : null}
          {hover !== null ? (
            <>
              <line x1={x(hover)} x2={x(hover)} y1={padT - 4} y2={H - padB} style={{ stroke: 'var(--color-rule-strong)' }} />
              {series.map((s, k) => (s.values[hover] !== null
                ? <circle key={k} cx={x(hover)} cy={y(s.values[hover])} r="3.5" style={{ fill: 'var(--color-surface)', stroke: lineStyle(k).stroke, strokeWidth: 2 }} />
                : null))}
            </>
          ) : null}
        </svg>
        {hover !== null ? (
          <div className="oui-tip" style={{ left: tipLeft + 'px' }}>
            <div className="oui-tip-label">{labels[hover]}</div>
            {series.map((s, k) => (s.values[hover] !== null ? (
              <div key={k} className="oui-tip-row">
                {series.length > 1 ? <span className="oui-swatch" style={{ borderTopColor: lineStyle(k).stroke }}></span> : null}
                {series.length > 1 ? <span>{s.name}</span> : null}
                <b>{said(k, hover, unit)}</b>
              </div>
            ) : null))}
          </div>
        ) : null}
      </div>
      {figures(printable)}
    </div>
  );
}, sameData);
