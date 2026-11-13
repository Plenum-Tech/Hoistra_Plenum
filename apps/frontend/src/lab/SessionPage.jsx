// SessionPage — one session of the eval store (11 Nov 2026, task plan B1.1 and B1.2), in Langfuse's words.
//
// A session is one conversation: a scenario asked once. The page shows what it is (its run tag, its scenario and the
// version it was asked with, where it ran), the scenario's turns with the expected outcome each was written with, and
// then a tab per turn. Each tab is one trace: the question, the answer, how it ended, its scores, and the whole tree of
// spans and generations, drawn by the same view the question page uses (TraceTab.jsx), so the two never differ.
//
// The tree of a turn is read only when its tab is opened: a tree can be megabytes and most sessions are opened for
// one turn.
//
// Annotating happens here (11 Nov 2026, when Runs was deleted): on the turn in view a person writes notes on a span's
// input or output and a comment on the turn as a whole (TraceNotes.jsx), and marks each failure mode present or absent
// in that turn. A turn's notes and labels are read when its tab is opened, like its tree.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { TraceView } from './TraceTab.jsx';
import { FAILURE_TEXT, FORMAT_TEXT, LEVELS, LEVEL_TEXT, TRACE_STATUS_TEXT, TYPE_TEXT, canScoreDomain, modeRows, needsRead, planLines,
  scoreLines, sessionTitle, turnTabs, verdictText, failureReasons } from './logic/sessions.js';
import { fmtWhen } from './logic/format.js';
import { NextStep } from './NextStep.jsx';
import { useTraceNotes } from './TraceNotes.jsx';
import './runs.css';
import './sessions.css';

function Expected({ expected }) {
  const e = expected || {};
  const plan = planLines(e);
  const fh = e.failure_handling || null;
  const must = Array.isArray(e.must_include) ? e.must_include : [];
  const never = Array.isArray(e.must_not_include) ? e.must_not_include : [];
  return (
    <div className="lab-ses-expected">
      <span className="lab-qp-label">{'Expected outcome'}</span>
      <dl className="lab-ses-dl">
        <dt>{'Plan'}</dt>
        <dd>{plan.length ? <ol className="lab-ses-plan">{plan.map((line) => <li key={line}><code className="lab-mono">{line}</code></li>)}</ol>
          : 'No calls expected.'}</dd>
        {SHOW_FAILURE_ROWS ? <><dt>{'Answer format'}</dt>
          <dd>{FORMAT_TEXT[e.answer_format] || '—'}</dd></> : null}
        {SHOW_FAILURE_ROWS && fh ? <><dt>{'Failure handling'}</dt>
          <dd>{(FAILURE_TEXT[fh.state] || fh.state) + (fh.failed && fh.failed.length ? ' · the answer must say that ' + fh.failed.join(', ') + ' failed' : '')}</dd></> : null}
        {must.length ? <><dt>{'Must include'}</dt><dd>{must.join(' · ')}</dd></> : null}
        {never.length ? <><dt>{'Must not include'}</dt><dd>{never.join(' · ')}</dd></> : null}
        {e.answer ? <><dt>{'Expected answer'}</dt><dd className="lab-ses-pre">{e.answer}</dd></> : null}
      </dl>
    </div>
  );
}

// The specification sections a scenario names (e.g. missing_evidence) are hidden on the session page (13 Nov 2026,
// Hussain): most have no rule written behind them yet. Set to true to show them again.
const SHOW_SECTION_TAGS = false;

// Failure wording only when Hoistra failed (13 Nov 2026, Hussain). Hidden: the expected outcome's "Answer format" and
// "Failure handling" rows, and the scenario type "Failure" (it reads like a verdict). A turn that failed shows why it
// failed instead (WhyFailed). Set to true to show them again.
const SHOW_FAILURE_ROWS = false;
const typeText = (type) => (!SHOW_FAILURE_ROWS && type === 'failure' ? null : TYPE_TEXT[type] || type || null);

