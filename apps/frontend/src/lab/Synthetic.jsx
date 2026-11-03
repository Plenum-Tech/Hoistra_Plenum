// Synthetic — generate a batch of questions for one instance, and ask a few of them for real.
//
// A batch belongs to one instance. A question asked in Ops Brain cannot reach the compliance
// engine, so "50 synthetic questions" is not one set: it is a set per instance, and a batch
// generated without knowing its instance fills with questions the instance refuses. A refusal
// scored as an answer measures the gate instead of the engines, so every generated question is
// put to the REAL router before it is listed and one that routes outside its instance is
// dropped. That check is what makes this a generator rather than a list of strings.
//
// Asking is separate from generating, and deliberately small: each question is a real chat turn
// against a real model and costs money. Answers are shown here and not stored — keeping them
// needs a scenario table, and a Trace Lab migration is applied by a person.
import React from 'react';
import { labApi } from './labApi.js';
import { INSTANCES } from '../logic/instances.js';

const COUNTS = [10, 25, 50, 100];
const ASK = [1, 2, 3, 5];
const REFUSALS = [[0, 'none'], [0.1, '10%'], [0.25, '25%']];

const secs = (ms) => Math.round((Number(ms) || 0) / 1000) + 's';
const usd = (n) => '$' + Number(n || 0).toFixed(4);

