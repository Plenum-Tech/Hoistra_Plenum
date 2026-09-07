"""Node 4: Schema Mapping Human Review — HITL gate for field mapping + DDL intent.

Two categories of review:

1. LOW-CONFIDENCE MAPPINGS (T1/T2)
   Actions: accept | reject | override
   These map to EXISTING canonical fields — no DDL required.

2. UNMAPPED FIELDS
   Actions: custom | raw_metadata | skip
   - custom:       user specifies target_table, custom_column_name, data_type
                   → node builds ExtraFieldConfig → Node 8 generates DDL
   - raw_metadata: stored in existing raw_metadata JSONB column — no DDL
   - skip:         discarded — no DDL

Decision format from frontend:
{
  "decisions": [
    // T1/T2 low-confidence
    {"action": "accept",   "source_field": "...", "source_table": "..."},
    {"action": "reject",   "source_field": "...", "source_table": "..."},
    {"action": "override", "source_field": "...", "source_table": "...",
     "target_field": "asset_code", "rationale": "..."},

    // Unmapped → custom column (existing table)
    {"action": "custom",
     "source_field": "vendor_ref", "source_table": "assets_ext",
     "target_table": "assets",
     "custom_column_name": "vendor_asset_ref",
     "data_type": "VARCHAR(100)",
     "nullable": true},

    // Unmapped → custom column (NEW table)
    {"action": "custom",
     "source_field": "work_type", "source_table": "wo_ext",
     "target_table": "preventive_orders",
     "custom_column_name": "work_type",
     "data_type": "VARCHAR(50)",
     "is_new_table": true,
     "new_table_pk": "id",
     "nullable": true},

    // Unmapped → raw_metadata JSONB
    {"action": "raw_metadata", "source_field": "internal_code", "source_table": "assets_ext"},

    // Unmapped → discard
    {"action": "skip", "source_field": "legacy_id", "source_table": "assets_ext"}
  ]
}
"""

import logging
from datetime import datetime
from typing import Optional
from uuid import UUID

from langgraph.types import interrupt

from ..schema_state import SchemaMappingState, ExtraFieldConfig, UnstructuredCandidate

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── DB audit helper ───────────────────────────────────────────────────────────