// Why a turn failed: how it ended and each failed step with its reason. Only the reason, never an expected answer.
function WhyFailed({ trace, nodes }) {
  const reasons = failureReasons(trace, nodes);
  if (!reasons.length) return null;
  return (
    <section className="lab-ses-why" aria-label="Why it failed">
      <span className="lab-qp-label">{'Why it failed'}</span>
      <ul className="lab-ses-lines">
        {reasons.map((r, i) => <li key={i} className="lab-ses-line">{r.step ? <><strong>{r.step}</strong>{': ' + r.reason}</> : r.reason}</li>)}
      </ul>
    </section>
  );
}

function Scenario({ scenario }) {
  if (!scenario) {
    return (
      <section className="lab-qp-card" aria-label="Scenario">
        <p className="lab-small">{'This session is not linked to a scenario, so there is no expected outcome to check it against.'}</p>
      </section>
    );
  }
  const turns = Array.isArray(scenario.turns) ? scenario.turns : [];
  const stale = Array.isArray(scenario.stale) ? scenario.stale : [];
  return (
    <section className="lab-qp-card" aria-label="Scenario">
      <div className="lab-qp-meta">{['Scenario', scenario.scenario_id, 'version ' + scenario.version, typeText(scenario.type)].filter(Boolean).join(' · ')}</div>
      <div className="lab-chips">
        {(SHOW_SECTION_TAGS ? scenario.spec_sections || [] : []).map((s) => <span key={s} className="lab-chip" title="Section of the specification this scenario tests">{s}</span>)}
        {scenario.status === 'draft' ? <span className="lab-chip lab-ses-warn">{'Expected outcome not approved yet'}</span> : null}
        {stale.length ? <span className="lab-chip lab-ses-warn" title={'Changed since this version was written: ' + stale.join(', ')}>{'Expected outcome is out of date'}</span> : null}
      </div>
      {turns.map((t, i) => (
        <div key={i} className="lab-ses-turn">
          <p className="lab-qp-said"><strong>{'Turn ' + (i + 1) + ': '}</strong>{t.message}</p>
          <Expected expected={t.expected} />
        </div>
      ))}
    </section>
  );
}

// A person's score at one level: pass or fail, a score from 0 to 1 and a comment. Saving adds a score; it never
// changes one, and it becomes the trace's current score at that level.
function ScoreForm({ level, traceId, busy, onSave }) {
  const [verdict, setVerdict] = React.useState('');
  const [value, setValue] = React.useState('');
  const [comment, setComment] = React.useState('');
  const number = value === '' ? null : Number(value);
  const bad = number != null && (Number.isNaN(number) || number < 0 || number > 1);
  const name = 'lab-ses-verdict-' + level + '-' + traceId;
  const submit = (e) => {
    e.preventDefault();
    if (!verdict || bad || busy) return;
    onSave(level, { verdict, value: number, comment: comment.trim() || null });
  };
  return (
    <form className="lab-ses-form" onSubmit={submit}>
      <fieldset className="lab-ses-fieldset" disabled={!!busy}>
        <legend className="lab-qp-label">{'Your score'}</legend>
        <div className="lab-row" role="radiogroup" aria-label={LEVEL_TEXT[level] + ' verdict'}>
          {[['pass', 'Pass'], ['fail', 'Fail']].map(([v, text]) => (
            <label key={v} className="lab-ses-check">
              <input type="radio" name={name} value={v} checked={verdict === v} onChange={() => setVerdict(v)} /><span>{text}</span>
            </label>
          ))}
        </div>
        <label className="lab-field lab-ses-num"><span>{'Score from 0 to 1'}</span>
          <input type="number" min="0" max="1" step="0.05" inputMode="decimal" value={value} onChange={(e) => setValue(e.target.value)} />
        </label>
        {bad ? <div role="alert" className="lab-error">{'A score is a number from 0 to 1.'}</div> : null}
        <label className="lab-field"><span>{'Comment'}</span>
          <textarea className="lab-note" rows={2} maxLength={4000} value={comment} onChange={(e) => setComment(e.target.value)} />
        </label>
        <div className="lab-row">
          <button type="submit" className="lab-btn lab-btn-sm" disabled={!verdict || bad || !!busy}>{busy ? 'Saving…' : 'Save score'}</button>
        </div>
      </fieldset>
    </form>
  );
}

