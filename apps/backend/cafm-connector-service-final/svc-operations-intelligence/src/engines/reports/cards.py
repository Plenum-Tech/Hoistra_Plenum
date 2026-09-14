"""Custom report cards: a pinned question, its cadence, and its refreshes.

A card is one question from a session chat, kept and asked again. The cadence is the
owner's — an interval, a time each day, or chosen days at a time, in their zone — and the
server keeps it whether or not any browser is open. Every refresh is asked of the
orchestrator AS the owner, with a short-lived token minted for them, so the answer is drawn
from exactly the buildings they may see and nothing else; a card cannot become a way to read
past the boundary. The answer is stored as a run: what was said, which tools were read, how
long it took, or why it failed.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from ...config import settings
from ...core.logging import get_logger
from ...models.reports import Report, ReportCard, ReportCardRun
from ..auth import tokens as token_engine

log = get_logger(__name__)

MIN_EVERY_MINUTES = 5
MAX_EVERY_MINUTES = 7 * 24 * 60
#: Runs kept per card. The page shows the latest few; the rest is history nobody reads.
MAX_RUNS_KEPT = 10
DEFAULT_RUNS_RETURNED = 3
DEFAULT_REPORT_NAME = "My report"
#: The shell's cadence menu, so a client can offer the same choices without inventing them.
PRESETS: list[dict[str, Any]] = [
    {"key": "30m", "label": "Refresh every 30 minutes", "badge": "30 min", "refresh": {"every_minutes": 30}},
    {"key": "1h", "label": "Refresh every 1 hour", "badge": "1 hr", "refresh": {"every_minutes": 60}},
    {"key": "6h", "label": "Refresh every 6 hours", "badge": "6 hr", "refresh": {"every_minutes": 360}},
    {"key": "12h", "label": "Refresh every 12 hours", "badge": "12 hr", "refresh": {"every_minutes": 720}},
    {"key": "24h", "label": "Refresh every 24 hours", "badge": "24 hr", "refresh": {"every_minutes": 1440}},
    {"key": "daily", "label": "Refresh daily at 02:00", "badge": "Daily", "refresh": {"daily_at": "02:00"}},
    {"key": "days", "label": "Refresh on chosen days", "badge": "Days",
     "refresh": {"days": [], "time": "14:00"}, "pick_days": True},
]
DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
_TIME = re.compile(r"^(\d{1,2}):(\d{2})$")


class RefreshError(ValueError):
    """A cadence the server cannot keep. ``reason`` is stable for clients."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hhmm(value: Any, *, field: str) -> str:
    m = _TIME.match(str(value or "").strip())
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        raise RefreshError("bad_time", f"{field} must be HH:MM (24-hour).")
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def parse_refresh(value: Any) -> dict[str, Any]:
    """Normalise a cadence to one of the three shapes, or refuse it with a reason.

    Accepts a preset key too ("1h", "daily", …). Anything else — two shapes at once, an
    interval below five minutes, a day outside 0–6 — is refused rather than guessed at: a
    cadence that runs every minute is a cost, and one that never runs is a report that lies.
    """
    if isinstance(value, str):
        for p in PRESETS:
            if p["key"] == value.strip().lower() and not p.get("pick_days"):
                return dict(p["refresh"])
        raise RefreshError("bad_refresh", f"Unknown refresh preset {value!r}.")
    if not isinstance(value, dict):
        raise RefreshError("bad_refresh", "refresh must be an object.")
    keys = {k for k in ("every_minutes", "daily_at", "days") if value.get(k) is not None}
    if len(keys) != 1:
        raise RefreshError("bad_refresh",
                           "refresh must be exactly one of every_minutes, daily_at, or days+time.")
    if "every_minutes" in keys:
        try:
            n = int(value["every_minutes"])
        except (TypeError, ValueError):
            raise RefreshError("bad_refresh", "every_minutes must be a whole number.") from None
        if not MIN_EVERY_MINUTES <= n <= MAX_EVERY_MINUTES:
            raise RefreshError("bad_refresh",
                               f"every_minutes must be between {MIN_EVERY_MINUTES} and {MAX_EVERY_MINUTES}.")
        return {"every_minutes": n}
    if "daily_at" in keys:
        return {"daily_at": _hhmm(value["daily_at"], field="daily_at")}
    days = value.get("days")
    if not isinstance(days, list):
        raise RefreshError("bad_refresh", "days must be a list of weekday numbers, 0 = Sunday.")
    try:
        norm = sorted({int(d) for d in days})
    except (TypeError, ValueError):
        raise RefreshError("bad_refresh", "days must be whole numbers 0–6.") from None
    if not norm or any(d < 0 or d > 6 for d in norm):
        raise RefreshError("no_days", "Pick at least one day, 0 (Sunday) to 6 (Saturday).")
    return {"days": norm, "time": _hhmm(value.get("time") or "14:00", field="time")}


