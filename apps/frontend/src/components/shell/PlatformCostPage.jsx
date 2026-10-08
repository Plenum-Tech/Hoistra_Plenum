// PlatformCostPage — the Super Admin console's second page: what each company cost to run in
// a month against what it was billed. `vals` carries the pc* view model (logic/platformCost.js);
// styling is in styles/platformcost.css. The page leads with the answer as one sentence and
// the two bars it rests on (cost, split by where it comes from, against billed, on one dollar
// scale), then the companies behind it. A company opens in PlatformCostPanel.
import React from 'react';
import PlatformCostPanel, { ToneIcon } from './PlatformCostPanel.jsx';

// One tooltip for the page. Marks spread bindTip(value, label) onto themselves; the tooltip
// sits above the mark, values first. It enhances only: every value is also on the page.
function useTip(resetKey) {
  const [tip, setTip] = React.useState(null);
  // A tooltip is pinned to where its mark was; a scroll moves the mark, and a new month can
  // unmount it, so either hides it.
  React.useEffect(() => {
    const hide = () => setTip(null);
    window.addEventListener('scroll', hide, true);
    return () => window.removeEventListener('scroll', hide, true);
  }, []);
  React.useEffect(() => { setTip(null); }, [resetKey]);
  const bind = React.useCallback((value, label) => {
    const show = (e) => {
      const r = e.currentTarget.getBoundingClientRect();
      const x = Math.min(Math.max(r.left + r.width / 2, 90), window.innerWidth - 90);
      setTip({ value, label, x, y: r.top });
    };
    const hide = () => setTip(null);
    return { tabIndex: 0, role: 'img', 'aria-label': value + ', ' + label, onPointerEnter: show, onFocus: show, onPointerLeave: hide, onBlur: hide };
  }, []);
  const node = tip ? (
    <div className="pc-tip" role="tooltip" style={{ left: tip.x + 'px', top: tip.y + 'px' }}>
      <strong>{tip.value}</strong><span>{tip.label}</span>
    </div>
  ) : null;
  return [bind, node];
}

function Hero({ hero, bindTip }) {
  const { cost, billed } = hero.bars;
  // The plot keeps 96px at the right for the value that sits at each bar's tip.
  const w = (pct) => 'calc((100% - 96px) * ' + pct / 100 + ')';
  return (
    <section className="pc-hero" aria-label="The month's result">
      <p className="pc-answer">
        {hero.sentence.map((p, i) => (p.strong ? <strong key={i}>{p.text}</strong> : <React.Fragment key={i}>{p.text}</React.Fragment>))}
      </p>
      <ul className="pc-ledger">
        <li className="pc-ledger-row">
          <span className="pc-ledger-name">Cost</span>
          <div className="pc-ledger-plot">
            <div className="pc-stack" style={{ width: w(cost.pct) }}>
              {cost.segments.filter((s) => s.pct > 0).map((s) => (
                <span key={s.key} className={'pc-seg pc-seg--' + s.key} style={{ flex: s.pct + ' 1 0px' }} {...bindTip(s.value, s.label)}></span>
              ))}
            </div>
            <span className="pc-ledger-value">{cost.value}</span>
          </div>
        </li>
        <li className="pc-ledger-row">
          <span className="pc-ledger-name">Billed</span>
          <div className="pc-ledger-plot">
            <div className="pc-stack" style={{ width: w(billed.pct) }}>
              {billed.pct > 0 ? <span className="pc-seg pc-seg--billed" style={{ flex: '1 1 0px' }} {...bindTip(billed.value, 'Billed')}></span> : null}
            </div>
            <span className="pc-ledger-value">{billed.value}</span>
          </div>
        </li>
      </ul>
      <ul className="pc-legend" aria-label="Key">
        {hero.legend.map((l) => (
          <li key={l.key}><span className={'pc-key pc-key--' + l.key}></span>{l.label}<span className="pc-num">{l.value}</span></li>
        ))}
      </ul>
    </section>
  );
}

// The four figures the page opens with. Each card's key is the chart colour of what it
// measures (the cost card carries both cost colours), so the cards read the bars below them.
const CARD_KEYS = { cost: ['infra', 'chat'], billed: ['billed'], perQuery: ['chat'], margin: [] };

function Cards({ cards }) {
  return (
    <ul className="pc-cards" aria-label="The month in four figures">
      {cards.map((k) => (
        <li key={k.key} className="pc-card">
          <div className="pc-card-label">
            {(CARD_KEYS[k.key] || []).map((c) => <span key={c} className={'pc-key pc-key--' + c} aria-hidden="true"></span>)}
            {k.label}
          </div>
          <div className="pc-card-value"><ToneIcon tone={k.tone} />{k.value}</div>
          <div className="pc-card-hint">{k.hint}</div>
        </li>
      ))}
    </ul>
  );
}

