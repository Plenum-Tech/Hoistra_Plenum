import type {
  MigrationState,
  MigrationListResponse,
  MappingListResponse,
  AuditEntry,
  SchemaMappingState,
  SchemaMappingListResponse,
  MappingRecord,
  UnmappedFieldsResponse,
  SchemaAuditEntry,
  ExtraFieldConfig,
  DocRagDocument,
  DocRagUploadResponse,
  DocRagChunk,
  DocRagQueryResponse,
  DocRagMatchRowsResponse,
  RowIndexTable,
  RowIndexUploadResponse,
  DbTable,
  DbTableColumn,
} from './types'

// Base URL — use proxy path in dev (/api → localhost:8003), full URL in prod
let _apiBase = ''

export function setApiBase(base: string) {
  // Strip trailing slash; empty string means use Vite proxy
  _apiBase = base.replace(/\/$/, '')
}

function url(path: string) {
  return `${_apiBase}${path}`
}

// Connector service base URL (cafm-connector-service, default port 8000)
let _connectorBase = 'http://127.0.0.1:8000'

export function setConnectorBase(base: string) {
  _connectorBase = base.replace(/\/$/, '')
}

export function getConnectorBase(): string {
  return _connectorBase
}

function connectorUrl(path: string) {
  return `${_connectorBase}${path}`
}

// Table Editor service base URL (standalone service, default port 8005)
// Local:  http://127.0.0.1:8005
// Azure:  https://your-host/table-editor  (reverse proxy strips the prefix)
let _tableEditorBase = 'http://127.0.0.1:8005'

export function setTableEditorBase(base: string) {
  _tableEditorBase = base.replace(/\/$/, '')
}

export function getTableEditorBase(): string {
  return _tableEditorBase
}

function tableEditorUrl(path: string) {
  return `${_tableEditorBase}${path}`
}

async function handleResponse<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText
    try {
      const body = await res.json()
      detail = body.detail ?? JSON.stringify(body)
    } catch { /* ignore */ }
    throw new Error(`${res.status}: ${detail}`)
  }
  return res.json() as Promise<T>
}

// ═══════════════════════════════════════════════════════════════════════════════
// Flow 1 — Ingestor  (/api/migration/...)
// ═══════════════════════════════════════════════════════════════════════════════

// ── Start ─────────────────────────────────────────────────────────────────────

export interface StartMigrationParams {
  file: File
  orgId: string
  cmmsName: string
}

export async function startMigration({
  file,
  orgId,
  cmmsName,
}: StartMigrationParams): Promise<{ migration_id: string; status: string; message: string }> {
  const form = new FormData()
  form.append('file', file)
  form.append('organization_id', orgId)
  form.append('cmms_name', cmmsName)

  const res = await fetch(url('/api/migration/start-with-upload'), {
    method: 'POST',
    body: form,
  })
  return handleResponse(res)
}

// ── Poll status (includes nodes[]) ───────────────────────────────────────────

export async function getMigrationStatus(migrationId: string): Promise<MigrationState> {
  const res = await fetch(url(`/api/migration/${migrationId}/status`))
  return handleResponse(res)
}

export interface RuntimeLogEntry {
  seq: number
  ts: string
  level: string
  logger: string
  message: string
  migration_id?: string | null
  schema_mapping_id?: string | null
}

export interface RuntimeLogsResponse {
  logs: RuntimeLogEntry[]
  next_since: number
}

export async function getMigrationRuntimeLogs(
  migrationId: string,
  since = 0,
  limit = 200,
): Promise<RuntimeLogsResponse> {
  const res = await fetch(
    url(`/api/migration/${migrationId}/runtime-logs?since=${since}&limit=${limit}`),
  )
  return handleResponse(res)
}

// ── Advance past step_paused node ─────────────────────────────────────────────

