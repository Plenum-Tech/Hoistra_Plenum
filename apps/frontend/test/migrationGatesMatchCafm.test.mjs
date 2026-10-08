// migrationGatesMatchCafm — the gates after the primary key, as CAFM Web shows and answers them.
//
// Each gate's rows and answer are pure functions over the gate payload (logic/migrationGates.js).
// The payload shapes are the ones svc-ai-schema-mapper builds: grouping_review_node (classification),
// column_mapping_review_node (column mapping), pre_semantic_review_node (both passes),
// human_review_node (field mapping), verify_hierarchy_node (hierarchy), write_node (the write).
// Where these tests expect something CAFM does NOT send, the reason is the service: the comment
// on that gate in migrationGates.js says what CAFM's answer would have done.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  uniqueTablesView, classificationView, classificationBody, columnMappingView, columnMappingBody,
  tableRoutingView, tableRoutingBody, columnMatchingView, columnMatchingBody, toSnakeCase, inferDataType,
  fieldMappingView, fieldMappingBody, fieldMappingProblems,
  hierarchyView, hierarchyBody, hierarchyCycles, hierarchyExport, finalView, relKey
} from '../src/logic/migrationGates.js';

// ── unique tables ───────────────────────────────────────────────────────────────────

const UNIQUE = {
  gate: 'unique_table_approval',
  table_resolution: {
    metadata_cards: [
      { table: 'Sites', primary_key: ['site_id'], column_count: 4, samples: [{ column: 'site_id', values: ['S1', 'S2', 'S3', 'S4'] }] },
      { table: 'Vendors', primary_key: ['vendor_code'], column_count: 6, samples: [] },
      { table: 'Vendor_Contracts', primary_key: ['vendor_code'], column_count: 6, samples: [] }
    ],
    pk_detection: [
      { table: 'Sites', kind: 'natural', primary_key: ['site_id'], uniqueness: 1, null_rate: 0, tie_break: 'id-named' },
      { table: 'Vendors', kind: 'natural', primary_key: ['vendor_code'], uniqueness: 1, null_rate: 0, tie_break: '' },
      { table: 'Vendor_Contracts', kind: 'natural', primary_key: ['vendor_code'], uniqueness: 1, null_rate: 0, tie_break: '' }
    ],
    duplicate_tables: { groups: [{ tables: ['Vendors', 'Vendor_Contracts'], count: 2, label: 'Vendor', shared_columns: ['vendor_code', 'vendor_name'], similarity: 0.83 }], duplicate_count: 2, checked: 3 },
    merged_columns: [{ table: 'Sites', kept: 'site_id', dropped: ['site_ref'], members: ['site_id', 'site_ref'], match_pct: 100, row_count: 4 }],
    pairwise: { highest_pair: { table_a: 'Vendors', table_b: 'Vendor_Contracts', metadata_similarity: 0.83, name_similarity: 0.6 }, verdict: 'Highest pair: Vendors ↔ Vendor_Contracts — merged.' },
    counts: { tables: 3 },
    final_decisions: [{ source: 'Sites', destination: 'sites', method: 'exact', confidence: 1 }]
  }
};

test('unique tables: a duplicate group is one card with its members, never printed as JSON', () => {
  const v = uniqueTablesView(UNIQUE);
  assert.deepEqual(v.unique.map((c) => c.label), ['Sites', 'Vendor']);
  assert.deepEqual(v.unique[1].group, { count: 2, members: ['Vendors', 'Vendor_Contracts'] });
  assert.deepEqual(v.dupGroups[0].tables, ['Vendors', 'Vendor_Contracts']);
  assert.equal(v.dupGroups[0].overlap, '83% column overlap');
  assert.ok(!JSON.stringify(v).includes('{\\"tables'), 'no group is stringified');
  assert.equal(v.unique[0].samples[0].values, 'S1, S2, S3', 'three sample values');
  assert.equal(v.mergeNote, 'Vendor (×2) merged from similar columns — shown as 2 unique tables instead of 3.');
  assert.equal(v.uniqueMeta, '2 unique · 3 source tables · 1 duplicate group');
});

