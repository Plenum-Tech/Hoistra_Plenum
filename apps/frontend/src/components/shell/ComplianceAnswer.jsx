// ComplianceAnswer — the structured compliance reply, rendered in the side chat.
//
// Shape comes from svc-deepagents' compliance preflight (run-stateful only):
//   compliance_response → narrative · kpis · groups · actions · insights · certificates
//                         · pending · offers · validation
//   compliance_pipeline → steps [{ stage, label, detail, parts?, queries?, issues? }]
//                         · cost { calls, usd, usd_complete, input_tokens, output_tokens,
//                                  cache_read, wall_ms, by_role, models } — llm_cost.py's
//                         per-turn ledger, real spend from the model calls this turn made.
//
// Not shown per step — the ledger records one entry per model call (role, model, ms, usd),
// but nothing on a `steps` entry says which ledger entry is its own, so attributing a step to
// a specific call would be a guess, not a fact. `cost` is a turn-level total instead: real
// numbers, scoped to what they actually describe.
import React from 'react';
import Markdown from './Markdown.jsx';
import { richHasCards, costSummary, stepCostLabel } from '../../logic/complianceLive.js';

const SEV = {
  critical: { fg: 'var(--st-risk)', bg: 'var(--st-risk-bg)' },
  warning: { fg: 'var(--st-warn)', bg: 'var(--st-warn-bg)' },
  info: { fg: 'var(--color-accent)', bg: 'var(--color-accent-900)' },
  ok: { fg: 'var(--st-ok)', bg: 'var(--st-ok-bg)' }
};
const sev = (s) => SEV[String(s || '').toLowerCase()] || { fg: 'var(--color-neutral-400)', bg: 'var(--color-bg)' };

const LABEL = {
  fontSize: '9.5px', letterSpacing: '0.11em', textTransform: 'uppercase',
  color: 'var(--color-neutral-500)'
};
const CARD = {
  border: '1px solid var(--color-divider)', borderRadius: '9px',
  background: 'var(--color-bg)', padding: '10px 11px'
};

const STAGE_ICON = { plan: 'ph-list-checks', data: 'ph-database', analyse: 'ph-brain', validate: 'ph-shield-check', review: 'ph-eye', revise: 'ph-pencil-simple' };

// Insight kinds the analyst emits: correlation, risk, anomaly.
const INSIGHT = {
  correlation: { title: 'Correlation detected', icon: 'ph-lightning', fg: 'var(--color-accent)', bg: 'var(--color-accent-900)', edge: 'var(--color-divider)' },
  risk: { title: 'Risk', icon: 'ph-warning', fg: 'var(--st-risk)', bg: 'var(--st-risk-bg)', edge: 'var(--st-risk-bg)' },
  anomaly: { title: 'Anomaly', icon: 'ph-pulse', fg: 'var(--st-warn)', bg: 'var(--st-warn-bg)', edge: 'var(--st-warn-bg)' },
  _: { title: 'Insight', icon: 'ph-info', fg: 'var(--color-neutral-400)', bg: 'var(--color-bg)', edge: 'var(--color-divider)' }
};

const OFFER_ICON = { confirm_draft: 'ph-check', verify_now: 'ph-shield-check', renewal_email: 'ph-envelope-simple' };

