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
