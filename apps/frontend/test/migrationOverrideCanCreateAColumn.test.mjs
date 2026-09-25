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
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { safeColumn } from '../src/logic/migration.js';

const src = fs.readFileSync(new URL('../src/logic/migration.js', import.meta.url), 'utf8');

test('the picker offers a new column', () => {
  assert.ok(src.includes("{ value: '__new__', label: 'New column…' }"),
    'the override list must end with an option that is not an existing column');
});

test('choosing it sends what the gate needs to emit DDL', () => {
  for (const key of ['is_new_column: true', 'data_type:', 'nullable: true']) {
    assert.ok(src.includes(key), `the decision must carry ${key}`);
  }
});

test('the sentinel cannot be mistaken for a column name', () => {
  // A real column can never be called __new__: safeColumn keeps the underscores, but no
  // plenum_cafm table has one, and the branch that reads it runs before the generic override.
  const newBranch = src.indexOf("if (v === NEWCOL)");
  const genericBranch = src.indexOf("v !== it.suggested_target) return { action: 'override'");
  assert.ok(newBranch > -1 && genericBranch > -1);
  assert.ok(newBranch < genericBranch,
    'the new-column branch must be tested first, or the sentinel is sent as a target field');
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
});

test('a name cannot carry SQL out of the box', () => {
  const out = safeColumn('x"; DROP TABLE plenum_cafm.assets; --');
  assert.ok(!/[";'()\- ]/.test(out), 'nothing that could end a statement survives: ' + out);
  assert.ok(/^[a-z_][a-z0-9_]*$/.test(out) || out === '');
});

test('the row tells the reviewer the name that will actually be created', () => {
  // Typing "Asset Ref" creates asset_ref. Showing the typed text would show a name that is
  // not the one made, and the reviewer approves a column they will not find afterwards.
  assert.ok(src.includes('newNameSafe: safeColumn('));
  // Which table it lands on is stated too. That follows the typed table where there is one and
  // falls back to the routed destination, so the line always names the table actually written.
  assert.ok(src.includes('newTarget: safeColumn('));
});

test('the offered types are all types the service will accept', () => {
  const m = src.match(/newTypes: \[([^\]]+)\]/);
  assert.ok(m, 'the row must offer a fixed list, not free text');
  const offered = m[1].match(/'([^']+)'/g).map((t) => t.replace(/'/g, ''));
  // Mirrors _ALLOWED_TYPES in schema_write_node.py; a type outside it is dropped from the DDL.
  const allowed = /^(VARCHAR\(\d+\)|TEXT|INTEGER|BIGINT|NUMERIC\(\d+,\d+\)|BOOLEAN|DATE|TIMESTAMPTZ|UUID|JSONB)$/;
  offered.forEach((t) => assert.ok(allowed.test(t), t + ' is not a type the writer accepts'));
  assert.ok(offered.length >= 8);
});

// ── Override can also send the field to a table of its own ────────────────────────

test('the decision can name its own table', () => {
  assert.ok(src.includes('target_table: tbl || null'),
    'blank must mean the routed destination, which is what an override always meant');
  assert.ok(src.includes('is_new_table:'), 'and say whether that table has to be created');
  assert.ok(src.includes("new_table_pk: 'id'"));
});

test('a table already on the schema is added to, not created', () => {
  // canonical_columns_by_table is the set of tables that exist. Claiming "new" for one of them
  // would emit CREATE TABLE IF NOT EXISTS, which succeeds and does nothing — and the column
  // would never arrive.
  assert.ok(src.includes("hasOwnProperty.call(known, tbl)"),
    'is_new_table must be decided against the tables that exist, not asserted');
});

test('the table name is normalised the same way as the column', () => {
  assert.equal(safeColumn('Vendor Extras'), 'vendor_extras');
  assert.equal(safeColumn('9bad'), '', 'a table cannot start with a digit either');
});

test('the row says whether it will create a table or add to one', () => {
  assert.ok(src.includes('newTableIsNew:'));
  assert.ok(src.includes("newTarget: safeColumn(dec[k + '#table'] || '') || (routing[table] || table)"),
    'the stated destination must follow the typed table, not the routed one');
  assert.ok(src.includes('routedTable: (routing[table] || table)'),
    'and the box must say where a blank sends it');
});
