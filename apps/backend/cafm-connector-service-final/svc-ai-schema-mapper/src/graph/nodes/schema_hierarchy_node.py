"""Node 5: Schema FK & Hierarchy Detection.

Combines column-name analysis with canonical plenum_cafm schema cross-reference.

Column-name detection strategies (in order of confidence):

  1. Explicit FK constraints in schema metadata          → confidence 0.99
  2. Hungarian int{Table}ID  /  int{Table}Code           → confidence 0.92
  3. Snake-case  {table}_id  /  {table}_code             → confidence 0.88
  4. CamelCase   {Table}Id   /  {Table}Code              → confidence 0.85
  5. Suffix-only _id / _code (no table-name prefix)
     resolved by matching to any table PK column         → confidence 0.70
  6. Self-referential: parent_id / {table}ParentID
     or any column whose name == own-table + ID          → confidence 0.90
  7. Shared FK columns: a column with the same name
     appearing in 3+ tables → its target is likely
     a high-level "parent" entity                        → confidence 0.75

Canonical schema cross-reference (new in this node):

  After column-name detection, every FK target is resolved against the
  canonical plenum_cafm schema to:

  A. Annotate each FK with its canonical_target_table (e.g. "assets"),
     so the UI and downstream nodes know which canonical entity it maps to.

  B. Boost confidence (+0.05) when the source FK entity name matches a
     canonical table name — i.e. the relationship is validated by the
     target DB schema, not just the source column name.

  C. Infer FKs that column-name analysis missed: for every T1/T2 mapped
     field whose canonical counterpart is a FK column in plenum_cafm,
     synthesise the corresponding source FK relationship at confidence 0.80.

Hierarchy tree construction:
  - Direction: tables with MORE incoming FK edges = parents (roots)
  - Tables referenced by many others → top of the hierarchy
  - Tables that reference many others but are rarely referenced → leaves
  - Multiple disconnected sub-trees are represented under a virtual root
  - Self-referential tables (e.g. Asset with intAssetParentID) are
    marked with a "self_referential" flag but still placed in the tree
"""

import logging
import re
from collections import defaultdict
from datetime import datetime
from typing import Optional

from ..schema_state import (
    ForeignKeyDetection,
    HierarchyNode,
    HorizontalRelationship,
    JunctionTable,
    SchemaMappingState,
    SchemaTableInfo,
)

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


# ── Hungarian-notation FK prefixes (Fiix CMMS) ───────────────────────────────
_HUNGARIAN_PREFIXES = ("int", "str", "dbl", "dtm", "bol", "qty", "flt", "lng", "cur", "obj")

# Suffixes that strongly suggest a FK column
_FK_SUFFIXES = ("id", "code", "key", "num", "no")

# Words that appear in "parent" column names (self-referential)
_PARENT_WORDS = ("parent", "parentof", "ownerof", "superior", "ancestor", "master")


# ── Main node ─────────────────────────────────────────────────────────────────


