"""A work order has one score per month, however many times it is scored.

28 Sep 2026: one Rebuild scorecards run on Bishopsgate Tower wrote two vendor_wo_scores rows
for every Apex Lifts work order in the month. The scorer inserted a fresh row for each work
order it was handed and never replaced what an earlier run had written, so a batch that
carried a job twice — or a second Rebuild — doubled it. The monthly card, the month's
cost-overrun alert (which counts rows) and the Evidence tab all read every row in the month,
so each of them counted the job twice.
"""

import re
from datetime import date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.sql.dml import Delete

from src.engines.contract_performance import scoring as sc
from src.engines.contract_performance.scoring import weights_to_dict
from src.models.contract_performance import VendorWoScore


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return self

    def all(self):
        return self._rows

    def scalar_one_or_none(self):
        return self._rows[0] if self._rows else None

    def mappings(self):
        return self

    def first(self):
        return None


class _RecordingSession:
    """Answers every read with nothing, and records what was executed and added, in order."""

    def __init__(self):
        self.events = []
        self.params = []

    async def execute(self, stmt, *a, **_kw):
        self.events.append(("execute", stmt))
        self.params.append((stmt, a[0] if a else None))
        return _FakeResult([])

    def add(self, obj):
        self.events.append(("add", obj))

    async def flush(self):
        return None

    async def commit(self):
        return None

    def scores_added(self):
        return [o for kind, o in self.events if kind == "add" and isinstance(o, VendorWoScore)]

    def score_deletes(self):
        return [
            (i, s) for i, (kind, s) in enumerate(self.events)
            if kind == "execute" and isinstance(s, Delete) and s.table.name == "vendor_wo_scores"
        ]


def _wo(code="WO-B-301-6183", **over):
    wo = {
        "wo_code": code,
        "priority": "P2",
        "status": "Completed",
        "reported_at": datetime(2026, 9, 12, 9, 0),
        "attended_at": datetime(2026, 9, 12, 12, 0),
        "completed_at": datetime(2026, 9, 12, 20, 0),
        "first_fix": True,
        "recall": False,
    }
    wo.update(over)
    return wo


@pytest.fixture
def scoring_env(monkeypatch):
    async def _weights(*_a, **_kw):
        return weights_to_dict(None)

    async def _not_blocked(*_a, **_kw):
        return False

    async def _accred(*_a, **_kw):
        return True, None

    async def _crit(*_a, **_kw):
        return "L2"

    monkeypatch.setattr(sc, "get_or_create_weights", _weights)
    monkeypatch.setattr(sc, "_vendor_blocked", _not_blocked)
    monkeypatch.setattr(sc, "_accreditation_current", _accred)
    monkeypatch.setattr(sc, "effective_criticality", _crit)


async def _score(session, work_orders, vendor_id=None):
    return await sc.score_completed_work_orders(
        session, work_orders, vendor_id=vendor_id or uuid4(), score_month=date(2026, 9, 1),
        allow_default_parameters=True,
    )


@pytest.mark.asyncio
async def test_a_job_the_batch_carries_twice_is_scored_once(scoring_env):
    session = _RecordingSession()
    out = await _score(session, [_wo(), _wo(), _wo("WO-B-301-6185")])

    assert [s.wo_code for s in session.scores_added()] == ["WO-B-301-6183", "WO-B-301-6185"]
    assert out["count"] == 2
    dup = [e for e in out["exclusions"] if e["reason"] == "duplicate_work_order"]
    assert [e["wo_ref"] for e in dup] == ["WO-B-301-6183"], "the dropped copy must be named, not silently lost"


@pytest.mark.asyncio
async def test_rescoring_a_month_replaces_the_earlier_scores_before_writing(scoring_env):
    session = _RecordingSession()
    vid = uuid4()
    await _score(session, [_wo(), _wo("WO-B-301-6185")], vendor_id=vid)

    deletes = session.score_deletes()
    assert len(deletes) == 1, "an earlier run's rows for these work orders must be cleared"
    at, stmt = deletes[0]
    first_add = next(i for i, (kind, o) in enumerate(session.events) if kind == "add" and isinstance(o, VendorWoScore))
    assert at < first_add, "clear first, then write — otherwise the new rows are deleted too"

    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert str(vid).replace("-", "") in sql.replace("-", ""), "only this vendor's rows"
    assert "2026-09-01" in sql, "only this month's rows"
    assert "WO-B-301-6183" in sql and "WO-B-301-6185" in sql, "only the work orders being scored now"


