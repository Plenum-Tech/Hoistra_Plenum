"""A1 — Certificate lifecycle status computation (PRD alert ladder)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any


DEFAULT_THRESHOLDS: dict[str, int] = {
    "current_gt": 90,
    "expiring_soon_min": 61,
    "expiring_soon_max": 90,
    "due_for_renewal_min": 31,
    "due_for_renewal_max": 60,
    "overdue_min": 8,
    "overdue_max": 30,
    "critical_min": 0,
    "critical_max": 7,
    # Legacy alias kept so older Country Pack rows still merge cleanly
    "lapsed_lte": 7,
}

# PRD ladder statuses
STATUS_CURRENT = "Current"
STATUS_EXPIRING_SOON = "Expiring Soon"
STATUS_DUE_FOR_RENEWAL = "Due for Renewal"
STATUS_OVERDUE = "Overdue"
STATUS_CRITICAL = "Critical"
STATUS_LAPSED = "Lapsed"
STATUS_NOT_ON_RECORD = "Not on record"

# Back-compat alias (older code referenced Critical window without a status)
STATUS_CRITICAL_WINDOW = STATUS_CRITICAL

BUILDING_ALERT_CHANNELS: dict[str, dict[str, Any]] = {
    STATUS_EXPIRING_SOON: {
        # 61–90 days — Email to PM (informational); no Approvals action
        "severity": "Info",
        "channel": "email",
        "subject_prefix": "NOTICE",
        "queue": False,
        "platform_notification": False,
        "action": "No action — PM awareness",
    },
    STATUS_DUE_FOR_RENEWAL: {
        # 31–60 days — Email + platform notification; booking prompt
        "severity": "Info",
        "channel": "email+notification",
        "subject_prefix": "WARNING",
        "queue": True,
        "platform_notification": True,
        "booking_prompt": True,
        "action": "Booking prompt with contractor recommendation",
    },
    STATUS_OVERDUE: {
        # 8–30 days — Email + Approvals queue; one-click booking approve
        "severity": "Action required",
        "channel": "email+queue",
        "subject_prefix": "WARNING",
        "queue": True,
        "platform_notification": True,
        "booking_draft": True,
        "action": "PM approves booking request — one click from email or queue",
    },
    STATUS_CRITICAL: {
        # ≤7 days (still not past expiry) — Escalation + Critical queue item
        "severity": "Critical",
        "channel": "escalation_email+critical_queue",
        "subject_prefix": "URGENT",
        "queue": True,
        "platform_notification": True,
        "escalate_senior": True,
        "booking_draft": True,
        "action": "Escalated to senior contact; immediate PM action",
    },
    STATUS_LAPSED: {
        # Past expiry — Immediate email + platform alert + insurance risk flag
        "severity": "Critical",
        "channel": "email+queue+alert+insurance",
        "subject_prefix": "URGENT",
        "queue": True,
        "platform_notification": True,
        "insurance_risk": True,
        "escalate_senior": True,
        "action": "PM acknowledges; renewal initiated",
    },
}


@dataclass(frozen=True)
class LifecycleResult:
    status: str
    days_to_expiry: int | None
    insurance_risk_flag: bool
    alert_meta: dict[str, Any]


def days_until(expiry: date | datetime | None, today: date | None = None) -> int | None:
    if expiry is None:
        return None
    if isinstance(expiry, datetime):
        expiry_d = expiry.date()
    else:
        expiry_d = expiry
    ref = today or datetime.now(timezone.utc).date()
    return (expiry_d - ref).days


def _normalize_thresholds(thresholds: dict[str, Any] | None) -> dict[str, int]:
    """Merge pack overrides; map legacy lapsed_lte → critical_max when critical_max omitted."""
    raw = dict(thresholds or {})
    th = {**DEFAULT_THRESHOLDS, **raw}
    if "critical_max" in raw:
        th["critical_max"] = int(raw["critical_max"])
    elif "lapsed_lte" in raw:
        th["critical_max"] = int(raw["lapsed_lte"])
    if "critical_min" not in th:
        th["critical_min"] = 0
    # Keep alias in sync for admin UIs still editing lapsed_lte
    th["lapsed_lte"] = int(th["critical_max"])
    return {k: int(v) for k, v in th.items()}


def resolve_status_anchor(
    expiry_date: date | datetime | None,
    next_due_date: date | datetime | None = None,
) -> date | datetime | None:
    """CCC §5 — use next_due_date / review date when statutory expiry is absent."""
    return expiry_date if expiry_date is not None else next_due_date


def compute_certificate_status(
    expiry_date: date | datetime | None,
    *,
    thresholds: dict[str, Any] | None = None,
    today: date | None = None,
    next_due_date: date | datetime | None = None,
) -> LifecycleResult:
    """
    A1 status ladder (PRD):
      Current (>90)
      Expiring Soon (61–90) — email informational
      Due for Renewal (31–60) — email + platform notification + booking prompt
      Overdue (8–30) — email + Approvals queue + one-click booking
      Critical (0–7) — escalation email + Critical queue
      Lapsed (<0 / past expiry) — email + alert + insurance risk flag

    When expiry_date is null, falls back to next_due_date (FRA / living docs).
    """
    th = _normalize_thresholds(thresholds)
    anchor = resolve_status_anchor(expiry_date, next_due_date)
    dte = days_until(anchor, today=today)

    if dte is None:
        return LifecycleResult(
            status=STATUS_NOT_ON_RECORD,
            days_to_expiry=None,
            insurance_risk_flag=False,
            alert_meta={},
        )

    if dte < 0:
        status = STATUS_LAPSED
    elif int(th["critical_min"]) <= dte <= int(th["critical_max"]):
        status = STATUS_CRITICAL
    elif int(th["overdue_min"]) <= dte <= int(th["overdue_max"]):
        status = STATUS_OVERDUE
    elif int(th["due_for_renewal_min"]) <= dte <= int(th["due_for_renewal_max"]):
        status = STATUS_DUE_FOR_RENEWAL
    elif int(th["expiring_soon_min"]) <= dte <= int(th["expiring_soon_max"]):
        status = STATUS_EXPIRING_SOON
    else:
        status = STATUS_CURRENT

    alert_meta = dict(BUILDING_ALERT_CHANNELS.get(status, {}))

    return LifecycleResult(
        status=status,
        days_to_expiry=dte,
        insurance_risk_flag=bool(alert_meta.get("insurance_risk")),
        alert_meta=alert_meta,
    )


def vendor_risk_level(days_to_expiry: int | None) -> str:
    """
    A3 risk levels:
      Medium Risk: 31–90 days
      High Risk: ≤30 days
      Lapsed / Blocked: past expiry or ≤0
      Clear: >90 or None treated as unknown→Clear informational
    """
    if days_to_expiry is None:
        return "Unknown"
    if days_to_expiry <= 0:
        return "Lapsed"
    if days_to_expiry <= 30:
        return "High Risk"
    if days_to_expiry <= 90:
        return "Medium Risk"
    return "Clear"
