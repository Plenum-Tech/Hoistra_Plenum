"""An ingested EPC must arrive carrying the band it states.

The MEES tiles — below E now, below B by 2030 — are computed from compliance_certificates.
energy_rating and nothing else. ops-intelligence's extractor read the band off the page
(/api/compliance/extract returned energy_rating "C", energy_score 74 for the Harbour Point
EPC), and this door then posted certificate_number, the dates, the inspector and the result:
the same omission that once dropped the building name. On 24 Sep 2026 both EPC rows for
Harbour Point were stored with energy_rating NULL and "MEES — proposed 2030" read 0 over a
certificate that said C.
"""
from __future__ import annotations

from src.agents.compliance_single_door import _extract_energy_rating

EPC_TEXT = """Energy Performance Certificate (EPC) - Non-Domestic Building
EPC reference number (RRN)                0660-5580-7384-4815-3898
Energy rating                             Band C
Asset rating (A-G) and score              C (74)
CO2 emissions rating                      58 kgCO2/m2/year
"""


class TestReadFromTheExtractedFields:
    def test_the_snake_case_keys_win(self):
        assert _extract_energy_rating({"energy_rating": "C", "energy_score": 74}, "") == ("C", 74)

    def test_the_pack_label_carrying_band_and_score_together(self):
        assert _extract_energy_rating({"Asset rating (A-G) and score": "B (84)"}, "") == ("B", 84)

    def test_band_prefixed_with_the_word_band_is_not_read_as_a_b(self):
        assert _extract_energy_rating({"Energy rating": "Band C"}, "") == ("C", None)

    def test_a_lowercase_band_is_normalised(self):
        assert _extract_energy_rating({"energy_rating": "d"}, "") == ("D", None)


class TestFallBackToTheDocumentText:
    def test_the_rating_row_is_read_when_extraction_captured_nothing(self):
        assert _extract_energy_rating({}, EPC_TEXT) == ("C", 74)

    def test_an_extracted_band_still_wins_over_the_text(self):
        band, _ = _extract_energy_rating({"energy_rating": "B"}, EPC_TEXT)
        assert band == "B"

    def test_a_certificate_with_no_rating_yields_nothing(self):
        assert _extract_energy_rating({}, "Gas Safe Register CP17 Pass") == (None, None)

    def test_a_co2_rating_figure_is_not_a_band(self):
        assert _extract_energy_rating({}, "CO2 emissions rating  58 kgCO2/m2/year") == (None, None)
