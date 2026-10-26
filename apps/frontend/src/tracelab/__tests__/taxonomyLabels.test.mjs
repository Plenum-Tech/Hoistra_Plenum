// taxonomyLabels — the Labels grid (logic/labels.js, taxonomyActions.js): solid, dashed and inherited cells, a press
// toggles with the head last read as base, the missing-only filter and the "not rates" header, the cell panel with its
// history, and a stale base that keeps the grid and says another tab changed it.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { taxonomyApi, A, C, tlStore, S, X, tick, fresh, loaded, installDefaults, restoreApis } from './tax-helper.mjs';
const L = await import('../logic/labels.js');
const { TABS } = await import('../nav.js');

beforeEach(installDefaults);
afterEach(restoreApis);

const M1 = { id: 'm-1', name: 'counts_cancelled_jobs_as_open', status: 'confirmed', level: 'technical', definition: 'Present when cancelled jobs count as open.' };
const M2 = { id: 'm-2', name: 'invents_asset_codes', status: 'frozen', level: 'technical', definition: 'Present when an asset code is made up.' };
const cell = (state, extra) => Object.assign({ state, label_id: null, source: null, human_origin: false, via: null, via_name: null,
  decided_by: null, decided_at: null, approval: null, base: null }, extra || {});
const MATRIX = { ok: true, available: true, served_at: '2026-10-15T10:00:00+00:00', modes: [M1, M2],
  rows: [
    { turn_id: 't1', conversation_key: 's:chat-1', organization_id: 'org-1', question: 'How many work orders are open?', started_at: '2026-10-14T09:00:00Z',
      batch: { id: 'b-1', plan_step: 'initial_uniform' }, verdicts: { right: 0, wrong: 1, mine: 'wrong' },
      cells: { 'm-1': cell('present', { label_id: 'L-1', source: 'human_open_code', human_origin: true, decided_by: 'ada@example.com', approval: 'single', base: 'L-1' }),
        'm-2': cell('unchecked', { proposal: { id: 'p-1', suggestion: 'absent', evidence: 'Every code it names is in the asset register.', quote: '' } }) } },
    { turn_id: 't2', conversation_key: 's:chat-2', organization_id: 'org-1', question: 'Which certificates expire?', started_at: '2026-10-14T10:00:00Z',
      batch: null, verdicts: { right: 1, wrong: 0, mine: 'right' },
      cells: { 'm-1': cell('absent', { label_id: 'L-2', source: 'human_structured_label', human_origin: true, via: 'm-0', via_name: 'old_wording', approval: 'single', base: null }),
        'm-2': cell('unchecked') } }],
  hidden_rows: 2, total_rows: 2, totals: { reviewed_turns: 2, required: 4, done: 2, missing: 2 }, missing_preview: [],
  sample_fractions: { 'm-1': { present: 1, decided: 2, fraction: 0.5, kind: 'sample_fraction' } },
  fair_sample: { 'm-1': { present: 1, n: 1, rate: 1, ci_low: 0.2065, ci_high: 1, kind: 'uniform_batches_only' } },
  fraction_copy: 'Share of reviewed answers — not a rate for all answers: targeted batches change the mix.',
  batches: [{ id: 'b-1', plan_step: 'initial_uniform' }] };
// A company admin (the default) reading the grid; `owner` makes the person the platform team.
const withGrid = (extra, owner) => fresh(loaded(Object.assign({ tlMatrix: MATRIX, tlMatrixLoadedAt: 1 }, extra || {}), !!owner), { tab: 'labels' });
const grid = () => L.taxonomyLabelsVals(S(), A).tlTaxLabels;

test('cells read solid when a person decided, dashed for an AI suggestion, and say via which mode an inherited one counts', () => {
  withGrid();
  const v = grid();
  const [a, b] = v.rows[0].cells;
  assert.deepEqual([a.kind, a.style, a.mark], ['present', 'solid', '■ present']);
  assert.match(a.title, /From a reviewer’s note · by ada@example.com/);
  assert.deepEqual([b.kind, b.style, b.mark], ['ai', 'dashed', 'AI: absent']);
  assert.match(b.title, /^AI suggestion, not a judgment: absent\. Every code it names is in the asset register\./);
  const [c2, d2] = v.rows[1].cells;
  assert.deepEqual([c2.kind, c2.style, c2.via], ['absent', 'solid', 'via old_wording']);
  assert.deepEqual([d2.kind, d2.mark], ['missing', '? missing']);
  assert.equal(L.cellView({ state: 'present', approval: 'bulk', source: 'accepted_label_proposal' }, 'x').title, 'Accepted AI proposal (approved in bulk)');
  assert.equal(L.cellView({ state: 'present', note_withdrawn: true, source: 'human_open_code' }, 'x').withdrawn, true);
});

