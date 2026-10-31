// RawPanel — the one place Trace Lab shows raw trace payloads (tool and agent names, SQL, the stored input and
// output), always collapsed until the reviewer opens it. The page is admin-only and the service checks who asks.
// Whether it is open lives in the store (actions.setOpen), so a refresh never closes it.
import React from 'react';

const pretty = (v) => {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'string') return v;
  try { return JSON.stringify(v, null, 2); } catch (e) { return String(v); }
};

export default function RawPanel({ title, data, open, onToggle }) {
  return (
    <details className="tl-raw" open={!!open} onToggle={(e) => { if (onToggle && e.currentTarget.open !== !!open) onToggle(e.currentTarget.open); }}>
      <summary>{title || 'Technical detail'}</summary>
      {open ? <pre className="tl-pre">{pretty(data)}</pre> : null}
    </details>
  );
}
