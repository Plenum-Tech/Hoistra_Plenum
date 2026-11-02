// Two things a person could not see on the migration card (reported 2 Nov 2026).
//
// 1. The lookup tables a run is about to create — shared_buildings, shared_vendors — appeared
//    nowhere on "Confirm column matching". Nothing was missing from the payload: the
//    pre-semantic gate carries the whole column_intelligence, shared_attribute_tables included,
//    and the classification step already draws them. The column-matching screen builds its list
//    from review_items_by_table / auto_approved_by_table / the unresolved set, all keyed by
//    SOURCE sheet, and a lookup table is a destination no source sheet names. So it fell through
//    every list, and the reconcile line's "all 5 identified tables" meant the 5 CSVs.
//
// 2. "Run details" listed only steps with a duration, which means finished ones. A run paused at
//    step 6 showed five lines and said nothing about the five still ahead. The version chip
//    beside the filename was a plain <span>: it looked clickable and answered nothing.
//
// These are structural. This suite is pure logic with no DOM, so a render cannot be driven here;
// what can be pinned is that the screen reaches for the data at all, which is the part that was
// missing. The step model underneath IS pure, so that half is asserted properly.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const read = (p) => readFileSync(new URL(p, import.meta.url), 'utf-8');
const GATE = read('../src/cafm/features/ai/pipeline/migration/gates/gate-pre-semantic.tsx');
const WIZARD = read('../src/cafm/hoistra-migration-wizard.tsx');


// -- 1. the new tables are on the screen that confirms the schema -------------------------

test('the column-matching gate reads the lookup tables', () => {
  assert.ok(GATE.includes('shared_attribute_tables'),
    'the gate never mentions shared_attribute_tables, so a run that creates shared_buildings '
    + 'shows no sign of it on the screen where the schema is confirmed');
});

test('it reads them from the live column intelligence, not the raw payload', () => {
  // columnIntelligence follows a "Re-match now" preview and a table-routing override;
  // payload.column_intelligence is the state as the gate opened and goes stale behind both.
  assert.ok(/columnIntelligence\?\.shared_attribute_tables/.test(GATE),
    'reading payload.column_intelligence directly would leave the panel stale after a re-match');
});

test('it renders the columns the service declared, not its own list', () => {
  // udr/column_intelligence.py declares the shape and renders create_ddl from it. A panel that
  // wrote the four column names out again would be a second copy of the schema, free to drift
  // from the one actually created -- which is the whole reason the backend now publishes them.
  assert.ok(/\(t\.columns \?\? \[\]\)\.map/.test(GATE),
    'the panel does not walk the declared columns');
  for (const field of ['c.name', 'c.type', 'c.note', 'c.is_primary_key']) {
    assert.ok(GATE.includes(field), 'the panel ignores ' + field + ' from the declaration');
  }
  assert.ok(GATE.includes('(t.unique_on ?? [])'),
    'the per-company unique should come from the service too, not be composed here');
});

test('it does not keep its own copy of the column names', () => {
  const panel = GATE.slice(GATE.indexOf('New lookup tables'), GATE.indexOf('{/* Bulk action */}'));
  assert.ok(!/"organization_id"|'organization_id'/.test(panel),
    'the panel hard-codes a column name again; the declaration is the one source');
});

test('it says where the values came from', () => {
  assert.ok(GATE.includes('source_columns'),
    'without the source columns a reader cannot tell which sheets fed the lookup table');
});

test('the panel does not invite an approval it cannot take', () => {
  // The decision was taken at the classification gate and the columns are fixed by the writer.
  // A checkbox here would post a decision the gate's body builder does not carry.
  const panel = GATE.slice(GATE.indexOf('New lookup tables'), GATE.indexOf('{/* Bulk action */}'));
  assert.ok(panel.length > 0, 'the lookup panel is gone; this test needs re-aiming');
  assert.ok(!/onChange|onClick/.test(panel), 'the lookup panel took on a control');
});


// -- 2. the whole run, not only the part that finished ------------------------------------

test('run details lists every step, not only the timed ones', () => {
  const start = WIZARD.indexOf('function RunDetails');
  const body = WIZARD.slice(start, WIZARD.indexOf('\nfunction ', start + 10));
  assert.ok(body.length > 0, 'RunDetails is gone');
  assert.ok(!/steps\.filter\(\(s\) => s\.durationMs != null\)/.test(body),
    'RunDetails filters to finished steps again, so a paused run hides the steps still to come');
  assert.ok(/steps\.map\(/.test(body), 'RunDetails no longer walks every step');
});

test('a step with no duration still gets a row', () => {
  const start = WIZARD.indexOf('function RunDetails');
  const body = WIZARD.slice(start, WIZARD.indexOf('\nfunction ', start + 10));
  assert.ok(body.includes('s.durationMs != null ? fmtDuration(s.durationMs)'),
    'a step that has not run needs a placeholder, not a blank or a crash');
});

test('the version chip is something you can actually click', () => {
  assert.ok(/onClick=\{\(\) => setRunOpen/.test(WIZARD),
    'the id chip is not wired to anything, so clicking it still answers nothing');
  assert.ok(WIZARD.includes('aria-expanded={runOpen}'),
    'a control that opens a panel says so');
});

test('run details is told which version it is describing', () => {
  assert.ok(/migrationId=\{migrationId\}/.test(WIZARD) && /versionLabel=/.test(WIZARD),
    'the panel the chip opens should name the version the chip shows');
});


// -- the step model under it, which is pure and can be asserted properly ---------------------

const { wizardModel, WIZARD_STEPS } = await import('../src/cafm/hoistra-wizard-steps.js');

test('there are ten steps and they are numbered one to ten', () => {
  assert.equal(WIZARD_STEPS.length, 10);
  assert.deepEqual(WIZARD_STEPS.map((s) => s.n), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
});

test('a run paused in the middle still describes all ten steps', () => {
  const model = wizardModel({ kind: 'gate', node: 6 }, [], []);
  assert.equal(model.steps.length, 10,
    'the model dropped steps; the panel can only show what it is given');
  const states = model.steps.map((s) => s.state);
  assert.ok(states.includes('done') && states.includes('pending'),
    'a run at step 6 has both finished steps and steps to come: ' + states.join(','));
});

test('a step that has not run yet carries no duration', () => {
  // Which is exactly the case the old panel dropped on the floor.
  const model = wizardModel({ kind: 'gate', node: 6 }, [], []);
  const ahead = model.steps.filter((s) => s.n > 6);
  assert.equal(ahead.length, 4);
  assert.ok(ahead.every((s) => s.durationMs == null && s.state === 'pending'));
});

test('every step is numbered and titled, so the panel can name it', () => {
  const model = wizardModel({ kind: 'running', node: 3 }, [], []);
  for (const s of model.steps) {
    assert.ok(Number.isInteger(s.n) && s.n >= 1 && s.n <= 10, JSON.stringify(s));
    assert.ok(s.title && s.short, 'step ' + s.n + ' has no title');
    assert.ok(['done', 'active', 'pending', 'error'].includes(s.state), s.state);
  }
});