@pytest.mark.asyncio
async def test_nothing_is_cleared_when_nothing_is_eligible(scoring_env):
    session = _RecordingSession()
    # completed before it was reported: excluded, so there is nothing to replace
    await _score(session, [_wo(completed_at=datetime(2026, 9, 12, 8, 0))])
    assert session.score_deletes() == []
    assert session.scores_added() == []


@pytest.mark.asyncio
async def test_a_copy_with_contradictory_times_does_not_stop_the_good_one(scoring_env):
    session = _RecordingSession()
    bad = _wo(completed_at=datetime(2026, 9, 12, 8, 0))
    out = await _score(session, [bad, _wo()])
    assert [s.wo_code for s in session.scores_added()] == ["WO-B-301-6183"]
    assert [e["reason"] for e in out["exclusions"]] == ["contradictory_timestamps"]


# ── review, 28 Sep: the replacement is a delete, so it must only ever reach the caller's own rows ──

async def _score_as(session, work_orders, organization_id, vendor_id=None):
    return await sc.score_completed_work_orders(
        session, work_orders, vendor_id=vendor_id or uuid4(), organization_id=organization_id,
        score_month=date(2026, 9, 1), allow_default_parameters=True,
    )


@pytest.mark.asyncio
async def test_the_replacement_only_reaches_the_callers_own_company(scoring_env):
    """POST /score/work-orders does not check that the vendor is the caller's. Before the
    replacement that could only ADD rows tagged with the caller's company; a delete keyed on the
    vendor alone would have erased another company's scores for the same codes."""
    session = _RecordingSession()
    org = uuid4()
    await _score_as(session, [_wo()], organization_id=org)
    (_, stmt), = session.score_deletes()
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True})).replace("-", "")
    assert "organization_id" in sql and str(org).replace("-", "") in sql


@pytest.mark.asyncio
async def test_with_no_company_named_only_company_less_rows_are_replaced(scoring_env):
    session = _RecordingSession()
    await _score_as(session, [_wo()], organization_id=None)
    (_, stmt), = session.score_deletes()
    sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
    assert "organization_id IS NULL" in sql


@pytest.mark.asyncio
async def test_two_different_jobs_that_share_a_code_are_both_scored(scoring_env):
    """A copy is the same job twice — same code, same completion, same asset. Two jobs that only
    share a code (legacy numbering across buildings) are two jobs, and both are scored."""
    session = _RecordingSession()
    a = _wo(asset_id="11111111-1111-4111-8111-111111111111")
    b = _wo(asset_id="22222222-2222-4222-8222-222222222222", completed_at=datetime(2026, 9, 14, 20, 0))
    out = await _score(session, [a, b, dict(a)])
    assert len(session.scores_added()) == 2
    assert [e["reason"] for e in out["exclusions"]] == ["duplicate_work_order"], "only the true copy is dropped"


# ── second review, 28 Sep: what the replacement is scoped to, the readers that count must be too ──

def _selects_on_scores(session):
    from sqlalchemy.sql.selectable import Select
    out = []
    for kind, stmt in session.events:
        if kind == "execute" and isinstance(stmt, Select):
            sql = str(stmt.compile(compile_kwargs={"literal_binds": True}))
            parts = re.split(r"\bWHERE\b", sql, maxsplit=1)
            if "vendor_wo_scores" in sql and len(parts) == 2:
                out.append(parts[1])   # the filter, not the column list
    return out


@pytest.mark.asyncio
async def test_the_months_overrun_count_reads_only_the_callers_company(scoring_env):
    session = _RecordingSession()
    org = uuid4()
    await _score_as(session, [_wo()], organization_id=org)
    reads = _selects_on_scores(session)
    assert reads, "the month's rows are read for the overrun count"
    assert all("organization_id" in q and str(org).replace("-", "") in q.replace("-", "") for q in reads)


@pytest.mark.asyncio
async def test_the_monthly_card_reads_only_the_callers_company():
    session = _RecordingSession()
    org = uuid4()
    out = await sc.generate_monthly_scorecard(session, vendor_id=uuid4(), score_month=date(2026, 9, 1), organization_id=org)
    assert out["ok"] is False  # nothing on record in the fake
    (q,) = _selects_on_scores(session)
    assert "organization_id" in q and str(org).replace("-", "") in q.replace("-", "")


# ── third review, 28 Sep: with no company named, the reads must match the delete ──
# The replace-DELETE with no company removed only company-less rows, while the overrun count
# and the card read every company's. An org-less Rebuild wrote company-less copies beside a
# company's rows and then counted both — every job twice, the very doubling this replaces.

