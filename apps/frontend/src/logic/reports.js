// reports — a custom report is a pinned prompt re-run on a cadence.
//
// Built from a session: the session's question becomes the report's prompt, the cadence says
// how often it is asked again, and each run is the orchestrator's answer to that prompt on a
// fresh thread. This is the Plenum shell's "pinned run" — the plenum_cafm.pinned_run table
// has no route yet, so the reports live in this browser's localStorage and are re-run by the
// app while it is open. The page says so.
//
// The pure functions are tested in test/reports.test.mjs; the methods at the bottom are mixed
// into HoistraLogic.prototype and `this` is the controller.
import { deepAgentsApi } from '../api/deepAgents.js';
import { extractComplianceAnswer } from './complianceLive.js';
import { errorFromAnswer } from './chat.js';
import { fmtDateTime } from './homeLive.js';

export const REPORTS_KEY = 'hoistra.reports.v1';
export const MAX_RUNS = 3;
const TICK_MS = 30000;
const T_RUN = 180000;

// Interval-based and clock-based cadences in one list, since the user thinks of them the
// same way — "how often does this re-read the graph".
export const CADENCES = [
  { label: 'Refresh every 30 minutes', badge: '30 min', everyMs: 30 * 60000 },
  { label: 'Refresh every 1 hour', badge: '1 hr', everyMs: 60 * 60000 },
  { label: 'Refresh every 6 hours', badge: '6 hr', everyMs: 6 * 3600000 },
  { label: 'Refresh every 12 hours', badge: '12 hr', everyMs: 12 * 3600000 },
  { label: 'Refresh every 24 hours', badge: '24 hr', everyMs: 24 * 3600000 },
  { label: 'Refresh daily at 02:00', badge: 'Daily', daily: '02:00' },
  { label: 'Refresh on chosen days', badge: 'Days', pickDays: true }
];
export const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const DEFAULT_CAD = 1;

const cadOf = (cad) => CADENCES[cad && Number.isInteger(cad.i) ? cad.i : -1] || CADENCES[DEFAULT_CAD];
const daysOf = (cad) => ((cad && Array.isArray(cad.days)) ? cad.days : []).filter((d) => Number.isInteger(d) && d >= 0 && d <= 6).sort();

// 'HH:MM' → [h, m]; anything unreadable is 14:00.
function parseTime(t) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(t || '').trim());
  if (!m) return [14, 0];
  const h = parseInt(m[1], 10), mi = parseInt(m[2], 10);
  if (h > 23 || mi > 59) return [14, 0];
  return [h, mi];
}

// A day-picked cadence reads back as the days and time actually chosen.
export function cadenceLabel(cad) {
  const c = cadOf(cad);
  if (!c.pickDays) return c.label;
  const d = daysOf(cad).map((i) => DAYS[i]);
  const [h, m] = parseTime(cad && cad.time);
  const hm = String(h).padStart(2, '0') + ':' + String(m).padStart(2, '0');
  return d.length
    ? 'Refresh ' + (d.length === 7 ? 'every day' : d.join(', ')) + ' at ' + hm
    : 'Refresh on chosen days — pick at least one';
}

export function cadenceBadge(cad) {
  const c = cadOf(cad);
  if (!c.pickDays) return c.badge;
  const d = daysOf(cad).map((i) => DAYS[i]);
  return d.length === 7 ? 'Daily' : d.length ? d.join(' · ') : 'Days';
}

// When the next run falls, strictly after `from`, in local time. null when a days cadence
// has no days.
export function nextRunAt(cad, from) {
  const c = cadOf(cad);
  const f = typeof from === 'number' ? from : Date.now();
  if (c.everyMs) return f + c.everyMs;
  const d0 = new Date(f);
  if (c.daily) {
    const [h, m] = parseTime(c.daily);
    const cand = new Date(d0.getFullYear(), d0.getMonth(), d0.getDate(), h, m, 0, 0);
    if (cand.getTime() <= f) cand.setDate(cand.getDate() + 1);
    return cand.getTime();
  }
  const days = daysOf(cad);
  if (!days.length) return null;
  const [h, m] = parseTime(cad && cad.time);
  for (let off = 0; off <= 7; off++) {
    const cand = new Date(d0.getFullYear(), d0.getMonth(), d0.getDate() + off, h, m, 0, 0);
    if (cand.getTime() > f && days.indexOf(cand.getDay()) > -1) return cand.getTime();
  }
  return null;
}

