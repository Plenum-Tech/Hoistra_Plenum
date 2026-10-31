// watchChart — the Watch tab's pure helpers: the corrected-failure-rate chart as geometry, the "checked automatically vs
// by people" shares, the Due marker, the failure modes of the latest batches and the small text helpers. No requests and no
// store access; watchCommon.js holds the state, watchActions.js acts, watch.js shapes what is drawn.
// Every number comes from the server and nothing here estimates anything: a missing number reads "—" and is never drawn
// as zero. The only arithmetic is geometry (where a value sits on the plot) and the two shares of one whole.

export const CHART = Object.freeze({ width: 560, height: 200, padL: 44, padR: 16, padT: 14, padB: 30 });

export const STATUS_TEXT = Object.freeze({ queued: 'Waiting', running: 'Running', done: 'Done', capped: 'Stopped at its cost cap',
  failed: 'Failed', interrupted: 'Interrupted, resume it' });
export const CASE_STATUS = Object.freeze({ proposed: 'Proposed: confirm the failure', draft: 'Ready for a baseline', baselining: 'Baseline running',
  active: 'Active', retired: 'Retired', superseded: 'Replaced by a newer version' });
export const DECISION_TEXT = Object.freeze({ pass: '✓ Pass', block: '✗ Blocked', incomplete: '… Incomplete' });
// The statuses a case can still be worked on in.
export const LIVE = Object.freeze(['proposed', 'draft', 'baselining', 'active']);

const round1 = (v) => Math.round(v * 10) / 10;
export const has = (v) => v !== null && v !== undefined && v !== '' && !Number.isNaN(Number(v));
export const pctText = (v) => (has(v) ? (Math.round(Number(v) * 1000) / 10) + '%' : '—');
export const usdText = (v) => (has(v) ? '$' + Number(v).toFixed(2) : '—');

// The plot for one failure mode: `chart` is the server's {mode, points, threshold, y_max}. y_max is the top of the scale
// (the server picks it so the interval fits); a value above it is held at the top. Empty (nothing drawn) when there are no
// points or no usable scale.
export function chartGeometry(chart, dims) {
  const d = Object.assign({}, CHART, dims || {});
  const pts = (chart && chart.points) || [];
  const yMax = chart && Number(chart.y_max) > 0 ? Number(chart.y_max) : null;
  if (!pts.length || !yMax) return { empty: true, points: [], thresholdY: null, thresholdLabel: '', ticks: [], width: d.width, height: d.height, padL: d.padL, padR: d.padR };
  const innerW = d.width - d.padL - d.padR;
  const innerH = d.height - d.padT - d.padB;
  const y = (v) => (has(v) ? round1(d.padT + innerH - (Math.max(0, Math.min(Number(v), yMax)) / yMax) * innerH) : null);
  const x = (i) => round1(d.padL + (pts.length > 1 ? (i * innerW) / (pts.length - 1) : innerW / 2));
  const points = pts.map((p, i) => ({
    x: x(i), corrected: y(p.corrected), raw: y(p.raw), low: y(p.ci_low), high: y(p.ci_high),
    label: p.period_label || '', crossed: !!p.crossed, warning: p.validity_warning || '',
    title: (p.period_label || '') + ': ' + pctText(p.corrected) + ' corrected (' + pctText(p.ci_low) + ' to ' + pctText(p.ci_high) + '), '
      + pctText(p.raw) + ' flagged in the sample' + (p.crossed ? ', above the threshold' : '')
  }));
  const thr = has(chart.threshold) ? Number(chart.threshold) : null;
  return {
    empty: false, points, width: d.width, height: d.height, padL: d.padL, padR: d.padR,
    thresholdY: thr === null ? null : y(thr), thresholdLabel: thr === null ? '' : 'threshold ' + String(thr),
    ticks: [0, 0.5, 1].map((f) => ({ y: y(f * yMax), label: pctText(f * yMax) }))
  };
}

// "50% of 2 (10% to 90%)": the server's rate over the decided answers with its interval; a dash with none decided.
function accText(a, nKey) {
  if (!a || !Number(a[nKey])) return '—';
  return pctText(a.rate) + ' of ' + a[nKey] + ' (' + pctText(a.low) + ' to ' + pctText(a.high) + ')';
}

// The automatic-vs-people trend, one entry per week. The two shares are one whole: the server sends the automatic share
// and the people share is what is left. A week with nothing checked, or no usable share, is empty and reads as a dash.
export function stackShares(weeks) {
  return (weeks || []).map((w) => {
    const n = Number(w.checked_any) || 0;
    const raw = n && has(w.auto_share) ? Number(w.auto_share) : null;
    const auto = raw !== null && raw >= 0 && raw <= 1 ? raw : null;
    const autoPct = auto === null ? 0 : Math.round(auto * 1000) / 10;
    const peoplePct = auto === null ? 0 : Math.round((100 - autoPct) * 10) / 10;
    return {
      week: w.week, n, empty: auto === null, autoShare: auto, peopleShare: auto === null ? null : Math.round((1 - auto) * 1e9) / 1e9,
      autoPct, peoplePct, autoText: auto === null ? '—' : autoPct + '%', peopleText: auto === null ? '—' : peoplePct + '%',
      sources: w.auto_by_source || {}, people: w.people_by_source || {},
      modesAutomated: Number(w.modes_automated) || 0, modesConfirmed: Number(w.modes_confirmed) || 0,
      golden: accText(w.golden_accuracy, 'decided'), live: accText(w.live_accuracy, 'reviewed')
    };
  });
}

// The Due marker: the server names the week and whether a batch for it is still to run.
export function dueLabel(due) {
  if (!due || !due.period_label) return null;
  return due.is_due ? { text: 'Due: ' + due.period_label + ' has not been checked yet', isDue: true }
    : { text: 'Up to date: ' + due.period_label + ' checked', isDue: false };
}

// The failure modes the batches estimated, once each, newest batch first, each with the batch that has it (the chart of a
// mode is asked for once per mode).
export function latestModes(batches) {
  const out = [];
  const seen = new Set();
  (batches || []).forEach((b) => ((b.results && b.results.modes) || []).forEach((m) => {
    if (!m.mode_id || seen.has(m.mode_id)) return;
    seen.add(m.mode_id);
    out.push({ modeId: m.mode_id, name: m.mode_name || 'failure mode', crossed: !!m.crossed, batchId: b.id });
  }));
  return out;
}
