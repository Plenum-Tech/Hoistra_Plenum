// cronsPage — Administration › Hoist Crons: every scheduled job, its run history, its status
// day by day, who did what to it, and New job / Edit schedule.
//
// The jobs are the same ones the Home panel lists and the chat's card creates
// (svc-operations-intelligence engines/crons); this page is where an admin manages them.
// The New job and Edit schedule dialog share the chat card's form (crons.js: cronForm,
// cronToggleJob, cronSet …), so the two places schedule by one set of rules.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { cronsApi } from '../api/crons.js';
import { isStaleScope } from '../api/client.js';
import { CRON_FREQS, DAY_NAMES, companyWideOnly, recipientsOf, refreshFor, summaryLine } from './crons.js';

const zone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (e) { return 'UTC'; }
};
const pad = (n) => String(n).padStart(2, '0');

// The next `n` times a cadence lands on, after `now`, in the browser's zone — the dialog's
// preview. The service computes the real next run in the job's own zone; this is the same
// arithmetic for the reader's eye. Pure.
export function nextRuns(refresh, n, now) {
  const out = [];
  const at = new Date(now || Date.now());
  if (!refresh) return out;
  if (refresh.every_minutes) {
    for (let i = 1; i <= n; i++) out.push(new Date(at.getTime() + i * refresh.every_minutes * 60000));
    return out;
  }
  const [h, m] = String(refresh.daily_at || refresh.time || '00:00').split(':').map(Number);
  if (refresh.monthly_day) {
    const d = new Date(at.getFullYear(), at.getMonth(), refresh.monthly_day, h, m, 0, 0);
    for (let i = 0; i < 24 && out.length < n; i++) {
      if (d > at) out.push(new Date(d));
      d.setMonth(d.getMonth() + 1);
    }
    return out;
  }
  const days = refresh.daily_at ? [0, 1, 2, 3, 4, 5, 6] : (refresh.days || []);
  if (!days.length) return out;
  const d = new Date(at);
  d.setHours(h, m, 0, 0);
  for (let i = 0; i < 400 && out.length < n; i++) {
    if (d > at && days.includes(d.getDay())) out.push(new Date(d));
    d.setDate(d.getDate() + 1);
  }
  return out;
}

export function fmtWhen(dt, now) {
  const d = dt instanceof Date ? dt : new Date(dt);
  if (isNaN(d)) return '';
  const today = new Date(now || Date.now());
  const sameDay = d.toDateString() === today.toDateString();
  const tomorrow = new Date(today); tomorrow.setDate(today.getDate() + 1);
  const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
  if (sameDay) return 'today ' + hm;
  if (d.toDateString() === tomorrow.toDateString()) return 'tomorrow ' + hm;
  return DAY_NAMES[d.getDay()] + ' ' + d.getDate() + ' ' + d.toLocaleString('en-GB', { month: 'short' }) + ' ' + hm;
}

// A job's cadence back into the dialog's form, for Edit schedule. Pure.
export function formFromJob(job) {
  const r = (job && job.refresh) || {};
  const p = (job && job.params) || {};
  let freq = '1h';
  const byMinutes = { 15: '15m', 30: '30m', 60: '1h', 360: '6h', 720: '12h' };
  if (r.every_minutes) freq = byMinutes[r.every_minutes] || '1h';
  else if (r.daily_at) freq = 'daily';
  else if (r.days) freq = 'days';
  else if (r.monthly_day) freq = 'monthly';
  return {
    jobs: [job.job_key], freq: freq, time: r.daily_at || r.time || '07:00', days: r.days || [1],
    prompt: p.prompt || '', email: !!p.email,
    recipients: Array.isArray(p.recipients) ? p.recipients.slice() : [], recipientDraft: '',
    monthDay: r.monthly_day || 1, buildingId: p.building_id || '', runNow: false
  };
}

const TONE = { ok: 'var(--st-ok)', failed: 'var(--st-risk)', partial: 'var(--st-warn)', none: 'var(--color-divider)' };

