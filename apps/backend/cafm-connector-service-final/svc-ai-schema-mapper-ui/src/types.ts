// ── Node status (shared by both flows) ───────────────────────────────────────

export type NodeStatus = 'complete' | 'running' | 'pending'

// Output shapes are node-specific — typed loosely per flow below.
// The `logs` array is always a flat list of human-readable strings.

export interface NodeInfo {
  node_id: number
  node_name: string
  status: NodeStatus
  started_at: string | null
  completed_at: string | null
  duration_ms: number | null
  output: Record<string, any> | null
  logs: string[]
}

// ── Ingestor node output shapes ───────────────────────────────────────────────
// node_id 1–9 for the Ingestor pipeline

export interface IngestorNode1Output {   // File Ingestion
  row_count: number
  column_count: number
  table_count: number
  tables: string[]
  detected_format: string
}

export interface IngestorNode2Output {   // Deterministic Mapping
  total_columns: number
  tier1_mapped: number
  unresolved: number
  coverage_pct: number
  overall_confidence: number
}

export interface IngestorNode3Output {   // Gate 0: Pre-Semantic Review
  approved: number
  sent_to_semantic: number
  updated_tier1_count: number
}

export interface IngestorNode4Output {   // Semantic Mapping
  tier2_auto_mapped: number
  tier2_flagged: number
  unmappable: number
  overall_confidence: number
}

export interface IngestorNode5Output {   // Gate 1: Field Mapping Review
  decisions_processed: number
  tier2_human_count: number
  extra_fields_config_count: number
  overall_confidence: number
}

export interface IngestorNode6Output {   // Data Preprocessing
  total_original_rows: number
  total_cleaned_rows: number
  dedup_ratio: number
  table_count: number
  warning_count: number
}

export interface IngestorNode7Output {   // Hierarchy Detection
  hierarchy_count: number
  cycle_count: number
  orphan_count: number
  max_depth: number
  table_count: number
}

export interface IngestorNode8Output {   // Gate 2: Hierarchy Verification
  confirmed_hierarchy_count: number
  corrections_applied: number
  hierarchy_confirmed: boolean
}

export interface IngestorNode9Output {   // Output Generation
  table_count: number
  artifacts_uploaded: number
  formats: string[]
  json_url: string | null
  csv_url: string | null
  sql_url: string | null
  report_url: string | null
}

// ── Schema Mapper node output shapes ─────────────────────────────────────────
// node_id 0–7 for the Schema Mapper pipeline

export interface SchemaNode0Output {   // Canonical Schema Fetch
  canonical_table_count: number
  canonical_column_count: number
}

export interface SchemaNode1Output {   // Schema Ingestion
  table_count: number
  total_columns: number
  tables: string[]
}

export interface SchemaNode2Output {   // Deterministic Mapping
  total_columns: number
  tier1_mapped: number
  unresolved: number
  coverage_pct: number
}

export interface SchemaNode3Output {   // Semantic Mapping
  tier2_auto_mapped: number
  tier2_flagged: number
  unmappable: number
  overall_confidence: number
}

export interface SchemaNode4Output {   // Gate 1: Field Mapping Review
  decisions_received: number
  approved: number
  rejected: number
  custom_ddl: number
  skipped: number
}

export interface SchemaNode5Output {   // Hierarchy Detection
  total_fks: number
  canonical_backed_fks: number
  hierarchy_count: number
  junction_table_count: number
  horizontal_relationship_count: number
  isolated_table_count: number
}

export interface SchemaNode6Output {   // Gate 2: Hierarchy Verification
  approved_fks: number
  rejected_fks: number
  final_fk_count: number
  hierarchy_user_modified: boolean
  reviewer_notes: string
}

export interface SchemaNode7Output {   // Output Generation
  canonical_fields_count: number
  total_source_fields: number
  tier1_auto_mapped: number
  tier2_auto_mapped: number
  tier2_flagged: number
  unmappable: number
  mapping_coverage_pct: number
  detected_fk_count: number
  hierarchy_count: number
  max_hierarchy_depth: number
}

// ── Migration status ──────────────────────────────────────────────────────────

export type MigrationStatus =
  | 'running'
  | 'step_paused'
  | 'awaiting_review'
  | 'complete'
  | 'failed'
  | 'ddl_failed'
  | 'cancelled'

export interface MigrationState {
  migration_id: string
  status: MigrationStatus
  progress_pct: number
  current_step: number
  cmms_name: string
  started_at: string
  completed_at: string | null
  t1_mapped_count: number
  t2_auto_count: number
  t2_human_count: number
  unmapped_count: number
  total_fields: number
  output_json_url: string | null
  output_csv_url: string | null
  output_sql_url: string | null
  migration_report_url: string | null
  pending_gate_type: string | null
  pending_gate_payload: IngestorFieldMappingGatePayload | IngestorHierarchyGatePayload | null
  error_message: string | null
  nodes: NodeInfo[]  // 9 entries, node_id 1–9
}