test('the header says these are shares of the reviewed sample, and the title counts missing judgments', () => {
  withGrid();
  const v = grid();
  assert.equal(v.header, 'Counts here are shares of the reviewed sample, not rates for all traffic.');
  assert.equal(v.title, 'Labels — 2 answers × 2 modes · 2 missing judgments');
  assert.equal(v.fractionCopy, MATRIX.fraction_copy);
  assert.equal(v.modes[0].fraction, '1 of 2 judged (50%)');
  assert.equal(v.modes[0].fair, '100% in fair batches (21%–100%, 1 answers)');
  assert.equal(v.modes[1].fraction, '—');
  assert.equal(v.hiddenNote, '2 answers stay hidden until you have judged it yourself.');
  fresh();
  assert.equal(grid().title, 'Labels');
  withGrid({ tlMatrix: Object.assign({}, MATRIX, { modes: [], rows: [] }) });
  assert.equal(grid().emptyText, 'No confirmed failure modes yet. Labels start once a failure mode is confirmed.');
});

test('a press toggles the cell with the head it last read as base, and the grid updates in place', async () => {
  const sent = [];
  taxonomyApi.setLabel = async (t, m, body) => { sent.push([t, m, body]); return { ok: true, cell: cell(body.present ? 'present' : body.present === false ? 'absent' : 'unchecked', { base: 'L-9' }), label: { id: 'L-9' } }; };
  withGrid();
  await grid().rows[0].cells[0].toggle();          // present → absent, base L-1
  await grid().rows[1].cells[1].toggle();          // missing → absent, no base
  await grid().rows[0].cells[1].toggle();          // AI says absent → absent (the server records an accepted proposal)
  assert.deepEqual(sent, [['t1', 'm-1', { present: false, base: 'L-1' }], ['t2', 'm-2', { present: false, base: null }],
    ['t1', 'm-2', { present: false, base: null }]]);
  const v = grid();
  assert.equal(v.rows[0].cells[0].kind, 'absent');
  assert.equal(v.rows[1].cells[1].kind, 'absent');
  assert.equal(v.title, 'Labels — 2 answers × 2 modes · 0 missing judgments');
  assert.equal(L.nextValue(cell('absent')), true);
  assert.equal(L.nextValue(cell('unchecked', { proposal: { suggestion: 'present' } })), true);
  assert.equal(L.nextValue(null), false);
  // Clearing sends null and puts the cell back to undecided.
  await A.matrixClear('t1', 'm-1');
  assert.deepEqual(sent.at(-1), ['t1', 'm-1', { present: null, base: 'L-9' }]);
  assert.equal(grid().rows[0].cells[0].kind, 'missing');
  assert.equal(grid().title, 'Labels — 2 answers × 2 modes · 1 missing judgments');
});

test('a stale base keeps the grid, says another tab changed it and offers a reload; a failure offers Retry', async () => {
  taxonomyApi.setLabel = async () => { throw Object.assign(new Error('Changed'), { status: 409, reason: 'stale' }); };
  withGrid();
  assert.equal(await A.matrixToggle('t1', 'm-1'), false);
  let v = grid();
  assert.equal(v.rows[0].cells[0].kind, 'present');
  assert.deepEqual(v.failures.map((f) => [f.text, f.conflict, !!f.retry]), [['Changed in another tab — reload this answer.', true, false]]);
  taxonomyApi.setLabel = async () => { throw new Error('network down'); };
  await A.matrixToggle('t2', 'm-2');
  v = grid();
  const f = v.failures.find((x) => x.key === 'label:t2:m-2');
  assert.equal(f.text, 'Could not save — network down');
  assert.ok(f.retry);
  assert.equal(v.rows[1].cells[1].error, 'Could not save — network down');
  taxonomyApi.setLabel = async () => ({ ok: true, cell: cell('absent', { base: 'L-3' }) });
  assert.equal((await f.retry()).ok, true, 'Retry sends the same press again');
  assert.equal(grid().rows[1].cells[1].kind, 'absent');
  assert.equal(grid().failures.some((x) => x.key === 'label:t2:m-2'), false);
});

