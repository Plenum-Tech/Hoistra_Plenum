// migration — the CSV / Excel migration page against svc-ai-schema-mapper.
//
// The gate bodies here are held to the shapes the service's handlers read (app.py's
// /gate/* routes; human_review_node.py for field_mapping; verify_hierarchy_node.py for
// hierarchy). The fixtures mirror status documents captured from the deployed service on
// 21 Sep 2026 for cmms_clean_test.xlsx (five sheets: Sites, Assets, Vendors, Resources,
// WorkOrders), trimmed to the fields the page reads.
import { test, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';

import {
  defaultGateBody, pollDelay, isTerminal, runKind, shapeNodes, isSpreadsheet, whenLabel, NODES, currentNode
} from '../src/logic/migration.js';
import { gatePath } from '../src/api/schemaMapper.js';

// The controller harness is set up BEFORE any test is declared: node:test stalls when tests
// declared before a top-level `await import` run alongside ones declared after it.
// ── the controller against a mocked service ─────────────────────────────────────────

const mem = {};
globalThis.window = {
  location: { origin: 'http://test.local' }, scrollTo: () => {},
  addEventListener: () => {}, removeEventListener: () => {},
  localStorage: { getItem: (k) => (k in mem ? mem[k] : null), setItem: (k, v) => { mem[k] = String(v); }, removeItem: (k) => { delete mem[k]; } },
  sessionStorage: { getItem: () => null, setItem: () => {}, removeItem: () => {} }
};
globalThis.WebSocket = class { constructor() { throw new Error('no sockets in tests'); } };

let calls, handlers;
globalThis.fetch = async (url, opts) => {
  const u = new URL(String(url));
  const method = (opts && opts.method) || 'GET';
  const key = method + ' ' + u.pathname;
  calls.push({ key: key, body: opts && opts.body, query: u.search });
  const h = handlers[key];
  if (!h) return { ok: false, status: 404, statusText: '404', text: async () => '{"detail":"not mocked"}' };
  const out = typeof h === 'function' ? h(opts) : h;
  return { ok: true, status: 200, statusText: 'OK', text: async () => JSON.stringify(out) };
};

const { HoistraLogic } = await import('../src/logic/HoistraLogic.js');
const settle = () => new Promise((r) => setTimeout(r, 40));
const ID = '2f9f3738-016d-4a5b-bd7b-0971c4b13e6d';
const STATUS = 'GET /backend/schema-mapper/api/migration/' + ID + '/status';


// ── fixtures ────────────────────────────────────────────────────────────────────────

const PK_PAYLOAD = {
  gate: 'pk_approval',
  pk_confirmation: {
    Assets: { kind: 'natural', surrogate: false, confidence: 1.0, detected_pk: ['asset_code'],
      columns: [{ column: 'asset_code', uniqueness: 1.0, null_rate: 0.0, qualifies: true }, { column: 'model', uniqueness: 1.0, null_rate: 0.0, qualifies: true }, { column: 'status', uniqueness: 0.2, null_rate: 0.0, qualifies: false }] },
    Sites: { kind: 'natural', surrogate: false, confidence: 1.0, detected_pk: ['site_id'], columns: [{ column: 'site_id', uniqueness: 1.0, null_rate: 0.0, qualifies: true }] },
    Notes: { kind: 'surrogate', surrogate: true, confidence: 0.4, detected_pk: [], columns: [] }
  }
};

const PRE_SEMANTIC_COLUMNS = {
  gate: 'pre_semantic', gate_step: 'column_matching', locked_phase: 'columns',
  suggested_target_by_table: { Assets: 'assets' },
  review_items_by_table: {
    Assets: [
      { source_field: 'asset_code', target_field: 'asset_code', confidence: 0.98, tier: 'T1_table_exact', dest_table: 'assets', data_type: 'VARCHAR(255)', is_primary_key: true },
      { source_field: 'site_ref', target_field: 'site_id', confidence: 0.71, tier: 'T1_alias', dest_table: 'assets' }
    ]
  }
};

const PRE_SEMANTIC_TABLES = {
  gate: 'pre_semantic', gate_step: 'table_routing', locked_phase: 'tables',
  suggested_target_by_table: { Assets: 'assets', WorkOrders: 'work_orders' },
  existing_canonical_tables: ['assets', 'work_orders', 'maintenance_history']
};

const CLASSIFICATION = {
  gate: 'classification_approval',
  classification: [
    { group_id: 'G1', verdict: 'Foreign Key relationship', members: ['assets.asset_code', 'workorders.asset_id'], has_pk: 'assets.asset_code', fk_column: 'workorders.asset_id', ri: 1.0 },
    { group_id: 'G2', verdict: 'Foreign Key relationship', members: ['sites.site_id', 'assets.site_ref'], has_pk: 'sites.site_id', ri: 1.0 },
    { group_id: 'G3', verdict: 'Shared Attribute', members: ['vendors.trade', 'resources.trade'] },
    { group_id: 'G4', verdict: 'Primary Key group', members: ['sites.site_id'] }
  ],
  shared_attribute_tables: [{ from_group: 'G3', table_name: 'trade', pk_column: 'trade', sample_values: ['Electrical', 'Fire'], distinct_count: 5 }]
};

const COLUMN_MAPPING = {
  gate: 'column_mapping_approval',
  dest_mapping: [
    { source: 'assets.asset_code', source_column: 'asset_code', source_tables: ['Assets'], dest_table: 'assets', matched_column: 'asset_code', outcome: 'auto-resolved', confidence: 0.98, is_primary_key: true },
    { source: 'assets.site_ref', source_column: 'site_ref', source_tables: ['Assets'], dest_table: 'assets', matched_column: 'site_id', outcome: 'suggested — confirm', confidence: 0.71 },
    { source: 'vendors.trade', source_column: 'trade', source_tables: ['Vendors'], dest_table: 'vendors', matched_column: null, outcome: 'new column', confidence: null }
  ],
  dest_columns_by_table: { assets: ['asset_code', 'site_id', 'barcode'], vendors: ['vendor_id', 'vendor_name'] }
};

const FIELD_MAPPING = {
  total_flagged: 2, total_unmappable: 1, overall_confidence: 0.9,
  review_items_by_table: {
    Assets: [
      { source_table: 'Assets', source_field: 'site_ref', item_type: 'flagged', suggested_target: 'site_id', confidence: 0.99, rationale: 'values match sites.site_id', suggestions: ['site_id', 'location_id'] },
      { source_table: 'Assets', source_field: 'install_date', item_type: 'flagged', suggested_target: 'installation_date', confidence: 0.7 }
    ]
  },
  unmappable_items_by_table: {
    Sites: [{ source_field: 'manager_email', item_type: 'unmapped', suggested_action: { action: 'custom', target_table: 'sites', custom_column_name: 'manager_email', data_type: 'VARCHAR(255)' } }]
  },
  table_routing: { Assets: 'assets', Sites: 'sites' },
  canonical_columns_by_table: { assets: ['site_id', 'location_id', 'installation_date', 'barcode'] }
};

const HIERARCHY = {
  instructions: '…', total_cycles: 0, total_orphans: 0, hierarchy_tree: '└── Sites\n    └── Assets',
  hierarchies_to_review: [
    { source_table: 'Assets', source_column: 'site_id', target_table: 'Sites', target_column: 'site_id', relationship_type: 'CONTAINMENT', confidence: 1.0, data_match_rate: 1.0 },
    { source_table: 'WorkOrders', source_column: 'asset_id', target_table: 'Assets', target_column: 'asset_code', relationship_type: 'REFERENCE', confidence: 1.0, data_match_rate: 1.0 },
    { source_table: 'Assets', source_column: 'building_id', target_table: 'buildings', target_column: 'id', relationship_type: 'CONTAINMENT', system_default: true }
  ],
  // As verify_hierarchy_node builds them: one per relationship, then the code-shaped columns.
  review_items: [
    { id: 'hierarchy_0', type: 'hierarchy', source_table: 'Assets', source_column: 'site_id', target_table: 'Sites', target_column: 'site_id', relationship_type: 'CONTAINMENT', confidence: 1.0, data_match_rate: 1.0, system_default: false, mapping_note: false, read_only: false },
    { id: 'hierarchy_1', type: 'hierarchy', source_table: 'WorkOrders', source_column: 'asset_id', target_table: 'Assets', target_column: 'asset_code', relationship_type: 'REFERENCE', confidence: 1.0, data_match_rate: 1.0, system_default: false, mapping_note: false, read_only: false },
    { id: 'hierarchy_2', type: 'system_default', source_table: 'Assets', source_column: 'building_id', target_table: 'buildings', target_column: 'id', relationship_type: 'CONTAINMENT', system_default: true, mapping_note: false, read_only: true },
    { type: 'implicit_hierarchy', column: 'Assets.asset_code', levels: 2, separator: '-', examples: ['A-001', 'A-002'] }
  ]
};

const WRITE = { summary: { source_type: 'excel', entity_counts: { sites: 10, assets: 10 }, total_entities: 20, source_filename: 'cmms_clean_test.xlsx', overall_confidence: 0.97 }, migration_id: 'm-1' };

const doc = (over) => Object.assign({
  migration_id: '2f9f3738-016d-4a5b-bd7b-0971c4b13e6d', status: 'awaiting_review', progress_pct: 0, current_step: 1,
  cmms_name: 'Custom', source_filename: 'cmms_clean_test.xlsx', started_at: '2026-09-21T09:29:01Z', completed_at: null,
  t1_mapped_count: 26, t2_auto_count: 0, t2_human_count: 2, unmapped_count: 0, total_fields: 29,
  pending_gate_type: 'pk_approval', pending_gate_payload: PK_PAYLOAD, error_message: null,
  nodes: [{ node_id: 1, node_name: 'File Ingestion', status: 'complete', outcome: 'Ingested 5 tables, 50 rows', duration_ms: 5412, logs: ['Parsed 50 rows × 29 columns'] }]
}, over || {});

// ── the gate bodies ─────────────────────────────────────────────────────────────────

test('pk_approval sends the detected key for every table, a surrogate where nothing qualified, and the reader\'s override', () => {
  assert.deepEqual(defaultGateBody('pk_approval', PK_PAYLOAD, {}), { pk_overrides: { Assets: ['asset_code'], Sites: ['site_id'], Notes: [] } });
  assert.deepEqual(defaultGateBody('pk_approval', PK_PAYLOAD, { Assets: ['model'] }).pk_overrides.Assets, ['model']);
});

test('unique_table_approval is a plain confirm — the key was decided at the previous gate', () => {
  assert.deepEqual(defaultGateBody('unique_table_approval', { gate: 'unique_table_approval' }, {}), { approved: true });
});

test('pre_semantic column pass approves every rule match unless a column was sent to semantic', () => {
  // An existing table's match is not echoed back: target_field is sent only for a rename.
  const body = defaultGateBody('pre_semantic', PRE_SEMANTIC_COLUMNS, { 'ps:Assets.site_ref': 'semantic' });
  assert.deepEqual(body, {
    decisions: {
      Assets: [
        { source_field: 'asset_code', decision: 'approve' },
        { source_field: 'site_ref', decision: 'semantic' }
      ]
    }
  });
});

test('pre_semantic table pass sends only the destinations the reader changed, marking a name not in the catalogue as a new table', () => {
  assert.deepEqual(defaultGateBody('pre_semantic', PRE_SEMANTIC_TABLES, {}), { decisions: {}, table_overrides: {} });
  const body = defaultGateBody('pre_semantic', PRE_SEMANTIC_TABLES, { 'rt:WorkOrders': { target: 'maintenance_history', isNew: false }, 'rt:Assets': { target: 'equipment_register', isNew: true } });
  assert.deepEqual(body.table_overrides, {
    WorkOrders: { target_table: 'maintenance_history', is_new_table: false },
    Assets: { target_table: 'equipment_register', is_new_table: true }
  });
});

test('classification_approval: untouched groups send nothing; exclude rejects; a changed verdict is an override; PK groups are never touched', () => {
  assert.deepEqual(defaultGateBody('classification_approval', CLASSIFICATION, {}), { rejected_groups: [], verdict_overrides: {} });
  const body = defaultGateBody('classification_approval', CLASSIFICATION, { G1: 'fk', G2: 'shared', G3: 'exclude', G4: 'shared' });
  assert.deepEqual(body, { rejected_groups: ['G3'], verdict_overrides: { G2: 'shared' } });
});

test('column_mapping_approval sends overrides only where the destination differs from the match, keyed source table → column', () => {
  assert.deepEqual(defaultGateBody('column_mapping_approval', COLUMN_MAPPING, {}), { overrides: {} });
  // New column on a row with no match is sent too: it is what makes the service add the column.
  const body = defaultGateBody('column_mapping_approval', COLUMN_MAPPING, { 'cm:assets.site_ref': 'barcode', 'cm:assets.asset_code': 'asset_code', 'cm:vendors.trade': '__new__' });
  assert.deepEqual(body, { overrides: { Assets: { site_ref: 'barcode' }, Vendors: { trade: '__new__' } } });
  assert.deepEqual(defaultGateBody('column_mapping_approval', COLUMN_MAPPING, { 'cm:vendors.trade': 'vendor_name' }), { overrides: { Vendors: { trade: 'vendor_name' } } });
});

test('field_mapping: flagged fields accept their suggestion by default, can be rejected or overridden; unmapped fields follow the suggested custom column unless told otherwise', () => {
  const dflt = defaultGateBody('field_mapping', FIELD_MAPPING, {});
  assert.deepEqual(dflt.flagged, {
    Assets: [
      { action: 'accept', source_field: 'site_ref', target_field: 'site_id', rationale: null },
      { action: 'accept', source_field: 'install_date', target_field: 'installation_date', rationale: null }
    ]
  });
  assert.deepEqual(dflt.unmapped, {
    Sites: [{ action: 'custom', source_field: 'manager_email', target_table: 'sites', custom_column_name: 'manager_email', data_type: 'VARCHAR(255)', nullable: true, is_new_table: false, new_table_pk: null }]
  });
  const edited = defaultGateBody('field_mapping', FIELD_MAPPING, { 'fm:Assets.site_ref': 'override', 'fm:Assets.site_ref#to': 'location_id', 'fm:Assets.install_date': 'reject', 'fu:Sites.manager_email': 'skip' });
  assert.deepEqual(edited.flagged.Assets, [
    { action: 'override', source_field: 'site_ref', target_field: 'location_id', rationale: null },
    { action: 'reject', source_field: 'install_date', target_field: null, rationale: null }
  ]);
  assert.deepEqual(edited.unmapped.Sites, [{ action: 'skip', source_field: 'manager_email', target_table: null, custom_column_name: null, data_type: null }]);
});

test('hierarchy confirms every detected relationship except the ones rejected, and skips platform defaults', () => {
  const dflt = defaultGateBody('hierarchy', HIERARCHY, {});
  assert.equal(dflt.confirmed_hierarchies.length, 2, 'the system_default row is not the reader\'s to confirm');
  assert.deepEqual(dflt.hierarchy_corrections, {});
  // A rejection is simply not confirmed — the confirmed list replaces the detected one.
  const body = defaultGateBody('hierarchy', HIERARCHY, { 'WorkOrders>asset_id>Assets>asset_code': 'reject' });
  assert.deepEqual(body.confirmed_hierarchies.map((r) => r.source_table), ['Assets']);
  assert.deepEqual(body.hierarchy_corrections, {});
});

test('hierarchy reads review_items, and hierarchies_to_review when review_items is absent', () => {
  const fromItems = defaultGateBody('hierarchy', { review_items: HIERARCHY.review_items }, {});
  assert.deepEqual(fromItems.confirmed_hierarchies.map((r) => r.source_table), ['Assets', 'WorkOrders']);
  const fromList = defaultGateBody('hierarchy', { hierarchies_to_review: HIERARCHY.hierarchies_to_review }, {});
  assert.deepEqual(fromList.confirmed_hierarchies.map((r) => r.source_table), ['Assets', 'WorkOrders']);
});

test('the write gate never confirms by default', () => {
  assert.deepEqual(defaultGateBody('write', WRITE, {}), { confirmed: false });
  assert.deepEqual(defaultGateBody('write', WRITE, { confirm: true }), { confirmed: true });
});

test('every gate the status document can name has a route, and the write gate is answered at gate/final', () => {
  for (const g of ['pk_approval', 'unique_table_approval', 'pre_semantic', 'classification_approval', 'column_mapping_approval', 'field_mapping', 'hierarchy']) {
    assert.equal(gatePath(g), g.replace(/_/g, '-'));
  }
  assert.equal(gatePath('write'), 'final');
  assert.throws(() => gatePath('artifacts_review'), /no gate route/);
});

// ── the small helpers ───────────────────────────────────────────────────────────────

test('polling follows the run: quick while working, quicker after a step, slow at a gate, off when finished', () => {
  assert.equal(pollDelay('running'), 2500);
  assert.equal(pollDelay('step_paused'), 1500);
  assert.equal(pollDelay('awaiting_review'), 8000);
  assert.equal(pollDelay('complete'), 0);
  assert.equal(pollDelay('ddl_failed'), 0);
  assert.ok(isTerminal('FAILED') && !isTerminal('running'));
});

test('runKind names the four states the screen switches on', () => {
  assert.equal(runKind(doc()), 'gate');
  assert.equal(runKind(doc({ status: 'step_paused', pending_gate_type: 'step_2_deterministic_mapping' })), 'step');
  assert.equal(runKind(doc({ status: 'running', pending_gate_type: null })), 'running');
  assert.equal(runKind(doc({ status: 'complete' })), 'done');
  assert.equal(runKind(doc({ status: 'ddl_failed' })), 'failed');
  assert.equal(runKind(null), 'running');
});

test('the tracker shows every node in order: the service\'s own where it reported one, the current one marked by the run\'s state', () => {
  const rows = shapeNodes(doc({ current_step: 1, status: 'awaiting_review' }));
  assert.equal(rows.length, NODES.length);
  assert.equal(rows[0].status, 'complete');
  assert.equal(rows[0].outcome, 'Ingested 5 tables, 50 rows');
  assert.equal(rows[1].status, 'pending');
  assert.deepEqual(NODES.map((n) => n.id), [1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 'ids are the service\'s node ids, write last');
  assert.ok(shapeNodes(doc({ status: 'complete' })).every((n) => n.status === 'complete'));
});

test('the highlighted node is the one the run is on, not the node whose id equals current_step', () => {
  // The service's current_step skips semantic mapping and Gate 1, so from preprocessing on it
  // reads one below the node id. What run 117b1beb (24 Sep 2026) reported, step by step:
  const ticked = (...ids) => ids.map((id) => ({ node_id: id, node_name: 'n' + id, status: 'complete' }));
  const at = (over) => currentNode(doc(Object.assign({ pending_gate_payload: {} }, over)));
  assert.equal(at({ status: 'awaiting_review', current_step: 1, pending_gate_type: 'pk_approval', nodes: [] }), 1);
  assert.equal(at({ status: 'awaiting_review', current_step: 2, pending_gate_type: 'column_mapping_approval', nodes: ticked(1, 2) }), 3);
  assert.equal(at({ status: 'step_paused', current_step: 5, pending_gate_type: 'step_5_preprocess', nodes: ticked(1, 2, 3) }), 6);
  assert.equal(at({ status: 'running', current_step: 5, pending_gate_type: null, nodes: ticked(1, 2, 3, 6) }), 7);
  assert.equal(at({ status: 'awaiting_review', current_step: 6, pending_gate_type: 'hierarchy', nodes: ticked(1, 2, 3, 6, 7) }), 8);
  assert.equal(at({ status: 'step_paused', current_step: 8, pending_gate_type: 'step_8_output_generation', nodes: ticked(1, 2, 3, 6, 7, 8) }), 9);
  assert.equal(at({ status: 'awaiting_review', current_step: 8, pending_gate_type: 'write', nodes: ticked(1, 2, 3, 6, 7, 8, 9) }), 10);
  // The screenshot: the write under way after its gate, every node above it ticked. It used to
  // highlight "Verify hierarchy".
  const writing = doc({ status: 'running', current_step: 8, pending_gate_type: null, pending_gate_payload: {}, nodes: ticked(1, 2, 3, 6, 7, 8, 9) });
  assert.equal(currentNode(writing), 10);
  const rows = shapeNodes(writing);
  assert.equal(rows[9].name, 'Write to database');
  assert.equal(rows[9].status, 'running');
  assert.equal(rows[7].status, 'complete');
  assert.equal(rows[3].status, 'pending', 'a skipped semantic node stays unticked, in its place');
});

test('only CSV and Excel names are spreadsheets, whatever the browser says the MIME type is', () => {
  assert.ok(isSpreadsheet({ name: 'assets.CSV', type: '' }));
  assert.ok(isSpreadsheet({ name: 'export.xlsx', type: 'application/octet-stream' }));
  assert.ok(!isSpreadsheet({ name: 'certificate.pdf', type: 'application/pdf' }));
  assert.ok(!isSpreadsheet(null));
});

test('whenLabel is relative for a day and a date after that', () => {
  const now = Date.parse('2026-09-21T12:00:00Z');
  assert.equal(whenLabel('2026-09-21T11:57:00Z', now), '3 min ago');
  assert.equal(whenLabel('2026-09-21T09:00:00Z', now), '3 h ago');
  assert.equal(whenLabel('2026-09-01T09:00:00Z', now), '2026-09-01');
  assert.equal(whenLabel(null, now), '');
});

let c;
beforeEach(() => {
  calls = []; handlers = {};
  c = new HoistraLogic();
  c.setState({ signedIn: true, role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' } });
});
afterEach(() => {
  clearTimeout(c._mgTimer); clearTimeout(c._tt);
  clearInterval(c._orchTick); clearInterval(c._ccTick);
});

// Migration had a nav entry of its own while it was a page. It is a conversation now, and
// a nav item that merely opened the chat would be a second door onto the same room.
test('Migration has no nav entry of its own — a run is reached through the conversation', () => {
  const admin = c.renderVals().navAdmin.map((a) => a.label);
  assert.deepEqual(admin, ['Integrations', 'Users & access', 'Audit trail']);
  c.setState({ role: 'user' });
  assert.deepEqual(c.renderVals().navAdmin, []);
  assert.ok(!c.renderVals().navSections.some((n) => n.label === 'Migration'), 'the user nav is reports only');
});

test('opening a run reads its status and shows the pk gate with the detected keys pre-selected', async () => {
  handlers[STATUS] = doc();
  c.mgOpen(ID);
  await settle();
  const v = c.renderVals();
  assert.equal(v.mgKind, 'gate');
  assert.equal(v.mgGate, 'pk_approval');
  assert.equal(v.mgGateTitle, 'Which column identifies each row?');
  assert.deepEqual(v.pkRows.map((r) => [r.label, r.kind, r.chips.map((x) => x.column)]), [['Assets', 'natural', ['asset_code']], ['Sites', 'natural', ['site_id']], ['Notes', 'surrogate', []]]);
  assert.equal(v.mgPrimary.label, 'Confirm ID columns');
  assert.equal(v.mgPill.label, 'Awaiting your review');
  assert.equal(v.mgNodes[0].status, 'complete');
  assert.ok(!calls.some((x) => x.key.endsWith('/advance')), 'a human gate is never advanced by the page');
});

test('a step pause is never continued by the page: the card owns it before its mount effect and after it unmounts', async () => {
  // CAFM Web's card (src/cafm) continues the ingest and deterministic pauses itself and shows a
  // Continue button for the rest. The conversation's own read never calls /advance — not on open,
  // before the card has declared itself mounted, and not from a timer that fires after the card
  // has gone (leaving the chat must not continue a semantic pause behind the reader's back).
  handlers[STATUS] = doc({ status: 'step_paused', current_step: 3, pending_gate_type: 'step_3_semantic_mapping', pending_gate_payload: { node: 3, label: 'Semantic Mapping' } });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/advance'] = { ok: true };
  const advances = () => calls.filter((x) => x.key.endsWith('/advance')).length;
  const before = advances();
  c.mgOpen(ID);
  await settle();
  assert.equal(advances(), before, 'not on open');
  c.mgCafmMounted(true);
  c.mgCafmMounted(false);
  clearTimeout(c._mgTimer);
  await c.mgPoll(true);
  assert.equal(advances(), before, 'not after the card has unmounted');
  assert.ok(c._mgTimer, 'the read keeps going on its own clock');
  clearTimeout(c._mgTimer);
});

test('an ingest pause is shown as its report while the card, not the page, continues it', async () => {
  handlers[STATUS] = doc({ status: 'step_paused', pending_gate_type: 'step_1_ingest', pending_gate_payload: { node: 1, label: 'File Ingestion', rows: 50, columns: 29 } });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/advance'] = { ok: true };
  c.setState({ mgAuto: false, mgStatus: null });
  c.mgOpen(ID);
  await settle();
  assert.equal(calls.filter((x) => x.key.endsWith('/advance')).length, 0);
  assert.ok(c._mgTimer, 'the page keeps reading while the card continues the step');
  clearTimeout(c._mgTimer);
  const v = c.renderVals();
  assert.equal(v.mgKind, 'step');
  assert.equal(v.mgPrimary.label, 'Continue');
  assert.equal(v.mgGateTitle, 'Your file has been read');
  assert.deepEqual(v.mgStepFacts.map((f) => f.label), ['rows', 'columns']);
  assert.equal(v.mgIngestLive, true, 'the ingest pause is shown as its report');
  assert.deepEqual(v.mgIngest.tiles.map((t) => t.value), ['50', '29', '—']);
});

test('while auto-continue runs, the step says so instead of offering a button', async () => {
  handlers[STATUS] = doc({ status: 'step_paused', pending_gate_type: 'step_1_ingest', pending_gate_payload: { node: 1, label: 'Ingest & Configure', rows: 50, columns: 29 } });
  // /advance is not mocked: the pause stays in view, as it does while the service resumes.
  c.setState({ mgBusy: 'Continuing…' });
  c.mgOpen(ID);
  await settle();
  c.setState({ mgBusy: '' });
  const v = c.renderVals();
  assert.equal(v.mgKind, 'step');
  assert.equal(v.mgContinuing, true);
  assert.equal(v.mgPrimary, null);
  assert.equal(v.mgGateBlurb, 'Continuing to the next step on its own.');
});

test('the ingest report outlives its pause and stays readable above the gates that follow', async () => {
  const pause = { node: 1, label: 'Ingest & Configure', rows: 289606, columns: 181, format: 'excel',
    nan_report: { total_nan_cells: 718, total_rows_with_nan: 214, columns_with_nan: 29, tables: { Assets: { nan_cells: 60, rows_with_nan: 60, columns: { warranty_expiry: 60 } } } } };
  handlers[STATUS] = doc({ status: 'step_paused', pending_gate_type: 'step_1_ingest', pending_gate_payload: pause });
  c.mgOpen(ID);
  await settle();
  // The card continues the pause; the next read finds the run at the PK gate, and Node 1's
  // output was too big for the poll to carry.
  handlers[STATUS] = doc();
  clearTimeout(c._mgTimer);
  await c.mgPoll(true);
  await settle();
  const v = c.renderVals();
  assert.equal(v.mgGate, 'pk_approval');
  assert.equal(v.mgIngestLive, false);
  assert.ok(v.mgIngest, 'the report seen at the pause is kept');
  assert.equal(v.mgIngest.tiles[0].value, '289,606');
  assert.equal(v.mgIngest.summary, '289,606 rows · 181 columns · excel · 718 null/NaN values');
  assert.equal(v.mgIngestOpen, false, 'history sits collapsed so the gate stays in view');
  v.mgToggleIngest();
  assert.equal(c.renderVals().mgIngestOpen, true);

  // Another run does not inherit it.
  handlers['GET /backend/schema-mapper/api/migration/other/status'] = doc({ migration_id: 'other' });
  c.mgOpen('other');
  await settle();
  assert.equal(c.renderVals().mgIngest, null);
});

test('reopened after the pause, the report comes from Node 1\'s output when the poll carries it', async () => {
  handlers[STATUS] = doc({ nodes: [{ node_id: 1, node_name: 'File Ingestion', status: 'complete', outcome: '', duration_ms: 5412, logs: [],
    output: { row_count: 50, column_count: 29, detected_format: 'excel', nan_report: { total_nan_cells: 0, tables: {} } } }] });
  c.mgOpen(ID);
  await settle();
  const v = c.renderVals();
  assert.deepEqual(v.mgIngest.tiles.map((t) => t.value), ['50', '29', 'excel']);
  assert.equal(v.mgIngest.nan.clean, true);
});

test('the PK chips add, remove and fall back to a surrogate, and a group edits every member', async () => {
  const payload = { ...PK_PAYLOAD, pk_confirmation: { ...PK_PAYLOAD.pk_confirmation,
    Vendors: { kind: 'natural', detected_pk: ['vendor_code'], columns: [{ column: 'vendor_code', uniqueness: 1, null_rate: 0, qualifies: true }] },
    Vendor_Contracts: { kind: 'natural', detected_pk: ['vendor_code'], columns: [{ column: 'vendor_code', uniqueness: 1, null_rate: 0, qualifies: true }, { column: 'contract_ref', uniqueness: 1, null_rate: 0, qualifies: true }] } },
    table_resolution: { duplicate_tables: { groups: [{ tables: ['Vendor_Contracts', 'Vendors'], count: 2, label: 'Vendor' }] } } };
  handlers[STATUS] = doc({ pending_gate_payload: payload });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/gate/pk-approval'] = { ok: true };
  c.mgOpen(ID);
  await settle();
  const row = (label) => c.renderVals().pkRows.find((r) => r.label === label);
  row('Assets').add('model');
  assert.deepEqual(row('Assets').chips.map((x) => x.column), ['asset_code', 'model']);
  assert.equal(row('Assets').kind, 'composite');
  row('Assets').remove('asset_code');
  assert.deepEqual(row('Assets').chips.map((x) => x.column), ['model']);
  row('Sites').useSurrogate();
  assert.equal(row('Sites').kind, 'surrogate');
  row('Vendor').add('contract_ref');
  c.renderVals().mgPrimary.run();
  await settle();
  const post = calls.find((x) => x.key.endsWith('/gate/pk-approval'));
  assert.deepEqual(JSON.parse(post.body).pk_overrides, {
    Assets: ['model'], Sites: [], Notes: [],
    Vendors: ['vendor_code', 'contract_ref'], Vendor_Contracts: ['vendor_code', 'contract_ref']
  });
});

test('approving a gate posts the default body to that gate\'s route, with the reader\'s decisions folded in', async () => {
  handlers[STATUS] = doc({ pending_gate_type: 'classification_approval', pending_gate_payload: CLASSIFICATION, current_step: 2 });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/gate/classification-approval'] = { ok: true };
  c.mgOpen(ID);
  await settle();
  const v = c.renderVals();
  assert.deepEqual([v.clView.fk.length, v.clView.shared.length], [2, 1]);
  v.mgSet(v.clView.shared[0].id, 'exclude');
  assert.equal(c.renderVals().mgPrimary.label, 'Apply 1 change & continue');
  c.renderVals().mgPrimary.run();
  await settle();
  const post = calls.find((x) => x.key.endsWith('/gate/classification-approval'));
  assert.ok(post, 'the classification route was called');
  assert.deepEqual(JSON.parse(post.body), { rejected_groups: ['G3'], verdict_overrides: {} });
  assert.deepEqual(c.state.mgDec, {}, 'decisions are spent once the gate is answered');
});

test('Node 2\'s unmatched fields are kept from its pause and answered at the column pass', async () => {
  handlers[STATUS] = doc({ status: 'step_paused', current_step: 2, pending_gate_type: 'step_2_deterministic_mapping',
    pending_gate_payload: { node: 2, label: 'Deterministic Mapping', unresolved_by_table: { Assets: ['serial'] } } });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/gate/pre-semantic'] = { ok: true };
  c.mgOpen(ID);
  await settle();
  // The card continues Node 2's pause; the next read finds the run at the column pass.
  handlers[STATUS] = doc({ current_step: 3, pending_gate_type: 'pre_semantic', pending_gate_payload: Object.assign({}, PRE_SEMANTIC_COLUMNS, {
    canonical_columns_by_table: { assets: ['asset_code', 'site_id', 'serial_number'] } }) });
  clearTimeout(c._mgTimer);
  await c.mgPoll(true);
  await settle();
  const v = c.renderVals();
  assert.equal(v.mgGate, 'pre_semantic');
  const assets = v.psView.tables.find((t) => t.key === 'Assets');
  assert.deepEqual(assets.unresolved.map((u) => u.field), ['serial']);
  v.mgSet(assets.unresolved[0].key, 'serial_number');
  c.renderVals().mgPrimary.run();
  await settle();
  const post = JSON.parse(calls.find((x) => x.key.endsWith('/gate/pre-semantic')).body);
  assert.deepEqual(post.decisions.Assets[2], { source_field: 'serial', decision: 'approve', target_field: 'serial_number' });
  assert.ok(!('table_overrides' in post), 'the column pass never re-sends the routing');
});

test('the write gate needs two clicks and posts {confirmed: true}; Reject posts {confirmed: false}', async () => {
  handlers[STATUS] = doc({ pending_gate_type: 'write', pending_gate_payload: WRITE, current_step: 8 });
  handlers['POST /backend/schema-mapper/api/migration/' + ID + '/gate/final'] = { ok: true };
  c.mgOpen(ID);
  await settle();
  let v = c.renderVals();
  assert.equal(v.mgPrimary, null, 'no single-click approve on the gate that writes');
  assert.equal(v.wrView.total, '20');
  assert.equal(v.wrView.title, 'Ready to write 20 rows to Plenum');
  assert.equal(v.mgGateTitle, 'Ready to write 20 rows to Plenum');
  assert.equal(v.wrArmed, false);
  v.mgArm();
  v = c.renderVals();
  assert.equal(v.wrArmed, true);
  assert.equal(v.mgWriteLabel, 'Yes, write 20 rows');
  v.mgConfirmWrite();
  await settle();
  const post = calls.find((x) => x.key.endsWith('/gate/final'));
  assert.deepEqual(JSON.parse(post.body), { confirmed: true });

  calls = [];
  c.renderVals().mgRejectWrite();
  await settle();
  assert.deepEqual(JSON.parse(calls.find((x) => x.key.endsWith('/gate/final')).body), { confirmed: false });
});

test('a finished run shows its artefacts and stops polling; a failed one shows the reason', async () => {
  handlers[STATUS] = doc({ status: 'complete', current_step: 9, pending_gate_type: null, pending_gate_payload: {}, progress_pct: 100,
    output_json_url: 'https://blob/o.json', output_sql_url: 'https://blob/o.sql', migration_report_url: 'https://blob/r.pdf' });
  c.mgOpen(ID);
  await settle();
  let v = c.renderVals();
  assert.equal(v.mgKind, 'done');
  assert.equal(v.mgProgress, 100);
  assert.deepEqual(v.mgOutputs.map((o) => o.label), ['Report (PDF)', 'JSON', 'SQL']);
  assert.equal(c._mgTimer, undefined, 'nothing more to read');

  handlers[STATUS] = doc({ status: 'failed', error_message: 'DDL failed: relation "trade" already exists', pending_gate_type: null, pending_gate_payload: {} });
  c.setState({ mgStatus: null });
  c.mgOpen(ID);
  await settle();
  v = c.renderVals();
  assert.equal(v.mgKind, 'failed');
  assert.match(v.mgGateBlurb, /relation "trade" already exists/);
});

test('one file starts on start-with-upload, several on the multi route as one job, and the run opens in the conversation', async () => {
  handlers['POST /backend/schema-mapper/api/migration/start-with-upload'] = { migration_id: ID, status: 'running' };
  handlers['POST /backend/schema-mapper/api/migration/start-with-upload-multi'] = { migration_id: ID, status: 'running' };
  handlers[STATUS] = doc({ status: 'running', pending_gate_type: null, pending_gate_payload: {} });
  c.mgShow();
  c.mgPickFiles([new File(['a,b\n1,2'], 'assets.csv'), new File(['x'], 'notes.pdf')]);
  assert.deepEqual(c.state.mgFiles.map((f) => f.name), ['assets.csv'], 'a PDF is not a migration');
  assert.match(c.state.toast, /notes\.pdf is not a CSV or Excel file/);
  await c.mgStart();
  let post = calls.find((x) => x.key.endsWith('/start-with-upload'));
  assert.ok(post && post.body instanceof FormData);
  assert.equal(post.body.get('cmms_name'), 'Custom');
  assert.equal(post.body.get('file').name, 'assets.csv');
  assert.equal(c.state.mgId, ID);
  assert.equal(c.state.view, 'chat', 'the run is answered in the Orchestrator, not on a page');
  assert.deepEqual(c.state.mgFiles, []);
  clearTimeout(c._mgTimer);

  calls = [];
  c.mgNew();
  c.mgPickFiles([new File(['a'], 'sites.xlsx'), new File(['b'], 'assets.xlsx')]);
  c.setState({ mgCmms: 'Maximo' });
  await c.mgStart();
  post = calls.find((x) => x.key.endsWith('/start-with-upload-multi'));
  assert.ok(post, 'two files are one job');
  assert.equal(post.body.getAll('files').length, 2);
  assert.equal(post.body.get('cmms_name'), 'Maximo');
});

test('a failed upload is reported on the panel and nothing opens', async () => {
  c.mgShow();
  c.mgPickFiles([new File(['a'], 'assets.csv')]);
  await c.mgStart();
  assert.equal(c.state.mgId, null);
  assert.match(c.state.mgError, /did not start a migration/);
  assert.equal(c.state.mgBusy, '');
});

test('the recent list reads the company\'s runs and opens one on click', async () => {
  handlers['GET /backend/schema-mapper/api/migration'] = { total_count: 1, migrations: [{ migration_id: ID, cmms_name: 'Custom', status: 'awaiting_review', progress_pct: 0, t1_count: 26, t2_count: 0, started_at: '2026-09-21T09:29:01Z', completed_at: null }] };
  handlers[STATUS] = doc();
  c.mgShow();
  await settle();
  const v = c.renderVals();
  assert.equal(v.mgRecent.length, 1);
  assert.equal(v.mgRecent[0].status, 'awaiting review');
  assert.equal(v.mgRecent[0].mapped, 26);
  v.mgRecent[0].open();
  await settle();
  assert.equal(c.state.mgId, ID);
  assert.ok(c.state.mgStatus);
});

test('a staged spreadsheet migrates from the composer, and a reply that started a run reopens it', async () => {
  c.setState({ view: 'chat', ccChat: [{ role: 'you', text: 'migrate this' }, { role: 'bot', text: 'Paused at the primary-key gate.', migrations: [ID] }] });
  let v = c.renderVals();
  const bot = v.orchChat[1];
  assert.equal(bot.migShow, 'flex');
  assert.equal(bot.migIds[0].short, '2f9f3738');
  assert.equal(v.orchMigrateShow, 'none');

  c.ccAddFiles([new File(['a'], 'assets.csv'), new File(['b'], 'cert.pdf')]);
  v = c.renderVals();
  assert.equal(v.orchMigrateShow, 'flex');
  // Through the orchestrator's upload route: that is the one that binds the ingest to a
  // building and writes the audit row (svc-deepagents' batch worker), which schema-mapper's
  // own start-with-upload has no field for. Both files go — it splits them by type.
  handlers['POST /backend/deep-agents/api/workflow/run-stateful-with-files'] = { session_id: 's1', answer: 'Paused at the primary-key gate.', tool_calls: [], success: true, ingested_migration_ids: [ID] };
  handlers[STATUS] = doc();
  await v.orchMigrateHere();
  await settle();
  assert.equal(c.state.view, 'chat', 'there is no page to go to');
  assert.equal(c.state.mgId, ID, 'the spreadsheet started its run where it was attached');
  assert.deepEqual(c.state.ccFiles, [], 'the whole tray went with the turn');

  bot.migIds[0].open();
  await settle();
  assert.equal(c.state.mgId, ID);
});

test('a chat turn with a spreadsheet attached asks the orchestrator for an interactive migration and keeps the ids it returns', async () => {
  c.setState({ view: 'chat', sessionId: null });
  handlers['POST /backend/deep-agents/api/workflow/run-stateful-with-files'] = { session_id: 's1', answer: 'Migration paused at pk approval gate.', tool_calls: [], success: true, error: null, ingested_migration_ids: [ID] };
  c.ccAddFiles([new File(['a,b'], 'assets.csv')]);
  c.setState({ orchQuery: 'migrate this file' });
  await c.ccAsk('migrate this file');
  await settle();
  const post = calls.find((x) => x.key.endsWith('/run-stateful-with-files'));
  assert.ok(post, 'the upload route was called');
  assert.equal(post.body.get('interactive_migration'), 'true');
  const bot = (c.state.ccChat || []).filter((m) => m.role === 'bot').pop();
  assert.deepEqual(bot.migrations, [ID]);
});

test('opening a run puts it in the Orchestrator, not on a page of its own', async () => {
  handlers[STATUS] = doc();
  c.mgOpen(ID);
  await settle();
  assert.equal(c.state.view, 'chat', 'the run is answered in the conversation');
  assert.equal(c.state.mgId, ID);
  assert.ok(c.renderVals().mgHasRun, 'the card has a run to render');
});

// The status document runs to ~500 KB, so it is only re-read where it is rendered. That
// was one view; it is now wherever the conversation is — the chat page, or a page keeping
// its dock. A guard still naming 'migration' stops the run dead the moment it is opened.
test('a run is read where it is rendered: polling follows the Orchestrator and stops off it', async () => {
  handlers[STATUS] = doc();
  c.setState({ view: 'chat' });
  c.mgOpen(ID);
  await settle();
  assert.ok(c._mgTimer, 'the next status read is scheduled from the chat');
  clearTimeout(c._mgTimer);
  c._mgTimer = null;
  // A page that merely keeps a dock does not render the card, so it does not re-read a
  // ~500 KB status document either.
  c.setState({ view: 'buildings' });
  await c.mgPoll(true);
  await settle();
  assert.ok(!c._mgTimer, 'a dock page does not follow the run');
});

test('a run opened from a dock page lands on the Orchestrator, where its gates render', async () => {
  handlers[STATUS] = doc();
  c.setState({ view: 'buildings' });
  c.mgOpen(ID);
  await settle();
  assert.equal(c.state.view, 'chat', 'the gates are never rendered nowhere');
  assert.equal(c.state.orchOpen, false, 'and the dock it came from is closed behind it');
});

test('a reload lands back on the open run: the conversation and the migration id both persist', async () => {
  const { loadSession, saveSession } = await import('../src/logic/session.js');
  saveSession({ signedIn: true, refreshToken: 'ref-1', email: 'a@b.c', role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' }, view: 'chat', mgId: ID }, { signedIn: true });
  const back = loadSession();
  assert.equal(back.view, 'chat');
  assert.equal(back.mgId, ID);
  saveSession({ signedIn: true, refreshToken: 'ref-1', email: 'a@b.c', role: 'admin', account: { id: 'u1', email: 'a@b.c', role: 'admin', status: 'active' }, view: 'home', mgId: 'not an id at all!' }, { signedIn: true });
  assert.equal(loadSession().mgId, undefined, 'a malformed id is not restored');
});

// Cancel is two presses: the first arms the button, the second sends DELETE. A finished run
// offers nothing to cancel, and a refusal from the service (another company's run) is shown.
test('a run can be cancelled with two presses, and a finished run offers no cancel', async () => {
  handlers[STATUS] = doc();
  handlers['GET /backend/schema-mapper/api/migration'] = { total_count: 0, migrations: [] };
  handlers['DELETE /backend/schema-mapper/api/migration/' + ID] = { migration_id: ID, status: 'cancelled', message: 'ok' };
  c.mgOpen(ID);
  await settle();
  let v = c.renderVals();
  assert.equal(v.mgCanCancel, true);
  assert.equal(v.mgCancelLabel, 'Cancel migration');

  v.mgCancel();
  await settle();
  v = c.renderVals();
  assert.equal(v.mgCancelArmed, true);
  assert.equal(v.mgCancelLabel, 'Click again to cancel');
  assert.ok(!calls.some((x) => x.key.startsWith('DELETE')), 'the first press only arms');

  handlers[STATUS] = Object.assign(doc(), { status: 'cancelled', pending_gate_type: null, pending_gate_payload: null });
  v.mgCancel();
  await settle();
  assert.equal(calls.filter((x) => x.key === 'DELETE /backend/schema-mapper/api/migration/' + ID).length, 1);
  v = c.renderVals();
  assert.equal(v.mgCanCancel, false, 'a cancelled run has nothing left to cancel');
  clearTimeout(c._mgCancelTimer);
});

test('a refused cancel says why and leaves the run as it was', async () => {
  handlers[STATUS] = doc();
  c.mgOpen(ID);
  await settle();
  c.setState({ mgCancelArmed: ID });   // already armed
  await c.mgCancel();                  // DELETE not mocked -> the service refuses
  await settle();
  const v = c.renderVals();
  assert.match(v.mgError, /^The migration was not cancelled/);
  assert.equal(v.mgCanCancel, true);
});
