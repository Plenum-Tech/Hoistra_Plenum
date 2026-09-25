// api/schemaMapper — svc-ai-schema-mapper's migration pipeline (CSV / Excel → plenum_cafm).
// Routes: apps/backend/.../svc-ai-schema-mapper/src/app.py (/api/migration/*), mounted behind
// the gateway at /backend/schema-mapper/.
//
// One migration is one LangGraph run over one upload. The run stops in two ways and the
// status document says which: `status: "step_paused"` is a node that finished and is waiting
// to be told to continue (POST /advance — nothing to decide); `status: "awaiting_review"` is a
// human gate, named by `pending_gate_type`, with what there is to review in
// `pending_gate_payload`, and each gate has its own POST. `gate()` maps the gate NAME the
// status reports onto the PATH the gate is answered at, so the screen never hard-codes one.
//
// The bodies each gate accepts are read from the handlers, not guessed — see
// logic/migration.js's defaultGateBody for what is sent and why:
//   pk_approval               {pk_overrides: {SourceTable: "column"}}
//   unique_table_approval     {approved: true}
//   pre_semantic              {decisions: {SourceTable: [{source_field, decision}]}}
//   classification_approval   {rejected_groups: [G..], verdict_overrides: {G..: "fk"|"shared"}}
//   column_mapping_approval   {overrides: {SourceTable: {source_field: dest_col | "__new__"}}}
//   field_mapping             {flagged: {table: [...]}, unmapped: {table: [...]}}
//   hierarchy                 {confirmed_hierarchies: [...], hierarchy_corrections: [...]}
//   write (gate/final)        {confirmed: true|false}
//
// The upload itself is multipart. `start` picks the single- or multi-file route by count:
// several sheets sent together are ONE job (the multi route), which is what lets the
// pipeline see the foreign keys between them.
import { BASES, apiFetch } from './client.js';

const B = BASES.schemaMapper;
const enc = encodeURIComponent;

// The status document can run to ~500 KB once column intelligence is in it; the deployed
// service has answered in 10 s under load. Uploads wait longer still — parsing happens
// before the response.
const T_STATUS = 60000;
const T_UPLOAD = 180000;
const T_GATE = 90000;

// Gate name (as `pending_gate_type` reports it) → the path segment it is answered at.
export const GATE_PATHS = {
  pk_approval: 'pk-approval',
  unique_table_approval: 'unique-table-approval',
  pre_semantic: 'pre-semantic',
  classification_approval: 'classification-approval',
  column_mapping_approval: 'column-mapping-approval',
  field_mapping: 'field-mapping',
  hierarchy: 'hierarchy',
  write: 'final',
  final_confirmation: 'final'
};

export function gatePath(gateType) {
  const seg = GATE_PATHS[String(gateType || '').toLowerCase()];
  if (!seg) throw new Error('no gate route is known for "' + gateType + '"');
  return seg;
}

export const schemaMapperApi = {
  health: () => apiFetch(B, '/health', { timeoutMs: 6000 }),

  // POST /api/migration/start-with-upload{,-multi} → {migration_id, status, …}.
  start: (files, cmmsName, organizationId) => {
    const list = Array.from(files || []);
    if (!list.length) return Promise.reject(new Error('no file to migrate'));
    const form = new FormData();
    form.append('cmms_name', cmmsName || 'Custom');
    if (organizationId) form.append('organization_id', organizationId);
    if (list.length === 1) {
      form.append('file', list[0], list[0].name);
      return apiFetch(B, '/api/migration/start-with-upload', { method: 'POST', form: form, timeoutMs: T_UPLOAD });
    }
    list.forEach((f) => form.append('files', f, f.name));
    return apiFetch(B, '/api/migration/start-with-upload-multi', { method: 'POST', form: form, timeoutMs: T_UPLOAD });
  },

  // GET /api/migration/{id}/status → the whole status document (see logic/migration.js).
  status: (id) => apiFetch(B, '/api/migration/' + enc(id) + '/status', { timeoutMs: T_STATUS }),

  // GET /api/migration?organization_id=&limit= → {total_count, migrations: [{migration_id,
  // cmms_name, status, progress_pct, t1_count, t2_count, started_at, completed_at}]}.
  list: (organizationId, limit) =>
    apiFetch(B, '/api/migration', { query: { organization_id: organizationId || undefined, limit: limit || 12 }, timeoutMs: 30000 }),

  // POST /api/migration/{id}/advance — continue past a step_paused node.
  advance: (id) => apiFetch(B, '/api/migration/' + enc(id) + '/advance', { method: 'POST', timeoutMs: T_GATE }),

  // POST /api/migration/{id}/gate/<path> — answer the open human gate.
  gate: (id, gateType, body) =>
    apiFetch(B, '/api/migration/' + enc(id) + '/gate/' + gatePath(gateType), { method: 'POST', body: body || {}, timeoutMs: T_GATE }),

  // Where a finished run's artefacts are served from — the status document carries blob
  // URLs already; this is the service's own download route for when it does not.
  downloadUrl: (id, format) => B + '/api/migration/' + enc(id) + '/download/' + enc(format)
};
