// PlatformCostPanel — one company's month on the Platform cost page, in a side panel that
// leaves the companies table usable beside it: click another company to swap, Esc or the
// close button to dismiss. `panel` is pcVals().pcPanel; its `sheet` is pcPanelSheet() for
// the open tab, null while this company's figures are still being read.
import React from 'react';

// Margin as a reader judges it: the icon's shape carries the judgement, not its colour alone.
const TONE_ICON = { loss: 'ph-trend-down', thin: 'ph-minus', healthy: 'ph-trend-up' };
const TONE_WORD = { loss: 'Loss', thin: 'Thin margin', healthy: 'Healthy margin' };

export function ToneIcon({ tone }) {
  if (!tone) return null;
  return <i className={'ph ' + TONE_ICON[tone] + ' pc-tone pc-tone--' + tone} title={TONE_WORD[tone]} aria-label={TONE_WORD[tone]} role="img"></i>;
}

function DayColumns({ days, bindTip }) {
  const n = days.bars.length;
  const [picked, setActive] = React.useState(n - 1);
  // The panel stays open across a month change: a 30-day month's last day is not a column
  // of a 5-day one, so the one tab stop is clamped to the columns there are.
  const active = Math.min(picked, n - 1);
  const refs = React.useRef([]);
  // One tab stop for the chart; the arrow keys walk the days, each showing its tooltip.
  const onKey = (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const next = Math.max(0, Math.min(n - 1, active + (e.key === 'ArrowRight' ? 1 : -1)));
    setActive(next);
    if (refs.current[next]) refs.current[next].focus();
  };
  return (
    <div>
      <div className="pc-cols-wrap">
        <div className="pc-cols-axis" aria-hidden="true">
          <span style={{ top: '0%' }}>{days.max}</span>
          <span style={{ top: '100%' }}>$0</span>
        </div>
        <div className="pc-cols" role="group" aria-label="Model spend by day. Arrow keys move between days." onKeyDown={onKey}>
          {days.bars.map((b, i) => (
            <span
              key={b.day}
              ref={(el) => { refs.current[i] = el; }}
              className={'pc-col' + (b.pct ? '' : ' is-zero')}
              {...bindTip(b.value, b.label + ', ' + b.queries + (b.queries === 1 ? ' query' : ' queries'))}
              tabIndex={i === active ? 0 : -1}
            >
              <span style={{ height: b.pct + '%' }}></span>
            </span>
          ))}
        </div>
      </div>
      <div className="pc-cols-x" aria-hidden="true">
        {days.bars.map((b) => <span key={b.day}>{b.tick ? b.axis : ''}</span>)}
      </div>
    </div>
  );
}

function BarList({ items, bindTip }) {
  return (
    <ul className="pc-barlist pc-barlist--compact">
      {items.map((x) => (
        <li key={x.label}>
          <span className="pc-barlist-label" title={x.label}>{x.label}</span>
          <span className="pc-barlist-track"><span className="pc-barlist-bar" style={{ width: x.pct + '%' }} {...bindTip(x.value, x.label)}></span></span>
          <span className="pc-num">{x.value}</span>
          <span className="pc-muted pc-num">{x.hint}</span>
        </li>
      ))}
    </ul>
  );
}

