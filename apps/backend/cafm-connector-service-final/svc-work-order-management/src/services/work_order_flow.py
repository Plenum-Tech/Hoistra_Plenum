"""
BE1-07: Work Order Flow
Orchestrates: email parse → AI assessment → DB create in a single async call.
OpenAI calls are sync internally but wrapped with asyncio.to_thread so they
don't block the event loop.
"""
import asyncio
import time
from difflib import SequenceMatcher
from datetime import datetime, timezone
from typing import AsyncGenerator, Dict, Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import SQLAlchemyError

from .email_parser import EmailParser
from .ai_extraction_service import AIExtractionService
from .journey_service import create_journey_for_work_order, record_status_change
from ..models.asset import Asset
from ..models.location import Location
from ..models.work_order import WorkOrder
from ..config import settings
from ..core.exceptions import DatabaseError
from ..core.logging import get_logger

log = get_logger(__name__)


def _wo_id() -> str:
    return f"WO-{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')[:18]}"


def _norm_text(value: str | None) -> str:
    if not value:
        return ""
    return " ".join(str(value).strip().lower().split())


def _similarity(a: str | None, b: str | None) -> float:
    aa = _norm_text(a)
    bb = _norm_text(b)
    if not aa or not bb:
        return 0.0
    return SequenceMatcher(None, aa, bb).ratio()


