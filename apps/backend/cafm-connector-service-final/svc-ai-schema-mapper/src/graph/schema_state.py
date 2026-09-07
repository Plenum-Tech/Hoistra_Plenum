"""LangGraph state definitions for schema mapping pipeline.

Separate TypedDict for SchemaMapping flow (distinct from MigrationState).
This pipeline maps external CMMS schemas to plenum_cafm canonical schema.
"""

from typing import TypedDict, Optional, Any
from datetime import datetime


class SchemaMappingFieldInfo(TypedDict, total=False):
    """Metadata about a single column from external schema."""
    field_name: str              # original field name from external source
    data_type: str              # SQL type (varchar, integer, timestamp, etc.)
    nullable: bool              # whether field allows NULL
    is_primary_key: bool        # if this is a primary key
    is_foreign_key: bool        # if this has an FK constraint
    fk_target_table: Optional[str]  # if FK, which table it references
    fk_target_column: Optional[str] # if FK, which column it references
    description: Optional[str]  # any column-level comments
    migration_target: Optional[str]  # plenum_cafm column (Fiix flow: pre-seeded alias)
    # ── Text analysis (populated at ingest for string-type columns) ───────
    sample_values: Optional[list]    # up to 5 raw sample values from source data
    avg_char_length: Optional[int]   # average string length of non-null samples
    max_char_length: Optional[int]   # maximum string length seen in samples


class SchemaTableInfo(TypedDict, total=False):
    """Metadata about a table from external schema."""
    table_name: str
    row_count: Optional[int]    # if available
    primary_key: Optional[str]  # PK column name(s)
    columns: list[SchemaMappingFieldInfo]
    description: Optional[str]


class CanonicalFieldMapping(TypedDict, total=False):
    """Result of mapping one external column → canonical field."""
    source_field: str           # external column name
    source_table: str           # external table name
    target_field: str           # canonical field name (from plenum_cafm)
    confidence: float           # 0.0 - 1.0
    tier: str                   # T1_exact, T1_alias, T1_regex, T2_semantic, unmapped
    rationale: str              # why this mapping was chosen
    auto_mappable: bool         # can be auto-accepted (confidence ≥ 0.85)
    human_review_needed: bool   # flag for HITL gate


class ExtraFieldConfig(TypedDict, total=False):
    """Configuration for an unmapped/extra column — captures DDL intent from Node 4 HITL."""
    source_field: str
    source_table: str            # which external table this came from
    storage_strategy: str        # "custom" | "raw_metadata" | "skip"

    # ── DDL fields (populated when storage_strategy == "custom") ─────────
    target_table: str            # plenum_cafm table to add the column to
    custom_column_name: str      # exact column name to create
    data_type: str               # SQL type: VARCHAR(255), INTEGER, BOOLEAN, etc.
    is_new_table: bool           # True if target_table doesn't exist yet
    new_table_pk: str            # PK column name for new tables (default "id")
    nullable: bool               # whether the new column allows NULLs (default True)

    user_approved: bool


class UnstructuredCandidate(TypedDict, total=False):
    """
    A column flagged as potentially unstructured / free-text.

    Raised in two cases:
      A. Column WAS matched (any tier) but is string-type with avg_char_length > threshold.
         User can choose to keep the mapping OR reclassify as raw_metadata.
      B. Column was NOT matched at any stage and is string-type (any length).
         User must decide: treat as unstructured OR skip.

    Presented to user in the Node 4 HITL gate with sample values.
    """
    source_field: str
    source_table: str
    match_status: str            # "matched" | "unmapped"
    matched_target: Optional[str]   # canonical field name (if matched)
    match_tier: Optional[str]       # T1_exact, T2_semantic, etc. (if matched)
    match_confidence: Optional[float]
    data_type: str
    avg_char_length: int         # 0 if no samples available
    max_char_length: int
    sample_values: list          # up to 5 raw values for UI display
    reason: str                  # human-readable explanation for why it was flagged


