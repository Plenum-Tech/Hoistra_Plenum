"""One building per site — backfill plenum_cafm.buildings from plenum_cafm.sites.

A site is an address; a building is a structure on it. Where a portfolio has one building
per site the two are the same thing, and this creates that building so the Buildings screen
(which roots on the graph) has something to show.

It creates records that did not exist before, so it defaults to a dry run and reports
exactly what it would do — the same contract as ``compliance/site_links.backfill_site_links``.

What it will not do:

* **Invent a country.** A site with no country gets a building with no country, which reads
  against no regulation pack. Pass ``country_code`` to state one for the whole batch; the
  report says how many rows it was applied to. Guessing "UK" from a region called
  "South East" would put a benchmark standard on a building on no evidence.
* **Overwrite anything.** A site that already has a building is skipped and counted.
* **Collide.** If the derived building id is taken by a building belonging to a different
  site, the row is reported as a conflict and skipped rather than merged.
"""
from __future__ import annotations

import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ..compliance.site_links import sites_shape
from .building_rollup import graph_shape

log = get_logger(__name__)

# Rows per commit during a real write. A single uncommitted transaction spanning hundreds
# of sites means one dropped connection anywhere in the run discards every row that had
# already succeeded — observed live, on a real backfill of 626 sites. Small enough that a
# drop costs little; large enough that a portfolio this size does not spend the run in
# round trips to the database for commits alone.
_COMMIT_BATCH = 25

# Columns copied straight across when the sites table has them: (sites column, buildings column).
_CARRY: tuple[tuple[str, str], ...] = (
    ("country", "country"),
    ("country_code", "country_code"),
    ("state", "state"),
    ("region", "state"),
    ("city", "city"),
    ("postcode", "postcode"),
    ("use_type", "use_type"),
    ("site_type", "use_type"),
    ("status", "status"),
    ("floors", "floors_recorded"),
    ("gfa_sqm", "gfa_sqm_recorded"),
    ("eui_kwh_per_m2", "eui_kwh_per_m2"),
    ("benchmark_kwh_per_m2", "benchmark_kwh_per_m2"),
    ("benchmark_standard", "benchmark_standard"),
    ("benchmark_standing", "benchmark_standing"),
    ("benchmark_standing_note", "benchmark_standing_note"),
    ("metering_route", "metering_route"),
    ("metering_granularity", "metering_granularity"),
    ("hoist_score", "hoist_score"),
)


def derive_building_id(site_id: str) -> str:
    """S-01 → B-01. Any other shape is prefixed rather than rewritten.

    The rule is deliberately dull: a reader must be able to look at a building id and know
    which site it came from without a lookup table, and the transformation must be the same
    every run so a second pass is a no-op rather than a second building.
    """
    s = str(site_id or "").strip()
    if not s:
        return ""
    if re.match(r"^S[-_]", s, re.I):
        return "B-" + s[2:]
    if s.upper().startswith("B-"):
        return s
    return "B-" + s


def plan_row(
    site: dict[str, Any],
    *,
    existing_by_site: dict[str, str],
    existing_ids: dict[str, str],
    country_code: str | None,
) -> dict[str, Any]:
    """What would happen to one site. Pure — the whole plan is testable without a database."""
    site_id = str(site.get("site_id") or "").strip()
    name = (site.get("site_name") or site.get("name") or "").strip() or None
    bid = derive_building_id(site_id)

    if site_id in existing_by_site:
        return {"site_id": site_id, "action": "skip", "reason": "site already has a building",
                "building_id": existing_by_site[site_id]}
    owner = existing_ids.get(bid)
    if owner is not None and owner != site_id:
        return {"site_id": site_id, "action": "conflict", "building_id": bid,
                "reason": f"building {bid} already belongs to site {owner}"}

    # `bid` is the human-readable code ("B-01"), reported at the top level for every action
    # (create/skip/conflict) so a caller always knows which code a site maps to. It is NOT
    # the real primary key: plenum_cafm.buildings.building_id is a uuid with its own
    # database default (gen_random_uuid()), so the code belongs in building_code — the
    # column that exists for exactly this. Writing it into building_id fails at the
    # database on every row ("invalid UUID 'B-01'"); this is what a live run against 626
    # sites found, creating zero buildings.
    values: dict[str, Any] = {"building_code": bid, "site_id": site_id, "name": name}
    for src, dst in _CARRY:
        v = site.get(src)
        if v not in (None, "") and values.get(dst) in (None, ""):
            values[dst] = v
    if country_code and not values.get("country_code"):
        values["country_code"] = country_code
        values["_country_from_parameter"] = True

    missing = [f for f in ("name", "country_code", "use_type", "floors_recorded",
                           "gfa_sqm_recorded") if not values.get(f)]
    return {"site_id": site_id, "action": "create", "building_id": bid,
            "values": values, "missing": missing}


async def _load_sites(session: AsyncSession, limit: int) -> list[dict[str, Any]]:
    shape = await sites_shape(session)
    cols = shape["columns"]
    if not shape["usable"]:
        return []
    key = "site_id" if "site_id" in cols else shape["key_columns"][0]
    want = ["site_name", "name", "building_name"] + [c for c, _ in _CARRY]
    select = [f"{key}::text AS site_id"] + [
        f"{c}::text AS {c}" for c in dict.fromkeys(want) if c in cols and c != key
    ]
    sql = f"SELECT {', '.join(select)} FROM plenum_cafm.sites LIMIT {int(limit)}"
    try:
        async with session.begin_nested():
            rows = (await session.execute(text(sql))).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_backfill.sites_read_failed", error=str(exc)[:200])
        return []
    return [dict(r) for r in rows]


