// ProgressView — how far one run's review has come (22 Oct 2026 feedback, after Cartwheel's "Review completion" and
// "Failure modes"): five tiles (questions someone reviewed, questions with a recorded answer, failure modes, label cells
// filled, notes), then a card per failure mode, the one found most often first: how many of the run's questions someone
// marked positive, how many only negative, the same count over the last runs (so a mode getting better or worse shows)
// and up to three notes written on its positive questions. With no run named, the newest run the caller can see.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import './labels.css';

const num = (n) => (Number(n) || 0).toLocaleString('en-GB');
function when(iso) {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' });
}

// The change since the run before: more positive questions is worse, fewer is better.
export function moveOf(trend) {
  const t = trend || [];
  if (t.length < 2) return null;
  const prev = t[t.length - 2];
  const d = (Number(t[t.length - 1].positives) || 0) - (Number(prev.positives) || 0);
  if (d > 0) return { dir: 'up', text: d + ' more than ' + prev.run };
  if (d < 0) return { dir: 'down', text: -d + ' fewer than ' + prev.run };
  return { dir: 'same', text: 'Same as ' + prev.run };
}

function Trend({ trend, current }) {
  const t = trend || [];
  if (!t.length) return null;
  const top = Math.max(1, ...t.map((x) => Number(x.positives) || 0));
  const move = moveOf(t);
  return (
    <div className="lab-lv-trend-wrap">
      <span className="lab-lv-label">{'Positive questions by run'}</span>
      <div className="lab-row lab-lv-trend-row">
        <ol className="lab-lv-trend" aria-label={'Positive questions by run: ' + t.map((x) => x.run + ' ' + (Number(x.positives) || 0)).join(' · ')}>
          {t.map((x) => {
            const n = Number(x.positives) || 0;
            return (
              <li key={x.run} className={x.run === current ? 'lab-on' : undefined} title={x.run + ': ' + n + ' positive'}>
                <span className="lab-lv-trend-n">{n}</span>
                <span className="lab-lv-trend-bar" aria-hidden="true"><span style={{ height: Math.round((n / top) * 100) + '%' }} /></span>
                <span className="lab-lv-trend-run">{x.run}</span>
              </li>
            );
          })}
        </ol>
        {move ? <span className={'lab-lv-move lab-lv-move-' + move.dir}>{move.text}</span> : null}
      </div>
    </div>
  );
}

function ModeCard({ m, current }) {
  const pos = Number(m.positives) || 0;
  const examples = (m.examples || []).slice(0, 3);
  return (
    <article className={'lab-lv-mcard' + (pos ? ' lab-lv-mcard-hot' : '')}>
      <div className="lab-lv-mcard-head">
        <h3 className="lab-lv-mcard-name">{String(m.name || '').replace(/_/g, '_​')}</h3>
        <div className="lab-lv-mcard-count">
          <span className="lab-lv-mcard-n">{num(pos)}</span>
          <span className="lab-lv-mcard-unit">{pos === 1 ? 'positive question' : 'positive questions'}</span>
        </div>
      </div>
      {m.definition ? <p className="lab-lv-mcard-def" title={m.definition}>{m.definition}</p> : <p className="lab-small lab-muted">{'No definition yet.'}</p>}
      <span className="lab-lv-count lab-lv-count-negative lab-small">{'negative ' + num(m.negatives)}</span>
      <Trend trend={m.trend} current={current} />
      {pos ? (
        <div className="lab-lv-examples-wrap">
          <span className="lab-lv-label">{'From notes on its positive questions'}</span>
          {examples.length ? (
            <ul className="lab-lv-examples">
              {examples.map((x, i) => (
                <li key={i}>
                  <p className="lab-lv-example" title={x.note}>{x.note}</p>
                  <span className="lab-small">{(x.by || 'Someone') + (when(x.at) ? ' · ' + when(x.at) : '')}</span>
                </li>
              ))}
            </ul>
          ) : <p className="lab-small lab-muted">{'No notes on its positive questions yet.'}</p>}
        </div>
      ) : <p className="lab-small lab-muted">{'No positive questions in ' + current + '.'}</p>}
    </article>
  );
}

