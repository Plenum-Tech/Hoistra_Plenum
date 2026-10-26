// sampling — pure helpers and view models for Trace Lab's Map and Progress tabs: the batch plan, the form for
// previewing and drawing review batches, the manifest (every answer's reason and seed), the map of answers, and
// the open-coding progress figures. Only fair (uniform) batches count toward rates; the others are for discovery
// only, and the copy says so everywhere. No requests and no state of its own: the screens draw what these
// return and call samplingActions.js.
//
// The state is the `sampling` slice of the store. The store merges patches and resetForCompany() only resets
// the keys store.js lists, so the slice carries the store's `seq` it was made under (samplingInitial(seq));
// samplingOf(s) hands back a slice from an earlier seq, or none, as an untouched one. A company switch therefore
// empties it without store.js having to know it exists.
import { labelFor } from './timeline.js';

export const SAMPLING_RANGES = [{ key: '7d', label: '7 days', days: 7 }, { key: '30d', label: '30 days', days: 30 }, { key: '60d', label: '60 days', days: 60 }];
export const PLAN_STEP_LABELS = { initial_uniform: 'First fair batch', cluster_representatives: 'One from each group', dimension: 'Split by a dimension',
  depth_search: 'More like the wrong answers', final_uniform: 'Final fair batch', extra: 'Extra batch' };
export const STRATEGY_LABELS = { uniform: 'Fair (uniform)', diversity: 'Spread across groups', dimension: 'Split by a dimension',
  outlier: 'Unusual answers', enrich: 'Similar to wrong answers' };
// What a plan step draws by default (the service's plan; `extra` is another fair batch).
export const STEP_DEFAULTS = { initial_uniform: { strategy: 'uniform', k: 15 }, cluster_representatives: { strategy: 'diversity', k: 15, random_share: 0 },
  dimension: { strategy: 'dimension', k: 30 }, depth_search: { strategy: 'enrich', k: 25 }, final_uniform: { strategy: 'uniform', k: 15 },
  extra: { strategy: 'uniform', k: 15 } };
// A batch may be split by these only. Outcomes (ratings, errors, verdicts) are never offered.
export const DIMENSION_CHOICES = [{ key: 'channel', label: 'Channel' }, { key: 'route_target', label: 'Where it was routed' },
  { key: 'asker_role', label: "Asker's role" }, { key: 'page', label: 'Page' }];
const VALUE_LABELS = { '(none)': 'Not recorded', '(other)': 'Other', unrouted: 'Not routed', multi: 'Several areas' };
const MAP_W = 640; const MAP_H = 400; const MAP_PAD = 24;
const CURVE_W = 560; const CURVE_H = 160; const CURVE_PAD = 28;

const nil = (v) => v === null || v === undefined;
const pct = (v) => (nil(v) ? '—' : Math.round(Number(v) * 100) + '%');
const num = (v) => (nil(v) ? '—' : String(v));
const answers = (n) => n + ' ' + (n === 1 ? 'answer' : 'answers');

export function valueLabel(dimension, value) {
  if (VALUE_LABELS[value]) return VALUE_LABELS[value];
  if (dimension === 'route_target') return labelFor('agent', value);
  if (dimension === 'asker_role') return { user: 'User', admin: 'Admin', superadmin: 'Platform admin' }[value] || value;
  return String(value);
}

// The period the Map, plan and batches read: the chosen range back from now, to an hour ahead (so the newest
// answers are inside it). An unknown range reads as 30 days.
export function samplingWindow(key, now) {
  const r = SAMPLING_RANGES.find((x) => x.key === key) || SAMPLING_RANGES[1];
  const t = now === undefined ? Date.now() : now;
  return { since: new Date(t - r.days * 86400000).toISOString(), until: new Date(t + 3600000).toISOString() };
}

export function defaultBatchForm() {
  return { plan_step: 'initial_uniform', strategy: 'uniform', k: 15, seed: 7, dimension: '', reviews_required: 1, random_share: '', include_view_as: false };
}

// The form after a change. Choosing a plan step fills in that step's strategy and size.
export function nextForm(form, patch) {
  const next = Object.assign({}, form || defaultBatchForm(), patch);
  if (patch && patch.plan_step && STEP_DEFAULTS[patch.plan_step]) Object.assign(next, { random_share: '' }, STEP_DEFAULTS[patch.plan_step]);
  return next;
}

