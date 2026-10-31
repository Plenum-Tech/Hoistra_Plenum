// LabApp — Trace Lab as its own place (20 Oct 2026; runs, 22 Oct). Opening: a stored lab session is refreshed, else
// Sign in (the Hoistra email and password; an unverified email asks for its code). There is no company level: the
// platform team and a consultant see every company's runs, everyone else their own company's. The service says who the
// person is in Trace Lab (/lab/me): company admins, the platform team and a consultant go straight in; anyone else waits
// until a company admin gives them a Trace Lab role. The workspace: Runs (Runs -> Questions -> one answer's steps),
// Labels (failure modes per run), Progress, Notes and, for admins, People. A run and question in view are kept in the
// address (?run=R3&q=R3-12), so a link opens the same place.
import React from 'react';
import { authApi } from '../api/auth.js';
import { setActingOrg } from '../api/client.js';
import { createLabAuth } from './authFlow.js';
import { labApi } from './labApi.js';
import { ROLE_TEXT, errText } from './logic/labelling.js';
import RunsPage from './Runs.jsx';
import LabelsView from './LabelsView.jsx';
import ProgressView from './ProgressView.jsx';
import Notes from './Notes.jsx';
import People from './People.jsx';

const COMPANY_KEY = 'tracelab.company.v1';
const store = () => { try { return window.localStorage; } catch (e) { return null; } };
const locks = () => { try { return navigator.locks || null; } catch (e) { return null; } };

// The workspace menu. People is for company admins (and the platform team).
export function navFor(me) {
  const items = [['runs', 'Runs'], ['labels', 'Labels'], ['progress', 'Progress'], ['notes', 'Notes']];
  if (me && me.manages) items.push(['people', 'People']);
  return items;
}

// The run (R3) of a question code (R3-12).
export const runOf = (code) => { const m = /^\s*(R\d+)-\d+\s*$/i.exec(String(code || '')); return m ? m[1].toUpperCase() : null; };

function readPlace() {
  try {
    const q = new URLSearchParams(window.location.search);
    const run = (q.get('run') || '').toUpperCase() || null;
    const question = (q.get('q') || '').toUpperCase() || null;
    return { run: run || runOf(question), question, view: q.get('view') || null };
  } catch (e) { return { run: null, question: null, view: null }; }
}
function keepPlace(view, run, question) {
  try {
    const q = new URLSearchParams();
    if (view && view !== 'runs') q.set('view', view);
    if (run) q.set('run', run);
    if (question) q.set('q', question);
    const s = q.toString();
    window.history.replaceState(null, '', window.location.pathname + (s ? '?' + s : ''));
  } catch (e) { /* the address is a convenience */ }
}

// The run picker and Sync now in the top bar: every run the person may see, newest first.
export function RunPicker({ runs, run, onRun, onSync, syncing, synced }) {
  return (
    <div className="lab-row lab-runbar">
      <label className="lab-visually-hidden" htmlFor="lab-run-pick">{'Run'}</label>
      <select id="lab-run-pick" className="lab-run-pick" value={run || ''} onChange={(e) => onRun(e.target.value || null)}>
        <option value="">{'All runs'}</option>
        {(runs || []).map((r) => <option key={r.code} value={r.code}>{r.code + ' · ' + r.title + ' · ' + (r.n_questions || 0) + (r.n_questions === 1 ? ' question' : ' questions')}</option>)}
      </select>
      <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onSync} disabled={syncing}>{syncing ? 'Syncing…' : 'Sync now'}</button>
      {synced ? <span className="lab-small" role="status">{synced}</span> : null}
    </div>
  );
}


export function Mark({ size }) {
  const n = size || 24;
  return (
    <span className="lab-mark">
      <svg width={n} height={n} viewBox="0 0 28 28" fill="none" aria-hidden="true">
        <path d="M10 3h8M12 3v7l-6.5 11.5A2.3 2.3 0 0 0 7.5 25h13a2.3 2.3 0 0 0 2-3.5L16 10V3" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
        <path d="M8.5 18h11" stroke="var(--color-accent)" strokeWidth="1.8" strokeLinecap="round" />
      </svg>
      <span>{'Trace Lab'}</span>
    </span>
  );
}

