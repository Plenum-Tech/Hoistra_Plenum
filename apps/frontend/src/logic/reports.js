// reports — a custom report card is a pinned prompt, re-run on a cadence.
//
// Built from a session: the session's question becomes the card's prompt, the cadence says
// how often it is asked again, and each run is the orchestrator's answer to that prompt on a
// fresh thread. This used to live entirely in this browser (localStorage + a 30s client
// timer); svc-operations-intelligence now owns it for real — engines/reports/cards.py is the
// engine, engines/reports/scheduler.py is the server-side refresh loop (FOR UPDATE SKIP
// LOCKED, decoupled from any tab being open), and api/routes/reports.py is the REST contract.
// This module is now a thin client over that API: it loads the caller's reports+cards,
// creates/runs/deletes cards through the backend, and polls so a refresh the SERVER made
// while this tab was idle still shows up here.
//
// The pure functions are tested in test/reports.test.mjs; the methods at the bottom are mixed
// into HoistraLogic.prototype and `this` is the controller.
import { reportsApi } from '../api/reports.js';
import { fmtDateTime } from './homeLive.js';
import { saveHidden, withHidden, withNoneHidden } from './reportCards.js';

export const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

// Mirrors engines/reports/cards.py's PRESETS — used until GET /api/reports/refresh-options
// answers, and as a safety net if it never does, so the "Create report" menu always has
// something to show. The server's own list (same keys) wins the moment it loads.
export const FALLBACK_PRESETS = [
  { key: '30m', label: 'Refresh every 30 minutes', badge: '30 min' },
  { key: '1h', label: 'Refresh every 1 hour', badge: '1 hr' },
  { key: '6h', label: 'Refresh every 6 hours', badge: '6 hr' },
  { key: '12h', label: 'Refresh every 12 hours', badge: '12 hr' },
  { key: '24h', label: 'Refresh every 24 hours', badge: '24 hr' },
  { key: 'daily', label: 'Refresh daily at 02:00', badge: 'Daily' },
  { key: 'days', label: 'Refresh on chosen days', badge: 'Days', pick_days: true }
];

// The zone a clock cadence ("daily at 02:00") is read in. Browsers still report the LEGACY
// IANA alias for a good many zones — Chrome on an Indian Mac says "Asia/Calcutta", never
// "Asia/Kolkata" — and a server whose tzdata ships without the backward-compatibility links
// (Debian moved them out into tzdata-legacy) refuses those names outright, which 422'd the
// entire create. Canonicalise the aliases a browser actually emits; rpCreate's UTC retry
// covers whatever is left.
const TZ_ALIASES = {
  'Asia/Calcutta': 'Asia/Kolkata', 'Asia/Saigon': 'Asia/Ho_Chi_Minh', 'Asia/Katmandu': 'Asia/Kathmandu',
  'Asia/Rangoon': 'Asia/Yangon', 'Asia/Dacca': 'Asia/Dhaka', 'Asia/Macao': 'Asia/Macau',
  'Asia/Chungking': 'Asia/Shanghai', 'Asia/Chongqing': 'Asia/Shanghai', 'Asia/Harbin': 'Asia/Shanghai',
  'Asia/Istanbul': 'Europe/Istanbul', 'Asia/Thimbu': 'Asia/Thimphu', 'Asia/Ulan_Bator': 'Asia/Ulaanbaatar',
  'Europe/Kiev': 'Europe/Kyiv', 'Europe/Uzhgorod': 'Europe/Kyiv', 'Europe/Zaporozhye': 'Europe/Kyiv',
  'Europe/Nicosia': 'Asia/Nicosia', 'Atlantic/Faeroe': 'Atlantic/Faroe',
  'America/Buenos_Aires': 'America/Argentina/Buenos_Aires', 'America/Catamarca': 'America/Argentina/Catamarca',
  'America/Cordoba': 'America/Argentina/Cordoba', 'America/Jujuy': 'America/Argentina/Jujuy',
  'America/Mendoza': 'America/Argentina/Mendoza', 'America/Rosario': 'America/Argentina/Cordoba',
  'America/Indianapolis': 'America/Indiana/Indianapolis', 'America/Fort_Wayne': 'America/Indiana/Indianapolis',
  'America/Louisville': 'America/Kentucky/Louisville', 'America/Godthab': 'America/Nuuk',
  'America/Atka': 'America/Adak', 'America/Ensenada': 'America/Tijuana', 'America/Santa_Isabel': 'America/Tijuana',
  'Africa/Asmera': 'Africa/Asmara', 'Africa/Timbuktu': 'Africa/Bamako',
  'Pacific/Ponape': 'Pacific/Pohnpei', 'Pacific/Truk': 'Pacific/Chuuk', 'Pacific/Samoa': 'Pacific/Pago_Pago',
  'Australia/Canberra': 'Australia/Sydney', 'Australia/ACT': 'Australia/Sydney', 'Australia/NSW': 'Australia/Sydney',
  'Australia/Queensland': 'Australia/Brisbane', 'Australia/Victoria': 'Australia/Melbourne',
  'Australia/West': 'Australia/Perth', 'Australia/South': 'Australia/Adelaide',
  'GMT': 'UTC', 'UCT': 'UTC', 'Universal': 'UTC', 'Zulu': 'UTC', 'Etc/UTC': 'UTC', 'Etc/GMT': 'UTC'
};

