"""Unit tests — Feature B5 insights layer (FR-028)."""
import asyncio
from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock


def _make_wo_score(wo_code: str, cost_actual: float | None, cost_estimated: float | None):
    """Minimal VendorWoScore mock."""
    row = MagicMock()
    row.wo_code = wo_code
    row.cost_actual = Decimal(str(cost_actual)) if cost_actual is not None else None
    row.cost_estimated = Decimal(str(cost_estimated)) if cost_estimated is not None else None
    row.component_scores = {}
    return row


def _run_compute_insights(wo_rows, iv_ratios=None):
    """Run compute_insights with mocked session."""

    async def _inner():
        iv_ratios_list = iv_ratios or []

        # First execute call → VendorWoScore rows
        wo_res = MagicMock()
        wo_res.scalars.return_value.all.return_value = wo_rows

        # Second execute call → InvoiceVerification ratios
        iv_res = MagicMock()
        iv_res.scalars.return_value.all.return_value = iv_ratios_list

        call_count = 0

        async def fake_execute(q, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            return wo_res if call_count == 1 else iv_res

        session = AsyncMock()
        session.execute = fake_execute

        from src.engines.contract_performance.insights import compute_insights

        return await compute_insights(
            session,
            vendor_id=None,
            organization_id=None,
            from_date=date(2026, 2, 1),
            to_date=date(2026, 7, 31),
        )

    return asyncio.run(_inner())


class TestFR028Insights:
    """FR-028: insights endpoint returns traced variance figures."""

    def test_cost_variance_pct_correct(self):
        """T027(a): cost_variance_pct = (actual - estimated) / estimated × 100."""
        rows = [
            _make_wo_score("WO-001", cost_actual=110.0, cost_estimated=100.0),
            _make_wo_score("WO-002", cost_actual=220.0, cost_estimated=200.0),
        ]
        result = _run_compute_insights(rows)
        assert result["ok"] is True
        assert result["message"] is None
        # (330 - 300) / 300 × 100 = 10.0
        assert result["cost_variance_pct"] == 10.0

    def test_cost_variance_negative_when_under_budget(self):
        """cost_variance_pct is negative when actual < estimated."""
        rows = [
            _make_wo_score("WO-001", cost_actual=90.0, cost_estimated=100.0),
        ]
        result = _run_compute_insights(rows)
        assert result["cost_variance_pct"] == -10.0

    def test_no_data_returns_message(self):
        """T027(b): empty WO scores → no-data message, empty contributing_wo_ids."""
        result = _run_compute_insights([])
        assert result["ok"] is True
        assert result["message"] == "No data for the requested period"
        assert result["cost_variance_pct"] is None
        assert result["labour_variance_pct"] is None
        assert result["contributing_wo_ids"] == []
        assert result["matched_flagged_trend"] == []

    def test_contributing_wo_ids_traced(self):
        """T027(c): constitution Principle II — contributing_wo_ids lists WOs with cost data."""
        rows = [
            _make_wo_score("WO-A", cost_actual=150.0, cost_estimated=120.0),
            _make_wo_score("WO-B", cost_actual=None, cost_estimated=None),  # no cost → excluded
            _make_wo_score("WO-C", cost_actual=80.0, cost_estimated=100.0),
        ]
        result = _run_compute_insights(rows)
        assert set(result["contributing_wo_ids"]) == {"WO-A", "WO-C"}
        # WO-B has no cost data, must not appear
        assert "WO-B" not in result["contributing_wo_ids"]

    def test_matched_flagged_trend_returned(self):
        """Trend list contains matched_flagged_ratio values from InvoiceVerification."""
        rows = [_make_wo_score("WO-1", cost_actual=100.0, cost_estimated=100.0)]
        iv_ratios = [Decimal("0.80"), Decimal("0.85"), Decimal("0.90")]
        result = _run_compute_insights(rows, iv_ratios=iv_ratios)
        assert result["matched_flagged_trend"] == [0.80, 0.85, 0.90]

    def test_period_echoed_in_response(self):
        """Period from_date / to_date are reflected in the response."""
        result = _run_compute_insights([])
        assert result["period"]["from_date"] == "2026-02-01"
        assert result["period"]["to_date"] == "2026-07-31"

    def test_rows_without_cost_data_excluded_from_variance(self):
        """Rows with None cost fields don't affect cost_variance_pct but still in rows."""
        rows = [
            _make_wo_score("WO-1", cost_actual=200.0, cost_estimated=200.0),
            _make_wo_score("WO-2", cost_actual=None, cost_estimated=None),
        ]
        result = _run_compute_insights(rows)
        # Only WO-1 contributes; 200/200 variance = 0%
        assert result["cost_variance_pct"] == 0.0
        assert result["contributing_wo_ids"] == ["WO-1"]
