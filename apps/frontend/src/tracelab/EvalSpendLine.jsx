// EvalSpendLine — Trace Lab's one line on the Platform cost page (attachment A3 in
// components/shell/PlatformCostPage.jsx): "Evaluation $X, not included in the totals above". With the flag off it
// renders nothing and its body is never requested. With it on, the body loads lazily as its own chunk, shows
// nothing until the month's spend is read, and a failure inside it stays inside this boundary, so the Platform
// cost page is never affected.
import React, { Suspense } from 'react';
import { ON } from './flag.js';

const Body = ON ? React.lazy(() => import('./views/EvalSpendBody.jsx')) : null;

class Quiet extends React.Component {
  constructor(props) {
    super(props);
    this.state = { failed: false };
  }
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch() { /* contained: the line is left out, the page carries on */ }
  render() { return this.state.failed ? null : this.props.children; }
}

export default function EvalSpendLine({ monthLabel }) {
  if (!ON || !Body) return null;
  return (
    <Quiet>
      <Suspense fallback={null}>
        <Body monthLabel={monthLabel} />
      </Suspense>
    </Quiet>
  );
}