export const cronsPageMethods = {
  cpOpen() {
    window.scrollTo(0, 0);
    this.setState({ view: 'crons', role: 'admin', navOpen: true, detail: null, cpJobId: null, cpModal: null });
    this.cronLoad();
    this.cronLoadCatalogue();
  },
  cpOpenJob(id) {
    window.scrollTo(0, 0);
    this.setState({ cpJobId: id, cpDetail: null, cpDetailErr: '' });
    this.cpLoadDetail(id);
  },
  cpBack() { this.setState({ cpJobId: null, cpDetail: null }); },

  // The run email's link: {app}/?cron=<job id>. Read once at boot and scrubbed from the bar;
  // the job opens when the session is in place (now, or after the sign-in the link led to).
  cpLinkFromUrl() {
    let id = '';
    try {
      const params = new URLSearchParams(window.location.search || '');
      id = String(params.get('cron') || '').trim();
      if (!id) return;
      params.delete('cron');
      const rest = params.toString();
      window.history.replaceState(null, '', (window.location.pathname || '/') + (rest ? '?' + rest : ''));
    } catch (e) { /* no URL or history - nothing to open */ }
    if (/^[0-9a-f-]{36}$/i.test(id)) this._cpLinked = id;
  },
  cpOpenLinked() {
    const id = this._cpLinked;
    if (!id || !this.state.signedIn) return;
    this._cpLinked = null;
    this.cpOpen();
    this.cpOpenJob(id);
  },

  async cpLoadDetail(id) {
    if (!id || this._cpLoading === id) return;
    this._cpLoading = id;
    try {
      const [runs, events, daily] = await Promise.all([
        cronsApi.runs(id, 100), cronsApi.events(id), cronsApi.daily(30, zone(), id)
      ]);
      if (this.state.cpJobId !== id) return;
      this.setState({
        cpDetail: { job: runs && runs.job, runs: (runs && runs.runs) || [], events: (events && events.events) || [],
          days: ((daily && daily.jobs && daily.jobs[0]) || {}).days || [] },
        cpDetailErr: ''
      });
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ cpDetailErr: (e && e.message) || String(e) });
    } finally {
      this._cpLoading = null;
    }
  },

  // New job: the chat card's form, opened as the page's dialog.
  cpNew() {
    this.setState({ cpModal: 'new', cronMsg: '', cronBusy: false,
      cronForm: { jobs: [], freq: '1h', time: '07:00', days: [1], monthDay: 1, buildingId: '', prompt: '', email: false,
        recipients: [], recipientDraft: '', runNow: false } });
    this.cronLoadCatalogue();
  },
  cpEdit(job) {
    if (!job) return;
    this.setState({ cpModal: 'edit', cpEditId: job.id, cronMsg: '', cronBusy: false, cronForm: formFromJob(job) });
  },
  cpModalClose() { this.setState({ cpModal: null, cronMsg: '' }); },

  async cpSaveEdit() {
    const id = this.state.cpEditId;
    const f = this.state.cronForm || {};
    if (!id || this.state.cronBusy) return;
    const r = refreshFor(f);
    if (r.error) return this.setState({ cronMsg: r.error });
    const isQuestion = (f.jobs || []).includes('question');
    if (isQuestion && !String(f.prompt || '').trim()) return this.setState({ cronMsg: 'Write the question to ask.' });
    const to = recipientsOf(f);
    if (to.error) return this.setState({ cronMsg: to.error });
    if (companyWideOnly(f).length) return this.setState({ cronMsg: 'This job runs company-wide only — choose All buildings.' });
    // Only a changed list is sent, so the job's history says "recipients changed" only when they did.
    const job = (this.state.cronJobs || []).find((j) => j.id === id) || {};
    const had = ((job.params && job.params.recipients) || []).join(',');
    this.setState({ cronBusy: true, cronMsg: '' });
    try {
      await cronsApi.update(id, Object.assign({ refresh: r.refresh, timezone: zone() },
        to.recipients.join(',') !== had ? { recipients: to.recipients } : {},
        // null puts the job back to company-wide; unchanged, nothing is sent.
        (f.buildingId || '') !== ((job.params && job.params.building_id) || '') ? { building_id: f.buildingId || null } : {},
        isQuestion ? { prompt: f.prompt, email: !!f.email } : {}));
      this.setState({ cronBusy: false, cpModal: null });
      this.flash('Schedule saved.');
      this.cronLoad();
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ cronBusy: false }); return; }
      this.setState({ cronBusy: false, cronMsg: 'Not saved: ' + ((e && e.message) || String(e)) });
    }
  },

  cpSetFilter(f) { this.setState({ cpFilter: f }); },
  cpSetSearch(e) { this.setState({ cpSearch: e.target.value }); },

  cronsPageVals() { return cronsPageVals(this); }
};