async def backfill_buildings_from_sites(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    country_code: str | None = None,
    limit: int = 5000,
) -> dict[str, Any]:
    """Create one building per site. Reports what it would do; writes only when dry_run=False."""
    shape = await graph_shape(session, refresh=True)
    if not shape["buildings"]["exists"]:
        return {"ok": False, "error": "plenum_cafm.buildings does not exist — apply udr_building_graph.sql"}
    have = shape["buildings"]["columns"]

    existing_by_site: dict[str, str] = {}
    existing_ids: dict[str, str] = {}
    try:
        async with session.begin_nested():
            # Keyed by building_code, never by building_id: the real primary key is a fresh
            # random uuid on every row, so it could never match anything a previous run
            # produced — building_code is the only identity two backfill passes share.
            rows = (await session.execute(
                text(
                    """SELECT building_code, site_id::text AS sid FROM plenum_cafm.buildings
                       WHERE building_code IS NOT NULL"""
                )
            )).all()
        for code, sid in rows:
            existing_ids[str(code)] = str(sid) if sid else ""
            if sid:
                existing_by_site.setdefault(str(sid), str(code))
    except Exception as exc:  # noqa: BLE001
        log.warning("building_backfill.existing_read_failed", error=str(exc)[:200])

    sites = await _load_sites(session, limit)
    plans = [
        plan_row(s, existing_by_site=existing_by_site, existing_ids=existing_ids,
                 country_code=country_code)
        for s in sites
    ]
    creates = [p for p in plans if p["action"] == "create"]

    written = 0
    location_link: dict[str, object] = {"linked": 0, "skipped": "dry run"}

    if not dry_run and creates:
        for p in creates:
            # building_id is deliberately never in this list — it is the real primary key,
            # and plenum_cafm.buildings already generates one (gen_random_uuid()) for every
            # row that omits it, exactly like building_create.py's manual path does.
            vals = {k: v for k, v in p["values"].items()
                    if not k.startswith("_") and (k in have or k == "site_id")}
            params = {f"p{i}": v for i, v in enumerate(vals.values())}
            placeholders = ", ".join(f":p{i}" for i in range(len(vals)))
            sql = f"INSERT INTO plenum_cafm.buildings ({', '.join(vals)}) VALUES ({placeholders})"
            try:
                async with session.begin_nested():
                    await session.execute(text(sql), params)
                written += 1
            except Exception as exc:  # noqa: BLE001 — one bad row must not lose the batch
                p["action"] = "failed"
                p["reason"] = str(exc)[:200]
                log.warning("building_backfill.insert_failed", building=p["building_id"],
                            error=str(exc)[:200])
            # Committed every _COMMIT_BATCH rows rather than once at the very end. A batch
            # this size (hundreds of sites) held open as one uncommitted transaction means a
            # single dropped connection anywhere in the run — observed live, mid-way through
            # a real backfill — discards every row that had already succeeded, not just the
            # one that failed. Committing as we go makes a drop cost at most one small batch,
            # and a retry resumes on its own: `existing_by_site` already skips whatever the
            # previous attempt got durably written.
            if written and written % _COMMIT_BATCH == 0:
                await session.commit()
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001 — the buildings above are already durable;
            # a failure flushing the remainder must not turn into a 500 that hides that.
            log.warning("building_backfill.final_commit_failed", error=str(exc)[:200])
        # A building with no location is scored against no standard — it falls through to
        # the rolling portfolio benchmark and reads as though its market has no regulation.
        # Linking here is what stops this backfill recreating that gap every time it runs.
        # Its own failure (network, a single bad location) must not cost the buildings
        # already committed above — caught and reported, never re-raised.
        try:
            from .building_create import link_buildings_to_locations

            linked = await link_buildings_to_locations(session, dry_run=False)
            location_link = {
                "linked": linked.get("linked", 0),
                "locations_created": linked.get("locations_created", 0),
                "left_unscored": len(linked.get("skipped") or []),
            }
        except Exception as exc:  # noqa: BLE001 — a missing location must not lose the rows
            location_link = {"error": str(exc)[:200]}
            log.warning("building_backfill.link_locations_failed", error=str(exc)[:200])
        try:
            await session.commit()
        except Exception as exc:  # noqa: BLE001 — same reasoning as the flush commit above:
            # the buildings are already durable, so this failing must not 500 the request.
            log.warning("building_backfill.final_commit_failed", error=str(exc)[:200])
            if "error" not in location_link:
                location_link = {"error": str(exc)[:200]}

    incomplete: dict[str, int] = {}
    for p in creates:
        for f in p.get("missing", []):
            incomplete[f] = incomplete.get(f, 0) + 1

    return {
        "ok": True,
        "dry_run": dry_run,
        "sites_read": len(sites),
        # A building with no location is scored against no standard, so what the backfill
        # linked is part of what it did, not a footnote.
        "location_link": location_link,
        "would_create" if dry_run else "created": len(creates) if dry_run else written,
        "skipped_existing": sum(1 for p in plans if p["action"] == "skip"),
        "conflicts": [p for p in plans if p["action"] == "conflict"],
        "failed": [p for p in plans if p["action"] == "failed"],
        "country_code_applied_to": sum(
            1 for p in creates if p.get("values", {}).get("_country_from_parameter")
        ),
        # Fields the new buildings would have nothing in — the Buildings table shows a dash
        # for each, so this is the honest gap list rather than a success number.
        "fields_left_empty": incomplete,
        "plan": [
            {k: v for k, v in p.items() if k != "values"} for p in plans
        ][:200],
    }
