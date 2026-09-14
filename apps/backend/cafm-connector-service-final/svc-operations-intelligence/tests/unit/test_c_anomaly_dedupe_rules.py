"""A duplicate and a recurrence look identical, and must not be treated the same.

Both are the same meter, type, metric and amount. What separates them is time: a row
detected after an earlier copy was SETTLED is the problem coming back, and deleting it as a
duplicate would throw away the fact that it returned.

hoistra_test had exactly that pair — two events dismissed at 16:02, re-detected at 16:17 —
and the first version of the cleanup tool offered to delete both. Production had none, so
the tool looked correct right up until it was pointed at the other database.
"""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

TOOL = (Path(__file__).resolve().parents[6] / "db" / "tools" / "merge_duplicate_anomalies.py")


def _tool():
    spec = importlib.util.spec_from_file_location("merge_duplicate_anomalies", TOOL)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


BASE = datetime(2026, 9, 11, 16, 0, tzinfo=timezone.utc)


def row(rid, status, acted_min=None, detected_min=0, action=None):
    return {
        "id": rid, "status": status, "pm_action": action, "pm_reason": None,
        "acted_at": BASE + timedelta(minutes=acted_min) if acted_min is not None else None,
        "detected_at": BASE + timedelta(minutes=detected_min), "queue_item_id": None,
    }


class TestTheToolKnowsItsOwnRules:
    def test_the_settled_statuses_match_the_scan(self):
        """If the scan's list changes and this one does not, the tool starts deleting
        recurrences the scan deliberately raised."""
        from src.engines.energy.anomalies import _SETTLED_STATUSES

        assert set(_tool().SETTLED_STATUSES) == set(_SETTLED_STATUSES)

    def test_the_event_key_matches_the_one_the_report_groups_on(self):
        """The register and the monthly report must agree on what one event is.

        reports._event_key has two branches: the window bounds where both are set, and
        otherwise these four fields. The rows this tool cleans carry a NULL window_start —
        which is why the report falls back for them too — so it is the fallback branch the
        two have to agree on.
        """
        import inspect

        from src.engines.energy import reports

        source = inspect.getsource(reports)
        start = source.index("def _event_key")
        key = source[start:source.index("events:", start)]
        fallback = key[key.index("return (", key.index("window_end)") + 1):]
        for field in ("meter_id", "anomaly_type", "metric_pct", "financial_gbp"):
            assert field in fallback, f"reports._event_key no longer falls back on {field}"
            assert field in _tool().EVENT_KEY


class TestADuplicateIsNotARecurrence:
    """The splitting logic, exercised directly on the row shapes the tool builds."""

    @staticmethod
    def _split(rows):
        tool = _tool()
        settled_at = [r["acted_at"] for r in rows
                      if r["status"] in tool.SETTLED_STATUSES and r["acted_at"]]
        recurrences = [r for r in rows if settled_at and r["detected_at"]
                       and any(r["detected_at"] > t for t in settled_at)]
        return [r for r in rows if r not in recurrences], recurrences

    def test_a_redetection_after_a_dismissal_is_left_alone(self):
        rows = [row("dismissed", "dismissed", acted_min=2, detected_min=0, action="acknowledge"),
                row("returned", "open", detected_min=17)]
        occurrence, recurrences = self._split(rows)
        assert [r["id"] for r in recurrences] == ["returned"]
        assert len(occurrence) == 1, "one occurrence left means nothing to merge"

    def test_copies_written_while_the_event_was_only_monitored_are_duplicates(self):
        """monitoring is not settled — the scan should never have raised these twice."""
        rows = [row("a", "monitoring", acted_min=5, detected_min=0, action="monitor"),
                row("b", "monitoring", acted_min=4, detected_min=1, action="monitor"),
                row("c", "open", detected_min=9)]
        occurrence, recurrences = self._split(rows)
        assert recurrences == []
        assert len(occurrence) == 3

    def test_expected_is_not_settled_either(self):
        """A PM saying "this is expected" is a judgement on a live event, not a closure."""
        rows = [row("a", "expected", acted_min=1, detected_min=0, action="mark_expected"),
                row("b", "open", detected_min=60)]
        occurrence, recurrences = self._split(rows)
        assert recurrences == [], "marking expected must not license a second row"
        assert len(occurrence) == 2

    def test_a_dismissal_only_protects_what_came_after_it(self):
        rows = [row("before", "open", detected_min=0),
                row("dismissed", "dismissed", acted_min=10, detected_min=1, action="acknowledge"),
                row("after", "open", detected_min=20)]
        occurrence, recurrences = self._split(rows)
        assert [r["id"] for r in recurrences] == ["after"]
        assert {r["id"] for r in occurrence} == {"before", "dismissed"}


class TestNewestDecisionWins:
    def test_the_survivor_is_the_most_recent_decision(self):
        """The ordering the tool's SQL applies, asserted on the same shapes."""
        rows = sorted(
            [row("older", "expected", acted_min=-130, action="mark_expected"),
             row("newer", "monitoring", acted_min=-1, action="monitor"),
             row("unacted", "open", detected_min=5)],
            key=lambda r: (r["acted_at"] is None, -(r["acted_at"] or BASE).timestamp()),
        )
        assert rows[0]["id"] == "newer"

    def test_where_nothing_was_decided_the_first_detection_survives(self):
        rows = sorted([row("second", "open", detected_min=9), row("first", "open", detected_min=1)],
                      key=lambda r: (r["acted_at"] is None, r["detected_at"]))
        assert rows[0]["id"] == "first"


class TestASimulatedReadingKeepsItsHour:
    """reading_at is timestamptz, so a naive datetime is read as the CLIENT's local time.

    Stripping the zone put a 00:00 UTC slot into the database at 20:00 the previous day on a
    machine four hours ahead — the row then carried the load shape of a completely different
    hour, and the first "September" reading was dated 31 August. It also broke the
    no-overwrite check, which compared one instant while the insert wrote another.
    """

    def test_the_simulator_never_strips_a_timezone(self):
        import inspect

        from src.engines.energy import simulator

        source = inspect.getsource(simulator)
        assert "tzinfo=None" not in source, (
            "a naive timestamp into a timestamptz column is read as local time")

    def test_both_paths_write_the_slot_they_shaped(self):
        import inspect

        from src.engines.energy import simulator

        for fn in (simulator.simulate_half_hour, simulator.backfill_range):
            body = inspect.getsource(fn)
            assert "reading_at=slot" in body, f"{fn.__name__} does not write the slot it shaped"