test('Only missing and the batch filter re-read the grid from the first page', async () => {
  const asked = [];
  taxonomyApi.labelsMatrix = async (q) => { asked.push(q); return MATRIX; };
  withGrid({ tlMatrix: null, tlMatrixLoadedAt: null, tlMatrixFilter: { batch_id: '', state: 'all', offset: 50 } });
  grid().filter.toggleMissing();
  await tick();
  assert.deepEqual(asked[0], { batch_id: undefined, state: 'missing', limit: 50, offset: 0 });
  grid().filter.setBatch({ target: { value: 'b-1' } });
  await tick();
  assert.deepEqual(asked[1], { batch_id: 'b-1', state: 'missing', limit: 50, offset: 0 });
  assert.equal(grid().filter.onlyMissing, true);
  assert.deepEqual(grid().filter.batches.map((b) => b.label), ['All reviewed answers', 'First fair batch · b-1']);
  TABS.push('labels');
  try {
    withGrid({ tlMatrix: null, tlMatrixLoadedAt: null }, false);
    fresh(loaded({}, false), { tab: 'progress' });
    A.matrixShowMissing();
    await tick();
    assert.equal(S().tab, 'labels', 'from Progress to the Labels tab');
    assert.equal(X().tlMatrixFilter.state, 'missing');
    assert.deepEqual(asked.at(-1), { batch_id: undefined, state: 'missing', limit: 50, offset: 0 });
  } finally { TABS.pop(); }
});

test('paging moves by whole pages and never past the last answer', async () => {
  const asked = [];
  const big = Object.assign({}, MATRIX, { total_rows: 120 });
  taxonomyApi.labelsMatrix = async (q) => { asked.push(q); return big; };
  withGrid({ tlMatrix: big });
  assert.equal(grid().pager.text, 'Answers 1–50 of 120');
  assert.deepEqual([grid().pager.canPrev, grid().pager.canNext], [false, true]);
  grid().pager.next();
  await tick();
  assert.equal(asked.at(-1).offset, 50);
  assert.equal(X().tlMatrixFilter.offset, 50);
  A.matrixPage(5);
  await tick();
  assert.equal(asked.at(-1).offset, 100, 'never past the last page');
  const before = asked.length;
  A.matrixPage(-5);
  await tick();
  assert.equal(asked.at(-1).offset, 0);
  A.matrixPage(-1);
  assert.equal(asked.length, before + 1, 'no read when already on the first page');
});

test('the cell panel shows the history and the explicit choices, and the platform team cannot open answers', async () => {
  taxonomyApi.labelHistory = async () => ({ ok: true, chain: [
    { id: 'L-2', present: true, tombstone: false, source: 'human_structured_label', approval: 'single', decided_by_email: 'ada@example.com', created_at: '2026-10-14T10:00:00Z', superseded_by: null },
    { id: 'L-1', present: false, tombstone: false, source: 'accepted_label_proposal', approval: 'bulk', decided_by_email: 'bo@example.com', created_at: '2026-10-13T10:00:00Z', superseded_by: 'L-2' }] });
  withGrid();
  await grid().rows[0].cells[1].more();
  const p = L.taxonomyLabelsVals(S(), A).tlTaxCell;
  assert.equal(p.modeName, 'invents_asset_codes');
  assert.deepEqual(p.proposal, { suggestion: 'absent', evidence: 'Every code it names is in the asset register.', quote: '' });
  assert.deepEqual([p.presentLabel, p.absentLabel], ['Mark present', 'Accept: absent']);
  assert.equal(p.clear, null);
  assert.deepEqual(p.history.map((h) => [h.text, h.source, h.current]), [['Present', 'Marked by a reviewer', true],
    ['Absent', 'Accepted AI proposal (approved in bulk)', false]]);
  assert.ok(grid().rows[0].open);
  A.matrixCloseCell();
  assert.equal(L.taxonomyLabelsVals(S(), A).tlTaxCell, null);
  withGrid({}, true);
  assert.equal(grid().rows[0].open, null);
});

