"""The Maintenance module's three reads, and the building boundary on each.

The shell's Maintenance screen wants decisions owed, inspection reports and PPM health. None
of the three is a table: they are derived from work orders, the approvals queue and the
inspections register, which is why one module answers all three.

These pin the boundary and the two bugs that cost the most to find: the state must be decided
in SQL, because deciding it after the LIMIT cut every decision out of a table whose newest
rows are all completed; and a caller allocated to nothing must get a predicate matching no
row rather than no predicate at all.
"""
from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from src.app import app
from src.services import maintenance as mx
from src.services import principal as P

MINE, THEIRS = uuid4(), uuid4()


def make(building_ids, role="user"):
    return P.Principal(user_id=uuid4(), email="x@example.com", organization_id=None,
                       role=role, building_ids=building_ids)


# ── the boundary ──────────────────────────────────────────────────────────────

def test_an_unrestricted_caller_gets_no_predicate():
    sql, params = mx._scope_sql(None, "w.building_id")
    assert sql == "" and params == {}


def test_a_restricted_caller_is_narrowed_to_their_buildings():
    sql, params = mx._scope_sql([MINE], "w.building_id")
    assert "w.building_id = ANY(CAST(:scope_b AS uuid[]))" in sql
    assert params["scope_b"] == [str(MINE)]


def test_allocated_to_nothing_matches_no_row_rather_than_every_row():
    """An empty allocation is a real answer. Returning no predicate would hand that caller
    the whole portfolio."""
    sql, params = mx._scope_sql([], "w.building_id")
    assert sql == " AND FALSE" and params == {}


@pytest.mark.parametrize("path", [
    "/api/maintenance/decisions",
    "/api/maintenance/inspections",
    "/api/maintenance/ppm",
    "/api/maintenance/summary",
])
def test_every_maintenance_route_needs_a_caller(path):
    client = TestClient(app)
    resp = client.get(path)
    assert resp.status_code == 401
    assert resp.json()["errors"][0]["code"] == "missing_token"


def test_the_ppm_scheduler_is_no_longer_open():
    """It creates work orders from an external system and had no caller at all."""
    client = TestClient(app)
    assert client.get("/api/ppm/due").status_code == 401


def test_asking_for_a_building_you_are_not_allocated_to_is_refused(monkeypatch):
    """403, not an empty list: "you may not see this" and "there is nothing here" are
    different answers and the screen should not confuse them."""
    from fastapi import HTTPException

    from src.api.routes.maintenance import _scope

    with pytest.raises(HTTPException) as exc:
        _scope(make((MINE,)), str(THEIRS))
    assert exc.value.status_code == 403


def test_naming_one_of_your_own_buildings_narrows_to_it():
    from src.api.routes.maintenance import _scope

    assert _scope(make((MINE, THEIRS)), str(MINE)) == [MINE]
    assert _scope(make(None), None) is None
    assert _scope(make(()), None) == []


# ── the state machine ─────────────────────────────────────────────────────────

def test_the_states_that_put_a_decision_in_front_of_someone_are_disjoint():
    buckets = (set(mx.BLOCKED), set(mx.AWAITING), set(mx.LIVE), set(mx.DONE))
    for i, a in enumerate(buckets):
        for b in buckets[i + 1:]:
            assert not (a & b), f"a status cannot be in two buckets: {a & b}"


def test_both_databases_spellings_of_a_pending_approval_are_recognised():
    assert "pending_approval" in mx.AWAITING       # one database
    assert "pending approval" in mx.AWAITING       # the other
    assert "completed" in mx.DONE and "closed" in mx.DONE


def test_the_state_is_decided_in_sql_not_after_the_limit():
    """Deciding it in Python meant the LIMIT ran first: where the newest two hundred orders
    are all completed, every decision was cut before anything looked at it."""
    import inspect

    src = inspect.getsource(mx.decisions)
    assert "CASE WHEN lower(coalesce(w.status" in src
    assert "AND {state} IS NOT NULL" in src or "IS NOT NULL\n             ORDER BY" in src


def test_each_section_runs_in_its_own_savepoint():
    """One shape surprise must not abort the transaction and take the sections after it
    down with it — the failure mode that once turned a whole report into zeroes."""
    import inspect

    for fn in (mx.decisions, mx._decisions_from_approvals, mx.inspections, mx.ppm_health):
        assert "begin_nested()" in inspect.getsource(fn), fn.__name__


def test_a_module_that_raised_a_decision_is_named_in_the_screens_own_words():
    assert mx.SOURCE_OF["energy_anomaly"] == "Energy"
    assert mx.SOURCE_OF["compliance_certificate"] == "Compliance"
    assert mx.SOURCE_OF["vendor"] == "Vendors"


# ── shape tolerance ───────────────────────────────────────────────────────────

def test_the_column_spelling_is_read_not_assumed():
    """One database spells the key wo_code and the other work_order_id; one has
    corrective_action on inspections and the other does not."""
    cols = {"work_order_id", "status", "building_id"}
    assert mx._pick(cols, "wo_code", "work_order_id") == "work_order_id"
    assert mx._pick({"wo_code"}, "wo_code", "work_order_id") == "wo_code"
    assert mx._pick(cols, "nothing_like_this") is None


def test_a_number_that_will_not_parse_is_null_not_a_crash():
    assert mx._num(None) is None
    assert mx._num("not a number") is None
    assert mx._num("12.5") == 12.5
