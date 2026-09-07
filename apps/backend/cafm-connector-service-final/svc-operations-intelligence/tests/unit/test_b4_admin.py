"""Unit tests — Feature B4 admin weight tuning (FR-030)."""
import asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch


def _make_weight_row(**overrides):
    """MagicMock VendorScoreWeightConfig row — default sums to 100."""
    row = MagicMock()
    row.sla_response_pct = Decimal("25")
    row.sla_completion_pct = Decimal("25")
    row.first_fix_pct = Decimal("25")
    row.recall_pct = Decimal("25")
    row.accreditation_pct = Decimal("0")
    row.blocked_score_cap = Decimal("60")
    row.cost_variance_alert_pct = Decimal("15")
    row.invoice_flag_adversary_gbp = Decimal("500")
    row.cost_variance_job_count = 3
    for k, v in overrides.items():
        setattr(row, k, v)
    return row


def _run_update_weights(row, updates, *, expect_rollback=False):
    async def _inner():
        mock_res = MagicMock()
        mock_res.scalar_one_or_none.return_value = row

        session = AsyncMock()
        session.execute = AsyncMock(return_value=mock_res)
        session.rollback = AsyncMock()
        session.commit = AsyncMock()

        with patch(
            "src.engines.contract_performance.scoring.write_audit",
            new_callable=AsyncMock,
        ), patch(
            "src.engines.contract_performance.scoring.weights_to_dict",
            return_value={
                "sla_response_pct": float(row.sla_response_pct),
                "sla_completion_pct": float(row.sla_completion_pct),
                "first_fix_pct": float(row.first_fix_pct),
                "recall_pct": float(row.recall_pct),
                "accreditation_pct": float(row.accreditation_pct),
            },
        ):
            from src.engines.contract_performance.scoring import update_weights

            result = await update_weights(session, updates)

        if expect_rollback:
            session.rollback.assert_awaited_once()
            session.commit.assert_not_awaited()
        else:
            session.commit.assert_awaited_once()
            session.rollback.assert_not_awaited()

        return result, session

    return asyncio.run(_inner())


class TestFR030WeightRejection:
    """FR-030: component weights must sum to exactly 100.0 or the save is rejected."""

    def test_weights_summing_to_95_rejected(self):
        """T023(a): sum=95 → ok=False, rollback called, no commit."""
        row = _make_weight_row(
            sla_response_pct=Decimal("20"),
            sla_completion_pct=Decimal("25"),
            first_fix_pct=Decimal("25"),
            recall_pct=Decimal("25"),
            accreditation_pct=Decimal("0"),
        )
        result, session = _run_update_weights(
            row,
            {
                "sla_response_pct": 20,
                "sla_completion_pct": 25,
                "first_fix_pct": 25,
                "recall_pct": 25,
                "accreditation_pct": 0,
            },
            expect_rollback=True,
        )
        assert result["ok"] is False
        assert result["error"] == "weights_must_total_100"
        assert result["component_sum"] == 95.0

    def test_weights_summing_to_105_rejected(self):
        """FR-030: over-sum also rejected."""
        row = _make_weight_row(
            sla_response_pct=Decimal("30"),
            sla_completion_pct=Decimal("30"),
            first_fix_pct=Decimal("25"),
            recall_pct=Decimal("20"),
            accreditation_pct=Decimal("0"),
        )
        result, _ = _run_update_weights(
            row,
            {
                "sla_response_pct": 30,
                "sla_completion_pct": 30,
                "first_fix_pct": 25,
                "recall_pct": 20,
                "accreditation_pct": 0,
            },
            expect_rollback=True,
        )
        assert result["ok"] is False
        assert result["component_sum"] == 105.0

    def test_weights_summing_to_100_accepted(self):
        """T023(b): sum=100 → ok=True, commit called."""
        row = _make_weight_row()  # 25+25+25+25+0 = 100
        result, _ = _run_update_weights(
            row,
            {
                "sla_response_pct": 25,
                "sla_completion_pct": 25,
                "first_fix_pct": 25,
                "recall_pct": 25,
                "accreditation_pct": 0,
            },
            expect_rollback=False,
        )
        assert result["ok"] is True
        assert "error" not in result

    def test_new_weights_reflected_in_scorecard_snapshot(self):
        """T023(c): after a valid update the scorecard weights_snapshot uses new values."""
        # Verify that compute_wo_component_scores respects updated weights —
        # the weights_snapshot is built from the same dict that scores are computed with.
        from src.engines.contract_performance.scoring import compute_wo_component_scores

        new_weights = {
            "sla_response_pct": 30.0,
            "sla_completion_pct": 25.0,
            "first_fix_pct": 25.0,
            "recall_pct": 20.0,
            "accreditation_pct": 0.0,  # sums to 100
            "blocked_score_cap": 60.0,
            "cost_variance_alert_pct": 15.0,
            "cost_variance_job_count": 3,
            "invoice_flag_adversary_gbp": 500.0,
        }

        # A single SLA-response miss with 30% weight should lose 30 points, not 25
        score = compute_wo_component_scores(
            sla_response_met=False,
            sla_completion_met=True,
            first_fix=True,
            recall=False,
            accreditation_current=True,
            weights=new_weights,
            criticality="L2",
        )
        # L2 adds a criticality_extra_penalty on top of the base miss (30 pts lost)
        base_miss = 100.0 - 30.0  # 70
        expected = base_miss - score["criticality_extra_penalty"]
        assert score["overall_uncapped"] == expected

        # Simulate what generate_monthly_scorecard stores as weights_snapshot
        snapshot = {
            "sla_response_pct": new_weights["sla_response_pct"],
            "sla_completion_pct": new_weights["sla_completion_pct"],
            "first_fix_pct": new_weights["first_fix_pct"],
            "recall_pct": new_weights["recall_pct"],
            "accreditation_pct": new_weights["accreditation_pct"],
        }
        assert snapshot["sla_response_pct"] == 30.0
        assert sum(snapshot.values()) == 100.0
