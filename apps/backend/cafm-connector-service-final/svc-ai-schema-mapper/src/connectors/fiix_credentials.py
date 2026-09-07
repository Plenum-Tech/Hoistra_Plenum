"""Resolve Fiix API credentials from request body or service settings."""

from __future__ import annotations

from typing import Any, Optional, Tuple

from fastapi import HTTPException

from ..config import Settings


def credentials_from_mapping(
    data: dict[str, Any] | None,
    settings: Settings,
) -> Tuple[str, str, str, str]:
    """
    Merge user-supplied Fiix keys with FIIX_* environment defaults.

    Raises HTTPException 400 when any required field is still missing.
    """
    data = data or {}
    subdomain = (data.get("fiix_subdomain") or settings.fiix_subdomain or "").strip()
    app_key = (data.get("fiix_app_key") or settings.fiix_app_key or "").strip()
    access_key = (data.get("fiix_access_key") or settings.fiix_access_key or "").strip()
    secret_key = (data.get("fiix_secret_key") or settings.fiix_secret_key or "").strip()

    if not all([subdomain, app_key, access_key, secret_key]):
        raise HTTPException(
            status_code=400,
            detail=(
                "Fiix credentials required: fiix_subdomain, fiix_app_key, "
                "fiix_access_key, fiix_secret_key"
            ),
        )
    return subdomain, app_key, access_key, secret_key


def summarize_fiix_mapper(mapper_config: dict[str, Any]) -> dict[str, Any]:
    """Compact summary for chat tools (full mapper JSON is too large)."""
    canonical = mapper_config.get("canonical_fields") or {}
    tables_by_object = mapper_config.get("tables_by_object") or {}
    table_names = sorted(tables_by_object.keys()) if isinstance(tables_by_object, dict) else []
    column_count = 0
    if isinstance(tables_by_object, dict):
        for fields in tables_by_object.values():
            if isinstance(fields, dict):
                column_count += len(fields)
    table_count = len(table_names)
    canonical_field_count = len(canonical) if isinstance(canonical, dict) else 0
    meta = mapper_config.get("metadata") if isinstance(mapper_config.get("metadata"), dict) else {}
    defined_objects = int(meta.get("total_objects") or table_count)
    return {
        "source_system": str(mapper_config.get("source_system") or "Fiix"),
        "canonical_field_count": canonical_field_count,
        "table_count": table_count,
        "fiix_object_count": table_count,
        "fiix_objects_defined": defined_objects,
        "column_count": column_count,
        "mapped_field_count": column_count,
        "sample_tables": table_names[:25],
        "tables_truncated": max(0, len(table_names) - 25),
        "fiix": {
            "label": "Fiix CMMS (source)",
            "table_count": table_count,
            "column_count": column_count,
            "canonical_field_count": canonical_field_count,
        },
    }


def fetch_plenum_cafm_schema_counts_sync(db_url: str) -> dict[str, int]:
    """Lightweight information_schema counts for plenum_cafm (target platform)."""
    if not db_url:
        return {"table_count": 0, "column_count": 0}
    url = db_url.replace("postgresql+asyncpg://", "postgresql://").replace(
        "postgres+asyncpg://", "postgresql://"
    )
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            table_count = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)::int
                        FROM information_schema.tables
                        WHERE table_schema = 'plenum_cafm'
                          AND table_type = 'BASE TABLE'
                        """
                    )
                ).scalar()
                or 0
            )
            column_count = int(
                conn.execute(
                    text(
                        """
                        SELECT COUNT(*)::int
                        FROM information_schema.columns
                        WHERE table_schema = 'plenum_cafm'
                        """
                    )
                ).scalar()
                or 0
            )
        return {"table_count": table_count, "column_count": column_count}
    finally:
        engine.dispose()


def build_schema_comparison(
    fiix_summary: dict[str, Any],
    plenum_counts: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Side-by-side Fiix (source) vs plenum_cafm (target) for UI and chat."""
    fiix = fiix_summary.get("fiix") if isinstance(fiix_summary.get("fiix"), dict) else {}
    if not fiix:
        fiix = {
            "label": "Fiix CMMS (source)",
            "table_count": int(fiix_summary.get("table_count") or 0),
            "column_count": int(
                fiix_summary.get("column_count") or fiix_summary.get("mapped_field_count") or 0
            ),
            "canonical_field_count": int(fiix_summary.get("canonical_field_count") or 0),
        }
    plenum = {
        "label": "plenum_cafm (target platform)",
        "table_count": int((plenum_counts or {}).get("table_count") or 0),
        "column_count": int((plenum_counts or {}).get("column_count") or 0),
    }
    fiix_tables = int(fiix.get("table_count") or 0)
    fiix_cols = int(fiix.get("column_count") or 0)
    plenum_tables = plenum["table_count"]
    plenum_cols = plenum["column_count"]
    defined = int(fiix_summary.get("fiix_objects_defined") or fiix_tables)
    markdown = (
        "### Schema overview\n\n"
        f"**Fiix CMMS (source — live API)**\n"
        f"- **{fiix_tables}** Fiix object types (tables)"
        + (f" ({defined} defined in connector)" if defined != fiix_tables else "")
        + "\n"
        f"- **{fiix_cols}** Fiix fields (columns)\n\n"
        f"**plenum_cafm (target — your platform database)**\n"
        f"- **{plenum_tables}** canonical tables\n"
        f"- **{plenum_cols}** canonical columns\n\n"
        "_Mapping connects Fiix source fields to plenum_cafm canonical columns._"
    )
    return {
        "fiix": fiix,
        "plenum_cafm": plenum,
        "markdown": markdown,
    }


