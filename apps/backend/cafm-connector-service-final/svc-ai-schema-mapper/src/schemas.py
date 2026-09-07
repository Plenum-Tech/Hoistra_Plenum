"""Request/response schemas for svc-AI-Schema-Mapper API.

All schemas validated with Pydantic v2.
"""

from datetime import datetime
from typing import Optional, List, Dict, Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


# ── Migration Start Request ────────────────────────────────────────────────

class MigrationStartRequest(BaseModel):
    """Request to start a new CMMS migration job.

    Args:
        cmms_name: Source CMMS system (Maximo, Fiix, SAP PM, Archibus, etc.)
        source_blob_url: Azure Blob URL of uploaded CSV/Excel file
        mapping_doc_url: Optional URL to mapping documentation
        organization_id: UUID of customer organization
    """

    cmms_name: str = Field(..., min_length=1, max_length=100)
    source_blob_url: str = Field(..., pattern=r"^https://")
    mapping_doc_url: Optional[str] = Field(None, pattern=r"^https://")
    organization_id: UUID


class MigrationStartResponse(BaseModel):
    """Response from starting a migration job."""

    migration_id: UUID
    status: str
    progress_pct: float
    message: str


# ── Migration Status Response ──────────────────────────────────────────────

class MigrationStatusResponse(BaseModel):
    """Current status of a migration job."""

    migration_id: UUID
    status: str  # running | awaiting_review | paused | complete | failed | ddl_failed | cancelled
    progress_pct: float
    current_step: int  # 0-9 derived from current_step string (e.g. "2_det..." → 2)
    cmms_name: str
    started_at: datetime
    completed_at: Optional[datetime] = None

    # Mapping statistics
    t1_mapped_count: int
    t2_auto_count: int
    t2_human_count: int
    unmapped_count: int
    total_fields: int

    # Source file (the uploaded CSV/Excel/etc.) — persisted to blob on upload so
    # it stays downloadable from the migration, history, and linked UDR scripts.
    source_filename: Optional[str] = None
    source_download_url: Optional[str] = None  # relative: /api/migration/{id}/source

    # Output URLs (only populated if complete)
    output_json_url: Optional[str] = None
    output_csv_url: Optional[str] = None
    output_sql_url: Optional[str] = None
    migration_report_url: Optional[str] = None
    output_structure_md_url: Optional[str] = None

    # HITL gate — populated when status == "awaiting_review"
    # Frontend reads these to know which gate UI to render and what payload to display.
    pending_gate_type: Optional[str] = None
    pending_gate_payload: Optional[Dict[str, Any]] = None

    # Tier-2 semantic / field-mapping UI draft persisted between steps.
    field_mapping_draft: Optional[Dict[str, Any]] = None

    # F7 7.9/7.10 — Test 1 (chunk→PK) + Test 2 (overlap→FK) reports, surfaced from
    # the UDR pass's node log so the FE can render results + remediation actions.
    udr_test_results: Optional[Dict[str, Any]] = None

    # F7 7.12/7.13 — schema vs LLM-inferred relationships (review queue) + sanctity
    # flags ('Relationship Quality'), surfaced from the UDR pass's node log.
    udr_relationship_report: Optional[Dict[str, Any]] = None

    # B7.1→B12.1 — Migration-Analysis table-resolution report (metadata cards · PK detection ·
    # deterministic / RAG / semantic table mapping · final decisions), from the UDR node log.
    udr_table_resolution: Optional[Dict[str, Any]] = None

    # B13.1→B21.1 — Column-Intelligence Pipeline (prefixing · metadata · FK candidates · format +
    # value-pattern grouping · unified canonical names · classification · dest column mapping),
    # from the UDR node log — so the post-write results view shows the same flow as the gate.
    udr_column_intelligence: Optional[Dict[str, Any]] = None

    # Error information
    error_message: Optional[str] = None

    # Per-node log array — always 9 entries (node_id 1–9).
    # Each entry: {node_id, node_name, status, started_at, completed_at, duration_ms, output, logs}
    # status values: "complete" | "running" | "pending"
    # Frontend uses this to drive the right-panel node log view.
    nodes: Optional[List[Any]] = None