export function makeReport(o) {
  const now = o.now === undefined ? Date.now() : o.now;
  return {
    key: String(o.key),
    name: String(o.name || 'Untitled report'),
    prompt: String(o.prompt || ''),
    sessionId: o.sessionId || null,
    page: o.page || 'Home',
    cad: Object.assign({ i: DEFAULT_CAD, days: [], time: '14:00' }, o.cad || {}),
    createdAt: now,
    lastRunAt: null,
    lastTriedAt: null,
    // Due at once: the first refresh runs as soon as the report exists.
    nextRunAt: now,
    status: 'pending',
    error: '',
    runs: []
  };
}

const uniq = (xs) => (xs || []).filter((x, i, a) => x && a.indexOf(x) === i);
const cell = (v) => String(v === null || v === undefined ? '' : v).replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();

// What the orchestrator is told about a scheduled run.
export function reportContext(rep) {
  return 'This question is a saved Hoistra custom report named “' + rep.name + '”, ' +
    cadenceLabel(rep.cad).replace(/^Refresh /, 're-run ') + '. It was pinned from the ' + rep.page +
    ' page. Answer it as a standalone report against the current data: lead with the findings, then the figures ' +
    'and the records behind them. Do not ask follow-up questions.';
}

// The export. Markdown, because the answers are markdown; a structured compliance answer is
// flattened into headings, a figure list and a certificate table.
export function reportMarkdown(rep, run) {
  const lines = ['# ' + rep.name, ''];
  lines.push('_Custom report · built from the session “' + rep.prompt + '” · ' + cadenceLabel(rep.cad) + '_');
  if (!run) {
    lines.push('', 'This report has not run yet.');
    return lines.join('\n') + '\n';
  }
  lines.push('_Refreshed ' + fmtDateTime(run.at ? new Date(run.at).toISOString() : null) +
    (typeof run.ms === 'number' ? ' · ' + Math.round(run.ms / 1000) + ' s' : '') +
    (uniq(run.calls).length ? ' · tools: ' + uniq(run.calls).join(', ') : '') + '_', '');
  if (run.error) {
    lines.push('**This refresh did not complete.** ' + run.error);
    return lines.join('\n') + '\n';
  }
  const r = run.rich;
  if (r) {
    if (r.narrative) lines.push(String(r.narrative), '');
    if ((r.kpis || []).length) {
      lines.push('## Key figures', '');
      r.kpis.forEach((k) => lines.push('- **' + cell(k.label) + '** ' + cell(k.count) + (k.unit ? ' ' + cell(k.unit) : '') + (k.sublabel ? ' — ' + cell(k.sublabel) : '')));
      lines.push('');
    }
    if ((r.actions || []).length) {
      lines.push('## Priority actions', '');
      r.actions.forEach((a) => lines.push('- ' + [a.severity, a.scope].filter(Boolean).map(cell).join(' · ') + (a.severity || a.scope ? ': ' : '') + cell(a.title)));
      lines.push('');
    }
    (r.groups || []).forEach((g) => {
      lines.push('### ' + cell(g.owner) + (g.scope ? ' · ' + cell(g.scope) : ''), '');
      if (g.headline) lines.push(cell(g.headline), '');
      (g.points || []).forEach((p) => lines.push('- ' + cell(p)));
      if ((g.points || []).length) lines.push('');
    });
    if ((r.insights || []).length) {
      lines.push('## Insights', '');
      r.insights.forEach((x) => lines.push('- ' + cell(x.text || x)));
      lines.push('');
    }
    if ((r.certificates || []).length) {
      lines.push('## Certificates in scope', '', '| Certificate | Holder | Scope | Status |', '| --- | --- | --- | --- |');
      r.certificates.forEach((c) => lines.push('| ' + cell(c.name) + (c.reason ? ' (' + cell(c.reason) + ')' : '') + ' | ' + cell(c.company) + ' | ' + cell(c.scope) + ' | ' + cell(c.status) + ' |'));
      lines.push('');
    }
  }
  if (run.answer) lines.push(String(run.answer), '');
  return lines.join('\n').replace(/\n{3,}/g, '\n\n') + '\n';
}

