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
import React, { useMemo } from 'react';
import { sev, LABEL, CARD } from './cardStyles.js';
import { richHasCards, costSummary, stepCostLabel } from '../../logic/complianceLive.js';
import LazyOpenUIAnswer from '../../openui/LazyOpenUIAnswer.jsx';
import { looksLikeOpenUIStart } from '../../openui/detect.js';
import AnyAnswer, { ALL_ANSWERS } from '../../openui/AnyAnswer.jsx';
import { narrativeIsLead, programFromCompliance } from '../../openui/fromCompliance.js';
import { draftSafe } from '../../openui/fromMarkdown.js';

// SEV / sev / LABEL / CARD live in cardStyles.js, shared with AnswerCards.jsx.

const STAGE_ICON = { plan: 'ph-list-checks', data: 'ph-database', analyse: 'ph-brain', validate: 'ph-shield-check', review: 'ph-eye', revise: 'ph-pencil-simple' };

// Insight kinds the analyst emits: correlation, risk, anomaly.
const INSIGHT = {
  correlation: { title: 'Correlation detected', icon: 'ph-lightning', fg: 'var(--color-accent)', bg: 'var(--color-accent-900)', edge: 'var(--color-divider)' },
  risk: { title: 'Risk', icon: 'ph-warning', fg: 'var(--st-risk)', bg: 'var(--st-risk-bg)', edge: 'var(--st-risk-bg)' },
  anomaly: { title: 'Anomaly', icon: 'ph-pulse', fg: 'var(--st-warn)', bg: 'var(--st-warn-bg)', edge: 'var(--st-warn-bg)' },
  _: { title: 'Insight', icon: 'ph-info', fg: 'var(--color-neutral-400)', bg: 'var(--color-bg)', edge: 'var(--color-divider)' }
};

const OFFER_ICON = { confirm_draft: 'ph-check', verify_now: 'ph-shield-check', renewal_email: 'ph-envelope-simple', attach_certificate: 'ph-upload-simple' };

// An offer the analysis makes against a certificate ("Draft renewal email", "Verify with the
// issuer"), as one real button (28 Oct 2026). They were <span onClick>s: out of reach of the
// keyboard, with no focus ring, no busy or done state, and four identical "Draft renewal email"
// labels in a row that only a tooltip told apart. renderVals gives each offer:
//   state   'busy' while its call runs, 'open' while its draft is on screen, 'error' after its last
//           run failed, 'done' once it has run (a drafted email, a recorded check), or ''
//   status  a few words for busy / open / error ("Drafting…", "Open below", "Failed — press to
//           retry"), drawn beside the label as UI, never in place of it (28 Oct 2026)
//   forName which certificate it is for, from the register; shown under the label when `named`
//   aria    the label, then the certificate and its holder, for a screen reader
//   run     undefined while busy, and while the answer is still being written (the live render
//           carries none), so the button is disabled then
// The label itself is the offer's own, never swapped for "Working…".
function OfferButton({ o, named }) {
  const icon = o.state === 'busy' ? 'ph-circle-notch' : o.state === 'done' ? 'ph-check' : o.state === 'error' ? 'ph-arrow-clockwise' : (OFFER_ICON[o.kind] || 'ph-arrow-right');
  return (
    <button type="button" className={'ans-offer' + (o.state ? ' is-' + o.state : '')} onClick={o.run} disabled={!o.run}
      aria-busy={o.state === 'busy' || undefined} aria-label={o.aria || undefined} title={o.reason || undefined}>
      <i className={'ph ' + icon} aria-hidden="true"></i>
      <span className="ans-offer-text">
        <span className="ans-offer-label">{o.label}{o.status ? <span className="ans-offer-state" aria-hidden="true">{o.status}</span> : null}</span>
        {named && o.forName ? <span className="ans-offer-for">{o.forName}</span> : null}
      </span>
    </button>
  );
}
// Whether any two offers in a list share a label: then each says which certificate it is for.
const sameLabels = (list) => list.some((o, i) => list.findIndex((x) => x.label === o.label) !== i);