// The page's values. Pure over state.
export function cronsPageVals(c) {
  const s = c.state;
  const now = Date.now();
  const jobs = s.cronJobs || [];
  const st = s.cronStats || {};
  const filter = s.cpFilter || 'All';
  const q = String(s.cpSearch || '').trim().toLowerCase();
  const keep = (j) => (filter === 'All' || (filter === 'Paused' ? !j.enabled : j.module === filter))
    && (!q || (j.name + ' ' + j.label + ' ' + ((j.created_by && j.created_by.email) || j.owner_email || '')).toLowerCase().includes(q));
  const strip = (id, n) => ((s.cronDaily || {})[id] || []).slice(-n).map((d) => ({
    date: d.date, tone: TONE[d.status] || TONE.none,
    title: d.date + ': ' + (d.runs ? d.runs + (d.runs === 1 ? ' run' : ' runs') + (d.failed ? ', ' + d.failed + ' failed' : ', all ok')
      + (d.last_summary ? ' · ' + summaryLine(d.last_summary) : '') : 'no run')
  }));
  const statusOf = (j) => (!j.enabled ? { label: 'Paused', tone: 'var(--color-neutral-500)' }
    : j.status === 'error' ? { label: 'Error on last run', tone: 'var(--st-risk)' }
    : j.status === 'running' ? { label: 'Running now', tone: 'var(--color-accent)' }
    : { label: 'Scheduled', tone: 'var(--st-ok)' });
  const lastOf = (j) => {
    const r = (j.runs || [])[0];
    if (!r) return { text: 'Not run yet', tone: 'var(--color-neutral-500)' };
    return { text: fmtWhen(r.finished_at, now) + ' · ' + (r.ok ? (summaryLine(r.summary) || 'ok') : 'failed: ' + String(r.error || '').slice(0, 70)),
      tone: r.ok ? 'var(--color-neutral-400)' : 'var(--st-risk)' };
  };
  const nextJob = jobs.filter((j) => j.enabled && j.next_run_at).sort((a, b) => new Date(a.next_run_at) - new Date(b.next_run_at))[0];
  const manage = !!s.cronCanManage;
  const row = (j) => {
    const stt = statusOf(j), last = lastOf(j);
    return {
      id: j.id, name: j.name, module: j.module, status: stt.label, statusTone: stt.tone, cadence: j.refresh_label,
      next: j.enabled && j.next_run_at ? fmtWhen(j.next_run_at, now) : '—',
      last: last.text, lastTone: last.tone, days: strip(j.id, 14),
      by: (j.created_by && j.created_by.email) || j.owner_email || '—',
      open: () => c.cpOpenJob(j.id),
      edit: () => c.cpEdit(j),
      run: () => c.cronRun(j.id), running: s.cronRunning === j.id,
      pause: () => c.cronPause(j.id, !j.enabled), pauseLabel: j.enabled ? 'Pause' : 'Resume', manage: manage
    };
  };

  // The open job.
  const d = s.cpDetail;
  const dj = (d && d.job) || jobs.find((j) => j.id === s.cpJobId) || null;
  const detail = dj ? (() => {
    const stt = statusOf(dj);
    const runs = (d && d.runs) || [];
    const okN = runs.filter((r) => r.ok).length;
    const avg = runs.length ? runs.reduce((a, r) => a + (r.duration_ms || 0), 0) / runs.length / 1000 : 0;
    const ACTION = { created: 'Created', changed: 'Changed', paused: 'Paused', resumed: 'Resumed', removed: 'Removed', run_requested: 'Run requested' };
    const detailOf = (e) => {
      const x = e.details || {};
      if (e.action === 'created') return (x.refresh ? 'schedule set' : '') + (x.source_session_id ? ' · from the chat' : '');
      if (e.action === 'changed') {
        if (Object.keys(x).length === 1 && Array.isArray(x.recipients)) {
          return x.recipients.length ? 'emails to ' + x.recipients.join(', ') : 'extra recipients cleared';
        }
        return Object.keys(x).filter((k) => k !== 'timezone').map((k) => k === 'refresh' ? 'schedule' : k).join(', ') + ' changed';
      }
      if (e.action === 'run_requested') return 'Run now';
      return '';
    };
    return {
      name: dj.name, label: dj.label, module: dj.module, status: stt.label, statusTone: stt.tone,
      cadence: dj.refresh_label, zone: dj.timezone,
      next: dj.enabled && dj.next_run_at ? fmtWhen(dj.next_run_at, now) : '—',
      building: (dj.params && dj.params.building_name) || 'All buildings (company-wide)',
      runsAs: dj.owner_email || (dj.created_by && dj.created_by.email) || '—',
      createdBy: ((dj.created_by && dj.created_by.email) || '—') + (dj.created_at ? ' · ' + fmtWhen(dj.created_at, now) : ''),
      question: (dj.params && dj.params.prompt) || '', emails: !!(dj.params && dj.params.email),
      // Who each run's result goes to: the creator if they ticked "email me", then the list.
      mailTo: (() => {
        const p = dj.params || {};
        const list = (p.email ? [dj.owner_email || 'the creator'] : []).concat(Array.isArray(p.recipients) ? p.recipients : []);
        return list.filter((a, i) => list.indexOf(a) === i).join(', ');
      })(),
      okLine: runs.length ? okN + ' of ' + runs.length + ' ok' : 'No runs yet', okTone: runs.length && okN < runs.length ? 'var(--st-risk)' : 'var(--st-ok)',
      avg: runs.length ? 'avg ' + avg.toFixed(1) + ' s per run' : '',
      days: ((d && d.days) || []).map((x) => ({
        tone: TONE[x.status] || TONE.none, label: String(Number(String(x.date).slice(8, 10))),
        title: x.date + ': ' + (x.runs ? x.runs + (x.runs === 1 ? ' run' : ' runs') + (x.failed ? ', ' + x.failed + ' failed' : ', all ok') : 'no run')
      })),
      runs: runs.map((r) => ({
        at: fmtWhen(r.finished_at, now), by: r.trigger === 'schedule' ? 'The schedule' : 'Run now · ' + (r.requested_by || 'someone'),
        took: ((r.duration_ms || 0) / 1000).toFixed(1) + ' s',
        result: r.ok ? (summaryLine(r.summary) || 'ok') : 'Failed: ' + (r.error || 'no reason given'),
        tone: r.ok ? 'var(--color-text)' : 'var(--st-risk)', answer: r.answer ? String(r.answer).slice(0, 400) : '',
        // The dashboard the orchestrator built for this answer (kept on the run's summary), so a
        // scheduled question shows the same cards the chat does; the full answer is its fallback.
        rich: r.summary && r.summary.rich && typeof r.summary.rich === 'object' ? r.summary.rich : null,
        answerFull: r.answer ? String(r.answer) : ''
      })),
      events: ((d && d.events) || []).map((e) => ({
        action: ACTION[e.action] || e.action, who: e.user_email || 'unknown', at: fmtWhen(e.at, now), detail: detailOf(e),
        tone: e.action === 'created' ? 'var(--st-ok)' : e.action === 'removed' ? 'var(--st-risk)' : 'var(--color-accent)'
      })),
      loading: !d && !s.cpDetailErr, error: s.cpDetailErr || '',
      run: () => c.cronRun(dj.id), running: s.cronRunning === dj.id,
      pause: () => c.cronPause(dj.id, !dj.enabled), pauseLabel: dj.enabled ? 'Pause' : 'Resume',
      edit: () => c.cpEdit(dj),
      remove: async () => {
        const armed = s.cronRemoveArmed === dj.id;
        await c.cronRemove(dj.id);
        if (armed) c.cpBack();
      },
      removeLabel: s.cronRemoveArmed === dj.id ? 'Click again to remove' : 'Remove', manage: manage
    };
  })() : null;

  // The dialog's next-runs preview, from the form as it stands.
  const f = s.cronForm || {};
  const r = refreshFor(f);
  const preview = r.error ? r.error : nextRuns(r.refresh, 3, now).map((x) => fmtWhen(x, now)).join(', ')
    + ' — then ' + (CRON_FREQS.find((x) => x.key === f.freq) || {}).label.replace('…', '').toLowerCase();

  return {
    isCrons: s.signedIn && s.view === 'crons',
    cpLoading: !s.cronLoadedAt && !s.cronLoadErr,
    cpError: s.cronLoadErr || '',
    cpManage: manage,
    cpNew: () => c.cpNew(),
    cpTiles: [
      { value: String(jobs.length), label: 'Scheduled jobs', hint: jobs.filter((j) => j.enabled).length + ' running · ' + jobs.filter((j) => !j.enabled).length + ' paused', tone: 'var(--color-text)' },
      { value: String(st.runs_today || 0), label: 'Runs today', hint: ((st.runs_today || 0) - (st.failed_today || 0)) + ' ok · ' + (st.failed_today || 0) + ' failed', tone: st.failed_today ? 'var(--st-risk)' : 'var(--st-ok)' },
      { value: nextJob ? fmtWhen(nextJob.next_run_at, now).replace(/^today /, '') : '—', label: 'Next run', hint: nextJob ? nextJob.name : 'nothing scheduled', tone: 'var(--color-text)' },
      { value: String(st.emails_week || 0), label: 'Emailed this week', hint: 'from admin@hoistra.ai', tone: 'var(--color-text)' }
    ],
    cpFilters: ['All', 'Energy', 'Compliance', 'Assets', 'Vendors', 'Maintenance', 'Orchestrator', 'Paused'].map((x) => ({ label: x, on: filter === x, pick: () => c.cpSetFilter(x) })),
    cpSearch: s.cpSearch || '', cpSetSearch: (e) => c.cpSetSearch(e),
    cpRows: jobs.filter(keep).map(row),
    cpEmpty: !jobs.length,
    cpNoMatch: jobs.length > 0 && !jobs.filter(keep).length,
    cpRecent: (s.cronRuns || []).slice(0, 12).map((x) => ({
      at: fmtWhen(x.finished_at, now), job: x.name, by: x.trigger === 'schedule' ? 'The schedule' : 'Run now · ' + (x.requested_by || 'someone'),
      took: ((x.duration_ms || 0) / 1000).toFixed(1) + ' s',
      result: x.ok ? (summaryLine(x.summary) || 'ok') : 'Failed: ' + (x.error || 'no reason given'),
      tone: x.ok ? 'var(--color-text)' : 'var(--st-risk)', open: () => c.cpOpenJob(x.job_id)
    })),
    cpDetail: detail, cpBack: () => c.cpBack(),
    // the dialog
    cpModal: s.cpModal || null,
    cpModalTitle: s.cpModal === 'edit' ? 'Edit schedule' : 'New job',
    cpModalEdit: s.cpModal === 'edit',
    cpPreview: preview,
    cpSave: s.cpModal === 'edit' ? () => c.cpSaveEdit() : () => c.cronCreate(),
    cpSaveLabel: s.cronBusy ? 'Saving…' : s.cpModal === 'edit' ? 'Save schedule'
      : ((f.jobs || []).length ? 'Create ' + f.jobs.length + (f.jobs.length === 1 ? ' job' : ' jobs') : 'Pick a job'),
    cpModalClose: () => c.cpModalClose()
  };
}
