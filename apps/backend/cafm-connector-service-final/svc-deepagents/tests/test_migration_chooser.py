"""'I want to migrate my data' offers three ways in, and only when no method is named.

The keyword table reads "migrate my data" as "run mapping over the uploaded files", so a user with
nothing uploaded got the mapping route instead of a choice. The rule (5 Oct 2026): offer CSV/Excel
data migration, PDF/document migration, or a direct database connection, each with its next step.
"""
from __future__ import annotations

import pytest

from src.agents import orchestrator as O
from src.agents.migration_chooser import CHOOSER_REPLY, is_bare_migration_request

_cls = next(v for v in vars(O).values() if isinstance(v, type) and hasattr(v, "_offers_migration_choice"))


@pytest.mark.parametrize("q", [
    "I want to migrate my data",
    "migrate my data",
    "i would like to migrate our data into hoistra",
    "help me migrate from our old CMMS",
    "How do I bring my data into the platform?",
    "We need to import our asset register",
    "I want to move my maintenance history over",
])
def test_a_request_that_names_no_method_gets_the_three_options(q):
    assert is_bare_migration_request(q)


@pytest.mark.parametrize("q", [
    "I want to migrate my CSV files",
    "migrate this Excel workbook",
    "I want to upload PDF certificates",
    "connect Fiix",
    "migrate data from our SQL Server database",
    "what is the status of my migration",
    "how many migrations failed last week",
    "resume the migration at gate 2",
    "how many work orders are open",
    "which assets are in poor condition",
])
def test_a_named_method_or_a_question_about_a_migration_goes_on_to_routing(q):
    assert not is_bare_migration_request(q)


def test_files_arriving_with_the_turn_decide_the_path():
    assert not is_bare_migration_request("I want to migrate my data", "Uploaded files: assets.csv")


def test_the_reply_offers_all_three_with_their_next_step():
    r = CHOOSER_REPLY
    assert r.index("CSV / Excel") < r.index("PDF / document") < r.index("Direct database")
    assert "attach the CSV or Excel files" in r and "attach the PDF or Word files" in r
    assert "connect Fiix" in r and "SQL Server" in r


def test_a_session_that_already_holds_uploads_keeps_its_mapping_route():
    assert _cls._offers_migration_choice("I want to migrate my data", None, {})
    assert not _cls._offers_migration_choice("I want to migrate my data", None, {"ingested_documents": 3})
    assert not _cls._offers_migration_choice("I want to migrate my data", None, {"fiix_ingestion_id": "f-1"})


def test_the_cards_match_the_reply_and_each_says_what_a_click_does():
    from src.agents.migration_chooser import CHOICES
    assert [c["title"] for c in CHOICES] == [
        "CSV / Excel data migration", "PDF / document migration", "Direct database migration"]
    assert [c["n"] for c in CHOICES] == [1, 2, 3]
    assert CHOICES[0]["action"] == {"kind": "attach", "accept": ".csv,.xls,.xlsx"}
    assert CHOICES[1]["action"] == {"kind": "attach", "accept": ".pdf,.doc,.docx"}
    assert CHOICES[2]["action"] == {"kind": "ask", "text": "connect Fiix"}


def test_the_streamed_completion_carries_the_cards_only_when_offered():
    from src.agents.migration_chooser import CHOICES
    from src.agents.session_workspace import workflow_stream_completion_payload
    assert workflow_stream_completion_payload("s-1", answer="x", choices=CHOICES)["choices"] == CHOICES
    assert "choices" not in workflow_stream_completion_payload("s-1", answer="x")
