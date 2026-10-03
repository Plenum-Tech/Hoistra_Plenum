"""Identical values in one file do not make two destination columns one column.

merge_duplicate_columns collapses columns whose values agree row for row. Right for a genuine
duplicate — "Asset Code" beside "asset_code" — where two source names are one field. Wrong when
both names are real, distinct columns on the destination, because then the agreement is a
coincidence of this file and says nothing about what the columns mean.

On 23 Sep 2026 a certificates sheet held the same date in expiry_date and next_due_date for all
16 rows. The merge dropped expiry_date. That is the one field everything downstream keys on: the
EPC tile read it, got null, and reported the only EPC on file as expired; the lifecycle ladder
had no anchor and stamped every certificate "Not on record". Nothing was wrong with the file.
"""
from __future__ import annotations

import logging
import sys
import types

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import column_merge as cm  # noqa: E402

# Enough rows to clear redundant_column_groups' minimum — a primitive that called two columns
# redundant on two rows would be reckless, and it does not. Dates and codes vary per row so the
# only things that agree row for row are the pairs meant to.
ROWS = [
    {"certificate_number": f"CERT-{i}", "issue_date": f"2026-01-{i + 1:02d}",
     "expiry_date": f"2031-01-{i + 1:02d}", "next_due_date": f"2031-01-{i + 1:02d}",
     "Asset Code": f"A{i}", "asset_code": f"A{i}"}
    for i in range(30)
]


class TestTheSchemaIsKnown:
    def test_the_destination_columns_were_loaded(self):
        assert "expiry_date" in cm.KNOWN_DESTINATION_COLUMNS
        assert "next_due_date" in cm.KNOWN_DESTINATION_COLUMNS
        assert "asset_code" in cm.KNOWN_DESTINATION_COLUMNS

    def test_a_source_spelling_is_not_a_destination_column(self):
        assert "asset code" not in cm.KNOWN_DESTINATION_COLUMNS


class TestTwoFactsSurviveAgreeing:
    def test_expiry_and_next_due_both_survive(self):
        merged, report = cm.merge_duplicate_columns({"compliance_certificates": ROWS})
        cols = set(merged["compliance_certificates"][0])
        assert "expiry_date" in cols, "the field every downstream reader keys on"
        assert "next_due_date" in cols

    def test_the_decline_is_reported_not_silent(self):
        _merged, report = cm.merge_duplicate_columns({"compliance_certificates": ROWS})
        entries = report["tables"].get("compliance_certificates", [])
        declined = [e for e in entries if e.get("declined")]
        assert declined, "a group the merge left alone must say so"
        assert set(declined[0]["members"]) >= {"expiry_date", "next_due_date"}
        assert declined[0]["dropped"] == []

    def test_a_genuine_duplicate_still_merges(self):
        """The merge is not disabled. 'Asset Code' beside 'asset_code' is one field."""
        merged, report = cm.merge_duplicate_columns({"compliance_certificates": ROWS})
        cols = set(merged["compliance_certificates"][0])
        assert not ({"Asset Code", "asset_code"} <= cols), "one of the two spellings goes"
        assert "asset_code" in cols or "Asset Code" in cols

    def test_the_survivor_of_a_genuine_duplicate_is_the_destination_name(self):
        merged, _ = cm.merge_duplicate_columns({"compliance_certificates": ROWS})
        assert "asset_code" in set(merged["compliance_certificates"][0])

    def test_dropped_count_excludes_declined_groups(self):
        _m, report = cm.merge_duplicate_columns({"compliance_certificates": ROWS})
        assert report["total_columns_dropped"] == 1, "only the spelling duplicate was dropped"


class TestNothingElseChanged:
    def test_a_table_with_no_duplicates_passes_through_by_reference(self):
        rows = [{"a": 1, "b": 2}]
        merged, report = cm.merge_duplicate_columns({"t": rows})
        assert merged["t"] is rows
        assert report["total_merges"] == 0