// The scores of one trace: each level with its current score, why it is what it is, its other scores, and a
// form to score it. `saving` is the level being saved; `error` what went wrong saving.
export function ScoresPanel({ trace, me, onSave, saving, error }) {
  const t = trace || {};
  const scores = t.scores || {};
  const past = Array.isArray(t.score_history) ? t.score_history : [];
  const domainOpen = canScoreDomain(me);
  return (
    <section className="lab-ses-scorepanel" aria-label="Scores">
      <span className="lab-qp-label">{'Scores'}</span>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      <div className="lab-ses-levels">
        {LEVELS.map((level) => {
          const sc = scores[level] || null;
          const verdict = sc ? sc.verdict : null;
          const who = sc ? (sc.source === 'ANNOTATION' ? 'Scored by ' + (sc.author_email || sc.author || 'a person') : 'Scored automatically') : 'Not scored yet';
          const lines = scoreLines(level, sc);
          // Every other score of this level: the current one is left out by its id (a person's score is current even
          // when an automatic one is newer, so it is not always the first).
          const earlier = past.filter((h) => h.name === level && !(sc && h.id === sc.id));
          return (
            <div key={level} className="lab-ses-level">
              <div className="lab-row">
                <strong>{LEVEL_TEXT[level]}</strong>
                <span className={'lab-chip' + (verdict === 'pass' ? ' lab-chip-ok' : verdict === 'fail' ? ' lab-ses-bad' : '')}>{verdictText(verdict)}</span>
                {sc && sc.value != null ? <span className="lab-small">{'score ' + Number(sc.value).toFixed(2)}</span> : null}
                <span className="lab-small">{who}</span>
              </div>
              {sc && sc.comment ? <p className="lab-qp-said lab-ses-comment">{sc.comment}</p> : null}
              {lines.length ? (
                <ul className="lab-ses-lines">
                  {lines.map((l, i) => <li key={i} className={'lab-ses-line lab-ses-line-' + l.tone}>{l.text}</li>)}
                </ul>
              ) : null}
              {earlier.length ? (
                <details className="lab-ses-earlier">
                  <summary>{'Other scores (' + earlier.length + ')'}</summary>
                  <ul className="lab-ses-lines">
                    {earlier.map((h, i) => (
                      <li key={i} className="lab-ses-line lab-ses-line-quiet">
                        {[verdictText(h.verdict), h.source === 'ANNOTATION' ? (h.author_email || 'a person') : (h.evaluator || 'automatic'),
                          fmtWhen(h.created_at), h.comment].filter(Boolean).join(' · ')}
                      </li>
                    ))}
                  </ul>
                </details>
              ) : null}
              {level === 'domain' && !domainOpen
                ? <p className="lab-small">{'Only a domain expert, the platform team or the consultant scores the domain level.'}</p>
                : <ScoreForm key={t.id + level} level={level} traceId={t.id} busy={saving === level} onSave={onSave || (() => {})} />}
            </div>
          );
        })}
      </div>
    </section>
  );
}

const VALUE_TEXT = Object.freeze({ positive: 'present', negative: 'absent' });

// Whether each failure mode is in this turn: the reader's own value (Present / Absent; pressing the pressed one again
// clears it), what everyone else said, and how many said each. `data` is GET /eval/traces/{id}/labels (null while
// reading); `busy` the mode being saved; `onSet(mode, value)`.
export function FailureModes({ data, busy, error, onSet }) {
  const rows = modeRows(data);
  const set = onSet || (() => {});
  return (
    <section className="lab-ses-modes" aria-label="Failure modes in this turn">
      <span className="lab-qp-label">{'Failure modes'}</span>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {data == null ? <p className="lab-small" role="status">{'Reading the failure modes…'}</p>
        : !rows.length ? <p className="lab-small">{'No failure modes yet. The platform team or the consultant adds them on the Labels page.'}</p>
          : (
            <ul className="lab-ses-lines">
              {rows.map((r) => (
                <li key={r.lineage} className="lab-ses-mode">
                  <div className="lab-row">
                    <strong title={r.definition || undefined}>{r.name}</strong>
                    <div className="lab-row" role="group" aria-label={'Is “' + r.name + '” in this turn?'}>
                      {[['positive', 'Present'], ['negative', 'Absent']].map(([value, text]) => (
                        <button key={value} type="button" className={'lab-btn lab-btn-sm' + (r.mine === value ? '' : ' lab-btn-quiet')}
                          aria-pressed={r.mine === value ? 'true' : 'false'} disabled={busy === r.lineage}
                          onClick={() => set(r.lineage, r.mine === value ? null : value)}>{text}</button>
                      ))}
                    </div>
                    {r.pos || r.neg ? <span className="lab-small">{r.pos + ' present · ' + r.neg + ' absent'}</span> : null}
                  </div>
                  {r.definition ? <div className="lab-small">{r.definition}</div> : null}
                  {r.others.length ? <div className="lab-small">{r.others.map((o) => o.who + ': ' + VALUE_TEXT[o.value]).join(' · ')}</div> : null}
                </li>
              ))}
            </ul>
          )}
    </section>
  );
}