@pytest.mark.asyncio
async def test_with_no_company_named_the_months_overrun_count_reads_only_company_less_rows(scoring_env):
    session = _RecordingSession()
    await _score_as(session, [_wo()], organization_id=None)
    reads = _selects_on_scores(session)
    assert reads, "the month's rows are read for the overrun count"
    assert all("organization_id IS NULL" in q for q in reads), reads


@pytest.mark.asyncio
async def test_a_card_cut_for_no_company_reads_only_company_less_rows():
    """The single-company deployment, whose rows all carry no company, still reads all of
    them; a deployment with companies no longer has a company's rows counted in a card cut
    for none."""
    session = _RecordingSession()
    await sc.generate_monthly_scorecard(session, vendor_id=uuid4(), score_month=date(2026, 9, 1))
    (q,) = _selects_on_scores(session)
    assert "organization_id IS NULL" in q


# ── review, 28 Sep: dropping a later copy must not hide a conflict between the copies ──
# The in-batch dedup ran before the FR-039 conflict check, so a second copy of a job whose
# cost or attendance differed was dropped as a duplicate. The check compares an incoming copy
# with ONE stored row (LIMIT 1); when that row was the first copy, nothing ever compared the
# second, and £1,200 was scored where the corrected copy said £12,000.

def _queued(session, item_type):
    from src.models import ApprovalsQueueItem
    return [o for kind, o in session.events
            if kind == "add" and isinstance(o, ApprovalsQueueItem) and o.item_type == item_type]


def _conflict_flag_writes(session):
    return [(str(s), p) for s, p in session.params if "conflict_flag = TRUE" in str(s)]


@pytest.mark.asyncio
async def test_two_copies_that_disagree_raise_the_conflict_instead_of_one_being_dropped(scoring_env):
    session = _RecordingSession()
    a = _wo(actual_cost=1200.0, estimated_cost=1000.0)
    b = _wo(actual_cost=12000.0, estimated_cost=1000.0)
    out = await _score(session, [a, b])

    assert session.scores_added() == [], "neither copy is scored until a PM says which is true"
    assert [e["reason"] for e in out["exclusions"]] == ["conflict_flagged", "conflict_flagged"]

    (item,) = _queued(session, "work_order_conflict")
    assert item.payload["wo_code"] == "WO-B-301-6183"
    assert item.payload["conflict_details"] == {"actual_cost": {"stored": "1200.0", "incoming": "12000.0"}}

    (flag,) = _conflict_flag_writes(session)
    sql, params = flag
    assert "UPDATE plenum_cafm.work_orders" in sql
    assert params["wc"] == "WO-B-301-6183", "flagged the way detect_and_flag_wo_conflict flags"
    assert '"actual_cost": "12000.0"' in params["payload"], "the later copy is what 'Take incoming' applies"


@pytest.mark.asyncio
async def test_copies_that_agree_are_still_deduplicated_without_a_conflict(scoring_env):
    session = _RecordingSession()
    out = await _score(session, [_wo(actual_cost=1200.0), _wo(actual_cost=1200.0)])
    assert len(session.scores_added()) == 1
    assert [e["reason"] for e in out["exclusions"]] == ["duplicate_work_order"]
    assert _queued(session, "work_order_conflict") == []
    assert _conflict_flag_writes(session) == []


@pytest.mark.asyncio
async def test_a_third_copy_does_not_raise_the_conflict_twice(scoring_env):
    session = _RecordingSession()
    out = await _score(session, [_wo(actual_cost=1200.0), _wo(actual_cost=12000.0), _wo(actual_cost=1200.0)])
    assert session.scores_added() == []
    assert [e["reason"] for e in out["exclusions"]] == ["conflict_flagged"] * 3
    assert len(_queued(session, "work_order_conflict")) == 1
    assert len(_conflict_flag_writes(session)) == 1


@pytest.mark.asyncio
async def test_a_copy_of_a_job_the_stored_row_already_flagged_is_not_scored_either(scoring_env, monkeypatch):
    calls = []

    async def _stored_row_disagrees(*_a, **kw):
        calls.append(kw["wo_code"])
        return {"conflict": True, "wo_code": kw["wo_code"]}

    monkeypatch.setattr(sc, "detect_and_flag_wo_conflict", _stored_row_disagrees)
    session = _RecordingSession()
    out = await _score(session, [_wo(), _wo()])
    assert calls == ["WO-B-301-6183"], "the copy is compared with the first, not flagged again"
    assert session.scores_added() == []
    assert [e["reason"] for e in out["exclusions"]] == ["conflict_flagged", "conflict_flagged"]
    assert _conflict_flag_writes(session) == []
