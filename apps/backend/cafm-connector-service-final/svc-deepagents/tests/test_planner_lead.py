"""The Overall card's lead (planner._lead) and the condition cards' tones (27 Oct 2026).

The lead is the answer's first paragraph of prose. A table or a heading at the top of the answer
is not prose: read as one, the table's cells became the card's narrative, run together.
"""
from __future__ import annotations

from src.agents import planner


def test_the_lead_is_the_first_paragraph_of_prose():
    answer = ("## Work orders this week\n\n| WO | Job | Days |\n|---|---|---|\n| WO-1184 | Boiler service | 12 |\n\n"
              "Seven work orders are overdue, most at Kingsway House.\n\nMore detail follows.")
    assert planner._lead(answer) == "Seven work orders are overdue, most at Kingsway House."


def test_a_bullet_or_short_line_is_still_skipped():
    assert planner._lead("- one item\n\nShort.\n\nThe September scorecards are the latest.") == \
        "The September scorecards are the latest."


def _tones(summary):
    kpis: list = []
    planner._cards_condition({"summary": summary}, "q", kpis, [], [])
    return {k["label"]: k["severity"] for k in kpis}


def test_none_in_control_is_not_painted_good():
    assert _tones({"threat": 2, "watch": 1, "in_control": 0})["In control"] != "ok"
    assert _tones({"threat": 0, "watch": 0, "in_control": 5})["In control"] == "ok"
