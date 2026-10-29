// ReleaseBanner — "a newer version is available", with the reload that takes it.
//
// Sits above everything (the toast is z-index 500, overlays below that) and stays until the person
// acts: a notice that disappears on its own is one a person reading something else never sees. It
// appears at most once per tab, and only when a reload would lose typed text: otherwise the
// page reloads itself the moment a response says the server is serving a different build.
import React from 'react';
import { watchForRelease, watchEveryRequest, actOnRelease, hardReload } from '../../logic/release.js';

export default function ReleaseBanner() {
  const [ready, setReady] = React.useState(false);
  const [hidden, setHidden] = React.useState(false);

  // Two ways of noticing, because neither covers the other. The header rides on every request,
  // so an active page reloads itself within one click of a deploy. The poll catches a tab that
  // is open and idle, making no requests at all, which is exactly the tab left open overnight.
  React.useEffect(() => {
    const show = () => setReady(true);
    const stopPolling = watchForRelease((served) => actOnRelease(served, show));
    const stopWatchingRequests = watchEveryRequest(show);
    return () => { stopPolling(); stopWatchingRequests(); };
  }, []);

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
