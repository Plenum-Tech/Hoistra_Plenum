// Sessions — the eval store's pages (11 Nov 2026, task plan Workstream B), in Langfuse's words.
//
// Three views under one tab:
//   Sessions    one row per session (a scenario asked once, every turn of it), filtered by run tag, scenario type,
//               failure status and score, the plan's filters (B4.2). A row opens the session as a tab per turn
//               (SessionPage.jsx), where each trace is scored at the three levels.
//   Compare     two run tags side by side, scenario by scenario: baseline_v0 against the run after a fix (Compare.jsx).
//   Scenarios   the scenarios with the expected outcome each was written with, and the specification they were
//               written from (Scenarios.jsx).
//
// Sessions get here by reading what the chat recorded ("Read new sessions", for curators): conversations whose
// session id names their run tag and scenario. Nothing is asked from this page. "Evaluate" scores a run tag's traces
// automatically; "Sync to dev" sends this machine's sessions to the development instance.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { fmtWhen, isCurator, plural } from './logic/format.js';
import SessionPage from './SessionPage.jsx';
import Compare from './Compare.jsx';
import Scenarios from './Scenarios.jsx';
import { LEVELS, LEVEL_TEXT, STATUS_OPTIONS, TYPE_OPTIONS, evaluateLevels, evaluateStep, evaluateText, ingestText, sessionQuery,
  sessionRow } from './logic/sessions.js';
import './runs.css';
import './sessions.css';

const PAGE = 50;
const LEVEL_OPTIONS = [['', 'Any level']].concat(LEVELS.map((k) => [k, LEVEL_TEXT[k]]));
const VERDICT_OPTIONS = [['', 'Any verdict'], ['pass', 'Pass'], ['fail', 'Fail'], ['not_evaluated', 'Not evaluated']];
const EMPTY = { runTag: '', type: '', status: '', level: '', verdict: '', needsSme: false, stale: false, allAttempts: false };
const PASSES_MAX = 400;
// Scores are hidden on this page for now (Hussain, 12 Nov 2026): the list's Logic / User experience / Domain columns,
// the Level, Verdict and "Waiting for a domain expert" filters, the Evaluate button and the Compare view. A session's verdict is to come
// from what people annotate on its turns. Set to true to show the automatic scores again.
export const SHOW_SCORES = false;
// The page's views. Compare (two run tags' verdicts side by side) is hidden with the scores.
export const VIEWS = [['sessions', 'Sessions']].concat(SHOW_SCORES ? [['compare', 'Compare']] : [], [['scenarios', 'Scenarios']]);

function readSession() {
  try { return new URLSearchParams(window.location.search).get('session') || null; } catch (e) { return null; }
}
function keepSession(id) {
  try {
    const q = new URLSearchParams(window.location.search);
    if (id) q.set('session', id); else q.delete('session');
    const s = q.toString();
    window.history.replaceState(null, '', window.location.pathname + (s ? '?' + s : ''));
  } catch (e) { /* the address is a convenience */ }
}

function Select({ label, value, options, onChange }) {
  return (
    <label className="lab-field lab-ses-filter"><span>{label}</span>
      <select value={value || ''} onChange={(e) => onChange(e.target.value)}>
        {options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}
      </select>
    </label>
  );
}

function Check({ label, checked, onChange }) {
  return (
    <label className="lab-ses-check">
      <input type="checkbox" checked={!!checked} onChange={(e) => onChange(e.target.checked)} /><span>{label}</span>
    </label>
  );
}

// The plan's filters (B4.2): run tag, scenario type, failure status and score (a level with its verdict), and three
// switches: sessions waiting for a domain expert, sessions whose expected outcome is out of date, every attempt.
export function Filters({ tags, value, onChange, scores = SHOW_SCORES }) {
  const v = value || {};
  const set = (key) => (x) => onChange({ ...v, [key]: x });
  const tagOptions = [['', 'All run tags']].concat((tags || []).map((t) => [t.tag, t.tag + ' (' + (Number(t.sessions) || 0) + ')']));
  return (
    <div className="lab-ses-filterbox" role="group" aria-label="Filter the sessions">
      <div className="lab-row lab-ses-filters">
        <Select label="Run tag" value={v.runTag} options={tagOptions} onChange={set('runTag')} />
        <Select label="Scenario type" value={v.type} options={TYPE_OPTIONS} onChange={set('type')} />
        <Select label="Status" value={v.status} options={STATUS_OPTIONS} onChange={set('status')} />
        {scores ? <Select label="Level" value={v.level} options={LEVEL_OPTIONS} onChange={set('level')} /> : null}
        {scores ? <Select label="Verdict" value={v.verdict} options={VERDICT_OPTIONS} onChange={set('verdict')} /> : null}
      </div>
      <div className="lab-row">
        {scores ? <Check label="Waiting for a domain expert" checked={v.needsSme} onChange={set('needsSme')} /> : null}
        <Check label="Expected outcome out of date" checked={v.stale} onChange={set('stale')} />
        <Check label="Show every attempt" checked={v.allAttempts} onChange={set('allAttempts')} />
      </div>
      {scores && ((v.level && !v.verdict) || (!v.level && v.verdict))
        ? <p className="lab-small">{'Choose a level and a verdict together to filter by score.'}</p> : null}
    </div>
  );
}

