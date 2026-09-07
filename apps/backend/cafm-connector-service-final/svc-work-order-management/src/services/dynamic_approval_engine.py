"""
Dynamic approval engine — rule scoring + historical similarity + availability.

Replaces static determine_approver() routing for multi-step approval chains.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

# Used when migration 005 tables are not present yet (inline scoring only).
_INLINE_APPROVAL_RULES: List[Dict[str, Any]] = [
    {"dimension": "priority", "match_value": "critical", "match_operator": "eq", "weight": 45},
    {"dimension": "priority", "match_value": "urgent", "match_operator": "eq", "weight": 40},
    {"dimension": "priority", "match_value": "high", "match_operator": "eq", "weight": 25},
    {"dimension": "priority", "match_value": "medium", "match_operator": "eq", "weight": 15},
    {"dimension": "priority", "match_value": "low", "match_operator": "eq", "weight": 10},
    {"dimension": "work_type", "match_value": "hvac", "match_operator": "eq", "weight": 15},
    {"dimension": "work_type", "match_value": "repair", "match_operator": "eq", "weight": 5},
]

# When user_roles.user_id (uuid) cannot join users.id (integer), resolve by email.
_ROLE_EMAIL_FALLBACK: Dict[str, str] = {
    "Maintenance Supervisor": "khalid.alrashid@facility.ae",
    "Operations Manager": "ops.manager@facility.ae",
    "Facilities Director": "facilities.director@facility.ae",
}

_INLINE_THRESHOLD_ROLES: List[tuple[int, int, Optional[int], List[str]]] = [
    (1, 0, 39, ["Maintenance Supervisor"]),
    (2, 40, 69, ["Maintenance Supervisor", "Operations Manager"]),
    (3, 70, None, ["Maintenance Supervisor", "Operations Manager", "Facilities Director"]),
]


def _utc_naive_now() -> datetime:
    """UTC now without tzinfo — matches Azure TIMESTAMP WITHOUT TIME ZONE columns."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _utc_naive(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def normalize_work_order_payload(wo: Dict[str, Any]) -> Dict[str, Any]:
    """Map service WO fields / chat kwargs into engine dimensions."""
    priority = (wo.get("priority") or "medium").strip().lower()
    work_type = (
        wo.get("work_type")
        or wo.get("request_type")
        or "general"
    ).strip().lower()
    building = (wo.get("building") or wo.get("location") or "").strip().lower()
    asset_category = (
        wo.get("asset_category")
        or (str(wo.get("asset") or "").split()[0] if wo.get("asset") else "general")
    ).strip().lower()
    cost_raw = wo.get("estimated_cost") or wo.get("estimated_cost_aed") or 0
    try:
        estimated_cost = float(cost_raw)
    except (TypeError, ValueError):
        estimated_cost = 0.0

    return {
        "work_order_id": wo.get("work_order_id") or wo.get("id"),
        "priority": priority,
        "work_type": work_type,
        "building": building,
        "location": wo.get("location") or building,
        "asset_category": asset_category,
        "estimated_cost": estimated_cost,
        "title": wo.get("title") or wo.get("issue_description"),
    }


