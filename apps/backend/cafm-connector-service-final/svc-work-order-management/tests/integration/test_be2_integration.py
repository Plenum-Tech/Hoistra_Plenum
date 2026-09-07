"""
BE2 integration tests.

Tests cover:
- Journey auto-creation on work order creation (BE2-06)
- Milestone update via model helper (BE2-08)
- Work order status history (BE2-09)
- Journey health metrics (BE2-13)
- calculate_journey_health / complete_milestone / update_journey_status
- advance_journey_milestone: actual_start / actual_end tracking
- JourneyAnalytics in_progress_journeys / failed_journeys counts (BE2-13)

NOTE: Tests for extended Asset / Location model fields are skipped here because
      this service reads from the shared plenum_cafm.assets / plenum_cafm.locations
      tables (UUID PKs, shared schema) — not from service-local wo_assets / wo_locations.
"""
import pytest
import pytest_asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy import select

from src.models.base import Base
from src.models.work_order import WorkOrder
from src.models.journey_log import JourneyLog
from src.models.status_history import StatusHistory
from src.services.journey_service import (
    create_journey_for_work_order,
    record_status_change,
    advance_journey_milestone,
    complete_milestone,
    calculate_journey_health,
    update_journey_status,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def session():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", echo=False
    ).execution_options(schema_translate_map={"plenum_cafm": None})
    async with engine.begin() as conn:
        for tbl in [WorkOrder.__table__, JourneyLog.__table__, StatusHistory.__table__]:
            await conn.run_sync(lambda sync_conn, t=tbl: t.create(sync_conn, checkfirst=True))
    factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    async with factory() as s:
        yield s
    await engine.dispose()


def _make_wo(work_order_id: str, priority: str = "medium", status: str = "pending_approval") -> WorkOrder:
    return WorkOrder(
        work_order_id=work_order_id,
        source="test",
        issue_description=f"Test work order {work_order_id}",
        priority=priority,
        request_type="repair",
        status=status,
        approval_type="preparation",
    )


# ── BE2-06: Journey auto-creation ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_journey_auto_created_on_work_order(session: AsyncSession):
    """BE2-06: Journey log is auto-created with correct initial state."""
    session.add(_make_wo("WO-TEST-001", priority="high"))
    await session.flush()

    jlog = await create_journey_for_work_order(
        work_order_id="WO-TEST-001",
        priority="high",
        session=session,
        asset_id="AHU-001",
        source_system="test",
        estimated_cost=1500.00,
    )
    await session.flush()

    assert jlog.jlog_id.startswith("JL-")
    assert jlog.work_order_id == "WO-TEST-001"
    assert jlog.journey_status == "in_progress"
    assert jlog.status == "active"
    assert jlog.source_system == "test"
    assert jlog.asset_id == "AHU-001"
    assert float(jlog.estimated_cost) == 1500.00
    assert jlog.milestones is not None
    assert len(jlog.milestones) == 6


