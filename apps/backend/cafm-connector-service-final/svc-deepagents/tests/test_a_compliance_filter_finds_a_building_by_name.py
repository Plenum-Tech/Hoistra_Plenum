"""A compliance filter that names a building or a vendor finds it however it is typed (29 Oct 2026).

"HOW MANY FLOORS IN JASMEET BUILDING" found nothing because every comparison of a typed name with a
stored one did it its own way. The compliance register read is one of them: the planner's filter
{"field": "building_name", "op": "eq", "value": "Jasmeet Building"} was compiled to
lower(c.building_name) = lower('Jasmeet Building'), and the building is named "JASMEET". So the
certificates of a building that is on the register were not found, and "expired certificates at
Jasmeet Building" was relaxed to every building's expired certificates.

A name filter is now looked up by name first, with the same ladder every other path uses
(agents/name_match.py): exact, letters and digits only, the phrase at a word start, every word in
any order, then one wrong letter. The filter compares the stored names it found. Several names that
match are all kept; a phrase of kind words alone ("the building") matches nothing; codes stay exact.
The lookup reads only the caller's company, and only their buildings when they are held to some.

No database is touched: the session factory is replaced, and the few condition shapes the register
read writes are evaluated here over a handful of certificates.
"""
from __future__ import annotations

import asyncio
import re
from uuid import UUID

import pytest

from src import database
from src.agents import activity_log
from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal

HOME = "00000000-0000-0000-0000-000000000001"
COLS = {"organization_id", "org_id", "raw_metadata", "vendor_id", "status", "expiry_date",
        "building_name", "building_reference", "cert_scope", "building_id"}

#: (id, building_name, vendor_name, status)
CERTS = [
    ("c1", "JASMEET", None, "Expired"),
    ("c2", "JASMEET", None, "Current"),
    ("c3", "Jasmeet Heights", None, "Expired"),
    ("c4", "hussain tower", None, "Current"),
    ("c5", "Bishopsgate Tower", None, "Expired"),
    ("c6", "sams tower", None, "Current"),
    ("c7", "RT5, Crossrail", None, "Current"),
    ("c8", "B-090765", None, "Current"),
    ("c9", "Building 7", None, "Current"),
    ("c10", "Building 70", None, "Current"),
    ("v1", None, "Acme Facilities Ltd", "Current"),
    ("v2", None, "Northbridge Electrical", "Expired"),
]
_COL = {"c.building_name": 1, "v.vendor_name": 2, "c.status": 3}


class _Result:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


def _holds(cert, sql, params):
    """The condition shapes the register read writes, evaluated over one certificate as Postgres
    would (a NULL compared with anything is not true)."""
    if re.search(r"\bFALSE\b", sql):
        return False
    shapes = [
        (r"lower\(([\w.]+)::text\) = ANY\(CAST\(:(\w+) AS text\[\]\)\)", lambda v, p: v in p),
        (r"lower\(([\w.]+)::text\) IN \(SELECT lower\(x\) FROM unnest\(CAST\(:(\w+) AS text\[\]\)\) x\)",
         lambda v, p: v in [str(x).lower() for x in p]),
        (r"lower\(([\w.]+)::text\) <> ALL\(CAST\(:(\w+) AS text\[\]\)\)", lambda v, p: v not in p),
        (r"lower\(([\w.]+)::text\) = lower\(:(\w+)\)", lambda v, p: v == str(p).lower()),
        (r"lower\(([\w.]+)::text\) <> lower\(:(\w+)\)", lambda v, p: v != str(p).lower()),
    ]
    for shape, test in shapes:
        for col, p in re.findall(shape, sql):
            if col not in _COL:
                continue          # a column these certificates do not carry
            v = cert[_COL[col]]
            if v is None or not test(v.lower(), params[p]):
                return False
    return True


class _Session:
    def __init__(self, seen):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql, params=None):
        sql, params = str(sql), dict(params or {})
        self.seen.append((sql, params))
        m = re.search(r"/\* name lookup (\w+) \*/", sql)
        if m:
            idx = {"building_name": 1, "site_name": 1, "vendor_name": 2}[m.group(1)]
            names = sorted({c[idx] for c in CERTS if c[idx]})
            return _Result([{"n": n} for n in names])
        rows = [{"id": c[0], "building_name": c[1], "vendor_name": c[2], "status": c[3],
                 "cert_scope": "vendor" if c[2] else "building"}
                for c in CERTS if _holds(c, sql, params)]
        return _Result(rows)


@pytest.fixture
def seen(monkeypatch):
    seen: list = []
    monkeypatch.setattr(database, "AsyncSessionLocal", lambda: _Session(seen))
    monkeypatch.setattr(activity_log, "fire", lambda **k: None)

    async def allow(self):
        return set(COLS)

    monkeypatch.setattr(O, "_compliance_column_allowlist", allow)
    return seen


def _principal(buildings=None):
    return Principal(user_id=UUID(int=7), email="a@x", organization_id=UUID(HOME), role="admin",
                     can_ingest=True, building_ids=buildings)


def _read(*filters, principal=None, logic="and"):
    spec = {"filters": [{"field": f, "op": op, "value": v} for f, op, v in filters], "logic": logic}

    async def go():
        tp = caller_principal.set(principal or _principal())
        to = caller_organization_id.set(None)
        try:
            return await O.__new__(O)._fetch_compliance_table_direct(limit=50, spec=spec)
        finally:
            caller_principal.reset(tp)
            caller_organization_id.reset(to)

    return sorted(r["id"] for r in asyncio.run(go()))