// `fallbackText` is the orchestrator's own markdown answer (`m.text`). The compliance
// preflight (compliance_response) does not always fire — some turns route through the
// pipeline only, or through a plain skill with no structured payload at all — and when it
// doesn't, `rich` still exists (compliance_pipeline alone is enough to build one) but every
// card-shaped field on it is empty. Without a fallback, that renders as a "how this answer
// was produced" panel with nothing behind it and the real answer thrown away. Falling back
// to the markdown text keeps the answer itself the one thing that is never lost.
//
// `view` is the answer as the orchestrator drew it in OpenUI (logic/openuiView.js): it takes the
// narrative's place and the presentational cards built from the same tool results (tiles, bars,
// priority actions, owner groups, insights), which would only say it twice. The run panel and the
// lists with buttons on them stay. `live` is the turn being written: a narrative that is an
// OpenUI program streaming in is drawn as one, not shown as code.
//
// Without a view of its own, the answer's cards are written as one (openui/fromCompliance.js) and
// drawn by the same components, so a compliance answer reads like every other (27 Oct 2026). What
// runs something here stays drawn here: the pending items and the offers with their buttons.
//
// THE WHOLE ANSWER, NOT ITS FIRST PARAGRAPH (27 Oct 2026). Cards built by the planner or the
// general loop carry, as their narrative, only the answer's first long paragraph (planner._lead):
// drawn from it alone, a planned answer lost its tables, later sections and caveats, and a
// bullet-only answer showed no words at all. The full Markdown answer (`fallbackText`) goes to the
// converter beside the cards, which draws it in the narrative's place when the narrative is that
// lead; a narrative the compliance engine wrote itself stays its own. The kill switch
// (VITE_OPENUI_ALL_ANSWERS=0) follows the same rule (ownText below).
//
// The live draft is cleaned of half-written Markdown (fromMarkdown.draftSafe) — an unclosed "**",
// a table row whose divider has not arrived — so neither shows for the frames before the rest
// streams in. A finished answer is drawn from its own text.
// The path that does not convert applies fromCompliance's own rule (narrativeIsLead): the
// narrative is the answer's lead when, marks and spacing aside, it is empty, or opens the answer
// or one of its paragraphs.