export async function advancePipeline(migrationId: string): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/advance`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({}),
  })
  return handleResponse(res)
}

// ── Gate 0: Pre-Semantic ──────────────────────────────────────────────────────

export async function submitGatePreSemantic(
  migrationId: string,
  body: { decisions: Record<string, Array<{ source_field: string; decision: string }>> },
): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/gate/pre-semantic`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Gate 1: Field Mapping ─────────────────────────────────────────────────────

export async function submitGateFieldMapping(
  migrationId: string,
  body: {
    decisions: {
      flagged: Record<string, Array<Record<string, any>>>
      unmapped: Record<string, Array<Record<string, any>>>
    }
  },
): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/gate/field-mapping`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Gate 2: Hierarchy ─────────────────────────────────────────────────────────

export async function submitGateHierarchy(
  migrationId: string,
  body: {
    decisions: Array<{
      id: string
      type: string
      action: string
      modified_target?: string
    }>
  },
): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/gate/hierarchy`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Gate 3: Final confirmation ────────────────────────────────────────────────

export async function submitGateFinal(
  migrationId: string,
  body: { decisions: { confirmed: boolean } },
): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/gate/final`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Retry DDL ─────────────────────────────────────────────────────────────────

export async function retryMigrationDdl(
  migrationId: string,
  extraFieldsConfig: ExtraFieldConfig[],
): Promise<void> {
  const res = await fetch(url(`/api/migration/${migrationId}/retry-ddl`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extra_fields_config: extraFieldsConfig }),
  })
  return handleResponse(res)
}

// ── Read-only data endpoints ──────────────────────────────────────────────────

export async function getMigrationMappings(
  migrationId: string,
  tier?: string,
): Promise<MappingListResponse> {
  const params = tier ? `?tier=${encodeURIComponent(tier)}` : ''
  const res = await fetch(url(`/api/migration/${migrationId}/mappings${params}`))
  return handleResponse(res)
}

export async function getMigrationHierarchy(migrationId: string): Promise<any> {
  const res = await fetch(url(`/api/migration/${migrationId}/hierarchy`))
  return handleResponse(res)
}

export async function getMigrationAudit(migrationId: string): Promise<AuditEntry[]> {
  const res = await fetch(url(`/api/migration/${migrationId}/audit`))
  return handleResponse(res)
}

// ── Download ──────────────────────────────────────────────────────────────────

export type MigrationDownloadFormat = 'json' | 'csv' | 'sql' | 'pdf'

// Returns a signed download URL — use as href or window.open()
export async function getMigrationDownload(
  migrationId: string,
  format: MigrationDownloadFormat,
): Promise<{ download_url: string; expires_in_minutes: number }> {
  const res = await fetch(url(`/api/migration/${migrationId}/download/${format}`))
  return handleResponse(res)
}

// ── List + delete ─────────────────────────────────────────────────────────────

export interface ListMigrationsParams {
  organizationId?: string
  status?: string
  limit?: number
  offset?: number
}

export async function listMigrations(params: ListMigrationsParams = {}): Promise<MigrationListResponse> {
  const q = new URLSearchParams()
  if (params.organizationId) q.set('organization_id', params.organizationId)
  if (params.status)         q.set('status', params.status)
  if (params.limit != null)  q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const res = await fetch(url(`/api/migration${q.size ? `?${q}` : ''}`))
  return handleResponse(res)
}

export async function deleteMigration(migrationId: string): Promise<{ status: string; message: string }> {
  const res = await fetch(url(`/api/migration/${migrationId}`), { method: 'DELETE' })
  return handleResponse(res)
}

// ── WebSocket ─────────────────────────────────────────────────────────────────

export function createMigrationSocket(migrationId: string): WebSocket {
  const wsBase = _apiBase
    ? _apiBase.replace(/^https?/, m => (m === 'https' ? 'wss' : 'ws'))
    : `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}`
  return new WebSocket(`${wsBase}/ws/migration/${migrationId}`)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Flow 2 — Schema Mapper  (/api/schema-mapping/...)
// ═══════════════════════════════════════════════════════════════════════════════

// ── Start ─────────────────────────────────────────────────────────────────────

export interface StartSchemaMappingParams {
  connectorType: 'upload' | 'fiix'
  externalCmmsName?: string
  organizationId?: string
  // fiix credentials
  fiixSubdomain?: string
  fiixAppKey?: string
  fiixAccessKey?: string
  fiixSecretKey?: string
  // upload connector
  schemaContent?: string
  schemaFormat?: 'yaml' | 'json' | 'sql' | 'db_url'
  schemaSource?: string
  // db_url sub-option
  dbUrl?: string
}

export async function startSchemaMapping(
  params: StartSchemaMappingParams,
): Promise<{ schema_mapping_id: string; status: string }> {
  const body: Record<string, any> = {
    connector_type: params.connectorType,
    external_cmms_name: params.externalCmmsName ?? 'Unknown',
    organization_id: params.organizationId,
  }

  if (params.connectorType === 'fiix') {
    body.fiix_subdomain = params.fiixSubdomain
    body.fiix_app_key   = params.fiixAppKey
    body.fiix_access_key = params.fiixAccessKey
    body.fiix_secret_key = params.fiixSecretKey
  }

  if (params.connectorType === 'upload') {
    body.schema_content = params.schemaContent
    body.schema_format  = params.schemaFormat ?? 'yaml'
    body.schema_source  = params.schemaSource ?? 'yaml_file'
  }

  if (params.dbUrl) {
    body.db_url = params.dbUrl
  }

  const res = await fetch(url('/api/schema-mapping'), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Poll status (includes nodes[]) ────────────────────────────────────────────

export async function getSchemaMappingStatus(sessionId: string): Promise<SchemaMappingState> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/status`))
  return handleResponse(res)
}

