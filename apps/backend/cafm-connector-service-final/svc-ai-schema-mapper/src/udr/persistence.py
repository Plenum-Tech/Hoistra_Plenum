"""Feature 7 — UDR Layer-3 persistence (relationship graph + edges).

Async upsert writers for the two Postgres metadata tables produced by a UDR run:
  - ``UdrRunGraph``          — one snapshot row per run (upsert on unique ``run_id``).
  - ``UdrEntityRelationship`` — one row per edge (upsert on ``uq_udr_rel_edge``).

Idempotent by design (ON CONFLICT DO UPDATE) so re-running the same migration updates the
canonical UDR incrementally rather than raising IntegrityError or duplicating edges. The
pure ``_rel_to_columns`` mapper is unit-testable; DB + model imports are lazy so the module
imports clean without DB config. Mirrors the session pattern in :mod:`udr.activity_persist`.
"""

from __future__ import annotations

from datetime import datetime

from .activity_persist import _run_in_session  # shared session helper (lazy DB import)


# ── pure: graph edge dict → UdrEntityRelationship column values ──────────────────
def _rel_to_columns(rel: dict, *, run_id: str, organization_id=None) -> dict:
    """Map one ``build_relationship_graph`` edge to ``UdrEntityRelationship`` columns.

    ``src_column`` / ``dst_column`` are coerced to ``""`` (never NULL): they are part of the
    ``uq_udr_rel_edge`` key, and Postgres treats NULLs as DISTINCT, which would defeat the
    idempotent ON CONFLICT upsert for any (degenerate) edge missing a column name.
    """
    return {
        "run_id": run_id,
        "organization_id": organization_id,
        "src_entity": rel.get("src_entity"),
        "src_column": rel.get("src_column") or "",
        "rel_type": rel.get("rel_type", "REFERENCES"),
        "dst_entity": rel.get("dst_entity"),
        "dst_column": rel.get("dst_column") or "",
        "provenance": rel.get("provenance", "schema"),
        "confidence": rel.get("confidence", 1.0) if rel.get("confidence") is not None else 1.0,
        "evidence": rel.get("evidence"),
    }


# ── write: per-run snapshot (upsert on run_id) ──────────────────────────────────
async def persist_run_graph(graph: dict, *, run_id: str, organization_id=None, session=None) -> str:
    """Upsert one ``UdrRunGraph`` snapshot (final table + column metadata + edge count)."""
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from ..models.migration import UdrRunGraph

    values = {
        "run_id": run_id,
        "organization_id": organization_id,
        "table_metadata": graph.get("tables", []),
        "column_metadata": graph.get("columns", []),
        "relationship_count": len(graph.get("relationships", [])),
    }

    async def _do(s):
        stmt = pg_insert(UdrRunGraph).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=["run_id"],
            set_={
                "organization_id": stmt.excluded.organization_id,
                "table_metadata": stmt.excluded.table_metadata,
                "column_metadata": stmt.excluded.column_metadata,
                "relationship_count": stmt.excluded.relationship_count,
                "updated_at": datetime.utcnow(),
            },
        )
        await s.execute(stmt)
        return run_id

    return await _run_in_session(_do, session)


# ── write: relationship edges (idempotent upsert on the unique edge key) ─────────
async def persist_relationships(
    relationships: list[dict], *, run_id: str, organization_id=None, session=None
) -> int:
    """Bulk-upsert the relationship edges. Returns the number of edges written."""
    rows = [_rel_to_columns(r, run_id=run_id, organization_id=organization_id) for r in (relationships or [])]
    if not rows:
        return 0  # short-circuit before any DB import (keeps the module unit-testable)

    from sqlalchemy.dialects.postgresql import insert as pg_insert

    from ..models.migration import UdrEntityRelationship

    async def _do(s):
        for r in rows:
            stmt = pg_insert(UdrEntityRelationship).values(**r)
            stmt = stmt.on_conflict_do_update(
                constraint="uq_udr_rel_edge",
                set_={
                    "rel_type": stmt.excluded.rel_type,
                    "confidence": stmt.excluded.confidence,
                    "evidence": stmt.excluded.evidence,
                    "organization_id": stmt.excluded.organization_id,
                    "updated_at": datetime.utcnow(),
                },
            )
            await s.execute(stmt)
        return len(rows)

    return await _run_in_session(_do, session)


