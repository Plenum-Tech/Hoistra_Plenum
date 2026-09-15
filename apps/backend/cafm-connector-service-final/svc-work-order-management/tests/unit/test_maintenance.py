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


class _FakeSession:
    """Just enough of AsyncSession for scope_building_ids: one execute returning rows, and
    a record of the organisation it was asked about."""

    def __init__(self, rows):
        self._rows = rows
        self.asked_for = None

    async def execute(self, stmt):
        for clause in getattr(stmt, "_where_criteria", ()):
            right = getattr(clause, "right", None)
            if right is not None and getattr(right, "value", None) is not None:
                self.asked_for = right.value
        rows = self._rows

        class _R:
            @staticmethod
            def all():
                return rows

        return _R()


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


@pytest.mark.asyncio
async def test_asking_for_a_building_you_are_not_allocated_to_is_refused(monkeypatch):
    """403, not an empty list: "you may not see this" and "there is nothing here" are
    different answers and the screen should not confuse them."""
    from fastapi import HTTPException

    from src.api.routes.maintenance import _scope

    with pytest.raises(HTTPException) as exc:
        await _scope(None, make((MINE,)), str(THEIRS))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_an_admin_cannot_read_another_company_by_naming_its_building():
    """assert_building returns early for ANY caller whose building_ids is None — which is
    every admin, not only a superadmin — so on its own it let an admin of one company read
    another company's rows by naming one of their buildings. The named building has to be one
    the caller's own company holds."""
    from fastapi import HTTPException

    from src.api.routes.maintenance import _scope

    org = uuid4()
    admin = P.Principal(user_id=uuid4(), email="a@example.com", organization_id=org,
                        role="admin", building_ids=None)
    # The company holds MINE and nothing else.
    session = _FakeSession([(MINE,)])
    with pytest.raises(HTTPException) as exc:
        await _scope(session, admin, str(THEIRS))
    assert exc.value.status_code == 403
    assert exc.value.detail["reason"] == "building_not_allocated"

    # Their own building still resolves.
    assert await _scope(_FakeSession([(MINE,)]), admin, str(MINE)) == [MINE]


@pytest.mark.asyncio
async def test_last_read_is_scoped_to_one_company():
    """It answered from the whole table, so a page showed whichever tenant ran last. No
    company resolves to no row rather than to everyone's."""
    from src.services import maintenance as mx_svc

    assert await mx_svc.last_inspection_read(None, organization_id=None) is None


def test_the_acting_company_is_the_one_a_row_is_stamped_with():
    """A run recording another company's data must not be labelled with the caller's own —
    last_inspection_read filters on that column."""
    from fastapi import HTTPException

    org, other = uuid4(), uuid4()
    user = P.Principal(user_id=uuid4(), email="u@example.com", organization_id=org,
                       role="admin", building_ids=None)
    su = P.Principal(user_id=uuid4(), email="s@example.com", organization_id=org,
                     role="superadmin", building_ids=None)

    assert P.effective_organization_id(user, None) == org
    assert P.effective_organization_id(user, str(org)) == org
    assert P.effective_organization_id(su, str(other)) == other, "a superadmin may act as another"
    with pytest.raises(HTTPException) as exc:
        P.effective_organization_id(user, str(other))
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_naming_one_of_your_own_buildings_narrows_to_it():
    """A named building short-circuits before any company lookup, so no session is touched."""
    from src.api.routes.maintenance import _scope

    assert await _scope(None, make((MINE, THEIRS)), str(MINE)) == [MINE]
    # An allocation is the boundary whether or not a company was named.
    assert await _scope(None, make((MINE,)), None) == [MINE]
    assert await _scope(None, make(()), None) == []


@pytest.mark.asyncio
async def test_an_unrestricted_caller_is_narrowed_to_their_own_company():
    """This is the hole the acting-company parameter closed. `building_ids is None` means
    "the whole company" — it used to reach the engines as None, which is no predicate at
    all, and that is the whole database. Two companies read the same rows."""
    from src.api.routes.maintenance import _scope

    org, b1, b2 = uuid4(), uuid4(), uuid4()
    session = _FakeSession([(b1,), (b2,)])
    admin = P.Principal(user_id=uuid4(), email="a@example.com", organization_id=org,
                        role="admin", building_ids=None)
    assert await _scope(session, admin, None) == [b1, b2]
    assert session.asked_for == org


