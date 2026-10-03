"""Three faults from one migration run on 23 Sep 2026, in the order they bit.

A Harbour Point workbook was ingested into an emptied hoistra_test. Node 9 reported progress for
eight minutes, wrote nothing, and froze every page in the product while it did it.

1. THE DUPLICATE BUILDING. _NATURAL_KEYS had no entry for `buildings`, so the Buildings sheet
   inserted a SECOND row for a building already on file — ON CONFLICT DO NOTHING cannot catch
   that, because `id` is a fresh uuid4() and never collides. The duplicate was not the damage.
   Every building lookup afterwards found two rows for "B-101", and BuildingResolver refuses an
   ambiguous match, so from that point nothing could be placed on that building: no building
   meant no meter could be created, and meter_readings.meter_id is NOT NULL, so all 35,040
   readings were rejected. The energy half of the ingest was lost to one missing key.

2. THE LOCK HELD ACROSS THE LOAD. The same transaction ran ALTER TABLE ADD COLUMN on `buildings`
   and then loaded every row of every table. ADD COLUMN takes ACCESS EXCLUSIVE and holds it to
   the end of the transaction, and every page in the product reads `buildings` — so all fifteen
   connections queued behind it until the pool gave up with "QueuePool limit of size 5 overflow
   10 reached". The lock itself is brief; holding it across the load is what did the damage.

3. FORTY MINUTES TO LEARN WHAT ROW ONE KNEW. With meter_id null, the batch insert failed, and
   the per-row fallback then inserted, rejected and rolled back each of 35,040 rows at about
   fifteen a second. The fallback exists so ONE bad row costs its own row; it is not a way to
   discover that every row is bad.
"""
from __future__ import annotations

import logging
import re
import sys
import types

import pytest

if "cafm_shared" not in sys.modules:
    _shared = types.ModuleType("cafm_shared")
    _logging = types.ModuleType("cafm_shared.logging")
    _logging.get_logger = lambda name=None: logging.getLogger(name or "test")
    _shared.logging = _logging
    sys.modules["cafm_shared"] = _shared
    sys.modules["cafm_shared.logging"] = _logging

from src.graph.nodes import write_node as wn  # noqa: E402

SRC = open(wn.__file__, encoding="utf-8").read()


class TestABuildingIsIdentifiedByItsCode:
    def test_buildings_has_a_natural_key(self):
        assert "buildings" in wn._NATURAL_KEYS, \
            "without this a Buildings sheet duplicates the building on every run"

    def test_that_key_is_the_building_code(self):
        assert ("building_code",) in wn._NATURAL_KEYS["buildings"]

    def test_the_key_is_used_when_the_row_carries_it(self):
        keys = wn._natural_keys_for(
            "buildings", {"building_code": "B-101", "name": "Harbour Point"},
            {"building_code", "name"})
        assert [("building_code",), ["B-101"]] in [[k, v] for k, v in keys]

    def test_a_row_without_a_code_claims_no_identity(self):
        """Better a duplicate than matching the wrong building on a blank."""
        assert wn._natural_keys_for("buildings", {"building_code": "  "},
                                    {"building_code"}) == []


class TestDdlIsNotHeldAcrossTheLoad:
    @staticmethod
    def _ddl_block() -> str:
        i = SRC.index("ADD COLUMN IF NOT EXISTS {col_name} {col_type}")
        return SRC[i:i + 1800]

    def test_the_ddl_commits_before_any_row_is_loaded(self):
        block = self._ddl_block()
        assert "await session.commit()" in block, \
            "ADD COLUMN holds ACCESS EXCLUSIVE until the transaction ends"

    def test_it_only_commits_when_there_was_ddl(self):
        assert re.search(r"if missing_columns:\s*\n\s*#", self._ddl_block()), \
            "a table with no new columns must not get a commit of its own"


class TestATableStopsWhenEveryRowFailsTheSameWay:
    def test_there_is_a_ceiling_on_identical_failures(self):
        assert isinstance(wn._MAX_CONSECUTIVE_ROW_FAILURES, int)

    def test_it_is_high_enough_for_a_merely_dirty_file(self):
        assert wn._MAX_CONSECUTIVE_ROW_FAILURES >= 50

    def test_it_is_low_enough_to_beat_a_long_load(self):
        """35,040 rows at ~15/sec is 40 minutes. The ceiling must be reached in seconds."""
        assert wn._MAX_CONSECUTIVE_ROW_FAILURES <= 500

    def test_the_streak_is_reset_wherever_a_row_lands(self):
        # Two success paths: the plain insert and the orphan-FK retry. Both end a streak, or a
        # file with one recurring problem and many good rows would be cut off wrongly.
        assert SRC.count("_streak, _streak_sig = 0, None") >= 2

    def test_only_the_same_error_extends_a_streak(self):
        assert "_streak = _streak + 1 if _sig == _streak_sig else 1" in SRC

    def test_the_run_says_it_stopped_rather_than_reporting_a_short_insert(self):
        assert '"abandoned": abandoned' in SRC
        assert "were not attempted" in SRC

    def test_the_rows_not_attempted_are_counted_as_skipped(self):
        """A row nobody tried is not a row that succeeded."""
        assert 'skipped + max(_left, 0)' in SRC