# ── HITL Approval Request ──────────────────────────────────────────────────

class MigrationApprovalDecision(BaseModel):
    """Single human decision for a field mapping or hierarchy."""

    action: Literal["accept", "reject", "override"]
    source_field: str
    target_field: Optional[str] = None
    notes: Optional[str] = None


class MigrationApprovalRequest(BaseModel):
    """Request to resume a HITL gate with human decisions.

    gate_type must match migration_jobs.pending_gate_type so the backend knows
    which resume_migration ARQ task to enqueue.

    decisions shape per gate_type:
      pre_semantic   — {table_name: [{source_field, decision: "approve"|"semantic"}]}
      field_mapping  — {table_name: [{action: "accept"|"reject"|"override",
                                      source_field, target_field?, reviewer_id?, notes?}]}
      hierarchy      — list of [{action: "confirm"|"reject"|"modify", ...}]
      write          — {action: "confirm"|"reject"}
    """

    gate_type: Literal["pre_semantic", "field_mapping", "hierarchy", "write"]
    decisions: Any          # shape varies by gate_type — validated inside each gate node
    reviewer_id: Optional[UUID] = None


class CanonicalFieldScoresRequest(BaseModel):
    """Score source column against canonical plenum_cafm column names (embedding cache)."""

    source_field: str = Field(..., min_length=1)
    field_description: Optional[str] = None
    sample_values: Optional[List[str]] = None
    canonical_fields: List[str] = Field(default_factory=list)


class CanonicalFieldScoresResponse(BaseModel):
    """Per-column semantic similarity in [0, 1] for fields present in the embedding cache."""

    scores: Dict[str, float] = Field(default_factory=dict)


class EmbedRankCandidate(BaseModel):
    """One candidate to rank against the query."""

    id: str
    text: str


class EmbedRankRequest(BaseModel):
    """Embedding fallback: rank candidate texts by similarity to a query."""

    query: str = Field(..., min_length=1)
    candidates: List[EmbedRankCandidate] = Field(default_factory=list)
    top_k: int = 5


class EmbedRankItem(BaseModel):
    id: str
    score: float


class EmbedRankResponse(BaseModel):
    ranked: List[EmbedRankItem] = Field(default_factory=list)


class MigrationApprovalResponse(BaseModel):
    """Response to approval request."""

    migration_id: UUID
    status: str
    message: str
    decisions_processed: int


# ── Audit Trail Response ───────────────────────────────────────────────────

class FieldMappingAudit(BaseModel):
    """Single field mapping in the audit trail."""

    source_field: str
    target_field: str
    confidence: float
    tier: str
    rationale: str
    decided_at: datetime
    reviewer_id: Optional[UUID] = None


class MigrationAuditResponse(BaseModel):
    """Complete audit trail for a migration."""

    migration_id: UUID
    total_mappings: int
    mappings: List[FieldMappingAudit]


# ── Migration List Response ────────────────────────────────────────────────

class MigrationListItem(BaseModel):
    """Single item in migration list."""

    migration_id: UUID
    cmms_name: str
    status: str
    progress_pct: float
    t1_count: int
    t2_count: int
    started_at: datetime
    completed_at: Optional[datetime] = None


class MigrationListResponse(BaseModel):
    """List of migrations for an organization."""

    total_count: int
    migrations: List[MigrationListItem]


# ── Download Request/Response ──────────────────────────────────────────────

class MigrationDownloadResponse(BaseModel):
    """Response to download request — returns signed Blob URL."""

    migration_id: UUID
    format: Literal["json", "csv", "sql", "pdf"]
    download_url: str
    expires_in_minutes: int = 60


# ── Cancellation Response ──────────────────────────────────────────────────

class MigrationCancelResponse(BaseModel):
    """Response to cancellation request."""

    migration_id: UUID
    status: str
    message: str


# ── LangSmith Trace URL Response ───────────────────────────────────────────