def parse_timezone(value: Any) -> str:
    tz = str(value or "UTC").strip() or "UTC"
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, ValueError):
        raise RefreshError("bad_timezone", f"Unknown timezone {tz!r}; use an IANA name like Europe/London.") from None
    return tz


def next_run_at(refresh: dict[str, Any], tz: str, after: datetime | None = None) -> datetime:
    """The first instant strictly after ``after`` that the cadence lands on, in UTC.

    Clock cadences are read in the owner's zone — "daily at 02:00" means their 02:00,
    across a DST change too — and the result is stored in UTC like every other timestamp.
    """
    after = after or _now()
    if refresh.get("every_minutes"):
        return after + timedelta(minutes=int(refresh["every_minutes"]))
    zone = ZoneInfo(tz or "UTC")
    local = after.astimezone(zone)
    if refresh.get("daily_at"):
        h, m = (int(x) for x in refresh["daily_at"].split(":"))
        cand = local.replace(hour=h, minute=m, second=0, microsecond=0)
        if cand <= local:
            cand = (cand + timedelta(days=1)).replace(hour=h, minute=m)
        return cand.astimezone(timezone.utc)
    days = set(refresh.get("days") or [])
    h, m = (int(x) for x in str(refresh.get("time") or "14:00").split(":"))
    for offset in range(0, 8):
        day = (local + timedelta(days=offset)).replace(hour=h, minute=m, second=0, microsecond=0)
        # Python: Monday = 0; the shell and this API: Sunday = 0.
        sunday_first = (day.weekday() + 1) % 7
        if day > local and sunday_first in days:
            return day.astimezone(timezone.utc)
    raise RefreshError("no_days", "Pick at least one day.")


def refresh_label(refresh: dict[str, Any]) -> str:
    if refresh.get("every_minutes"):
        n = int(refresh["every_minutes"])
        if n % 1440 == 0:
            return f"every {n // 1440 * 24} hours" if n > 1440 else "every 24 hours"
        if n % 60 == 0:
            return f"every {n // 60} hour{'s' if n > 60 else ''}"
        return f"every {n} minutes"
    if refresh.get("daily_at"):
        return f"daily at {refresh['daily_at']}"
    days = refresh.get("days") or []
    names = "every day" if len(days) == 7 else ", ".join(DAYS[d] for d in days)
    return f"{names} at {refresh.get('time', '14:00')}"


def report_context(card: ReportCard) -> str:
    """What the orchestrator is told about a scheduled run — the same brief the shell gave."""
    return (
        f"This question is a saved Hoistra custom report card named “{card.name}”, re-run "
        f"{refresh_label(card.refresh or {})}. It was pinned from the {card.source_page or 'Home'} page. "
        "Answer it as a standalone report against the current data: lead with the findings, then the "
        "figures and the records behind them. Do not ask follow-up questions."
    )