test('unique tables: the confirmed keys read back one row per group, and the verdict is not said twice', () => {
  const v = uniqueTablesView(UNIQUE);
  assert.deepEqual(v.pkRows.map((r) => [r.label, r.pk]), [['Sites', 'site_id'], ['Vendor', 'vendor_code']]);
  assert.equal(v.pairNote.pair, null, 'the verdict already names the highest pair');
  assert.equal(v.merges[0].kept, 'site_id');
});

// ── classification ──────────────────────────────────────────────────────────────────

const CLASS = {
  gate: 'classification_approval',
  classification: [
    { group_id: 'G1', verdict: 'Foreign Key relationship', canonical_name: 'asset_code', members: ['workorders.asset_id', 'assets.asset_code'], has_pk: 'assets.asset_code', ri: 0.97 },
    { group_id: 'G3', verdict: 'Shared Attribute', canonical_name: 'trade', members: ['vendors.trade', 'resources.trade'] },
    { group_id: 'PK-sites', verdict: 'Primary Key group', canonical_name: 'sites.site_id', members: ['sites.site_id'] }
  ],
  shared_attribute_tables: [{ from_group: 'G3', table_name: 'trade', pk_column: 'trade', distinct_count: 5, sample_values: ['Electrical'] }]
};

test('classification: every key anchor is listed, the FK reads from the foreign side to the key', () => {
  const v = classificationView(CLASS, {});
  assert.deepEqual(v.pks, ['assets.asset_code', 'sites.site_id']);
  assert.equal(v.fk[0].fkSide, 'workorders.asset_id');
  assert.equal(v.fk[0].pkSide, 'assets.asset_code');
  assert.equal(v.fk[0].ri, 'RI 97%');
  assert.equal(v.counts, '2 PK · 1 FK · 1 Shared');
  assert.equal(v.submitLabel, 'Confirm links');
  assert.deepEqual(v.shared[0].lookup, { table: 'trade', pk: 'trade', values: 5 });
});

test('classification: a shared group with no key member cannot be made a foreign key', () => {
  const v = classificationView(CLASS, { G3: 'fk' });
  assert.equal(v.shared[0].fkAllowed, false);
  assert.equal(v.shared[0].chosen, 'shared');
  assert.deepEqual(classificationBody(CLASS, { G3: 'fk' }), { rejected_groups: [], verdict_overrides: {} });
});

test('classification: demote an FK and exclude a group — the button says what will be applied', () => {
  const dec = { G1: 'shared', G3: 'exclude' };
  assert.deepEqual(classificationBody(CLASS, dec), { rejected_groups: ['G3'], verdict_overrides: { G1: 'shared' } });
  const v = classificationView(CLASS, dec);
  assert.equal(v.submitLabel, 'Apply 2 changes & continue');
  assert.equal(v.fk[0].note, 'Re-classified: FK will NOT be enforced — a lookup table is created instead.');
  assert.equal(v.shared[0].lookup, null, 'an excluded group creates no lookup table');
});

// ── column mapping ──────────────────────────────────────────────────────────────────

const CM = {
  gate: 'column_mapping_approval',
  dest_mapping: [
    { source: 'assets.serial', source_column: 'serial', source_tables: ['Assets', 'AssetsCopy'], dest_table: 'assets', matched_column: 'serial_no', outcome: 'suggested — confirm', classification: 'Shared', format: 'text', samples: ['A1', 'A2'] },
    { source: 'assets.colour', source_column: 'colour', source_tables: ['Assets', 'AssetsCopy'], dest_table: 'assets', matched_column: null, outcome: 'new column' },
    { source: 'sites.site_id', source_column: 'site_id', source_tables: ['Sites'], dest_table: 'sites', matched_column: 'site_id', outcome: 'auto-resolved', is_primary_key: true }
  ],
  dest_columns_by_table: { assets: ['serial_no', 'serial_number', 'colour_code'], sites: ['site_id'] }
};

test('column mapping: rows are grouped by source table, and a match reads Keep', () => {
  const v = columnMappingView(CM, {});
  assert.deepEqual(v.tables.map((t) => [t.label, t.sources, t.dest, t.colsLabel]), [['assets', ['Assets', 'AssetsCopy'], 'assets', '2 columns'], ['sites', ['Sites'], 'sites', '1 column']]);
  assert.equal(v.tables[0].rows[1].outcome, 'new column · no existing match');
  assert.equal(v.tables[0].rows[0].kind, 'keep');
  assert.deepEqual(columnMappingBody(CM, {}), { overrides: {} });
  assert.equal(v.submitLabel, 'Confirm columns');
});