// ── storage ──────────────────────────────────────────────────────────────────
const store = () => {
  try { return (typeof window !== 'undefined' && window.localStorage) || null; } catch (e) { return null; }
};

export function loadReports(storage) {
  const st = storage || store();
  if (!st) return [];
  let raw = null;
  try { raw = st.getItem(REPORTS_KEY); } catch (e) { return []; }
  if (!raw) return [];
  let d = null;
  try { d = JSON.parse(raw); } catch (e) { return []; }
  if (!Array.isArray(d)) return [];
  const out = [];
  d.forEach((r) => {
    if (!r || typeof r !== 'object' || typeof r.key !== 'string' || !r.key) return;
    const rep = makeReport({ key: r.key, name: r.name, prompt: r.prompt, sessionId: r.sessionId, page: r.page, cad: r.cad, now: Number(r.createdAt) || Date.now() });
    rep.lastRunAt = Number(r.lastRunAt) || null;
    rep.lastTriedAt = Number(r.lastTriedAt) || null;
    rep.nextRunAt = Number(r.nextRunAt) || rep.createdAt;
    rep.runs = (Array.isArray(r.runs) ? r.runs : []).filter((x) => x && typeof x === 'object').slice(0, MAX_RUNS);
    rep.error = typeof r.error === 'string' ? r.error : '';
    // A run that was in flight when the page closed never finished: back to pending, and
    // due, so the scheduler picks it up.
    rep.status = r.status === 'ready' || r.status === 'error' ? r.status : 'pending';
    if (r.status === 'running') rep.nextRunAt = Math.min(rep.nextRunAt, Date.now());
    out.push(rep);
  });
  return out;
}

export function saveReports(list, storage) {
  const st = storage || store();
  if (!st) return false;
  const l = (list || []).map((r) => Object.assign({}, r, { runs: (r.runs || []).slice(0, MAX_RUNS) }));
  try {
    st.setItem(REPORTS_KEY, JSON.stringify(l));
    return true;
  } catch (e) {
    // Too big: keep one run per report, then give up quietly.
    try {
      st.setItem(REPORTS_KEY, JSON.stringify(l.map((r) => Object.assign({}, r, { runs: (r.runs || []).slice(0, 1) }))));
      return true;
    } catch (e2) { return false; }
  }
}

