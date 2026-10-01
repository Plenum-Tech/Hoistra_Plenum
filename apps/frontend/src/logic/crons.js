// crons — scheduling Hoist crons from the chat, and the jobs on the Home page's Hoist Crons panel.
//
// "Run an energy anomaly scan every hour", "schedule the compliance check daily at 7" — a
// scheduling request in the chat is not a question for the orchestrator. It opens a card in
// the conversation instead: the jobs the service can run (ticked from what was asked), how
// often (every 15/30 min, hourly, every 6/12 hours, daily at a time, chosen days at a time),
// and Create. The service keeps the cadence (svc-operations-intelligence engines/crons), so a
// job runs whether or not anyone has the page open, and each run lands on the Hoist Crons
// panel with what it found.
//
// Methods are mixed into HoistraLogic.prototype; `this` is the controller.
import { cronsApi } from '../api/crons.js';
import { isStaleScope } from '../api/client.js';

// The catalogue as the service names it; read from GET /api/crons/catalogue when it answers,
// this copy only labels the card until then.
export const CRON_JOBS = [
  { key: 'energy_anomaly_scan', label: 'Energy anomaly scan', module: 'Energy', words: /anomal|energy scan|consumption spike|baseline drift/ },
  { key: 'energy_chiller_scan', label: 'Chiller efficiency scan', module: 'Energy', words: /chiller/ },
  { key: 'energy_condition_scan', label: 'Asset condition scan', module: 'Assets', words: /condition|asset health|threat|watch/ },
  { key: 'energy_meter_gaps', label: 'Meter gap check', module: 'Energy', words: /meter gap|gaps|missing reading|readings stopped/ },
  { key: 'energy_benchmarks', label: 'Benchmark validation', module: 'Energy', words: /benchmark|eui|tm46/ },
  { key: 'compliance_expiry_scan', label: 'Compliance expiry scan', module: 'Compliance', words: /compliance|certificate|expir|lapse/ },
  { key: 'compliance_reverify', label: 'Certificate re-verification', module: 'Compliance', words: /re-?verif|verify|register check/ },
  { key: 'question', label: 'Ask a question', module: 'Orchestrator', words: /ask|report on|summar|brief me|send me/ }
];

