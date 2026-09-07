"""
svc-ingestion/tests/test_intermediate_schema.py

Unit tests for the IntermediateSchema Pydantic model.
Verifies: valid construction, required field enforcement, cross-field validation,
enum coercion, and AED auto-computation.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from shared.intermediate_schema import (
    AgentId,
    AssetEntity,
    AuditInfo,
    CertificateEntity,
    ConfidenceLevel,
    ConfidenceResult,
    EntitiesBlock,
    ExtractionMethod,
    FindingEntity,
    IntermediateSchema,
    ModelUsed,
    ReadingEntity,
    SourceType,
    SparePartEntity,
    TechnicianEntity,
    VendorEntity,
    WorkOrderEntity,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def minimal_schema(**overrides) -> IntermediateSchema:
    """Return the smallest valid IntermediateSchema."""
    defaults = dict(
        source_type=SourceType.PDF,
        agent_id=AgentId.PDF,
        source_filename="test.pdf",
        extraction_method=ExtractionMethod.CLAUDE_VISION,
        model_used=ModelUsed.SONNET,
    )
    defaults.update(overrides)
    return IntermediateSchema(**defaults)


# ── IntermediateSchema — happy path ───────────────────────────────────────────


def test_minimal_schema_constructs() -> None:
    schema = minimal_schema()
    assert schema.source_type == SourceType.PDF
    assert schema.agent_id == AgentId.PDF
    assert isinstance(schema.ingestion_id, uuid.UUID)
    assert isinstance(schema.extracted_at, datetime)


def test_defaults_populated() -> None:
    schema = minimal_schema()
    assert schema.entities.total_count == 0
    assert schema.confidence.overall == ConfidenceLevel.LOW
    assert schema.confidence.eval_score == 0.0
    assert schema.audit.tokens_in == 0
    assert schema.audit.cost_usd == 0.0


def test_all_source_types_accepted() -> None:
    pairs = [
        (SourceType.PDF, AgentId.PDF, ExtractionMethod.CLAUDE_VISION),
        (SourceType.EXCEL, AgentId.EXCEL, ExtractionMethod.OPENPYXL_CLAUDE),
        (SourceType.WORD, AgentId.WORD, ExtractionMethod.PANDOC_CLAUDE),
        (SourceType.CSV, AgentId.CSV, ExtractionMethod.PANDAS_CLAUDE),
        (SourceType.XML, AgentId.XML_JSON, ExtractionMethod.LXML_CLAUDE),
        (SourceType.JSON, AgentId.XML_JSON, ExtractionMethod.LXML_CLAUDE),
    ]
    for source_type, agent_id, method in pairs:
        schema = minimal_schema(
            source_type=source_type,
            agent_id=agent_id,
            source_filename=f"file.{source_type.value}",
            extraction_method=method,
        )
        assert schema.source_type == source_type


def test_ingestion_id_is_unique() -> None:
    s1 = minimal_schema()
    s2 = minimal_schema()
    assert s1.ingestion_id != s2.ingestion_id


def test_explicit_ingestion_id_preserved() -> None:
    fixed_id = uuid.uuid4()
    schema = minimal_schema(ingestion_id=fixed_id)
    assert schema.ingestion_id == fixed_id


def test_source_blob_url_optional() -> None:
    schema = minimal_schema(source_blob_url="https://blob.example.com/file.pdf")
    assert schema.source_blob_url == "https://blob.example.com/file.pdf"

    schema_no_url = minimal_schema()
    assert schema_no_url.source_blob_url is None


# ── IntermediateSchema — validation failures ───────────────────────────────────


def test_empty_filename_rejected() -> None:
    with pytest.raises(ValidationError, match="source_filename"):
        minimal_schema(source_filename="   ")


def test_missing_source_type_rejected() -> None:
    with pytest.raises(ValidationError):
        IntermediateSchema(
            agent_id=AgentId.PDF,
            source_filename="test.pdf",
            extraction_method=ExtractionMethod.CLAUDE_VISION,
            model_used=ModelUsed.SONNET,
        )


def test_agent_source_type_mismatch_rejected() -> None:
    with pytest.raises(ValidationError, match="not valid for source_type"):
        minimal_schema(
            source_type=SourceType.EXCEL,
            agent_id=AgentId.PDF,  # mismatch
            extraction_method=ExtractionMethod.CLAUDE_VISION,
        )


def test_csv_agent_pdf_source_rejected() -> None:
    with pytest.raises(ValidationError, match="not valid for source_type"):
        minimal_schema(
            source_type=SourceType.PDF,
            agent_id=AgentId.CSV,
            extraction_method=ExtractionMethod.PANDAS_CLAUDE,
        )


# ── EntitiesBlock ─────────────────────────────────────────────────────────────


def test_entities_block_total_count() -> None:
    entities = EntitiesBlock(
        assets=[AssetEntity(name="AHU-001")],
        work_orders=[WorkOrderEntity(title="Repair pump")],
        readings=[ReadingEntity(reading_type="temperature", value=22.5)],
        findings=[FindingEntity(description="Loose bolt")],
    )
    assert entities.total_count == 4


def test_entities_embedded_in_schema() -> None:
    schema = minimal_schema()
    schema.entities.assets.append(AssetEntity(asset_code="A-001", name="Pump"))
    assert schema.entities.total_count == 1


# ── AssetEntity ───────────────────────────────────────────────────────────────


def test_asset_with_code() -> None:
    a = AssetEntity(asset_code="AC-001")
    assert a.asset_code == "AC-001"


def test_asset_with_serial() -> None:
    a = AssetEntity(serial_number="SN-9999")
    assert a.serial_number == "SN-9999"


def test_asset_no_identifier_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        AssetEntity()


def test_asset_extra_fields_accepted() -> None:
    a = AssetEntity(name="Chiller", extra={"floor": "3", "zone": "B"})
    assert a.extra["floor"] == "3"


# ── WorkOrderEntity ───────────────────────────────────────────────────────────


def test_work_order_all_optional() -> None:
    # WorkOrderEntity has no mandatory fields — agents may populate partially
    wo = WorkOrderEntity(work_order_number="WO-1234")
    assert wo.work_order_number == "WO-1234"


def test_work_order_hours() -> None:
    wo = WorkOrderEntity(estimated_hours=4.5, actual_hours=5.0)
    assert wo.estimated_hours == 4.5


# ── ReadingEntity ─────────────────────────────────────────────────────────────


def test_reading_with_value() -> None:
    r = ReadingEntity(reading_type="vibration", value=3.2, unit="mm/s")
    assert r.value == 3.2


def test_reading_no_value_no_type_rejected() -> None:
    with pytest.raises(ValidationError, match="at least value or reading_type"):
        ReadingEntity()


def test_reading_only_type_accepted() -> None:
    r = ReadingEntity(reading_type="visual_inspection")
    assert r.reading_type == "visual_inspection"


# ── TechnicianEntity ──────────────────────────────────────────────────────────


def test_technician_with_name() -> None:
    t = TechnicianEntity(name="Ahmed Al Mansoori")
    assert t.name == "Ahmed Al Mansoori"


def test_technician_no_identifier_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        TechnicianEntity()


# ── VendorEntity ──────────────────────────────────────────────────────────────


def test_vendor_with_name() -> None:
    v = VendorEntity(name="Plenum Tech LLC")
    assert v.name == "Plenum Tech LLC"


def test_vendor_no_identifier_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        VendorEntity()


# ── SparePartEntity ───────────────────────────────────────────────────────────


def test_spare_part_with_part_number() -> None:
    sp = SparePartEntity(part_number="FLT-220V-10A", quantity=5)
    assert sp.part_number == "FLT-220V-10A"


def test_spare_part_no_identifier_rejected() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        SparePartEntity()


# ── ConfidenceResult ──────────────────────────────────────────────────────────


def test_confidence_defaults() -> None:
    c = ConfidenceResult()
    assert c.overall == ConfidenceLevel.LOW
    assert c.eval_score == 0.0
    assert c.rules_passed is False
    assert c.rules_violations == []


def test_confidence_score_rounded() -> None:
    c = ConfidenceResult(eval_score=0.94567)
    assert c.eval_score == 0.946


def test_confidence_score_out_of_range_rejected() -> None:
    with pytest.raises(ValidationError):
        ConfidenceResult(eval_score=1.5)

    with pytest.raises(ValidationError):
        ConfidenceResult(eval_score=-0.1)


def test_confidence_per_field() -> None:
    c = ConfidenceResult(
        overall=ConfidenceLevel.HIGH,
        per_field={"asset_id": ConfidenceLevel.HIGH, "serial": ConfidenceLevel.MEDIUM},
        eval_score=0.92,
        rules_passed=True,
    )
    assert c.per_field["serial"] == ConfidenceLevel.MEDIUM


def test_confidence_violations() -> None:
    c = ConfidenceResult(
        rules_violations=["contradiction: Normal reading + Critical severity on AHU-004"]
    )
    assert len(c.rules_violations) == 1


# ── AuditInfo ─────────────────────────────────────────────────────────────────


def test_audit_defaults() -> None:
    a = AuditInfo()
    assert a.tokens_in == 0
    assert a.cost_usd == 0.0
    assert a.cost_aed == 0.0
    assert a.passes == 1


def test_audit_aed_auto_computed() -> None:
    a = AuditInfo(cost_usd=1.0)
    assert a.cost_aed == pytest.approx(3.67, rel=1e-3)


def test_audit_explicit_aed_preserved() -> None:
    a = AuditInfo(cost_usd=1.0, cost_aed=4.0)
    assert a.cost_aed == 4.0  # explicit value not overwritten


def test_audit_negative_tokens_rejected() -> None:
    with pytest.raises(ValidationError):
        AuditInfo(tokens_in=-1)


def test_audit_passes_minimum_one() -> None:
    with pytest.raises(ValidationError):
        AuditInfo(passes=0)


# ── Full round-trip ───────────────────────────────────────────────────────────


def test_full_schema_round_trip() -> None:
    """Serialize to dict and reconstruct — all values preserved."""
    schema = IntermediateSchema(
        source_type=SourceType.PDF,
        agent_id=AgentId.PDF,
        source_filename="inspection_report_nov_2025.pdf",
        source_blob_url="https://plenumstorage.blob.core.windows.net/pdf-raw/doc.pdf",
        extraction_method=ExtractionMethod.CLAUDE_VISION,
        model_used=ModelUsed.SONNET,
        entities=EntitiesBlock(
            assets=[AssetEntity(asset_code="AHU-001", name="Air Handling Unit 1")],
            findings=[
                FindingEntity(
                    asset_code="AHU-001",
                    severity="Major",
                    description="Filter blocked — replace immediately",
                )
            ],
        ),
        confidence=ConfidenceResult(
            overall=ConfidenceLevel.HIGH,
            eval_score=0.94,
            rules_passed=True,
        ),
        audit=AuditInfo(
            tokens_in=4521,
            tokens_out=892,
            cache_read_tokens=3100,
            cost_usd=0.021,
            processing_ms=8400,
        ),
    )

    data = schema.model_dump()
    reconstructed = IntermediateSchema(**data)

    assert reconstructed.ingestion_id == schema.ingestion_id
    assert reconstructed.source_filename == "inspection_report_nov_2025.pdf"
    assert reconstructed.entities.assets[0].asset_code == "AHU-001"
    assert reconstructed.confidence.eval_score == 0.94
    assert reconstructed.audit.tokens_in == 4521
    assert reconstructed.audit.cost_aed == pytest.approx(0.021 * 3.67, rel=1e-3)


def test_schema_json_serialisable() -> None:
    schema = minimal_schema()
    json_str = schema.model_dump_json()
    assert "ingestion_id" in json_str
    assert "cafm-ingestion" not in json_str  # service name not leaked
