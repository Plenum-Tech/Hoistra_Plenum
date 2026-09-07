"""Unit tests — Feature B2 vendor scoring."""
from datetime import date, datetime, timedelta, timezone

from src.engines.contract_performance.parameters import merge_extraction_with_defaults
from src.engines.contract_performance.scoring import (
    DEFAULT_WEIGHTS,
    apply_block_cap,
    compute_ppm_compliance,
    compute_wo_component_scores,
    detect_recall,
    score_work_order_pure,
)


class TestB2Components:
    def test_perfect_score_is_100(self):
        c = compute_wo_component_scores(
            sla_response_met=True,
            sla_completion_met=True,
            first_fix=True,
            recall=False,
            accreditation_current=True,
            weights=DEFAULT_WEIGHTS,
            criticality="L2",
        )
        assert c["overall_uncapped"] == 100.0

    def test_l1_sla_miss_hurts_3x_vs_l3(self):
        kwargs = dict(
            sla_response_met=False,
            sla_completion_met=True,
            first_fix=True,
            recall=False,
            accreditation_current=True,
            weights=DEFAULT_WEIGHTS,
        )
        l1 = compute_wo_component_scores(**kwargs, criticality="L1")
        l3 = compute_wo_component_scores(**kwargs, criticality="L3")
        # Perfect = 100; L3 miss response loses 25 → 75; L1 loses 75 → 25
        assert l3["overall_uncapped"] == 75.0
        assert l1["overall_uncapped"] == 25.0
        assert l1["criticality_extra_penalty"] == 50.0
        assert (100 - l1["overall_uncapped"]) == 3 * (100 - l3["overall_uncapped"])

    def test_blocked_vendor_capped_at_60(self):
        capped, was = apply_block_cap(95.0, vendor_blocked=True, cap=60.0)
        assert capped == 60.0
        assert was is True

    def test_invoice_ratio_blends_into_score(self):
        from src.engines.contract_performance.scoring import blend_invoice_ratio_into_score

        # 0.85*80 + 0.15*100 = 68 + 15 = 83
        assert blend_invoice_ratio_into_score(80.0, 1.0) == 83.0
        assert blend_invoice_ratio_into_score(80.0, None) == 80.0

    def test_clear_vendor_not_capped(self):
        capped, was = apply_block_cap(95.0, vendor_blocked=False, cap=60.0)
        assert capped == 95.0
        assert was is False

    def test_recall_within_30_days(self):
        done = datetime(2026, 6, 15, tzinfo=timezone.utc)
        prior = [
            {
                "asset_id": "a1",
                "completed_at": (done - timedelta(days=10)).isoformat(),
            }
        ]
        assert detect_recall(asset_id="a1", completed_at=done, prior_completions=prior) is True

    def test_recall_outside_window(self):
        done = datetime(2026, 6, 15, tzinfo=timezone.utc)
        prior = [
            {
                "asset_id": "a1",
                "completed_at": (done - timedelta(days=45)).isoformat(),
            }
        ]
        assert detect_recall(asset_id="a1", completed_at=done, prior_completions=prior) is False


class TestB2PpmAndScore:
    def test_ppm_within_tolerance(self):
        visits = [
            {"scheduled_date": "2026-05-01", "completed_date": "2026-05-05"},
            {"scheduled_date": "2026-05-10", "completed_date": "2026-05-20"},  # +10 days fail
        ]
        assert compute_ppm_compliance(visits) == 50.0

    def test_score_wo_met_sla(self):
        params, _, _ = merge_extraction_with_defaults(
            {"sla_response_p1_hours": 4, "sla_completion_p1_hours": 24}
        )
        start = datetime(2026, 5, 1, 8, 0, tzinfo=timezone.utc)
        wo = {
            "wo_code": "WO-1",
            "priority": "P1",
            "reported_at": start.isoformat(),
            "attended_at": (start + timedelta(hours=2)).isoformat(),
            "completed_at": (start + timedelta(hours=10)).isoformat(),
            "first_fix": True,
            "cost_actual": 1100,
            "cost_estimated": 1000,
        }
        # FR-038: cap requires lapse_date on/before reported_at
        scored = score_work_order_pure(
            wo,
            params=params,
            weights=DEFAULT_WEIGHTS,
            accreditation_current=True,
            vendor_blocked=True,
            criticality="L2",
            lapse_date=date(2026, 4, 30),  # before the WO's reported_at 2026-05-01
        )
        assert scored["sla_response_met"] is True
        assert scored["sla_completion_met"] is True
        assert scored["capped_by_block"] is True
        assert scored["overall_score"] == 60.0
        assert scored["cost_variance_pct"] == 10.0


