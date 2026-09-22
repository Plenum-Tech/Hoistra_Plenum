"""The Hoist Score is ingestion coverage, not data entry.

A building hoisted through the form has a name, a country, a floor count and an area, and
the platform knows nothing about it: no assets, no certificates, no contract, no meter, no
maintenance. That building scores nothing, and the score rises as each of those arrives.

The failure this replaces went the other way twice. create_building wrote a hard 0 that
nothing ever updated, so every hoisted building reported zero forever; the first fix then
derived the score from record completeness, which scored a building for having had its form
filled in. Neither is what the score means.
"""
from __future__ import annotations

from src.engines.energy.buildings import (
    HOIST_SCORE_DOMAINS,
    apply_graph_rollup,
    hoist_score_for,
)


class TestWhatItCounts:
    def test_a_building_nobody_has_ingested_anything_about_scores_nothing(self):
        out = hoist_score_for({})
        assert out["score"] == 0
        assert out["missing"] == ["assets", "compliance", "contracts", "energy", "maintenance"]

    def test_the_form_fields_do_not_earn_a_point(self):
        """floors, spaces, documents and equipment are all counted by the graph and none of
        them is one of the five. A building can carry plenty of those and still score 0."""
        out = hoist_score_for({"floors": 12, "spaces": 40, "documents": 3, "equipment": 9})
        assert out["score"] == 0

    def test_each_domain_is_an_equal_share_and_the_score_rises_with_each(self):
        steps = [
            ({"assets": 23}, 20),
            ({"assets": 23, "work_orders": 16}, 40),
            ({"assets": 23, "work_orders": 16, "meters": 2}, 60),
            ({"assets": 23, "work_orders": 16, "meters": 2, "certificates": 13}, 80),
            ({"assets": 23, "work_orders": 16, "meters": 2, "certificates": 13, "contracts": 2}, 100),
        ]
        for counts, expected in steps:
            assert hoist_score_for(counts)["score"] == expected, counts

    def test_one_full_branch_does_not_carry_the_rest(self):
        """A thousand assets is one domain, not a covered building."""
        assert hoist_score_for({"assets": 1000})["score"] == 20

    def test_energy_is_met_by_a_reading_as_well_as_by_a_meter(self):
        """A building whose EUI is on record has its energy covered however the reading
        arrived — refusing it because no meter row exists would score the measurement out."""
        assert hoist_score_for({}, has_eui=True)["score"] == 20
        assert "energy" in hoist_score_for({}, has_eui=True)["covered"]

    def test_the_missing_domains_are_named(self):
        """A number nobody can act on is half an answer: the row says what would raise it."""
        out = hoist_score_for({"assets": 1, "certificates": 2})
        assert out["covered"] == ["assets", "compliance"]
        assert out["missing"] == ["contracts", "energy", "maintenance"]
        assert out["of"] == [d for d, _ in HOIST_SCORE_DOMAINS]


class TestOnTheRow:
    def test_the_rollup_puts_the_derived_score_on_the_building(self):
        row = {"hoist_score": 0, "hoist_score_source": "graph_coverage"}
        out = apply_graph_rollup(row, {"counts": {"assets": 4, "certificates": 1}})
        assert out["hoist_score"] == 40
        assert out["hoist_score_source"] == "graph_coverage"
        assert out["hoist_score_missing"] == ["contracts", "energy", "maintenance"]

    def test_a_recorded_score_is_someone_s_judgement_and_outranks_the_derivation(self):
        row = {"hoist_score": 88, "hoist_score_source": "recorded"}
        out = apply_graph_rollup(row, {"counts": {"assets": 4}})
        assert out["hoist_score"] == 88
        assert out["hoist_score_source"] == "recorded"

    def test_a_building_with_no_rows_at_all_keeps_its_zero(self):
        row = {"hoist_score": 0, "hoist_score_source": "graph_coverage"}
        assert apply_graph_rollup(row, None)["hoist_score"] == 0
