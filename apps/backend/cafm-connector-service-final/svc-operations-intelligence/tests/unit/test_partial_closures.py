"""Guard: append-only audit migration present and pack counts locked."""
from pathlib import Path

from src.engines.compliance.country_pack import load_pack_json


def test_ops_audit_append_only_migration_exists():
    mig = (
        Path(__file__).resolve().parents[2]
        / "migrations"
        / "phase2_ops_audit_append_only.sql"
    )
    text = mig.read_text(encoding="utf-8")
    assert "forbid_ops_audit_mutation" in text
    assert "BEFORE UPDATE" in text
    assert "BEFORE DELETE" in text
    assert "ops_audit_log" in text


def test_udr_phase2_contract_doc_exists():
    doc = Path(__file__).resolve().parents[2] / "UDR_PHASE2.md"
    body = doc.read_text(encoding="utf-8")
    assert "plenum_cafm" in body
    assert "ops_audit_log" in body
    assert "never INSERT work orders" in body


def test_uk_pack_type_counts_metadata():
    pack = load_pack_json()
    assert pack["type_counts"]["total"] == len(pack["certificate_types"])
