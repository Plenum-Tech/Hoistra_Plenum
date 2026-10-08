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
    # the decisions
    "accepted", "reassigned", "overridden", "rejected", "approved_on_confirmation",
    # the steps before one: the check ran and was clean, the check ran and held the write,
    # the uploader answered the question it asked
    "validated", "held", "clarified",
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
    case_id: UUID | None = None,
    commit: bool = True,
) -> str:
    if outcome not in OUTCOMES:
        raise ValueError(f"unknown outcome {outcome!r}; one of {sorted(OUTCOMES)}")
    new_id = (
        await session.execute(
            text("""INSERT INTO plenum_cafm.ingestion_audit_events
                        (organization_id, actor_user_id, actor_role, document_name, document_id,
                         building_id, reassigned_to_building_id, warning, explanation, outcome,
                         approved_by, detail, case_id)
                    VALUES (:o, :a, :r, :dn, :did, :b, :rb, :w, :x, :oc, :ap, CAST(:d AS jsonb),
                            :case)
                    RETURNING id::text"""),
            {"o": organization_id, "a": actor_user_id, "r": actor_role, "dn": document_name,
             "did": document_id, "b": building_id, "rb": reassigned_to_building_id,
             "w": warning, "x": explanation, "oc": outcome, "ap": approved_by,
             "d": json.dumps(detail or {}, default=str), "case": case_id},
        )
    ).scalar()
    if commit:
        await session.commit()
    return str(new_id)


#: The roles a caller may filter on. "admin" means admin-or-superadmin, which is the line
#: the trail itself draws when it labels a row — a superadmin reading as an admin is an
#: admin to whoever reads the entry. Anything outside this set is refused rather than
#: ignored: ignoring it would hand the unfiltered company trail back to someone who asked
#: for a subset, and being wrong in the direction of MORE data is the wrong direction.
ACTOR_ROLES = frozenset({"admin", "user"})

#: One FROM for all four statements. Rows, total, per-outcome tally and actor roster must be
#: narrowed by exactly the same predicates: a filter that reaches the rows but not the
#: tallies puts another company's count — or another company's name — on screen without a
#: single row of theirs being listed.
_FROM = """
          FROM plenum_cafm.ingestion_audit_events e
          LEFT JOIN plenum_cafm.users u  ON u.id = e.actor_user_id
          LEFT JOIN plenum_cafm.users ap ON ap.id = e.approved_by
          LEFT JOIN plenum_cafm.buildings b  ON b.building_id = e.building_id
          LEFT JOIN plenum_cafm.buildings rb ON rb.building_id = e.reassigned_to_building_id
"""