class LangSmithTraceResponse(BaseModel):
    """Response with LangSmith trace URL for debugging."""

    migration_id: UUID
    trace_url: str
    project: str
    message: str


# ── WebSocket Event (internal) ─────────────────────────────────────────────

class WebSocketEvent(BaseModel):
    """Event sent over WebSocket to client."""

    timestamp: datetime
    event_type: Literal["node_start", "node_end", "gate_pause", "gate_resume", "error", "complete"]
    node_name: str
    node_number: Optional[int] = None
    progress_pct: float
    message: str
    detail: Optional[Dict[str, Any]] = None


# ── Error Response ─────────────────────────────────────────────────────────

class ErrorResponse(BaseModel):
    """Standard error response."""

    error: str
    detail: Optional[str] = None
    status_code: int


# ── Internal Testing: Upload & Ingest ──────────────────────────────────────

class TableHealthSummary(BaseModel):
    """Health metrics for a single parsed table."""

    row_count: int
    column_count: int
    avg_null_percentage: float
    null_percentages: Dict[str, float]


class TestIngestResponse(BaseModel):
    """Response from the internal test-upload endpoint.

    Runs Node 1 (ingest_and_configure) directly against the uploaded file
    and returns the full parsed state — no DB write, no ARQ dispatch.
    """

    migration_id: str               # Ephemeral UUID (not persisted)
    filename: str
    file_size_bytes: int
    detected_file_format: Optional[str] = None   # "csv" | "excel"
    detected_encoding: Optional[str] = None
    detected_delimiter: Optional[str] = None
    row_count: int = 0
    column_count: int = 0
    table_names: List[str] = []
    table_health: Dict[str, Any] = {}
    dataset_summary: Optional[str] = None
    column_descriptions: Optional[Dict[str, str]] = None
    el_m1_passed: bool = False
    error_message: Optional[str] = None
    duration_ms: float


# ── JSON Mapper Configuration (Customer-Provided Field Mappings) ────────────

class RegexPatternConfig(BaseModel):
    """Regex-based field matching rule."""

    patterns: List[str]
    confidence: float = Field(..., ge=0.0, le=1.0)
    description: Optional[str] = None


class CustomTransformation(BaseModel):
    """Field transformation logic."""

    type: Literal["concat", "map", "formula", "split"]
    fields: Optional[List[str]] = None
    values: Optional[Dict[str, str]] = None
    separator: Optional[str] = None
    description: Optional[str] = None


class JsonMapperConfig(BaseModel):
    """Complete JSON mapper configuration.

    Defines how to map customer's source fields to canonical target fields.
    Includes aliases, regex patterns, and custom transformations.
    """

    version: str = Field(default="1.0", description="Mapper format version")
    source_system: str = Field(..., description="CMMS system name (Maximo, Fiix, SAP PM, etc.)")
    customer_id: Optional[str] = Field(None, description="Customer UUID (optional)")
    description: Optional[str] = Field(None, description="Description of this mapping")

    # Core mappings
    canonical_fields: Dict[str, str] = Field(
        ...,
        description="Target field definitions: {field_name: description}"
    )
    vendor_aliases: Dict[str, List[str]] = Field(
        ...,
        description="Source field aliases: {canonical_field: [source_aliases]}"
    )

    # Optional enhancements
    regex_patterns: Optional[Dict[str, RegexPatternConfig]] = Field(
        None,
        description="Regex patterns for matching fields"
    )
    custom_transformations: Optional[Dict[str, CustomTransformation]] = Field(
        None,
        description="Field transformation rules"
    )
    excluded_fields: Optional[List[str]] = Field(
        None,
        description="Source fields to exclude from mapping"
    )
    confidence_overrides: Optional[Dict[str, float]] = Field(
        None,
        description="Override confidence scores for specific fields"
    )

    class Config:
        json_schema_extra = {
            "example": {
                "version": "1.0",
                "source_system": "Maximo",
                "canonical_fields": {
                    "asset_id": "Unique asset identifier",
                    "asset_code": "Human-readable code"
                },
                "vendor_aliases": {
                    "asset_id": ["ASSET_ID", "AssetID", "id"],
                    "asset_code": ["ASSET_CODE", "code", "tag"]
                },
                "regex_patterns": {
                    "asset_id": {
                        "patterns": ["^asset_?id$", "^id$"],
                        "confidence": 0.90
                    }
                }
            }
        }


