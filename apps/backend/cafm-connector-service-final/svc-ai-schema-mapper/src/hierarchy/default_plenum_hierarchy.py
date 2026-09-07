"""
Default Plenum CAFM containment model for single-table CSV/Excel imports.

When the ingest produces only one table (e.g. ``data``), cross-table FK detection
cannot run. We still show the standard hierarchy for user review and map the
import table to the appropriate tier (usually ``assets``).
"""

from __future__ import annotations

import re
from typing import Any

# Canonical containment chain (child → parent)
PLENUM_DEFAULT_CONTAINMENT: list[tuple[str, str, str, str]] = [
    ("work_orders", "asset_id", "assets", "id"),
    ("assets", "location_id", "locations", "id"),
    ("locations", "site_id", "sites", "id"),
]

PLENUM_DEFAULT_STRUCTURE = "sites → locations → assets → work_orders → tasks"

PLENUM_LEVELS: dict[str, int] = {
    "sites": 0,
    "locations": 1,
    "assets": 2,
    "work_orders": 3,
    "tasks": 4,
}

_LOCATION_ID_SLUG_RE = re.compile(r"(?:^|_)(?:location_id|loc_id|building_id)$")
_LOCATION_ID_NEGATED_SLUG_RE = re.compile(r"(?:^|_)(?:not|non|without)(?:_|$)")


def is_single_table_import(cleaned_tables: dict[str, list[dict]]) -> bool:
    non_empty = [name for name, rows in cleaned_tables.items() if rows]
    return len(non_empty) == 1


def infer_import_table_plenum_role(table_name: str, columns: list[str]) -> str:
    """Pick which canonical tier best matches the flat import table."""
    joined = " ".join(columns).lower()
    name_l = table_name.lower()
    if any(t in joined or t in name_l for t in ("work_order", "wo_", "wo-", "maintenance")):
        return "work_orders"
    if any(t in joined or t in name_l for t in ("location", "loc_", "building", "floor")):
        return "locations"
    if any(t in joined or t in name_l for t in ("site", "facility", "campus")):
        return "sites"
    return "assets"


def _is_location_id_column(column: str, norm: str) -> bool:
    """True when column name is an intentional location FK (not substring false positives)."""
    if norm in ("locationid", "locid", "buildingid"):
        return True
    slug = re.sub(r"-", "_", column.lower().strip())
    if _LOCATION_ID_NEGATED_SLUG_RE.search(slug):
        return False
    if _LOCATION_ID_SLUG_RE.search(slug):
        return True
    # camelCase (e.g. parentLocationId) — suffix like siteid on line 97
    if norm.endswith("locationid"):
        if "allocation" in norm or norm.startswith(("nota", "non", "without")):
            return False
        return True
    return False


def _template_fk(
    source_table: str,
    source_column: str,
    target_table: str,
    target_column: str,
    *,
    extra_reason: str = "",
) -> dict[str, Any]:
    reason = (
        "System default Plenum CAFM hierarchy (reference model). "
        "These tables are not in your file — they describe how data is organized in Plenum."
    )
    if extra_reason:
        reason = f"{reason} {extra_reason}"
    return {
        "source_table": source_table,
        "source_column": source_column,
        "target_table": target_table,
        "target_column": target_column,
        "relationship_type": "CONTAINMENT",
        "confidence": 1.0,
        "data_match_rate": 1.0,
        "validated": True,
        "system_default": True,
        "pattern_matched": "plenum_default",
        "reasoning": reason.strip(),
    }


