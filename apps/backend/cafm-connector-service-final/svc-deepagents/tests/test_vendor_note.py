"""What the upload reply says about the supplier — and that a failure is never silent.

On 17 Sep 2026 a contract was ingested, the reply read "Ingestion complete", and the
Vendors page never showed it. The supplier named in the document could not be registered
(the backend was squashing the company id to an integer), so the contract was written with
no vendor and had no card to appear under. The reply carried a note for three outcomes —
created, matched, not named — and NOTHING for the two failure statuses the backend actually
emits, `could_not_create` and `error`. Silence is what let a broken vendor register look
like a working upload for three weeks.
"""
from src.agents.contract_performance_single_door import vendor_note_for

NAME = "Moreland Estate Property Management Limited"


def test_a_newly_registered_supplier_is_announced_by_name():
    note = vendor_note_for({"status": "created", "vendor_name": NAME})
    assert NAME in note and "new" in note.lower()


def test_a_matched_supplier_is_named_as_existing():
    note = vendor_note_for({"status": "matched", "vendor_name": NAME})
    assert NAME in note and "existing" in note.lower()


def test_a_document_that_names_nobody_says_the_contract_is_unlinked():
    note = vendor_note_for({"status": "not_named_in_contract", "vendor_name": None})
    assert "unlinked" in note.lower()


def test_a_supplier_that_could_not_be_registered_is_reported_not_swallowed():
    """The symptom the reader actually sees is a contract missing from Vendors. Say that."""
    note = vendor_note_for({"status": "could_not_create", "vendor_name": NAME})
    assert NAME in note, "name the supplier the document named"
    assert "not" in note.lower() and "vendors" in note.lower(), \
        "say it is not linked and will not appear under Vendors"


def test_a_backend_error_during_registration_is_reported_the_same_way():
    note = vendor_note_for({"status": "error", "vendor_name": NAME})
    assert NAME in note and "vendors" in note.lower()


def test_a_vendor_the_caller_supplied_needs_no_note():
    assert vendor_note_for({"status": "supplied", "vendor_id": "v-1", "vendor_name": NAME}) == ""


def test_an_unrecognised_status_with_a_name_still_warns_rather_than_saying_nothing():
    """A status added later and not handled here must fail loud, not quiet — quiet is the bug."""
    note = vendor_note_for({"status": "something_new", "vendor_name": NAME})
    assert note != "" and NAME in note
