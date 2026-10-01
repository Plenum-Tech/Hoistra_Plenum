// migrationOverrideCanCreateAColumn — Override could only point at a column that already existed.
//
// The field-mapping gate offers Accept / Reject / Override. Override's picker was built from
// the AI's alternative suggestions plus canonical_columns_by_table — every one of them a column
// already on the destination table. So a source field whose real home was a NEW column had no
// decision that fitted: reject it and the data is dropped, or override it onto a column that
// means something else and the data is wrong.
//
// The gate has understood this the whole time. human_review_node reads is_new_column on an
// override and records an ExtraFieldConfig so schema_write_node emits the ALTER TABLE. Nothing
// could ask for it. These tests hold the picker to sending it.
//
// Since 30 Sep 2026 the gate is drawn as CAFM Web's semantic review (logic/migrationGates.js):
// Override is "Existing column" or "New column (DDL)", and the table a sheet's new columns land
// on is the sheet's canonical table, chosen once per sheet. The tests now hold the behaviour —
// what is sent — rather than the text of the old picker.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { safeColumn } from '../src/logic/migration.js';
import { fieldMappingBody, fieldMappingView, fieldMappingProblems, FM_DATA_TYPES } from '../src/logic/migrationGates.js';

const PAYLOAD = {
  review_items_by_table: {
    Assets: [{ source_field: 'Asset Ref', suggested_target: 'asset_code', confidence: 0.6, suggestions: [{ target: 'asset_code', confidence: 0.6 }] }]
  },
  unmappable_items_by_table: {},
  table_routing: { Assets: 'assets' },
  existing_canonical_tables: ['assets', 'sites'],
  canonical_columns_by_table: { assets: ['asset_code', 'barcode'], sites: ['site_id'] }
};
const K = 'fm:Assets.Asset Ref';
const newCol = (extra) => Object.assign({ [K]: 'override', [K + '#mode']: 'new' }, extra || {});

test('the picker offers a new column', () => {
  const v = fieldMappingView(PAYLOAD, newCol());
  assert.equal(v.tables[0].flagged[0].mode, 'new');
  assert.equal(v.tally.find((t) => t.label === 'New columns').value, 1);
});

test('choosing it sends what the gate needs to emit DDL', () => {
  const row = fieldMappingBody(PAYLOAD, newCol({ [K + '#type']: 'DATE' })).flagged.Assets[0];
  assert.equal(row.action, 'override');
  assert.equal(row.is_new_column, true);
  assert.equal(row.data_type, 'DATE');
  assert.equal(row.nullable, true);
});

test('the sentinel cannot be mistaken for a column name', () => {
  // The mode is its own key, so no column name ever has to double as "make a new one".
  const row = fieldMappingBody(PAYLOAD, newCol({ [K + '#to']: 'barcode' })).flagged.Assets[0];
  assert.equal(row.target_field, 'asset_ref', 'a new column is named by its own box, not by the existing-column pick');
  const existing = fieldMappingBody(PAYLOAD, { [K]: 'override', [K + '#to']: 'barcode' }).flagged.Assets[0];
  assert.deepEqual(existing, { action: 'override', source_field: 'Asset Ref', target_field: 'barcode', rationale: null });
});

test('the name is normalised the way the writer normalises it', () => {
  assert.equal(safeColumn('Asset Ref'), 'asset_ref');
  assert.equal(safeColumn('vendor-code'), 'vendor_code');
  assert.equal(safeColumn('  Legacy ID  '), 'legacy_id');
  assert.equal(safeColumn('asset_ref'), 'asset_ref');
});

test('a name that is not an identifier is refused, not repaired into something else', () => {
  assert.equal(safeColumn(''), '');
  assert.equal(safeColumn(null), '');
  assert.equal(safeColumn(undefined), '');
  assert.equal(safeColumn('1col'), '', 'PostgreSQL identifiers cannot start with a digit');
  assert.equal(safeColumn('a'.repeat(70)), '', 'past the 63-character limit');
  // And the gate is not answered with one: human_review_node would drop every decision.
  assert.deepEqual(fieldMappingProblems(PAYLOAD, newCol({ [K + '#name']: '1col' })), ['Assets.Asset Ref: new column name required']);
});

test('a name cannot carry SQL out of the box', () => {
  const out = safeColumn('x"; DROP TABLE plenum_cafm.assets; --');
  assert.ok(!/[";'()\- ]/.test(out), 'nothing that could end a statement survives: ' + out);
  assert.ok(/^[a-z_][a-z0-9_]*$/.test(out) || out === '');
  const row = fieldMappingBody(PAYLOAD, newCol({ [K + '#name']: 'x"; DROP TABLE plenum_cafm.assets; --' })).flagged.Assets[0];
  assert.ok(/^[a-z_][a-z0-9_]*$/.test(row.target_field) || row.target_field === '');
});

test('the row tells the reviewer the name that will actually be created', () => {
  const r = fieldMappingView(PAYLOAD, newCol({ [K + '#name']: 'Asset Ref' })).tables[0].flagged[0];
  assert.equal(r.nameSafe, 'asset_ref');
  assert.equal(r.preview, 'Creates plenum_cafm.assets.asset_ref on submit.');
});

test('the offered types are all types the service will accept', () => {
  // Mirrors _ALLOWED_TYPES and _TYPE_RE in schema_write_node.py; a type outside them is dropped.
  const allowed = /^(varchar|text|integer|bigint|numeric|decimal|boolean|date|timestamp|timestamptz|uuid|jsonb)(\(\d+(,\d+)?\))?$/;
  FM_DATA_TYPES.forEach((t) => assert.ok(allowed.test(t.toLowerCase()), t + ' is not a type the writer accepts'));
  assert.ok(FM_DATA_TYPES.length >= 8);
});

// ── Override can also send the field to a table of its own ────────────────────────

test('the decision can name its own table', () => {
  const row = fieldMappingBody(PAYLOAD, newCol({ 'ft:Assets': { table: 'Asset Extras', isNew: true } })).flagged.Assets[0];
  assert.equal(row.target_table, 'asset_extras');
  assert.equal(row.is_new_table, true);
  assert.equal(row.new_table_pk, 'id');
});

test('a table already on the schema is added to, not created', () => {
  // canonical_columns_by_table is the set of tables that exist. Claiming "new" for one of them
  // would emit CREATE TABLE IF NOT EXISTS, which succeeds and does nothing — and the column
  // would never arrive.
  const row = fieldMappingBody(PAYLOAD, newCol({ 'ft:Assets': { table: 'sites', isNew: false } })).flagged.Assets[0];
  assert.equal(row.target_table, 'sites');
  assert.equal(row.is_new_table, false);
});

test('the table name is normalised the same way as the column', () => {
  assert.equal(safeColumn('Vendor Extras'), 'vendor_extras');
  assert.equal(safeColumn('9bad'), '', 'a table cannot start with a digit either');
});

test('the row says whether it will create a table or add to one', () => {
  const onNew = fieldMappingView(PAYLOAD, newCol({ 'ft:Assets': { table: 'asset_extras', isNew: true } })).tables[0];
  assert.equal(onNew.canonicalIsNew, true);
  assert.equal(onNew.flagged[0].preview, 'Creates plenum_cafm.asset_extras.asset_ref on submit.');
  const routed = fieldMappingView(PAYLOAD, newCol()).tables[0];
  assert.equal(routed.canonical, 'assets', 'with nothing chosen, the column goes where the sheet was routed');
});
