"""Unit tests for CCC §3 soft-archive: type matcher + request bounds."""
from __future__ import annotations

from uuid import uuid4

import pytest

from src.api.schemas.compliance import ArchiveCertificatesRequest
from src.engines.compliance.certificates import _row_matches_type


def test_type_matcher_matches_code_and_synonym():
    row = {"certificate_type_code": "FRA", "certificate_type_name": "Fire Risk Assessment (FRA)"}
    assert _row_matches_type(row, "FRA")
    assert _row_matches_type(row, "fra")
    assert _row_matches_type(row, "fire risk assessment")


def test_type_matcher_bridges_pack_and_canonical_codes():
    row = {
        "certificate_type_code": "GAS_SAFE",
        "certificate_type_name": "Gas Safety Certificate — Commercial (CP17)",
    }
    assert _row_matches_type(row, "GASSAFE_COMPANY")  # canonical CCC code
    assert _row_matches_type(row, "Gas Safe")  # brand synonym


def test_type_matcher_rejects_other_type():
    row = {"certificate_type_code": "FRA", "certificate_type_name": "Fire Risk Assessment"}
    assert not _row_matches_type(row, "EPC")


def test_archive_request_requires_at_least_one_id():
    with pytest.raises(Exception):
        ArchiveCertificatesRequest(certificate_ids=[])


def test_archive_request_accepts_multi_select():
    req = ArchiveCertificatesRequest(certificate_ids=[uuid4(), uuid4()], reason="duplicate")
    assert len(req.certificate_ids) == 2
