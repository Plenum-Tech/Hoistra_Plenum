// migrationWizardSteps — the migration card's step rail: which of the ten pipeline steps is on
// screen, which are done and can be reopened, which are still to come, and the one-line
// status the header shows. The service tells the card what it is waiting for (a gate, a step
// pause, a running node); this model turns that into the rail. It is the same order the
// service runs: ingest → PK / unique-table gates → deterministic mapping → pre-semantic
// (routing → keys & shared attributes → column mapping → column matching) → semantic →
// field mapping → preprocess → hierarchy → verification → output → write.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { WIZARD_STEPS, wizardModel, preSemanticSub, fmtDuration, settleActive } from '../src/cafm/hoistra-wizard-steps.js';

const titles = (m) => m.steps.map((s) => s.title);
const states = (m) => m.steps.map((s) => s.state).join(' ');

test('the rail lists the ten pipeline steps in the order the service runs them', () => {
  assert.deepEqual(WIZARD_STEPS.map((s) => s.n), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
  assert.deepEqual(titles(wizardModel({ kind: 'loading' }, [], [])), [
    'File ingestion', 'Table & column analysis', 'Pre-semantic review', 'Semantic mapping',
    'Field mapping review', 'Data preprocessing', 'Hierarchy detection', 'Hierarchy verification',
    'Output generation', 'Write to database'
  ]);
});

test('a gate marks its node active, earlier nodes done and later nodes pending', () => {
  const m = wizardModel({ kind: 'gate', node: 2, sub: 'pk' }, [1], []);
  assert.equal(m.activeNode, 2);
  assert.equal(states(m), 'done active pending pending pending pending pending pending pending pending');
  assert.equal(m.status.label, 'Needs your review');
  assert.equal(m.status.tone, 'accent');
  assert.equal(m.subTitle, 'Confirm primary keys');
  assert.deepEqual(m.subIndex, [1, 2]);
  assert.equal(m.context, 'Step 2 of 10 — Table & column analysis: confirm primary keys (1 of 2)');
});

test('the second pre-semantic pass is the last of the four pre-semantic sub-steps', () => {
  const m = wizardModel({ kind: 'gate', node: 3, sub: 'columns' }, [1, 2], []);
  assert.equal(m.subTitle, 'Confirm column matching');
  assert.deepEqual(m.subIndex, [4, 4]);
  assert.equal(m.context, 'Step 3 of 10 — Pre-semantic review: confirm column matching (4 of 4)');
  const c = wizardModel({ kind: 'gate', node: 3, sub: 'classification' }, [1, 2], []);
  assert.equal(c.subTitle, 'Keys & shared attributes');
  assert.deepEqual(c.subIndex, [2, 4]);
});

test('a done step can be reopened only when its output was kept', () => {
  const kept = wizardModel({ kind: 'gate', node: 3, sub: 'routing' }, [1, 2], []);
  assert.deepEqual(kept.steps.slice(0, 3).map((s) => s.reviewable), [true, true, false]);
  const lost = wizardModel({ kind: 'gate', node: 3, sub: 'routing' }, [1], []);
  assert.deepEqual(lost.steps.slice(0, 3).map((s) => s.reviewable), [true, false, false]);
});

test('a running node reads as running', () => {
  const m = wizardModel({ kind: 'running', node: 4 }, [1, 2, 3], []);
  assert.equal(states(m), 'done done done active pending pending pending pending pending pending');
  assert.equal(m.status.label, 'Running');
  assert.equal(m.subTitle, null);
  assert.equal(m.context, 'Step 4 of 10 — Semantic mapping is running');
});

test('a step pause that waits for a person says so; an automatic one keeps running', () => {
  const manual = wizardModel({ kind: 'pause', node: 6, auto: false }, [1, 2, 3, 4, 5], []);
  assert.equal(manual.status.label, 'Paused');
  assert.equal(manual.status.tone, 'neutral');
  assert.equal(manual.context, 'Step 6 of 10 — Data preprocessing finished: review it, then continue');
  const auto = wizardModel({ kind: 'pause', node: 9, auto: true }, [1, 2, 3, 4, 5, 6, 7, 8], []);
  assert.equal(auto.status.label, 'Running');
  assert.equal(auto.context, 'Step 9 of 10 — Output generation finished: continuing');
});

test('a review step that needs decisions counts as a gate', () => {
  const m = wizardModel({ kind: 'gate', node: 5 }, [1, 2, 3, 4], []);
  assert.equal(m.subTitle, 'Field mapping decisions');
  assert.equal(m.subIndex, null);
  assert.equal(m.context, 'Step 5 of 10 — Field mapping review: field mapping decisions');
  const w = wizardModel({ kind: 'gate', node: 10 }, [1, 2, 3, 4, 5, 6, 7, 8, 9], []);
  assert.equal(w.subTitle, 'Confirm & write');
  assert.equal(w.context, 'Step 10 of 10 — Write to database: confirm & write');
});

test('completion marks every step done with the write as the current one', () => {
  const m = wizardModel({ kind: 'complete', node: 10 }, [1, 2, 3, 4, 5, 6, 7, 8, 9], []);
  assert.equal(states(m), 'done done done done done done done done done done');
  assert.equal(m.activeNode, 10);
  assert.equal(m.steps[9].current, true);
  assert.equal(m.steps[9].reviewable, false);
  assert.equal(m.status.label, 'Complete');
  assert.equal(m.status.tone, 'ok');
  assert.equal(m.context, 'All 10 steps complete — the data is in the database');
});

test('a failure marks the failed node and keeps the later steps pending', () => {
  const m = wizardModel({ kind: 'failed', node: 6 }, [1, 2, 3, 4, 5], []);
  assert.equal(states(m), 'done done done done done error pending pending pending pending');
  assert.equal(m.status.label, 'Failed');
  assert.equal(m.status.tone, 'risk');
  assert.equal(m.context, 'Step 6 of 10 — Data preprocessing failed');
});

test('a cancelled or restarting run is named, not shown as a step', () => {
  const c = wizardModel({ kind: 'cancelled' }, [1, 2], []);
  assert.equal(c.status.label, 'Cancelled');
  assert.equal(c.context, 'Migration cancelled');
  // Cancelled at step 3: what finished stays green, nothing is marked current.
  const at3 = wizardModel({ kind: 'cancelled', node: 3 }, [1, 2], []);
  assert.equal(states(at3), 'done done pending pending pending pending pending pending pending pending');
  assert.equal(at3.steps.some((s) => s.current), false);
  assert.deepEqual(at3.steps.slice(0, 2).map((s) => s.reviewable), [true, true]);
  const r = wizardModel({ kind: 'restarting', node: 1 }, [1, 2, 3], []);
  assert.equal(r.status.label, 'Restarting');
  assert.equal(states(r), 'active pending pending pending pending pending pending pending pending pending');
  assert.equal(r.context, 'Restarting from step 1 — File ingestion');
});

test('before the first status arrives the first step is starting', () => {
  const m = wizardModel({ kind: 'loading' }, [], []);
  assert.equal(m.activeNode, 1);
  assert.equal(m.steps[0].state, 'active');
  assert.equal(m.status.label, 'Starting');
  assert.equal(m.context, 'Starting the migration');
});

test('a step shows how long the service took on it', () => {
  const nodes = [{ node_id: 1, duration_ms: 2900 }, { node_id: 2, duration_ms: null }];
  const m = wizardModel({ kind: 'gate', node: 3, sub: 'routing' }, [1, 2], nodes);
  assert.equal(m.steps[0].durationMs, 2900);
  assert.equal(m.steps[1].durationMs, null);
  assert.equal(m.steps[9].durationMs, null);
});

test('the pre-semantic pass is read from the gate payload', () => {
  assert.equal(preSemanticSub({ locked_phase: 'columns' }), 'columns');
  assert.equal(preSemanticSub({ gate_step: 'column_matching' }), 'columns');
  assert.equal(preSemanticSub({ locked_phase: 'tables', gate_step: 'table_routing' }), 'routing');
  assert.equal(preSemanticSub({}), 'routing');
  assert.equal(preSemanticSub(null), 'routing');
});

test('while the service is running the current step never slips backwards', () => {
  // The service reports current_step 2 while it builds step 3's column analysis after the
  // routing answer (run 4cfd9b86, 30 Sep 2026): the rail had shown step 3 and must stay there.
  const atRouting = { kind: 'gate', node: 3, sub: 'routing' };
  assert.deepEqual(settleActive(atRouting, { kind: 'running', node: 2 }), { kind: 'running', node: 3 });
  assert.deepEqual(settleActive(atRouting, { kind: 'running', node: 4 }), { kind: 'running', node: 4 });
  assert.deepEqual(settleActive({ kind: 'running', node: 6 }, { kind: 'running', node: 5 }), { kind: 'running', node: 6 });
});

test('a gate, a pause, a failure or a restart is taken as reported even when it is earlier', () => {
  assert.deepEqual(settleActive({ kind: 'gate', node: 5 }, { kind: 'gate', node: 3, sub: 'columns' }), { kind: 'gate', node: 3, sub: 'columns' });
  assert.deepEqual(settleActive({ kind: 'running', node: 7 }, { kind: 'pause', node: 6, auto: false }), { kind: 'pause', node: 6, auto: false });
  assert.deepEqual(settleActive({ kind: 'running', node: 7 }, { kind: 'failed', node: 6 }), { kind: 'failed', node: 6 });
  assert.deepEqual(settleActive({ kind: 'restarting', node: 1 }, { kind: 'running', node: 1 }), { kind: 'running', node: 1 });
  assert.deepEqual(settleActive({ kind: 'gate', node: 8 }, { kind: 'restarting', node: 1 }), { kind: 'restarting', node: 1 });
  assert.deepEqual(settleActive(null, { kind: 'running', node: 2 }), { kind: 'running', node: 2 });
  assert.deepEqual(settleActive({ kind: 'loading' }, { kind: 'running', node: 1 }), { kind: 'running', node: 1 });
});

test('durations read as people say them', () => {
  assert.equal(fmtDuration(2900), '2.9 s');
  assert.equal(fmtDuration(500), '0.5 s');
  assert.equal(fmtDuration(61000), '1 min 1 s');
  assert.equal(fmtDuration(125000), '2 min 5 s');
  assert.equal(fmtDuration(null), null);
  assert.equal(fmtDuration(0), '0 s');
});
