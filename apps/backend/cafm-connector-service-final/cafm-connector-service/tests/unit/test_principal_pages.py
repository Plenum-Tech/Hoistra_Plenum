"""The pages a caller holds travel with the caller.

The identity service returns ``pages`` beside the buildings: null for a caller who may open
every page, a list for one an administrator held to some. This service keeps them on the
Principal so its routes can refuse data owned by a page the caller does not hold. A reply
without the field is an identity service that predates it, and is read as "every page".
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from uuid import uuid4

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
os.environ.setdefault("DB_URL", "postgresql+asyncpg://u:p@localhost/db")
os.environ.setdefault("JWT_SECRET", "test-secret")

from cafm_connector.api import security as P  # noqa: E402


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
