// Learning — what the annotations add up to, and where it goes.
//
// Reviewers leave three things on a run: a note on a step saying what went wrong in their own
// words, an expected answer saying what right would have looked like, and — for a synthetic
// question generated outside its instance — a machine verdict when the instance answered it
// anyway. All three sat in Trace Lab and reached nothing.
//
// This page drafts them into markdown, grouped by the skill document each note is probably
// about, because one document is the unit a skill document is edited in and the unit the Skill
// lab takes a proposal in. The grouping is a SUGGESTION and says so on every heading: filing a
// lesson against the wrong document teaches the wrong agent, and only a person who knows what
// the document already says can decide.
//
// Nothing here writes a skill document. The documents the agents read live in the application's
// database, which this service may never write; the hand-off is a person carrying the draft to
// Administration → Skill lab, proposing it there, and approving it. From then on the agents read
// it without a deploy. That is also the right shape on its own merits — an annotation is one
// reviewer's opinion, and a loop that taught the agents straight from opinions would drift with
// whoever reviewed last.
import React from 'react';
import { labApi } from './labApi.js';
import './runs.css';

const copy = (text) => {
  try {
    if (navigator && navigator.clipboard && navigator.clipboard.writeText) return navigator.clipboard.writeText(text);
  } catch (e) { /* a page with no clipboard still shows the text to select */ }
  return Promise.reject(new Error('no clipboard'));
};

// Saved from the markdown already on the page. Asking the service for the file again would need
// the lab's bearer on a plain link, which an <a href> cannot carry.
function download(markdown, runCode) {
  const name = 'what-the-reviews-say' + (runCode ? '-' + runCode : '') + '.md';
  const url = URL.createObjectURL(new Blob([markdown], { type: 'text/markdown' }));
  const a = document.createElement('a');
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

function Copy({ text, label }) {
  const [said, setSaid] = React.useState('');
  return (
    <button type="button" className="lab-btn-quiet" onClick={() => {
      copy(text).then(() => setSaid('Copied'), () => setSaid('Select it and copy'));
      setTimeout(() => setSaid(''), 2000);
    }}>{said || label || 'Copy'}</button>
  );
}

export default function Learning() {
  const [run, setRun] = React.useState('');
  const [asked, setAsked] = React.useState('');
  const [out, setOut] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');

  const load = React.useCallback((code) => {
    setBusy(true);
    setError('');
    labApi.learningDraft(code || undefined)
      .then((res) => { setOut(res); setAsked(code || ''); })
      .catch((e) => { setError((e && e.message) || String(e)); setOut(null); })
      .finally(() => setBusy(false));
  }, []);

  React.useEffect(() => { load(''); }, [load]);

  const counts = (out && out.counts) || { notes: 0, expected: 0, gates: 0 };
  const groups = (out && out.by_skill) || [];
  const nothing = out && !counts.notes && !counts.expected && !counts.gates;

  return (
    <div className="lab-page-body lab-page-full">
      <div className="lab-page-head">
        <h1>{'What the reviews say'}</h1>
        <p className="lab-small lab-big">
          {'Every note, expected answer and gate verdict reviewers have left, drafted into markdown with its evidence. Assembled from the rows, not written by a model: every line traces back to somebody who typed it.'}
        </p>
      </div>

      <div className="lab-card">
        <div className="lab-row">
          <label className="lab-small" htmlFor="learning-run">{'Run'}</label>
          <input id="learning-run" className="lab-input" value={run} placeholder="all runs, or R32"
            onChange={(e) => setRun(e.target.value)} />
          <button type="button" className="lab-btn" disabled={busy} onClick={() => load(run.trim())}>
            {busy ? 'Reading…' : 'Draft it'}
          </button>
          {asked ? <span className="lab-note">{'Showing ' + asked}</span>
            : <span className="lab-note">{'Showing every run this company has annotated.'}</span>}
        </div>
        {out ? (
          <p className="lab-note" aria-live="polite">
            {counts.notes + ' step notes · ' + counts.expected + ' expected answers · '
              + (out.gates_available
                ? counts.gates + ' questions answered in an instance that should have refused them'
                : 'gate verdicts cannot be looked for yet (the scenario tables are not applied)')}
          </p>
        ) : null}
      </div>

      {error ? <div className="lab-bad" role="alert">{error}</div> : null}

      {nothing ? (
        <div className="lab-card">
          <p>{'Nothing to draft yet. Open a run, read a question with its steps, and leave a note on the step that went wrong or say what the answer should have been. This page is built from those.'}</p>
        </div>
      ) : null}

      {groups.length ? (
        <div className="lab-card">
          <h2>{'By skill document'}</h2>
          <p className="lab-note">
            {'Which document each note is probably about, worked out from the step it was left on. A suggestion: the reviewer picks the document, because only somebody who knows what it already says can tell.'}
          </p>
          {groups.map((g) => (
            <article key={g.skill + '/' + g.doc} className="lab-qitem">
              <p className="lab-note">
                {g.skill + '/' + g.doc + ' · ' + g.notes + (g.notes === 1 ? ' note' : ' notes')
                  + (g.suggested ? ' · suggested' : '')}
              </p>
              <pre className="lab-pre">{g.markdown}</pre>
              <div className="lab-row">
                <Copy text={g.markdown} label={'Copy ' + g.skill + '/' + g.doc} />
              </div>
            </article>
          ))}
        </div>
      ) : null}

      {out && out.markdown ? (
        <div className="lab-card">
          <h2>{'The whole draft'}</h2>
          <pre className="lab-pre">{out.markdown}</pre>
          <div className="lab-row">
            <Copy text={out.markdown} label="Copy the draft" />
            <button type="button" className="lab-btn-quiet" onClick={() => download(out.markdown, asked)}>
              {'Download .md'}
            </button>
          </div>
        </div>
      ) : null}

      <div className="lab-card">
        <h2>{'What happens to it'}</h2>
        {/* Said plainly because the reply says applies_anything: false and a page that stays
            quiet about it reads as if annotating were enough on its own. */}
        <p>{'This page applies nothing. The documents the agents read live in the application\'s database, which Trace Lab may never write.'}</p>
        <ol className="lab-ol">
          <li>{'Decide what the agents should actually be taught. The draft is evidence, not an instruction — it is what people said, not what a model should be told.'}</li>
          <li>{'Open Administration → Skill lab in Hoistra and propose the rewrite against that skill document.'}</li>
          <li>{'Approve it. From then on the agents read the approved text instead of the shipped file, with no deploy, and the repo copy can be brought up to date from it later.'}</li>
        </ol>
      </div>
    </div>
  );
}
