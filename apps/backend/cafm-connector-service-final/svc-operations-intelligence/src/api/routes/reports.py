"""Custom reports — pinned questions from a session chat, refreshed on a cadence by the server.

    GET    /api/reports                          the caller's reports, each with its cards and latest runs
    POST   /api/reports                          create an (empty) report
    PATCH  /api/reports/{report_id}              rename
    DELETE /api/reports/{report_id}              remove the report and every card on it
    GET    /api/reports/refresh-options          the cadence presets a client can offer
    POST   /api/reports/cards                    pin a question as a card (from a session chat)
    GET    /api/reports/cards/{card_id}          one card with its recent runs
    PATCH  /api/reports/cards/{card_id}          rename, re-cadence, pause/resume, reorder, move
    DELETE /api/reports/cards/{card_id}          remove this one card
    POST   /api/reports/cards/{card_id}/run      refresh now (waits for the answer)
    GET    /api/reports/cards/{card_id}/runs     run history

Reports are personal: every row is the caller's own, admin or not. A refresh runs as the
card's owner, so its answer is drawn from the buildings they may see — the boundary every
other route applies, applied here by the same mechanism.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import access
from ...engines.reports import cards as card_svc
from .auth import scope

router = APIRouter(prefix="/api/reports", tags=["reports"], dependencies=[Depends(scope)])


def _bad(exc: card_svc.RefreshError) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                         detail={"ok": False, "error": str(exc), "reason": exc.reason})


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                         detail={"ok": False, "error": f"That {what} is not one of yours.", "reason": f"{what}_not_found"})


class ReportBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)


class SeedRun(BaseModel):
    """The answer the card is pinned from — shown until the first live refresh lands."""
    answer: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    rich: dict[str, Any] | None = None


class CardBody(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=8000, description="The session's question.")
    name: str | None = Field(None, max_length=200)
    report_id: UUID | None = Field(None, description="Omit to add to your first report (created if needed).")
    refresh: Any = Field("1h", description='Preset key ("30m","1h","6h","12h","24h","daily") or '
                                          '{"every_minutes": n} | {"daily_at": "HH:MM"} | {"days": [0-6], "time": "HH:MM"}')
    timezone: str | None = Field(None, description="IANA zone for clock cadences; default UTC.")
    source_session_id: str | None = Field(None, max_length=120)
    source_page: str | None = Field(None, max_length=80)
    source_message_id: str | None = Field(None, max_length=120)
    seed: SeedRun | None = None
    run_now: bool = Field(True, description="Refresh at once; false waits for the first cadence tick.")


class CardPatch(BaseModel):
    name: str | None = Field(None, max_length=200)
    prompt: str | None = Field(None, max_length=8000)
    refresh: Any = None
    timezone: str | None = None
    enabled: bool | None = None
    position: int | None = Field(None, ge=0)
    report_id: UUID | None = None


# ── reports ──────────────────────────────────────────────────────────────────

@router.get("")
async def list_reports(
    runs: int = Query(card_svc.DEFAULT_RUNS_RETURNED, ge=0, le=card_svc.MAX_RUNS_KEPT,
                      description="Recent runs to include per card."),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    reports = await card_svc.list_reports(session, owner_id=s.user_id)
    cards = await card_svc.list_cards(session, owner_id=s.user_id)
    by_card = await card_svc.latest_runs(session, [c.id for c in cards], per_card=runs) if runs else {}
    grouped: dict[UUID, list[dict[str, Any]]] = {}
    for c in cards:
        grouped.setdefault(c.report_id, []).append(card_svc.card_to_dict(c, by_card.get(c.id, []) if runs else None))
    out = [card_svc.report_to_dict(r, grouped.get(r.id, [])) for r in reports]
    return {"ok": True, "count": len(out), "reports": out}


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_report(body: ReportBody, session: AsyncSession = Depends(get_session),
                        s: access.Scope = Depends(scope)):
    r = await card_svc.create_report(session, owner_id=s.user_id, organization_id=s.organization_id, name=body.name)
    await session.commit()
    return {"ok": True, "report": card_svc.report_to_dict(r, [])}


@router.patch("/{report_id}")
async def rename_report(report_id: UUID, body: ReportBody, session: AsyncSession = Depends(get_session),
                        s: access.Scope = Depends(scope)):
    r = await card_svc.get_report(session, owner_id=s.user_id, report_id=report_id)
    if r is None:
        raise _not_found("report")
    await card_svc.rename_report(session, r, body.name)
    await session.commit()
    return {"ok": True, "report": card_svc.report_to_dict(r)}


@router.delete("/{report_id}")
async def delete_report(report_id: UUID, session: AsyncSession = Depends(get_session),
                        s: access.Scope = Depends(scope)):
    r = await card_svc.get_report(session, owner_id=s.user_id, report_id=report_id)
    if r is None:
        raise _not_found("report")
    removed = await card_svc.delete_report(session, r)
    await session.commit()
    return {"ok": True, "report_id": str(report_id), "cards_removed": removed}


@router.get("/refresh-options")
async def refresh_options():
    """The cadence menu. Returned so a client offers the same choices the server keeps."""
    return {"ok": True, "options": card_svc.PRESETS, "days": card_svc.DAYS,
            "min_every_minutes": card_svc.MIN_EVERY_MINUTES, "max_every_minutes": card_svc.MAX_EVERY_MINUTES}


# ── cards ────────────────────────────────────────────────────────────────────

@router.post("/cards", status_code=status.HTTP_201_CREATED)
async def add_card(body: CardBody, session: AsyncSession = Depends(get_session),
                   s: access.Scope = Depends(scope)):
    """Pin a session's question as a card. The seed answer, if sent, shows immediately; the
    first live refresh is queued at once (the scheduler picks it up within a tick)."""
    try:
        card, report = await card_svc.add_card(
            session, owner_id=s.user_id, organization_id=s.organization_id,
            prompt=body.prompt, name=body.name, report_id=body.report_id, refresh=body.refresh,
            timezone_name=body.timezone or "UTC", source_session_id=body.source_session_id,
            source_page=body.source_page, source_message_id=body.source_message_id,
            seed=body.seed.model_dump() if body.seed else None, run_now=body.run_now,
        )
    except card_svc.RefreshError as exc:
        raise _bad(exc) from None
    except LookupError:
        raise _not_found("report") from None
    await session.commit()
    runs = await card_svc.card_runs(session, card.id)
    return {"ok": True, "report": card_svc.report_to_dict(report), "card": card_svc.card_to_dict(card, runs)}


@router.get("/cards/{card_id}")
async def get_card(card_id: UUID, runs: int = Query(card_svc.DEFAULT_RUNS_RETURNED, ge=0, le=card_svc.MAX_RUNS_KEPT),
                   session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)):
    card = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    if card is None:
        raise _not_found("card")
    history = await card_svc.card_runs(session, card.id, limit=runs) if runs else []
    return {"ok": True, "card": card_svc.card_to_dict(card, history)}


@router.patch("/cards/{card_id}")
async def update_card(card_id: UUID, body: CardPatch, session: AsyncSession = Depends(get_session),
                      s: access.Scope = Depends(scope)):
    card = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    if card is None:
        raise _not_found("card")
    try:
        await card_svc.update_card(session, card, owner_id=s.user_id, name=body.name, prompt=body.prompt,
                                   refresh=body.refresh, timezone_name=body.timezone, enabled=body.enabled,
                                   position=body.position, report_id=body.report_id)
    except card_svc.RefreshError as exc:
        raise _bad(exc) from None
    except LookupError:
        raise _not_found("report") from None
    await session.commit()
    return {"ok": True, "card": card_svc.card_to_dict(card)}


@router.delete("/cards/{card_id}")
async def remove_card(card_id: UUID, session: AsyncSession = Depends(get_session),
                      s: access.Scope = Depends(scope)):
    """Remove this one card from its report. Other cards are untouched."""
    card = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    if card is None:
        raise _not_found("card")
    await card_svc.remove_card(session, card)
    await session.commit()
    return {"ok": True, "card_id": str(card_id), "report_id": str(card.report_id), "removed": True}


@router.post("/cards/{card_id}/run")
async def run_card_now(card_id: UUID, session: AsyncSession = Depends(get_session),
                       s: access.Scope = Depends(scope)):
    """Refresh now and return the run. Takes as long as the orchestrator takes (up to the
    run timeout); a card already refreshing answers 409 rather than asking twice."""
    card = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    if card is None:
        raise _not_found("card")
    if card.status == "running":
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={
            "ok": False, "error": "This card is refreshing already.", "reason": "already_running"})
    run = await card_svc.run_card(session, card.id, trigger="manual")
    fresh = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    return {"ok": run is not None and run.ok, "card": card_svc.card_to_dict(fresh) if fresh else None,
            "run": card_svc.run_to_dict(run) if run else None}


@router.get("/cards/{card_id}/runs")
async def list_runs(card_id: UUID, limit: int = Query(card_svc.DEFAULT_RUNS_RETURNED, ge=1, le=card_svc.MAX_RUNS_KEPT),
                    session: AsyncSession = Depends(get_session), s: access.Scope = Depends(scope)):
    card = await card_svc.get_card(session, owner_id=s.user_id, card_id=card_id)
    if card is None:
        raise _not_found("card")
    runs = await card_svc.card_runs(session, card.id, limit=limit)
    return {"ok": True, "card_id": str(card_id), "count": len(runs), "runs": [card_svc.run_to_dict(r) for r in runs]}
