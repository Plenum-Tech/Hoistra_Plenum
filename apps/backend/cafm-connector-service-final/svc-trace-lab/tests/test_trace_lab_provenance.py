"""Trace Lab provenance is inferred at READ time only (addendum §2.6): classify() and
INFERRED_CHANNEL_SQL over the turn row, refined by the activity payload in detail views."""
from __future__ import annotations

import re

import pytest
from sqlalchemy import text

from src.agents import agent_router
from trace_lab import provenance as prov

MARK = agent_router.SUPPORT_SESSION_MARK

INFER = [
    # (question, session_id, route, answer, channel, origin)
    ("q", "golden-r1-C1-r1", None, "a", "golden", None),
    ("q", "report-1a2b-1759300000", None, "a", "report", None),
    ("Re-run with corrections (query): How many boilers?", "s1", None, "a", "correction", "correction.rerun"),
    ("Re-answer the previous question with this correction: use May. Keep the same building and period unless "
     "the correction changes them.", "s1", None, "a", "correction", "correction.reanswer"),
    ("q", "rerun", None, "a", "correction", "correction.rerun"),
    ("q", "rerun-abc", None, "a", "correction", "correction.rerun"),
    ("File this document on the building.", "docs-abc", None, "a", "upload", None),
    ("q", "ing-4", None, "a", "upload", None),
    ("Validate and ingest this document.", "s1", None, "a", "upload", "upload.files"),
    ("x uploaded files were already processed by the single-door pipeline y", "s1", None, "a", "upload", None),
    ("Ingest 3 documents for Kingsway House.", "s1", None, "a", "upload", "upload.ingest_panel"),
    ("Ingest 1 document for Kingsway House. Expect a gas certificate.", "s1", None, "a", "upload", "upload.ingest_panel"),
    ("Ingest this certificate for Harbour Point. They are certificates for the building.", "s1", None, "a",
     "upload", "upload.ingest_panel"),
    ("Ingest these 2 certificates for the vendor Apex.", "s1", None, "a", "upload", "upload.ingest_panel"),
    ("Migrate assets.xlsx, sites.csv", "s1", None, "a", "upload", "upload.migrate"),
    (MARK + ".\n\n---\n\n**Current user message:**\nhow?", "s1", None, "a", "support", None),
    ("  " + MARK + ". Guide follows.", "s1", None, "a", "support", None),
    ("Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?", "s1", None,
     "a", "ui_action", "ui.matrix_cell:risk"),
    ("The Gas Safety Certificate at Kingsway House is expiring soon — what is the situation, and what should I do "
     "next?", "s1", None, "a", "ui_action", "ui.matrix_cell:warn"),
    ("Why is there no Gas Safety Certificate on record for Kingsway House, and what should I do next?", "s1", None,
     "a", "ui_action", "ui.matrix_cell:gap"),
    ("What is the status of the Gas Safety Certificate at Kingsway House, and who services it?", "s1", None, "a",
     "ui_action", "ui.matrix_cell:ok"),
    ("Map each regulated area of my buildings to its compliance obligations: for every obligation, name the "
     "certificate.", "s1", None, "a", "ui_action", "ui.hoist_graph"),
    ("Why is AHU-1 at Kingsway House showing a baseload drift worth £1,200 a year, and what should I do about it?",
     "s1", None, "a", "ui_action", "ui.energy_investigate:anomaly"),
    ("Why is AHU-1 at Kingsway House where it is, and what should I do about it?", "s1", None, "a", "ui_action",
     "ui.energy_investigate:anomaly"),
    ("Why is Kingsway House at 241 kWh/m² against a reference of 180, and how much of that gap can I act on?", "s1",
     None, "a", "ui_action", "ui.energy_investigate:eui"),
    ("What do we know about Kingsway House's energy performance, and what is missing to assess it properly?", "s1",
     None, "a", "ui_action", "ui.energy_investigate:unknown"),
    ("Why is the Gas Safety Certificate at Kingsway House lapsed or blocked, and what should I do next?\nThanks",
     "s1", None, "a", "chat", None),
    ("", "s1", None, "a", "workflow", None),
    ("   ", "s1", None, "a", "workflow", None),
    ("q", "s1", None, "gate_interrupt", "workflow", None),
    ("q", "s1", "single:clarify", "a", "workflow", None),
    ("How many boilers are at Kingsway House?", "s1", "single:udr", "Three.", "chat", None),
    ("How many boilers?", None, None, None, "chat", None),
    (None, None, None, None, "workflow", None),
]


@pytest.mark.parametrize("question, session_id, route, answer, channel, origin", INFER)
def test_classify(question, session_id, route, answer, channel, origin):
    got = prov.classify(question, session_id, route, answer)
    assert tuple(got) == (channel, origin, "inferred")
    assert (got.channel, got.origin, got.source) == (channel, origin, "inferred")


def test_the_activity_payload_refines_a_turn_the_row_cannot_tell():
    q = "how do I ingest?"
    assert prov.classify(q, "s1", None, "a").channel == "chat"
    assert prov.classify(q, "s1", None, "a", {"extra_context": MARK + " ..."}).channel == "support"
    assert prov.classify(q, "s1", None, "a", {"context": "  " + MARK}).channel == "support"
    assert prov.classify(q, "s1", None, "a", {"mode": "rerun", "rerun_of": "turn-1"}) == \
        ("correction", "correction.rerun", "inferred")
    assert prov.classify(q, "s1", None, "a", {"mode": "stream", "extra_context": "Buildings page"}).channel == "chat"
    assert prov.classify(q, "s1", None, "a", "not a mapping").channel == "chat"
    # server facts still win over the payload
    assert prov.classify(q, "report-1", None, "a", {"mode": "rerun"}).channel == "report"


def test_the_support_mark_is_svc_deepagents_own():
    assert prov.SUPPORT_SESSION_MARK is agent_router.SUPPORT_SESSION_MARK


def test_constants():
    assert prov.CLASSIFIER_VERSION == "inferred-1" and prov.SOURCE == "inferred"
    assert prov.CHANNELS == ("chat", "ui_action", "support", "upload", "correction", "report", "workflow", "golden")


def test_inferred_channel_sql_is_one_case_over_tt():
    sql = prov.INFERRED_CHANNEL_SQL
    assert sql.startswith("CASE") and sql.endswith("END")
    assert "tt.session_id" in sql and "tt.question" in sql and "tt.answer" in sql and "tt.route" in sql
    produced = set(re.findall(r"(?:THEN|ELSE) '([a-z_]+)'", sql))
    assert produced == set(prov.CHANNELS)
    assert MARK in sql and "\\Z" not in sql and ".+''s energy" in sql and ".+'s energy" not in sql
    assert text(sql).compile().params == {}          # no accidental bind parameter (e.g. a ":word")


def test_provenance_is_read_time_only():
    for gone in ("write_turn_meta", "row_params", "capture", "set_request", "decide", "REQUEST", "TurnMeta",
                 "TurnProvenance", "RequestProvenance"):
        assert not hasattr(prov, gone), gone
    source = prov.__file__
    with open(source, encoding="utf-8") as fh:
        body = fh.read()
    for word in ("INSERT", "UPDATE ", "DELETE", "sqlalchemy", "ContextVar"):
        assert word not in body, word
