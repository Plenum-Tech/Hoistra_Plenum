// TraceLabApp — Administration › Trace Lab, its own page (loaded lazily by Mount.jsx, only with the flag on).
// Tabs: Conversations (with the reviewer's verdicts, notes, comments and AI suggestions), Runs, Golden set, Model calls,
// Map, Progress (with the failure modes), Labels, Judges and Watch. State is the module's own store (store.js); actions.js and the *Actions.js files act; logic/*.js shapes
// what is drawn; wiring.js starts the reads and the r / w keys that Phase 4's tabs need. The address bar carries ?tl=<tab>&run=&conv=&turn= while the page is open and
// is scrubbed when it closes; a company switch (a superadmin's View as, or another company's account) empties
// the page and reads again; the open tab is checked for news every 45 seconds without closing anything open.
import React from 'react';
import './styles.css';
import { getActingOrg } from '../api/client.js';
import { tlStore, useStore, resetForCompany } from './store.js';
import * as act from './actions.js';
import * as gact from './goldenActions.js';
import * as tact from './taxonomyActions.js';
import * as rw from './wiring.js';
import { POLL_MS, RANGES, channelOptions, parseLink, tabsView, updateLine } from './logic/shell.js';
import { runOptions } from './logic/views.js';
import { shellNotice } from './logic/tabNotice.js';
import Conversations from './views/Conversations.jsx';
import Runs from './views/Runs.jsx';
import ModelCalls from './views/ModelCalls.jsx';
import GoldenSet from './views/GoldenSet.jsx';
import MapTab from './views/Map.jsx';
import Progress from './views/Progress.jsx';
import Labels from './views/Labels.jsx';
import Judges from './views/Judges.jsx';
import Watch from './views/Watch.jsx';
import ModeDrawer from './views/ModeDrawer.jsx';
import ModeDialogs from './views/ModeDialogs.jsx';

function TabBar({ s }) {
  const tabs = tabsView(s);
  const onKey = (e, i) => {
    if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return;
    e.preventDefault();
    const next = tabs[(i + (e.key === 'ArrowRight' ? 1 : tabs.length - 1)) % tabs.length];
    act.setTab(next.key);
    const el = typeof document !== 'undefined' ? document.getElementById('tl-tab-' + next.key) : null;
    if (el) el.focus();
  };
  return (
    <div role="tablist" aria-label="Trace Lab views" className="tl-tabs">
      {tabs.map((t, i) => (
        <button key={t.key} id={'tl-tab-' + t.key} type="button" role="tab" aria-selected={t.on ? 'true' : 'false'} tabIndex={t.on ? 0 : -1}
          className={'tl-tab' + (t.on ? ' tl-tab-on' : '')} onClick={() => act.setTab(t.key)} onKeyDown={(e) => onKey(e, i)}>{t.label}</button>
      ))}
    </div>
  );
}

