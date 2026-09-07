"""Unit tests — Feature B3 invoice verification + adversary £500 gate."""
from src.engines.contract_performance.invoice import build_insights, match_invoice_line
from src.swarm.adversary import validate_invoice_flag_delta


class TestB3Match:
    def test_matched_line_auto_cleared(self):
        wos = {
            "WO-100": {
                "wo_code": "WO-100",
                "status": "Completed",
                "attendance_hours": 8.0,
                "parts_cost": 100.0,
            }
        }
        result = match_invoice_line(
            {
                "line_id": "1",
                "wo_code": "WO-100",
                "labour_hours": 8.2,
                "parts_cost": 102.0,
                "labour_rate": 40.0,
                "amount": 500,
            },
            work_orders=wos,
            labour_day_rate=400.0,  # £50/h
        )
        assert result["status"] == "matched"
        assert result["decision"] == "auto_cleared"

    def test_missing_wo_flagged(self):
        result = match_invoice_line(
            {"line_id": "2", "wo_code": "WO-MISSING", "amount": 900},
            work_orders={},
            labour_day_rate=350,
        )
        assert result["status"] == "flagged"
        assert result["delta_gbp"] == 900.0
        assert "No ingested" in result["discrepancy"]

    def test_labour_hours_out_of_tolerance(self):
        wos = {
            "WO-1": {
                "status": "Closed",
                "attendance_hours": 8.0,
                "completed_at": "2026-05-01",
            }
        }
        result = match_invoice_line(
            {"line_id": "3", "wo_code": "WO-1", "labour_hours": 12.0, "amount": 200},
            work_orders=wos,
            labour_day_rate=400.0,
        )
        assert result["status"] == "flagged"
        assert "Labour hours" in result["discrepancy"]

    def test_rate_exceeds_contract(self):
        wos = {
            "WO-1": {
                "status": "Completed",
                "attendance_hours": 8.0,
            }
        }
        result = match_invoice_line(
            {
                "line_id": "4",
                "wo_code": "WO-1",
                "labour_hours": 8.0,
                "labour_rate": 80.0,  # contract hourly = 400/8 = 50
                "amount": 640,
            },
            work_orders=wos,
            labour_day_rate=400.0,
        )
        assert result["status"] == "flagged"
        assert "Labour rate" in result["discrepancy"]


class TestB3AdversaryAndInsights:
    def test_adversary_approves_matching_delta(self):
        line = {"wo_code": "WO-1", "labour_hours": 12, "amount": 200}
        wo = {"attendance_hours": 8.0, "status": "Completed"}
        # delta hours = 4 * (400/8) = 200
        result = validate_invoice_flag_delta(
            claimed_delta_gbp=200.0,
            line=line,
            work_order=wo,
            labour_day_rate=400.0,
        )
        assert result.approved is True

    def test_adversary_rejects_mismatched_delta(self):
        line = {"wo_code": "WO-1", "labour_hours": 12, "amount": 999}
        wo = {"attendance_hours": 8.0}
        result = validate_invoice_flag_delta(
            claimed_delta_gbp=999.0,
            line=line,
            work_order=wo,
            labour_day_rate=400.0,
        )
        assert result.approved is False
        assert "delta_arithmetic_mismatch" in result.reasons

    def test_insights_ratio(self):
        lines = [
            {"status": "matched", "delta_gbp": 0},
            {"status": "matched", "delta_gbp": 0},
            {"status": "flagged", "delta_gbp": 50, "discrepancy": "Parts cost £10"},
        ]
        insights = build_insights(lines, work_orders=[{}], labour_day_rate=350)
        assert insights["matched_count"] == 2
        assert insights["flagged_count"] == 1
        assert insights["matched_flagged_ratio"] == round(2 / 3, 4)


# ---------------------------------------------------------------------------
# T020 (FR-034): adversary gate and PM decision audit trail
# ---------------------------------------------------------------------------


