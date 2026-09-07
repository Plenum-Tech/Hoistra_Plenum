"""
svc-ingestion/src/shared/unifier.py

Stage 4 — Unify.

Responsibilities:
  1. Tier 1 entity resolution (exact match via DB) — full 4-tier resolver added in Task 2.8
  2. Map IntermediateSchema entities → plenum_cafm ORM objects
  3. Write to PostgreSQL (assets first, then dependent entities)
  4. Store final_json in ingestion_documents
  5. Update ingestion_documents status → accepted
  6. Return UnifyResult with entity counts and unresolved items

Processing order matters — assets must exist before readings/work_orders can reference them.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation

from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_connector.models.plenum_cafm import (
    Asset,
    AssetDocument,
    AssetReading,
    SparePart,
    Vendor,
    WorkOrder,
)
from cafm_shared.logging import get_logger
from cafm_shared.metrics import entity_resolutions
from models.ingestion import IngestionDocument
from shared.intermediate_schema import (
    AssetEntity,
    CertificateEntity,
    FindingEntity,
    IntermediateSchema,
    ReadingEntity,
    SparePartEntity,
    VendorEntity,
    WorkOrderEntity,
)

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class UnifyResult:
    ingestion_id: uuid.UUID
    entities_written: int = 0
    unresolved_count: int = 0
    # Per-entity-type counts
    assets_written: int = 0
    work_orders_written: int = 0
    readings_written: int = 0
    vendors_written: int = 0
    spare_parts_written: int = 0
    documents_written: int = 0
    # Unresolved references (asset_code → not found)
    unresolved_refs: list[str] = field(default_factory=list)


# ── Date / decimal helpers ────────────────────────────────────────────────────


def _parse_date(val: str | None) -> date | None:
    if not val:
        return None
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(val.strip(), fmt).date()
        except (ValueError, AttributeError):
            continue
    return None


def _parse_decimal(val: str | float | int | None) -> Decimal | None:
    if val is None:
        return None
    try:
        return Decimal(str(val))
    except (InvalidOperation, TypeError):
        return None


def _priority_from_severity(severity: str | None) -> str:
    """Map inspection finding severity → work order priority."""
    mapping = {
        "critical": "critical",
        "major": "high",
        "minor": "medium",
        "observation": "low",
    }
    return mapping.get((severity or "").lower(), "medium")


# ── Tier 1 entity resolution (exact match) ────────────────────────────────────


async def _resolve_asset(
    db: AsyncSession,
    organization_id: uuid.UUID,
    asset_code: str | None,
    serial_number: str | None,
) -> Asset | None:
    """Tier 1: exact match on asset_code or serial_number within the organisation."""
    if asset_code:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.asset_code == asset_code,
            )
        )
        asset = result.scalar_one_or_none()
        if asset:
            entity_resolutions.add(
                1, attributes={"entity_type": "asset", "tier": "1", "resolved": "true"}
            )
            return asset

    if serial_number:
        result = await db.execute(
            select(Asset).where(
                Asset.organization_id == organization_id,
                Asset.serial_number == serial_number,
            )
        )
        asset = result.scalar_one_or_none()
        if asset:
            entity_resolutions.add(
                1, attributes={"entity_type": "asset", "tier": "1", "resolved": "true"}
            )
            return asset

    entity_resolutions.add(
        1, attributes={"entity_type": "asset", "tier": "1", "resolved": "false"}
    )
    return None


async def _resolve_vendor(
    db: AsyncSession,
    organization_id: uuid.UUID,
    vendor_name: str,
) -> Vendor | None:
    """Tier 1: exact match on vendor_name within the organisation."""
    result = await db.execute(
        select(Vendor).where(
            Vendor.organization_id == organization_id,
            Vendor.vendor_name == vendor_name,
        )
    )
    vendor = result.scalar_one_or_none()
    resolved = vendor is not None
    entity_resolutions.add(
        1,
        attributes={"entity_type": "vendor", "tier": "1", "resolved": str(resolved).lower()},
    )
    return vendor


# ── Entity mappers ────────────────────────────────────────────────────────────


def _map_asset(entity: AssetEntity, organization_id: uuid.UUID) -> Asset:
    return Asset(
        id=uuid.uuid4(),
        organization_id=organization_id,
        asset_name=entity.name or entity.asset_code or entity.serial_number or "Unknown",
        asset_code=entity.asset_code,
        serial_number=entity.serial_number,
        manufacturer=entity.manufacturer,
        model_number=entity.model_number,
        installation_date=_parse_date(entity.installation_date),
        warranty_expiry=_parse_date(entity.warranty_expiry),
        status=entity.status or "active",
    )


def _map_work_order(
    entity: WorkOrderEntity,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID | None,
) -> WorkOrder:
    return WorkOrder(
        id=uuid.uuid4(),
        organization_id=organization_id,
        asset_id=asset_id,
        title=entity.title or entity.work_order_number or "Work Order",
        description=entity.description,
        priority=entity.priority or "medium",
        status=entity.status or "open",
        completed_at=(
            datetime.fromisoformat(entity.completed_date)
            if entity.completed_date
            else None
        ),
    )


def _map_finding_as_work_order(
    entity: FindingEntity,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID | None,
) -> WorkOrder:
    """Inspection findings become work orders."""
    return WorkOrder(
        id=uuid.uuid4(),
        organization_id=organization_id,
        asset_id=asset_id,
        title=entity.description or "Inspection Finding",
        description=entity.recommendation,
        priority=_priority_from_severity(entity.severity),
        status="open",
    )


def _map_reading(
    entity: ReadingEntity,
    organization_id: uuid.UUID,
    asset_id: uuid.UUID,
) -> AssetReading:
    return AssetReading(
        id=uuid.uuid4(),
        organization_id=organization_id,
        asset_id=asset_id,
        reading_type=entity.reading_type or "unknown",
        value=Decimal(str(entity.value)) if entity.value is not None else Decimal("0"),
        unit=entity.unit,
        recorded_at=(
            datetime.fromisoformat(entity.reading_date)
            if entity.reading_date
            else datetime.now(timezone.utc)
        ),
    )


def _map_vendor(entity: VendorEntity, organization_id: uuid.UUID) -> Vendor:
    return Vendor(
        id=uuid.uuid4(),
        organization_id=organization_id,
        vendor_name=entity.name or entity.vendor_code or "Unknown Vendor",
        address=entity.address,
    )


def _map_certificate_as_document(
    entity: CertificateEntity,
    asset_id: uuid.UUID,
    blob_url: str,
) -> AssetDocument:
    return AssetDocument(
        id=uuid.uuid4(),
        asset_id=asset_id,
        file_url=blob_url,
        document_type=entity.certificate_type or "certificate",
    )


def _map_spare_part(entity: SparePartEntity, organization_id: uuid.UUID) -> SparePart:
    qty = int(entity.quantity) if entity.quantity is not None else 0
    return SparePart(
        id=uuid.uuid4(),
        organization_id=organization_id,
        part_name=entity.name or entity.part_number or "Unknown Part",
        part_code=entity.part_number,
        unit_price=_parse_decimal(entity.unit_cost),
        stock_quantity=max(qty, 0),
        reorder_level=0,
    )


# ── Core Stage 4 function ─────────────────────────────────────────────────────


async def unify(
    *,
    schema: IntermediateSchema,
    organization_id: uuid.UUID,
    db: AsyncSession,
) -> UnifyResult:
    """
    Stage 4 — Write all extracted entities to the plenum_cafm tables.

    Processing order:
      1. Assets (must exist before readings / work orders can reference them)
      2. Vendors
      3. Work orders (from WorkOrderEntity + FindingEntity)
      4. Asset readings
      5. Asset documents (certificates)
      6. Spare parts
      7. Update ingestion_documents record

    Args:
        schema:          The validated IntermediateSchema produced by Stage 2/3.
        organization_id: The organisation that owns these entities.
        db:              Active AsyncSession — caller is responsible for commit.

    Returns:
        UnifyResult with per-type counts and any unresolved references.
    """
    result = UnifyResult(ingestion_id=schema.ingestion_id)

    with tracer.start_as_current_span("ingestion.stage4.unify") as span:
        span.set_attribute("cafm.ingestion_id", str(schema.ingestion_id))
        span.set_attribute("cafm.source_type", schema.source_type.value)
        span.set_attribute("cafm.agent_id", schema.agent_id.value)

        try:
            entities = schema.entities

            # ── 1. Assets ─────────────────────────────────────────────────────
            # Map asset_code/serial → Asset.id for downstream use
            asset_id_cache: dict[str, uuid.UUID] = {}

            for entity in entities.assets:
                existing = await _resolve_asset(
                    db, organization_id, entity.asset_code, entity.serial_number
                )
                if existing:
                    # Use the existing asset — no duplicate
                    key = entity.asset_code or entity.serial_number or str(uuid.uuid4())
                    asset_id_cache[key] = existing.id
                    logger.info(
                        "unify_asset_resolved",
                        ingestion_id=str(schema.ingestion_id),
                        asset_id=str(existing.id),
                        asset_code=entity.asset_code,
                    )
                else:
                    # Create new asset
                    new_asset = _map_asset(entity, organization_id)
                    db.add(new_asset)
                    await db.flush()  # get the id without committing
                    key = entity.asset_code or entity.serial_number or str(new_asset.id)
                    asset_id_cache[key] = new_asset.id
                    result.assets_written += 1
                    result.entities_written += 1
                    logger.info(
                        "unify_asset_created",
                        ingestion_id=str(schema.ingestion_id),
                        asset_id=str(new_asset.id),
                        asset_code=entity.asset_code,
                    )

            # ── 2. Vendors ────────────────────────────────────────────────────
            vendor_id_cache: dict[str, uuid.UUID] = {}

            for entity in entities.vendors:
                name = entity.name or entity.vendor_code or ""
                if not name:
                    continue
                existing = await _resolve_vendor(db, organization_id, name)
                if existing:
                    vendor_id_cache[name] = existing.id
                else:
                    new_vendor = _map_vendor(entity, organization_id)
                    db.add(new_vendor)
                    await db.flush()
                    vendor_id_cache[name] = new_vendor.id
                    result.vendors_written += 1
                    result.entities_written += 1

            # ── 3. Work orders (from WorkOrderEntity) ─────────────────────────
            for entity in entities.work_orders:
                asset_id = _lookup_asset_id(
                    asset_id_cache, entity.asset_code, entity.asset_serial
                )
                if entity.asset_code and asset_id is None:
                    result.unresolved_refs.append(
                        f"work_order asset_code={entity.asset_code}"
                    )
                    result.unresolved_count += 1
                wo = _map_work_order(entity, organization_id, asset_id)
                db.add(wo)
                result.work_orders_written += 1
                result.entities_written += 1

            # ── 4. Work orders (from FindingEntity) ───────────────────────────
            for entity in entities.findings:
                asset_id = _lookup_asset_id(
                    asset_id_cache, entity.asset_code, None
                )
                if entity.asset_code and asset_id is None:
                    result.unresolved_refs.append(
                        f"finding asset_code={entity.asset_code}"
                    )
                    result.unresolved_count += 1
                wo = _map_finding_as_work_order(entity, organization_id, asset_id)
                db.add(wo)
                result.work_orders_written += 1
                result.entities_written += 1

            # ── 5. Asset readings ─────────────────────────────────────────────
            for entity in entities.readings:
                if entity.value is None:
                    continue
                asset_id = _lookup_asset_id(
                    asset_id_cache, entity.asset_code, entity.asset_serial
                )
                if asset_id is None:
                    result.unresolved_refs.append(
                        f"reading asset_code={entity.asset_code or entity.asset_serial}"
                    )
                    result.unresolved_count += 1
                    continue  # readings without an asset are dropped
                reading = _map_reading(entity, organization_id, asset_id)
                db.add(reading)
                result.readings_written += 1
                result.entities_written += 1

            # ── 6. Certificates → asset documents ─────────────────────────────
            for entity in entities.certificates:
                asset_id = _lookup_asset_id(
                    asset_id_cache, entity.asset_code, None
                )
                if asset_id is None:
                    result.unresolved_refs.append(
                        f"certificate asset_code={entity.asset_code}"
                    )
                    result.unresolved_count += 1
                    continue
                # Use the ingestion blob_url as the document file_url
                blob_url = str(schema.source_blob_url or "")
                if not blob_url:
                    continue
                doc = _map_certificate_as_document(entity, asset_id, blob_url)
                db.add(doc)
                result.documents_written += 1
                result.entities_written += 1

            # ── 7. Spare parts ────────────────────────────────────────────────
            for entity in entities.spare_parts:
                part = _map_spare_part(entity, organization_id)
                db.add(part)
                result.spare_parts_written += 1
                result.entities_written += 1

            # ── 8. Update ingestion_documents ─────────────────────────────────
            await _update_ingestion_document(db, schema, result)

            # ── Flush all (caller commits) ────────────────────────────────────
            await db.flush()

            span.set_attribute("cafm.entities_written", result.entities_written)
            span.set_attribute("cafm.unresolved_count", result.unresolved_count)
            span.set_attribute("cafm.resolution_tier_used", 1)
            span.set_status(StatusCode.OK)

            logger.info(
                "unify_stage4_complete",
                ingestion_id=str(schema.ingestion_id),
                entities_written=result.entities_written,
                unresolved_count=result.unresolved_count,
                assets=result.assets_written,
                work_orders=result.work_orders_written,
                readings=result.readings_written,
                vendors=result.vendors_written,
            )

            return result

        except Exception as exc:
            span.record_exception(exc)
            span.set_status(StatusCode.ERROR, str(exc))
            logger.error(
                "unify_stage4_failed",
                ingestion_id=str(schema.ingestion_id),
                error=str(exc),
            )
            raise


# ── Helpers ───────────────────────────────────────────────────────────────────


def _lookup_asset_id(
    cache: dict[str, uuid.UUID],
    asset_code: str | None,
    serial_number: str | None,
) -> uuid.UUID | None:
    if asset_code and asset_code in cache:
        return cache[asset_code]
    if serial_number and serial_number in cache:
        return cache[serial_number]
    return None


async def _update_ingestion_document(
    db: AsyncSession,
    schema: IntermediateSchema,
    result: UnifyResult,
) -> None:
    """Store final_json and mark the document as accepted."""
    doc_result = await db.execute(
        select(IngestionDocument).where(
            IngestionDocument.id == schema.ingestion_id
        )
    )
    doc: IngestionDocument | None = doc_result.scalar_one_or_none()
    if doc is None:
        return

    doc.final_json = {
        "entities_written": result.entities_written,
        "assets": result.assets_written,
        "work_orders": result.work_orders_written,
        "readings": result.readings_written,
        "vendors": result.vendors_written,
        "spare_parts": result.spare_parts_written,
        "documents": result.documents_written,
        "unresolved_count": result.unresolved_count,
        "unresolved_refs": result.unresolved_refs,
    }
    doc.status = "accepted"
    doc.confidence_overall = schema.confidence.overall.value
    doc.eval_score = float(schema.confidence.eval_score)
    doc.model_used = schema.model_used.value
    doc.tokens_in = schema.audit.tokens_in
    doc.tokens_out = schema.audit.tokens_out
    doc.cache_read_tokens = schema.audit.cache_read_tokens
    doc.cost_usd = float(schema.audit.cost_usd)
    doc.processing_ms = schema.audit.processing_ms
    doc.processed_at = datetime.now(timezone.utc)
