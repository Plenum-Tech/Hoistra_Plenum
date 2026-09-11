"""The ingestion audit trail: who submitted what, what they were warned, what they said, and
who approved the outcome.

Every flagged ingestion, clarification, override and approval — for users and admins alike.
If a document is ever questioned, this record answers it without anyone having to
reconstruct a chat transcript.

Outcomes, as the console filters them:

    accepted                  the document went in as submitted
    reassigned                it was filed against a different building than proposed
    overridden                a warning was shown and an admin proceeded anyway
    rejected                  it was refused
    approved_on_confirmation  a bulk pack went in after an explicit confirmation step

Written by the ingest path and read by the company admin. Nothing here is ever updated or
deleted: an audit entry that can be edited is not one.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

OUTCOMES = frozenset({
    "accepted", "reassigned", "overridden", "rejected", "approved_on_confirmation",
})


async def record(
    session: AsyncSession,
    *,
    organization_id: UUID | None,
    actor_user_id: UUID | None,
    actor_role: str | None,
    outcome: str,
    document_name: str | None = None,
    document_id: UUID | None = None,
    building_id: UUID | None = None,
    reassigned_to_building_id: UUID | None = None,
    warning: str | None = None,
    explanation: str | None = None,
    approved_by: UUID | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> str:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; one of {sorted(OUTCOMES)}")
    new_id = (
        await session.execute(
            text("""INSERT INTO plenum_cafm.ingestion_audit_events
                        (organization_id, actor_user_id, actor_role, document_name, document_id,
                         building_id, reassigned_to_building_id, warning, explanation, outcome,
                         approved_by, detail)
                    VALUES (:o, :a, :r, :dn, :did, :b, :rb, :w, :x, :oc, :ap, CAST(:d AS jsonb))
                    RETURNING id::text"""),
            {"o": organization_id, "a": actor_user_id, "r": actor_role, "dn": document_name,
             "did": document_id, "b": building_id, "rb": reassigned_to_building_id,
             "w": warning, "x": explanation, "oc": outcome, "ap": approved_by,
             "d": json.dumps(detail or {}, default=str)},
        )
    ).scalar()
    if commit:
        await session.commit()
    return str(new_id)


async def list_events(
    session: AsyncSession,
    *,
    organization_id: UUID,
    outcome: str | None = None,
    building_ids: list[UUID] | None = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """The trail for one company, newest first, with the names a reader needs beside the ids.

    ``building_ids`` narrows to an allocation — a plain user reading their own trail sees
    only the buildings they hold. An admin passes None and sees the company.
    """
    if outcome and outcome not in OUTCOMES:
        return {"ok": False, "error": f"unknown outcome {outcome!r}",
                "outcomes": sorted(OUTCOMES)}
    where = ["e.organization_id = :o"]
    params: dict[str, Any] = {"o": organization_id, "lim": max(1, min(limit, 500)),
                              "off": max(0, offset)}
    if outcome:
        where.append("e.outcome = :oc")
        params["oc"] = outcome
    if building_ids is not None:
        # Both the building it went to and the one it was moved from count as "involving".
        where.append("(e.building_id = ANY(CAST(:b AS uuid[])) "
                     "OR e.reassigned_to_building_id = ANY(CAST(:b AS uuid[])))")
        params["b"] = [str(x) for x in building_ids]
    clause = " AND ".join(where)
    rows = (await session.execute(
        text(f"""SELECT e.id::text, e.occurred_at, e.outcome, e.document_name,
                        e.document_id::text AS document_id, e.warning, e.explanation, e.detail,
                        e.actor_user_id::text AS actor_user_id, e.actor_role,
                        u.full_name AS actor_name, u.email AS actor_email,
                        e.building_id::text AS building_id, b.name AS building_name,
                        e.reassigned_to_building_id::text AS reassigned_to_building_id,
                        rb.name AS reassigned_to_building_name,
                        e.approved_by::text AS approved_by, ap.full_name AS approved_by_name
                   FROM plenum_cafm.ingestion_audit_events e
                   LEFT JOIN plenum_cafm.users u  ON u.id = e.actor_user_id
                   LEFT JOIN plenum_cafm.users ap ON ap.id = e.approved_by
                   LEFT JOIN plenum_cafm.buildings b  ON b.building_id = e.building_id
                   LEFT JOIN plenum_cafm.buildings rb ON rb.building_id = e.reassigned_to_building_id
                  WHERE {clause}
                  ORDER BY e.occurred_at DESC
                  LIMIT :lim OFFSET :off"""),
        params,
    )).mappings().all()
    total = (await session.execute(
        text(f"SELECT count(*) FROM plenum_cafm.ingestion_audit_events e WHERE {clause}"),
        {k: v for k, v in params.items() if k not in ("lim", "off")},
    )).scalar()
    counts = (await session.execute(
        text("""SELECT outcome, count(*) AS n FROM plenum_cafm.ingestion_audit_events
                 WHERE organization_id = :o GROUP BY outcome"""),
        {"o": organization_id},
    )).mappings().all()
    return {
        "ok": True,
        "count": int(total or 0),
        "by_outcome": {r["outcome"]: int(r["n"]) for r in counts},
        "outcomes": sorted(OUTCOMES),
        "entries": [dict(r, occurred_at=r["occurred_at"].isoformat()) for r in rows],
    }
