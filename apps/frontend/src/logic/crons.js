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
import { scrubInternal } from './publicText.js';

// The catalogue as the service names it; read from GET /api/crons/catalogue when it answers,
// this copy only labels the card until then.
// perBuilding: the job can be scheduled for one building, not only company-wide.
export const CRON_JOBS = [
  { key: 'energy_anomaly_scan', label: 'Energy anomaly scan', module: 'Energy', perBuilding: true, words: /anomal|energy scan|consumption spike|baseline drift/ },
  { key: 'energy_chiller_scan', label: 'Chiller efficiency scan', module: 'Energy', words: /chiller/ },
  { key: 'energy_condition_scan', label: 'Asset condition scan', module: 'Assets', perBuilding: true, words: /condition|asset health|threat|watch/ },
  { key: 'energy_meter_gaps', label: 'Meter gap check', module: 'Energy', perBuilding: true, words: /meter gap|gaps|missing reading|readings stopped/ },
  { key: 'energy_benchmarks', label: 'Benchmark validation', module: 'Energy', perBuilding: true, words: /benchmark|eui|tm46/ },
  { key: 'compliance_expiry_scan', label: 'Compliance expiry scan', module: 'Compliance', perBuilding: true, words: /compliance|certificate|expir|lapse/ },
  { key: 'compliance_reverify', label: 'Certificate re-verification', module: 'Compliance', words: /re-?verif|verify|register check/ },
  { key: 'vendor_scorecards_monthly', label: 'Monthly vendor scorecards', module: 'Vendors', perBuilding: true, words: /scorecard|vendor score|vendor performance/ },
  { key: 'maintenance_sla_watch', label: 'Work-order SLA watch', module: 'Maintenance', perBuilding: true, words: /\bsla\b|work orders? (past|over|breach)|overdue work order/ },
  { key: 'maintenance_ppm_due', label: 'PPM due list', module: 'Maintenance', perBuilding: true, words: /ppm due|due ppm|planned (preventive )?maintenance|maintenance plans? due/ },
  { key: 'maintenance_ppm_missed', label: 'Missed PPM follow-up', module: 'Maintenance', perBuilding: true, words: /missed ppm|missed visit|deferred ppm|rebook/ },
  { key: 'maintenance_parts_reorder', label: 'Spare parts reorder', module: 'Maintenance', words: /spare part|reorder|low stock|stock level/ },
  { key: 'maintenance_monthly_summary', label: 'Monthly maintenance summary', module: 'Maintenance', perBuilding: true, words: /maintenance summary|monthly maintenance|maintenance kpi/ },
  { key: 'question', label: 'Ask a question', module: 'Orchestrator', perBuilding: true, words: /ask|report on|summar|brief me|send me/ }
];

// How often, as the card offers it. `refresh` is the service's own shape.
export const CRON_FREQS = [
  { key: '15m', label: 'Every 15 minutes', refresh: { every_minutes: 15 } },
  { key: '30m', label: 'Every 30 minutes', refresh: { every_minutes: 30 } },
  { key: '1h', label: 'Every hour', refresh: { every_minutes: 60 } },
  { key: '6h', label: 'Every 6 hours', refresh: { every_minutes: 360 } },
  { key: '12h', label: 'Every 12 hours', refresh: { every_minutes: 720 } },
  { key: 'daily', label: 'Every day at…', time: true },
  { key: 'days', label: 'On chosen days at…', time: true, days: true },
  { key: 'monthly', label: 'Monthly on day…', time: true, monthly: true }
];
// A day of the month that falls in every month, February included.
export const MONTH_DAYS = Array.from({ length: 28 }, (_, i) => i + 1);
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
  if (/\bmonthly\b|\bevery month\b|\beach month\b|\bmonth[- ]end\b/.test(s)) {
    const md = /\b(\d{1,2})(?:st|nd|rd|th)\b/.exec(s);
    const day = md && Number(md[1]) >= 1 && Number(md[1]) <= 28 ? Number(md[1]) : 1;
    return { freq: 'monthly', time: time || '06:00', monthDay: day };
  }
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
  if (f.monthly) {
    const d = Number(form.monthDay || 1);
    if (!(d >= 1 && d <= 28)) return { error: 'Choose a day of the month, 1 to 28.' };
    return { refresh: { monthly_day: d, time: time } };
  }
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

