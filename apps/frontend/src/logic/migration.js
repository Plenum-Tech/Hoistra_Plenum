// migration — the CSV / Excel migration page (screens/Migration.jsx), against
// svc-ai-schema-mapper's migration pipeline (api/schemaMapper.js).
//
// A migration is one upload walked through nine nodes with human gates between them.
// This module owns: the staged upload (mgFiles → start), the open run (mgId + the status
// document the service last returned), the poll that keeps that document current, the
// step-pause auto-continue, and the decisions the reader makes at each gate before the
// gate is answered. Nothing is written to plenum_cafm until the LAST gate — `write` — is
// confirmed, and that confirmation is a two-step control here (arm, then confirm) because it
// is the one click on this page that changes the database.
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

export const fmtBytes = (n) => n < 1024 ? n + ' B' : n < 1048576 ? Math.round(n / 1024) + ' KB' : (n / 1048576).toFixed(1) + ' MB';
export const shortId = (id) => String(id || '').slice(0, 8);
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
  mgShow() {
    window.scrollTo(0, 0);
    this.setState({ view: 'migration', navOpen: true, detail: null, queueOpen: false, paletteOpen: false });
    this.mgListLoad();
    if (this.state.mgId) this.mgPoll(true);
  },

  // Opens one run on the page — from the recent list, from a chat reply that started one,
  // or on reload. A different run than before drops the previous document and decisions.
  mgOpen(id) {
    const same = this.state.mgId === id;
    window.scrollTo(0, 0);
    this.setState({
      view: 'migration', navOpen: true, detail: null, queueOpen: false, paletteOpen: false,
      mgId: id, mgError: '', mgArmed: false,
      mgStatus: same ? this.state.mgStatus : null,
      mgDec: same ? this.state.mgDec : {},
      mgOpenNodes: same ? this.state.mgOpenNodes : {}
    });
    if (!same) this._mgAdvanced = null;
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

  // The chat's staged spreadsheets, brought here instead of being sent to the orchestrator.
  mgFromChat() {
    const files = (this.state.ccFiles || []).filter(isSpreadsheet);
    if (!files.length) return;
    this.setState((p) => ({ ccFiles: (p.ccFiles || []).filter((f) => !isSpreadsheet(f)), mgId: null, mgStatus: null, mgDec: {}, mgArmed: false }));
    this.mgPickFiles(files);
    this.mgShow();
  },

  async mgStart() {
    const files = this.state.mgFiles || [];
    if (!files.length || this.state.mgBusy) return;
    this.setState({ mgBusy: 'Uploading…', mgError: '' });
    try {
      const r = await schemaMapperApi.start(files, this.state.mgCmms || 'Custom', currentOrgId() || undefined);
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
      if (this.state.view === 'migration') this._mgTimer = setTimeout(() => this.mgPoll(), 6000);
      return;
    }
    this._mgPolling = false;
    if (this.state.mgId !== id) return;
    const prevGate = this.state.mgStatus ? this.state.mgStatus.pending_gate_type : null;
    const gateChanged = (doc.pending_gate_type || null) !== (prevGate || null);
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
    if (delay && this.state.view === 'migration') this._mgTimer = setTimeout(() => this.mgPoll(), delay);
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
      isMigration: s.signedIn && s.view === 'migration',
      openMigration: () => this.mgShow(),

      // upload panel
      mgFiles: files.map((f, i) => ({ key: i, name: f.name, size: fmtBytes(f.size), icon: /\.csv$|\.tsv$/i.test(f.name) ? 'ph-file-csv' : 'ph-file-xls', drop: () => this.mgDropFile(i) })),
      mgFilesEmpty: files.length === 0,
      mgFilesNote: files.length > 1 ? files.length + ' files go as ONE job, so keys between them can be found.' : files.length === 1 ? 'A workbook with several sheets is one job; each sheet becomes a table.' : 'CSV, XLSX or XLS. Several files at once are migrated as one job.',
      mgPickFiles: (e) => { this.mgPickFiles(e.target.files); e.target.value = ''; },
      mgDropFiles: (e) => { e.preventDefault(); this.mgPickFiles(e.dataTransfer && e.dataTransfer.files); },
      mgCmms: s.mgCmms || 'Custom',
      mgSetCmms: (e) => this.setState({ mgCmms: e.target.value }),
      mgCanStart: files.length > 0 && !busy,
      mgStart: () => this.mgStart(),
      mgStartLabel: busy ? s.mgBusy : 'Start migration',
      mgOrg: currentOrgId() || 'the default company',

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
      mgGateBlurb: kind === 'gate' ? text[1] : kind === 'step' ? 'This node has finished. It continues on its own unless you switch auto-continue off to read each result first.' : kind === 'done' ? 'Every gate was answered and the rows are in plenum_cafm. The artefacts are below.' : kind === 'failed' ? ((doc && doc.error_message) || 'The service reported a failure and gave no reason.') : 'The pipeline is working on the current node; this page follows it.',
      mgGateCount: payload.total_reviewable ? payload.total_reviewable + ' item' + (payload.total_reviewable === 1 ? '' : 's') + ' to review' : '',
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
