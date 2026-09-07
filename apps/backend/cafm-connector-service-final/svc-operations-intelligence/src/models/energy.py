"""ORM models for Feature C — Energy Intelligence Engine."""
from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import Boolean, Date, DateTime, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class EnergyMeter(Base):
    __tablename__ = "energy_meters"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meter_type: Mapped[str] = mapped_column(String(20), nullable=False)
    mpan: Mapped[str | None] = mapped_column(String(40))
    mprn: Mapped[str | None] = mapped_column(String(40))
    dcc_device_id: Mapped[str | None] = mapped_column(String(80))
    tariff_gbp_per_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0.28"))
    carbon_kg_per_kwh: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=Decimal("0.207"))
    is_sub_meter: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    asset_type_benchmark_kwh: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Per-meter metadata; simulate=true marks a meter fed by the demo simulator rather
    # than a real DCC feed, so a reading's provenance is never ambiguous.
    raw_metadata: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeterReading(Base):
    __tablename__ = "meter_readings"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    reading_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=30)
    consumption_kwh: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="dcc")
    quality_flag: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MeterReadingGap(Base):
    __tablename__ = "meter_reading_gaps"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meter_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    gap_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    gap_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    missing_periods: Mapped[int] = mapped_column(Integer, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    last_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class BuildingEnergyProfile(Base):
    __tablename__ = "building_energy_profiles"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    building_type: Mapped[str] = mapped_column(String(80), nullable=False, default="office")
    gia_m2: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tm46_electricity_benchmark: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    tm46_gas_benchmark: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EuiSnapshot(Base):
    __tablename__ = "eui_snapshots"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    meter_type: Mapped[str] = mapped_column(String(20), nullable=False)
    total_kwh: Mapped[Decimal] = mapped_column(Numeric(16, 4), nullable=False)
    gia_m2: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    eui_kwh_per_m2: Mapped[Decimal] = mapped_column(Numeric(14, 6), nullable=False)
    benchmark_kwh_per_m2: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    deviation_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    excess_kwh: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    financial_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tariff_used: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AssetConditionScore(Base):
    __tablename__ = "asset_condition_scores"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    asset_code: Mapped[str | None] = mapped_column(String(120))
    condition_score: Mapped[int] = mapped_column(Integer, nullable=False)
    llm_label: Mapped[str | None] = mapped_column(String(80))
    provenance_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    source_report_ref: Mapped[str | None] = mapped_column(Text)
    deduced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    written_to_asset: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyRecommendation(Base):
    __tablename__ = "energy_recommendations"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    recommendation_type: Mapped[str] = mapped_column(String(40), nullable=False)
    condition_score: Mapped[int | None] = mapped_column(Integer)
    consumption_vs_benchmark_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    repair_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    replace_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyAnomaly(Base):
    __tablename__ = "energy_anomalies"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    meter_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    asset_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    anomaly_type: Mapped[str] = mapped_column(String(60), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metric_pct: Mapped[Decimal | None] = mapped_column(Numeric(10, 4))
    excess_kwh: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    annualised_excess_kwh: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    financial_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    tariff_used: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    status: Mapped[str] = mapped_column(String(40), nullable=False, default="open")
    pm_action: Mapped[str | None] = mapped_column(String(40))
    pm_reason: Mapped[str | None] = mapped_column(Text)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    queue_item_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EnergyMonthlyReport(Base):
    __tablename__ = "energy_monthly_reports"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    report_month: Mapped[date] = mapped_column(Date, nullable=False)
    eui_trend_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    anomalies_ranked_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    total_excess_cost_gbp: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    carbon_exposure_kg: Mapped[Decimal | None] = mapped_column(Numeric(16, 4))
    report_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    pdf_blob_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SiteOccupancyLog(Base):
    """Logged occupancy changes — suppress baseline_drift when present in window."""

    __tablename__ = "site_occupancy_logs"
    __table_args__ = {"schema": SCHEMA}

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    organization_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    site_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    occupancy_state: Mapped[str] = mapped_column(String(40), nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