export async function getSchemaMappingRuntimeLogs(
  sessionId: string,
  since = 0,
  limit = 200,
): Promise<RuntimeLogsResponse> {
  const res = await fetch(
    url(`/api/schema-mapping/${sessionId}/runtime-logs?since=${since}&limit=${limit}`),
  )
  return handleResponse(res)
}

// ── Advance past step_paused node ─────────────────────────────────────────────

export async function advanceSchemaMapping(sessionId: string, body?: Record<string, any>): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/advance`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  return handleResponse(res)
}

// ── Gate 1: Field Mapping ─────────────────────────────────────────────────────

export type SchemaFieldMappingDecision =
  | { action: 'accept';       source_field: string; source_table: string }
  | { action: 'reject';       source_field: string; source_table: string }
  | { action: 'override';     source_field: string; source_table: string; target_field: string; rationale?: string }
  | { action: 'custom';       source_field: string; source_table: string; target_table: string; custom_column_name: string; data_type: string }
  | { action: 'raw_metadata'; source_field: string; source_table: string }
  | { action: 'skip';         source_field: string; source_table: string }

export async function submitSchemaGateFieldMapping(
  sessionId: string,
  decisions: SchemaFieldMappingDecision[],
): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/gate/field-mapping`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  return handleResponse(res)
}

// ── Gate 2: Hierarchy ─────────────────────────────────────────────────────────

export async function submitSchemaGateHierarchy(
  sessionId: string,
  body: {
    approved_foreign_keys: Array<{
      source_table: string
      source_column: string
      target_table: string
      target_column?: string
      user_confirmed?: boolean
      [key: string]: any
    }>
    rejected_foreign_keys: Array<{
      source_table: string
      source_column: string
      target_table: string
      [key: string]: any
    }>
    reviewer_notes: string
  },
): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/gate/hierarchy`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Retry DDL ─────────────────────────────────────────────────────────────────

export async function retrySchemaMappingDdl(
  sessionId: string,
  extraFieldsConfig: ExtraFieldConfig[],
): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/retry-ddl`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ extra_fields_config: extraFieldsConfig }),
  })
  return handleResponse(res)
}

// ── Custom mapping (multipart/form-data) ──────────────────────────────────────

