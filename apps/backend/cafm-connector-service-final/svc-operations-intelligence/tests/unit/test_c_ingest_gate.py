"""The mandatory-field gate: what it asks, what it does not, and what it records."""
from __future__ import annotations

import asyncio

from src.engines import ingest_gate as G


def _check(doc_type, extracted, *, conf=None, required=None):
    """Run the gate with the pack lookup stubbed, so this stays a pure test."""
    async def go():
        if required is not None:
            async def fake(session, dt, **kw):
                return list(required), "a stub"
            orig = G.required_fields
            G.required_fields = fake
            try:
                return await G.check_document(None, doc_type, extracted, field_confidence=conf)
            finally:
                G.required_fields = orig
        return await G.check_document(None, doc_type, extracted, field_confidence=conf)

    return asyncio.run(go())


# ── it asks only about what is missing ───────────────────────────────────────


def test_a_field_extraction_read_is_never_asked_for():
    """A gate that re-asks for what it already has trains people to click through it, and a
    gate people click through is worse than none: friction with no accuracy."""
    out = _check("vendor_invoice", {"invoice_ref": "INV-1", "lines": [{"x": 1}]})
    assert out["ready"] is True
    assert [q["field"] for q in out["questions"]] == []


def test_a_missing_required_field_blocks_and_says_why():
    out = _check("vendor_invoice", {"invoice_ref": "INV-1"})
    assert out["ready"] is False
    q = next(q for q in out["questions"] if q["field"] == "lines")
    assert q["blocking"] is True
    assert "sum of its lines" in q["why"]


def test_an_empty_list_counts_as_missing():
    """An invoice with a `lines: []` is an invoice with no lines, not an invoice with lines."""
    assert _check("vendor_invoice", {"invoice_ref": "I", "lines": []})["ready"] is False


def test_placeholder_text_counts_as_missing():
    for junk in ("", "   ", "N/A", "unknown", "None", None):
        out = _check("vendor_invoice", {"invoice_ref": junk, "lines": [{"x": 1}]})
        assert out["ready"] is False, junk


# ── low confidence is confirmed, not retyped ─────────────────────────────────


def test_a_value_read_unsurely_is_shown_for_confirmation_not_blanked():
    """Reading "is this right?" is a second's work. An empty box is a minute of squinting
    at the PDF for something the extractor already found."""
    out = _check("service_contract", {"contract_ref": "C-1", "vendor_name": "Apex"},
                 conf={"vendor_name": "low"})
    q = next(q for q in out["questions"] if q["field"] == "vendor_name")
    assert q["blocking"] is False, "an unsure read must not stop the ingest"
    assert q["found"] == "Apex", "what was read travels with the question"
    assert out["ready"] is True


def test_a_confidently_read_value_is_not_questioned():
    out = _check("service_contract", {"contract_ref": "C-1", "vendor_name": "Apex"},
                 conf={"vendor_name": "high"})
    assert [q["field"] for q in out["questions"]] == []


# ── a date that is not a date ────────────────────────────────────────────────


def test_text_where_a_date_belongs_is_worse_than_nothing_and_is_caught():
    """It would write, and be wrong everywhere it is later compared."""
    out = _check("compliance_certificate", {"expiry_date": "see overleaf", "cert_scope": "Building"},
                 required=["expiry_date"])
    q = next(q for q in out["questions"] if q["field"] == "expiry_date")
    assert q["blocking"] is True
    assert "not a date" in q["prompt"]


def test_a_real_date_passes():
    out = _check("compliance_certificate", {"expiry_date": "2027-01-13"},
                 required=["expiry_date"])
    assert out["ready"] is True


# ── constrained answers carry their options ──────────────────────────────────


def test_a_field_with_two_valid_answers_offers_them():
    """A free-text box for a value with exactly two valid answers invites a third."""
    out = _check("compliance_certificate", {}, required=["cert_scope"])
    q = next(q for q in out["questions"] if q["field"] == "cert_scope")
    assert q["kind"] == "choice"
    assert q["options"] == ["Building", "Vendor"]


def test_the_register_s_own_required_fields_are_gated_too():
    """cert_scope is not on the document and the pack does not declare it, but the write
    refuses without it. Leaving it out of the gate turns "ready to file" into a rejected
    write after the person has left the screen."""
    assert "cert_scope" in G.PLATFORM_REQUIRED["compliance_certificate"]


# ── answers are recorded as answers ──────────────────────────────────────────


def test_an_answer_is_recorded_as_supplied_not_as_extracted():
    """A date a PM typed is usable evidence. It is not the same evidence as one read off
    the certificate, and a register that cannot tell them apart cannot later say which of
    its dates were actually seen."""
    out = G.apply_answers(
        {"certificate_number": "C-1", "field_confidence": {"certificate_number": "high"}},
        {"expiry_date": "2027-01-13"},
        answered_by="bala.r@plenum-tech.com",
    )
    assert out["expiry_date"] == "2027-01-13"
    assert out["field_confidence"]["expiry_date"] == "stated"
    assert out["field_confidence"]["certificate_number"] == "high", "untouched"
    rec = out["raw_metadata"]["answered_fields"]["expiry_date"]
    assert rec["by"] == "bala.r@plenum-tech.com"
    assert rec["was"] is None, "what extraction had, so a correction is visible as one"


def test_answering_over_a_value_keeps_what_was_there_before():
    out = G.apply_answers({"expiry_date": "2020-01-01"}, {"expiry_date": "2027-01-13"})
    assert out["expiry_date"] == "2027-01-13"
    assert out["raw_metadata"]["answered_fields"]["expiry_date"]["was"] == "2020-01-01"


def test_blank_answers_are_not_recorded_as_answers():
    before = {"expiry_date": "2027-01-13"}
    assert G.apply_answers(before, {"expiry_date": "", "issuer": None}) == before


def test_no_answers_leaves_the_payload_exactly_as_it_was():
    before = {"a": 1}
    assert G.apply_answers(before, None) == before
    assert G.apply_answers(before, {}) == before


# ── the mandatory list is not a second copy of the pack's ────────────────────


def test_the_gate_holds_no_certificate_field_list_of_its_own():
    """The pack declares required fields per certificate type, and 55 of 55 UK types do.
    A list here would be a second answer to a question already answered, and the two would
    drift apart the first time a pack was updated."""
    assert "compliance_certificate" not in G.DOC_REQUIRED