test('column mapping: an override reaches every sheet routed to the row, and New column is sent even with no match', () => {
  const dec = { 'cm:assets.serial': 'serial_number', 'cm:assets.colour': '__new__' };
  assert.deepEqual(columnMappingBody(CM, dec), {
    overrides: { Assets: { serial: 'serial_number', colour: '__new__' }, AssetsCopy: { serial: 'serial_number', colour: '__new__' } }
  });
  assert.equal(columnMappingView(CM, dec).submitLabel, 'Apply 2 changes & continue');
  // Re-targeting to the column it already matched is no override.
  assert.deepEqual(columnMappingBody(CM, { 'cm:assets.serial': 'serial_no' }), { overrides: {} });
});

// ── pre-semantic, pass 1: table routing ─────────────────────────────────────────────

const ROUTING = {
  gate: 'pre_semantic', locked_phase: 'tables', gate_step: 'table_routing',
  existing_canonical_tables: ['sites', 'assets', 'work_orders', 'vendors'],
  suggested_target_by_table: { Sites: 'sites', Assets: 'assets', Findings: 'findings', Vendors: 'vendors', Vendor_Contracts: 'vendors' },
  table_match_confidence_by_table: { Sites: 1, Assets: 0.82 },
  table_routing_suggestion_by_table: { Findings: { action: 'create', suggested_new_name: 'Inspection Findings' } },
  table_match_candidates_by_table: { Assets: [{ table: 'assets', pct: 0.82 }, { table: 'work_orders', pct: 0.4 }] },
  table_resolution: { duplicate_tables: { groups: [{ tables: ['Vendors', 'Vendor_Contracts'], count: 2, label: 'Vendor' }] } }
};

test('routing: a sheet with no existing table becomes a new one by default, named in snake case', () => {
  const v = tableRoutingView(ROUTING, {});
  const f = v.rows.find((r) => r.key === 'Findings');
  assert.deepEqual([f.target, f.isNew, f.match], ['inspection_findings', true, 'new']);
  assert.equal(v.rows.find((r) => r.key === 'Sites').match, 'exact');
  assert.equal(v.rows.find((r) => r.key === 'Assets').conf, '82% match');
  assert.deepEqual(v.rows.map((r) => r.label), ['Sites', 'Assets', 'Findings', 'Vendor'], 'the duplicate group is one row');
  assert.equal(v.complete, true);
});

test('routing: only new and changed sheets are sent, and a group moves together', () => {
  assert.deepEqual(tableRoutingBody(ROUTING, {}), {
    decisions: {}, table_overrides: { Findings: { target_table: 'inspection_findings', is_new_table: true } }
  });
  const body = tableRoutingBody(ROUTING, { 'rt:Vendors': { target: 'work_orders', isNew: false }, 'rt:Sites': { target: 'Site Register', isNew: true } });
  assert.deepEqual(body.table_overrides.Vendors, { target_table: 'work_orders', is_new_table: false });
  assert.deepEqual(body.table_overrides.Vendor_Contracts, { target_table: 'work_orders', is_new_table: false });
  assert.deepEqual(body.table_overrides.Sites, { target_table: 'site_register', is_new_table: true });
  assert.ok(!('Assets' in body.table_overrides), 'an unchanged sheet is not echoed — the service would read it as rerouted');
  const mine = tableRoutingView(ROUTING, { 'rt:Assets': { target: 'work_orders', isNew: false } }).rows.find((r) => r.key === 'Assets');
  assert.equal(mine.match, 'yours', 'a table the reader picked is not a guess to check');
});

test('routing: a sheet left without a target stops the confirm', () => {
  assert.equal(tableRoutingView(ROUTING, { 'rt:Assets': { target: '', isNew: false } }).complete, false);
  assert.equal(tableRoutingView(ROUTING, { 'rt:Assets': { target: 'work_orders', isNew: false } }).rows.find((r) => r.key === 'Assets').conf, '',
    'the match % belongs to the proposed table, not one the reader chose');
});

