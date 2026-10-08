"""After an ingest, the chat says whether the meters reached a building.

A migration reports rows written, and rows written is not the question. Nearly every link in
the energy chain is a plain uuid with no constraint behind it, so readings can land perfectly
and reach no building: the run reports success, the page stays empty, and the two states look
identical from the front. The only way to tell them apart was to run a script against the
database, which is not an answer to give the person who just uploaded the file.

So the report is phrased for them. Not "7 checks passed" but which meter is on which building,
where it sits, and how much it carries — or, when something did not link, what that costs.
"""
from __future__ import annotations

import pytest

from src.engines.energy import meters as M

HARBOUR = "c343c566-8cc2-40d4-93d3-5cf1ec52d26f"


def meter(supply, *, fuel="electricity", building="Harbour Point", code="B-101",
          section=None, floor=None, sub=False, readings=17520, active=True):
    return {"supply": supply, "meter_type": fuel, "is_sub_meter": sub, "active": active,
            "building_code": code, "building_name": building, "section": section,
            "floor": floor, "readings": readings}


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def mappings(self):
        return self

    def all(self):
        return self._rows


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.params = None

    async def execute(self, _stmt, params=None):
        self.params = params
        return _Result(self.rows)


async def report(rows, building_ids=None):
    return await M.link_report(_Session(rows), building_ids=building_ids)


class TestWhenEverythingLinked:
    @pytest.mark.asyncio
    async def test_it_says_so_and_names_each_meter(self):
        out = await report([meter("NB-B-101-E0"), meter("NB-B-101-G1", fuel="gas")])
        assert out["linked"] == 2 and out["unlinked"] == 0
        assert "All 2 meters are linked" in out["summary"]
        assert "NB-B-101-E0" in out["summary"] and "NB-B-101-G1" in out["summary"]

    @pytest.mark.asyncio
    async def test_it_says_which_building_and_where(self):
        out = await report([meter("NB-B-101-E0", section="Tenant floors", sub=True)])
        assert "Harbour Point" in out["summary"]
        assert "Tenant floors" in out["summary"]

    @pytest.mark.asyncio
    async def test_a_main_meter_is_described_as_the_whole_building(self):
        out = await report([meter("NB-B-101-E0", sub=False)])
        assert "the whole building" in out["summary"]

    @pytest.mark.asyncio
    async def test_it_says_how_many_readings_each_carries(self):
        out = await report([meter("NB-B-101-E0", readings=17520)])
        assert "17,520 readings" in out["summary"]
        assert out["readings"] == 17520


class TestWhenSomethingDidNot:
    @pytest.mark.asyncio
    async def test_an_unlinked_meter_is_reported_with_what_it_costs(self):
        """"Not linked" means nothing to a reader. What it costs is the point."""
        out = await report([meter("NB-B-101-E0"),
                            meter("ORPHAN-1", building=None, code=None)])
        assert out["unlinked"] == 1
        assert "1 of 2 meters reached no building" in out["summary"]
        assert "counted towards nothing" in out["summary"]

    @pytest.mark.asyncio
    async def test_a_linked_meter_with_no_readings_is_called_out(self):
        """The meter landed and the readings did not. That is its own failure, and it looks
        exactly like success until someone opens the page."""
        out = await report([meter("NB-B-101-E0", readings=0)])
        assert "carry no readings yet" in out["summary"]

    @pytest.mark.asyncio
    async def test_no_meters_at_all_is_said_plainly(self):
        out = await report([])
        assert out["summary"] == "No meters on record, so nothing was linked."
        assert out["linked"] == 0 and out["readings"] == 0

    @pytest.mark.asyncio
    async def test_a_sub_meter_placed_nowhere_is_not_described_as_the_building(self):
        """A sub-meter with no section is not the incoming supply, and saying so would hide
        the very thing that makes a building's consumption count twice."""
        out = await report([meter("NB-B-101-E0", sub=True, section=None)])
        assert "the whole building" not in out["summary"]
        assert "nowhere" in out["summary"]


class TestScope:
    @pytest.mark.asyncio
    async def test_an_empty_scope_returns_nothing_rather_than_everything(self):
        """A caller allocated to no building must not be shown the portfolio."""
        out = await report([meter("NB-B-101-E0")], building_ids=[])
        assert out["meters"] == []
        assert "No buildings in scope" in out["summary"]

    @pytest.mark.asyncio
    async def test_a_scoped_call_filters_by_building(self):
        s = _Session([meter("NB-B-101-E0")])
        await M.link_report(s, building_ids=[HARBOUR])
        assert s.params and s.params["bids"] == [HARBOUR]

    @pytest.mark.asyncio
    async def test_no_scope_means_the_whole_portfolio(self):
        s = _Session([meter("NB-B-101-E0")])
        await M.link_report(s, building_ids=None)
        assert not s.params