def _import_table_column_hints(table_name: str, columns: list[str]) -> list[dict[str, Any]]:
    """Map recognizable column names on the flat table to canonical parent entities."""
    hints: list[dict[str, Any]] = []
    for col in columns:
        norm = re.sub(r"[^a-z0-9]", "", col.lower())
        if norm in ("siteid", "sitecode", "sitekey") or norm.endswith("siteid"):
            hints.append(
                {
                    "source_table": table_name,
                    "source_column": col,
                    "target_table": "sites",
                    "target_column": "id",
                    "relationship_type": "REFERENCE",
                    "confidence": 0.85,
                    "data_match_rate": 0.85,
                    "validated": True,
                    "system_default": False,
                    "pattern_matched": "column_hint",
                    "reasoning": (
                        f"Column '{col}' on '{table_name}' looks like a site reference — "
                        "confirm how it maps in the Plenum model."
                    ),
                }
            )
        elif _is_location_id_column(col, norm):
            hints.append(
                {
                    "source_table": table_name,
                    "source_column": col,
                    "target_table": "locations",
                    "target_column": "id",
                    "relationship_type": "REFERENCE",
                    "confidence": 0.85,
                    "data_match_rate": 0.85,
                    "validated": True,
                    "system_default": False,
                    "pattern_matched": "column_hint",
                    "reasoning": (
                        f"Column '{col}' on '{table_name}' looks like a location reference."
                    ),
                }
            )
        elif norm in ("assetid", "assetcode") or (norm.endswith("assetid") and table_name.lower() != "assets"):
            hints.append(
                {
                    "source_table": table_name,
                    "source_column": col,
                    "target_table": "assets",
                    "target_column": "id",
                    "relationship_type": "REFERENCE",
                    "confidence": 0.85,
                    "data_match_rate": 0.85,
                    "validated": True,
                    "system_default": False,
                    "pattern_matched": "column_hint",
                    "reasoning": f"Column '{col}' on '{table_name}' looks like an asset reference.",
                }
            )
    return hints


def build_default_hierarchy_for_single_table(
    table_name: str,
    columns: list[str],
    existing_fks: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], dict[str, Any]]:
    """
    Merge detected FKs with Plenum default template + import-table role mapping.

    Returns:
        merged_fks, hierarchy_levels, metadata for gate payload
    """
    plenum_role = infer_import_table_plenum_role(table_name, columns)
    templates = [_template_fk(st, sc, tt, tc) for st, sc, tt, tc in PLENUM_DEFAULT_CONTAINMENT]

    import_role_row = _template_fk(
        table_name,
        "(import_table)",
        plenum_role,
        "id",
        extra_reason=(
            f"Your file table '{table_name}' is mapped to the **{plenum_role}** tier "
            f"in the default model ({PLENUM_DEFAULT_STRUCTURE})."
        ),
    )
    import_role_row["relationship_type"] = "PART_OF"
    import_role_row["mapping_note"] = True

    column_hints = _import_table_column_hints(table_name, columns)

    merged: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for fk in existing_fks + column_hints + templates + [import_role_row]:
        key = (
            str(fk.get("source_table", "")),
            str(fk.get("source_column", "")),
            str(fk.get("target_table", "")),
            str(fk.get("target_column", "id")),
        )
        if key not in merged or fk.get("system_default"):
            merged[key] = fk

    levels = dict(PLENUM_LEVELS)
    levels[table_name] = levels.get(plenum_role, 2)

    meta = {
        "single_table_import": True,
        "system_default_hierarchy": True,
        "import_table_name": table_name,
        "import_table_plenum_role": plenum_role,
        "proposed_structure": PLENUM_DEFAULT_STRUCTURE,
        "hierarchy_tree": PLENUM_DEFAULT_STRUCTURE,
    }
    return list(merged.values()), levels, meta


def filter_confirmed_hierarchies_for_output(
    confirmed: list[dict[str, Any]],
    cleaned_tables: dict[str, list[dict]],
    *,
    single_table_mode: bool,
) -> list[dict[str, Any]]:
    """
    Drop pure Plenum template FKs that reference tables absent from the import.

    Keeps edges where the source table exists in cleaned_tables (column hints, self-ref).
    """
    if not single_table_mode:
        return confirmed

    import_tables = {name for name, rows in cleaned_tables.items() if rows}
    kept: list[dict[str, Any]] = []
    for rel in confirmed:
        if rel.get("mapping_note"):
            continue
        src = rel.get("source_table")
        tgt = rel.get("target_table")
        if rel.get("system_default") and src not in import_tables:
            continue
        if rel.get("system_default") and tgt not in import_tables and src in import_tables:
            # Virtual parent (sites/locations) with only a column hint — keep REFERENCE hints
            if rel.get("relationship_type") == "REFERENCE":
                kept.append(rel)
            continue
        if rel.get("system_default") and src not in import_tables and tgt not in import_tables:
            continue
        kept.append(rel)
    return kept
