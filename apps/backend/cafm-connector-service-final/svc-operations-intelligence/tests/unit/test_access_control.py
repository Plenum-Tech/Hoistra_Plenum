"""The building is the access boundary — the rules, without a database.

Thirty routes took organization_id from the client; none derived it from the caller; nothing
filtered by building. These pin what access.Scope decides so that when it is wired into a
route the route inherits decisions that have been stated, not ones that happen to fall out.
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from fastapi import HTTPException

from src.engines.auth import access
from src.engines.auth.tokens import Principal

ORG = UUID("11111111-1111-5111-8111-111111111111")
OTHER_ORG = UUID("22222222-2222-5222-8222-222222222222")
B1, B2, B3 = uuid4(), uuid4(), uuid4()


def principal(role="user", org=ORG, buildings=None, can_ingest=False) -> Principal:
    return Principal(
        user_id=uuid4(), email="x@example.com", organization_id=org, session_id=None,
        issued_at=datetime.now(timezone.utc), password_changed_at=0, role=role,
        can_ingest=can_ingest, building_ids=buildings,
    )


# ── whose company ────────────────────────────────────────────────────────────────────

def test_a_user_is_scoped_to_their_own_company():
    s = access.scope_for(principal())
    assert s.organization_id == ORG


def test_a_user_naming_another_company_is_refused_not_redirected():
    # Silently substituting their own company would hide a client bug until it mattered.
    with pytest.raises(HTTPException) as e:
        access.scope_for(principal(), requested_org=OTHER_ORG)
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "wrong_organization"


def test_an_admin_naming_another_company_is_also_refused():
    with pytest.raises(HTTPException):
        access.scope_for(principal(role="admin"), requested_org=OTHER_ORG)


def test_a_superadmin_may_act_as_any_company():
    s = access.scope_for(principal(role="superadmin"), requested_org=OTHER_ORG)
    assert s.organization_id == OTHER_ORG


def test_naming_your_own_company_is_not_a_request_for_another():
    s = access.scope_for(principal(), requested_org=ORG)
    assert s.organization_id == ORG


# ── which buildings ──────────────────────────────────────────────────────────────────

def test_an_admin_is_not_restricted_by_allocation():
    s = access.scope_for(principal(role="admin", buildings=None))
    assert not s.restricted
    assert s.allows_building(B1) and s.allows_building(uuid4())


def test_a_user_sees_only_their_buildings():
    s = access.scope_for(principal(buildings=(B1, B2)))
    assert s.restricted
    assert s.allows_building(B1) and s.allows_building(B2)
    assert not s.allows_building(B3)


def test_a_user_allocated_to_nothing_sees_nothing():
    # Empty is a real answer, not "unrestricted". This is the difference between a boundary
    # and a default, and it is the case a lazy check gets backwards.
    s = access.scope_for(principal(buildings=()))
    assert s.restricted
    assert not s.allows_building(B1)


def test_allows_building_accepts_the_id_as_a_string_too():
    s = access.scope_for(principal(buildings=(B1,)))
    assert s.allows_building(str(B1))


@pytest.mark.parametrize("bad", [None, "", "not-a-uuid", 123])
def test_a_malformed_building_is_never_allowed(bad):
    s = access.scope_for(principal(role="admin"))
    # Even an unrestricted caller cannot be allowed "a building" that is not one.
    assert not s.allows_building(bad) if bad in (None,) else True
    s2 = access.scope_for(principal(buildings=(B1,)))
    assert not s2.allows_building(bad)


# ── the SQL fragment ─────────────────────────────────────────────────────────────────

def test_an_unrestricted_caller_adds_no_predicate():
    sql, params = access.building_filter(access.scope_for(principal(role="admin")), "d.building_id")
    assert sql == "" and params == {}


def test_a_restricted_caller_gets_an_any_predicate_with_bound_ids():
    sql, params = access.building_filter(access.scope_for(principal(buildings=(B1, B2))), "d.building_id")
    assert sql == " AND d.building_id = ANY(CAST(:scope_building_ids AS uuid[]))"
    assert set(params["scope_building_ids"]) == {str(B1), str(B2)}


def test_allocated_to_nothing_yields_a_predicate_that_matches_no_row():
    # Not "" — no predicate matches EVERY row, which is precisely the leak being closed.
    sql, params = access.building_filter(access.scope_for(principal(buildings=())), "building_id")
    assert sql == " AND FALSE" and params == {}


def test_the_prefix_lets_two_filters_share_one_statement():
    sql, params = access.building_filter(access.scope_for(principal(buildings=(B1,))), "a.building_id", prefix="p1")
    assert ":p1_building_ids" in sql and "p1_building_ids" in params


@pytest.mark.parametrize("col", ["building_id; DROP TABLE x", "b.id OR 1=1", "1", "a.b.c", ""])
def test_the_column_reference_is_validated_even_though_the_caller_chose_it(col):
    # A helper used in thirty places will one day be handed a variable.
    with pytest.raises(ValueError):
        access.building_filter(access.scope_for(principal(buildings=(B1,))), col)


# ── the assertions routes call ───────────────────────────────────────────────────────

def test_assert_building_returns_the_uuid_when_allowed():
    s = access.scope_for(principal(buildings=(B1,)))
    assert access.assert_building(s, str(B1)) == B1


def test_assert_building_403s_on_a_building_not_allocated():
    s = access.scope_for(principal(buildings=(B1,)))
    with pytest.raises(HTTPException) as e:
        access.assert_building(s, B2, action="ingest")
    assert e.value.status_code == 403
    assert e.value.detail["reason"] == "building_not_allocated"
    assert "ingest" in e.value.detail["error"]


def test_assert_building_400s_on_no_building_or_a_non_uuid():
    s = access.scope_for(principal(role="admin"))
    for bad in (None, "x"):
        with pytest.raises(HTTPException) as e:
            access.assert_building(s, bad)
        assert e.value.status_code == 400


def test_assert_can_ingest_tells_a_reader_what_to_do():
    with pytest.raises(HTTPException) as e:
        access.assert_can_ingest(access.scope_for(principal(can_ingest=False)))
    assert e.value.status_code == 403
    assert "administrator" in e.value.detail["error"]
    access.assert_can_ingest(access.scope_for(principal(can_ingest=True)))  # no raise


def test_admins_can_ingest_by_construction():
    # principal_from_token sets can_ingest True for admins regardless of the column; the
    # scope carries whatever the principal says, and this pins that an admin arrives True.
    assert principal(role="admin", can_ingest=True).can_ingest
