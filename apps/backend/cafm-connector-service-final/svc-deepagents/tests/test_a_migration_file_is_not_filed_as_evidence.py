"""A spreadsheet bound for the migration wizard is not checked as a document.

Reported 2 Nov 2026: a five-CSV upload for Vasiliki tower 3 produced "Answering a held document
— bms_alarms.csv / Nothing in this document identifies Vasiliki tower 3, so I cannot confirm it
belongs here. Can you tell me what connects it to this building?"

The binding gate is right about what it does -- binding is what turns a document into evidence,
and from that moment it counts in a building's compliance position, so a document that does not
clearly belong is held and put to the uploader. What was wrong is which files it was asked about:
EVERY saved file went through it, spreadsheets included.

bms_alarms.csv is 769 alarm rows whose building_id column spans the portfolio. It is not about
one building, so the gate's question has no answer -- the column is what connects its rows to
buildings, which is the whole point of migrating it. And because one held file suppressed binding
for the entire batch, an unanswerable question about a CSV also kept the real documents beside it
from being bound.

So the gate now asks _file_kind, the same routing the ingestion itself used to decide where each
file went: "structured" went to the migration wizard, "document" to doc-RAG. Asking the router
rather than listing extensions at the gate is what stops the two drifting apart.
"""
from __future__ import annotations

import inspect
import re

import pytest

from src.agents.single_door_flow import _file_kind


# -- the routing the gate now defers to ------------------------------------------------------

@pytest.mark.parametrize("name", [
    "bms_alarms.csv", "compliance_documents.csv", "contacts.csv", "contracts.csv",
    "ppm_schedules.csv", "Vasiliki_House_data.xlsx", "book.xls", "macro.xlsm",
])
def test_a_spreadsheet_is_structured(name):
    assert _file_kind("/tmp/" + name) == "structured"


@pytest.mark.parametrize("name", [
    "EICR_certificate.pdf", "fire_risk_assessment.docx", "notes.txt", "meter.png", "scan.tiff",
])
def test_a_document_is_still_a_document(name):
    assert _file_kind("/tmp/" + name) == "document"


# -- what the route does with it ---------------------------------------------------------------

@pytest.fixture(scope="module")
def upload_route():
    from src.api.routes import workflow as wf
    return inspect.getsource(wf.run_stateful_workflow_with_files)


def test_the_gate_skips_a_structured_file(upload_route):
    assert 'if _file_kind(_p) == "structured":' in upload_route, (
        "every saved file is validated against the selected building again, so a migration "
        "CSV is asked which building it belongs to")
    # The skip has to be INSIDE the loop that validates, not somewhere that never runs.
    loop = upload_route[upload_route.index("for _p in saved_paths:"):]
    assert loop.index('_file_kind(_p) == "structured"') < loop.index("validation_gate.validate"), (
        "the skip comes after the validation call, so the file is checked anyway")


def test_the_gate_does_not_keep_its_own_list_of_extensions(upload_route):
    """A second list is how the gate and the router come to disagree about one file."""
    window = upload_route[upload_route.index("for _p in saved_paths:"):]
    window = window[:window.index("validation_gate.validate")]
    # Code only: the comment above the skip names bms_alarms.csv, which is the point of it.
    code = "\n".join(ln for ln in window.split("\n") if not ln.lstrip().startswith("#"))
    assert ".csv" not in code and ".xlsx" not in code, (
        "the route decides for itself what a spreadsheet is instead of asking _file_kind")


def test_a_document_still_reaches_the_gate(upload_route):
    """The fix must not turn the check off. A PDF filed against a building is exactly what
    binding is for, and holding one that does not belong is the behaviour being protected."""
    assert "validation_gate.validate(" in upload_route
    assert "building_id=building" in upload_route


def test_the_skip_is_recorded(upload_route):
    """Silently not checking a file is indistinguishable from the check passing."""
    assert "single_door.validation_skipped_structured" in upload_route


# -- what the hold still does, so the next reader knows ----------------------------------------

def test_one_held_document_still_holds_the_batch(upload_route):
    """Unchanged on purpose, and recorded here rather than left to be rediscovered.

    `bound = {} if held else ...` binds nothing when anything is held. For documents that is
    arguable -- they are filed together and a reader answering one question expects the set to
    settle together. It mattered here only because a spreadsheet could raise the hold, and a
    spreadsheet no longer can.
    """
    assert re.search(r"bound = \{\} if held else", upload_route), (
        "the batch-level hold changed; if that was deliberate this test should say so instead")