class TestIngestWithMapperResponse(BaseModel):
    """Response from test-ingest-with-mapper endpoint.

    Runs Node 1 + Node 2 with customer-provided JSON mapper and returns results.
    """

    migration_id: str
    filename: str
    file_size_bytes: int
    detected_file_format: Optional[str] = None
    detected_encoding: Optional[str] = None
    detected_delimiter: Optional[str] = None
    row_count: int = 0
    column_count: int = 0
    mapped_fields: Dict[str, Any] = Field(default_factory=dict)
    unmapped_fields: List[str] = []
    overall_confidence: float = 0.0
    el_m2_passed: bool = False
    table_names: List[str] = []
    el_m1_passed: bool = False
    mapper_source_system: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float


class Tier2Mapping(BaseModel):
    """Tier 2 semantic mapping result for a field."""

    source_field: str
    target_field: str
    confidence: float
    tier: str  # "T2_auto" or "T2_flagged"
    rationale: Optional[str] = None


class TestIngestWithSemanticResponse(BaseModel):
    """Response from test-ingest-with-semantic endpoint.

    Runs Node 1 + Node 2 + Node 3 with customer-provided JSON mapper.
    Includes semantic mapping results and allows manual field entry for custom additions.
    """

    migration_id: str
    filename: str
    file_size_bytes: int
    detected_file_format: Optional[str] = None
    detected_encoding: Optional[str] = None
    detected_delimiter: Optional[str] = None
    row_count: int = 0
    column_count: int = 0

    # Node 1 results
    table_health: Dict[str, Any] = Field(default_factory=dict)  # Per-table health metrics
    parsed_tables: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)  # Sample parsed tables (5 rows for analysis)
    full_tables: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)  # Full tables (complete file for downstream processing)
    column_descriptions: Dict[str, str] = Field(default_factory=dict)  # Haiku-generated column descriptions
    dataset_summary: Optional[str] = None  # AI summary from Haiku

    # Node 2 results
    tier1_mappings: List[Dict[str, Any]] = Field(default_factory=list)  # T1 auto mappings
    mapped_fields: Dict[str, Any] = Field(default_factory=dict)  # Mapped fields dict

    # Node 3 results
    tier2_auto_mappings: List[Tier2Mapping] = Field(default_factory=list)  # T2 auto (conf >= 0.85)
    tier2_flagged_mappings: List[Tier2Mapping] = Field(default_factory=list)  # T2 flagged (0.65-0.84)
    tier2_unmappable: List[str] = Field(default_factory=list)  # Fields < 0.65 confidence (flat list)
    tier2_unmappable_by_table: Dict[str, List[str]] = Field(default_factory=dict)  # Unmappable grouped by table

    overall_confidence: float = 0.0
    el_m2_passed: bool = False
    el_m3_passed: bool = False
    table_names: List[str] = []
    el_m1_passed: bool = False
    mapper_source_system: Optional[str] = None
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)  # Detailed execution logs


# ── Node 4: Human Review (GATE 1) ────────────────────────────────────────

class MappingApproval(BaseModel):
    """User decision on a flagged mapping."""

    source_field: str
    approved: bool  # True = accept mapping, False = reject and mark unmapped
    target_field: Optional[str] = None  # Only used if approved=True
    confidence_override: Optional[float] = None  # User can override confidence


class CustomFieldMapping(BaseModel):
    """User-provided custom field mapping."""

    source_field: str
    target_field: str
    confidence: float = 0.90
    description: Optional[str] = None