function FilterBar({ s }) {
  const f = s.filters;
  const statuses = [{ key: '', label: 'All' }, { key: 'ok', label: 'Answered' }, { key: 'error', label: 'Failed' }];
  return (
    <div className="tl-filters">
      {RANGES.map((r) => (
        <button key={r.key} type="button" className={'tl-chip-btn' + (f.range === r.key ? ' tl-on' : '')} aria-pressed={f.range === r.key ? 'true' : 'false'}
          onClick={() => act.setFilter({ range: r.key })}>{r.label}</button>
      ))}
      <label className="tl-field"><span>{'Source'}</span>
        <select value={f.channel} onChange={(e) => act.setFilter({ channel: e.target.value })}>
          {channelOptions(s).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
      <label className="tl-field"><span>{'Run'}</span>
        <select value={s.run} onChange={(e) => act.setRun(e.target.value)}>
          {runOptions(s).map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
      </label>
      {statuses.map((x) => (
        <button key={x.key || 'all'} type="button" className={'tl-chip-btn' + (f.status === x.key ? ' tl-on' : '')} aria-pressed={f.status === x.key ? 'true' : 'false'}
          onClick={() => act.setFilter({ status: x.key })}>{x.label}</button>
      ))}
      <form className="tl-search" onSubmit={(e) => { e.preventDefault(); act.search(); }}>
        <input aria-label="Search questions and answers" placeholder="Search questions and answers…" value={s.qDraft} onChange={(e) => act.setQueryDraft(e.target.value)} />
        <button type="submit" className="tl-link">{'Search'}</button>
      </form>
    </div>
  );
}

// Golden set, Map, Progress, Labels, Judges and Watch have their own controls: no conversation filters on them, and Map,
// Progress, Labels, Judges and Watch refresh themselves, so the shared refresh line is for the other tabs.
const UNFILTERED = ['golden', 'map', 'progress', 'labels', 'judges', 'watch'];
const OWN_REFRESH = ['map', 'progress', 'labels', 'judges', 'watch'];

// The page as drawn from one state: exported so the tests can render it without effects.
// `chrome` is Hoistra's frame (crumb, heading, tab bar); the standalone lab (src/lab) draws its own and passes false.
export function Shell({ s, now, company, chrome = true }) {
  const notice = shellNotice(s);
  const polled = s.tab === 'conversations' || s.tab === 'runs';
  return (
    <div className="tl-page">
      {chrome ? <div className="tl-crumbs"><span>{'Administration'}</span><span>{'/'}</span><span>{'Trace Lab'}</span></div> : null}
      {chrome ? <div className="tl-head"><h2>{'Trace Lab'}</h2></div> : null}
      {chrome ? <TabBar s={s} /> : null}
      {notice ? <div className="tl-notice" role="status">{notice}</div> : (
        <>
          {UNFILTERED.indexOf(s.tab) < 0 ? <FilterBar s={s} /> : null}
          {OWN_REFRESH.indexOf(s.tab) < 0 ? (
            <div className="tl-update">
              {polled ? <span aria-live="polite">{updateLine(s)}</span> : null}
              <button type="button" className="tl-link" onClick={act.refresh}>{s.poll.error ? 'Retry' : 'Refresh now'}</button>
            </div>
          ) : null}
          {s.tab === 'conversations' ? <Conversations s={s} now={now} /> : null}
          {s.tab === 'runs' ? <Runs s={s} now={now} /> : null}
          {s.tab === 'models' ? <ModelCalls s={s} now={now} /> : null}
          {s.tab === 'golden' ? <GoldenSet s={s} now={now} /> : null}
          {s.tab === 'map' ? <MapTab s={s} /> : null}
          {s.tab === 'progress' ? <Progress s={s} /> : null}
          {s.tab === 'labels' ? <Labels s={s} /> : null}
          {s.tab === 'judges' ? <Judges s={s} now={now} /> : null}
          {s.tab === 'watch' ? <Watch s={s} now={now} company={company} /> : null}
          <ModeDrawer s={s} />
          <ModeDialogs s={s} />
        </>
      )}
    </div>
  );
}

export default function TraceLabApp({ vals, chrome = true }) {
  const s = useStore(tlStore);
  const scope = (getActingOrg() || '') + '|' + ((vals && vals.tenant) || '');
  const scopeRef = React.useRef(null);

  // Open: take the ?tl= link, read the open tab, start the 45-second check. Close: stop it, empty the page (the
  // next company must never see this one's rows) and take the link out of the address bar.
  React.useEffect(() => {
    scopeRef.current = scope;
    act.applyLink(parseLink(window.location.search));
    act.loadTab();
    const timer = setInterval(() => { act.poll(false); }, POLL_MS);
    return () => { clearInterval(timer); gact.leavePage(); resetForCompany(); act.scrubUrl(); };
  }, []);

  // The r / w verdict keys while the page is open; closing it takes them away and stops the 30-second annotation read.
  React.useEffect(() => rw.attach(), []);

  // The failure-mode work keeps one timer of its own (the proposal runs, every 5 seconds): closing the page, or
  // another company, stops it before anything is emptied. (Declared before the switch effect below, so it runs first.)
  React.useEffect(() => () => { tact.stopPolls(); }, []);
  React.useEffect(() => { tact.stopPolls(); }, [scope]);

  // A company switch while the page is open: empty it and read the new company's.
  React.useEffect(() => {
    if (scopeRef.current === null || scopeRef.current === scope) return;
    scopeRef.current = scope;
    gact.leavePage();
    resetForCompany();
    act.loadTab();
  }, [scope]);

  // Map and Progress read their figures when their tab opens (and again for another company); the open
  // conversation's verdicts and comments are read once it is on screen, again for each answer shown.
  React.useEffect(() => { rw.loadSamplingFor(s); }, [s.tab, s.seq]);
  React.useEffect(() => { rw.loadAnnotationsFor(s); }, [s.tab, s.seq, s.convKey, s.turnId, s.conv.data, s.conv.loading]);

  // The address bar follows the tab, run, conversation and answer.
  React.useEffect(() => { act.syncUrl(); }, [s.tab, s.run, s.convKey, s.turnId]);

  return <Shell s={s} now={Date.now()} company={(vals && vals.tenant) || ''} chrome={chrome} />;
}
