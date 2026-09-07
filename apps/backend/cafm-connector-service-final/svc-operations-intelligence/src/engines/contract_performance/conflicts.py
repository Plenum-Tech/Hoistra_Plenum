"""FR-039 — Re-ingestion conflict detection for work orders.

When a WO arrives with different cost or date values vs what's already stored,
flag it for PM review rather than overwriting silently.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...shared.approvals import enqueue_approval, write_audit

_CONFLICT_FIELDS = ("actual_cost", "estimated_cost", "attended_at", "completed_at")
_NUMERIC_TOLERANCE = 0.01  # 1%


def _as_datetime(value: Any) -> datetime | None:
    """Coerce a datetime, date or ISO string to a naive datetime, else None."""
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).replace(
                tzinfo=None
            )
        except ValueError:
            return None
    return None


def _differs(stored: Any, incoming: Any) -> bool:
    """Return True if values differ enough to constitute a conflict."""
    if stored is None and incoming is None:
        return False
    if (stored is None) != (incoming is None):
        return True
    try:
        s, i = float(stored), float(incoming)
        if s == 0 and i == 0:
            return False
        if s == 0:
            return True
        return abs(s - i) / abs(s) > _NUMERIC_TOLERANCE
    except (TypeError, ValueError):
        pass

    # Timestamps arrive from the database as datetimes but from the scoring fetch as ISO
    # strings, and str() renders them differently — "2023-07-04 09:45:00" against
    # "2023-07-04T09:45:00". Comparing those as text made every work order conflict with
    # itself on every scoring run, which flagged the row and then excluded it from scoring.
    # Normalise both sides before deciding.
    s_dt, i_dt = _as_datetime(stored), _as_datetime(incoming)
    if s_dt is not None and i_dt is not None:
        return abs((s_dt - i_dt).total_seconds()) > 1
    return str(stored) != str(incoming)


async def detect_and_flag_wo_conflict(
    session: AsyncSession,
    *,
    wo_code: str,
    incoming_fields: dict[str, Any],
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """Check whether a WO re-ingestion conflicts with an existing row.

    Queries plenum_cafm.work_orders for wo_code. If any of actual_cost,
    estimated_cost, attended_at, completed_at differs beyond tolerance,
    sets conflict_flag=True, writes conflict_payload, and enqueues approval.

    Returns {"conflict": True/False, "wo_code": wo_code}.
    """
    row = (
        await session.execute(
            text(
                "SELECT actual_cost, estimated_cost, attended_at, completed_at "
                "FROM plenum_cafm.work_orders "
                "WHERE wo_code = :wc "
                "LIMIT 1"
            ),
            {"wc": wo_code},
        )
    ).mappings().first()

    if row is None:
        # First-time ingestion — no conflict possible
        return {"conflict": False, "wo_code": wo_code}

    conflict_details: dict[str, dict[str, Any]] = {}
    for field in _CONFLICT_FIELDS:
        stored_val = row.get(field)
        incoming_val = incoming_fields.get(field)
        if _differs(stored_val, incoming_val):
            # Stringify here, not at the point of use. These values come straight from
            # work_orders, so timestamp columns arrive as datetimes — and conflict_details
            # goes into a JSONB approval payload, where a raw datetime raises
            # "Object of type datetime is not JSON serializable" and takes the whole
            # scoring run down with it.
            conflict_details[field] = {
                "stored": None if stored_val is None else str(stored_val),
                "incoming": None if incoming_val is None else str(incoming_val),
            }

    if not conflict_details:
        return {"conflict": False, "wo_code": wo_code}

    # Set conflict_flag and write incoming values to conflict_payload
    await session.execute(
        text(
            "UPDATE plenum_cafm.work_orders "
            "SET conflict_flag = TRUE, "
            "    conflict_payload = CAST(:payload AS jsonb) "
            "WHERE wo_code = :wc"
        ),
        {
            "payload": __import__("json").dumps(
                {k: str(v) if v is not None else None for k, v in incoming_fields.items()}
            ),
            "wc": wo_code,
        },
    )

    await enqueue_approval(
        session,
        item_type="work_order_conflict",
        source_feature="B",
        summary=f"Re-ingestion conflict on {wo_code}: {', '.join(conflict_details.keys())}",
        severity="medium",
        organization_id=organization_id,
        payload={
            "wo_code": wo_code,
            "conflict_details": conflict_details,
        },
    )

    return {"conflict": True, "wo_code": wo_code}


async def resolve_wo_conflict(
    session: AsyncSession,
    *,
    wo_code: str,
    accept: str,
    resolved_by: UUID | None,
    note: str | None,
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """PM resolves a WO conflict by accepting stored or incoming values (FR-039 / T031).

    accept: "stored" — keep current row values, clear conflict flag.
            "incoming" — apply conflict_payload values to the row.
    """
    row = (
        await session.execute(
            text(
                "SELECT conflict_payload FROM plenum_cafm.work_orders "
                "WHERE wo_code = :wc LIMIT 1"
            ),
            {"wc": wo_code},
        )
    ).mappings().first()

    if row is None:
        return {"ok": False, "error": "wo_not_found", "wo_code": wo_code}

    if accept == "incoming" and row["conflict_payload"]:
        payload: dict[str, Any] = row["conflict_payload"]
        set_parts = []
        params: dict[str, Any] = {"wc": wo_code}
        for field in _CONFLICT_FIELDS:
            if field in payload:
                set_parts.append(f"{field} = :{field}")
                params[field] = payload[field] if payload[field] != "None" else None
        if set_parts:
            await session.execute(
                text(
                    "UPDATE plenum_cafm.work_orders SET "
                    + ", ".join(set_parts)
                    + " WHERE wo_code = :wc"
                ),
                params,
            )

    await session.execute(
        text(
            "UPDATE plenum_cafm.work_orders "
            "SET conflict_flag = FALSE, conflict_payload = '{}'::jsonb "
            "WHERE wo_code = :wc"
        ),
        {"wc": wo_code},
    )

    await write_audit(
        session,
        actor=str(resolved_by),
        action_type="work_order_conflict.resolve",
        source_feature="B",
        organization_id=organization_id,
        input_payload={"accept": accept, "note": note},
        output_payload={"wo_code": wo_code, "conflict_flag": False},
    )

    return {"ok": True, "wo_code": wo_code, "conflict_flag": False}