class HumanReviewRequest(BaseModel):
    """Request for Node 4 human review testing.

    Takes Node 2/3 results and user approvals/custom mappings,
    returns final validated mappings.
    """

    migration_id: str
    tier1_mappings: List[Dict[str, Any]]  # Auto-mapped from Node 2
    tier2_flagged_mappings: List[Tier2Mapping] = []  # Flagged from Node 3 (0.65-0.84)
    tier2_unmappable: List[str] = []  # Unmappable from Node 3 (< 0.65)

    # User decisions
    flagged_approvals: List[MappingApproval] = []  # Approve/reject tier2_flagged
    custom_mappings: List[CustomFieldMapping] = []  # New custom mappings
    intentionally_unmapped: List[str] = []  # Fields user says can't map


class FinalMapping(BaseModel):
    """Final approved mapping after human review."""

    source_field: str
    target_field: str
    confidence: float
    approval_status: str  # "auto_approved", "human_approved", "custom", "intentionally_unmapped"
    source: str  # "T1", "T2", "custom"


class HumanReviewResponse(BaseModel):
    """Response from Node 4 human review endpoint.

    Contains final validated mappings ready for Node 5.
    """

    migration_id: str
    total_source_fields: int
    final_mappings: List[FinalMapping]
    intentionally_unmapped: List[str]
    tier2_flagged_mappings: List[Tier2Mapping] = []  # Flagged mappings awaiting user approval
    tier2_unmappable_count: int = 0  # Count of unmappable fields
    mapping_stats: Dict[str, Any] = {
        "auto_approved": 0,
        "human_approved": 0,
        "custom_added": 0,
        "intentionally_unmapped": 0,
        "overall_confidence": 0.0,
    }
    el_m4_passed: bool  # EL-M.4 validation gate
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)  # Detailed execution logs


# ── Node 5: Preprocess & Validate ──────────────────────────────────

class PreprocessRequest(BaseModel):
    """Request for Node 5 preprocessing.

    Takes cleaned tables and final mappings from Node 4, applies:
    - Deduplication
    - Null handling
    - Date coercion
    - JSON Schema validation
    - FK pre-check
    """

    migration_id: str
    cleaned_tables: Dict[str, List[Dict[str, Any]]]  # Table name → list of records
    final_mappings: List[FinalMapping]  # Final mappings from Node 4
    table_names: List[str] = []  # Optional: expected table names


class DataQualityMetrics(BaseModel):
    """Data quality metrics for a single table after preprocessing."""

    table_name: str
    original_row_count: int
    dedup_drop_count: int
    post_dedup_row_count: int
    dedup_ratio: float  # post_dedup / original
    null_fills_applied: int  # Count of null→0/empty fills
    date_coercions: int  # Count of dates normalized to ISO 8601
    validation_warnings: List[str] = []


class PreprocessResponse(BaseModel):
    """Response from Node 5 preprocessing."""

    migration_id: str
    cleaned_tables: Dict[str, Any] = {}  # Cleaned tables (serialized)
    total_original_rows: int
    total_rows_post_dedup: int
    total_dedup_drop_count: int
    overall_dedup_ratio: float  # post_dedup / original (must be ≥ 0.80)

    # Per-table metrics
    table_metrics: List[DataQualityMetrics] = []

    # Data quality warnings (non-blocking)
    data_quality_warnings: List[str] = []

    # FK detection results
    detected_fk_columns: Dict[str, List[str]] = {}  # table_name → list of suspected FK columns

    # EL-M.5 validation result
    el_m5_passed: bool
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)  # Detailed execution logs


# ── Node 6: Resolve Hierarchy ──────────────────────────────────────────────


class ResolveHierarchyRequest(BaseModel):
    """Request for Node 6 hierarchy resolution testing."""

    migration_id: str
    cleaned_tables: Dict[str, List[Dict[str, Any]]]  # Cleaned data from Node 5
    final_mappings: List[FinalMapping] = []  # Field mappings from Node 4


