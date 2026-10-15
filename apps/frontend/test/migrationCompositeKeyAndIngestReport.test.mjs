// migrationCompositeKeyAndIngestReport — the primary-key gate and the ingest report, as CAFM Web
// shows them.
//
// The PK gate offered one column per table. A table whose rows are only unique on several
// columns together — Asset_Readings on asset_code + reading_type + recorded_at — was sent as
// its FIRST column alone, which is a key that does not hold. The service has always taken a
// list (pk_confirmation.normalize_pk_overrides: {table: "col"} or {table: [cols]}, an empty
// list being a surrogate), and CAFM Web sends one. These tests hold the page to the same.
//
// The ingest report is Node 1's step pause: the three totals and the Null / NaN scan. The
// pause is continued within a second, so the report is kept once seen and stays readable
// above the gates that follow it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { defaultGateBody, pkRows, ingestReport } from '../src/logic/migration.js';

const col = (column, uniqueness, null_rate, qualifies) => ({ column, uniqueness, null_rate, qualifies });

const PK = {
  gate: 'pk_approval',
  pk_confirmation: {
    Sites: { kind: 'natural', detected_pk: ['site_id'], columns: [col('site_id', 1, 0, true), col('name', 0.9, 0, false)] },
    Asset_Readings: { kind: 'composite', detected_pk: ['asset_code', 'reading_type', 'recorded_at'],
      columns: [col('asset_code', 0.2, 0, false), col('reading_type', 0.1, 0, false), col('recorded_at', 0.6, 0.05, false), col('value', 0.8, 0, false)] },
    Notes: { kind: 'surrogate', surrogate: true, detected_pk: ['_udr_id'], columns: [col('body', 0.4, 0.2, false)] },
    Vendors: { kind: 'natural', detected_pk: ['vendor_code'], columns: [col('vendor_code', 1, 0, true), col('vendor_name', 1, 0, true)] },
    Vendor_Contracts: { kind: 'natural', detected_pk: ['vendor_code'], columns: [col('vendor_code', 1, 0, true)] }
  },
  table_resolution: {
    duplicate_tables: { groups: [{ tables: ['Vendor_Contracts', 'Vendors'], count: 2, label: 'Vendor', shared_columns: ['vendor_code'] }] }
  }
};

test('a composite key is sent whole, a surrogate as an empty list, every other key as detected', () => {
  assert.deepEqual(defaultGateBody('pk_approval', PK, {}).pk_overrides, {
    Sites: ['site_id'],
    Asset_Readings: ['asset_code', 'reading_type', 'recorded_at'],
    Notes: [],
    Vendors: ['vendor_code'],
    Vendor_Contracts: ['vendor_code']
  });
});

test('the reader\'s key is sent as they built it', () => {
  const body = defaultGateBody('pk_approval', PK, { Sites: ['site_id', 'name'], Asset_Readings: [] }).pk_overrides;
  assert.deepEqual(body.Sites, ['site_id', 'name']);
  assert.deepEqual(body.Asset_Readings, [], 'use surrogate');
  // A decision held as a bare column still means that column.
  assert.deepEqual(defaultGateBody('pk_approval', PK, { Sites: 'name' }).pk_overrides.Sites, ['name']);
  assert.deepEqual(defaultGateBody('pk_approval', PK, { Sites: '__surrogate__' }).pk_overrides.Sites, []);
});

test('a duplicate group is one row, labelled and counted, carrying every member', () => {
  const rows = pkRows(PK, {});
  assert.deepEqual(rows.map((r) => r.label), ['Sites', 'Asset_Readings', 'Notes', 'Vendor']);
  const v = rows.find((r) => r.label === 'Vendor');
  assert.deepEqual(v.members, ['Vendor_Contracts', 'Vendors']);
  assert.equal(v.isGroup, true);
});