// How often, as the card offers it. `refresh` is the service's own shape.
export const CRON_FREQS = [
  { key: '15m', label: 'Every 15 minutes', refresh: { every_minutes: 15 } },
  { key: '30m', label: 'Every 30 minutes', refresh: { every_minutes: 30 } },
  { key: '1h', label: 'Every hour', refresh: { every_minutes: 60 } },
  { key: '6h', label: 'Every 6 hours', refresh: { every_minutes: 360 } },
  { key: '12h', label: 'Every 12 hours', refresh: { every_minutes: 720 } },
  { key: 'daily', label: 'Every day at…', time: true },
  { key: 'days', label: 'On chosen days at…', time: true, days: true }
];
export const DAY_NAMES = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// Is this a request to schedule something, rather than a question? Deliberately narrow: a
// question that merely mentions "daily" ("what is our daily consumption") is a question.
const SCHEDULE_RE = /\b(cron|crons|schedul\w*|recurring|automat\w*)\b|\b(run|check|scan|ask|send|report)\b[^.?!]{0,80}\b(every|each|daily|hourly|nightly|weekly)\b/i;
export function isScheduleRequest(q) {
  const s = String(q || '');
  if (!SCHEDULE_RE.test(s)) return false;
  // "What is scheduled for tomorrow?" / "show the PPM schedule" are reads of a schedule.
  return !/\b(ppm|maintenance) schedule\b|\bwhat('s| is) scheduled\b|\bscheduled (visits?|work|ppm)\b/i.test(s);
}

// The jobs a message names, in catalogue order. None named → none ticked; the reader picks.
export function guessJobs(q) {
  const s = String(q || '').toLowerCase();
  const hit = CRON_JOBS.filter((j) => j.key !== 'question' && j.words.test(s)).map((j) => j.key);
  return hit;
}

const pad = (n) => String(n).padStart(2, '0');
// "at 7", "at 7am", "at 19:30", "at 2 pm" → "HH:MM"; null when no time is given.
export function guessTime(q) {
  const m = /\bat\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?\b/i.exec(String(q || ''));
  if (!m) return /\bnightly\b/i.test(q) ? '02:00' : /\bmorning\b/i.test(q) ? '07:00' : null;
  let h = Number(m[1]);
  const mi = m[2] ? Number(m[2]) : 0;
  const ap = (m[3] || '').toLowerCase();
  if (ap === 'pm' && h < 12) h += 12;
  if (ap === 'am' && h === 12) h = 0;
  return h < 24 && mi < 60 ? pad(h) + ':' + pad(mi) : null;
}

// The cadence a message names → {freq, time, days}. Falls back to every hour.
export function guessCadence(q) {
  const s = String(q || '').toLowerCase();
  const time = guessTime(s);
  const days = DAY_NAMES.map((d, i) => (new RegExp('\\b' + d.toLowerCase() + '(day|nesday|sday|urday|rsday)?s?\\b').test(s) ? i : -1)).filter((i) => i >= 0);
  if (/\bweekdays?\b/.test(s)) return { freq: 'days', time: time || '07:00', days: [1, 2, 3, 4, 5] };
  if (days.length) return { freq: 'days', time: time || '07:00', days: days };
  if (/\bweekly\b|\bevery week\b/.test(s)) return { freq: 'days', time: time || '07:00', days: [1] };
  if (/\b15 ?min/.test(s)) return { freq: '15m' };
  if (/\b30 ?min|half[- ]hour/.test(s)) return { freq: '30m' };
  if (/\b(6|six) ?hours?\b/.test(s)) return { freq: '6h' };
  if (/\b(12|twelve) ?hours?\b|twice a day/.test(s)) return { freq: '12h' };
  if (/\bhourly\b|\bevery hour\b/.test(s)) return { freq: '1h' };
  if (/\bdaily\b|\bevery day\b|\bnightly\b|\beach day\b|\bevery morning\b/.test(s) || time) return { freq: 'daily', time: time || '07:00' };
  return { freq: '1h' };
}

// The service's refresh shape for the card's choice, or a reason it cannot be sent.
export function refreshFor(form) {
  const f = CRON_FREQS.find((x) => x.key === (form && form.freq));
  if (!f) return { error: 'Choose how often.' };
  if (f.refresh) return { refresh: f.refresh };
  const t = /^(\d{1,2}):(\d{2})$/.exec(String(form.time || '').trim());
  if (!t || Number(t[1]) > 23 || Number(t[2]) > 59) return { error: 'Choose a time, HH:MM.' };
  const time = pad(Number(t[1])) + ':' + t[2];
  if (!f.days) return { refresh: { daily_at: time } };
  const days = (form.days || []).slice().sort();
  if (!days.length) return { error: 'Pick at least one day.' };
  return { refresh: { days: days, time: time } };
}

// "Send me a compliance summary every Monday at 9" → "Give me a compliance summary." — the
// question the orchestrator is asked each time, without the delivery or the schedule in it.
export function promptFrom(q) {
  let s = String(q || '').trim();
  s = s.replace(/\s*\b(every|each|daily|hourly|nightly|weekly|on (mon|tue|wed|thu|fri|sat|sun)\w*|at \d)\b.*$/i, '').trim();
  const lead = /^(please\s+)?(send|e-?mail|mail|brief|give)\s+me\s+/i;
  if (lead.test(s)) s = 'Give me ' + s.replace(lead, '');
  s = s.replace(/[\s.?!,]+$/, '');
  if (!s) return '';
  const asksWhat = /^(what|which|who|how|is|are|do|does|why|when)\b/i.test(s);
  return s.charAt(0).toUpperCase() + s.slice(1) + (asksWhat ? '?' : '.');
}

const zone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'; } catch (e) { return 'UTC'; }
};

