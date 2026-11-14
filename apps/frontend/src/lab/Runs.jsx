// Runs — the review loop's runs (22 Oct 2026 feedback; laid out like the Cartwheel Trace Lab's "Scenario runs", in
// Hoistra's look). Without a run: every run the person may see as a card (its code and title, synthetic or live with whose
// questions and which day, where it came from, when its questions were asked, how far the review has got), a search, a
// filter, "Sync now" (the chat's newer live answers become questions, one run per person per day) and, for curators
// (company admins, the platform team, the consultant), an Excel / CSV import and a run typed on the page. With a run: its
// questions on the left (code, question, role, expected answer, answers, notes) and the chosen question on the right
// (QuestionPage.jsx).
//
// The Sessions tab moved in here (14 Nov 2026, Hussain). A run the sync made from a run tag's sessions ("Sessions ·
// <tag>") and a person's live day run open as their conversations, one row per conversation in the Sessions list's own
// table, and a row opens the session page (SessionPage.jsx): one page for scenario questions and live chat questions.
// A live question whose conversation is not filed yet is a "Reading in…" row until a sync files it. A run typed on the
// page or imported keeps its questions. The Read buttons and the Scenarios view are here too.
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import { ingestText } from './logic/sessions.js';
import QuestionPage, { fmtDay, fmtRange, fmtWhen, isCurator, plural } from './QuestionPage.jsx';
import SessionPage from './SessionPage.jsx';
import Scenarios from './Scenarios.jsx';
import { SessionsTable } from './Sessions.jsx';
import './runs.css';
import './sessions.css';

const PAGE = 50;
const QUESTIONS_MAX = 2000;
const QUESTION_MAX = 4000;
// Sessions in place of Synthetic (13 Nov 2026, Hussain): All shows every run; Sessions the runs the service makes from
// a run tag's sessions ("Sessions · <tag>", runs.sync_sessions); Live the runs synced from what people ask by hand.
export const KINDS = [['', 'All'], ['sessions', 'Sessions'], ['production', 'Live']];
export const SOURCE_TEXT = Object.freeze({ coding_agent: 'coding agent', excel: 'Excel', manual: 'manual', sync: 'synced' });

// One run as its card says it.
export function runView(r) {
  const x = r || {};
  const live = x.kind === 'production';
  const n = (k) => Number(x[k]) || 0;
  // A run tag's run (a link's ?tag=, or a sessions run, 14 Nov 2026) is counted from its sessions, not the questions
  // linked to it: its turns may already be questions of live days, so it can hold none.
  const tagged = x.entry === 'tag' || (!!sessionsTagOf(x) && x.n_sessions != null);
  const sessionsCount = x.n_sessions == null ? '' : plural(n('n_sessions'), 'session', 'sessions') + ' · ' + plural(n('n_turns'), 'turn', 'turns');
  if (x.entry === 'tag') {
    return { key: 'tag:' + x.run_tag, code: '', title: x.title || 'Sessions · ' + (x.run_tag || ''), live: false, kindText: 'sessions',
      source: SOURCE_TEXT.sync, range: fmtRange(x.first_at, x.last_at) || '', counts: sessionsCount };
  }
  return {
    key: x.code || 'run:' + x.run_no,
    code: x.code || (x.run_no ? 'R' + x.run_no : '—'), title: x.title || '', live,
    // A run made from a run tag's sessions is the one synthetic run the sync itself makes: say "sessions", not "synthetic".
    kindText: live ? ['live', x.owner_email || 'unknown person', fmtDay(x.run_day)].filter(Boolean).join(' · ')
      : (x.kind === 'synthetic' && x.source === 'sync' ? 'sessions' : 'synthetic'),
    source: SOURCE_TEXT[x.source] || x.source || '',
    range: fmtRange(x.first_at, x.last_at) || (x.created_at ? 'Created ' + fmtWhen(x.created_at) : ''),
    counts: tagged ? sessionsCount : [plural(n('n_questions'), 'question', 'questions'), n('n_traced') + ' with answers', n('n_expected') + ' with expected',
      plural(n('n_notes'), 'note', 'notes'), n('n_reviewed') + ' reviewed'].join(' · ')
  };
}

// One question of a run as its list item says it.
export function questionItem(q) {
  const x = q || {};
  const answers = Array.isArray(x.turn_ids) ? x.turn_ids.length : 0;
  const notes = Number(x.n_notes) || 0;
  return {
    code: x.code || '—', text: x.question || '—', role: x.role || '', expected: !!x.has_expected,
    answers: answers ? plural(answers, 'answer', 'answers') : 'no answer yet',
    notes: notes ? plural(notes, 'note', 'notes') + ' · ' + plural(Number(x.n_annotators) || 0, 'person', 'people') : ''
  };
}

