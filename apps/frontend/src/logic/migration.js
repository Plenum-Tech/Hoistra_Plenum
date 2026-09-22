// migration — a CSV / Excel migration, against svc-ai-schema-mapper's pipeline
// (api/schemaMapper.js), answered in the Orchestrator conversation that started it
// (components/shell/MigrationRun.jsx).
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
import { currentOrgId, isStaleScope } from '../api/client.js';

// Statuses after which the run will not move again.
export const TERMINAL = new Set(['complete', 'failed', 'ddl_failed', 'cancelled']);
export const isTerminal = (status) => TERMINAL.has(String(status || '').toLowerCase());

// The nine nodes, for the tracker before the service has reported any, and for the label
// a status document's `current_step` maps to.
export const NODES = [
  { id: 1, name: 'File ingestion', blurb: 'Parse the file, detect tables and columns, confirm primary keys and unique tables' },
  { id: 2, name: 'Deterministic mapping', blurb: 'Exact, alias and pattern matches; column groups and destination columns' },
  { id: 3, name: 'Pre-semantic review', blurb: 'Every rule-based match, approved or sent on to semantic matching' },
  { id: 4, name: 'Semantic mapping', blurb: 'Embedding matches for what the rules left; flagged fields gated' },
  { id: 5, name: 'Field mapping review', blurb: 'Accept, reject or override each flagged field; decide unmapped ones' },
  { id: 6, name: 'Data preprocessing', blurb: 'Dedup, null handling, type coercion, validation' },
  { id: 7, name: 'Hierarchy', blurb: 'Foreign keys and containment between the tables, confirmed by you' },
  { id: 8, name: 'Output generation', blurb: 'JSON, CSV, SQL and the report, uploaded to blob storage' },
  { id: 9, name: 'Write to database', blurb: 'The confirmed rows land in plenum_cafm' }
];

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
  return [doc.status, doc.current_step, done, doc.pending_gate_type || ''].join('|');
}

// How long a run may report the same thing before the card stops calling it work. Nodes
// take seconds and a gate answer resumes in seconds, so minutes of silence is a stall —
// see the ARQ-worker case in test/chatMigration.test.mjs.
export const STALL_AFTER_MS = 3 * 60 * 1000;

export const cmmsName = (v) => (String(v == null ? '' : v).trim() || 'Custom');
const pct = (x) => (x === null || x === undefined || isNaN(x)) ? '—' : Math.round(Number(x) * 100) + '%';

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

// Human labels for a gate, and one sentence on what deciding it does.
export const GATE_TEXT = {
  pk_approval: ['Primary keys', 'One key per source table. The detected key is pre-selected; change it where the detector picked a column that merely happens to be unique.'],
  unique_table_approval: ['Unique tables', 'Each sheet routed to the plenum_cafm table it will land in, with its confirmed key. Approve to continue to column mapping.'],
  pre_semantic: ['Rule-based matches', 'Every column the rules matched. Approve keeps the match; Semantic sends the column on to embedding matching instead.'],
  classification_approval: ['Keys and relationships', 'Foreign keys and shared attributes found across the sheets. A foreign key is enforced; a shared attribute becomes a lookup table; Exclude drops the group.'],
  column_mapping_approval: ['Destination columns', 'The destination column matched for every source column. Re-target one, or make it a new column on the destination table.'],
  field_mapping: ['Flagged fields', 'Fields the semantic matcher was unsure about, and fields nothing matched. Unmapped fields become custom columns unless you say otherwise.'],
  hierarchy: ['Hierarchy', 'The containment and reference relationships between the tables, from the keys you confirmed. Reject any that is wrong.'],
  write: ['Write to database', 'The last gate. Confirm inserts the rows into plenum_cafm; Reject ends the run with nothing written.'],
  final_confirmation: ['Write to database', 'The last gate. Confirm inserts the rows into plenum_cafm; Reject ends the run with nothing written.']
};

