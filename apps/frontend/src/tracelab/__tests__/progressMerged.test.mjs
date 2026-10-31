// progressMerged — the shape GET /progress will have once the failure-mode extension is merged into it
// (`discovery` becomes a list of blocks, `saturation` becomes {hint, text}) against the two readers of it:
// the Phase 4 progress view (logic/sampling.js progressView, the older card) and the failure-mode rows
// (logic/taxonomy.js). The rows read both shapes. The older view did not at first (the task that built the rows
// did not edit it): the second test was marked `todo` until the Progress-tab wiring task (Phase 5 Task 8) made
// progressView report `discoveryMoved: true` for a list, so the older curve and saturation line step aside for the
// new rows. The `todo` option is gone; the test now guards it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { initialState } from '../store.js';
import { samplingInitial, progressView } from '../logic/sampling.js';

const state = (sampling) => Object.assign(initialState(), { sampling: Object.assign(samplingInitial(), sampling || {}) });

const OLD = { ok: true, open_batches: 1, block: 10, open_coding: { reviewed: 12, wrong: 3, right: 9, missing: 0, missing_turns: [], missing_more: 0 },
  discovery: { block: 10, points: [{ block: 1, reviews_through: 10, wrong: 3, partial: false }] }, saturation: { available: false, hint: false } };
const MERGED = Object.assign({}, OLD, {
  discovery: [{ block: 1, reviews_through: 10, new_modes: 2, cumulative_modes: 2 }],
  saturation: { hint: true, text: 'No new failure mode in the last 20 reviews; consider moving to counting.' } });

test('the older progress view keeps drawing its own curve for the older shape', () => {
  const v = progressView(state({ progress: OLD, progressLoadedAt: 1 }));
  assert.equal(v.curve.dots.length, 1);
  assert.equal(v.saturation, 'Saturation: — (shown once failure modes are named)');
});

test('the older progress view steps aside for the merged shape', () => {
  const v = progressView(state({ progress: MERGED, progressLoadedAt: 1 }));
  assert.equal(v.discoveryMoved, true, 'discovery arrived as a list of blocks');
  assert.equal(v.curve.empty, true, 'the list is never read as .points');
  assert.deepEqual(v.tiles.map((t) => t.value), ['12', '3', '0', '1'], 'the rest of the older view is unaffected');
});
