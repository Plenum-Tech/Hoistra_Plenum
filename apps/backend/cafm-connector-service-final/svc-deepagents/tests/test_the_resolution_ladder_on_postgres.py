"""The resolution ladder against a real Postgres (29 Oct 2026) — the regexes are Postgres's, not Python's.

Runs only with NAME_MATCH_PG_DSN set to a THROWAWAY database (it creates and drops a schema named
plenum_scoped in it). Never the stack database:

    docker network create hoist-nm
    docker run -d --name hoist-nm-pg --network hoist-nm -e POSTGRES_PASSWORD=nm --tmpfs /var/lib/postgresql/data postgres:16-alpine
    NAME_MATCH_PG_DSN=postgresql+asyncpg://postgres:nm@hoist-nm-pg:5432/postgres pytest tests/test_the_resolution_ladder_on_postgres.py
"""
from __future__ import annotations

import asyncio
import os
from urllib.parse import urlparse

import pytest

from src.agents import ontology_qa as oq

DSN = os.environ.get("NAME_MATCH_PG_DSN")
pytestmark = pytest.mark.skipif(not DSN, reason="NAME_MATCH_PG_DSN not set (a throwaway Postgres only)")

BUILDINGS = [
    ("b-jas", "JASMEET", "B-090765", None),
    ("b-hus", "hussain tower", "B-019087", None),
    ("b-bis", "Bishopsgate Tower", "B-301", "Bishopsgate"),
    ("b-rt5", "RT5, Crossrail", "B-302", None),
    ("b-mth", "Manchester Town Hall", "B-303", None),
    ("b-sam", "sams tower", "B-01", None),
    ("b-b7", "Building 7", "B-07", None),
    ("b-zur", "Zürich Haus", "B-08", None),
    ("b-dep", "Depot 3010", "B-3010", None),
    ("b-b70", "Building 70", "B-070", None),
    ("b-jah", "Jasmeet Heights", "B-090766", None),
]


class _C:
    def __init__(self, name, table, key, label, identifiers, search, synonyms=()):
        self.name, self.table, self.key, self.label = name, table, key, label
        self.identifiers, self.search, self.synonyms = list(identifiers), list(search), list(synonyms)
        self.states, self.attributes = {}, {}


class _Onto:
    schema = "plenum_scoped"

    def __init__(self):
        self.concepts = {
            "Building": _C("Building", "buildings", "building_id", "name", ["building_code"],
                           ["name", "building_code", "site_name"]),
            "Technician": _C("Technician", "technicians", "id", "full_name", [], ["full_name"],
                             ["engineer", "operative", "technician", "technicians"]),
            "Asset": _C("Asset", "assets", "id", "asset_name", ["asset_code"], ["asset_name", "asset_code"]),
        }


class _Reader:
    def __init__(self, conn):
        self.conn, self.queries = conn, []

    async def try_fetch(self, sql, p, label=""):
        from sqlalchemy import text

        self.queries.append(label)
        res = await self.conn.execute(text(sql), p.d)
        return [dict(r) for r in res.mappings().all()]


async def _with_db(fn):
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool
    from sqlalchemy import text

    assert urlparse(DSN.replace("+asyncpg", "")).hostname == "hoist-nm-pg", "a throwaway Postgres only"
    eng = create_async_engine(DSN, poolclass=NullPool)
    try:
        async with eng.begin() as conn:
            await conn.execute(text("DROP SCHEMA IF EXISTS plenum_scoped CASCADE"))
            await conn.execute(text("CREATE SCHEMA plenum_scoped"))
            await conn.execute(text("CREATE TABLE plenum_scoped.buildings (building_id text, name text, "
                                    "building_code text, site_name text)"))
            await conn.execute(text("CREATE TABLE plenum_scoped.technicians (id text, full_name text)"))
            await conn.execute(text("CREATE TABLE plenum_scoped.assets (id text, asset_name text, asset_code text)"))
            for row in BUILDINGS:
                await conn.execute(text("INSERT INTO plenum_scoped.buildings VALUES (:a, :b, :c, :d)"),
                                   dict(zip("abcd", row)))
            await conn.execute(text("INSERT INTO plenum_scoped.technicians VALUES ('t-1', 'Priya Sharma'), "
                                    "('t-2', 'Jasmeet Kaur')"))
            await conn.execute(text("INSERT INTO plenum_scoped.assets VALUES ('a-1', 'Lift Asset-4471', 'PT-B-301-A-LIFT-01')"))
        async with eng.connect() as conn:
            return await fn(conn)
    finally:
        await eng.dispose()