// The ticked jobs that cannot run for one building, when one is chosen.
export function companyWideOnly(f) {
  if (!f || !f.buildingId) return [];
  return CRON_JOBS.filter((j) => (f.jobs || []).includes(j.key) && !j.perBuilding).map((j) => j.label);
}

// The building a chat request names, by its name in the register; '' for company-wide.
export function buildingFrom(q, buildings) {
  const s = String(q || '').toLowerCase();
  const hit = (buildings || [])
    .filter((b) => b && b.buildingId && b.name && b.name.length >= 3 && s.includes(String(b.name).toLowerCase()))
    .sort((a, b) => b.name.length - a.name.length)[0];
  return hit ? hit.buildingId : '';
}

// A job's extra recipients. The service holds the same limit and the same check.
export const MAX_RECIPIENTS = 10;
const EMAIL_RE = /^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$/;

// Typed text → addresses: split on commas, semicolons and spaces, lower-cased. Returns
// { add: [...], bad: [...] } so a typo stays in the box for fixing instead of vanishing.
export function parseRecipients(text) {
  const add = [], bad = [];
  String(text || '').split(/[,;\s]+/).map((a) => a.trim().toLowerCase()).filter(Boolean).forEach((a) => {
    if (!EMAIL_RE.test(a) || a.length > 254) bad.push(a);
    else if (!add.includes(a)) add.push(a);
  });
  return { add, bad };
}

// Addresses written into a chat request: "… every Monday to fm@x.com and ops@y.com".
export function recipientsFrom(q) {
  return parseRecipients((String(q || '').match(/[^@\s,;<>()]+@[^@\s,;<>()]+\.[a-z]{2,}/gi) || []).join(' ')).add;
}