async def schema_hierarchy_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 5: Detect FK relationships and build hierarchy tree.

    Uses column-name analysis on the source CMMS schema, then cross-references
    all detected FKs against the canonical plenum_cafm schema (from Node 0) to
    annotate, boost confidence, and infer relationships the column-name pass missed.
    """

    _node_started_at = datetime.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    external_tables = state.get("external_tables", {})
    canonical_tables = state.get("canonical_tables", {})
    tier1_mappings = state.get("tier1_mappings", [])
    tier2_auto_mapped = state.get("tier2_auto_mapped", [])

    logger.info(
        f"[Node 5] Starting FK & hierarchy detection: "
        f"{len(external_tables)} source tables, {len(canonical_tables)} canonical tables"
    )

    if not external_tables:
        logger.warning("[Node 5] No tables to analyse")
        state["detected_foreign_keys"] = []
        state["detected_hierarchies"] = []
        state["junction_tables"] = []
        state["horizontal_relationships"] = []
        state["isolated_tables"] = []
        state["hierarchy_cycles"] = []
        state["implicit_hierarchies"] = {}
        state["status"] = "verify_hierarchy"
        return state

    try:
        # ── Step 1: Build normalised table indexes ────────────────────
        table_index = _build_table_index(external_tables)
        canonical_table_index = _build_table_index(canonical_tables) if canonical_tables else {}
        canonical_fk_index = _build_canonical_fk_index(canonical_tables)
        logger.info(
            f"[Node 5] Canonical FK index: {len(canonical_fk_index)} known FK relationships"
        )

        # ── Step 2: Detect all FK candidates (column-name analysis) ──
        detected_fks = _detect_foreign_keys(external_tables, table_index)
        logger.info(f"[Node 5] Column-name FKs: {len(detected_fks)}")

        # ── Step 3: Detect self-referential relationships ─────────────
        self_refs = _detect_self_referential(external_tables)
        detected_fks.extend(self_refs)
        if self_refs:
            logger.info(f"[Node 5] Self-referential: {len(self_refs)}")

        # ── Step 4: Canonical cross-reference ─────────────────────────
        # Annotate each FK with its canonical target, boost confidence when aligned.
        if canonical_table_index:
            detected_fks = _enrich_with_canonical(detected_fks, canonical_table_index)
            canonical_backed_count = sum(1 for fk in detected_fks if fk.get("canonical_backed"))
            logger.info(f"[Node 5] Canonical-backed FKs: {canonical_backed_count}")

        # ── Step 5: Mapping-backed FK inference ──────────────────────
        # Synthesise FKs that column-name analysis missed, using T1/T2 mappings
        # + canonical FK relationships as the source of truth.
        if canonical_fk_index and (tier1_mappings or tier2_auto_mapped):
            inferred_fks = _infer_fks_from_mappings(
                tier1_mappings, tier2_auto_mapped,
                canonical_fk_index, external_tables, canonical_tables,
            )
            if inferred_fks:
                logger.info(f"[Node 5] Mapping-inferred FKs: {len(inferred_fks)}")
                detected_fks.extend(inferred_fks)

        # ── Step 6: Deduplicate FKs ───────────────────────────────────
        detected_fks = _deduplicate_fks(detected_fks)

        # ── Step 7: Build directed relationship graph ─────────────────
        relationship_graph = _build_relationship_graph(detected_fks, external_tables)

        # ── Step 8: Detect cycles ─────────────────────────────────────
        cycles = _detect_cycles(relationship_graph)
        if cycles:
            logger.warning(f"[Node 5] ⚠ {len(cycles)} cycle(s) in FK graph")

        # ── Step 9: Build hierarchy forest (all roots) ───────────────
        hierarchy_forest, isolated_tables = _build_hierarchy_forest(
            relationship_graph, external_tables, detected_fks
        )

        # ── Step 10: Detect junction / bridge tables ──────────────────
        junction_tables = _detect_junction_tables(external_tables, detected_fks)
        junction_table_names = {jt.get("table_name") for jt in junction_tables}
        logger.info(f"[Node 5] Junction tables: {sorted(junction_table_names)}")

        # ── Step 11: Detect horizontal / peer relationships ───────────
        horizontal_relationships = _detect_horizontal_relationships(
            external_tables, detected_fks, hierarchy_forest, junction_tables
        )
        logger.info(f"[Node 5] Horizontal relationships: {len(horizontal_relationships)}")

        # ── Step 12: Implicit / SAP-style code hierarchies ───────────
        implicit_hierarchies = _detect_implicit_hierarchies(external_tables)

        # ── Step 13: Summary ──────────────────────────────────────────
        max_depth = max((_calculate_hierarchy_depth(r) for r in hierarchy_forest), default=0)
        canonical_backed_count = sum(1 for fk in detected_fks if fk.get("canonical_backed"))
        logger.info(
            f"[Node 5] ✓ Complete: {len(detected_fks)} FKs "
            f"({canonical_backed_count} canonical-backed), "
            f"{len(hierarchy_forest)} hierarchies (max depth {max_depth}), "
            f"{len(junction_tables)} junction tables, "
            f"{len(horizontal_relationships)} horizontal, "
            f"{len(isolated_tables)} isolated, "
            f"{len(cycles)} cycle(s)"
        )

        state["detected_foreign_keys"] = detected_fks
        state["detected_hierarchies"] = hierarchy_forest
        state["junction_tables"] = junction_tables
        state["horizontal_relationships"] = horizontal_relationships
        state["isolated_tables"] = isolated_tables
        state["hierarchy_cycles"] = cycles
        state["implicit_hierarchies"] = implicit_hierarchies
        state["status"] = "verify_hierarchy"
        state["notes"] = state.get("notes", []) + [
            f"Detected {len(detected_fks)} FKs "
            f"({canonical_backed_count} canonical-backed): "
            f"{len(hierarchy_forest)} hierarchy trees (max depth {max_depth}), "
            f"{len(junction_tables)} junction tables, "
            f"{len(horizontal_relationships)} horizontal relationships, "
            f"{len(isolated_tables)} isolated tables"
        ]

        if schema_mapping_id:
            try:
                from .schema_db_writer import schema_write_step_pause_auto
                fk_preview = [
                    {
                        "source": f"{fk.get('source_table')}.{fk.get('source_column')}",
                        "target": f"{fk.get('target_table')}.{fk.get('target_column')}",
                        "canonical_target": fk.get("canonical_target_table"),
                        "canonical_backed": fk.get("canonical_backed", False),
                        "type": fk.get("relationship_type"),
                        "confidence": round(fk.get("confidence", 0.0), 3),
                        "reasoning": fk.get("reasoning", ""),
                    }
                    for fk in detected_fks
                ]
                payload = {
                    "node": 5,
                    "title": "Hierarchy Detection Complete",
                    "total_fks": len(detected_fks),
                    "canonical_backed_fks": canonical_backed_count,
                    "hierarchy_count": len(hierarchy_forest),
                    "hierarchy_roots": [r.get("table_name") for r in hierarchy_forest],
                    "max_hierarchy_depth": max_depth,
                    "junction_table_count": len(junction_tables),
                    "junction_tables": [jt.get("table_name") for jt in junction_tables],
                    "horizontal_relationship_count": len(horizontal_relationships),
                    "isolated_table_count": len(isolated_tables),
                    "isolated_tables": isolated_tables,
                    "implicit_hierarchies_count": len(implicit_hierarchies),
                    "cycles_count": len(cycles),
                    "cycles": [" → ".join(c) for c in cycles[:5]],
                    "fk_preview": fk_preview,
                    "implicit_hierarchy_names": list(implicit_hierarchies.keys())[:10],
                }
                await schema_write_step_pause_auto(
                    schema_mapping_id, 5, "step_5_hierarchy", payload
                )
                from .schema_db_writer import schema_append_node_log_auto
                await schema_append_node_log_auto(
                    schema_mapping_id, 6, "Hierarchy Detection", _node_started_at, datetime.utcnow(),
                    output={"total_fks": len(detected_fks), "canonical_backed_fks": canonical_backed_count,
                            "hierarchy_count": len(hierarchy_forest), "junction_table_count": len(junction_tables),
                            "horizontal_relationship_count": len(horizontal_relationships),
                            "isolated_table_count": len(isolated_tables), "cycles_count": len(cycles),
                            "max_hierarchy_depth": max_depth},
                    logs=[f"Detected {len(detected_fks)} FK relationships ({canonical_backed_count} canonical-backed)",
                          f"Built {len(hierarchy_forest)} hierarchy trees (max depth {max_depth})",
                          f"{len(junction_tables)} junction tables detected",
                          f"{len(horizontal_relationships)} horizontal relationships detected",
                          f"{len(isolated_tables)} isolated tables, {len(cycles)} cycle(s)"],
                )
            except Exception as _exc:
                logger.warning(f"[Node 5] DB step write failed (non-fatal): {_exc}")

        return state

    except Exception as e:
        logger.exception(f"[Node 5] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"FK & hierarchy detection failed: {str(e)}"
        return state


# ── Index helpers ─────────────────────────────────────────────────────────────


def _build_table_index(external_tables: dict) -> dict[str, str]:
    """
    Build a case-insensitive, normalised map: normalised_name → original_table_name.

    Normalisation: lowercase, strip underscores/hyphens, strip trailing 's' for
    naive singular-form matching (e.g. "assets" → matches column "assetid").
    """
    index: dict[str, str] = {}
    for table_name in external_tables.keys():
        norm = _normalise_name(table_name)
        index[norm] = table_name
        # Singular form (strip trailing 's' or 'es')
        if norm.endswith("ies"):
            index[norm[:-3] + "y"] = table_name
        elif norm.endswith("ses") or norm.endswith("xes") or norm.endswith("zes"):
            index[norm[:-2]] = table_name
        elif norm.endswith("s") and len(norm) > 3:
            index[norm[:-1]] = table_name
    return index


def _normalise_name(name: str) -> str:
    """Lowercase, remove underscores, hyphens, and spaces."""
    return re.sub(r"[\s_\-]+", "", name).lower()


def _strip_hungarian(col_name: str) -> str:
    """Strip Hungarian prefix from a column name if present."""
    for prefix in _HUNGARIAN_PREFIXES:
        if col_name.startswith(prefix) and len(col_name) > len(prefix) + 2:
            remainder = col_name[len(prefix):]
            if remainder[0].isupper() or remainder[0].isdigit():
                return remainder
    return col_name


def _extract_fk_entity(col_name: str) -> Optional[str]:
    """
    Extract the entity name from a column name that looks like a FK.

    Handles:
      intSiteID         → "site"
      int_site_id       → "site"
      site_id / siteId  → "site"
      SITE_CODE         → "site"
      workorderId       → "workorder"
      WorkOrderID       → "workorder"
      parent_id         → None  (self-referential, handled separately)
    """
    # 1. Strip Hungarian prefix
    stripped = _strip_hungarian(col_name)

    # 2. Normalise to lowercase without separators
    norm = _normalise_name(stripped)

    # 3. Check it ends with a FK suffix
    matched_suffix: Optional[str] = None
    for suffix in _FK_SUFFIXES:
        if norm.endswith(suffix) and len(norm) > len(suffix):
            matched_suffix = suffix
            entity_part = norm[: -len(suffix)]
            # Skip pure-suffix columns with no entity part (e.g. just "id")
            if not entity_part or entity_part in _PARENT_WORDS:
                return None
            return entity_part

    return None


def _get_table_primary_key(table_info: dict) -> str:
    """Return the primary key column name for a table (default 'id')."""
    pk = table_info.get("primary_key")
    if pk:
        return pk
    # Fallback: look for a column flagged as PK
    for col in table_info.get("columns", []):
        if col.get("is_primary_key"):
            return col.get("field_name", "id")
    return "id"


# ── Canonical schema helpers ──────────────────────────────────────────────────


def _build_canonical_fk_index(canonical_tables: dict) -> dict[tuple[str, str], tuple[str, str]]:
    """
    Build an index of all FK relationships declared in the canonical schema.

    Returns: {(table_name, column_name): (target_table, target_column)}

    Also infers FK columns by name convention within canonical tables
    (e.g. asset_id → assets.id, location_id → locations.id).
    """
    index: dict[tuple[str, str], tuple[str, str]] = {}
    canonical_norm_index = {_normalise_name(t): t for t in canonical_tables}

    for table_name, table_info in canonical_tables.items():
        for col in table_info.get("columns", []):
            col_name = col.get("field_name", "")
            if not col_name:
                continue

            # Explicit FK metadata
            if col.get("is_foreign_key"):
                target_table = col.get("fk_target_table")
                target_col = col.get("fk_target_column", "id")
                if target_table:
                    index[(table_name, col_name)] = (target_table, target_col)
                continue

            # Infer by snake_case _id / _code suffix within canonical tables
            entity = _extract_fk_entity(col_name)
            if not entity:
                continue
            target = canonical_norm_index.get(entity) or canonical_norm_index.get(entity + "s")
            if target and target != table_name:
                target_pk = _get_table_primary_key(canonical_tables[target])
                index[(table_name, col_name)] = (target, target_pk)

    return index


def _enrich_with_canonical(
    fks: list[ForeignKeyDetection],
    canonical_table_index: dict[str, str],
) -> list[ForeignKeyDetection]:
    """
    Cross-reference each detected FK's target table against the canonical schema.

    For each FK whose target entity name matches a canonical table:
      - Set canonical_target_table and canonical_target_column
      - Set canonical_backed = True
      - Boost confidence by +0.05 (capped at 0.99)

    Also handles the case where the source FK entity name directly resolves
    to a canonical table even though no source table was matched — in that case
    we record the canonical alignment without changing the target_table pointer
    (which stays as the source CMMS table).
    """
    enriched: list[ForeignKeyDetection] = []
    for fk in fks:
        target_table = fk.get("target_table", "")
        entity = _normalise_name(target_table)

        canonical_match = canonical_table_index.get(entity)
        if not canonical_match and entity.endswith("s"):
            canonical_match = canonical_table_index.get(entity[:-1])
        if not canonical_match:
            # Try singular forms (strip -es, -ies)
            if entity.endswith("ies"):
                canonical_match = canonical_table_index.get(entity[:-3] + "y")
            elif entity.endswith("ses") or entity.endswith("xes"):
                canonical_match = canonical_table_index.get(entity[:-2])

        if canonical_match:
            fk = dict(fk)  # type: ignore[assignment]  # copy before mutating
            fk["canonical_target_table"] = canonical_match
            fk["canonical_target_column"] = "id"
            fk["canonical_backed"] = True
            fk["confidence"] = min(0.99, fk.get("confidence", 0.0) + 0.05)
            logger.debug(
                f"[Node 5] Canonical-backed: "
                f"{fk.get('source_table')}.{fk.get('source_column')} → "
                f"{target_table} (canonical: {canonical_match})"
            )

        enriched.append(fk)
    return enriched


def _infer_fks_from_mappings(
    tier1_mappings: list,
    tier2_auto_mapped: list,
    canonical_fk_index: dict[tuple[str, str], tuple[str, str]],
    external_tables: dict,
    canonical_tables: dict,
) -> list[ForeignKeyDetection]:
    """
    Synthesise FK relationships that column-name analysis missed.

    Logic:
      For each T1/T2 mapped field:
        source: WorkOrders.intAssetID  →  canonical: work_orders.asset_id
        canonical_fk_index says: (work_orders, asset_id) → (assets, id)

      We know the source field maps to a canonical FK column.
      Now find which source table maps to the canonical FK target (assets).
      If found → emit a new ForeignKeyDetection at confidence 0.80.
    """
    inferred: list[ForeignKeyDetection] = []

    # Build reverse map: canonical_table_name → source table name(s)
    # from existing T1/T2 mappings where target_field looks like "<table>.<col>"
    canonical_to_source_table: dict[str, str] = {}
    for mapping in list(tier1_mappings) + list(tier2_auto_mapped):
        target_field = mapping.get("target_field", "")
        source_table = mapping.get("source_table", "")
        if "." in target_field:
            canonical_tbl = target_field.split(".")[0]
            if canonical_tbl and source_table:
                canonical_to_source_table.setdefault(canonical_tbl, source_table)

    # Also build a simple canonical table name → source table name index
    # by matching normalised names between external_tables and canonical_tables
    canonical_norm_to_source: dict[str, str] = {}
    source_table_index = _build_table_index(external_tables)
    for canonical_name in canonical_tables:
        norm = _normalise_name(canonical_name)
        source_match = source_table_index.get(norm)
        if not source_match and norm.endswith("s"):
            source_match = source_table_index.get(norm[:-1])
        if source_match:
            canonical_norm_to_source[canonical_name] = source_match

    all_mappings = list(tier1_mappings) + list(tier2_auto_mapped)
    seen: set[tuple[str, str, str]] = set()

    for mapping in all_mappings:
        source_field = mapping.get("source_field", "")
        source_table = mapping.get("source_table", "")
        target_field = mapping.get("target_field", "")

        if not source_field or not source_table or not target_field:
            continue

        # Parse canonical table and column from target_field (e.g. "work_orders.asset_id")
        if "." in target_field:
            canonical_tbl, canonical_col = target_field.split(".", 1)
        else:
            # target_field is just a column name — we can't resolve the table
            continue

        # Check if this canonical column is a FK
        fk_target = canonical_fk_index.get((canonical_tbl, canonical_col))
        if not fk_target:
            continue

        canonical_fk_target_table, canonical_fk_target_col = fk_target

        # Find the source table that maps to the canonical FK target
        source_target_table = (
            canonical_to_source_table.get(canonical_fk_target_table)
            or canonical_norm_to_source.get(canonical_fk_target_table)
        )

        if not source_target_table:
            continue
        if source_target_table == source_table:
            continue  # self-referential — handled by _detect_self_referential

        dedup_key = (source_table, source_field, source_target_table)
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        target_table_info = external_tables.get(source_target_table, {})
        target_pk = _get_table_primary_key(target_table_info) if target_table_info else "id"

        inferred.append(ForeignKeyDetection(
            source_table=source_table,
            source_column=source_field,
            target_table=source_target_table,
            target_column=target_pk,
            relationship_type="REFERENCE",
            confidence=0.80,
            reasoning=(
                f"Inferred from field mapping: {source_field} → {target_field} "
                f"(canonical FK: {canonical_tbl}.{canonical_col} → "
                f"{canonical_fk_target_table}.{canonical_fk_target_col})"
            ),
            user_confirmed=False,
            canonical_target_table=canonical_fk_target_table,
            canonical_target_column=canonical_fk_target_col,
            canonical_backed=True,
        ))
        logger.debug(
            f"[Node 5] Mapping-inferred FK: "
            f"{source_table}.{source_field} → {source_target_table}.{target_pk} "
            f"(via canonical {canonical_tbl}.{canonical_col})"
        )

    return inferred


# ── FK detection ──────────────────────────────────────────────────────────────


def _detect_foreign_keys(
    external_tables: dict,
    table_index: dict[str, str],
) -> list[ForeignKeyDetection]:
    """
    Scan every column of every table and attempt to resolve it to a target table
    using only the column name.
    """
    fks: list[ForeignKeyDetection] = []

    # Track how many times each entity name appears across all tables
    # (used to boost confidence on shared FK columns)
    entity_occurrence: dict[str, int] = defaultdict(int)

    # First pass: collect occurrence counts for shared-FK boost
    for table_name, table_info in external_tables.items():
        for col in table_info.get("columns", []):
            col_name = col.get("field_name", "")
            entity = _extract_fk_entity(col_name)
            if entity:
                entity_occurrence[entity] += 1

    # Second pass: build FK records
    for table_name, table_info in external_tables.items():
        columns = table_info.get("columns", [])

        for col in columns:
            col_name = col.get("field_name", "")
            if not col_name:
                continue

            # Strategy 1: explicit FK in schema metadata
            if col.get("is_foreign_key"):
                target_table = col.get("fk_target_table")
                target_col = col.get("fk_target_column", "id")
                if target_table:
                    fks.append(ForeignKeyDetection(
                        source_table=table_name,
                        source_column=col_name,
                        target_table=target_table,
                        target_column=target_col,
                        relationship_type="REFERENCE",
                        confidence=0.99,
                        reasoning="Explicit FK constraint in schema metadata",
                        user_confirmed=False,
                    ))
                    logger.debug(
                        f"[Node 5] Explicit FK: {table_name}.{col_name} → {target_table}.{target_col}"
                    )
                continue

            # Skip self-referential patterns (handled separately)
            norm_col = _normalise_name(_strip_hungarian(col_name))
            if any(p in norm_col for p in _PARENT_WORDS):
                continue

            # Strategies 2-5: infer from column name
            entity = _extract_fk_entity(col_name)
            if not entity:
                continue

            # Try to resolve entity → table
            target_table_name = table_index.get(entity)
            if not target_table_name:
                # Try without trailing vowel for irregular plurals
                target_table_name = table_index.get(entity.rstrip("aeiou"))
            if not target_table_name:
                continue

            # Don't create a FK from a table to itself here (self-ref handled separately)
            if target_table_name == table_name:
                continue

            # Determine confidence based on how the match was found
            confidence, strategy = _score_fk_match(col_name, entity, target_table_name)

            # Boost confidence when the same FK column appears in many tables
            # (indicates a shared "master" entity like Site or Asset)
            if entity_occurrence.get(entity, 0) >= 3:
                confidence = min(0.99, confidence + 0.05)

            target_table_info = external_tables.get(target_table_name, {})
            target_pk = _get_table_primary_key(target_table_info)

            fks.append(ForeignKeyDetection(
                source_table=table_name,
                source_column=col_name,
                target_table=target_table_name,
                target_column=target_pk,
                relationship_type="REFERENCE",
                confidence=confidence,
                reasoning=strategy,
                user_confirmed=False,
            ))
            logger.debug(
                f"[Node 5] Inferred FK ({confidence:.2f}): "
                f"{table_name}.{col_name} → {target_table_name}.{target_pk} [{strategy}]"
            )

    return fks


def _score_fk_match(col_name: str, entity: str, target_table: str) -> tuple[float, str]:
    """
    Return (confidence, strategy_description) for an inferred FK match.
    """
    # Strategy 2: Hungarian int{Table}ID  /  int{Table}Code
    stripped = _strip_hungarian(col_name)
    if col_name != stripped:  # prefix was actually stripped
        norm_stripped = _normalise_name(stripped)
        if norm_stripped.startswith(entity):
            return 0.92, f"Hungarian int/str prefix + entity+ID pattern ({col_name} → {target_table})"

    # Strategy 3: snake_case {table}_id / {table}_code
    if re.match(rf"^{re.escape(entity)}[_]?(id|code|key|num)$", _normalise_name(col_name), re.I):
        return 0.88, f"Snake-case {entity}_id pattern ({col_name} → {target_table})"

    # Strategy 4: camelCase {Table}Id / {Table}Code
    if re.match(rf"^{re.escape(entity)}(Id|Code|Key|Num)$", stripped, re.I):
        return 0.85, f"CamelCase {entity}Id pattern ({col_name} → {target_table})"

    # Strategy 5: suffix-only match (weaker)
    return 0.70, f"FK suffix pattern, entity '{entity}' resolved to {target_table}"


def _detect_self_referential(external_tables: dict) -> list[ForeignKeyDetection]:
    """
    Detect self-referential FKs (parent-child within the same table).

    Patterns detected:
      - intAssetParentID       (Hungarian prefix + table name + Parent + ID)
      - parent_id              (generic parent)
      - parent_{table_name}_id
      - {table}ParentId / {table}_parent_id
    """
    self_refs: list[ForeignKeyDetection] = []

    for table_name, table_info in external_tables.items():
        table_norm = _normalise_name(table_name)
        table_pk = _get_table_primary_key(table_info)

        for col in table_info.get("columns", []):
            col_name = col.get("field_name", "")
            if not col_name:
                continue

            norm_col = _normalise_name(col_name)
            stripped = _normalise_name(_strip_hungarian(col_name))

            is_self_ref = False
            reasoning = ""

            # Pattern A: generic parent_id (very common)
            if norm_col in ("parentid", "parent_id", "parentkey"):
                is_self_ref = True
                reasoning = f"Generic parent_id column in {table_name}"

            # Pattern B: {table}ParentID  or  int{Table}ParentID
            elif any(
                stripped.startswith(table_norm + p) or stripped.endswith(p + "id")
                for p in _PARENT_WORDS
            ):
                is_self_ref = True
                reasoning = f"Self-referential parent pattern ({col_name} in {table_name})"

            # Pattern C: intAssetParentID — strip Hungarian, normalise, check
            elif any(p in stripped for p in _PARENT_WORDS):
                # Confirm it also contains the table entity name
                if table_norm in stripped or table_norm[:-1] in stripped:
                    is_self_ref = True
                    reasoning = f"Hungarian + parent + table pattern ({col_name} in {table_name})"

            if is_self_ref:
                self_refs.append(ForeignKeyDetection(
                    source_table=table_name,
                    source_column=col_name,
                    target_table=table_name,
                    target_column=table_pk,
                    relationship_type="SELF_REFERENTIAL",
                    confidence=0.90,
                    reasoning=reasoning,
                    user_confirmed=False,
                ))
                logger.debug(
                    f"[Node 5] Self-ref: {table_name}.{col_name} → {table_name}.{table_pk}"
                )

    return self_refs


def _deduplicate_fks(fks: list[ForeignKeyDetection]) -> list[ForeignKeyDetection]:
    """
    Remove duplicate FK entries — keep the one with the highest confidence.
    """
    seen: dict[tuple, ForeignKeyDetection] = {}
    for fk in fks:
        key = (
            fk.get("source_table"),
            fk.get("source_column"),
            fk.get("target_table"),
        )
        existing = seen.get(key)
        if existing is None or fk.get("confidence", 0) > existing.get("confidence", 0):
            seen[key] = fk
    return list(seen.values())


# ── Relationship graph & hierarchy tree ───────────────────────────────────────


def _build_relationship_graph(
    fks: list[ForeignKeyDetection],
    external_tables: dict,
) -> dict[str, set[str]]:
    """
    Build directed graph: {table → set_of_tables_it_references}.
    Self-referential edges are excluded (they don't change hierarchy position).
    """
    graph: dict[str, set[str]] = {t: set() for t in external_tables}
    for fk in fks:
        src = fk.get("source_table")
        tgt = fk.get("target_table")
        if src and tgt and src != tgt and src in graph and tgt in graph:
            graph[src].add(tgt)
    return graph


def _detect_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    """DFS-based cycle detection."""
    cycles: list[list[str]] = []
    visited: set[str] = set()
    rec_stack: set[str] = set()

    def dfs(node: str, path: list[str]):
        visited.add(node)
        rec_stack.add(node)

        for neighbor in graph.get(node, set()):
            if neighbor not in visited:
                dfs(neighbor, path + [node])
            elif neighbor in rec_stack:
                try:
                    cycle_start = path.index(neighbor)
                    cycles.append(path[cycle_start:] + [node, neighbor])
                except ValueError:
                    pass

        rec_stack.discard(node)

    for table in list(graph.keys()):
        if table not in visited:
            dfs(table, [])

    return cycles


def _build_hierarchy_forest(
    graph: dict[str, set[str]],
    external_tables: dict,
    fks: list[ForeignKeyDetection],
) -> tuple[list[HierarchyNode], list[str]]:
    """
    Build the full hierarchy forest — one tree per disconnected component.

    Returns:
        (forest, isolated_tables)
        forest          — list of root HierarchyNode, one per independent hierarchy
        isolated_tables — table names with no FK relationships at all

    Root of each tree = a table with no incoming FK edges that has at least one
    child table referencing it.  When a component has no clear root (e.g. a cycle
    or mutual references), the table with the most incoming edges is used.
    """
    # Count incoming edges
    incoming: dict[str, int] = {t: 0 for t in graph}
    for src, targets in graph.items():
        for tgt in targets:
            if tgt in incoming:
                incoming[tgt] += 1

    # Reverse map: parent → [children]
    children_of: dict[str, list[str]] = defaultdict(list)
    for src, targets in graph.items():
        for tgt in targets:
            children_of[tgt].append(src)

    # FK column lookup: (child, parent) → fk_column on child
    child_fk_col: dict[tuple[str, str], str] = {}
    for fk in fks:
        if fk.get("relationship_type") != "SELF_REFERENTIAL":
            key = (fk.get("source_table", ""), fk.get("target_table", ""))
            child_fk_col[key] = fk.get("source_column", "")

    # Canonical table map: source table → canonical table name (from FK annotations)
    source_to_canonical: dict[str, str] = {}
    for fk in fks:
        if fk.get("canonical_backed") and fk.get("canonical_target_table"):
            tgt = fk.get("target_table", "")
            can = fk.get("canonical_target_table", "")
            if tgt and can:
                source_to_canonical[tgt] = can

    # Find connected components using undirected adjacency
    undirected: dict[str, set[str]] = {t: set() for t in graph}
    for src, targets in graph.items():
        for tgt in targets:
            undirected[src].add(tgt)
            undirected[tgt].add(src)

    visited_global: set[str] = set()

    def component_of(start: str) -> set[str]:
        comp: set[str] = set()
        stack = [start]
        while stack:
            node = stack.pop()
            if node in comp:
                continue
            comp.add(node)
            stack.extend(undirected.get(node, set()) - comp)
        return comp

    def build_node(table_name: str, depth: int, parent_name: Optional[str]) -> HierarchyNode:
        visited_global.add(table_name)
        table_info = external_tables.get(table_name, {})
        pk = _get_table_primary_key(table_info)

        parent_fk = child_fk_col.get((table_name, parent_name)) if parent_name else None

        child_nodes: list[HierarchyNode] = []
        for child in sorted(children_of.get(table_name, [])):
            if child not in visited_global and child != table_name:
                child_nodes.append(build_node(child, depth + 1, table_name))

        self_ref_col = next(
            (fk.get("source_column") for fk in fks
             if fk.get("source_table") == table_name
             and fk.get("target_table") == table_name),
            None,
        )

        node = HierarchyNode(
            table_name=table_name,
            primary_key_field=pk,
            parent_fk_field=parent_fk,
            children=child_nodes,
            level=depth,
        )
        if self_ref_col:
            node["self_referential_column"] = self_ref_col  # type: ignore[typeddict-unknown-key]
        canonical = source_to_canonical.get(table_name)
        if canonical:
            node["canonical_table"] = canonical  # type: ignore[typeddict-unknown-key]

        return node

    forest: list[HierarchyNode] = []
    isolated: list[str] = []

    all_tables = list(graph.keys())
    for table in all_tables:
        if table in visited_global:
            continue

        comp = component_of(table)

        # Isolated — no FK connections at all
        if len(comp) == 1 and not graph.get(table) and not children_of.get(table):
            isolated.append(table)
            visited_global.add(table)
            continue

        # Find the root of this component
        comp_roots = [
            t for t in comp
            if incoming.get(t, 0) == 0 and bool(children_of.get(t))
        ]
        if not comp_roots:
            # Fall back: most-referenced table in the component
            max_in = max((incoming.get(t, 0) for t in comp), default=0)
            comp_roots = [t for t in comp if incoming.get(t, 0) == max_in]

        # Sort for determinism — pick most-referenced first
        comp_roots.sort(key=lambda t: -incoming.get(t, 0))
        root = comp_roots[0]

        tree = build_node(root, 0, None)
        forest.append(tree)

        # Any component members still unvisited become orphan roots within this component
        for t in sorted(comp):
            if t not in visited_global:
                orphan = build_node(t, 0, None)
                forest.append(orphan)

    logger.info(
        f"[Node 5] Forest: {len(forest)} trees, "
        f"roots={[r.get('table_name') for r in forest]}, "
        f"{len(isolated)} isolated"
    )
    return forest, isolated


def _calculate_hierarchy_depth(node: Optional[HierarchyNode]) -> int:
    if not node:
        return 0
    children = node.get("children", [])
    if not children:
        return 1
    return 1 + max(_calculate_hierarchy_depth(c) for c in children)


def _detect_junction_tables(
    external_tables: dict,
    fks: list[ForeignKeyDetection],
) -> list[JunctionTable]:
    """
    Find bridge / association tables — tables whose primary purpose is to model
    a many-to-many relationship between two other tables.

    Heuristics:
      1. The table has exactly 2 or more FK columns pointing to *different* tables.
      2. FK columns represent ≥ 40% of all columns, OR the table has ≤ 5 columns total.
      3. The table is not already identified as a parent (root) entity.

    For each qualifying table, emit a JunctionTable for every pair of FK targets.
    """
    # Build map: table → list of FKs that originate from it
    table_fks: dict[str, list[ForeignKeyDetection]] = defaultdict(list)
    for fk in fks:
        if fk.get("relationship_type") != "SELF_REFERENTIAL":
            src = fk.get("source_table")
            if src:
                table_fks[src].append(fk)

    junctions: list[JunctionTable] = []

    for table_name, table_info in external_tables.items():
        outgoing = table_fks.get(table_name, [])
        if len(outgoing) < 2:
            continue

        # Only count FKs pointing to distinct tables
        unique_targets = {fk.get("target_table") for fk in outgoing if fk.get("target_table")}
        if len(unique_targets) < 2:
            continue

        total_cols = len(table_info.get("columns", []))
        fk_ratio = len(outgoing) / max(total_cols, 1)
        if fk_ratio < 0.40 and total_cols > 5:
            continue

        # Emit a junction entry for every pair of FK targets
        target_list = sorted(unique_targets)
        for i in range(len(target_list)):
            for j in range(i + 1, len(target_list)):
                left_table = target_list[i]
                right_table = target_list[j]

                left_fk = next(
                    (fk.get("source_column", "") for fk in outgoing
                     if fk.get("target_table") == left_table), ""
                )
                right_fk = next(
                    (fk.get("source_column", "") for fk in outgoing
                     if fk.get("target_table") == right_table), ""
                )

                avg_confidence = sum(
                    fk.get("confidence", 0.0) for fk in outgoing
                    if fk.get("target_table") in (left_table, right_table)
                ) / 2

                junctions.append(JunctionTable(
                    table_name=table_name,
                    left_table=left_table,
                    left_fk_column=left_fk,
                    right_table=right_table,
                    right_fk_column=right_fk,
                    confidence=round(avg_confidence, 3),
                    reasoning=(
                        f"Junction table: {fk_ratio:.0%} of columns are FKs "
                        f"({len(outgoing)}/{total_cols}); "
                        f"links {left_table} ↔ {right_table}"
                    ),
                ))
                logger.debug(
                    f"[Node 5] Junction: {table_name} → "
                    f"{left_table} ↔ {right_table}"
                )

    return junctions


def _detect_horizontal_relationships(
    external_tables: dict,
    fks: list[ForeignKeyDetection],
    hierarchy_forest: list[HierarchyNode],
    junction_tables: list[JunctionTable],
) -> list[HorizontalRelationship]:
    """
    Detect lateral / peer relationships — connections between tables at the same
    hierarchy level rather than the parent-child FK relationships already captured.

    Three patterns:

    SIBLING — both tables are direct children of the same parent table.
      e.g. WorkOrders and Assets are both children of Locations.

    MANY_TO_MANY — two tables are connected via a junction table.
      e.g. WorkOrders ↔ Parts via WorkOrderParts.

    PEER_FK — table A has a FK to table B, but A and B sit at the same hierarchy
      level (same depth in their respective trees).
      e.g. WorkOrders.intAssignedTechnicianID → Technicians
           where both are level-2 nodes in separate sub-trees.
    """
    horizontals: list[HorizontalRelationship] = []
    seen: set[frozenset[str]] = set()

    # Build level map: table_name → level across the entire forest
    table_level: dict[str, int] = {}

    def collect_levels(node: HierarchyNode) -> None:
        table_level[node.get("table_name", "")] = node.get("level", 0)
        for child in node.get("children", []):
            collect_levels(child)

    for root in hierarchy_forest:
        collect_levels(root)

    # ── Pattern 1: SIBLING ─────────────────────────────────────────
    # Build parent → [direct children] map
    parent_to_children: dict[str, list[str]] = defaultdict(list)

    def collect_parent_child(node: HierarchyNode) -> None:
        tbl = node.get("table_name", "")
        for child in node.get("children", []):
            child_name = child.get("table_name", "")
            parent_to_children[tbl].append(child_name)
            collect_parent_child(child)

    for root in hierarchy_forest:
        collect_parent_child(root)

    for parent, siblings in parent_to_children.items():
        sibling_list = sorted(set(siblings))
        for i in range(len(sibling_list)):
            for j in range(i + 1, len(sibling_list)):
                a, b = sibling_list[i], sibling_list[j]
                key = frozenset([a, b])
                if key in seen:
                    continue
                seen.add(key)
                horizontals.append(HorizontalRelationship(
                    source_table=a,
                    target_table=b,
                    relationship_type="SIBLING",
                    shared_parent=parent,
                    confidence=0.85,
                    reasoning=f"Both are direct children of {parent}",
                ))

    # ── Pattern 2: MANY_TO_MANY (via junction table) ───────────────
    for jt in junction_tables:
        a = jt.get("left_table", "")
        b = jt.get("right_table", "")
        key = frozenset([a, b])
        if key in seen:
            # upgrade existing entry to MANY_TO_MANY if it was SIBLING
            for h in horizontals:
                pair = frozenset([h.get("source_table"), h.get("target_table")])
                if pair == key:
                    h["relationship_type"] = "MANY_TO_MANY"
                    h["via_table"] = jt.get("table_name", "")
                    h["confidence"] = max(h.get("confidence", 0.0), jt.get("confidence", 0.0))
            continue
        seen.add(key)
        horizontals.append(HorizontalRelationship(
            source_table=a,
            target_table=b,
            relationship_type="MANY_TO_MANY",
            via_table=jt.get("table_name", ""),
            confidence=jt.get("confidence", 0.80),
            reasoning=(
                f"Many-to-many via junction table {jt.get('table_name')}; "
                f"FK cols: {jt.get('left_fk_column')} ↔ {jt.get('right_fk_column')}"
            ),
        ))

    # ── Pattern 3: PEER_FK — same-level direct reference ──────────
    for fk in fks:
        if fk.get("relationship_type") == "SELF_REFERENTIAL":
            continue
        src = fk.get("source_table", "")
        tgt = fk.get("target_table", "")
        if not src or not tgt:
            continue

        src_level = table_level.get(src)
        tgt_level = table_level.get(tgt)

        # Only emit PEER_FK when both tables are at the same non-zero level
        if src_level is None or tgt_level is None:
            continue
        if src_level != tgt_level or src_level == 0:
            continue

        key = frozenset([src, tgt])
        if key in seen:
            continue
        seen.add(key)
        horizontals.append(HorizontalRelationship(
            source_table=src,
            target_table=tgt,
            relationship_type="PEER_FK",
            source_fk_column=fk.get("source_column", ""),
            confidence=min(fk.get("confidence", 0.0), 0.80),
            reasoning=(
                f"Direct FK at the same hierarchy level (depth {src_level}): "
                f"{src}.{fk.get('source_column')} → {tgt}"
            ),
        ))

    return horizontals


# ── Implicit hierarchy detection ─────────────────────────────────────────────


def _detect_implicit_hierarchies(external_tables: dict) -> dict[str, dict]:
    """
    Detect SAP-style code hierarchies and other implicit structures from column names.
    """
    implicit: dict[str, dict] = {}

    for table_name, table_info in external_tables.items():
        fields = []
        for col in table_info.get("columns", []):
            col_name = col.get("field_name", "").lower()
            if any(
                pat in col_name
                for pat in [
                    "level_code", "hierarchy_code", "code_level", "hierarchical",
                    "path", "breadcrumb", "ancestry", "depth", "sort_order",
                    "display_order", "tree_", "_tree", "lft", "rgt", "rght",
                ]
            ):
                fields.append({
                    "field_name": col.get("field_name"),
                    "description": col.get("description"),
                })

        if fields:
            implicit[table_name] = {"fields": fields, "type": "implicit_hierarchy"}
            logger.debug(f"[Node 5] Implicit hierarchy: {table_name} ({len(fields)} field(s))")

    return implicit
