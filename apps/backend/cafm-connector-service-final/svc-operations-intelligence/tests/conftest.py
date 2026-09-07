"""Shared fixtures for Compliance Engine unit tests (no live DB required)."""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def today() -> date:
    return date(2026, 7, 27)


@pytest.fixture
def building_cert_payload(today: date) -> dict:
    return {
        "cert_scope": "Building",
        "certificate_type_code": "EICR",
        "certificate_number": "EICR-2024-001",
        "issue_date": (today - timedelta(days=400)).isoformat(),
        "expiry_date": (today + timedelta(days=45)).isoformat(),
        "inspector_name": "Jane Inspector",
        "inspector_accreditation_number": "NICEIC-12345",
        "result": "Pass",
        "country_code": "UK",
        "confirmed_by_pm": True,
    }


@pytest.fixture
def vendor_cert_payload(today: date) -> dict:
    return {
        "cert_scope": "Vendor",
        "certificate_type_code": "GAS_SAFE",
        "certificate_number": "GS-998877",
        "issue_date": (today - timedelta(days=300)).isoformat(),
        "expiry_date": (today + timedelta(days=10)).isoformat(),
        "vendor_id": str(uuid4()),
        "country_code": "UK",
        "confirmed_by_pm": True,
    }


@pytest.fixture
def mock_pack_row() -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid4(),
        pack_id="UK-COMPLIANCE-v1.1",
        country_code="UK",
        pack_version="1.1",
        certificate_type_code="GAS_SAFE",
        certificate_type_name="Gas Safe Registration",
        certificate_scope="Vendor",
        trade_category="Gas",
        regulation_reference="Gas Safety Regulations",
        regulation_url=None,
        frequency_months=12,
        issuing_body="Gas Safe Register",
        required_contractor_accreditation=None,
        verification_url="https://www.gassaferegister.co.uk/find-an-engineer/",
        key_fields_schema={"required": ["certificate_number", "issue_date", "expiry_date"]},
        alert_thresholds={},
        is_active=True,
    )