@pytest.mark.asyncio
async def test_a_superadmin_naming_a_company_is_narrowed_to_it():
    """The whole point of the parameter. Without it a superadmin read every company's rows
    whatever the client's header said it was viewing, so two companies looked identical."""
    from src.api.routes.maintenance import _scope

    theirs, b = uuid4(), uuid4()
    session = _FakeSession([(b,)])
    su = P.Principal(user_id=uuid4(), email="s@example.com", organization_id=uuid4(),
                     role="superadmin", building_ids=None)
    assert await _scope(session, su, None, str(theirs)) == [b]
    assert session.asked_for == theirs, "the company asked for, not the one they belong to"


@pytest.mark.asyncio
async def test_a_superadmin_naming_no_company_still_reads_across_all_of_them():
    """The existing behaviour, kept: it is the one caller meant to, and every call that does
    not name a company is unchanged by this."""
    from src.api.routes.maintenance import _scope

    su = P.Principal(user_id=uuid4(), email="s@example.com", organization_id=uuid4(),
                     role="superadmin", building_ids=None)
    assert await _scope(_FakeSession([]), su, None) is None


@pytest.mark.asyncio
async def test_anyone_but_a_superadmin_naming_another_company_is_refused():
    """403 rather than silently their own: a client sending the wrong id has a bug, and
    substituting quietly hides it until it matters."""
    from fastapi import HTTPException

    from src.api.routes.maintenance import _scope

    admin = P.Principal(user_id=uuid4(), email="a@example.com", organization_id=uuid4(),
                        role="admin", building_ids=None)
    with pytest.raises(HTTPException) as exc:
        await _scope(_FakeSession([]), admin, None, str(uuid4()))
    assert exc.value.status_code == 403
    assert exc.value.detail["reason"] == "wrong_organization"


@pytest.mark.asyncio
async def test_naming_your_own_company_is_allowed_and_narrows_the_same_way():
    from src.api.routes.maintenance import _scope

    org, b = uuid4(), uuid4()
    session = _FakeSession([(b,)])
    admin = P.Principal(user_id=uuid4(), email="a@example.com", organization_id=org,
                        role="admin", building_ids=None)
    assert await _scope(session, admin, None, str(org)) == [b]


@pytest.mark.asyncio
async def test_an_acting_company_never_widens_an_allocation():
    """A user allocated to two buildings stays on those two whichever company is named —
    an override that could widen a boundary would not be a boundary."""
    from src.api.routes.maintenance import _scope

    su = P.Principal(user_id=uuid4(), email="s@example.com", organization_id=uuid4(),
                     role="superadmin", building_ids=(MINE,))
    assert await _scope(_FakeSession([(THEIRS,)]), su, None, str(uuid4())) == [MINE]


@pytest.mark.asyncio
async def test_a_caller_with_no_company_at_all_matches_nothing():
    """Failing closed is the only safe default for a tenancy boundary."""
    from src.api.routes.maintenance import _scope

    nobody = P.Principal(user_id=uuid4(), email="n@example.com", organization_id=None,
                         role="admin", building_ids=None)
    assert await _scope(_FakeSession([]), nobody, None) == []


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


# ── the work order key ────────────────────────────────────────────────────────

def test_the_work_order_is_keyed_on_the_tables_real_primary_key():
    """plenum_cafm.work_orders.id is the primary key on both databases and is set and
    distinct on every row. The service used to key on a column called work_order_id, which
    does not exist on one of them — so every ORM read raised there."""
    from src.models.work_order import WorkOrder

    col = WorkOrder.__table__.columns["id"]
    assert col.primary_key, "the primary key must be the id column"
    assert WorkOrder.work_order_id.property.columns[0].name == "id", (
        "the attribute keeps its name so 300-odd references and every route path still mean "
        "the same thing, but it must read the id column"
    )


def test_wo_code_is_carried_but_is_not_the_key():
    """1,728 of 2,775 rows set and only 948 distinct on one database — it cannot be a key."""
    from src.models.work_order import WorkOrder

    assert "wo_code" in WorkOrder.__table__.columns
    assert not WorkOrder.__table__.columns["wo_code"].primary_key


def test_neither_key_column_is_typed():
    """id is a uuid on one database and an integer on the other; wo_code is a varchar on
    both. Pinning a type breaks one of them."""
    from sqlalchemy import String

    from src.models.work_order import WorkOrder

    assert isinstance(WorkOrder.__table__.columns["id"].type, String)


def test_a_caller_holding_the_human_code_still_finds_the_row():
    """Links and scripts written before the key moved must keep working, and the comparison
    is cast to text because a uuid, an integer and a varchar cannot be compared directly."""
    import inspect

    from src.api.routes import work_orders

    src = inspect.getsource(work_orders._get_wo_or_404)
    assert "WorkOrder.wo_code" in src
    assert "cast(" in src and "String" in src