class ForeignKeyDetection(TypedDict, total=False):
    """Detected FK relationship between tables."""
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str      # REFERENCE, CONTAINMENT, OWNERSHIP, PART_OF
    confidence: float           # 0.0 - 1.0
    reasoning: str
    user_confirmed: bool
    confirmed_at: Optional[datetime]
    # Canonical schema cross-reference (set by Node 5 when a canonical alignment is found)
    canonical_target_table: str   # e.g. "assets" — canonical plenum_cafm table this maps to
    canonical_target_column: str  # e.g. "id"
    canonical_backed: bool        # True when the FK aligns with a known canonical relationship


class HierarchyNode(TypedDict, total=False):
    """Node in a single hierarchy tree."""
    table_name: str
    primary_key_field: str
    parent_fk_field: Optional[str]  # FK column on this table that points up to parent
    children: list["HierarchyNode"]
    level: int
    # Optional extras set by Node 5
    self_referential_column: str    # e.g. intAssetParentID
    canonical_table: str            # corresponding canonical plenum_cafm table name


class JunctionTable(TypedDict, total=False):
    """A bridge / association table that connects two entities (many-to-many)."""
    table_name: str
    left_table: str
    left_fk_column: str
    right_table: str
    right_fk_column: str
    confidence: float
    reasoning: str


class HorizontalRelationship(TypedDict, total=False):
    """
    A peer / lateral relationship between two tables.

    This covers:
      - Siblings: both tables are children of the same parent
      - Many-to-many: linked via a junction table
      - Direct peer FK: one table references the other but they sit at the same level
    """
    source_table: str
    target_table: str
    relationship_type: str   # SIBLING, MANY_TO_MANY, PEER_FK, SHARED_PARENT
    via_table: str           # junction table name (for MANY_TO_MANY)
    shared_parent: str       # common parent table name (for SIBLING / SHARED_PARENT)
    source_fk_column: str
    target_fk_column: str
    confidence: float
    reasoning: str


