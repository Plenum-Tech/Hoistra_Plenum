"""The rate an invoice line is checked against, when the contract prices labour by the hour.

The check derived its threshold as ``labour_day_rate / 8``. A contract priced per hour has
no day rate to divide, so it fell to the £350 system default — £43.75/h — and every line of
a £62/h invoice breached it. The three lines that genuinely overcharged at £78.50 were
indistinguishable from the twenty that were correct.

Figures below are from the Gough and Kelly September 2023 invoice (UKRI-2938).
"""
from __future__ import annotations

from src.engines.contract_performance.invoice import match_invoice_line
from src.engines.contract_performance.parameters import (
    SYSTEM_DEFAULTS,
    contracted_hourly_rate,
)
from src.swarm.adversary import validate_invoice_flag_delta

WO = {"WO-1": {"wo_code": "WO-1", "status": "Completed", "labour_hours": 2.0, "parts_cost": 46.0}}


def _line(rate: float, hours: float = 2.0, **kw):
    line = {
        "line_id": 1,
        "wo_code": "WO-1",
        "labour_hours": hours,
        "labour_rate": rate,
        "parts_cost": 46.0,
        "part_code": "ACS-SPARE-GEN",
        "amount": round(rate * hours + 46.0, 2),
    }
    line.update(kw)
    return line


class TestResolver:
    def test_hourly_rate_is_used_as_given(self):
        assert contracted_hourly_rate(None, 62.0) == 62.0

    def test_hourly_rate_wins_over_a_day_rate(self):
        # Both present: the contract said £62/h, so £62/h is the threshold.
        assert contracted_hourly_rate(350.0, 62.0) == 62.0

    def test_day_rate_still_divides_by_eight(self):
        assert contracted_hourly_rate(496.0) == 62.0

    def test_neither_known_means_no_threshold(self):
        # None, not 0.0 — a zero threshold would flag every line as an overcharge.
        assert contracted_hourly_rate(None, None) is None
        assert contracted_hourly_rate(0, 0) is None

    def test_unparseable_values_do_not_raise(self):
        assert contracted_hourly_rate("nonsense") is None
        assert contracted_hourly_rate(496.0, "nonsense") == 62.0


class TestMatcherUsesTheContractRate:
    def test_correct_line_clears_against_its_hourly_rate(self):
        out = match_invoice_line(
            _line(62.0), work_orders=WO, labour_day_rate=None, labour_hour_rate=62.0
        )
        assert out["status"] == "matched"
        assert out["checks"]["contracted_hourly"] == 62.0

    def test_overcharged_line_is_flagged_with_the_right_delta(self):
        # Line 15 of the real invoice: £78.50/h against a £62/h contract.
        out = match_invoice_line(
            _line(78.5, hours=2.0),
            work_orders=WO,
            labour_day_rate=None,
            labour_hour_rate=62.0,
        )
        assert out["status"] == "flagged"
        assert out["delta_gbp"] == 33.0  # (78.50 - 62.00) x 2h
        assert "exceeds contracted £62.00/h" in out["discrepancy"]

    def test_the_default_day_rate_is_what_flagged_everything(self):
        # The behaviour this change exists to remove: a correct £62/h line, checked against
        # the £350 default day rate, reads as an overcharge.
        out = match_invoice_line(
            _line(62.0),
            work_orders=WO,
            labour_day_rate=float(SYSTEM_DEFAULTS["labour_day_rate"]),
        )
        assert out["status"] == "flagged"
        assert out["checks"]["contracted_hourly"] == 43.75

    def test_no_rate_at_all_skips_the_rate_check(self):
        out = match_invoice_line(
            _line(78.5), work_orders=WO, labour_day_rate=None, labour_hour_rate=None
        )
        assert out["status"] == "matched"
        assert "contracted_hourly" not in out["checks"]


class TestAdversaryAgrees:
    """The Adversary re-computes independently, but must land on the same rate.

    A different hourly figure here returns delta_arithmetic_mismatch, which blocks the flag
    from the PM queue — a genuine overcharge would disappear silently.
    """

    def test_adversary_confirms_the_matcher_delta(self):
        line = _line(78.5, hours=2.0)
        matched = match_invoice_line(
            line, work_orders=WO, labour_day_rate=None, labour_hour_rate=62.0
        )
        adv = validate_invoice_flag_delta(
            claimed_delta_gbp=float(matched["delta_gbp"]),
            line=line,
            work_order=WO["WO-1"],
            labour_day_rate=None,
            labour_hour_rate=62.0,
        )
        assert adv.approved, adv.reasons
        assert adv.checks["recomputed_delta_gbp"] == matched["delta_gbp"]

    def test_hours_overbilling_is_valued_at_the_contract_rate(self):
        # 12.71h billed against a 0.71h record, at £62/h — the largest line on the invoice.
        wo = {"WO-1": {"wo_code": "WO-1", "status": "Completed", "labour_hours": 0.71}}
        line = _line(62.0, hours=12.71)
        line["parts_cost"] = 0.0
        out = match_invoice_line(
            line, work_orders=wo, labour_day_rate=None, labour_hour_rate=62.0
        )
        assert out["status"] == "flagged"
        assert out["delta_gbp"] == 744.0  # (12.71 - 0.71) x 62
        adv = validate_invoice_flag_delta(
            claimed_delta_gbp=float(out["delta_gbp"]),
            line=line,
            work_order=wo["WO-1"],
            labour_day_rate=None,
            labour_hour_rate=62.0,
        )
        assert adv.approved, adv.reasons
