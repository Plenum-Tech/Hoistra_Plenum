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
// against a real model and costs money. What came back is saved as a run, so the batch is read and
// annotated on the Runs page like any other. A batch is asked a few at a time: the whole batch is
// generated from the seed and then sliced, so "the next five" are the next five OF THIS BATCH
// rather than a fresh five that happen to be shorter.
import React from 'react';
import { labApi } from './labApi.js';
import { INSTANCES } from '../logic/instances.js';

const COUNTS = [10, 25, 50, 100];
const ASK = [1, 2, 3, 5];
const REFUSALS = [[0, 'none'], [0.1, '10%'], [0.25, '25%']];

const secs = (ms) => Math.round((Number(ms) || 0) / 1000) + 's';
const usd = (n) => '$' + Number(n || 0).toFixed(4);

export default function Synthetic({ instance, onPick, org }) {
  // The company the questions are asked against. The lab takes it from whichever run is
  // open, which leaves the platform view with none; picked here instead, because asking is
  // not about a run.
  const [companies, setCompanies] = React.useState([]);
  const [company, setCompany] = React.useState('');
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
  // How many of this batch have been asked. Generate starts a new batch, so it resets.
  const [asked, setAsked] = React.useState(0);

  const [batch, setBatch] = React.useState(null);
  const [busy, setBusy] = React.useState('');
  const [error, setError] = React.useState('');
  const [run, setRun] = React.useState(null);

  // A batch on screen belongs to the instance it was generated in. Switching instance elsewhere
  // must not leave Ops Brain questions sitting under a Compliance Engine heading.
  React.useEffect(() => { setBatch(null); setRun(null); setError(''); }, [instance]);

  React.useEffect(() => {
    let gone = false;
    labApi.companies()
      .then((out) => {
        if (gone) return;
        const rows = (out && out.companies) || [];
        setCompanies(rows);
        // One company to choose from is not a choice: pick it.
        if (rows.length === 1) setCompany(rows[0].id);
      })
      .catch(() => { if (!gone) setCompanies([]); });
    return () => { gone = true; };
  }, []);

  React.useEffect(() => {
    let gone = false;
    labApi.spaces()
      .then((out) => { if (!gone) setSpaces((out && out.spaces) || []); })
      .catch(() => { if (!gone) setSpaces([]); });
    return () => { gone = true; };
  }, []);

  // The batch on screen belongs to the settings that made it; changing any of them clears it
  // rather than leaving results above controls that no longer describe them.
  const change = (fn) => (v) => { fn(v); setBatch(null); setRun(null); setError(''); setAsked(0); };

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
    // The earlier answers stay on screen: the next few are added UNDER them, because they are the
    // next few questions of one batch, not a second batch. Clearing here would make each ask look
    // like a separate run, which is also what it would be in the database.
    if (!asked) setRun(null);
    const from = asked;
    try {
      const out = await labApi.runScenarios({
        space, count: take, seed: Number(seed) || 0,
        challenge_ratio: Number(challenge), offset: from, batch: Number(count)
      }, asking);
      setRun((prev) => (from && prev ? {
        ...out,
        results: (prev.results || []).concat(out.results || []),
        asked: (prev.asked || 0) + (out.asked || 0),
        ms: (prev.ms || 0) + (out.ms || 0),
        cost_usd: (prev.cost_usd || 0) + (out.cost_usd || 0),
        // The run code comes from the newest reply; it is the same run either way, and saying so
        // is what tells a reviewer where to go and annotate.
        persisted: !!out.persisted && prev.persisted !== false,
        seq_from: prev.seq_from || out.seq_from,
      } : out));
      // Move on by what was actually asked, not by what was requested: a batch the generator
      // could not fill would otherwise skip questions that were never put to anybody.
      setAsked(from + ((out && out.asked) || 0));
    } catch (e) {
      const problems = (e && e.body && e.body.detail && e.body.detail.problems) || [];
      setError(((e && e.message) || String(e)) + (problems.length ? ' — ' + problems.join('; ') : ''));
    } finally {
      setBusy('');
    }
  }

  // What is left of the batch. Asking past the end would hand back nothing and read as a
  // failure, so the button stops instead, and a short last slice asks only what remains.
  const left = Math.max(0, Number(count) - asked);
  const take = asked ? Math.min(Number(askCount), left) : Number(askCount);

  const chosen = spaces.find((s) => s.key === space);
  // The picked company wins; the open run's is the fallback, for a company admin who
  // has only one and never sees the picker.
  const asking = company || org || '';

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

        {companies.length > 1 ? (
          <div className="lab-row">
            <label className="lab-small" htmlFor="synthetic-company">{'Company'}</label>
            <select id="synthetic-company" className="lab-input" value={company}
              onChange={(e) => setCompany(e.target.value)}>
              <option value="">{'Choose a company…'}</option>
              {companies.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
            </select>
            <span className="lab-note">{'Whose records the questions are asked against. Generating needs none; asking does.'}</span>
          </div>
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
          <span className="lab-small">{asked ? 'Ask the next' : 'Ask the first'}</span>
          <div className="lab-seg" role="radiogroup" aria-label="Ask the first">
          {ASK.map((n) => (
            <button key={n} type="button" className={'lab-seg-btn' + (Number(askCount) === n ? ' lab-on' : '')}
              aria-pressed={Number(askCount) === n ? 'true' : 'false'}
              onClick={() => setAskCount(n)}>{n}</button>
          ))}
          </div>
          <button type="button" className="lab-btn"
            disabled={!!busy || !asking || (asked > 0 && left === 0)} onClick={ask}
            title={asking ? undefined : 'Choose the company these questions are asked against.'}>
            {busy === 'ask' ? 'Asking…'
              : !asked ? 'Ask them for real'
                : left === 0 ? 'All ' + count + ' asked'
                  : 'Ask the next ' + take + ' (' + asked + ' of ' + count + ' done)'}
          </button>
          {/* Said beside the button that needs it, not as a line at the top of the page. The
              service refuses without a company, and "choose a company to view as" reads like a
              display setting rather than a thing that has to be true before asking. */}
          {asking ? null : (
            <span className="lab-note">{'Choose a company above first.'}</span>
          )}
        </div>
        <p className="lab-note">
          {'Each question asked is a real chat turn in this instance: a model is called and it costs money. What came back is saved as a run and can be annotated on the Runs page.'}
        </p>
      </div>

      {error ? <div className="lab-bad" role="alert">{error}</div> : null}

      {run ? (
        <div className="lab-card">
          <h2>{'Answers'}</h2>
          <p className="lab-note" aria-live="polite">
            {run.asked + ' of ' + count + ' asked in ' + (run.space || '—') + ' · ' + secs(run.ms) + ' · ' + usd(run.cost_usd)
              + (run.persisted === false ? ' · not stored: ' + (run.save_error || 'the write failed')
                : run.run_code ? ' · saved as ' + run.run_code : '')}
          </p>
          {/* Said here rather than only in Runs: the point of asking is the annotating, and a
              reviewer who does not know the batch is already filed has nowhere to go. */}
          {run.run_code ? (
            <p className="lab-note">
              {'Open ' + run.run_code + ' on the Runs page to annotate: each question there has its answer, its Orchestrator, model and code steps, a place for notes beside each one and a place to say what the answer should have been. Those notes are what the learning draft is written from.'}
            </p>
          ) : null}
          {(run.results || []).map((r, i) => (
            <article key={r.scenario_id} className="lab-qitem">
              <p className="lab-note">
                {/* The question's code in the run, so what is on screen and what is in Runs are
                    the same thing named the same way. */}
                {(run.run_code ? run.run_code + '-' + ((run.seq_from || 1) + i) + ' · ' : '')
                  + r.scenario_id + ' · ' + (r.engine || '—') + (r.in_module ? '' : ' · must be refused') + (r.ok ? '' : ' · failed')
                  + ((r.needs || []).length ? ' · needs ' + r.needs.join(' + ') : '')}
              </p>
              <p><strong>{r.question}</strong></p>
              {(r.needs || []).length ? (
                <p className="lab-note">{'Must cover: ' + r.covers}</p>
              ) : null}
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
              // The service says WHY it is short. "the router could not place the rest" read as a
              // fault; usually it just means the instance has no more distinct questions, and a
              // batch never asks the same question twice.
              + (batch.short ? ' — ' + (batch.short_reason || 'the router could not place the rest inside this instance') : '')
              + (batch.out_of_module ? ' · ' + batch.out_of_module + ' the instance must refuse' : '')
              + (batch.cross_domain ? ' · ' + batch.cross_domain + ' need more than one area' : '')
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
                  <td>
                    {r.question}
                    {/* A question with three parts is the one an answer fails quietly: it covers
                        the first well, drops the rest, and reads as a good answer. So the parts
                        are on screen beside it, for the person who will judge it. */}
                    {(r.needs || []).length ? (
                      <span className="lab-note lab-cross">
                        {'Needs ' + r.needs.join(' + ') + '. Must cover: ' + r.covers}
                      </span>
                    ) : null}
                  </td>
                  <td>{r.engine}</td>
                  <td>{r.role}</td>
                  <td>{(r.needs || []).length ? 'cross-domain' : r.style}</td>
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