// The form's recipients with whatever is still typed in the box, or the reason it cannot be sent.
export function recipientsOf(f) {
  const { add, bad } = parseRecipients(f.recipientDraft);
  if (bad.length) return { error: '“' + bad[0] + '” is not an email address.' };
  const all = (f.recipients || []).slice();
  add.forEach((a) => { if (!all.includes(a)) all.push(a); });
  if (all.length > MAX_RECIPIENTS) return { error: 'A job can email at most ' + MAX_RECIPIENTS + ' addresses.' };
  return { recipients: all };
}

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
        recipients: recipientsFrom(q), recipientDraft: '',
        monthDay: cad.monthDay || 1,
        // "… for Bishopsgate Tower" names one building; only jobs that take one keep it.
        buildingId: buildingFrom(q, this.state.bldLive),
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
      cronForm: { jobs: [], freq: '1h', time: '07:00', days: [1], monthDay: 1, prompt: '', recipients: [], recipientDraft: '',
        buildingId: '', runNow: false }
    });
    this.openChat();
    this.cronLoadCatalogue();
  },

  async cronLoadCatalogue() {
    // The building picker reads the Buildings register.
    if (!this.state.bldLive && typeof this.bldLoad === 'function') this.bldLoad();
    if (this.state.cronCatalogue) return;
    try {
      const c = await cronsApi.catalogue();
      this.setState({ cronCatalogue: (c && c.jobs) || null });
    } catch (e) { /* the built-in labels stand in */ }
  },

  cronSet(field, value) {
    this.setState((p) => ({ cronForm: Object.assign({}, p.cronForm || {}, { [field]: value }), cronMsg: '' }));
  },
  // The recipients box: a comma, semicolon, space or Enter turns what is typed into an address;
  // a typo stays in the box with the reason under it.
  cronSetRecipientDraft(value) {
    const v = String(value || '');
    if (/[,;\s]$/.test(v)) return this.cronAddRecipients(v);
    this.cronSet('recipientDraft', v);
  },
  cronAddRecipients(text) {
    const { add, bad } = parseRecipients(text !== undefined ? text : (this.state.cronForm || {}).recipientDraft);
    this.setState((p) => {
      const f = Object.assign({}, p.cronForm || {});
      const all = (f.recipients || []).slice();
      add.forEach((a) => { if (!all.includes(a)) all.push(a); });
      if (all.length > MAX_RECIPIENTS) {
        return { cronMsg: 'A job can email at most ' + MAX_RECIPIENTS + ' addresses.' };
      }
      f.recipients = all;
      f.recipientDraft = bad.join(' ');
      return { cronForm: f, cronMsg: bad.length ? '“' + bad[0] + '” is not an email address.' : '' };
    });
  },
  cronRemoveRecipient(addr) {
    this.setState((p) => {
      const f = Object.assign({}, p.cronForm || {});
      f.recipients = (f.recipients || []).filter((a) => a !== addr);
      return { cronForm: f, cronMsg: '' };
    });
  },
  cronRecipientKey(e) {
    const f = this.state.cronForm || {};
    if (e.key === 'Enter') { e.preventDefault(); this.cronAddRecipients(); }
    else if (e.key === 'Backspace' && !f.recipientDraft && (f.recipients || []).length) {
      this.cronRemoveRecipient(f.recipients[f.recipients.length - 1]);
    }
  },

  cronToggleJob(key) {
    this.setState((p) => {
      const f = Object.assign({}, p.cronForm || {});
      const set = new Set(f.jobs || []);
      const adding = !set.has(key);
      if (set.has(key)) set.delete(key); else set.add(key);
      f.jobs = CRON_JOBS.map((j) => j.key).filter((k) => set.has(k));
      // The scorecards are a month-end job: ticked on their own, they start on the 1st at 06:00.
      if (adding && key === 'vendor_scorecards_monthly' && f.jobs.length === 1) {
        f.freq = 'monthly'; f.monthDay = 1; f.time = '06:00';
      }
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
    const to = recipientsOf(f);
    if (to.error) return this.setState({ cronMsg: to.error });
    const wide = companyWideOnly(f);
    if (wide.length) return this.setState({ cronMsg: wide.join(', ') + (wide.length === 1 ? ' runs' : ' run') + ' company-wide only — untick it or choose All buildings.' });
    this.setState({ cronBusy: true, cronMsg: '' });
    try {
      const out = await cronsApi.create({
        job_keys: f.jobs, refresh: r.refresh, timezone: zone(), prompt: f.prompt || undefined,
        email: (f.jobs || []).includes('question') && !!f.email, recipients: to.recipients,
        building_id: f.buildingId || undefined,
        run_now: !!f.runNow, source_session_id: this.state.sessionId || undefined
      });
      const made = (out && out.jobs) || [];
      const line = made.map((j) => j.label + ' — ' + j.refresh_label).join('; ');
      // From the Hoist Crons page the dialog closes and the list shows the new jobs; the
      // conversation is not where it was asked, so nothing is added to it.
      if (this.state.cpModal === 'new') {
        this.setState({ cronBusy: false, cpModal: null });
        this.flash('Scheduled ' + made.length + (made.length === 1 ? ' job: ' : ' jobs: ') + line + '.');
        this.cronLoad();
        return;
      }
      this.setState((p) => ({
        cronBusy: false, cronOpen: false,
        ccChat: (p.ccChat || []).concat([{ role: 'bot', isNote: true,
          text: 'Scheduled ' + made.length + (made.length === 1 ? ' job: ' : ' jobs: ') + line +
            '. ' + (f.runNow ? 'The first run starts now. ' : '') +
            ((f.jobs || []).includes('question') && f.email ? 'Each answer is emailed to you from admin@hoistra.ai. ' : '') +
            (to.recipients.length ? 'Each result is also emailed to ' + to.recipients.join(', ') + '. ' : '') +
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
      const [d, daily] = await Promise.all([cronsApi.list(zone()), cronsApi.daily(14, zone()).catch(() => null)]);
      const byJob = {};
      ((daily && daily.jobs) || []).forEach((j) => { byJob[j.job_id] = j.days || []; });
      this.setState({ cronJobs: (d && d.jobs) || [], cronRuns: (d && d.recent_runs) || [], cronDaily: byJob,
        cronStats: (d && d.stats) || null, cronCanManage: !!(d && d.can_manage), cronLoadErr: '', cronLoadedAt: Date.now() });
      // The Hoist Crons page's open job follows the same read.
      if (this.state.cpJobId && typeof this.cpLoadDetail === 'function') this.cpLoadDetail(this.state.cpJobId);
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
  // Yours: the jobs this account scheduled (the email in whatever case it was stored).
  const me = String((s.account && s.account.email) || '').trim().toLowerCase();
  const mine = jobs.filter((j) => String((j.created_by && j.created_by.email) || j.owner_email || '').trim().toLowerCase() === me);
  // Yours first when you have crons of your own; otherwise the company's. A plain user cannot
  // create one, and a super-admin viewing as a company did not create that company's, so a
  // "Yours" default would always be empty for them (8 Oct 2026 review).
  const scope = s.cronScope === 'all' || s.cronScope === 'mine' ? s.cronScope : (mine.length ? 'mine' : 'all');
  const shown = scope === 'all' ? jobs : mine;
  return {
    cronOpen: !!s.cronOpen,
    cronBusy: !!s.cronBusy,
    cronMsg: s.cronMsg || '',
    cronJobOpts: CRON_JOBS.map((j) => ({
      key: j.key, label: (byKey[j.key] && byKey[j.key].label) || j.label, module: j.module,
      desc: (byKey[j.key] && byKey[j.key].description) || '',
      // With a building chosen, a company-wide-only job says so instead of being ticked.
      wideOnly: !!f.buildingId && !j.perBuilding,
      on: (f.jobs || []).includes(j.key), toggle: () => c.cronToggleJob(j.key)
    })),
    // Which building: every building, or one of the company's.
    cronBuildings: [{ id: '', name: 'All buildings (company-wide)' }].concat(
      (s.bldLive || []).filter((b) => b && b.buildingId).map((b) => ({ id: b.buildingId, name: b.name + (b.code ? ' · ' + b.code : '') }))),
    cronBuildingId: f.buildingId || '',
    cronSetBuilding: (e) => c.cronSet('buildingId', e.target.value),
    cronBuildingsLoading: !s.bldLive && !!s.bldLoading,
    cronShowMonthDay: !!(CRON_FREQS.find((x) => x.key === f.freq) || {}).monthly,
    cronMonthDays: MONTH_DAYS,
    cronMonthDay: String(f.monthDay || 1),
    cronSetMonthDay: (e) => c.cronSet('monthDay', Number(e.target.value)),
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
    cronRecipients: (f.recipients || []).map((a) => ({ addr: a, remove: () => c.cronRemoveRecipient(a) })),
    cronRecipientDraft: f.recipientDraft || '',
    cronSetRecipientDraft: (e) => c.cronSetRecipientDraft(e.target.value),
    cronRecipientKey: (e) => c.cronRecipientKey(e),
    cronRecipientBlur: () => { if (f.recipientDraft) c.cronAddRecipients(); },
    cronRecipientsFull: (f.recipients || []).length >= MAX_RECIPIENTS,
    cronRunNow: !!f.runNow,
    cronToggleRunNow: () => c.cronSet('runNow', !f.runNow),
    cronCreate: () => c.cronCreate(),
    cronClose: () => c.cronClose(),
    cronCreateLabel: s.cronBusy ? 'Scheduling…' : 'Create ' + ((f.jobs || []).length || '') + ((f.jobs || []).length === 1 ? ' job' : ' jobs'),
    cronZone: (() => { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return 'UTC'; } })(),
    // The Hoist Crons panel on Home, as information (Hussain with Aasim, 8 Oct 2026): for each of
    // YOUR crons - the checks you set up for yourself - what it watches, whether it is running
    // successfully and what it found; the company's one switch away. Running, pausing and
    // removing are on the Hoist Crons page, which a row opens. Everything the engines raised is
    // in Notifications, so the panel no longer repeats it.
    cronHas: shown.length > 0,
    cronCanManage: !!s.cronCanManage,
    cronSchedule: () => c.cronOpenBlank(),
    cronAllJobs: () => c.cpOpen(),
    cronScopeOpts: [['mine', 'Yours', mine.length], ['all', 'Company', jobs.length]].map(([key, label, n]) => ({
      key: key, label: label, n: String(n), on: scope === key, pick: () => c.setState({ cronScope: key })
    })),
    cronSummary: summaryOf(shown),
    cronSummaryFull: (() => {
      const f = shown.filter((j) => { const l = (j.runs || [])[0]; return j.enabled && l && !l.ok; });
      return f.length ? f.length + ' of ' + shown.length + ' failed ' + (f.length === 1 ? 'its' : 'their') + ' last run: ' + f.map((j) => j.name).join(', ')
        : summaryOf(shown);
    })(),
    cronEmpty: s.cronLoadErr && !s.cronLoadedAt ? 'Hoist Crons could not be read — ' + s.cronLoadErr
      : !s.cronLoadedAt ? 'Reading your Hoist Crons…'
      : !jobs.length ? 'No Hoist Crons yet — schedule the checks you want every day' + (s.cronCanManage ? '.' : '; an admin sets them up.')
      : !shown.length ? 'None of yours yet — ' + jobs.length + (jobs.length === 1 ? ' company cron is' : ' company crons are') + ' under Company.'
      : '',
    cronRows: shown.map((j) => {
      const last = (j.runs || [])[0] || null;
      const h = healthOf(j, last);
      const p = j.params || {};
      const nm = j.name || j.label || '';
      const where = p.building_name && !nm.endsWith(p.building_name) ? p.building_name : '';
      const line = oneLiner(j, last, h.label, now);
      const by = (j.created_by && j.created_by.email) || j.owner_email || '';
      return {
        id: j.id, name: j.name || j.label, where: where, health: h.label, tone: h.tone, line: line, failed: h.label === 'Failed',
        // The hover: the same, with what it is set to and who set it, in sentences.
        title: (j.name || j.label) + (where ? ', ' + where : '') + (p.prompt ? ' — asks “' + p.prompt + '”' : '')
          + ' — ' + (j.refresh_label || 'on its schedule') + '. ' + line + '.' + (by ? ' Scheduled by ' + by + '.' : ''),
        // Its page: every run, who did what, and the controls - for whoever can manage it.
        open: s.cronCanManage ? () => { c.cpOpen(); c.cpOpenJob(j.id); } : null
      };
    })
  };
}

// A cron's state as one sentence (Hussain, 8 Oct 2026: "a clear one liner that gives me all
// info"): what it found, when it ran, when it runs next - or why it is not running.
function oneLiner(j, last, health, now) {
  // A run stamped a moment ahead of this clock (server skew) ran "just now", not "in 0 min".
  const ago = last ? (Date.parse(last.finished_at) > now ? 'just now' : whenShort(last.finished_at, now)) : '';
  const next = j.enabled && j.next_run_at ? whenShort(j.next_run_at, now) : '';
  if (health === 'Paused') {
    if (!last) return 'Paused before its first run';
    return 'Paused — last run ' + ago + (last.ok ? ' found ' + findingsOf(last.summary) : ' failed');
  }
  if (health === 'Running') return 'Running now';
  if (health === 'Not run yet') return next ? 'First run ' + next : 'Waiting for its first run';
  if (health === 'Failed') return 'Failed ' + ago + ': ' + scrubInternal(String(last.error || 'no reason given')).slice(0, 160);
  const f = findingsOf(last.summary);
  const mail = mailTrouble(last.summary);
  return f.charAt(0).toUpperCase() + f.slice(1) + ' — ran ' + ago + (next ? ', next ' + next : '') + (mail ? '; ' + mail : '');
}

// A run that went fine but whose email did not: said on the row, because for an "email me" job
// the email is the point (8 Oct 2026 review). summaryLine said so before the rows were rewritten.
function mailTrouble(summary) {
  const sm = summary && typeof summary === 'object' ? summary : {};
  if (sm.email_status !== 'failed' && sm.email_status !== 'partial') return '';
  return 'email to ' + (sm.email_failed || sm.emailed_to || 'its recipients') + ' failed';
}

// The figures a run reports, in plain words. A route's summary is its own top-level numbers
// (engines/crons summarise), named for the code - blocks_set, past_sla, due_in_24h. What needs
// someone comes first, at most three; what it looked at is said only when nothing needs anyone.
const ATTENTION = /block|alert|lapse|expir|past|breach|overdue|miss|fail|due|anomal|gap|reorder|low|held|flag|risk|critical|late/;
const PHRASE = {
  blocks_set: (n) => n + (n === 1 ? ' vendor blocked' : ' vendors blocked'),
  alerts_created: (n) => n + (n === 1 ? ' alert raised' : ' alerts raised'),
  past_sla: (n) => n + ' past SLA',
  due_in_24h: (n) => n + ' due within 24 h',
  expiring_soon: (n) => n + ' expiring soon'
};
const MAIL_KEYS = ['ok', 'tools', 'rich', 'emailed_to', 'email_status', 'email_error', 'email_failed'];
const plainFigure = (k, n) => (PHRASE[k] ? PHRASE[k](n) : n.toLocaleString('en-GB') + ' ' + k.replace(/_/g, ' '));

export function findingsOf(summary) {
  const sm = summary && typeof summary === 'object' ? summary : {};
  const nums = Object.entries(sm).filter(([k, v]) => !MAIL_KEYS.includes(k) && typeof v === 'number' && isFinite(v));
  const attention = nums.filter(([k, v]) => ATTENTION.test(k) && v > 0).slice(0, 3).map(([k, v]) => plainFigure(k, v));
  if (attention.length) return attention.join(', ');
  const looked = nums.find(([k, v]) => !ATTENTION.test(k) && v > 0);
  if (looked) return 'nothing needs attention (' + plainFigure(looked[0], looked[1]) + ')';
  if (sm.tools !== undefined) return 'answered';
  return nums.length ? 'nothing needs attention' : 'ran, with nothing to report';
}

// A job's health from its state and last run.
function healthOf(j, last) {
  if (!j.enabled) return { label: 'Paused', tone: 'var(--color-neutral-600)' };
  if (j.status === 'running') return { label: 'Running', tone: 'var(--color-accent)' };
  if (!last) return { label: 'Not run yet', tone: 'var(--color-neutral-400)' };
  if (!last.ok) return { label: 'Failed', tone: 'var(--st-risk)' };
  return mailTrouble(last.summary) ? { label: 'Email failed', tone: 'var(--st-warn)' } : { label: 'Healthy', tone: 'var(--st-ok)' };
}

// One line over the jobs shown: what failed first, else how they stand.
function summaryOf(list) {
  if (!list.length) return '';
  const n = { Healthy: 0, Failed: 0, 'Email failed': 0, Paused: 0, 'Not run yet': 0, Running: 0 };
  list.forEach((j) => { n[healthOf(j, (j.runs || [])[0] || null).label] += 1; });
  // Short: it shares a line with the Yours / Company switch in a 250px column. The full
  // sentence is the line's tooltip (cronSummaryFull).
  if (n.Failed) return n.Failed + ' of ' + list.length + ' failed' + (n.Paused ? ' · ' + n.Paused + ' paused' : '');
  if (n.Healthy === list.length) return list.length === 1 ? 'Running fine' : 'All ' + list.length + ' running fine';
  return [['Healthy', 'healthy'], ['Email failed', 'email failed'], ['Running', 'running now'], ['Not run yet', 'not run yet'], ['Paused', 'paused']]
    .filter(([k]) => n[k]).map(([k, w]) => n[k] + ' ' + w).join(' · ');
}

// One line for a run's figures: {meters_scanned: 58, alerts_created: 3} → "58 meters scanned · 3 alerts created".
export function summaryLine(summary) {
  if (!summary || typeof summary !== 'object') return '';
  const to = summary.emailed_to ? String(summary.emailed_to).split(/,\s*/) : [];
  const who = to.length > 1 ? to.length + ' people' : to[0];
  const mail = !to.length ? ''
    : summary.email_status === 'failed' ? 'email to ' + who + ' failed'
    : summary.email_status === 'partial' ? 'emailed ' + (to.length - String(summary.email_failed || '').split(/,\s*/).filter(Boolean).length) + ' of ' + to.length
    : 'emailed to ' + who;
  const rest = Object.entries(summary)
    .filter(([k, v]) => !['ok', 'tools', 'emailed_to', 'email_status', 'email_error', 'email_failed'].includes(k) &&
      (typeof v === 'number' || (typeof v === 'string' && v.length < 40)))
    .slice(0, 4)
    .map(([k, v]) => (typeof v === 'number' ? v.toLocaleString('en-GB') + ' ' : v + ' ') + k.replace(/_/g, ' '));
  return (mail ? rest.concat([mail]) : rest).join(' · ') || (summary.tools !== undefined ? 'answered' : '');
}