const verdictKey = (v) => /foreign/i.test(v || '') ? 'fk' : /shared/i.test(v || '') ? 'shared' : 'pk';
const firstTable = (m) => (Array.isArray(m.source_tables) && m.source_tables[0]) || String(m.source || '').split('.')[0] || '';
export const relKey = (r) => [r.source_table, r.source_column, r.target_table, r.target_column].join('>');

// The body a gate is answered with, from what the gate showed plus the reader's decisions
// (`dec`: {key: value}, keys as the view model below sets them). With no decisions at all
// this is "accept everything the pipeline proposed", which is also what the auto-driven
// flow sends.
export function defaultGateBody(gateType, payload, dec) {
  const p = payload || {};
  const d = dec || {};
  switch (String(gateType || '').toLowerCase()) {
    case 'pk_approval': {
      const out = {};
      Object.entries(p.pk_confirmation || {}).forEach(([table, info]) => {
        const detected = Array.isArray(info.detected_pk) ? info.detected_pk : [];
        const chosen = d[table] !== undefined ? d[table] : (info.surrogate || !detected.length ? '__surrogate__' : detected[0]);
        out[table] = chosen;
      });
      return { pk_overrides: out };
    }
    case 'unique_table_approval':
      return { approved: true };
    case 'pre_semantic': {
      if (String(p.gate_step || '') === 'table_routing' || (!p.review_items_by_table && p.suggested_target_by_table)) {
        const overrides = {};
        Object.entries(p.suggested_target_by_table || {}).forEach(([table, suggested]) => {
          const chosen = d['table:' + table];
          if (chosen && chosen !== suggested) {
            const existing = (p.existing_canonical_tables || []).indexOf(chosen) > -1;
            overrides[table] = { target_table: chosen, is_new_table: !existing };
          }
        });
        return { decisions: {}, table_overrides: overrides };
      }
      const decisions = {};
      Object.entries(p.review_items_by_table || {}).forEach(([table, items]) => {
        decisions[table] = (items || []).map((it) => {
          const k = table + '.' + it.source_field;
          const row = { source_field: it.source_field, decision: d[k] === 'semantic' ? 'semantic' : 'approve' };
          if (it.target_field) row.target_field = it.target_field;
          if (it.data_type) row.data_type = it.data_type;
          return row;
        });
      });
      return { decisions: decisions };
    }
    case 'classification_approval': {
      const rejected = [];
      const overrides = {};
      (p.classification || []).forEach((g) => {
        const detected = verdictKey(g.verdict);
        if (detected === 'pk') return;
        const chosen = d[g.group_id];
        if (chosen === 'exclude') rejected.push(g.group_id);
        else if ((chosen === 'fk' || chosen === 'shared') && chosen !== detected) overrides[g.group_id] = chosen;
      });
      return { rejected_groups: rejected, verdict_overrides: overrides };
    }
    case 'column_mapping_approval': {
      const overrides = {};
      (p.dest_mapping || []).forEach((m) => {
        const table = firstTable(m);
        const k = table + '.' + m.source_column;
        const current = m.matched_column || '__new__';
        const chosen = d[k];
        if (chosen && chosen !== current) {
          overrides[table] = overrides[table] || {};
          overrides[table][m.source_column] = chosen;
        }
      });
      return { overrides: overrides };
    }
    case 'field_mapping': {
      const flagged = {};
      Object.entries(p.review_items_by_table || {}).forEach(([table, items]) => {
        flagged[table] = (items || []).map((it) => {
          const k = 'f:' + table + '.' + it.source_field;
          const v = d[k];
          if (v === 'reject') return { action: 'reject', source_field: it.source_field, target_field: null, rationale: null };
          if (v && v !== 'accept' && v !== it.suggested_target) return { action: 'override', source_field: it.source_field, target_field: v, rationale: null };
          return { action: 'accept', source_field: it.source_field, target_field: it.suggested_target || null, rationale: null };
        });
      });
      const unmapped = {};
      Object.entries(p.unmappable_items_by_table || {}).forEach(([table, items]) => {
        unmapped[table] = (items || []).map((it) => {
          const sa = it.suggested_action || {};
          const action = d['u:' + table + '.' + it.source_field] || sa.action || 'custom';
          return {
            action: action,
            source_field: it.source_field,
            target_table: action === 'custom' ? (sa.target_table || table) : null,
            custom_column_name: action === 'custom' ? (sa.custom_column_name || it.source_field) : null,
            data_type: action === 'custom' ? (sa.data_type || 'VARCHAR(255)') : null,
            nullable: true
          };
        });
      });
      return { flagged: flagged, unmapped: unmapped };
    }
    case 'hierarchy': {
      const rels = Array.isArray(p.hierarchies_to_review) && p.hierarchies_to_review.length
        ? p.hierarchies_to_review
        : (p.review_items || []).filter((r) => r.type === 'hierarchy');
      const confirmed = [];
      const corrections = [];
      rels.forEach((r) => {
        if (r.system_default || r.mapping_note) return;
        if (d[relKey(r)] === 'reject') {
          corrections.push({ type: 'rejected', source_table: r.source_table, target_table: r.target_table, reason: 'Rejected by reviewer' });
        } else {
          confirmed.push(r);
        }
      });
      return { confirmed_hierarchies: confirmed, hierarchy_corrections: corrections };
    }
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
  const current = doc ? Number(doc.current_step || 0) : 0;
  const kind = runKind(doc);
  return NODES.map((n) => {
    const l = byId[n.id];
    let status = l ? String(l.status || '').toLowerCase() : 'pending';
    if (!l && current === n.id && kind !== 'done') status = kind === 'gate' ? 'awaiting_review' : kind === 'step' ? 'paused' : 'running';
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

export const migrationMethods = {
  // ── navigation ────────────────────────────────────────────────────────────────────
  //
  // There is no page. A migration is answered in the conversation that started it:
  // openChat() puts the transcript up and MigrationRun.jsx renders the open gate under it.
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
    const same = this.state.mgId === id;
    // The card renders in the Orchestrator's transcript and nowhere else, so a run opened
    // from a page that merely keeps a dock (Buildings, Home, the console) must land on the
    // chat — chatView() is true on those and would have left the gates rendered nowhere.
    // Already on the chat: only the state changes, so answering a gate never scrolls the
    // transcript back to the top or drops a form the dock was showing.
    if (this.state.view !== 'chat') this.openChat();
    this.setState({
      mgId: id, mgError: '', mgArmed: false,
      mgStatus: same ? this.state.mgStatus : null,
      mgDec: same ? this.state.mgDec : {},
      mgOpenNodes: same ? this.state.mgOpenNodes : {}
    });
    if (!same) { this._mgAdvanced = null; this._mgProgressKey = null; this._mgMovedAt = Date.now(); }
    this.mgPoll(true);
  },

  // Back to the upload panel. The run itself is untouched — it stays in the recent list.
  mgNew() {
    clearTimeout(this._mgTimer);
    this._mgAdvanced = null;
    this.setState({ mgId: null, mgStatus: null, mgError: '', mgDec: {}, mgArmed: false, mgOpenNodes: {} });
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
    this.setState({
      mgStatus: doc, mgLoading: false, mgError: '', mgLoadedAt: Date.now(),
      mgDec: gateChanged ? {} : this.state.mgDec,
      mgArmed: gateChanged ? false : this.state.mgArmed
    });
    if (this._mgRepoll) { this._mgRepoll = false; return this.mgPoll(true); }
    // A step pause is continued once. Until the document changes, the same pause is not
    // advanced again — a service still reporting step_paused right after /advance would
    // otherwise be hit in a loop, and (with an answer that arrives without I/O) one that
    // never yields to the timer queue at all.
    const stepKey = String(doc.current_step) + ':' + String(doc.pending_gate_type || '');
    if (runKind(doc) === 'step' && this.state.mgAuto && !this.state.mgBusy && this._mgAdvanced !== stepKey) {
      this._mgAdvanced = stepKey;
      return this.mgAdvance();
    }
    const delay = pollDelay(doc.status);
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
      this.setState({ mgBusy: '', mgError: 'The step did not continue: ' + ((e && e.message) || String(e)) });
    }
    this.mgPoll(true);
  },

  // Answers the open gate. `body` overrides the default built from the decisions — the
  // write gate passes {confirmed} explicitly.
  async mgSubmitGate(body) {
    const id = this.state.mgId;
    const doc = this.state.mgStatus;
    if (!id || !doc || runKind(doc) !== 'gate' || this.state.mgBusy) return;
    const gate = doc.pending_gate_type;
    const sent = body || defaultGateBody(gate, doc.pending_gate_payload, this.state.mgDec);
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
    const text = GATE_TEXT[gate] || [gate.replace(/_/g, ' '), payload.instructions || ''];

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
      g.pkRows = Object.entries(payload.pk_confirmation || {}).map(([table, info]) => {
        const detected = Array.isArray(info.detected_pk) ? info.detected_pk : [];
        const chosen = dec[table] !== undefined ? dec[table] : (info.surrogate || !detected.length ? '__surrogate__' : detected[0]);
        const cols = Array.isArray(info.columns) ? info.columns : [];
        return {
          table: table, detected: detected.join(', ') || (info.surrogate ? 'surrogate' : '—'),
          kind: info.kind || (info.surrogate ? 'surrogate' : 'natural'), confidence: pct(info.confidence),
          chosen: chosen, changed: chosen !== (detected[0] || '__surrogate__'),
          options: [{ value: '__surrogate__', label: 'Generate a surrogate key' }].concat(cols.map((c) => ({
            value: c.column,
            label: c.column + '  ·  unique ' + pct(c.uniqueness) + ', null ' + pct(c.null_rate) + (c.qualifies ? '' : '  (does not qualify)')
          }))),
          pick: (e) => this.mgDecide(table, e.target.value)
        };
      });
    } else if (gate === 'unique_table_approval') {
      const tr = payload.table_resolution || {};
      const pks = payload.pk_confirmed_by_table || {};
      g.utRows = (tr.final_decisions || []).map((d) => ({
        source: d.source, destination: d.destination, method: d.method, confidence: pct(d.confidence),
        pk: (pks[d.source] || []).join(', ') || '—'
      }));
      const dup = (tr.duplicate_tables || {});
      g.utDuplicates = (dup.groups || []).map((grp) => Array.isArray(grp) ? grp.join(' = ') : JSON.stringify(grp));
      g.utNote = dup.checked ? dup.checked + ' tables checked · ' + (dup.duplicate_count || 0) + ' duplicate' + (dup.duplicate_count === 1 ? '' : 's') : '';
    } else if (gate === 'pre_semantic') {
      const routing = String(payload.gate_step || '') === 'table_routing' || (!payload.review_items_by_table && payload.suggested_target_by_table);
      g.psRouting = !!routing;
      if (routing) {
        const existing = payload.existing_canonical_tables || [];
        g.psTables = Object.entries(payload.suggested_target_by_table || {}).map(([table, suggested]) => {
          const chosen = dec['table:' + table] || suggested;
          return {
            table: table, suggested: suggested, chosen: chosen, changed: chosen !== suggested,
            options: (existing.indexOf(chosen) > -1 ? existing : existing.concat([chosen])).map((t) => ({ value: t, label: t })),
            pick: (e) => this.mgDecide('table:' + table, e.target.value),
            setNew: (e) => this.mgDecide('table:' + table, e.target.value.trim() || undefined)
          };
        });
      } else {
        g.psTables = Object.entries(payload.review_items_by_table || {}).map(([table, items]) => ({
          table: table, target: (payload.suggested_target_by_table || {})[table] || (items[0] && items[0].dest_table) || '',
          rows: (items || []).map((it) => {
            const k = table + '.' + it.source_field;
            const chosen = dec[k] === 'semantic' ? 'semantic' : 'approve';
            return {
              field: it.source_field, target: it.target_field || '—', confidence: pct(it.confidence),
              tier: String(it.tier || '').replace(/^T1_/, '').replace(/_/g, ' '), samples: (it.sample_values || []).slice(0, 3).join(', '),
              pk: !!it.is_primary_key, chosen: chosen,
              seg: seg([{ value: 'approve', label: 'Approve' }, { value: 'semantic', label: 'Semantic' }], chosen, k)
            };
          })
        }));
      }
      g.psSemanticCount = Object.values(dec).filter((v) => v === 'semantic').length;
    } else if (gate === 'classification_approval') {
      const groups = payload.classification || [];
      const shared = payload.shared_attribute_tables || [];
      g.clPk = groups.filter((x) => verdictKey(x.verdict) === 'pk').map((x) => ({ id: x.group_id, members: (x.members || []).join(', '), canonical: x.canonical_name || '' }));
      g.clRows = groups.filter((x) => verdictKey(x.verdict) !== 'pk').map((x) => {
        const detected = verdictKey(x.verdict);
        const chosen = dec[x.group_id] || detected;
        const lookup = shared.find((t) => t.from_group === x.group_id);
        return {
          id: x.group_id, detected: detected, chosen: chosen, changed: chosen !== detected,
          members: (x.members || []).join('  →  '), ri: x.ri === null || x.ri === undefined ? '' : 'RI ' + pct(x.ri),
          pk: x.has_pk || '', fk: x.fk_column || '',
          lookup: lookup ? 'lookup table ' + lookup.table_name + ' · ' + (lookup.distinct_count || (lookup.sample_values || []).length) + ' values: ' + (lookup.sample_values || []).slice(0, 5).join(', ') : '',
          seg: seg([{ value: 'fk', label: 'Foreign key' }, { value: 'shared', label: 'Shared attribute' }, { value: 'exclude', label: 'Exclude', tone: 'var(--st-risk)' }], chosen, x.group_id)
        };
      });
      g.clExcluded = g.clRows.filter((r) => r.chosen === 'exclude').length;
    } else if (gate === 'column_mapping_approval') {
      const byTable = payload.dest_columns_by_table || {};
      g.cmRows = (payload.dest_mapping || []).map((m) => {
        const table = firstTable(m);
        const k = table + '.' + m.source_column;
        const current = m.matched_column || '__new__';
        const chosen = dec[k] || current;
        const cols = byTable[m.dest_table] || [];
        return {
          key: k, source: m.source || (table + '.' + m.source_column), dest: m.dest_table || '', outcome: m.outcome || '',
          confidence: pct(m.confidence), samples: (m.samples || []).slice(0, 3).join(', '), pk: !!m.is_primary_key,
          chosen: chosen, changed: chosen !== current, suggested: m.outcome === 'suggested — confirm' || /suggest/i.test(m.outcome || ''),
          options: [{ value: '__new__', label: 'New column on ' + (m.dest_table || 'the table') }].concat((cols.indexOf(chosen) > -1 || chosen === '__new__' ? cols : cols.concat([chosen])).map((c) => ({ value: c, label: c }))),
          pick: (e) => this.mgDecide(k, e.target.value)
        };
      });
      g.cmChanged = g.cmRows.filter((r) => r.changed).length;
      g.cmNew = g.cmRows.filter((r) => r.chosen === '__new__').length;
    } else if (gate === 'field_mapping') {
      const canon = payload.canonical_columns_by_table || {};
      const routing = payload.table_routing || {};
      g.fmFlagged = Object.entries(payload.review_items_by_table || {}).map(([table, items]) => ({
        table: table,
        rows: (items || []).map((it) => {
          const k = 'f:' + table + '.' + it.source_field;
          const v = dec[k];
          const mode = v === 'reject' ? 'reject' : (v && v !== 'accept' && v !== it.suggested_target) ? 'override' : 'accept';
          const dest = routing[table] || table;
          const cols = canon[dest] || [];
          const alts = (it.suggestions || []).map((x) => typeof x === 'string' ? x : (x && (x.target_field || x.field))).filter(Boolean);
          return {
            field: it.source_field, suggested: it.suggested_target || '—', confidence: pct(it.confidence), rationale: it.rationale || '',
            samples: (it.sample_values || []).slice(0, 3).join(', '), mode: mode, target: mode === 'override' ? v : (it.suggested_target || ''),
            seg: seg([{ value: 'accept', label: 'Accept' }, { value: 'reject', label: 'Reject', tone: 'var(--st-risk)' }], mode === 'override' ? 'override' : mode, k),
            options: [{ value: '', label: 'Override with…' }].concat(alts.concat(cols.filter((c) => alts.indexOf(c) < 0)).map((c) => ({ value: c, label: c }))),
            pickTarget: (e) => this.mgDecide(k, e.target.value || undefined)
          };
        })
      }));
      g.fmUnmapped = Object.entries(payload.unmappable_items_by_table || {}).map(([table, items]) => ({
        table: table,
        rows: (items || []).map((it) => {
          const sa = it.suggested_action || {};
          const k = 'u:' + table + '.' + it.source_field;
          const chosen = dec[k] || sa.action || 'custom';
          return {
            field: it.source_field, target: sa.target_table || table, column: sa.custom_column_name || it.source_field, type: sa.data_type || 'VARCHAR(255)',
            chosen: chosen,
            seg: seg([{ value: 'custom', label: 'New column' }, { value: 'raw_metadata', label: 'Raw metadata' }, { value: 'skip', label: 'Skip', tone: 'var(--st-risk)' }], chosen, k)
          };
        })
      }));
      g.fmCounts = (payload.total_flagged || 0) + ' flagged · ' + (payload.total_unmappable || 0) + ' unmapped · overall confidence ' + pct(payload.overall_confidence);
      g.fmAlert = payload.confidence_alert && payload.confidence_alert.message ? payload.confidence_alert.message : '';
    } else if (gate === 'hierarchy') {
      const rels = Array.isArray(payload.hierarchies_to_review) && payload.hierarchies_to_review.length
        ? payload.hierarchies_to_review
        : (payload.review_items || []).filter((r) => r.type === 'hierarchy');
      g.hiRows = rels.map((r) => {
        const k = relKey(r);
        const chosen = dec[k] === 'reject' ? 'reject' : 'keep';
        return {
          key: k, from: r.source_table + '.' + r.source_column, to: r.target_table + '.' + r.target_column,
          type: String(r.relationship_type || '').toLowerCase(), confidence: pct(r.confidence), match: r.data_match_rate === undefined ? '' : 'rows matched ' + pct(r.data_match_rate),
          fixed: !!(r.system_default || r.mapping_note), chosen: chosen,
          seg: seg([{ value: 'keep', label: 'Keep' }, { value: 'reject', label: 'Reject', tone: 'var(--st-risk)' }], chosen, k)
        };
      });
      g.hiImplicit = (payload.review_items || []).filter((r) => r.type === 'implicit_hierarchy').map((r) => ({
        column: r.column, levels: r.levels, examples: (r.examples || []).slice(0, 4).join(', '), separator: r.separator
      }));
      g.hiTree = payload.hierarchy_tree || '';
      g.hiNote = (payload.total_cycles || 0) + ' cycle' + (payload.total_cycles === 1 ? '' : 's') + ' · ' + (payload.total_orphans || 0) + ' orphan' + (payload.total_orphans === 1 ? '' : 's');
      g.hiRejected = g.hiRows.filter((r) => r.chosen === 'reject').length;
    } else if (gate === 'write' || gate === 'final_confirmation') {
      const sm = payload.summary || {};
      g.wrCounts = Object.entries(sm.entity_counts || {}).map(([t, n]) => ({ table: t, n: n }));
      g.wrTotal = sm.total_entities || g.wrCounts.reduce((a, x) => a + (Number(x.n) || 0), 0);
      g.wrConfidence = pct(sm.overall_confidence);
      g.wrFile = sm.source_filename || (doc && doc.source_filename) || '';
      g.wrArmed = !!s.mgArmed;
    }

    // Artefacts a finished (or output-generated) run points at.
    const outputs = doc ? [
      ['Report (PDF)', doc.migration_report_url], ['JSON', doc.output_json_url], ['CSV / Excel', doc.output_csv_url],
      ['SQL', doc.output_sql_url], ['Structure (Markdown)', doc.output_structure_md_url]
    ].filter((x) => x[1]).map((x) => ({ label: x[0], href: x[1] })) : [];

    // What the step pause produced, for the "continue" card when auto-continue is off.
    const stepFacts = kind === 'step' ? scalarFacts(payload) : [];

    // One primary action per state.
    const primary = kind === 'gate'
      ? (gate === 'write' || gate === 'final_confirmation'
          ? null
          : { label: busy ? s.mgBusy : (gate === 'unique_table_approval' ? 'Approve and continue' : 'Approve decisions and continue'), run: busy ? () => {} : () => this.mgSubmitGate() })
      : kind === 'step'
        ? { label: busy ? s.mgBusy : 'Continue to the next node', run: busy ? () => {} : () => this.mgAdvance() }
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
        open: () => this.mgOpen(m.migration_id)
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
      mgStepLabel: doc ? ('Node ' + (doc.current_step || 0) + ' of ' + NODES.length) : '',
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
        current: doc ? Number(doc.current_step) === n.id && kind !== 'done' : false,
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
      mgNew: () => this.mgNew(),
      mgLoadedAt: s.mgLoadedAt ? whenLabel(new Date(s.mgLoadedAt).toISOString(), now) : '',

      // the open gate / step
      mgGate: gate,
      mgGateTitle: kind === 'gate' ? text[0] : kind === 'step' ? (payload.label || 'Step finished') : kind === 'done' ? 'Migration complete' : kind === 'failed' ? 'Migration stopped' : 'Working…',
      mgGateBlurb: kind === 'gate' ? text[1] : kind === 'step' ? 'This node has finished. It continues on its own unless you switch auto-continue off to read each result first.' : kind === 'done' ? 'Every gate was answered and the rows are in plenum_cafm. The artefacts are below.' : kind === 'failed' ? ((doc && doc.error_message) || 'The service reported a failure and gave no reason.')
        // No document at all is not "working" — it is "nobody has looked". Saying the
        // pipeline is busy when nothing has been read is how a restored run sat at
        // "Not loaded" for ever and read as progress.
        : !doc ? (s.mgLoading ? 'Reading the migration…' : 'This run has not been read yet — Refresh reads it.')
        : 'The pipeline is working on the current node; this page follows it.',
      mgGateCount: payload.total_reviewable ? payload.total_reviewable + ' item' + (payload.total_reviewable === 1 ? '' : 's') + ' to review' : '',
      // A run that has stopped moving. Only ever said of a run that claims to be WORKING:
      // a gate waits for a person and may wait all day, and a finished or failed run is
      // not waiting for anything.
      mgStallNote: ((kind === 'running' || kind === 'step') && this._mgMovedAt && (now - this._mgMovedAt) > STALL_AFTER_MS)
        ? 'This run has not moved for ' + Math.round((now - this._mgMovedAt) / 60000) + ' minutes. A node takes seconds, so the pipeline is probably not processing it: answering a gate hands the run to the migration worker (arq src.worker.WorkerSettings), and if that worker is not running the job waits in the queue and nothing here will change.'
        : '',
      mgPrimary: primary,
      mgStepFacts: stepFacts,
      mgOutputs: outputs,
      mgDecided: Object.keys(dec).length,
      mgResetDecisions: () => this.setState({ mgDec: {} }),
      ...g,

      // the write gate's two steps
      mgArm: () => this.setState({ mgArmed: true }),
      mgDisarm: () => this.setState({ mgArmed: false }),
      mgConfirmWrite: busy ? () => {} : () => this.mgSubmitGate({ confirmed: true }),
      mgRejectWrite: busy ? () => {} : () => this.mgSubmitGate({ confirmed: false }),
      mgWriteLabel: busy ? s.mgBusy : 'Confirm — write ' + (g.wrTotal || 0) + ' rows'
    };
  }
};
