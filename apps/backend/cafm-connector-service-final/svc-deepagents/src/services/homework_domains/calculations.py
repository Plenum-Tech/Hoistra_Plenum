"""Pure domain arithmetic shared by guarded Homework adapters.

Keeping these functions free of database/model calls makes expected values
independently checkable before any caller persists or presents a result.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping


DEFAULT_WEIGHTS = {
    "sla_response_pct": 25,
    "sla_completion_pct": 25,
    "first_fix_pct": 20,
    "recall_pct": 15,
    "accreditation_pct": 15,
}


@dataclass(frozen=True)
class ScoringConfig:
    weights: Mapping[str, int] = None
    recall_window_days: int = 30
    score_cap: int = 60
    ppm_tolerance_days: int = 7
    cost_variance_alert_pct: Decimal = Decimal("15")
    cost_variance_alert_jobs: int = 3
    invoice_flag_adversary_gbp: Decimal = Decimal("500")

    def validated(self) -> "ScoringConfig":
        weights = dict(self.weights or DEFAULT_WEIGHTS)
        if set(weights) != set(DEFAULT_WEIGHTS):
            raise ValueError("Exactly the five approved score components are configurable")
        if any(type(v) is not int or v < 0 or v > 100 for v in weights.values()):
            raise ValueError("Component weights must be whole percentages from 0 to 100")
        if sum(weights.values()) != 100:
            raise ValueError("Component weights must total 100")
        if not 0 <= self.recall_window_days <= 365 or not 0 <= self.score_cap <= 100:
            raise ValueError("Recall window or score cap is outside the supported range")
        if not 0 <= self.ppm_tolerance_days <= 365:
            raise ValueError("PPM tolerance is outside the supported range")
        if (self.cost_variance_alert_pct < 0 or not 1 <= self.cost_variance_alert_jobs <= 100
                or self.invoice_flag_adversary_gbp < 0):
            raise ValueError("Cost alert settings are invalid")
        return ScoringConfig(weights, self.recall_window_days, self.score_cap,
                             self.ppm_tolerance_days, self.cost_variance_alert_pct,
                             self.cost_variance_alert_jobs, self.invoice_flag_adversary_gbp)


def _decimal(value, field: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{field} must be numeric") from None
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def energy_eui(*, consumption_kwh, area_m2, period_start: date,
               period_end: date, annualised: bool = False) -> dict:
    """Calculate period EUI; unavailable inputs are errors, never zero-filled."""
    kwh = _decimal(consumption_kwh, "consumption_kwh")
    area = _decimal(area_m2, "area_m2")
    days = (period_end - period_start).days + 1
    if area <= 0 or days <= 0 or kwh < 0:
        raise ValueError("Area and inclusive period must be positive; consumption cannot be negative")
    period_eui = kwh / area
    result = {
        "consumption_kwh": str(kwh), "area_m2": str(area),
        "period_start": period_start.isoformat(), "period_end": period_end.isoformat(),
        "inclusive_days": days, "period_eui_kwh_m2": str(period_eui),
        "annualised_eui_kwh_m2_year": str(period_eui * Decimal(365) / days)
            if annualised else None,
    }
    return result


def vendor_score(components: Mapping[str, Decimal | int | float], *,
                 config: ScoringConfig, raised_date: date,
                 lapse_start: date | None = None,
                 lapse_evidence_ref: str | None = None) -> dict:
    """Score one eligible work-order contribution; apply only evidenced date cap."""
    settings = config.validated()
    expected = set(settings.weights)
    if set(components) != expected:
        raise ValueError("All five approved component values are required")
    values = {key: _decimal(value, key) for key, value in components.items()}
    if any(value < 0 or value > 100 for value in values.values()):
        raise ValueError("Component values must be between 0 and 100")
    raw = sum(values[key] * Decimal(settings.weights[key]) / 100 for key in expected)
    capped = bool(lapse_start and raised_date >= lapse_start)
    if capped and not lapse_evidence_ref:
        raise ValueError("A lapse cap requires a source evidence reference")
    score = min(raw, Decimal(settings.score_cap)) if capped else raw
    return {
        "score": int(score.quantize(Decimal("1"), rounding=ROUND_HALF_UP)),
        "raw_score": str(raw), "components": {key: str(values[key]) for key in sorted(values)},
        "configuration": {"weights": dict(settings.weights), "score_cap": settings.score_cap,
                          "recall_window_days": settings.recall_window_days,
                          "ppm_tolerance_days": settings.ppm_tolerance_days},
        "accreditation_cap_applied": capped,
        "lapse_evidence_ref": lapse_evidence_ref if capped else None,
    }


def invoice_diagnostic(*, matched_count: int, cleared_count: int,
                       flagged_count: int, period: str, source_refs: list[str]) -> dict:
    """Keep match and verification outcomes distinct; never feed this into score."""
    if min(matched_count, cleared_count, flagged_count) < 0:
        raise ValueError("Invoice counts cannot be negative")
    if cleared_count + flagged_count > matched_count:
        raise ValueError("Cleared and flagged lines cannot exceed matched lines")
    return {"period": period, "matched_count": matched_count,
            "cleared_count": cleared_count, "flagged_count": flagged_count,
            "unmatched_count": None, "source_refs": list(source_refs),
            "score_component": False}


def elapsed_sla_hours(*, raised_at, responded_at=None, completed_at=None,
                      response_target_hours=None, completion_target_hours=None) -> dict:
    """Assess only supplied timestamps and elapsed-hour targets; no calendar guessing."""
    if raised_at is None:
        return {"assessable": False, "reason": "missing_raise_timestamp"}
    result = {"assessable": True, "basis": "elapsed_hours"}
    for label, stamp, target in (
        ("response", responded_at, response_target_hours),
        ("completion", completed_at, completion_target_hours),
    ):
        if stamp is None or target is None:
            result[label] = {"assessable": False, "reason": "missing_source_value"}
            continue
        hours = Decimal(str((stamp - raised_at).total_seconds())) / Decimal(3600)
        target_hours = _decimal(target, f"{label}_target_hours")
        if hours < 0 or target_hours < 0:
            result[label] = {"assessable": False, "reason": "contradictory_timestamp_or_target"}
        else:
            result[label] = {"assessable": True, "elapsed_hours": str(hours),
                             "target_hours": str(target_hours), "met": hours <= target_hours}
    return result