export function SignIn({ busy, error, onSubmit, code, onCode, email: initialEmail }) {
  const [email, setEmail] = React.useState(initialEmail || '');
  const [password, setPassword] = React.useState('');
  const [digits, setDigits] = React.useState('');
  return (
    <div className="lab-signin">
      <section className="lab-pitch">
        <div><Mark size={28} /><div className="lab-by">{'by Hoistra'}</div></div>
        <div>
          <h1>{'Check the chat’s answers before your customers do.'}</h1>
          <p>{'Upload questions with known answers, review what the chat said, group the mistakes by cause, and watch them go away.'}</p>
        </div>
        <ol className="lab-loop" aria-label="The loop">{['check', 'explain', 'group', 'fix', 'watch'].map((w) => <li key={w}>{w}</li>)}</ol>
      </section>
      <section className="lab-form-side">
        {code ? (
          <form className="lab-form" onSubmit={(e) => { e.preventDefault(); onCode(email, digits); }}>
            <h2>{'Check your email'}</h2>
            <p className="lab-small">{'We sent a code to ' + email + '. Enter it to finish signing in.'}</p>
            <label className="lab-field"><span>{'Code'}</span><input inputMode="numeric" autoComplete="one-time-code" value={digits} onChange={(e) => setDigits(e.target.value)} /></label>
            <button type="submit" className="lab-btn" disabled={busy}>{busy ? 'Checking…' : 'Confirm and sign in'}</button>
            {error ? <div role="alert" className="lab-error">{error}</div> : null}
          </form>
        ) : (
          <form className="lab-form" onSubmit={(e) => { e.preventDefault(); onSubmit(email, password); }}>
            <h2>{'Sign in'}</h2>
            <p className="lab-small">{'Use your Hoistra email and password. There is no separate Trace Lab account.'}</p>
            <label className="lab-field"><span>{'Email'}</span><input type="email" autoComplete="username" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
            <label className="lab-field">
              <span className="lab-field-row">{'Password'}<a href="/" className="lab-link-small">{'Forgot it? Reset in Hoistra'}</a></span>
              <input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} required />
            </label>
            <button type="submit" className="lab-btn" disabled={busy}>{busy ? 'Signing in…' : 'Sign in to Trace Lab'}</button>
            {error ? <div role="alert" className="lab-error">{error}</div> : null}
            <p className="lab-small">{'For company admins and the Hoistra platform team. Signing in here does not sign you in or out of Hoistra.'}</p>
            <p className="lab-small lab-sep">{'Looking for the Hoistra app? '}<a href="/">{'Open Hoistra'}</a></p>
          </form>
        )}
      </section>
    </div>
  );
}

export function Waiting({ me, onSignOut, onRetry }) {
  const removed = me && me.status === 'removed';
  return (
    <div className="lab-center">
      <div className="lab-narrow">
        <Mark />
        <h1>{removed ? 'Your access to Trace Lab was turned off' : 'You do not have access to Trace Lab'}</h1>
        <p className="lab-small lab-big">{removed
          ? 'Your company admin turned off your access. Ask them if you think this is a mistake.'
          : 'You signed in as ' + ((me && me.email) || 'this account') + '. Trace Lab is open only to people your company admin chooses. Ask them to turn on Trace Lab for you in Hoistra, under Users & access.'}</p>
        <div className="lab-row">
          {removed ? null : <button type="button" className="lab-btn" onClick={onRetry}>{'Check again'}</button>}
          <button type="button" className="lab-btn lab-btn-quiet" onClick={onSignOut}>{'Sign out'}</button>
        </div>
      </div>
    </div>
  );
}

export function Denied({ user, onSignOut }) {
  return (
    <div className="lab-center">
      <div className="lab-narrow">
        <Mark />
        <h1>{'Trace Lab is for company admins and the platform team'}</h1>
        <p className="lab-small lab-big">{'You signed in as ' + ((user && user.email) || 'this account') + '. Ask your company admin for access, or carry on in Hoistra.'}</p>
        <div className="lab-row"><a className="lab-btn" href="/">{'Open Hoistra'}</a><button type="button" className="lab-btn lab-btn-quiet" onClick={onSignOut}>{'Sign out'}</button></div>
      </div>
    </div>
  );
}

