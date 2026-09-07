"""Content-aware table routing: rank candidate plenum_cafm tables for a source sheet by
how many of its COLUMNS actually map to each table (exact / identity / schema-alias /
FM-crosswalk / CMMS-alias). Lets the gate offer the best destination table(s) to select
when the sheet NAME is misleading (e.g. a sheet called 'work_tasks' whose columns are
ASSETNUM/manufacturer/model really belongs in 'assets', not 'work_orders').

Pure-ish: uses the generated schema + alias modules; no DB, no LLM.
"""
from __future__ import annotations

import re

from .plenum_cafm_schema import TABLES, COLUMN_ALIASES_BY_TABLE

# Internal / non-domain tables that should never be offered as a routing target.
_NON_DOMAIN = {
    "alembic_version", "canonical_registry", "agent_audit_log", "orchestration_audit_log",
    "claude_api_usage", "claude_budget_config", "ingestion_audit_log", "ingestion_documents",
    "document_chunks", "document_generation_log", "corrections_log", "review_queue",
    "prompt_ab_tests", "prompt_templates", "migration_field_mappings", "migration_hierarchy",
    "migration_jobs", "schema_mapping_field_mappings", "schema_mapping_jobs",
    "fiix_ingestion_jobs", "fiix_schema_cache", "import_errors", "import_jobs", "field_maps",
    "connectors", "mapping_templates", "udr_entity_relationships", "udr_run_graph",
    "udr_run_versions", "activity_actions", "activity_log_entries", "audit_logs",
    "query_audit_log", "data", "alembic_version",
}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s).lower()).strip("_")


def _id_forms(table: str) -> set[str]:
    sing = table[:-1] if table.endswith("s") and not table.endswith("ss") else table
    sn = _norm(sing)
    return {"id", f"{_norm(table)}_id", f"{sn}_id", f"{sn}id", f"{sn}num", f"{sn}_num",
            f"{sn}_no", f"{sn}_ref", f"{sn}_reference", f"{sn}_number", f"{sn}_identifier"}


def _column_hits(source_col: str, table: str, tcn: dict, id_forms: set[str]) -> bool:
    """True if source_col would map to a REAL column of `table` deterministically."""
    n = _norm(source_col)
    if n in tcn:                                   # exact column
        return True
    if n in id_forms:                              # entity identity → id
        return True
    a = (COLUMN_ALIASES_BY_TABLE.get(table) or {}).get(n)   # CSV-derived alias
    if a and _norm(a) in tcn:
        return True
    try:
        from .fm_crosswalk import FM_COLUMN_ALIASES_BY_TABLE
        a = (FM_COLUMN_ALIASES_BY_TABLE.get(table) or {}).get(n)   # FM platform alias
        if a and _norm(a) in tcn:
            return True
    except Exception:
        pass
    try:
        from .cmms_aliases import get_cmms_alias
        hit = get_cmms_alias(source_col)            # CMMS/FM canonical alias
        if hit and _norm(hit[0]) in tcn:
            return True
    except Exception:
        pass
    return False


def rank_target_tables(
    source_columns,
    *,
    tables: dict | None = None,
    top_k: int = 3,
    min_pct: float = 0.30,
    min_hits: int = 2,
) -> list[dict]:
    """Rank candidate destination tables by column-overlap with `source_columns`.

    Returns up to top_k dicts (best first):
        {"table", "mapped", "total", "pct"}   pct = mapped / total (0..1)
    Only tables clearing min_pct OR min_hits are returned. Empty if nothing fits.
    """
    cols = [str(c) for c in (source_columns or []) if str(c).strip()]
    total = len(cols)
    if not total:
        return []
    tabs = tables or TABLES
    scored = []
    for table, real_cols in tabs.items():
        if table in _NON_DOMAIN or not real_cols:
            continue
        tcn = {_norm(c): c for c in real_cols}
        idf = _id_forms(table)
        mapped = sum(1 for c in cols if _column_hits(c, table, tcn, idf))
        if mapped == 0:
            continue
        pct = mapped / total
        if pct >= min_pct or mapped >= min_hits:
            scored.append({"table": table, "mapped": mapped, "total": total, "pct": round(pct, 2)})
    # best first: more columns mapped, then higher %, then fewer table columns (tighter fit)
    scored.sort(key=lambda r: (r["mapped"], r["pct"], -len(tabs[r["table"]])), reverse=True)
    return scored[:top_k]
