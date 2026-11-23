"""The Consultant role (23 Nov 2026, Hussain).

A consultant opens one screen, Buildings, yet reads every module's data through the chat,
their sessions and reports, inside the buildings an administrator allocates to them. The
other roles hold their ticked pages for screens AND data alike; a consultant splits the two:
the data is a normal user's (``pages is None``) and the screens are fixed (``screens``).
"""
from __future__ import annotations

import pytest

from src.engines.auth import pages as P


def test_consultant_is_a_role_with_its_label():
    assert P.ACCESS_ROLES["consultant"] == "Consultant"
    assert P.label("consultant") == "Consultant"


def test_a_consultant_reads_every_page_of_data():
    # None, not a set: every service and the chat treat the caller as a normal user, so the
    # chat, sessions and reports reach compliance, energy, vendors, maintenance and assets.
    assert P.resolve("user", "consultant", ["buildings"]) == ("consultant", None)
    assert P.resolve("user", "CONSULTANT", None) == ("consultant", None)
    assert P.resolve("user", " consultant ", ["compliance"]) == ("consultant", None)


def test_an_admin_with_consultant_on_the_row_is_still_an_admin():
    assert P.resolve("admin", "consultant", ["buildings"]) == (None, None)


def test_a_consultant_sees_only_the_buildings_screen_by_default():
    assert P.screens("consultant", None) == ["buildings"]
    assert P.screens("consultant", None, None) == ["buildings"]
    assert P.screens("consultant", None, ["buildings"]) == ["buildings"]


def test_a_consultant_also_sees_the_pages_ticked_for_them_and_buildings_always():
    # 23 Nov 2026, Hussain: Buildings by default, with tick boxes for Vendors, Maintenance,
    # Assets, Energy and Compliance.
    assert P.screens("consultant", None, ["vendors", "compliance"]) == ["buildings", "compliance", "vendors"]
    assert P.screens("consultant", None, ["ops", "assets", "energy"]) == ["buildings", "energy", "ops", "assets"]
    # junk in the column never adds a screen
    assert P.screens("consultant", None, ["nope", 3]) == ["buildings"]
    assert P.screens("consultant", None, "vendors") == ["buildings"]


def test_whatever_is_ticked_a_consultants_data_is_every_page():
    assert P.resolve("user", "consultant", ["buildings", "vendors"]) == ("consultant", None)
    assert P.data_after("consultant", ["buildings", "vendors", "energy"]) is None


def test_the_stored_ticks_never_change_anyone_elses_screens():
    assert P.screens("general_user", frozenset({"energy"}), ["vendors"]) == ["energy"]
    assert P.screens(None, None, ["vendors"]) is None


def test_screens_of_the_other_kinds_follow_their_pages():
    assert P.screens(None, None) is None
    assert P.screens("compliance_officer", frozenset({"compliance", "buildings"})) == ["buildings", "compliance"]
    assert P.screens("general_user", frozenset()) == []


def test_public_fields_tell_a_client_the_screens():
    out = P.public_fields("consultant", None)
    assert out["access_role"] == "consultant"
    assert out["access_role_label"] == "Consultant"
    assert out["pages"] is None
    assert out["screens"] == ["buildings"]
    assert P.public_fields("consultant", None, ["energy"])["screens"] == ["buildings", "energy"]
    assert P.public_fields("consultant", None, ["energy"])["pages"] is None
    assert P.public_fields(None, None)["screens"] is None
    assert P.public_fields("general_user", frozenset({"energy"}))["screens"] == ["energy"]


@pytest.mark.parametrize("sent", [None, [], ["buildings"], ["buildings", "buildings"]])
def test_assigning_consultant_stores_buildings_by_default(sent):
    assert P.validate_assignment(platform_role="user", access_role="consultant", pages=sent) == (
        "consultant", ["buildings"])


def test_a_consultant_may_be_given_more_pages_to_open_and_keeps_buildings():
    assert P.validate_assignment(platform_role="user", access_role="consultant",
                                 pages=["compliance", "vendors", "Vendors"]) == (
        "consultant", ["buildings", "compliance", "vendors"])
    assert P.validate_assignment(platform_role="user", access_role="consultant",
                                 pages=["ops", "assets", "energy", "buildings"]) == (
        "consultant", ["buildings", "energy", "ops", "assets"])


def test_an_unknown_page_for_a_consultant_is_refused_not_dropped():
    with pytest.raises(P.AccessRoleError) as e:
        P.validate_assignment(platform_role="user", access_role="consultant", pages=["payroll"])
    assert e.value.reason == "unknown_page"


def test_consultant_cannot_be_given_to_an_admin():
    with pytest.raises(P.AccessRoleError) as e:
        P.validate_assignment(platform_role="admin", access_role="consultant", pages=["buildings"])
    assert e.value.reason == "role_needs_plain_user"


def test_data_after_an_assignment():
    # What the person's data is bounded by once the assignment is stored: decides whether
    # their earlier chat history is hidden (narrowed). A consultant's data is not narrowed.
    assert P.data_after("consultant", ["buildings"]) is None
    assert P.data_after(None, None) is None
    assert P.data_after("general_user", ["energy"]) == frozenset({"energy"})


def test_becoming_a_consultant_hides_no_history():
    assert P.narrowed(None, P.data_after("consultant", ["buildings"])) is False
    # A role user who becomes a consultant gains data, never loses it.
    assert P.narrowed(frozenset({"compliance"}), P.data_after("consultant", ["buildings"])) is False
    # A consultant given an ordinary role does lose data.
    assert P.narrowed(None, P.data_after("general_user", ["buildings"])) is True
