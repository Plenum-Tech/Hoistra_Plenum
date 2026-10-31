// sampling (logic) — the Map and Progress tabs: the batch plan in plain words, batch forms that never offer an
// outcome as a dimension, the request a form sends, the map's empty state and stable projection, and progress
// copy that shows "—" for missing data and never calls a sample fraction a rate. The view models are plain data
// read from the store's state (no callbacks: the screens call samplingActions.js).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialState } from '../store.js';
import {
  DIMENSION_CHOICES, STEP_DEFAULTS, SAMPLING_RANGES, batchNotice, batchRequest, defaultBatchForm, nextForm, samplingWindow, mapGeometry, curveGeometry,
  saturationText, valueLabel, samplingInitial, samplingOf, samplingView, mapView, progressView
} from '../logic/sampling.js';

const state = (sampling, over) => Object.assign(initialState(), over || {}, { sampling: Object.assign(samplingInitial(), sampling || {}) });

const PLAN = { ok: true, next_step: 'cluster_representatives', distinct_sampled: 24, target_total: 100, pool_size: 40, steps: [
  { step: 'initial_uniform', strategy: 'uniform', target: 15, selected: 15, reviewed: 15, batch_ids: ['b1'] },
  { step: 'cluster_representatives', strategy: 'diversity', target: 15, selected: 9, reviewed: 2, batch_ids: ['b2'] },
  { step: 'dimension', strategy: 'dimension', target: 30, selected: 0, reviewed: 0, batch_ids: [] },
  { step: 'depth_search', strategy: 'enrich', target: 25, selected: 0, reviewed: 0, batch_ids: [] },
  { step: 'final_uniform', strategy: 'uniform', target: 15, selected: 0, reviewed: 0, batch_ids: [] }] };

test('the plan view names each step, marks the next one and states a shortfall', () => {
  const v = samplingView(state({ plan: PLAN })).plan;
  assert.deepEqual(v.steps.map((s) => [s.label, s.done, s.next, s.fair]), [
    ['First fair batch', true, false, true], ['One from each group', false, true, false], ['Split by a dimension', false, false, false],
    ['More like the wrong answers', false, false, false], ['Final fair batch', false, false, true]]);
  assert.equal(v.sampled, '24 of 100 answers sampled');
  assert.equal(v.shortfall, 'Only 40 unsampled answers in this period; the plan cannot reach 100.');
  assert.equal(v.nextLabel, 'One from each group');
  assert.equal(v.steps[1].strategy, 'Spread across groups');
  assert.equal(samplingView(state()).plan, null, 'no plan until it is read');
  const roomy = samplingView(state({ plan: Object.assign({}, PLAN, { pool_size: 500 }) })).plan;
  assert.equal(roomy.shortfall, '');
  const extra = samplingView(state({ plan: Object.assign({}, PLAN, { next_step: null }) })).plan;
  assert.equal(extra.nextLabel, 'Extra batch');
});

test('a batch can be split only by channel, routing, role or page - never by an outcome', () => {
  assert.deepEqual(DIMENSION_CHOICES.map((d) => d.key), ['channel', 'route_target', 'asker_role', 'page']);
  const keys = DIMENSION_CHOICES.map((d) => d.key);
  ['feedback_rating', 'ok', 'verdict', 'error', 'thumbs'].forEach((w) => assert.ok(keys.indexOf(w) < 0, w));
  assert.deepEqual(samplingView(state()).form.dimensions.map((d) => d.key), keys);
});