// `fallbackText` is the orchestrator's own markdown answer (`m.text`). The compliance
// preflight (compliance_response) does not always fire — some turns route through the
// pipeline only, or through a plain skill with no structured payload at all — and when it
// doesn't, `rich` still exists (compliance_pipeline alone is enough to build one) but every
// card-shaped field on it is empty. Without a fallback, that renders as a "how this answer
// was produced" panel with nothing behind it and the real answer thrown away. Falling back
// to the markdown text keeps the answer itself the one thing that is never lost.
export default function ComplianceAnswer({ rich, ms, open, onToggle, fallbackText }) {
  if (!rich) return null;
  const cost = costSummary(rich.cost);
  // The ledger's own wall-clock covers only the model calls it tracked; the client-measured
  // `ms` covers the whole round trip (network included) and is what to show when the ledger
  // has nothing. Prefer the ledger's once it exists — it is what the $ figure beside it means.
  const secs = cost && cost.secsLabel
    ? cost.secsLabel
    : (typeof ms === 'number' ? (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' s' : null);
  const v = rich.validation;
  const hasCards = richHasCards(rich);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '11px' }}>

      {/* ── how this answer was produced ───────────────────────────────── */}
      {rich.steps.length ? (
        <div style={{ border: '1px solid var(--color-divider)', borderRadius: '9px', overflow: 'hidden' }}>
          <div className="hv2" onClick={onToggle} style={{ display: 'flex', alignItems: 'center', gap: '7px', padding: '8px 10px', background: 'var(--color-bg)', cursor: 'pointer' }}>
            <i className={`ph ${open ? 'ph-caret-down' : 'ph-caret-right'}`} style={{ fontSize: '11px', color: 'var(--color-neutral-500)' }}></i>
            <span style={{ ...LABEL, flex: '1' }}>{'How this answer was produced'}</span>
            <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9.5px', color: 'var(--color-neutral-500)', whiteSpace: 'nowrap' }}>
              {rich.steps.length + ' steps'
                + (cost ? ' · ' + cost.calls + ' model call' + (cost.calls === 1 ? '' : 's') : '')
                + (secs ? ' · ' + secs : '')
                + (cost ? ' · ' + cost.usdLabel + (cost.usdComplete ? '' : '+') : '')}
            </span>
          </div>
          {open ? (
            <div style={{ display: 'flex', flexDirection: 'column' }}>
              {rich.steps.map((s, i) => {
                const rows = (s.queries || []).reduce((a, q) => a + (Number(q.matched_rows) || 0), 0);
                const relaxed = (s.queries || []).map((q) => q.relaxed).filter(Boolean);
                const stepCost = stepCostLabel(s);
                return (
                  <div key={i} style={{ display: 'grid', gridTemplateColumns: '15px minmax(0,1fr)', gap: '9px', padding: '9px 11px', borderTop: '1px solid var(--color-divider)' }}>
                    <i className={`ph ${STAGE_ICON[s.stage] || 'ph-check'}`} style={{ fontSize: '12px', color: 'var(--st-ok)', marginTop: '2px' }}></i>
                    <div style={{ minWidth: '0' }}>
                      <div style={{ fontSize: '11.5px', lineHeight: '1.35' }}>{s.label}</div>
                      {/* This step's own model call, when it made one — a database read
                          (register, pack, coverage) or a code-only check carries no badge,
                          since it never touched the cost ledger. Runs as its own line, not a
                          right-aligned column: the dock is often under 300px wide, and a fixed
                          side column there forces the label into a sliver that wraps one word
                          per line. */}
                      {stepCost ? (
                        <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9.5px', color: 'var(--color-neutral-500)', marginTop: '3px', lineHeight: '1.4' }}>
                          {stepCost.line}
                        </div>
                      ) : null}
                      {s.detail ? (
                        <div style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)', marginTop: '3px', lineHeight: '1.45' }}>{s.detail}</div>
                      ) : null}
                      {(s.parts || []).length ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '2px', marginTop: '5px' }}>
                          {s.parts.map((p, j) => (
                            <div key={j} style={{ fontSize: '10px', color: 'var(--color-neutral-400)' }}>
                              {'· ' + (p.text || p.id || '')}
                              {p.scope ? <span style={{ color: 'var(--color-neutral-500)' }}>{' — ' + p.scope}</span> : null}
                            </div>
                          ))}
                        </div>
                      ) : null}
                      {rows ? (
                        <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9.5px', color: 'var(--color-neutral-500)', marginTop: '5px' }}>
                          {rows + ' row' + (rows === 1 ? '' : 's') + ' matched'}
                        </div>
                      ) : null}
                      {relaxed.length ? (
                        <div style={{ fontSize: '10px', color: 'var(--st-warn)', marginTop: '4px' }}>
                          {'Filter widened: ' + relaxed.join('; ')}
                        </div>
                      ) : null}
                      {(s.issues || []).length ? (
                        <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '5px' }}>
                          {s.issues.map((x, j) => (
                            <div key={j} style={{ fontSize: '10px', color: 'var(--st-warn)', lineHeight: '1.4' }}>{'• ' + x}</div>
                          ))}
                        </div>
                      ) : null}
                    </div>
                  </div>
                );
              })}
              {v ? (
                <div style={{ padding: '8px 11px', borderTop: '1px solid var(--color-divider)', fontFamily: 'ui-monospace,monospace', fontSize: '9.5px', color: 'var(--color-neutral-500)' }}>
                  {[
                    typeof v.rows_fetched === 'number' ? v.rows_fetched + ' rows fetched' : null,
                    typeof v.certificates_kept === 'number' ? v.certificates_kept + ' cited' : null,
                    (v.issues || []).length ? (v.issues || []).length + ' correction(s)' : 'nothing ungrounded'
                  ].filter(Boolean).join(' · ')}
                </div>
              ) : null}
              {secs ? (
                <div style={{ padding: '0 11px 8px', fontSize: '9.5px', color: 'var(--color-neutral-500)' }}>
                  {cost
                    ? 'Total ' + secs + ' · ' + cost.usdLabel + (cost.usdComplete ? '' : ', partial — an unpriced model was used') + '.'
                    : 'Total ' + secs + ', measured in the browser. This turn carried no cost ledger.'}
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}

      {/* ── cost by role ───────────────────────────────────────────────── */
        /* llm_cost.py's per-turn ledger: real spend, split by which stage of the pipeline
           made the model call. Absent whenever `cost` never arrived — no zero-cost row is
           shown in its place, because a turn with no ledger is not a turn that cost $0. */}
      {cost && cost.roles.length ? (
        <div style={CARD}>
          <div style={{ display: 'flex', alignItems: 'baseline', justifyContent: 'space-between', gap: '10px' }}>
            <span style={LABEL}>{'Cost by role'}</span>
            <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '11px', color: 'var(--color-text)' }}>
              {cost.usdLabel}
            </span>
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '9px' }}>
            {cost.roles.map((r, i) => (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '76px minmax(0,1fr) 50px', gap: '8px', alignItems: 'center' }}>
                <span style={{ fontSize: '10.5px', color: 'var(--color-neutral-400)', textTransform: 'capitalize', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {r.label}
                </span>
                <div style={{ height: '6px', borderRadius: '3px', background: 'var(--color-neutral-900)', overflow: 'hidden' }}>
                  <div style={{ height: '100%', borderRadius: '3px', width: Math.max(r.pct, r.usd > 0 ? 2 : 0) + '%', background: 'var(--color-accent)' }}></div>
                </div>
                <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '10px', color: 'var(--color-neutral-500)', textAlign: 'right' }}>
                  {r.usdLabel}
                </span>
              </div>
            ))}
          </div>
          <div style={{ fontSize: '9.5px', color: 'var(--color-neutral-500)', marginTop: '9px' }}>
            {cost.calls + ' model call' + (cost.calls === 1 ? '' : 's')
              + (cost.models.length ? ' · ' + cost.models.join(', ') : '')
              + (cost.cacheReadLabel ? ' · cache read ' + cost.cacheReadLabel : '')
              + (cost.usdComplete ? '' : ' · one call used an unpriced model — total is a minimum')}
          </div>
        </div>
      ) : null}

      {/* ── overall ────────────────────────────────────────────────────── */}
      {rich.narrative ? (
        <div>
          <div style={LABEL}>{'Overall'}</div>
          <div style={{ fontSize: '11.5px', lineHeight: '1.55', marginTop: '5px', textWrap: 'pretty' }}>
            {rich.narrative}
          </div>
        </div>
      ) : (!hasCards && fallbackText) ? (
        <div style={{ fontSize: '13px', lineHeight: '1.55', textWrap: 'pretty' }}>
          <Markdown text={fallbackText} />
        </div>
      ) : null}

      {/* ── kpis ───────────────────────────────────────────────────────── */}
      {rich.kpis.length ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(128px,1fr))', gap: '7px' }}>
          {rich.kpis.map((k, i) => {
            const c = sev(k.severity);
            return (
              <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}` }}>
                <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '19px', lineHeight: '1.1', color: c.fg }}>{k.count}</div>
                <div style={{ fontSize: '10.5px', lineHeight: '1.35', marginTop: '3px' }}>{k.label}</div>
                {k.sublabel ? (
                  <div style={{ fontSize: '10px', color: 'var(--color-neutral-500)', marginTop: '2px', lineHeight: '1.35' }}>{k.sublabel}</div>
                ) : null}
                {k.unit ? (
                  <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9px', color: 'var(--color-neutral-500)', marginTop: '4px' }}>{k.unit}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {/* ── days overdue ───────────────────────────────────────────────── */}
      {(rich.overdue && (rich.overdue.buildings.length || rich.overdue.vendors.length)) ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))', gap: '8px' }}>
          {[['Days overdue — buildings', rich.overdue.buildings], ['Days overdue — vendors', rich.overdue.vendors]]
            .filter(([, rows]) => rows.length)
            .map(([title, rows], k) => (
              <div key={k} style={{ ...CARD }}>
                <div style={LABEL}>{title}</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', marginTop: '8px' }}>
                  {rows.map((r, j) => {
                    const c = sev(r.severity);
                    return (
                      <div key={j} style={{ display: 'grid', gridTemplateColumns: 'minmax(0,1fr) 46px', gap: '7px', alignItems: 'center' }}>
                        <div style={{ minWidth: '0' }}>
                          <div style={{ fontSize: '10px', color: 'var(--color-neutral-400)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                            {r.label}
                          </div>
                          <div style={{ height: '7px', borderRadius: '3px', background: 'var(--color-neutral-900)', overflow: 'hidden', marginTop: '3px' }}>
                            <div style={{ height: '100%', borderRadius: '3px', width: r.pct, background: c.fg }}></div>
                          </div>
                        </div>
                        <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '10px', color: c.fg, textAlign: 'right' }}>
                          {r.days.toLocaleString()}
                        </span>
                      </div>
                    );
                  })}
                </div>
                <div style={{ fontSize: '9px', color: 'var(--color-neutral-500)', marginTop: '7px' }}>
                  {'days past expiry'}
                </div>
              </div>
            ))}
        </div>
      ) : null}

      {/* ── priority actions ───────────────────────────────────────────── */}
      {rich.actions.length ? (
        <div>
          <div style={LABEL}>{'Priority actions'}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '5px', marginTop: '6px' }}>
            {rich.actions.map((a, i) => {
              const c = sev(a.severity);
              const certs = (a.cert_ids || []).length;
              return (
                <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}`, padding: '9px 11px' }}>
                  <div style={{ display: 'flex', alignItems: 'baseline', gap: '8px' }}>
                    <span style={{ flex: '1', minWidth: '0', fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: c.fg }}>
                      {[a.severity, a.scope].filter(Boolean).join(' · ')}
                    </span>
                    {certs ? (
                      <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9px', color: 'var(--color-neutral-500)', flexShrink: '0' }}>
                        {certs + ' cert' + (certs === 1 ? '' : 's')}
                      </span>
                    ) : null}
                  </div>
                  <div style={{ fontSize: '11.5px', lineHeight: '1.4', marginTop: '4px' }}>{a.title}</div>
                  {(a.tags || []).length ? (
                    <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap', marginTop: '6px' }}>
                      {a.tags.map((g, j) => (
                        <span key={j} style={{ fontSize: '9px', padding: '2px 6px', borderRadius: '5px', background: 'var(--color-bg)', border: '1px solid var(--color-divider)', color: 'var(--color-neutral-500)' }}>
                          {g}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}

      {/* ── owner groups ───────────────────────────────────────────────── */}
      {rich.groups.map((g, i) => {
        const c = sev(g.severity);
        const offers = rich.offers.filter((o) => (g.cert_ids || []).indexOf(o.cert_id) > -1 || o.owner === g.owner);
        return (
          <div key={i} style={{ ...CARD, borderLeft: `3px solid ${c.fg}` }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: '7px' }}>
              <span style={{ fontSize: '12px', flex: '1', minWidth: '0' }}>{g.owner}</span>
              <span style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9px', color: 'var(--color-neutral-500)', flexShrink: '0' }}>
                {String(g.scope || '').toUpperCase()}
              </span>
            </div>
            {g.headline ? (
              <div style={{ fontSize: '10.5px', color: c.fg, marginTop: '4px', lineHeight: '1.4' }}>{g.headline}</div>
            ) : null}
            {(g.points || []).length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: '3px', marginTop: '7px' }}>
                {g.points.map((p, j) => (
                  <div key={j} style={{ display: 'grid', gridTemplateColumns: '7px minmax(0,1fr)', gap: '6px', fontSize: '10.5px', color: 'var(--color-neutral-300)', lineHeight: '1.45' }}>
                    <span>{'•'}</span><span>{p}</span>
                  </div>
                ))}
              </div>
            ) : null}
            {offers.length ? (
              <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginTop: '8px', paddingTop: '7px', borderTop: '1px solid var(--color-divider)' }}>
                {offers.map((o, j) => (
                  <span key={j} className="hv13" onClick={o.run} title={o.reason || ''} style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10px', padding: '4px 8px', borderRadius: '6px', border: '1px solid var(--color-divider)', background: 'var(--color-surface)', color: 'var(--color-neutral-300)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                    <i className={`ph ${OFFER_ICON[o.kind] || 'ph-arrow-right'}`} style={{ fontSize: '10px', color: 'var(--color-accent)' }}></i>
                    {o.label}
                  </span>
                ))}
              </div>
            ) : null}
          </div>
        );
      })}

      {/* ── insights ───────────────────────────────────────────────────── */}
      {rich.insights.length ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {rich.insights.map((x, i) => {
            const k = INSIGHT[String(x.type || '').toLowerCase()] || INSIGHT._;
            return (
              <div key={i} style={{ display: 'grid', gridTemplateColumns: '14px minmax(0,1fr)', gap: '8px', padding: '9px 11px', borderRadius: '9px', background: k.bg, border: `1px solid ${k.edge}` }}>
                <i className={`ph ${k.icon}`} style={{ fontSize: '12px', color: k.fg, marginTop: '2px' }}></i>
                <div style={{ minWidth: '0' }}>
                  <div style={{ fontSize: '9px', letterSpacing: '0.1em', textTransform: 'uppercase', color: k.fg }}>
                    {k.title}
                  </div>
                  <div style={{ fontSize: '10.5px', lineHeight: '1.5', marginTop: '3px', color: 'var(--color-neutral-300)' }}>
                    {x.text}
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      ) : null}

      {/* ── certificates in scope ──────────────────────────────────────── */}
      {rich.certificates.length ? (
        <div>
          <div style={LABEL}>{'Certificates in scope · ' + rich.certificates.length}</div>
          <div style={{ overflowX: 'auto', border: '1px solid var(--color-divider)', borderRadius: '8px', marginTop: '6px' }}>
            <table style={{ borderCollapse: 'collapse', width: '100%', fontSize: '10.5px' }}>
              <thead>
                <tr>
                  {['Certificate', 'Held by', 'Scope', 'Status'].map((h) => (
                    <th key={h} style={{ textAlign: 'left', whiteSpace: 'nowrap', padding: '6px 9px', background: 'var(--color-bg)', borderBottom: '1px solid var(--color-divider)', ...LABEL }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {rich.certificates.map((c, i) => {
                  const cc = sev(c.severity);
                  return (
                    <tr key={c.id || i}>
                      <td style={{ padding: '6px 9px', borderTop: '1px solid var(--color-divider)', verticalAlign: 'top' }}>
                        <div style={{ display: 'flex', gap: '6px', alignItems: 'baseline' }}>
                          <span style={{ width: '5px', height: '5px', borderRadius: '50%', background: cc.fg, flexShrink: '0', marginTop: '4px' }}></span>
                          <div style={{ minWidth: '0' }}>
                            <div>{c.name}</div>
                            {c.reason ? <div style={{ fontSize: '9.5px', color: 'var(--color-neutral-500)', marginTop: '2px' }}>{c.reason}</div> : null}
                          </div>
                        </div>
                      </td>
                      <td style={{ padding: '6px 9px', borderTop: '1px solid var(--color-divider)', verticalAlign: 'top' }}>{c.company}</td>
                      <td style={{ padding: '6px 9px', borderTop: '1px solid var(--color-divider)', whiteSpace: 'nowrap', verticalAlign: 'top' }}>{c.scope}</td>
                      <td style={{ padding: '6px 9px', borderTop: '1px solid var(--color-divider)', whiteSpace: 'nowrap', verticalAlign: 'top', color: cc.fg }}>{c.status}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      ) : null}

      {/* ── pending ────────────────────────────────────────────────────── */}
      {rich.pending.length ? (
        <div>
          <div style={LABEL}>{'Waiting on you · ' + rich.pending.length}</div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', marginTop: '6px' }}>
            {rich.pending.map((p, i) => {
              const acts = rich.offers.filter((o) => o.cert_id === p.cert_id);
              return (
                <div key={i} style={{ ...CARD }}>
                  <div style={{ fontSize: '11px', lineHeight: '1.4' }}>{p.name}</div>
                  <div style={{ fontSize: '10.5px', color: 'var(--color-neutral-500)', marginTop: '3px', lineHeight: '1.45' }}>
                    {p.what_is_pending}
                  </div>
                  {acts.length ? (
                    <div style={{ display: 'flex', gap: '5px', flexWrap: 'wrap', marginTop: '8px' }}>
                      {acts.map((o, j) => (
                        <span key={j} className="hv13" onClick={o.run} title={o.reason || ''} style={{ display: 'inline-flex', alignItems: 'center', gap: '5px', fontSize: '10.5px', padding: '5px 9px', borderRadius: '7px', border: '1px solid var(--color-divider)', background: 'var(--color-surface)', cursor: 'pointer', whiteSpace: 'nowrap' }}>
                          <i className={`ph ${OFFER_ICON[o.kind] || 'ph-arrow-right'}`} style={{ fontSize: '11px', color: 'var(--color-accent)' }}></i>
                          {o.label}
                        </span>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
            })}
          </div>
        </div>
      ) : null}
    </div>
  );
}
