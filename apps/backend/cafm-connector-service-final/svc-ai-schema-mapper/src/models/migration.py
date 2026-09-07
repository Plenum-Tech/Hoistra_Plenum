"""SQLAlchemy ORM models for svc-AI-Schema-Mapper.

Three tables in plenum_cafm schema:
- migration_jobs: tracks overall migration run status
- migration_field_mappings: immutable per-field mapping audit trail
- migration_hierarchy: FK relationships detected during migration
"""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Column, String, Integer, Float, Text, Boolean, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID as PG_UUID, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from typing import Optional, Any


class MigrationBase(DeclarativeBase):
    """Base class for all migration models."""

    __table_args__ = {"schema": "plenum_cafm"}


class MigrationJob(MigrationBase):
    """Master record for a CMMS migration run.

    One row per customer CMMS export upload.
    Tracks overall progress, status, and output URLs.
    """

    __tablename__ = "migration_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    cmms_name: Mapped[str] = mapped_column(String(100), nullable=False)
    source_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_blob_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mapping_doc_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="running",
        index=True,
    )
    current_step: Mapped[str] = mapped_column(String(100), nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Mapping statistics
    t1_mapped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    t2_auto_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    t2_human_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    t2_multi_merge_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmapped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_records: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    orphan_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cycle_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Output URLs
    output_json_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_csv_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_sql_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    migration_report_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Recommended finalized DB-structure summary (.md) — Feature 4a.
    output_structure_md_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    mapping_flow_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # HITL gate state — written by gate nodes so the frontend can read the payload
    # and POST decisions back via /approve.
    # gate_type values: "pre_semantic" | "field_mapping" | "hierarchy" | "write"
    pending_gate_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    pending_gate_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Tier-2 semantic / field-mapping UI draft (body + meta.canonicalTableBySource).
    field_mapping_draft: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Per-node log accumulator — appended by each node as it completes.
    # Array of {node_id, node_name, status, started_at, completed_at, duration_ms, output, logs}.
    # Frontend reads this from GET /status to drive the right-panel node log view.
    node_logs: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True, default=list)

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MigrationFieldMapping(MigrationBase):
    """Immutable per-field mapping audit trail.

    One row per source column that was mapped to a target field.
    Includes confidence scores, mapping tier, LLM rationale, and LangSmith trace ID.
    """

    __tablename__ = "migration_field_mappings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    migration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plenum_cafm.migration_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Source field(s)
    source_field: Mapped[str] = mapped_column(String(255), nullable=False)
    source_fields: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Multi-column merge: ["first_name", "last_name"]
    merge_strategy: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # concat_space|concat_comma|coalesce|concat_dash

    # Target field
    target_field: Mapped[str] = mapped_column(String(255), nullable=False, index=True)

    # Confidence & tier
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # T1_exact|T1_alias|T1_regex|T1_llm|T2_semantic|T2_human|T2_multi_merge|unmapped

    # Rationale & samples
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    sample_values: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    transformation: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Human review (if T2_human)
    reviewer_id: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)

    # LangSmith trace linkage (for negative feedback on corrections)
    langsmith_run_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Timestamp
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class MigrationHierarchy(MigrationBase):
    """Detected hierarchy relationships (FK, containment, ownership, etc.).

    One row per relationship. Customer can confirm or correct in HITL gate 2.
    """

    __tablename__ = "migration_hierarchy"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    migration_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plenum_cafm.migration_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Relationship definition
    source_table: Mapped[str] = mapped_column(String(255), nullable=False)
    source_column: Mapped[str] = mapped_column(String(255), nullable=False)
    target_table: Mapped[str] = mapped_column(String(255), nullable=False)

    # Relationship type
    relationship_type: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # CONTAINMENT|REFERENCE|OWNERSHIP|PART_OF|SELF_REF

    # Direction & confidence
    direction: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    data_match_rate: Mapped[float] = mapped_column(Float, nullable=False)

    # LLM rationale
    reasoning: Mapped[str] = mapped_column(Text, nullable=False)

    # Customer confirmation
    customer_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