export const cronMethods = {
  cronVals() { return cronVals(this); },

  // A scheduling request from the chat: the question goes in the transcript, the card opens
  // under it, ticked from what was asked. Nothing is sent to the orchestrator.
  cronOpenFromChat(q, opts) {
    // From ccAsk the question is already in the transcript; from elsewhere it is added here.
    const withQuestion = !(opts && opts.appendQuestion === false);
    const jobs = guessJobs(q);
    const cad = guessCadence(q);
    // "Send me a compliance summary every morning" is a question on a schedule, not a scan.
    const asks = /\b(ask|send me|brief me|summar\w*|report on)\b/i.test(q);
    const keys = asks ? ['question'] : jobs;
    this.setState((p) => ({
      ccChat: (p.ccChat || []).concat((withQuestion ? [{ role: 'you', text: q }] : []).concat([
        { role: 'bot', isNote: true, text: keys.length
          ? 'Here is that as a scheduled job. Check what runs and when, then Create — it then runs on its own and shows on Hoist Crons.'
          : 'Pick the jobs to run and when — they then run on their own and show on Hoist Crons.' }
      ])),
      cronOpen: true, cronMsg: '', cronBusy: false,
      cronForm: {
        jobs: keys, freq: cad.freq, time: cad.time || '07:00', days: cad.days || [1],
        prompt: asks ? promptFrom(q) : '',
        // "Send me …" / "email me …" asks for delivery, not only a record on the panel.
        email: asks && /\b(send|e-?mail|mail)\s+me\b/i.test(q),
        runNow: false
      }
    }));
    // A dock page has nowhere to put the card; the chat page does, with the same transcript.
    if (withQuestion || (typeof this.dockAnswers === 'function' && this.dockAnswers())) this.openChat();
    this.cronLoadCatalogue();
  },

  cronOpenBlank() {
    this.setState({
      cronOpen: true, cronMsg: '', cronBusy: false,
      cronForm: { jobs: [], freq: '1h', time: '07:00', days: [1], prompt: '', runNow: false }
    });
    this.openChat();
    this.cronLoadCatalogue();
  },

  async cronLoadCatalogue() {
    if (this.state.cronCatalogue) return;
    try {
      const c = await cronsApi.catalogue();
      this.setState({ cronCatalogue: (c && c.jobs) || null });
    } catch (e) { /* the built-in labels stand in */ }
  },

  cronSet(field, value) {
    this.setState((p) => ({ cronForm: Object.assign({}, p.cronForm || {}, { [field]: value }), cronMsg: '' }));
  },
  cronToggleJob(key) {
    this.setState((p) => {
      const f = Object.assign({}, p.cronForm || {});
      const set = new Set(f.jobs || []);
      if (set.has(key)) set.delete(key); else set.add(key);
      f.jobs = CRON_JOBS.map((j) => j.key).filter((k) => set.has(k));
      return { cronForm: f, cronMsg: '' };
    });
  },
  cronToggleDay(i) {
    this.setState((p) => {
      const f = Object.assign({}, p.cronForm || {});
      const set = new Set(f.days || []);
      if (set.has(i)) set.delete(i); else set.add(i);
      f.days = Array.from(set).sort();
      return { cronForm: f, cronMsg: '' };
    });
  },
  cronClose() { this.setState({ cronOpen: false, cronMsg: '' }); },

  async cronCreate() {
    const f = this.state.cronForm || {};
    if (this.state.cronBusy) return;
    if (!(f.jobs || []).length) return this.setState({ cronMsg: 'Tick at least one job.' });
    if ((f.jobs || []).includes('question') && !String(f.prompt || '').trim()) {
      return this.setState({ cronMsg: 'Write the question to ask.' });
    }
    const r = refreshFor(f);
    if (r.error) return this.setState({ cronMsg: r.error });
    this.setState({ cronBusy: true, cronMsg: '' });
    try {
      const out = await cronsApi.create({
        job_keys: f.jobs, refresh: r.refresh, timezone: zone(), prompt: f.prompt || undefined,
        email: (f.jobs || []).includes('question') && !!f.email,
        run_now: !!f.runNow, source_session_id: this.state.sessionId || undefined
      });
      const made = (out && out.jobs) || [];
      const line = made.map((j) => j.label + ' — ' + j.refresh_label).join('; ');
      this.setState((p) => ({
        cronBusy: false, cronOpen: false,
        ccChat: (p.ccChat || []).concat([{ role: 'bot', isNote: true,
          text: 'Scheduled ' + made.length + (made.length === 1 ? ' job: ' : ' jobs: ') + line +
            '. ' + (f.runNow ? 'The first run starts now. ' : '') +
            ((f.jobs || []).includes('question') && f.email ? 'Each answer is emailed to you from admin@hoistra.ai. ' : '') +
            'They are on the Hoist Crons panel on Home, with each run and what it found.' }])
      }));
      this.cronLoad();
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ cronBusy: false }); return; }
      this.setState({ cronBusy: false, cronMsg: 'Not scheduled: ' + ((e && e.message) || String(e)) });
    }
  },

  // The company's jobs for the Hoist Crons panel.
  async cronLoad() {
    if (!this.state.signedIn || this._cronLoading) return;
    this._cronLoading = true;
    try {
      const [d, daily] = await Promise.all([cronsApi.list(), cronsApi.daily(14, zone()).catch(() => null)]);
      const byJob = {};
      ((daily && daily.jobs) || []).forEach((j) => { byJob[j.job_id] = j.days || []; });
      this.setState({ cronJobs: (d && d.jobs) || [], cronRuns: (d && d.recent_runs) || [], cronDaily: byJob,
        cronCanManage: !!(d && d.can_manage), cronLoadErr: '' });
    } catch (e) {
      if (!isStaleScope(e)) this.setState({ cronLoadErr: (e && e.message) || String(e) });
    } finally {
      this._cronLoading = false;
    }
  },

  async cronRun(id) {
    if (this.state.cronRunning === id) return;
    this.setState({ cronRunning: id });
    try {
      const r = await cronsApi.run(id);
      this.flash(r && r.ok ? 'Ran — the result is on Hoist Crons.' : 'The run failed: ' + ((r && r.error) || 'no reason given'));
    } catch (e) {
      if (!isStaleScope(e)) this.flash('Did not run: ' + ((e && e.message) || String(e)));
    }
    this.setState({ cronRunning: null });
    this.cronLoad();
  },
  async cronPause(id, enabled) {
    try { await cronsApi.update(id, { enabled: !!enabled }); } catch (e) {
      if (!isStaleScope(e)) this.flash('Not changed: ' + ((e && e.message) || String(e)));
    }
    this.cronLoad();
  },
  async cronRemove(id) {
    if (this.state.cronRemoveArmed !== id) {
      clearTimeout(this._cronRmTimer);
      this.setState({ cronRemoveArmed: id });
      this._cronRmTimer = setTimeout(() => { if (this.state.cronRemoveArmed === id) this.setState({ cronRemoveArmed: null }); }, 5000);
      return;
    }
    clearTimeout(this._cronRmTimer);
    this.setState({ cronRemoveArmed: null });
    try { await cronsApi.remove(id); } catch (e) {
      if (!isStaleScope(e)) this.flash('Not removed: ' + ((e && e.message) || String(e)));
    }
    this.cronLoad();
  }
};

