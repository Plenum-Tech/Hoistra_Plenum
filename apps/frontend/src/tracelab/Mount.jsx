// Mount — the one Trace Lab component the app renders (attachment A1 in App.jsx, only while vals.isTraceLab).
// With the flag off it renders nothing and the page's chunk is never requested. With it on, the page loads
// lazily as its own chunk, and a failure inside it (a render error, a chunk that will not load) stays inside
// this boundary: the rest of the app keeps working.
import React, { Suspense } from 'react';
import { ON } from './flag.js';

const TraceLabApp = ON ? React.lazy(() => import('./TraceLabApp.jsx')) : null;

const QUIET = { padding: '24px', fontSize: '12.5px', color: 'var(--color-neutral-500)' };

class Contained extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { /* contained: the page says so below, the app carries on */ }
  render() {
    if (this.state.failed) return <div role="alert" style={QUIET}>{'Trace Lab could not be shown. The rest of Hoistra is not affected; reload the page to try again.'}</div>;
    return this.props.children;
  }
}

export default function TraceLabMount({ vals }) {
  if (!ON || !TraceLabApp) return null;
  return (
    <Contained>
      <Suspense fallback={<div style={QUIET}>{'Reading…'}</div>}>
        <TraceLabApp vals={vals} />
      </Suspense>
    </Contained>
  );
}