export function browserTimezone(raw) {
  let z = raw;
  if (z === undefined) {
    try { z = Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { z = null; }
  }
  const name = String(z || '').trim();
  if (!name) return 'UTC';
  return TZ_ALIASES[name] || name;
}

// Only a clock cadence is read in the owner's zone. An interval one ("every 30 minutes") is
// plain arithmetic, so falling back to UTC changes nothing a person would notice — worth
// knowing, because it decides whether a zone fallback is worth interrupting them about.
export function cadenceIsClockBound(refresh) {
  if (typeof refresh === 'string') return refresh === 'daily' || refresh === 'days';
  return !!(refresh && (refresh.daily_at || refresh.days || refresh.time));
}

const STATUS_BADGE = { pending: 'Pending', running: 'Running', error: 'Failed', paused: 'Paused' };

// The compact cadence, for the sidebar's badge column. The server sends `refresh_label` as
// a sentence fragment ("every 30 minutes") meant for a line of prose — in a 60px badge it
// truncates to nothing useful — so the short form is derived from the `refresh` object the
// same response carries, matching the badge vocabulary the server's own PRESETS use.
export function refreshBadge(refresh) {
  if (!refresh || typeof refresh !== 'object') return '';
  if (refresh.daily_at) return 'Daily';
  if (Array.isArray(refresh.days)) {
    const d = refresh.days.filter((x) => x >= 0 && x <= 6).map((x) => DAYS[x]);
    return d.length === 7 ? 'Daily' : d.length ? d.join(' · ') : 'Days';
  }
  const n = Number(refresh.every_minutes);
  if (!isFinite(n) || n <= 0) return '';
  if (n < 60) return n + ' min';
  if (n >= 2880 && n % 1440 === 0) return (n / 1440) + ' d';
  return (n % 60 === 0 ? n / 60 : (n / 60).toFixed(1)) + ' hr';
}

export function cardStatusBadge(card) {
  if (!card) return '';
  return STATUS_BADGE[card.status] || refreshBadge(card.refresh) || card.refresh_label || '';
}

const POLL_MS = 30000;

// Every report's cards, flattened into one array — each keeps its own id/report_id, plus
// the parent report's name for display. The grid and the sidebar list both read this.
export function flattenCards(reports) {
  const out = [];
  (reports || []).forEach((r) => {
    (r.cards || []).forEach((c) => out.push(Object.assign({ report_name: r.name }, c)));
  });
  return out;
}

const uniq = (xs) => (xs || []).filter((x, i, a) => x && a.indexOf(x) === i);
const cell = (v) => String(v === null || v === undefined ? '' : v).replace(/\|/g, '\\|').replace(/\s+/g, ' ').trim();

// The export. Markdown, because the answers are markdown; a structured compliance answer is
// flattened into headings, a figure list and a certificate table. `card`/`run` are the
// server's own shapes (card_to_dict / run_to_dict in engines/reports/cards.py).
export function cardMarkdown(card, run) {
  const lines = ['# ' + (card.name || 'Untitled report'), ''];
  lines.push('_Custom report · asks “' + card.prompt + '” · ' + (card.refresh_label || '') + '_');
  if (!run) {
    lines.push('', 'This report has not run yet.');
    return lines.join('\n') + '\n';
  }
  const calls = uniq((run.tool_calls || []).map((t) => (t && t.tool) || t));
  lines.push('_Refreshed ' + fmtDateTime(run.ran_at) +
    (typeof run.duration_ms === 'number' ? ' · ' + Math.round(run.duration_ms / 1000) + ' s' : '') +
    (calls.length ? ' · tools: ' + calls.join(', ') : '') + '_', '');
  if (run.error || run.ok === false) {
    lines.push('**This refresh did not complete.** ' + (run.error || ''));
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

function patchCardInReports(reports, cardId, patch) {
  return (reports || []).map((r) => Object.assign({}, r, {
    cards: (r.cards || []).map((c) => (c.id === cardId ? Object.assign({}, c, patch) : c))
  }));
}

// ── controller ───────────────────────────────────────────────────────────────
export const reportsMethods = {
  // Who these rows belong to. A report is personal — /api/reports filters on the caller's
  // user_id — so the array in state is only ever the rows of whoever was signed in when it
  // was read. One tab routinely sees more than one account (sign out, sign in as someone
  // else), so the rows are stamped with that account and renderVals shows them only to it.
  rpOwner() {
    const a = this.state.account;
    return a && a.email ? String(a.email).trim().toLowerCase() : null;
  },

  async rpLoad(opts) {
    if (this._rpLoading) return;
    this._rpLoading = true;
    // Captured before the read, compared after: a response issued for the previous account
    // must not land on this one's navigator, and must not overwrite rows already read
    // correctly for it either.
    const asked = this.rpOwner();
    this.setState({ reportsLoading: true });
    try {
      const r = await reportsApi.list();
      const reports = (r && Array.isArray(r.reports)) ? r.reports : [];
      if (this.rpOwner() !== asked) return;
      this.setState({ reports: reports, reportsOwner: asked, reportsLoading: false, reportsError: '', reportsLoadedAt: Date.now() });
      if (opts && opts.announce) this.flash('Reports refreshed.');
    } catch (e) {
      if (this.rpOwner() !== asked) return;
      this.setState({ reportsLoading: false, reportsError: (e && e.message) || String(e) });
      if (opts && opts.announce) this.flash('Could not refresh reports — ' + ((e && e.message) || String(e)));
    } finally {
      this._rpLoading = false;
    }
  },

  // GET /api/reports/refresh-options answers {ok, options, days, min_every_minutes,
  // max_every_minutes} — `options`, not `presets`. Reading the wrong key here meant the
  // server's own cadence menu never loaded and the hardcoded fallback silently stood in
  // for it forever.
  async rpLoadPresets() {
    try {
      const r = await reportsApi.refreshOptions();
      const presets = (r && Array.isArray(r.options)) ? r.options : [];
      if (presets.length) this.setState({ reportPresets: presets });
    } catch (e) { /* the fallback list already in state keeps the menu usable */ }
  },

  // Mount: load once, then poll — the server's own scheduler (engines/reports/scheduler.py)
  // refreshes due cards independently of this tab, so a plain load-once would show a card
  // stuck on "pending" long after the server actually answered it.
  rpStart() {
    this.rpStop();
    // A tab on the sign-in gate has no token to read with; loadLiveData() does both of
    // these once authEnter lets the person in.
    if (this.state.signedIn) {
      this.rpLoad();
      this.rpLoadPresets();
    }
    this._rpTimer = setInterval(() => { if (this.state.signedIn) this.rpLoad(); }, POLL_MS);
  },
  rpStop() { clearInterval(this._rpTimer); clearTimeout(this._rpArmTimer); },

  // Deleting is the one report action the server cannot undo — the row is soft-removed and
  // never comes back through any route here — so a click arms a 4s confirm window instead of
  // firing, and a second click on the SAME control inside it is the confirm. Same shape as
  // the Users screen's suspend/delete (users.js's usArm), for the same reason.
  rpArm(key) {
    clearTimeout(this._rpArmTimer);
    this.setState({ rpArmed: key });
    this._rpArmTimer = setTimeout(() => this.setState((p) => (p.rpArmed === key ? { rpArmed: null } : {})), 4000);
  },
  rpDisarm() {
    clearTimeout(this._rpArmTimer);
    this.setState({ rpArmed: null });
  },

  // The navigator menu's Create report. Source = a chat session (its question is the prompt).
  async rpCreate() {
    const s = this.state;
    const chats = (s.sessions || []).filter((q) => q.kind === 'chat');
    const src = chats.find((q) => q.id === s.reportSrcId) || chats[0] || null;
    if (!src) return this.flash('Ask something first — a report card is built from a session’s question.');
    const presets = (s.reportPresets && s.reportPresets.length) ? s.reportPresets : FALLBACK_PRESETS;
    const preset = presets[s.reportCad] || presets[0];
    if (!preset) return this.flash('Refresh options have not loaded yet — try again in a moment.');
    let refresh = preset.key;
    if (preset.pick_days) {
      if (!(s.reportDays || []).length) return this.flash('Pick at least one day for the refresh.');
      refresh = { days: s.reportDays.slice(), time: s.reportTime || '14:00' };
    }
    const body = {
      prompt: src.title,
      name: (s.reportName || '').trim() || src.title,
      refresh: refresh,
      timezone: browserTimezone(),
      source_session_id: src.id,
      source_page: src.page,
      run_now: true
    };
    this.setState({ reportMenu: false, reportName: '' });
    let out;
    try {
      out = await this.rpPostCard(body);
    } catch (e) {
      return this.flash('Could not create the report card — ' + ((e && e.message) || String(e)));
    }
    // POST /api/reports/cards answers {ok, report, card} — the card is nested, and reading
    // the envelope as the card itself left rpOpen with an undefined id.
    const card = out.card;
    this.flash(out.tzFellBack && cadenceIsClockBound(refresh)
      ? 'Report card created, but this server does not recognise your timezone (' + body.timezone + ') — the refresh time is read as UTC.'
      : 'Report card created — the first refresh is running.');
    await this.rpLoad();
    if (card && card.id) this.rpOpen(card.id);
  },

  // One create, with a single retry on a zone the server will not accept. A cadence that is
  // pure arithmetic ("every hour") does not care which zone it is stamped with, and a card
  // the person asked for is worth more than the zone field — so the card gets made either
  // way, and rpCreate says so out loud when the zone actually changes what time it runs.
  async rpPostCard(body) {
    try {
      return { card: (await reportsApi.createCard(body)).card, tzFellBack: false };
    } catch (e) {
      if (!(e && e.reason === 'bad_timezone') || body.timezone === 'UTC') throw e;
      const r = await reportsApi.createCard(Object.assign({}, body, { timezone: 'UTC' }));
      return { card: r.card, tzFellBack: true };
    }
  },

  // Refresh now — hits the same route the server's own scheduler calls, so the result is
  // wired through the identical run_card() lifecycle (owner-scoped token, audit trail, etc).
  async rpRunCard(cardId) {
    if (this._rpRunBusy) return;
    this._rpRunBusy = cardId;
    this.setState((p) => ({ reports: patchCardInReports(p.reports, cardId, { status: 'running' }) }));
    try {
      await reportsApi.runCard(cardId);
    } catch (e) {
      // 409 already_running is the server's own scheduler having claimed this card first —
      // not a failure, and the reload below shows the run it is already doing.
      if (!(e && e.reason === 'already_running')) this.flash('Refresh failed — ' + ((e && e.message) || String(e)));
    } finally {
      this._rpRunBusy = null;
      await this.rpLoad();
    }
  },

  rpOpen(cardId) {
    this.setState({ view: 'report', reportKey: cardId, reportRunIdx: 0, navOpen: true, detail: null, queueOpen: false, paletteOpen: false });
    if (typeof window !== 'undefined' && window.scrollTo) window.scrollTo(0, 0);
  },

  async rpDeleteCard(cardId) {
    // One DELETE per card at a time: the confirm click can be repeated faster than the
    // round trip, and the second request would 404 against a card the first already removed.
    const busy = this._rpDeleting || (this._rpDeleting = {});
    if (busy[cardId]) return;
    busy[cardId] = true;
    try {
      await reportsApi.deleteCard(cardId);
    } catch (e) {
      delete busy[cardId];
      return this.flash('Could not delete — ' + ((e && e.message) || String(e)));
    }
    delete busy[cardId];
    clearTimeout(this._rpArmTimer);
    // The card is gone, so its curation is dead weight — prune it rather than leave the
    // hidden-set growing by one entry for every card the account ever had.
    const pruned = withNoneHidden(this.state.reportHidden, this.state.account, cardId);
    saveHidden(pruned);
    this.setState((p) => Object.assign(
      {
        reports: (p.reports || []).map((r) => Object.assign({}, r, { cards: (r.cards || []).filter((c) => c.id !== cardId) })),
        reportSelected: (p.reportSelected || []).filter((id) => id !== cardId),
        reportHidden: pruned,
        rpArmed: null
      },
      // Back to the grid of what is left, not out to Home — the person was working in
      // reports and deleting one card is no reason to leave.
      p.view === 'report' && p.reportKey === cardId ? { reportKey: null } : {}
    ));
    this.flash('Report card deleted.');
  },

  // The report itself — the container the cards sit in. DELETE /api/reports/{id} takes every
  // card on it with it, which is why this is the one delete that says how many.
  async rpDeleteReport(reportId) {
    const report = (this.state.reports || []).find((r) => r.id === reportId);
    const goneIds = report ? (report.cards || []).map((c) => c.id) : [];
    try {
      await reportsApi.remove(reportId);
    } catch (e) {
      return this.flash('Could not delete the report — ' + ((e && e.message) || String(e)));
    }
    clearTimeout(this._rpArmTimer);
    this.setState((p) => Object.assign(
      {
        reports: (p.reports || []).filter((r) => r.id !== reportId),
        reportSelected: (p.reportSelected || []).filter((id) => goneIds.indexOf(id) === -1),
        rpArmed: null
      },
      // The card on the page went with it — fall back to the grid rather than a page bound
      // to an id the server no longer has.
      p.view === 'report' && goneIds.indexOf(p.reportKey) > -1 ? { reportKey: null } : {}
    ));
    this.flash(goneIds.length
      ? 'Report deleted, with ' + goneIds.length + ' card' + (goneIds.length === 1 ? '' : 's') + ' on it.'
      : 'Report deleted.');
  },

  // ── curating the cards inside one report ───────────────────────────────────
  // A view preference, not a server fact: the backend has no notion of a hidden section, and
  // the answer itself is never altered — Export still writes the whole thing. Stored per
  // account, per report card, so it survives every refresh and every reload.
  rcHide(key) {
    const id = this.state.reportKey;
    if (!id || !key) return;
    const next = withHidden(this.state.reportHidden, this.state.account, id, key, true);
    this.setState({ reportHidden: next });
    saveHidden(next);
  },
  rcRestore(key) {
    const id = this.state.reportKey;
    if (!id || !key) return;
    const next = withHidden(this.state.reportHidden, this.state.account, id, key, false);
    this.setState({ reportHidden: next });
    saveHidden(next);
  },
  rcRestoreAll() {
    const id = this.state.reportKey;
    if (!id) return;
    const next = withNoneHidden(this.state.reportHidden, this.state.account, id);
    this.setState({ reportHidden: next, reportTrayOpen: false });
    saveHidden(next);
  },
  rcToggleTray() { this.setState((p) => ({ reportTrayOpen: !p.reportTrayOpen })); },

  // A card shows its picture and its headline; the sentences behind it open on demand. Not
  // persisted — which paragraph you had open is not a preference, it is where you were
  // looking a moment ago.
  rcToggleOpen(key) {
    this.setState((p) => {
      const open = p.reportOpenBlocks || [];
      return { reportOpenBlocks: open.indexOf(key) > -1 ? open.filter((k) => k !== key) : open.concat(key) };
    });
  },

  rpToggleSelect(cardId) {
    this.setState((p) => ({
      reportSelected: (p.reportSelected || []).indexOf(cardId) > -1
        ? p.reportSelected.filter((id) => id !== cardId)
        : (p.reportSelected || []).concat(cardId)
    }));
  },
  rpClearSelection() { this.setState({ reportSelected: [] }); },

  // Bulk delete — every selected card, in one round of requests. A card that fails to
  // delete server-side stays in the list (and selected), so the user can see and retry it.
  async rpDeleteSelected() {
    const ids = (this.state.reportSelected || []).slice();
    if (!ids.length) return;
    const results = await Promise.allSettled(ids.map((id) => reportsApi.deleteCard(id)));
    const ok = [], failed = [];
    ids.forEach((id, i) => (results[i].status === 'fulfilled' ? ok : failed).push(id));
    clearTimeout(this._rpArmTimer);
    this.setState((p) => Object.assign(
      {
        reports: (p.reports || []).map((r) => Object.assign({}, r, { cards: (r.cards || []).filter((c) => ok.indexOf(c.id) === -1) })),
        reportSelected: failed,
        rpArmed: null
      },
      p.view === 'report' && ok.indexOf(p.reportKey) > -1 ? { reportKey: null } : {}
    ));
    this.flash(failed.length
      ? ok.length + ' deleted, ' + failed.length + ' failed — try again.'
      : ok.length + ' report card' + (ok.length === 1 ? '' : 's') + ' deleted.');
  },

  // Saves the run in view as a markdown file, client-side.
  rpExport(cardId, runIdx) {
    const card = flattenCards(this.state.reports).find((c) => c.id === cardId);
    if (!card) return;
    const run = (card.runs || [])[runIdx || 0] || card.latest_run || null;
    const md = cardMarkdown(card, run);
    if (typeof document === 'undefined' || typeof window === 'undefined' || !window.URL || !window.URL.createObjectURL) {
      return this.flash('Export needs a browser.');
    }
    const url = window.URL.createObjectURL(new Blob([md], { type: 'text/markdown;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = (card.name || 'report').replace(/[^\w.-]+/g, '-').replace(/^-+|-+$/g, '').toLowerCase() + '.md';
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
    this.flash('Saved ' + a.download);
  }
};