// Is this turn's expected outcome wrong? Expected outcomes are checked while reviewing (12 Nov 2026): a reviewer who
// thinks the expected outcome itself is wrong flags it, with an optional reason, and the turn is not counted against
// Hoistra until the scenario is corrected. `data` is GET /eval/traces/{id}/expected-flag (null while reading);
// `onSet(flagged, comment)`.
export function ExpectedFlag({ data, busy, error, onSet }) {
  const [open, setOpen] = React.useState(false);
  const [text, setText] = React.useState('');
  const d = data || null;
  const set = onSet || (() => {});
  const flags = d && Array.isArray(d.flags) ? d.flags : [];
  return (
    <section className="lab-ses-flag" aria-label="Expected outcome check">
      <span className="lab-qp-label">{'Is the expected outcome for this turn wrong?'}</span>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {d == null ? <p className="lab-small" role="status">{'Reading the expected outcome flags…'}</p> : null}
      {flags.length ? (
        <ul className="lab-ses-lines">
          {flags.map((f, i) => (
            <li key={i} className="lab-ses-line lab-ses-line-bad">
              {(f.mine ? 'You flagged the expected outcome as wrong' : 'Flagged as wrong by ' + (f.author_email || 'someone'))
                + (f.comment ? ': ' + f.comment : '.')}
            </li>
          ))}
        </ul>
      ) : null}
      {flags.length ? <p className="lab-small">{'While it is flagged, this turn is not counted against Hoistra: it is not scored and compare leaves it out until the scenario is corrected.'}</p> : null}
      {d && d.mine ? (
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => set(false, null)}>{'Clear my flag'}</button>
      ) : d && open ? (
        <div className="lab-row">
          <textarea className="lab-note" rows={2} maxLength={2000} value={text} disabled={!!busy} aria-label="What is wrong with the expected outcome"
            placeholder="What is wrong with the expected outcome (optional)" onChange={(e) => setText(e.target.value)} />
          <button type="button" className="lab-btn lab-btn-sm" disabled={!!busy} onClick={() => { set(true, text.trim() || null); setOpen(false); setText(''); }}>{busy ? 'Saving…' : 'Flag it'}</button>
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => { setOpen(false); setText(''); }}>{'Cancel'}</button>
        </div>
      ) : d ? (
        <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={!!busy} onClick={() => setOpen(true)}>{'Expected outcome is wrong'}</button>
      ) : null}
    </section>
  );
}

