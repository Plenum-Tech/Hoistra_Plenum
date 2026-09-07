"""LangGraph state machine state definitions for svc-AI-Schema-Mapper.

TypedDict structures for MigrationState and supporting domain objects.
"""

from typing import TypedDict, Optional, Any
from datetime import datetime


class ExtraFieldConfig(TypedDict, total=False):
    """DDL intent for an unmapped field — built by Node 4, executed by Node 9.

    storage_strategy:
      "custom"       — add a new column to a plenum_cafm table (requires DDL)
      "raw_metadata" — store in existing raw_metadata JSONB column (no DDL)
      "skip"         — discard the field entirely
    """
    source_field: str
    source_table: str            # which source table this came from
    storage_strategy: str        # "custom" | "raw_metadata" | "skip"

    # ── DDL fields (only when storage_strategy == "custom") ──────────
    target_table: str            # plenum_cafm table to add the column to
    custom_column_name: str      # exact column name to create
    data_type: str               # SQL type: VARCHAR(255), INTEGER, BOOLEAN, etc.
    is_new_table: bool           # True if target_table doesn't exist yet
    new_table_pk: str            # PK column name for new tables (default "id")
    nullable: bool               # whether the new column allows NULLs (default True)

    auto_created: bool           # 7.7 AC4 — system-proposed new column, shown for confirmation
    user_approved: bool


class FieldMapping(TypedDict, total=False):
    """Per-field mapping decision."""
    source_field: str
    target_field: str
    confidence: float  # 0.0-1.0
    tier: str  # T1_exact, T1_alias, T1_regex, T1_llm, T2_semantic, T2_human, T2_multi_merge, unmapped
    rationale: str
    sample_values: list[str]
    data_type: str  # user-chosen SQL type for a new-table column (else inferred at write)
    # Feature 5: up to 3 alternative target fits, best-first, each {target_field, confidence, is_primary}.
    candidates: list
    # #6: per-component match score breakdown for UI display (semantic / keyword /
    # datatype / numeric_pattern / ontology / final). Display-only; never alters routing.
    score_breakdown: dict
    transformation: Optional[str]  # e.g., "concat_space", "coalesce", None
    source_fields: Optional[list[str]]  # for multi-merge strategies
    merge_strategy: Optional[str]  # concat_space, concat_comma, coalesce, concat_dash
    reviewer_id: Optional[str]  # user who approved/rejected
    review_timestamp: Optional[datetime]
    langsmith_run_id: Optional[str]  # LangSmith trace ID for this field's mapping (Strategy 4 or Node 3)


class HierarchyRelationship(TypedDict, total=False):
    """Detected FK or containment relationship."""
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    relationship_type: str  # CONTAINMENT, REFERENCE, OWNERSHIP, PART_OF, SELF_REF
    confidence: float
    data_match_rate: float
    reasoning: str
    customer_confirmed: bool
    confirmed_at: Optional[datetime]
    system_default: bool  # Plenum template edge (not in import file)
    mapping_note: bool  # Import table → Plenum tier mapping row


