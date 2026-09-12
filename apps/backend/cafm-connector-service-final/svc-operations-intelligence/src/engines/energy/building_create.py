"""Create and remove a building from the UI.

The four things the proposed contract got wrong against this schema, and what is done
instead:

* **use_type "Mall"** is not a member of ``plenum_cafm.building_primary_use``. The UI
  vocabulary is kept — a facilities manager calls it a mall — and mapped on write, with the
  response saying what it was stored as. Rejecting the word the user actually uses, or
  passing it through to fail at the database with an enum error, are both worse.
* **gfa_sqm** is square metres; the column is ``gross_area_sqft``. Converted here. Writing
  one into the other understates a building by 10.76×, and area is the denominator of every
  EUI computed from meter readings.
* **benchmark_standard / standing / note** are not columns and are not written. They are
  derived per request from the building's location → regulation pack, and a stored copy is a
  second answer that goes stale the day a standard changes.
* **B-NNN** is a building code, not a site id and not the primary key. It is allocated into
  ``building_code``; ``site_id`` is only ever a reference to a site that already exists.

Two things the contract did not account for. Country and region are properties of a
``location``, not of a building — and the location is what points at the regulation pack, so
a building created without one has no benchmark at all. One is resolved or created here.
And ``use_mix``, metering granularity, metering route and postcode have no column on the
canonical table; per the platform's rule an unmatched field goes to ``raw_metadata`` rather
than being dropped.
"""
from __future__ import annotations

import json
import re
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...shared.approvals import write_audit
from .building_rollup import SQFT_PER_SQM, graph_shape
from .buildings import get_building

log = get_logger(__name__)

#: What the UI offers, and what each stores as. The vocabulary is the user's; the enum is
#: the database's. Mall is the one that differs and it is the reason this map exists.
USE_TYPE_STORES_AS: dict[str, str] = {
    "commercial": "Commercial",
    "office": "Commercial",
    "retail": "Retail",
    "mall": "Retail",
    "shopping centre": "Retail",
    "residential": "Residential",
    "mixed": "Mixed",
    "hospital": "Hospital",
    "healthcare": "Hospital",
    "hotel": "Hotel",
    "industrial": "Industrial",
    "logistics": "Logistics",
    "warehouse": "Logistics",
    "education": "Education",
    "school": "Education",
    "laboratory": "Laboratory",
    "leisure": "Leisure",
    "other": "Other",
}

COUNTRY_CODES = {"UK", "US", "AE", "SG"}
#: The pack each market is scored against, matched on the standard the migration seeds.
PACK_STANDARD_FOR = {
    "UK": "CIBSE TM46",
    "US": "Energy Star · ASHRAE 100",
    "SG": "BCA Benchmarking Report",
    "AE": "Rolling portfolio benchmark",
}
GRANULARITIES = {"none", "building-level", "sub-metered"}


def plan_location_insert(id_data_type: str | None) -> dict[str, Any]:
    """Whether a new row can be inserted into ``plenum_cafm.locations`` right now, given the
    real, introspected type of its ``id`` column.

    ``locations`` predates this feature on a deployment where cafm-connector-service created
    it first: ``id`` is that service's legacy integer primary key. This engine's own
    migration (``udr_building_graph.sql``) declares ``CREATE TABLE IF NOT EXISTS locations
    (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), …)`` — which no-ops against a table that
    already exists, so ``id`` never becomes uuid there. Generating a fresh ``uuid4()`` and
    inserting it into an integer column is refused by Postgres outright
    (``DatatypeMismatchError: column "id" is of type integer but expression is of type
    uuid``), which is what a hoist attempt hit before this check existed. Anything other
    than a confirmed ``uuid`` column refuses the insert — a missing or unexpected type is
    exactly the situation this exists to catch, not a case to guess through.
    """
    can_insert = (id_data_type or "").lower() == "uuid"
    return {
        "can_insert": can_insert,
        "reason": None if can_insert else (
            "No location could be linked — plenum_cafm.locations.id is "
            + (id_data_type or "of an unrecognised type")
            + " on this deployment, not uuid, so a new location cannot be created until it "
            "is migrated. The building was created without one."
        ),
    }