export async function submitSchemaCustomMapping(
  sessionId: string,
  mapping: { source_field: string; source_table: string; target_field: string; rationale?: string },
): Promise<{ tier: string; confidence: number; status: string }> {
  const form = new FormData()
  form.append('source_field', mapping.source_field)
  form.append('source_table', mapping.source_table)
  form.append('target_field', mapping.target_field)
  if (mapping.rationale) form.append('rationale', mapping.rationale)

  const res = await fetch(url(`/api/schema-mapping/${sessionId}/custom-mapping`), {
    method: 'POST',
    body: form,
  })
  return handleResponse(res)
}

// ── Read-only data endpoints ──────────────────────────────────────────────────

export async function getSchemaMappings(
  sessionId: string,
  tier?: string,
): Promise<MappingListResponse> {
  const params = tier ? `?tier=${encodeURIComponent(tier)}` : ''
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/mappings${params}`))
  return handleResponse(res)
}

export async function getSchemaUnmapped(sessionId: string): Promise<UnmappedFieldsResponse> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/unmapped`))
  return handleResponse(res)
}

export async function getSchemaMappingAuditTrail(sessionId: string): Promise<SchemaAuditEntry[]> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/audit-trail`))
  return handleResponse(res)
}

// ── List + delete ─────────────────────────────────────────────────────────────

export interface ListSchemaMappingsParams {
  organizationId?: string
  status?: string
  limit?: number
  offset?: number
}

export async function listSchemaMappings(
  params: ListSchemaMappingsParams = {},
): Promise<SchemaMappingListResponse> {
  const q = new URLSearchParams()
  if (params.organizationId) q.set('organization_id', params.organizationId)
  if (params.status)         q.set('status', params.status)
  if (params.limit != null)  q.set('limit', String(params.limit))
  if (params.offset != null) q.set('offset', String(params.offset))
  const res = await fetch(url(`/api/schema-mapping${q.size ? `?${q}` : ''}`))
  return handleResponse(res)
}

export async function deleteSchemaMapping(
  sessionId: string,
): Promise<{ status: string; message: string }> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}`), { method: 'DELETE' })
  return handleResponse(res)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Flow 3 — Doc RAG  (/documents/... and /rag/...)
// ═══════════════════════════════════════════════════════════════════════════════

let _docRagBase = 'http://127.0.0.1:8004'

export function setDocRagBase(base: string) {
  _docRagBase = base.replace(/\/$/, '')
}

export function getDocRagBase(): string {
  return _docRagBase
}

function docUrl(path: string) {
  return `${_docRagBase}${path}`
}

// ── Documents ─────────────────────────────────────────────────────────────────

export async function uploadDocument(file: File): Promise<DocRagUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  const res = await fetch(docUrl('/documents/upload'), { method: 'POST', body: form })
  return handleResponse<DocRagUploadResponse>(res)
}

export async function listDocuments(): Promise<DocRagDocument[]> {
  const res = await fetch(docUrl('/documents'))
  return handleResponse<DocRagDocument[]>(res)
}

export async function getDocument(documentId: string): Promise<DocRagDocument> {
  const res = await fetch(docUrl(`/documents/${documentId}`))
  return handleResponse<DocRagDocument>(res)
}

export async function deleteDocument(documentId: string): Promise<void> {
  const res = await fetch(docUrl(`/documents/${documentId}`), { method: 'DELETE' })
  return handleResponse<void>(res)
}

export async function getDocumentChunks(
  documentId: string,
  limit = 50,
): Promise<DocRagChunk[]> {
  const res = await fetch(docUrl(`/documents/${documentId}/chunks?limit=${limit}`))
  return handleResponse<DocRagChunk[]>(res)
}

// ── RAG query ─────────────────────────────────────────────────────────────────

export interface RagQueryParams {
  query: string
  top_k?: number
  filters?: Record<string, any>
  user_id?: string
  session_id?: string
  debug?: boolean
}