test('snake case and inferred types follow CAFM', () => {
  assert.equal(toSnakeCase('Distance (km)'), 'distance_km');
  assert.equal(toSnakeCase('WorkOrderID'), 'work_order_id');
  assert.equal(toSnakeCase('***'), 'col');
  assert.equal(inferDataType('created_at'), 'TIMESTAMP');
  assert.equal(inferDataType('install_date'), 'DATE');
  assert.equal(inferDataType('is_active'), 'BOOLEAN');
  assert.equal(inferDataType('parts_cost'), 'NUMERIC');
  assert.equal(inferDataType('asset_id'), 'INTEGER');
  assert.equal(inferDataType('description'), 'VARCHAR(255)');
});

// ── pre-semantic, pass 2: column matching ───────────────────────────────────────────

const COLUMNS = {
  gate: 'pre_semantic', locked_phase: 'columns', gate_step: 'column_matching',
  existing_canonical_tables: ['assets', 'sites'],
  suggested_target_by_table: { Assets: 'assets', Findings: 'inspection_findings' },
  canonical_columns_by_table: { assets: ['asset_code', 'site_id', 'serial_number', 'barcode'] },
  review_items_by_table: {
    Assets: [
      { source_field: 'asset_code', target_field: 'asset_code', confidence: 0.99, tier: 'T1_exact', is_primary_key: true },
      { source_field: 'site_ref', target_field: 'site_id', confidence: 0.8, tier: 'T1_alias', candidates: [{ target_field: 'site_id', confidence: 0.8, is_primary: true }] }
    ],
    Findings: [{ source_field: 'Finding Date', target_field: 'raised_at', confidence: 0.7, tier: 'T1_alias' }]
  },
  unresolved_suggestion_by_table: { Assets: { serial: { target_field: 'serial_number', confidence: 0.7, candidates: [{ target_field: 'serial_number', confidence: 0.7 }] } } }
};

test('column matching: an existing table sends decisions only, a rename only when changed, and no table_overrides', () => {
  assert.deepEqual(columnMatchingBody(COLUMNS, {}, null), {
    decisions: {
      Assets: [{ source_field: 'asset_code', decision: 'approve' }, { source_field: 'site_ref', decision: 'approve' }],
      Findings: [{ source_field: 'Finding Date', decision: 'approve', target_field: 'finding_date', data_type: 'DATE' }]
    }
  });
  const body = columnMatchingBody(COLUMNS, { 'ps:Assets.site_ref': 'semantic', 'ps:Assets.asset_code#to': 'barcode' }, null);
  assert.deepEqual(body.decisions.Assets, [{ source_field: 'asset_code', decision: 'approve', target_field: 'barcode' }, { source_field: 'site_ref', decision: 'semantic' }]);
});

test('column matching: a new table names its columns after the source, not a match found on another table', () => {
  const v = columnMatchingView(COLUMNS, {}, null);
  const f = v.tables.find((t) => t.key === 'Findings');
  assert.equal(f.isNew, true);
  assert.equal(f.rows[0].to, 'finding_date');
  assert.equal(f.rows[0].dest, 'new');
});

test('column matching: an unmatched field goes to semantic unless a free column is assigned', () => {
  const stash = { Assets: ['serial', 'asset_code'] };
  const v = columnMatchingView(COLUMNS, {}, stash);
  const a = v.tables.find((t) => t.key === 'Assets');
  assert.deepEqual(a.unresolved.map((u) => u.field), ['serial'], 'a field already under review is not listed again');
  assert.equal(a.unresolved[0].defaultAssign, 'serial_number');
  assert.deepEqual(a.unresolved[0].leftover, ['serial_number', 'barcode'], 'columns already taken are not offered');
  assert.equal(v.stats.find((s) => s.label === 'Auto → Semantic').value, 1);
  assert.ok(!columnMatchingBody(COLUMNS, {}, stash).decisions.Assets.some((d) => d.source_field === 'serial'));
  const assigned = columnMatchingBody(COLUMNS, { 'pu:Assets.serial': 'serial_number' }, stash);
  assert.deepEqual(assigned.decisions.Assets[2], { source_field: 'serial', decision: 'approve', target_field: 'serial_number' });
});

test('column matching: two fields into one column are flagged', () => {
  const v = columnMatchingView(COLUMNS, { 'ps:Assets.site_ref#to': 'asset_code' }, null);
  assert.deepEqual(v.tables[0].rows.map((r) => r.dupTarget), [true, true]);
  assert.equal(v.tables[0].rows[1].modified, "User-modified — system suggested 'site_id'");
});

