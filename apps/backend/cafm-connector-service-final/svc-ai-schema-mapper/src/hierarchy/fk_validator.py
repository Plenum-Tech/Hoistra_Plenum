"""Foreign key validation via data matching.

For each FK candidate:
1. Sample up to 500 values from source column
2. Check match rate against target table's primary key
3. Confirm FK if data_match_rate >= 0.80
"""

import logging
import random
from typing import Optional

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def validate_foreign_keys(
    fk_candidates: list[dict],
    cleaned_tables: dict[str, list[dict]],
) -> list[dict]:
    """
    Validate FK candidates by checking data match rates.

    Args:
        fk_candidates: List of FK candidates from scanner
        cleaned_tables: dict[table_name] = [row_dicts]

    Returns:
        List of validated FKs: [{source_table, source_column, target_table,
                                target_column, confidence, data_match_rate, validated}]
    """

    validated_fks = []

    for candidate in fk_candidates:
        source_table = candidate["source_table"]
        source_column = candidate["source_column"]
        target_table = candidate["target_table"]

        # Get source column values
        if source_table not in cleaned_tables:
            logger.warning(f"Source table {source_table} not found in cleaned_tables")
            continue

        source_records = cleaned_tables[source_table]
        if not source_records:
            logger.warning(f"Source table {source_table} is empty")
            continue

        # Extract source values
        source_values = []
        for record in source_records:
            val = record.get(source_column)
            if val is not None and str(val).strip():
                source_values.append(str(val).lower().strip())

        if not source_values:
            logger.warning(f"No values found in {source_table}.{source_column}")
            continue

        # Sample up to 500 values
        sample_size = min(500, len(source_values))
        source_sample = random.sample(source_values, sample_size)

        # Get target column values (try common PK names)
        if target_table not in cleaned_tables:
            logger.warning(f"Target table {target_table} not found in cleaned_tables")
            continue

        target_records = cleaned_tables[target_table]
        if not target_records:
            logger.warning(f"Target table {target_table} is empty")
            continue

        # Try to find primary key column
        target_pk_col = _find_pk_column(target_records, source_column)
        if not target_pk_col:
            logger.warning(f"Could not find PK column in {target_table}")
            continue

        # Extract target PK values
        target_pks = set()
        for record in target_records:
            val = record.get(target_pk_col)
            if val is not None:
                target_pks.add(str(val).lower().strip())

        if not target_pks:
            logger.warning(f"Target table {target_table}.{target_pk_col} has no non-null values")
            continue

        # Calculate match rate: matched / total non-null samples
        # Only count non-NULL, non-empty source values
        non_null_sample = [v for v in source_sample if v and str(v).strip()]
        if not non_null_sample:
            logger.warning(f"Source table {source_table}.{source_column} has no non-null values")
            continue

        matched = sum(1 for val in non_null_sample if val in target_pks)
        match_rate = matched / len(non_null_sample) if non_null_sample else 0.0

        # Validate: require >= 0.70 match rate for cross-table FKs
        # (higher for self-refs to avoid false positives)
        is_self_ref = source_table == target_table
        threshold = 0.85 if is_self_ref else 0.70
        is_valid = match_rate >= threshold

        validator_type = "SELF-REF" if is_self_ref else "CROSS-TABLE"
        logger.info(
            f"[FK Validator] {validator_type} {source_table}.{source_column} → {target_table}.{target_pk_col}: "
            f"match_rate={match_rate:.1%} (non_null_sample={len(non_null_sample)}, matched={matched}, threshold={threshold}) "
            f"{'VALID' if is_valid else 'INVALID'}"
        )

        validated_fk = {
            "source_table": source_table,
            "source_column": source_column,
            "target_table": target_table,
            "target_column": target_pk_col,
            "confidence": match_rate if is_valid else match_rate,
            "data_match_rate": match_rate,
            "validated": is_valid,
        }

        validated_fks.append(validated_fk)

    logger.info(
        f"[FK Validator] Validated: {sum(1 for fk in validated_fks if fk['validated'])} / {len(validated_fks)}"
    )
    return validated_fks


def _find_pk_column(records: list[dict], hint_column: str) -> Optional[str]:
    """
    Find primary key column in target table.

    Heuristics (in order):
    1. Look for column with EXACT same name as source column (asset_id → asset_id)
    2. Look for column matching base name (asset_id → asset)
    3. Look for standard PK patterns (_id, _code, id)
    4. Return first column

    Args:
        records: List of row dicts from target table
        hint_column: Source column name (e.g. 'asset_id')

    Returns:
        Target PK column name, or None
    """

    if not records:
        return None

    columns = list(records[0].keys())

    # HEURISTIC 1: Exact column name match (asset_id → asset_id)
    for col in columns:
        if col.lower() == hint_column.lower():
            return col

    # HEURISTIC 2: Base name match (asset_id → asset)
    # Remove FK suffixes to get base name
    base_hint = hint_column.replace("_code", "").replace("_id", "").replace("_key", "").replace("_num", "")

    for col in columns:
        if col.lower() == base_hint.lower():
            return col

    # HEURISTIC 3: Common PK patterns (look for _id, _code, or plain 'id')
    for pattern in ["_id", "_code", "_key", "_num"]:
        for col in columns:
            col_lower = col.lower()
            if col_lower.endswith(pattern):
                return col

    # HEURISTIC 4: Plain 'id' column
    for col in columns:
        if col.lower() == "id":
            return col

    # HEURISTIC 5: Return first column if no better match
    return columns[0] if columns else None
