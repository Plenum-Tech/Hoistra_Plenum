"""Node 2: Schema Deterministic Mapper — 4-tier field mapping strategy.

Strategies (in order):
1. Exact field name match (confidence: 0.99)
2. Alias lookup from CMMS_ALIASES (confidence: 0.95–0.98)
3. Regex pattern matching (confidence: 0.90–0.94)
4. Haiku constrained LLM (confidence: 0.85–0.92)
"""

import logging
import re
from datetime import datetime

from anthropic import AsyncAnthropic

from ...matchers import CMMS_ALIASES, PATTERNS, match_field_by_pattern, registry_lookup_learned_only
from ..schema_state import CanonicalFieldMapping, SchemaMappingState
from .mapping_cutoff import is_auto_deterministic

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Canonical field registry from CMMS aliases
CANONICAL_FIELDS = set(set(CMMS_ALIASES.values()))


def _norm_col(s: str) -> str:
    """Normalise a column name for matching: lowercase, strip non-alphanumerics."""
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


async def schema_deterministic_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 2: Deterministically map external schema columns → canonical fields.

    Uses 4-tier strategy:
    1. Exact match
    2. Alias lookup
    3. Regex patterns
    4. Haiku constrained call (if needed)
    """

    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    external_tables = state.get("external_tables", {})

    logger.info(f"[Node 2] Starting deterministic mapping: {len(external_tables)} tables")

    if not external_tables:
        logger.warning("[Node 2] No tables to map")
        state["tier1_mappings"] = []
        state["tier1_mapped_count"] = 0
        state["unmapped_after_t1"] = []
        state["status"] = "semantic"
        return state

    try:
        tier1_mappings: list[CanonicalFieldMapping] = []
        unmapped_after_t1 = []
        total_columns = 0

        from ...connectors.fiix_plenum_mappings import plenum_table_for_fiix_object
        _canonical_tables = state.get("canonical_tables", {})

        # Process each table
        for table_name, table_info in sorted(external_tables.items()):
            columns = table_info.get("columns", [])
            logger.info(f"[Node 2] Processing table {table_name}: {len(columns)} columns")

            # Determine this Fiix object's target CAFM table so its columns can be
            # matched directly against the table's REAL columns (mirrors the migration
            # flow). Covers table-specific columns not in the global canonical set.
            _target_table = plenum_table_for_fiix_object(table_name)
            _target_cols_norm: dict[str, str] = {}
            if _target_table and _target_table in _canonical_tables:
                for c in _canonical_tables[_target_table].get("columns", []):
                    fn = c.get("field_name")
                    if fn:
                        _target_cols_norm[_norm_col(fn)] = fn

            for field_info in columns:
                source_field = field_info.get("field_name")
                data_type = field_info.get("data_type", "string")
                description = field_info.get("description", "")

                total_columns += 1

                # ── Strategy 0: Fiix pre-seeded migration target ───────
                migration_target = field_info.get("migration_target")
                if migration_target and _target_exists_in_canonical(
                    migration_target, table_name, state.get("canonical_tables", {})
                ):
                    mapping = CanonicalFieldMapping(
                        source_field=source_field,
                        source_table=table_name,
                        target_field=migration_target,
                        confidence=0.99,
                        tier="T1_fiix",
                        rationale=f"Fiix→plenum alias ({table_name}.{source_field})",
                        auto_mappable=True,
                        human_review_needed=False,
                    )
                    tier1_mappings.append(mapping)
                    logger.info(
                        f"[Node 2]   {source_field} → {migration_target} (T1_fiix, 0.99)"
                    )
                    continue

                # ── Strategy 0b: Exact column match on the target CAFM table ──
                # Map straight to a REAL column of the Fiix object's plenum_cafm table
                # (e.g. a Fiix sites column → plenum_cafm.sites column). Covers
                # table-specific columns (city, country, floors, …) that the global
                # canonical-field strategies below would miss.
                if _target_cols_norm:
                    _hit = _target_cols_norm.get(_norm_col(source_field))
                    if _hit:
                        mapping = CanonicalFieldMapping(
                            source_field=source_field,
                            source_table=table_name,
                            target_field=_hit,
                            confidence=0.98,
                            tier="T1_table_exact",
                            rationale=f"Exact column match on target table '{_target_table}'",
                            auto_mappable=True,
                            human_review_needed=False,
                        )
                        tier1_mappings.append(mapping)
                        logger.info(f"[Node 2]   {source_field} → {_hit} (T1_table_exact, 0.98)")
                        continue

                # ── Strategy 1: Exact match ────────────────────────────
                if source_field in CANONICAL_FIELDS:
                    mapping = CanonicalFieldMapping(
                        source_field=source_field,
                        source_table=table_name,
                        target_field=source_field,
                        confidence=0.99,
                        tier="T1_exact",
                        rationale="Exact field name match",
                        auto_mappable=True,
                        human_review_needed=False,
                    )
                    tier1_mappings.append(mapping)
                    logger.info(f"[Node 2]   {source_field} → {source_field} (T1_exact, 0.99)")
                    continue

                # ── Strategy 2: Alias lookup ───────────────────────────
                alias_match = _match_alias(source_field)
                if alias_match:
                    target, confidence = alias_match
                    mapping = CanonicalFieldMapping(
                        source_field=source_field,
                        source_table=table_name,
                        target_field=target,
                        confidence=confidence,
                        tier="T1_alias",
                        rationale=f"CMMS alias lookup",
                        auto_mappable=True,
                        human_review_needed=False,
                    )
                    tier1_mappings.append(mapping)
                    logger.info(f"[Node 2]   {source_field} → {target} (T1_alias, {confidence:.2f})")
                    continue

                # ── Strategy 3: Regex patterns ─────────────────────────
                pattern_match = _match_pattern(source_field)
                if pattern_match:
                    target, confidence = pattern_match
                    mapping = CanonicalFieldMapping(
                        source_field=source_field,
                        source_table=table_name,
                        target_field=target,
                        confidence=confidence,
                        tier="T1_regex",
                        rationale=f"Regex pattern match",
                        auto_mappable=True if confidence >= 0.85 else False,
                        human_review_needed=confidence < 0.85,
                    )
                    tier1_mappings.append(mapping)
                    logger.info(f"[Node 2]   {source_field} → {target} (T1_regex, {confidence:.2f})")
                    continue

                # ── Strategy R: Registry lookup (learned semantic matches) ──
                registry_hit = registry_lookup_learned_only(source_field)
                if registry_hit:
                    target, conf, _tier = registry_hit
                    if target in CANONICAL_FIELDS:
                        mapping = CanonicalFieldMapping(
                            source_field=source_field,
                            source_table=table_name,
                            target_field=target,
                            confidence=conf,
                            tier="T1_registry",
                            rationale="Registry hit — previously approved semantic match (deterministic)",
                            auto_mappable=True,
                            human_review_needed=False,
                        )
                        tier1_mappings.append(mapping)
                        logger.info(
                            f"[Node 2]   {source_field} → {target} (T1_registry, {conf:.2f}) "
                            f"[saved LLM call]"
                        )
                        continue

                # ── Strategy 4: Unresolved (pass to semantic mapper) ───
                # Tag with source_table so Node 3/4 can group by table
                field_info_tagged = dict(field_info)
                field_info_tagged["source_table"] = table_name
                unmapped_after_t1.append(field_info_tagged)
                logger.info(f"[Node 2]   {source_field}: unresolved → T2")

        # F5-5: enforce the SAME explicit deterministic cutoff as the migration flow — a
        # Tier-1 match auto-approves only at >= 95% confidence; below it, the mapping is
        # flagged for human/semantic review (the schema flow's pre-semantic gate routes on
        # auto_mappable / human_review_needed).
        for _m in tier1_mappings:
            _auto = is_auto_deterministic(_m.get("confidence", 0.0))
            _m["auto_mappable"] = _auto
            _m["human_review_needed"] = not _auto

        # ── Summary ────────────────────────────────────────────────────
        tier1_count = len(tier1_mappings)
        unmapped_count = len(unmapped_after_t1)

        logger.info(
            f"[Node 2] ✓ Deterministic mapping complete: "
            f"{tier1_count}/{total_columns} mapped, {unmapped_count} unresolved"
        )

        # ── Update state ───────────────────────────────────────────────
        state["tier1_mappings"] = tier1_mappings
        state["tier1_mapped_count"] = tier1_count
        state["unmapped_after_t1"] = unmapped_after_t1
        state["status"] = "semantic"
        state["notes"] = state.get("notes", []) + [
            f"Tier 1 deterministic: {tier1_count} exact/alias/regex matches, {unmapped_count} unresolved"
        ]

        schema_mapping_id = state.get("schema_mapping_id")
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            # Build mappings_by_table: {table_name: [{source_field, target_field, confidence, tier}]}
            mappings_by_table: dict = {}
            for m in tier1_mappings:
                tbl = m.get("source_table") or "unknown"
                if tbl not in mappings_by_table:
                    mappings_by_table[tbl] = []
                mappings_by_table[tbl].append({
                    "source_field": m.get("source_field"),
                    "target_field": m.get("target_field"),
                    "confidence": round(m.get("confidence", 0.0), 3),
                    "tier": m.get("tier"),
                })
            # Build unresolved_by_table: {table_name: [field_name, ...]}
            unresolved_by_table: dict = {}
            for f in unmapped_after_t1:
                # Look up which table this field belongs to
                tbl = "unknown"
                for tn, ti in external_tables.items():
                    if any(c.get("field_name") == f.get("field_name") for c in ti.get("columns", [])):
                        tbl = tn
                        break
                if tbl not in unresolved_by_table:
                    unresolved_by_table[tbl] = []
                unresolved_by_table[tbl].append(f.get("field_name"))
            payload = {
                "node": 2,
                "title": "Deterministic Mapping Complete",
                "total_columns": total_columns,
                "t1_mapped": tier1_count,
                "unresolved": unmapped_count,
                "mappings_by_table": mappings_by_table,
                "unresolved_by_table": unresolved_by_table,
            }
            await schema_write_step_pause_auto(
                schema_mapping_id, 2, "step_2_deterministic", payload
            )
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 2, "Deterministic Mapping", _node_started_at, datetime.utcnow(),
                output={"total_columns": total_columns, "tier1_mapped": tier1_count,
                        "unresolved": unmapped_count,
                        "coverage_pct": round(tier1_count / total_columns * 100, 1) if total_columns else 0},
                logs=[f"Ran 4-tier deterministic matching (exact → alias → regex → Haiku)",
                      f"{tier1_count}/{total_columns} fields matched at Tier 1",
                      f"{unmapped_count} fields unresolved → passed to semantic mapping"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 2] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Deterministic mapping failed: {str(e)}"
        return state


# ────────────────────────────────────────────────────────────────────────────
# Matching Strategies
# ────────────────────────────────────────────────────────────────────────────


def _target_exists_in_canonical(
    target: str,
    fiix_object_name: str,
    canonical_tables: dict,
) -> bool:
    """Confirm migration target is a real plenum_cafm column (preferred: mapped table)."""
    if not canonical_tables:
        return True

    from ...connectors.fiix_plenum_mappings import plenum_table_for_fiix_object

    preferred_table = plenum_table_for_fiix_object(fiix_object_name)
    if preferred_table and preferred_table in canonical_tables:
        cols = {
            c.get("field_name")
            for c in canonical_tables[preferred_table].get("columns", [])
        }
        if target in cols:
            return True

    for table_info in canonical_tables.values():
        cols = {c.get("field_name") for c in table_info.get("columns", [])}
        if target in cols:
            return True

    return False


def _match_alias(field_name: str) -> tuple[str, float] | None:
    """
    Strategy 2: Match field name in CMMS_ALIASES.

    Returns: (canonical_field, confidence) or None
    """
    normalized = field_name.lower().strip()

    # Direct match
    if normalized in CMMS_ALIASES:
        canonical = CMMS_ALIASES[normalized]
        return (canonical, 0.97)

    # Try without underscores/dashes
    normalized_compact = normalized.replace("_", "").replace("-", "")
    for alias_key, canonical in CMMS_ALIASES.items():
        if alias_key.replace("_", "").replace("-", "") == normalized_compact:
            return (canonical, 0.95)

    return None


def _match_pattern(field_name: str) -> tuple[str, float] | None:
    """
    Strategy 3: Match field name against regex patterns.

    Returns: (canonical_field, confidence) or None
    """
    field_lower = field_name.lower()

    # Common patterns (abbreviated from PATTERNS dict in regex_patterns.py)
    patterns = {
        r"^asset": "asset_code",
        r"assetcode|assetnum|equipcode|equipment": "asset_code",
        r"^asset.*name": "asset_name",
        r"workorder|wo_": "wo_code",
        r"priority": "wo_priority",
        r"status": "wo_status",
        r"^pm_": "sm_code",
        r"schedule|interval": "schedule_interval",
        r"part": "part_code",
        r"stock|inventory": "stock_on_hand",
        r"user|person|technician": "user_full_name",
    }

    for pattern, canonical in patterns.items():
        if re.search(pattern, field_lower):
            # Confidence based on specificity
            if pattern.startswith("^"):
                confidence = 0.93  # Prefix match (more specific)
            else:
                confidence = 0.90  # Substring match
            return (canonical, confidence)

    return None


async def _match_llm_constrained(
    client: AsyncAnthropic,
    source_field: str,
    source_table: str,
    data_type: str,
    description: str,
) -> tuple[str, float] | None:
    """
    Strategy 4: Use Haiku to suggest canonical field (constrained).

    Only called if strategies 1-3 fail and we need LLM help.
    Haiku is constrained to choose from CANONICAL_FIELDS only.

    Returns: (canonical_field, confidence) or None
    """
    canonical_fields_str = ", ".join(sorted(CANONICAL_FIELDS))

    prompt = f"""Map this external database column to a canonical field from our schema.

External Column:
- Name: {source_field}
- Type: {data_type}
- Table: {source_table}
- Description: {description}

MUST choose from these canonical fields ONLY:
{canonical_fields_str}

If no good match, respond with "UNMAPPABLE".

Respond with ONLY the field name or "UNMAPPABLE", no explanation."""

    try:
        from ...matchers.fm_ontology import fm_skill_preamble

        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=50,
            # FM Skill Context: reason about the column in FM terms (COBie/BRICK/SFG20)
            # rather than as generic text.
            system=fm_skill_preamble(),
            messages=[{"role": "user", "content": prompt}],
        )

        result = response.content[0].text.strip().upper()

        if result == "UNMAPPABLE":
            return None

        # Verify it's actually in canonical fields
        if result in [f.upper() for f in CANONICAL_FIELDS]:
            # Find actual casing
            canonical = [f for f in CANONICAL_FIELDS if f.upper() == result][0]
            confidence = 0.88  # LLM match
            logger.info(f"[Node 2] LLM match: {source_field} → {canonical} ({confidence:.2f})")
            return (canonical, confidence)

        return None

    except Exception as e:
        logger.warning(f"[Node 2] LLM constrained match failed: {e}")
        return None