# ── read: all relationship edges for a run (Test-2 resolve, etc.) ───────────────
async def load_run_edges(run_id: str, *, organization_id=None, rel_type=None, session=None) -> list[dict]:
    """All ``UdrEntityRelationship`` edges for a run as plain dicts (optionally one rel_type)."""
    from sqlalchemy import select

    from ..models.migration import UdrEntityRelationship

    async def _do(s):
        stmt = select(UdrEntityRelationship).where(UdrEntityRelationship.run_id == run_id)
        if organization_id is not None:
            stmt = stmt.where(UdrEntityRelationship.organization_id == organization_id)
        if rel_type is not None:
            stmt = stmt.where(UdrEntityRelationship.rel_type == rel_type)
        rows = (await s.execute(stmt)).scalars().all()
        return [
            {
                "src_entity": r.src_entity,
                "src_column": r.src_column,
                "dst_entity": r.dst_entity,
                "dst_column": r.dst_column,
                "rel_type": r.rel_type,
                "provenance": r.provenance,
                "confidence": r.confidence,
            }
            for r in rows
        ]

    return await _run_in_session(_do, session)


async def load_run_table_metadata(run_id: str, *, session=None) -> list[dict]:
    """The persisted ``UdrRunGraph.table_metadata`` for a run (PKs, column names, …)."""
    from sqlalchemy import select

    from ..models.migration import UdrRunGraph

    async def _do(s):
        row = (
            await s.execute(select(UdrRunGraph).where(UdrRunGraph.run_id == run_id))
        ).scalar_one_or_none()
        return list(row.table_metadata or []) if row else []

    return await _run_in_session(_do, session)


# ── read: the entity 'work cloud' (F7-3) from the persisted edges ───────────────
async def query_work_cloud(
    entity_type: str, *, run_id: str | None = None, organization_id=None, session=None
) -> dict:
    """Build one entity's work cloud from the persisted ``UdrEntityRelationship`` edges
    (both directions). Requires a ``run_id`` or ``organization_id`` scope — an unscoped call
    returns an empty cloud rather than merging edges across every tenant/run."""
    from .graph import build_work_cloud

    # cross-tenant / cross-run guard (mirrors list_activity / unread_summary)
    if run_id is None and organization_id is None:
        return build_work_cloud([], entity_type)

    from sqlalchemy import or_, select

    from ..models.migration import UdrEntityRelationship

    async def _do(s):
        stmt = select(UdrEntityRelationship).where(
            or_(
                UdrEntityRelationship.src_entity == entity_type,
                UdrEntityRelationship.dst_entity == entity_type,
            )
        )
        if run_id is not None:
            stmt = stmt.where(UdrEntityRelationship.run_id == run_id)
        if organization_id is not None:
            stmt = stmt.where(UdrEntityRelationship.organization_id == organization_id)
        rows = (await s.execute(stmt)).scalars().all()
        edges = [
            {
                "src_entity": r.src_entity,
                "src_column": r.src_column,
                "dst_entity": r.dst_entity,
                "dst_column": r.dst_column,
                "rel_type": r.rel_type,
                "provenance": r.provenance,
                "confidence": r.confidence,
            }
            for r in rows
        ]
        return build_work_cloud(edges, entity_type)

    return await _run_in_session(_do, session)


