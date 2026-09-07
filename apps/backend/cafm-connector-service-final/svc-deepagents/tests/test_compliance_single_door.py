"""Single Door — Feature A certificate classification."""
from src.agents.compliance_single_door import classify_compliance_certificate_doc


def test_classify_eicr_filename():
    hit = classify_compliance_certificate_doc("/tmp/Building_EICR_2025.pdf")
    assert hit is not None
    assert hit["certificate_type_code"] == "EICR"
    assert hit["cert_scope"] == "Building"


def test_classify_gas_safe_landlord_building():
    hit = classify_compliance_certificate_doc(
        "/tmp/scan.pdf",
        user_query="Please index this gas safe landlord certificate",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "GAS_SAFE"
    assert hit["cert_scope"] == "Building"


def test_classify_gas_safe_company_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/Gas_Safe_Register_Company.pdf",
        source_text="Gas Safe Register company membership\nCompany name Acme Heating Ltd",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "GAS_SAFE"
    assert hit["cert_scope"] == "Vendor"


def test_classify_acs_vendor():
    hit = classify_compliance_certificate_doc("/tmp/engineer_ACS_CARD.pdf")
    assert hit is not None
    assert hit["certificate_type_code"] == "ACS_CARD"
    assert hit["cert_scope"] == "Vendor"


def test_classify_bpca_pestguard_not_eicr():
    """Pack doc 53_BPCA_PestGuard must be Vendor BPCA — never Building EICR."""
    hit = classify_compliance_certificate_doc(
        "/tmp/3153810b-1ac0-4f00-8fe1-052063b06ad1_53_BPCA_PestGuard.docx",
        source_text=(
            "BPCA Corporate Membership Certificate\n"
            "This certificate confirms PestGuard Ltd is a full member.\n"
            "BPCA member no. BPCA-29596\n"
        ),
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "BPCA"
    assert hit["cert_scope"] == "Vendor"


def test_classify_bafe_sp203_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/BAFE_SP203_1_FireAlarm.pdf",
        source_text="BAFE SP203-1 registration certificate",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "BAFE_SP203_1"
    assert hit["cert_scope"] == "Vendor"


def test_classify_napit_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/NAPIT_membership.pdf",
        source_text="NAPIT member number 12345",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "NAPIT"
    assert hit["cert_scope"] == "Vendor"


def test_classify_refcom_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/REFCOM_registration.pdf",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "REFCOM"
    assert hit["cert_scope"] == "Vendor"


def test_classify_chas_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/CHAS_SSIP.pdf",
        source_text="CHAS / SSIP membership certificate",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "CHAS_SSIP"
    assert hit["cert_scope"] == "Vendor"


def test_classify_sia_acs_vendor():
    hit = classify_compliance_certificate_doc(
        "/tmp/SIA_ACS_Gold.pdf",
        source_text="SIA Approved Contractor Scheme certificate",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "SIA_ACS"
    assert hit["cert_scope"] == "Vendor"


def test_classify_emergency_lighting_before_niceic():
    """Building EL must win even when NICEIC contractor name appears on the cert."""
    hit = classify_compliance_certificate_doc(
        "/tmp/EL_test_report.pdf",
        source_text="Emergency lighting test to BS 5266\nContractor: NICEIC Approved Ltd",
        peek_file=False,
    )
    assert hit is not None
    assert hit["certificate_type_code"] == "EMERGENCY_LIGHTING"
    assert hit["cert_scope"] == "Building"


def test_classify_generic_certificate_wording_not_eicr():
    """Bare 'certificate' in body must not invent EICR."""
    hit = classify_compliance_certificate_doc(
        "/tmp/unknown_scan.pdf",
        source_text="This certificate confirms membership for the period.",
        peek_file=False,
    )
    assert hit is None


def test_classify_non_cert_returns_none():
    assert classify_compliance_certificate_doc("/tmp/random_notes.txt") is None