class ResolveHierarchyResponse(BaseModel):
    """Response from Node 6 hierarchy resolution."""

    migration_id: str
    fk_candidates_count: int  # Total FK candidates scanned
    confirmed_fks_count: int  # Validated FK relationships
    hierarchy_cycles_count: int  # Detected cycles
    implicit_hierarchies_count: int  # Detected implicit hierarchies (SAP-style)
    self_referencing_trees_count: int  # Resolved self-referencing hierarchies

    # Detailed results
    fk_candidates: List[Dict[str, Any]] = []  # All FK candidates found
    confirmed_hierarchies: List[Dict[str, Any]] = []  # Validated & classified hierarchies
    hierarchy_cycles: List[Any] = []  # Any cycles found (list of cycles, each is a list or dict)
    implicit_hierarchies: Dict[str, Any] = {}  # Implicit hierarchies (dict of detected patterns)
    containment_hierarchy: Dict[str, Any] = {}  # Nested containment structure
    cleaned_tables: Dict[str, List[Dict[str, Any]]] = Field(default_factory=dict)  # Pass through cleaned tables for downstream nodes

    # EL-M.6 validation result
    el_m6_passed: bool
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)  # Detailed execution logs


# ── Node 7: Verify Hierarchy ────────────────────────────────────────────────


class VerifyHierarchyRequest(BaseModel):
    """Request for Node 7 hierarchy verification."""

    migration_id: str
    confirmed_hierarchies: List[Dict[str, Any]] = []  # Validated hierarchies from Node 6
    hierarchy_cycles: List[Any] = []  # Cycles detected in Node 6 (to verify/resolve)
    customer_corrections: Optional[List[Dict[str, Any]]] = None  # Customer corrections


class VerifyHierarchyResponse(BaseModel):
    """Response from Node 7 hierarchy verification."""

    migration_id: str
    hierarchies_approved: int
    cycles_resolved: int
    hierarchy_confirmed: bool

    # Verified hierarchy ready for output generation
    confirmed_hierarchies: List[Dict[str, Any]] = []
    containment_hierarchy: Dict[str, Any] = {}

    # EL-M.7 validation result
    el_m7_passed: bool
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)


# ── Node 8: Generate Output ──────────────────────────────────────────────────


class GenerateOutputRequest(BaseModel):
    """Request for Node 8 output generation."""

    migration_id: str
    final_mappings: List[FinalMapping] = []  # Final field mappings from Node 4
    cleaned_tables: Dict[str, List[Dict[str, Any]]]  # Cleaned data from Node 5
    hierarchy_relationships: List[Dict[str, Any]] = []  # Verified hierarchies from Node 7


class GenerateOutputResponse(BaseModel):
    """Response from Node 8 output generation."""

    migration_id: str
    json_generated: bool
    csv_generated: bool
    sql_generated: bool
    report_generated: bool

    # Output file URLs (use UI-expected field names)
    output_json_url: Optional[str] = None
    output_csv_url: Optional[str] = None
    output_sql_url: Optional[str] = None
    migration_report_url: Optional[str] = None

    # IntermediateSchema for display
    intermediate_schema: Optional[Dict[str, Any]] = None

    # IntermediateSchema validation
    intermediate_schema_valid: bool
    schema_validation_errors: List[str] = []

    # EL-M.8 validation result
    el_m8_passed: bool
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)


# ── Node 9: Write Output ───────────────────────────────────────────────────


class WriteOutputRequest(BaseModel):
    """Request for Node 9 final write/handoff."""

    migration_id: str
    intermediate_schema: Dict[str, Any]  # Complete IntermediateSchema
    customer_approval: bool = True  # Customer approves handoff


class WriteOutputResponse(BaseModel):
    """Response from Node 9 final write/handoff."""

    migration_id: str
    handoff_complete: bool
    handoff_status: str  # For UI compatibility: "success", "rejected", "queued", "blocked"
    ingestion_service_url: str
    ingestion_status: str

    # Handoff summary (for UI review_payload)
    write_review_payload: Optional[Dict[str, Any]] = None

    # EL-M.9 validation result
    el_m9_passed: bool
    error_message: Optional[str] = None
    duration_ms: float
    execution_logs: List[str] = Field(default_factory=list)
