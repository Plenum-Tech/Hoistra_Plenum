"""Which pages a person may open, and who may be given a role.

An access role is a label an administrator gives a plain user, together with the pages that
user may open. These are the rules that decide what a restricted person can read, so every
branch is pinned here rather than exercised only through an endpoint.
"""
from __future__ import annotations

import pytest

from src.engines.auth import pages as P


def test_the_six_pages_and_four_roles_are_exactly_these():
    assert P.PAGES == ("buildings", "compliance", "energy", "vendors", "ops", "assets")
    assert P.ACCESS_ROLES == {
        "portfolio_management": "Portfolio Management",
        "property_manager": "Property Manager",
        "general_user": "General User",
        "compliance_officer": "Compliance Officer",
    }


# ── reading a row ─────────────────────────────────────────────────────────────

def test_a_plain_user_with_no_role_is_unrestricted():
    assert P.resolve("user", None, None) == (None, None)
    assert P.resolve("user", "", None) == (None, None)


def test_pages_left_on_a_row_without_a_role_still_restrict():
    # Found in review: a row edited by hand lost its role and kept its pages, and read as
    # "every page". A restriction is never lost to a missing label.
    assert P.resolve("user", "", ["compliance"]) == (None, frozenset({"compliance"}))


def test_resolve_is_unrestricted_for_admin_even_with_a_role_on_the_row():
    assert P.resolve("admin", "compliance_officer", ["compliance"]) == (None, None)
    assert P.resolve("superadmin", "compliance_officer", ["compliance"]) == (None, None)


def test_resolve_keeps_the_pages_of_a_role_user():
    role, pages = P.resolve("user", "compliance_officer", ["compliance", "buildings"])
    assert role == "compliance_officer"
    assert pages == frozenset({"compliance", "buildings"})


def test_resolve_drops_unknown_pages():
    _, pages = P.resolve("user", "general_user", ["compliance", "payroll", 7, None])
    assert pages == frozenset({"compliance"})


@pytest.mark.parametrize("raw", [None, [], "compliance", 5, ["nope"]])
def test_resolve_role_with_no_valid_page_holds_nothing(raw):
    # Fails closed: a role with nothing readable behind it is restricted to nothing,
    # never treated as "no restriction".
    assert P.resolve("user", "general_user", raw) == ("general_user", frozenset())


def test_resolve_unknown_role_key_is_still_restricted():
    # A row holding a role this code has never heard of is still a restricted row.
    role, pages = P.resolve("user", "night_porter", ["energy"])
    assert role == "night_porter"
    assert pages == frozenset({"energy"})


def test_an_unknown_platform_role_is_treated_as_a_plain_user():
    # roles.rank() ranks an unknown role below everything; it must not escape the pages.
    assert P.resolve("Admin ", "general_user", ["energy"]) == (None, None)
    assert P.resolve("fm", "general_user", ["energy"]) == ("general_user", frozenset({"energy"}))
    assert P.resolve(None, "general_user", ["energy"]) == ("general_user", frozenset({"energy"}))


def test_labels():
    assert P.label("property_manager") == "Property Manager"
    assert P.label(None) is None
    assert P.label("night_porter") is None


# ── assigning ─────────────────────────────────────────────────────────────────

def test_validate_no_role_and_no_pages_is_a_normal_user():
    assert P.validate_assignment(platform_role="user", access_role=None, pages=None) == (None, None)
    assert P.validate_assignment(platform_role="admin", access_role=None, pages=None) == (None, None)


def reason(**kw) -> str:
    with pytest.raises(P.AccessRoleError) as e:
        P.validate_assignment(**kw)
    assert e.value.message
    return e.value.reason


def test_validate_needs_at_least_one_page():
    assert reason(platform_role="user", access_role="general_user", pages=None) == "pages_required"
    assert reason(platform_role="user", access_role="general_user", pages=[]) == "pages_required"


def test_validate_refuses_an_unknown_role():
    assert reason(platform_role="user", access_role="night_porter",
                  pages=["energy"]) == "unknown_access_role"


def test_validate_refuses_an_unknown_page():
    assert reason(platform_role="user", access_role="general_user",
                  pages=["energy", "payroll"]) == "unknown_page"


def test_validate_refuses_pages_without_a_role():
    assert reason(platform_role="user", access_role=None, pages=["energy"]) == "pages_without_role"


@pytest.mark.parametrize("platform_role", ["admin", "superadmin"])
def test_validate_refuses_a_role_on_an_admin(platform_role):
    assert reason(platform_role=platform_role, access_role="general_user",
                  pages=["energy"]) == "role_needs_plain_user"


def test_validate_orders_and_dedupes():
    assert P.validate_assignment(
        platform_role="user", access_role="compliance_officer",
        pages=["assets", "compliance", "assets", "buildings"],
    ) == ("compliance_officer", ["buildings", "compliance", "assets"])


def test_the_messages_name_no_internals():
    for kw in (dict(platform_role="user", access_role="general_user", pages=[]),
               dict(platform_role="user", access_role="x", pages=["energy"]),
               dict(platform_role="user", access_role="general_user", pages=["x"]),
               dict(platform_role="user", access_role=None, pages=["energy"]),
               dict(platform_role="admin", access_role="general_user", pages=["energy"])):
        with pytest.raises(P.AccessRoleError) as e:
            P.validate_assignment(**kw)
        text = e.value.message.lower()
        for internal in ("allowed_pages", "access_role", "plenum", "platform_role", "svc-"):
            assert internal not in text, (kw, text)


# ── narrowing ─────────────────────────────────────────────────────────────────

F = frozenset


@pytest.mark.parametrize("before,after,expected", [
    (None, F({"energy"}), True),                       # a normal user is given a role
    (F({"energy", "assets"}), F({"energy"}), True),    # a page is removed
    (F({"energy"}), F({"assets"}), True),              # swapped: one was removed
    (F({"energy"}), F({"energy", "assets"}), False),   # only widened
    (F({"energy"}), F({"energy"}), False),             # unchanged
    (F({"energy"}), None, False),                      # back to a normal user
    (None, None, False),
    (F(), F(), False),
    (None, F(), True),
])
def test_narrowed(before, after, expected):
    assert P.narrowed(before, after) is expected
