"""Foreign key candidate detection via naming pattern analysis.

Strategy: Scan for 8 common FK suffix patterns in column names.
Returns candidates with initial confidence for validation.
"""

import logging
import re
from typing import Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# 8 FK suffix patterns (target_table implied by stripping suffix)
FK_PATTERNS = [
    # Pattern: (suffix_regex, example_source, example_target)
    (r"_code$", "asset_code", "asset"),
    (r"_id$", "asset_id", "asset"),
    (r"_key$", "asset_key", "asset"),
    (r"_ref$", "asset_ref", "asset"),
    (r"^parent_(.+)$", "parent_asset", "asset"),
    (r"_fk$", "asset_fk", "asset"),
    (r"_num$", "wo_num", "wo"),
    (r"^(.+)_to_(.+)$", "asset_to_location", "location"),
]


def scan_foreign_keys(
    table_names: list[str],
    column_names_per_table: dict[str, list[str]],
) -> list[dict]:
    """
    Scan column names for FK candidate patterns.

    Args:
        table_names: List of all table names (target tables)
        column_names_per_table: dict[table_name] = [column_names]

    Returns:
        List of FK candidates: [{source_table, source_column, target_table, target_column, initial_confidence}]
    """

    candidates = []

    for source_table, columns in column_names_per_table.items():
        for col in columns:
            # Skip obviously non-FK columns
            if col in ["id", "name", "description", "created_at", "updated_at"]:
                continue

            # Try each FK pattern
            for pattern, ex_src, ex_tgt in FK_PATTERNS:
                match = re.search(pattern, col, re.IGNORECASE)

                if match:
                    # Extract implied target table name
                    if pattern == r"^parent_(.+)$":
                        # parent_asset → asset
                        target_base = match.group(1).lower()
                    elif pattern == r"^(.+)_to_(.+)$":
                        # asset_to_location → location (second part is target)
                        target_base = match.group(2).lower()
                    else:
                        # Remove suffix to get base name
                        target_base = re.sub(pattern, "", col, flags=re.IGNORECASE).lower()

                    # Check if target table exists
                    # Try: exact match, plural match, singular match
                    target_table = None

                    # Try exact match first (asset_id → asset)
                    for table in table_names:
                        if table.lower() == target_base:
                            target_table = table
                            break

                    # Try plural forms (asset → assets, site → sites)
                    if not target_table:
                        for table in table_names:
                            table_lower = table.lower()
                            # Check if table is plural of target_base
                            if table_lower == target_base + "s" or table_lower == target_base + "es":
                                target_table = table
                                break

                    # Try singular forms (assets → asset, sites → site)
                    if not target_table:
                        for table in table_names:
                            table_lower = table.lower()
                            if target_base.endswith("s"):
                                singular = target_base[:-1]
                                if table_lower == singular or table_lower == singular + "es":
                                    target_table = table
                                    break

                    # Fallback: substring matching
                    if not target_table:
                        for table in table_names:
                            if target_base in table.lower() or table.lower() in target_base:
                                target_table = table
                                break

                    if target_table:
                        # Guard: a column resolving to its OWN table (commonly via the loose
                        # substring fallback above, e.g. asset_id → assetclassification) is a
                        # spurious self-FK that creates a cycle and blows the recursion limit
                        # downstream. Genuine parent-child self-refs are handled by
                        # scan_self_referential_foreign_keys; skip self-refs here.
                        if target_table.lower() == source_table.lower():
                            logger.debug(
                                f"Skipping spurious self-FK {source_table}.{col} → {target_table}"
                            )
                            break

                        candidate = {
                            "source_table": source_table,
                            "source_column": col,
                            "target_table": target_table,
                            "target_column": "id",  # Will be determined by validator
                            "initial_confidence": 0.70,  # Will be validated
                            "pattern_matched": pattern,
                        }
                        candidates.append(candidate)
                        logger.debug(
                            f"FK candidate: {source_table}.{col} → {target_table}"
                        )
                        break

    logger.info(f"[FK Scanner] Found {len(candidates)} FK candidates")
    return candidates


_PARENT_COL_RE = re.compile(
    r"^(parent_?id|parent_?key|parentid|parent_key|int[a-z]*parentid)$",
    re.IGNORECASE,
)


def _guess_pk_column(columns: list[str], table_name: str) -> Optional[str]:
    """Best-effort PK column for self-referential detection."""
    lower = {c.lower(): c for c in columns}
    table_norm = table_name.lower().rstrip("s")
    for candidate in (
        "id",
        f"{table_name.lower()}_id",
        f"{table_norm}_id",
        f"{table_name.lower()}_code",
        f"{table_norm}_code",
    ):
        if candidate in lower:
            return lower[candidate]
    for col in columns:
        cl = col.lower()
        if cl.endswith("_id") or cl.endswith("_code") or cl == "id":
            return col
    return columns[0] if columns else None


def scan_self_referential_foreign_keys(
    column_names_per_table: dict[str, list[str]],
) -> list[dict]:
    """
    Detect parent-child within the same table from column names (parent_id → table PK).
    """
    candidates: list[dict] = []
    for table_name, columns in column_names_per_table.items():
        pk = _guess_pk_column(columns, table_name)
        if not pk:
            continue
        table_norm = re.sub(r"[^a-z0-9]", "", table_name.lower())
        for col in columns:
            if col == pk:
                continue
            norm = re.sub(r"[^a-z0-9]", "", col.lower())
            is_parent = bool(_PARENT_COL_RE.match(norm))
            if not is_parent and "parent" in norm and (table_norm in norm or norm.endswith("id")):
                is_parent = True
            if is_parent:
                candidates.append(
                    {
                        "source_table": table_name,
                        "source_column": col,
                        "target_table": table_name,
                        "target_column": pk,
                        "initial_confidence": 0.88,
                        "pattern_matched": "self_referential",
                    }
                )
                logger.debug(f"Self-ref FK candidate: {table_name}.{col} → {table_name}.{pk}")
    logger.info(f"[FK Scanner] Found {len(candidates)} self-referential FK candidates")
    return candidates