export function Picker({ companies, error, chosen, onChoose, onCancel, companiesOnly }) {
  const own = (companies || []).map((c) => ({ id: c.id, name: c.name, what: 'Questions, answers and labels for this company.' }));
  const places = companiesOnly ? own
    : [{ id: null, name: 'Platform view', what: 'Accept proposed failure modes, build and freeze judges, start monitoring, see evaluation spend.' }].concat(own);
  const [pick, setPick] = React.useState(chosen ? chosen.id : (places[companiesOnly ? 0 : 1] ? places[companiesOnly ? 0 : 1].id : null));
  React.useEffect(() => { if (pick === null && places[0] && companiesOnly) setPick(places[0].id); }, [companies]);
  const current = places.find((p) => p.id === pick) || places[0];
  return (
    <div className="lab-center lab-top">
      <div className="lab-wide">
        <h1>{'Where do you want to work?'}</h1>
        <p className="lab-small lab-big">{companiesOnly ? 'You are on the platform team, so you can label in any company. Change it any time from the top bar.' : 'You are on the platform team, so you can work inside any company, or stay in the platform view. Change it any time from the top bar.'}</p>
        {error ? <div role="alert" className="lab-error">{'Could not read the companies: ' + error}</div> : null}
        {!companies && !error ? <p className="lab-small">{'Reading the companies…'}</p> : null}
        <div className="lab-places" role="radiogroup" aria-label="Where to work">
          {places.map((p) => (
            <button key={String(p.id)} type="button" role="radio" aria-checked={p.id === pick ? 'true' : 'false'} className="lab-place" onClick={() => setPick(p.id)}>
              <span><strong>{p.name}</strong><span className="lab-small">{p.what}</span></span>
            </button>
          ))}
        </div>
        <div className="lab-row">
          <button type="button" className="lab-btn" disabled={!current} onClick={() => current && onChoose({ id: current.id, name: current.name })}>{current ? 'Open ' + current.name : 'Choose a company'}</button>
          {onCancel ? <button type="button" className="lab-btn lab-btn-quiet" onClick={onCancel}>{'Cancel'}</button> : null}
        </div>
      </div>
    </div>
  );
}

export function Workspace({ user, me, view, onView, onSignOut }) {
  const wide = !!(me && me.all_companies);
  const company = wide ? 'All companies' : ((user && user.organization_name) || 'Your company');
  const items = navFor(me);
  const current = items.some(([k]) => k === view) ? view : 'runs';
  const start = React.useRef(readPlace());
  const [run, setRun] = React.useState(start.current.run);
  const [question, setQuestion] = React.useState(start.current.question);
  const [runs, setRuns] = React.useState(null);
  const [syncing, setSyncing] = React.useState(false);
  const [synced, setSynced] = React.useState('');
  const [stamp, setStamp] = React.useState(0);
  const loadRuns = React.useCallback(() => labApi.runs({ limit: 200 })
    .then((out) => setRuns((out && out.runs) || [])).catch(() => setRuns([])), []);
  React.useEffect(() => { loadRuns(); }, [loadRuns]);
  React.useEffect(() => { keepPlace(current, run, question); }, [current, run, question]);
  const pickRun = (code) => { setRun(code); setQuestion(null); };
  const openQuestion = (code) => { const r = runOf(code); if (!r) return; setRun(r); setQuestion(code); onView('runs'); };
  const sync = async () => {
    setSyncing(true); setSynced('');
    try {
      const out = await labApi.syncRuns();
      const n = (out && out.new_questions) || 0;
      setSynced(n ? n + (n === 1 ? ' new question' : ' new questions') + (out.more ? ', more to come: sync again' : '') : 'Up to date');
      await loadRuns();
      setStamp((x) => x + 1);
    } catch (e) {
      setSynced('Sync failed: ' + errText(e));
    } finally {
      setSyncing(false);
    }
  };
  return (
    <div className="lab-shell">
      <div className="lab-head">
        <header className="lab-bar">
          <div className="lab-row">
            <Mark size={22} />
            <span className="lab-company">{company}</span>
            <RunPicker runs={runs} run={run} onRun={pickRun} onSync={sync} syncing={syncing} synced={synced} />
          </div>
          <div className="lab-row lab-bar-right">
            <span>{(user && user.email) + (me && me.role ? ' · ' + (ROLE_TEXT[me.role] || me.role) : '')}</span>
            <button type="button" className="lab-btn lab-btn-quiet lab-btn-sm" onClick={onSignOut}>{'Sign out'}</button>
          </div>
        </header>
        <nav className="lab-tabs" aria-label="Trace Lab">
          {items.map(([key, label]) => (
            <button key={key} type="button" className={'lab-tab' + (current === key ? ' lab-on' : '')} aria-current={current === key ? 'page' : undefined}
              onClick={() => onView(key)}>{label}</button>
          ))}
        </nav>
      </div>
      <main className={'lab-main' + (current === 'runs' && run ? ' lab-main-split' : '')} key={'v' + stamp}>
        {current === 'runs' ? <RunsPage me={me} run={run} onRun={pickRun} question={question} onQuestion={setQuestion} /> : null}
        {current === 'labels' ? <LabelsView me={me} run={run || (runs && runs[0] ? runs[0].code : null)} onOpenQuestion={openQuestion} /> : null}
        {current === 'progress' ? <ProgressView me={me} run={run} /> : null}
        {current === 'notes' ? <Notes me={me} onOpenQuestion={openQuestion} /> : null}
        {current === 'people' ? <People me={me} /> : null}
      </main>
    </div>
  );
}

