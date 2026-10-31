"""A certificate has to be nameable before it can be recognised.

There was no `certificate` type in the classifier. Measured on the live database, 253 ingested
documents carried these types: asset_manual 100, unknown 100, inspection_report 25, null 21,
contract 6, sla 1 — and never one certificate, because none could be produced. 65 of the
unclassified ones are plainly accreditations by filename. They ingested and indexed fine, then
produced no compliance_certificates row, which is why only 7 of the 807 vendors reachable from
a building hold any accreditation.

These tests use the real filenames from that store.
"""
import re
from pathlib import Path

import pytest

SRC = (Path(__file__).resolve().parents[1] / "app" / "services" / "document_classifier.py"
       ).read_text(encoding="utf-8")

# The module imports the doc-rag app; the rules are exercised without standing that up.
_ns: dict = {}
exec(compile(re.sub(r"^from app\..*$", "", SRC, flags=re.M), "classifier", "exec"), _ns)
name_key = _ns["_name_key"]
FILENAME_RULES = _ns["_FILENAME_RULES"]
KEYWORD_RULES = _ns["_KEYWORD_RULES"]


name_matches = _ns["_name_matches"]


def from_name(file_name: str) -> list[str]:
    """Exactly what the classifier asks of a filename, negative rules included."""
    key = name_key(file_name)
    return [t for t, frags in FILENAME_RULES.items() if name_matches(key, t, frags)]


class TestCertificateIsAType:

    def test_certificate_exists_as_a_keyword_type(self):
        """The absence of this key is the whole bug — nothing else could have produced one."""
        assert "certificate" in KEYWORD_RULES

    def test_the_schemes_this_estate_files_are_named(self):
        blob = " ".join(KEYWORD_RULES["certificate"])
        for scheme in ("bpca", "ssaib", "gas safe", "niceic", "loler", "bafe"):
            assert scheme in blob, scheme


class TestTheRealFilenamesInTheStore:
    """Every one of these is a document sitting in the live store as `unknown`."""

    @pytest.mark.parametrize("name", [
        "b9224317-6ae6-43e0-aeab-d9e80e281eb6_bpca-membership-certificate-2.pdf",
        "00c5ee4c-76b7-4ad8-9e9b-683401df720f_sia_acs_certificate_of_approval.pdf",
        "0e779b0c-2467-4183-9b72-ba2aef0a14ea_SSAIB_certificate_of_registration.pdf",
        "ten-building-1786715110_EICR_building.pdf",
        "c23fdd25-8181-47e1-99b1-2b3f9c1addb6_TM44-ac-report.pdf",
        "2bf2264f-210b-4c16-a02f-888a3138dde7_53_BPCA_PestGuard.docx",
    ])
    def test_it_is_recognised_as_a_certificate(self, name):
        assert "certificate" in from_name(name), name

    def test_the_uuid_prefix_does_not_hide_the_name(self):
        """The store prefixes every upload with a UUID. Matching the raw name would fail on
        the hyphens in it, and matching only a prefix would never see the human part."""
        assert from_name("b9224317-6ae6-43e0-aeab-d9e80e281eb6_bpca-membership-certificate.pdf") \
            == from_name("bpca-membership-certificate.pdf")

    def test_separators_do_not_matter(self):
        """gas_safe, gas-safe and GasSafe are one thing."""
        assert from_name("gas_safe_reg.pdf") == from_name("gas-safe-reg.pdf") \
            == from_name("GasSafeReg.pdf") == ["certificate"]


class TestItDoesNotOverReach:

    def test_a_survey_is_still_an_inspection(self):
        assert from_name("Asbestos_HSG264_survey.pdf") == ["inspection_report"]

    def test_a_contract_is_still_a_contract(self):
        assert from_name("contract.pdf") == ["contract"]

    def test_a_name_with_no_signal_claims_nothing(self):
        """Silence is the right answer for assets.pdf — the body decides it."""
        assert from_name("8ff536f7-6911-45f0-9c68-2e7e9aec71de_assets.pdf") == []

    def test_the_filename_does_not_outweigh_a_body(self):
        """A filename is a hint and a body is proof. One weighted vote must not overturn a
        document whose text says clearly what it is."""
        assert _ns["_FILENAME_WEIGHT"] <= 3


class TestContractorIsNotAContract:
    """"contractor" contains "contract". CONTRACTOR_PL_INSURANCE.docx is a contractor's public
    liability insurance — a certificate, and not a contract at all. Eight documents in the live
    store are exactly this, and they read as both types until the negative rule was added."""

    def test_a_contractors_insurance_is_a_certificate_only(self):
        for name in ("1_49_CONTRACTOR_PL_INSURANCE_MeridianFM.docx",
                     "1_48_CONTRACTOR_EL_INSURANCE_MeridianFM.docx"):
            assert from_name(name) == ["certificate"], name

    def test_a_real_contract_still_reads_as_a_contract(self):
        """The exclusion must not cost the word its ordinary meaning."""
        assert from_name("contract.pdf") == ["contract"]
        assert "contract" in from_name("MSA_agreement_2026.pdf")
