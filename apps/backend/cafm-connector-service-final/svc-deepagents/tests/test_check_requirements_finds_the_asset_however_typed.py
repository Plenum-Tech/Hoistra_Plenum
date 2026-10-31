"""check_requirements finds the asset however its code or name is typed (29 Oct 2026).

check_requirements compared the typed asset_code with `a.asset_code = :asset_code`: exact and
case-sensitive. "pt-b-301-a-boiler-01", "PTB301ABOILER01" or the asset's name found no row, and with
no row there were no findings, so an asset with an overdue PM was reported "compliant" — and so was
an asset that does not exist. The code is now resolved first through the same ladder every name
uses (exact, same letters and digits, phrase, every word, close spelling), inside the caller's
building boundary; several assets are named back as ambiguous, none as not found.

No database is touched: the session factory is replaced and plays plenum_cafm.assets.
"""
from __future__ import annotations

import asyncio
import re
from uuid import UUID

import pytest

from src import database
from src.agents import compliance_agent as ca
from src.services.principal import Principal, caller_principal

ASSETS = [
    {"asset_code": "PT-B-301-A-BOILER-01", "asset_name": "Boiler 1 central plant"},
    {"asset_code": "PT-B-302-A-BOILER-01", "asset_name": "Boiler 1 east wing"},
    {"asset_code": "B-090765-CH-01", "asset_name": "Rooftop Chiller"},
    {"asset_code": "AHU-7", "asset_name": "Air Handling Unit 7"},
    {"asset_code": "AHU-70", "asset_name": "Air Handling Unit 70"},
]
#: Assets with an overdue PM, by their stored code.
OVERDUE = {"PT-B-301-A-BOILER-01", "B-090765-CH-01", "AHU-7"}


def _pg(pattern: str) -> str:
    """A Postgres ARE of the kind the resolver sends, as a Python regex."""
    return pattern.replace("[^[:alnum:]]", "[^a-zA-Z0-9]").replace("\\m", r"\b").replace("\\M", r"\b")


class _Rows:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        s, p = str(sql), dict(params or {})
        self.seen.append((s, p))
        mode = re.search(r"/\* resolve asset: (\w+) \*/", s)
        if mode:
            return _Rows(self._resolve(mode.group(1), p))
        code = p.get("asset_code")
        if "maintenance_plans" in s and code in OVERDUE:
            return _Rows([{"sm_code": "SM-" + code, "frequency_type": "months", "frequency_value": 1,
                           "next_due_date": "2026-09-01", "is_overdue": True}])
        return _Rows([])

    @staticmethod
    def _resolve(mode, p):
        def texts(a):
            return [a["asset_code"], a["asset_name"]]

        def both(a):
            return a["asset_code"] + " " + a["asset_name"]

        if mode == "exact":
            hit = [a for a in ASSETS if any(t.lower() == p["q"].lower() for t in texts(a))]
        elif mode == "compact":
            hit = [a for a in ASSETS if any(re.sub("[^a-z0-9]+", "", t.lower()) == p["q"] for t in texts(a))]
        elif mode == "search":
            hit = [a for a in ASSETS if any(re.search(_pg(p["q"]), t, re.IGNORECASE) for t in texts(a))]
        elif mode == "words":
            pats = [v for k, v in sorted(p.items()) if re.fullmatch(r"w\d+", k)]
            hit = [a for a in ASSETS if all(re.search(_pg(w), both(a), re.IGNORECASE) for w in pats)]
        elif mode == "fuzzy":
            hit = [a for a in ASSETS if re.search(_pg(p["q"]), both(a), re.IGNORECASE)]
        else:
            raise AssertionError(mode)
        return [dict(a) for a in hit]


@pytest.fixture
def seen(monkeypatch):
    seen = []
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(seen))
    return seen


def _check(code):
    return asyncio.run(ca.check_requirements.ainvoke({"asset_code": code}))


@pytest.mark.parametrize("typed,stored", [
    ("PT-B-301-A-BOILER-01", "PT-B-301-A-BOILER-01"),
    ("pt-b-301-a-boiler-01", "PT-B-301-A-BOILER-01"),       # case
    ("PTB301ABOILER01", "PT-B-301-A-BOILER-01"),            # without its hyphens
    ("Rooftop Chiller", "B-090765-CH-01"),                  # by its name
    ("chiller rooftop", "B-090765-CH-01"),                  # another word order
    ("Rooftop Chiler", "B-090765-CH-01"),                   # one letter missing
    ("asset AHU-7", "AHU-7"),                               # the kind word beside it; never AHU-70
])
def test_an_asset_is_found_however_it_is_typed(seen, typed, stored):
    out = _check(typed)
    assert out["asset_code"] == stored
    assert out["compliance_status"] == "non_compliant"      # its overdue PM is read
    assert out["findings_count"] == 1


def test_a_name_two_assets_share_is_ambiguous_never_one_of_them(seen):
    out = _check("Boiler 1")
    assert out.get("ambiguous") is True
    assert sorted(m["asset_code"] for m in out["matches"]) == ["PT-B-301-A-BOILER-01", "PT-B-302-A-BOILER-01"]
    assert "compliance_status" not in out
    assert not any("maintenance_plans" in s for s, _ in seen)


def test_an_asset_not_on_the_register_is_never_compliant(seen):
    out = _check("XYZ-999")
    assert "compliance_status" not in out
    assert "not on the register" in out["error"]


def test_the_asset_alone_names_nothing(seen):
    out = _check("the asset")
    assert "compliance_status" not in out
    assert not any("maintenance_plans" in s for s, _ in seen)


def test_the_resolution_keeps_the_callers_building_boundary(seen):
    p = Principal(user_id=UUID(int=7), email="u@x", organization_id=UUID(int=1), role="user",
                  can_ingest=False, building_ids=(UUID(int=42),))
    token = caller_principal.set(p)
    try:
        _check("pt-b-301-a-boiler-01")
    finally:
        caller_principal.reset(token)
    resolves = [(s, prm) for s, prm in seen if "resolve asset" in s]
    assert resolves
    for s, prm in resolves:
        assert "a.building_id = ANY(CAST(:scope_buildings AS uuid[]))" in s
        assert prm["scope_buildings"] == [str(UUID(int=42))]
