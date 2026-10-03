// migration — a CSV / Excel migration, against svc-ai-schema-mapper's pipeline
// (api/schemaMapper.js), answered in the Orchestrator conversation that started it
// (cafm/hoistra-migration-wizard.tsx — CAFM Web's own migration panel).
//
// A migration is one upload walked through nine nodes with human gates between them.
// This module owns: the upload (staged in the composer's tray → mgStartFromChat), the open
// run (mgId + the status document the service last returned), the poll that keeps that
// document current, the step-pause auto-continue, and the decisions the reader makes at
// each gate before the gate is answered. Nothing is written to plenum_cafm until the LAST
// gate — `write` — is confirmed, and that confirmation is a two-step control here (arm,
// then confirm) because it is the one click in this flow that changes the database.
//
// It had a page of its own until 21 Sep 2026. Moving it into the conversation changed
// where a run is SHOWN and how one is STARTED; the nine nodes, the body each gate is
// answered with and the arm/confirm write are exactly as they were.
//
// Where a gate's body shape came from: the gate handlers in svc-ai-schema-mapper/src/app.py
// and the nodes that consume them (human_review_node.py for field_mapping,
// verify_hierarchy_node.py for hierarchy). defaultGateBody() below is the one place that
// knowledge lives; it is a pure function so a test can hold it to those shapes.
//
// Pure helpers are named exports; the methods are mixed into HoistraLogic.prototype and
// `this` is the controller.
import { schemaMapperApi } from '../api/schemaMapper.js';
import { deepAgentsApi } from '../api/deepAgents.js';
import { opsApi } from '../api/opsIntelligence.js';
import { extrasStatus } from './workbookExtras.js';
import { currentOrgId, isStaleScope } from '../api/client.js';
import {
  relKey, uniqueTablesView, classificationView, classificationBody, columnMappingView, columnMappingBody,
  hierarchyView, hierarchyBody, hierarchyExport, finalView, preSemanticPhase, tableRoutingView, tableRoutingBody,
  columnMatchingView, columnMatchingBody, fieldMappingView, fieldMappingBody, fieldMappingProblems, PS_DATA_TYPES
} from './migrationGates.js';

export { relKey };

// Statuses after which the run will not move again.
export const TERMINAL = new Set(['complete', 'failed', 'ddl_failed', 'cancelled']);

// What the post-write engines did with a single workbook's Contract_Terms and Invoice_Lines.
// Empty when the workbook carried neither - an ordinary migration says nothing more.
export function extrasLine(res) {
  if (!res) return '';
  if (res.loading) return "Reading the workbook's contract terms and invoices…";
  if (res.error) return 'Contract terms and invoices could not be read — ' + res.error;
  if (!res.found) return '';
  const cs = res.contracts || [], iv = res.invoices || [];
  const held = iv.reduce((n, x) => n + (Number(x.held) || 0), 0);
  const bits = [];
  if (cs.length) bits.push(cs.length + (cs.length === 1 ? ' contract' : ' contracts') + ' read into draft terms — confirm each on the Vendors page');
  if (iv.length) bits.push(iv.length + (iv.length === 1 ? ' invoice' : ' invoices') + ' verified' + (held ? ', ' + held + (held === 1 ? ' line' : ' lines') + ' held for your decision' : ''));
  const tel = res.telemetry || {};
  if (tel.chiller_readings || tel.degree_days || tel.bms_samples) {
    bits.push('plant telemetry stored (' + [
      tel.chiller_readings ? tel.chiller_readings + ' chiller readings' : '',
      tel.degree_days ? tel.degree_days + ' months of degree days' : '',
      tel.bms_samples ? tel.bms_samples + ' BMS samples' : ''].filter(Boolean).join(', ') + ') — run the energy scan to assess them');
  }
  const sk = (res.skipped || []).length;
  if (sk) bits.push(sk + ' skipped (' + (res.skipped || []).map((x) => x.reason).filter((v, i, a) => a.indexOf(v) === i).join(', ') + ')');
  return bits.join(' · ');
}
export const isTerminal = (status) => TERMINAL.has(String(status || '').toLowerCase());

// The pipeline's nodes, numbered as the service numbers them in `nodes[].node_id`, plus the
// write, which the service reports no row for. Semantic mapping and Gate 1 are skipped when
// every field is rule-matched; they stay in the list, unticked, so the order reads true.
export const NODES = [
  { id: 1, name: 'File ingestion', blurb: 'Parse the file, detect tables and columns, confirm primary keys and unique tables' },
  { id: 2, name: 'Deterministic mapping', blurb: 'Exact, alias and pattern matches; column groups and destination columns' },
  { id: 3, name: 'Pre-semantic review', blurb: 'Every rule-based match, approved or sent on to semantic matching' },
  { id: 4, name: 'Semantic mapping', blurb: 'Embedding matches for what the rules left; flagged fields gated' },
  { id: 5, name: 'Field mapping review', blurb: 'Accept, reject or override each flagged field; decide unmapped ones' },
  { id: 6, name: 'Data preprocessing', blurb: 'Dedup, null handling, type coercion, validation' },
  { id: 7, name: 'Hierarchy detection', blurb: 'Foreign keys and containment between the tables' },
  { id: 8, name: 'Verify hierarchy', blurb: 'The relationships, confirmed by you' },
  { id: 9, name: 'Output generation', blurb: 'JSON, CSV, SQL and the report, uploaded to blob storage' },
  { id: 10, name: 'Write to database', blurb: 'The confirmed rows land in plenum_cafm' }
];

// The node an open gate or step pause belongs to. The name says it exactly, where the
// number does not.
const NODE_BY_PAUSE = {
  step_1_ingest: 1, pk_approval: 1, unique_table_approval: 1,
  step_2_deterministic_mapping: 2,
  pre_semantic: 3, classification_approval: 3, column_mapping_approval: 3,
  step_3_semantic_mapping: 4, field_mapping: 5,
  step_5_preprocess: 6, step_7_hierarchy: 7, hierarchy: 8,
  step_8_output_generation: 9, write: 10, final_confirmation: 10
};

// `current_step` counts the pipeline's own steps, which skip the two semantic nodes: step 5 is
// preprocessing (node 6), step 8 output generation (node 9), step 9 the write. Read literally
// against node ids it lands one node early - on 24 Sep 2026 a write in progress highlighted
// "Verify hierarchy" with every node above it ticked.
const NODE_BY_STEP = { 0: 1, 1: 1, 2: 2, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9, 9: 10 };

// Which tracker row the run is on now.
export function currentNode(doc) {
  if (!doc) return 0;
  const gate = String(doc.pending_gate_type || '').toLowerCase();
  if (NODE_BY_PAUSE[gate]) return NODE_BY_PAUSE[gate];
  const step = Number(doc.current_step || 0);
  let n = NODE_BY_STEP[step] || Math.min(Math.max(step, 1), NODES.length);
  // Running past a node the service has already ticked means the next one is under way -
  // after the write gate is confirmed the step still reads 8, and node 9 is complete.
  const done = new Set((Array.isArray(doc.nodes) ? doc.nodes : [])
    .filter((x) => String(x.status || '').toLowerCase() === 'complete').map((x) => x.node_id));
  while (done.has(n) && n < NODES.length) n += 1;
  return n;
}

// How long to wait before the next status read. A running node moves quickly; a human gate
// only changes when THIS page answers it (or another tab does), so it is read slowly.
export function pollDelay(status) {
  const s = String(status || '').toLowerCase();
  if (isTerminal(s)) return 0;
  if (s === 'step_paused') return 1500;
  if (s === 'awaiting_review') return 8000;
  return 2500;
}

// A staged file the pipeline can take. Judged on the name — a browser's `type` for .csv is
// blank on some platforms and "application/vnd.ms-excel" on others.
export const isSpreadsheet = (f) => /\.(csv|tsv|xlsx|xlsm|xls)$/i.test(String((f && f.name) || ''));

// A request to SEE the runs, matched on the WHOLE normalised message and never a substring.
// "why did that migration fail?" contains the word and is a question for the orchestrator;
// these six phrases are the only ones that can only mean "show me the list". The same
// discipline as chatCases.js's CC_YES, and for the same reason: a substring match on a
// word that appears in ordinary questions steals them.
const MG_LIST = new Set([
  'migrations', 'my migrations', 'show my migrations', 'show me my migrations',
  'recent migrations', 'list migrations'
]);
export const mgIsListRequest = (text) =>
  MG_LIST.has(String(text || '').trim().toLowerCase().replace(/[.!?\s]+$/, ''));

export const fmtBytes = (n) => n < 1024 ? n + ' B' : n < 1048576 ? Math.round(n / 1024) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
export const shortId = (id) => String(id || '').slice(0, 8);