# ── serialisation ────────────────────────────────────────────────────────────

def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def run_to_dict(r: ReportCardRun) -> dict[str, Any]:
    return {
        "id": str(r.id), "card_id": str(r.card_id), "ran_at": _iso(r.ran_at),
        "duration_ms": r.duration_ms, "ok": r.ok, "trigger": r.trigger,
        "answer": r.answer, "tool_calls": r.tool_calls or [], "rich": r.rich, "error": r.error,
    }


def card_to_dict(c: ReportCard, runs: list[ReportCardRun] | None = None) -> dict[str, Any]:
    d = {
        "id": str(c.id), "report_id": str(c.report_id), "name": c.name, "prompt": c.prompt,
        "source": {"session_id": c.source_session_id, "page": c.source_page, "message_id": c.source_message_id},
        "refresh": c.refresh, "refresh_label": refresh_label(c.refresh or {}), "timezone": c.timezone,
        "position": c.position, "enabled": c.enabled, "status": c.status,
        "last_run_at": _iso(c.last_run_at), "last_tried_at": _iso(c.last_tried_at),
        "next_run_at": _iso(c.next_run_at), "last_error": c.last_error,
        "created_at": _iso(c.created_at), "updated_at": _iso(c.updated_at),
    }
    if runs is not None:
        d["runs"] = [run_to_dict(r) for r in runs]
        d["latest_run"] = d["runs"][0] if d["runs"] else None
    return d