class MigrationState(TypedDict, total=False):
    """
    Full state for a single migration run through the 9-node pipeline.

    CRITICAL: source_blob_url stores URL only, NOT file bytes. Node 1 downloads,
    parses, then clears source_file_bytes before checkpoint write.
    """

    # ── Session & Metadata ────────────────────────────────────────────────
    migration_id: str
    organization_id: str
    cmms_name: str  # "Maximo", "Fiix", "SAP PM", "Archibus", "Custom"
    source_filename: str  # original uploaded filename (for #10 document inventory)
    source_system: str  # source CMMS identifier from customer
    uploaded_by: str  # user ID
    upload_timestamp: datetime

    # ── Node 1: Ingest ────────────────────────────────────────────────────
    source_blob_url: str  # Azure Blob URL (NOT file bytes)
    source_file_bytes: Optional[bytes]  # Transient; cleared before checkpoint
    source_encoding: str  # e.g., "utf-8", "iso-8859-1"
    source_delimiter: str  # "," or "\t" or ";"
    detected_file_format: str  # "csv" or "xlsx"

    parsed_tables: dict[str, Any]  # dict[table_name] = pandas.DataFrame.to_dict() — 5-row sample
    full_tables: dict[str, Any]    # dict[table_name] = full file records — set by Node 1, used by Node 5+
    # Blob-offload ref for full_tables (graph/bulk_tables.py). When set, the bulk rows live in
    # Azure Blob (NOT the checkpoint) and full_tables is emptied before each checkpoint write, so
    # the state never carries >1 GB into Postgres; the node wrapper hydrates it back on demand.
    full_tables_ref: Optional[str]
    row_count: int
    column_count: int
    table_health: dict[str, Any]  # TableHealth metrics per table
    # Node 1 first data-quality step: NaN scan across every column of every table.
    # Detect-only (filling happens in Node 5) — surfaced in the Activity Log + Query Space.
    nan_report: dict[str, Any]
    # Node 1 second data-quality step: columns inside a table whose values are row-for-row
    # identical (works.tagnum == works.id) merged to one name BEFORE table mapping, so the rest
    # of the pipeline never sees the duplicate. Records what was kept and what was dropped.
    duplicate_column_report: dict[str, Any]
    cafm_table_matches: dict[str, Optional[str]]  # Node 1: source sheet → plenum_cafm table (or None)
    cafm_table_match_confidence: dict[str, float]  # Node 1: source sheet → match confidence 0-1
    dataset_summary: str  # Human-readable description of dataset
    column_descriptions: dict[str, str]  # dict[column_name] = "semantic description"

    # ── Node 2: Deterministic Mapping ────────────────────────────────────
    tier1_mappings_by_table: dict[str, list[FieldMapping]]  # dict[table_name] = [FieldMapping]
    tier1_mapped_count: int
    unresolved_by_table: dict[str, list[str]]  # dict[table_name] = [unresolved field names]
    # best-guess auto-suggestion per unmapped field: {table: {field: {target_field, confidence,
    # candidates, sample_values}}} — lets the gate offer "suggested: X (Y%)" + send-to-semantic
    unresolved_suggestions_by_table: dict[str, Any]
    # content-aware table routing candidates per source sheet, ranked by column overlap:
    # {source_table: [{table, mapped, total, pct}]} — lets the gate offer the best target.
    table_match_candidates_by_table: dict[str, Any]
    # value-centric merge: {source_table: {representative_col: [merged_cols]}} — columns with
    # identical VALUES collapsed to one (e.g. ASSETNUM + asset_ref → one asset-id column).
    merged_columns_by_table: dict[str, Any]
    # Challenge 1: partial-similarity column pairs (60–95% value overlap) flagged for manual
    # review: {source_table: [{column_a, column_b, overlap, sample_a, sample_b}]}.
    near_duplicate_columns_by_table: dict[str, Any]

    # ── Pre-Semantic Gate (between Node 2 and Node 3) ────────────────────
    pre_semantic_review_payload: Optional[dict[str, Any]]  # Interrupt payload for the pre-semantic gate
    tier1_approved_by_table: dict[str, list[FieldMapping]]  # T1 fields approved at pre-semantic gate (alias auto-included)
    # Primary-key confirmation (HITL) — the user-approved PK per source table, decided at the
    # pk-review gate BEFORE hierarchy/FK detection. {table: [pk_col, ...]}; [] = surrogate key
    # approved. Overrides the auto-detected PK in the B8.1 report + downstream. Declared channel.
    pk_confirmed_by_table: dict[str, list[str]]
    # True once the pk-review gate (Group A step 1: B8.1 primary-key IDENTIFICATION + approval)
    # has been ANSWERED in this run — prevents re-interrupting it. Cleared on a restart.
    pk_reviewed: bool
    # True once the SECOND Group-A gate — B7.1 unique-table identification (showing each table's
    # confirmed PK) + approval — has been answered. Runs AFTER pk_reviewed. Cleared on a restart.
    unique_table_reviewed: bool
    # B14.1 column-mapping gate — HITL per-column approval of the matched source→destination column
    # (from column_intelligence.dest_mapping), runs AFTER the B20 classification gate. Answered flag
    # + the user's per-column overrides: {source_table: {source_field: "<dest_col>" | "__new__"}}.
    # "__new__" = create as a new column (don't merge); a dest column = re-target. Applied to the
    # final field mappings at output-generation. Cleared on a restart.
    column_mapping_reviewed: bool
    column_dest_overrides: dict[str, dict[str, str]]
    # Two-pass reorder of the pre-semantic node: pass 1 shows the "Confirm table routing" gate and
    # CAPTURES the routing choice (pending_table_overrides), builds the column-intelligence analysis,
    # then the B20 + B14.1 gates run; the graph loops back for pass 2, which shows the "Column
    # matching" gate and applies routing + matching together (the intertwined resume logic runs once
    # here). pre_semantic_ci_built = pass 1 done; pending_table_overrides = routing from pass 1.
    pre_semantic_ci_built: bool
    # Cached B7.1→B12.1 table-resolution report from pass 1, reused on the pass-2 / resume
    # re-entry so the dedup + resolution scan don't run twice. Cleared with pre_semantic_ci_built.
    table_resolution_report: dict
    pending_table_overrides: dict[str, Any]
    # True once the pre-semantic gate has been ANSWERED in this run. The node checks it at the top
    # and skips a second interrupt(), so an already-decided gate can't re-open (e.g. a table-routing
    # override re-partitions tier-1 mappings, re-creating reviewable items). Cleared at the start of
    # deterministic mapping, so a genuine restart-from-node still re-opens the gate. MUST be a
    # declared channel — see canonical_overrides below (undeclared keys are dropped on checkpoint).
    pre_semantic_reviewed: bool
    # User-pinned canonical names from the pre-semantic "Unified column names" gate:
    # {group_id | "table.col": canonical_name}, e.g. {"G2": "asset_id"}. MUST be a declared
    # channel — LangGraph derives its persistent channels from this TypedDict, so an undeclared
    # key is silently dropped on checkpoint (aupdate_state and `return state` both no-op it),
    # which is why pins like vendors.id → asset_id never survived. Default (replace) reducer.
    canonical_overrides: dict[str, str]
    # Built column-intelligence report (groups / column_canonical / classification / dest_mapping).
    # Declared so the pin-stamped CI persists for /state-canonicals + the Tier-2 summary instead
    # of being rebuilt from scratch on every read.
    column_intelligence: dict[str, Any]
    # True once the B20.1 classification gate has been ANSWERED this run — a HITL select/unselect of
    # the FK / Shared assignments. Same re-open guard semantics as pre_semantic_reviewed. Declared.
    grouping_reviewed: bool
    # group_ids the user DE-selected at the classification gate (their lookup tables / FK are not
    # applied). Declared channel so it persists through the checkpoint.
    classification_rejected: list[str]
    # {group_id: "fk_to_shared" | "shared_to_fk"} — human re-classifications applied at the B20.1
    # gate (a FK group demoted to a shared attribute, or a shared attribute promoted to FK).
    # Declared channel so the decisions persist and are re-applied to the post-write UDR report.
    classification_overrides: dict[str, str]

    # ── Node 3: Semantic Mapping ─────────────────────────────────────────
    tier2_auto_by_table: dict[str, list[FieldMapping]]  # confidence >= 0.85, grouped by table
    tier2_flagged_by_table: dict[str, list[FieldMapping]]  # 0.65 <= confidence < 0.85, grouped by table
    tier2_unmappable_by_table: dict[str, list[str]]  # source fields < 0.65 confidence, grouped by table
    overall_confidence: float  # weighted average of all mapped fields

    # ── Node 4: Human Review ──────────────────────────────────────────────
    human_review_payload: Optional[dict[str, Any]]  # Interrupt payload for GATE 1 (grouped by table)
    tier2_human_decisions_by_table: dict[str, list[FieldMapping]]  # dict[table_name] = decisions
    tier2_human_count: int
    extra_fields_config: list[ExtraFieldConfig]  # DDL intent for unmapped fields — built by Node 4, executed by Node 9
    # True once the field-mapping gate (Gate 1) has been ANSWERED in this run. Same re-open guard as
    # pre_semantic_reviewed: the node checks it at the top and skips a second interrupt(), so an
    # already-decided gate can't re-open (e.g. LangGraph re-invokes the node, or a table_routing edit
    # re-enters it). Cleared at the start of deterministic mapping, so a genuine restart-from-node still
    # re-opens the gate. MUST be a declared channel — undeclared keys are dropped on checkpoint.
    field_mapping_reviewed: bool

    # ── Table Routing (multi-table support) ───────────────────────────────
    # Maps each source sheet name to its target entity type in the IntermediateSchema.
    # Built by Node 2 via name/field inference; updated by Node 4 for is_new_table=True entries.
    # Example: {"Assets": "assets", "Work Orders": "work_orders", "Custom": "custom_table"}
    table_routing: dict[str, str]
    # Names of brand-new plenum_cafm tables to be created by Node 9 DDL.
    # Populated by Node 4 when extra_fields_config contains is_new_table=True entries.
    new_tables: list[str]

    # ── Node 5: Preprocess ────────────────────────────────────────────────
    cleaned_tables: dict[str, Any]  # dict[table_name] = [records] — Deduplicated, null-handled, coerced
    cleaned_tables_ref: Optional[str]  # Blob-offload ref for cleaned_tables (see full_tables_ref)
    row_count_post_dedup_by_table: dict[str, int]  # dict[table_name] = post-dedup row count
    dedup_drop_count_by_table: dict[str, int]  # dict[table_name] = rows dropped
    data_quality_warnings: list[str]

    # ── Section-2 per-node timing (Activity-Log Processing Log) ───────────
    deterministic_duration_ms: float  # how long the deterministic mapper node took
    deterministic_at: str             # ISO timestamp the deterministic node finished
    semantic_duration_ms: float       # how long the semantic mapper node took
    semantic_at: str                  # ISO timestamp the semantic node finished

    # ── Node 6-7: Hierarchy Detection & Verification ──────────────────────
    fk_candidates: list[HierarchyRelationship]  # Initial FK scan (source_table → target_table)
    confirmed_hierarchies: list[HierarchyRelationship]  # After validation + human confirmation
    containment_hierarchy_by_table: dict[str, Any]  # dict[table_name] = nested tree structure
    hierarchy_cycles: list[list[str]]  # Any detected cycles (list of cycle paths)
    hierarchy_review_payload: Optional[dict[str, Any]]  # Interrupt payload for GATE 2
    implicit_hierarchies: dict[str, Any]  # SAP-style code hierarchies per table
    # True once the hierarchy gate (Gate 2) has been ANSWERED (and EL-M.7 passed) in this run. Same
    # re-open guard as pre_semantic_reviewed/field_mapping_reviewed: skips a second interrupt() so a
    # decided gate can't re-open. NOT set on the cycle-remaining re-interrupt path (el_m7 fails), so
    # unresolved cycles still re-prompt. Cleared at the start of deterministic mapping for a genuine
    # restart. MUST be a declared channel — undeclared keys are dropped on checkpoint.
    hierarchy_reviewed: bool

    # ── Node 8: Output Generation ─────────────────────────────────────────
    output_json_url: str  # Azure Blob URL
    output_csv_url: str  # Azure Blob URL
    output_sql_url: str  # Azure Blob URL
    output_sql_script: str  # Generated SQL statements (used by Node 9 direct DB apply)
    migration_report_url: str  # PDF report
    mapping_flow_url: str  # Optional flow diagram

    # IntermediateSchema Pydantic object (serialized as dict for checkpoint)
    intermediate_schema: Optional[dict[str, Any]]

    # ── Node 9: Write to Platform ─────────────────────────────────────────
    write_review_payload: Optional[dict[str, Any]]  # Interrupt payload for GATE 3
    handoff_status: str  # "pending", "sent", "acknowledged", "failed"
    svc_ingestion_response: Optional[dict[str, Any]]  # Response from svc-ingestion API

    # ── Error & Status Tracking ──────────────────────────────────────────
    current_step: int  # 1-9, which node last ran
    status: str  # "running", "awaiting_review", "complete", "failed", "cancelled"
    error_message: Optional[str]
    error_node: Optional[int]
    error_timestamp: Optional[datetime]

    # ── LangSmith Integration ────────────────────────────────────────────
    langsmith_run_id: Optional[str]  # Master run ID for this migration (set in worker.py config)
    node_langsmith_run_ids: dict[int, str]  # Per-node trace tracking

    # ── Evaluation Layer Results ─────────────────────────────────────────
    el_m1_passed: bool
    el_m2_passed: bool
    el_m3_passed: bool
    el_3_0_force_gate1: bool  # True if overall_confidence < 0.80
    el_m4_passed: bool
    el_m5_passed: bool
    el_m6_passed: bool
    el_m7_passed: bool
    el_m8_passed: bool
    el_m9_passed: bool

    # ── Audit Trail ──────────────────────────────────────────────────────
    event_log: list[dict[str, Any]]  # [{timestamp, event, detail}, ...]
    checkpoint_count: int

    # ── Feature 7: UDR run (post-write relationship graph + Activity-Log entry) ──
    udr_run_id: Optional[str]
    udr_table_count: int
    udr_column_count: int
    udr_relationship_count: int
    udr_blocked: bool
    udr_status: Optional[str]  # completed | pending_human_input | failed | skipped
    udr_activity_id: Optional[str]
