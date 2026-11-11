// format — small pure helpers the lab pages share. They lived in QuestionPage.jsx until Runs and its question page were
// deleted (11 Nov 2026); Sessions, Scenarios and the session page read them from here.

export const plural = (n, one, many) => (Number(n) || 0) + ' ' + (Number(n) === 1 ? one : many);

// Reading recorded conversations in, importing scenarios and evaluating: company admins, the platform team and the
// consultant.
export const isCurator = (me) => !!(me && (me.all_companies || me.manages));

function toDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? null : d;
}

// "22 Oct 2026, 09:14" in the reader's time zone.
export function fmtWhen(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' }) : '';
}

export function fmtTime(iso) {
  const d = toDate(iso);
  return d ? d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' }) : '';
}

// "22 Oct 2026" for a calendar day ("2026-10-22" is that day wherever the reader is, never shifted by the time zone).
export function fmtDay(v) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(v || ''));
  if (m) return new Date(Date.UTC(+m[1], +m[2] - 1, +m[3])).toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  const d = toDate(v);
  return d ? d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric' }) : '';
}

// "21 Oct 2026, 09:14 → 22 Oct 2026, 17:40", or "22 Oct 2026, 09:14 → 17:40" within one day.
export function fmtRange(a, b) {
  const x = toDate(a);
  const y = toDate(b);
  if (!x && !y) return '';
  if (!x || !y || x.getTime() === y.getTime()) return fmtWhen(x ? a : b);
  return fmtWhen(a) + ' → ' + (x.toDateString() === y.toDateString() ? fmtTime(b) : fmtWhen(b));
}
