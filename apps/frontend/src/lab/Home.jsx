// Home — where the loop stands for this company and the one thing to do next (logic/home.js). Reads replies Trace
// Lab already serves; any that fails is simply unknown on the page.
import React from 'react';
import { goldenApi } from '../tracelab/goldenApi.js';
import { reviewApi } from '../tracelab/reviewApi.js';
import { taxonomyApi } from '../tracelab/taxonomyApi.js';
import { judgesApi } from '../tracelab/judgesApi.js';
import { watchApi } from '../tracelab/watchApi.js';
import { homeView, summarize } from './logic/home.js';

const settle = (p) => p.then((v) => v, () => null);

export function HomeView({ company, v, onOpen }) {
  return (
    <div className="lab-home">
      <div>
        <h1>{company}</h1>
        <p className="lab-small lab-big">{'Where the checking loop stands, and the one thing to do next.'}</p>
      </div>
      <section className="lab-next" aria-label="Next step">
        <div><span className="lab-next-label">{'Next step'}</span><span className="lab-next-text">{v.next.text}</span></div>
        {v.next.tab ? <button type="button" className="lab-btn" onClick={() => onOpen(v.next.tab)}>{v.next.button}</button> : null}
      </section>
      <section className="lab-stages" aria-label="The loop">
        {v.stages.map((x) => (
          <article key={x.name} className="lab-card">
            <span className="lab-card-name">{x.name}</span>
            <span className="lab-card-big">{x.big}</span>
            <span className="lab-small">{x.line}</span>
            <button type="button" className="lab-link" onClick={() => onOpen(x.tab)}>{'Open ' + x.name.toLowerCase()}</button>
          </article>
        ))}
      </section>
    </div>
  );
}

export default function Home({ company, onOpen }) {
  const [summary, setSummary] = React.useState(() => summarize({}));
  React.useEffect(() => {
    let alive = true;
    Promise.all([
      settle(goldenApi.sets()), settle(goldenApi.runs({ limit: 5 })), settle(reviewApi.progress({})),
      settle(taxonomyApi.unfiledNotes({})), settle(taxonomyApi.modes({})), settle(judgesApi.judges({})), settle(watchApi.monitorBatches({ limit: 20 }))
    ]).then(([sets, runs, progress, notes, modes, judges, batches]) => {
      if (alive) setSummary(summarize({ sets, runs, progress, notes, modes, judges, batches }));
    });
    return () => { alive = false; };
  }, [company]);
  return <HomeView company={company} v={homeView(summary)} onOpen={onOpen} />;
}