# ---------------------------------------------------------------------------
# FR-036: recall -> first_fix = False
# ---------------------------------------------------------------------------

_PARAMS_SIMPLE = {
    "sla_response_p1_hours": 1.0,
    "sla_response_p2_hours": 4.0,
    "sla_response_p3_hours": 8.0,
    "sla_response_p4_hours": 24.0,
    "sla_completion_p1_hours": 4.0,
    "sla_completion_p2_hours": 24.0,
    "sla_completion_p3_hours": 72.0,
    "sla_completion_p4_hours": 168.0,
}


class TestFR036Recall:
    def _wo(self, **overrides) -> dict:
        base = {
            "wo_code": "WO-001",
            "reported_at": "2026-02-10T09:00:00",
            "attended_at": "2026-02-10T10:00:00",
            "completed_at": "2026-02-10T17:00:00",
            "priority": "P3",
            "asset_id": "asset-aaa",
        }
        base.update(overrides)
        return base

    def test_recall_detected_sets_first_fix_false(self):
        """FR-036: when detect_recall fires the scored WO has first_fix=False."""
        prior = [{"asset_id": "asset-aaa", "completed_at": "2026-02-01T12:00:00"}]
        wo = self._wo()
        result = score_work_order_pure(
            wo,
            params=_PARAMS_SIMPLE,
            weights=DEFAULT_WEIGHTS,
            prior_completions=prior,
            accreditation_current=True,
            vendor_blocked=False,
        )
        assert result["recall"] is True
        assert result["first_fix"] is False

    def test_no_recall_outside_window(self):
        """No recall when prior WO is more than 30 days before."""
        prior = [{"asset_id": "asset-aaa", "completed_at": "2026-01-01T12:00:00"}]
        wo = self._wo()
        result = score_work_order_pure(
            wo,
            params=_PARAMS_SIMPLE,
            weights=DEFAULT_WEIGHTS,
            prior_completions=prior,
            accreditation_current=True,
            vendor_blocked=False,
        )
        assert result["recall"] is False


# ---------------------------------------------------------------------------
# FR-038: per-WO accreditation lapse cap
# ---------------------------------------------------------------------------


class TestFR038LapseCap:
    def _wo(self, reported_at: str = "2026-02-10T09:00:00") -> dict:
        return {
            "wo_code": "WO-038",
            "reported_at": reported_at,
            "attended_at": reported_at,
            "completed_at": "2026-02-10T17:00:00",
            "priority": "P3",
            "asset_id": "asset-bbb",
        }

    def test_wo_after_lapse_date_is_capped(self):
        """FR-038: WO raised on/after lapse_date must have capped_by_block=True."""
        result = score_work_order_pure(
            self._wo(reported_at="2026-02-10T09:00:00"),
            params=_PARAMS_SIMPLE,
            weights=DEFAULT_WEIGHTS,
            accreditation_current=False,
            vendor_blocked=True,
            lapse_date=date(2026, 2, 5),  # lapse before WO
        )
        assert result["capped_by_block"] is True
        assert result["overall_score"] <= float(DEFAULT_WEIGHTS["blocked_score_cap"])
        assert result["component_scores"]["lapse_cap_applied"] is True
        assert result["component_scores"]["lapse_date"] == "2026-02-05"

    def test_wo_before_lapse_date_not_capped(self):
        """FR-038: WO raised before lapse_date must NOT be capped."""
        result = score_work_order_pure(
            self._wo(reported_at="2026-02-10T09:00:00"),
            params=_PARAMS_SIMPLE,
            weights=DEFAULT_WEIGHTS,
            accreditation_current=False,
            vendor_blocked=True,
            lapse_date=date(2026, 2, 15),  # lapse after WO
        )
        assert result["capped_by_block"] is False
        assert result["component_scores"]["lapse_cap_applied"] is False

    def test_lapse_date_none_suppresses_cap(self):
        """When lapse_date is None the block cap is not applied regardless of vendor_blocked."""
        result = score_work_order_pure(
            self._wo(),
            params=_PARAMS_SIMPLE,
            weights=DEFAULT_WEIGHTS,
            accreditation_current=False,
            vendor_blocked=True,
            lapse_date=None,
        )
        assert result["capped_by_block"] is False
        assert result["component_scores"]["lapse_cap_applied"] is False


