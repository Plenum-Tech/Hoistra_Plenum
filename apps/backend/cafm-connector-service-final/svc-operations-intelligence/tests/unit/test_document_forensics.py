"""Unit tests — document forensics / authenticity scoring."""
from __future__ import annotations

from src.engines.compliance.document_forensics import (
    compose_forensics_warning,
    merge_authenticity_warnings,
    run_document_forensics,
)


def test_clean_certificate_text_passes():
    text = """
    Electrical Installation Condition Report
    Certificate Number: EICR-123456
    Issue date: 15/01/2024
    Expiry date: 15/01/2029
    Result: Pass
    Inspector: Jane Smith NICEIC 998877
    """
    out = run_document_forensics(
        source_text=text,
        file_name="eicr_building_a.pdf",
        certificate_type_code="EICR",
        pdf_metadata={"producer": "Adobe PDF Library 15.0", "creator": "Acrobat"},
        page_count=3,
        has_text_layer=True,
        file_size_bytes=120_000,
    )
    assert out["ok"] is True
    assert out["verdict"] == "pass"
    assert out["allow_extract"] is True
    assert out["risk_score"] < 30


def test_sample_watermark_fails():
    text = "SAMPLE CERTIFICATE - NOT VALID FOR USE\nCertificate Number: X-1\nExpiry 01/01/2030"
    out = run_document_forensics(
        source_text=text,
        file_name="sample.pdf",
        certificate_type_code="EICR",
        page_count=1,
        file_size_bytes=50_000,
    )
    assert out["verdict"] == "fail"
    assert out["risk_score"] >= 70
    assert any(f["code"] == "sample_watermark" for f in out["findings"])
    assert out["requires_pm_review"] is True


def test_suspicious_producer_raises_review():
    out = run_document_forensics(
        source_text="Gas Safe certificate number 123 expiry 01/06/2027",
        file_name="gas.pdf",
        certificate_type_code="GAS_SAFE",
        pdf_metadata={"producer": "iLovePDF Online"},
        page_count=1,
        file_size_bytes=40_000,
    )
    assert out["verdict"] in {"review", "fail"}
    assert any(f["code"] == "suspicious_producer" for f in out["findings"])


def test_empty_pdf_blocks_extract():
    out = run_document_forensics(
        source_text="",
        file_name="empty.pdf",
        certificate_type_code="EICR",
        page_count=0,
        file_size_bytes=200,
    )
    assert out["allow_extract"] is False
    assert out["verdict"] == "fail"


def test_merge_warnings_prefers_forensics_first():
    soft = "Inspector accreditation number not verified live against [Gas Safe]"
    forensic = run_document_forensics(
        source_text="SAMPLE draft only certificate expiry 01/01/2030",
        file_name="x.pdf",
        certificate_type_code="GAS_SAFE",
        page_count=1,
        file_size_bytes=10_000,
    )
    merged = merge_authenticity_warnings(soft, forensic)
    assert merged is not None
    assert "Document forensics" in merged
    assert "Gas Safe" in merged


def test_compose_pass_without_findings_is_none():
    assert compose_forensics_warning({"verdict": "pass", "risk_score": 5, "findings": []}) is None
