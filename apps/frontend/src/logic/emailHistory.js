// A request the company already sent, drafted again: the dock offers a reminder rather than a
// second copy of the same request. What counts as "already sent" is the platform's own email
// log (GET /api/approvals/sent-emails) - only mail that really left, matched on the subject.

export const REMINDER_PREFIX = 'Reminder: ';

export function baseSubject(subject) {
  let s = String(subject || '').trim();
  while (s.toLowerCase().startsWith(REMINDER_PREFIX.toLowerCase())) s = s.slice(REMINDER_PREFIX.length).trim();
  return s;
}

const when = (iso) => {
  if (!iso) return 'an earlier date';
  const d = new Date(iso);
  if (isNaN(d)) return String(iso);
  return d.toLocaleDateString('en-GB', { day: '2-digit', month: 'short', year: 'numeric' })
    + ', ' + d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
};
const day = (iso) => when(iso).split(',')[0];

// "You sent this request on 30 Sep 2026, 12:00 to x@y.com · 1 reminder since, last 02 Oct 2026."
export function historyNote(hist) {
  if (!hist || !hist.count) return '';
  const first = (hist.items || []).filter((i) => !i.reminder).slice(-1)[0] || (hist.items || []).slice(-1)[0] || {};
  const r = Number(hist.reminders) || 0;
  return 'You sent this request on ' + when(hist.first_sent_at) + (first.to ? ' to ' + first.to : '')
    + (r ? ' · ' + r + (r === 1 ? ' reminder' : ' reminders') + ' since, the last on ' + day(hist.last_sent_at) : '')
    + '.';
}

// The same request as a reminder: its subject says so, its body opens by naming what was sent
// and when, then carries the original request, and it goes to the address last used when the
// draft has none of its own.
export function reminderDraft(draft, hist) {
  const d = draft || {};
  const body = String(d.body || '');
  const m = /^(Hello[^\n]*\n)\n?/.exec(body);
  const greeting = m ? m[1] : '';
  const rest = m ? body.slice(m[0].length) : body;
  const r = Number(hist && hist.reminders) || 0;
  const line = 'A reminder of our request of ' + day(hist && hist.first_sent_at)
    + (r ? ', followed up on ' + day(hist.last_sent_at) : '')
    + ', which we have not yet had a reply to. The original request is below.';
  return {
    kicker: String(d.kicker || 'Draft').replace(/·\s*draft\s*$/i, '· reminder'),
    to: String(d.to || '').trim() || (hist && hist.last_to) || '',
    subject: REMINDER_PREFIX + baseSubject(d.subject),
    body: (greeting ? greeting + '\n' : '') + line + '\n\n— Original request —\n' + rest
  };
}

// The dock's check, run as a draft opens: ask the log, and when the request already went out,
// turn the draft into a reminder - unless the reader has started editing it meanwhile. The
// original stays in emOrig, so "send as a new request instead" is one click back.
export const emailHistoryMethods = {
  async emCheckHistory() {
    const s0 = this.state;
    const subject = baseSubject(s0.emSubject);
    if (!subject || s0.flow !== 'email') return;
    const token = (this._emHistToken = (this._emHistToken || 0) + 1);
    const body0 = s0.emBody;
    this.setState({ emPrev: null, emOrig: null, emReminder: false });
    let hist = null;
    try {
      const { opsApi } = await import('../api/opsIntelligence.js');
      hist = await opsApi.sentEmails(subject);
    } catch (e) {
      return;                                          // no history is no reason to stop a draft
    }
    const s = this.state;
    if (token !== this._emHistToken || s.flow !== 'email' || !hist || !hist.count) return;
    if (baseSubject(s.emSubject) !== subject || s.emBody !== body0) return;
    const orig = { kicker: s.emKicker, to: s.emTo, subject: s.emSubject, body: s.emBody };
    const r = reminderDraft(orig, hist);
    this.setState({ emPrev: hist, emOrig: orig, emReminder: true,
      emKicker: r.kicker, emTo: r.to, emSubject: r.subject, emBody: r.body });
  },
  emUseOriginal() {
    const o = this.state.emOrig;
    if (!o) return;
    this.setState({ emReminder: false, emKicker: o.kicker, emTo: o.to || this.state.emTo, emSubject: o.subject, emBody: o.body });
  },
  emUseReminder() {
    const o = this.state.emOrig, h = this.state.emPrev;
    if (!o || !h) return;
    const r = reminderDraft(o, h);
    this.setState({ emReminder: true, emKicker: r.kicker, emTo: r.to, emSubject: r.subject, emBody: r.body });
  }
};
