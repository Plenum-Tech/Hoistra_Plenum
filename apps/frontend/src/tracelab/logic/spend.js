// spend — the Platform cost page's evaluation line (attachment A3, EvalSpendLine.jsx): the page's month label
// ("October 2026") as the key the spend route takes ("2026-10"), and the line itself. Pure.
import { fmtUsd } from '../../logic/platformCost.js';

const MONTHS = ['january', 'february', 'march', 'april', 'may', 'june', 'july', 'august', 'september', 'october', 'november', 'december'];

export function monthKeyOf(label) {
  const s = String(label || '').trim();
  if (/^\d{4}-(0[1-9]|1[0-2])$/.test(s)) return s;
  const m = /^([A-Za-z]+) (\d{4})$/.exec(s);
  if (!m) return null;
  const i = MONTHS.indexOf(m[1].toLowerCase());
  return i < 0 ? null : m[2] + '-' + String(i + 1).padStart(2, '0');
}

// "Evaluation $X, not included in the totals above" for a month the service answered; '' for anything else, so
// a missing or broken Trace Lab leaves the page exactly as it was.
export function spendText(data) {
  if (!data || data.total_usd === null || data.total_usd === undefined || data.total_usd === '') return '';
  const n = Number(data.total_usd);
  if (!isFinite(n) || n < 0) return '';
  return 'Evaluation ' + (n === 0 ? '$0.00' : fmtUsd(n)) + ', not included in the totals above';
}