function Companies({ rows, quiet, monthLabel }) {
  return (
    <section className="pc-section" aria-labelledby="pc-companies">
      <div className="pc-section-head">
        <h2 className="pc-h2" id="pc-companies">Companies</h2>
        <p className="pc-hint">Select a company to see where its cost went.</p>
      </div>
      <div className="pc-table-wrap">
        <table className="pc-table">
          <thead>
            <tr>
              <th scope="col" className="pc-left">Company</th>
              <th scope="col" className="pc-opt">Queries</th>
              <th scope="col" className="pc-opt">Model cost</th>
              <th scope="col" className="pc-opt">Infrastructure</th>
              <th scope="col">Total cost</th>
              <th scope="col">Billed</th>
              <th scope="col" className="pc-opt-narrow">Profit</th>
              <th scope="col">Margin</th>
              <th scope="col" className="pc-bars-col">
                <span className="pc-mini-keys">
                  <span><span className="pc-key pc-key--infra"></span><span className="pc-key pc-key--chat"></span>Cost</span>
                  <span><span className="pc-key pc-key--billed"></span>Billed</span>
                </span>
              </th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id} className={r.selected ? 'is-selected' : ''} onClick={r.open}>
                <th scope="row" className="pc-left">
                  <button type="button" className="pc-rowbtn" aria-expanded={r.selected} aria-controls="pc-panel" title={r.name} onClick={(e) => { e.stopPropagation(); r.open(); }}>
                    <i className="ph ph-caret-right" aria-hidden="true"></i><span className="pc-rowname">{r.name}</span>
                  </button>
                </th>
                <td className="pc-opt">{r.queries}</td>
                <td className="pc-opt">{r.model}</td>
                <td className="pc-opt">{r.infra}</td>
                <td className="pc-strong">{r.cost}</td>
                <td>{r.billed}</td>
                <td className="pc-opt-narrow">{r.profit}</td>
                <td><span className="pc-margin"><ToneIcon tone={r.tone} />{r.margin}</span></td>
                <td className="pc-bars-col" aria-hidden="true">
                  <div className="pc-mini">
                    <div className="pc-mini-row">
                      <span style={{ width: r.bars.infraPct + '%', background: 'var(--pc-infra)' }}></span>
                      <span style={{ width: r.bars.modelPct + '%', background: 'var(--pc-chat)' }}></span>
                    </div>
                    <div className="pc-mini-row">
                      <span style={{ width: r.bars.billedPct + '%', background: 'var(--pc-billed)' }}></span>
                    </div>
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {quiet ? (
        <p className="pc-quiet">
          {'No activity in ' + monthLabel + ': '}
          {quiet.names.map((n, i) => <React.Fragment key={i}>{i ? ', ' : ''}<strong>{n}</strong></React.Fragment>)}
          {'. ' + quiet.note}
        </p>
      ) : null}
    </section>
  );
}

function Lower({ models, modelsEmpty, counting, bindTip }) {
  return (
    <div className="pc-lower">
      <section aria-labelledby="pc-models">
        <div className="pc-section-head">
          <h2 className="pc-h2" id="pc-models">Model spend</h2>
          <p className="pc-hint">All companies, by model</p>
        </div>
        {models.length ? (
          <ul className="pc-barlist">
            {models.map((m) => (
              <li key={m.model}>
                <span className="pc-barlist-label" title={m.model}>{m.model}</span>
                <span className="pc-barlist-track"><span className="pc-barlist-bar" style={{ width: m.pct + '%' }} {...bindTip(m.value, m.model + ', ' + m.calls + ' calls')}></span></span>
                <span className="pc-num">{m.value}</span>
                <span className="pc-muted pc-num">{m.share}</span>
                <span className="pc-muted pc-num">{m.calls + ' calls'}</span>
              </li>
            ))}
          </ul>
        ) : <p className="pc-note">{modelsEmpty}</p>}
      </section>
      {counting ? (
        <section aria-labelledby="pc-counting">
          <div className="pc-section-head"><h2 className="pc-h2" id="pc-counting">How this is counted</h2></div>
          <ul className="pc-counting">{counting.lines.map((l) => <li key={l}>{l}</li>)}</ul>
          {counting.notRecorded.length ? (
            <>
              <h3 className="pc-h3">Not recorded yet</h3>
              <ul className="pc-counting">{counting.notRecorded.map((l) => <li key={l}>{l.charAt(0).toUpperCase() + l.slice(1)}</li>)}</ul>
            </>
          ) : null}
        </section>
      ) : null}
    </div>
  );
}

