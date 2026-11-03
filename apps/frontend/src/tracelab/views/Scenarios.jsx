// Scenarios — generate a synthetic batch for one role space, look at what it produced, and take
// it away in the golden-set shape.
//
// The instance is the first control rather than a detail, because it decides which engines may
// answer at all: a question asked in Ops Brain cannot reach compliance. Every generated question
// is put to the REAL router before it is shown, and one that would route outside its own space is
// dropped — so the count on screen can be lower than the count asked for, and it says so when it
// is rather than quietly returning fewer.
import React, { useEffect } from 'react';
import * as sact from '../scenariosActions.js';

const COUNTS = [10, 25, 50, 100];

export default function Scenarios({ s }) {
  const v = s.scenarios;
  useEffect(() => { sact.loadSpaces(); }, []);
  const spaces = v.spaces.rows.length ? v.spaces.rows : [{ key: 'ops', name: 'Ops Brain', engines: [] }];
  const chosen = spaces.find((x) => x.key === v.space);
  const b = v.batch;

  return (
    <section className="tl-card" aria-label="Synthetic scenarios">
      <h3 className="tl-title">{'Synthetic questions'}</h3>
      <p className="tl-meta">
        {'A batch belongs to one instance. Every question is put to the real router before it is listed, and one that would route outside its instance is dropped — a question the instance refuses would measure the gate, not the engines.'}
      </p>

      <div className="tl-form">
        <div className="tl-field" role="group" aria-label="Instance"><span>{'Instance'}</span>
          {spaces.map((sp) => (
            <button key={sp.key} type="button" className={'tl-chip-btn' + (sp.key === v.space ? ' tl-on' : '')}
              aria-pressed={sp.key === v.space ? 'true' : 'false'}
              onClick={() => sact.setForm({ space: sp.key })}>{sp.name}</button>
          ))}
        </div>
        {chosen && chosen.engines.length ? (
          <p className="tl-meta">{'Answers can come from: ' + chosen.engines.join(', ') + '.'}</p>
        ) : null}

        <div className="tl-field" role="group" aria-label="How many"><span>{'How many'}</span>
          {COUNTS.map((n) => (
            <button key={n} type="button" className={'tl-chip-btn' + (Number(v.count) === n ? ' tl-on' : '')}
              aria-pressed={Number(v.count) === n ? 'true' : 'false'}
              onClick={() => sact.setForm({ count: n })}>{n}</button>
          ))}
        </div>

        <label className="tl-field"><span>{'Seed'}</span>
          <input type="number" min="0" value={v.seed}
            onChange={(e) => sact.setForm({ seed: e.target.value })} />
        </label>
        <p className="tl-meta">{'The same seed gives the same batch, so a failure can be re-run exactly.'}</p>

        <div className="tl-field" role="group" aria-label="Questions the instance must refuse">
          <span>{'Include refusals'}</span>
          {[0, 0.1, 0.25].map((r) => (
            <button key={r} type="button" className={'tl-chip-btn' + (Number(v.challenge) === r ? ' tl-on' : '')}
              aria-pressed={Number(v.challenge) === r ? 'true' : 'false'}
              onClick={() => sact.setForm({ challenge: r })}>{r === 0 ? 'none' : Math.round(r * 100) + '%'}</button>
          ))}
        </div>
        <p className="tl-meta">{'Questions this instance must refuse. A gate that stops working is silent otherwise.'}</p>

        <div className="tl-actions">
          <button type="button" className="tl-btn tl-primary" disabled={v.generating} onClick={sact.generate}>
            {v.generating ? 'Generating…' : 'Generate'}
          </button>
          {b ? (
            <button type="button" className="tl-btn" disabled={v.downloading} onClick={sact.download}>
              {v.downloading ? 'Preparing…' : 'Download as a golden set'}
            </button>
          ) : null}
        </div>

        {/* Asking them for real. Separate from Generate because this one calls models and costs
            money, and because it answers a few rather than the whole batch. */}
        <div className="tl-field" role="group" aria-label="Ask for real"><span>{'Ask the first'}</span>
          {[1, 2, 3, 5].map((n) => (
            <button key={n} type="button" className={'tl-chip-btn' + (Number(v.runCount) === n ? ' tl-on' : '')}
              aria-pressed={Number(v.runCount) === n ? 'true' : 'false'}
              onClick={() => sact.setForm({ runCount: n })}>{n}</button>
          ))}
          <button type="button" className="tl-btn tl-primary" disabled={v.running} onClick={sact.runQuestions}>
            {v.running ? 'Asking…' : 'Ask them for real'}
          </button>
        </div>
        <p className="tl-meta">{'Each question is a real chat turn in this instance: a model is called and it costs money. Answers are shown here and not stored — keeping them needs a scenario table, which is a migration a person applies.'}</p>
      </div>

      {v.error ? <div className="tl-bad" role="alert">{"The batch could not be generated: " + v.error}</div> : null}
      {v.downloaded ? (
        <div className="tl-meta">
          {'Downloaded ' + v.downloaded + '. The expected-answer column is empty on purpose: a known right answer has to come from a real row and be read by a person before it can grade anything.'}
        </div>
      ) : null}

      {v.runError ? <div className="tl-bad" role="alert">{'The questions could not be asked: ' + v.runError}</div> : null}

      {v.run ? (
        <section className="tl-card" aria-label="Answers">
          <h3 className="tl-title">{'Answers'}</h3>
          <div className="tl-meta" aria-live="polite">
            {v.run.asked + ' asked in ' + (v.run.space || '—')
              + ' · ' + Math.round((v.run.ms || 0) / 1000) + 's'
              + ' · $' + Number(v.run.cost_usd || 0).toFixed(4)
              + (v.run.persisted ? '' : ' · not stored')}
          </div>
          {(v.run.results || []).map((r) => (
            <article key={r.scenario_id} className="tl-card" aria-label={r.scenario_id}>
              <div className="tl-meta">
                {r.scenario_id + ' · ' + (r.engine || '—')
                  + (r.in_module ? '' : ' · must be refused')
                  + (r.ok ? '' : ' · failed')}
              </div>
              <p><strong>{r.question}</strong></p>
              {r.error ? <div className="tl-bad">{r.error}</div> : <p>{r.answer || '(no answer)'}</p>}
              <div className="tl-meta">
                {'Tools: ' + ((r.tools || []).length ? r.tools.join(' → ') : 'none')}
                {' · Models: ' + ((r.models || []).length ? r.models.join(', ') : '—')}
                {' · ' + (r.spans || 0) + ' spans · ' + Math.round((r.ms || 0) / 1000) + 's · $'
                  + Number(r.cost_usd || 0).toFixed(4)}
              </div>
              {r.refusal_expected ? (
                <div className={r.gate_ok ? 'tl-meta' : 'tl-bad'}>
                  {r.gate_ok
                    ? 'The instance refused it, as it should.'
                    : 'This question is outside the instance and was ANSWERED — the gate did not hold.'}
                </div>
              ) : null}
              <div className="tl-meta">{'Asked ' + (r.asked_at || '')}</div>
            </article>
          ))}
        </section>
      ) : null}

      {b ? (
        <>
          <div className="tl-meta" aria-live="polite">
            {b.generated + ' of ' + b.requested + ' questions'}
            {b.short ? ' — the router could not place the rest inside this instance' : ''}
            {b.out_of_module ? ' · ' + b.out_of_module + ' the instance must refuse' : ''}
            {' · batch ' + b.fingerprint}
          </div>
          <table className="tl-table">
            <thead><tr>{['Id', 'Question', 'Answers from', 'Asked by', 'Written as', 'In module'].map((h) => (
              <th key={h} scope="col">{h}</th>
            ))}</tr></thead>
            <tbody>
              {(b.scenarios || []).map((r) => (
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
        </>
      ) : null}
    </section>
  );
}
