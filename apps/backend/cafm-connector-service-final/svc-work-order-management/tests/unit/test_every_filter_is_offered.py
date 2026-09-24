"""The Maintenance filter row is the same shape whatever the data holds.

`available` listed only the states that had rows and the modules that had raised something,
so the row changed shape with the portfolio: eight chips against a populated database, four
against one mid-ingest. The same screen in two environments then read as a missing feature
rather than an empty category.

A chip at 0 is a fact — "nothing is deviating" is worth knowing, and it is not the same
statement as "deviation is not a thing on this platform". The counts in by_state/by_source
still cover only what exists; the client reads a category this list names but the counts do
not as 0, which is safe precisely because this list is exhaustive.
"""
from __future__ import annotations

from src.services.maintenance import SOURCE_LABELS, SOURCE_OF, STATE_ORDER


class TestTheVocabularyIsComplete:
    def test_every_module_that_can_raise_one_is_a_label(self):
        # Every value SOURCE_OF maps to, plus the fallback it applies to an unmapped kind.
        assert set(SOURCE_LABELS) == set(SOURCE_OF.values()) | {"Maintenance"}

    def test_the_fallback_module_is_included(self):
        # shape() uses SOURCE_OF.get(kind, "Maintenance"), so rows can carry it while no
        # entry in SOURCE_OF names it. Leaving it out would drop a chip for rows that exist.
        assert "Maintenance" in SOURCE_LABELS

    def test_the_labels_are_stable_and_sorted(self):
        assert list(SOURCE_LABELS) == sorted(SOURCE_LABELS)
        assert len(set(SOURCE_LABELS)) == len(SOURCE_LABELS)

    def test_the_four_states_are_the_ones_the_screen_groups_by(self):
        assert set(STATE_ORDER) == {"Blocked", "Deviation", "Awaiting approval", "To raise"}


class TestWhatAvailableWouldOffer:
    """The shape the service builds, without a database behind it."""

    def _available(self, by_source: dict) -> dict:
        # Mirrors the expression in maintenance.py's return.
        return {
            "state": list(STATE_ORDER),
            "source": sorted(set(SOURCE_LABELS) | set(by_source)),
        }

    def test_an_empty_portfolio_still_offers_every_filter(self):
        a = self._available({})
        assert a["state"] == list(STATE_ORDER), "a state with no rows must still be offered"
        assert a["source"] == list(SOURCE_LABELS)

    def test_a_partial_portfolio_offers_the_same_row(self):
        # Two states and one module present — the row is identical to the empty case.
        assert self._available({"Maintenance": 4}) == self._available({})

    def test_a_module_the_map_has_not_been_taught_is_still_offered(self):
        # SOURCE_OF.get() falls back to "Maintenance", but a future writer could put its own
        # label on a row. Dropping it would hide those rows from the filter row entirely.
        a = self._available({"Procurement": 3})
        assert "Procurement" in a["source"]
        assert set(SOURCE_LABELS).issubset(set(a["source"]))
