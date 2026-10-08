"""A vendor's month raises one cost-overrun alert, however many times it is scored.

28 Sep 2026: every scoring run called enqueue_approval for the month's overrun unconditionally,
so each Rebuild — here, and one on an Azure instance sharing hoistra_test — added another
"Systematic cost overrun" item beside the pending one. The Decision queue carried 30 of them
for 24 vendor-months. A re-score now refreshes the pending alert instead of adding one.

Review, same day: refreshing only the newest left the older duplicates pending; a month that
no longer crossed the threshold (its doubled rows de-duplicated) kept its alert pending with
the doubled count; and the refresh replaced the whole payload, erasing a PM's Edit and the
email_sent / pm_action_status flags the sibling sync writes. The duplicates and the stale
alert are now closed with a stated reason, and the refresh merges.
"""
from datetime import date, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy.dialects import postgresql

from src.engines.contract_performance import scoring as sc
from src.engines.contract_performance.scoring import weights_to_dict


class _Session:
    def __init__(self, rows):
        self.rows, self.statements = rows, []

    async def execute(self, stmt, *a, **k):
        self.statements.append(stmt)
        rows = self.rows

        class _R:
            def scalars(self):
                return self

            def all(self):
                return list(rows)

        return _R()


def _sql(stmt, literal=True):
    kw = {"compile_kwargs": {"literal_binds": True}} if literal else {}
    return " ".join(str(stmt.compile(dialect=postgresql.dialect(), **kw)).split())


async def test_the_lookup_is_the_same_vendor_month_and_still_pending():
    s = _Session([])
    vid, org = uuid4(), uuid4()
    await sc._pending_cost_alerts(s, organization_id=org, vendor_id=vid, score_month=date(2026, 8, 1))
    sql = _sql(s.statements[0])
    assert "item_type = 'cost_variance_alert'" in sql
    assert "status = 'pending'" in sql
    assert f"related_entity_id = '{vid}'" in sql
    assert "->> 'score_month') = '2026-08-01'" in sql
    assert f"organization_id = '{org}'" in sql
    assert "ORDER BY" in sql and "created_at DESC" in sql, "newest first: that is the one kept"
    assert "LIMIT" not in sql, "every pending copy is found, not only the newest"


async def test_no_company_matches_only_items_with_no_company():
    s = _Session([])
    await sc._pending_cost_alerts(s, organization_id=None, vendor_id=uuid4(), score_month=date(2026, 8, 1))
    assert "organization_id IS NULL" in _sql(s.statements[0], literal=False)


def _alert(**payload):
    return SimpleNamespace(id=uuid4(), summary="old", status="pending", severity="high",
                           pm_notes=None, decided_at=None, decided_by=None, updated_at=None,
                           payload={"vendor_id": "v", "overrun_count": 9, "score_month": "2026-08-01",
                                    "window": "month", **payload})


@pytest.fixture
def queue(monkeypatch):
    """_pending_cost_alerts answers with what the test puts in `pending`; enqueues and audit
    rows are recorded."""
    state = SimpleNamespace(pending=[], enqueued=[], audits=[])

    async def found(*a, **k):
        return list(state.pending)

    async def enqueue(*a, **k):
        state.enqueued.append(k)
        return SimpleNamespace(id=uuid4())

    async def audit(*a, **k):
        state.audits.append(k)

    monkeypatch.setattr(sc, "_pending_cost_alerts", found)
    monkeypatch.setattr(sc, "enqueue_approval", enqueue)
    monkeypatch.setattr(sc, "write_audit", audit)
    return state


async def _raise(month_overruns=11):
    return await sc._raise_cost_alert(None, organization_id=uuid4(), vendor_id=uuid4(),
                                      score_month=date(2026, 8, 1), month_overruns=month_overruns,
                                      alert_pct=15.0)


async def test_a_pending_alert_is_refreshed_not_duplicated(queue):
    existing = _alert()
    queue.pending = [existing]
    out = await _raise()
    assert queue.enqueued == [], "a second alert was queued beside the pending one"
    assert out == str(existing.id)
    assert existing.payload["overrun_count"] == 11
    assert "11 jobs" in existing.summary
    assert isinstance(existing.updated_at, datetime)


async def test_with_none_pending_one_is_queued(queue):
    await _raise()
    assert len(queue.enqueued) == 1 and queue.enqueued[0]["item_type"] == "cost_variance_alert"