function Tiles({ t }) {
  const tiles = [
    { key: 'reviewed', n: t.reviewed, of: t.questions, text: 'reviewed questions' },
    { key: 'traced', n: t.traced, base: t.questions, text: 'with answers' },
    { key: 'modes', n: t.modes, text: 'failure modes' },
    { key: 'cells', n: t.cells_filled, of: t.cells_total, text: 'label cells' },
    { key: 'notes', n: t.notes, text: 'notes' }];
  return (
    <div className="lab-lv-tiles">
      {tiles.map((x) => {
        const base = x.of !== undefined ? Number(x.of) || 0 : Number(x.base) || 0;
        const share = base ? Math.round(Math.min(1, (Number(x.n) || 0) / base) * 100) : 0;
        return (
          <div key={x.key} className="lab-lv-tile">
            <span className="lab-lv-tile-n">{num(x.n)}{x.of !== undefined ? <span className="lab-lv-tile-of">{' / ' + num(x.of)}</span> : null}</span>
            <span className="lab-lv-tile-text">{x.text}</span>
            {base ? <span className="lab-lv-meter" role="img" aria-label={num(x.n) + ' of ' + num(base)}><span style={{ width: share + '%' }} /></span> : null}
          </div>
        );
      })}
    </div>
  );
}

// The page from what GET /lab/progress returned; nothing here reads the service.
export function ProgressBody({ data, loading, error, canEdit, onRetry }) {
  const head = (sub) => (
    <div className="lab-page-head">
      <h1>{'Progress'}</h1>
      <p className="lab-small lab-big">{sub}</p>
    </div>
  );
  if (!data) {
    return (
      <div className="lab-page-body lab-page-full">
        {head('How far the review has come, and which failure modes it found.')}
        {error ? <div role="alert" className="lab-error">{error}</div> : <p className="lab-small">{loading === false ? '' : 'Reading…'}</p>}
        {error && onRetry ? <div className="lab-row"><button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onRetry}>{'Try again'}</button></div> : null}
      </div>
    );
  }
  if (!data.run) {
    return (
      <div className="lab-page-body lab-page-full">
        {head('How far the review has come, and which failure modes it found.')}
        <div className="lab-empty lab-empty-big">
          <h2>{'No runs yet'}</h2>
          <p className="lab-small">{'Runs appear here once questions are imported, synced from the chat or typed in.'}</p>
        </div>
      </div>
    );
  }
  const code = data.run.code;
  const modes = (data.modes || []).slice().sort((a, b) => (Number(b.positives) || 0) - (Number(a.positives) || 0)
    || String(a.name || '').localeCompare(String(b.name || '')));
  return (
    <div className="lab-page-body lab-page-full lab-lv-page">
      {head('Review completion for ' + code + (data.run.title ? ' · ' + data.run.title : '') + ', and the failure modes its labels found.')}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {loading ? <p className="lab-small">{'Reading…'}</p> : null}
      <section className="lab-lv-block" aria-labelledby="lab-lv-done">
        <h2 id="lab-lv-done" className="lab-lv-h2">{'Review completion'}</h2>
        <Tiles t={data.tiles || {}} />
      </section>
      <section className="lab-lv-block" aria-labelledby="lab-lv-modes">
        <h2 id="lab-lv-modes" className="lab-lv-h2">{'Failure modes'}</h2>
        {modes.length ? (
          <div className="lab-lv-cards">{modes.map((m) => <ModeCard key={m.lineage_id || m.name} m={m} current={code} />)}</div>
        ) : (
          <div className="lab-empty">
            <h2>{'No failure modes yet'}</h2>
            <p className="lab-small">{canEdit
              ? 'Add them on Labels, from the coding agent’s taxonomy: each one becomes a column every reviewer marks positive or negative.'
              : 'The platform team or the consultant adds them on Labels, from the coding agent’s taxonomy.'}</p>
          </div>
        )}
      </section>
    </div>
  );
}

export default function ProgressView({ me, run }) {
  const [res, setRes] = React.useState({ data: null, loading: true, error: '' });
  const [tick, setTick] = React.useState(0);
  React.useEffect(() => {
    let live = true;
    const wanted = run ? String(run).toUpperCase() : null;
    setRes((p) => ({ data: p.data && p.data.run && (!wanted || String(p.data.run.code).toUpperCase() === wanted) ? p.data : null,
      loading: true, error: '' }));
    labApi.progress(run || undefined)
      .then((out) => { if (live) setRes({ data: out || null, loading: false, error: '' }); })
      .catch((e) => { if (live) setRes((p) => Object.assign({}, p, { loading: false, error: errText(e) })); });
    return () => { live = false; };
  }, [run, tick]);
  return <ProgressBody data={res.data} loading={res.loading} error={res.error} canEdit={!!(me && me.all_companies)}
    onRetry={() => setTick((n) => n + 1)} />;
}
