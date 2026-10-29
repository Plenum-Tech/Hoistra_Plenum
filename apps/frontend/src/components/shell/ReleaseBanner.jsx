// ReleaseBanner — "a newer version is available", with the reload that takes it.
//
// Sits above everything (the toast is z-index 500, overlays below that) and stays until the person
// acts: a notice that disappears on its own is one a person reading something else never sees. It
// appears at most once per tab, because watchForRelease stops after the first difference.
import React from 'react';
import { watchForRelease, hardReload } from '../../logic/release.js';

export default function ReleaseBanner() {
  const [ready, setReady] = React.useState(false);
  const [hidden, setHidden] = React.useState(false);

  React.useEffect(() => watchForRelease(() => setReady(true)), []);

  if (!ready || hidden) return null;

  return (
    <div
      role="status"
      style={{
        position: 'fixed', top: '14px', left: '50%', transform: 'translateX(-50%)', zIndex: 900,
        display: 'flex', alignItems: 'center', gap: '12px', padding: '10px 14px',
        borderRadius: '9px', background: 'var(--color-surface)', boxShadow: 'var(--shadow-lg)',
        border: '1px solid var(--color-border, rgba(0,0,0,.08))', maxWidth: 'calc(100vw - 32px)',
      }}
    >
      <i className="ph ph-arrow-clockwise" style={{ fontSize: '15px', color: 'var(--brand, #ff6b35)' }} />
      <span style={{ fontSize: '12.5px' }}>
        A new version of Hoistra is available. Reload to use it.
      </span>
      <button
        type="button"
        onClick={() => { hardReload(); }}
        style={{
          fontSize: '12.5px', fontWeight: 600, padding: '6px 12px', borderRadius: '7px',
          border: 'none', cursor: 'pointer', color: '#fff', background: 'var(--brand, #ff6b35)',
        }}
      >
        Reload
      </button>
      <button
        type="button"
        onClick={() => setHidden(true)}
        aria-label="Dismiss"
        title="Dismiss"
        style={{
          fontSize: '13px', lineHeight: 1, padding: '6px', borderRadius: '7px', border: 'none',
          cursor: 'pointer', background: 'transparent', color: 'var(--color-text-muted, #777)',
        }}
      >
        <i className="ph ph-x" />
      </button>
    </div>
  );
}