test('choosing a plan step fills its strategy and size; uniform is the only fair kind', () => {
  const next = nextForm(defaultBatchForm(), { plan_step: 'cluster_representatives' });
  assert.deepEqual([next.plan_step, next.strategy, next.k, next.random_share], ['cluster_representatives', 'diversity', 15, 0]);
  assert.equal(samplingView(state({ form: next })).form.fairNote, 'For discovery only: this batch never counts toward rates.');
  const final = nextForm(next, { plan_step: 'final_uniform' });
  assert.deepEqual([final.strategy, final.random_share], ['uniform', '']);
  assert.equal(samplingView(state({ form: final })).form.fairNote, 'Counts toward rates.');
  assert.deepEqual(STEP_DEFAULTS.dimension, { strategy: 'dimension', k: 30 });
  // A field the reader edits changes only that field; an unknown step changes only the step.
  assert.equal(nextForm(final, { k: 40 }).strategy, 'uniform');
  assert.deepEqual([nextForm(final, { plan_step: 'brand_new' }).plan_step, nextForm(final, { plan_step: 'brand_new' }).strategy], ['brand_new', 'uniform']);
  assert.equal(samplingView(state({ form: { strategy: 'dimension' } })).form.showDimension, true);
  assert.equal(samplingView(state()).form.showDimension, false);
});

test('the team-answers switch: off by default and left out of the request; on, it asks for view-as answers and says they are for testing', () => {
  const win = { since: 'a', until: 'b' };
  assert.equal(defaultBatchForm().include_view_as, false);
  assert.equal('include_view_as' in batchRequest(defaultBatchForm(), win), false);
  assert.equal(batchRequest({ include_view_as: true }, win).include_view_as, true);
  assert.equal(samplingView(state({ form: defaultBatchForm() })).form.teamNote, '');
  assert.match(samplingView(state({ form: Object.assign(defaultBatchForm(), { include_view_as: true }) })).form.teamNote, /testing only/);
});

test('a form becomes the request it sends: defaults resolved, empty optionals left out, the window and seed carried', () => {
  const win = { since: 'a', until: 'b' };
  const dim = batchRequest({ strategy: 'dimension', plan_step: 'dimension', k: 30 }, win);
  assert.equal(dim.dimension, 'route_target', 'a dimension batch with none chosen splits by where it was routed');
  assert.equal(batchRequest({ strategy: 'dimension', plan_step: 'dimension', k: 30, dimension: 'page' }, win).dimension, 'page');
  assert.equal('dimension' in batchRequest({ strategy: 'uniform', plan_step: 'initial_uniform', k: 15, dimension: 'page' }, win), false);
  const plain = batchRequest(defaultBatchForm(), win);
  assert.deepEqual(plain, { plan_step: 'initial_uniform', strategy: 'uniform', k: 15, seed: 7, since: 'a', until: 'b', reviews_required: 1 });
  assert.equal('random_share' in plain, false);
  assert.equal(batchRequest({ random_share: 0 }, win).random_share, 0, 'zero is a choice, not an empty field');
  assert.equal(batchRequest({ random_share: '0.25' }, win).random_share, 0.25);
  assert.deepEqual([batchRequest({ k: '', seed: '', reviews_required: '' }, win).k, batchRequest({ k: '', seed: '' }, win).seed, batchRequest({ reviews_required: 0 }, win).reviews_required], [15, 0, 1]);
  assert.equal(batchRequest({ k: '25', seed: '9', reviews_required: '2' }, win).k, 25);
  assert.equal(batchRequest(null, win).strategy, 'uniform');
});

test('the window is the range back from now, to an hour ahead; an unknown range reads as 30 days', () => {
  const now = Date.parse('2026-10-16T12:00:00Z');
  assert.deepEqual(samplingWindow('7d', now), { since: '2026-10-09T12:00:00.000Z', until: '2026-10-16T13:00:00.000Z' });
  assert.equal(samplingWindow('60d', now).since, '2026-08-17T12:00:00.000Z');
  assert.equal(samplingWindow('nonsense', now).since, samplingWindow('30d', now).since);
  assert.equal(samplingWindow(undefined, now).since, samplingWindow('30d', now).since);
  assert.deepEqual(SAMPLING_RANGES.map((r) => r.key), ['7d', '30d', '60d']);
  assert.deepEqual(samplingView(state({ range: '7d' })).ranges.map((r) => [r.key, r.on]), [['7d', true], ['30d', false], ['60d', false]]);
});

