// home — the lab's landing page: one card per stage of the loop and one next step, drawn from replies Trace Lab
// already serves. Anything not yet read is a dash, never a made-up number.
import { test } from 'node:test';
import assert from 'node:assert/strict';

const { homeView, summarize } = await import('../logic/home.js');

const PROGRESS = { open_coding: { reviewed: 13, wrong: 7, missing: 7 }, open_batches: 1, fair_sample: { rate: 0.54 } };

test('the replies become one summary; a reply that failed or is missing stays unknown', () => {
  const s = summarize({
    sets: { active: { id: 's1', version: 1, name: 'Bishopsgate 5' }, versions: [{ id: 's1' }] },
    runs: { runs: [{ id: 'r1', status: 'done', pass_rate: { passes: 4, graded: 4 }, started_at: '2026-10-19T06:24:00Z' }] },
    progress: PROGRESS,
    notes: { notes: [{ id: 'n1' }] },
    modes: { modes: [{ status: 'candidate' }, { status: 'confirmed' }, { status: 'candidate' }, { status: 'retired' }] },
    judges: { judges: [{ id: 'j1', frozen_at: '2026-10-19T17:07:00Z' }, { id: 'j2', frozen_at: null }] },
    batches: { batches: [{ id: 'b1' }, { id: 'b2' }] }
  });
  assert.deepEqual(s, { hasGolden: true, goldenName: 'Bishopsgate 5', lastRun: { passes: 4, graded: 4 }, reviewed: 13, wrong: 7, missing: 7,
    openBatches: 1, unfiled: 1, modes: { live: 3, confirmed: 1 }, judges: 2, frozen: 1, batches: 2 });
  const none = summarize({});
  assert.deepEqual(none, { hasGolden: null, goldenName: null, lastRun: null, reviewed: null, wrong: null, missing: null, openBatches: null,
    unfiled: null, modes: null, judges: null, frozen: null, batches: null });
});

test('five stages in loop order, each with a tab to open, and dashes for what is unknown', () => {
  const v = homeView(summarize({ progress: PROGRESS }));
  assert.deepEqual(v.stages.map((x) => [x.name, x.tab]), [['Check', 'golden'], ['Explain', 'progress'], ['Group', 'progress'], ['Fix', 'judges'], ['Watch', 'watch']]);
  assert.equal(v.stages[1].big, '13 reviewed');
  assert.equal(v.stages[0].big, '—');
  assert.equal(v.stages[3].big, '—');
});

test('the next step: a golden set first, then waiting verdicts, then unfiled notes, then a failure mode, then a judge, then Watch', () => {
  const step = (over) => homeView(Object.assign(summarize({}), over)).next;
  assert.deepEqual(step({ hasGolden: false }), { text: 'Upload a golden set: the questions with the answers you know are right.', tab: 'golden', button: 'Open the golden set' });
  assert.equal(step({ hasGolden: true, missing: 7 }).tab, 'map');
  assert.match(step({ hasGolden: true, missing: 7 }).text, /7 answers are waiting for your verdict/);
  assert.equal(step({ hasGolden: true, missing: 0, unfiled: 2 }).tab, 'progress');
  assert.equal(step({ hasGolden: true, missing: 0, unfiled: 0, modes: { live: 0, confirmed: 0 } }).tab, 'progress');
  assert.equal(step({ hasGolden: true, missing: 0, unfiled: 0, modes: { live: 2, confirmed: 1 }, frozen: 0 }).tab, 'judges');
  assert.equal(step({ hasGolden: true, missing: 0, unfiled: 0, modes: { live: 2, confirmed: 1 }, frozen: 1, batches: 0 }).tab, 'watch');
  assert.equal(step({ hasGolden: true, missing: 0, unfiled: 0, modes: { live: 2, confirmed: 1 }, frozen: 1, batches: 3 }).tab, 'map');
  assert.equal(step({}).text, 'Reading where things stand…');
});