// The request a form sends: defaults resolved, empty optionals left out.
export function batchRequest(form, win) {
  const f = Object.assign(defaultBatchForm(), form || {});
  const out = { plan_step: f.plan_step, strategy: f.strategy, k: Number(f.k) || 15, seed: Number(f.seed) || 0, since: win.since, until: win.until,
    reviews_required: Number(f.reviews_required) || 1 };
  if (f.strategy === 'dimension') out.dimension = f.dimension || 'route_target';
  if (f.random_share !== '' && !nil(f.random_share)) out.random_share = Number(f.random_share);
  // Answers the platform team gave while viewing as this company are left out of every pool unless asked for.
  if (f.include_view_as) out.include_view_as = true;
  return out;
}

// What the reader is told after a batch is drawn.
export function batchNotice(batch) {
  if (!batch) return '';
  const n = Number(batch.selected) || 0;
  if (!n) return 'No answers were drawn.';
  return 'Drew ' + answers(n) + ' (seed ' + batch.seed + '). ' + (n === 1 ? 'It is' : 'They are') + ' in the review queue.';
}

// Points scaled into the SVG box, the same projection every time.
export function mapGeometry(nodes, opts) {
  const o = Object.assign({ w: MAP_W, h: MAP_H, pad: MAP_PAD }, opts || {});
  const list = nodes || [];
  if (!list.length) return [];
  const xs = list.map((n) => n.x);
  const ys = list.map((n) => n.y);
  const x0 = Math.min(...xs); const x1 = Math.max(...xs); const y0 = Math.min(...ys); const y1 = Math.max(...ys);
  const sx = (o.w - 2 * o.pad) / ((x1 - x0) || 1);
  const sy = (o.h - 2 * o.pad) / ((y1 - y0) || 1);
  return list.map((n) => Object.assign({}, n, { px: Math.round((o.pad + (n.x - x0) * sx) * 10) / 10, py: Math.round((o.h - o.pad - (n.y - y0) * sy) * 10) / 10 }));
}

// The discovery curve's line: one point per block of reviews, y = wrong verdicts in that block.
export function curveGeometry(points, opts) {
  const o = Object.assign({ w: CURVE_W, h: CURVE_H, pad: CURVE_PAD }, opts || {});
  const list = points || [];
  if (!list.length) return { path: '', dots: [], max: 0 };
  const max = Math.max(1, ...list.map((p) => p.wrong));
  const step = list.length > 1 ? (o.w - 2 * o.pad) / (list.length - 1) : 0;
  const dots = list.map((p, i) => ({ x: Math.round((o.pad + i * step) * 10) / 10, y: Math.round((o.h - o.pad - (p.wrong / max) * (o.h - 2 * o.pad)) * 10) / 10,
    label: 'Reviews up to ' + p.reviews_through + ': ' + p.wrong + ' wrong', p }));
  return { path: dots.map((d, i) => (i ? 'L' : 'M') + d.x + ' ' + d.y).join(' '), dots, max };
}

export function saturationText(sat) {
  if (!sat || !sat.available) return 'Saturation: — (shown once failure modes are named)';
  if (!sat.hint) return 'New failure modes are still appearing.';
  if (sat.kind === 'final_batch') return 'The final fair batch produced ' + sat.final_new_modes + ' new failure modes. Review another batch before treating the list as stable.';
  return 'No new failure mode in the last ' + sat.reviews_without_new + ' reviews; consider moving to counting.';
}

// The `sampling` slice of the store, at its default, made under the store's `seq`. A fresh object every call.
export function samplingInitial(seq) {
  return {
    seq: seq || 0,
    range: '30d', plan: null, planError: '', planLoadedAt: null,
    batches: null, batchesError: '', batchesLoadedAt: null,
    form: defaultBatchForm(), preview: null, busy: false, error: '', notice: '',
    manifest: null, manifestOpen: false, manifestError: '',
    map: null, mapError: '', mapLoading: false, mapLoadedAt: null, mapFocus: null,
    progress: null, progressError: '', progressLoading: false, progressLoadedAt: null, progressBlock: 10
  };
}

// The slice in force for this state: the one made under the store's current seq, else an untouched one.
export function samplingOf(s) {
  const seq = (s && s.seq) || 0;
  return s && s.sampling && s.sampling.seq === seq ? s.sampling : samplingInitial(seq);
}

// ── view models ──────────────────────────────────────────────────────────────────────────────────────────────

