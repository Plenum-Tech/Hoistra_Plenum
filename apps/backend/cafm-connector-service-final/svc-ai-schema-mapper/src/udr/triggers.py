"""Feature 4 — the Activity Log trigger emitters (AL.1 trigger model, triggers 2-4).

Trigger 1 (a query/UDR run) is emitted by the pipeline. This module covers the other three:
  2. ``external_input`` — an email / sheet / work-order request arrived,
  3. ``threshold``      — a document/certificate expiry or deviation was detected,
  4. ``scheduled``      — a planned schedule (e.g. a scheduled work order) fired.

Pure builders + scan helpers (``now`` is injected) so the windows are unit-testable; the
async recording reuses ``record_activity``, and the real data sources (the inbox, the
documents-with-expiry table, the schedule) drive the endpoints / crons that call these.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

EXPIRY_DEFAULT_WINDOW_DAYS = 30  # AL.1 trigger 3 — flag certs/docs expiring within N days

_COLOR_BY_STATUS = {
    "completed": "green",
    "escalated": "orange",
    "failed": "red",
    "pending_human_input": "red",
}


def _color_for(status: str) -> str:
    return _COLOR_BY_STATUS.get(status, "green")


def _entry(trigger: str, *, summary: str, detail, status: str, notif_color, refs) -> dict:
    return {
        "trigger": trigger,
        "trigger_detail": detail,
        "outcome": summary,
        "status": status,
        "notif_color": notif_color or _color_for(status),
        "refs": refs or None,
        "processing_log": None,
    }


# ── Trigger 2 — external input (email / sheet / work-order request) ──────────────
def build_external_input_entry(
    *, source: str, summary: str, detail: str | None = None, status: str = "completed",
    notif_color: str | None = None, refs: dict | None = None,
) -> dict:
    return _entry(
        "external_input",
        summary=summary,
        detail=detail or f"Received from {source}",
        status=status,
        notif_color=notif_color,
        refs={"source": source, **(refs or {})},
    )


# ── Trigger 3 — threshold breach / expiry / deviation ───────────────────────────
def build_threshold_entry(
    *, summary: str, detail: str | None = None, status: str = "escalated",
    notif_color: str | None = None, refs: dict | None = None,
) -> dict:
    return _entry("threshold", summary=summary, detail=detail, status=status,
                  notif_color=notif_color, refs=refs)


# ── Trigger 4 — planned schedule fired ──────────────────────────────────────────
def build_scheduled_entry(
    *, summary: str, detail: str | None = None, status: str = "completed",
    notif_color: str | None = None, refs: dict | None = None,
) -> dict:
    return _entry("scheduled", summary=summary, detail=detail, status=status,
                  notif_color=notif_color, refs=refs)


def _parse_date(v):
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str) and v.strip():
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(v.strip()[:10])
            except ValueError:
                return None
    return None


def _parse_datetime(v):
    if isinstance(v, datetime):
        return v
    if isinstance(v, date):
        return datetime(v.year, v.month, v.day)
    if isinstance(v, str) and v.strip():
        try:
            return datetime.fromisoformat(v.strip().replace("Z", "+00:00"))
        except ValueError:
            d = _parse_date(v)
            return datetime(d.year, d.month, d.day) if d else None
    return None


def _name(item: dict) -> str:
    return str(item.get("name") or item.get("title") or item.get("id") or "item")


# ── Scanner for trigger 3 — items with an ``expires_at`` ────────────────────────
def scan_expiries(items, *, now: datetime, within_days: int = EXPIRY_DEFAULT_WINDOW_DAYS) -> list[dict]:
    """A threshold entry per item already expired (red) or expiring within the window
    (orange). ``now`` injected; ``items`` = ``[{id, name, expires_at}]``."""
    today = now.date()
    horizon = today + timedelta(days=within_days)
    out: list[dict] = []
    for it in items or []:
        exp = _parse_date(it.get("expires_at"))
        if exp is None:
            continue
        if exp < today:
            overdue = (today - exp).days
            out.append(build_threshold_entry(
                summary=f"{_name(it)} expired {overdue} day(s) ago",
                detail="Document/certificate expiry",
                status="pending_human_input",
                refs={"item_id": it.get("id"), "expires_at": exp.isoformat(), "overdue_days": overdue},
            ))
        elif exp <= horizon:
            remaining = (exp - today).days
            out.append(build_threshold_entry(
                summary=f"{_name(it)} expires in {remaining} day(s)",
                detail="Document/certificate expiry approaching",
                status="escalated",
                refs={"item_id": it.get("id"), "expires_at": exp.isoformat(), "days_remaining": remaining},
            ))
    return out


# ── Scanner for trigger 4 — items with a ``due_at`` that has arrived ─────────────
def scan_due_schedules(items, *, now: datetime) -> list[dict]:
    """A scheduled entry per item whose ``due_at`` has arrived (<= now)."""
    out: list[dict] = []
    for it in items or []:
        due = _parse_datetime(it.get("due_at"))
        if due is None:
            continue
        if due <= now:
            out.append(build_scheduled_entry(
                summary=f"Scheduled: {_name(it)}",
                detail="Planned schedule fired",
                refs={"item_id": it.get("id"), "due_at": due.isoformat()},
            ))
    return out
