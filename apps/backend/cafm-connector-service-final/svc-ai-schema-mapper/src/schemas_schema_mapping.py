"""Pydantic schemas for schema mapping API endpoints.

Separate from data ingestion schemas — focused on schema definition mapping.
"""

from typing import Optional, Any
from pydantic import BaseModel, Field


# ────────────────────────────────────────────────────────────────────────────
# Requests
# ────────────────────────────────────────────────────────────────────────────

class FiixCredentialsRequest(BaseModel):
    """Fiix CMMS API credentials (same fields as Schema Mapper start panel)."""

    fiix_subdomain: Optional[str] = Field(
        None, description="Tenant subdomain, e.g. plenumtechnology"
    )
    fiix_app_key: Optional[str] = None
    fiix_access_key: Optional[str] = None
    fiix_secret_key: Optional[str] = None


class StartSchemaMappingRequest(BaseModel):
    """Start a schema mapping session."""
    external_cmms_name: str = Field(..., description="Maximo, Fiix, SAP PM, etc.")
    external_cmms_version: Optional[str] = None

    # How we receive the schema definition
    schema_source: str = Field(..., description="database_url | yaml_file | json_file | ddl_sql | fiix_api")
    schema_format: str = Field(..., description="sql | yaml | json")

    # Connection or content
    db_url: Optional[str] = None  # if schema_source is database_url
    schema_content: Optional[str] = None  # if schema_source is yaml_file, json_file, or ddl_sql

    organization_id: str

    # Fiix CMMS credentials — required when schema_source is "fiix_api".
    # User-supplied values override FIIX_* environment variables.
    fiix_subdomain: Optional[str] = None
    fiix_app_key: Optional[str] = None
    fiix_access_key: Optional[str] = None
    fiix_secret_key: Optional[str] = None


class HierarchyApprovalRequest(BaseModel):
    """User submits hierarchy approval/corrections."""
    schema_mapping_id: str

    # Approved foreign key relationships
    approved_foreign_keys: list[dict[str, Any]]

    # Rejected FKs (if any)
    rejected_foreign_keys: Optional[list[dict[str, Any]]] = None

    # User corrections to detected hierarchy
    hierarchy_corrections: Optional[dict[str, Any]] = None

    # Notes from user
    reviewer_notes: Optional[str] = None


# ────────────────────────────────────────────────────────────────────────────
# Responses
# ────────────────────────────────────────────────────────────────────────────

class FieldMappingResponse(BaseModel):
    """Single field mapping in response."""
    source_field: str
    source_table: str
    target_field: str
    confidence: float
    tier: str  # T1_exact, T1_alias, T2_semantic, unmapped
    rationale: str
    auto_mappable: bool


class SchemaMappingProgressResponse(BaseModel):
    """Status of ongoing schema mapping."""
    schema_mapping_id: str
    status: str  # ingest | mapping | semantic | hierarchy | complete | error
    current_node: str
    progress_percent: float

    # Counts
    total_tables: int
    total_columns: int
    mapped_fields: int
    unmapped_fields: int

    # Current stage results
    tier1_count: int
    tier2_auto_count: int
    tier2_flagged_count: int

    # If hierarchy detection done
    detected_fk_count: Optional[int] = None
    hierarchy_levels: Optional[int] = None

    error_message: Optional[str] = None


class MappingResultResponse(BaseModel):
    """Complete mapping results after Node 3 (before hierarchy)."""
    schema_mapping_id: str
    external_cmms_name: str

    # Mapped fields (auto-accept)
    tier1_mappings: list[FieldMappingResponse]
    tier1_count: int

    # Semantic mappings (user review)
    tier2_auto: list[FieldMappingResponse]
    tier2_flagged: list[FieldMappingResponse]
    tier2_auto_count: int
    tier2_flagged_count: int

    # Unmapped
    unmapped_fields: list[dict[str, Any]]
    unmapped_count: int

    # Overall
    overall_confidence: float
    ready_for_hierarchy_detection: bool


class HierarchyApprovalResponse(BaseModel):
    """Response after hierarchy verification."""
    schema_mapping_id: str
    hierarchy_approved: bool
    hierarchy_status: str  # approved | needs_correction | error

    # FK summary
    total_fks_detected: int
    total_fks_approved: int
    total_fks_rejected: int

    # Hierarchy structure
    hierarchy_depth: int
    root_table: Optional[str]

    # Next step
    ready_for_output: bool
    notes: list[str]


class FinalMappingConfigResponse(BaseModel):
    """Final output mapping configuration."""
    schema_mapping_id: str
    source_system: str
    mapping_version: str
    created_at: str

    # The actual mapping config (compatible with JsonMapperConfig)
    canonical_fields: dict[str, str]  # {field_name: description}
    vendor_aliases: dict[str, list[str]]  # {canonical_field: [alias1, alias2, ...]}

    # Hierarchy info
    detected_hierarchies: dict[str, Any]
    foreign_keys: list[dict[str, Any]]

    # Extra fields not in canonical schema
    unmappable_fields: list[dict[str, Any]]

    # Audit
    stats: dict[str, Any]  # {
        #   "total_fields": int,
        #   "mapped_fields": int,
        #   "mapping_coverage": float,
        #   "hierarchy_confidence": float,
        # }

    # Can this be used directly?
    ready_for_use: bool
    warnings: list[str]
    recommendations: list[str]


class SchemaMappingListResponse(BaseModel):
    """List of schema mappings."""
    total: int
    items: list[dict[str, Any]]  # {
        #   "schema_mapping_id": str,
        #   "external_cmms_name": str,
        #   "status": str,
        #   "created_at": str,
        #   "mapping_coverage": float,
        #   ...
        # }


class SchemaMappingErrorResponse(BaseModel):
    """Error response."""
    schema_mapping_id: Optional[str] = None
    error: str
    error_code: str
    details: Optional[dict[str, Any]] = None