function planView(plan) {
  if (!plan) return null;
  const sampled = plan.distinct_sampled || 0;
  const target = plan.target_total || 100;
  return {
    steps: (plan.steps || []).map((s) => ({ key: s.step, label: PLAN_STEP_LABELS[s.step] || s.step, strategy: STRATEGY_LABELS[s.strategy] || s.strategy,
      target: s.target, selected: s.selected, reviewed: s.reviewed, done: s.selected >= s.target, next: plan.next_step === s.step, fair: s.strategy === 'uniform' })),
    nextLabel: PLAN_STEP_LABELS[plan.next_step] || 'Extra batch',
    sampled: sampled + ' of ' + target + ' answers sampled',
    shortfall: plan.pool_size !== undefined && plan.pool_size < target - sampled
      ? 'Only ' + plan.pool_size + ' unsampled answers in this period; the plan cannot reach ' + target + '.' : ''
  };
}

function batchView(b) {
  return {
    id: b.id, label: PLAN_STEP_LABELS[b.plan_step] || b.plan_step, strategy: STRATEGY_LABELS[b.strategy] || b.strategy,
    tag: b.counts_for_prevalence ? 'counts toward rates' : 'for discovery only', fair: !!b.counts_for_prevalence,
    selected: b.selected, requested: b.requested, reviewed: b.reviewed || 0, mine: b.mine || 0, missing: b.missing || 0,
    wrong: b.wrong || 0, right: b.right || 0, disputed: b.disputed || 0, seed: b.seed, who: b.created_by_email || '—',
    withdrawn: !!b.withdrawn_at, canWithdraw: !b.withdrawn_at, canReview: !b.withdrawn_at && (b.missing || 0) > 0
  };
}

// The Map tab's batch plan, form, preview, batch list and manifest.
export function samplingView(s) {
  const r = samplingOf(s);
  const form = Object.assign(defaultBatchForm(), r.form || {});
  const pv = r.preview;
  return {
    ranges: SAMPLING_RANGES.map((x) => ({ key: x.key, label: x.label, on: (r.range || '30d') === x.key })),
    plan: planView(r.plan), planError: r.planError || '',
    form: Object.assign({}, form, {
      steps: Object.keys(PLAN_STEP_LABELS).map((k) => ({ key: k, label: PLAN_STEP_LABELS[k] })),
      strategies: Object.keys(STRATEGY_LABELS).map((k) => ({ key: k, label: STRATEGY_LABELS[k] })),
      dimensions: DIMENSION_CHOICES, showDimension: form.strategy === 'dimension',
      fairNote: form.strategy === 'uniform' ? 'Counts toward rates.' : 'For discovery only: this batch never counts toward rates.',
      teamNote: form.include_view_as ? 'Includes answers your team gave while viewing as this company: for testing only, as they are not real use and would skew the rates.' : ''
    }),
    busy: !!r.busy, error: r.error || '', notice: r.notice || '',
    preview: pv ? {
      poolSize: pv.pool_size, excluded: pv.excluded_sampled, truncated: !!pv.truncated,
      picks: (pv.picks || []).map((p) => ({ turnId: p.turn_id, reason: p.reason })), empty: !(pv.picks || []).length
    } : null,
    batches: r.batches ? r.batches.map(batchView) : null, batchesError: r.batchesError || '',
    manifest: r.manifestOpen && r.manifest ? (r.manifest.batches || []).map((b) => ({ id: b.id, label: PLAN_STEP_LABELS[b.plan_step] || b.plan_step,
      seed: b.seed, tag: b.counts_for_prevalence ? 'counts toward rates' : 'for discovery only',
      items: (b.items || []).map((i) => ({ position: i.position, turnId: i.turn_id, reason: i.reason })) })) : null,
    manifestError: r.manifestError || ''
  };
}