const STATUS_TONE = { ready: 'var(--st-ok)', running: 'var(--color-accent)', pending: 'var(--color-neutral-400)',
  error: 'var(--st-risk)', paused: 'var(--color-neutral-600)' };

const whenShort = (iso, now) => {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  const d = Math.round((t - now) / 60000);
  const abs = Math.abs(d);
  const span = abs < 60 ? abs + ' min' : abs < 1440 ? Math.round(abs / 60) + ' h' : Math.round(abs / 1440) + ' d';
  return d >= 0 ? 'in ' + span : span + ' ago';
};

// The values the scheduling card and the Hoist Crons panel render. Pure over state.
export function cronVals(c) {
  const s = c.state;
  const f = s.cronForm || {};
  const cat = Array.isArray(s.cronCatalogue) ? s.cronCatalogue : null;
  const byKey = {};
  (cat || []).forEach((j) => { byKey[j.key] = j; });
  const now = Date.now();
  const jobs = (s.cronJobs || []);
  return {
    cronOpen: !!s.cronOpen,
    cronBusy: !!s.cronBusy,
    cronMsg: s.cronMsg || '',
    cronJobOpts: CRON_JOBS.map((j) => ({
      key: j.key, label: (byKey[j.key] && byKey[j.key].label) || j.label, module: j.module,
      desc: (byKey[j.key] && byKey[j.key].description) || '',
      on: (f.jobs || []).includes(j.key), toggle: () => c.cronToggleJob(j.key)
    })),
    cronFreqOpts: CRON_FREQS.map((x) => ({ key: x.key, label: x.label, on: f.freq === x.key, pick: () => c.cronSet('freq', x.key) })),
    cronShowTime: !!(CRON_FREQS.find((x) => x.key === f.freq) || {}).time,
    cronShowDays: !!(CRON_FREQS.find((x) => x.key === f.freq) || {}).days,
    cronTime: f.time || '07:00',
    cronSetTime: (e) => c.cronSet('time', e.target.value),
    cronDays: DAY_NAMES.map((d, i) => ({ label: d, on: (f.days || []).includes(i), toggle: () => c.cronToggleDay(i) })),
    cronNeedsPrompt: (f.jobs || []).includes('question'),
    cronPrompt: f.prompt || '',
    cronSetPrompt: (e) => c.cronSet('prompt', e.target.value),
    cronEmail: !!f.email,
    cronToggleEmail: () => c.cronSet('email', !f.email),
    cronEmailTo: (s.account && s.account.email) || 'your address',
    cronRunNow: !!f.runNow,
    cronToggleRunNow: () => c.cronSet('runNow', !f.runNow),
    cronCreate: () => c.cronCreate(),
    cronClose: () => c.cronClose(),
    cronCreateLabel: s.cronBusy ? 'Scheduling…' : 'Create ' + ((f.jobs || []).length || '') + ((f.jobs || []).length === 1 ? ' job' : ' jobs'),
    cronZone: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return 'UTC'; } })(),
    // the Hoist Crons panel
    cronHas: jobs.length > 0,
    cronCanManage: !!s.cronCanManage,
    cronSchedule: () => c.cronOpenBlank(),
    cronRows: jobs.map((j) => {
      const last = (j.runs || [])[0];
      return {
        id: j.id, name: j.name, module: j.module, cadence: j.refresh_label,
        status: j.enabled ? j.status : 'paused', tone: STATUS_TONE[j.enabled ? j.status : 'paused'] || 'var(--color-neutral-400)',
        next: j.enabled && j.next_run_at ? whenShort(j.next_run_at, now) : 'paused',
        last: last ? (last.ok ? summaryLine(last.summary) || 'ran' : 'failed: ' + String(last.error || '').slice(0, 80)) +
          ' · ' + whenShort(last.finished_at, now) : 'not run yet',
        lastOk: last ? !!last.ok : null,
        by: (j.created_by && j.created_by.email) || j.owner_email || '',
        lastBy: last ? (last.requested_by || '') : '',
        // The last 14 days, oldest first: green all ran, amber some failed, red all failed, grey none ran.
        days: ((s.cronDaily || {})[j.id] || []).map((d) => ({
          date: d.date,
          tone: d.status === 'ok' ? 'var(--st-ok)' : d.status === 'failed' ? 'var(--st-risk)' : d.status === 'partial' ? 'var(--st-warn)' : 'var(--color-divider)',
          title: d.date + ': ' + (d.runs ? d.runs + (d.runs === 1 ? ' run' : ' runs') + (d.failed ? ', ' + d.failed + ' failed' : ', all ok') +
            (d.last_summary ? ' · ' + summaryLine(d.last_summary) : '') : 'no run')
        })),
        running: s.cronRunning === j.id,
        run: () => c.cronRun(j.id),
        pause: () => c.cronPause(j.id, !j.enabled), pauseLabel: j.enabled ? 'Pause' : 'Resume',
        remove: () => c.cronRemove(j.id), removeArmed: s.cronRemoveArmed === j.id
      };
    })
  };
}

// One line for a run's figures: {meters_scanned: 58, alerts_created: 3} → "58 meters scanned · 3 alerts created".
export function summaryLine(summary) {
  if (!summary || typeof summary !== 'object') return '';
  const mail = summary.emailed_to
    ? (summary.email_status === 'failed' ? 'email to ' + summary.emailed_to + ' failed' : 'emailed to ' + summary.emailed_to)
    : '';
  const rest = Object.entries(summary)
    .filter(([k, v]) => !['ok', 'tools', 'emailed_to', 'email_status', 'email_error'].includes(k) &&
      (typeof v === 'number' || (typeof v === 'string' && v.length < 40)))
    .slice(0, 4)
    .map(([k, v]) => (typeof v === 'number' ? v.toLocaleString('en-GB') + ' ' : v + ' ') + k.replace(/_/g, ' '));
  return (mail ? rest.concat([mail]) : rest).join(' · ') || (summary.tools !== undefined ? 'answered' : '');
}
