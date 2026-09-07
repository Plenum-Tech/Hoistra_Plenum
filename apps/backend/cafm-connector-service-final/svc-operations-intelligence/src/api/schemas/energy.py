"""Pydantic schemas — Feature C Energy Intelligence."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class MeterUpsertRequest(BaseModel):
    id: UUID | None = None
    organization_id: UUID | None = None
    site_id: UUID | None = None
    asset_id: UUID | None = None
    meter_type: str = "electricity"
    mpan: str | None = None
    mprn: str | None = None
    dcc_device_id: str | None = None
    tariff_gbp_per_kwh: float | None = 0.28
    carbon_kg_per_kwh: float | None = 0.207
    is_sub_meter: bool = False
    asset_type_benchmark_kwh: float | None = None


class ReadingsIngestRequest(BaseModel):
    meter_id: UUID
    readings: list[dict[str, Any]]
    organization_id: UUID | None = None
    source: str = "dcc"
    detect_gaps: bool = True


class BuildingProfileRequest(BaseModel):
    site_id: UUID
    gia_m2: float
    building_type: str = "office"
    organization_id: UUID | None = None


class EuiComputeRequest(BaseModel):
    site_id: UUID
    period_start: date
    period_end: date
    meter_type: str = "electricity"
    organization_id: UUID | None = None


class ConditionDeduceRequest(BaseModel):
    asset_id: UUID
    report_text: str | None = None
    source_report_ref: str | None = None
    asset_code: str | None = None
    organization_id: UUID | None = None
    write_to_asset: bool = True
    auto_cross_ref: bool = True
    from_vectors: bool = False


class ConditionFromVectorsRequest(BaseModel):
    asset_id: UUID
    asset_code: str | None = None
    organization_id: UUID | None = None
    write_to_asset: bool = True
    auto_cross_ref: bool = True


class CrossRefRequest(BaseModel):
    asset_id: UUID
    organization_id: UUID | None = None
    days: int = 30


class AnomalyScanRequest(BaseModel):
    meter_id: UUID
    organization_id: UUID | None = None


class AnomalyActionRequest(BaseModel):
    action: str  # acknowledge | monitor | mark_expected
    reason: str | None = None


class MonthlyReportRequest(BaseModel):
    site_id: UUID
    report_month: date | None = None
    organization_id: UUID | None = None
    export_pdf: bool = True


class MeterPullRequest(BaseModel):
    meter_id: UUID
    window_start: datetime
    window_end: datetime
    organization_id: UUID | None = None


class OccupancyLogRequest(BaseModel):
    site_id: UUID
    occupancy_state: str
    changed_at: datetime | None = None
    notes: str | None = None
    organization_id: UUID | None = None
    source: str = "manual"


class QueueDecisionRequest(BaseModel):
    decision: str
    pm_notes: str | None = None
    prepare_email_handoff: bool = True