// The map of answers: the empty state, the caution line, the nodes projected into the box, the groups.
export function mapView(s) {
  const r = samplingOf(s);
  const map = r.map;
  const focus = r.mapFocus;
  const geo = map && !map.too_few ? mapGeometry(map.nodes) : [];
  return {
    loading: !!r.mapLoading && !r.mapLoadedAt, error: r.mapError || '',
    tooFew: !!(map && map.too_few), tooFewCopy: 'Not enough answers in this window to draw a map.',
    caution: 'A cluster is a prompt to read answers, never evidence that they failed.',
    explained: map && map.explained_variance ? 'The two axes carry ' + pct(map.explained_variance[0]) + ' and ' + pct(map.explained_variance[1]) + ' of the spread between answers.' : '',
    truncated: !!(map && map.truncated), width: MAP_W, height: MAP_H,
    nodes: geo.map((n) => ({ id: n.turn_id, turnId: n.turn_id, conversationKey: n.conversation_key, x: n.px, y: n.py, cluster: n.cluster,
      reviewed: !!n.reviewed, mine: !!n.reviewed_by_me, sampled: !!n.batch_id, dim: !nil(focus) && focus !== n.cluster, on: focus === n.cluster,
      title: (n.question || 'Answer') + ' · group ' + n.cluster + (n.reviewed ? ' · reviewed' : ' · not reviewed') + (n.batch_id ? ' · in a review batch' : '') })),
    clusters: map && map.clusters ? map.clusters.map((g) => ({ id: g.id, label: 'Group ' + g.id + ' · ' + answers(g.size) + ' · ' + g.reviewed + ' reviewed',
      on: focus === g.id, centroid: (map.feature_names || []).map((f) => ({ label: f.label, value: g.centroid ? g.centroid[f.key] : null })) })) : []
  };
}

// The Progress tab: tiles, open batches, reviewers, what is missing, the fair-sample rate, agreement, the
// discovery curve, saturation and coverage. "—" wherever nothing has been read.
export function progressView(s) {
  const r = samplingOf(s);
  const prg = r.progress;
  const oc = prg && prg.open_coding;
  // From Phase 5 on, GET /progress carries the failure-mode curve as a list of blocks (views/ProgressTaxonomy.jsx draws
  // it); the older object with `points` (wrong answers per block) is read only when that is what arrived.
  const disc = prg && prg.discovery && !Array.isArray(prg.discovery) ? prg.discovery : null;
  const curve = curveGeometry(disc ? disc.points : []);
  const fair = prg && prg.fair_sample;
  const ag = prg && prg.agreement;
  const cov = prg && prg.coverage;
  const block = r.progressBlock || 10;
  return {
    loading: !!r.progressLoading && !r.progressLoadedAt, error: r.progressError || '',
    discoveryMoved: !!(prg && Array.isArray(prg.discovery)),
    tiles: [
      { label: 'Answers reviewed', value: oc ? num(oc.reviewed) : '—' },
      { label: 'Wrong', value: oc ? num(oc.wrong) : '—' },
      { label: 'Missing judgments', value: oc ? num(oc.missing) : '—' },
      { label: 'Open batches', value: prg ? num(prg.open_batches) : '—' }
    ],
    batches: prg && prg.batches ? prg.batches.filter((b) => !b.withdrawn_at).map(batchView) : [],
    reviewers: oc ? (oc.reviewers || []).map((x) => ({ who: x.email || '—', reviewed: x.reviewed })) : [],
    missing: oc ? { turns: oc.missing_turns || [], more: oc.missing_more ? '…and ' + oc.missing_more + ' more' : '' } : null,
    fair: fair ? {
      label: fair.label,
      text: nil(fair.rate) ? '—' : pct(fair.rate) + ' wrong (95% interval ' + pct(fair.ci_low) + '–' + pct(fair.ci_high) + ')',
      detail: fair.wrong + ' wrong · ' + fair.right + ' right · ' + fair.disputed + ' disputed'
    } : null,
    agreement: ag ? pct(ag.percent) + ' agreement on ' + ag.shared + ' shared answers'
      + (!nil(ag.kappa) ? ' · kappa ' + Number(ag.kappa).toFixed(2) : (ag.note ? ' · ' + ag.note : '')) : '—',
    curve: { path: curve.path, dots: curve.dots, max: curve.max, width: CURVE_W, height: CURVE_H, empty: !curve.dots.length,
      caption: 'Wrong answers found in each block of ' + ((disc && disc.block) || block) + ' reviews. New failure modes join this chart once they are named.' },
    saturation: saturationText(prg && prg.saturation),
    coverage: cov ? DIMENSION_CHOICES.map((d) => ({ label: d.label, rows: ((cov.dimensions || {})[d.key] || []).map((x) => ({
      value: valueLabel(d.key, x.value), pool: x.pool, reviewed: x.reviewed, poolShare: pct(x.pool_share), reviewedShare: pct(x.reviewed_share), thin: !!x.under_covered })) })) : [],
    unreviewedGroups: cov && cov.clusters_unreviewed ? cov.clusters_unreviewed.map((g) => 'Group ' + g.id + ' (' + answers(g.size) + ')') : [],
    blocks: [5, 10, 20].map((n) => ({ n, on: block === n }))
  };
}
