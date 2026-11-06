"""A certificate's status read from its expiry date - one rule for every reader in this service.

A certificate stored as in date whose expiry date has passed is stale: the lifecycle scan that moves
its status on has not run. 2 Nov 2026: LOLER-B-301-4471, expired 2026-10-18, was stored as "valid"
and the answer called it current. The compliance register read (orchestrator._status_from_expiry)
applied the date from 2 Nov; the certificate tools (compliance_engine_agent) asked the service for
status=Lapsed, which filters on the stored column, so a stale "valid" never came back (3 Nov 2026).

Read-only: what an answer is given changes, the register does not.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

#: Stored statuses that say a certificate is in date. Past its expiry date such a status is stale.
IN_DATE_STATUSES = frozenset({"valid", "current", "active", "expiring soon", "due for renewal", "critical"})

#: Words a person or a model uses for the same status, folded to one key for comparing.
_FAMILY = {
    "lapsed": "lapsed", "expired": "lapsed",
    "current": "current", "valid": "current", "active": "current", "in date": "current",
}

#: What a result says when its statuses were read from the expiry date.
STATUS_RULE = ("A certificate stored as in date whose expiry date has passed is shown as Lapsed, with "
               "the stored value in status_recorded; days_to_expiry is counted from the expiry date.")


def _expiry(raw: Any) -> date | None:
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def status_from_expiry(row: dict[str, Any], today: Any = None) -> dict[str, Any]:
    """Days to expiry from the expiry date, and "Lapsed" for a certificate stored as in date whose
    date has passed, with the stored status kept as status_recorded. {} when the row has no
    readable expiry date: its stored values stand."""
    expiry = _expiry(row.get("expiry_date"))
    if expiry is None:
        return {}
    days = (expiry - (today or date.today())).days
    out: dict[str, Any] = {"days_to_expiry": days}
    status = str(row.get("status") or "").strip()
    if days < 0 and status.lower() in IN_DATE_STATUSES:
        out.update(status="Lapsed", status_recorded=status)
    return out


def family(status: Any) -> str:
    """A status as one comparable key: "Expired" and "lapsed" are one, "Valid" and "Current" are one."""
    s = " ".join(str(status or "").strip().lower().replace("_", " ").split())
    return _FAMILY.get(s, s)


def read_rows(rows: list[Any], today: Any = None) -> list[Any]:
    """Each certificate row with its status read from its expiry date (new dicts; rows not changed)."""
    out = []
    for r in rows:
        if isinstance(r, dict):
            r = {**r, **status_from_expiry(r, today)}
        out.append(r)
    return out


def keep_status(rows: list[Any], wanted: str) -> list[Any]:
    """The rows whose (date-read) status is the one asked for."""
    key = family(wanted)
    return [r for r in rows if isinstance(r, dict) and family(r.get("status")) == key]