// One turn: its trace header, the comment box for the turn, its failure modes, its input and output, and its tree with
// notes on every span. `notes` is GET /eval/traces/{id}/notes ({record, labels, organization_id}) or null while reading;
// `labels` GET /eval/traces/{id}/labels; `onLabel(mode, value)` sets the reader's label; `onNoted(step)` offers the next
// step after a note is saved.
function Trace({ trace, tree, me, notes, labels, labelBusy, labelError, onLabel, onNoted, onScore, saving, scoreError, flag,
  flagBusy, flagError, onFlag }) {
  const t = tree || null;
  const n = notes || null;
  const nodes = t && t.tree && Array.isArray(t.tree.nodes) ? t.tree.nodes : [];
  const meId = (n && n.me) || (me && me.user_id) || null;
  const ready = !!(n && !n.loading && !n.error);
  const noted = useTraceNotes({ turnId: trace.id, org: n && n.organization_id ? n.organization_id : undefined,
    record: n ? n.record || null : null, nodes, me: meId, labels: ready ? n.labels : null, onSaved: onNoted });
  return (
    <div className="lab-ses-trace">
      <div className="lab-row">
        <span className="lab-qp-label">{'Trace'}</span>
        <code className="lab-mono">{trace.id}</code>
        <span className={'lab-chip' + (trace.status === 'ok' ? ' lab-chip-ok' : ' lab-ses-bad')}>{TRACE_STATUS_TEXT[trace.status] || trace.status}</span>
      </div>
      {/* Hidden for now (Hussain, 11 Nov 2026): the three level cards and their score forms. ScoresPanel is kept as it is;
          put this line back to show it again.
      <ScoresPanel trace={trace} me={me} saving={saving} error={scoreError} onSave={(level, body) => onScore && onScore(trace.id, level, body)} />
      */}
      <div className="lab-ses-io">
        <div><span className="lab-qp-label">{'Input'}</span><p className="lab-qp-said">{trace.input || '—'}</p></div>
        <div><span className="lab-qp-label">{'Output'}</span><p className="lab-qp-said">{trace.output || '—'}</p></div>
      </div>
      <WhyFailed trace={trace} nodes={nodes} />
      <ExpectedFlag data={flag && !flag.loading && !flag.error ? flag : null} busy={flagBusy}
        error={(flag && flag.error) || flagError || ''} onSet={onFlag} />
      {/* Hidden for now (Hussain, 13 Nov 2026): the failure modes with their Present / Absent buttons. FailureModes is
          kept as it is; put these two lines back to show it again.
      <FailureModes data={labels && !labels.loading && !labels.error ? labels : null} busy={labelBusy}
        error={(labels && labels.error) || labelError || ''} onSet={onLabel} />
      */}
      {!n || n.loading ? <p className="lab-small" role="status">{'Reading the notes…'}</p> : null}
      {n && n.error ? <div role="alert" className="lab-error">{'Could not read the notes: ' + n.error}</div> : null}
      {ready && n.record ? noted.comment : null}
      {ready && n.record == null ? <p className="lab-small">{'The chat no longer has this turn, so notes cannot be added here.'}</p> : null}
      {t && t.tree && ready && n.record ? <p className="lab-small lab-tt-hint">{'Pick a span, then press Add note under its input or output, select text in it to quote it, or double-click it.'}</p> : null}
      {!t || t.loading ? <p className="lab-small" role="status">{'Reading the trace…'}</p> : null}
      {t && t.error ? <div role="alert" className="lab-error">{t.error}</div> : null}
      {t && t.tree ? <TraceView key={trace.id} tree={t.tree} annotate={ready && n.record ? noted.annotate : undefined} /> : null}
    </div>
  );
}