// ── field mapping ───────────────────────────────────────────────────────────────────

const FM = {
  total_flagged: 2, total_unmappable: 1, overall_confidence: 0.74,
  confidence_alert: { message: 'Overall mapping confidence is 74%, below 0.80 threshold. Please review all flagged mappings.' },
  review_items_by_table: {
    Assets: [
      { source_field: 'site_ref', suggested_target: 'site_id', confidence: 0.91, rationale: 'values match', suggestions: [{ target: 'location_id', confidence: 0.62 }, { target: 'site_id', confidence: 0.91 }], score_breakdown: { semantic: 0.9, keyword: 0.5 } },
      { source_field: 'Distance (km)', suggested_target: 'distance', confidence: 0.55, suggestions: [] }
    ]
  },
  unmappable_items_by_table: {
    Assets: [{ source_field: 'Mgr Email', suggested_action: { action: 'custom', target_table: 'assets', custom_column_name: 'mgr_email', data_type: 'VARCHAR(255)' } }]
  },
  table_routing: { Assets: 'assets' },
  existing_canonical_tables: ['assets', 'sites'],
  canonical_columns_by_table: { assets: ['site_id', 'location_id', 'distance', 'manager_email'] },
  column_canonical: { 'Assets.Mgr Email': 'manager_email' },
  combined_mapping_view: { auto_accepted: [{ source_table: 'Assets', source_field: 'asset_code', target_table: 'assets', target_field: 'asset_code', confidence: 1 }] }
};

test('field mapping: the AI alternatives are read as the service sends them', () => {
  const v = fieldMappingView(FM, { 'fm:Assets.site_ref': 'override' });
  assert.deepEqual(v.tables[0].flagged[0].scores.map((s) => [s.field, s.score]), [['site_id', '91%'], ['location_id', '62%']]);
  assert.equal(v.counters[0].value, 1, 'auto accepted');
  assert.equal(v.accepted[0].dest, 'assets.asset_code');
  assert.deepEqual(v.tables[0].flagged[0].breakdown.map((b) => b.label), ['Semantic', 'Keyword']);
});

test('field mapping: accept, reject and override send what the gate reads', () => {
  const body = fieldMappingBody(FM, {
    'fm:Assets.site_ref': 'override', 'fm:Assets.site_ref#to': 'location_id',
    'fm:Assets.Distance (km)': 'reject'
  });
  assert.deepEqual(body.flagged.Assets, [
    { action: 'override', source_field: 'site_ref', target_field: 'location_id', rationale: null },
    { action: 'reject', source_field: 'Distance (km)', target_field: null, rationale: null }
  ]);
  assert.deepEqual(fieldMappingBody(FM, {}).flagged.Assets[0], { action: 'accept', source_field: 'site_ref', target_field: 'site_id', rationale: null });
});

test('field mapping: override to a new column is one override the service can build a column from', () => {
  const body = fieldMappingBody(FM, { 'fm:Assets.Distance (km)': 'override', 'fm:Assets.Distance (km)#mode': 'new', 'fm:Assets.Distance (km)#type': 'NUMERIC' });
  assert.deepEqual(body.flagged.Assets[1], {
    action: 'override', source_field: 'Distance (km)', target_field: 'distance_km', is_new_column: true,
    target_table: 'assets', is_new_table: false, new_table_pk: 'id', data_type: 'NUMERIC', nullable: true,
    rationale: 'New column created at the review gate'
  });
  assert.ok(!body.unmapped.Assets.some((u) => u.source_field === 'Distance (km)'), 'not also sent as a custom unmapped field');
});

test('field mapping: an unmappable field becomes the name it is shown under', () => {
  const v = fieldMappingView(FM, {});
  assert.equal(v.tables[0].unmapped[0].display, 'manager_email');
  assert.equal(v.tables[0].unmapped[0].name, 'manager_email');
  assert.match(v.tables[0].unmapped[0].collision, /already exists on assets/);
  assert.deepEqual(fieldMappingBody(FM, {}).unmapped.Assets[0], {
    action: 'custom', source_field: 'Mgr Email', target_table: 'assets', custom_column_name: 'manager_email',
    data_type: 'VARCHAR(255)', nullable: true, is_new_table: false, new_table_pk: null
  });
  assert.deepEqual(fieldMappingBody(FM, { 'fu:Assets.Mgr Email': 'skip' }).unmapped.Assets[0], {
    action: 'skip', source_field: 'Mgr Email', target_table: null, custom_column_name: null, data_type: null
  });
});