// What Sync now did (14 Nov 2026, Hussain: one button syncs everything): one line of what is new, the parts that are
// zero left out ("Up to date" when nothing is), and one short line for each part that failed or was skipped.
const SYNC_PARTS = [['new_questions', 'new question', 'new questions'], ['chat_sessions', 'conversation', 'conversations'],
  ['codex_sessions', 'Codex session', 'Codex sessions'], ['new_runs', 'new run', 'new runs']];
const SYNC_FAILED = [['questions', 'Live questions could not be synced: '], ['chat', 'Chat conversations could not be read: '],
  ['codex', 'Codex sessions could not be read: '], ['runs', 'Runs could not be made: ']];
export function syncLines(out) {
  const o = out || {};
  const parts = SYNC_PARTS.filter(([k]) => Number(o[k]) > 0).map(([k, one, many]) => plural(o[k], one, many));
  const line = (parts.join(' · ') || 'Up to date') + (o.more ? ' · more to come: press Sync now again' : '');
  const failed = o.failed || {};
  const problems = SYNC_FAILED.filter(([k]) => failed[k]).map(([k, said]) => said + failed[k]);
  const skipped = o.skipped || {};
  return { line, problems: problems.concat(Object.keys(skipped).map((k) => skipped[k]).filter(Boolean)) };
}
export function syncText(out) {
  const { line, problems } = syncLines(out);
  return [line].concat(problems).join('. ');
}

// A run the sync made from a run tag's sessions: one batch of the tag, "Sessions · <tag> · 14 Nov 10:02" (14 Nov 2026), or
// "Sessions · <tag>" when it was made before batches. The service says its tag (run_tag, null when it is no batch).
const SESSIONS_PREFIX = 'Sessions · ';
const BATCH_START = / · \d{1,2} [A-Z][a-z]{2} \d{2}:\d{2}$/;
export function sessionsTagOf(r) {
  if (!r || r.kind !== 'synthetic' || r.source !== 'sync') return null;
  if (Object.prototype.hasOwnProperty.call(r, 'run_tag')) return r.run_tag || null;
  const t = String(r.title || '');
  if (!t.startsWith(SESSIONS_PREFIX) || t.length <= SESSIONS_PREFIX.length) return null;
  const tag = t.slice(SESSIONS_PREFIX.length).replace(BATCH_START, '');
  return tag && tag !== 'chat' && tag !== '(no run tag)' ? tag : null;
}
// How a run opens (14 Nov 2026, Hussain): a sessions run and a live day run as their conversations ('sessions',
// 'live'), a run typed on the page or imported as its questions ('questions'); null while the run is not known yet.
export function runMode(r) {
  if (!r || !r.kind) return null;
  if (r.kind === 'production') return 'live';
  return sessionsTagOf(r) ? 'sessions' : 'questions';
}

// Every run opens inside Runs (the Sessions tab is gone, 14 Nov 2026).
export function openRow(r, onRun) {
  if (onRun) onRun(r && r.code);
}

// A run as the top bar's picker names it: a sessions run by its sessions, any other by its questions (14 Nov 2026).
export function runLabel(r) {
  const x = r || {};
  const n = (k) => Number(x[k]) || 0;
  const what = sessionsTagOf(x) && x.n_sessions != null ? plural(n('n_sessions'), 'session', 'sessions')
    : plural(n('n_questions'), 'question', 'questions');
  return x.code + ' · ' + x.title + ' · ' + what;
}

// The conversation a question of the run is a turn of, and that turn ({ id, turn }), or null when it is in none yet.
export function sessionForQuestion(data, code) {
  const want = String(code || '').toUpperCase();
  const rows = (data && data.sessions) || [];
  for (const s of rows) {
    const i = (s.codes || []).indexOf(want);
    if (i >= 0) return { id: s.id, turn: (s.turn_ids || [])[i] || null };
  }
  // A live day's scenario question is not a row of the day, but its conversation is still known.
  const other = ((data && data.elsewhere) || []).find((o) => o && o.code === want);
  return other ? { id: other.session_id, turn: other.turn_id || null } : null;
}

