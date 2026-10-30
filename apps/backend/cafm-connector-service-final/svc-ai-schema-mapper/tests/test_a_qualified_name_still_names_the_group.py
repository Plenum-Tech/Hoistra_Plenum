"""A relationship prefix must not cost a group its name.

Reported 30 Oct: alarm_id and schedule_id were marked shared attributes and no lookup table by
that name appeared. The tables were built — as `id_code_attr_G25` and `id_code_attr_G23`. The
person was looking for `alarm_id` and `schedule_id`, so as far as they could tell nothing was
created at all.

The canonical name is decided by consensus: a name carried by at least two members wins. These
groups have two members and two spellings of one idea —

    work_orders.linked_alarm_id   +  bms_alarms.alarm_id
    work_orders.ppm_schedule_id   +  ppm_schedules.schedule_id
    work_orders.linked_meter_event_id + meter_events.event_id

— so nothing is carried twice, and the group falls through to the neutral `{format}_attr_{gid}`.
That fallback is right for a genuinely mixed group: naming (site_name, postcode, description)
after any one member would mislead. But these are not mixed. One member's name is the other's
with a relationship qualifier in front of it, which is how a foreign key is normally spelled.

So before giving up, strip the qualifier: when every member's name ends in the same base name on
an underscore boundary, that base is the group's name. A group with no such base keeps the
neutral name it has now.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.column_intelligence import canonical_name_for_group  # noqa: E402


def _name(members, tables=None, gid="G25"):
    return canonical_name_for_group(
        [tuple(m.split(".", 1)) for m in members], {}, tables=tables, group_id=gid)


def _tbl(members):
    """Minimal tables so the Tier-5 fallback can read a format if it is reached."""
    out: dict[str, dict] = {}
    for m in members:
        t, c = m.split(".", 1)
        out.setdefault(t, {"rows": [], "columns": []})
        out[t]["columns"].append(c)
        out[t]["rows"] = [{c: "ALM-00001"}, {c: "ALM-00002"}]
    return out


class TestTheReportedGroups:
    @pytest.mark.parametrize("members,expected", [
        (["work_orders.linked_alarm_id", "bms_alarms.alarm_id"], "alarm_id"),
        (["ppm_schedules.schedule_id", "work_orders.ppm_schedule_id"], "schedule_id"),
        (["meter_events.event_id", "work_orders.linked_meter_event_id"], "event_id"),
    ])
    def test_the_group_is_named_after_the_column_it_points_at(self, members, expected):
        assert _name(members, _tbl(members)) == expected

    def test_the_order_of_the_members_does_not_decide_it(self):
        a = ["work_orders.linked_alarm_id", "bms_alarms.alarm_id"]
        assert _name(a, _tbl(a)) == _name(list(reversed(a)), _tbl(a)) == "alarm_id"


class TestItOnlyFiresOnARealSharedBase:
    def test_a_mixed_group_keeps_the_neutral_name(self):
        # The case Tier 5 exists for: naming this after any one member would mislead.
        m = ["sites.site_name", "sites.postcode", "faults.fault_description"]
        assert _name(m, _tbl(m), gid="G7").endswith("_attr_G7")

    def test_two_different_ideas_sharing_a_format_are_not_merged(self):
        m = ["vendors.trading_name", "vendor_certificates.vendor_name"]
        assert _name(m, _tbl(m), gid="G17").endswith("_attr_G17")

    def test_a_partial_match_is_not_enough(self):
        """Every member must end in the base; two out of three is a mixed group."""
        m = ["work_orders.linked_alarm_id", "bms_alarms.alarm_id", "contacts.phone"]
        assert _name(m, _tbl(m), gid="G9").endswith("_attr_G9")

    def test_the_boundary_is_a_whole_word(self):
        # 'valid' must not be read as carrying the base 'id'.
        m = ["a.is_valid", "b.valid"]
        assert _name(m, _tbl(m), gid="G3") != "id"

    def test_a_bare_generic_base_is_not_a_name(self):
        # Stripping down to 'id' names nothing; these stay neutral.
        m = ["work_orders.asset_id", "vendors.vendor_id"]
        assert _name(m, _tbl(m), gid="G4").endswith("_attr_G4")


class TestTheExistingTiersAreUntouched:
    def test_a_plain_consensus_still_wins(self):
        m = ["assets.asset_id", "work_orders.asset_id", "bms_alarms.asset_id"]
        assert _name(m, _tbl(m)) == "asset_id"

    def test_a_single_column_keeps_its_own_name(self):
        m = ["work_orders.linked_alarm_id"]
        assert _name(m, _tbl(m)) == "linked_alarm_id"