test('field mapping: an answer the service would refuse is named before it is sent', () => {
  assert.deepEqual(fieldMappingProblems(FM, {}), []);
  assert.deepEqual(fieldMappingProblems(FM, { 'fm:Assets.site_ref': 'override', 'fm:Assets.site_ref#to': '' }), ['Assets.site_ref: override target required']);
  assert.deepEqual(fieldMappingProblems(FM, { 'fu:Assets.Mgr Email#name': '1bad' }), ['Assets.Mgr Email: new column name required']);
});

test('field mapping: the tallies and the submit label count the decisions', () => {
  const v = fieldMappingView(FM, { 'fm:Assets.Distance (km)': 'reject' });
  assert.deepEqual(v.tally.map((t) => t.value), [1, 1, 0, 1]);
  assert.equal(v.submitLabel, 'Submit decisions (1 accepted · 0 changed · 1 rejected)');
});

// ── hierarchy ───────────────────────────────────────────────────────────────────────

const HIER = {
  total_hierarchies: 3, total_cycles: 0, total_orphans: 2, hierarchy_tree: '└── sites\n    └── assets',
  review_items: [
    { type: 'hierarchy', id: 'hierarchy_0', source_table: 'assets', source_column: 'site_id', target_table: 'sites', target_column: 'site_id', relationship_type: 'CONTAINMENT', confidence: 0.95, data_match_rate: 1, reasoning: 'every value resolves', system_default: false, mapping_note: false, read_only: false },
    { type: 'hierarchy', id: 'hierarchy_1', source_table: 'locations', source_column: 'parent_id', target_table: 'locations', target_column: 'id', relationship_type: 'REFERENCE', confidence: 0.9, read_only: false },
    { type: 'system_default', id: 'hierarchy_2', source_table: 'assets', source_column: 'building_id', target_table: 'buildings', target_column: 'id', relationship_type: 'CONTAINMENT', system_default: true, read_only: true, reasoning: 'Plenum model' },
    { type: 'implicit_hierarchy', column: 'assets.asset_code', levels: 2, separator: '-', examples: ['A-1'] }
  ],
  tables_metadata: [{ table_name: 'assets', row_count: 10, column_count: 2, primary_keys: ['asset_code'], foreign_keys: ['site_id'], source_file: 'x.xlsx', columns: [{ column_name: 'asset_code', datatype: 'text', nullable: false, unique: true, sample_values: ['A-1'] }] }]
};

test('hierarchy: reference rows are shown apart and never sent; the rest are confirmed by default', () => {
  const v = hierarchyView(HIER, {});
  assert.equal(v.refs.length, 1);
  assert.equal(v.review.length, 2, 'the implicit code-shaped item is not a relationship to review');
  assert.deepEqual(v.stats.map((s) => [s.label, s.value, s.tone]), [['FK relationships', 3, 'info'], ['Cycles detected', 0, 'ok'], ['Orphaned records', 2, 'warn'], ['To review', 2, 'neutral']]);
  const body = hierarchyBody(HIER, {});
  assert.deepEqual(body.confirmed_hierarchies.map((r) => r.source_table), ['assets', 'locations']);
  assert.deepEqual(body.hierarchy_corrections, {});
  assert.equal(body.confirmed_hierarchies[0].customer_confirmed, true);
});

test('hierarchy: a modified target is confirmed and recorded as a correction; a rejected row is left out', () => {
  const k0 = relKey(HIER.review_items[0]), k1 = relKey(HIER.review_items[1]);
  const body = hierarchyBody(HIER, { [k0]: 'modify', [k0 + '#table']: 'locations', [k0 + '#column']: 'id', [k1]: 'reject' });
  assert.equal(body.confirmed_hierarchies.length, 1);
  assert.deepEqual([body.confirmed_hierarchies[0].target_table, body.confirmed_hierarchies[0].target_column], ['locations', 'id']);
  assert.deepEqual(Object.keys(body.hierarchy_corrections), ['assets.site_id']);
  assert.equal(hierarchyView(HIER, { [k0]: 'modify', [k1]: 'reject' }).submitLabel, 'Confirm links (0 confirmed · 1 changed · 1 rejected)');
});