class DynamicApprovalEngine:
    """Combines rule-based scoring with historical similarity for approval chains."""

    _schema_caps: Optional[Dict[str, bool]] = None

    HISTORY_LOOKBACK_DAYS = 365
    STRONG_MATCH_THRESHOLD = 85
    PARTIAL_MATCH_THRESHOLD = 60
    STALE_MONTHS = 12
    COST_BAND_TOLERANCE = 0.30

    def __init__(self, aimms_api_url: str = ""):
        self.aimms_api_url = aimms_api_url

    @classmethod
    async def _capabilities(cls, session: AsyncSession) -> Dict[str, bool]:
        if cls._schema_caps is not None:
            return cls._schema_caps

        async def _table(name: str) -> bool:
            row = await session.execute(
                text("""
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'plenum_cafm' AND table_name = :name
                    LIMIT 1
                """),
                {"name": name},
            )
            return row.fetchone() is not None

        async def _column(table: str, column: str) -> bool:
            row = await session.execute(
                text("""
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'
                      AND table_name = :table AND column_name = :column
                    LIMIT 1
                """),
                {"table": table, "column": column},
            )
            return row.fetchone() is not None

        async def _dtype(table: str, column: str) -> Optional[str]:
            row = await session.execute(
                text("""
                    SELECT data_type FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'
                      AND table_name = :table AND column_name = :column
                    LIMIT 1
                """),
                {"table": table, "column": column},
            )
            found = row.fetchone()
            return found[0] if found else None

        users_id_type = await _dtype("users", "id")
        ur_uid_type = await _dtype("user_roles", "user_id")
        uuid_ok = lambda t: bool(t and "uuid" in t.lower())

        caps = {
            "rules": await _table("wo_approval_rules"),
            "thresholds": await _table("wo_approval_thresholds"),
            "suggestions": await _table("wo_approval_suggestions"),
            "wo_estimated_cost": await _column("work_orders", "estimated_cost"),
            "wo_asset_category": await _column("work_orders", "asset_category"),
            "ar_step_order": await _column("wo_approval_requests", "step_order"),
            "ar_level": await _column("wo_approval_requests", "level"),
            "ar_risk_score": await _column("wo_approval_requests", "risk_score"),
            "ar_match_score": await _column("wo_approval_requests", "match_score"),
            "ar_suggestion_source": await _column("wo_approval_requests", "suggestion_source"),
            "ar_unblocked_at": await _column("wo_approval_requests", "unblocked_at"),
            "users_role_column": await _column("users", "role"),
            "user_roles_linked": bool(
                await _table("user_roles")
                and users_id_type
                and ur_uid_type
                and uuid_ok(users_id_type) == uuid_ok(ur_uid_type)
            ),
        }
        cls._schema_caps = caps
        if not caps["rules"] or not caps["wo_estimated_cost"]:
            log.warning(
                "dynamic_approval.schema_incomplete",
                caps=caps,
                hint="Run: cd svc-work-order-management && alembic upgrade head",
            )
        if not caps["user_roles_linked"]:
            log.warning(
                "dynamic_approval.user_roles_skipped",
                users_id_type=users_id_type,
                user_roles_user_id_type=ur_uid_type,
                hint="Resolving approvers by email / users.role instead of user_roles join",
            )
        return caps

    @staticmethod
    def _approval_step_order_sql(caps: Dict[str, bool], alias: str = "ar") -> str:
        if caps.get("ar_step_order") and caps.get("ar_level"):
            return f"COALESCE({alias}.step_order, {alias}.level, 1)"
        if caps.get("ar_step_order"):
            return f"COALESCE({alias}.step_order, 1)"
        if caps.get("ar_level"):
            return f"COALESCE({alias}.level, 1)"
        return "1"

    async def _run_step(
        self,
        step: str,
        wo: Dict[str, Any],
        coro,
    ):
        """Run one suggest_chain step; log and re-raise on failure."""
        try:
            return await coro
        except Exception as exc:
            log.error(
                "dynamic_approval.step_failed",
                step=step,
                work_order_id=wo.get("work_order_id"),
                work_type=wo.get("work_type"),
                priority=wo.get("priority"),
                location=wo.get("location"),
                asset_category=wo.get("asset_category"),
                error=str(exc),
                exc_type=type(exc).__name__,
                exc_info=True,
            )
            raise

    async def suggest_chain(
        self,
        session: AsyncSession,
        work_order: Dict[str, Any],
        *,
        persist: bool = True,
    ) -> Dict[str, Any]:
        wo = normalize_work_order_payload(work_order)

        risk_score = await self._run_step(
            "compute_risk_score", wo, self._compute_risk_score(session, wo)
        )
        rule_chain = await self._run_step(
            "build_rule_chain", wo, self._build_rule_chain(session, risk_score)
        )
        if not rule_chain:
            rule_chain = self._demo_fallback_chain(risk_score)
            log.warning(
                "dynamic_approval.using_fallback_chain",
                work_order_id=wo.get("work_order_id"),
                risk_score=risk_score,
                hint="Run: python -m scripts.seed_approval_demo",
            )
        similar_wos = await self._run_step(
            "find_similar_wos", wo, self._find_similar_wos(session, wo)
        )
        previous_processes = await self._run_step(
            "load_previous_approval_processes",
            wo,
            self._load_previous_approval_processes(session, similar_wos[:5]),
        )
        history = similar_wos  # backward-compatible alias

        confidence = "rules_only"
        source = "rules"
        match_score = 0
        reason = f"No similar work orders. Risk score {risk_score}/125."
        chain: List[Dict[str, Any]] = rule_chain
        historical_alternative_chain: List[Dict[str, Any]] = []
        based_on_work_order_id: Optional[str] = None

        if similar_wos:
            top = similar_wos[0]
            match_score = int(top.get("match_score") or 0)
            top_status = (top.get("final_status") or top.get("status") or "").lower()
            based_on_work_order_id = top.get("work_order_id")
            historical_alternative_chain = (
                previous_processes[0].get("approval_chain_followed", [])
                if previous_processes
                else await self._reuse_historical_chain(session, top)
            )

            if top_status == "rejected":
                confidence = "partial"
                source = "hybrid"
                chain = rule_chain
                reason = (
                    f"Top precedent {based_on_work_order_id} ({match_score}% match) was rejected; "
                    f"using rule-based chain. Notes: {top.get('rejection_notes') or 'n/a'}."
                )
                if risk_score >= 40:
                    chain = await self._escalate_chain_one_level(session, chain, risk_score)
            elif match_score >= self.STRONG_MATCH_THRESHOLD:
                chain = list(historical_alternative_chain) or await self._reuse_historical_chain(
                    session, top
                )
                confidence = "high"
                source = "history"
                reason = self._build_history_reason(similar_wos, previous_processes)
            elif match_score >= self.PARTIAL_MATCH_THRESHOLD:
                confidence = "partial"
                source = "hybrid"
                chain = rule_chain
                reason = (
                    f"Partial precedent ({match_score}%) on {based_on_work_order_id}. "
                    "Recommended rule-based chain; past approval process shown as alternative."
                )
        else:
            log.info(
                "dynamic_approval.no_history",
                work_order_id=wo.get("work_order_id"),
                risk_score=risk_score,
            )

        chain = await self._run_step(
            "check_availability", wo, self._check_availability(session, chain)
        )
        if historical_alternative_chain:
            historical_alternative_chain = await self._run_step(
                "check_availability_historical",
                wo,
                self._check_availability(session, historical_alternative_chain),
            )

        if persist and wo.get("work_order_id"):
            await self._run_step(
                "save_suggestion",
                wo,
                self._save_suggestion(
                    session,
                    wo,
                    chain,
                    source,
                    confidence,
                    match_score,
                    risk_score,
                ),
            )

        log.info(
            "dynamic_approval.suggest_chain",
            work_order_id=wo.get("work_order_id"),
            confidence=confidence,
            source=source,
            risk_score=risk_score,
            match_score=match_score,
            chain_steps=len(chain),
            top3=self._format_top3_log(chain),
        )

        auto_suggestion = self._build_auto_suggestion(
            wo=wo,
            chain=chain,
            confidence=confidence,
            source=source,
            match_score=match_score,
            risk_score=risk_score,
            reason=reason,
            previous_processes=previous_processes,
            based_on_work_order_id=based_on_work_order_id,
            historical_alternative_chain=historical_alternative_chain,
        )

        return {
            "confidence": confidence,
            "source": source,
            "match_score": match_score,
            "risk_score": risk_score,
            "chain": chain,
            "reason": reason,
            "historical_matches": similar_wos[:3],
            "previous_approval_processes": previous_processes,
            "historical_alternative_chain": historical_alternative_chain,
            "based_on_work_order_id": based_on_work_order_id,
            "auto_suggestion": auto_suggestion,
        }

    @staticmethod
    def _format_top3_log(chain: List[Dict[str, Any]]) -> str:
        parts = []
        for i, step in enumerate(chain[:3], start=1):
            name = step.get("name") or step.get("email") or "?"
            role = step.get("role") or "?"
            parts.append(f"#{i} {name} ({role})")
        return " | ".join(parts) if parts else "(empty chain)"

    async def _compute_risk_score(self, session: AsyncSession, wo: Dict[str, Any]) -> int:
        caps = await self._capabilities(session)
        if not caps["rules"]:
            score = 0
            for rule in _INLINE_APPROVAL_RULES:
                if self._rule_matches(rule, wo):
                    score += int(rule.get("weight") or 0)
            return score

        result = await session.execute(
            text("SELECT * FROM plenum_cafm.wo_approval_rules WHERE active = TRUE")
        )
        rules = [dict(r._mapping) for r in result.fetchall()]
        if not rules:
            return sum(
                int(r.get("weight") or 0)
                for r in _INLINE_APPROVAL_RULES
                if self._rule_matches(r, wo)
            )
        score = 0
        for rule in rules:
            if self._rule_matches(rule, wo):
                score += int(rule.get("weight") or 0)
        return score

    def _rule_matches(self, rule: Dict[str, Any], wo: Dict[str, Any]) -> bool:
        dim = (rule.get("dimension") or "").lower()
        op = (rule.get("match_operator") or "eq").lower()

        if dim == "cost":
            cost = float(wo.get("estimated_cost") or 0)
            low = float(rule.get("match_threshold") or 0)
            high = rule.get("match_threshold_upper")
            if op == "lte":
                return cost <= low if low > 0 else cost <= 5000
            if op == "gte":
                return cost >= low
            if op == "between" and high is not None:
                return low <= cost < float(high)
            return False

        if dim == "priority":
            val = (wo.get("priority") or "").lower()
            target = (rule.get("match_value") or "").lower()
            return op == "eq" and val == target

        if dim == "work_type":
            val = (wo.get("work_type") or "").lower()
            target = (rule.get("match_value") or "").lower()
            return op == "eq" and (val == target or target in val)

        if dim == "building":
            loc = (wo.get("building") or wo.get("location") or "").lower()
            target = (rule.get("match_value") or "").lower()
            return op == "eq" and target in loc

        return False

    async def _find_similar_wos(
        self,
        session: AsyncSession,
        wo: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Distinct similar WOs that completed an approval process (last 12 months)."""
        caps = await self._capabilities(session)
        cutoff = _utc_naive_now() - timedelta(days=self.HISTORY_LOOKBACK_DAYS)
        cost = float(wo.get("estimated_cost") or 0)
        cost_den = cost if cost > 0 else 1.0
        ar_order = self._approval_step_order_sql(caps)

        asset_cat_col = (
            "wo.asset_category"
            if caps["wo_asset_category"]
            else "NULL::varchar"
        )
        asset_match = (
            f"CASE WHEN LOWER(COALESCE({asset_cat_col}, '')) = LOWER(:asset_category) THEN 25 "
            f"WHEN LOWER(COALESCE(wo.asset, '')) LIKE LOWER(:asset_like) THEN 15 ELSE 0 END"
            if caps["wo_asset_category"]
            else "CASE WHEN LOWER(COALESCE(wo.asset, '')) LIKE LOWER(:asset_like) THEN 15 ELSE 0 END"
        )
        cost_match = (
            "CASE WHEN :cost > 0 AND wo.estimated_cost IS NOT NULL "
            "AND ABS(wo.estimated_cost - :cost) / :cost_den < :cost_tol THEN 15 ELSE 0 END"
            if caps["wo_estimated_cost"]
            else "0"
        )
        estimated_sel = "wo.estimated_cost" if caps["wo_estimated_cost"] else "NULL::numeric AS estimated_cost"
        asset_sel = (
            "wo.asset_category"
            if caps["wo_asset_category"]
            else "NULL::varchar AS asset_category"
        )

        exclude_wo = wo.get("work_order_id")
        exclude_clause = "AND wo.work_order_id != :exclude_wo" if exclude_wo else ""

        params: Dict[str, Any] = {
            "work_type": wo.get("work_type") or "general",
            "asset_category": wo.get("asset_category") or "general",
            "asset_like": f"%{(wo.get('asset_category') or wo.get('asset') or 'hvac')[:20]}%",
            "location": (wo.get("location") or "")[:255],
            "cost": cost,
            "cost_den": cost_den,
            "cost_tol": self.COST_BAND_TOLERANCE,
            "priority": wo.get("priority") or "medium",
            "cutoff": cutoff,
        }
        if exclude_wo:
            params["exclude_wo"] = exclude_wo

        result = await session.execute(
            text(f"""
                SELECT
                    wo.work_order_id,
                    wo.title,
                    wo.request_type AS work_type,
                    wo.location,
                    wo.priority,
                    wo.asset,
                    {asset_sel},
                    {estimated_sel},
                    wo.created_at,
                    (
                      CASE WHEN LOWER(wo.request_type) = LOWER(:work_type) THEN 30 ELSE 0 END +
                      {asset_match} +
                      CASE WHEN LOWER(COALESCE(wo.location, '')) = LOWER(:location) THEN 20 ELSE 0 END +
                      {cost_match} +
                      CASE WHEN LOWER(wo.priority) = LOWER(:priority) THEN 10 ELSE 0 END
                    ) AS match_score,
                    (
                      SELECT ar.status
                      FROM plenum_cafm.wo_approval_requests ar
                      WHERE ar.work_order_id = wo.work_order_id
                      ORDER BY {ar_order} DESC
                      LIMIT 1
                    ) AS final_status,
                    (
                      SELECT ar.notes
                      FROM plenum_cafm.wo_approval_requests ar
                      WHERE ar.work_order_id = wo.work_order_id
                        AND ar.status = 'rejected'
                      ORDER BY ar.responded_at DESC NULLS LAST
                      LIMIT 1
                    ) AS rejection_notes
                FROM plenum_cafm.work_orders wo
                WHERE wo.created_at > :cutoff
                  {exclude_clause}
                  AND EXISTS (
                    SELECT 1 FROM plenum_cafm.wo_approval_requests ar
                    WHERE ar.work_order_id = wo.work_order_id
                      AND ar.status IN ('approved', 'rejected')
                  )
                ORDER BY match_score DESC, wo.created_at DESC
                LIMIT 10
            """),
            params,
        )
        rows = [dict(r._mapping) for r in result.fetchall()]
        return [r for r in rows if int(r.get("match_score") or 0) >= self.PARTIAL_MATCH_THRESHOLD]

    async def _load_previous_approval_processes(
        self,
        session: AsyncSession,
        similar_wos: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        """Full multi-step approval history for each similar WO (what users actually followed)."""
        processes: List[Dict[str, Any]] = []
        for ref in similar_wos:
            wo_id = ref.get("work_order_id")
            if not wo_id:
                continue
            chain = await self._reuse_historical_chain(session, ref)
            caps = await self._capabilities(session)
            ar_order = self._approval_step_order_sql(caps)
            step_cols = []
            if caps["ar_step_order"]:
                step_cols.append("ar.step_order")
            if caps["ar_level"]:
                step_cols.append("ar.level")
            step_select = ", ".join(step_cols) + "," if step_cols else ""

            steps_detail = await session.execute(
                text(f"""
                    SELECT
                        {step_select}
                        ar.approver,
                        ar.status,
                        ar.requested_at,
                        ar.responded_at,
                        ar.notes,
                        u.full_name
                    FROM plenum_cafm.wo_approval_requests ar
                    LEFT JOIN plenum_cafm.users u
                      ON u.email = ar.approver OR CAST(u.id AS TEXT) = ar.approver
                    WHERE ar.work_order_id = :wo_id
                    ORDER BY {ar_order} ASC, ar.requested_at ASC
                """),
                {"wo_id": wo_id},
            )
            steps = []
            first_at = None
            last_at = None
            for row in steps_detail.fetchall():
                r = dict(row._mapping)
                steps.append({
                    "step": r.get("step_order") or r.get("level") or len(steps) + 1,
                    "approver": r.get("full_name") or r.get("approver"),
                    "status": r.get("status"),
                    "requested_at": r["requested_at"].isoformat() if r.get("requested_at") else None,
                    "responded_at": r["responded_at"].isoformat() if r.get("responded_at") else None,
                    "notes": r.get("notes"),
                })
                if r.get("requested_at") and first_at is None:
                    first_at = r["requested_at"]
                if r.get("responded_at"):
                    last_at = r["responded_at"]

            duration_hours = None
            if first_at and last_at:
                delta = _utc_naive(last_at) - _utc_naive(first_at)  # type: ignore[operator]
                duration_hours = round(delta.total_seconds() / 3600, 1)

            processes.append({
                "work_order_id": wo_id,
                "title": ref.get("title"),
                "match_score": ref.get("match_score"),
                "work_type": ref.get("work_type"),
                "location": ref.get("location"),
                "priority": ref.get("priority"),
                "final_status": ref.get("final_status"),
                "approval_chain_followed": chain,
                "approval_steps": steps,
                "total_approval_hours": duration_hours,
                "chain_summary": self._chain_summary(chain),
            })
        return processes

    @staticmethod
    def _chain_summary(chain: List[Dict[str, Any]]) -> str:
        if not chain:
            return ""
        return " → ".join(
            f"{s.get('name', '?')} ({s.get('role', 'Approver')})" for s in chain
        )

    def _build_auto_suggestion(
        self,
        *,
        wo: Dict[str, Any],
        chain: List[Dict[str, Any]],
        confidence: str,
        source: str,
        match_score: int,
        risk_score: int,
        reason: str,
        previous_processes: List[Dict[str, Any]],
        based_on_work_order_id: Optional[str],
        historical_alternative_chain: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """User-facing auto-suggestion payload for chat / API consumers."""
        recommended_summary = self._chain_summary(chain)
        confidence_label = {
            "high": "HIGH",
            "partial": "PARTIAL",
            "rules_only": "RULES ONLY",
        }.get(confidence, confidence.upper())

        past_lines: List[str] = []
        for proc in previous_processes[:3]:
            pct = proc.get("match_score", "?")
            wo_ref = proc.get("work_order_id", "?")
            summary = proc.get("chain_summary") or "—"
            hours = proc.get("total_approval_hours")
            outcome = proc.get("final_status", "unknown")
            time_note = f", {hours}h end-to-end" if hours is not None else ""
            past_lines.append(
                f"• {wo_ref} ({pct}% match, {outcome}): {summary}{time_note}"
            )

        message_parts = [
            "Suggested approval chain (auto-generated):",
            recommended_summary or "(no approvers resolved — check role assignments in users/roles)",
            f"Confidence: {confidence_label} ({match_score}% history match, risk {risk_score}/125).",
            reason,
        ]
        if past_lines:
            message_parts.append("")
            message_parts.append("Previous similar work orders followed:")
            message_parts.extend(past_lines)
        if (
            historical_alternative_chain
            and confidence != "high"
            and self._chain_summary(historical_alternative_chain) != recommended_summary
        ):
            alt = self._chain_summary(historical_alternative_chain)
            message_parts.append("")
            message_parts.append(
                f"Past process on {based_on_work_order_id or 'best match'} used: {alt}"
            )
        message_parts.append("")
        message_parts.append(
            "Confirm this chain when creating the work order, or say if you want different approvers."
        )

        return {
            "message": "\n".join(message_parts),
            "recommended_chain_summary": recommended_summary,
            "recommended_steps": chain,
            "confidence_label": confidence_label,
            "match_score": match_score,
            "risk_score": risk_score,
            "source": source,
            "follow_pattern_from": based_on_work_order_id,
            "similar_cases_count": len(previous_processes),
            "ask_user_to_confirm": True,
        }

    def _build_history_reason(
        self,
        history: List[Dict[str, Any]],
        previous_processes: List[Dict[str, Any]],
    ) -> str:
        if not history:
            return "No historical precedent."
        top = history[0]
        proc = previous_processes[0] if previous_processes else {}
        summary = proc.get("chain_summary") or self._chain_summary(
            proc.get("approval_chain_followed") or []
        )
        hours = proc.get("total_approval_hours")
        time_note = f", typical approval time {hours}h" if hours is not None else ""
        approved_like = sum(
            1 for p in previous_processes[:5]
            if (p.get("final_status") or "").lower() == "approved"
        )
        return (
            f"Strong match ({top.get('match_score', 0)}%) to {top.get('work_order_id')}. "
            f"Past process: {summary}. "
            f"{approved_like} of {min(5, len(previous_processes))} similar WOs completed approval"
            f"{time_note}."
        )

    @staticmethod
    def _role_label_for_email(email: str) -> str:
        lower = (email or "").lower()
        for role, em in _ROLE_EMAIL_FALLBACK.items():
            if em.lower() == lower:
                return role
        return "Approver"

    async def _find_user_for_role(
        self,
        session: AsyncSession,
        role: str,
    ) -> Optional[Dict[str, Any]]:
        """Resolve an active user for a role name without uuid/int join errors."""
        caps = await self._capabilities(session)

        if caps.get("user_roles_linked"):
            user_row = await session.execute(
                text("""
                    SELECT u.id, u.full_name, u.email, r.name AS role
                    FROM plenum_cafm.users u
                    JOIN plenum_cafm.user_roles ur ON ur.user_id = u.id
                    JOIN plenum_cafm.roles r ON r.id = ur.role_id
                    WHERE LOWER(r.name) = LOWER(:role)
                      AND LOWER(u.status) = 'active'
                    ORDER BY (
                        SELECT COUNT(*) FROM plenum_cafm.wo_approval_requests ar
                        WHERE ar.approver = u.email AND ar.status = 'pending'
                    ) ASC
                    LIMIT 1
                """),
                {"role": role},
            )
            row = user_row.fetchone()
            if row:
                return dict(row._mapping)

        if caps.get("users_role_column"):
            legacy = await session.execute(
                text("""
                    SELECT id, full_name, email, role AS role
                    FROM plenum_cafm.users
                    WHERE LOWER(role) = LOWER(:role)
                      AND LOWER(status) = 'active'
                    LIMIT 1
                """),
                {"role": role},
            )
            row = legacy.fetchone()
            if row:
                return dict(row._mapping)

        fallback_email = _ROLE_EMAIL_FALLBACK.get(role)
        if fallback_email:
            by_email = await session.execute(
                text("""
                    SELECT id, full_name, email
                    FROM plenum_cafm.users
                    WHERE LOWER(email) = LOWER(:email)
                      AND LOWER(status) = 'active'
                    LIMIT 1
                """),
                {"email": fallback_email},
            )
            row = by_email.fetchone()
            if row:
                m = dict(row._mapping)
                m["role"] = role
                return m

        any_user = await session.execute(
            text("""
                SELECT id, full_name, email
                FROM plenum_cafm.users
                WHERE LOWER(status) = 'active'
                ORDER BY id ASC
                LIMIT 1
            """)
        )
        row = any_user.fetchone()
        if row:
            m = dict(row._mapping)
            m["role"] = role
            return m
        return None

    async def _reuse_historical_chain(
        self,
        session: AsyncSession,
        top_match: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        wo_id = top_match["work_order_id"]
        caps = await self._capabilities(session)
        ar_order = self._approval_step_order_sql(caps)
        step_cols = ["ar.approver"]
        if caps["ar_step_order"]:
            step_cols.append("ar.step_order")
        if caps["ar_level"]:
            step_cols.append("ar.level")
        step_select = ", ".join(step_cols)

        result = await session.execute(
            text(f"""
                SELECT {step_select},
                       u.id AS user_id, u.full_name, u.email
                FROM plenum_cafm.wo_approval_requests ar
                LEFT JOIN plenum_cafm.users u
                  ON u.email = ar.approver OR CAST(u.id AS TEXT) = ar.approver
                WHERE ar.work_order_id = :wo_id
                ORDER BY {ar_order} ASC, ar.requested_at ASC
            """),
            {"wo_id": wo_id},
        )
        chain: List[Dict[str, Any]] = []
        seen_steps: set[int] = set()
        for i, row in enumerate(result.fetchall()):
            r = dict(row._mapping)
            step = int(r.get("step_order") or r.get("level") or (i + 1))
            if step in seen_steps:
                continue
            seen_steps.add(step)
            approver_email = r.get("email") or r.get("approver")
            chain.append({
                "user_id": str(r["user_id"]) if r.get("user_id") else None,
                "name": r.get("full_name") or approver_email,
                "email": approver_email,
                "role": self._role_label_for_email(str(approver_email or "")),
                "step": step,
            })
        return chain

    async def _build_rule_chain(
        self,
        session: AsyncSession,
        score: int,
    ) -> List[Dict[str, Any]]:
        caps = await self._capabilities(session)
        roles: List[str] = []
        if caps["thresholds"]:
            row = await session.execute(
                text("""
                    SELECT level, required_roles
                    FROM plenum_cafm.wo_approval_thresholds
                    WHERE :score >= min_score
                      AND (:score <= max_score OR max_score IS NULL)
                    ORDER BY level DESC
                    LIMIT 1
                """),
                {"score": score},
            )
            threshold = row.fetchone()
            if threshold:
                roles = list(threshold.required_roles or [])
        else:
            for _level, min_s, max_s, req_roles in reversed(_INLINE_THRESHOLD_ROLES):
                if score >= min_s and (max_s is None or score <= max_s):
                    roles = list(req_roles)
                    break
        if not roles:
            return []
        chain: List[Dict[str, Any]] = []
        for i, role in enumerate(roles):
            u = await self._find_user_for_role(session, role)
            if u:
                chain.append({
                    "user_id": str(u["id"]),
                    "name": u.get("full_name") or u.get("email"),
                    "email": u.get("email"),
                    "role": u.get("role") or role,
                    "step": i + 1,
                })
        return chain

    async def _escalate_chain_one_level(
        self,
        session: AsyncSession,
        chain: List[Dict[str, Any]],
        risk_score: int,
    ) -> List[Dict[str, Any]]:
        """Add Facilities Director step when precedent was rejected and risk is elevated."""
        escalated = await self._build_rule_chain(session, min(risk_score + 30, 125))
        if len(escalated) > len(chain):
            return escalated
        return chain

    async def _check_availability(
        self,
        session: AsyncSession,
        chain: List[Dict[str, Any]],
    ) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []

        for step in chain:
            email = step.get("email")
            if not email:
                out.append(step)
                continue

            user_row = await session.execute(
                text("""
                    SELECT u.id, u.full_name, u.email, u.status
                    FROM plenum_cafm.users u
                    WHERE u.email = :email OR CAST(u.id AS TEXT) = :email
                    LIMIT 1
                """),
                {"email": email},
            )
            user = user_row.fetchone()
            available = user and (user.status or "").lower() == "active"

            if available:
                out.append(step)
                continue

            role_name = step.get("role") or "Maintenance Supervisor"
            rep_user = await self._find_user_for_role(session, role_name)
            if rep_user and (rep_user.get("email") or "").lower() != (email or "").lower():
                out.append({
                    **step,
                    "user_id": str(rep_user["id"]),
                    "name": rep_user.get("full_name"),
                    "email": rep_user.get("email"),
                    "role": rep_user.get("role") or role_name,
                    "note": f"Original approver unavailable; routed to {rep_user.get('full_name')}",
                })
            else:
                out.append(step)

        return out

    async def _save_suggestion(
        self,
        session: AsyncSession,
        wo: Dict[str, Any],
        chain: List[Dict[str, Any]],
        source: str,
        confidence: str,
        match_score: int,
        risk_score: int,
    ) -> None:
        caps = await self._capabilities(session)
        if not caps["suggestions"]:
            return

        fingerprint = hashlib.sha256(
            (
                f"{wo.get('work_type')}|{wo.get('priority')}|"
                f"{wo.get('location')}|{self._cost_band(float(wo.get('estimated_cost') or 0))}"
            ).encode()
        ).hexdigest()[:64]

        await session.execute(
            text("""
                INSERT INTO plenum_cafm.wo_approval_suggestions
                  (work_order_id, fingerprint, source, confidence,
                   match_score, risk_score, suggested_chain)
                VALUES (:wo_id, :fp, :source, :confidence, :match, :risk, CAST(:chain AS jsonb))
            """),
            {
                "wo_id": wo["work_order_id"],
                "fp": fingerprint,
                "source": source,
                "confidence": confidence,
                "match": match_score,
                "risk": risk_score,
                "chain": json.dumps(chain),
            },
        )
        await session.flush()

    @staticmethod
    def _demo_fallback_chain(risk_score: int) -> List[Dict[str, Any]]:
        """Used when no users/roles resolve from DB — keeps UI and agent flow working."""
        chain = [
            {
                "step": 1,
                "name": "Khalid Al Rashid",
                "email": "khalid.alrashid@facility.ae",
                "role": "Maintenance Supervisor",
            },
            {
                "step": 2,
                "name": "Sara Operations",
                "email": "ops.manager@facility.ae",
                "role": "Operations Manager",
            },
        ]
        if risk_score >= 70:
            chain.append({
                "step": 3,
                "name": "Omar Facilities",
                "email": "facilities.director@facility.ae",
                "role": "Facilities Director",
            })
        return chain

    @staticmethod
    def _cost_band(cost: float) -> str:
        if cost < 5000:
            return "tier1"
        if cost < 25000:
            return "tier2"
        if cost < 100000:
            return "tier3"
        return "tier4"
