// Runs — the review loop's runs (22 Oct 2026 feedback; laid out like the Cartwheel Trace Lab's "Scenario runs", in
// Hoistra's look). Without a run: every run the person may see as a card (its code and title, synthetic or live with whose
// questions and which day, where it came from, when its questions were asked, how far the review has got), a search, a
// filter, "Sync now" (the chat's newer live answers become questions, one run per person per day) and, for curators
// (company admins, the platform team, the consultant), an Excel / CSV import and a run typed on the page. With a run: its
// questions on the left (code, question, role, expected answer, answers, notes) and the chosen question on the right
// (QuestionPage.jsx).
import React from 'react';
import { labApi } from './labApi.js';
import { errText } from './logic/labelling.js';
import QuestionPage, { fmtDay, fmtRange, fmtWhen, isCurator, plural } from './QuestionPage.jsx';
import './runs.css';

const PAGE = 50;
const QUESTIONS_MAX = 2000;
const QUESTION_MAX = 4000;
const KINDS = [['', 'All'], ['synthetic', 'Synthetic'], ['production', 'Live']];
export const SOURCE_TEXT = Object.freeze({ coding_agent: 'coding agent', excel: 'Excel', manual: 'manual', sync: 'synced' });

// One run as its card says it.
export function runView(r) {
  const x = r || {};
  const live = x.kind === 'production';
  const n = (k) => Number(x[k]) || 0;
  return {
    code: x.code || (x.run_no ? 'R' + x.run_no : '—'), title: x.title || '', live,
    kindText: live ? ['live', x.owner_email || 'unknown person', fmtDay(x.run_day)].filter(Boolean).join(' · ') : 'synthetic',
    source: SOURCE_TEXT[x.source] || x.source || '',
    range: fmtRange(x.first_at, x.last_at) || (x.created_at ? 'Created ' + fmtWhen(x.created_at) : ''),
    counts: [plural(n('n_questions'), 'question', 'questions'), n('n_traced') + ' with answers', n('n_expected') + ' with expected',
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

export function syncText(out) {
  const n = Number(out && out.new_questions) || 0;
  const r = Number(out && out.new_runs) || 0;
  if (!n) return 'No new questions: every live answer is already here.';
  return plural(n, 'new question', 'new questions') + (r ? ' in ' + plural(r, 'new run', 'new runs') : '')
    + (out && out.more ? '. There are more: press Sync now again.' : '.');
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
        {rows.map((r) => (
          <li key={r.code}>
            <button type="button" className="lab-run-card" onClick={() => open(r.code)}>
              <span className="lab-run-top">
                <strong className="lab-run-code">{r.code}</strong>
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

function RunsHome({ me, onRun }) {
  const curator = isCurator(me);
  const [f, setF] = React.useState({ q: '', kind: '' });
  const [applied, setApplied] = React.useState({ q: '', kind: '' });
  const [list, setList] = React.useState({ runs: [], total: 0, loading: true, error: '' });
  const [sync, setSync] = React.useState({ busy: false, msg: '', error: '' });
  const [panel, setPanel] = React.useState(null);
  const ask = React.useRef(0);

  const load = React.useCallback(async (g, offset) => {
    const mine = (ask.current += 1);
    setApplied(g);
    setList((p) => Object.assign({}, p, { loading: true, error: '' }));
    try {
      const out = await labApi.runs({ q: g.q.trim() || undefined, kind: g.kind || undefined, limit: PAGE, offset: offset || undefined });
      if (mine !== ask.current) return;
      const runs = (out && out.runs) || [];
      setList((p) => ({ runs: offset ? p.runs.concat(runs) : runs, total: (out && out.total) || 0, loading: false, error: '' }));
    } catch (e) {
      if (mine === ask.current) setList((p) => Object.assign({}, p, { loading: false, error: errText(e) }));
    }
  }, []);
  React.useEffect(() => { load({ q: '', kind: '' }, 0); }, [load]);

  const pickKind = (kind) => { const g = Object.assign({}, f, { kind }); setF(g); load(g, 0); };
  const runSync = async () => {
    setSync({ busy: true, msg: '', error: '' });
    try {
      const out = await labApi.syncRuns();
      setSync({ busy: false, msg: syncText(out), error: '' });
      load(applied, 0);
    } catch (e) {
      setSync({ busy: false, msg: '', error: errText(e) });
    }
  };
  const toggle = (which) => setPanel(panel === which ? null : which);
  const filtered = !!(applied.q.trim() || applied.kind);

  return (
    <div className="lab-page-body lab-runs-home">
      <div className="lab-page-head lab-head-row">
        <div>
          <h1>{'Runs'}</h1>
          <p className="lab-small lab-big">{'Synthetic batches of questions, and each person’s live questions by day. Open a run to review its questions.'}</p>
        </div>
        <div className="lab-row lab-runs-actions">
          <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={runSync} disabled={sync.busy}>{sync.busy ? 'Syncing…' : 'Sync now'}</button>
          {curator ? <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" aria-expanded={panel === 'import' ? 'true' : 'false'}
            onClick={() => toggle('import')}>{'Import Excel/CSV'}</button> : null}
          {curator ? <button type="button" className="lab-btn lab-btn-sm" aria-expanded={panel === 'new' ? 'true' : 'false'}
            onClick={() => toggle('new')}>{'New run'}</button> : null}
        </div>
      </div>
      {sync.msg ? <p className="lab-runs-note" role="status">{sync.msg}</p> : null}
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
    </div>
  );
}

function RunDetail({ me, code, onRun, question, onQuestion }) {
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
        {question ? <QuestionPage key={question} code={question} me={me} onChanged={() => load(true)} /> : (
          <div className="lab-empty">
            <h2>{'Pick a question'}</h2>
            <p className="lab-small">{'Choose a question on the left to see its expected answer and how its answer was made. Click a step to select it; double-click it or select text in it to write a note.'}</p>
          </div>
        )}
      </div>
    </div>
  );
}

// Runs: the list of runs, or one run's questions beside the chosen question. `run` and `question` are codes (R3, R3-12)
// the caller keeps; `onRun(code | null)` and `onQuestion(code | null)` change them.
export default function RunsPage({ me, run, onRun, question, onQuestion }) {
  const go = onRun || (() => {});
  if (!run) return <RunsHome me={me} onRun={(code) => { if (onQuestion) onQuestion(null); go(code); }} />;
  return <RunDetail me={me} code={run} onRun={go} question={question || null} onQuestion={onQuestion} />;
}