// ── controller ───────────────────────────────────────────────────────────────
export const reportsMethods = {
  rpPatch(key, patch) {
    this.setState((p) => ({
      reports: (p.reports || []).map((r) => (r.key === key ? Object.assign({}, r, typeof patch === 'function' ? patch(r) : patch) : r))
    }));
  },

  // The navigator menu's Create report. Source = a chat session (its question is the prompt).
  rpCreate() {
    const s = this.state;
    const chats = (s.sessions || []).filter((q) => q.kind === 'chat');
    const src = chats.find((q) => q.id === s.reportSrcId) || chats[0] || null;
    if (!src) return this.flash('Ask something first — a report is built from a session’s question.');
    const cad = { i: s.reportCad, days: (s.reportDays || []).slice(), time: s.reportTime || '14:00' };
    if ((CADENCES[cad.i] || {}).pickDays && !cad.days.length) return this.flash('Pick at least one day for the refresh.');
    const key = 'r' + Date.now().toString(36);
    const rep = makeReport({ key: key, name: (s.reportName || '').trim() || src.title, prompt: src.title, sessionId: src.id, page: src.page, cad: cad, now: Date.now() });
    this.setState((p) => ({
      reports: (p.reports || []).concat([rep]),
      reportMenu: false, reportName: '', view: 'report', reportKey: key, reportRunIdx: 0, detail: null, navOpen: true
    }));
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
    this.rpRun(key);
  },

  // One refresh: the prompt on a fresh thread, so no earlier conversation colours the report.
  // run-stateful, not run — the compliance preflight that produces the structured answer only
  // sits on the stateful path (see ccAsk).
  async rpRun(key) {
    const rep = (this.state.reports || []).find((r) => r.key === key);
    if (!rep) return;
    if (this._rpRunning) {
      if (this._rpRunning !== key) this.flash('Another report is refreshing — this one runs next.');
      return;
    }
    this._rpRunning = key;
    const t0 = Date.now();
    this.rpPatch(key, { status: 'running', error: '' });
    try {
      const sid = 'report-' + key + '-' + t0.toString(36);
      const r = await deepAgentsApi.runStateful(rep.prompt, sid, reportContext(rep), null);
      if (r && r.success === false) throw new Error(r.error || 'the orchestrator returned no answer');
      const answer = (r && r.answer) || '';
      const toolCalls = (r && r.tool_calls) || [];
      const engineError = errorFromAnswer(answer);
      if (engineError) throw new Error(engineError);
      const rich = extractComplianceAnswer(toolCalls) || null;
      if (!answer && !rich) throw new Error('the orchestrator returned an empty answer');
      const at = Date.now();
      this.rpPatch(key, (p) => ({
        status: 'ready', error: '', lastRunAt: at, lastTriedAt: at, nextRunAt: nextRunAt(p.cad, at),
        runs: [{ at: at, ms: at - t0, answer: answer, calls: uniq(toolCalls.map((t) => t.tool)), rich: rich }].concat(p.runs || []).slice(0, MAX_RUNS)
      }));
      if (this.state.view === 'report' && this.state.reportKey === key) this.setState({ reportRunIdx: 0 });
    } catch (e) {
      const msg = (e && e.message) || String(e);
      const at = Date.now();
      const unreachable = (e && (e.status === 502 || e.status === 503 || e.status === 504)) || /ECONNREFUSED|ENOTFOUND|network|failed to fetch|timed out/i.test(msg);
      const text = msg + (unreachable ? ' — svc-deepagents is not reachable at /backend/deep-agents.' : '');
      this.rpPatch(key, (p) => ({
        status: 'error', error: text, lastTriedAt: at, nextRunAt: nextRunAt(p.cad, at),
        runs: [{ at: at, ms: at - t0, error: text }].concat(p.runs || []).slice(0, MAX_RUNS)
      }));
    } finally {
      this._rpRunning = null;
    }
  },

  // The scheduler: while the app is open and signed in, due reports refresh one at a time.
  //
  // Mount issues reads only. A report left overdue from a previous visit — loadReports even
  // forces an interrupted run back to due — used to be caught up 4 seconds after every
  // mount with no one having asked for anything: open the tab, and the orchestrator ran a
  // write-capable turn against production on its own. `_rpBootAt` is the line: a report due
  // before this boot stays parked (Run now clears it explicitly, or it simply waits for its
  // own next cadence, which lands after boot and ticks normally) — only a cadence that
  // comes due *while this tab is open and being watched* fires unattended.
  rpStart() {
    this.rpStop();
    this._rpBootAt = Date.now();
    this._rpTimer = setInterval(() => this.rpTick(), TICK_MS);
  },
  rpStop() { clearInterval(this._rpTimer); },
  rpTick() {
    if (!this.state.signedIn || this._rpRunning) return;
    const now = Date.now();
    const bootAt = typeof this._rpBootAt === 'number' ? this._rpBootAt : 0;
    const due = (this.state.reports || [])
      .filter((r) => r.status !== 'running' && typeof r.nextRunAt === 'number' && r.nextRunAt <= now && r.nextRunAt >= bootAt)
      .sort((a, b) => a.nextRunAt - b.nextRunAt);
    if (due.length) this.rpRun(due[0].key);
  },

  rpOpen(key) {
    this.setState({ view: 'report', reportKey: key, reportRunIdx: 0, navOpen: true, detail: null, queueOpen: false, paletteOpen: false });
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  },

  rpDelete(key) {
    this.setState((p) => Object.assign(
      { reports: (p.reports || []).filter((r) => r.key !== key) },
      p.view === 'report' && p.reportKey === key ? { view: 'home', reportKey: null } : {}
    ));
    this.flash('Report deleted.');
  },

  // Saves the run in view as a markdown file, client-side.
  rpExport(key, runIdx) {
    const rep = (this.state.reports || []).find((r) => r.key === key);
    if (!rep) return;
    const run = (rep.runs || [])[runIdx || 0] || (rep.runs || [])[0] || null;
    const md = reportMarkdown(rep, run);
    if (typeof document === 'undefined' || typeof window === 'undefined' || !window.URL || !window.URL.createObjectURL) {
      return this.flash('Export needs a browser.');
    }
    const url = window.URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = (rep.name || 'report').replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() + '.md';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    this.flash('Saved ' + a.download);
  }
};
