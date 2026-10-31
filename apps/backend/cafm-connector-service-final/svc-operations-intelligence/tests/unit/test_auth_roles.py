"""Who may grant what.

These are the rules that decide who gets the run of the platform, so every branch is
pinned here rather than left to be exercised only through an endpoint.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.engines.auth import roles as R

ORG_A, ORG_B = uuid4(), uuid4()
ACTOR, TARGET = uuid4(), uuid4()


def decide(actor_role, new_role, *, target_role=R.USER, actor_org=ORG_A,
           target_org=ORG_A, actor_id=ACTOR, target_id=TARGET):
    return R.may_assign(
        actor_role=actor_role, actor_id=actor_id, actor_org=actor_org,
        target_id=target_id, target_role=target_role, target_org=target_org,
        new_role=new_role,
    )


# ── ranking ──────────────────────────────────────────────────────────────────


def test_the_three_roles_are_ordered():
    assert R.rank(R.SUPERADMIN) > R.rank(R.ADMIN) > R.rank(R.USER)


def test_at_least_admits_everyone_above():
    assert R.at_least(R.SUPERADMIN, R.ADMIN)
    assert R.at_least(R.ADMIN, R.ADMIN)
    assert not R.at_least(R.USER, R.ADMIN)


@pytest.mark.parametrize("junk", ["Admin", "SUPERADMIN", "fm", "root", "", None, "  "])
def test_an_unrecognised_role_grants_nothing(junk):
    """Fails closed. A row that somehow holds 'Admin' or 'fm' — a typo in a script, a
    half-finished migration — behaves as an ordinary account rather than as whatever the
    nearest match happens to be."""
    if junk in ("Admin", "SUPERADMIN"):
        # Case is normalised, so these two ARE recognised. Named so the test says which
        # kinds of junk are tolerated and which are not.
        assert R.rank(junk) > 0
        return
    assert R.rank(junk) == 0
    assert not R.is_admin(junk)


def test_case_and_whitespace_do_not_change_a_role():
    assert R.normalise("  Admin ") == R.ADMIN
    assert R.normalise("SuperAdmin") == R.SUPERADMIN
    assert R.normalise("facilities-manager") is None


# ── a superadmin may do anything, to anyone but themselves ───────────────────


@pytest.mark.parametrize("new_role", [R.SUPERADMIN, R.ADMIN, R.USER])
def test_a_superadmin_may_set_any_role(new_role):
    assert decide(R.SUPERADMIN, new_role).allowed


def test_a_superadmin_may_act_across_organisations():
    assert decide(R.SUPERADMIN, R.ADMIN, target_org=ORG_B).allowed


# ── an admin is bounded in three directions ──────────────────────────────────


def test_an_admin_may_promote_a_user_in_their_own_organisation():
    assert decide(R.ADMIN, R.ADMIN).allowed


def test_an_admin_may_not_appoint_a_superadmin():
    """Otherwise admin and superadmin are one role with two names: any admin could award
    themselves the other through a colleague."""
    d = decide(R.ADMIN, R.SUPERADMIN)
    assert not d.allowed and d.reason == "cannot_grant_superadmin"


def test_an_admin_may_not_demote_a_superadmin():
    d = decide(R.ADMIN, R.USER, target_role=R.SUPERADMIN)
    assert not d.allowed and d.reason == "cannot_demote_superadmin"


def test_an_admin_may_not_reach_into_another_organisation():
    d = decide(R.ADMIN, R.ADMIN, target_org=ORG_B)
    assert not d.allowed and d.reason == "other_organisation"


def test_an_admin_with_no_organisation_cannot_act_on_anyone():
    """A null organisation must not compare equal to another null and become a match."""
    d = decide(R.ADMIN, R.ADMIN, actor_org=None, target_org=None)
    assert not d.allowed and d.reason == "other_organisation"


# ── nobody, at any level, changes their own role ─────────────────────────────


@pytest.mark.parametrize("actor_role", [R.SUPERADMIN, R.ADMIN, R.USER])
def test_nobody_changes_their_own_role(actor_role):
    """Two things at once: one taken-over session cannot promote itself, and a superadmin
    cannot accidentally demote the last superadmin — themselves."""
    d = decide(actor_role, R.SUPERADMIN, actor_id=ACTOR, target_id=ACTOR)
    assert not d.allowed and d.reason == "self"


def test_the_self_check_runs_before_the_privilege_check():
    """A plain user aiming at themselves must be told it is the self rule, not that they
    lack privilege — otherwise the message changes with the caller's role and becomes a
    way to probe what role you have."""
    assert decide(R.USER, R.ADMIN, actor_id=ACTOR, target_id=ACTOR).reason == "self"


# ── an ordinary user grants nothing ──────────────────────────────────────────


@pytest.mark.parametrize("new_role", [R.SUPERADMIN, R.ADMIN, R.USER])
def test_a_plain_user_may_not_set_any_role(new_role):
    d = decide(R.USER, new_role)
    assert not d.allowed and d.reason == "forbidden"


def test_an_unknown_role_is_refused_before_anything_else():
    """Checked first, so a superadmin sending 'root' gets told the role does not exist
    rather than being allowed and writing a value nothing checks for."""
    d = decide(R.SUPERADMIN, "root")
    assert not d.allowed and d.reason == "unknown_role"
    assert "superadmin" in d.message and "admin" in d.message


# ── the defaults ─────────────────────────────────────────────────────────────


def test_self_registration_creates_the_lowest_role():
    """Never admin. An endpoint that takes the role from the request body is an endpoint
    where anyone registers as an administrator."""
    assert R.DEFAULT_ROLE == R.USER


def test_every_role_has_a_label_a_person_can_read():
    assert set(R.LABELS) == R.ROLES
    assert "facilities manager" in R.LABELS[R.USER].lower()


def test_describe_lists_them_highest_first_with_the_default_marked():
    described = R.describe()
    assert [d["role"] for d in described] == [R.SUPERADMIN, R.ADMIN, R.USER]
    assert [d["default"] for d in described] == [False, False, True]