def _resolve(concept, text_):
    async def go(conn):
        r = _Reader(conn)
        name, keys, labels, note = await oq.resolve_match(r, _Onto(), concept, text_)
        return name, set(labels), note, r.queries
    return asyncio.run(_with_db(go))


@pytest.mark.parametrize("typed,expected", [
    ("JASMEET BUILDING", {"JASMEET"}),
    ("the Jasmeet building", {"JASMEET"}),
    ("building JASMEET", {"JASMEET"}),
    ("Jasmeet's", {"JASMEET"}),
    ("jasmeet", {"JASMEET"}),
    ("B-090765", {"JASMEET"}),
    ("B090765", {"JASMEET"}),
    ("b 090765", {"JASMEET"}),
    ("Hussain Tower", {"hussain tower"}),
    ("tower hussain", {"hussain tower"}),
    ("hussain tower building", {"hussain tower"}),
    ("Bishopsgate", {"Bishopsgate Tower"}),
    ("RT5 Crossrail", {"RT5, Crossrail"}),
    ("rt5 crossrail building", {"RT5, Crossrail"}),
    ("town hall", {"Manchester Town Hall"}),
    ("Jasmet", {"JASMEET"}),
    ("Hussian tower", {"hussain tower"}),
    ("Bishopgate Tower", {"Bishopsgate Tower"}),
    ("Building 7", {"Building 7"}),
    ("Zurich Haus", {"Zürich Haus"}),
    ("B-301", {"Bishopsgate Tower"}),
    ("tower", {"hussain tower", "Bishopsgate Tower", "sams tower"}),   # ambiguous: every one, never a pick
    ("Jasmeet Heights", {"Jasmeet Heights"}),
    ("building 70", {"Building 70"}),
])
def test_a_building_is_found_however_it_is_named(typed, expected):
    name, labels, note, _ = _resolve("Building", typed)
    assert name == "Building" and labels == expected, (typed, labels, note)


@pytest.mark.parametrize("typed", ["the building", "B-305", "Kensington Palace", "Tower A", "buildings", "B-1"])
def test_nothing_is_found_for_a_name_that_is_not_there(typed):
    name, labels, note, _ = _resolve("Building", typed)
    assert labels == set(), (typed, labels)
    assert "not found" in (note or "")


def test_a_name_planned_as_the_wrong_kind_is_found_as_what_it_is():
    name, labels, note, _ = _resolve("Technician", "Priya Building")
    assert (name, labels) == ("Technician", {"Priya Sharma"})
    name, labels, note, _ = _resolve("Asset", "JASMEET")
    assert (name, labels) == ("Building", {"JASMEET"}) and "matched Building instead" in note


def test_an_exact_name_beats_names_that_merely_start_with_it():
    # "Building 7" is not "Building 70"; "Jasmeet" is not "Jasmeet Heights" - when the exact one exists.
    for typed, expected in [("Building 7", {"Building 7"}), ("Jasmeet", {"JASMEET"}),
                            ("JASMEET BUILDING", {"JASMEET"}), ("the jasmeet building", {"JASMEET"})]:
        _, labels, _, _ = _resolve("Building", typed)
        assert labels == expected, (typed, labels)


def test_a_partial_match_says_when_another_kind_holds_the_exact_name():
    # The planner's kind is kept (it read the question), but the answer is told of the exact
    # namesake: technician "Jasmeet Kaur" matched, building "JASMEET" is named exactly that.
    name, labels, note, _ = _resolve("Technician", "Jasmeet")
    assert (name, labels) == ("Technician", {"Jasmeet Kaur"})
    assert "Building 'JASMEET'" in (note or "")


def test_a_misspelt_name_says_so():
    _, labels, note, _ = _resolve("Building", "Jasmet")
    assert labels == {"JASMEET"} and "closest name is 'JASMEET'" in note


def test_an_asset_named_with_its_number_spaced_out():
    name, labels, _, _ = _resolve("Asset", "lift asset 4471")
    assert (name, labels) == ("Asset", {"Lift Asset-4471"})


def test_an_exact_name_stops_the_ladder_at_the_first_rung_that_finds_it():
    _, labels, note, queries = _resolve("Building", "B-090765")
    assert labels == {"JASMEET"} and note is None and queries == ["resolve Building (exact)"]
