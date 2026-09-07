"""Build nested JSON from cleaned tables and hierarchy structure.

Traverses containment_hierarchy to build: sites > locations > assets > work_orders > tasks.
Applies multi-merge strategies for fields mapped to same target.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

# Multi-merge strategies for handling multi-source fields
MERGE_STRATEGIES = {
    "concat_space": lambda vals: " ".join(str(v).strip() for v in vals if v),
    "concat_comma": lambda vals: ", ".join(str(v).strip() for v in vals if v),
    "concat_dash": lambda vals: " - ".join(str(v).strip() for v in vals if v),
    "coalesce": lambda vals: next((v for v in vals if v), None),  # First non-null
}


def build_nested_json(
    cleaned_tables: Dict[str, List[Dict]],
    containment_hierarchy: Dict[str, Any],
    confirmed_hierarchies: List[Dict],
) -> Dict[str, Any]:
    """
    Build nested JSON structure from cleaned data and hierarchy.

    Nesting order: sites > locations > assets > work_orders > tasks

    Args:
        cleaned_tables: dict[table_name] = [row_dicts]
        containment_hierarchy: Detected containment relationships
        confirmed_hierarchies: Customer-confirmed FK relationships

    Returns:
        Nested dict: {sites: [{name, locations: [{name, assets: [{...}]}]}]}
    """

    logger.info("[JSON Builder] Starting nested JSON construction...")

    # Infer nesting order from confirmed hierarchies
    nesting_order = _infer_nesting_order(confirmed_hierarchies)
    logger.info(f"[JSON Builder] Nesting order: {' > '.join(nesting_order)}")

    # Build nested structure
    nested = {"sites": []}

    # Start from top-level (sites/locations)
    sites_table = next((t for t in nesting_order if "site" in t.lower() or "location" in t.lower()), None)

    if sites_table and sites_table in cleaned_tables:
        for site_record in cleaned_tables[sites_table]:
            site_obj = _build_nested_level(
                site_record,
                cleaned_tables,
                confirmed_hierarchies,
                nesting_order,
                current_level=0,
            )
            nested["sites"].append(site_obj)

    logger.info(f"[JSON Builder] Built nested structure with {len(nested['sites'])} sites")
    return nested


def _infer_nesting_order(confirmed_hierarchies: List[Dict]) -> List[str]:
    """
    Infer nesting order from confirmed hierarchies.

    Default: sites > locations > assets > work_orders > tasks

    Args:
        confirmed_hierarchies: List of confirmed FK relationships

    Returns:
        List of table names in nesting order
    """

    default_order = [
        "sites",
        "locations",
        "assets",
        "work_orders",
        "tasks",
        "parts",
        "maintenance_plans",
    ]

    # Try to extract from hierarchies
    extracted = set()
    for h in confirmed_hierarchies:
        if h.get("relationship_type") == "CONTAINMENT":
            extracted.add(h.get("source_table"))
            extracted.add(h.get("target_table"))

    # Filter default order to only include extracted tables
    if extracted:
        order = [t for t in default_order if t in extracted]
        if order:
            return order

    return default_order


def _build_nested_level(
    parent_record: Dict[str, Any],
    cleaned_tables: Dict[str, List[Dict]],
    hierarchies: List[Dict],
    nesting_order: List[str],
    current_level: int,
) -> Dict[str, Any]:
    """
    Recursively build nested level of structure.

    Args:
        parent_record: Current record being processed
        cleaned_tables: All cleaned data
        hierarchies: Confirmed FK relationships
        nesting_order: Expected nesting order
        current_level: Current nesting depth

    Returns:
        Nested dict at this level
    """

    # Return record as-is if at leaf level
    if current_level >= len(nesting_order) - 1:
        return parent_record

    current_table = nesting_order[current_level]
    next_table = nesting_order[current_level + 1] if current_level + 1 < len(nesting_order) else None

    if not next_table or next_table not in cleaned_tables:
        return parent_record

    # Find FK relationship: current_table -> next_table
    fk = next(
        (h for h in hierarchies
         if h.get("source_table") == next_table and
         h.get("target_table") == current_table and
         h.get("relationship_type") == "CONTAINMENT"),
        None,
    )

    if not fk:
        return parent_record

    # Find child records (next_table records that reference this record)
    parent_pk = parent_record.get(fk.get("target_column"))
    if not parent_pk:
        return parent_record

    next_level_key = next_table + "_list"
    nested_obj = parent_record.copy()
    nested_obj[next_level_key] = []

    for child_record in cleaned_tables[next_table]:
        child_fk_val = child_record.get(fk.get("source_column"))
        if child_fk_val and str(child_fk_val).lower() == str(parent_pk).lower():
            child_obj = _build_nested_level(
                child_record,
                cleaned_tables,
                hierarchies,
                nesting_order,
                current_level + 1,
            )
            nested_obj[next_level_key].append(child_obj)

    return nested_obj


def apply_merge_strategy(
    source_values: List[Any],
    strategy: str = "coalesce",
) -> Optional[str]:
    """
    Apply merge strategy to multiple values mapped to same target field.

    Args:
        source_values: List of values to merge
        strategy: Strategy name (concat_space, concat_comma, coalesce, concat_dash)

    Returns:
        Merged value, or None if empty
    """

    if not source_values:
        return None

    merge_func = MERGE_STRATEGIES.get(strategy, MERGE_STRATEGIES["coalesce"])

    return merge_func(source_values)