export async function ragQuery(params: RagQueryParams): Promise<DocRagQueryResponse> {
  const { debug = false, ...body } = params
  const endpoint = debug ? '/rag/debug' : '/rag/query'
  const res = await fetch(docUrl(endpoint), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse<DocRagQueryResponse>(res)
}

// ── Match rows ────────────────────────────────────────────────────────────────

export async function matchDocumentRows(
  documentId: string,
  options: {
    confidence_threshold?: number
    group_by_table?: boolean
    source_table?: string
  } = {},
): Promise<DocRagMatchRowsResponse> {
  const params = new URLSearchParams()
  if (options.confidence_threshold != null)
    params.set('confidence_threshold', String(options.confidence_threshold))
  if (options.group_by_table != null)
    params.set('group_by_table', String(options.group_by_table))
  if (options.source_table)
    params.set('source_table', options.source_table)
  const qs = params.size ? `?${params}` : ''
  const res = await fetch(docUrl(`/documents/${documentId}/match-rows${qs}`), {
    method: 'POST',
  })
  return handleResponse<DocRagMatchRowsResponse>(res)
}

export async function matchDocumentRowsFromFile(
  documentId: string,
  file: File,
  options: {
    pk_column?: string
    source_table?: string
    confidence_threshold?: number
    group_by_table?: boolean
  } = {},
): Promise<DocRagMatchRowsResponse> {
  const form = new FormData()
  form.append('file', file)
  if (options.pk_column)            form.append('pk_column', options.pk_column)
  if (options.source_table)         form.append('source_table', options.source_table)
  if (options.confidence_threshold != null)
    form.append('confidence_threshold', String(options.confidence_threshold))
  if (options.group_by_table != null)
    form.append('group_by_table', String(options.group_by_table))
  const res = await fetch(docUrl(`/documents/${documentId}/match-rows/from-file`), {
    method: 'POST',
    body: form,
  })
  return handleResponse<DocRagMatchRowsResponse>(res)
}

// ── Confirm matches — write document_id back to CMMS rows ────────────────────

export interface ConfirmMatchesResult {
  document_id: string
  file_name: string
  rows_updated: number
  rows_not_found: number
  by_table: Record<string, number>
  columns_created: string[]
  latency_ms: number
}

export async function confirmDocumentMatches(
  documentId: string,
  confirmedRows: Array<{ source_table: string; row_pk: string }>,
): Promise<ConfirmMatchesResult> {
  const res = await fetch(docUrl(`/documents/${documentId}/confirm-matches`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ confirmed_rows: confirmedRows }),
  })
  return handleResponse<ConfirmMatchesResult>(res)
}

// ── Row index management ───────────────────────────────────────────────────────

export async function listRowIndexTables(): Promise<RowIndexTable[]> {
  const res = await fetch(docUrl('/row-index/tables'))
  return handleResponse<RowIndexTable[]>(res)
}

export async function uploadRowIndexFile(
  file: File,
  tableName: string,
  pkColumn: string,
): Promise<RowIndexUploadResponse> {
  const form = new FormData()
  form.append('file', file)
  form.append('table_name', tableName)
  form.append('pk_column', pkColumn)
  const res = await fetch(docUrl('/row-index/upload'), { method: 'POST', body: form })
  return handleResponse<RowIndexUploadResponse>(res)
}

export async function deleteRowIndexTable(tableName: string): Promise<void> {
  const res = await fetch(docUrl(`/row-index/tables/${encodeURIComponent(tableName)}`), {
    method: 'DELETE',
  })
  return handleResponse<void>(res)
}

export async function listDbTables(): Promise<DbTable[]> {
  const res = await fetch(docUrl('/row-index/db-tables'))
  return handleResponse<DbTable[]>(res)
}

export async function getDbTableColumns(tableName: string): Promise<DbTableColumn[]> {
  const res = await fetch(docUrl(`/row-index/db-tables/${encodeURIComponent(tableName)}/columns`))
  return handleResponse<DbTableColumn[]>(res)
}

export async function importDbTable(
  tableName: string,
  pkColumn: string,
  rowLimit = 10000,
): Promise<RowIndexUploadResponse> {
  const form = new FormData()
  form.append('table_name', tableName)
  form.append('pk_column', pkColumn)
  form.append('row_limit', String(rowLimit))
  const res = await fetch(docUrl('/row-index/import-db-table'), { method: 'POST', body: form })
  return handleResponse<RowIndexUploadResponse>(res)
}