test('hierarchy: a loop the service would refuse is named — a table pointing at itself counts', () => {
  assert.deepEqual(hierarchyCycles([{ source_table: 'locations', target_table: 'locations' }]), [['locations', 'locations']]);
  assert.deepEqual(hierarchyCycles([{ source_table: 'a', target_table: 'b' }, { source_table: 'b', target_table: 'a' }]), [['a', 'b', 'a']]);
  const v = hierarchyView(HIER, {});
  assert.deepEqual(v.loops, ['locations → locations']);
  assert.equal(v.review[1].selfRef, true);
  assert.deepEqual(hierarchyView(HIER, { [relKey(HIER.review_items[1])]: 'reject' }).loops, []);
});

test('hierarchy: the tables list and the JSON downloads come from the gate payload', () => {
  const v = hierarchyView(HIER, {});
  assert.equal(v.tablesMeta[0].pk, 'PK: asset_code');
  assert.equal(v.tablesMeta[0].columns[0].isPk, true);
  const f = hierarchyExport('column_metadata', HIER, {}, '9b491bf6-2d86', '2026-09-30T12:00:00.000Z');
  assert.equal(f.filename, 'migration_9b491bf6_column_metadata_2026-09-30T12-00-00.json');
  assert.equal(JSON.parse(f.text).columns[0].primary_key, true);
  const full = JSON.parse(hierarchyExport('full_package', HIER, {}, 'm', '2026-09-30T12:00:00.000Z').text);
  assert.equal(full.validation_results.review_count, 2);
});

test('hierarchy: a single-table import says so and accepts the Plenum default', () => {
  const p = { single_table_import: true, import_table_name: 'assets', import_table_plenum_role: 'assets', review_items: [] };
  const v = hierarchyView(p, {});
  assert.equal(v.blurb, 'Your file has one table, so Plenum’s standard structure is used. Check any column references on your data below.');
  assert.equal(v.proposed.label, 'System default Plenum hierarchy');
  assert.equal(hierarchyBody(p, {}).plenum_default_hierarchy_accepted, true);
});

// ── the write ───────────────────────────────────────────────────────────────────────

test('the write shows confidence and counts, and never names the file "unknown"', () => {
  const v = finalView({ summary: { overall_confidence: 0.912, entity_counts: { sites: 10, assets: 1200 }, total_entities: 1210, source_filename: 'unknown', source_type: 'xlsx' } }, { source_filename: 'estate.xlsx' });
  assert.equal(v.pctLabel, '91%');
  assert.equal(v.tone, 'ok');
  assert.equal(v.file, 'estate.xlsx');
  assert.equal(v.total, '1,210');
  assert.deepEqual(v.counts, [{ label: 'sites', value: '10' }, { label: 'assets', value: '1,200' }]);
  assert.equal(v.writeLabel, 'Yes, write 1,210 rows');
  assert.equal(finalView({ summary: { overall_confidence: 72 } }, null).tone, 'warn', 'a percentage is read as one');
});

// ── the card's stepper and "needs a look" line ─────────────────────────────────────

test('the stepper names six stages and says where the run is', async () => {
  const { stagesFor, gateAttention } = await import('../src/logic/migration.js');
  const at = (doc) => stagesFor(doc).map((s) => s.state).join(',');
  assert.equal(at({ status: 'awaiting_review', pending_gate_type: 'pk_approval', current_step: 1, nodes: [{ node_id: 1, status: 'complete' }] }), 'done,needs,todo,todo,todo,todo');
  assert.equal(at({ status: 'awaiting_review', pending_gate_type: 'hierarchy', current_step: 6, nodes: [] }), 'done,done,done,done,needs,todo');
  assert.equal(at({ status: 'complete', pending_gate_type: null, current_step: 9, nodes: [] }), 'done,done,done,done,done,done');
  assert.equal(at({ status: 'failed', pending_gate_type: null, current_step: 2, nodes: [{ node_id: 1, status: 'complete' }] }), 'done,done,failed,todo,todo,todo');
  // What needs a look, in words: a key that cannot hold is named first.
  const att = gateAttention('pk_approval', { pkRows: [{ invalid: true }, { invalid: false }, { invalid: false }] });
  assert.deepEqual(att.map((a) => a.label), ['1 key won’t hold — not unique or has blanks', '2 tables look right']);
});