// What the run is labelled with. The service takes any string and uses it to pick its
// alias pack, so a field left blank — or left as spaces — is 'Custom', not ''.
// What "moving" means for a run: the status it reports, the node it is on, how many nodes
// have finished, and which gate it waits at. A pipeline that is working changes one of
// these within seconds; one that has been abandoned changes none of them ever.
export function progressKey(doc) {
  if (!doc) return '';
  const nodes = Array.isArray(doc.nodes) ? doc.nodes : [];
  const done = nodes.filter((n) => String(n.status || '') === 'complete').length;
  // progress_pct is the heartbeat of a long step: the output step's uploads and the write
  // step's batches beat it a few times a minute (svc-ai-schema-mapper graph/progress_beat.py).
  return [doc.status, doc.current_step, done, doc.pending_gate_type || '', doc.progress_pct == null ? '' : doc.progress_pct].join('|');
}

// How long a run may report the same thing before the card stops calling it work. Short
// steps finish in seconds and long ones beat progress_pct as they go, so minutes of silence
// is a stall — see the ARQ-worker case in test/chatMigration.test.mjs.
export const STALL_AFTER_MS = 3 * 60 * 1000;

export const cmmsName = (v) => (String(v == null ? '' : v).trim() || 'Custom');
// The column name the migration service will actually create. schema_write_node normalises
// and then validates the same way before it will build any DDL from it, so showing the typed
// text would show a name that is not the one made. Kept in step with _safe_identifier there.
export function safeColumn(raw) {
  const norm = String(raw == null ? '' : raw).trim().toLowerCase()
    .replace(/[ -]/g, '_').replace(/[^a-z0-9_]/g, '');
  return /^[a-z_][a-z0-9_]{0,62}$/.test(norm) ? norm : '';
}


// "3 min ago" for the recent-runs list; absolute past a day, since the list spans weeks.
export function whenLabel(iso, now) {
  if (!iso) return '';
  const t = new Date(iso).getTime();
  if (isNaN(t)) return '';
  const d = Math.max(0, ((now || Date.now()) - t) / 1000);
  if (d < 60) return 'just now';
  if (d < 3600) return Math.round(d / 60) + ' min ago';
  if (d < 86400) return Math.round(d / 3600) + ' h ago';
  return new Date(iso).toISOString().slice(0, 10);
}

// The gate the status document is stopped at, in one word the screen can switch on:
//   'running'  the service is working; nothing to do but wait
//   'step'     a node finished and waits for /advance (auto-continued unless switched off)
//   'gate'     a human gate — pending_gate_type names it
//   'done' / 'failed'
export function runKind(doc) {
  if (!doc) return 'running';
  const s = String(doc.status || '').toLowerCase();
  if (s === 'complete') return 'done';
  if (s === 'failed' || s === 'ddl_failed' || s === 'cancelled') return 'failed';
  if (s === 'awaiting_review') return 'gate';
  if (s === 'step_paused') return 'step';
  return 'running';
}

// What each gate asks, in the reader's words, and one sentence on what deciding it does. The
// card opens every gate with this question rather than the pipeline's step name: the person
// answering is a property or FM data owner, not the engineer who built the pipeline, and
// "B8.1 Primary-key detection" tells them nothing about what they are being asked. The codes
// and the analysis behind each answer are still on the card, under "How we worked this out".
export const GATE_TEXT = {
  pk_approval: ['Which column identifies each row?', 'We picked an ID column (primary key) for every table. Change any pick that is wrong — combine columns when no single one is unique, or let Plenum generate an ID.'],
  unique_table_approval: ['Are these the right tables?', 'Each table should hold one kind of record. Sheets with the same columns were merged into one table. Check nothing is missing or doubled before the tables are matched to Plenum.'],
  pre_semantic: ['Where should each sheet go?', 'Each sheet is matched to a Plenum table. Check the guesses — a sheet with no match becomes a new table.'],
  pre_semantic_columns: ['Check the column matches', 'Confident matches are approved already. Send any you doubt to AI matching, or pick a different target column.'],
  classification_approval: ['How are your tables linked?', 'A column that points at another table’s ID becomes an enforced link. Repeated values, like trades, become a lookup list. Change or leave out any of them.'],
  column_mapping_approval: ['Where does each column land?', 'Every column is matched to a column in Plenum. Keep the match, pick another, or add it as a new column.'],
  field_mapping: ['Review the matches the AI wasn’t sure about', 'Accept a match, pick a better column, or reject the field. Fields nothing matched become new columns unless you choose otherwise.'],
  hierarchy: ['How do your records nest?', 'These links say what belongs to what — assets in sites, work orders on assets. Confirm, change or reject each one.'],
  write: ['Ready to write to Plenum', 'Nothing has been written yet. Writing adds these rows to the live database and cannot be undone.'],
  final_confirmation: ['Ready to write to Plenum', 'Nothing has been written yet. Writing adds these rows to the live database and cannot be undone.']
};

// The six stages the card's stepper shows. Ten pipeline nodes and eight gates are the
// service's view; a person needs to know how far along the whole job is and what is left.
export const STAGES = [
  { key: 'read', label: 'Read file' },
  { key: 'keys', label: 'Keys & tables' },
  { key: 'match', label: 'Match columns' },
  { key: 'review', label: 'Review fields' },
  { key: 'links', label: 'Relationships' },
  { key: 'write', label: 'Write' }
];
const STAGE_BY_NODE = { 1: 0, 2: 2, 3: 2, 4: 3, 5: 3, 6: 4, 7: 4, 8: 4, 9: 5, 10: 5 };

// Which stage the run is in, and how each stage reads.
export function stagesFor(doc) {
  const kind = runKind(doc);
  const gate = String((doc && doc.pending_gate_type) || '').toLowerCase();
  let at = !doc ? 0 : (gate === 'pk_approval' || gate === 'unique_table_approval') ? 1 : (STAGE_BY_NODE[currentNode(doc)] ?? 0);
  // Past the file's own node with nothing asked yet, the keys are next.
  const n1 = doc && Array.isArray(doc.nodes) ? doc.nodes.find((x) => x && x.node_id === 1) : null;
  if (at === 0 && n1 && String(n1.status || '').toLowerCase() === 'complete' && kind !== 'step') at = 1;
  return STAGES.map((st, i) => ({
    key: st.key, label: st.label,
    state: kind === 'done' || i < at ? 'done' : i > at ? 'todo' : kind === 'failed' ? 'failed' : kind === 'gate' ? 'needs' : 'working'
  }));
}

// ── the primary-key gate ────────────────────────────────────────────────────────────
//
// A key is a LIST of columns. One column is a natural key, several a composite key, none a
// surrogate (the service mints _udr_id). The gate takes {table: [cols]} and reads an empty list
// as "surrogate approved" (pk_confirmation.normalize_pk_overrides); it used to be sent the
// first detected column alone, which cut Asset_Readings' asset_code + reading_type +
// recorded_at down to asset_code — a key that does not hold.

// _udr_id is the surrogate the service adds itself; it is never a column the reader picks.
export const detectedPk = (info) => ((info && info.detected_pk) || []).filter((c) => c && c !== '_udr_id');

// A decision as the list the gate takes. A bare column (or the old surrogate sentinel) is
// still read, so a decision held in either shape means the same key.
const pkList = (v) => Array.isArray(v)
  ? v.filter((c) => c && c !== '_udr_id')
  : (v == null || v === '' || v === '__surrogate__' || v === '_udr_id') ? [] : [String(v)];

export const chosenPk = (table, info, dec) =>
  (dec && dec[table] !== undefined) ? pkList(dec[table]) : detectedPk(info);

const two = (x) => (typeof x === 'number' && isFinite(x)) ? x.toFixed(2) : '—';

