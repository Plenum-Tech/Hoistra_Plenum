// The notes under each asset on the Assets page: the design's "Work orders and inspection
// notes" block, built from GET /api/maintenance/inspections rows. Until 28 Sep 2026 every row
// was hard-coded hist: [] and the block never showed, although the reports were on record.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { inspectionNotes, woState, openWorkOrderNotes } from '../src/logic/assetsCondition.js';

const chiller = {
  asset_id: 'a-101', asset_code: 'B-301-CHILLER-101', wo_code: 'WO-B-301-4421', inspection_date: '2026-07-10',
  vendor: 'Apex Mechanical', inspector: 'Apex Mechanical engineer', risk_level: 'High', recommendation_open: true,
  observations: 'Compressor 2 contactor replaced; Refrigerant charge 8% low — topped up, leak not found; Condenser coils fouled',
  recommendation: 'Leak test within 3 months; condenser clean',
  warranty: 'Compressor 2 under OEM warranty to Mar 2027 — contactor claimable'
};

test('a report reads as the design has it: order, date, vendor, grade, findings, open recommendation, warranty', () => {
  const [n] = inspectionNotes([chiller]);
  assert.equal(n.id, 'WO-B-301-4421');
  assert.equal(n.when, '10 Jul 2026 · Apex Mechanical · High risk');
  assert.equal(n.text, 'Compressor 2 contactor replaced · Refrigerant charge 8% low — topped up, leak not found · Condenser coils fouled');
  assert.equal(n.flag, 'Recommendation open · Leak test within 3 months; condenser clean');
  assert.equal(n.flagShow, 'block');
  assert.equal(n.warranty, 'Compressor 2 under OEM warranty to Mar 2027 — contactor claimable');
  assert.notEqual(n.warrShow, 'none');
});

test('a done recommendation reads done and muted; no warranty, no chip; no order, says inspection', () => {
  const [n] = inspectionNotes([{ inspection_date: '2026-05-04', inspector: 'Apex Lifts engineer', recommendation: 'Monitor at PPM',
    recommendation_open: false, observations: 'Serviceable' }]);
  assert.equal(n.id, 'Inspection');
  assert.equal(n.flag, 'Recommendation done · Monitor at PPM');
  assert.equal(n.flagColor, 'var(--color-neutral-500)');
  assert.equal(n.warrShow, 'none');
});

test('newest first, three at most, and nothing on record is an empty list', () => {
  const rows = ['2026-01-01', '2026-06-01', '2026-03-01', '2026-09-01'].map((d) => ({ inspection_date: d, observations: d }));
  assert.deepEqual(inspectionNotes(rows).map((n) => n.text), ['2026-09-01', '2026-06-01', '2026-03-01']);
  assert.deepEqual(inspectionNotes([]), []);
  assert.deepEqual(inspectionNotes(null), []);
});

test('a report that records a grade reads it, as the prototype does', () => {
  const [n] = inspectionNotes([{ inspection_date: '2026-06-14', vendor: 'Apex Mechanical', risk_level: 'High',
    finding_type: 'Condition grade 4', observations: 'Condenser coils fouled', wo_code: 'WO-4421' }]);
  assert.equal(n.when, '14 Jun 2026 · Apex Mechanical · grade 4');
  const [m] = inspectionNotes([{ inspection_date: '2026-06-14', risk_level: 'High', finding_type: 'Condition' }]);
  assert.match(m.when, /High risk$/);
});

test('migrated open orders are open, in their CMMS spelling, and read as the prototype does', () => {
  assert.equal(woState('Draft'), 'awaiting');
  assert.equal(woState('In progress'), 'live');
  assert.equal(woState('Held'), 'held');
  assert.equal(woState('active'), 'live');
  assert.equal(woState('Completed'), null);
  const notes = openWorkOrderNotes([
    { wo_code: 'WO-4527', request_type: 'Predictive', status: 'Draft', created_at: '2026-09-25' },
    { wo_code: 'WO-4512', request_type: 'Compliance', status: 'Held', issue_description: 'Blocked — accreditation', created_at: '2026-09-20' },
    { wo_code: 'WO-4400', status: 'Completed' }]);
  assert.equal(notes.length, 2);
  assert.equal(notes[0].id, 'WO-4527');
  assert.equal(notes[0].when, 'Predictive · Draft');
  assert.equal(notes[0].text, 'Awaiting approval');
  assert.equal(notes[1].text, 'Blocked — accreditation');
  assert.equal(notes[1].icon, 'ph-wrench');
});