// ── Ingestor gate payloads (from pending_gate_payload) ───────────────────────

export interface IngestorFlaggedField {
  source_field: string
  source_table: string
  suggested_target: string
  confidence: number
  alternatives: string[]
}

export interface IngestorUnmappedField {
  source_field: string
  source_table: string
}

export interface IngestorFieldMappingGatePayload {
  flagged: IngestorFlaggedField[]
  unmapped: IngestorUnmappedField[]
}

export interface IngestorHierarchyNode {
  table: string
  children: IngestorHierarchyNode[]
  parent_fk_field?: string
}

export interface IngestorJunctionTable {
  table_name: string
  left_table: string
  right_table: string
  confidence: number
}

export interface IngestorHorizontalRelationship {
  source_table: string
  target_table: string
  relationship_type: string
  confidence: number
}

export interface IngestorForeignKey {
  source_table: string
  source_column: string
  target_table: string
  target_column: string
  confidence: number
  canonical_backed: boolean
}

export interface IngestorHierarchyGatePayload {
  hierarchy_forest: IngestorHierarchyNode[]
  junction_tables: IngestorJunctionTable[]
  horizontal_relationships: IngestorHorizontalRelationship[]
  isolated_tables: string[]
  foreign_keys: IngestorForeignKey[]
}

// ── Pipeline steps (the visual stepper) ──────────────────────────────────────

export type StepState = 'waiting' | 'running' | 'paused' | 'complete' | 'error'

export interface PipelineStep {
  id: string
  label: string
  sublabel: string
  isGate: boolean
  gateType?: string
  stepKey?: string
  nodeNum?: number
}

// ── Legacy gate payload shapes (Ingestor — kept for backward compat) ──────────

export interface PreSemanticItem {
  source_field: string
  target_field: string
  confidence: number
  tier: string
  rationale?: string
  sample_values?: string[]
}

export interface PreSemanticPayload {
  total_reviewable: number
  review_items_by_table: Record<string, PreSemanticItem[]>
}

export interface FlaggedItem {
  source_field: string
  target_field: string
  suggested_target?: string
  confidence: number
  rationale?: string
  sample_values?: string[]
  suggestions?: string[]
}

export interface UnmappedItem {
  source_field: string
  sample_values?: string[]
}

export interface FieldMappingPayload {
  total_flagged: number
  total_unmappable: number
  confidence_alert?: { message: string }
  review_items_by_table: Record<string, FlaggedItem[]>
  unmappable_items_by_table: Record<string, UnmappedItem[]>
  existing_canonical_tables?: string[]
}

export interface HierarchyItem {
  id: string
  type: 'fk' | 'cycle' | 'orphan' | 'implicit' | 'hierarchy' | 'cycle_alert' | 'orphaned_records' | 'implicit_hierarchy'
  source_table?: string
  source_column?: string
  target_table?: string
  target_column?: string
  relationship_type?: string
  confidence?: number
  reasoning?: string
  description?: string
  suggested_action?: 'confirm' | 'reject'
  data_match_rate?: number
  message?: string
  cycle?: string[]
  sample_rows?: Record<string, any>[]
}

export interface HierarchyPayload {
  total_hierarchies: number
  total_cycles: number
  total_orphans: number
  hierarchy_tree?: string
  review_items: HierarchyItem[]
}

export interface FinalPayload {
  summary: {
    source_filename: string
    overall_confidence: number
    total_entities: number
    entity_counts: Record<string, number>
  }
}

export interface StepPayload {
  node: number | string
  label: string
  [key: string]: any
}

// ── Schema Mapping workflow ───────────────────────────────────────────────────

export type SchemaMappingStatus =
  | 'ingest'
  | 'running'
  | 'step_paused'
  | 'awaiting_review'
  | 'complete'
  | 'error'
  | 'ddl_failed'
  | 'cancelled'

export interface SchemaMappingState {
  schema_mapping_id: string
  status: SchemaMappingStatus
  current_node: number
  progress_pct: number
  external_cmms_name: string
  started_at: string
  completed_at: string | null
  stats: {
    total_tables: number | null
    total_fields: number | null
    tier1_mapped: number | null
    tier2_auto_mapped: number | null
    tier2_flagged: number | null
    unmapped: number | null
    detected_fk_count: number | null
    hierarchy_depth: number | null
    mapping_coverage_pct: number | null
  }
  error_message: string | null
  pending_gate_type: 'field_mapping' | 'hierarchy' | 'pre_semantic' | 'artifacts_review' | null
  pending_gate_payload: SchemaFieldMappingGatePayload | SchemaHierarchyGatePayload | null
  ddl_error: string | null
  nodes: NodeInfo[]  // 11 entries, node_id 0–10
}