test('each row says its kind from the key chosen, not the key detected', () => {
  const by = (d) => Object.fromEntries(pkRows(PK, d).map((r) => [r.label, r.kind]));
  assert.deepEqual(by({}), { Sites: 'natural', Asset_Readings: 'composite', Notes: 'surrogate', Vendor: 'natural' });
  assert.equal(by({ Sites: ['site_id', 'name'] }).Sites, 'composite');
  assert.equal(by({ Sites: [] }).Sites, 'surrogate');
});

test('uniqueness and null-rate are the weakest column of the key, and a key that cannot hold says so', () => {
  const ar = pkRows(PK, {}).find((r) => r.label === 'Asset_Readings');
  assert.equal(ar.uniqueness, '0.10');
  assert.equal(ar.nullRate, '0.05');
  assert.equal(ar.invalid, true);
  const sites = pkRows(PK, {}).find((r) => r.label === 'Sites');
  assert.equal(sites.uniqueness, '1.00');
  assert.equal(sites.nullRate, '0.00');
  assert.equal(sites.invalid, false);
  const notes = pkRows(PK, {}).find((r) => r.label === 'Notes');
  assert.equal(notes.invalid, false, 'a surrogate key always holds');
  assert.deepEqual(notes.chips, [], 'the synthetic _udr_id is not offered as a column');
});

test('chips mark the columns that qualify; the picker offers only the columns not already in the key', () => {
  const sites = pkRows(PK, {}).find((r) => r.label === 'Sites');
  assert.deepEqual(sites.chips, [{ column: 'site_id', qualifies: true }]);
  assert.deepEqual(sites.available, [{ column: 'name', qualifies: false }]);
  assert.equal(sites.changed, false);
  assert.equal(pkRows(PK, { Sites: ['name'] }).find((r) => r.label === 'Sites').changed, true);
});

test('the ingest report reads the step pause the way the service writes it', () => {
  const r = ingestReport({
    node: 1, label: 'Ingest & Configure', rows: 289606, columns: 181, format: 'excel',
    nan_report: {
      total_nan_cells: 718, total_rows_with_nan: 214, columns_with_nan: 29,
      tables: {
        Sites: { nan_cells: 0, rows_with_nan: 0, columns: {} },
        Assets: { nan_cells: 60, rows_with_nan: 60, columns: { warranty_expiry: 60 }, sample_rows: [{ a: 1 }] }
      }
    }
  });
  assert.equal(r.label, 'Ingest & Configure');
  assert.deepEqual(r.tiles, [
    { label: 'Total rows', value: '289,606' }, { label: 'Total columns', value: '181' }, { label: 'Format', value: 'excel' }
  ]);
  assert.equal(r.nan.clean, false);
  assert.equal(r.nan.summary, '718 values · 214 rows · 29 columns');
  assert.deepEqual(r.nan.tables, [
    { name: 'Sites', clean: true, badge: 'no null/NaN (0)', columns: [] },
    { name: 'Assets', clean: false, badge: '60 rows with null/NaN', columns: [{ name: 'warranty_expiry', count: 60 }] }
  ]);
});

test('the ingest report reads Node 1\'s own output too, and a report the poll left out is no report', () => {
  const r = ingestReport({ row_count: 50, column_count: 29, detected_format: 'csv', nan_report: { total_nan_cells: 0, tables: {} } });
  assert.deepEqual(r.tiles.map((t) => t.value), ['50', '29', 'csv']);
  assert.equal(r.nan.clean, true);
  assert.equal(ingestReport({ _omitted: true, _bytes: 90000 }), null);
  assert.equal(ingestReport(null), null);
  assert.equal(ingestReport({ node: 2, label: 'Deterministic mapping' }), null, 'a pause with none of the totals is not an ingest report');
});

test('a missing total reads as a dash, never as a fake zero', () => {
  const r = ingestReport({ rows: 10, format: 'excel' });
  assert.deepEqual(r.tiles.map((t) => t.value), ['10', '—', 'excel']);
  assert.equal(r.nan, null, 'no scan, no panel');
});