MAX_FLOORS = 300
NAME_MAX = 200
_CODE_RE = re.compile(r"^B-(\d{2,6})$")


def _s(v: Any, limit: int = 400) -> str | None:
    if v is None:
        return None
    out = str(v).strip()
    return out[:limit] or None


def validate_payload(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Field-keyed errors, and the cleaned values. Pure — no database, no writes.

    Every error names the field it belongs to so the form can put the message next to the
    input rather than showing one banner for a page of fields.
    """
    e: dict[str, str] = {}
    c: dict[str, Any] = {}

    # The contract calls it site_name; the canonical table calls it name. Both accepted so
    # neither side has to change its vocabulary to talk to the other.
    name = _s(body.get("name") or body.get("site_name"), NAME_MAX)
    if not name:
        e["name"] = "Required. The building's name, 1–200 characters."
    else:
        c["name"] = name

    cc = (_s(body.get("country_code")) or "").upper()
    if cc == "GB":
        cc = "UK"
    if cc == "UAE":
        cc = "AE"
    if cc not in COUNTRY_CODES:
        e["country_code"] = f"Required. One of {', '.join(sorted(COUNTRY_CODES))}."
    else:
        c["country_code"] = cc
    c["country"] = _s(body.get("country")) or c.get("country_code")

    region = _s(body.get("region") or body.get("state"))
    if not region:
        e["region"] = "Required. The state, emirate or region."
    else:
        c["region"] = region

    raw_use = _s(body.get("use_type"))
    if not raw_use:
        e["use_type"] = "Required."
    elif raw_use.lower() not in USE_TYPE_STORES_AS:
        e["use_type"] = (
            "Unknown use. One of: "
            + ", ".join(sorted({v for v in USE_TYPE_STORES_AS.values()}))
            + " (Mall is accepted and stored as Retail)."
        )
    else:
        c["use_type"] = raw_use
        c["primary_use"] = USE_TYPE_STORES_AS[raw_use.lower()]

    mix = body.get("use_mix")
    if not isinstance(mix, list) or not mix:
        e["use_mix"] = "Required. A list of {use, pct} summing to 100."
    else:
        clean_mix: list[dict[str, Any]] = []
        total = 0.0
        bad = False
        for item in mix:
            if not isinstance(item, dict):
                bad = True
                break
            use = _s(item.get("use") or item.get("use_type"))
            try:
                pct = float(item.get("pct"))
            except (TypeError, ValueError):
                bad = True
                break
            if not use or pct <= 0:
                bad = True
                break
            total += pct
            clean_mix.append({"use": use, "pct": round(pct, 2)})
        if bad:
            e["use_mix"] = "Each entry needs a use and a positive pct."
        elif abs(total - 100.0) > 0.5:
            e["use_mix"] = f"Percentages must sum to 100 — these sum to {round(total, 2)}."
        else:
            c["use_mix"] = clean_mix

    floors = body.get("floors")
    try:
        fl = int(floors)
    except (TypeError, ValueError):
        e["floors"] = f"Required. A whole number of floors, 1–{MAX_FLOORS}."
    else:
        if not 1 <= fl <= MAX_FLOORS:
            e["floors"] = f"Must be between 1 and {MAX_FLOORS}."
        else:
            c["floors"] = fl

    gran = _s(body.get("metering_granularity"))
    if not gran:
        e["metering_granularity"] = f"Required. One of {', '.join(sorted(GRANULARITIES))}."
    elif gran not in GRANULARITIES:
        e["metering_granularity"] = f"One of {', '.join(sorted(GRANULARITIES))}."
    else:
        c["metering_granularity"] = gran

    src = _s(body.get("source"))
    if not src:
        e["source"] = "Required. Who is creating this, e.g. hoistra-ui."
    else:
        c["source"] = src

    # ── optional ────────────────────────────────────────────────────────────────
    gfa = body.get("gfa_sqm")
    if gfa is not None and _s(gfa) is not None:
        try:
            g = float(gfa)
        except (TypeError, ValueError):
            e["gfa_sqm"] = "Must be a number of square metres."
        else:
            if g <= 0:
                e["gfa_sqm"] = "Must be greater than zero."
            elif g > 5_000_000:
                e["gfa_sqm"] = "Implausibly large — check the units are square metres."
            else:
                c["gfa_sqm"] = g
                # The column is square FEET. This conversion is the whole point.
                c["gross_area_sqft"] = round(g * SQFT_PER_SQM, 2)

    code = _s(body.get("building_code") or body.get("site_id_code"))
    if code:
        if not _CODE_RE.match(code.upper()) and len(code) > 50:
            e["building_code"] = "Too long — 50 characters at most."
        else:
            c["building_code"] = code.upper()

    for opt in ("city", "postcode", "metering_route", "created_by"):
        v = _s(body.get(opt))
        if v:
            c[opt] = v

    site_id = _s(body.get("site_id"))
    if site_id:
        c["site_id"] = site_id

    org = _s(body.get("organization_id"))
    if org:
        try:
            c["organization_id"] = UUID(org)
        except (ValueError, AttributeError):
            e["organization_id"] = "Must be a uuid."

    return c, e


async def _next_building_code(session: AsyncSession) -> str:
    """The next free B-NNN, read inside the caller's transaction.

    Allocated from the highest existing code rather than a count, because a deleted building
    must not hand its number to the next one — two buildings sharing a code would resolve
    documents to whichever the matcher saw first.
    """
    try:
        rows = (
            await session.execute(
                text(
                    """SELECT building_code FROM plenum_cafm.buildings
                       WHERE building_code ~ '^B-[0-9]+$'"""
                )
            )
        ).all()
    except Exception as exc:  # noqa: BLE001
        log.warning("building_create.code_scan_failed", error=str(exc)[:200])
        rows = []
    highest = 0
    for r in rows:
        m = _CODE_RE.match(str(r[0] or "").upper())
        if m:
            highest = max(highest, int(m.group(1)))
    return f"B-{highest + 1:02d}"


async def _resolve_location(
    session: AsyncSession, *, country_code: str, region: str, city: str | None,
    organization_id: str | None = None,
) -> dict[str, Any]:
    """The location this building sits in, created if this market has none yet.

    Not incidental: the location is what carries the regulation pack, so a building created
    without one is scored against nothing and its benchmark column comes back empty.
    """
    pack_id = None
    standard = PACK_STANDARD_FOR.get(country_code)
    if standard:
        row = (
            await session.execute(
                text("SELECT pack_id::text FROM plenum_cafm.regulation_packs WHERE standard = :s"),
                {"s": standard},
            )
        ).first()
        pack_id = row[0] if row else None

    existing = (
        await session.execute(
            text(
                """SELECT id::text FROM plenum_cafm.locations
                   WHERE upper(country_code) = :cc AND lower(coalesce(region,'')) = :rg
                   LIMIT 1"""
            ),
            {"cc": country_code, "rg": (region or "").lower()},
        )
    ).first()
    if existing:
        return {"location_id": existing[0], "created": False, "pack_id": pack_id}

    id_type = (
        await session.execute(
            text(
                """SELECT data_type FROM information_schema.columns
                   WHERE table_schema = 'plenum_cafm' AND table_name = 'locations'
                     AND column_name = 'id'"""
            )
        )
    ).scalar()
    plan = plan_location_insert(id_type)
    if not plan["can_insert"]:
        log.warning("building_create.location_skipped", id_data_type=id_type, reason=plan["reason"])
        return {"location_id": None, "created": False, "pack_id": pack_id,
                "skipped_reason": plan["reason"]}

    # organization_id is written when the column demands it. plenum_cafm.locations is
    # declared twice — cafm-connector-service's ORM makes it NOT NULL, this service's
    # udr_building_graph.sql does not — and whichever ran first decides. Omitting it
    # worked on a database built from the migrations alone and failed with a NOT NULL
    # violation on one built the documented way, which is the shape a real deployment has.
    #
    # Introspected rather than assumed, so this is right on both, and stays right if the
    # two definitions are ever reconciled.
    org_col = (
        await session.execute(
            text("""SELECT is_nullable FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm' AND table_name = 'locations'
                      AND column_name = 'organization_id'""")
        )
    ).first()
    want_org = org_col is not None

    if want_org and not organization_id:
        # The column exists and the caller gave us nothing. Rather than fail, take the
        # organisation the platform already has — the same rule registration follows.
        row = (
            await session.execute(
                text("SELECT id::text FROM plenum_cafm.organizations ORDER BY created_at LIMIT 1")
            )
        ).first()
        organization_id = row[0] if row else None

    loc_id = str(uuid4())
    cols = "id, name, type, country_code, region, pack_id"
    vals = "CAST(:id AS UUID), :nm, 'region', :cc, :rg, CAST(:pk AS UUID)"
    params: dict[str, Any] = {
        "id": loc_id, "nm": city or region, "cc": country_code, "rg": region, "pk": pack_id,
    }
    if want_org:
        cols += ", organization_id"
        vals += ", CAST(:org AS UUID)"
        params["org"] = organization_id

    await session.execute(
        text(f"INSERT INTO plenum_cafm.locations ({cols}) VALUES ({vals})"), params,
    )
    return {"location_id": loc_id, "created": True, "pack_id": pack_id}


async def create_building(
    session: AsyncSession, body: dict[str, Any]
) -> dict[str, Any]:
    """Create one building. Returns the row in the same shape GET /buildings uses.

    ``ok: False`` with field-keyed ``errors`` for a bad payload, and ``conflict`` when a
    supplied building_code is already taken — an existing building is never overwritten.
    """
    clean, errors = validate_payload(body)
    if errors:
        return {"ok": False, "status": 400, "errors": errors}

    shape = await graph_shape(session, refresh=True)
    if not shape["buildings"]["exists"]:
        return {"ok": False, "status": 503,
                "errors": {"_": "plenum_cafm.buildings is absent — run the migration."}}
    have = shape["buildings"]["columns"]

    # A supplied code is never taken from an existing building.
    if clean.get("building_code"):
        taken = (
            await session.execute(
                text("SELECT building_id::text FROM plenum_cafm.buildings WHERE upper(building_code) = :c"),
                {"c": clean["building_code"]},
            )
        ).first()
        if taken:
            return {
                "ok": False, "status": 409,
                "errors": {"building_code": f"{clean['building_code']} already exists."},
                "conflict_building_id": taken[0],
            }
    else:
        clean["building_code"] = await _next_building_code(session)

    # A site_id is a reference, never an allocation. Pointing a building at a site that is
    # not there breaks every rollup that joins through it.
    if clean.get("site_id") and shape["sites"]["exists"]:
        skey = shape["sites"]["key"]
        found = (
            await session.execute(
                text(f"SELECT 1 FROM plenum_cafm.sites WHERE {skey}::text = :s"),
                {"s": clean["site_id"]},
            )
        ).first()
        if not found:
            return {"ok": False, "status": 400,
                    "errors": {"site_id": f"No site {clean['site_id']} exists."}}

    loc = await _resolve_location(
        session,
        country_code=clean["country_code"],
        region=clean["region"],
        city=clean.get("city"),
    )

    building_id = str(uuid4())
    # Fields the canonical table has no column for. Kept rather than dropped.
    extras = {
        k: clean[k]
        for k in ("use_mix", "metering_granularity", "metering_route", "postcode",
                  "city", "country", "use_type", "source", "created_by")
        if clean.get(k) is not None
    }
    extras["created_via"] = "api:POST /api/energy/buildings"

    cols: dict[str, Any] = {"building_id": building_id}
    candidates = {
        "name": clean["name"],
        "building_code": clean["building_code"],
        "primary_use": clean.get("primary_use"),
        "floors": clean.get("floors"),
        "gross_area_sqft": clean.get("gross_area_sqft"),
        "site_id": clean.get("site_id"),
        "location_id": loc["location_id"],
        # The company, when the column exists (access_control.sql adds it). Without it
        # the building is invisible to every company admin until a superadmin assigns it.
        "organization_id": clean.get("organization_id"),
        "hoist_score": 0,
        "raw_metadata": json.dumps(extras),
    }
    stored_extras = sorted(extras) if "raw_metadata" in have else []
    not_stored = [] if "raw_metadata" in have else sorted(extras)
    for col, val in candidates.items():
        if col in have and val is not None:
            cols[col] = val

    casts = {"building_id": "CAST(:building_id AS UUID)",
             "location_id": "CAST(:location_id AS UUID)",
             "organization_id": "CAST(:organization_id AS UUID)",
             "primary_use": "CAST(:primary_use AS plenum_cafm.building_primary_use)",
             "raw_metadata": "CAST(:raw_metadata AS JSONB)"}
    names = ", ".join(cols)
    values = ", ".join(casts.get(c, f":{c}") for c in cols)
    try:
        await session.execute(
            text(f"INSERT INTO plenum_cafm.buildings ({names}) VALUES ({values})"), cols
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("building_create.insert_failed", error=str(exc)[:250])
        return {"ok": False, "status": 400,
                "errors": {"_": f"Could not create the building: {str(exc)[:200]}"}}

    # The audit row is required, and losing the building because writing it failed is the
    # worse outcome — the create is what the user asked for. The failure is surfaced in the
    # response rather than swallowed, so an un-audited write is visible rather than assumed.
    audit_error: str | None = None
    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor=clean.get("created_by") or clean["source"],
                action_type="building.create",
                source_feature="C",
                organization_id=clean.get("organization_id"),
                input_payload={k: str(v) for k, v in clean.items() if k != "organization_id"},
                output_payload={"building_id": building_id,
                                "building_code": clean["building_code"]},
                detail={"source": clean["source"], "location_created": loc["created"]},
            )
    except Exception as exc:  # noqa: BLE001
        audit_error = str(exc)[:200]
        log.error("building_create.audit_failed", error=audit_error, building_id=building_id)
    await session.commit()

    fetched = await get_building(
        session, building_id, organization_id=clean.get("organization_id")
    )
    warnings: list[str] = []
    if loc.get("skipped_reason"):
        warnings.append(loc["skipped_reason"])
    if clean.get("use_type", "").lower() == "mall":
        warnings.append("Stored as Retail — the database has no Mall category.")
    if not loc["pack_id"]:
        warnings.append(
            f"No regulation pack for {clean['country_code']} — this building will read "
            "against the rolling portfolio benchmark."
        )
    if audit_error:
        warnings.append(f"Created, but the audit row could not be written: {audit_error}")
    if not_stored:
        warnings.append(
            "These were not stored — plenum_cafm.buildings has no raw_metadata column "
            "(run the migration): " + ", ".join(not_stored)
        )
    return {
        "ok": True,
        "status": 201,
        "building_id": building_id,
        "building_code": clean["building_code"],
        "stored_as": clean.get("primary_use"),
        "location": loc,
        "stored_extras": stored_extras,
        "warnings": warnings,
        "building": fetched.get("building") if fetched.get("ok") else None,
    }


#: Everything that points at a building. Counted before a delete, and detached rather than
#: destroyed — a certificate or an invoice is a record of something that happened, and it
#: does not stop being true because the building row was removed.
_CHILD_TABLES = (
    ("floors", "building_id"),
    ("spaces", "building_id"),
    ("assets", "building_id"),
    ("equipment", "building_id"),
    ("meters", "building_id"),
    ("documents", "building_id"),
    ("work_orders", "building_id"),
    ("compliance_certificates", "building_id"),
)


async def delete_building(
    session: AsyncSession,
    building_id: str,
    *,
    confirm: bool = False,
    detach: bool = True,
    actor: str = "hoistra-ui",
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Remove a building. Reports what it would touch unless ``confirm`` is true.

    Children are **detached, never deleted**. A certificate, an invoice or a work order is
    the record of something that actually happened; the building row going away does not
    make it untrue, and cascading would destroy evidence to tidy up a directory entry. They
    are left with ``building_id`` NULL, which the resolver reports as unplaced — recoverable
    by re-linking, where a delete is not.

    With ``detach=false`` a building holding anything is refused outright.
    """
    bid = str(building_id or "").strip()
    if not bid:
        return {"ok": False, "status": 400, "errors": {"building_id": "Required."}}

    row = (
        await session.execute(
            text(
                """SELECT building_id::text, name, building_code
                   FROM plenum_cafm.buildings WHERE building_id::text = :b"""
            ),
            {"b": bid},
        )
    ).first()
    if not row:
        return {"ok": False, "status": 404,
                "errors": {"building_id": f"No building {bid}."}}

    shape = await graph_shape(session)
    attached: dict[str, int] = {}
    for table, col in _CHILD_TABLES:
        info = shape.get(table)
        if not info or not info.get("exists") or col not in (info.get("columns") or set()):
            continue
        try:
            n = (
                await session.execute(
                    text(f"SELECT count(*) FROM plenum_cafm.{table} WHERE {col}::text = :b"),
                    {"b": bid},
                )
            ).scalar()
        except Exception:  # noqa: BLE001
            continue
        if n:
            attached[table] = int(n)

    plan = {
        "ok": True,
        "building_id": bid,
        "name": row[1],
        "building_code": row[2],
        "attached": attached,
        "attached_total": sum(attached.values()),
        "children_are": "detached, not deleted" if detach else "blocking",
    }

    if not confirm:
        return {**plan, "status": 200, "dry_run": True,
                "message": "Nothing was changed. Re-send with confirm=true to delete."}

    if attached and not detach:
        return {**plan, "ok": False, "status": 409,
                "errors": {"_": "This building still holds records. Send detach=true to "
                                "unlink them, or remove them first."}}

    detached: dict[str, int] = {}
    try:
        for table in attached:
            await session.execute(
                text(
                    f"UPDATE plenum_cafm.{table} SET building_id = NULL "
                    f"WHERE building_id::text = :b"
                ),
                {"b": bid},
            )
            detached[table] = attached[table]
        await session.execute(
            text("DELETE FROM plenum_cafm.buildings WHERE building_id::text = :b"),
            {"b": bid},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("building_delete.failed", error=str(exc)[:250], building_id=bid)
        return {**plan, "ok": False, "status": 400,
                "errors": {"_": f"Could not delete: {str(exc)[:200]}"}}

    audit_error = None
    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor=actor,
                action_type="building.delete",
                source_feature="C",
                organization_id=organization_id,
                input_payload={"building_id": bid, "detach": detach},
                output_payload={"detached": detached, "name": row[1],
                                "building_code": row[2]},
                detail={"attached": attached},
            )
    except Exception as exc:  # noqa: BLE001
        audit_error = str(exc)[:200]
        log.error("building_delete.audit_failed", error=audit_error, building_id=bid)
    await session.commit()
    return {**plan, "status": 200, "deleted": True, "detached": detached,
            "audit_error": audit_error,
            "message": (
                f"Deleted {row[1] or bid}."
                + (f" {sum(detached.values())} records were unlinked and kept."
                   if detached else "")
            )}


async def link_buildings_to_locations(
    session: AsyncSession,
    *,
    dry_run: bool = True,
    limit: int = 5000,
    actor: str = "hoistra-ui",
) -> dict[str, Any]:
    """Give every building without a location one, derived from the site it belongs to.

    A building sits in a country and a region, and those are properties of a ``location``,
    not of the building — the location is also what points at the regulation pack. The
    foreign key has always been there and the read path has always joined through it. What
    was missing is anything that POPULATES it: ``backfill-from-sites`` creates buildings and
    sets no location, so every building on an existing deployment has none, and a building
    with no location is scored against no standard. It falls through to the rolling
    portfolio benchmark and reads as though its market simply has no regulation — which for
    a London or a New York building is not true, and is the kind of wrong that looks
    perfectly ordinary on screen.

    Derived from the site because that is the only address on record. A building whose site
    carries no country is left alone and reported: inventing a market would attach a legal
    standard to a building on no evidence, and an unscored building is the safer error.

    Defaults to a dry run.
    """
    shape = await graph_shape(session, refresh=True)
    if not (shape["buildings"]["exists"] and shape["locations"]["exists"]):
        return {"ok": False, "error": "buildings or locations absent — run the migration."}
    have = shape["buildings"]["columns"]
    if "location_id" not in have:
        return {"ok": False, "error": "buildings.location_id absent — run the migration."}

    scols = shape["sites"]["columns"] if shape["sites"]["exists"] else set()
    if "country_code" not in scols:
        return {"ok": False, "error": "sites carries no country_code — nothing to derive from."}
    skey = shape["sites"]["key"]
    region_col = next((c for c in ("region", "state", "city") if c in scols), None)

    rows = (
        await session.execute(
            text(
                f"""SELECT b.building_id::text AS bid, b.name AS name,
                           s.country_code::text AS cc,
                           {("s." + region_col + "::text") if region_col else "NULL"} AS region,
                           {("s.city::text") if "city" in scols else "NULL"} AS city
                    FROM plenum_cafm.buildings b
                    LEFT JOIN plenum_cafm.sites s ON s.{skey}::text = b.site_id::text
                    WHERE b.location_id IS NULL
                    LIMIT :lim"""
            ),
            {"lim": int(limit)},
        )
    ).mappings().all()

    planned: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for r in rows:
        cc = (r["cc"] or "").strip().upper()
        if cc == "GB":
            cc = "UK"
        if cc == "UAE":
            cc = "AE"
        if not cc:
            skipped.append({"building_id": r["bid"], "name": r["name"],
                            "why": "its site carries no country"})
            continue
        region = (r["region"] or r["city"] or cc).strip()
        planned.append({"building_id": r["bid"], "name": r["name"],
                        "country_code": cc, "region": region, "city": r["city"],
                        "standard": PACK_STANDARD_FOR.get(cc, "Rolling portfolio benchmark")})

    out: dict[str, Any] = {
        "ok": True,
        "dry_run": dry_run,
        "unlinked_found": len(rows),
        "would_link" if dry_run else "linked": len(planned),
        "skipped": skipped,
        "buildings": planned[:50],
    }
    if dry_run or not planned:
        out["message"] = (
            f"{len(planned)} building(s) would gain a location. Nothing was changed. "
            "Re-send with dry_run=false to apply."
        ) if dry_run else "Nothing to link."
        return out

    locations_created = 0
    for item in planned:
        loc = await _resolve_location(
            session,
            country_code=item["country_code"],
            region=item["region"],
            city=item.get("city"),
        )
        locations_created += 1 if loc["created"] else 0
        await session.execute(
            text(
                """UPDATE plenum_cafm.buildings SET location_id = CAST(:l AS UUID)
                   WHERE building_id::text = :b AND location_id IS NULL"""
            ),
            {"l": loc["location_id"], "b": item["building_id"]},
        )
        item["location_id"] = loc["location_id"]
    out["locations_created"] = locations_created

    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor=actor,
                action_type="building.link_locations",
                source_feature="C",
                output_payload={"linked": len(planned), "locations_created": locations_created},
                detail={"skipped": len(skipped)},
            )
    except Exception as exc:  # noqa: BLE001
        log.error("building_link_locations.audit_failed", error=str(exc)[:200])
    await session.commit()
    out["message"] = (
        f"{len(planned)} building(s) now carry a location, "
        f"{locations_created} of which had to be created. "
        f"{len(skipped)} left alone for want of a country on their site."
    )
    return out