test('preview and created batches read as what was drawn, with the reasons', () => {
  const v = samplingView(state({ preview: { pool_size: 80, excluded_sampled: 15, truncated: true, picks: [{ turn_id: 'turn-1', reason: 'uniform pick' }], request: {} },
    busy: true, error: 'No answers are left to draw in that period.', notice: 'Drew 15 answers (seed 7). They are in the review queue.' }));
  assert.deepEqual([v.preview.poolSize, v.preview.excluded, v.preview.truncated, v.preview.empty, v.preview.picks[0].reason], [80, 15, true, false, 'uniform pick']);
  assert.deepEqual([v.busy, v.error, v.notice], [true, 'No answers are left to draw in that period.', 'Drew 15 answers (seed 7). They are in the review queue.']);
  assert.equal(samplingView(state({ preview: { pool_size: 0, excluded_sampled: 0, picks: [] } })).preview.empty, true);
  assert.equal(samplingView(state()).preview, null);
});

test('after a draw the reader is told how many answers, which seed, and where they are', () => {
  assert.equal(batchNotice({ selected: 15, seed: 7 }), 'Drew 15 answers (seed 7). They are in the review queue.');
  assert.equal(batchNotice({ selected: 1, seed: 3 }), 'Drew 1 answer (seed 3). It is in the review queue.');
  assert.equal(batchNotice({ selected: 0, seed: 3 }), 'No answers were drawn.');
  assert.equal(batchNotice(null), '');
});

