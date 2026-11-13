"""The pages a caller holds travel with the caller.

The identity service returns ``pages`` beside the buildings: null for a caller who may open
every page, a list for one an administrator held to some. This service keeps them on the
Principal so its routes can refuse data owned by a page the caller does not hold. A reply
without the field is an identity service that predates it, and is read as "every page".
"""
from __future__ import annotations

from uuid import uuid4

from src.services import principal as P


def me(**user):
    return {"user": {"id": str(uuid4()), "email": "a@b.com", "organization_id": str(uuid4()),
                     "role": "user", "building_ids": [], "can_ingest": False, **user}}


def test_pages_absent_means_unrestricted():
    p = P._from_me(me())
    assert p.pages is None
    assert p.allows_page("energy") and p.allows_page("anything")


def test_pages_null_means_unrestricted():
    assert P._from_me(me(pages=None)).pages is None


def test_pages_list_is_kept():
    p = P._from_me(me(pages=["compliance", "buildings"]))
    assert p.pages == frozenset({"compliance", "buildings"})
    assert p.allows_page("compliance") and not p.allows_page("energy")


def test_unknown_page_keys_are_dropped():
    assert P._from_me(me(pages=["compliance", "payroll", 7, None])).pages == frozenset({"compliance"})


def test_empty_list_holds_nothing():
    p = P._from_me(me(pages=[]))
    assert p.pages == frozenset()
    assert not p.allows_page("buildings")


def test_junk_instead_of_a_list_holds_nothing():
    # Fails closed: a restricted answer this service cannot read is never read as unrestricted.
    assert P._from_me(me(pages="compliance")).pages == frozenset()
    assert P._from_me(me(pages=5)).pages == frozenset()


def test_the_role_and_the_narrowing_time_are_kept():
    p = P._from_me(me(pages=["energy"], access_role="general_user",
                      access_narrowed_at="2026-11-12T09:30:00+00:00"))
    assert p.access_role == "general_user"
    assert p.access_narrowed_at.isoformat() == "2026-11-12T09:30:00+00:00"


def test_a_naive_or_unreadable_narrowing_time():
    assert P._from_me(me(pages=["energy"], access_narrowed_at="2026-11-12T09:30:00")
                      ).access_narrowed_at.isoformat() == "2026-11-12T09:30:00+00:00"
    # Sent but unreadable, for a caller held to pages: everything earlier is hidden, not nothing.
    assert P._from_me(me(pages=["energy"], access_narrowed_at="yesterday")).access_narrowed_at.year == 9999
    assert P._from_me(me(access_narrowed_at="yesterday")).access_narrowed_at is None
    assert P._from_me(me()).access_narrowed_at is None and P._from_me(me()).access_role is None
