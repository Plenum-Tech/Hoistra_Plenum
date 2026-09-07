"""Quality / Adversary — independent falsification of actionable Worker outputs.

Outcomes (PRD):
  approved  → pass to Approvals queue / act
  flagged   → return to Worker with specific error
  escalated → PM with explanation (also for confidence < 0.85)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger
from ..engines.compliance.lifecycle import days_until
from ..models import ComplianceCertificate
from ..shared.approvals import write_audit

log = get_logger(__name__)

AdversaryOutcome = Literal["approved", "flagged", "escalated"]
CONFIDENCE_GATE = 0.85


@dataclass
class AdversaryResult:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    checks: dict[str, Any] = field(default_factory=dict)
    outcome: AdversaryOutcome = "approved"

    def __post_init__(self) -> None:
        if self.outcome == "approved" and not self.approved:
            self.outcome = self._infer_outcome()
        elif self.approved:
            self.outcome = "approved"

    def _infer_outcome(self) -> AdversaryOutcome:
        # Missing critical inputs → escalate to PM; logic mismatches → flag Worker
        if any(
            r.startswith("missing_") or r.endswith("_not_found") or "confidence_below" in r
            for r in self.reasons
        ):
            return "escalated"
        return "flagged"

    def to_dict(self) -> dict[str, Any]:
        return {
            "approved": self.approved,
            "outcome": self.outcome,
            "reasons": self.reasons,
            "checks": self.checks,
        }


def apply_confidence_gate(
    result: AdversaryResult,
    *,
    confidence: float | None,
) -> AdversaryResult:
    """PRD: Adversary also fires when actionable output confidence < 85%."""
    if confidence is None:
        return result
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        return result
    result.checks["confidence"] = conf
    if conf < CONFIDENCE_GATE:
        result.approved = False
        result.reasons = list(result.reasons) + [f"confidence_below_{CONFIDENCE_GATE}"]
        result.outcome = "escalated"
    return result


def finalize_adversary(
    result: AdversaryResult,
    *,
    confidence: float | None = None,
) -> AdversaryResult:
    result = apply_confidence_gate(result, confidence=confidence)
    if result.approved:
        result.outcome = "approved"
    elif result.outcome == "approved":
        result.outcome = result._infer_outcome()
    return result


async def validate_lapsed_flag(
    session: AsyncSession,
    *,
    certificate: ComplianceCertificate,
    confidence: float | None = None,
) -> AdversaryResult:
    """Confirm no successor certificate exists before insurance risk / Lapsed flag."""
    reasons: list[str] = []
    checks: dict[str, Any] = {}

    if not certificate.expiry_date:
        return finalize_adversary(
            AdversaryResult(approved=False, reasons=["missing_expiry_date"]),
            confidence=confidence,
        )

    dte = days_until(certificate.expiry_date)
    checks["days_to_expiry"] = dte
    # Insurance risk / Lapsed adversary only after calendar expiry (past expiry)
    if dte is not None and dte >= 0:
        return finalize_adversary(
            AdversaryResult(
                approved=False,
                reasons=["expiry_not_in_lapsed_window"],
                checks=checks,
            ),
            confidence=confidence,
        )

    # Draft siblings (raw_metadata.draft) must not suppress Lapsed PM alerts —
    # only confirmed certificates count as successors.
    q = select(ComplianceCertificate).where(
        ComplianceCertificate.id != certificate.id,
        ComplianceCertificate.certificate_type_code == certificate.certificate_type_code,
        ComplianceCertificate.cert_scope == certificate.cert_scope,
    )
    if certificate.cert_scope == "Building" and certificate.asset_id:
        q = q.where(ComplianceCertificate.asset_id == certificate.asset_id)
    if certificate.cert_scope == "Vendor" and certificate.vendor_id:
        q = q.where(ComplianceCertificate.vendor_id == certificate.vendor_id)
    if certificate.site_id and certificate.cert_scope == "Building":
        q = q.where(
            (ComplianceCertificate.site_id == certificate.site_id)
            | (ComplianceCertificate.site_id.is_(None))
        )

    candidates = list((await session.execute(q)).scalars().all())
    today = date.today()

    def _is_draft(c: ComplianceCertificate) -> bool:
        meta = c.raw_metadata or {}
        return bool(meta.get("draft") or meta.get("requires_pm_confirmation"))

    successors = [
        c
        for c in candidates
        if c.expiry_date
        and c.expiry_date > today
        and c.id != certificate.id
        and not _is_draft(c)
    ]
    checks["successor_count"] = len(successors)
    if successors:
        checks["successor_ids"] = [str(s.id) for s in successors]
        return finalize_adversary(
            AdversaryResult(
                approved=False,
                reasons=["successor_certificate_exists"],
                checks=checks,
            ),
            confidence=confidence,
        )

    return finalize_adversary(
        AdversaryResult(approved=True, checks=checks),
        confidence=confidence,
    )


def validate_block_state_change(
    *,
    accreditation_type: str | None,
    expiry_date: date | None,
    proposed_block_state: str,
    confidence: float | None = None,
) -> AdversaryResult:
    """Confirm accreditation type and expiry logic before vendor block."""
    reasons: list[str] = []
    if not accreditation_type:
        reasons.append("missing_accreditation_type")
    dte = days_until(expiry_date)
    if dte is None:
        reasons.append("missing_expiry_date")
    elif dte > 0 and proposed_block_state == "Blocked":
        reasons.append("expiry_still_in_future_block_not_allowed")
    if proposed_block_state not in {"Blocked", "Clear"}:
        reasons.append("invalid_block_state")
    if reasons:
        return finalize_adversary(
            AdversaryResult(
                approved=False,
                reasons=reasons,
                checks={"days_to_expiry": dte},
            ),
            confidence=confidence,
        )
    return finalize_adversary(
        AdversaryResult(
            approved=True,
            checks={"days_to_expiry": dte, "accreditation_type": accreditation_type},
        ),
        confidence=confidence,
    )


def validate_invoice_flag_delta(
    *,
    claimed_delta_gbp: float,
    line: dict[str, Any],
    work_order: dict[str, Any] | None,
    labour_day_rate: float | None = None,
    labour_hour_rate: float | None = None,
    parts_framework: dict[str, Any] | None = None,
    tolerance_gbp: float = 1.0,
    confidence: float | None = None,
) -> AdversaryResult:
    """B3 — independently re-compute £ delta for flags above threshold before PM queue.

    The hourly rate comes from the same resolver the matcher used. Independent arithmetic is
    the point of this check, but it has to be arithmetic on the same contracted rate — a
    different one here reads as ``delta_arithmetic_mismatch`` and silently blocks a genuine
    overcharge from ever reaching the PM.
    """
    from ..engines.contract_performance.parameters import contracted_hourly_rate

    contracted_hourly = contracted_hourly_rate(labour_day_rate, labour_hour_rate)
    reasons: list[str] = []
    recomputed = 0.0
    checks: dict[str, Any] = {"claimed_delta_gbp": claimed_delta_gbp}

    def _f(v: Any) -> float | None:
        if v is None or v == "":
            return None
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if work_order is None and not (line.get("wo_code") or line.get("work_order_code")):
        return finalize_adversary(
            AdversaryResult(approved=False, reasons=["missing_work_order_for_delta"], checks=checks),
            confidence=confidence,
        )
    if work_order is None:
        amount = _f(line.get("amount") or line.get("line_total")) or 0.0
        recomputed = amount
        checks["basis"] = "missing_wo_full_amount"
    else:
        inv_hours = _f(line.get("labour_hours") or line.get("hours"))
        attendance = _f(
            work_order.get("attendance_hours")
            or work_order.get("labour_hours")
            or work_order.get("duration_hours")
        )
        if inv_hours is not None and attendance is not None and attendance > 0:
            if abs(inv_hours - attendance) / attendance > 0.10:
                recomputed += abs(inv_hours - attendance) * (contracted_hourly or 0.0)

        inv_parts = _f(line.get("parts_cost") or line.get("parts_amount"))
        framework_parts = None
        part_code = line.get("part_code")
        if parts_framework and part_code and part_code in parts_framework:
            framework_parts = _f(parts_framework[part_code])
        if framework_parts is None:
            framework_parts = _f(work_order.get("parts_cost") or work_order.get("parts_estimated"))
        if inv_parts is not None and framework_parts is not None and framework_parts > 0:
            if abs(inv_parts - framework_parts) / framework_parts > 0.05:
                recomputed += abs(inv_parts - framework_parts)

        inv_rate = _f(line.get("labour_rate") or line.get("hourly_rate"))
        if inv_rate is not None and contracted_hourly is not None:
            if inv_rate > contracted_hourly + 0.01:
                hours = inv_hours or attendance or 1.0
                recomputed += (inv_rate - contracted_hourly) * hours

        if recomputed == 0.0:
            recomputed = _f(line.get("amount") or line.get("line_total")) or claimed_delta_gbp

    checks["recomputed_delta_gbp"] = round(recomputed, 2)
    if abs(recomputed - claimed_delta_gbp) > tolerance_gbp:
        return finalize_adversary(
            AdversaryResult(
                approved=False,
                reasons=["delta_arithmetic_mismatch"],
                checks=checks,
            ),
            confidence=confidence,
        )
    if claimed_delta_gbp < 0:
        return finalize_adversary(
            AdversaryResult(approved=False, reasons=["negative_delta"], checks=checks),
            confidence=confidence,
        )
    return finalize_adversary(
        AdversaryResult(approved=True, checks=checks),
        confidence=confidence,
    )


def validate_vendor_email_draft(
    *,
    vendor_contact_email: str | None,
    accreditation_type: str | None,
    renewal_url: str | None,
    draft: dict[str, Any],
    confidence: float | None = None,
) -> AdversaryResult:
    """Confirm correct vendor contact, accreditation type, renewal URL."""
    reasons: list[str] = []
    to_addr = (draft.get("to") or "").strip()
    body = draft.get("body") or ""
    subject = draft.get("subject") or ""

    if not vendor_contact_email:
        reasons.append("missing_vendor_contact")
    elif to_addr.lower() != vendor_contact_email.lower():
        reasons.append("vendor_contact_mismatch")

    if not accreditation_type:
        reasons.append("missing_accreditation_type")
    elif accreditation_type not in subject and accreditation_type not in body:
        reasons.append("accreditation_type_not_in_email")

    if renewal_url and renewal_url not in body:
        reasons.append("renewal_url_missing_from_body")

    if reasons:
        return finalize_adversary(
            AdversaryResult(
                approved=False,
                reasons=reasons,
                checks={"to": to_addr, "accreditation_type": accreditation_type},
            ),
            confidence=confidence,
        )
    return finalize_adversary(
        AdversaryResult(approved=True, checks={"to": to_addr}),
        confidence=confidence,
    )


async def run_adversary(
    session: AsyncSession,
    *,
    check_type: str,
    payload: dict[str, Any],
    organization_id: UUID | None = None,
) -> dict[str, Any]:
    """HTTP-facing adversary entrypoint."""
    confidence = payload.get("confidence")
    if confidence is not None:
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            confidence = None

    result: AdversaryResult

    if check_type == "lapsed_flag":
        cert_id = payload.get("certificate_id")
        cert = await session.get(ComplianceCertificate, UUID(str(cert_id))) if cert_id else None
        if not cert:
            result = finalize_adversary(
                AdversaryResult(approved=False, reasons=["certificate_not_found"]),
                confidence=confidence,
            )
        else:
            result = await validate_lapsed_flag(
                session, certificate=cert, confidence=confidence
            )

    elif check_type == "block_state":
        result = validate_block_state_change(
            accreditation_type=payload.get("accreditation_type"),
            expiry_date=(
                date.fromisoformat(payload["expiry_date"])
                if payload.get("expiry_date")
                else None
            ),
            proposed_block_state=payload.get("proposed_block_state") or "Blocked",
            confidence=confidence,
        )

    elif check_type == "vendor_email":
        result = validate_vendor_email_draft(
            vendor_contact_email=payload.get("vendor_contact_email"),
            accreditation_type=payload.get("accreditation_type"),
            renewal_url=payload.get("renewal_url"),
            draft=payload.get("draft") or {},
            confidence=confidence,
        )

    elif check_type == "invoice_flag_delta":
        result = validate_invoice_flag_delta(
            claimed_delta_gbp=float(payload.get("claimed_delta_gbp") or 0),
            line=payload.get("line") or {},
            work_order=payload.get("work_order"),
            labour_day_rate=(
                float(payload["labour_day_rate"])
                if payload.get("labour_day_rate") is not None
                else None
            ),
            parts_framework=payload.get("parts_framework") or {},
            tolerance_gbp=float(payload.get("tolerance_gbp") or 1.0),
            confidence=confidence,
        )

    elif check_type == "confidence_gate":
        result = finalize_adversary(
            AdversaryResult(approved=True, checks={}),
            confidence=confidence if confidence is not None else 0.0,
        )

    else:
        result = finalize_adversary(
            AdversaryResult(approved=False, reasons=[f"unknown_check_type:{check_type}"]),
            confidence=confidence,
        )

    feature = "B" if check_type == "invoice_flag_delta" else "A"
    await write_audit(
        session,
        actor="adversary",
        action_type=f"adversary.{check_type}",
        source_feature=feature,
        organization_id=organization_id,
        input_payload=payload,
        output_payload=result.to_dict(),
        detail=result.checks,
    )
    await session.commit()

    return {
        **result.to_dict(),
        "check_type": check_type,
    }
