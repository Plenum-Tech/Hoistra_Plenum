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


class ChillerDesignRequest(BaseModel):
    """What the chiller was sold as. design_kw_per_rt is the figure every reading is judged against."""
    design_kw_per_rt: float = Field(..., gt=0, description="e.g. 0.68")
    design_capacity_rt: float | None = Field(None, gt=0)
    design_ambient_c: float | None = None
    design_chw_supply_c: float | None = None
    building_id: UUID | None = None
    organization_id: UUID | None = None
    source: str | None = Field(None, description="datasheet | commissioning | manual")
    notes: str | None = None


class ChillerReadingsRequest(BaseModel):
    """BMS / sub-meter samples: reading_at, kw_input, and cooling_load_rt or cooling_load_kw;
    ambient_c, chw_supply_c, chw_return_c optional."""
    readings: list[dict[str, Any]]
    building_id: UUID | None = None
    organization_id: UUID | None = None
    source: str = "bms"


class ChillerScanRequest(BaseModel):
    asset_id: UUID | None = Field(None, description="One chiller; omit to scan every chiller with a design figure.")
    organization_id: UUID | None = None
    window_days: int = Field(14, ge=1, le=90)


class DegreeDaysRequest(BaseModel):
    """Monthly heating/cooling degree days for a building: [{month: 'YYYY-MM-01', hdd, cdd}]."""
    building_id: UUID
    months: list[dict[str, Any]]
    base_temp_c: float = 15.5
    station: str | None = None
    source: str = "manual"
    organization_id: UUID | None = None


class BmsTrendsRequest(BaseModel):
    """Zone samples: [{recorded_at, zone, heating_pct, cooling_pct, zone_temp_c?, setpoint_c?, asset_id?}]."""
    building_id: UUID
    samples: list[dict[str, Any]]
    source: str = "bms"
    organization_id: UUID | None = None


class RatingComputeRequest(BaseModel):
    building_id: UUID
    scheme: str = Field(..., description="energy_star | ll97")
    months: int = Field(12, ge=3, le=24)
    year: int | None = Field(None, description="LL97 compliance year; defaults to the window's end year")
    organization_id: UUID | None = None