export function RunsList({ runs, onOpen, loading, error, filtered, curator }) {
  const rows = (runs || []).map(runView);
  const open = onOpen || (() => {});
  return (
    <section className="lab-run-list-wrap" aria-label="Runs">
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {loading && !rows.length ? <p className="lab-small">{'Reading the runs…'}</p> : null}
      {!loading && !error && !rows.length ? (
        <div className="lab-empty">
          <h2>{filtered ? 'No runs match' : 'No runs yet'}</h2>
          <p className="lab-small">{filtered ? 'Try other words, or show all runs.'
            : 'Sync now brings in the chat’s live questions, one run per person per day.'
              + (curator ? ' Import a file or start a new run for synthetic questions.' : '')}</p>
        </div>
      ) : null}
      <ul className="lab-run-list">
        {rows.map((r, i) => (
          <li key={r.key}>
            <button type="button" className="lab-run-card" onClick={() => openRow((runs || [])[i], open)}>
              <span className="lab-run-top">
                {r.code ? <strong className="lab-run-code">{r.code}</strong> : null}
                {r.title ? <span className="lab-run-title">{r.title}</span> : null}
              </span>
              <span className="lab-chips">
                <span className={'lab-chip ' + (r.live ? 'lab-chip-live' : 'lab-chip-synth')}>{r.kindText}</span>
                {r.source ? <span className="lab-chip">{r.source}</span> : null}
              </span>
              {r.range ? <span className="lab-small">{r.range}</span> : null}
              <span className="lab-small lab-run-counts">{r.counts}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function RunQuestionsList({ code, run, questions, selected, loading, error, onOpen, onBack }) {
  const r = run ? runView(run) : null;
  const items = (questions || []).map(questionItem);
  const open = onOpen || (() => {});
  const shown = r ? r.code : (code || 'this run');
  return (
    <section className="lab-qlist lab-runq-list" aria-label={'Questions in ' + shown}>
      <div className="lab-runq-head">
        <button type="button" className="lab-link lab-back" onClick={onBack || (() => {})}>{'← All runs'}</button>
        <div className="lab-runq-title"><strong className="lab-run-code">{shown}</strong>{r && r.title ? <span>{r.title}</span> : null}</div>
        {r ? (
          <span className="lab-chips">
            <span className={'lab-chip ' + (r.live ? 'lab-chip-live' : 'lab-chip-synth')}>{r.kindText}</span>
            {r.source ? <span className="lab-chip">{r.source}</span> : null}
          </span>
        ) : null}
        {r ? <p className="lab-small">{r.counts}</p> : null}
      </div>
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      {loading && !items.length ? <p className="lab-small">{'Reading the questions…'}</p> : null}
      {!loading && !error && !items.length ? <p className="lab-small">{'No questions in this run yet.'}</p> : null}
      <ul className="lab-qitems">
        {items.map((q) => (
          <li key={q.code}>
            <button type="button" className={'lab-qitem' + (q.code === selected ? ' lab-on' : '')}
              aria-current={q.code === selected ? 'true' : undefined} onClick={() => open(q.code)}>
              <span className="lab-qcode">{q.code}</span>
              <span className="lab-qtext">{q.text}</span>
              <span className="lab-chips">
                {q.role ? <span className="lab-chip">{q.role}</span> : null}
                {q.expected ? <span className="lab-chip lab-chip-ok">{'expected ✓'}</span> : null}
                <span className="lab-chip">{q.answers}</span>
                {q.notes ? <span className="lab-chip">{q.notes}</span> : null}
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function ImportResult({ result, onOpen }) {
  const r = result || {};
  const problems = Array.isArray(r.problems) ? r.problems : [];
  const n = (k) => Number(r[k]) || 0;
  return (
    <div className="lab-runs-result" role="status">
      <p className="lab-runs-result-line">
        {r.run ? <strong>{r.run + ' · '}</strong> : null}
        {[plural(n('questions_added'), 'question added', 'questions added'),
          plural(n('expected_added'), 'expected answer added', 'expected answers added'),
          plural(n('answers_linked'), 'answer linked', 'answers linked')].join(' · ')}
      </p>
      {problems.length ? (
        <>
          <p className="lab-small">{plural(problems.length, 'row needs', 'rows need') + ' a look:'}</p>
          <ul className="lab-problems">
            {problems.map((p, i) => <li key={i}>{(p && p.row ? 'Row ' + p.row + ': ' : '') + ((p && p.error) || 'Not imported.')}</li>)}
          </ul>
        </>
      ) : null}
      {r.run && onOpen ? <button type="button" className="lab-link" onClick={() => onOpen(r.run)}>{'Open ' + r.run}</button> : null}
    </div>
  );
}

export function ImportPanel({ onImported, onOpen, onClose }) {
  const [file, setFile] = React.useState(null);
  const [run, setRun] = React.useState('');
  const [title, setTitle] = React.useState('');
  const [st, setSt] = React.useState({ busy: false, error: '', result: null });
  const submit = async (e) => {
    e.preventDefault();
    if (!file || st.busy) return;
    setSt({ busy: true, error: '', result: null });
    try {
      const out = (await labApi.importRuns(file, { run: run.trim().toUpperCase(), title: run.trim() ? '' : title.trim() })) || {};
      setSt({ busy: false, error: '', result: out });
      if (onImported) onImported(out);
    } catch (err) {
      setSt({ busy: false, error: errText(err), result: null });
    }
  };
  return (
    <section className="lab-runs-panel" aria-labelledby="lab-import-h">
      <h2 id="lab-import-h">{'Import questions from Excel or CSV'}</h2>
      <p className="lab-small">{'A header row with a question column, or a question code such as R3-12. Optional columns: follow-ups, role, intent, expected answer, expected id, evidence and turn id. A row with a question code adds its expected answer (or links its answer) to that question; any other row becomes a new question.'}</p>
      <form className="lab-runs-form" onSubmit={submit}>
        <label className="lab-field"><span>{'File'}</span>
          <input type="file" accept=".xlsx,.csv" onChange={(e) => setFile((e.target.files && e.target.files[0]) || null)} disabled={st.busy} required />
        </label>
        <div className="lab-runs-form-row">
          <label className="lab-field"><span>{'Add to run (optional)'}</span>
            <input type="text" value={run} maxLength={20} placeholder="e.g. R3" onChange={(e) => setRun(e.target.value)} disabled={st.busy} />
          </label>
          <label className="lab-field"><span>{'Title of the new run (optional)'}</span>
            <input type="text" value={title} maxLength={200} placeholder="The file name when left empty" onChange={(e) => setTitle(e.target.value)}
              disabled={st.busy || !!run.trim()} />
          </label>
        </div>
        {st.error ? <div role="alert" className="lab-error">{st.error}</div> : null}
        <div className="lab-row">
          <button type="submit" className="lab-btn lab-btn-sm" disabled={st.busy || !file}>{st.busy ? 'Importing…' : 'Import'}</button>
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onClose} disabled={st.busy}>{'Close'}</button>
        </div>
      </form>
      {st.result ? <ImportResult result={st.result} onOpen={onOpen} /> : null}
    </section>
  );
}

export function NewRunPanel({ onCreated, onClose }) {
  const [title, setTitle] = React.useState('');
  const [text, setText] = React.useState('');
  const [st, setSt] = React.useState({ busy: false, error: '' });
  const lines = text.split('\n').map((s) => s.trim()).filter(Boolean);
  const tooMany = lines.length > QUESTIONS_MAX;
  const tooLong = lines.some((s) => s.length > QUESTION_MAX);
  const ready = !!title.trim() && !tooMany && !tooLong && !st.busy;
  const submit = async (e) => {
    e.preventDefault();
    if (!ready) return;
    setSt({ busy: true, error: '' });
    try {
      const out = await labApi.createRun({ title: title.trim(), questions: lines.map((question) => ({ question })) });
      setSt({ busy: false, error: '' });
      if (onCreated) onCreated((out && out.run && out.run.code) || null);
    } catch (err) {
      setSt({ busy: false, error: errText(err) });
    }
  };
  return (
    <section className="lab-runs-panel" aria-labelledby="lab-newrun-h">
      <h2 id="lab-newrun-h">{'New run'}</h2>
      <form className="lab-runs-form" onSubmit={submit}>
        <label className="lab-field"><span>{'Title'}</span>
          <input type="text" value={title} maxLength={200} placeholder="e.g. Certificates, October" onChange={(e) => setTitle(e.target.value)} disabled={st.busy} required />
        </label>
        <label className="lab-field"><span>{'Questions, one per line'}</span>
          <textarea className="lab-note" rows={6} value={text} onChange={(e) => setText(e.target.value)} disabled={st.busy}
            placeholder={'Which certificates expire this month?\nWhich vendors are blocked right now?'} />
        </label>
        <p className="lab-small">{lines.length ? plural(lines.length, 'question', 'questions') + ', numbered when the run is made.'
          : 'Leave it empty to import questions into the run later.'}</p>
        {tooMany ? <div role="alert" className="lab-error">{'Up to 2,000 questions at a time.'}</div> : null}
        {tooLong ? <div role="alert" className="lab-error">{'Keep each question under 4,000 characters.'}</div> : null}
        {st.error ? <div role="alert" className="lab-error">{st.error}</div> : null}
        <div className="lab-row">
          <button type="submit" className="lab-btn lab-btn-sm" disabled={!ready}>{st.busy ? 'Creating…' : 'Create run'}</button>
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onClose} disabled={st.busy}>{'Cancel'}</button>
        </div>
      </form>
    </section>
  );
}

// The page's two views (14 Nov 2026): the runs, and the scenarios the Sessions tab used to hold.
export const RUNS_TABS = [['runs', 'Runs'], ['scenarios', 'Scenarios']];

export function RunsHome({ me, onRun, startTab }) {
  const curator = isCurator(me);
  const [tab, setTab] = React.useState(startTab === 'scenarios' ? 'scenarios' : 'runs');
  const [reading, setReading] = React.useState({ busy: false, said: '', error: '' });
  const [f, setF] = React.useState({ q: '', kind: '' });
  const [applied, setApplied] = React.useState({ q: '', kind: '' });
  const [list, setList] = React.useState({ runs: [], total: 0, loading: true, error: '' });
  const [sync, setSync] = React.useState({ busy: false, msg: '', error: '' });
  const [panel, setPanel] = React.useState(null);
  const ask = React.useRef(0);
  const appliedRef = React.useRef(applied);       // the filter in view, for a reload that finishes later

  const load = React.useCallback(async (g, offset) => {
    const mine = (ask.current += 1);
    appliedRef.current = g;
    setApplied(g);
    setList((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      // for_page: no live day that holds only scenario questions (14 Nov 2026).
      const out = await labApi.runs({ q: g.q.trim() || undefined, kind: g.kind || undefined, limit: PAGE, offset: offset || undefined, for_page: 'true' });
      if (mine !== ask.current) return;
      const runs = (out && out.runs) || [];
      setList((p) => ({ runs: offset ? p.runs.concat(runs) : runs, total: (out && out.total) || 0, loading: false, error: '' }));
    } catch (e) {
      if (mine === ask.current) setList((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, []);
  React.useEffect(() => { load({ q: '', kind: '' }, 0); }, [load]);

  // "Read new sessions" files the scenario runs' conversations; "Read chat questions" the chat's own conversations of
  // the last week as well (run tag "chat"), without waiting for the automatic sync. As on the old Sessions tab.
  const readIn = (chat) => () => {
    setReading({ busy: true, said: '', error: '' });
    labApi.evalIngest(chat ? { chat: true } : {})
      .then((out) => { setReading({ busy: false, error: '', said: ingestText(out) }); load(appliedRef.current, 0); })
      .catch((e) => setReading({ busy: false, said: '', error: errText(e) }));
  };

  const pickKind = (kind) => { const g = Object.assign({}, f, { kind }); setF(g); load(g, 0); };
  // One Sync now for everything (14 Nov 2026): live questions, chat conversations, Codex sessions, then the runs.
  const runSync = async () => {
    setSync({ busy: true, msg: '', problems: [], error: '' });
    try {
      const out = await labApi.syncRuns();
      const said = syncLines(out);
      setSync({ busy: false, msg: said.line, problems: said.problems, error: '' });
      load(appliedRef.current, 0);
    } catch (e) {
      setSync({ busy: false, msg: '', problems: [], error: errText(e) });
    }
  };
  const toggle = (which) => setPanel(panel === which ? null : which);
  const filtered = !!(applied.q.trim() || applied.kind);

  return (
    <div className="lab-page-body lab-runs-home">
      <div className="lab-page-head lab-head-row">
        <div>
          <h1>{'Runs'}</h1>
          <p className="lab-small lab-big">{'Sessions by run tag, and each person’s live questions by day. Open one to review its conversations.'}</p>
        </div>
        <div className="lab-turns" role="tablist" aria-label="Runs views">
          {RUNS_TABS.map(([key, label]) => (
            <button key={key} type="button" role="tab" aria-selected={tab === key ? 'true' : 'false'}
              className={'lab-turn lab-ses-view' + (tab === key ? ' lab-on' : '')} onClick={() => setTab(key)}>{label}</button>
          ))}
        </div>
      </div>
      {tab === 'scenarios' ? <Scenarios me={me} /> : null}
      {tab === 'runs' ? <>
      <div className="lab-head-row">
        <span />
        <div className="lab-row lab-runs-actions">
          {/* One Sync now in the whole lab (14 Nov 2026, Hussain): it is the top bar's, on every page, and a sync reloads
              this list. Put this line back for a second one here:
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={runSync} disabled={sync.busy}>{sync.busy ? 'Syncing…' : 'Sync now'}</button>
          */}
          {curator ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" aria-expanded={panel === 'import' ? 'true' : 'false'}
            onClick={() => toggle('import')}>{'Import Excel/CSV'}</button> : null}
          {curator ? <button type="button" className="lab-btn lab-btn-sm" aria-expanded={panel === 'new' ? 'true' : 'false'}
            onClick={() => toggle('new')}>{'New run'}</button> : null}
          {/* Commented out (14 Nov 2026, Hussain: "I only need one button which syncs everything together"): Sync now reads
            the chat conversations and the Codex sessions too. Put these back, with readIn above, to bring them back.
          {curator ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={reading.busy} onClick={readIn(false)}>
            {reading.busy ? 'Reading…' : 'Read new sessions'}</button> : null}
          {curator ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={reading.busy} onClick={readIn(true)}
            title="Also read the questions asked in the chat over the last 7 days, under the run tag 'chat'">
            {reading.busy ? 'Reading…' : 'Read chat questions'}</button> : null} */}
        </div>
      </div>
      {reading.said ? <p className="lab-runs-note" role="status">{reading.said}</p> : null}
      {reading.error ? <div role="alert" className="lab-error">{'Could not read new sessions: ' + reading.error}</div> : null}
      {sync.msg ? <p className="lab-runs-note" role="status">{sync.msg}</p> : null}
      {(sync.problems || []).map((p) => <div key={p} role="alert" className="lab-error">{p}</div>)}
      {sync.error ? <div role="alert" className="lab-error">{'Could not sync: ' + sync.error}</div> : null}
      {curator && panel === 'import' ? <ImportPanel onImported={() => load(applied, 0)} onOpen={onRun} onClose={() => setPanel(null)} /> : null}
      {curator && panel === 'new' ? (
        <NewRunPanel onClose={() => setPanel(null)} onCreated={(code) => { setPanel(null); if (code) onRun(code); else load(applied, 0); }} />
      ) : null}
      <form className="lab-runs-toolbar" role="search" onSubmit={(e) => { e.preventDefault(); load(f, 0); }}>
        <label className="lab-visually-hidden" htmlFor="lab-runs-q">{'Search runs'}</label>
        <input id="lab-runs-q" type="search" value={f.q} placeholder="Search by code, title or person"
          onChange={(e) => setF(Object.assign({}, f, { q: e.target.value }))} />
        <button type="submit" className="lab-btn lab-btn-quiet lab-btn-sm">{'Search'}</button>
        <div className="lab-seg" role="radiogroup" aria-label="Which runs">
          {KINDS.map(([k, t]) => (
            <button key={k || 'all'} type="button" role="radio" aria-checked={f.kind === k ? 'true' : 'false'}
              className={'lab-seg-btn' + (f.kind === k ? ' lab-on' : '')} onClick={() => pickKind(k)}>{t}</button>
          ))}
        </div>
        {list.total ? <span className="lab-small lab-push" aria-live="polite">{list.loading ? 'Reading…' : 'Showing ' + list.runs.length + ' of ' + list.total}</span> : null}
      </form>
      <RunsList runs={list.runs} loading={list.loading} error={list.error} filtered={filtered} curator={curator} onOpen={onRun} />
      {list.runs.length < list.total ? (
        <div className="lab-row">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" disabled={list.loading} onClick={() => load(applied, list.runs.length)}>
            {list.loading ? 'Reading…' : 'Show more'}
          </button>
        </div>
      ) : null}
      </> : null}
    </div>
  );
}

function RunDetail({ me, code, onRun, question, onQuestion, onOpenLoop, focus }) {
  const [st, setSt] = React.useState({ run: null, questions: [], loading: true, error: '' });
  const ask = React.useRef(0);
  const pane = React.useRef(null);
  const load = React.useCallback(async (quiet) => {
    const mine = (ask.current += 1);
    if (!quiet) setSt((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      const out = await labApi.runQuestions(code);
      if (mine !== ask.current) return;
      setSt({ run: (out && out.run) || null, questions: (out && out.questions) || [], loading: false, error: '' });
    } catch (e) {
      if (mine === ask.current) setSt((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, [code]);
  React.useEffect(() => { setSt({ run: null, questions: [], loading: true, error: '' }); load(false); }, [load]);
  React.useEffect(() => { if (pane.current) pane.current.scrollTop = 0; }, [question]);
  const back = () => { if (onQuestion) onQuestion(null); if (onRun) onRun(null); };
  return (
    <div className="lab-questions lab-runq">
      <RunQuestionsList code={code} run={st.run} questions={st.questions} selected={question} loading={st.loading} error={st.error}
        onOpen={(c) => { if (onQuestion) onQuestion(c); }} onBack={back} />
      <div className="lab-flow-wrap" ref={pane}>
        {question ? <QuestionPage key={question} code={question} me={me} onChanged={() => load(true)} onOpenLoop={onOpenLoop} focus={focus} /> : (
          <div className="lab-empty">
            <h2>{'Pick a question'}</h2>
            <p className="lab-small">{'Choose a question on the left to see its expected answer and how its answer was made. Click a step to select it; double-click it or select text in it to write a note.'}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// A sessions run's or a live day's conversations: the way back, the run's header, "Show every attempt" for a sessions
// run, then the Sessions list's table, with the questions not filed yet as rows that cannot be opened.
export function RunConversationsList({ run, code, mode, sessions, waiting, error, allAttempts, onAttempts, onOpen, onBack, more, onMore }) {
  const r = run ? runView(run) : null;
  const shown = r ? (r.code || r.title) : (code || 'this run');
  return (
    <section className="lab-ses-list" aria-label={'Conversations in ' + shown}>
      <div className="lab-runq-head">
        <button type="button" className="lab-link lab-back" onClick={onBack || (() => {})}>{'← All runs'}</button>
        <div className="lab-runq-title">{r && !r.code ? null : <strong className="lab-run-code">{shown}</strong>}{r && r.title ? <span>{r.title}</span> : null}</div>
        {r ? (
          <span className="lab-chips">
            <span className={'lab-chip ' + (r.live ? 'lab-chip-live' : 'lab-chip-synth')}>{r.kindText}</span>
            {r.source ? <span className="lab-chip">{r.source}</span> : null}
          </span>
        ) : null}
        {r && r.counts ? <p className="lab-small">{r.counts}</p> : null}
      </div>
      {mode === 'sessions' ? (
        <div className="lab-row">
          <label className="lab-ses-check">
            <input type="checkbox" checked={!!allAttempts} onChange={(e) => (onAttempts || (() => {}))(e.target.checked)} /><span>{'Show every attempt'}</span>
          </label>
        </div>
      ) : null}
      {error ? <div role="alert" className="lab-error">{error}</div> : null}
      <SessionsTable rows={error && sessions == null ? [] : sessions} waiting={waiting} onOpen={onOpen || (() => {})}
        readingText="Reading the conversations…" />
      {more && sessions && sessions.length ? (
        <div className="lab-row lab-empty-actions">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onMore}>{'Show older sessions'}</button>
        </div>
      ) : null}
    </section>
  );
}

function RunConversations({ me, code, mode, run, session, onSession, question, onQuestion, focus, onBack, onOpenLoop, turnAt }) {
  const [allAttempts, setAllAttempts] = React.useState(false);
  const [st, setSt] = React.useState({ data: null, error: '' });
  const ask = React.useRef(0);
  const load = React.useCallback(async (before) => {
    const mine = (ask.current += 1);
    if (!before) setSt({ data: null, error: '' });
    try {
      const out = await labApi.runSessions(code, Object.assign({}, allAttempts ? { all_attempts: 'true' } : {}, before ? { before } : {}));
      if (mine !== ask.current) return;
      setSt((p) => ({ error: '', data: before && p.data ? Object.assign({}, out, { sessions: p.data.sessions.concat((out && out.sessions) || []) }) : (out || {}) }));
    } catch (e) {
      if (mine === ask.current) setSt((p) => ({ data: p.data, error: errText(e) }));
    }
  }, [code, allAttempts]);
  React.useEffect(() => { load(null); }, [load]);
  // A question named by a link (Notes, Labels, Loop) opens the conversation it is a turn of, on its turn.
  const named = st.data && question ? sessionForQuestion(st.data, question) : null;
  // A session opened from Labels opens on the row's turn.
  const [turn, setTurn] = React.useState(turnAt || null);
  React.useEffect(() => {
    if (!named) return;
    setTurn((focus && focus.turn) || named.turn);
    if (onSession) onSession(named.id);
    if (onQuestion) onQuestion(null);
  }, [named && named.id, question]); // eslint-disable-line react-hooks/exhaustive-deps
  const back = '← ' + code;
  if (session) {
    return (
      <div className="lab-page-body lab-runs-home">
        <SessionPage key={session} sessionId={session} me={me} turn={turn} backText={back} onOpenLoop={onOpenLoop}
          onBack={() => { setTurn(null); if (onSession) onSession(null); }} />
      </div>
    );
  }
  // A question whose conversation is not filed yet, opened by a link: its own page, as before, until a sync files it.
  if (question && st.data && !named) {
    return (
      <div className="lab-page-body lab-runs-home">
        <div className="lab-row"><button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={() => onQuestion && onQuestion(null)}>{back}</button></div>
        <QuestionPage key={question} code={question} me={me} onOpenLoop={onOpenLoop} focus={focus} />
      </div>
    );
  }
  const d = st.data;
  return (
    <div className="lab-page-body lab-runs-home">
      <RunConversationsList run={(d && d.run) || run} code={code} mode={(d && d.mode) || mode} sessions={d ? d.sessions || [] : null}
        waiting={(d && d.waiting) || []} error={st.error} allAttempts={allAttempts} onAttempts={setAllAttempts}
        onOpen={(id) => { setTurn(null); if (onSession) onSession(id); }} onBack={onBack}
        more={d && d.next} onMore={() => load(d.next)} />
    </div>
  );
}

// A scenario run tag's conversations (14 Nov 2026): its sessions as the Sessions list read them, latest attempt first
// unless every attempt is asked for, and the session page opened from them.
function TagConversations({ me, tag, session, onSession, onBack, onOpenLoop }) {
  const [allAttempts, setAllAttempts] = React.useState(false);
  const [st, setSt] = React.useState({ rows: null, next: null, error: '' });
  const ask = React.useRef(0);
  const load = React.useCallback(async (before) => {
    const mine = (ask.current += 1);
    if (!before) setSt({ rows: null, next: null, error: '' });
    try {
      const out = await labApi.evalSessions(Object.assign({ run_tag: tag, limit: 50 }, allAttempts ? { all_attempts: 'true' } : {}, before ? { before } : {}));
      if (mine !== ask.current) return;
      const got = (out && out.sessions) || [];
      setSt((p) => ({ rows: before && p.rows ? p.rows.concat(got) : got, next: (out && out.next) || null, error: '' }));
    } catch (e) {
      if (mine === ask.current) setSt((p) => ({ rows: p.rows, next: p.next, error: errText(e) }));
    }
  }, [tag, allAttempts]);
  React.useEffect(() => { load(null); }, [load]);
  const entry = { entry: 'tag', run_tag: tag, title: 'Sessions · ' + tag };
  if (session) {
    return (
      <div className="lab-page-body lab-runs-home">
        <SessionPage key={session} sessionId={session} me={me} backText={'← ' + entry.title} onOpenLoop={onOpenLoop}
          onBack={() => { if (onSession) onSession(null); }} />
      </div>
    );
  }
  return (
    <div className="lab-page-body lab-runs-home">
      <RunConversationsList run={entry} mode="sessions" sessions={st.rows} waiting={[]} error={st.error} allAttempts={allAttempts}
        onAttempts={setAllAttempts} onOpen={(id) => { if (onSession) onSession(id); }} onBack={onBack}
        more={st.next} onMore={() => load(st.next)} />
    </div>
  );
}

// Which way a run opens, read from the runs already listed (`known`), else asked for once.
function RunOpen(props) {
  const { code, known } = props;
  const listed = (known || []).find((r) => r && r.code === code) || null;
  const [asked, setAsked] = React.useState({ run: null, error: '' });
  React.useEffect(() => {
    if (listed) return undefined;
    let live = true;
    setAsked({ run: null, error: '' });
    labApi.run(code).then((out) => { if (live) setAsked({ run: (out && out.run) || null, error: '' }); },
      (e) => { if (live) setAsked({ run: null, error: errText(e) }); });
    return () => { live = false; };
  }, [code, !!listed]); // eslint-disable-line react-hooks/exhaustive-deps
  const run = listed || asked.run;
  const mode = runMode(run) || (asked.error ? 'questions' : null);
  const back = () => { if (props.onQuestion) props.onQuestion(null); if (props.onSession) props.onSession(null); if (props.onRun) props.onRun(null); };
  if (!mode) return <div className="lab-page-body lab-runs-home"><p className="lab-small" role="status">{'Reading the run…'}</p></div>;
  if (mode === 'questions') {
    return <RunDetail me={props.me} code={code} onRun={props.onRun} question={props.question || null} onQuestion={props.onQuestion}
      onOpenLoop={props.onOpenLoop} focus={props.focus || null} />;
  }
  return <RunConversations me={props.me} code={code} mode={mode} run={run} session={props.session || null} onSession={props.onSession}
    question={props.question || null} onQuestion={props.onQuestion} focus={props.focus || null} onBack={back} onOpenLoop={props.onOpenLoop}
    turnAt={props.turnAt || null} />;
}

// Runs: the list of runs (or the scenarios), one run's questions beside the chosen question, or one run's conversations
// and the session opened from them. `run`, `question` and `session` are kept by the caller (R3, R3-12, a session id);
// `onRun`, `onQuestion` and `onSession` change them. `known` is the runs the caller already has (the top bar's).
export default function RunsPage({ me, run, onRun, question, onQuestion, onOpenLoop, focus, session, onSession, known, tag, onTag, turnAt }) {
  const go = onRun || (() => {});
  if (!run && tag) {
    return <TagConversations key={tag} me={me} tag={tag} session={session || null} onSession={onSession} onOpenLoop={onOpenLoop}
      onBack={() => { if (onSession) onSession(null); if (onTag) onTag(null); }} />;
  }
  if (!run && session) {
    return (
      <div className="lab-page-body lab-runs-home">
        <SessionPage key={session} sessionId={session} me={me} backText="← All runs" onOpenLoop={onOpenLoop}
          onBack={() => { if (onSession) onSession(null); }} />
      </div>
    );
  }
  if (!run) return <RunsHome me={me} onRun={(code) => { if (onQuestion) onQuestion(null); go(code); }} />;
  return <RunOpen key={run} me={me} code={run} known={known} onRun={go} question={question || null} onQuestion={onQuestion}
    onOpenLoop={onOpenLoop} focus={focus || null} session={session || null} onSession={onSession} turnAt={turnAt || null} />;
}