export default function ComplianceAnswer({ rich, ms, open, onToggle, fallbackText, view, viewTail, live }) {
  const streamingProgram = !!(live && rich && looksLikeOpenUIStart(rich.narrative));
  const auto = useMemo(
    () => (ALL_ANSWERS && rich && !view && !streamingProgram
      // While live the draft is the narrative itself and there is no finished answer yet.
      ? programFromCompliance(live ? Object.assign({}, rich, { narrative: draftSafe(rich.narrative) }) : rich, live ? '' : (fallbackText || ''))
      : ''),
    [rich, view, streamingProgram, live, fallbackText]
  );
  if (!rich) return null;
  const drawn = !!(view || auto);
  // Without the converter: the answer's own text in the narrative's place, when the narrative is
  // only its lead (see above).
  const ownText = !live && !!fallbackText && narrativeIsLead(rich, fallbackText);
  // Offers on no pending item: the owner cards carried them, and the owner cards are now drawn
  // in the view, which cannot run them.
  const looseOffers = drawn ? rich.offers.filter((o) => !rich.pending.some((p) => p.cert_id === o.cert_id)) : [];
  const cost = costSummary(rich.cost);
  // The ledger's own wall-clock covers only the model calls it tracked; the client-measured
  // `ms` covers the whole round trip (network included) and is what to show when the ledger
  // has nothing. Prefer the ledger's once it exists — it is what the $ figure beside it means.
  const secs = cost && cost.secsLabel
    ? cost.secsLabel
    : (typeof ms === 'number' ? (ms / 1000).toFixed(ms < 10000 ? 1 : 0) + ' s' : null);
  const v = rich.validation;
  const hasCards = richHasCards(rich);
  // The provenance line's figures, closed or open: steps, model calls, time, total cost.
  const meta = [
    rich.steps.length ? rich.steps.length + ' steps' : null,
    cost ? cost.calls + ' model call' + (cost.calls === 1 ? '' : 's') : null,
    secs,
    cost ? cost.usdLabel + (cost.usdComplete ? '' : '+') : null
  ].filter(Boolean);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '12px' }}>

      {/* ── how this answer was produced ───────────────────────────────── */
        /* ONE LINE ABOVE THE ANSWER (28 Oct 2026). How the answer was reached and what it cost
           fold into a single provenance line, so the answer itself comes first: closed, it reads
           "4 steps · 2 model calls · 125 s · $0.191"; opened, the steps, the check, the total and
           then the cost by role, in the order they were on screen before. The cost used to be a
           card of its own between this line and the answer, with orange bars on data nobody acts
           on. A turn with a cost ledger but no steps now gets the line too (its meta starts at the
           model calls), so its cost is never lost. The header is a real button: the same toggle,
           now reachable by keyboard and announced as open or closed. Styles: styles/answer.css. */}
      {(rich.steps.length || (cost && cost.roles.length)) ? (
        <section className={open ? 'ans-prov is-open' : 'ans-prov'}>
          <button type="button" className="ans-prov-head" aria-expanded={!!open} onClick={onToggle}>
            <i className="ph ph-caret-right ans-prov-chev" aria-hidden="true"></i>
            <span className="ans-prov-title">{'How this answer was produced'}</span>
            {/* The same "4 steps · 2 model calls · 125 s · $0.191", each figure kept whole with
                the separator after it, so where the dock wraps the line a "·" never starts one. */}
            <span className="ans-prov-meta">
              {meta.map((p, i) => (
                <React.Fragment key={i}>
                  {i ? ' ' : ''}
                  <span className="ans-prov-fig">{p + (i < meta.length - 1 ? ' ·' : '')}</span>
                </React.Fragment>
              ))}
            </span>
          </button>
          {open ? (
            <div className="ans-prov-body">
              {rich.steps.length ? (
                <ol className="ans-prov-steps">
                  {rich.steps.map((s, i) => {
                    const rows = (s.queries || []).reduce((a, q) => a + (Number(q.matched_rows) || 0), 0);
                    const relaxed = (s.queries || []).map((q) => q.relaxed).filter(Boolean);
                    const stepCost = stepCostLabel(s);
                    return (
                      <li key={i} className={(s.issues || []).length ? 'ans-prov-step has-issues' : 'ans-prov-step'}>
                        <i className={`ph ${STAGE_ICON[s.stage] || 'ph-check'}`} aria-hidden="true"></i>
                        <div style={{ minWidth: '0' }}>
                          <div className="ans-prov-label">{s.label}</div>
                          {/* This step's own model call, when it made one — a database read
                              (register, pack, coverage) or a code-only check carries no badge,
                              since it never touched the cost ledger. Runs as its own line, not a
                              right-aligned column: the dock is often under 300px wide, and a fixed
                              side column there forces the label into a sliver that wraps one word
                              per line. */}
                          {stepCost ? <div className="ans-prov-cost">{stepCost.line}</div> : null}
                          {s.detail ? <div className="ans-prov-detail">{s.detail}</div> : null}
                          {(s.parts || []).length ? (
                            <div className="ans-prov-parts">
                              {s.parts.map((p, j) => (
                                <div key={j} className="ans-prov-part">
                                  {'· ' + (p.text || p.id || '')}
                                  {p.scope ? <span className="ans-prov-scope">{' — ' + p.scope}</span> : null}
                                </div>
                              ))}
                            </div>
                          ) : null}
                          {rows ? (
                            <div className="ans-prov-rows">{rows + ' row' + (rows === 1 ? '' : 's') + ' matched'}</div>
                          ) : null}
                          {relaxed.length ? (
                            <div className="ans-prov-flag">{'Filter widened: ' + relaxed.join('; ')}</div>
                          ) : null}
                          {(s.issues || []).map((x, j) => (
                            <div key={j} className="ans-prov-flag">{'• ' + x}</div>
                          ))}
                        </div>
                      </li>
                    );
                  })}
                </ol>
              ) : null}
              {v ? (
                <div className="ans-prov-check">
                  {[
                    typeof v.rows_fetched === 'number' ? v.rows_fetched + ' rows fetched' : null,
                    typeof v.certificates_kept === 'number' ? v.certificates_kept + ' cited' : null,
                    (v.issues || []).length ? (v.issues || []).length + ' correction(s)' : 'nothing ungrounded'
                  ].filter(Boolean).join(' · ')}
                </div>
              ) : null}
              {secs ? (
                <div className="ans-prov-total">
                  {cost
                    ? 'Total ' + secs + ' · ' + cost.usdLabel + (cost.usdComplete ? '' : ', partial — an unpriced model was used') + '.'
                    : 'Total ' + secs + ', measured in the browser. This turn carried no cost ledger.'}
                </div>
              ) : null}

              {/* ── cost by role ─────────────────────────────────────────── */
                /* llm_cost.py's per-turn ledger: real spend, split by which stage of the pipeline
                   made the model call. Absent whenever `cost` never arrived — no zero-cost row is
                   shown in its place, because a turn with no ledger is not a turn that cost $0.
                   Last in the line's body, so in the dock (where it opens by default) it pushes
                   the answer down by the least; its bars are grey, as spend is read, not acted on. */}
              {cost && cost.roles.length ? (
                <div className="ans-prov-spend">
                  <div className="ans-prov-spend-head">
                    <span className="ans-prov-spend-title">{'Cost by role'}</span>
                    <span className="ans-prov-spend-total">{cost.usdLabel}</span>
                  </div>
                  <div className="ans-prov-roles">
                    {cost.roles.map((r, i) => (
                      <div key={i} className="ans-prov-role">
                        <span className="ans-prov-role-name">{r.label}</span>
                        <span className="ans-prov-role-track">
                          <span className="ans-prov-role-fill" style={{ width: Math.max(r.pct, r.usd > 0 ? 2 : 0) + '%' }}></span>
                        </span>
                        <span className="ans-prov-role-usd">{r.usdLabel}</span>
                      </div>
                    ))}
                  </div>
                  <div className="ans-prov-spend-foot">
                    {cost.calls + ' model call' + (cost.calls === 1 ? '' : 's')
                      + (cost.models.length ? ' · ' + cost.models.join(', ') : '')
                      + (cost.cacheReadLabel ? ' · cache read ' + cost.cacheReadLabel : '')
                      + (cost.usdComplete ? '' : ' · one call used an unpriced model — total is a minimum')}
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </section>
      ) : null}

      {/* ── overall ────────────────────────────────────────────────────── */}
      {view ? (
        <LazyOpenUIAnswer text={view} tail={viewTail} fallbackText={fallbackText} />
      ) : streamingProgram ? (
        <LazyOpenUIAnswer text={rich.narrative} fallbackText={fallbackText} streaming />
      ) : auto ? (
        <LazyOpenUIAnswer text={auto} fallbackText={fallbackText || rich.narrative} streaming={!!live} />
      ) : ownText ? (
        <div style={{ fontSize: '13px', lineHeight: '1.55', textWrap: 'pretty' }}>
          <AnyAnswer text={fallbackText} />
        </div>
      ) : rich.narrative ? (
        <div>
          <div style={LABEL}>{'Overall'}</div>
          <div style={{ fontSize: '11.5px', lineHeight: '1.55', marginTop: '5px', textWrap: 'pretty' }}>
            {live ? draftSafe(rich.narrative) : rich.narrative}
          </div>
        </div>
      ) : (!hasCards && fallbackText) ? (
        <div style={{ fontSize: '13px', lineHeight: '1.55', textWrap: 'pretty' }}>
          <AnyAnswer text={fallbackText} />
        </div>
      ) : null}

      {/* ── kpis ───────────────────────────────────────────────────────── */}
      {!drawn && rich.kpis.length ? (
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
                {/* "other" is the builders' placeholder (planner._kpi), not a unit worth printing */}
                {k.unit && k.unit !== 'other' ? (
                  <div style={{ fontFamily: 'ui-monospace,monospace', fontSize: '9px', color: 'var(--color-neutral-500)', marginTop: '4px' }}>{k.unit}</div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}

      {/* ── days overdue ───────────────────────────────────────────────── */}
      {(!drawn && rich.overdue && (rich.overdue.buildings.length || rich.overdue.vendors.length)) ? (
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(230px,1fr))', gap: '8px' }}>
          {/* Eight bars a scope at most (complianceLive.overdueBars); `more` says how many were
              left out, so the cut is never silent (27 Oct 2026). */}
          {[['Days overdue — buildings', rich.overdue.buildings, (rich.overdue.more || {}).buildings], ['Days overdue — vendors', rich.overdue.vendors, (rich.overdue.more || {}).vendors]]
            .filter(([, rows]) => rows.length)
            .map(([title, rows, more], k) => (
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
                  {'days past expiry' + (more > 0 ? ' · +' + more + ' more in Certificates in scope' : '')}
                </div>
              </div>
            ))}
        </div>
      ) : null}

      {/* ── priority actions ───────────────────────────────────────────── */}
      {!drawn && rich.actions.length ? (
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
      {(drawn ? [] : rich.groups).map((g, i) => {
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
              <div className="ans-offers" style={{ marginTop: '8px', paddingTop: '8px', borderTop: '1px solid var(--color-divider)' }}>
                {offers.map((o, j) => <OfferButton key={j} o={o} named={sameLabels(offers)} />)}
              </div>
            ) : null}
          </div>
        );
      })}

      {/* ── insights ───────────────────────────────────────────────────── */}
      {!drawn && rich.insights.length ? (
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

      {/* ── offers whose owner card is drawn in the view ────────────────── */}
      {/* Standing on their own under the answer, these always say which certificate each is for. */}
      {looseOffers.length ? (
        <div className="ans-offers" role="group" aria-label="Actions on these certificates">
          {looseOffers.map((o, j) => <OfferButton key={j} o={o} named />)}
        </div>
      ) : null}

      {/* ── certificates in scope ──────────────────────────────────────── */}
      {!auto && rich.certificates.length ? (
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
                  {/* The card's title already names the certificate. */}
                  {acts.length ? (
                    <div className="ans-offers" style={{ marginTop: '8px' }}>
                      {acts.map((o, j) => <OfferButton key={j} o={o} named={false} />)}
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
