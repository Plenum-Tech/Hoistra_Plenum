// ReportCards — a report's answer as a grid of cards, plus the tray they go to.
//
// One long column reads as a letter; a grid reads as a report. Every card carries the same
// anatomy — a label, its content, and a quiet control to put it away — and the tray beside
// the grid holds what has been put away until it is wanted again. `vals` is the view model
// (logic/renderVals.js builds reportBlocks / reportTray*; logic/reportCards.js decides what
// a card is and what its stable key is).
import React from 'react';
import Markdown from './Markdown.jsx';
import { DurationBars, StatusStrip, HolderBars, Ring, SeverityPip, Waffle, Donut, FigureBars, DataTable, seriesColor } from './ReportCharts.jsx';

const LABEL = { fontSize: '9.5px', letterSpacing: '0.11em', textTransform: 'uppercase', color: 'var(--color-neutral-500)' };

// Text does not disappear, it steps back: clamped to a few lines with the rest one click
// away. The user asked for less of it, not for less information.
const clamp = (lines) => ({ display: '-webkit-box', WebkitLineClamp: String(lines), WebkitBoxOrient: 'vertical', overflow: 'hidden' });

function More({ open, onClick, label }) {
  return (
    <div className="hv6" onClick={onClick} style={{ fontSize: '10.5px', color: 'var(--color-accent)', cursor: 'pointer', marginTop: '8px', display: 'inline-flex', alignItems: 'center', gap: '4px' }}>
      <i className={`ph ${open ? 'ph-caret-up' : 'ph-caret-down'}`} style={{ fontSize: '10px' }}></i>
      {open ? 'Less' : label}
    </div>
  );
}

const SEV = {
  critical: { fg: 'var(--st-risk)', bg: 'var(--st-risk-bg)' },
  warning: { fg: 'var(--st-warn)', bg: 'var(--st-warn-bg)' },
  info: { fg: 'var(--color-accent)', bg: 'var(--color-accent-900)' },
  ok: { fg: 'var(--st-ok)', bg: 'var(--st-ok-bg)' }
};
const sev = (s) => SEV[String(s || '').toLowerCase()] || { fg: 'var(--color-neutral-400)', bg: 'var(--color-bg)' };

const KIND_ICON = {
  duration: 'ph-chart-bar-horizontal', status: 'ph-squares-four', holders: 'ph-buildings',
  narrative: 'ph-text-align-left', kpi: 'ph-gauge', action: 'ph-lightning', group: 'ph-buildings',
  insight: 'ph-pulse', certificates: 'ph-certificate', pending: 'ph-hourglass-medium',
  markdown: 'ph-article', gone: 'ph-prohibit', figures: 'ph-chart-bar',
  distribution: 'ph-squares-four', table: 'ph-table'
};

// A card whose subject carries a severity wears it as a hairline down its left edge, so the
// grid has a weight to it before a single word is read.
const ACCENT = { action: true, group: true, insight: true, duration: true };
function accentOf(c) {
  if (!ACCENT[c.kind]) return null;
  if (c.kind === 'duration') return (c.data && c.data.worst && c.data.worst.color) || null;
  const s = String((c.data && (c.data.severity || c.data.type)) || '').toLowerCase();
  if (s === 'anomaly') return 'var(--st-warn)';
  if (s === 'risk' || s === 'critical') return 'var(--st-risk)';
  if (s === 'warning') return 'var(--st-warn)';
  return null;
}

