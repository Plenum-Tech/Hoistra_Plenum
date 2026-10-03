"""Change one building. Only the fields sent are touched.

Three things this does that a naive column-setter would not, each of which is the difference
between an edit that works and one that quietly corrupts a row.

**Changing the country or region re-resolves the location.** The location is what points at
the regulation pack, so a building moved from the UK to the UAE without one keeps being
scored against CIBSE TM46 — the wrong standard, silently, on a row that otherwise looks
entirely correct.

**raw_metadata is merged, not replaced.** A patch sending only ``floors`` must not erase the
use mix and metering route the create stored there.

**A blank is not the same as an absence.** A field the patch omits is left alone. A field
sent EMPTY is a deliberate act: for an optional field it clears it, and for a required one
it is refused — a form submitting an empty box should not be able to leave a building with
no country, and ignoring the blank would tell the user it saved something it did not.

Validation reuses the create's rules rather than restating them, so a value a create would
refuse cannot slip in through an edit.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...shared.approvals import write_audit
from .building_create import _resolve_location, _s, validate_payload
from .building_rollup import graph_shape
from .buildings import get_building

log = get_logger(__name__)

#: Fields that must always hold a value. A patch may change one; it may not empty one.
REQUIRED_FIELDS = frozenset(
    {"name", "country_code", "region", "use_type", "floors", "metering_granularity"}
)

#: Everything a patch may touch. Anything else — benchmark_standard, hoist_score,
#: building_id — is refused BY NAME rather than ignored, because a field that is silently
#: dropped looks exactly like a field that was saved.
PATCHABLE = frozenset(
    REQUIRED_FIELDS
    | {"site_name", "state", "use_mix", "gfa_sqm", "building_code", "site_id",
       "city", "postcode", "metering_route", "country", "updated_by",
       "expected_updated_at"}
)

#: Columns a patch can write. The rest of what it accepts lives in raw_metadata.
_COLUMNS = frozenset(
    {"name", "building_code", "primary_use", "floors", "gross_area_sqft",
     "site_id", "location_id", "raw_metadata"}
)
_EXTRA_KEYS = ("use_mix", "metering_granularity", "metering_route", "postcode",
               "city", "country", "use_type")


def validate_patch(body: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
    """Validate only what was sent, by the same rules a create would apply."""
    errors: dict[str, str] = {k: "Not editable here." for k in body if k not in PATCHABLE}

    # Normalise the two spellings the form and the table each use, so the rest reads one
    # vocabulary rather than checking both everywhere.
    sent = {k: v for k, v in body.items() if k in PATCHABLE}
    if "site_name" in sent and "name" not in sent:
        sent["name"] = sent.pop("site_name")
    if "state" in sent and "region" not in sent:
        sent["region"] = sent.pop("state")

    for k in [k for k in REQUIRED_FIELDS
              if k in sent and (sent[k] is None or str(sent[k]).strip() == "")]:
        errors[k] = "Cannot be cleared — every building must have one."
        sent.pop(k, None)

    if not sent and not errors:
        return {}, {"_": "Nothing to change."}

    # Pad the payload with placeholders for the fields this patch does not touch, run the
    # create validator over it, then keep only what was actually sent. One set of rules.
    padded: dict[str, Any] = {
        "name": "x", "country_code": "UK", "region": "x", "use_type": "Commercial",
        "use_mix": [{"use": "office", "pct": 100}], "floors": 1,
        "metering_granularity": "none", "source": "patch",
    }
    padded.update({k: v for k, v in sent.items() if v is not None})
    clean_all, errs_all = validate_payload(padded)
    for k, msg in errs_all.items():
        if k in sent:
            errors[k] = msg

    clean: dict[str, Any] = {}
    for k in sent:
        if k in ("updated_by", "expected_updated_at"):
            clean[k] = _s(sent[k])
        elif sent[k] is None or str(sent[k]).strip() == "":
            clean[k] = None                      # an optional field, deliberately cleared
        elif k in clean_all:
            clean[k] = clean_all[k]
    # Derived values travel only when the field they come from was sent.
    if "use_type" in sent and "primary_use" in clean_all:
        clean["primary_use"] = clean_all["primary_use"]
    if sent.get("gfa_sqm") is not None and "gross_area_sqft" in clean_all:
        clean["gross_area_sqft"] = clean_all["gross_area_sqft"]
    return clean, errors


def _same_instant(a: Any, b: Any) -> bool | None:
    """Whether two timestamps are the same moment. None when b is unparseable.

    Compared as datetimes rather than strings so a client that round-trips the value through
    ISO-8601 — dropping a trailing zero, or writing T instead of a space — is not told its
    edit conflicts with itself.
    """
    from datetime import datetime

    def _dt(v: Any) -> Any:
        t = str(v or "").strip().replace(" ", "T")
        if t.endswith("+00"):
            t = t[:-3] + "+00:00"
        try:
            return datetime.fromisoformat(t)
        except ValueError:
            return None

    da, db = _dt(a), _dt(b)
    if db is None:
        return None          # unparseable: do not block the edit on our own parsing
    if da is None:
        return True
    return da == db


async def update_building(
    session: AsyncSession, building_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Apply a patch. Returns the row in the same shape GET /buildings uses."""
    bid = str(building_id or "").strip()
    if not bid:
        return {"ok": False, "status": 400, "errors": {"building_id": "Required."}}

    clean, errors = validate_patch(body or {})
    if errors:
        return {"ok": False, "status": 400, "errors": errors}

    shape = await graph_shape(session, refresh=True)
    if not shape["buildings"]["exists"]:
        return {"ok": False, "status": 503,
                "errors": {"_": "plenum_cafm.buildings is absent — run the migration."}}
    have = shape["buildings"]["columns"]

    meta_col = "raw_metadata" if "raw_metadata" in have else "NULL"
    upd_col = "updated_at::text" if "updated_at" in have else "NULL"
    row = (
        await session.execute(
            text(
                f"""SELECT name, building_code, {meta_col} AS meta, {upd_col} AS upd
                    FROM plenum_cafm.buildings WHERE building_id::text = :b"""
            ),
            {"b": bid},
        )
    ).mappings().first()
    if not row:
        return {"ok": False, "status": 404, "errors": {"building_id": f"No building {bid}."}}

    # Optimistic concurrency, only when the caller opted in by sending what it read.
    expected = clean.pop("expected_updated_at", None)
    # Compared whole, not truncated. Two people editing the same building inside one second
    # is exactly the case this exists for, and a second-resolution compare declares those
    # identical — which is the one moment it must not.
    if expected and row["upd"] and _same_instant(row["upd"], expected) is False:
        return {
            "ok": False, "status": 409,
            "errors": {"_": "This building changed since you opened it. Reload and re-apply "
                            "your edit rather than overwriting someone else's."},
            "current_updated_at": row["upd"],
        }

    if clean.get("building_code"):
        taken = (
            await session.execute(
                text(
                    """SELECT building_id::text FROM plenum_cafm.buildings
                       WHERE upper(building_code) = :c AND building_id::text <> :b"""
                ),
                {"c": clean["building_code"], "b": bid},
            )
        ).first()
        if taken:
            return {"ok": False, "status": 409,
                    "errors": {"building_code": f"{clean['building_code']} already in use."},
                    "conflict_building_id": taken[0]}

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

    # The market moved, so the regulation pack must move with it.
    relocated = None
    if ("country_code" in clean or "region" in clean) and "location_id" in have:
        meta_now = row["meta"] or {}
        cc = clean.get("country_code") or _s(meta_now.get("country_code")) or "UK"
        region = clean.get("region") or _s(meta_now.get("region")) or cc
        relocated = await _resolve_location(
            session, country_code=cc, region=region, city=clean.get("city")
        )
        clean["location_id"] = relocated["location_id"]

    touched_extras = {k: clean[k] for k in _EXTRA_KEYS if k in clean}
    if touched_extras and "raw_metadata" in have:
        merged = dict(row["meta"] or {})
        for k, v in touched_extras.items():
            if v is None:
                merged.pop(k, None)
            else:
                merged[k] = v
        # country_code and region are not columns, but the relocation reads them back on a
        # later patch that changes only one of the pair.
        for k in ("country_code", "region"):
            if clean.get(k):
                merged[k] = clean[k]
        merged["last_patched_by"] = clean.get("updated_by") or "api"
        clean["raw_metadata"] = json.dumps(merged)

    column_values = {k: v for k, v in clean.items() if k in have and k in _COLUMNS}
    if not column_values:
        return {"ok": False, "status": 400,
                "errors": {"_": "Nothing in this patch maps to a stored column."}}

    casts = {"location_id": "CAST(:location_id AS UUID)",
             "primary_use": "CAST(:primary_use AS plenum_cafm.building_primary_use)",
             "raw_metadata": "CAST(:raw_metadata AS JSONB)"}
    sets = ", ".join(f"{c} = {casts.get(c, ':' + c)}" for c in column_values)
    if "updated_at" in have:
        sets += ", updated_at = now()"
    try:
        await session.execute(
            text(f"UPDATE plenum_cafm.buildings SET {sets} WHERE building_id::text = :__b"),
            {**column_values, "__b": bid},
        )
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        log.warning("building_update.failed", error=str(exc)[:250], building_id=bid)
        return {"ok": False, "status": 400,
                "errors": {"_": f"Could not update: {str(exc)[:200]}"}}

    # An audit row is required, and losing the edit because writing it failed is the worse
    # outcome. The failure is surfaced rather than swallowed.
    audit_error: str | None = None
    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor=clean.get("updated_by") or "hoistra-ui",
                action_type="building.update",
                source_feature="C",
                input_payload={k: str(v) for k, v in clean.items() if k != "raw_metadata"},
                output_payload={"building_id": bid, "changed": sorted(column_values)},
                detail={"was": {"name": row["name"], "building_code": row["building_code"]}},
            )
    except Exception as exc:  # noqa: BLE001
        audit_error = str(exc)[:200]
        log.error("building_update.audit_failed", error=audit_error, building_id=bid)
    await session.commit()

    warnings: list[str] = []
    if str((body or {}).get("use_type") or "").lower() == "mall":
        warnings.append("Stored as Retail — the database has no Mall category.")
    if relocated:
        warnings.append(
            "Moved to a new location — this building now reads against that market's "
            "regulation pack."
        )
    if audit_error:
        warnings.append(f"Updated, but the audit row could not be written: {audit_error}")

    fetched = await get_building(session, bid)
    return {
        "ok": True,
        "status": 200,
        "building_id": bid,
        "changed": sorted(column_values),
        "relocated": bool(relocated),
        "warnings": warnings,
        "building": fetched.get("building") if fetched.get("ok") else None,
    }