// The rows of the primary-key table, as CAFM Web draws them (EditablePkDetection in
// migration-metadata-view.tsx). Tables the service found to be duplicates of each other
// (table_resolution.duplicate_tables — Vendors + Vendor_Contracts) are ONE row under the
// group's label, and a key chosen on that row is the key of every member.
export function pkRows(payload, dec) {
  const conf = (payload && payload.pk_confirmation) || {};
  const groups = (((payload && payload.table_resolution) || {}).duplicate_tables || {}).groups || [];
  const groupOf = {};
  groups.forEach((g) => { (Array.isArray(g && g.tables) ? g.tables : []).forEach((t) => { groupOf[t] = g; }); });
  const seen = new Set();
  const rows = [];
  Object.keys(conf).forEach((t) => {
    const g = groupOf[t];
    if (!g) { rows.push({ key: t, label: t, members: [t] }); return; }
    const gk = g.tables.join('|');
    if (seen.has(gk)) return;
    seen.add(gk);
    const rep = g.tables.find((x) => conf[x]) || t;
    rows.push({ key: rep, label: g.label || rep, members: g.tables.slice() });
  });
  return rows.map((r) => {
    const info = conf[r.key] || {};
    const cols = Array.isArray(info.columns) ? info.columns : [];
    const stat = (c) => cols.find((x) => x.column === c) || {};
    const detected = detectedPk(info);
    const selected = chosenPk(r.key, info, dec);
    const changed = selected.length !== detected.length || selected.some((c, i) => c !== detected[i]);
    // The key is only as strong as its weakest column: the lowest uniqueness, the highest nulls.
    const uniq = selected.length ? Math.min(...selected.map((c) => Number(stat(c).uniqueness) || 0)) : 1;
    const nulls = selected.length ? Math.max(...selected.map((c) => Number(stat(c).null_rate) || 0)) : 0;
    return {
      key: r.key, label: r.label, members: r.members, isGroup: r.members.length > 1,
      kind: selected.length === 0 ? 'surrogate' : selected.length > 1 ? 'composite' : 'natural',
      selected: selected, changed: changed,
      chips: selected.map((c) => ({ column: c, qualifies: !!stat(c).qualifies })),
      available: cols.filter((c) => c && c.column && c.column !== '_udr_id' && selected.indexOf(c.column) < 0)
        .map((c) => ({ column: c.column, qualifies: !!c.qualifies })),
      uniqueness: two(uniq), nullRate: two(nulls),
      invalid: selected.length > 0 && (uniq < 1 || nulls > 0)
    };
  });
}

// ── the ingest report ───────────────────────────────────────────────────────────────
//
// Node 1's step pause (ingest_node.write_step_pause: rows, columns, format, nan_report) or
// Node 1's own output (row_count, column_count, detected_format, nan_report) — the same report
// under two sets of names, read as CAFM Web's Node1Ingest reads it. A missing total is a dash,
// never a zero, and a node output the poll left out (`_omitted`, over 16 KB) is no report.
const num = (v) => (typeof v === 'number' && isFinite(v)) ? v : null;
const count = (n) => Number(n).toLocaleString('en-GB');
const plural = (n, one) => count(n) + ' ' + one + (n === 1 ? '' : 's');

export function ingestReport(src) {
  if (!src || typeof src !== 'object' || src._omitted) return null;
  const sum = (src.overall_summary && typeof src.overall_summary === 'object') ? src.overall_summary : {};
  const rows = num(src.rows) ?? num(src.row_count) ?? num(sum.total_rows);
  const cols = num(src.columns) ?? num(src.column_count) ?? num(sum.total_columns);
  const fmtv = [src.format, src.detected_format, sum.detected_format].find((x) => typeof x === 'string' && x) || null;
  const nr = (src.nan_report && typeof src.nan_report === 'object') ? src.nan_report : null;
  if (rows === null && cols === null && !fmtv && !nr) return null;

  let nan = null;
  if (nr) {
    const cells = num(nr.total_nan_cells) || 0;
    const tables = (nr.tables && typeof nr.tables === 'object') ? nr.tables : {};
    nan = {
      clean: cells === 0,
      summary: plural(cells, 'value') + ' · ' + plural(num(nr.total_rows_with_nan) || 0, 'row') + ' · ' + plural(num(nr.columns_with_nan) || 0, 'column'),
      // Every table, the clean ones included — "no null/NaN (0)" says it was checked, not skipped.
      tables: Object.entries(tables).filter(([, t]) => t && typeof t === 'object').map(([name, t]) => {
        const clean = (num(t.nan_cells) || 0) === 0;
        const withNan = num(t.rows_with_nan) || 0;
        return {
          name: name, clean: clean,
          badge: clean ? 'no null/NaN (0)' : plural(withNan, 'row') + ' with null/NaN',
          columns: clean ? [] : Object.entries(t.columns || {}).map(([c, k]) => ({ name: c, count: num(k) || 0 }))
        };
      })
    };
  }
  return {
    label: (typeof src.label === 'string' && src.label) || 'Ingest & Configure',
    tiles: [
      { label: 'Total rows', value: rows === null ? '—' : count(rows) },
      { label: 'Total columns', value: cols === null ? '—' : count(cols) },
      { label: 'Format', value: fmtv || '—' }
    ],
    nan: nan,
    summary: [rows === null ? '' : plural(rows, 'row'), cols === null ? '' : plural(cols, 'column'), fmtv || '',
      !nan ? '' : nan.clean ? 'no null/NaN values' : count(num(nr.total_nan_cells) || 0) + ' null/NaN values'].filter(Boolean).join(' · ')
  };
}

// Node 2's unmatched fields for the open run: as kept from its step pause, else from Node 2's
// own output when the poll carried it (it is left out over 16 KB).
function unresolvedFor(s) {
  if (s.mgUnresolved && s.mgUnresolved.id === s.mgId) return s.mgUnresolved.byTable;
  const n2 = s.mgStatus && Array.isArray(s.mgStatus.nodes) ? s.mgStatus.nodes.find((n) => n && n.node_id === 2) : null;
  const out = n2 && n2.output && !n2.output._omitted ? n2.output.unresolved_by_table : null;
  return out && typeof out === 'object' ? out : null;
}

// The step a pause is at, as the service names it in pending_gate_type (step_1_ingest, …).
const pauseKey = (doc) => String((doc && doc.pending_gate_type) || '').toLowerCase();

// The body a gate is answered with, from what the gate showed plus the reader's decisions
// (`dec`: {key: value}, keys as the view model below sets them). With no decisions at all
// this is "accept everything the pipeline proposed". Every gate but the first and the last is
// CAFM Web's answer, built in logic/migrationGates.js; `ctx.unresolved` is Node 2's list of
// fields no rule matched, kept from its step pause for the second pre-semantic pass.
export function defaultGateBody(gateType, payload, dec, ctx) {
  const p = payload || {};
  const d = dec || {};
  switch (String(gateType || '').toLowerCase()) {
    case 'pk_approval': {
      const out = {};
      Object.entries(p.pk_confirmation || {}).forEach(([table, info]) => {
        out[table] = chosenPk(table, info, d);
      });
      return { pk_overrides: out };
    }
    case 'unique_table_approval':
      return { approved: true };
    case 'pre_semantic':
      return preSemanticPhase(p) === 'tables' ? tableRoutingBody(p, d) : columnMatchingBody(p, d, ctx && ctx.unresolved);
    case 'classification_approval':
      return classificationBody(p, d);
    case 'column_mapping_approval':
      return columnMappingBody(p, d);
    case 'field_mapping':
      return fieldMappingBody(p, d);
    case 'hierarchy':
      return hierarchyBody(p, d);
    case 'write':
    case 'final_confirmation':
      return { confirmed: d.confirm === true };
    default:
      return {};
  }
}

// Tracker rows: the service's own node list when it has one, the fixed nine otherwise.
export function shapeNodes(doc) {
  const live = Array.isArray(doc && doc.nodes) ? doc.nodes : [];
  const byId = {};
  live.forEach((n) => { byId[n.node_id] = n; });
  const current = currentNode(doc);
  const kind = runKind(doc);
  return NODES.map((n) => {
    const l = byId[n.id];
    let status = l ? String(l.status || '').toLowerCase() : 'pending';
    if (current === n.id && kind !== 'done' && kind !== 'failed' && status !== 'complete') {
      status = kind === 'gate' ? 'awaiting_review' : kind === 'step' ? 'paused' : 'running';
    }
    if (kind === 'done') status = 'complete';
    if (kind === 'failed' && current === n.id && status !== 'complete') status = 'failed';
    return {
      id: n.id, name: (l && l.node_name) || n.name, blurb: n.blurb,
      status: status,
      outcome: (l && l.outcome) || '',
      ms: l && typeof l.duration_ms === 'number' ? l.duration_ms : null,
      logs: (l && Array.isArray(l.logs)) ? l.logs : []
    };
  });
}

// Every scalar a step-pause payload carries, as label/value pairs for the "what this node
// produced" list. Urls and nested objects are left to the artefact links / the node log.
export function scalarFacts(payload) {
  return Object.entries(payload || {})
    .filter(([k, v]) => k !== 'node' && k !== 'label' && !/_url$/.test(k) && (typeof v === 'number' || typeof v === 'boolean' || (typeof v === 'string' && v.length < 80)))
    .map(([k, v]) => ({ label: k.replace(/_/g, ' '), value: typeof v === 'boolean' ? (v ? 'yes' : 'no') : String(v) }));
}