function Skeleton() {
  return (
    <div aria-hidden="true">
      <div className="pc-cards">
        {[0, 1, 2, 3].map((i) => <div key={i} className="pc-skel" style={{ height: '104px', borderRadius: '12px' }}></div>)}
      </div>
      <div className="pc-hero">
        <div className="pc-skel" style={{ height: '30px', width: 'min(720px, 92%)' }}></div>
        <div className="pc-skel" style={{ height: '30px', width: 'min(420px, 60%)', marginTop: '10px' }}></div>
        <div className="pc-skel" style={{ height: '22px', width: 'min(900px, 96%)', marginTop: '28px' }}></div>
        <div className="pc-skel" style={{ height: '22px', width: '22%', marginTop: '10px' }}></div>
      </div>
      <div className="pc-skel" style={{ height: '180px', marginTop: '48px', borderRadius: '12px' }}></div>
    </div>
  );
}

export default function PlatformCostPage({ vals }) {
  const st = vals.pcStatus || {};
  const month = vals.pcMonth || {};
  const rows = vals.pcRows || [];
  const [bindTip, tipNode] = useTip(month.label);
  const hasData = !!(vals.pcHero || rows.length || vals.pcEmpty);
  return (
    <div className="pc">
      <header className="pc-head">
        <div>
          <h1 className="pc-title">Platform cost</h1>
          <p className="pc-sub">What each company cost to run, against what it was billed.</p>
        </div>
        <div className="pc-controls">
          <div className="pc-month" role="group" aria-label="Month">
            <button type="button" className="pc-icon-btn" onClick={month.prev} aria-label="Previous month"><i className="ph ph-caret-left"></i></button>
            <span className="pc-month-label" aria-live="polite">{month.label}</span>
            <button type="button" className="pc-icon-btn" onClick={month.next || undefined} disabled={!month.next} aria-label="Next month"><i className="ph ph-caret-right"></i></button>
          </div>
          <button type="button" className="pc-btn" onClick={vals.pcReload} disabled={st.loading || st.refreshing}>
            <i className={'ph ph-arrow-clockwise' + (st.loading || st.refreshing ? ' is-spinning' : '')} aria-hidden="true"></i>
            {st.loading || st.refreshing ? 'Reading…' : 'Refresh'}
          </button>
          {vals.pcReadAt ? <span className="pc-readat">{vals.pcReadAt}</span> : null}
        </div>
      </header>

      {st.error ? (
        <div className="pc-callout" role="alert">
          <i className="ph ph-warning-circle" aria-hidden="true"></i>
          <div>
            <strong>{hasData ? 'Could not read ' + month.label + '.' : 'Platform cost could not be read.'}</strong>
            <p>{st.error + (hasData && st.showing && st.showing !== month.label ? '. The figures below are still ' + st.showing + '.' : '')}</p>
          </div>
          <button type="button" className="pc-btn" onClick={st.retry}>Try again</button>
        </div>
      ) : null}

      {st.loading ? <Skeleton /> : null}

      {hasData ? (
        <div className={'pc-body' + (st.refreshing ? ' is-refreshing' : '')} aria-busy={st.refreshing ? 'true' : 'false'}>
          {vals.pcCards ? <Cards cards={vals.pcCards} /> : null}
          {vals.pcHero ? <Hero hero={vals.pcHero} bindTip={bindTip} /> : null}
          {vals.pcEmpty ? <div className="pc-empty"><p className="pc-answer" style={{ fontSize: '22px' }}>{vals.pcEmpty}</p></div> : null}
          {vals.pcCounting && vals.pcCounting.warning ? (
            <div className="pc-callout" role="status"><i className="ph ph-warning" aria-hidden="true"></i><div>{vals.pcCounting.warning}</div></div>
          ) : null}
          {rows.length ? <Companies rows={rows} quiet={vals.pcQuiet} monthLabel={st.showing || month.label} /> : null}
          <Lower models={vals.pcModels || []} modelsEmpty={vals.pcModelsEmpty} counting={vals.pcCounting} bindTip={bindTip} />
        </div>
      ) : null}

      {vals.pcPanel ? <PlatformCostPanel panel={vals.pcPanel} bindTip={bindTip} /> : null}
      {tipNode}
    </div>
  );
}