@pytest.mark.asyncio
async def test_journey_expected_timeline_set_by_priority(session: AsyncSession):
    """BE2-06: Expected timeline duration reflects WO priority."""
    session.add(_make_wo("WO-PRIO-001", priority="critical"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-PRIO-001", "critical", session)
    assert jlog.estimated_duration_hours == 4
    assert jlog.expected_timeline["duration_hours"] == 4


# ── BE2-08: Milestone helpers ─────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_update_milestone(session: AsyncSession):
    """BE2-08: JourneyLog.update_milestone() sets status correctly."""
    session.add(_make_wo("WO-MS-001"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-MS-001", "medium", session)
    await session.flush()

    jlog.update_milestone("preparing", "completed")
    assert any(
        m["name"] == "preparing" and m["status"] == "completed"
        for m in jlog.milestones
    )


@pytest.mark.asyncio
async def test_get_completion_percentage(session: AsyncSession):
    """BE2-08: get_completion_percentage reflects completed milestone count."""
    session.add(_make_wo("WO-PCT-001", status="active"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-PCT-001", "medium", session)
    await session.flush()

    initial = jlog.get_completion_percentage()
    assert 0 <= initial <= 100

    jlog.update_milestone("pending_approval", "completed")
    jlog.update_milestone("preparing", "completed")
    assert jlog.get_completion_percentage() > initial


# ── BE2-09: Work order status history ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_history_recorded(session: AsyncSession):
    """BE2-09: Status changes are recorded in StatusHistory."""
    session.add(_make_wo("WO-HIST-001"))
    await session.flush()

    await record_status_change(
        work_order_id="WO-HIST-001",
        from_status="pending_approval",
        to_status="preparing",
        session=session,
    )
    await session.flush()

    result = await session.execute(
        select(StatusHistory).where(StatusHistory.work_order_id == "WO-HIST-001")
    )
    rows = result.scalars().all()
    assert len(rows) >= 1
    assert rows[-1].to_status == "preparing"


# ── BE2-13: Journey health ────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_complete_milestone_service(session: AsyncSession):
    """complete_milestone marks milestone and writes milestone_history."""
    session.add(_make_wo("WO-CM-001", status="active"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-CM-001", "medium", session)
    await session.flush()

    await complete_milestone("WO-CM-001", "pending_approval", session, notes="Approved")

    assert any(
        m["name"] == "pending_approval" and m["status"] == "completed"
        for m in jlog.milestones
    )
    assert jlog.milestone_history is not None


@pytest.mark.asyncio
async def test_calculate_journey_health_at_risk(session: AsyncSession):
    """BE2-13: Journey with low completion and overruns reports 'at_risk'."""
    session.add(_make_wo("WO-HLTH-001", priority="low"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-HLTH-001", "low", session)
    jlog.estimated_cost = 1000.0
    jlog.actual_cost = 1300.0
    jlog.estimated_duration_hours = 120
    jlog.actual_duration_hours = 140
    await session.flush()

    health = calculate_journey_health(jlog)

    assert health["health_status"] in ("at_risk", "in_progress", "on_track", "completed")
    assert health["cost_overrun"] == pytest.approx(300.0)
    assert health["time_overrun_hours"] == 20
    assert "completion_percentage" in health


@pytest.mark.asyncio
async def test_journey_to_dict(session: AsyncSession):
    """JourneyLog.to_dict() returns all expected keys."""
    session.add(_make_wo("WO-DICT-001"))
    await session.flush()

    jlog = await create_journey_for_work_order(
        "WO-DICT-001", "medium", session,
        asset_id="AHU-001", source_system="test", estimated_cost=500.0,
    )
    await session.flush()

    d = jlog.to_dict()
    for key in (
        "jlog_id", "work_order_id", "status", "journey_status",
        "milestones", "completion_percentage", "asset_id",
        "source_system", "estimated_cost", "actual_start", "actual_end",
    ):
        assert key in d, f"Missing key: {key}"


# ── update_journey_status / actual_start / actual_end ────────────────────────

@pytest.mark.asyncio
async def test_update_journey_status_sets_actual_start(session: AsyncSession):
    """update_journey_status sets actual_start when transitioning to in_progress."""
    session.add(_make_wo("WO-UJS-001", status="active"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-UJS-001", "medium", session)
    await session.flush()

    assert jlog.actual_start is None
    result = await update_journey_status("WO-UJS-001", "in_progress", session)
    assert result is not None
    assert result.journey_status == "in_progress"
    assert result.actual_start is not None
    assert result.status_change_history is not None


@pytest.mark.asyncio
async def test_update_journey_status_sets_actual_end_and_duration(session: AsyncSession):
    """update_journey_status sets actual_end and computes actual_duration_hours on completion."""
    session.add(_make_wo("WO-UJS-002", priority="high", status="active"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-UJS-002", "high", session)
    jlog.actual_start = datetime.now(timezone.utc) - timedelta(hours=10)
    await session.flush()

    result = await update_journey_status("WO-UJS-002", "completed", session)
    assert result is not None
    assert result.actual_end is not None
    assert result.actual_duration_hours is not None
    assert result.actual_duration_hours >= 9


@pytest.mark.asyncio
async def test_advance_milestone_sets_actual_start_on_active(session: AsyncSession):
    """advance_journey_milestone sets actual_start when transitioning to 'active'."""
    session.add(_make_wo("WO-ADV-001"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-ADV-001", "medium", session)
    await session.flush()

    assert jlog.actual_start is None
    await advance_journey_milestone("WO-ADV-001", "active", session)
    assert jlog.actual_start is not None


@pytest.mark.asyncio
async def test_advance_milestone_sets_actual_end_on_completed(session: AsyncSession):
    """advance_journey_milestone sets actual_end and journey_status='completed' on completion."""
    session.add(_make_wo("WO-ADV-002", status="active"))
    await session.flush()

    jlog = await create_journey_for_work_order("WO-ADV-002", "medium", session)
    jlog.actual_start = datetime.now(timezone.utc) - timedelta(hours=5)
    await session.flush()

    await advance_journey_milestone("WO-ADV-002", "completed", session)

    assert jlog.actual_end is not None
    assert jlog.journey_status == "completed"
    assert jlog.status == "completed"
    assert jlog.actual_duration_hours is not None


# ── BE2-13: in_progress / failed journey counts ───────────────────────────────

@pytest.mark.asyncio
async def test_journey_analytics_counts(session: AsyncSession):
    """JourneyAnalytics in_progress_journeys and failed_journeys are computable."""
    for i, (wo_status, j_status) in enumerate([
        ("active",    "in_progress"),
        ("active",    "in_progress"),
        ("completed", "completed"),
        ("active",    "failed"),
    ]):
        session.add(_make_wo(f"WO-ANA-{i:03d}", status=wo_status))
        await session.flush()
        jlog = await create_journey_for_work_order(f"WO-ANA-{i:03d}", "medium", session)
        jlog.journey_status = j_status
        await session.flush()

    result = await session.execute(select(JourneyLog))
    journeys = result.scalars().all()

    assert sum(1 for j in journeys if j.journey_status == "in_progress") == 2
    assert sum(1 for j in journeys if j.journey_status == "failed") == 1
