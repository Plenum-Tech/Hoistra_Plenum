"""Feature 7 F7-5 — sanctity evidence integration.

``validate_entity_relationship`` no longer needs the caller to hand-supply evidence: it
GATHERS it. For a WO↔asset link it pulls the work-order row and the asset row from the
migrated ``plenum_cafm`` tables (the structured evidence — descriptions, location, asset
name), and enriches each with the doc-rag semantic layer (``row_semantic_index.semantic_text``,
the per-row vector representation) when that table is reachable. Both feed the pure
:func:`udr.sanctity.validate_relationship`.

All table/column names are validated before any SQL; values are parameterised. The semantic
enrichment degrades gracefully (skipped if doc-rag's table isn't in this database).
"""

from __future__ import annotations

from .activity_persist import _run_in_session
from .instance_cloud import is_safe_identifier
from .sanctity import validate_relationship

_SCHEMA = "plenum_cafm"


async def _resolve_pk(s, run_id, table) -> str | None:
    """The table's primary-key column from the run's stored metadata (UdrRunGraph)."""
    if run_id is None:
        return None
    from sqlalchemy import select

    from ..models.migration import UdrRunGraph

    rg = (await s.execute(select(UdrRunGraph).where(UdrRunGraph.run_id == run_id))).scalar_one_or_none()
    for t in (rg.table_metadata or []) if rg else []:
        if isinstance(t, dict) and t.get("table") == table:
            pk = t.get("primary_key") or []
            return pk[0] if pk else None
    return None


async def _fetch_row(s, table, pk_col, pk_value) -> dict | None:
    """One row from a migrated ``plenum_cafm`` table (validated identifiers, parameterised value)."""
    if not (is_safe_identifier(table) and is_safe_identifier(pk_col)):
        return None
    from sqlalchemy import text

    try:
        q = text(f'SELECT * FROM {_SCHEMA}."{table}" WHERE "{pk_col}" = :id LIMIT 1')
        r = (await s.execute(q, {"id": pk_value})).mappings().first()
        return dict(r) if r is not None else None
    except Exception:
        return None


async def _fetch_semantic_text(s, table, pk_value) -> str | None:
    """The doc-rag per-row semantic text, if ``row_semantic_index`` is reachable. No dynamic
    identifiers (fixed table + bound values); skipped gracefully when absent."""
    from sqlalchemy import text

    try:
        q = text(
            "SELECT semantic_text FROM row_semantic_index "
            "WHERE source_table = :t AND row_pk = :pk LIMIT 1"
        )
        r = (await s.execute(q, {"t": str(table), "pk": str(pk_value)})).first()
        return r[0] if r else None
    except Exception:
        return None


def _row_evidence(row: dict | None, semantic_text: str | None) -> str:
    parts = [str(v) for v in (row or {}).values() if v is not None]
    if semantic_text:
        parts.append(semantic_text)
    return " ".join(parts)


async def validate_entity_relationship(
    *,
    wo_table: str,
    wo_id,
    asset_table: str,
    asset_id,
    wo_pk: str | None = None,
    asset_pk: str | None = None,
    run_id: str | None = None,
    organization_id=None,
    record: bool = False,
    session=None,
) -> dict:
    """Gather a WO + asset's evidence from the migrated data (+ semantic layer) and validate
    the link's plausibility. Optionally records an escalatable flag when implausible. Never
    mutates the source data."""

    async def _do(s):
        wpk = wo_pk or await _resolve_pk(s, run_id, wo_table)
        apk = asset_pk or await _resolve_pk(s, run_id, asset_table)
        wo_row = await _fetch_row(s, wo_table, wpk, wo_id) if wpk else None
        asset_row = await _fetch_row(s, asset_table, apk, asset_id) if apk else None

        wo_sem = await _fetch_semantic_text(s, wo_table, wo_id)
        asset_sem = await _fetch_semantic_text(s, asset_table, asset_id)

        wo_evidence = _row_evidence(wo_row, wo_sem)
        asset_evidence = _row_evidence(asset_row, asset_sem)
        verdict = validate_relationship(wo_evidence, asset_evidence)

        out = {
            "verdict": verdict,
            "wo": {"table": wo_table, "id": wo_id, "found": wo_row is not None,
                   "semantic_evidence": wo_sem is not None},
            "asset": {"table": asset_table, "id": asset_id, "found": asset_row is not None,
                      "semantic_evidence": asset_sem is not None},
        }
        if record and not verdict["plausible"]:
            from .actions_persist import record_sanctity_flag

            out["flag"] = await record_sanctity_flag(
                verdict, wo_id=str(wo_id), asset_id=str(asset_id),
                organization_id=organization_id, session=s,
            )
        return out

    return await _run_in_session(_do, session)