# ---------------------------------------------------------------------------
# FR-033: exclusion reporting in score_completed_work_orders
# ---------------------------------------------------------------------------


class TestFR033Exclusions:
    """Tests that require async score_completed_work_orders with all DB calls stubbed."""

    def _run(self, work_orders: list[dict]) -> dict:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _inner():
            with (
                patch(
                    "src.engines.contract_performance.scoring.get_or_create_weights",
                    return_value=dict(DEFAULT_WEIGHTS),
                ),
                patch(
                    "src.engines.contract_performance.scoring._load_confirmed_params",
                    return_value=_PARAMS_SIMPLE,
                ),
                patch(
                    "src.engines.contract_performance.scoring._vendor_blocked",
                    return_value=False,
                ),
                patch(
                    "src.engines.contract_performance.scoring._accreditation_current",
                    return_value=(True, None),
                ),
                patch(
                    "src.engines.contract_performance.scoring.effective_criticality",
                    return_value="L2",
                ),
                patch(
                    "src.engines.contract_performance.scoring._resolve_asset_id",
                    return_value=None,
                ),
                patch(
                    "src.engines.contract_performance.scoring.enqueue_approval",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.engines.contract_performance.scoring.write_audit",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.engines.contract_performance.scoring.detect_and_flag_wo_conflict",
                    new_callable=AsyncMock,
                    return_value={"conflict": False, "wo_code": ""},
                ),
            ):
                session = AsyncMock()
                session.add = lambda x: None
                session.flush = AsyncMock()
                session.commit = AsyncMock()
                # MagicMock so .scalars().all() is synchronous (SQLAlchemy async pattern)
                mock_res = MagicMock()
                mock_res.scalars.return_value.all.return_value = []
                session.execute = AsyncMock(return_value=mock_res)

                from src.engines.contract_performance.scoring import (
                    score_completed_work_orders,
                )

                # These cases exercise FR-033 exclusion mechanics, not the contract gate.
                # With the session stubbed empty there is no confirmed contract, so the
                # opt-out is declared explicitly rather than implied.
                return await score_completed_work_orders(
                    session,
                    work_orders,
                    vendor_id=None,
                    organization_id=None,
                    allow_default_parameters=True,
                )

        return asyncio.run(_inner())

    def test_contradictory_timestamps_excluded(self):
        """FR-033: completed_at < reported_at lands in exclusions, not scores."""
        bad = {
            "wo_code": "WO-BAD",
            "reported_at": "2026-02-10T17:00:00",
            "attended_at": "2026-02-10T18:00:00",
            "completed_at": "2026-02-10T09:00:00",  # before reported
            "priority": "P3",
            "asset_id": "00000000-0000-0000-0000-000000000001",
        }
        result = self._run([bad])
        assert result["excluded_count"] == 1
        assert result["count"] == 0
        assert any(e["reason"] == "contradictory_timestamps" for e in result["exclusions"])
        assert "WO-BAD" not in [s.get("wo_code") for s in result["scores"]]

    def test_valid_wo_not_excluded(self):
        """A well-formed WO must appear in scores, not exclusions."""
        good = {
            "wo_code": "WO-OK",
            "reported_at": "2026-02-10T09:00:00",
            "attended_at": "2026-02-10T10:00:00",
            "completed_at": "2026-02-10T17:00:00",
            "priority": "P3",
            "asset_id": "00000000-0000-0000-0000-000000000002",
        }
        result = self._run([good])
        assert result["excluded_count"] == 0
        assert result["count"] == 1

    def test_mixed_batch_separated_correctly(self):
        """Mix of valid and invalid WOs: exclusions and scores are disjoint."""
        bad = {
            "wo_code": "WO-X",
            "reported_at": "2026-02-10T17:00:00",
            "completed_at": "2026-02-10T09:00:00",
            "priority": "P3",
            "asset_id": "00000000-0000-0000-0000-000000000010",
        }
        good = {
            "wo_code": "WO-Y",
            "reported_at": "2026-02-10T09:00:00",
            "attended_at": "2026-02-10T10:00:00",
            "completed_at": "2026-02-10T17:00:00",
            "priority": "P3",
            "asset_id": "00000000-0000-0000-0000-000000000011",
        }
        result = self._run([bad, good])
        assert result["excluded_count"] == 1
        assert result["count"] == 1
        assert "WO-Y" in [s.get("wo_code") for s in result["scores"]]
        assert "WO-X" not in [s.get("wo_code") for s in result["scores"]]


