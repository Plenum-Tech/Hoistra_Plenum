"""Build IntermediateSchema Pydantic model from cleaned data and mappings.

IntermediateSchema is the standardised contract between all ingestion agents
(PDF, DOCX, CSV, etc.) and the rest of the platform.

This module builds IntermediateSchema from the cleaned, mapped, and hierarchically
structured data, ready for handoff to svc-ingestion.
"""

import logging
from datetime import datetime
from typing import Dict, List, Any, Optional
from uuid import uuid4

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# NOTE: In production, this would import from svc-ingestion:
# from svc_ingestion.shared.intermediate_schema import IntermediateSchema
# For now, we'll define a minimal version here


class IntermediateSchema:
    """
    Standardised intermediate schema for all ingestion sources.

    Contract between all ingestion agents and svc-ingestion.
    """

    def __init__(
        self,
        ingestion_id: str,
        source_type: str,
        agent_id: str,
        source_filename: str,
        source_blob_url: str,
        extracted_at: str,
        extraction_method: str,
        model_used: str,
        entities: Dict[str, List[Dict]],
        confidence: Dict[str, Any],
        audit: Dict[str, Any],
    ):
        self.ingestion_id = ingestion_id
        self.source_type = source_type
        self.agent_id = agent_id
        self.source_filename = source_filename
        self.source_blob_url = source_blob_url
        self.extracted_at = extracted_at
        self.extraction_method = extraction_method
        self.model_used = model_used
        self.entities = entities
        self.confidence = confidence
        self.audit = audit

    def dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "ingestion_id": self.ingestion_id,
            "source_type": self.source_type,
            "agent_id": self.agent_id,
            "source_filename": self.source_filename,
            "source_blob_url": self.source_blob_url,
            "extracted_at": self.extracted_at,
            "extraction_method": self.extraction_method,
            "model_used": self.model_used,
            "entities": self.entities,
            "confidence": self.confidence,
            "audit": self.audit,
        }