def report_to_dict(r: Report, cards: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    d = {"id": str(r.id), "name": r.name, "created_at": _iso(r.created_at), "updated_at": _iso(r.updated_at)}
    if cards is not None:
        d["cards"] = cards
        d["card_count"] = len(cards)
    return d


# ── reports ──────────────────────────────────────────────────────────────────

async def list_reports(session: AsyncSession, *, owner_id: UUID) -> list[Report]:
    q = select(Report).where(Report.owner_user_id == owner_id, Report.deleted_at.is_(None)).order_by(Report.created_at)
    return list((await session.execute(q)).scalars().all())


async def get_report(session: AsyncSession, *, owner_id: UUID, report_id: UUID) -> Report | None:
    q = select(Report).where(Report.id == report_id, Report.owner_user_id == owner_id, Report.deleted_at.is_(None))
    return (await session.execute(q)).scalar_one_or_none()


async def create_report(session: AsyncSession, *, owner_id: UUID, organization_id: UUID | None,
                        name: str) -> Report:
    r = Report(id=uuid4(), owner_user_id=owner_id, organization_id=organization_id,
               name=(name or "").strip() or DEFAULT_REPORT_NAME)
    session.add(r)
    await session.flush()
    return r


async def default_report(session: AsyncSession, *, owner_id: UUID, organization_id: UUID | None) -> Report:
    """The owner's first report — created the first time they pin a card without naming one."""
    reports = await list_reports(session, owner_id=owner_id)
    if reports:
        return reports[0]
    return await create_report(session, owner_id=owner_id, organization_id=organization_id, name=DEFAULT_REPORT_NAME)


async def rename_report(session: AsyncSession, report: Report, name: str) -> Report:
    report.name = (name or "").strip() or report.name
    report.updated_at = _now()
    await session.flush()
    return report


async def delete_report(session: AsyncSession, report: Report) -> int:
    """Soft: the report and every card on it stop showing and stop running."""
    now = _now()
    report.deleted_at = now
    report.updated_at = now
    n = (await session.execute(
        update(ReportCard)
        .where(ReportCard.report_id == report.id, ReportCard.removed_at.is_(None))
        .values(removed_at=now, enabled=False, updated_at=now)
    )).rowcount
    await session.flush()
    return int(n or 0)


# ── cards ────────────────────────────────────────────────────────────────────

async def list_cards(session: AsyncSession, *, owner_id: UUID, report_id: UUID | None = None) -> list[ReportCard]:
    q = select(ReportCard).where(ReportCard.owner_user_id == owner_id, ReportCard.removed_at.is_(None))
    if report_id is not None:
        q = q.where(ReportCard.report_id == report_id)
    q = q.order_by(ReportCard.report_id, ReportCard.position, ReportCard.created_at)
    return list((await session.execute(q)).scalars().all())


async def get_card(session: AsyncSession, *, owner_id: UUID, card_id: UUID) -> ReportCard | None:
    q = select(ReportCard).where(ReportCard.id == card_id, ReportCard.owner_user_id == owner_id,
                                 ReportCard.removed_at.is_(None))
    return (await session.execute(q)).scalar_one_or_none()


async def latest_runs(session: AsyncSession, card_ids: list[UUID], *, per_card: int = DEFAULT_RUNS_RETURNED
                      ) -> dict[UUID, list[ReportCardRun]]:
    """The newest ``per_card`` runs for each card, in one query."""
    if not card_ids:
        return {}
    rows = (await session.execute(text("""
        SELECT id FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY card_id ORDER BY ran_at DESC) AS rn
              FROM plenum_cafm.report_card_runs
             WHERE card_id = ANY(CAST(:ids AS uuid[]))
        ) x WHERE rn <= :n"""), {"ids": [str(c) for c in card_ids], "n": per_card})).scalars().all()
    if not rows:
        return {c: [] for c in card_ids}
    runs = (await session.execute(
        select(ReportCardRun).where(ReportCardRun.id.in_(list(rows))).order_by(ReportCardRun.ran_at.desc())
    )).scalars().all()
    out: dict[UUID, list[ReportCardRun]] = {c: [] for c in card_ids}
    for r in runs:
        out.setdefault(r.card_id, []).append(r)
    return out


async def add_card(
    session: AsyncSession,
    *,
    owner_id: UUID,
    organization_id: UUID | None,
    prompt: str,
    name: str | None = None,
    report_id: UUID | None = None,
    refresh: Any = "1h",
    timezone_name: Any = "UTC",
    source_session_id: str | None = None,
    source_page: str | None = None,
    source_message_id: str | None = None,
    seed: dict[str, Any] | None = None,
    run_now: bool = True,
) -> tuple[ReportCard, Report]:
    """Pin a question. The answer it was pinned from, if given, becomes run zero so the card
    is never empty; the first live refresh is due at once unless the caller says otherwise."""
    prompt = (prompt or "").strip()
    if not prompt:
        raise RefreshError("no_prompt", "A card needs the question to ask.")
    ref = parse_refresh(refresh)
    tz = parse_timezone(timezone_name)
    if report_id is not None:
        report = await get_report(session, owner_id=owner_id, report_id=report_id)
        if report is None:
            raise LookupError("report_not_found")
    else:
        report = await default_report(session, owner_id=owner_id, organization_id=organization_id)
    position = (await session.execute(text("""
        SELECT COALESCE(MAX(position), -1) + 1 FROM plenum_cafm.report_cards
         WHERE report_id = :r AND removed_at IS NULL"""), {"r": str(report.id)})).scalar() or 0
    now = _now()
    card = ReportCard(
        id=uuid4(), report_id=report.id, owner_user_id=owner_id, organization_id=organization_id,
        name=(name or "").strip()[:200] or prompt[:200], prompt=prompt,
        source_session_id=(source_session_id or None), source_page=(source_page or None),
        source_message_id=(source_message_id or None),
        refresh=ref, timezone=tz, position=int(position), enabled=True,
        status="pending", next_run_at=now if run_now else next_run_at(ref, tz, now),
        created_at=now, updated_at=now,
    )
    session.add(card)
    if seed and (seed.get("answer") or seed.get("tool_calls")):
        session.add(ReportCardRun(
            id=uuid4(), card_id=card.id, ran_at=now, duration_ms=None, ok=True, trigger="seed",
            answer=str(seed.get("answer") or ""), tool_calls=list(seed.get("tool_calls") or []),
            rich=seed.get("rich") if isinstance(seed.get("rich"), dict) else None, error=None,
        ))
        card.status = "ready"
        card.last_run_at = now
    report.updated_at = now
    await session.flush()
    return card, report


async def update_card(session: AsyncSession, card: ReportCard, *, owner_id: UUID, name: str | None = None,
                      refresh: Any = None, timezone_name: Any = None, enabled: bool | None = None,
                      position: int | None = None, report_id: UUID | None = None,
                      prompt: str | None = None) -> ReportCard:
    now = _now()
    if name is not None:
        card.name = name.strip()[:200] or card.name
    if prompt is not None and prompt.strip():
        card.prompt = prompt.strip()
    if refresh is not None or timezone_name is not None:
        card.refresh = parse_refresh(refresh) if refresh is not None else card.refresh
        card.timezone = parse_timezone(timezone_name) if timezone_name is not None else card.timezone
        # A new cadence starts from now — not from the old schedule's last tick.
        card.next_run_at = next_run_at(card.refresh, card.timezone, now)
    if enabled is not None:
        card.enabled = bool(enabled)
        if card.enabled and card.status == "paused":
            card.status = "pending"
            card.next_run_at = next_run_at(card.refresh, card.timezone, now)
        if not card.enabled:
            card.status = "paused"
    if position is not None:
        card.position = int(position)
    if report_id is not None and report_id != card.report_id:
        target = await get_report(session, owner_id=owner_id, report_id=report_id)
        if target is None:
            raise LookupError("report_not_found")
        card.report_id = target.id
    card.updated_at = now
    await session.flush()
    return card


async def remove_card(session: AsyncSession, card: ReportCard) -> ReportCard:
    """Take the card off its report. Soft — its runs stay attached, the scheduler skips it."""
    now = _now()
    card.removed_at = now
    card.enabled = False
    card.updated_at = now
    await session.flush()
    return card


async def card_runs(session: AsyncSession, card_id: UUID, *, limit: int = DEFAULT_RUNS_RETURNED) -> list[ReportCardRun]:
    q = (select(ReportCardRun).where(ReportCardRun.card_id == card_id)
         .order_by(ReportCardRun.ran_at.desc()).limit(max(1, min(int(limit), MAX_RUNS_KEPT))))
    return list((await session.execute(q)).scalars().all())


# ── running a card ───────────────────────────────────────────────────────────

class OwnerUnavailable(Exception):
    """The owner can no longer be acted for — deactivated, deleted, or gone."""


async def _owner_token(session: AsyncSession, owner_id: UUID) -> str:
    """A short-lived access token for the card's owner, so the refresh runs as them.

    Minted, not borrowed: no session of theirs is reused and none is created. The token
    carries no session id, so it is bound to the account's live state (status, password
    epoch) exactly as a signed-in caller's is, and it expires on the normal access TTL.
    """
    row = (await session.execute(text("""
        SELECT id, email, organization_id, status, password_changed_at, platform_role
          FROM plenum_cafm.users WHERE id = :i"""), {"i": owner_id})).mappings().first()
    if row is None or str(row["status"] or "").lower() not in {"active", "pending_verification"}:
        raise OwnerUnavailable("owner_inactive" if row is not None else "owner_missing")
    token, _ttl = token_engine.issue_access_token(
        user_id=row["id"], email=str(row["email"] or ""), organization_id=row["organization_id"],
        session_id=None, password_changed_at=row["password_changed_at"],
        role=str(row["platform_role"] or "user"),
    )
    return token


async def _ask_orchestrator(card: ReportCard, token: str, *, http: httpx.AsyncClient | None = None) -> dict[str, Any]:
    payload = {
        "message": card.prompt,
        "session_id": f"report-{str(card.id)[:8]}-{int(_now().timestamp())}",
        "context": report_context(card),
    }
    headers = {"Authorization": f"Bearer {token}"}
    timeout = float(settings.report_run_timeout_seconds)
    if http is not None:
        resp = await http.post("/api/workflow/run-stateful", json=payload, headers=headers, timeout=timeout)
    else:
        async with httpx.AsyncClient(base_url=settings.deep_agents_base_url.rstrip("/"), timeout=timeout) as client:
            resp = await client.post("/api/workflow/run-stateful", json=payload, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"orchestrator answered {resp.status_code}: {resp.text[:200]}")
    body = resp.json()
    if not isinstance(body, dict):
        raise RuntimeError("orchestrator returned no answer")
    return body


async def run_card(session: AsyncSession, card_id: UUID, *, trigger: str = "schedule",
                   claimed: bool = False, http: httpx.AsyncClient | None = None) -> ReportCardRun | None:
    """One refresh of one card, recorded whatever happens.

    The card is marked running first (unless the scheduler already claimed it), so a second
    worker or a "Run now" click does not ask the same question twice at once. The run row
    says whether it succeeded; the card's status, next due time and last error follow from
    it. A failure is a run too — a report that silently skips a refresh looks current when
    it is not.
    """
    card = (await session.execute(select(ReportCard).where(ReportCard.id == card_id))).scalar_one_or_none()
    if card is None or card.removed_at is not None:
        return None
    if not claimed:
        if card.status == "running":
            return None
        card.status = "running"
        card.last_tried_at = _now()
        card.updated_at = card.last_tried_at
        await session.commit()
    t0 = _now()
    answer, tool_calls, rich, error = "", [], None, None
    try:
        token = await _owner_token(session, card.owner_user_id)
        body = await _ask_orchestrator(card, token, http=http)
        if body.get("success") is False:
            raise RuntimeError(str(body.get("error") or "the orchestrator returned no answer"))
        answer = str(body.get("answer") or "")
        tool_calls = list(body.get("tool_calls") or [])
        if not answer and not tool_calls:
            raise RuntimeError("the orchestrator returned an empty answer")
    except OwnerUnavailable as exc:
        error = f"The report's owner cannot be acted for ({exc}); the card is paused."
    except Exception as exc:  # noqa: BLE001 — every failure is a run row, never a lost refresh
        error = str(exc)[:1000]
    finished = _now()
    run = ReportCardRun(
        id=uuid4(), card_id=card.id, ran_at=finished,
        duration_ms=int((finished - t0).total_seconds() * 1000), ok=error is None, trigger=trigger,
        answer=answer or None,
        tool_calls=[{"tool": t.get("tool"), "input": t.get("input")} if isinstance(t, dict) else t for t in tool_calls],
        rich=rich, error=error,
    )
    session.add(run)
    card.last_tried_at = finished
    card.last_error = error
    if error is None:
        card.status = "ready"
        card.last_run_at = finished
    elif "paused" in error:
        card.status = "paused"
        card.enabled = False
    else:
        card.status = "error"
    card.next_run_at = next_run_at(card.refresh or {"every_minutes": 60}, card.timezone or "UTC", finished) \
        if card.enabled else None
    card.updated_at = finished
    # Keep the newest MAX_RUNS_KEPT; the rest is history nobody reads.
    await session.execute(text("""
        DELETE FROM plenum_cafm.report_card_runs WHERE card_id = :c AND id NOT IN (
            SELECT id FROM plenum_cafm.report_card_runs WHERE card_id = :c ORDER BY ran_at DESC LIMIT :n)"""),
        {"c": str(card.id), "n": MAX_RUNS_KEPT})
    await session.commit()
    log.info("report_card.run", card_id=str(card.id), ok=error is None, trigger=trigger,
             duration_ms=run.duration_ms, error=(error or "")[:120])
    return run
