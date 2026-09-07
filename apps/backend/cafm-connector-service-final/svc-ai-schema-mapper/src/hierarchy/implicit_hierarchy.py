"""Detect SAP-style implicit hierarchies encoded in code values.

Example: Equipment codes like "PLANT-LINE-UNIT-ID" encode a 4-level hierarchy
via a consistent separator pattern.
"""

import logging
import re
from collections import Counter
from typing import Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def detect_implicit_hierarchies(
    cleaned_tables: dict[str, list[dict]],
) -> dict[str, dict]:
    """
    Detect implicit hierarchies in code columns.

    Looks for columns with consistent separator patterns (e.g., "-", "_", ".")
    that suggest hierarchical encoding.

    Args:
        cleaned_tables: dict[table_name] = [row_dicts]

    Returns:
        dict[column_name] = {
            separator: str,
            levels: int,
            examples: list[str],
            confidence: float
        }
    """

    implicit_hierarchies = {}

    for table_name, records in cleaned_tables.items():
        if not records:
            continue

        for col_name in records[0].keys():
            # Look for code-like columns
            if not any(x in col_name.lower() for x in ["code", "id", "num"]):
                continue

            # Extract unique values
            values = []
            for record in records:
                val = record.get(col_name)
                if val is not None:
                    values.append(str(val).strip())

            if not values:
                continue

            # Detect separator
            separator = _detect_separator(values)
            if not separator:
                continue

            # Check if separator is consistent
            levels = _count_levels(values, separator)
            if levels < 2:
                continue  # Not hierarchical (no separator found)

            # Check consistency
            consistency = _check_consistency(values, separator, levels)
            if consistency < 0.80:
                continue  # Not consistent enough

            implicit_hierarchies[f"{table_name}.{col_name}"] = {
                "separator": separator,
                "levels": levels,
                "examples": values[:5],
                "confidence": consistency,
            }

            logger.info(
                f"[Implicit Hierarchy] {table_name}.{col_name}: "
                f"{levels} levels, sep='{separator}', confidence={consistency:.1%}"
            )

    return implicit_hierarchies


def _detect_separator(values: list[str]) -> Optional[str]:
    """
    Detect the most common separator in a list of values.

    Candidates: - _ . / :
    """

    separators = ["-", "_", ".", "/", ":"]
    sep_counts = Counter()

    for val in values[:100]:  # Sample first 100
        for sep in separators:
            if sep in val:
                sep_counts[sep] += 1

    if not sep_counts:
        return None

    most_common_sep, count = sep_counts.most_common(1)[0]
    threshold = len(values[:100]) * 0.50

    if count >= threshold:
        return most_common_sep

    return None


def _count_levels(values: list[str], separator: str) -> int:
    """
    Count the number of hierarchy levels (parts separated by separator).

    Args:
        values: List of code values
        separator: Separator character

    Returns:
        Most common number of levels (0 if inconsistent)
    """

    level_counts = Counter()

    for val in values[:100]:
        parts = val.split(separator)
        level_counts[len(parts)] += 1

    if not level_counts:
        return 0

    most_common_level, count = level_counts.most_common(1)[0]

    # Only return if this level is the majority
    if count >= len(values[:100]) * 0.60:
        return most_common_level

    return 0


def _check_consistency(values: list[str], separator: str, levels: int) -> float:
    """
    Check consistency of separator usage across all values.

    Returns: Proportion of values that match the expected level count
    """

    if not values or levels < 2:
        return 0.0

    matching = 0
    for val in values:
        parts = val.split(separator)
        if len(parts) == levels:
            matching += 1

    return matching / len(values)
