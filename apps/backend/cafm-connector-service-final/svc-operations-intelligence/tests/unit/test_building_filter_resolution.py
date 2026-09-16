"""A building filter that matches nothing must not read as a clean building.

Live, on the deployed app: "in Building 5 which are assets giving anomalies power consumption?"
answered "No energy anomalies were found for Building 5 across any status, so there are no
metered assets or circuits currently flagged." Building 5 has TEN anomalies, nine of them open,
the largest an asset_spike worth GBP 42,745.

The cause was the building_id filter I added. It compares as text, and an agent reading a
person's sentence passes what the person said — "Building 5", not a uuid. No row matched, the
list came back empty, and empty is indistinguishable from clean.
"""
import pytest

from src.engines.energy.anomalies import UnknownBuilding, resolve_building


class TestAnUnknownBuildingIsAnError:

    def test_it_is_a_value_error(self):
        """So a caller that does not know about this class still fails rather than proceeding
        with a filter that silently matches nothing."""
        assert issubclass(UnknownBuilding, ValueError)

    def test_the_message_names_what_was_not_found(self):
        exc = UnknownBuilding("No building matches 'Nowhere House'.")
        assert "Nowhere House" in str(exc)


class TestResolutionRules:
    """resolve_building is async and hits the database for names; these pin the parts that do
    not — the uuid path and the empty path — so the contract holds without a live connection."""

    @pytest.mark.asyncio
    async def test_none_stays_none(self):
        assert await resolve_building(None, None) is None

    @pytest.mark.asyncio
    async def test_empty_stays_none(self):
        assert await resolve_building(None, "") is None

    @pytest.mark.asyncio
    async def test_a_uuid_needs_no_lookup(self):
        """Passing None as the session proves it: a uuid must not touch the database."""
        out = await resolve_building(None, "a40ef675-9584-4db5-a3c8-5e71c6812e0c")
        assert out == "a40ef675-9584-4db5-a3c8-5e71c6812e0c"

    @pytest.mark.asyncio
    async def test_a_uuid_is_normalised(self):
        upper = "A40EF675-9584-4DB5-A3C8-5E71C6812E0C"
        assert await resolve_building(None, upper) == upper.lower()


class TestAssetsAndMetersHaveTheSameProblem:
    """Fixing the building filter alone left two more of the same bug. People say the asset
    CODE — "ACS-DL-07", "FCU-301" — and the MPAN printed on the meter, not uuids. ACS-DL-07
    carries five anomalies and `asset_id::text = 'ACS-DL-07'` matched none of them."""

    def test_each_identifier_has_its_own_error(self):
        from src.engines.energy.anomalies import UnknownAsset, UnknownBuilding, UnknownMeter
        for cls in (UnknownBuilding, UnknownAsset, UnknownMeter):
            assert issubclass(cls, ValueError)
        # Distinct, so a caller can tell which of three filters was the unresolvable one.
        assert len({UnknownBuilding, UnknownAsset, UnknownMeter}) == 3

    @pytest.mark.asyncio
    async def test_a_uuid_asset_needs_no_lookup(self):
        from src.engines.energy.anomalies import resolve_asset
        uid = "158335d4-1111-2222-3333-444444444444"
        assert await resolve_asset(None, uid) == uid

    @pytest.mark.asyncio
    async def test_a_uuid_meter_needs_no_lookup(self):
        from src.engines.energy.anomalies import resolve_meter
        uid = "47191ed4-1111-2222-3333-444444444444"
        assert await resolve_meter(None, uid) == uid

    @pytest.mark.asyncio
    async def test_none_is_none_for_all_three(self):
        from src.engines.energy.anomalies import (resolve_asset, resolve_building,
                                                  resolve_meter)
        for fn in (resolve_building, resolve_asset, resolve_meter):
            assert await fn(None, None) is None
            assert await fn(None, "") is None


class TestTheRouteReportsAllThree:

    def test_every_unresolvable_identifier_is_caught(self):
        """One except clause, three errors. Catching only UnknownBuilding would turn an
        unresolvable asset code into a 500 instead of a 404 that names it."""
        from pathlib import Path
        route = (Path(__file__).resolve().parents[2]
                 / "src" / "api" / "routes" / "energy.py").read_text(encoding="utf-8")
        assert "anom_svc.UnknownBuilding" in route
        assert "anom_svc.UnknownAsset" in route
        assert "anom_svc.UnknownMeter" in route
