"""An ingested building certificate must arrive carrying the building it is about.

ops-intelligence already knows what to do with a building name and reference: _pick_field
finds them, resolve_site_link links the site FK, attach_to_graph places the certificate on
the building. It simply never received them. This door posted certificate_number, the dates,
the inspector and the result — and nothing else off the page.

The cost, on 22 Sep 2026: a CP17 and an EICR both printing "Building name: Harbour Point /
Building reference: B-101" were stored with building_name NULL, building_reference NULL and
site_id NULL, and the Compliance console filed them under "No building on certificate" while
Harbour Point reported "No certificate has been filed against a document on this building."

The UK pack's key_fields_schema names no building field for either type, so `extracted` will
often not hold one — which is why the document text is read as a fallback.
"""
from __future__ import annotations

from src.agents.compliance_single_door import _extract_building_fields

CP17 = """Gas Safe Register
Type code: CP17 . Scope: Building . Trade: Mechanical
certificate_number: CP17-B-101-2026-0812
Building name                             Harbour Point
Building reference                        B-101
Inspector accreditation number            5581
"""


class TestReadFromTheExtractedFields:
    def test_the_pack_labels_are_used_when_present(self):
        name, ref = _extract_building_fields(
            {"Building name": "Harbour Point", "Building reference": "B-101"}, ""
        )
        assert (name, ref) == ("Harbour Point", "B-101")

    def test_snake_case_keys_are_accepted_too(self):
        name, ref = _extract_building_fields(
            {"building_name": "Ashgrove Court", "building_reference": "B-102"}, ""
        )
        assert (name, ref) == ("Ashgrove Court", "B-102")


class TestFallBackToTheDocumentText:
    def test_a_labelled_row_is_read_when_the_pack_captured_nothing(self):
        assert _extract_building_fields({}, CP17) == ("Harbour Point", "B-101")

    def test_an_extracted_value_still_wins_over_the_text(self):
        name, _ = _extract_building_fields({"Building name": "From the pack"}, CP17)
        assert name == "From the pack"

    def test_a_document_naming_no_building_yields_nothing(self):
        assert _extract_building_fields({}, "BAFE SP203-1 Certificate of Registration") == (
            None,
            None,
        )

    def test_a_certificate_number_is_never_mistaken_for_a_building_reference(self):
        _, ref = _extract_building_fields({}, "certificate_number: CP17-B-101-2026-0812")
        assert ref is None