class SchemaMappingState(TypedDict, total=False):
    """
    Full state for schema mapping pipeline (separate from data ingestion).

    Maps external CMMS schema → canonical plenum_cafm schema through:
    0. Fetch canonical schema (plenum_cafm database)
    1. Ingest external schema (Fiix, CSV, JSON, etc.)
    2. Deterministic mapping (4-tier strategy)
    3. Semantic mapping (embeddings)
    4. Human review (HITL gate for field mappings)
    5. FK & Hierarchy detection
    6. Verify hierarchy (HITL gate)
    7. Output generation
    8. Write to database
    """

    # ── Session & Metadata ────────────────────────────────────────────────
    schema_mapping_id: str      # UUID
    organization_id: str
    external_cmms_name: str     # "Maximo", "Fiix", "SAP PM", etc.
    created_by: str             # user ID
    created_at: datetime

    # ── Node 0: Canonical Schema (plenum_cafm reference) ──────────────────
    canonical_tables: dict[str, SchemaTableInfo]  # plenum_cafm schema {table_name: SchemaTableInfo}
    canonical_table_count: int
    canonical_column_count: int
    # Pre-semantic gate Step-1 routing: Fiix source table → chosen existing CAFM table,
    # and Fiix source table → user-chosen NEW CAFM table name.
    table_overrides: dict[str, str]
    new_tables_requested: dict[str, str]

    # ── Node 1: Ingest ───────────────────────────────────────────────────
    # Schema definition input (can be from DB introspection, YAML, JSON, or SQL DDL)
    external_schema_source: str # "database_url" | "yaml_file" | "json_file" | "ddl_sql" | "fiix_api"
    external_schema_format: str # "sql" | "yaml" | "json"
    schema_content: Optional[str]  # Raw schema definition (YAML/JSON/DDL/Fiix mapper JSON)
    db_url: Optional[str]  # Database URL for introspection (if schema_source == "database_url")

    # Parsed external schema (list of tables with their columns)
    external_tables: dict[str, SchemaTableInfo]  # {table_name: SchemaTableInfo}
    table_count: int
    total_columns: int
    schema_summary: str         # Human-readable description of schema

    # ── Node 2: Deterministic Mapping ────────────────────────────────────
    tier1_mappings: list[CanonicalFieldMapping]  # Exact, alias, regex matches
    tier1_mapped_count: int
    unmapped_after_t1: list[SchemaMappingFieldInfo]  # Fields needing semantic mapping

    # ── Node 2a: Pre-Semantic Review (HITL GATE) ──────────────────────────
    pre_semantic_review_payload: Optional[dict[str, Any]]  # Gate payload shown to user

    # ── Node 4 (was 3): Semantic Mapping ─────────────────────────────────
    tier2_auto_mapped: list[CanonicalFieldMapping]     # confidence ≥ 0.85
    tier2_flagged: list[CanonicalFieldMapping]         # 0.65 ≤ confidence < 0.85
    tier2_unmappable: list[SchemaMappingFieldInfo]     # confidence < 0.65
    overall_mapping_confidence: float

    # ── Node 4 pre-check: Unstructured candidates ─────────────────────
    # Columns flagged as potentially free-text before HITL gate runs.
    # Scenario A: matched column with string type + avg_char_length > threshold
    # Scenario B: unmapped column with string type (any length)
    unstructured_candidates: list[UnstructuredCandidate]

    # ── Node 4: FK & Hierarchy Detection ──────────────────────────────────
    detected_foreign_keys: list[ForeignKeyDetection]
    # Forest of independent hierarchy trees (each entry is a root node)
    detected_hierarchies: list[HierarchyNode]
    # Bridge / junction tables detected (many-to-many connectors)
    junction_tables: list[JunctionTable]
    # Lateral / peer relationships between tables at the same level
    horizontal_relationships: list[HorizontalRelationship]
    # Tables with no FK connections to any other table
    isolated_tables: list[str]
    implicit_hierarchies: dict[str, Any]           # SAP-style code hierarchies
    hierarchy_cycles: list[list[str]]              # Detected cycles (if any)

    # ── Node 5: Verify Hierarchy (HITL GATE 1) ────────────────────────────
    hierarchy_review_payload: Optional[dict[str, Any]]  # What user sees for approval
    user_hierarchy_corrections: Optional[dict[str, Any]]  # User-provided corrections
    hierarchy_approved: bool
    hierarchy_approved_at: Optional[datetime]

    # ── Node 6: Output Generation ────────────────────────────────────────
    # Final mapping configuration (same structure as JsonMapperConfig)
    final_mapping_config: dict[str, Any]  # {
        #   "version": "1.0",
        #   "source_system": external_cmms_name,
        #   "canonical_fields": {...},
        #   "vendor_aliases": {...}
        # }

    # Extra fields that don't map to canonical schema
    extra_fields_config: list[ExtraFieldConfig]

    # ── DB Session & Gate State (injected by worker) ─────────────────────
    db_session: Optional[Any]            # AsyncSession injected by worker for DB writes
    pending_gate_type: Optional[str]     # "field_mapping" | "hierarchy" — set by gate nodes
    pending_gate_payload: Optional[dict[str, Any]]  # Gate payload for frontend rendering

    # ── Node 7: Output Artifacts (Azure Blob URLs) ───────────────────────────
    output_json_url: Optional[str]    # URL to mapper_config.json in Blob
    output_csv_url: Optional[str]     # URL to field_mappings.csv in Blob
    output_sql_url: Optional[str]     # URL to schema_ddl_preview.sql in Blob

    # ── Node 8: New Schema ───────────────────────────────────────────────────
    new_schema_name: Optional[str]    # e.g. plenum_cafm_maximo_20260518143200

    # ── Audit & Tracking ──────────────────────────────────────────────────
    status: str                 # ingest | mapping | semantic | hierarchy | complete | error
    error_message: Optional[str]
    processing_started_at: datetime
    processing_completed_at: Optional[datetime]
    langsmith_run_ids: list[str]  # LangSmith trace IDs for audit
    notes: list[str]            # Processing notes at each stage
