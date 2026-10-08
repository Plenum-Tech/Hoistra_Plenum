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


#: The unified store's data tables — what a company's "UDR data" is counted over (Hussain,
#: 29 Sep 2026: "rows in the UDR"). The workbook migration's destination tables plus
#: documents, floors and locations. `contracts` is a view over vendor_contracts, so counting it
#: would count the same rows twice.
UDR_TABLES: tuple[str, ...] = (
    "sites", "buildings", "building_sections", "floors", "locations", "vendors",
    "vendor_contracts", "technicians", "assets", "asset_reading_bands", "asset_readings",
    "maintenance_plans", "ppm_visits", "work_orders", "inspections", "spare_parts",
    "energy_meters", "meter_readings", "compliance_certificates", "documents",
)

#: The link columns a row can reach its company by, in order, and the set of the company's ids
#: each must be in (a CTE below), or None for a column that names the company itself.
_LINKS: tuple[tuple[str, str | None], ...] = (
    ("organization_id", None), ("org_id", None), ("building_id", "b"), ("section_id", "s"),
    ("asset_id", "a"), ("meter_id", "m"), ("vendor_id", "v"),
)
#: The CTEs, in dependency order: name -> (table, its id column).
_SETS: tuple[tuple[str, str, str], ...] = (
    ("b", "buildings", "building_id"), ("s", "building_sections", "section_id"),
    ("a", "assets", "id"), ("m", "energy_meters", "id"), ("v", "vendors", "id"),
)


def _predicates(table: str, cols: set[str], sets: set[str]) -> str | None:
    """How a row of ``table`` reaches the company, as one SQL condition over alias x.

    A row's own company column decides for it; only a row that carries NO company is placed
    by its building, section, asset, meter or vendor. OR-ing the two counted another company's
    row that pointed at this company's building or vendor (re-review, 29 Sep 2026). None when
    the table has no way to reach a company.
    """
    own = {t: n for n, t, _c in _SETS}.get(table)
    own_cols = [c for c, via in _LINKS if via is None and c in cols]
    links = [f"x.{c}::text IN (SELECT id FROM {via})" for c, via in _LINKS
             if via is not None and c in cols and via in sets and via != own]
    mine = " OR ".join(f"x.{c}::text = :o" for c in own_cols)
    if own_cols and links:
        empty = " AND ".join(f"x.{c} IS NULL" for c in own_cols)
        return f"({mine}) OR ({empty} AND ({' OR '.join(links)}))"
    if own_cols:
        return f"({mine})"
    return " OR ".join(links) if links else None


