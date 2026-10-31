// EvalSpendBody — the evaluation line's body, loaded lazily by EvalSpendLine.jsx only in a VITE_TRACE_LAB=1 build:
// reads the month's evaluation spend from Trace Lab (superadmin only, platform-wide, so it never names a company)
// and shows one quiet line under the Platform cost figures. While reading, on any refusal or failure, or for a
// month it cannot name, it shows nothing.
import React from 'react';
import { apiFetch } from '../../api/client.js';
import { BASE, PREFIX } from '../api.js';
import { monthKeyOf, spendText } from '../logic/spend.js';

export function readMonthSpend(month) {
  return apiFetch(BASE, PREFIX + '/spend', { query: { month }, timeoutMs: 15000 });
}

export function SpendLine({ text }) {
  // Trace Lab lives at /lab/, its own place; the line on Platform cost is the one way there from Hoistra.
  return text ? <p className="pc-quiet" role="status">{text}{' · '}<a href="/lab/">{'Open Trace Lab'}</a></p> : null;
}

export default function EvalSpendBody({ monthLabel }) {
  const month = monthKeyOf(monthLabel);
  const [got, setGot] = React.useState({ month: null, data: null });
  React.useEffect(() => {
    if (!month) return undefined;
    let gone = false;
    readMonthSpend(month).then((d) => { if (!gone) setGot({ month, data: d }); }, () => { if (!gone) setGot({ month, data: null }); });
    return () => { gone = true; };
  }, [month]);
  return <SpendLine text={got.month === month ? spendText(got.data) : ''} />;
}
