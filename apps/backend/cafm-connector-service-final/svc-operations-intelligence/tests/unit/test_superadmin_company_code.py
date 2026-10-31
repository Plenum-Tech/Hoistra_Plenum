"""organizations.code is NOT NULL and UNIQUE in the live schema, but the create-company
form never asked for one — every creation 500'd on the NOT NULL violation before the
company (and with it the admin invitation and its email) ever existed. _unique_code()
derives a slug from the name so the insert always has one.
"""
from __future__ import annotations

import asyncio

from src.api.routes.superadmin import _unique_code


class FakeSession:
    def __init__(self, taken: set[str]):
        self.taken = taken
        self.checked: list[str] = []
    async def execute(self, stmt, params):
        code = params["c"]
        self.checked.append(code)
        return FakeResult(code in self.taken)


class FakeResult:
    def __init__(self, hit: bool): self._hit = hit
    def scalar(self): return 1 if self._hit else None


def test_a_plain_name_becomes_an_uppercase_hyphenated_slug():
    s = FakeSession(taken=set())
    code = asyncio.run(_unique_code(s, "Acme Facilities LLC"))
    assert code == "ACME-FACILITIES-LLC"
    assert s.checked == ["ACME-FACILITIES-LLC"], "no collision — the first candidate is used"


def test_a_collision_gets_a_short_random_suffix_instead_of_failing(monkeypatch):
    monkeypatch.setattr("src.api.routes.superadmin.secrets.token_hex", lambda n: "ab12")
    s = FakeSession(taken={"ACME-FACILITIES-LLC"})
    code = asyncio.run(_unique_code(s, "Acme Facilities LLC"))
    assert code == "ACME-FACILITIES-LLC-AB12"
    assert len(s.checked) == 2, "checked the plain slug, found it taken, checked the suffixed one"


def test_punctuation_and_whitespace_collapse_to_single_hyphens():
    s = FakeSession(taken=set())
    code = asyncio.run(_unique_code(s, "  Bright & Co. (UK) — Facilities!!  "))
    assert code == "BRIGHT-CO-UK-FACILITIES"


def test_a_blank_or_symbol_only_name_still_produces_a_usable_code():
    s = FakeSession(taken=set())
    code = asyncio.run(_unique_code(s, "!!!"))
    assert code == "COMPANY"