// The page's body from what the service sent. `active` is the trace in view; `trees`, `notes` and `labels` hold each
// opened trace's tree, notes and failure-mode labels ({...} | {error} | {loading}) by trace id.
export function SessionBody({ data, active, trees, notes, labels, flags, flagState, onFlag, onTab, me, label, onLabel, onNoted, onScore,
  saving, scoreError }) {
  const d = data || {};
  const s = d.session || {};
  const tabs = turnTabs(d.traces);
  const traces = Array.isArray(d.traces) ? d.traces : [];
  const current = traces.find((t) => t.id === active) || null;
  const pick = onTab || (() => {});
  return (
    <div className="lab-ses">
      <header className="lab-page-head">
        <div className="lab-qp-meta">{'Session'}</div>
        <h1>{sessionTitle(s)}</h1>
        <div className="lab-chips">
          <code className="lab-mono" title="Session id">{s.id}</code>
          {s.run_tag ? <span className="lab-chip lab-chip-synth" title="Run tag">{s.run_tag}</span> : null}
          {s.scenario_id ? <span className="lab-chip" title="Scenario and the version it was asked with">{s.scenario_id + ' · version ' + s.scenario_version}</span> : null}
          {typeText(s.type) ? <span className="lab-chip">{typeText(s.type)}</span> : null}
          {s.role ? <span className="lab-chip" title="Role the scenario is asked as">{s.role}</span> : null}
          {s.origin ? <span className="lab-chip" title="Where it was asked">{s.origin}</span> : null}
          {Number(s.attempt) > 1 ? <span className="lab-chip">{'attempt ' + s.attempt}</span> : null}
        </div>
      </header>
      <Scenario scenario={d.scenario} />
      {tabs.length ? (
        <>
          <div className="lab-ses-tabs" role="tablist" aria-label="Turns of this session">
            {tabs.map((t) => (
              <button key={t.key} type="button" role="tab" aria-selected={t.traceId === active ? 'true' : 'false'}
                aria-controls="lab-ses-panel" id={'lab-ses-' + t.key} title={TRACE_STATUS_TEXT[t.status] || t.status}
                className={'lab-turn' + (t.traceId === active ? ' lab-on' : '') + (t.status !== 'ok' ? ' lab-ses-tab-bad' : '')}
                onClick={() => pick(t.traceId)}>{t.label}{t.flagged ? <span className="lab-ses-flagmark" title="Expected outcome flagged as wrong" aria-label="expected outcome flagged">{' ⚑'}</span> : null}</button>
            ))}
          </div>
          <div role="tabpanel" id="lab-ses-panel" aria-label={current ? 'Turn ' + current.turn_index : 'Turn'}>
            {/* Keyed by the trace: each turn has its own notes state, so a save that finishes after the tab changed
                lands on its own turn, and an editor or a half-written comment never carries over. */}
            {current ? <Trace key={current.id} trace={current} tree={(trees || {})[current.id]} me={me} notes={(notes || {})[current.id] || null}
              labels={(labels || {})[current.id] || null} labelBusy={label && label.traceId === current.id ? label.busy : null}
              labelError={label && label.traceId === current.id ? label.error : ''}
              onLabel={(mode, value) => onLabel && onLabel(current.id, mode, value)} onNoted={onNoted}
              onScore={onScore} saving={saving} scoreError={scoreError} flag={(flags || {})[current.id] || null}
              flagBusy={flagState && flagState.traceId === current.id ? flagState.busy : false}
              flagError={flagState && flagState.traceId === current.id ? flagState.error : ''}
              onFlag={(flagged, comment) => onFlag && onFlag(current.id, flagged, comment)} />
              : <p className="lab-small">{'Choose a turn.'}</p>}
          </div>
        </>
      ) : <div className="lab-empty"><p className="lab-small">{'This session has no traces.'}</p></div>}
    </div>
  );
}