test('batches are tagged by whether they count toward rates, and a withdrawn one cannot be withdrawn again', () => {
  const v = samplingView(state({ batches: [{ id: 'b1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15 },
    { id: 'b2', plan_step: 'dimension', strategy: 'dimension', counts_for_prevalence: false, selected: 30, requested: 30, withdrawn_at: '2026-10-14T10:00:00Z' }] }));
  assert.deepEqual(v.batches.map((b) => [b.tag, b.withdrawn, b.canWithdraw]), [['counts toward rates', false, true], ['for discovery only', true, false]]);
  assert.deepEqual([v.batches[0].label, v.batches[0].strategy, v.batches[1].label], ['First fair batch', 'Fair (uniform)', 'Split by a dimension']);
  assert.equal(samplingView(state()).batches, null, 'not read yet');
});

test('the manifest lists every batch with its seed and each answer\'s reason, once opened', () => {
  const manifest = { batches: [{ id: 'b1', plan_step: 'initial_uniform', seed: 7, counts_for_prevalence: true,
    items: [{ position: 1, turn_id: 'turn-1', reason: 'uniform pick' }, { position: 2, turn_id: 'turn-2', reason: 'uniform pick' }] }] };
  assert.equal(samplingView(state({ manifest, manifestOpen: false })).manifest, null);
  const open = samplingView(state({ manifest, manifestOpen: true, manifestError: 'x' }));
  assert.deepEqual(open.manifest[0], { id: 'b1', label: 'First fair batch', seed: 7, tag: 'counts toward rates',
    items: [{ position: 1, turnId: 'turn-1', reason: 'uniform pick' }, { position: 2, turnId: 'turn-2', reason: 'uniform pick' }] });
  assert.equal(open.manifestError, 'x');
});

const MAP = { ok: true, too_few: false, explained_variance: [0.612, 0.18], feature_names: [{ key: 'tokens', label: 'Tokens' }],
  nodes: [{ turn_id: 't1', conversation_key: 's:a', x: 0, y: 0, cluster: 1, reviewed: true, batch_id: 'b1', question: 'Boilers?' },
    { turn_id: 't2', conversation_key: 's:b', x: 1, y: 1, cluster: 2, reviewed: false, batch_id: null, question: 'Lifts?' }],
  clusters: [{ id: 1, size: 1, reviewed: 1, centroid: { tokens: 900 } }, { id: 2, size: 1, reviewed: 0, centroid: { tokens: 1200 } }] };

test('the map has an empty state, a caution line and a stable projection into the box', () => {
  const few = mapView(state({ map: { ok: true, too_few: true, nodes: [], clusters: [] }, mapLoadedAt: 1 }));
  assert.equal(few.tooFew, true);
  assert.equal(few.tooFewCopy, 'Not enough answers in this window to draw a map.');
  assert.equal(few.caution, 'A cluster is a prompt to read answers, never evidence that they failed.');
  assert.deepEqual(few.nodes, []);
  const geo = mapGeometry([{ x: -2, y: 0 }, { x: 2, y: 4 }, { x: 0, y: 2 }], { w: 100, h: 100, pad: 10 });
  assert.deepEqual(geo.map((n) => [n.px, n.py]), [[10, 90], [90, 10], [50, 50]]);
  assert.deepEqual(mapGeometry([]), []);
  const one = mapGeometry([{ x: 5, y: 5 }], { w: 100, h: 100, pad: 10 });
  assert.deepEqual([one[0].px, one[0].py], [10, 90], 'a single point has no spread to scale');
  const v = mapView(state({ map: MAP, mapLoadedAt: 1, mapFocus: 2 }));
  assert.equal(v.explained, 'The two axes carry 61% and 18% of the spread between answers.');
  assert.deepEqual(v.nodes.map((n) => [n.dim, n.on, n.sampled]), [[true, false, true], [false, true, false]]);
  assert.equal(v.nodes[0].title, 'Boilers? · group 1 · reviewed · in a review batch');
  assert.equal(v.nodes[1].title, 'Lifts? · group 2 · not reviewed');
  assert.deepEqual([v.nodes[1].id, v.nodes[1].conversationKey, v.nodes[1].turnId], ['t2', 's:b', 't2'], 'a node carries what opens it');
  assert.equal(v.clusters[1].label, 'Group 2 · 1 answer · 0 reviewed');
  assert.deepEqual(v.clusters[1].centroid, [{ label: 'Tokens', value: 1200 }]);
  assert.equal(mapView(state({ map: MAP, mapLoadedAt: 1 })).nodes.every((n) => !n.dim && !n.on), true, 'no group chosen: nothing dimmed');
  const big = Object.assign({}, MAP, { clusters: [{ id: 1, size: 12, reviewed: 3, centroid: {} }] });
  assert.equal(mapView(state({ map: big, mapLoadedAt: 1 })).clusters[0].label, 'Group 1 · 12 answers · 3 reviewed');
  assert.equal(mapView(state({ mapLoading: true })).loading, true);
  assert.equal(mapView(state({ mapLoading: true, mapLoadedAt: 5 })).loading, false, 'a reload keeps what is drawn');
  assert.equal(mapView(state({ map: { too_few: false, truncated: true, nodes: [], clusters: [] } })).truncated, true);
  assert.equal(mapView(state()).explained, '');
  assert.equal(mapView(state({ mapError: 'Unreachable' })).error, 'Unreachable');
});

const PROGRESS = { ok: true, open_batches: 2, batches: [{ id: 'b1', plan_step: 'initial_uniform', strategy: 'uniform', counts_for_prevalence: true, selected: 15, requested: 15 },
  { id: 'b0', plan_step: 'extra', strategy: 'uniform', withdrawn_at: '2026-10-01T00:00:00Z' }], block: 10,
  open_coding: { reviewed: 12, wrong: 3, right: 9, missing: 37, missing_turns: ['t1'], missing_more: 12, reviewers: [{ email: 'a@x', reviewed: 12 }, { reviewed: 4 }] },
  fair_sample: { label: 'From fair (uniform) batches only', rate: 0.25, ci_low: 0.0889, ci_high: 0.5323, wrong: 3, right: 9, disputed: 1 },
  agreement: { reviewers: ['a', 'b'], shared: 8, percent: 0.875, kappa: null, note: 'fewer than 20 shared answers' },
  discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3, partial: false }, { block: 2, reviews_through: 12, wrong: 0, partial: true }] },
  saturation: { available: false, hint: false }, coverage: null };