class TestABuildingNamedInAFilter:
    @pytest.mark.parametrize("typed,ids", [
        ("Jasmeet Building", ["c1", "c2"]),        # the kind word beside the name
        ("JASMEET", ["c1", "c2"]),                 # exactly: never Jasmeet Heights as well
        ("the Jasmeet building", ["c1", "c2"]),
        ("tower hussain", ["c4"]),                 # another word order
        ("RT5 Crossrail", ["c7"]),                 # the stored comma
        ("B090765", ["c8"]),                       # a code typed without its hyphen
        ("Building 7", ["c9"]),                    # never Building 70
        ("Jasmet", ["c1", "c2"]),                  # one letter missing: the clearly closest
        ("tower", ["c4", "c5", "c6"]),             # several match: all of them, never one picked
    ])
    def test_its_certificates_are_found(self, seen, typed, ids):
        assert _read(("building_name", "eq", typed)) == ids

    def test_a_phrase_that_names_nothing_matches_nothing(self, seen):
        assert _read(("building_name", "eq", "the building")) == []
        assert not any("name lookup" in s for s, _ in seen), "nothing to look up"

    def test_a_name_that_is_not_on_the_register_matches_nothing(self, seen):
        assert _read(("building_name", "eq", "Kensington Palace")) == []

    def test_the_other_conditions_still_hold(self, seen):
        # Expired certificates at Jasmeet Building: JASMEET's one, not Jasmeet Heights' too, and
        # never every building's expired certificates after the building filter was dropped.
        assert _read(("building_name", "eq", "Jasmeet Building"), ("status", "eq", "Expired")) == ["c1"]

    def test_a_list_of_names_finds_each(self, seen):
        assert _read(("building_name", "in", ["Jasmeet Building", "tower hussain"])) == ["c1", "c2", "c4"]

    def test_excluding_a_building_excludes_the_building_it_names(self, seen):
        got = _read(("building_name", "neq", "Jasmeet Building"))
        assert "c1" not in got and "c2" not in got and "c3" in got

    def test_contains_keeps_the_text_match_and_adds_the_name_match(self, seen):
        _read(("building_name", "contains", "Jasmeet Building"))
        sql, params = seen[-1]
        assert "lower(c.building_name::text) = ANY(CAST(:p0 AS text[]))" in sql
        assert "jasmeet" in params["p0"]
        assert "c.building_name::text ILIKE :p0l" in sql and params["p0l"] == "%Jasmeet Building%"

    def test_the_site_name_is_looked_up_the_same_way(self, seen):
        _read(("site_name", "eq", "Jasmeet Building"))
        lookup = next(s for s, _ in seen if "name lookup site_name" in s)
        assert "LEFT JOIN plenum_cafm.sites" in lookup
        sql, params = seen[-1]
        assert "lower(COALESCE(s.site_name, c.building_name)::text) = ANY(CAST(:p0 AS text[]))" in sql
        assert "jasmeet" in params["p0"]


class TestAVendorNamedInAFilter:
    @pytest.mark.parametrize("typed", ["Acme", "Acme contractor", "acme facilities ltd", "Acme Facilites Ltd"])
    def test_its_accreditations_are_found(self, seen, typed):
        assert _read(("vendor_name", "eq", typed)) == ["v1"]

    def test_the_vendor_words_alone_match_nothing(self, seen):
        assert _read(("vendor_name", "eq", "the contractor")) == []


class TestTheBoundary:
    def test_the_lookup_reads_only_the_callers_company(self, seen):
        _read(("building_name", "eq", "Jasmeet Building"))
        sql, params = next((s, p) for s, p in seen if "name lookup" in s)
        assert "c.organization_id::text = :scope_org" in sql and params["scope_org"] == HOME
        assert "c.raw_metadata->>'a1_test_fixture'" in sql, "the rows the page hides stay hidden"

    def test_the_lookup_reads_only_the_buildings_a_restricted_caller_holds(self, seen):
        _read(("building_name", "eq", "Jasmeet Building"), principal=_principal(buildings=(UUID(int=1),)))
        sql, params = next((s, p) for s, p in seen if "name lookup" in s)
        assert ":scope_buildings" in sql and params["scope_buildings"] == [str(UUID(int=1))]


def test_every_value_reaches_the_database_as_a_bound_parameter(seen):
    # SQLAlchemy's text() reads ":name" as a bind; "::" straight after one, or a value pasted into
    # the SQL, would break the query or open it to the text typed.
    from sqlalchemy import text

    _read(("building_name", "eq", "Jasmeet Building"), ("vendor_name", "contains", "Acme's"))
    for sql, params in seen:
        assert set(text(sql)._bindparams) == set(params), sql
        assert "Jasmeet" not in sql and "Acme" not in sql


class TestCodesStayExact:
    def test_a_building_reference_is_compared_as_written(self, seen):
        _read(("building_reference", "eq", "B090765"))
        assert not any("name lookup" in s for s, _ in seen)
        sql, params = seen[-1]
        assert "lower(c.building_reference::text) = lower(:p0)" in sql and params["p0"] == "B090765"


def test_a_name_filter_compiled_without_a_lookup_is_compared_as_before():
    clause, params = O.__new__(O)._spec_where_clause(
        [{"field": "building_name", "op": "eq", "value": "JASMEET"}], "and")
    assert clause == "(lower(c.building_name::text) = lower(:p0))" and params == {"p0": "JASMEET"}