export default function LabApp() {
  const [phase, setPhase] = React.useState('booting');   // booting | signin | code | checking | waiting | work
  const [user, setUser] = React.useState(null);
  const [busy, setBusy] = React.useState(false);
  const [error, setError] = React.useState('');
  const [email, setEmail] = React.useState('');
  // A shared link (?view=labels&run=R3) opens on that screen; a plain /lab/ opens on Runs.
  const [view, setView] = React.useState(() => readPlace().view || 'runs');
  const [me, setMe] = React.useState(null);
  const [meError, setMeError] = React.useState('');
  const authRef = React.useRef(null);
  if (!authRef.current) {
    authRef.current = createLabAuth({ api: authApi, storage: store(), locks: locks(), onChange: (u) => { setUser(u); if (!u) setPhase('signin'); } });
  }
  const auth = authRef.current;

  // Who the person is in Trace Lab for the company in view: in, or waiting for their company admin.
  const check = React.useCallback(async () => {
    setPhase('checking'); setMeError('');
    try {
      const out = await labApi.me();
      const who = (out && out.me) || null;
      setMe(who);
      setPhase(who && who.status === 'active' ? 'work' : 'waiting');
    } catch (e) {
      setMeError(errText(e));
      setPhase('checking');
    }
  }, []);

  // No company level any more: the platform team acts for no company (every company), so nothing is kept or picked.
  const route = React.useCallback(() => {
    try { store().removeItem(COMPANY_KEY); } catch (e) { /* nothing kept */ }
    setActingOrg(null); check();
  }, [check]);

  React.useEffect(() => {
    auth.boot().then((ok) => { if (ok) route(auth.user()); else setPhase('signin'); });
  }, []);


  const signIn = async (em, pw) => {
    setBusy(true); setError(''); setEmail(em);
    const out = await auth.signIn(em, pw);
    setBusy(false);
    if (out.needsCode) { setPhase('code'); return; }
    if (out.error) { setError(out.error); return; }
    route(auth.user());
  };
  const verify = async (em, code) => {
    setBusy(true); setError('');
    const out = await auth.verify(em, code);
    setBusy(false);
    if (out.error) { setError(out.error); return; }
    route(auth.user());
  };
  const signOut = async () => { await auth.signOut(); setActingOrg(null); setPhase('signin'); };

  if (phase === 'booting') return <div className="lab-center"><p className="lab-small">{'Opening Trace Lab…'}</p></div>;
  if (phase === 'signin' || phase === 'code') {
    return <SignIn busy={busy} error={error} email={email} code={phase === 'code'} onSubmit={signIn} onCode={verify} />;
  }
  if (phase === 'checking') {
    return (
      <div className="lab-center"><div className="lab-narrow">
        {meError ? <><div role="alert" className="lab-error">{'Could not open Trace Lab: ' + meError}</div>
          <div className="lab-row"><button type="button" className="lab-btn" onClick={check}>{'Try again'}</button>
            <button type="button" className="lab-btn lab-btn-quiet" onClick={signOut}>{'Sign out'}</button></div></>
          : <p className="lab-small">{'Opening Trace Lab…'}</p>}
      </div></div>
    );
  }
  if (phase === 'waiting') return <Waiting me={me} onSignOut={signOut} onRetry={check} />;
  return <Workspace user={user} me={me} view={view} onView={setView} onSignOut={signOut} />;
}