test('progress shows "—" until it has data, and labels the fair-sample rate', () => {
  const empty = progressView(state());
  assert.deepEqual(empty.tiles.map((t) => t.value), ['—', '—', '—', '—']);
  assert.equal(empty.agreement, '—');
  assert.equal(empty.fair, null);
  assert.deepEqual([empty.missing, empty.reviewers, empty.batches, empty.coverage], [null, [], [], []]);
  assert.equal(empty.saturation, 'Saturation: — (shown once failure modes are named)');
  assert.equal(empty.curve.empty, true);
  const v = progressView(state({ progress: PROGRESS, progressLoadedAt: 1 }));
  assert.deepEqual(v.tiles.map((t) => t.value), ['12', '3', '37', '2']);
  assert.deepEqual(v.tiles.map((t) => t.label), ['Answers reviewed', 'Wrong', 'Missing judgments', 'Open batches']);
  assert.equal(v.fair.label, 'From fair (uniform) batches only');
  assert.equal(v.fair.text, '25% wrong (95% interval 9%–53%)');
  assert.equal(v.fair.detail, '3 wrong · 9 right · 1 disputed');
  assert.equal(v.agreement, '88% agreement on 8 shared answers · fewer than 20 shared answers');
  assert.equal(v.missing.more, '…and 12 more');
  assert.deepEqual(v.reviewers, [{ who: 'a@x', reviewed: 12 }, { who: '—', reviewed: 4 }]);
  assert.equal(v.curve.dots.length, 2);
  assert.equal(v.curve.caption, 'Wrong answers found in each block of 10 reviews. New failure modes join this chart once they are named.');
  assert.equal(v.saturation, 'Saturation: — (shown once failure modes are named)');
  assert.deepEqual(v.batches.map((b) => b.id), ['b1'], 'a withdrawn batch is not an open one');
  assert.deepEqual(v.blocks.map((b) => [b.n, b.on]), [[5, false], [10, true], [20, false]]);
});

test('progress copes with a sparse reply: no rate yet, a kappa when there is one, no reviewers list', () => {
  const sparse = { open_batches: 0, open_coding: { reviewed: 0, wrong: 0, right: 0, missing: 0, missing_turns: [], missing_more: 0 },
    fair_sample: { label: 'From fair (uniform) batches only', rate: null, ci_low: null, ci_high: null, wrong: 0, right: 0, disputed: 0 },
    agreement: { shared: 30, percent: 0.9, kappa: 0.8123 }, discovery: null };
  const v = progressView(state({ progress: sparse }));
  assert.deepEqual(v.tiles.map((t) => t.value), ['0', '0', '0', '0']);
  assert.equal(v.fair.text, '—');
  assert.equal(v.agreement, '90% agreement on 30 shared answers · kappa 0.81');
  assert.deepEqual([v.reviewers, v.missing.more, v.missing.turns], [[], '', []]);
  assert.equal(v.curve.caption, 'Wrong answers found in each block of 10 reviews. New failure modes join this chart once they are named.');
  const noKappa = progressView(state({ progress: { agreement: { shared: 3, percent: null, note: 'fewer than 20 shared answers' } } }));
  assert.equal(noKappa.agreement, '— agreement on 3 shared answers · fewer than 20 shared answers');
});