// ── Schema Mapper gate payloads (from pending_gate_payload) ──────────────────

export interface SchemaFlaggedField {
  source_field: string
  source_table: string
  suggested_target: string
  confidence: number
  alternatives: string[]
}

export interface SchemaUnmappedFieldItem {
  source_field: string
  source_table: string
}

export interface SchemaFieldMappingGatePayload {
  flagged: SchemaFlaggedField[]
  unmapped: SchemaUnmappedFieldItem[]
}

export interface SchemaHierarchyNode {
  table: string
  children: SchemaHierarchyNode[]
  parent_fk_field?: string
}

export interface SchemaJunctionTable {
  table_name: string
  left_table: string
  right_table: string
  confidence: number
}

export interface SchemaHorizontalRelationship {
  source_table: string
  target_table: string
  relationship_type: string
  confidence: number
}

export interface SchemaForeignKey {
  source_table: string
  source_column: string
  target_table: string
  target_column: string
  confidence: number
  canonical_backed: boolean
}

export interface SchemaHierarchyGatePayload {
  hierarchy_forest: SchemaHierarchyNode[]
  junction_tables: SchemaJunctionTable[]
  horizontal_relationships: SchemaHorizontalRelationship[]
  isolated_tables: string[]
  foreign_keys: SchemaForeignKey[]
}

// ── Schema Gate 1 (legacy shape — some backends still use this) ───────────────

export interface SchemaLowConfidenceItem {
  source_field: string
  suggested_target: string
  confidence: number
  rationale?: string
  tier: 'T1' | 'T2'
}

export interface SchemaUnmappedItem {
  source_field: string
  data_type_hint?: string
  nullable?: boolean
  description?: string
  actions_available: string[]
}

export interface SchemaGate1Payload {
  schema_mapping_id: string
  total_flagged: number
  low_confidence_tier1: Record<string, SchemaLowConfidenceItem[]>
  low_confidence_tier2: Record<string, SchemaLowConfidenceItem[]>
  unmapped_fields: Record<string, SchemaUnmappedItem[]>
  existing_canonical_tables?: string[]
  instructions?: {
    low_confidence: string
    unmapped: string
  }
}

// ── Schema Gate 2 (legacy shape) ─────────────────────────────────────────────

export interface SchemaFKItem {
  source_table: string
  source_column: string
  target_table: string
  target_column: string
  relationship_type?: string
  confidence?: number
  reasoning?: string
  user_confirmed?: boolean
}

export interface SchemaHierarchyTreeNode {
  table_name: string
  primary_key?: string
  parent_fk_field?: string
  level?: number
  canonical_table?: string
  self_referential_column?: string
  children_count?: number
  children?: SchemaHierarchyTreeNode[]
}

export interface SchemaJunctionTableItem {
  table_name: string
  left_table: string
  left_fk_column: string
  right_table: string
  right_fk_column: string
  confidence?: number
  reasoning?: string
}

export interface SchemaHorizontalRelItem {
  source_table: string
  target_table: string
  relationship_type?: string
  via_table?: string
  shared_parent?: string
  source_fk_column?: string
  confidence?: number
  reasoning?: string
}

export interface SchemaGate2Payload {
  schema_mapping_id: string
  detected_foreign_keys: SchemaFKItem[]
  hierarchy_forest?: SchemaHierarchyTreeNode[]
  hierarchy_tree?: any
  junction_tables?: SchemaJunctionTableItem[]
  horizontal_relationships?: SchemaHorizontalRelItem[]
  isolated_tables?: string[]
  implicit_hierarchies?: Record<string, any>
  summary?: {
    total_fks: number
    total_implicit: number
    hierarchy_depth?: number
    max_hierarchy_depth?: number
    hierarchy_count?: number
    junction_table_count?: number
    horizontal_relationship_count?: number
    isolated_table_count?: number
    canonical_backed_fks?: number
  }
  instructions?: string
  action_required?: string
}

// ── Migration list ────────────────────────────────────────────────────────────

export interface MigrationSummary {
  migration_id: string
  cmms_name: string
  status: MigrationStatus
  progress_pct: number
  started_at: string
  completed_at: string | null
  t1_count: number
  t2_count: number
}

export interface MigrationListResponse {
  total_count: number
  migrations: MigrationSummary[]
}

// ── Migration mappings ────────────────────────────────────────────────────────

export interface MappingRecord {
  source_field: string
  source_table?: string
  target_field: string | null
  confidence: number
  tier: string
  rationale?: string
  decided_at?: string
  reviewer_id?: string | null
}