def build_intermediate_schema(
    migration_id: str,
    cmms_name: str,
    source_blob_url: str,
    cleaned_tables: Dict[str, List[Dict]],
    tier1_mappings: List[Dict],
    tier2_auto_mappings: List[Dict],
    tier2_human_decisions: List[Dict],
    overall_confidence: float,
    confirmed_hierarchies: List[Dict],
    table_routing: Optional[Dict[str, str]] = None,
    new_tables: Optional[List[str]] = None,
    source_filename: Optional[str] = None,
    detected_file_format: Optional[str] = None,
) -> IntermediateSchema:
    """
    Build IntermediateSchema from cleaned, mapped data.

    Args:
        migration_id: Migration ID
        cmms_name: Source CMMS system
        source_blob_url: Blob URL of original file
        cleaned_tables: Cleaned and deduplicated data (columns already renamed to canonical names)
        tier1_mappings: Tier 1 field mappings
        tier2_auto_mappings: Tier 2 auto-accepted mappings
        tier2_human_decisions: Tier 2 human-approved mappings
        overall_confidence: Overall mapping confidence
        confirmed_hierarchies: Customer-confirmed FK relationships
        table_routing: Optional mapping of source_sheet_name → target_entity_type.
            Built by Node 2 and updated by Node 4. When provided, used as primary
            routing authority. Unknown tables not in the routing are included
            directly using their lowercase source name as the entity type.
        new_tables: Optional list of brand-new plenum_cafm table names created by Node 9 DDL.
        source_filename: Original filename (used in schema metadata).
        detected_file_format: "csv" or "xlsx" (used in schema metadata).

    Returns:
        IntermediateSchema Pydantic model instance
    """

    logger.info(f"[Schema Builder] Building IntermediateSchema for {migration_id}...")

    # Extract entities from cleaned tables.
    # Routing priority:
    #   1. table_routing dict (built by Node 2, updated by Node 4 for new tables)
    #   2. Name-pattern fallback (same patterns as Node 2 uses)
    #   3. Unknown → use source table name as entity type (new/custom tables pass through)
    entities: Dict[str, List[Dict]] = {}

    # Fallback name-pattern map (same as Node 2 _TABLE_NAME_PATTERNS)
    _FALLBACK_PATTERNS = [
        ("asset", "assets"), ("equipment", "assets"), ("equip", "assets"),
        ("work_order", "work_orders"), ("workorder", "work_orders"), ("wo", "work_orders"),
        ("scheduled_pm", "maintenance_plans"), ("maintenance", "maintenance_plans"), ("pm", "maintenance_plans"),
        ("part", "spare_parts"), ("inventory", "spare_parts"),
        ("user", "technicians"), ("technician", "technicians"), ("personnel", "technicians"),
        ("inspection", "findings"), ("finding", "findings"),
        ("site", "locations"), ("location", "locations"),
    ]

    for table_name, records in cleaned_tables.items():
        if not records:
            continue

        # 1. Use table_routing if available
        if table_routing and table_name in table_routing:
            entity_type = table_routing[table_name]
        else:
            # 2. Name-pattern fallback
            table_lower = table_name.lower()
            entity_type = None
            for pattern, etype in _FALLBACK_PATTERNS:
                if pattern in table_lower:
                    entity_type = etype
                    break
            # 3. Unknown — include as its own entity type (new/custom table)
            if not entity_type:
                entity_type = table_lower
                logger.info(
                    f"[Schema Builder] Unknown table '{table_name}' → passthrough as entity '{entity_type}'"
                )

        if entity_type not in entities:
            entities[entity_type] = []
        entities[entity_type].extend(records)
        logger.info(f"[Schema Builder] '{table_name}' ({len(records)} records) → entity '{entity_type}'")

    # Ensure all required entity types exist (even if empty)
    required_entities = ["assets", "work_orders", "spare_parts", "technicians"]
    for entity_type in required_entities:
        if entity_type not in entities:
            entities[entity_type] = []

    # Build confidence breakdown
    confidence_breakdown = {
        "overall": "high" if overall_confidence >= 0.85 else ("medium" if overall_confidence >= 0.70 else "low"),
        "per_field": _build_per_field_confidence(
            tier1_mappings + tier2_auto_mappings + tier2_human_decisions
        ),
        "eval_score": overall_confidence,
        "rules_passed": True,
        "rules_violations": [],
    }

    # Build audit info
    audit_info = {
        "prompt_template_id": None,
        "prompt_version": None,
        "passes": 1,
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_read_tokens": 0,
        "cost_usd": 0.0,
        "cost_aed": 0.0,
        "processing_ms": 0,
        "mapping_tier_distribution": {
            "t1": len(tier1_mappings),
            "t2_auto": len(tier2_auto_mappings),
            "t2_human": len(tier2_human_decisions),
            "unresolved": sum(
                1 for t in tier1_mappings + tier2_auto_mappings + tier2_human_decisions
                if t.get("confidence", 0) < 0.70
            ),
        },
        "hierarchy_relationships": len(confirmed_hierarchies),
    }

    # Derive source_type from detected_file_format or fallback to "csv"
    source_type = detected_file_format or "csv"
    final_filename = source_filename or f"{cmms_name}_export.{source_type}"

    # Create schema
    schema = IntermediateSchema(
        ingestion_id=migration_id,
        source_type=source_type,
        agent_id="schema-mapper",
        source_filename=final_filename,
        source_blob_url=source_blob_url,
        extracted_at=datetime.utcnow().isoformat(),
        extraction_method="ai-schema-mapper",
        model_used="claude-haiku-4-5",
        entities=entities,
        confidence=confidence_breakdown,
        audit=audit_info,
    )

    logger.info(f"[Schema Builder] Built IntermediateSchema with {len(entities)} entity types")

    return schema


def _build_per_field_confidence(mappings: List[Dict]) -> Dict[str, str]:
    """
    Build per-field confidence map from all mappings.

    Args:
        mappings: All field mappings (T1, T2 auto, T2 human)

    Returns:
        dict[source_field] = "high" | "medium" | "low"
    """

    per_field = {}

    for mapping in mappings:
        source = mapping.get("source_field")
        confidence = mapping.get("confidence", 0.0)

        if confidence >= 0.85:
            level = "high"
        elif confidence >= 0.70:
            level = "medium"
        else:
            level = "low"

        per_field[source] = level

    return per_field
