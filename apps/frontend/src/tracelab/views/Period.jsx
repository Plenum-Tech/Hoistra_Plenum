// Period — the 7 / 30 / 60 day choice the Map and Progress tabs share (logic/sampling.js: one range for both, so the
// figures on Progress are for the same window as the map and the batch plan).
import React from 'react';
import * as sact from '../samplingActions.js';

export default function Period({ ranges }) {
  return (
    <div className="tl-period" role="group" aria-label="Period">
      <span className="tl-meta">{'Period'}</span>
      {ranges.map((r) => (
        <button key={r.key} type="button" className={'tl-chip-btn' + (r.on ? ' tl-on' : '')} aria-pressed={r.on ? 'true' : 'false'} onClick={() => sact.setRange(r.key)}>{r.label}</button>
      ))}
    </div>
  );
}