export default function Synthetic({ instance, onPick }) {
  // The instance is chosen here and on the Instance page, and both write the same state in
  // LabApp. Two VIEWS of one setting cannot disagree; two settings would.
  const space = instance;
  const [spaces, setSpaces] = React.useState([]);
  const [count, setCount] = React.useState(10);
  const [seed, setSeed] = React.useState(1);
  const [challenge, setChallenge] = React.useState(0);
  const [askCount, setAskCount] = React.useState(2);
  // Off by default: a batch should differ from the one before unless somebody asks
  // for the same one back.
  const [pinned, setPinned] = React.useState(false);

  const [batch, setBatch] = React.useState(null);
  const [busy, setBusy] = React.useState('');
  const [error, setError] = React.useState('');
  const [run, setRun] = React.useState(null);

  // A batch on screen belongs to the instance it was generated in. Switching instance elsewhere
  // must not leave Ops Brain questions sitting under a Compliance Engine heading.
  React.useEffect(() => { setBatch(null); setRun(null); setError(''); }, [instance]);

  React.useEffect(() => {
    let gone = false;
    labApi.spaces()
      .then((out) => { if (!gone) setSpaces((out && out.spaces) || []); })
      .catch(() => { if (!gone) setSpaces([]); });
    return () => { gone = true; };
  }, []);

  // The batch on screen belongs to the settings that made it; changing any of them clears it
  // rather than leaving results above controls that no longer describe them.
  const change = (fn) => (v) => { fn(v); setBatch(null); setRun(null); setError(''); };

  async function generate() {
    setBusy('generate');
    setError('');
    setBatch(null);
    setRun(null);
    // A new seed each time, so pressing Generate twice does not hand back the same ten questions.
    // The seed stays on screen and editable: typing one back reproduces that batch exactly, which
    // is how a failure is re-run.
    const next = pinned ? Number(seed) || 0 : Math.floor(Math.random() * 1000000);
    if (!pinned) setSeed(next);
    try {
      setBatch(await labApi.generate({ space, count: Number(count), seed: next, challenge_ratio: Number(challenge) }));
    } catch (e) {
      setError((e && e.message) || String(e));
    } finally {
      setBusy('');
    }
  }

  async function ask() {
    setBusy('ask');
    setError('');
    setRun(null);
    try {
      setRun(await labApi.runScenarios({ space, count: Number(askCount), seed: Number(seed) || 0, challenge_ratio: Number(challenge) }));
    } catch (e) {
      const problems = (e && e.body && e.body.detail && e.body.detail.problems) || [];
      setError(((e && e.message) || String(e)) + (problems.length ? ' — ' + problems.join('; ') : ''));
    } finally {
      setBusy('');
    }
  }

  const chosen = spaces.find((s) => s.key === space);

  return (
    <div className="lab-page-body lab-page-full">
      <div className="lab-page-head">
        <h1>{'Synthetic questions'}</h1>
        <p className="lab-small lab-big">
        {'Generate a batch for one instance, then ask a few of them for real. Every question is put to the real router first: one that would route outside its instance is dropped, because a question the instance refuses measures the gate rather than the engines.'}
        </p>
      </div>

      <div className="lab-card">
        <div className="lab-row">
          <label className="lab-small" htmlFor="synthetic-instance">{'Instance'}</label>
          <select id="synthetic-instance" className="lab-input" value={space}
            onChange={(e) => onPick(e.target.value)}>
            {INSTANCES.map((i) => <option key={i.key} value={i.key}>{i.name}</option>)}
          </select>
          <span className="lab-note">{'Which modules may answer. The Instance page says what each one is for.'}</span>
        </div>
        {chosen && chosen.engines && chosen.engines.length ? (
          <p className="lab-note">{'Answers can come from: ' + chosen.engines.join(', ') + '.'}</p>
        ) : null}

        <div className="lab-row">
          <span className="lab-small">{'How many'}</span>
          <div className="lab-seg" role="radiogroup" aria-label="How many">
          {COUNTS.map((n) => (
            <button key={n} type="button" className={'lab-seg-btn' + (Number(count) === n ? ' lab-on' : '')}
              aria-pressed={Number(count) === n ? 'true' : 'false'}
              onClick={() => change(setCount)(n)}>{n}</button>
          ))}
          </div>
        </div>

        <div className="lab-row">
          <span className="lab-small">{'Seed'}</span>
          <input type="number" min="0" value={seed} className="lab-input"
            onChange={(e) => { change(setSeed)(e.target.value); setPinned(true); }} />
          <label className="lab-small">
            <input type="checkbox" checked={pinned} onChange={(e) => setPinned(e.target.checked)} />
            {' keep this seed'}
          </label>
          <span className="lab-note">
            {pinned
              ? 'Pinned: Generate gives the same batch back, which is how a failure is re-run exactly.'
              : 'A new seed each time, so every batch differs. Pin it to get this one back.'}
          </span>
        </div>

        <div className="lab-row">
          <span className="lab-small">{'Include refusals'}</span>
          <div className="lab-seg" role="radiogroup" aria-label="Include refusals">
          {REFUSALS.map(([r, lab]) => (
            <button key={lab} type="button" className={'lab-seg-btn' + (Number(challenge) === r ? ' lab-on' : '')}
              aria-pressed={Number(challenge) === r ? 'true' : 'false'}
              onClick={() => change(setChallenge)(r)}>{lab}</button>
          ))}
          </div>
          <span className="lab-note">{'Questions this instance must refuse. A gate that stops working is silent otherwise.'}</span>
        </div>

        <div className="lab-row">
          <button type="button" className="lab-btn" disabled={!!busy} onClick={generate}>
            {busy === 'generate' ? 'Generating…' : 'Generate'}
          </button>
          <span className="lab-small">{'Ask the first'}</span>
          <div className="lab-seg" role="radiogroup" aria-label="Ask the first">
          {ASK.map((n) => (
            <button key={n} type="button" className={'lab-seg-btn' + (Number(askCount) === n ? ' lab-on' : '')}
              aria-pressed={Number(askCount) === n ? 'true' : 'false'}
              onClick={() => setAskCount(n)}>{n}</button>
          ))}
          </div>
          <button type="button" className="lab-btn" disabled={!!busy} onClick={ask}>
            {busy === 'ask' ? 'Asking…' : 'Ask them for real'}
          </button>
        </div>
        <p className="lab-note">
          {'Each question asked is a real chat turn in this instance: a model is called and it costs money. Answers are shown here and not stored.'}
        </p>
      </div>

      {error ? <div className="lab-bad" role="alert">{error}</div> : null}

      {run ? (
        <div className="lab-card">
          <h2>{'Answers'}</h2>
          <p className="lab-note" aria-live="polite">
            {run.asked + ' asked in ' + (run.space || '—') + ' · ' + secs(run.ms) + ' · ' + usd(run.cost_usd)
              + (run.persisted ? '' : ' · not stored')}
          </p>
          {(run.results || []).map((r) => (
            <article key={r.scenario_id} className="lab-qitem">
              <p className="lab-note">
                {r.scenario_id + ' · ' + (r.engine || '—') + (r.in_module ? '' : ' · must be refused') + (r.ok ? '' : ' · failed')}
              </p>
              <p><strong>{r.question}</strong></p>
              {r.error ? <p className="lab-bad">{r.error}</p> : <p>{r.answer || '(no answer)'}</p>}
              <p className="lab-note">
                {'Tools: ' + ((r.tools || []).length ? r.tools.join(' → ') : 'none')
                  + ' · Models: ' + ((r.models || []).length ? r.models.join(', ') : '—')
                  + ' · ' + (r.spans || 0) + ' spans · ' + secs(r.ms) + ' · ' + usd(r.cost_usd)}
              </p>
              {/* Every step, as the Runs page draws them: what ran, what it was given and what
                  it returned. Shown here so a batch can be read before it is saved -- the saved
                  one opens in Runs with notes beside each block. */}
              {(r.steps || []).length ? (
                <details className="lab-steps">
                  <summary className="lab-small">{'Steps · ' + r.steps.length}</summary>
                  <ol className="lab-steplist">
                    <li className="lab-step">
                      <span className="lab-stepkind">{'USER'}</span>
                      <span>{r.question}</span>
                    </li>
                    {r.steps.map((st, i) => (
                      <li key={st.id || i} className="lab-step"
                        style={{ marginLeft: st.parent_id ? '16px' : 0 }}>
                        <span className="lab-stepkind">{(st.kind || 'step').toUpperCase()}</span>
                        <span>
                          <strong>{st.name || '—'}</strong>
                          {st.agent ? ' · ' + st.agent : ''}
                          {st.model ? ' · ' + st.model : ''}
                          {st.ms != null ? ' · ' + (st.ms / 1000).toFixed(2) + ' s' : ''}
                          {st.cost_usd ? ' · $' + Number(st.cost_usd).toFixed(4) : ''}
                          {st.ok === false ? ' · failed' : ''}
                        </span>
                        {st.input ? <div className="lab-steppayload">{'Given: ' + st.input}</div> : null}
                        {st.output ? <div className="lab-steppayload">{'Returned: ' + st.output}</div> : null}
                        {st.error ? <div className="lab-bad">{st.error}</div> : null}
                      </li>
                    ))}
                    <li className="lab-step">
                      <span className="lab-stepkind">{'FINAL ANSWER'}</span>
                      <span>{r.answer || '(no answer)'}</span>
                    </li>
                  </ol>
                </details>
              ) : null}

              {r.refusal_expected ? (
                <p className={r.gate_ok ? 'lab-note' : 'lab-bad'}>
                  {r.gate_ok
                    ? 'The instance refused it, as it should.'
                    : 'Outside this instance and ANSWERED — the gate did not hold.'}
                </p>
              ) : null}
            </article>
          ))}
        </div>
      ) : null}

      {batch ? (
        <div className="lab-card">
          <h2>{'Questions'}</h2>
          <p className="lab-note" aria-live="polite">
            {batch.generated + ' of ' + batch.requested
              + (batch.short ? ' — the router could not place the rest inside this instance' : '')
              + (batch.out_of_module ? ' · ' + batch.out_of_module + ' the instance must refuse' : '')
              + ' · batch ' + batch.fingerprint}
          </p>
          <table className="lab-table">
            <thead>
              <tr>{['Id', 'Question', 'Answers from', 'Asked by', 'Written as', 'In module'].map((h) => (
                <th key={h} scope="col">{h}</th>
              ))}</tr>
            </thead>
            <tbody>
              {(batch.scenarios || []).map((r) => (
                <tr key={r.scenario_id}>
                  <td>{r.scenario_id}</td>
                  <td>{r.question}</td>
                  <td>{r.engine}</td>
                  <td>{r.role}</td>
                  <td>{r.style}</td>
                  <td>{r.in_module ? 'yes' : 'must be refused'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </div>
  );
}
