"""Unit tests — FR-039 WO re-ingestion conflict detection (T032)."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_session_for_conflict(existing_row=None):
    """Return an AsyncSession mock whose execute returns the given row mapping."""
    mapping = MagicMock()
    mapping.first.return_value = existing_row  # None = no existing WO

    result = MagicMock()
    result.mappings.return_value = mapping

    session = AsyncMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


def _run(coro):
    return asyncio.run(coro)


class TestFR039ConflictDetection:
    """FR-039: re-ingestion conflict detection on plenum_cafm.work_orders."""

    def test_first_ingest_no_conflict(self):
        """T032(a): no existing row → no conflict, enqueue_approval not called."""
        session = _mock_session_for_conflict(existing_row=None)

        with patch(
            "src.engines.contract_performance.conflicts.enqueue_approval",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            from src.engines.contract_performance.conflicts import detect_and_flag_wo_conflict

            result = _run(
                detect_and_flag_wo_conflict(
                    session,
                    wo_code="WO-001",
                    incoming_fields={"actual_cost": 100.0},
                )
            )

        assert result["conflict"] is False
        mock_enqueue.assert_not_awaited()

    def test_reingestion_with_changed_actual_cost_flags_conflict(self):
        """T032(b): re-ingestion with different actual_cost → conflict_flag=True, enqueue_approval called."""
        existing = {
            "actual_cost": 100.0,
            "estimated_cost": 90.0,
            "attended_at": None,
            "completed_at": "2026-05-01",
        }
        # Make the mapping dict-like
        existing_row = MagicMock()
        existing_row.get = lambda k, default=None: existing.get(k, default)

        session = _mock_session_for_conflict(existing_row=existing_row)

        with patch(
            "src.engines.contract_performance.conflicts.enqueue_approval",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            from src.engines.contract_performance.conflicts import detect_and_flag_wo_conflict

            result = _run(
                detect_and_flag_wo_conflict(
                    session,
                    wo_code="WO-002",
                    incoming_fields={
                        "actual_cost": 200.0,  # changed by >1%
                        "estimated_cost": 90.0,
                        "attended_at": None,
                        "completed_at": "2026-05-01",
                    },
                )
            )

        assert result["conflict"] is True
        assert result["wo_code"] == "WO-002"
        mock_enqueue.assert_awaited_once()
        call_kwargs = mock_enqueue.call_args.kwargs
        assert call_kwargs["item_type"] == "work_order_conflict"

    def test_reingestion_with_same_values_no_conflict(self):
        """No conflict when re-ingested values match stored values within tolerance."""
        existing = {
            "actual_cost": 100.0,
            "estimated_cost": 90.0,
            "attended_at": None,
            "completed_at": "2026-05-01",
        }
        existing_row = MagicMock()
        existing_row.get = lambda k, default=None: existing.get(k, default)

        session = _mock_session_for_conflict(existing_row=existing_row)

        with patch(
            "src.engines.contract_performance.conflicts.enqueue_approval",
            new_callable=AsyncMock,
        ) as mock_enqueue:
            from src.engines.contract_performance.conflicts import detect_and_flag_wo_conflict

            result = _run(
                detect_and_flag_wo_conflict(
                    session,
                    wo_code="WO-003",
                    incoming_fields={
                        "actual_cost": 100.5,  # within 1% tolerance
                        "estimated_cost": 90.0,
                        "attended_at": None,
                        "completed_at": "2026-05-01",
                    },
                )
            )

        assert result["conflict"] is False
        mock_enqueue.assert_not_awaited()

    def test_resolve_conflict_accept_stored_clears_flag(self):
        """T032(d): resolving with accept='stored' sets conflict_flag=False and calls write_audit."""
        existing = {"conflict_payload": {}}
        existing_row = MagicMock()
        existing_row.__getitem__ = lambda self, k: existing[k]

        # Need two execute calls: first gets the row, subsequent clear the flag
        call_count = 0

        async def fake_execute(stmt, params=None, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            res = MagicMock()
            if call_count == 1:
                mapping = MagicMock()
                mapping.first.return_value = existing_row
                res.mappings.return_value = mapping
            return res

        session = AsyncMock()
        session.execute = fake_execute
        session.commit = AsyncMock()

        with patch(
            "src.engines.contract_performance.conflicts.write_audit",
            new_callable=AsyncMock,
        ) as mock_audit:
            from src.engines.contract_performance.conflicts import resolve_wo_conflict

            result = _run(
                resolve_wo_conflict(
                    session,
                    wo_code="WO-004",
                    accept="stored",
                    resolved_by=None,
                    note="keeping stored values",
                )
            )

        assert result["ok"] is True
        assert result["conflict_flag"] is False
        assert result["wo_code"] == "WO-004"
        mock_audit.assert_awaited_once()
        call_kwargs = mock_audit.call_args.kwargs
        assert call_kwargs["action_type"] == "work_order_conflict.resolve"
        assert call_kwargs["input_payload"]["accept"] == "stored"
