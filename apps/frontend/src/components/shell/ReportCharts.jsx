// ReportCharts — the drawing primitives a report card uses instead of sentences.
//
// Hand-rolled: no chart library, and none wanted. Bars are HTML so their labels stay crisp
// and reflow on a phone; only the rings are SVG, where an arc is the honest shape. Colour is
// never decorative — it is severity, and it comes from the same three tokens the rest of the
// product uses, so a red bar here means what a red pill means everywhere else.
import React from 'react';

const MONO = 'ui-monospace,monospace';

// A duration axis a reader can trust: ticks at round years, gridlines behind the bars, and
// every bar measured against the same scale. Without the axis the longest bar is just "the
// longest bar"; with it, it is twenty years.
function ticksFor(maxYears) {
  if (!(maxYears > 0)) return [];
  const step = maxYears > 16 ? 5 : maxYears > 8 ? 2 : maxYears > 3 ? 1 : 0.5;
  const out = [];
  for (let t = step; t <= maxYears + 0.001; t += step) out.push(t);
  return out.slice(0, 6);
}

export function DurationBars({ rows, dense }) {
  if (!rows || !rows.length) return null;
  const maxYears = rows[0].years || 0;
  const ticks = ticksFor(maxYears);
  const pctOf = (y) => (maxYears > 0 ? Math.min(100, (y / maxYears) * 100) : 0);
  return (
    <div>
      <div style={{ position: 'relative' }}>
        {/* gridlines sit behind every bar, so the rows share one scale */}
        <div style={{ position: 'absolute', inset: '0', pointerEvents: 'none' }}>
          {ticks.map((t, i) => (
            <div key={i} style={{ position: 'absolute', left: pctOf(t) + '%', top: '0', bottom: '14px', width: '1px', background: 'var(--color-divider)', opacity: '0.55' }}></div>
          ))}
        </div>
        <div style={{ display: 'flex', flexDirection: 'column', gap: dense ? '7px' : '9px', position: 'relative' }}>
          {rows.map((r, i) => (
            <div key={i}>
              <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px', marginBottom: '3px' }}>
                <span style={{ flex: '1', minWidth: '0', fontSize: '10.5px', color: 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.label}>{r.label}</span>
                <span style={{ fontFamily: MONO, fontSize: '11px', color: r.color, flexShrink: '0' }}>{r.yearsLabel}</span>
              </div>
              <div style={{ height: dense ? '8px' : '10px', borderRadius: '5px', background: 'var(--color-neutral-900)', overflow: 'hidden' }}>
                <div style={{ height: '100%', width: r.pct + '%', borderRadius: '5px', background: r.color }}></div>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div style={{ position: 'relative', height: '13px', marginTop: '6px' }}>
        {ticks.map((t, i) => (
          <span key={i} style={{ position: 'absolute', left: pctOf(t) + '%', transform: 'translateX(-50%)', fontFamily: MONO, fontSize: '8.5px', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
            {t >= 1 ? t + 'y' : (t * 12) + 'm'}
          </span>
        ))}
        <span style={{ position: 'absolute', left: '0', fontFamily: MONO, fontSize: '8.5px', color: 'var(--color-neutral-600)' }}>{'0'}</span>
      </div>
    </div>
  );
}

// One strip, the whole register. Each segment is a status, width is its share, and the
// legend carries the counts — so "six of six lapsed" is a shape before it is a number.
export function StatusStrip({ segments, total }) {
  if (!segments || !segments.length) return null;
  return (
    <div>
      <div style={{ display: 'flex', height: '14px', borderRadius: '7px', overflow: 'hidden', background: 'var(--color-neutral-900)' }}>
        {segments.map((s, i) => (
          <div key={i} title={s.n + ' ' + s.status} style={{ width: s.pct + '%', background: s.color }}></div>
        ))}
      </div>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '10px', marginTop: '9px' }}>
        {segments.map((s, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
            <span style={{ width: '8px', height: '8px', borderRadius: '2px', background: s.color, flexShrink: '0' }}></span>
            <span style={{ fontFamily: MONO, fontSize: '12px', color: 'var(--color-text)' }}>{s.n}</span>
            <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)' }}>{s.status}</span>
          </div>
        ))}
        {typeof total === 'number' ? (
          <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-600)', marginLeft: 'auto' }}>{'of ' + total}</span>
        ) : null}
      </div>
    </div>
  );
}

// Who holds the failing duties. A holder with more than one is the concentration worth
// seeing; a row the engine could not attribute is drawn outlined rather than solid, because
// "belongs to nobody" is a different fact from "belongs to this company".
export function HolderBars({ rows }) {
  if (!rows || !rows.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '7px' }}>
      {rows.map((r, i) => (
        <div key={i} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 18px', gap: '9px', alignItems: 'center' }}>
          <div style={{ minWidth: '0' }}>
            <div style={{ fontSize: '10.5px', color: r.unattributed ? 'var(--st-warn)' : 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={r.company}>
              {r.company}
            </div>
            <div style={{ height: '7px', borderRadius: '4px', background: 'var(--color-neutral-900)', overflow: 'hidden', marginTop: '3px' }}>
              <div style={{
                height: '100%', width: r.pct + '%', borderRadius: '4px',
                background: r.unattributed ? 'transparent' : (r.concentrated ? 'var(--st-risk)' : 'var(--color-neutral-500)'),
                border: r.unattributed ? '1px dashed var(--st-warn)' : 'none', boxSizing: 'border-box'
              }}></div>
            </div>
          </div>
          <span style={{ fontFamily: MONO, fontSize: '11px', color: r.concentrated ? 'var(--st-risk)' : 'var(--color-neutral-500)', textAlign: 'right' }}>{r.n}</span>
        </div>
      ))}
    </div>
  );
}

// A count as an arc. Only drawn when the denominator is real (reportCharts.kpiDial decides);
// otherwise the number stands alone rather than sitting inside a ring that means nothing.
export function Ring({ pct, color, count, size }) {
  const S = size || 78;
  const r = (S - 10) / 2;
  const c = 2 * Math.PI * r;
  const filled = pct === null || pct === undefined ? null : Math.max(0, Math.min(100, pct));
  return (
    <div style={{ position: 'relative', width: S + 'px', height: S + 'px', flexShrink: '0' }}>
      {filled === null ? null : (
        <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} style={{ position: 'absolute', inset: '0', transform: 'rotate(-90deg)' }} aria-hidden="true">
          <circle cx={S / 2} cy={S / 2} r={r} fill="none" stroke="var(--color-neutral-900)" strokeWidth="7" />
          {filled > 0 ? (
            <circle cx={S / 2} cy={S / 2} r={r} fill="none" stroke={color} strokeWidth="7" strokeLinecap="round"
                    strokeDasharray={`${(filled / 100) * c} ${c}`} />
          ) : null}
        </svg>
      )}
      <div style={{ position: 'absolute', inset: '0', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <span style={{ fontFamily: MONO, fontSize: filled === null ? '26px' : '22px', lineHeight: '1', color: color }}>{count}</span>
      </div>
    </div>
  );
}

// The palette a distribution cycles through when its categories are not severities. Ordered
// so the first two — the ones that carry most of any real distribution — are the strongest.
const SERIES = ['var(--st-risk)', 'var(--st-warn)', 'var(--color-accent)', 'var(--st-ok)', 'var(--color-neutral-500)', 'var(--color-neutral-600)'];
const PRIORITY_TONE = {
  critical: 'var(--st-risk)', high: 'var(--st-risk)', highest: 'var(--st-risk)', urgent: 'var(--st-warn)',
  medium: 'var(--color-accent)', normal: 'var(--color-accent)', low: 'var(--st-ok)', lowest: 'var(--st-ok)',
  open: 'var(--st-warn)', closed: 'var(--st-ok)', lapsed: 'var(--st-risk)', expired: 'var(--st-risk)',
  valid: 'var(--st-ok)', pending: 'var(--st-warn)', draft: 'var(--color-neutral-500)', held: 'var(--st-risk)'
};
export const seriesColor = (value, i) =>
  PRIORITY_TONE[String(value || '').trim().toLowerCase()] || SERIES[i % SERIES.length];

// One square per real thing. Ten work orders are ten squares, six of them red — a count, a
// proportion and a severity read in a single glance, which no sentence and no bar manages at
// once. Only drawn when the total is small enough that a square still means one thing.
export function Waffle({ groups, total, columns }) {
  if (!groups || !groups.length || !(total > 0) || total > 80) return null;
  const cols = columns || Math.min(10, Math.max(5, Math.ceil(Math.sqrt(total * 1.6))));
  const cells = [];
  groups.forEach((g, gi) => {
    for (let i = 0; i < g.n; i++) cells.push({ color: g.color || seriesColor(g.value, gi), title: g.value });
  });
  return (
    <div style={{ display: 'grid', gridTemplateColumns: `repeat(${cols}, 1fr)`, gap: '4px', maxWidth: cols * 22 + 'px' }}>
      {cells.map((c, i) => (
        <div key={i} title={c.title} style={{ paddingBottom: '100%', borderRadius: '3px', background: c.color }}></div>
      ))}
    </div>
  );
}

// A proportion with the number living inside it. Bolder than the thin KPI ring: this one is
// the chart, not an ornament around a figure.
export function Donut({ groups, total, size, caption }) {
  if (!groups || !groups.length || !(total > 0)) return null;
  const S = size || 116;
  const r = (S - 18) / 2;
  const c = 2 * Math.PI * r;
  let offset = 0;
  const arcs = groups.map((g, i) => {
    const frac = g.n / total;
    const seg = { color: g.color || seriesColor(g.value, i), dash: frac * c, offset: -offset * c };
    offset += frac;
    return seg;
  });
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '15px', flexWrap: 'wrap' }}>
      <div style={{ position: 'relative', width: S + 'px', height: S + 'px', flexShrink: '0' }}>
        <svg width={S} height={S} viewBox={`0 0 ${S} ${S}`} style={{ transform: 'rotate(-90deg)' }} aria-hidden="true">
          <circle cx={S / 2} cy={S / 2} r={r} fill="none" stroke="var(--color-neutral-900)" strokeWidth="13" />
          {arcs.map((a, i) => (
            <circle key={i} cx={S / 2} cy={S / 2} r={r} fill="none" stroke={a.color} strokeWidth="13"
                    strokeDasharray={`${a.dash} ${c - a.dash}`} strokeDashoffset={a.offset} />
          ))}
        </svg>
        <div style={{ position: 'absolute', inset: '0', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center' }}>
          <span style={{ fontFamily: MONO, fontSize: '30px', lineHeight: '1', color: 'var(--color-text)' }}>{total}</span>
          {caption ? <span style={{ fontSize: '9px', letterSpacing: '0.09em', textTransform: 'uppercase', color: 'var(--color-neutral-500)', marginTop: '3px' }}>{caption}</span> : null}
        </div>
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '7px', minWidth: '0', flex: '1' }}>
        {groups.map((g, i) => (
          <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: '0' }}>
            <span style={{ width: '9px', height: '9px', borderRadius: '2px', background: g.color || seriesColor(g.value, i), flexShrink: '0' }}></span>
            <span style={{ flex: '1', minWidth: '0', fontSize: '11px', color: 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.value}>{g.value}</span>
            <span style={{ fontFamily: MONO, fontSize: '12px', color: 'var(--color-text)', flexShrink: '0' }}>{g.n}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// A labelled figure list as ranked bars — the shape an LLM's "- **Total:** 10" bullet list
// should have had all along.
export function FigureBars({ figures }) {
  if (!figures || !figures.length) return null;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '11px' }}>
      {figures.map((f, i) => (
        <div key={i}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
            <span style={{ flex: '1', minWidth: '0', fontSize: '11px', color: 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={f.label}>{f.label}</span>
            <span style={{ fontFamily: MONO, fontSize: '17px', lineHeight: '1', color: seriesColor(f.label, i) }}>{f.value}</span>
            {f.unit ? <span style={{ fontSize: '9.5px', color: 'var(--color-neutral-600)', flexShrink: '0' }}>{f.unit}</span> : null}
          </div>
          <div style={{ height: '9px', borderRadius: '5px', background: 'var(--color-neutral-900)', overflow: 'hidden', marginTop: '5px' }}>
            <div style={{ height: '100%', width: f.pct + '%', borderRadius: '5px', background: seriesColor(f.label, i) }}></div>
          </div>
        </div>
      ))}
    </div>
  );
}

// A markdown table, kept inside its card instead of pushing the layout sideways.
export function DataTable({ headers, rows, max }) {
  if (!headers || !headers.length) return null;
  const shown = typeof max === 'number' ? rows.slice(0, max) : rows;
  return (
    <div style={{ overflowX: 'auto', border: '1px solid var(--color-divider)', borderRadius: '9px', maxWidth: '100%' }}>
      <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '11px' }}>
        <thead>
          <tr>{headers.map((h, i) => (
            <th key={i} style={{ textAlign: 'left', whiteSpace: 'nowrap', padding: '8px 11px', background: 'var(--color-bg)', borderBottom: '1px solid var(--color-divider)', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: 'var(--color-neutral-500)' }}>{h}</th>
          ))}</tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={i}>
              {headers.map((h, j) => (
                <td key={j} style={{ padding: '8px 11px', borderTop: '1px solid var(--color-divider)', verticalAlign: 'top', color: j === 0 ? 'var(--color-text)' : 'var(--color-neutral-400)', fontFamily: j === 0 ? MONO : 'inherit', fontSize: j === 0 ? '10px' : '11px', maxWidth: '260px' }}>
                  <div style={{ maxHeight: '38px', overflow: 'hidden' }}>{r[j]}</div>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Severity as a shape, for a card header — so a card's weight is legible before its words.
export function SeverityPip({ color, label }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', flexShrink: '0' }}>
      <span style={{ width: '7px', height: '7px', borderRadius: '50%', background: color }}></span>
      {label ? <span style={{ fontSize: '9px', letterSpacing: '0.09em', textTransform: 'uppercase', color: color }}>{label}</span> : null}
    </span>
  );
}
