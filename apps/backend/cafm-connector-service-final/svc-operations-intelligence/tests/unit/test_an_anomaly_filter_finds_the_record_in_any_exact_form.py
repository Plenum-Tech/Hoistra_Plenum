"""An anomaly filter finds the building, asset or meter however its name is written (29 Oct 2026).

anomalies._resolve compared lower(btrim(name)) = lower(btrim(:typed)) and took LIMIT 1, so
"JASMEET BUILDING" was an unknown building (it is "JASMEET"), "ACSDL07" an unknown asset (it is
"ACS-DL-07"), and a name two buildings share resolved to whichever row Postgres yielded first.

The lookup reads buildings, assets and meters with NO company scope (a pre-existing gap, reported,
not widened here), so it takes only the exact forms — the name as typed, with the kind word out, as
letters and digits — never part of a name or a close spelling, which could land on another company's
record. A name several records share is never one of them picked: the anomaly list filters on all of
them (its company filter decides), and a single-record lookup says it is ambiguous.
"""
from __future__ import annotations

import re

import pytest

from src.engines.energy import anomalies as anom

BUILDINGS = [("b-jas", "JASMEET"), ("b-hts", "Jasmeet Heights"), ("b-7", "Building 7"),
             ("b-70", "Building 70"), ("b-hus", "Hussain Tower"), ("b-tw1", "Twin Court"),
             ("b-tw2", "Twin Court")]
ASSETS = [("a-1", "ACS-DL-07"), ("a-2", "ACS-DL-070")]


def _compact(v):
    return re.sub(r"[^a-z0-9]+", "", str(v or "").lower())


class _Register:
    """Runs the WHERE _resolve sends — by its parameters — over a list of (id, name)."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def execute(self, stmt, params=None):
        self.calls += 1
        p = params or {}
        texts, compacts = p.get("n_texts"), p.get("n_compacts")
        if texts is None:          # the old single-text form
            hits = [r for r in self.rows if r[1].strip().lower() == str(p.get("n", "")).strip().lower()][:1]
        else:
            hits = [r for r in self.rows if r[1].strip().lower() in texts or _compact(r[1]) in compacts]
        out = [{"k": k, "t0": name} for k, name in hits]

        class _R:
            def mappings(self_inner):
                return self_inner

            def all(self_inner):
                return out

            def scalar(self_inner):
                return out[0]["k"] if out else None

        return _R()


@pytest.mark.asyncio
@pytest.mark.parametrize("typed, expected", [
    ("JASMEET BUILDING", "b-jas"),
    ("jasmeet", "b-jas"),
    ("the JASMEET building", "b-jas"),
    ("Building 7", "b-7"),
    ("Building 70", "b-70"),
])
async def test_a_building_is_found_in_any_exact_form(typed, expected):
    assert await anom.resolve_building(_Register(BUILDINGS), typed) == expected


@pytest.mark.asyncio
async def test_an_asset_code_typed_without_its_hyphens_is_the_asset():
    assert await anom.resolve_asset(_Register(ASSETS), "ACSDL07") == "a-1"
    assert await anom.resolve_asset(_Register(ASSETS), "acs dl 070") == "a-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("typed", ["Jasmet", "tower hussain", "the building", "Building 5"])
async def test_no_part_name_or_close_spelling_on_an_unscoped_lookup(typed):
    with pytest.raises(anom.UnknownBuilding):
        await anom.resolve_building(_Register(BUILDINGS), typed)


@pytest.mark.asyncio
async def test_a_shared_name_is_ambiguous_for_one_record_and_all_of_them_for_the_list():
    with pytest.raises(anom.UnknownBuilding) as exc:
        await anom.resolve_building(_Register(BUILDINGS), "Twin Court")
    assert "more than one" in str(exc.value)
    assert sorted(await anom.resolve_building_ids(_Register(BUILDINGS), "Twin Court")) == ["b-tw1", "b-tw2"]


@pytest.mark.asyncio
async def test_the_list_filters_on_every_building_a_shared_name_names(monkeypatch):
    """list_anomalies' company filter decides which of them is the caller's."""
    seen = {}

    class _Session(_Register):
        async def execute(self, stmt, params=None):
            if params is None and hasattr(stmt, "compile"):
                seen["sql"] = str(stmt.compile(compile_kwargs={"literal_binds": True}))

                class _Empty:
                    def scalars(self_inner):
                        return self_inner

                    def all(self_inner):
                        return []
                return _Empty()
            return await super().execute(stmt, params)

    try:
        await anom.list_anomalies(_Session(BUILDINGS), status=None, building_id="Twin Court")
    except Exception:  # noqa: BLE001 - only the WHERE it built matters here
        pass
    assert "b-tw1" in seen.get("sql", "") and "b-tw2" in seen.get("sql", "")