async def udr_rows(session: AsyncSession, organization_id: UUID) -> dict[str, Any]:
    """The rows the company holds across UDR_TABLES, per table, in one query.

    Each table is reached by whichever link it has — its own company column, or its building,
    section, asset, meter or vendor — and a row counts once however many of its links point at
    the company. The columns are read from the database, not assumed: production carries more
    tables and columns than the schema file (a table with no link to a company is named, not
    guessed at). A count that cannot be made is None, never a zero.
    """
    try:
        async with session.begin_nested():
            rows = (await session.execute(text("""
                SELECT c.table_name, c.column_name, c.data_type
                  FROM information_schema.columns c
                  JOIN information_schema.tables t
                    ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                 WHERE c.table_schema = 'plenum_cafm' AND t.table_type = 'BASE TABLE'
                   AND c.table_name = ANY(:tables)"""), {"tables": list(UDR_TABLES)})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("usage.udr_columns_unreadable", error=str(exc)[:200])
        return {"rows": None, "tables": {}, "unlinked": []}
    cols: dict[str, set[str]] = {}
    for r in rows:
        # A company column that is not a uuid or text (sites.organization_id is an integer in
        # the schema file) cannot name a uuid company: it is not a link, and a table with no
        # other link is named unlinked rather than silently counted 0.
        if r["column_name"] in ("organization_id", "org_id") and str(r.get("data_type") or "").lower() \
                not in ("uuid", "text", "character varying", "character"):
            continue
        cols.setdefault(r["table_name"], set()).add(r["column_name"])

    ctes, sets = [], set()
    for name, table, idcol in _SETS:
        if table in cols and idcol in cols[table]:
            cond = _predicates(table, cols[table], sets)
            if cond:
                ctes.append(f"{name} AS (SELECT x.{idcol}::text AS id FROM plenum_cafm.{table} x "
                            f"WHERE {cond})")
                sets.add(name)
    parts, unlinked = [], []
    for table in UDR_TABLES:
        if table not in cols:
            continue
        cond = _predicates(table, cols[table], sets)
        if not cond:
            unlinked.append(table)
            continue
        parts.append(f"SELECT '{table}' AS t, count(*) AS n FROM plenum_cafm.{table} x "
                     f"WHERE {cond}")
    if not parts:
        return {"rows": 0, "tables": {}, "unlinked": unlinked}
    sql = (("WITH " + ", ".join(ctes) + " ") if ctes else "") + " UNION ALL ".join(parts)
    try:
        async with session.begin_nested():
            counted = (await session.execute(text(sql), {"o": str(organization_id)})).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("usage.udr_rows_unreadable", error=str(exc)[:200])
        return {"rows": None, "tables": {}, "unlinked": unlinked}
    per = {r["t"]: int(r["n"] or 0) for r in counted if int(r["n"] or 0)}
    return {"rows": sum(per.values()), "tables": per, "unlinked": unlinked}


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


async def company_usage(session: AsyncSession, organization_id: UUID,
                        with_udr: bool = True) -> dict[str, Any]:
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
    # c.country_code, qualified and deliberately the CERTIFICATE's. Unqualified this was
    # ambiguous — both tables carry the column — and the endpoint returned a 500 the moment a
    # company had one certificate. Qualifying it to the building would have fixed the crash and
    # answered wrongly: buildings.country_code is null on every row in both databases, because a
    # building records its country on plenum_cafm.locations. The console would then have shown a
    # certificate count beside an empty country list and looked merely uninformative.
    #
    # The certificate's own country is the right figure here regardless: it names the regime the
    # certificate was issued under, which is what a count of certificates is worth knowing by.
    # Counted by the certificate's own company, as the Compliance page counts them. Through
    # the company's buildings (as this was), every vendor certificate — they carry no building
    # — and every certificate filed without one was missing (29 Sep 2026).
    certs = (await q(text("""
        SELECT count(*) AS n,
               array_remove(array_agg(DISTINCT c.country_code), NULL) AS countries
          FROM plenum_cafm.compliance_certificates c
         WHERE c.organization_id = :o OR c.org_id = :o"""), p)).mappings().first()
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
               count(*) FILTER (WHERE kind = 'api_request') AS api_all,
               count(*) FILTER (WHERE kind = 'query') AS queries,
               count(*) FILTER (WHERE kind = 'ingest') AS ingests,
               max(occurred_at) AS last_activity
          FROM plenum_cafm.platform_usage_events WHERE organization_id = :o"""), p)).mappings().first()
    # UDR data: the rows the company holds across the unified store (udr_rows). It was the
    # extracted text of documents only, so 289,604 migrated rows read 0 MB.
    # with_udr=False: the company admin's card shows no UDR figure and should not pay for a
    # scan of every data table on each read.
    udr = (await udr_rows(session, organization_id) if with_udr
           else {"rows": None, "tables": {}, "unlinked": []})
    unreadable = [] if (udr["rows"] is not None or not with_udr) else ["rows in the UDR"]
    # Nothing on the platform writes an api_request event yet: until one is written the count
    # is None ("not recorded"), not a zero that reads like a measurement.
    api_recorded = int(usage["api_all"] or 0) > 0
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
        "udr_rows": udr["rows"],
        "udr_tables": udr["tables"],
        "udr_unlinked": udr["unlinked"],
        "compliance_certificates": int(certs["n"] or 0),
        "certificate_countries": sorted(str(c) for c in (certs["countries"] or [])),
        "api_requests_30d": int(usage["api_30d"] or 0) if api_recorded else None,
        "api_requests_recorded": api_recorded,
        "queries": int(usage["queries"] or 0),
        "ingests": int(usage["ingests"] or 0),
        "credits_this_month": float(usage["credits_month"] or 0),
        "credits_total": float(usage["credits_total"] or 0),
        "users": {k: int(users[k] or 0) for k in ("total", "active", "invited", "can_ingest")},
        "tariff": TARIFF,
        "unreadable": unreadable,
        "counted_from": {
            "buildings_created": "buildings.organization_id",
            "hoist_graphs": "buildings with at least one documents/assets/floors row",
            "udr_rows": "rows in UDR_TABLES reached by company, building, section, asset, meter or vendor",
            "compliance_certificates": "compliance_certificates.organization_id / org_id",
            "api_requests": "platform_usage_events kind=api_request (none written yet)",
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