async def list_events(
    session: AsyncSession,
    *,
    organization_id: UUID,
    outcome: str | None = None,
    building_ids: list[UUID] | None = None,
    building_id: UUID | None = None,
    actor_user_id: UUID | None = None,
    actor_role: str | None = None,
    q: str | None = None,
    since: Any = None,
    until: Any = None,
    limit: int = 100,
    offset: int = 0,
) -> dict[str, Any]:
    """The trail for one company, newest first, with the names a reader needs beside the ids.

    ``building_ids`` is the caller's ALLOCATION — a plain user reading their own trail sees
    only the buildings they hold, an admin passes None and sees the company. ``building_id``
    is something else: a filter the reader chose. The two are kept apart on purpose, because
    a filter may only narrow what the allocation already allows. Neither reaches past
    ``organization_id``, which is on every statement below without exception.

    The two derived tallies each leave out their own dimension. ``by_outcome`` ignores the
    outcome filter, so a chip keeps telling you what pressing a different one would give;
    ``actors`` ignores the person filter, so the picker's counts survive picking a name.
    """
    if outcome and outcome not in OUTCOMES:
        return {"ok": False, "error": f"unknown outcome {outcome!r}",
                "outcomes": sorted(OUTCOMES)}
    if actor_role and str(actor_role) not in ACTOR_ROLES:
        return {"ok": False, "error": f"unknown role {actor_role!r}",
                "roles": sorted(ACTOR_ROLES)}

    # The company clause is first and is never conditional.
    base = ["e.organization_id = :o"]
    params: dict[str, Any] = {"o": organization_id}
    if building_ids is not None:
        # Both the building it went to and the one it was moved from count as "involving".
        base.append("(e.building_id = ANY(CAST(:b AS uuid[])) "
                    "OR e.reassigned_to_building_id = ANY(CAST(:b AS uuid[])))")
        params["b"] = [str(x) for x in building_ids]
    if building_id:
        base.append("(e.building_id = :bid OR e.reassigned_to_building_id = :bid)")
        params["bid"] = str(building_id)
    if q and str(q).strip():
        # The only free text a caller controls, and it is bound, never interpolated. ILIKE
        # against the columns a reader can actually see on the row: searching one the page
        # does not show returns rows for a reason nobody could work out.
        base.append("(e.document_name ILIKE :q OR u.full_name ILIKE :q OR u.email ILIKE :q "
                    "OR b.name ILIKE :q OR rb.name ILIKE :q)")
        params["q"] = "%" + str(q).strip() + "%"
    if since is not None:
        base.append("e.occurred_at >= :since")
        params["since"] = since
    if until is not None:
        # Half-open, so a boundary row is not counted in two days at once.
        base.append("e.occurred_at < :until")
        params["until"] = until

    actor_sql: list[str] = []
    actor_params: dict[str, Any] = {}
    if actor_user_id:
        actor_sql.append("e.actor_user_id = :au")
        actor_params["au"] = str(actor_user_id)
    if actor_role:
        actor_sql.append("e.actor_role = ANY(:ar)")
        actor_params["ar"] = ["admin", "superadmin"] if actor_role == "admin" else ["user"]

    outcome_sql: list[str] = []
    outcome_params: dict[str, Any] = {}
    if outcome:
        outcome_sql.append("e.outcome = :oc")
        outcome_params["oc"] = outcome

    def where(*groups: list[str]) -> str:
        return " AND ".join(base + [clause for g in groups for clause in g])

    full = where(actor_sql, outcome_sql)
    full_params = {**params, **actor_params, **outcome_params}

    rows = (await session.execute(
        text(f"""SELECT e.id::text, e.occurred_at, e.outcome, e.document_name,
                        e.document_id::text AS document_id, e.warning, e.explanation, e.detail,
                        e.actor_user_id::text AS actor_user_id, e.actor_role,
                        u.full_name AS actor_name, u.email AS actor_email,
                        e.building_id::text AS building_id, b.name AS building_name,
                        e.reassigned_to_building_id::text AS reassigned_to_building_id,
                        rb.name AS reassigned_to_building_name,
                        e.approved_by::text AS approved_by, ap.full_name AS approved_by_name
                   {_FROM}
                  WHERE {full}
                  ORDER BY e.occurred_at DESC
                  LIMIT :lim OFFSET :off"""),
        {**full_params, "lim": max(1, min(limit, 500)), "off": max(0, offset)},
    )).mappings().all()
    total = (await session.execute(
        text(f"SELECT count(*) {_FROM} WHERE {full}"), full_params,
    )).scalar()
    counts = (await session.execute(
        text(f"""SELECT e.outcome, count(*) AS n {_FROM}
                  WHERE {where(actor_sql)} GROUP BY e.outcome"""),
        {**params, **actor_params},
    )).mappings().all()
    actors = (await session.execute(
        text(f"""SELECT e.actor_user_id::text AS actor_user_id, u.full_name AS actor_name,
                        e.actor_role, count(*) AS n {_FROM}
                  WHERE {where(outcome_sql)}
                  GROUP BY e.actor_user_id, u.full_name, e.actor_role
                  ORDER BY count(*) DESC"""),
        {**params, **outcome_params},
    )).mappings().all()
    return {
        "ok": True,
        "count": int(total or 0),
        "by_outcome": {r["outcome"]: int(r["n"]) for r in counts},
        "outcomes": sorted(OUTCOMES),
        "roles": sorted(ACTOR_ROLES),
        "actors": [{"user_id": r["actor_user_id"], "name": r["actor_name"],
                    "role": r["actor_role"], "count": int(r["n"])} for r in actors],
        "entries": [dict(r, occurred_at=r["occurred_at"].isoformat()) for r in rows],
    }