def _fiix_counts_from_nodes(nodes: list[dict[str, Any]] | None) -> tuple[int, int, str]:
    fiix_tables = fiix_cols = 0
    cmms_name = "Fiix"
    if not nodes:
        return fiix_tables, fiix_cols, cmms_name
    for node in nodes:
        if not isinstance(node, dict):
            continue
        out = node.get("output")
        if not isinstance(out, dict):
            continue
        nid = node.get("node_id")
        if nid == 1:
            fiix_tables = int(out.get("table_count") or 0)
            fiix_cols = int(out.get("total_columns") or 0)
            cmms_name = str(out.get("external_cmms_name") or cmms_name)
    return fiix_tables, fiix_cols, cmms_name


def enrich_schema_comparison_for_status(
    comparison: dict[str, Any] | None,
    *,
    job_total_tables: int = 0,
    job_total_fields: int = 0,
    final_summary: dict[str, Any] | None = None,
    nodes: list[dict[str, Any]] | None = None,
    pending_gate_payload: dict[str, Any] | None = None,
    external_cmms_name: str = "Fiix",
) -> dict[str, Any] | None:
    """Ensure Fiix side counts stay populated before node 1 completes (chat uses fetch summary)."""
    base: dict[str, Any] | None = comparison if isinstance(comparison, dict) else None
    if not base and isinstance(final_summary, dict):
        stored = final_summary.get("schema_comparison")
        if isinstance(stored, dict):
            base = stored
    if not base:
        base = schema_comparison_from_nodes(nodes)
    if not base:
        return None

    fiix_raw = base.get("fiix") if isinstance(base.get("fiix"), dict) else {}
    plenum_raw = base.get("plenum_cafm") if isinstance(base.get("plenum_cafm"), dict) else {}

    fiix_tables = int(fiix_raw.get("table_count") or 0)
    fiix_cols = int(fiix_raw.get("column_count") or 0)

    if fiix_tables == 0 or fiix_cols == 0:
        n_tables, n_cols, cmms = _fiix_counts_from_nodes(nodes)
        if fiix_tables == 0 and n_tables > 0:
            fiix_tables = n_tables
        if fiix_cols == 0 and n_cols > 0:
            fiix_cols = n_cols
        if n_tables > 0:
            external_cmms_name = cmms

    if fiix_tables == 0 and job_total_tables > 0:
        fiix_tables = int(job_total_tables)
    if fiix_cols == 0 and job_total_fields > 0:
        fiix_cols = int(job_total_fields)

    if (fiix_tables == 0 or fiix_cols == 0) and isinstance(pending_gate_payload, dict):
        if fiix_tables == 0:
            fiix_tables = int(pending_gate_payload.get("table_count") or 0)
        if fiix_cols == 0:
            fiix_cols = int(pending_gate_payload.get("total_columns") or 0)

    if isinstance(final_summary, dict):
        fs = final_summary.get("fiix_summary")
        if isinstance(fs, dict):
            if fiix_tables == 0:
                fiix_tables = int(fs.get("table_count") or fs.get("fiix_object_count") or 0)
            if fiix_cols == 0:
                fiix_cols = int(
                    fs.get("column_count") or fs.get("mapped_field_count") or 0
                )

    plenum_tables = int(plenum_raw.get("table_count") or 0)
    plenum_cols = int(plenum_raw.get("column_count") or 0)
    if plenum_tables == 0 and nodes:
        for node in nodes:
            if not isinstance(node, dict) or node.get("node_id") != 0:
                continue
            out = node.get("output")
            if isinstance(out, dict):
                plenum_tables = int(out.get("canonical_table_count") or plenum_tables)
                plenum_cols = int(out.get("canonical_column_count") or plenum_cols)

    if plenum_tables == 0 and fiix_tables == 0:
        return base

    label = str(fiix_raw.get("label") or f"{external_cmms_name} (source)")
    return build_schema_comparison(
        {
            "fiix": {
                "label": label,
                "table_count": fiix_tables,
                "column_count": fiix_cols,
                "canonical_field_count": int(fiix_raw.get("canonical_field_count") or 0),
            }
        },
        {"table_count": plenum_tables, "column_count": plenum_cols},
    )


def schema_comparison_from_nodes(nodes: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Build comparison from schema-mapping pipeline node outputs (nodes 0 and 1)."""
    if not nodes:
        return None
    plenum_tables = plenum_cols = 0
    fiix_tables, fiix_cols, cmms_name = _fiix_counts_from_nodes(nodes)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        out = node.get("output")
        if not isinstance(out, dict):
            continue
        if node.get("node_id") == 0:
            plenum_tables = int(out.get("canonical_table_count") or 0)
            plenum_cols = int(out.get("canonical_column_count") or 0)
    if plenum_tables == 0 and fiix_tables == 0:
        return None
    return build_schema_comparison(
        {
            "fiix": {
                "label": f"{cmms_name} (source)",
                "table_count": fiix_tables,
                "column_count": fiix_cols,
            }
        },
        {"table_count": plenum_tables, "column_count": plenum_cols},
    )