// What needs a look at the open gate, in a few words each — the line under the question.
// Only counts the views already made; says nothing where there is nothing to say.
export function gateAttention(gate, g) {
  const n = (x, one, many) => x + ' ' + (x === 1 ? one : many);
  const out = [];
  const add = (count, label, tone) => { if (count) out.push({ label: label, tone: tone }); };
  if (gate === 'pk_approval' && g.pkRows) {
    const bad = g.pkRows.filter((r) => r.invalid).length;
    add(bad, n(bad, 'key won’t hold', 'keys won’t hold') + ' — not unique or has blanks', 'warn');
    add(g.pkRows.length - bad, n(g.pkRows.length - bad, 'table looks right', 'tables look right'), 'ok');
  } else if (gate === 'unique_table_approval' && g.utView) {
    add(g.utView.unique.length, n(g.utView.unique.length, 'table', 'tables'), 'neutral');
    add(g.utView.dupGroups.length, n(g.utView.dupGroups.length, 'set of duplicate sheets merged', 'sets of duplicate sheets merged'), 'warn');
    add(g.utView.merges.length, n(g.utView.merges.length, 'repeated column removed', 'repeated columns removed'), 'neutral');
  } else if (gate === 'pre_semantic' && g.psView && g.psPhase === 'tables') {
    const by = (m) => g.psView.rows.filter((r) => r.match === m).length;
    add(by('none'), n(by('none'), 'sheet needs a destination', 'sheets need a destination'), 'risk');
    add(by('semantic'), n(by('semantic'), 'best guess to check', 'best guesses to check'), 'warn');
    add(by('new'), n(by('new'), 'new table', 'new tables'), 'accent');
    add(by('exact'), n(by('exact'), 'exact match', 'exact matches'), 'ok');
  } else if (gate === 'pre_semantic' && g.psView) {
    const st = Object.fromEntries(g.psView.stats.map((x) => [x.label, x.value]));
    add(st['Auto → Semantic'], n(st['Auto → Semantic'], 'field had no match — goes to AI matching', 'fields had no match — go to AI matching'), 'warn');
    add(st['→ Semantic (you)'], n(st['→ Semantic (you)'], 'sent to AI matching by you', 'sent to AI matching by you'), 'accent');
    add(st['T1 Approved'], n(st['T1 Approved'], 'match approved', 'matches approved'), 'ok');
  } else if (gate === 'classification_approval' && g.clView) {
    add(g.clView.fk.length, n(g.clView.fk.length, 'link between tables', 'links between tables'), 'neutral');
    add(g.clView.shared.length, n(g.clView.shared.length, 'lookup list', 'lookup lists'), 'neutral');
  } else if (gate === 'column_mapping_approval' && g.cmView) {
    const rows = [].concat(...g.cmView.tables.map((t) => t.rows));
    const sug = rows.filter((r) => /suggest/i.test(r.outcome)).length;
    const fresh = rows.filter((r) => !r.target).length;
    add(sug, n(sug, 'suggestion to confirm', 'suggestions to confirm'), 'warn');
    add(fresh, n(fresh, 'new column', 'new columns'), 'accent');
    add(rows.length - sug - fresh, n(rows.length - sug - fresh, 'column matched', 'columns matched'), 'ok');
  } else if (gate === 'field_mapping' && g.fmView) {
    const c = Object.fromEntries(g.fmView.counters.map((x) => [x.label, x.value]));
    add(c.Flagged, n(c.Flagged, 'uncertain match', 'uncertain matches'), 'warn');
    add(c.Unmappable, n(c.Unmappable, 'field with no match', 'fields with no match'), 'risk');
    add(c['Auto accepted'], n(c['Auto accepted'], 'accepted already', 'accepted already'), 'ok');
  } else if (gate === 'hierarchy' && g.hiView) {
    add(g.hiView.loops.length, n(g.hiView.loops.length, 'loop to break', 'loops to break'), 'risk');
    add(g.hiView.review.length, n(g.hiView.review.length, 'link to confirm', 'links to confirm'), 'neutral');
    const orphans = (g.hiView.stats.find((x) => x.label === 'Orphaned records') || {}).value || 0;
    add(orphans, n(orphans, 'record with no parent', 'records with no parent'), 'warn');
  }
  return out;
}