// ═══════════════════════════════════════════════════════════════════════════════
// Flow 4 — Table Customizer  (/api/v1/plenum/tables/...)
// ═══════════════════════════════════════════════════════════════════════════════

export interface TableInfo {
  table: string
  row_estimate: number
}

export interface ColumnDef {
  name: string
  type: string
  nullable: boolean
  default: string | null
  max_length: number | null
}

export interface TableRowsResponse {
  total: number
  limit: number
  offset: number
  columns: string[]
  rows: Record<string, any>[]
}

export async function listPlenumTables(): Promise<TableInfo[]> {
  const res = await fetch(tableEditorUrl('/tables'))
  return handleResponse<TableInfo[]>(res)
}

export async function getPlenumTableColumns(table: string): Promise<ColumnDef[]> {
  const res = await fetch(tableEditorUrl(`/tables/${encodeURIComponent(table)}/columns`))
  return handleResponse<ColumnDef[]>(res)
}

export async function getPlenumTableRows(
  table: string,
  limit = 20,
  offset = 0,
): Promise<TableRowsResponse> {
  const res = await fetch(
    tableEditorUrl(`/tables/${encodeURIComponent(table)}/rows?limit=${limit}&offset=${offset}`),
  )
  return handleResponse<TableRowsResponse>(res)
}

export async function createPlenumRow(
  table: string,
  data: Record<string, any>,
): Promise<Record<string, any>> {
  const res = await fetch(tableEditorUrl(`/tables/${encodeURIComponent(table)}/rows`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data }),
  })
  return handleResponse<Record<string, any>>(res)
}

export async function updatePlenumRow(
  table: string,
  rowId: string,
  data: Record<string, any>,
): Promise<Record<string, any>> {
  const res = await fetch(
    tableEditorUrl(`/tables/${encodeURIComponent(table)}/rows/${encodeURIComponent(rowId)}`),
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ data }),
    },
  )
  return handleResponse<Record<string, any>>(res)
}

export async function deletePlenumRow(table: string, rowId: string): Promise<void> {
  const res = await fetch(
    tableEditorUrl(`/tables/${encodeURIComponent(table)}/rows/${encodeURIComponent(rowId)}`),
    { method: 'DELETE' },
  )
  if (!res.ok && res.status !== 204) return handleResponse<void>(res)
}

export interface AddColumnParams {
  column_name: string
  data_type: string
  nullable?: boolean
  default?: string
}

export async function addPlenumColumn(table: string, params: AddColumnParams): Promise<void> {
  const res = await fetch(
    tableEditorUrl(`/tables/${encodeURIComponent(table)}/columns?confirm=true`),
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(params),
    },
  )
  return handleResponse<void>(res)
}

export async function dropPlenumColumn(table: string, col: string): Promise<void> {
  const res = await fetch(
    tableEditorUrl(
      `/tables/${encodeURIComponent(table)}/columns/${encodeURIComponent(col)}?confirm=true`,
    ),
    { method: 'DELETE' },
  )
  if (!res.ok && res.status !== 204) return handleResponse<void>(res)
}

// ── Gate 3: Artifacts review ──────────────────────────────────────────────────

export async function submitSchemaGateArtifacts(
  sessionId: string,
  body: { new_schema_name: string },
): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/gate/artifacts-review`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  return handleResponse(res)
}

// ── Pre-semantic gate (kept for Ingestor backward compat) ─────────────────────

export async function submitSchemaGatePreSemantic(
  sessionId: string,
  // Backend reads d.get("decision") with values "approve" | "semantic"
  decisions: Array<{ source_field: string; source_table: string; decision: string }>,
): Promise<void> {
  const res = await fetch(url(`/api/schema-mapping/${sessionId}/gate/pre-semantic`), {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ decisions }),
  })
  return handleResponse(res)
}