# ── read: INSTANCE-level work cloud (traverse FK edges into the live data rows) ──
async def query_instance_work_cloud(
    entity_type: str,
    entity_id,
    *,
    run_id: str | None = None,
    organization_id=None,
    limit: int = 50,
    session=None,
) -> dict:
    """Traverse one entity instance's relationships into the actual ``plenum_cafm`` data
    rows (parent rows it points at + child rows that reference it). Requires a scope
    (run_id / org). All table/column names are validated before being placed in SQL; values
    are parameterised. Per-related-table failures are skipped, never fatal."""
    from sqlalchemy import or_, select, text

    from ..models.migration import UdrEntityRelationship, UdrRunGraph
    from .instance_cloud import build_traversal_plan, is_safe_identifier

    empty = {"entity": entity_type, "entity_id": entity_id, "found": False, "related": []}
    if (run_id is None and organization_id is None) or not is_safe_identifier(entity_type):
        return empty

    lim = max(1, min(int(limit or 50), 500))

    async def _do(s):
        stmt = select(UdrEntityRelationship).where(
            or_(
                UdrEntityRelationship.src_entity == entity_type,
                UdrEntityRelationship.dst_entity == entity_type,
            )
        )
        if run_id is not None:
            stmt = stmt.where(UdrEntityRelationship.run_id == run_id)
        if organization_id is not None:
            stmt = stmt.where(UdrEntityRelationship.organization_id == organization_id)
        rows = (await s.execute(stmt)).scalars().all()
        edges = [
            {"src_entity": r.src_entity, "src_column": r.src_column,
             "dst_entity": r.dst_entity, "dst_column": r.dst_column}
            for r in rows
        ]

        # the entity's primary key — prefer the run's stored table metadata
        pk_column = None
        if run_id is not None:
            rg = (await s.execute(select(UdrRunGraph).where(UdrRunGraph.run_id == run_id))).scalar_one_or_none()
            for t in (rg.table_metadata or []) if rg else []:
                if isinstance(t, dict) and t.get("table") == entity_type:
                    pk = t.get("primary_key") or []
                    pk_column = pk[0] if pk else None
                    break

        plan = build_traversal_plan(edges, entity_type, pk_column=pk_column)
        pkc = plan["pk_column"]
        if not pkc:
            return {**empty, "reason": "primary key unknown for entity"}

        schema = "plenum_cafm"
        entity_row: dict = {}
        try:
            q = text(f'SELECT * FROM {schema}."{entity_type}" WHERE "{pkc}" = :id LIMIT 1')
            r0 = (await s.execute(q, {"id": entity_id})).mappings().first()
            entity_row = dict(r0) if r0 is not None else {}
        except Exception:
            entity_row = {}

        related: list[dict] = []

        # outbound: this entity's FK value -> parent rows
        for ob in plan["outbound"]:
            rt, vc, rpk = ob["related_table"], ob["via_column"], ob["related_pk_column"]
            fk_value = entity_row.get(vc)
            if fk_value is None:
                continue
            try:
                q = text(f'SELECT * FROM {schema}."{rt}" WHERE "{rpk}" = :v LIMIT {lim}')
                rrows = (await s.execute(q, {"v": fk_value})).mappings().all()
                related.append({"related_entity": rt, "direction": "out", "via_column": vc,
                                "rows": [dict(x) for x in rrows]})
            except Exception:
                continue

        # inbound: child rows whose FK references this entity's PK value
        for ib in plan["inbound"]:
            rt, fkc = ib["related_table"], ib["related_fk_column"]
            try:
                q = text(f'SELECT * FROM {schema}."{rt}" WHERE "{fkc}" = :id LIMIT {lim}')
                rrows = (await s.execute(q, {"id": entity_id})).mappings().all()
                related.append({"related_entity": rt, "direction": "in", "via_column": fkc,
                                "rows": [dict(x) for x in rrows]})
            except Exception:
                continue

        return {"entity": entity_type, "entity_id": entity_id, "found": bool(entity_row), "related": related}

    return await _run_in_session(_do, session)


# ── convenience: persist a whole run (graph snapshot + edges) in one unit ────────
async def persist_udr_run(result, *, organization_id=None, session=None) -> dict:
    """Persist a :class:`udr.pipeline.UdrRunResult`'s graph + edges. Commits as one unit
    when no session is passed (the caller must commit when it passes its own session)."""

    async def _do(s):
        await persist_run_graph(
            result.graph, run_id=result.run_id, organization_id=organization_id, session=s
        )
        n = await persist_relationships(
            result.graph.get("relationships", []),
            run_id=result.run_id,
            organization_id=organization_id,
            session=s,
        )
        return {"run_id": result.run_id, "relationship_count": n}

    return await _run_in_session(_do, session)