function Body({ c }) {
  const d = c.data || {};

  // ── the hero: how long, on one axis ───────────────────────────────────────
  if (c.kind === 'duration') {
    const w = d.worst;
    return (
      <div>
        {w ? (
          <div style={{ display: 'flex', alignItems: 'baseline', gap: '9px', marginBottom: '12px', flexWrap: 'wrap' }}>
            <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '28px', lineHeight: '1', color: w.color }}>{w.yearsLabel}</span>
            <span style={{ fontSize: '11.5px', color: 'var(--color-neutral-400)', minWidth: '0' }}>
              {'worst — ' + w.label}
            </span>
          </div>
        ) : null}
        <DurationBars rows={d.rows} />
      </div>
    );
  }

  if (c.kind === 'status') return <StatusStrip segments={d.segments} total={d.total} />;
  if (c.kind === 'holders') return <HolderBars rows={d.rows} />;
  if (c.kind === 'figures') return <FigureBars figures={d.figures} />;

  // A column's distribution. Small enough to count → one square per row, because a square
  // that stands for exactly one work order is the most literal chart there is. Larger →
  // a donut, where the proportion is the point and the individual row is not.
  if (c.kind === 'distribution') {
    const groups = (d.rows || []).map((r, i) => ({ value: r.value, n: r.n, color: seriesColor(r.value, i) }));
    if (d.total <= 40) {
      return (
        <div>
          <Waffle groups={groups} total={d.total} />
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '13px' }}>
            {groups.map((g, i) => (
              <div key={i} style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: '0' }}>
                <span style={{ width: '9px', height: '9px', borderRadius: '2px', background: g.color, flexShrink: '0' }}></span>
                <span style={{ flex: '1', minWidth: '0', fontSize: '11px', color: 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={g.value}>{g.value}</span>
                <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '12px', color: 'var(--color-text)' }}>{g.n}</span>
              </div>
            ))}
          </div>
        </div>
      );
    }
    return <Donut groups={groups} total={d.total} caption={d.column} />;
  }

  if (c.kind === 'table') {
    const rows = d.rows || [];
    const cap = c.open ? rows.length : 5;
    return (
      <div>
        <DataTable headers={d.headers} rows={rows} max={cap} />
        {rows.length > 5 ? <More open={c.open} onClick={c.toggleOpen} label={'All ' + rows.length + ' rows'} /> : null}
      </div>
    );
  }

  if (c.kind === 'kpi') {
    const dial = d.dial || {};
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: '13px' }}>
        <Ring pct={dial.pct} color={dial.color} count={dial.count} />
        <div style={{ minWidth: '0' }}>
          {dial.pct !== null && dial.pct !== undefined && dial.denom ? (
            <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '10px', color: 'var(--color-neutral-500)' }}>
              {'of ' + dial.denom}
            </div>
          ) : null}
          {d.sublabel ? (
            <div style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)', lineHeight: '1.4', marginTop: '4px', ...clamp(3) }}>{d.sublabel}</div>
          ) : null}
        </div>
      </div>
    );
  }

  if (c.kind === 'narrative') {
    return (
      <div>
        <div style={{ fontSize: '12px', lineHeight: '1.6', color: 'var(--color-neutral-300)', textWrap: 'pretty', ...(c.open ? {} : clamp(4)) }}>{d.text}</div>
        <More open={c.open} onClick={c.toggleOpen} label="Read it all" />
      </div>
    );
  }

  if (c.kind === 'action') {
    const t = sev(d.severity);
    return (
      <div>
        <SeverityPip color={t.fg} label={[d.severity, d.scope].filter(Boolean).join(' · ')} />
        <div style={{ fontSize: '12.5px', lineHeight: '1.45', marginTop: '7px', ...clamp(3) }}>{d.title}</div>
        {(d.tags || []).length ? (
          <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginTop: '9px' }}>
            {d.tags.map((g, j) => (
              <span key={j} style={{ fontSize: '9.5px', padding: '3px 7px', borderRadius: '5px', background: 'var(--color-bg)', border: '1px solid var(--color-divider)', color: 'var(--color-neutral-500)' }}>{g}</span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  if (c.kind === 'group') {
    const t = sev(d.severity);
    const points = d.points || [];
    const shown = c.open ? points : points.slice(0, 1);
    return (
      <div>
        {d.headline ? <div style={{ fontSize: '11.5px', color: t.fg, lineHeight: '1.45', ...clamp(2) }}>{d.headline}</div> : null}
        {points.length ? (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '10px' }}>
            {shown.map((p, j) => (
              <div key={j} style={{ display: 'grid', gridTemplateColumns: '6px minmax(0,1fr)', gap: '8px', fontSize: '11px', color: 'var(--color-neutral-400)', lineHeight: '1.5' }}>
                <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: t.fg, marginTop: '5px' }}></span>
                <span style={c.open ? {} : clamp(2)}>{p}</span>
              </div>
            ))}
          </div>
        ) : null}
        {points.length > 1 ? <More open={c.open} onClick={c.toggleOpen} label={'+' + (points.length - 1) + ' more'} /> : null}
        {(d.offers || []).length ? (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '11px', paddingTop: '9px', borderTop: '1px solid var(--color-divider)' }}>
            {d.offers.map((o, j) => (
              <span key={j} className="hv13" onClick={o.run} title={o.reason || ''} style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10.5px', padding: '5px 9px', borderRadius: '7px', border: '1px solid var(--color-divider)', background: 'var(--color-bg)', color: 'var(--color-neutral-300)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                <i className="ph ph-arrow-right" style={{ fontSize: '10px', color: 'var(--color-accent)' }}></i>{o.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  if (c.kind === 'insight') {
    return (
      <div>
        <div style={{ fontSize: '11px', lineHeight: '1.55', color: 'var(--color-neutral-400)', ...(c.open ? {} : clamp(3)) }}>{d.text || ''}</div>
        <More open={c.open} onClick={c.toggleOpen} label="Read it all" />
      </div>
    );
  }

  // The table is the evidence layer, not the headline — the status strip and holder bars
  // above already say what it adds up to, so it opens on request.
  if (c.kind === 'certificates') {
    const rows = d.rows || [];
    if (!c.open) {
      return (
        <div>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: '5px' }}>
            {rows.slice(0, 8).map((r, i) => (
              <span key={i} title={r.name + ' — ' + (r.company || '')} style={{ width: '9px', height: '9px', borderRadius: '2px', background: sev(r.severity).fg, opacity: '0.85' }}></span>
            ))}
          </div>
          <More open={false} onClick={c.toggleOpen} label={'Show the ' + rows.length + ' rows'} />
        </div>
      );
    }
    return (
      <div>
      <div style={{ overflowX: 'auto', border: '1px solid var(--color-divider)', borderRadius: '8px' }}>
        <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '11px' }}>
          <thead>
            <tr>{['Certificate', 'Held by', 'Scope', 'Status'].map((h) => (
              <th key={h} style={{ textAlign: 'left', whiteSpace: 'nowrap', padding: '7px 10px', background: 'var(--color-bg)', borderBottom: '1px solid var(--color-divider)', ...LABEL }}>{h}</th>
            ))}</tr>
          </thead>
          <tbody>
            {(d.rows || []).map((r, i) => {
              const t = sev(r.severity);
              return (
                <tr key={r.id || i}>
                  <td style={{ padding: '7px 10px', borderTop: '1px solid var(--color-divider)', verticalAlign: 'top' }}>
                    <div style={{ display: 'flex', gap: '7px', alignItems: 'baseline' }}>
                      <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: t.fg, flexShrink: '0', marginTop: '4px' }}></span>
                      <div style={{ minWidth: '0' }}>
                        <div>{r.name}</div>
                        {r.reason ? <div style={{ fontSize: '10px', color: 'var(--color-neutral-500)', marginTop: '2px' }}>{r.reason}</div> : null}
                      </div>
                    </div>
                  </td>
                  <td style={{ padding: '7px 10px', borderTop: '1px solid var(--color-divider)', verticalAlign: 'top' }}>{r.company}</td>
                  <td style={{ padding: '7px 10px', borderTop: '1px solid var(--color-divider)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{r.scope}</td>
                  <td style={{ padding: '7px 10px', borderTop: '1px solid var(--color-divider)', whiteSpace: 'nowrap', verticalAlign: 'top', color: t.fg }}>{r.status}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <More open={true} onClick={c.toggleOpen} label="" />
      </div>
    );
  }
  if (c.kind === 'pending') {
    return (
      <div>
        <div style={{ fontSize: '11px', color: 'var(--color-neutral-500)', lineHeight: '1.5', ...clamp(2) }}>{d.what_is_pending}</div>
        {(d.offers || []).length ? (
          <div style={{ display: 'flex', gap: '6px', flexWrap: 'wrap', marginTop: '10px' }}>
            {d.offers.map((o, j) => (
              <span key={j} className="hv13" onClick={o.run} style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10.5px', padding: '5px 9px', borderRadius: '7px', border: '1px solid var(--color-divider)', background: 'var(--color-bg)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                <i className="ph ph-arrow-right" style={{ fontSize: '10px', color: 'var(--color-accent)' }}></i>{o.label}
              </span>
            ))}
          </div>
        ) : null}
      </div>
    );
  }
  return (
    <div>
      <div style={{ fontSize: '12px', lineHeight: '1.6', ...(c.open ? {} : clamp(6)) }}><Markdown text={d.text || ''} /></div>
      <More open={c.open} onClick={c.toggleOpen} label="Read it all" />
    </div>
  );
}

export default function ReportCards({ vals }) {
  const blocks = vals.reportBlocks || [];
  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: '16px', marginTop: '14px' }}>
      <div style={{ flex: '1', minWidth: '0' }}>
        {vals.reportBlocksEmpty ? (
          <div style={{ padding: '34px', borderRadius: '12px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-sm)', display: 'flex', flexDirection: 'column', gap: '9px', alignItems: 'flex-start' }}>
            <i className="ph ph-tray" style={{ fontSize: '21px', color: 'var(--color-accent)' }}></i>
            <div style={{ fontSize: '15px' }}>{'Every card is in the tray'}</div>
            <div style={{ fontSize: '12.5px', color: 'var(--color-neutral-400)', maxWidth: '60ch', lineHeight: '1.55' }}>
              {'Nothing has been deleted — the answer is intact and Export still writes all of it. Bring a card back from the tray whenever you want it.'}
            </div>
            <div className="hv4" onClick={vals.restoreAllReportBlocks} style={{ marginTop: '4px', fontSize: '12px', padding: '7px 13px', borderRadius: '7px', border: '1px solid var(--color-divider)', color: 'var(--color-accent)', cursor: 'pointer' }}>
              {'Bring them all back'}
            </div>
          </div>
        ) : (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(232px, 1fr))', gridAutoFlow: 'dense', gap: '12px', alignItems: 'start' }}>
            {blocks.map((c) => {
              const accent = accentOf(c);
              return (
                <div key={c.key} className="hv1" style={{ gridColumn: `span ${Math.min(c.span || 1, 3)}`, position: 'relative', display: 'flex', flexDirection: 'column', gap: '11px', padding: '16px 18px', borderRadius: '13px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-sm)', minWidth: '0', overflow: 'hidden', transition: 'box-shadow 0.15s ease' }}>
                  {accent ? <span style={{ position: 'absolute', left: '0', top: '0', bottom: '0', width: '3px', background: accent }}></span> : null}
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <i className={`ph ${KIND_ICON[c.kind] || 'ph-square'}`} style={{ fontSize: '12px', color: accent || 'var(--color-neutral-500)', flexShrink: '0' }}></i>
                    <span style={{ ...LABEL, flex: '1', minWidth: '0', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</span>
                    <button type="button" className="hv11" onClick={c.hide} title={'Put "' + (c.title || 'this card') + '" in the tray — nothing is deleted'} style={{ font: 'inherit', background: 'transparent', border: 'none', padding: '2px', margin: '0', cursor: 'pointer', color: 'var(--color-neutral-600)', display: 'flex', flexShrink: '0' }}>
                      <i className="ph ph-x" style={{ fontSize: '12px' }}></i>
                    </button>
                  </div>
                  <Body c={c} />
                  {c.data && c.data.note ? (
                    <div style={{ fontSize: '10.5px', lineHeight: '1.5', color: 'var(--color-neutral-500)', paddingTop: '10px', borderTop: '1px solid var(--color-divider)', ...clamp(3) }}>
                      {c.data.note}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* The tray. Always present once a report has cards, so a reader can tell at a glance
          that putting one away is reversible — a control that only appears after the fact
          teaches nobody it exists. */}
      {vals.reportHasBlocks ? (
        <div style={{ width: vals.reportTrayOpen ? '250px' : '44px', flexShrink: '0', transition: 'width 0.18s ease', borderRadius: '12px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-sm)', overflow: 'hidden', position: 'sticky', top: '16px' }}>
          <div className="hv2" onClick={vals.toggleReportTray} title={vals.reportTrayOpen ? 'Hide the tray' : 'Removed cards'} style={{ display: 'flex', alignItems: 'center', gap: '9px', padding: '12px 13px', cursor: 'pointer' }}>
            <i className="ph ph-tray" style={{ fontSize: '15px', color: vals.reportTrayCount ? 'var(--color-accent)' : 'var(--color-neutral-500)', flexShrink: '0' }}></i>
            {vals.reportTrayOpen ? (
              <>
                <span style={{ ...LABEL, flex: '1' }}>{'Tray'}</span>
                <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)' }}>{vals.reportTrayCount}</span>
                <i className="ph ph-caret-right" style={{ fontSize: '11px', color: 'var(--color-neutral-500)' }}></i>
              </>
            ) : vals.reportTrayCount ? (
              <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '10px', color: 'var(--accent-ink)', background: 'var(--color-accent)', borderRadius: '9px', padding: '1px 5px', position: 'absolute', left: '24px', top: '8px' }}>
                {vals.reportTrayCount}
              </span>
            ) : null}
          </div>
          {vals.reportTrayOpen ? (
            <div style={{ padding: '0 11px 12px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
              {vals.reportTrayCount ? (
                <>
                  {(vals.reportTrayItems || []).map((it) => (
                    <div key={it.key} className="hv8" onClick={it.restore} title={it.gone ? 'This section is not in the latest refresh — restoring shows it again if it returns' : 'Bring this card back'} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '7px 9px', borderRadius: '8px', border: '1px solid var(--color-divider)', cursor: 'pointer' }}>
                      <i className={`ph ${KIND_ICON[it.kind] || 'ph-square'}`} style={{ fontSize: '12px', color: it.gone ? 'var(--color-neutral-600)' : 'var(--color-neutral-500)', flexShrink: '0' }}></i>
                      <span style={{ flex: '1', minWidth: '0', fontSize: '11.5px', color: it.gone ? 'var(--color-neutral-500)' : 'var(--color-neutral-300)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{it.title}</span>
                      <i className="ph ph-arrow-counter-clockwise" style={{ fontSize: '12px', color: 'var(--color-accent)', flexShrink: '0' }}></i>
                    </div>
                  ))}
                  <div className="hv6" onClick={vals.restoreAllReportBlocks} style={{ fontSize: '11px', color: 'var(--color-accent)', cursor: 'pointer', padding: '5px 2px' }}>
                    {'Bring them all back'}
                  </div>
                </>
              ) : (
                <div style={{ fontSize: '11px', color: 'var(--color-neutral-500)', lineHeight: '1.5', padding: '2px' }}>
                  {'Nothing here yet. The × on a card puts it in this tray — it is never deleted, and it comes back from here.'}
                </div>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