class TestFR034AuditTrail:
    """FR-034: PM decisions must be traceable; flagged lines >£500 pass adversary gate."""

    def _run_verify(self, lines: list[dict], work_orders: list[dict]) -> dict:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch

        async def _inner():
            with (
                patch(
                    "src.engines.contract_performance.invoice.get_or_create_weights",
                    return_value={
                        "invoice_flag_adversary_gbp": 500.0,
                        "cost_variance_alert_pct": 15.0,
                        "cost_variance_job_count": 3,
                    },
                ),
                patch(
                    "src.engines.contract_performance.invoice.enqueue_approval",
                    new_callable=AsyncMock,
                    return_value=MagicMock(id="00000000-0000-0000-0000-000000000099"),
                ),
                patch(
                    "src.engines.contract_performance.invoice.write_audit",
                    new_callable=AsyncMock,
                ),
                patch(
                    "src.engines.contract_performance.invoice._resolve_wo_ids",
                    return_value={},
                ),
                patch(
                    "src.engines.contract_performance.invoice._persist_invoice_lines",
                    new_callable=AsyncMock,
                ),
            ):
                session = AsyncMock()
                session.add = lambda x: None
                session.flush = AsyncMock()
                session.commit = AsyncMock()

                from src.engines.contract_performance.invoice import verify_invoice

                return await verify_invoice(
                    session,
                    invoice_ref="INV-TEST-001",
                    lines=lines,
                    work_orders=work_orders,
                    vendor_id=None,
                    organization_id=None,
                    labour_day_rate=400.0,
                )

        return asyncio.run(_inner())

    def test_flagged_line_above_500_has_adversary_reviewed(self):
        """FR-034 / T020(a): flagged line with delta > £500 triggers adversary gate."""
        # A line with a missing WO will be flagged; force a high amount to exceed £500
        line = {
            "line_id": "L1",
            "wo_code": "WO-NOTFOUND",
            "amount": 600.0,  # > adversary_threshold (500)
        }
        result = self._run_verify([line], work_orders=[])
        assert result["ok"] is True
        line_result = result["lines"][0]
        assert line_result["status"] == "flagged"
        # adversary key is present because delta (600) >= threshold (500)
        assert "adversary" in line_result

    def _run_decide(
        self,
        decision: str,
        *,
        capture_audit: list,
    ) -> dict:
        import asyncio
        from unittest.mock import AsyncMock, MagicMock, patch
        from uuid import uuid4

        iv_id = uuid4()
        line_id = "L1"

        async def _inner():
            mock_row = MagicMock()
            mock_row.id = iv_id
            mock_row.organization_id = None
            mock_row.lines_json = [{"line_id": line_id, "status": "flagged"}]

            session = AsyncMock()
            session.get = AsyncMock(return_value=mock_row)
            session.commit = AsyncMock()

            captured = []

            async def fake_write_audit(sess, *, actor, action_type, **kwargs):
                captured.append(
                    {"actor": actor, "action_type": action_type, **kwargs}
                )

            with patch(
                "src.engines.contract_performance.invoice.write_audit",
                side_effect=fake_write_audit,
            ):
                from src.engines.contract_performance.invoice import decide_invoice_line

                result = await decide_invoice_line(
                    session,
                    iv_id,
                    line_id=line_id,
                    decision=decision,
                    pm_notes="disputing labour hours",
                )
            capture_audit.extend(captured)
            return result

        return asyncio.run(_inner())

    def test_pm_rejection_records_audit_with_correct_action_type(self):
        """FR-034 / T020(b): PM rejection generates audit entry with action_type='invoice_line.pm_decision'."""
        captured: list[dict] = []
        result = self._run_decide("reject", capture_audit=captured)
        assert result["ok"] is True
        assert result["decision"] == "reject"
        assert len(captured) == 1
        audit = captured[0]
        assert audit["action_type"] == "invoice_line.pm_decision"
        assert audit["input_payload"]["decision"] == "reject"
        assert audit["input_payload"]["note"] == "disputing labour hours"
        assert "invoice_verification_id" in audit["output_payload"]

    def test_pm_decision_payload_includes_line_id_and_iv_id(self):
        """FR-034: output_payload must reference both line_id and invoice_verification_id."""
        captured: list[dict] = []
        self._run_decide("approve", capture_audit=captured)
        payload = captured[0]["output_payload"]
        assert "line_id" in payload
        assert "invoice_verification_id" in payload