export const migrationMethods = {
  // ── navigation ────────────────────────────────────────────────────────────────────
  //
  // There is no page. A migration is answered in the conversation that started it:
  // openChat() puts the transcript up and CAFM Web's panel (src/cafm) renders the open gate under it.
  mgShow() {
    this.openChat();
    this.mgListLoad();
    if (this.state.mgId) this.mgPoll(true);
  },

  // Opens one run in the conversation — from the recent list, from a chat reply that
  // started one, or on reload. A different run than before drops the previous document
  // and decisions. A run already open is NOT re-opened into a fresh chat: mgPoll alone
  // refreshes it, so answering a gate never scrolls the transcript back to the top.
  mgOpen(id) {
    // The card renders in the Orchestrator's transcript and nowhere else, so a run opened
    // from a page that merely keeps a dock (Buildings, Home, the console) must land on the
    // chat — chatView() is true on those and would have left the gates rendered nowhere.
    // Already on the chat: only the state changes, so answering a gate never scrolls the
    // transcript back to the top or drops a form the dock was showing.
    if (this.state.view !== 'chat') this.openChat();
    this.mgAttach(id);
    this.mgPoll(true);
  },

  // Makes `id` the open run without reading it — mgOpen() and openSession() both go through
  // here, one to poll straight away, the other to let openChat() read it. Stamps the run on
  // the active conversation's record (sessions.js), which is what lets "+ New query" drop
  // the card and reopening the conversation bring it back.
  mgAttach(id) {
    const same = this.state.mgId === id;
    this.setState({
      mgId: id, mgError: '', mgArmed: false,
      mgStatus: same ? this.state.mgStatus : null,
      mgDec: same ? this.state.mgDec : {},
      mgOpenNodes: same ? this.state.mgOpenNodes : {},
      mgIngestOpen: same ? this.state.mgIngestOpen : false
    });
    if (!same) { clearTimeout(this._mgTimer); this._mgAdvanced = null; this._mgProgressKey = null; this._mgMovedAt = Date.now(); }
    // The card (cafm/hoistra-migration-wizard.tsx) owns every open run from this moment, not
    // from its mount effect a tick later: a step_paused answer that lands in between must not
    // have this poll continue the step alongside the card's own advance.
    this._mgExternal = true;
    if (typeof this.sessionBindMigration === 'function') this.sessionBindMigration(id);
  },

  // Drops the card and its poll. The run itself is untouched — it stays in the recent list
  // and on the record of the conversation that started it.
  mgDetach() {
    clearTimeout(this._mgTimer);
    this._mgAdvanced = null;
    if (!this.state.mgId && !this.state.mgStatus) return;
    this.setState({ mgId: null, mgStatus: null, mgError: '', mgDec: {}, mgArmed: false, mgOpenNodes: {} });
  },

  // Back to the upload panel.
  mgNew() {
    this.mgDetach();
    this.mgListLoad();
  },

  // ── the staged upload ─────────────────────────────────────────────────────────────
  mgPickFiles(fileList) {
    const add = Array.from(fileList || []);
    if (!add.length) return;
    const bad = add.filter((f) => !isSpreadsheet(f));
    if (bad.length) this.flash(bad.map((f) => f.name).join(', ') + (bad.length === 1 ? ' is not a CSV or Excel file.' : ' are not CSV or Excel files.'));
    const ok = add.filter(isSpreadsheet);
    if (!ok.length) return;
    this.setState((p) => {
      const have = new Set((p.mgFiles || []).map((f) => f.name + ':' + f.size));
      return { mgFiles: (p.mgFiles || []).concat(ok.filter((f) => !have.has(f.name + ':' + f.size))).slice(0, 20), mgError: '' };
    });
  },
  mgDropFile(i) { this.setState((p) => ({ mgFiles: (p.mgFiles || []).filter((f, j) => j !== i) })); },

  // The chat's staged spreadsheets ARE the migration. Pressing send with nothing typed
  // starts it here, and the gates open under the message that started them.
  //
  // No model is asked whether a .xlsx is a migration. There is nothing to judge, and
  // chatCases.js records what happened the one time a file-shaped message was left to the
  // router: it read the words "upload" and "file", chose the migration sub-agent for a
  // held contract, and answered "the file cannot be found in the system".
  //
  // Only the spreadsheets leave the tray. A PDF staged beside them is a document for the
  // next question, not part of this job.
  async mgStartFromChat() {
    const staged = (this.state.ccFiles || []).filter(isSpreadsheet);
    if (!staged.length || this.state.mgBusy || this.state.ccBusy) return;
    const names = staged.map((f) => f.name).join(', ');

    // THROUGH THE ORCHESTRATOR'S UPLOAD ROUTE, NOT STRAIGHT TO SCHEMA-MAPPER.
    //
    // Both routes start the same nine-node run, and the direct one is a shorter trip — it
    // is also the one that records nothing. svc-deepagents' /run-stateful-with-files takes
    // the building the composer has chosen and, for every file it drives (spreadsheets
    // included), calls bind_and_log, record_ingestion_audit and record_usage with it;
    // schema-mapper's /start-with-upload takes file, cmms_name and organization_id and has
    // nowhere to put a building. Going direct meant a migration bound to nothing and no row
    // in the ingestion audit trail — invisible, and only noticed because the composer
    // stopped offering to choose a building at all.
    //
    // No model decides anything here either: the route splits files by TYPE
    // (workers/ingest_batch_worker.py), and interactive_migration stops the run at its
    // first gate instead of approving them all server-side. Five files or fewer run inline,
    // so the reply carries ingested_migration_ids and the card opens at gate 1.
    await this.ccAsk('Migrate ' + names);
    if (this.state.mgId) return;

    // The orchestrator answered, but with nothing that started a run. Only a definitively
    // unreachable upstream is retried: a TIMEOUT may still be completing server-side, and
    // a second upload would be a second migration of the same file — which is exactly what
    // ccAsk's own timeout wording tells the reader not to do by hand.
    const chat = this.state.ccChat || [];
    const last = chat[chat.length - 1] || {};
    if (!last.unreachable) return;

    // Straight to schema-mapper, so a dead orchestrator does not stop a migration
    // altogether — and say plainly what that costs.
    this.setState({ mgBusy: 'Uploading…', mgError: '' });
    this._mgAdvanced = null;
    try {
      const r = await schemaMapperApi.start(staged, cmmsName(this.state.mgCmms), currentOrgId() || undefined);
      const id = r && r.migration_id;
      if (!id) throw new Error('the service accepted the upload but returned no migration id');
      this.setState((p) => ({
        mgBusy: '',
        ccChat: (p.ccChat || []).concat([{
          role: 'bot', isNote: true,
          text: names + ' is migrating as run ' + shortId(id) + ', started directly against the mapper because the orchestrator could not be reached. Every gate is below. It is not bound to a building and leaves no row in the ingestion audit trail — the orchestrator is what writes those.'
        }])
      }));
      this.mgOpen(id);
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mgBusy: '' }); return; }
      this.setState((p) => ({
        mgBusy: '',
        ccChat: (p.ccChat || []).concat([{
          role: 'bot', isNote: true, error: true,
          text: names + ' did not start a migration: ' + ((e && e.message) || String(e))
        }])
      }));
    }
  },

  // The composer's migrate strip and the dock's both land here.
  mgFromChat() { return this.mgStartFromChat(); },

  // "migrations" — the runs this company has started, as a card in the transcript.
  async mgAnswerList() {
    await this.mgListLoad();
    const n = (this.state.mgList || []).length;
    this.setState((p) => ({
      ccChat: (p.ccChat || []).concat([{
        role: 'bot', mgList: true,
        text: n
          ? 'The runs this company has started. Open one to pick its gates back up.'
          : 'No migrations yet. Attach a CMMS export below and press send — the run opens here at its first gate.'
      }])
    }));
  },

  async mgStart() {
    const files = this.state.mgFiles || [];
    if (!files.length || this.state.mgBusy) return;
    this.setState({ mgBusy: 'Uploading…', mgError: '' });
    try {
      const r = await schemaMapperApi.start(files, cmmsName(this.state.mgCmms), currentOrgId() || undefined);
      const id = r && r.migration_id;
      if (!id) throw new Error('the service accepted the upload but returned no migration id');
      this.setState({ mgBusy: '', mgFiles: [] });
      this.mgOpen(id);
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mgBusy: '' }); return; }
      const msg = (e && e.message) || String(e);
      this.setState({ mgBusy: '', mgError: 'The upload did not start a migration: ' + msg });
    }
  },

  // ── the open run ──────────────────────────────────────────────────────────────────
  // What the platform did with a finished run's contract terms, invoices and telemetry. The
  // service reads the run itself (its sweep); the page asks where that stands, follows it
  // while it reads, and only asks for the read when nothing has picked the run up yet.
  mgLoadExtras(id, askIfWaiting = true) {
    if (!id) return;
    clearTimeout(this._mgExtrasTimer);
    if (!this.state.mgExtras) this.setState({ mgExtras: { loading: true } });
    opsApi.migrationWorkbookExtrasStatus(id)
      .then((st) => {
        if (this.state.mgId !== id) return;
        if (st && st.status === 'waiting' && askIfWaiting) return this.mgRunExtras(id);
        this.setState({ mgExtras: st || {} });
        if (st && (st.status === 'running' || st.status === 'waiting')) {
          this._mgExtrasTimer = setTimeout(() => this.mgLoadExtras(id, false), 8000);
        }
      })
      .catch((e) => { if (this.state.mgId === id) this.setState({ mgExtras: { error: (e && e.message) || String(e) } }); });
  },
  // Ask for the read now - "Read now", "Try again", "Read again" (force). Safe to repeat: an
  // invoice already verified is skipped and a re-read contract keeps the PM's rulings.
  mgRunExtras(id, force = false) {
    if (!id) return;
    clearTimeout(this._mgExtrasTimer);
    this.setState({ mgExtras: { status: 'running' } });
    opsApi.migrationWorkbookExtras(id, { force })
      .then((res) => {
        if (this.state.mgId !== id) return;
        this.setState({ mgExtras: res || {} });
        if (res && res.status === 'running') this._mgExtrasTimer = setTimeout(() => this.mgLoadExtras(id, false), 8000);
      })
      .catch((e) => { if (this.state.mgId === id) this.setState({ mgExtras: { error: (e && e.message) || String(e) } }); });
  },

  // One read of the status document, then the next is scheduled by what it said. Reads only
  // while this page is showing — the document is large and a gate does not move on its own.
  async mgPoll(immediate) {
    clearTimeout(this._mgTimer);
    const id = this.state.mgId;
    if (!id) return;
    if (this._mgPolling) { if (immediate) this._mgRepoll = true; return; }
    this._mgPolling = true;
    if (!this.state.mgStatus) this.setState({ mgLoading: true });
    let doc = null;
    try {
      doc = await schemaMapperApi.status(id);
    } catch (e) {
      this._mgPolling = false;
      if (isStaleScope(e) || this.state.mgId !== id) return;
      this.setState({ mgLoading: false, mgError: 'Could not read the migration: ' + ((e && e.message) || String(e)) });
      if (this.state.view === 'chat') this._mgTimer = setTimeout(() => this.mgPoll(), 6000);
      return;
    }
    this._mgPolling = false;
    if (this.state.mgId !== id) return;
    const prevGate = this.state.mgStatus ? this.state.mgStatus.pending_gate_type : null;
    const gateChanged = (doc.pending_gate_type || null) !== (prevGate || null);
    // The stall clock: restarted whenever the document says something new, so the card can
    // tell "still working" from "nothing is coming".
    const moved = progressKey(doc);
    if (moved !== this._mgProgressKey) { this._mgProgressKey = moved; this._mgMovedAt = Date.now(); }
    // The ingest report is on screen only while its pause lasts — under a second once
    // auto-continue has advanced it — and Node 1's output is usually too big for the poll to
    // carry afterwards. So it is kept here, the moment it is seen, for the gates after it.
    const ingest = runKind(doc) === 'step' && pauseKey(doc) === 'step_1_ingest' ? ingestReport(doc.pending_gate_payload) : null;
    // Node 2's fields no rule matched — the second pre-semantic pass lists them, and they are in
    // the gate payload only as far as the rules had a suggestion. The pause carries them all.
    const n2 = runKind(doc) === 'step' && /^step_2_deterministic/.test(pauseKey(doc)) && doc.pending_gate_payload ? doc.pending_gate_payload.unresolved_by_table : null;
    this.setState({
      mgStatus: doc, mgLoading: false, mgError: '', mgLoadedAt: Date.now(),
      mgDec: gateChanged ? {} : this.state.mgDec,
      mgArmed: gateChanged ? false : this.state.mgArmed,
      ...(ingest ? { mgIngest: { id: id, report: ingest } } : {}),
      ...(n2 && typeof n2 === 'object' ? { mgUnresolved: { id: id, byTable: n2 } } : {})
    });
    if (this._mgRepoll) { this._mgRepoll = false; return this.mgPoll(true); }
    // A finished run: once per run, show what the platform did with its contract terms,
    // invoices and telemetry - reading it now only if nothing has picked it up.
    if (String(doc.status || '') === 'complete' && this._mgExtrasFor !== id) {
      this._mgExtrasFor = id;
      this.setState({ mgExtras: null });
      this.mgLoadExtras(id);
    }
    // A step pause is the card's to continue (cafm/hoistra-migration-wizard.tsx): it moves the
    // ingest and deterministic pauses on itself and offers Continue for the rest, exactly as
    // CAFM Web does. This read never calls /advance — not before the card's mount effect has
    // run, and not from a timer that fires after the card has gone — it only keeps the
    // conversation's own view of the run current, slowly while the card polls for itself.
    const delay = this._mgExternal ? (isTerminal(doc.status) ? 0 : 20000) : pollDelay(doc.status);
    if (delay && this.state.view === 'chat') this._mgTimer = setTimeout(() => this.mgPoll(), delay);
  },

  async mgAdvance() {
    const id = this.state.mgId;
    if (!id || this.state.mgBusy) return;
    this.setState({ mgBusy: 'Continuing…', mgError: '' });
    try {
      await schemaMapperApi.advance(id);
      this.setState({ mgBusy: '' });
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mgBusy: '' }); return; }
      // Refused (409: the run that paused is still finishing) - forget this pause so the next
      // read continues it, instead of waiting on a pause the page believes it already advanced.
      this._mgAdvanced = null;
      this.setState({ mgBusy: '', mgError: 'The step did not continue: ' + ((e && e.message) || String(e)) });
    }
    this.mgPoll(true);
  },

  // Cancels the open run. Two presses: the first arms the button for five seconds, the second
  // cancels - stopping a migration is not something one stray click should do. The service
  // refuses a run of another company, and a finished run has nothing to cancel.
  async mgCancel(id) {
    const target = id || this.state.mgId;
    if (!target || this.state.mgBusy) return;
    if (this.state.mgCancelArmed !== target) {
      clearTimeout(this._mgCancelTimer);
      this.setState({ mgCancelArmed: target });
      this._mgCancelTimer = setTimeout(() => {
        if (this.state.mgCancelArmed === target) this.setState({ mgCancelArmed: null });
      }, 5000);
      return;
    }
    clearTimeout(this._mgCancelTimer);
    this.setState({ mgCancelArmed: null, mgBusy: target === this.state.mgId ? 'Cancelling…' : '', mgError: '' });
    try {
      await schemaMapperApi.cancel(target);
      this.setState({ mgBusy: '' });
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mgBusy: '' }); return; }
      // Nothing changed, so nothing is re-read: a re-read would clear the reason just shown.
      this.setState({ mgBusy: '', mgError: 'The migration was not cancelled: ' + ((e && e.message) || String(e)) });
      return;
    }
    if (target === this.state.mgId) this.mgPoll(true);
    this.mgListLoad();
  },

  // Answers the open gate. `body` overrides the default built from the decisions — the
  // write gate passes {confirmed} explicitly.
  async mgSubmitGate(body) {
    const id = this.state.mgId;
    const doc = this.state.mgStatus;
    if (!id || !doc || runKind(doc) !== 'gate' || this.state.mgBusy) return;
    const gate = doc.pending_gate_type;
    const sent = body || defaultGateBody(gate, doc.pending_gate_payload, this.state.mgDec, { unresolved: unresolvedFor(this.state) });
    this.setState({ mgBusy: 'Submitting…', mgError: '' });
    try {
      await schemaMapperApi.gate(id, gate, sent);
      this.setState({ mgBusy: '', mgDec: {}, mgArmed: false });
    } catch (e) {
      if (isStaleScope(e)) { this.setState({ mgBusy: '' }); return; }
      this.setState({ mgBusy: '', mgError: 'The gate did not accept the decision: ' + ((e && e.message) || String(e)) });
    }
    this.mgPoll(true);
  },

  mgDecide(key, value) {
    this.setState((p) => {
      const next = Object.assign({}, p.mgDec || {});
      if (value === undefined || value === null) delete next[key]; else next[key] = value;
      return { mgDec: next };
    });
  },

  // One of the hierarchy gate's "Download metadata JSON" files, saved from the page.
  mgDownloadJson(file) {
    if (!file || typeof document === 'undefined' || typeof window === 'undefined' || !window.URL || !window.URL.createObjectURL) {
      return this.flash('Download needs a browser.');
    }
    const url = window.URL.createObjectURL(new Blob([file.text], { type: 'application/json;charset=utf-8' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = file.filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => window.URL.revokeObjectURL(url), 1000);
  },

  // CAFM Web's migration panel mounting and unmounting (cafm/hoistra-migration-wizard.tsx).
  mgCafmMounted(on) { this._mgExternal = !!on; },

  // Opens a migration the moment an upload starts it, as CAFM Web's orchestrator does
  // (use-deep-agent-orchestrator.ts): while the upload request is open, the session's
  // workspace is read every 1.5 s — one read in flight at a time, never a fixed interval — and
  // the first migration it lists that was not there when the upload began is opened. The upload
  // only answers once the run has reached its first gate, so waiting for the reply opened the
  // card with every step already done; opened now, the card shows each step as it happens.
  // Returns the stop function; the reply still opens the run if nothing was found.
  mgDiscoverDuringUpload(sessionId) {
    if (!sessionId) return () => {};
    let stopped = false;
    let timer = null;
    let baseline = null;
    const every = this._mgDiscoverMs || 1500;
    const idsOf = (ws) => (ws && Array.isArray(ws.migration_ids) ? ws.migration_ids.filter(Boolean).map(String) : []);
    const probe = async () => {
      if (stopped) return;
      try {
        const ids = idsOf(await deepAgentsApi.workspace(sessionId));
        if (stopped) return;
        if (baseline === null) {
          baseline = new Set(ids.concat(this.state.mgId ? [this.state.mgId] : []));
        } else {
          const fresh = ids.find((id) => !baseline.has(id));
          if (fresh) { stopped = true; this.mgOpen(fresh); return; }
        }
      } catch (e) {
        // Best effort — the reply still opens the run.
        if (baseline === null) baseline = new Set(this.state.mgId ? [this.state.mgId] : []);
      }
      if (!stopped) timer = setTimeout(probe, every);
    };
    probe();
    return () => { stopped = true; clearTimeout(timer); };
  },

  mgToggleNode(id) {
    this.setState((p) => {
      const o = Object.assign({}, p.mgOpenNodes || {});
      o[id] = !o[id];
      return { mgOpenNodes: o };
    });
  },

  // ── the recent-runs list ──────────────────────────────────────────────────────────
  async mgListLoad() {
    if (this._mgListLoading) return;
    this._mgListLoading = true;
    this.setState({ mgListLoading: true });
    try {
      const r = await schemaMapperApi.list(currentOrgId() || undefined, 12);
      this._mgListLoading = false;
      this.setState({ mgList: Array.isArray(r && r.migrations) ? r.migrations : [], mgListLoading: false, mgListError: '' });
    } catch (e) {
      this._mgListLoading = false;
      if (isStaleScope(e)) { this.setState({ mgListLoading: false }); return; }
      this.setState({ mgListLoading: false, mgListError: (e && e.message) || String(e) });
    }
  },

  // ── the view model ────────────────────────────────────────────────────────────────
  mgVals(s) {
    const doc = s.mgStatus;
    const kind = runKind(doc);
    const gate = doc && kind === 'gate' ? String(doc.pending_gate_type || '') : '';
    const payload = (doc && doc.pending_gate_payload) || {};
    const dec = s.mgDec || {};
    const busy = !!s.mgBusy;
    const nodes = shapeNodes(doc);
    const done = nodes.filter((n) => n.status === 'complete').length;
    const progress = kind === 'done' ? 100 : doc ? Math.round((done / nodes.length) * 100) : 0;
    const text = (gate === 'pre_semantic' && preSemanticPhase(payload) === 'columns' ? GATE_TEXT.pre_semantic_columns : GATE_TEXT[gate])
      || [gate.replace(/_/g, ' '), payload.instructions || ''];

    const pill = !doc ? { label: s.mgLoading ? 'Reading…' : 'Not loaded', tone: 'var(--color-neutral-500)', bg: 'var(--color-neutral-900)' }
      : kind === 'done' ? { label: 'Complete', tone: 'var(--st-ok)', bg: 'var(--st-ok-bg)' }
      : kind === 'failed' ? { label: String(doc.status).replace(/_/g, ' '), tone: 'var(--st-risk)', bg: 'var(--st-risk-bg)' }
      : kind === 'gate' ? { label: 'Awaiting your review', tone: 'var(--color-accent)', bg: 'var(--color-accent-900)' }
      : kind === 'step' ? { label: s.mgAuto ? 'Continuing…' : 'Paused after a step', tone: 'var(--color-neutral-300)', bg: 'var(--color-neutral-900)' }
      : { label: 'Running', tone: 'var(--color-accent)', bg: 'var(--color-accent-900)' };

    const decide = (k) => (v) => this.mgDecide(k, v);
    const seg = (options, chosen, key) => options.map((o) => ({
      label: o.label, value: o.value, on: chosen === o.value, tone: o.tone || 'var(--color-text)',
      pick: () => this.mgDecide(key, o.value)
    }));

    // Gate-specific rows. Each row carries its own controls so the screen stays markup only.
    let g = {};
    if (gate === 'pk_approval') {
      g.pkRows = pkRows(payload, dec).map((r) => {
        // A duplicate group's row is every member's key.
        const set = (cols) => r.members.forEach((m) => this.mgDecide(m, cols));
        return {
          ...r,
          add: (c) => { if (c) set(r.selected.concat([c])); },
          pickAdd: (e) => { const c = e.target.value; if (c) set(r.selected.concat([c])); },
          remove: (c) => set(r.selected.filter((x) => x !== c)),
          useSurrogate: () => set([])
        };
      });
    } else if (gate === 'unique_table_approval') {
      g.utView = uniqueTablesView(payload);
    } else if (gate === 'pre_semantic') {
      g.psPhase = preSemanticPhase(payload);
      if (g.psPhase === 'tables') {
        const v = tableRoutingView(payload, dec);
        g.psView = Object.assign({}, v, {
          rows: v.rows.map((r) => Object.assign({}, r, {
            // "+ New table…" keeps a name already typed; switching from an existing table starts blank.
            pick: (e) => {
              const val = e.target.value;
              this.mgDecide('rt:' + r.key, val === '__new_table__' ? { target: r.isNew ? r.target : '', isNew: true } : { target: val, isNew: false });
            },
            setName: (e) => this.mgDecide('rt:' + r.key, { target: e.target.value, isNew: true }),
            useSuggestion: r.suggestion ? () => this.mgDecide('rt:' + r.key, { target: r.suggestion.target, isNew: r.suggestion.isNew }) : null,
            pickTop: (table) => this.mgDecide('rt:' + r.key, { target: table, isNew: false })
          }))
        });
      } else {
        g.psView = columnMatchingView(payload, dec, unresolvedFor(s));
        g.psApproveAll = () => g.psView.tables.forEach((t) => t.rows.forEach((r) => this.mgDecide(r.key, 'approve')));
      }
    } else if (gate === 'classification_approval') {
      g.clView = classificationView(payload, dec);
    } else if (gate === 'column_mapping_approval') {
      g.cmView = columnMappingView(payload, dec);
    } else if (gate === 'field_mapping') {
      g.fmView = fieldMappingView(payload, dec);
      g.fmProblems = fieldMappingProblems(payload, dec);
      g.fmAcceptAll = () => g.fmView.tables.forEach((t) => t.flagged.forEach((r) => this.mgDecide(r.key, 'accept')));
    } else if (gate === 'hierarchy') {
      g.hiView = hierarchyView(payload, dec);
      g.hiTab = s.mgHierView || 'tree';
      g.hiSetTab = (v) => this.setState({ mgHierView: v });
      g.hiTreeOpen = s.mgHierOpen !== false;
      g.hiToggleTree = () => this.setState((q) => ({ mgHierOpen: q.mgHierOpen === false }));
      g.hiDownload = (kind) => this.mgDownloadJson(hierarchyExport(kind, payload, dec, s.mgId, new Date().toISOString()));
    } else if (gate === 'write' || gate === 'final_confirmation') {
      g.wrView = finalView(payload, doc);
      g.wrArmed = !!s.mgArmed;
    }

    // Artefacts a finished (or output-generated) run points at.
    const outputs = doc ? [
      ['Report (PDF)', doc.migration_report_url], ['JSON', doc.output_json_url], ['CSV / Excel', doc.output_csv_url],
      ['SQL', doc.output_sql_url], ['Structure (Markdown)', doc.output_structure_md_url]
    ].filter((x) => x[1]).map((x) => ({ label: x[0], href: x[1] })) : [];

    // What the step pause produced, for the "continue" card when auto-continue is off.
    const stepFacts = kind === 'step' ? scalarFacts(payload) : [];

    // The ingest report: live while its pause is open, then as kept at that pause, then from
    // Node 1's output when the poll carried it (a run reopened after the pause).
    const ingestLive = kind === 'step' && pauseKey(doc) === 'step_1_ingest' ? ingestReport(payload) : null;
    const node1 = doc && Array.isArray(doc.nodes) ? doc.nodes.find((n) => n && n.node_id === 1) : null;
    const ingest = ingestLive
      || (s.mgIngest && s.mgIngest.id === s.mgId ? s.mgIngest.report : null)
      || (doc ? ingestReport(node1 && node1.output) : null);

    // A step pause auto-continues, as CAFM Web's does: while it does, the step says so rather
    // than offering a button. A continue that failed hands the button back.
    const continuing = kind === 'step' && !!s.mgAuto && !s.mgError;

    // One primary action per state.
    // Each gate's own button, in CAFM Web's words. A routing with a sheet left untargeted cannot
    // be sent, and a field-mapping answer the service would refuse is stopped here with the
    // reason — human_review_node applies none of the decisions when one of them is bad.
    const gateLabel = {
      pk_approval: 'Confirm ID columns',
      unique_table_approval: 'Tables look right — continue',
      pre_semantic: g.psPhase === 'tables' ? 'Confirm destinations' : (g.psView && g.psView.submitLabel),
      classification_approval: g.clView && g.clView.submitLabel,
      column_mapping_approval: g.cmView && g.cmView.submitLabel,
      field_mapping: g.fmView && g.fmView.submitLabel,
      hierarchy: g.hiView && g.hiView.submitLabel
    }[gate] || 'Approve decisions and continue';
    const blocked = gate === 'pre_semantic' && g.psPhase === 'tables' && g.psView && !g.psView.complete;
    const submit = () => {
      if (gate === 'field_mapping' && g.fmProblems && g.fmProblems.length) {
        return this.setState({ mgError: 'Not sent — ' + g.fmProblems.slice(0, 3).join('; ') + (g.fmProblems.length > 3 ? '; …' : '') });
      }
      return this.mgSubmitGate();
    };
    const primary = kind === 'gate'
      ? (gate === 'write' || gate === 'final_confirmation'
          ? null
          : { label: busy ? s.mgBusy : gateLabel, icon: 'ph-check-circle', disabled: busy || blocked, run: busy || blocked ? () => {} : submit })
      : kind === 'step' && !continuing
        ? { label: busy ? s.mgBusy : 'Continue', trail: 'ph-arrow-right', run: busy ? () => {} : () => this.mgAdvance() }
        : null;

    const list = Array.isArray(s.mgList) ? s.mgList : [];
    const now = Date.now();
    const files = s.mgFiles || [];

    return {
      // The upload panel went with the page. A migration is started from the composer now
      // (mgStartFromChat), so what is left here is the source-system the run is labelled
      // with and the staged-file list the dock's tray still reads.
      mgFiles: files.map((f, i) => ({ key: i, name: f.name, size: fmtBytes(f.size), icon: /\.csv$|\.tsv$/i.test(f.name) ? 'ph-file-csv' : 'ph-file-xls', drop: () => this.mgDropFile(i) })),
      mgCmms: s.mgCmms === undefined || s.mgCmms === null ? 'Custom' : s.mgCmms,
      mgSetCmms: (e) => this.setState({ mgCmms: e.target.value }),
      mgStart: () => this.mgStart(),

      // recent runs
      mgRecent: list.map((m) => ({
        id: m.migration_id, short: shortId(m.migration_id), cmms: m.cmms_name || 'Custom',
        status: String(m.status || '').replace(/_/g, ' '),
        tone: isTerminal(m.status) ? (m.status === 'complete' ? 'var(--st-ok)' : 'var(--st-risk)') : m.status === 'awaiting_review' ? 'var(--color-accent)' : 'var(--color-neutral-400)',
        when: whenLabel(m.started_at, now), mapped: (m.t1_count || 0) + (m.t2_count || 0),
        active: m.migration_id === s.mgId,
        open: () => this.mgOpen(m.migration_id),
        canCancel: !isTerminal(m.status),
        cancelArmed: s.mgCancelArmed === m.migration_id,
        cancel: (e) => { if (e && e.stopPropagation) e.stopPropagation(); this.mgCancel(m.migration_id); }
      })),
      mgRecentEmpty: !s.mgListLoading && list.length === 0,
      mgRecentNote: s.mgListLoading && !list.length ? 'Reading recent runs…' : s.mgListError ? 'Recent runs could not be read: ' + s.mgListError : '',
      mgRecentReload: () => this.mgListLoad(),

      // the open run
      mgHasRun: !!s.mgId,
      mgId: s.mgId || '',
      mgIdShort: shortId(s.mgId),
      mgLoading: !!s.mgLoading && !doc,
      mgError: s.mgError || '',
      mgBusy: s.mgBusy || '',
      mgFile: (doc && doc.source_filename) || '',
      mgCmmsName: (doc && doc.cmms_name) || '',
      mgStarted: doc && doc.started_at ? whenLabel(doc.started_at, now) : '',
      mgPill: pill,
      mgKind: kind,
      mgProgress: progress,
      mgStepLabel: doc ? ('Node ' + currentNode(doc) + ' of ' + NODES.length) : '',
      mgCoverage: doc ? [
        { label: 'Rule-matched', value: doc.t1_mapped_count || 0 }, { label: 'Semantic', value: doc.t2_auto_count || 0 },
        { label: 'Human', value: doc.t2_human_count || 0 }, { label: 'Unmapped', value: doc.unmapped_count || 0 }, { label: 'Total', value: doc.total_fields || 0 }
      ] : [],
      mgNodes: nodes.map((n) => ({
        id: n.id, name: n.name, blurb: n.blurb, outcome: n.outcome, status: n.status,
        ms: n.ms === null ? '' : n.ms < 1000 ? n.ms + ' ms' : (n.ms / 1000).toFixed(n.ms < 10000 ? 1 : 0) + ' s',
        icon: n.status === 'complete' ? 'ph-check-circle' : n.status === 'failed' ? 'ph-x-circle' : n.status === 'awaiting_review' ? 'ph-hand-palm' : n.status === 'running' || n.status === 'paused' ? 'ph-circle-notch' : 'ph-circle',
        tone: n.status === 'complete' ? 'var(--st-ok)' : n.status === 'failed' ? 'var(--st-risk)' : n.status === 'awaiting_review' || n.status === 'running' || n.status === 'paused' ? 'var(--color-accent)' : 'var(--color-neutral-700)',
        spin: n.status === 'running',
        current: doc ? currentNode(doc) === n.id && kind !== 'done' : false,
        logs: n.logs, logsOpen: !!(s.mgOpenNodes || {})[n.id], hasLogs: n.logs.length > 0,
        toggle: () => this.mgToggleNode(n.id)
      })),
      mgAuto: !!s.mgAuto,
      mgToggleAuto: () => {
        const next = !this.state.mgAuto;
        this.setState({ mgAuto: next });
        if (next && runKind(this.state.mgStatus) === 'step') { this._mgAdvanced = null; this.mgAdvance(); }
      },
      mgRefresh: () => this.mgPoll(true),
      // Cancel: offered while the run can still be stopped - running, paused at a step, or
      // waiting at a gate. Nothing to offer once it is complete, failed or cancelled.
      mgCanCancel: !!(doc && s.mgId && !isTerminal(doc.status)),
      mgCancelArmed: !!s.mgId && s.mgCancelArmed === s.mgId,
      mgCancelLabel: s.mgBusy === 'Cancelling…' ? 'Cancelling…' : (s.mgId && s.mgCancelArmed === s.mgId) ? 'Click again to cancel' : 'Cancel migration',
      mgCancel: () => this.mgCancel(),
      mgExtrasChip: kind === 'done' ? extrasStatus(s.mgExtras) : extrasStatus(null),
      mgExtrasRetry: kind === 'done' && !!extrasStatus(s.mgExtras).action,
      mgExtrasAction: extrasStatus(s.mgExtras).action,
      mgRetryExtras: () => this.mgRunExtras(s.mgId, !!(s.mgExtras && ['done', 'none', 'predates', 'failed'].includes(s.mgExtras.status))),
      mgNew: () => this.mgNew(),
      mgLoadedAt: s.mgLoadedAt ? whenLabel(new Date(s.mgLoadedAt).toISOString(), now) : '',

      // the open gate / step
      mgGate: gate,
      mgGateTitle: kind === 'gate' ? (g.wrView ? 'Ready to write ' + g.wrView.rowsLabel + ' to Plenum' : text[0])
        : kind === 'step' ? (pauseKey(doc) === 'step_1_ingest' ? 'Your file has been read' : (payload.label || payload.title || 'Step') + ' — done')
        : kind === 'done' ? 'Migration complete' : kind === 'failed' ? 'Migration stopped'
        : 'Working on ' + String((nodes.find((n) => n.status === 'running' || n.status === 'paused') || nodes.find((n) => n.status === 'pending') || { name: 'the next step' }).name).toLowerCase() + '…',
      mgGateBlurb: kind === 'gate' ? text[1] : kind === 'step' ? (continuing ? 'Continuing to the next step on its own.' : 'Check the result, then continue.') : kind === 'done' ? 'Every question was answered and the rows are in Plenum. The files the run produced are below.' : kind === 'failed' ? ((doc && doc.error_message) || 'The service reported a failure and gave no reason.')
        // No document at all is not "working" — it is "nobody has looked". Saying the
        // pipeline is busy when nothing has been read is how a restored run sat at
        // "Not loaded" for ever and read as progress.
        : !doc ? (s.mgLoading ? 'Reading the migration…' : 'This run has not been read yet — Refresh reads it.')
        : 'Nothing to decide yet — the next question appears here as soon as it is ready.',
      mgGateCount: payload.total_reviewable ? payload.total_reviewable + ' item' + (payload.total_reviewable === 1 ? '' : 's') + ' to review' : '',
      // A run that has stopped moving. Only ever said of a run that claims to be WORKING:
      // a gate waits for a person and may wait all day, and a finished or failed run is
      // not waiting for anything.
      mgStallNote: ((kind === 'running' || kind === 'step') && this._mgMovedAt && (now - this._mgMovedAt) > STALL_AFTER_MS)
        ? 'This run has not moved for ' + Math.round((now - this._mgMovedAt) / 60000) + ' minutes. Steps that upload files or write rows report progress as they go, so a run this quiet is probably not being processed: answering a gate hands the run to the migration worker (arq src.worker.WorkerSettings), and if that worker is not running the job waits in the queue and nothing here will change.'
        : '',
      mgPrimary: primary,
      mgStages: stagesFor(doc),
      mgStageText: (() => { const st = stagesFor(doc); const i = st.findIndex((x) => x.state !== 'done'); return i < 0 ? 'All stages done' : 'Stage ' + (i + 1) + ' of ' + st.length + ' · ' + st[i].label; })(),
      mgAttention: kind === 'gate' ? gateAttention(gate, g) : [],
      mgContinuing: continuing,
      // The CAFM-shaped gates draw their own head (code, title, badges, counts).
      mgOwnHead: kind === 'gate' && ['pre_semantic', 'classification_approval', 'column_mapping_approval', 'field_mapping', 'hierarchy', 'write', 'final_confirmation'].indexOf(gate) > -1,
      mgSet: (k, v) => this.mgDecide(k, v),
      mgCafmMounted: (on) => this.mgCafmMounted(on),
      mgSessionId: s.sessionId || '',
      mgPsTypes: PS_DATA_TYPES,
      mgCardOpen: (k, dflt) => { const o = (s.mgCards || {})[k]; return o === undefined ? !!dflt : !!o; },
      mgToggleCard: (k, dflt) => this.setState((q) => {
        const c = Object.assign({}, q.mgCards || {});
        c[k] = !(c[k] === undefined ? !!dflt : !!c[k]);
        return { mgCards: c };
      }),
      mgStepFacts: stepFacts,
      mgIngest: ingest,
      mgIngestLive: !!ingestLive,
      mgIngestOpen: !!s.mgIngestOpen,
      mgToggleIngest: () => this.setState((p) => ({ mgIngestOpen: !p.mgIngestOpen })),
      mgOutputs: outputs,
      mgDecided: Object.keys(dec).length,
      mgResetDecisions: () => this.setState({ mgDec: {} }),
      ...g,

      // the write gate's two steps
      mgArm: () => this.setState({ mgArmed: true }),
      mgDisarm: () => this.setState({ mgArmed: false }),
      mgConfirmWrite: busy ? () => {} : () => this.mgSubmitGate({ confirmed: true }),
      mgRejectWrite: busy ? () => {} : () => this.mgSubmitGate({ confirmed: false }),
      mgWriteLabel: busy ? s.mgBusy : (g.wrView ? g.wrView.writeLabel : 'Confirm — write')
    };
  }
};