const LEVEL_WORDS = Object.freeze({ logic: 'logic', ux: 'user experience', domain: 'domain' });
const listed = (words) => (words.length > 1 ? words.slice(0, -1).join(', ') + ' and ' + words[words.length - 1] : words[0] || '');

// Scoring a run tag's traces automatically at the levels the person may run (all three, or logic only). `said` is how
// far it has got.
export function EvaluateBar({ runTag, levels, busy, said, error, onRun }) {
  const mine = (levels && levels.length ? levels : LEVELS).map((l) => LEVEL_WORDS[l] || l);
  return (
    <section className="lab-ses-sync" aria-label="Evaluate">
      <div className="lab-row">
        <button type="button" className="lab-btn lab-btn-quiet" disabled={!runTag || !!busy} onClick={onRun}>
          {busy ? 'Evaluating…' : runTag ? 'Evaluate ' + runTag : 'Evaluate'}</button>
        <span className="lab-small">{runTag
          ? 'Scores every trace under this run tag at the ' + listed(mine) + (mine.length > 1 ? ' levels.' : ' level.') + ' A person\'s score is never replaced.'
          : 'Choose a run tag to evaluate it.'}</span>
      </div>
      {said ? <p className="lab-small" role="status">{said}</p> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
    </section>
  );
}

// The rows a push left out because the dev database already holds something else in their place (never changed there).
function leftOutText(conflicts) {
  const total = Object.values(conflicts || {}).reduce((a, n) => a + (Number(n) || 0), 0);
  return total ? ' ' + total + (total === 1 ? ' row was left out because the dev database holds something else in its place.'
    : ' rows were left out because the dev database holds something else in their place.') : '';
}

// Pushing this machine's sessions to the development instance (task plan B5.3). Drawn only when the service says
// there is somewhere to push to; what it shows of that place is the host and database the service sends, and what it
// says afterwards is the service's own sentence, so the page and the command line never differ.
export function SyncBar({ status, busy, said, error, onPush, onCheck }) {
  const st = status || {};
  if (!st.enabled) return null;
  const last = st.last || null;
  return (
    <section className="lab-ses-sync" aria-label="Sync to the development instance">
      <div className="lab-row">
        <button type="button" className="lab-btn" disabled={!!busy} onClick={onPush}>{busy ? 'Syncing…' : 'Sync to dev'}</button>
        <button type="button" className="lab-btn lab-btn-quiet" disabled={!!busy} onClick={onCheck}>{'Check what would go'}</button>
        <span className="lab-small">{'Sends sessions, traces, spans and scores to '}<code className="lab-mono">{st.peer}</code></span>
      </div>
      {last ? <p className="lab-small">{'Last push ' + (fmtWhen(last.finished_at) || '') + ': '
        + (last.status === 'done' ? (last.dry_run ? 'checked only' : 'finished') : last.status === 'failed' ? 'stopped part of the way' : 'refused')
        + (last.error ? ' (' + last.error + ')' : '') + '.' + leftOutText(last.conflicts)}</p> : null}
      {said ? <p className="lab-small" role="status">{said}</p> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
    </section>
  );
}

// The sessions as a table. `rows` null while reading.
export function SessionsTable({ rows, runTag, onOpen }) {
  if (rows == null) return <p className="lab-small" role="status">{'Reading the sessions…'}</p>;
  if (!rows.length) {
    return (
      <div className="lab-empty lab-empty-big">
        <h2>{runTag ? 'No sessions for ' + runTag + ' yet.' : 'No sessions yet.'}</h2>
        <p className="lab-small">{'A session appears here once a recorded conversation has been read in. Its session id names its run tag and scenario.'}</p>
      </div>
    );
  }
  return (
    <div className="lab-table-wrap lab-table-full">
      <table className="lab-table">
        <thead>
          <tr><th scope="col">{'Session'}</th><th scope="col">{'Run tag'}</th><th scope="col">{'Type'}</th><th scope="col">{'Turns'}</th>
            <th scope="col">{'Status'}</th>
            {SHOW_SCORES ? <><th scope="col">{'Logic'}</th><th scope="col">{'User experience'}</th><th scope="col">{'Domain'}</th></> : null}
            <th scope="col">{'Started'}</th></tr>
        </thead>
        <tbody>
          {rows.map((raw) => {
            const r = sessionRow(raw);
            return (
              <tr key={r.id}>
                <td>
                  <button type="button" className="lab-link lab-ses-open" onClick={() => onOpen(r.id)}>{r.title}</button>
                  <div className="lab-small lab-mono">{r.id}</div>
                  {r.origin ? <div className="lab-small">{'asked on ' + r.origin + (r.attempt > 1 ? ' · attempt ' + r.attempt : '')}</div> : null}
                  {raw.stale ? <div className="lab-small">{'Expected outcome is out of date'}</div> : null}
                </td>
                <td>{r.runTag ? <span className="lab-chip lab-chip-synth">{r.runTag}</span> : '—'}</td>
                <td>{r.type}</td>
                <td>{r.turns}</td>
                <td><span className={'lab-chip' + (r.failed ? ' lab-ses-bad' : ' lab-chip-ok')}>{r.status}</span></td>
                {SHOW_SCORES && r.levels.map((l) => (
                  <td key={l.key}><span className={'lab-chip' + (l.verdict === 'pass' ? ' lab-chip-ok' : l.verdict === 'fail' ? ' lab-ses-bad' : '')}
                    title={l.source === 'ANNOTATION' ? 'Scored by a person' : l.source === 'EVAL' ? 'Scored automatically' : 'Not scored yet'}>{l.text}</span></td>
                ))}
                <td>{fmtWhen(r.when) || '—'}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function SessionsList({ me, tags, reloadTags, onOpen }) {
  const [filters, setFilters] = React.useState(EMPTY);
  const [rows, setRows] = React.useState(null);
  const [next, setNext] = React.useState(null);
  const [error, setError] = React.useState('');
  const [reading, setReading] = React.useState({ busy: false, said: '', error: '' });
  const [sync, setSync] = React.useState({ status: null, busy: false, said: '', error: '' });
  const [evaluating, setEvaluating] = React.useState({ busy: false, said: '', error: '' });
  const ask = React.useRef(0);
  const curator = isCurator(me);
  const pusher = !!(me && me.all_companies);

  const load = React.useCallback((more) => {
    const mine = (ask.current += 1);
    if (!more) setRows(null);
    setError('');
    const query = { ...sessionQuery(filters), limit: PAGE };
    if (more && next) query.before = next;
    labApi.evalSessions(query)
      .then((out) => {
        if (mine !== ask.current) return;
        const got = (out && out.sessions) || [];
        setRows((old) => (more && old ? old.concat(got) : got));
        setNext((out && out.next) || null);
      })
      .catch((e) => { if (mine === ask.current) { setError(errText(e)); if (!more) setRows([]); } });
  }, [filters, next]);

  React.useEffect(() => { load(false); }, [filters]); // eslint-disable-line react-hooks/exhaustive-deps
  React.useEffect(() => {
    if (!pusher) return;
    labApi.evalSyncStatus().then((out) => setSync((old) => ({ ...old, status: out || null })), () => {});
  }, [pusher]);

  const readIn = () => {
    setReading({ busy: true, said: '', error: '' });
    labApi.evalIngest({})
      .then((out) => {
        setReading({ busy: false, error: '', said: ingestText(out) });
        reloadTags();
        load(false);
      })
      .catch((e) => setReading({ busy: false, said: '', error: errText(e) }));
  };

  const pushTo = (dryRun) => () => {
    setSync((old) => ({ ...old, busy: true, said: '', error: '' }));
    labApi.evalSync({ dry_run: dryRun })
      .then((out) => labApi.evalSyncStatus().catch(() => null)
        .then((st) => setSync((old) => ({ status: st || old.status, busy: false, said: (out && out.line) || 'Done.', error: '' }))))
      .catch((e) => setSync((old) => ({ ...old, busy: false, said: '', error: errText(e) })));
  };

  // One pass scores a few traces; the page asks again until nothing is left, the run tag's spending limit is reached,
  // or a pass got nowhere, so it never spins. The logic checks run first (they cost nothing and never wait on a model),
  // then the judges, passing over any trace whose judge call failed so one that is down does not hold up the rest.
  const levels = evaluateLevels(me);
  const evaluate = async () => {
    const tag = filters.runTag;
    if (!tag) return;
    const judges = levels.filter((l) => l !== 'logic');
    const phases = [['logic']].concat(judges.length ? [judges] : []);
    setEvaluating({ busy: true, said: 'Starting…', error: '' });
    let st = { scores: 0, skip: [], left: 0, stop: null };
    try {
      for (const phase of phases) {
        st = { ...st, stop: null };
        for (let pass = 0; pass < PASSES_MAX && !st.stop; pass += 1) {
          const out = await labApi.evalEvaluate({ run_tag: tag, levels: phase, skip: st.skip });
          st = evaluateStep(st, out);
          if (!st.stop) setEvaluating({ busy: true, error: '', said: evaluateText({ ...st, tag }) });
        }
        if (st.stop !== 'done') break;
      }
      const text = evaluateText({ ...st, tag });
      setEvaluating({ busy: false, said: st.stop === 'stuck' ? '' : text, error: st.stop === 'stuck' ? text : '' });
    } catch (e) {
      setEvaluating({ busy: false, said: '', error: errText(e) });
    }
    load(false);
  };

  return (
    <div className="lab-ses-list">
      <div className="lab-head-row">
        <p className="lab-small">{'Each session is one scenario asked once, with a trace per turn. Filter by run tag to read one batch.'}</p>
        {curator ? (
          <div className="lab-row">
            <button type="button" className="lab-btn lab-btn-quiet" disabled={reading.busy} onClick={readIn}>
              {reading.busy ? 'Reading…' : 'Read new sessions'}</button>
          </div>
        ) : null}
      </div>
      {reading.said ? <p className="lab-small" role="status">{reading.said}</p> : null}
      {reading.error ? <div role="alert" className="lab-error">{'Could not read new sessions: ' + reading.error}</div> : null}
      {pusher ? <SyncBar status={sync.status} busy={sync.busy} said={sync.said} error={sync.error}
        onPush={pushTo(false)} onCheck={pushTo(true)} /> : null}
      <Filters tags={tags} value={filters} onChange={setFilters} />
      {curator && SHOW_SCORES ? <EvaluateBar runTag={filters.runTag} levels={levels} busy={evaluating.busy} said={evaluating.said} error={evaluating.error} onRun={evaluate} /> : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      <SessionsTable rows={rows} runTag={filters.runTag} onOpen={onOpen} />
      {next && rows && rows.length ? (
        <div className="lab-row lab-empty-actions">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => load(true)}>{'Show older sessions'}</button>
        </div>
      ) : null}
    </div>
  );
}

export default function Sessions({ me, onOpenLoop }) {
  const [view, setView] = React.useState('sessions');
  const [tags, setTags] = React.useState(null);
  const [open, setOpen] = React.useState(readSession);

  const loadTags = React.useCallback(() => {
    labApi.evalRunTags().then((out) => setTags((out && out.run_tags) || []), () => setTags([]));
  }, []);
  React.useEffect(() => { loadTags(); }, [loadTags]);
  React.useEffect(() => { keepSession(open); }, [open]);

  if (open) return <SessionPage sessionId={open} me={me} onBack={() => setOpen(null)} onOpenLoop={onOpenLoop} />;
  return (
    <div className="lab-ses-page">
      <div className="lab-page-head lab-head-row">
        <h1>{'Sessions'}</h1>
        <div className="lab-turns" role="tablist" aria-label="Sessions views">
          {VIEWS.map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={view === key ? 'true' : 'false'}
              className={'lab-turn lab-ses-view' + (view === key ? ' lab-on' : '')} onClick={() => setView(key)}>{label}</button>
          ))}
        </div>
      </div>
      {view === 'sessions' ? <SessionsList me={me} tags={tags} reloadTags={loadTags} onOpen={setOpen} /> : null}
      {view === 'compare' && SHOW_SCORES ? <Compare me={me} tags={tags} /> : null}
      {view === 'scenarios' ? <Scenarios me={me} /> : null}
    </div>
  );
}