class MappingTemplate(MigrationBase):
    """Stored CMMS mapping configurations for reuse across migrations.

    One row per unique source system → table mapping.
    Entire mapping config (canonical_fields, vendor_aliases, regex_patterns,
    confidence_overrides) stored as JSONB for flexibility and query performance.
    """

    __tablename__ = "mapping_templates"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )

    # Identification
    source_system: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    # table_name: which table this mapping applies to (assets, work_orders, parts, etc.)
    table_name: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    name: Mapped[str] = mapped_column(String(255), nullable=False)  # human-readable name

    # Full mapping config — this is JSONB for flexibility
    config_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    # Expected structure:
    # {
    #   "version": "1.0",
    #   "source_system": "Maximo|Fiix|SAP PM|Archibus|Custom",
    #   "description": "...",
    #   "canonical_fields": { "asset_code": "...", "asset_name": "..." },
    #   "vendor_aliases": { "asset_code": ["alias1", "alias2"], ... },
    #   "regex_patterns": { "asset_code": "^ASSET.*", ... },
    #   "confidence_overrides": { "field": 0.95, ... }
    # }

    # Metadata
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True, index=True)
    created_by: Mapped[Optional[UUID]] = mapped_column(PG_UUID(as_uuid=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SchemaMappingJob(MigrationBase):
    """Master record for a schema mapping session (6-node pipeline).

    One row per schema mapping workflow (separate from 9-node migration pipeline).
    Tracks progress through 6 nodes: ingest → deterministic → semantic → hierarchy → verify → output
    """

    __tablename__ = "schema_mapping_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    external_cmms_name: Mapped[str] = mapped_column(String(100), nullable=False)

    # Schema source info
    schema_source: Mapped[str] = mapped_column(
        String(50), nullable=False
    )  # database_url | yaml_file | json_file | ddl_sql
    schema_format: Mapped[str] = mapped_column(String(20), nullable=False)  # sql | yaml | json

    # Current progress
    status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="ingest",
        index=True,
    )  # ingest | deterministic | semantic | hierarchy | verify | output | complete | error
    current_node: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # 1-6
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Mapping statistics
    total_tables: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_fields: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier1_mapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier2_auto_mapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    tier2_flagged: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unmapped: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detected_fk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Hierarchy info
    hierarchy_depth: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    implicit_hierarchy_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Final output
    final_mapping_config: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    final_summary: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    mapping_coverage_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Output artifacts (Azure Blob URLs — populated by Node 7)
    output_json_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_csv_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_sql_url: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # New schema created by Node 8 (e.g. plenum_cafm_maximo_20260518143200)
    new_schema_name: Mapped[Optional[str]] = mapped_column(String(200), nullable=True)

    # Error tracking
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_timestamp: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # HITL gate state — written by gate nodes so the frontend can read the payload
    # and POST decisions back via /approve.
    # gate_type values: "field_mapping" | "hierarchy"
    pending_gate_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    pending_gate_payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    # Per-node log accumulator — appended by each node as it completes.
    # Array of {node_id, node_name, status, started_at, completed_at, duration_ms, output, logs}.
    # Frontend reads this from GET /status to drive the right-panel node log view.
    node_logs: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True, default=list)

    # Node-specific state (serialized)
    node_state_json: Mapped[Optional[dict[str, Any]]] = mapped_column(
        JSONB, nullable=True
    )  # Full SchemaMappingState for resuming

    # Timestamps
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class SchemaMappingFieldMapping(MigrationBase):
    """Audit trail for schema mapping field mappings.

    One row per source field that was mapped to a canonical field.
    Includes confidence, tier, and LLM rationale.
    """

    __tablename__ = "schema_mapping_field_mappings"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    schema_mapping_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("plenum_cafm.schema_mapping_jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Field mapping
    source_field: Mapped[str] = mapped_column(String(255), nullable=False)
    source_table: Mapped[str] = mapped_column(String(255), nullable=False)
    target_field: Mapped[str] = mapped_column(String(255), nullable=False)

    # Confidence & tier
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    tier: Mapped[str] = mapped_column(
        String(30), nullable=False
    )  # T1_exact | T1_alias | T1_regex | T2_semantic | unmapped

    # Rationale
    rationale: Mapped[str] = mapped_column(Text, nullable=False)

    # Timestamp
    mapped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)


class FiixIngestionJob(MigrationBase):
    """Tracks a full Fiix data ingestion run (3-node pipeline).

    One row per ingestion trigger.  Stores fetch stats, preprocess stats,
    write results, and error lists as JSONB for flexible querying.
    """

    __tablename__ = "fiix_ingestion_jobs"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False, index=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    # Progress
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="pending", index=True
    )  # pending | fetching | preprocessing | writing | complete | failed
    current_step: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    progress_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Fetch stats (Node 1)
    total_records_fetched: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fetch_stats: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    fetch_errors: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    # Preprocess stats (Node 2)
    total_records_preprocessed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    preprocess_stats: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    preprocess_warnings: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    # Write results (Node 3)
    total_records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    write_results: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    write_errors: Mapped[Optional[list[Any]]] = mapped_column(JSONB, nullable=True)

    # Error info
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_node: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Timestamps
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )


