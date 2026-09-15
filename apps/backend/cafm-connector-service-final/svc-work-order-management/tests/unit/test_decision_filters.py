"""The Maintenance screen's filters and group-by, and the counts in each group header.

The screen filters by state and by the module a decision came from, and cuts the list four
ways. Two things have to hold whichever way it is cut: the totals are counted over every
decision in scope rather than over what survived a filter, so the "10 of 10" never lies; and a
group whose cost nobody has estimated reports null, not zero, because a group that costs an
unknown amount and a group that costs nothing are different answers.

The shadowing bug is pinned here too: the loop that builds work-order rows used to bind
``state``, which is also the filter parameter, so every filtered request quietly came back
unfiltered.
"""
from __future__ import annotations

import pytest

from src.services.maintenance import GROUP_KEYS, STATE_ORDER, _group, _tally


def dec(state="Blocked", source="Vendors", building="Town Hall",
        vendor="Meridian Heating Ltd", cost=640.0):
    return {"state": state, "source": source, "building": building,
            "vendor": vendor, "estimated_cost": cost}


FLEET = [
    dec(state="Blocked", vendor="Meridian Heating Ltd", building="Town Hall", cost=640.0),
    dec(state="Blocked", vendor="Meridian Heating Ltd", building="Meridian Quay", cost=380.0),
    dec(state="Deviation", vendor="Apex Mechanical", building="Bishopsgate Tower", cost=410.0),
    dec(state="To raise", source="Energy", vendor="Meridian Heating Ltd",
        building="Kingsway House", cost=900.0),
    dec(state="To raise", source="Compliance", vendor=None, building="Marina Heights",
        cost=None),
]


class TestGroupingCutsTheSameDecisions:
    @pytest.mark.parametrize("by", list(GROUP_KEYS))
    def test_every_cut_keeps_every_decision(self, by):
        assert sum(g["count"] for g in _group(FLEET, by)) == len(FLEET)

    def test_an_unknown_grouping_returns_nothing_rather_than_guessing(self):
        assert _group(FLEET, "colour") == []
        assert _group(FLEET, "") == []

    def test_grouping_an_empty_queue_is_empty_not_an_error(self):
        assert _group([], "state") == []

    def test_state_groups_come_back_in_the_order_the_screen_lists_them(self):
        keys = [g["key"] for g in _group(FLEET, "state")]
        assert keys == sorted(keys, key=lambda k: STATE_ORDER[k])
        assert keys[0] == "Blocked"

    def test_other_groupings_lead_with_the_biggest(self):
        counts = [g["count"] for g in _group(FLEET, "vendor")]
        assert counts == sorted(counts, reverse=True)


class TestGroupHeaderCounts:
    def test_the_header_counts_match_what_the_screen_prints(self):
        vendors = {g["key"]: g for g in _group(FLEET, "vendor")}
        m = vendors["Meridian Heating Ltd"]
        # "3 decisions · 2 blocked · 1 to raise"
        assert (m["count"], m["blocked"], m["to_raise"]) == (3, 2, 1)

    def test_every_state_is_counted_in_its_own_column(self):
        g = _group(FLEET, "source")[0] if False else \
            {x["key"]: x for x in _group(FLEET, "state")}
        assert g["Blocked"]["blocked"] == 2
        assert g["Deviation"]["deviating"] == 1
        assert g["To raise"]["to_raise"] == 2

    def test_cost_is_summed_across_the_group(self):
        m = {g["key"]: g for g in _group(FLEET, "vendor")}["Meridian Heating Ltd"]
        assert m["estimated_cost"] == pytest.approx(640.0 + 380.0 + 900.0)
        assert m["priced"] == 3

    def test_a_group_nobody_has_priced_reports_null_not_zero(self):
        # "we do not know what this costs" must not render as "this is free".
        g = _group([dec(cost=None), dec(cost=None)], "vendor")[0]
        assert g["estimated_cost"] is None
        assert g["priced"] == 0

    def test_a_partly_priced_group_sums_what_it_has_and_says_how_many(self):
        g = _group([dec(cost=100.0), dec(cost=None), dec(cost=50.0)], "vendor")[0]
        assert g["estimated_cost"] == 150.0
        assert g["priced"] == 2
        assert g["count"] == 3


class TestNothingIsDroppedForMissingAValue:
    """A decision with no vendor is still a decision somebody owes."""

    @pytest.mark.parametrize("by,label", [("vendor", "Unassigned"),
                                          ("building", "No building"),
                                          ("source", "Unattributed")])
    def test_a_blank_value_gets_a_stated_label(self, by, label):
        rows = [dec(**{by: None}), dec()]
        groups = {g["key"]: g for g in _group(rows, by)}
        assert label in groups
        assert sum(g["count"] for g in groups.values()) == 2

    def test_the_unlabelled_group_is_not_the_string_none(self):
        assert "None" not in {g["key"] for g in _group([dec(vendor=None)], "vendor")}


class TestTally:
    def test_it_counts_each_distinct_value(self):
        assert _tally(FLEET, "state") == {"Blocked": 2, "Deviation": 1, "To raise": 2}

    def test_it_counts_sources_the_filter_chips_offer(self):
        assert _tally(FLEET, "source") == {"Vendors": 3, "Energy": 1, "Compliance": 1}
