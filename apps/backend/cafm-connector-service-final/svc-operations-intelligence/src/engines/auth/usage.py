"""What a company has done on the platform, and what it has cost.

Two readers. The super admin console shows every company with its credits this month, and
one company's usage card: buildings, hoist graphs, last activity, UDR volume, certificates
and their countries, API requests, credits, users. The company admin console shows the same
kind of thing per building and per user.

Almost all of it is counted from tables that already exist — buildings, documents,
compliance_certificates, users. The two figures that did not exist anywhere are activity
and credits, so platform_usage_events is the ledger for those: one row per query turn, per
ingest, per API request, with the credits it cost written at the time. A tariff change later
does not rewrite history.

The tariff is deliberately simple and deliberately visible: a query is 1 credit, an ingest
is 5, an API request is 0.1. Billing here is usage-led with no seat limit, exactly as the
console says, so users are not charged for — more users simply consume more.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: Credits per event kind. Changing these changes future rows only.
TARIFF: dict[str, float] = {"query": 1.0, "ingest": 5.0, "api_request": 0.1}


async def record_event(
    session: AsyncSession,
    *,
    kind: str,
    organization_id: UUID | None,
    user_id: UUID | None,
    building_id: UUID | None = None,
    detail: dict[str, Any] | None = None,
    commit: bool = True,
) -> float:
    """Write one usage row and return the credits it cost."""
    credits = float(TARIFF.get(kind, 0.0))
    await session.execute(
        text("""INSERT INTO plenum_cafm.platform_usage_events
                    (organization_id, user_id, building_id, kind, credits, detail)
                VALUES (:o, :u, :b, :k, :c, CAST(:d AS jsonb))"""),
        {"o": organization_id, "u": user_id, "b": building_id, "k": kind, "c": credits,
         "d": __import__("json").dumps(detail or {}, default=str)},
    )
    if commit:
        await session.commit()
    return credits


def _month_start() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


async def credits_by_company(session: AsyncSession) -> list[dict[str, Any]]:
    """Every company with its credits this month — the bar chart on the super admin console."""
    rows = (await session.execute(
        text("""SELECT o.id::text AS organization_id, o.name, o.country_code, o.lifecycle,
                       o.status, o.created_at,
                       COALESCE(u.credits, 0) AS credits_this_month,
                       u.last_activity
                  FROM plenum_cafm.organizations o
                  LEFT JOIN (
                        SELECT organization_id, sum(credits) AS credits,
                               max(occurred_at) AS last_activity
                          FROM plenum_cafm.platform_usage_events
                         WHERE occurred_at >= :m
                         GROUP BY organization_id) u ON u.organization_id = o.id
                 ORDER BY COALESCE(u.credits, 0) DESC, o.name"""),
        {"m": _month_start()},
    )).mappings().all()
    return [dict(r, credits_this_month=float(r["credits_this_month"] or 0)) for r in rows]


async def company_usage(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    """One company's usage card. Every figure names where it was counted from.

    Counted, never estimated. A figure this console cannot count is reported as null with
    its source named, so a zero always means "we looked and found none".
    """
    org = (await session.execute(
        text("""SELECT id::text, name, country_code, country, lifecycle, status, admin_email,
                       created_at, updated_at
                  FROM plenum_cafm.organizations WHERE id = :o"""),
        {"o": organization_id},
    )).mappings().first()
    if org is None:
        return {"ok": False, "error": "company_not_found"}

    q = session.execute
    p = {"o": organization_id, "m": _month_start()}

    buildings = (await q(text(
        "SELECT count(*) FROM plenum_cafm.buildings WHERE organization_id = :o"), p)).scalar()
    # A hoist graph is a building the graph can draw: one with at least one branch row. The
    # console says "one per hoisted building", and this is what hoisted means in the data.
    graphs = (await q(text("""
        SELECT count(DISTINCT b.building_id) FROM plenum_cafm.buildings b
         WHERE b.organization_id = :o
           AND (EXISTS (SELECT 1 FROM plenum_cafm.documents d WHERE d.building_id = b.building_id)
             OR EXISTS (SELECT 1 FROM plenum_cafm.assets a WHERE a.building_id = b.building_id)
             OR EXISTS (SELECT 1 FROM plenum_cafm.floors f WHERE f.building_id = b.building_id))
    """), p)).scalar()
    certs = (await q(text("""
        SELECT count(*) AS n, array_remove(array_agg(DISTINCT country_code), NULL) AS countries
          FROM plenum_cafm.compliance_certificates c
          JOIN plenum_cafm.buildings b ON b.building_id = c.building_id
         WHERE b.organization_id = :o"""), p)).mappings().first()
    users = (await q(text("""
        SELECT count(*) FILTER (WHERE lower(status) = 'active') AS active,
               count(*) FILTER (WHERE lower(status) = 'invited') AS invited,
               count(*) FILTER (WHERE can_ingest) AS can_ingest,
               count(*) AS total
          FROM plenum_cafm.users WHERE organization_id = :o"""), p)).mappings().first()
    usage = (await q(text("""
        SELECT COALESCE(sum(credits) FILTER (WHERE occurred_at >= :m), 0) AS credits_month,
               COALESCE(sum(credits), 0)                                    AS credits_total,
               count(*) FILTER (WHERE kind = 'api_request'
                                  AND occurred_at >= now() - interval '30 days') AS api_30d,
               count(*) FILTER (WHERE kind = 'query') AS queries,
               count(*) FILTER (WHERE kind = 'ingest') AS ingests,
               max(occurred_at) AS last_activity
          FROM plenum_cafm.platform_usage_events WHERE organization_id = :o"""), p)).mappings().first()
    # UDR volume: bytes of ingested source text is the closest thing to "data added" that
    # exists on a row. ingestion_documents has no size column, so this is the sum of
    # extracted text over the company's documents.
    udr = (await q(text("""
        SELECT COALESCE(sum(octet_length(COALESCE(i.final_json::text, ''))), 0) AS bytes,
               count(*) AS documents
          FROM plenum_cafm.ingestion_documents i
          JOIN plenum_cafm.documents d ON d.document_id = i.id
          JOIN plenum_cafm.buildings b ON b.building_id = d.building_id
         WHERE b.organization_id = :o"""), p)).mappings().first()
    last_update = (await q(text("""
        SELECT greatest(
            (SELECT max(updated_at) FROM plenum_cafm.buildings WHERE organization_id = :o),
            (SELECT max(d.uploaded_at) FROM plenum_cafm.documents d
               JOIN plenum_cafm.buildings b ON b.building_id = d.building_id
              WHERE b.organization_id = :o),
            (SELECT max(occurred_at) FROM plenum_cafm.platform_usage_events
              WHERE organization_id = :o))"""), p)).scalar()

    return {
        "ok": True,
        "company": dict(org),
        "buildings_created": int(buildings or 0),
        "hoist_graphs": int(graphs or 0),
        "last_activity": last_update.isoformat() if last_update else None,
        "udr_data_bytes": int(udr["bytes"] or 0),
        "udr_documents": int(udr["documents"] or 0),
        "compliance_certificates": int(certs["n"] or 0),
        "certificate_countries": sorted(str(c) for c in (certs["countries"] or [])),
        "api_requests_30d": int(usage["api_30d"] or 0),
        "queries": int(usage["queries"] or 0),
        "ingests": int(usage["ingests"] or 0),
        "credits_this_month": float(usage["credits_month"] or 0),
        "credits_total": float(usage["credits_total"] or 0),
        "users": {k: int(users[k] or 0) for k in ("total", "active", "invited", "can_ingest")},
        "tariff": TARIFF,
        "counted_from": {
            "buildings_created": "buildings.organization_id",
            "hoist_graphs": "buildings with at least one documents/assets/floors row",
            "udr_data_bytes": "octet_length(ingestion_documents.final_json) over the company's documents",
            "compliance_certificates": "compliance_certificates joined to the company's buildings",
            "credits": "platform_usage_events at the tariff in force when each row was written",
        },
    }


async def usage_by_user(session: AsyncSession, organization_id: UUID) -> dict[str, dict[str, Any]]:
    """Per-user counters for the Users & access table: queries, ingests, last active."""
    rows = (await session.execute(
        text("""SELECT user_id::text AS user_id,
                       count(*) FILTER (WHERE kind = 'query')  AS queries,
                       count(*) FILTER (WHERE kind = 'ingest') AS ingests,
                       max(occurred_at) AS last_active
                  FROM plenum_cafm.platform_usage_events
                 WHERE organization_id = :o AND user_id IS NOT NULL
                 GROUP BY user_id"""),
        {"o": organization_id},
    )).mappings().all()
    return {r["user_id"]: {"queries": int(r["queries"]), "ingests": int(r["ingests"]),
                          "last_active": r["last_active"].isoformat() if r["last_active"] else None}
            for r in rows}


async def usage_by_building(session: AsyncSession, organization_id: UUID) -> list[dict[str, Any]]:
    """Per-building counters for the company admin: what each building holds and costs."""
    rows = (await session.execute(
        text("""SELECT b.building_id::text AS building_id, b.name, b.building_code,
                       (SELECT count(*) FROM plenum_cafm.documents d WHERE d.building_id = b.building_id) AS documents,
                       (SELECT count(*) FROM plenum_cafm.compliance_certificates c WHERE c.building_id = b.building_id) AS certificates,
                       (SELECT count(*) FROM plenum_cafm.user_buildings ub WHERE ub.building_id = b.building_id) AS users_allocated,
                       (SELECT COALESCE(sum(credits), 0) FROM plenum_cafm.platform_usage_events e
                         WHERE e.building_id = b.building_id AND e.occurred_at >= :m) AS credits_this_month,
                       (SELECT max(occurred_at) FROM plenum_cafm.platform_usage_events e
                         WHERE e.building_id = b.building_id) AS last_activity
                  FROM plenum_cafm.buildings b
                 WHERE b.organization_id = :o
                 ORDER BY b.name"""),
        {"o": organization_id, "m": _month_start()},
    )).mappings().all()
    return [dict(r, credits_this_month=float(r["credits_this_month"] or 0),
                 last_activity=r["last_activity"].isoformat() if r["last_activity"] else None)
            for r in rows]