test('a decided cell offers Clear in its panel, and a press from the panel sends that value', async () => {
  const sent = [];
  taxonomyApi.labelHistory = async () => ({ ok: true, chain: [] });
  taxonomyApi.setLabel = async (t, m, body) => { sent.push([t, m, body]); return { ok: true, cell: cell(body.present === null ? 'unchecked' : 'present', { base: 'L-8' }) }; };
  withGrid();
  await A.matrixOpenCell('t1', 'm-1');
  const p = () => L.taxonomyLabelsVals(S(), A).tlTaxCell;
  assert.equal(typeof p().clear, 'function');
  await p().markPresent();
  await p().clear();
  assert.deepEqual(sent, [['t1', 'm-1', { present: true, base: 'L-1' }], ['t1', 'm-1', { present: null, base: 'L-8' }]]);
  assert.equal(p().clear, null, 'nothing left to clear');
});

test('the labels slice keys are all in the reset, and a company switch empties the grid', () => {
  const dir = new URL('..', import.meta.url);
  const src = ['taxonomyActions.js', 'logic/labels.js', 'logic/taxCommon.js'].map((f) => readFileSync(new URL(f, dir), 'utf8')).join('\n');
  const keys = new Set([...src.matchAll(/\b(tlMatrix[A-Za-z]*)\s*:/g)].map((m) => m[1]));
  const patch = C.taxonomyInitial(1);
  assert.ok(keys.size >= 8, [...keys].join(','));
  for (const k of keys) assert.ok(Object.prototype.hasOwnProperty.call(patch, k), k);
  assert.notEqual(C.taxonomyInitial(1).tlMatrixFilter, patch.tlMatrixFilter, 'each reset gets a fresh filter');
  withGrid();
  tlStore.set((s) => ({ seq: s.seq + 1 }));
  assert.equal(X().tlMatrix, null);
});

test('each kind of cell reads as one thing: solid present or absent, dashed AI, or missing, with what to press', () => {
  const absent = L.cellView(cell('absent', { source: 'human_structured_label', decided_by: 'ada@example.com' }), 'invents_asset_codes');
  assert.deepEqual([absent.kind, absent.style, absent.mark, absent.via, absent.withdrawn], ['absent', 'solid', '□ absent', '', false]);
  assert.equal(absent.label, 'invents_asset_codes: absent. Press to change.');
  assert.equal(absent.title, 'Marked by a reviewer · by ada@example.com');
  const present = L.cellView(cell('present', { via: 'm-0', via_name: 'old_wording', note_withdrawn: true }), 'x');
  assert.deepEqual([present.kind, present.mark, present.via, present.withdrawn], ['present', '■ present', 'via old_wording', true]);
  assert.match(present.title, /The note this label came from was withdrawn\.$/);
  assert.equal(L.cellView(cell('present', { via: 'm-0' }), 'x').via, 'via a merged failure mode');
  const ai = L.cellView(cell('unchecked', { proposal: { suggestion: 'present', evidence: 'Counts job 4.', quote: 'job 4' } }), 'x');
  assert.deepEqual([ai.kind, ai.style, ai.mark, ai.label], ['ai', 'dashed', 'AI: present', 'x: AI suggests present. Press to accept.']);
  assert.equal(ai.title, 'AI suggestion, not a judgment: present. Counts job 4. “job 4”');
  assert.match(L.cellView(cell('unchecked', { proposal: { suggestion: 'absent' } }), 'x').title, /No evidence given\.$/);
  const none = L.cellView(null, '');
  assert.deepEqual([none.kind, none.style, none.mark, none.title, none.label], ['missing', 'missing', '? missing', 'No judgment yet.', 'This failure mode: missing. Press to mark absent.']);
  assert.equal(L.cellView(cell('unchecked'), 'x').kind, 'missing');
  assert.equal(L.sourceText('accepted_suggestion', 'bulk'), 'Accepted AI suggestion (approved in bulk)');
  assert.equal(L.sourceText('unknown_source', 'bulk'), '');
});

test('a press from the open cell panel reads that cell’s history again, and a press on another cell does not', async () => {
  let reads = 0;
  taxonomyApi.labelHistory = async () => { reads += 1; return { ok: true, chain: [] }; };
  taxonomyApi.setLabel = async (t, m, body) => ({ ok: true, cell: cell(body.present ? 'present' : 'absent', { base: 'L-7' }) });
  withGrid();
  await A.matrixOpenCell('t1', 'm-1');
  assert.equal(reads, 1);
  await A.matrixSet('t1', 'm-1', false);
  await tick();
  assert.equal(reads, 2, 'the chain the panel shows now ends with the press');
  await A.matrixSet('t2', 'm-2', false);
  await tick();
  assert.equal(reads, 2, 'a cell that is not open has no history on screen');
  assert.equal(grid().rows[1].cells[1].kind, 'absent');
});