# ---------------------------------------------------------------------------
# FR-031: weights_snapshot in generate_monthly_scorecard
# ---------------------------------------------------------------------------


class TestFR031WeightsSnapshot:
    def _run_scorecard(self, weights_override: dict | None = None) -> dict:
        import asyncio
        from decimal import Decimal
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        w = dict(DEFAULT_WEIGHTS)
        if weights_override:
            w.update(weights_override)

        async def _inner():
            mock_score = MagicMock()
            mock_score.id = uuid4()
            mock_score.overall_score = Decimal("75.0")
            mock_score.capped_by_block = False
            mock_score.component_scores = {
                "sla_response": 20.0,
                "sla_completion": 20.0,
                "first_fix": 20.0,
                "recall": 15.0,
                "accreditation": 15.0,
            }

            call_count = 0

            async def fake_execute(q, *args, **kwargs):
                nonlocal call_count
                call_count += 1
                # MagicMock so .scalars().all() / .scalar_one_or_none() are synchronous
                mock_result = MagicMock()
                if call_count == 1:
                    mock_result.scalars.return_value.all.return_value = [mock_score]
                else:
                    mock_result.scalars.return_value.all.return_value = []
                    mock_result.scalar_one_or_none.return_value = None
                return mock_result

            session = AsyncMock()
            session.execute = fake_execute
            session.add = lambda x: None
            session.flush = AsyncMock()
            session.commit = AsyncMock()

            with (
                patch(
                    "src.engines.contract_performance.scoring.get_or_create_weights",
                    return_value=w,
                ),
                patch(
                    "src.engines.contract_performance.scoring.load_ppm_visits_for_month",
                    return_value=[],
                ),
                patch(
                    "src.engines.contract_performance.scoring.write_audit",
                    new_callable=AsyncMock,
                ),
            ):
                from src.engines.contract_performance.scoring import (
                    generate_monthly_scorecard,
                )

                return await generate_monthly_scorecard(
                    session,
                    vendor_id=uuid4(),
                    score_month=date(2026, 2, 1),
                    organization_id=None,
                )

        return asyncio.run(_inner())

    def test_weights_snapshot_present_in_breakdown(self):
        """FR-031: generate_monthly_scorecard stores weights_snapshot in component_breakdown."""
        result = self._run_scorecard()
        assert result["ok"] is True
        snap = result["scorecard"]["component_breakdown"]["weights_snapshot"]
        for key in (
            "sla_response_pct",
            "sla_completion_pct",
            "first_fix_pct",
            "recall_pct",
            "accreditation_pct",
        ):
            assert key in snap

    def test_weights_snapshot_captures_values_at_scoring_time(self):
        """FR-031: snapshot reflects weights used, not any later change."""
        result = self._run_scorecard(
            weights_override={"sla_response_pct": 30.0, "recall_pct": 10.0}
        )
        snap = result["scorecard"]["component_breakdown"]["weights_snapshot"]
        assert snap["sla_response_pct"] == 30.0
        assert snap["recall_pct"] == 10.0
