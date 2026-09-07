"""State definitions for the Fiix data ingestion pipeline.

3-node linear graph:
  fiix_fetch_node → fiix_preprocess_node → fiix_write_node

No HITL gates — this is a fully automated ETL pipeline.
"""

from typing import TypedDict, Optional, Any
from datetime import datetime


class FiixIngestionState(TypedDict, total=False):
    """Full state for one Fiix data ingestion run."""

    # ── Identity ──────────────────────────────────────────────────────────────
    ingestion_id: str       # UUID — matches FiixIngestionJob.id
    organization_id: str
    created_by: str
    created_at: datetime

    # ── Target schema (resolved from SchemaMappingJob.new_schema_name) ─────────
    # This is the PostgreSQL schema the write node will INSERT into.
    # Set from SchemaMappingJob.new_schema_name before the graph runs.
    # e.g. "plenum_cafm_fiix_20260519143200"
    target_schema: str
    schema_mapping_id: Optional[str]     # SchemaMappingJob UUID used to resolve target_schema

    # ── Fiix credentials (passed in at start, not stored in checkpoints) ──────
    fiix_subdomain: str
    fiix_app_key: str
    fiix_access_key: str
    fiix_secret_key: str

    # ── Node 1: Fetch ─────────────────────────────────────────────────────────
    # Raw records straight from the Fiix API, keyed by Fiix object name.
    # e.g. {"Asset": [{id: 1, strCode: "MOB-001", ...}, ...], ...}
    fetched_objects: dict[str, list[dict[str, Any]]]
    fetch_stats: dict[str, int]          # {object_name: record_count}
    total_records_fetched: int
    fetch_errors: list[str]              # non-fatal per-object errors

    # ── Node 2: Preprocess ────────────────────────────────────────────────────
    # Records after field rename, dedup, null fill, date coercion, UUID assignment.
    # Keyed by TARGET plenum_cafm table name (not Fiix object name).
    # e.g. {"assets": [{id: "uuid...", asset_code: "MOB-001", ...}, ...], ...}
    preprocessed_tables: dict[str, list[dict[str, Any]]]
    preprocess_stats: dict[str, Any]     # dedup counts, warning counts per table
    preprocess_warnings: list[str]
    total_records_preprocessed: int

    # ── Node 3: Write ─────────────────────────────────────────────────────────
    write_results: dict[str, dict[str, int]]   # {table: {inserted, skipped, errors}}
    total_records_written: int
    write_errors: list[str]

    # ── DB Session (injected by worker, not checkpointed) ─────────────────────
    db_session: Optional[Any]            # AsyncSession

    # ── Status & Audit ────────────────────────────────────────────────────────
    status: str              # pending | fetching | preprocessing | writing | complete | failed
    current_node: int        # 1 | 2 | 3
    error_message: Optional[str]
    error_node: Optional[int]
    notes: list[str]
    started_at: datetime
    completed_at: Optional[datetime]
