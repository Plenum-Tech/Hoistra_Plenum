"""Strict request types for source-backed Homework energy reads."""
from datetime import date, datetime
from pydantic import BaseModel, ConfigDict, Field, model_validator


class EnergyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    building_id: str = Field(min_length=1, max_length=100)
    period_start: date
    period_end: date
    section_id: str | None = Field(default=None, max_length=100)
    meter_id: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def ordered_period(self):
        if self.period_end < self.period_start:
            raise ValueError("period_end must not precede period_start")
        if (self.period_end - self.period_start).days >= 366:
            raise ValueError("period is limited to one year")
        return self


class AnomalyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    building_id: str = Field(min_length=1, max_length=100)
    as_of: date
    meter_id: str | None = Field(default=None, max_length=160)
    section_id: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def bounded_as_of(self):
        from datetime import date as date_type
        if self.as_of > date_type.today():
            raise ValueError("as_of cannot be in the future")
        return self


class AnomalyAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    anomaly_id: str = Field(min_length=1, max_length=100)
    action: str = Field(pattern="^(acknowledge|monitor|mark_expected)$")
    reason: str | None = Field(default=None, max_length=1000)
    evidence_source_ids: list[str] = Field(default_factory=list,max_length=20)

    @model_validator(mode="after")
    def expected_requires_reason(self):
        if self.action == "mark_expected" and (not (self.reason or "").strip() or not self.evidence_source_ids):
            raise ValueError("reason and source evidence are required when marking an anomaly expected")
        return self


class AnomalyList(BaseModel):
    model_config = ConfigDict(extra="forbid")
    building_id: str = Field(min_length=1, max_length=100)
    status: str = Field(default="open", pattern="^(open|acknowledged|monitoring|expected)$")


class GapScan(EnergyQuery):
    idempotency_key: str = Field(default="source-window",min_length=8,max_length=160)
    @model_validator(mode="after")
    def bounded_window(self):
        if (self.period_end-self.period_start).days >= 35:
            raise ValueError("Gap scans are limited to 35 inclusive days")
        return self


class GapRetry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    gap_id: str = Field(min_length=1,max_length=160)
    test_clock: datetime

    @model_validator(mode="after")
    def aware(self):
        if self.test_clock.tzinfo is None:
            raise ValueError("An explicit timezone is required")
        return self


class MonthlyReport(EnergyQuery):
    @model_validator(mode="after")
    def full_month(self):
        from calendar import monthrange
        if self.period_start.day != 1 or self.period_end != date(self.period_start.year,
                self.period_start.month,monthrange(self.period_start.year,self.period_start.month)[1]):
            raise ValueError("Energy reports require one complete calendar month")
        return self


class ReportQuery(BaseModel):
    model_config=ConfigDict(extra="forbid")
    report_id: str=Field(min_length=1,max_length=160)