class WorkOrderFlow:
    """
    End-to-end pipeline: email dict → Claude parse → Claude assess → DB write.

    Usage (from a FastAPI route):
        flow = WorkOrderFlow(api_key=settings.openai_api_key, model=settings.openai_model)
        result = await flow.create_from_email(email_dict, db_session)
    """

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self.email_parser = EmailParser(api_key=api_key, model=model)
        self.ai_extraction = AIExtractionService(api_key=api_key, model=model)

    async def _resolve_asset(self, parsed_asset: str, session: AsyncSession) -> Dict[str, Any]:
        asset_input = _norm_text(parsed_asset)
        if not asset_input:
            return {"resolved": False, "reason": "empty_asset"}

        result = await session.execute(
            select(Asset).where(
                Asset.asset_name.ilike(f"%{parsed_asset}%")
                | Asset.asset_code.ilike(f"%{parsed_asset}%")
            ).limit(20)
        )
        candidates = result.scalars().all()
        if not candidates:
            return {"resolved": False, "reason": "asset_not_found", "input": parsed_asset}

        ranked: list[tuple[float, Asset]] = []
        for row in candidates:
            score = max(
                _similarity(parsed_asset, getattr(row, "asset_name", None)),
                _similarity(parsed_asset, getattr(row, "asset_code", None)),
            )
            ranked.append((score, row))
        ranked.sort(key=lambda x: x[0], reverse=True)

        top_score, top = ranked[0]
        if top_score < 0.55:
            return {
                "resolved": False,
                "reason": "asset_ambiguous",
                "input": parsed_asset,
                "top_score": round(top_score, 3),
                "candidates": [getattr(r, "asset_name", None) for _, r in ranked[:3]],
            }

        return {
            "resolved": True,
            "input": parsed_asset,
            "match_type": "fuzzy",
            "match_score": round(top_score, 3),
            "asset_id": str(getattr(top, "asset_id", "")),
            "asset_name": getattr(top, "asset_name", None),
            "asset_code": getattr(top, "asset_code", None),
            "asset_status": getattr(top, "status", None),
        }

    async def _resolve_location(self, parsed_location: str, session: AsyncSession) -> Dict[str, Any]:
        location_input = _norm_text(parsed_location)
        if not location_input:
            return {"resolved": False, "reason": "empty_location"}

        result = await session.execute(
            select(Location).where(Location.name.ilike(f"%{parsed_location}%")).limit(20)
        )
        candidates = result.scalars().all()
        if not candidates:
            return {"resolved": False, "reason": "location_not_found", "input": parsed_location}

        ranked: list[tuple[float, Location]] = []
        for row in candidates:
            score = _similarity(parsed_location, getattr(row, "name", None))
            ranked.append((score, row))
        ranked.sort(key=lambda x: x[0], reverse=True)

        top_score, top = ranked[0]
        if top_score < 0.55:
            return {
                "resolved": False,
                "reason": "location_ambiguous",
                "input": parsed_location,
                "top_score": round(top_score, 3),
                "candidates": [getattr(r, "name", None) for _, r in ranked[:3]],
            }

        return {
            "resolved": True,
            "input": parsed_location,
            "match_type": "fuzzy",
            "match_score": round(top_score, 3),
            "location_id": str(getattr(top, "location_id", "")),
            "location_name": getattr(top, "name", None),
            "location_type": getattr(top, "type", None),
        }

    async def _build_db_context(self, data: Dict[str, Any], session: AsyncSession) -> Dict[str, Any]:
        asset_info = await self._resolve_asset(data.get("asset", ""), session)
        location_info = await self._resolve_location(data.get("location", ""), session)

        unresolved_fields: list[str] = []
        if not asset_info.get("resolved"):
            unresolved_fields.append("asset")
        if not location_info.get("resolved"):
            unresolved_fields.append("location")

        context: Dict[str, Any] = {
            "asset_resolution": asset_info,
            "location_resolution": location_info,
            "unresolved_fields": unresolved_fields,
            "recent_work_orders": [],
            "asset_open_work_orders": 0,
            "location_open_work_orders": 0,
        }

        if asset_info.get("resolved"):
            asset_name = asset_info.get("asset_name")
            result = await session.execute(
                select(WorkOrder).where(WorkOrder.asset.ilike(f"%{asset_name}%"))
                .order_by(WorkOrder.created_at.desc())
                .limit(5)
            )
            recent = result.scalars().all()
            context["recent_work_orders"] = [
                {
                    "work_order_id": row.work_order_id,
                    "status": row.status,
                    "priority": row.priority,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                    "issue": row.issue_description,
                }
                for row in recent
            ]
            context["asset_open_work_orders"] = sum(
                1 for row in recent if (row.status or "").lower() not in ("closed", "completed")
            )

        if location_info.get("resolved"):
            location_name = location_info.get("location_name")
            result = await session.execute(
                select(WorkOrder).where(WorkOrder.location.ilike(f"%{location_name}%"))
                .order_by(WorkOrder.created_at.desc())
                .limit(10)
            )
            rows = result.scalars().all()
            context["location_open_work_orders"] = sum(
                1 for row in rows if (row.status or "").lower() not in ("closed", "completed")
            )

        return context

    async def create_from_email(
        self,
        email: Dict[str, Any],
        session: AsyncSession,
    ) -> Dict[str, Any]:
        """
        Step 1 — Parse email with OpenAI (extract asset, location, issue, requester).
        Step 2 — Run 13-block AI assessment on extracted data.
        Step 3 — Persist work order + journey log to DB.
        Returns result dict with work_order_id, journey_log_id, and full assessment.
        """
        email_id = email.get("id")
        log.info(
            "flow.email.start",
            email_id=email_id,
            subject=email.get("subject"),
            from_addr=email.get("from"),
        )

        # -- Step 0: classify — skip non-maintenance emails immediately ---
        classification = await asyncio.to_thread(self.email_parser.classify, email)
        if not classification.get("is_maintenance", True):
            log.info(
                "flow.email.not_maintenance",
                email_id=email_id,
                subject=email.get("subject"),
                reason=classification.get("reason"),
            )
            return {
                "status": "not_maintenance",
                "reason": classification.get("reason"),
                "email_id": email_id,
            }

        # -- Step 1: parse email ---
        t0 = time.monotonic()
        parsed = await asyncio.to_thread(self.email_parser.parse, email)
        parse_ms = round((time.monotonic() - t0) * 1000)

        if not parsed["ready"]:
            log.warning(
                "flow.email.missing_info",
                email_id=email_id,
                missing_fields=parsed["missing_fields"],
                parse_ms=parse_ms,
            )
            return {
                "status": "missing_info",
                "missing_fields": parsed["missing_fields"],
                "email_id": email_id,
            }

        data = parsed["data"]
        log.info(
            "flow.email.parsed",
            email_id=email_id,
            asset=data.get("asset"),
            location=data.get("location"),
            priority=data.get("priority"),
            parse_ms=parse_ms,
        )

        # -- Step 1.5: DB enrichment & validation ---
        db_context = await self._build_db_context(data, session)
        log.info(
            "flow.email.db_context",
            email_id=email_id,
            asset_resolved=db_context["asset_resolution"].get("resolved"),
            location_resolved=db_context["location_resolution"].get("resolved"),
            unresolved_fields=db_context["unresolved_fields"],
            recent_wo_count=len(db_context["recent_work_orders"]),
            asset_open_wo=db_context["asset_open_work_orders"],
            location_open_wo=db_context["location_open_work_orders"],
        )
        if db_context["unresolved_fields"]:
            log.warning(
                "flow.email.missing_info.db_unresolved",
                email_id=email_id,
                unresolved_fields=db_context["unresolved_fields"],
                asset_reason=db_context["asset_resolution"].get("reason"),
                location_reason=db_context["location_resolution"].get("reason"),
            )
            return {
                "status": "missing_info",
                "missing_fields": db_context["unresolved_fields"],
                "email_id": email_id,
            }

        data["asset"] = db_context["asset_resolution"].get("asset_name") or data.get("asset")
        data["location"] = (
            db_context["location_resolution"].get("location_name") or data.get("location")
        )
        data["db_context"] = db_context

        # -- Step 2: AI assessment ---
        assessment: Dict[str, Any] = {}
        try:
            t1 = time.monotonic()
            assessment = await asyncio.to_thread(self.ai_extraction.extract_all, data)
            assess_ms = round((time.monotonic() - t1) * 1000)
            log.info(
                "flow.email.assessed",
                email_id=email_id,
                criticality=assessment.get("criticality", {}).get("level"),
                safety_critical=assessment.get("safety", {}).get("critical_safety_detected"),
                assess_ms=assess_ms,
            )
        except Exception as exc:
            log.warning("flow.email.assessment_failed", email_id=email_id, exc_info=exc)
            assessment = {"error": str(exc)}

        # Use AI criticality level as priority if available
        ai_priority = (
            assessment.get("criticality", {}).get("level")
            or data.get("priority", "medium")
        )

        # Estimated duration from AI technician block
        ai_duration: float | None = None
        try:
            ai_duration = float(
                assessment.get("technician", {}).get("estimated_duration_hours") or 0
            ) or None
        except (TypeError, ValueError):
            pass

        # -- Step 3: persist to plenum_cafm.work_orders ----------------------
        org_id = None
        if settings.default_organization_id:
            try:
                org_id = int(settings.default_organization_id)
            except (TypeError, ValueError):
                org_id = None

        wo = WorkOrder(
            work_order_id=_wo_id(),
            organization_id=org_id,
            title=data.get("issue_description", ""),
            source=data.get("source", "email"),
            source_reference=data.get("source_reference"),
            asset=data.get("asset"),
            location=data.get("location"),
            issue_description=data.get("issue_description"),
            priority=ai_priority,
            request_type=data.get("request_type", "repair"),
            status="pending_approval",
            approval_type="preparation",
            requester_name=data.get("requester_name"),
            requester_email=str(data.get("requester_email") or ""),
            requester_phone=data.get("requester_phone"),
            # Scheduling is intentionally deferred until final multi-step approval.
            estimated_duration=None,
            manpower={
                "required_skills": (assessment.get("technician") or {}).get("required_skills") or [],
                "asset_type":      (assessment.get("asset_intelligence") or {}).get("asset_type") or "",
            },
        )

        try:
            session.add(wo)
            await session.flush()

            jlog = await create_journey_for_work_order(
                wo.work_order_id, ai_priority, session
            )
            wo.journey_log_id = jlog.jlog_id

            await record_status_change(
                wo.work_order_id, None, "pending_approval", session
            )

            await session.commit()
            await session.refresh(wo)
        except SQLAlchemyError as exc:
            await session.rollback()
            log.error(
                "flow.email.db_error",
                email_id=email_id,
                exc_info=exc,
            )
            raise DatabaseError(f"Failed to create work order from email: {exc}") from exc

        log.info(
            "flow.email.work_order_created",
            email_id=email_id,
            work_order_id=wo.work_order_id,
            journey_log_id=wo.journey_log_id,
            priority=wo.priority,
        )
        return {
            "status": "created",
            "work_order_id": wo.work_order_id,
            "journey_log_id": wo.journey_log_id,
            "priority": wo.priority,
            "assessment_summary": {
                "criticality_level":      assessment.get("criticality", {}).get("level"),
                "response_time_hours":    assessment.get("criticality", {}).get("response_time_hours"),
                "safety_score":           assessment.get("criticality", {}).get("safety_score"),
                "critical_safety":        assessment.get("safety", {}).get("critical_safety_detected"),
                "ppe_required":           assessment.get("safety", {}).get("ppe_required"),
                "compliance_required":    assessment.get("compliance", {}).get("compliance_required"),
                "suggested_timeframe":    assessment.get("schedule", {}).get("suggested_timeframe"),
                "estimated_duration_hrs": assessment.get("technician", {}).get("estimated_duration_hours"),
                "required_skills":        assessment.get("technician", {}).get("required_skills"),
                "sla_deadline_hours":     assessment.get("journey", {}).get("sla_deadline_hours"),
                "parts_needed":           len(assessment.get("parts_list", [])),
                "vendor_type":            (assessment.get("vendors") or [{}])[0].get("vendor_type"),
            },
            "full_assessment": assessment,
        }

    async def stream_from_email(
        self,
        email: Dict[str, Any],
        session: AsyncSession,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Same pipeline as create_from_email but yields step-event dicts at each stage
        so the frontend can display a live step-by-step flow visualization.
        """
        def _evt(step: str, status: str, message: str = "", elapsed_ms: int = 0, data: Dict | None = None) -> Dict:
            return {"step": step, "status": status, "message": message, "elapsed_ms": elapsed_ms, **({"data": data} if data else {})}

        email_id = email.get("id", "")

        # ── Step 0: email received ────────────────────────────────────────────
        yield _evt("email_received", "complete",
                   f"From: {email.get('from')} · Subject: {email.get('subject', '')[:60]}")

        # ── Step 1: classification ────────────────────────────────────────────
        yield _evt("classification", "running", "GPT-4o-mini: is this a maintenance request?")
        t0 = time.monotonic()
        classification = await asyncio.to_thread(self.email_parser.classify, email)
        elapsed = round((time.monotonic() - t0) * 1000)
        is_maint = classification.get("is_maintenance", True)
        yield _evt("classification",
                   "complete" if is_maint else "warning",
                   classification.get("reason", ""),
                   elapsed,
                   {"is_maintenance": is_maint})
        if not is_maint:
            yield {"step": "done", "status": "warning", "result": {"status": "not_maintenance", "reason": classification.get("reason")}}
            return

        # ── Step 2: field extraction ──────────────────────────────────────────
        yield _evt("parsing", "running", "GPT-4o-mini: extracting asset, location, priority, requester…")
        t0 = time.monotonic()
        parsed = await asyncio.to_thread(self.email_parser.parse, email)
        elapsed = round((time.monotonic() - t0) * 1000)
        data = parsed.get("data") or {}
        ready = parsed.get("ready", False)
        yield _evt("parsing",
                   "complete" if ready else "warning",
                   (f"Asset: {data.get('asset', '—')} · Location: {data.get('location', '—')} · "
                    f"Priority: {data.get('priority', '—')} · Requester: {data.get('requester_name', '—')}"),
                   elapsed,
                   {"asset": data.get("asset"), "location": data.get("location"),
                    "priority": data.get("priority"), "requester": data.get("requester_name")})
        if not ready:
            yield {"step": "done", "status": "warning",
                   "result": {"status": "missing_info", "missing_fields": parsed.get("missing_fields", [])}}
            return

        # ── Step 3: DB resolution ─────────────────────────────────────────────
        yield _evt("db_lookup", "running", "Resolving asset code and location against plenum_cafm database…")
        t0 = time.monotonic()
        db_context = await self._build_db_context(data, session)
        elapsed = round((time.monotonic() - t0) * 1000)
        unresolved = db_context["unresolved_fields"]
        ar = db_context["asset_resolution"]
        lr = db_context["location_resolution"]
        open_wos = db_context["asset_open_work_orders"]
        yield _evt("db_lookup",
                   "complete" if not unresolved else "warning",
                   (f"Asset: {ar.get('asset_name', '—')} (score {ar.get('match_score', '—')}) · "
                    f"Location: {lr.get('location_name', '—')} · Open WOs on asset: {open_wos}"),
                   elapsed,
                   {"asset_resolved": ar.get("resolved"), "location_resolved": lr.get("resolved"),
                    "asset_name": ar.get("asset_name"), "open_wos": open_wos})
        if unresolved:
            yield {"step": "done", "status": "warning",
                   "result": {"status": "missing_info", "missing_fields": unresolved}}
            return

        data["asset"]      = ar.get("asset_name") or data.get("asset")
        data["location"]   = lr.get("location_name") or data.get("location")
        data["db_context"] = db_context

        # ── Step 4: 13-block AI assessment ────────────────────────────────────
        yield _evt("ai_assessment", "running", "GPT-4o-mini: running 13-block intelligence assessment…")
        t0 = time.monotonic()
        assessment: Dict[str, Any] = {}
        try:
            assessment = await asyncio.to_thread(self.ai_extraction.extract_all, data)
            elapsed = round((time.monotonic() - t0) * 1000)
            crit = assessment.get("criticality") or {}
            yield _evt("ai_assessment", "complete",
                       (f"Criticality: {crit.get('level', '—')} · "
                        f"Safety score: {crit.get('safety_score', '—')} · "
                        f"Response time: {crit.get('response_time_hours', '—')}h · "
                        f"Parts needed: {len(assessment.get('parts_list') or [])}"),
                       elapsed,
                       {"criticality": crit.get("level"), "safety_score": crit.get("safety_score"),
                        "parts_needed": len(assessment.get("parts_list") or []),
                        "response_time_hours": crit.get("response_time_hours")})
        except Exception as exc:
            yield _evt("ai_assessment", "error", str(exc))

        # ── Step 5: create work order ─────────────────────────────────────────
        yield _evt("wo_create", "running", "Writing work order to plenum_cafm.work_orders…")
        t0 = time.monotonic()

        ai_priority = (assessment.get("criticality") or {}).get("level") or data.get("priority", "medium")
        ai_duration: float | None = None
        try:
            ai_duration = float((assessment.get("technician") or {}).get("estimated_duration_hours") or 0) or None
        except (TypeError, ValueError):
            pass

        org_id = None
        if settings.default_organization_id:
            try:
                org_id = int(settings.default_organization_id)
            except (TypeError, ValueError):
                org_id = None

        wo = WorkOrder(
            work_order_id=_wo_id(),
            organization_id=org_id,
            title=data.get("issue_description", ""),
            source=data.get("source", "email"),
            source_reference=data.get("source_reference"),
            asset=data.get("asset"),
            location=data.get("location"),
            issue_description=data.get("issue_description"),
            priority=ai_priority,
            request_type=data.get("request_type", "repair"),
            status="pending_approval",
            approval_type="preparation",
            requester_name=data.get("requester_name"),
            requester_email=str(data.get("requester_email") or ""),
            requester_phone=data.get("requester_phone"),
            # Scheduling is intentionally deferred until final multi-step approval.
            estimated_duration=None,
            manpower={
                "required_skills": (assessment.get("technician") or {}).get("required_skills") or [],
                "asset_type":      (assessment.get("asset_intelligence") or {}).get("asset_type") or "",
            },
        )

        try:
            session.add(wo)
            await session.flush()
            elapsed = round((time.monotonic() - t0) * 1000)
            yield _evt("wo_create", "complete",
                       f"Created {wo.work_order_id} · Priority: {wo.priority} · Status: pending_approval",
                       elapsed,
                       {"work_order_id": wo.work_order_id, "priority": wo.priority})
        except SQLAlchemyError as exc:
            await session.rollback()
            yield _evt("wo_create", "error", str(exc))
            yield {"step": "done", "status": "error", "result": {"status": "error", "error": str(exc)}}
            return

        # ── Step 6: journey log ───────────────────────────────────────────────
        yield _evt("journey_log", "running", "Initializing journey log, milestones and status history…")
        t0 = time.monotonic()
        try:
            jlog = await create_journey_for_work_order(wo.work_order_id, ai_priority, session)
            wo.journey_log_id = jlog.jlog_id
            await record_status_change(wo.work_order_id, None, "pending_approval", session)
            await asyncio.wait_for(session.commit(), timeout=15.0)
        except asyncio.TimeoutError:
            await session.rollback()
            yield _evt("journey_log", "error", "DB commit timed out after 15s")
            yield {"step": "done", "status": "error", "result": {"status": "error", "error": "DB commit timeout"}}
            return
        except Exception as exc:
            await session.rollback()
            yield _evt("journey_log", "error", str(exc))
            yield {"step": "done", "status": "error", "result": {"status": "error", "error": str(exc)}}
            return
        elapsed = round((time.monotonic() - t0) * 1000)
        yield _evt("journey_log", "complete",
                   f"Journey log {jlog.jlog_id} · Status history recorded",
                   elapsed,
                   {"jlog_id": jlog.jlog_id})

        # ── Done ──────────────────────────────────────────────────────────────
        yield {
            "step": "done",
            "status": "complete",
            "result": {
                "status":         "created",
                "work_order_id":  wo.work_order_id,
                "journey_log_id": wo.journey_log_id,
                "priority":       wo.priority,
                "full_assessment": assessment,
            },
        }
