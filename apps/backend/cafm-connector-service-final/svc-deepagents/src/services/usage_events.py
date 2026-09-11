"""Record what a caller did, for the consoles that bill and audit it.

Two ledgers in plenum_cafm, both written from the one place that knows who did what — the
workflow endpoint, which has just resolved the caller:

    platform_usage_events     one row per query turn, per ingest, per API request, with the
                              credits it cost written at the time
    ingestion_audit_events    who submitted a document, against which building, with what
                              warning and explanation, and what became of it

Written directly, the way building_binding already writes documents, because deep-agents
sits on the same database and a round trip to another service to insert one row would be a
second way for the same fact to go missing. Every write is best-effort: a ledger row that
cannot be written is logged and the request continues, because losing an upload over its
own receipt is not a trade worth making.

The tariff is the same one operations-intelligence reports on its usage card; the two are
kept in step by the test that compares them.
"""
from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import structlog
from sqlalchemy import text

from .. import database

log = structlog.get_logger(__name__)

#: Credits per event kind. Mirrors svc-operations-intelligence engines/auth/usage.TARIFF.
TARIFF: dict[str, float] = {"query": 1.0, "ingest": 5.0, "api_request": 0.1}

OUTCOMES = frozenset({
    "accepted", "reassigned", "overridden", "rejected", "approved_on_confirmation",
})


async def record_usage(
    *, kind: str, organization_id: UUID | None, user_id: UUID | None,
    building_id: UUID | None = None, detail: dict[str, Any] | None = None,
) -> float:
    credits = float(TARIFF.get(kind, 0.0))
    try:
        async with database.AsyncSessionLocal() as session:
            await session.execute(
                text("""INSERT INTO plenum_cafm.platform_usage_events
                            (organization_id, user_id, building_id, kind, credits, detail)
                        VALUES (CAST(:o AS uuid), CAST(:u AS uuid), CAST(:b AS uuid), :k, :c,
                                CAST(:d AS jsonb))"""),
                {"o": str(organization_id) if organization_id else None,
                 "u": str(user_id) if user_id else None,
                 "b": str(building_id) if building_id else None,
                 "k": kind, "c": credits, "d": json.dumps(detail or {}, default=str)},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — a receipt must never cost the request
        log.warning("usage.record_failed", kind=kind, error=str(exc)[:200])
    return credits


async def record_ingestion_audit(
    *, outcome: str, organization_id: UUID | None, actor_user_id: UUID | None,
    actor_role: str | None, document_name: str | None = None, document_id: UUID | None = None,
    building_id: UUID | None = None, reassigned_to_building_id: UUID | None = None,
    warning: str | None = None, explanation: str | None = None,
    approved_by: UUID | None = None, detail: dict[str, Any] | None = None,
) -> None:
    if outcome not in OUTCOMES:
        log.warning("ingestion_audit.unknown_outcome", outcome=outcome)
        return
    try:
        async with database.AsyncSessionLocal() as session:
            await session.execute(
                text("""INSERT INTO plenum_cafm.ingestion_audit_events
                            (organization_id, actor_user_id, actor_role, document_name,
                             document_id, building_id, reassigned_to_building_id, warning,
                             explanation, outcome, approved_by, detail)
                        VALUES (CAST(:o AS uuid), CAST(:a AS uuid), :r, :dn, CAST(:did AS uuid),
                                CAST(:b AS uuid), CAST(:rb AS uuid), :w, :x, :oc,
                                CAST(:ap AS uuid), CAST(:d AS jsonb))"""),
                {"o": str(organization_id) if organization_id else None,
                 "a": str(actor_user_id) if actor_user_id else None, "r": actor_role,
                 "dn": document_name, "did": str(document_id) if document_id else None,
                 "b": str(building_id) if building_id else None,
                 "rb": str(reassigned_to_building_id) if reassigned_to_building_id else None,
                 "w": warning, "x": explanation, "oc": outcome,
                 "ap": str(approved_by) if approved_by else None,
                 "d": json.dumps(detail or {}, default=str)},
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("ingestion_audit.record_failed", outcome=outcome, error=str(exc)[:200])