async def _log_schema_human_decision_to_db(
    db_session,
    schema_mapping_id: UUID,
    source_table: str,
    source_field: str,
    target_field: str,
    confidence: float,
    tier: str,
    rationale: str,
) -> None:
    """Log a human review decision to schema_mapping_field_mappings."""
    try:
        from ...models.migration import SchemaMappingFieldMapping

        mapping = SchemaMappingFieldMapping(
            schema_mapping_id=schema_mapping_id,
            source_field=source_field,
            source_table=source_table,
            target_field=target_field,
            confidence=confidence,
            tier=tier,
            rationale=rationale,
            mapped_at=datetime.utcnow(),
        )
        db_session.add(mapping)
        await db_session.commit()
        logger.debug(f"Logged human decision: {source_table}.{source_field} → {target_field} ({tier})")
    except Exception as e:
        logger.error(f"Failed to log human decision to DB: {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass


# ── Main node ─────────────────────────────────────────────────────────────────

async def schema_human_review_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 4: HITL gate — field mapping approval + DDL intent capture.

    Collects decisions for:
    - Low-confidence T1/T2 mappings  → approve/reject/override (no DDL)
    - Unmapped fields                → custom (DDL) / raw_metadata / skip

    Builds state["extra_fields_config"] for Node 8 DDL execution.
    """

    schema_mapping_id = state.get("schema_mapping_id")
    tier1_mappings = state.get("tier1_mappings", [])
    tier2_auto_mapped = state.get("tier2_auto_mapped", [])
    tier2_unmappable = state.get("tier2_unmappable", [])
    canonical_tables = state.get("canonical_tables", {})
    external_tables = state.get("external_tables", {})
    db_session = state.get("db_session")

    # ── Identify items needing review ────────────────────────────────
    low_conf_tier1 = [m for m in tier1_mappings if m.get("confidence", 0.99) < 0.85]
    low_conf_tier2 = [m for m in tier2_auto_mapped if m.get("confidence", 0.75) < 0.75]
    unmapped_count = len(tier2_unmappable)

    # ── Detect unstructured candidates ───────────────────────────────
    unstructured_candidates = detect_unstructured_candidates(
        external_tables, tier1_mappings, tier2_auto_mapped, tier2_unmappable
    )
    state["unstructured_candidates"] = unstructured_candidates
    if unstructured_candidates:
        logger.info(
            f"[Node 4] Unstructured candidates detected: {len(unstructured_candidates)} "
            f"({sum(1 for c in unstructured_candidates if c['match_status'] == 'matched')} matched-but-text-heavy, "
            f"{sum(1 for c in unstructured_candidates if c['match_status'] == 'unmapped')} unmapped-string)"
        )

    total_flagged = len(low_conf_tier1) + len(low_conf_tier2) + unmapped_count + len(unstructured_candidates)

    logger.info(
        f"[Node 4] HITL Gate: schema_id={schema_mapping_id} | "
        f"T1_low={len(low_conf_tier1)} T2_low={len(low_conf_tier2)} "
        f"unmapped={unmapped_count} unstructured={len(unstructured_candidates)}"
    )

    # Fast-path: nothing needs review
    if total_flagged == 0:
        logger.info("[Node 4] No flagged items; skipping gate")
        state["current_node"] = 4
        state["status"] = "hierarchy"
        # Preserve new-table columns already defined at the pre-semantic gate.
        state["extra_fields_config"] = list(state.get("extra_fields_config") or [])
        return state

    # ── Build review payload ─────────────────────────────────────────

    # Group T1 low-confidence by source table
    tier1_by_table: dict = {}
    for m in low_conf_tier1:
        tbl = m.get("source_table", "unknown")
        tier1_by_table.setdefault(tbl, []).append({
            "source_field": m.get("source_field"),
            "suggested_target": m.get("target_field"),
            "confidence": m.get("confidence"),
            "rationale": m.get("rationale"),
            "tier": "T1",
        })

    # Group T2 low-confidence by source table
    tier2_by_table: dict = {}
    for m in low_conf_tier2:
        tbl = m.get("source_table", "unknown")
        tier2_by_table.setdefault(tbl, []).append({
            "source_field": m.get("source_field"),
            "suggested_target": m.get("target_field"),
            "confidence": m.get("confidence"),
            "rationale": m.get("rationale"),
            "tier": "T2",
        })

    # Build reverse lookup: canonical field name → canonical table name
    # Used to suggest which plenum_cafm table an unmapped source field should go into.
    canonical_field_to_table: dict[str, str] = {}
    for tbl_name, tbl_info in canonical_tables.items():
        for col_info in (tbl_info.get("columns") or [] if isinstance(tbl_info, dict) else []):
            field_name = col_info.get("field_name") if isinstance(col_info, dict) else str(col_info)
            if field_name:
                canonical_field_to_table[field_name] = tbl_name

    import re as _re

    def _norm(s: str) -> str:
        """Normalise: lowercase, strip common CMMS prefixes, remove non-alpha."""
        s = _re.sub(r"^(int|str|txt|flt|fl|bln|lng|dbl|chr|bit|fld|n|s|b|d|f)\s*", "", s, flags=_re.IGNORECASE)
        return _re.sub(r"[^a-z0-9]", "", s.lower())

    # Pre-compute: normalised canonical table name → actual table name (built once, reused per field)
    _canon_norm_map: dict[str, str] = {_norm(t): t for t in canonical_tables if _norm(t)}

    def _suggest_for_field(source_field: str, source_table: str) -> tuple[str | None, bool]:
        """
        Returns (canonical_table_name | None, is_definitely_new_table).

        Rule: suggestion is table-driven only.
        - If the source table name matches a canonical table (exact normalised, then substring)
          → that canonical table is suggested for every unmapped field in this source table.
        - If the source table has no canonical counterpart
          → (None, True): caller must create a new table.
        Field-name matching and sibling-field votes are intentionally not used — they
        produce cross-table suggestions that mislead the user.
        """
        src_tbl_norm = _norm(source_table)

        # Exact normalised match (e.g. "RFQ" → "rfqs", "WorkOrders" → "work_orders")
        if src_tbl_norm in _canon_norm_map:
            return _canon_norm_map[src_tbl_norm], False

        # Substring match (e.g. "PurchaseOrderLines" contains "purchaseorder")
        for canon_norm, canon_name in _canon_norm_map.items():
            if canon_norm and (canon_norm in src_tbl_norm or src_tbl_norm in canon_norm):
                return canon_name, False

        # No canonical table matches this source table — must create a new one
        return None, True

    # Group unmapped fields by source table — include field metadata + AI table suggestion
    unmapped_by_table: dict = {}
    for field_info in tier2_unmappable:
        tbl = field_info.get("source_table", "unknown")
        src_field = field_info.get("field_name") or field_info.get("source_field") or ""
        suggested_tbl, is_new_table = _suggest_for_field(src_field, tbl)
        unmapped_by_table.setdefault(tbl, []).append({
            "source_field": src_field,
            "data_type_hint": field_info.get("data_type"),   # from source schema
            "nullable": field_info.get("nullable", True),
            "description": field_info.get("description"),
            "suggested_canonical_table": suggested_tbl,       # existing canonical table, or None
            "suggest_new_table": is_new_table,                # True when no canonical match found
            "actions_available": ["custom", "raw_metadata", "skip"],
        })

    # Group HIGH-confidence matched fields by source table so the gate can show
    # the COMPLETE column inventory per table — not just the flagged subset.
    # Without this, a table whose columns mostly auto-resolved (e.g. WorkOrderUser:
    # only one low-conf field) would display just that one field and hide the rest.
    # Low-confidence T1/T2 are excluded here (they render in their own flagged
    # section with accept/override/reject controls).
    low_conf_keys = {
        (m.get("source_field"), m.get("source_table"))
        for m in (low_conf_tier1 + low_conf_tier2)
    }
    matched_by_table: dict = {}
    for m in (tier1_mappings + tier2_auto_mapped):
        key = (m.get("source_field"), m.get("source_table"))
        if key in low_conf_keys:
            continue
        tbl = m.get("source_table", "unknown")
        matched_by_table.setdefault(tbl, []).append({
            "source_field": m.get("source_field"),
            "target_field": m.get("target_field"),
            "tier": m.get("tier"),
            "confidence": m.get("confidence"),
        })

    # Group unstructured candidates by source table for UI display
    unstructured_by_table: dict = {}
    for c in unstructured_candidates:
        tbl = c.get("source_table", "unknown")
        unstructured_by_table.setdefault(tbl, []).append({
            "source_field": c["source_field"],
            "match_status": c["match_status"],
            "matched_target": c.get("matched_target"),
            "match_tier": c.get("match_tier"),
            "match_confidence": c.get("match_confidence"),
            "data_type": c.get("data_type"),
            "avg_char_length": c.get("avg_char_length", 0),
            "max_char_length": c.get("max_char_length", 0),
            "sample_values": c.get("sample_values", []),
            "reason": c.get("reason", ""),
            "actions_available": (
                ["keep_mapping", "treat_as_unstructured"]
                if c["match_status"] == "matched"
                else ["treat_as_unstructured", "skip"]
            ),
        })

    review_payload = {
        "schema_mapping_id": schema_mapping_id,
        "total_flagged": total_flagged,
        "low_confidence_tier1": tier1_by_table,
        "low_confidence_tier2": tier2_by_table,
        "matched_fields": matched_by_table,
        "unmapped_fields": unmapped_by_table,
        "unstructured_candidates": unstructured_by_table,
        # Provide existing canonical table names so frontend can build the
        # "target_table" dropdown for custom column placement
        "existing_canonical_tables": sorted(canonical_tables.keys()),
        "instructions": {
            "low_confidence": (
                "For each flagged mapping choose:\n"
                "  accept  — approve the suggested canonical target\n"
                "  reject  — mark as unmapped\n"
                "  override — provide a different canonical target field"
            ),
            "unmapped": (
                "For each unmapped field choose:\n"
                "  custom       — add a new column to a plenum_cafm table\n"
                "                 Requires: target_table, custom_column_name, data_type\n"
                "                 Set is_new_table=true if target_table doesn't exist yet\n"
                "  raw_metadata — store in the table's existing raw_metadata JSONB column\n"
                "  skip         — discard this field"
            ),
            "unstructured": (
                "These columns contain free-text or unstructured data.\n"
                "For matched columns:\n"
                "  keep_mapping        — keep the existing canonical mapping\n"
                "  treat_as_unstructured — store in raw_metadata JSONB instead\n"
                "For unmapped columns:\n"
                "  treat_as_unstructured — store in raw_metadata JSONB\n"
                "  skip                  — discard this field"
            ),
        },
    }

    state["human_review_payload"] = review_payload

    # ── Write gate payload to DB so frontend renders the interactive gate UI ─
    # Uses schema_write_gate_payload_auto (sets status="awaiting_review",
    # pending_gate_type="field_mapping") so SchemaGateFieldMapping renders.
    if schema_mapping_id:
        from .schema_db_writer import schema_write_gate_payload_auto
        await schema_write_gate_payload_auto(
            schema_mapping_id, "field_mapping", review_payload
        )

    logger.info(f"[Node 4] Interrupting for user review")
    from datetime import datetime as _dt
    _gate_started_at = _dt.utcnow()

    # ── Pause for user decisions ─────────────────────────────────────
    # Resumed by /gate/field-mapping endpoint with Command(resume={"decisions": [...]})
    decisions = interrupt(review_payload)

    # Clear the gate payload in DB so frontend stops showing the gate UI
    if schema_mapping_id:
        from .schema_db_writer import schema_clear_gate_payload_auto
        await schema_clear_gate_payload_auto(schema_mapping_id)

    # ── Process decisions ────────────────────────────────────────────
    # The gate endpoint extracts `request["decisions"]` before passing to Command(resume=...),
    # so `decisions` arrives as a list directly. Guard against both forms.
    if isinstance(decisions, list):
        raw_decisions = decisions
    else:
        raw_decisions = decisions.get("decisions", [])
    logger.info(f"[Node 4] Resumed with {len(raw_decisions)} decisions")

    approved_mappings: list = []   # T1/T2 decisions → accepted into mapping config
    # Seed with new-table columns already defined (with SQL types) at the pre-semantic
    # gate so they survive into the DDL plan rather than being rebuilt from scratch.
    extra_fields_config: list[ExtraFieldConfig] = list(state.get("extra_fields_config") or [])
    rejected_count = 0

    # Build lookup of all T1/T2 mappings for quick access
    all_existing_mappings = {
        (m.get("source_field"), m.get("source_table")): m
        for m in (tier1_mappings + tier2_auto_mapped)
    }

    for decision in raw_decisions:
        action = decision.get("action")
        source_field = decision.get("source_field")
        source_table = decision.get("source_table")
        key = (source_field, source_table)
        original = all_existing_mappings.get(key)

        # ── T1 / T2 decisions ────────────────────────────────────────
        if action == "accept":
            target = (
                decision.get("target_field")
                or (original.get("target_field") if original else None)
            )
            if target:
                approved_mappings.append({
                    "source_field": source_field,
                    "source_table": source_table,
                    "target_field": target,
                    "confidence": original.get("confidence", 0.80) if original else 0.80,
                    "tier": "T1_human_approved",
                    "rationale": f"User approved: {original.get('rationale', '') if original else ''}",
                })
                logger.info(f"[Node 4]   ✓ ACCEPTED: {source_table}.{source_field} → {target}")
                if db_session:
                    await _log_schema_human_decision_to_db(
                        db_session, UUID(schema_mapping_id),
                        source_table, source_field, target,
                        original.get("confidence", 0.80) if original else 0.80,
                        "T1_human_approved",
                        f"User approved: {original.get('rationale', '') if original else ''}",
                    )

        elif action == "reject":
            rejected_count += 1
            logger.info(f"[Node 4]   ✗ REJECTED: {source_table}.{source_field}")
            if db_session:
                await _log_schema_human_decision_to_db(
                    db_session, UUID(schema_mapping_id),
                    source_table, source_field, "UNMAPPED",
                    0.0, "unmapped", "User rejected mapping",
                )

        elif action == "override":
            override_target = decision.get("target_field")
            override_rationale = decision.get("rationale", "User override")
            if override_target:
                approved_mappings.append({
                    "source_field": source_field,
                    "source_table": source_table,
                    "target_field": override_target,
                    "confidence": 0.90,
                    "tier": "T1_human_override",
                    "rationale": f"User override: {override_rationale}",
                })
                logger.info(f"[Node 4]   ⚙ OVERRIDE: {source_table}.{source_field} → {override_target}")
                if db_session:
                    await _log_schema_human_decision_to_db(
                        db_session, UUID(schema_mapping_id),
                        source_table, source_field, override_target,
                        0.90, "T1_human_override",
                        f"User override: {override_rationale}",
                    )

        # ── Unmapped field decisions (DDL territory) ─────────────────
        elif action == "custom":
            target_table = decision.get("target_table")
            custom_column_name = decision.get("custom_column_name")
            data_type = decision.get("data_type")

            if not target_table or not custom_column_name or not data_type:
                logger.warning(
                    f"[Node 4]   ⚠ CUSTOM missing required fields for "
                    f"{source_table}.{source_field} — skipping DDL entry. "
                    f"(target_table={target_table}, column={custom_column_name}, type={data_type})"
                )
                continue

            is_new_table = decision.get("is_new_table", False)
            new_table_pk = decision.get("new_table_pk", "id")
            nullable = decision.get("nullable", True)

            extra_fields_config.append({
                "source_field": source_field,
                "source_table": source_table,
                "storage_strategy": "custom",
                "target_table": target_table,
                "custom_column_name": custom_column_name,
                "data_type": data_type,
                "is_new_table": is_new_table,
                "new_table_pk": new_table_pk,
                "nullable": nullable,
                "user_approved": True,
            })
            logger.info(
                f"[Node 4]   ➕ CUSTOM DDL: {source_table}.{source_field} → "
                f"plenum_cafm.{target_table}.{custom_column_name} {data_type}"
                + (" [NEW TABLE]" if is_new_table else "")
            )

        elif action == "raw_metadata":
            extra_fields_config.append({
                "source_field": source_field,
                "source_table": source_table,
                "storage_strategy": "raw_metadata",
                "user_approved": True,
            })
            logger.info(f"[Node 4]   📦 RAW_METADATA: {source_table}.{source_field}")

        elif action == "skip":
            extra_fields_config.append({
                "source_field": source_field,
                "source_table": source_table,
                "storage_strategy": "skip",
                "user_approved": True,
            })
            logger.info(f"[Node 4]   🗑 SKIP: {source_table}.{source_field}")

        # ── Unstructured field decisions ─────────────────────────────
        elif action == "treat_as_unstructured":
            extra_fields_config.append({
                "source_field": source_field,
                "source_table": source_table,
                "storage_strategy": "raw_metadata",
                "user_approved": True,
            })
            logger.info(f"[Node 4]   📄 UNSTRUCTURED→raw_metadata: {source_table}.{source_field}")

            # If it was previously matched, remove it from tier mappings
            # so it doesn't get written to a canonical column
            state["tier1_mappings"] = [
                m for m in state.get("tier1_mappings", [])
                if not (m.get("source_field") == source_field and m.get("source_table") == source_table)
            ]
            state["tier2_auto_mapped"] = [
                m for m in state.get("tier2_auto_mapped", [])
                if not (m.get("source_field") == source_field and m.get("source_table") == source_table)
            ]

        elif action == "keep_mapping":
            # Explicit no-op — user confirmed keeping the existing canonical mapping
            logger.info(f"[Node 4]   ✓ KEEP_MAPPING: {source_table}.{source_field} (unstructured flag dismissed)")

    # ── Update state ─────────────────────────────────────────────────
    # Merge human-approved T1/T2 back into tier1_mappings
    state["tier1_mappings"] = [
        m for m in tier1_mappings
        if (m.get("source_field"), m.get("source_table")) not in
        {(d.get("source_field"), d.get("source_table")) for d in raw_decisions}
    ] + approved_mappings

    # Drop rejected/dropped fields from tier2_auto_mapped too. A rejected field
    # (e.g. an excluded table's high-confidence matched column) must not survive
    # in tier2 — the tier1 rebuild above doesn't cover it.
    _dropped_keys = {
        (d.get("source_field"), d.get("source_table"))
        for d in raw_decisions
        if d.get("action") in ("reject", "skip", "raw_metadata", "treat_as_unstructured")
    }
    if _dropped_keys:
        state["tier2_auto_mapped"] = [
            m for m in state.get("tier2_auto_mapped", [])
            if (m.get("source_field"), m.get("source_table")) not in _dropped_keys
        ]

    state["extra_fields_config"] = extra_fields_config
    state["user_rejected_count"] = rejected_count
    state["current_node"] = 4
    state["status"] = "hierarchy"

    custom_ddl_count = sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom")
    new_table_count = sum(1 for e in extra_fields_config if e.get("is_new_table"))
    logger.info(
        f"[Node 4] Gate complete: {len(approved_mappings)} approved, "
        f"{rejected_count} rejected, {custom_ddl_count} custom DDL "
        f"({new_table_count} new tables)"
    )

    if db_session and schema_mapping_id:
        from .schema_db_writer import schema_update_node_progress
        await schema_update_node_progress(
            db_session, schema_mapping_id, 4,
            progress_pct=55.0,
        )

    if schema_mapping_id:
        from datetime import datetime as _dt
        from .schema_db_writer import schema_append_node_log_auto
        await schema_append_node_log_auto(
            schema_mapping_id, 5, "Gate 2: Field Mapping Review", _gate_started_at, _dt.utcnow(),
            output={"decisions_received": len(raw_decisions), "approved": len(approved_mappings),
                    "rejected": rejected_count,
                    "custom_ddl": sum(1 for e in extra_fields_config if e.get("storage_strategy") == "custom"),
                    "skipped": sum(1 for d in raw_decisions if d.get("action") == "skip")},
            logs=[f"Processed {len(raw_decisions)} user decisions",
                  f"{len(approved_mappings)} mappings approved, {rejected_count} rejected"],
        )

    return state


# ── Unstructured candidate detection ─────────────────────────────────────────

# Columns with avg string length above this are flagged even when matched.
_TEXT_HEAVY_AVG_CHARS = 150

_TEXT_TYPES = {
    "text", "varchar", "character varying", "character", "char",
    "string", "nvarchar", "nchar", "clob", "longtext", "mediumtext",
}


def _is_text_type(data_type: str) -> bool:
    if not data_type:
        return False
    return data_type.lower().split("(")[0].strip() in _TEXT_TYPES


def detect_unstructured_candidates(
    external_tables: dict,
    tier1_mappings: list,
    tier2_auto_mapped: list,
    tier2_unmappable: list,
) -> list[UnstructuredCandidate]:
    """
    Scan all columns and flag ones that look like free-text / unstructured data.

    Scenario A — column IS matched (any tier):
        string type AND avg_char_length > _TEXT_HEAVY_AVG_CHARS
        → user may want to reclassify to raw_metadata

    Scenario B — column is NOT matched (tier2_unmappable):
        string type, any length
        → user must decide: treat_as_unstructured or skip

    Returns a list of UnstructuredCandidate dicts ready for the review payload.
    """
    # Build lookup: (source_field, source_table) → mapping info
    matched_lookup: dict[tuple, dict] = {}
    for m in tier1_mappings + tier2_auto_mapped:
        key = (m.get("source_field"), m.get("source_table"))
        matched_lookup[key] = m

    unmapped_keys: set[tuple] = {
        (f.get("field_name") or f.get("source_field"), f.get("source_table", "unknown"))
        for f in tier2_unmappable
    }

    candidates: list[UnstructuredCandidate] = []

    for table_name, table_info in external_tables.items():
        for col in table_info.get("columns", []):
            field_name = col.get("field_name")
            data_type = col.get("data_type", "")

            if not _is_text_type(data_type):
                continue

            key = (field_name, table_name)
            avg = col.get("avg_char_length") or 0
            max_len = col.get("max_char_length") or 0
            samples = col.get("sample_values") or []
            # Truncate samples for display — show first 200 chars per value
            display_samples = [str(s)[:200] for s in samples[:5]]

            if key in matched_lookup:
                # Scenario A — matched but text-heavy
                if avg > _TEXT_HEAVY_AVG_CHARS:
                    m = matched_lookup[key]
                    candidates.append({
                        "source_field": field_name,
                        "source_table": table_name,
                        "match_status": "matched",
                        "matched_target": m.get("target_field"),
                        "match_tier": m.get("tier"),
                        "match_confidence": m.get("confidence"),
                        "data_type": data_type,
                        "avg_char_length": avg,
                        "max_char_length": max_len,
                        "sample_values": display_samples,
                        "reason": (
                            f"Matched to '{m.get('target_field')}' but average value length "
                            f"is {avg} chars — may contain free-text content."
                        ),
                    })

            elif key in unmapped_keys:
                # Scenario B — unmapped string column
                candidates.append({
                    "source_field": field_name,
                    "source_table": table_name,
                    "match_status": "unmapped",
                    "matched_target": None,
                    "match_tier": None,
                    "match_confidence": None,
                    "data_type": data_type,
                    "avg_char_length": avg,
                    "max_char_length": max_len,
                    "sample_values": display_samples,
                    "reason": (
                        "No canonical mapping found and column is text type — "
                        "may contain unstructured free-text data."
                    ),
                })

    return candidates
