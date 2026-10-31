from datetime import date, datetime, timezone
from decimal import Decimal

import pytest

from src.services.homework_domains.calculations import (
    ScoringConfig, elapsed_sla_hours, energy_eui, invoice_diagnostic, vendor_score,
)


def test_energy_uses_inclusive_period_and_zero_is_valid():
    result = energy_eui(consumption_kwh=0, area_m2=100,
                       period_start=date(2026, 1, 1), period_end=date(2026, 1, 10),
                       annualised=True)
    assert result["inclusive_days"] == 10
    assert result["period_eui_kwh_m2"] == "0"
    assert result["annualised_eui_kwh_m2_year"] == "0"


@pytest.mark.parametrize("area,start,end", [
    (0, date(2026, 1, 1), date(2026, 1, 1)),
    (100, date(2026, 1, 2), date(2026, 1, 1)),
])
def test_energy_rejects_invalid_required_inputs(area, start, end):
    with pytest.raises(ValueError):
        energy_eui(consumption_kwh=50, area_m2=area, period_start=start, period_end=end)


def test_vendor_score_uses_five_components_and_only_dates_after_evidenced_lapse_are_capped():
    components = {key: 100 for key in ScoringConfig().validated().weights}
    config = ScoringConfig()
    before = vendor_score(components, config=config, raised_date=date(2026, 4, 30),
                          lapse_start=date(2026, 5, 1))
    after = vendor_score(components, config=config, raised_date=date(2026, 5, 1),
                         lapse_start=date(2026, 5, 1), lapse_evidence_ref="cert-v3")
    assert before["score"] == 100 and not before["accreditation_cap_applied"]
    assert after["score"] == 60 and after["accreditation_cap_applied"]
    assert after["lapse_evidence_ref"] == "cert-v3"


def test_scoring_configuration_rejects_bad_weights():
    with pytest.raises(ValueError, match="total 100"):
        ScoringConfig(weights={"sla_response_pct": 10, "sla_completion_pct": 10,
                              "first_fix_pct": 10, "recall_pct": 10,
                              "accreditation_pct": 10}).validated()


def test_invoice_result_is_separate_and_does_not_claim_exclusive_match_partition():
    result = invoice_diagnostic(matched_count=20, cleared_count=18, flagged_count=2,
                                period="2026-04", source_refs=["invoice-source-1"])
    assert result["score_component"] is False
    assert result["matched_count"] == 20 and result["cleared_count"] == 18
    assert result["unmatched_count"] is None


def test_sla_uses_elapsed_hours_and_does_not_invent_missing_timestamps():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = elapsed_sla_hours(raised_at=start,
                               responded_at=datetime(2026, 1, 1, 2, tzinfo=timezone.utc),
                               response_target_hours=2, completed_at=None,
                               completion_target_hours=4)
    assert result["response"]["met"] is True
    assert result["completion"] == {"assessable": False, "reason": "missing_source_value"}