export interface MappingListResponse {
  migration_id?: string
  schema_mapping_id?: string
  total_mappings: number
  tier_breakdown: Record<string, number>
  mappings: MappingRecord[]
}

// ── Migration audit ───────────────────────────────────────────────────────────

export interface AuditEntry {
  timestamp: string
  event: string
  node?: number
  details?: Record<string, any>
}

// ── Schema mapping list ───────────────────────────────────────────────────────

export interface SchemaMappingSummary {
  schema_mapping_id: string
  external_cmms_name: string
  status: SchemaMappingStatus
  progress_pct: number
  started_at: string
  completed_at: string | null
  stats: SchemaMappingState['stats']
}

export interface SchemaMappingListResponse {
  total_count: number
  sessions: SchemaMappingSummary[]
}

// ── Schema unmapped field ─────────────────────────────────────────────────────

export interface UnmappedField {
  source_table: string
  source_field: string
  data_type_hint?: string
  sample_values?: string[]
  nullable?: boolean
}

export interface UnmappedFieldsResponse {
  schema_mapping_id: string
  unmapped_count: number
  unmapped_fields: UnmappedField[]
}

// ── Schema audit trail entry ──────────────────────────────────────────────────

export interface SchemaAuditEntry {
  timestamp: string
  event: string
  node?: number
  gate_type?: string
  details?: Record<string, any>
}

// ── DDL retry ────────────────────────────────────────────────────────────────

export interface ExtraFieldConfig {
  source_field: string
  source_table: string
  storage_strategy: 'custom' | 'raw_metadata' | 'skip'
  target_table?: string
  custom_column_name?: string
  data_type?: string
  nullable?: boolean
  user_approved: boolean
}

// ── Doc RAG — Row index types ──────────────────────────────────────────────────

export interface RowIndexTable {
  source_table: string
  row_count: number
}

export interface RowIndexUploadResponse {
  table_name: string
  rows_inserted: number
  rows_updated: number
  total_rows_in_index: number
  columns_detected: string[]
  pk_column: string
}

export interface DbTable {
  table_name: string
  row_count: number | null
}

export interface DbTableColumn {
  name: string
  type: string
}

// ── Doc RAG types ─────────────────────────────────────────────────────────────

export interface DocRagDocument {
  id: string
  file_name: string
  mime_type: string | null
  document_type: string | null
  status: string
  num_pages: number
  num_chunks: number
  created_at: string
}

export interface DocRagUploadResponse {
  document_id: string
  status: string
  file_name: string
  num_pages: number
  num_chunks: number
  document_type: string | null
  processing_time_ms: number
}

export interface DocRagChunk {
  chunk_index: number
  page_start: number | null
  page_end: number | null
  block_type: string
  section_label: string | null
  text_content: string
  meta: Record<string, any> | null
}

export interface DocRagCitation {
  document_id: string
  file_name: string
  page_start: number | null
  page_end: number | null
  section: string | null
  chunk_id: string
  quote: string
}

export interface DocRagChunkMatch {
  chunk_id: string
  chunk_index: number
  page_number: number | null
  confidence: number
  semantic_score: number
  bm25_score: number
  metadata_score: number
  matched_fields: string[]
  chunk_text_preview: string
}

export interface DocRagMatchedRow {
  source_table: string
  row_pk: string
  confidence: number
  match_method: string
  row_data: Record<string, any>
  evidence: string
  matched_metadata_fields?: string[]
  match_details?: {
    semantic_score: number
    bm25_overlap: number
    metadata_overlap: number
    exact_key_match: boolean
    normalized_key_match: boolean
  }
  chunk_matches?: DocRagChunkMatch[]
  chunk_ids?: string[]
  chunk_count?: number
}

export interface DocRagRetrievedChunk {
  chunk_id: string
  document_id: string
  file_name: string
  score: number
  vector_score: number
  bm25_score: number
  block_type: string
  page_start: number | null
  page_end: number | null
  text_content: string
  meta: Record<string, any> | null
}

export interface DocRagQueryResponse {
  query_id: string
  query_type: string
  answer: string
  confidence: number
  citations: DocRagCitation[]
  matched_rows: DocRagMatchedRow[]
  latency_ms: number
  model_name: string
  retrieved_chunks?: DocRagRetrievedChunk[]
  stages?: Record<string, any>
}

export interface DocRagMatchRowsResponse {
  document_id: string
  file_name: string
  total_chunks_analyzed: number
  unique_rows_matched: number
  matched_rows: DocRagMatchedRow[]
  matched_rows_by_table?: Record<string, DocRagMatchedRow[]>
  by_table: Record<string, number>
  latency_ms: number
}