function Table({ head, rows }) {
  return (
    <div className="pc-scroll">
      <table className="pc-dtable">
        <thead><tr>{head.map((h) => <th key={h} scope="col">{h}</th>)}</tr></thead>
        <tbody>{rows.map((r, i) => <tr key={i}>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Overview({ s, monthLabel, bindTip }) {
  const [asTable, setAsTable] = React.useState(false);
  return (
    <>
      <section>
        <div className="pc-chart-head">
          <h3 className="pc-h3">Model spend by day</h3>
          <span className="pc-hint">{s.days.total + ' in ' + monthLabel}</span>
          <button type="button" className="pc-linkbtn" onClick={() => setAsTable(!asTable)}>{asTable ? 'Show chart' : 'Show numbers'}</button>
        </div>
        {asTable
          ? <Table head={['Day', 'Model spend', 'Queries']} rows={s.days.bars.filter((b) => b.queries || b.pct).map((b) => [b.label, b.value, b.queries])} />
          : <DayColumns days={s.days} bindTip={bindTip} />}
      </section>
      <section>
        <h3 className="pc-h3">What it cost</h3>
        <dl className="pc-rows">
          {s.split.map((r) => (
            <div key={r.label} className={(r.indent ? 'is-indent ' : '') + (r.strong ? 'is-strong' : '')}>
              <dt>{r.label}</dt><dd className="pc-rows-hint">{r.hint || ''}</dd><dd>{r.value}</dd>
            </div>
          ))}
        </dl>
      </section>
      {s.sources.length ? (
        <section>
          <h3 className="pc-h3">Where the questions came from</h3>
          <BarList items={s.sources} bindTip={bindTip} />
        </section>
      ) : null}
      <section>
        <h3 className="pc-h3">What was billed</h3>
        {s.billing.length
          ? <Table head={['Kind', 'Events', 'Credits', 'Billed']} rows={s.billing.map((b) => [b.kind, b.events, b.credits, b.value])} />
          : <p className="pc-note">{s.billingEmpty}</p>}
        {s.billingNote ? <p className="pc-note" style={{ marginTop: '8px' }}>{s.billingNote}</p> : null}
      </section>
    </>
  );
}

function Queries({ s, bindTip }) {
  return (
    <>
      <section>
        <h3 className="pc-h3">Cost by query type</h3>
        {s.types.length
          ? <BarList items={s.types.map((t) => ({ label: t.label, value: t.value, pct: t.pct, hint: t.queries + (t.queries === 1 ? ' query' : ' queries') }))} bindTip={bindTip} />
          : <p className="pc-note">{s.typesEmpty}</p>}
      </section>
      {s.types.length ? (
        <section>
          <h3 className="pc-h3">One query of each type</h3>
          <Table head={['Query type', 'Average', 'p90', 'Model calls', 'Tool calls', 'Time', 'Price at ' + s.target]}
            rows={s.types.map((t) => [t.label, t.avg, t.p90, t.calls, t.tools, t.time, t.price])} />
        </section>
      ) : null}
      <section>
        <h3 className="pc-h3">Most expensive questions</h3>
        {s.top.length ? (
          <ol className="pc-questions">
            {s.top.map((q, i) => (
              <li key={i}>
                <p className="pc-q">{q.question || 'No question text recorded'}</p>
                <div className="pc-q-meta">
                  <span className="pc-strong">{q.value}</span><span>{q.type}</span>{q.who ? <span>{q.who}</span> : null}{q.when ? <span>{q.when}</span> : null}
                </div>
              </li>
            ))}
          </ol>
        ) : <p className="pc-note">{s.topEmpty}</p>}
      </section>
    </>
  );
}

function Models({ s, bindTip }) {
  return (
    <>
      <section>
        <h3 className="pc-h3">By agent</h3>
        {s.agents.length
          ? <BarList items={s.agents.map((a) => ({ label: a.label, value: a.value, pct: a.pct, hint: a.calls + ' calls' }))} bindTip={bindTip} />
          : <p className="pc-note">{s.agentsEmpty}</p>}
      </section>
      {s.models.length ? (
        <section>
          <h3 className="pc-h3">By model</h3>
          <Table head={['Model', 'Calls', 'Cost', 'Input tokens', 'Output tokens', 'Cached']} rows={s.models.map((m) => [m.label, m.calls, m.value, m.input, m.output, m.cached])} />
        </section>
      ) : null}
      <section>
        <h3 className="pc-h3">Tools</h3>
        <p className="pc-note" style={{ marginBottom: '8px' }}>Tools carry no model cost of their own; what they return feeds the model calls above.</p>
        {s.tools.length
          ? <Table head={['Tool', 'Calls', 'Average time', 'Failed']} rows={s.tools.map((t) => [t.label, t.calls, t.time, t.failed])} />
          : <p className="pc-note">{s.toolsEmpty}</p>}
      </section>
    </>
  );
}

function Migrations({ s }) {
  if (s.empty) return <p className="pc-note">{s.empty}</p>;
  return (
    <>
      <section>
        <h3 className="pc-h3">Runs</h3>
        <Table head={['Run', 'Started', 'Status', 'Model calls', 'Cost']} rows={s.runs.map((r) => [r.id, r.when, r.status, r.calls, r.value])} />
      </section>
      {s.stages.length ? (
        <section>
          <h3 className="pc-h3">By stage</h3>
          <Table head={['Stage', 'Model', 'Calls', 'Cost']} rows={s.stages.map((x) => [x.stage, x.model, x.calls, x.value])} />
        </section>
      ) : null}
    </>
  );
}

function Pricing({ s }) {
  if (s.empty) return <p className="pc-note">{s.empty}</p>;
  return (
    <section>
      <h3 className="pc-h3">{'Unit economics at a ' + s.target + ' target margin'}</h3>
      <dl className="pc-rows pc-rows--explained">
        {s.rows.map((r) => (
          <div key={r.label}><dt>{r.label}</dt><dd>{r.value}</dd>{r.hint ? <dd className="pc-rows-hint">{r.hint}</dd> : null}</div>
        ))}
      </dl>
    </section>
  );
}

export default function PlatformCostPanel({ panel, bindTip }) {
  const closeRef = React.useRef(null);
  const closeFn = React.useRef(panel.close);
  closeFn.current = panel.close;
  const tabRefs = React.useRef({});
  // Opening a company (or swapping to another) puts focus in the panel; Esc closes it, and
  // closing hands focus back to the row it was opened from.
  // Declared first: effects run in order, so the opener is read before focus moves to Close.
  React.useEffect(() => {
    const opener = document.activeElement;
    return () => { if (opener && opener.isConnected && typeof opener.focus === 'function') opener.focus(); };
  }, []);
  React.useEffect(() => { if (closeRef.current) closeRef.current.focus(); }, [panel.id]);
  React.useEffect(() => {
    const onKey = (e) => { if (e.key === 'Escape') closeFn.current(); };
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);
  const onTabKey = (e) => {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    e.preventDefault();
    const i = panel.tabs.findIndex((t) => t.on);
    const next = panel.tabs[(i + (e.key === 'ArrowRight' ? 1 : panel.tabs.length - 1)) % panel.tabs.length];
    next.pick();
    if (tabRefs.current[next.key]) tabRefs.current[next.key].focus();
  };
  const s = panel.sheet;
  return (
    <aside className="pc-panel" id="pc-panel" role="dialog" aria-modal="false" aria-labelledby="pc-panel-title">
      <header className="pc-panel-head">
        <div>
          <h2 className="pc-panel-title" id="pc-panel-title">{panel.name || 'Company'}</h2>
          <p className="pc-panel-month">{panel.monthLabel}</p>
        </div>
        <button type="button" className="pc-icon-btn" ref={closeRef} onClick={panel.close} aria-label="Close"><i className="ph ph-x"></i></button>
      </header>
      {panel.figures.length ? (
        <dl className="pc-panel-figs">
          {panel.figures.map((f) => <div key={f.label}><dt>{f.label}</dt><dd className="pc-num"><ToneIcon tone={f.tone} />{f.value}</dd></div>)}
        </dl>
      ) : null}
      <div className="pc-tabs" role="tablist" aria-label="Breakdown" onKeyDown={onTabKey}>
        {panel.tabs.map((t) => (
          <button key={t.key} type="button" role="tab" id={'pc-tab-' + t.key} aria-selected={t.on} aria-controls="pc-tabpanel"
            tabIndex={t.on ? 0 : -1} ref={(el) => { tabRefs.current[t.key] = el; }} className="pc-tab" onClick={t.pick}>{t.label}</button>
        ))}
      </div>
      <div className="pc-panel-body" role="tabpanel" id="pc-tabpanel" aria-labelledby={'pc-tab-' + panel.tab}>
        {panel.error ? (
          <div className="pc-callout" role="alert">
            <i className="ph ph-warning-circle" aria-hidden="true"></i>
            <div><strong>{'Could not read ' + (panel.name || 'this company') + '.'}</strong><p>{panel.error}</p></div>
            <button type="button" className="pc-btn" onClick={panel.retry}>Try again</button>
          </div>
        ) : null}
        {panel.warning ? (
          <div className="pc-callout" role="status"><i className="ph ph-warning" aria-hidden="true"></i><div>{panel.warning}</div></div>
        ) : null}
        {!s && panel.loading ? (
          <div aria-hidden="true" style={{ display: 'grid', gap: '12px' }}>
            <div className="pc-skel" style={{ height: '140px' }}></div>
            <div className="pc-skel" style={{ height: '18px', width: '60%' }}></div>
            <div className="pc-skel" style={{ height: '18px', width: '80%' }}></div>
            <div className="pc-skel" style={{ height: '18px', width: '45%' }}></div>
          </div>
        ) : null}
        {s && panel.tab === 'overview' ? <Overview s={s} monthLabel={panel.monthLabel} bindTip={bindTip} /> : null}
        {s && panel.tab === 'queries' ? <Queries s={s} bindTip={bindTip} /> : null}
        {s && panel.tab === 'models' ? <Models s={s} bindTip={bindTip} /> : null}
        {s && panel.tab === 'migrations' ? <Migrations s={s} /> : null}
        {s && panel.tab === 'pricing' ? <Pricing s={s} /> : null}
      </div>
    </aside>
  );
}
