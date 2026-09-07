"""Export cleaned tables to CSV format.

Creates one CSV file per table with canonical column names.
"""

import csv
import io
import logging
from typing import Dict, List, Tuple

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


def export_to_csv(
    cleaned_tables: Dict[str, List[Dict]],
) -> Dict[str, str]:
    """
    Export cleaned tables to CSV format.

    Args:
        cleaned_tables: dict[table_name] = [row_dicts]

    Returns:
        dict[table_name] = CSV content (as string)
    """

    csv_exports = {}

    for table_name, records in cleaned_tables.items():
        if not records:
            logger.warning(f"[CSV Exporter] Table {table_name} is empty")
            continue

        # Get columns from first record
        columns = list(records[0].keys())

        # Write to CSV string
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=columns, restval="")

        writer.writeheader()
        for record in records:
            writer.writerow(record)

        csv_content = output.getvalue()
        csv_exports[table_name] = csv_content

        logger.info(f"[CSV Exporter] Exported {table_name}: {len(records)} rows × {len(columns)} columns")

    logger.info(f"[CSV Exporter] Exported {len(csv_exports)} tables to CSV")
    return csv_exports


def get_csv_filename(table_name: str) -> str:
    """Get standardized CSV filename for a table."""
    return f"{table_name.lower()}.csv"