class FiixSchemaCache(MigrationBase):
    """Persisted Fiix schema (extracted tables + columns / mapper config) so the Fiix
    API isn't re-pulled on every schema-mapping run.

    Keyed by a hash of the Fiix subdomain. Unlike a file cache this survives container/
    image rebuilds, is queryable, and is the single source of truth for a tenant's Fiix
    schema. Refresh by passing force_refresh=True (re-pull + upsert).
    """

    __tablename__ = "fiix_schema_cache"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    subdomain_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    subdomain: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mapper_config: Mapped[Any] = mapped_column(JSONB, nullable=False)
    table_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    column_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UdrEntityRelationship(MigrationBase):
    """Feature 7 — Layer 3 relationship graph (Postgres metadata layer).

    One row per pre-computed entity edge (asset→site, work_order→asset, …) produced
    by udr.graph.build_relationship_graph. ``provenance`` distinguishes schema-defined
    edges from Job-1 'llm_inferred' enrichment. Queryable by the orchestration agent
    for the entity 'work cloud'. Upsert is idempotent on the unique edge key, so the
    canonical UDR is updated incrementally (not rebuilt) per ingestion.
    """

    __tablename__ = "udr_entity_relationships"
    __table_args__ = (
        UniqueConstraint(
            "run_id", "src_entity", "src_column", "dst_entity", "dst_column", "provenance",
            name="uq_udr_rel_edge",
        ),
        {"schema": "plenum_cafm"},
    )

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    src_entity: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    src_column: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    rel_type: Mapped[str] = mapped_column(String(32), nullable=False, default="REFERENCES")
    dst_entity: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    dst_column: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provenance: Mapped[str] = mapped_column(String(32), nullable=False, default="schema")
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    evidence: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class UdrRunGraph(MigrationBase):
    """Feature 7 — per-run snapshot of the final table + column metadata (Layer 3).

    Stores the canonical structure produced for one UDR batch run (final table
    metadata of 7.11 AC3 and column metadata of 7.11 AC4) as JSONB, plus the edge
    count. Keyed by run_id (one snapshot per run, upserted).
    """

    __tablename__ = "udr_run_graph"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    run_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    table_metadata: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    column_metadata: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    relationship_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ActivityLogEntry(MigrationBase):
    """Feature 4 — Activity Summary entry (Section 1).

    One row per the FOUR triggers (query-run / external-input / threshold-or-expiry
    / scheduled) — NOT per tool call or session. Cross-session, queryable newest-first,
    and the source for the top-right notification icon. The full Processing Log
    (Section 2) is assembled from node_logs + the WS stream; ``refs`` links to e.g. the
    UDR run / saved script. ``read`` drives the unread badge.
    """

    __tablename__ = "activity_log_entries"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, index=True)
    # query | external_input | threshold | scheduled
    trigger: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    trigger_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    # completed | pending_human_input | failed | escalated
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="completed", index=True)
    # green (completed) | orange (escalated) | red (failed / pending)
    notif_color: Mapped[str] = mapped_column(String(16), nullable=False, default="green")
    refs: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    processing_log: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    read: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )


class ActivityAction(MigrationBase):
    """Feature 4 — Activity Log Section 3: a human-in-the-loop inline decision (AL.3/AL.4).

    A red-flagged processing-log item (e.g. "change asset 4471 -> 4465") rendered as a
    'closed query' with <=4 predefined options. The system NEVER proceeds autonomously on a
    pending action (AL.4 AC3): it stays ``pending`` until a human resolves it, or the
    server-side timeout sweep marks it ``timed_out`` (and raises a 'Pending Human Response'
    summary entry + flips the notification red) after ``deadline_at`` (created_at + 15 min).
    """

    __tablename__ = "activity_actions"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False, index=True)
    organization_id: Mapped[Optional[UUID]] = mapped_column(
        PG_UUID(as_uuid=True), nullable=True, index=True
    )
    # field_change | approval | escalation | reassignment
    kind: Mapped[str] = mapped_column(String(32), nullable=False, default="approval")
    label: Mapped[str] = mapped_column(Text, nullable=False)  # the red-flagged item text
    options: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)  # [{id,label,value}] (<=4)
    # pending | resolved | timed_out
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending", index=True)
    chosen_option_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    resolution_note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow
    )