async def test_a_refresh_keeps_what_a_pm_and_the_email_sync_wrote(queue):
    """decide_queue_item's Edit merges a PM's corrections into the payload and keeps the item
    pending; the compliance email stamps email_sent / pm_action_status onto every pending item
    about the same vendor. A refresh that replaced the payload threw both away."""
    existing = _alert(pm_correction="two of these were variations", email_sent=True,
                      pm_action_status="Email sent to PM for action")
    queue.pending = [existing]
    await _raise(month_overruns=4)
    assert existing.payload["overrun_count"] == 4
    assert existing.payload["pm_correction"] == "two of these were variations"
    assert existing.payload["email_sent"] is True
    assert existing.payload["pm_action_status"] == "Email sent to PM for action"


async def test_older_pending_copies_are_closed_when_the_newest_is_refreshed(queue):
    newest, older, oldest = _alert(), _alert(), _alert()
    queue.pending = [newest, older, oldest]
    out = await _raise()
    assert out == str(newest.id) and newest.status == "pending"
    for dup in (older, oldest):
        # 'closed' is the approvals vocabulary for an item the system settles, with the reason
        # in pm_notes — db/tools/close_orphaned_approvals.py closes orphans the same way.
        assert dup.status == "closed"
        assert isinstance(dup.decided_at, datetime) and dup.decided_by is None
        assert "duplicate" in dup.pm_notes and str(newest.id) in dup.pm_notes
    assert queue.enqueued == []
    (audit,) = queue.audits
    assert set(audit["output_payload"]["queue_item_ids"]) == {str(older.id), str(oldest.id)}


async def test_a_pm_note_already_on_a_duplicate_is_kept(queue):
    newest, older = _alert(), _alert()
    older.pm_notes = "Chased Apex on the 3rd."
    queue.pending = [newest, older]
    await _raise()
    assert older.pm_notes.startswith("Chased Apex on the 3rd. ")


# ── a month that no longer crosses the threshold ────────────────────────────────────

class _EmptyMonth:
    """No score rows on record for the month: its overrun count is 0."""

    def __init__(self):
        self.added = []

    async def execute(self, *a, **k):
        class _R:
            def scalars(self):
                return self

            def all(self):
                return []

            def mappings(self):
                return self

            def first(self):
                return None

        return _R()

    def add(self, obj):
        self.added.append(obj)

    async def flush(self):
        return None

    async def commit(self):
        return None


@pytest.fixture
def scoring_env(monkeypatch):
    async def _weights(*_a, **_kw):
        return weights_to_dict(None)

    async def _false(*_a, **_kw):
        return False

    async def _accred(*_a, **_kw):
        return True, None

    async def _crit(*_a, **_kw):
        return "L2"

    monkeypatch.setattr(sc, "get_or_create_weights", _weights)
    monkeypatch.setattr(sc, "_vendor_blocked", _false)
    monkeypatch.setattr(sc, "_accreditation_current", _accred)
    monkeypatch.setattr(sc, "effective_criticality", _crit)


async def _score_month(vendor_id):
    wo = {"wo_code": "WO-B-301-6183", "priority": "P2",
          "reported_at": datetime(2026, 8, 12, 9, 0), "attended_at": datetime(2026, 8, 12, 12, 0),
          "completed_at": datetime(2026, 8, 12, 20, 0)}
    return await sc.score_completed_work_orders(
        _EmptyMonth(), [wo], vendor_id=vendor_id, organization_id=uuid4(),
        score_month=date(2026, 8, 1), allow_default_parameters=True)


async def test_an_alert_the_month_no_longer_supports_is_closed_with_the_reason(queue, scoring_env):
    """The doubled B-301 rows counted 4 overruns against a threshold of 3 and raised '4 jobs';
    de-duplicated, the month has 2, and the '4 jobs' alert must not stay pending for good."""
    stale, dup = _alert(overrun_count=4), _alert(overrun_count=4)
    queue.pending = [stale, dup]
    out = await _score_month(uuid4())
    assert out["month_cost_overruns"] == 0 and out["cost_variance_alert_id"] is None
    for item in (stale, dup):
        assert item.status == "closed"
        assert isinstance(item.decided_at, datetime)
        assert "0 jobs" in item.pm_notes and "3" in item.pm_notes, item.pm_notes
    assert queue.enqueued == []


async def test_with_no_vendor_named_no_alert_is_closed(queue, scoring_env):
    """With no vendor the lookup would match every vendor-less alert for the month."""
    stale = _alert()
    queue.pending = [stale]
    await _score_month(None)
    assert stale.status == "pending"