export default function SessionPage({ sessionId, me, onBack, backText, onOpenLoop }) {
  const [data, setData] = React.useState(null);
  const [error, setError] = React.useState('');
  const [active, setActive] = React.useState(null);
  const [trees, setTrees] = React.useState({});
  const [notes, setNotes] = React.useState({});
  const [labels, setLabels] = React.useState({});
  const [flags, setFlags] = React.useState({});
  const [flagState, setFlagState] = React.useState({ traceId: null, busy: false, error: '' });
  // The label being saved and the last error, on the turn they belong to.
  const [label, setLabel] = React.useState({ traceId: null, busy: null, error: '' });
  const labelAsk = React.useRef(0);
  const [score, setScore] = React.useState({ saving: null, error: '' });
  // After a note is saved: a pop-up says the next step is the agent's proposal on the Loop page (6 Nov 2026).
  const [next, setNext] = React.useState(null);
  const ask = React.useRef(0);

  React.useEffect(() => {
    const mine = (ask.current += 1);
    setData(null); setError(''); setActive(null); setTrees({}); setNotes({}); setLabels({}); setFlags({}); setLabel({ traceId: null, busy: null, error: '' });
    labApi.evalSession(sessionId)
      .then((out) => {
        if (mine !== ask.current) return;
        setData(out);
        const tabs = turnTabs(out && out.traces);
        setActive(tabs.length ? tabs[0].traceId : null);
      })
      .catch((e) => { if (mine === ask.current) setError(errText(e)); });
  }, [sessionId]);

  // A turn's tree and labels are read when its tab is first opened, its notes every time it is opened (other people and
  // other pages change them); a read that failed is tried again. While notes are read again the ones shown stay.
  const readInto = (entry, setStore, read, key) => {
    const id = active;
    const mine = ask.current;
    setStore((old) => ({ ...old, [id]: old[id] && !old[id].error ? { ...old[id], refreshing: true } : { loading: true } }));
    read(id)
      .then((out) => { if (mine === ask.current) setStore((old) => ({ ...old, [id]: key ? { [key]: out && out[key] } : (out || {}) })); })
      .catch((e) => { if (mine === ask.current) setStore((old) => ({ ...old, [id]: { error: errText(e) } })); });
  };
  React.useEffect(() => {
    if (!active) return;
    const id = active;
    if (needsRead(trees[id])) readInto(trees[id], setTrees, labApi.evalTraceTree, 'tree');
    if (needsRead(notes[id], true)) readInto(notes[id], setNotes, labApi.evalTraceNotes, null);
    if (needsRead(labels[id])) readInto(labels[id], setLabels, labApi.evalTraceLabels, null);
    if (needsRead(flags[id], true)) readInto(flags[id], setFlags, labApi.evalTraceExpectedFlag, null);
  }, [active]); // eslint-disable-line react-hooks/exhaustive-deps

  // Saving a score reads the session again, so the page shows what is stored (the scores panel is hidden for now).
  const onScore = (traceId, level, body) => {
    const mine = ask.current;
    setScore({ saving: level, error: '' });
    labApi.evalScoreTrace(traceId, level, body)
      .then(() => labApi.evalSession(sessionId))
      .then((out) => { if (mine === ask.current) { setData(out); setScore({ saving: null, error: '' }); } })
      .catch((e) => { if (mine === ask.current) setScore({ saving: null, error: errText(e) }); });
  };

  // Setting a label reads the trace's labels again, so the page shows what is stored.
  // Only the newest click's answer is shown, so a slow earlier one never puts back an older value.
  // Flagging a turn's expected outcome reads the flags and the session again: the tab marker and the list come from it.
  const onFlag = (traceId, flagged, comment) => {
    const mine = ask.current;
    setFlagState({ traceId, busy: true, error: '' });
    labApi.evalSetTraceExpectedFlag(traceId, flagged, comment)
      .then(() => Promise.all([labApi.evalTraceExpectedFlag(traceId), labApi.evalSession(sessionId)]))
      .then(([f, s]) => {
        if (mine !== ask.current) return;
        setFlags((old) => ({ ...old, [traceId]: f || {} }));
        if (s) setData(s);
        setFlagState({ traceId, busy: false, error: '' });
      })
      .catch((e) => { if (mine === ask.current) setFlagState({ traceId, busy: false, error: errText(e) }); });
  };

  const onLabel = (traceId, mode, value) => {
    const mine = ask.current;
    const seq = (labelAsk.current += 1);
    const current = () => mine === ask.current && seq === labelAsk.current;
    setLabel({ traceId, busy: mode, error: '' });
    labApi.evalSetTraceLabel(traceId, mode, value)
      .then(() => labApi.evalTraceLabels(traceId))
      .then((out) => { if (current()) { setLabels((old) => ({ ...old, [traceId]: out || {} })); setLabel({ traceId, busy: null, error: '' }); } })
      .catch((e) => { if (current()) setLabel({ traceId, busy: null, error: errText(e) }); });
  };

  return (
    <div className="lab-ses-page">
      <div className="lab-row">
        {onBack ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onBack}>{backText || '← All sessions'}</button> : null}
      </div>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {!data && !error ? <p className="lab-small" role="status">{'Reading the session…'}</p> : null}
      {data ? <SessionBody data={data} active={active} trees={trees} notes={notes} labels={labels} onTab={setActive} me={me}
        flags={flags} flagState={flagState} onFlag={onFlag} label={label} onLabel={onLabel} onNoted={setNext} onScore={onScore} saving={score.saving}
        scoreError={score.error} /> : null}
      <NextStep step={onOpenLoop ? next : null} onClose={() => setNext(null)}
        onAct={(a) => { if (a === 'open_loop' && onOpenLoop) onOpenLoop(); }} />
    </div>
  );
}