test('progress coverage reads in plain words, marks thin groups and names map groups with no reviewed answer', () => {
  const coverage = { dimensions: { channel: [{ value: 'chat', pool: 80, reviewed: 20, pool_share: 0.8, reviewed_share: 0.4, under_covered: false }],
    route_target: [{ value: '(none)', pool: 10, reviewed: 0, pool_share: 0.1, reviewed_share: 0, under_covered: true }, { value: 'multi', pool: 5, reviewed: 1, pool_share: 0.05, reviewed_share: 0.2 }],
    asker_role: [{ value: 'superadmin', pool: 2, reviewed: 0, pool_share: 0.02, reviewed_share: 0, under_covered: true }] },
  clusters_unreviewed: [{ id: 3, size: 7 }] };
  const v = progressView(state({ progress: Object.assign({}, PROGRESS, { coverage }) }));
  assert.deepEqual(v.coverage.map((d) => d.label), ['Channel', 'Where it was routed', "Asker's role", 'Page']);
  assert.deepEqual(v.coverage[0].rows[0], { value: 'chat', pool: 80, reviewed: 20, poolShare: '80%', reviewedShare: '40%', thin: false });
  assert.deepEqual(v.coverage[1].rows.map((r) => [r.value, r.thin]), [['Not recorded', true], ['Several areas', false]]);
  assert.equal(v.coverage[2].rows[0].value, 'Platform admin');
  assert.deepEqual(v.coverage[3].rows, [], 'a dimension the reply lacks is empty, not an error');
  assert.deepEqual(v.unreviewedGroups, ['Group 3 (7 answers)']);
  assert.equal(progressView(state({ progress: Object.assign({}, PROGRESS, { coverage: { dimensions: {} } }) })).coverage.length, 4);
  assert.equal(progressView(state({ progressBlock: 20 })).blocks.find((b) => b.on).n, 20);
  assert.equal(progressView(state({ progressLoading: true })).loading, true);
  assert.equal(progressView(state({ progressError: 'Unreachable' })).error, 'Unreachable');
});

test('saturation copy follows the service\'s hint', () => {
  assert.equal(saturationText({ available: true, hint: true, kind: 'quiet', reviews_without_new: 20 }), 'No new failure mode in the last 20 reviews; consider moving to counting.');
  assert.equal(saturationText({ available: true, hint: true, kind: 'final_batch', final_new_modes: 3 }),
    'The final fair batch produced 3 new failure modes. Review another batch before treating the list as stable.');
  assert.equal(saturationText({ available: true, hint: false }), 'New failure modes are still appearing.');
  assert.equal(saturationText(null), 'Saturation: — (shown once failure modes are named)');
});

test('the curve scales wrong counts into the chart box', () => {
  const g = curveGeometry([{ wrong: 0, reviews_through: 10 }, { wrong: 4, reviews_through: 20 }], { w: 100, h: 100, pad: 10 });
  assert.equal(g.path, 'M10 90 L90 10');
  assert.equal(g.max, 4);
  assert.equal(g.dots[1].label, 'Reviews up to 20: 4 wrong');
  assert.deepEqual(curveGeometry([]), { path: '', dots: [], max: 0 });
  const flat = curveGeometry([{ wrong: 0, reviews_through: 10 }], { w: 100, h: 100, pad: 10 });
  assert.deepEqual([flat.path, flat.max], ['M10 90', 1], 'no wrong answers at all still draws on the baseline');
});

test('dimension values read in plain words', () => {
  assert.equal(valueLabel('route_target', '(none)'), 'Not recorded');
  assert.equal(valueLabel('route_target', 'multi'), 'Several areas');
  assert.equal(valueLabel('route_target', 'unrouted'), 'Not routed');
  assert.equal(valueLabel('route_target', '(other)'), 'Other');
  assert.equal(valueLabel('route_target', 'udr_agent'), 'Data lookup');
  assert.equal(valueLabel('asker_role', 'superadmin'), 'Platform admin');
  assert.equal(valueLabel('asker_role', 'admin'), 'Admin');
  assert.equal(valueLabel('asker_role', 'odd'), 'odd');
  assert.equal(valueLabel('page', 'cc'), 'cc');
});

test('a slice made before a company switch reads as untouched; a store with no slice reads as untouched', () => {
  assert.equal(initialState().sampling, undefined);
  assert.deepEqual(samplingOf(initialState()), samplingInitial(0));
  const before = Object.assign(initialState(), { seq: 0, sampling: Object.assign(samplingInitial(0), { range: '7d', plan: PLAN }) });
  assert.equal(samplingOf(before), before.sampling);
  const after = Object.assign({}, before, { seq: 3 });
  assert.deepEqual(samplingOf(after), samplingInitial(3));
  assert.equal(samplingView(after).plan, null);
  assert.equal(samplingView(after).ranges.find((r) => r.on).key, '30d');
});
