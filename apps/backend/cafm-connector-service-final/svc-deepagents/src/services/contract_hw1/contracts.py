"""Strict model/API contracts for the Contract Performance profile."""
from datetime import date
from decimal import Decimal
from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PerformanceQuery(Contract):
    vendor_id: str = Field(min_length=1, max_length=160)
    period_start: date
    period_end: date
    building_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=160)

    @model_validator(mode="after")
    def full_month(self):
        from calendar import monthrange
        if (self.period_start.day != 1 or self.period_end.year != self.period_start.year
                or self.period_end.month != self.period_start.month
                or self.period_end.day != monthrange(self.period_start.year, self.period_start.month)[1]):
            raise ValueError("Scorecards require one complete calendar month")
        return self


class InvoiceQuery(Contract):
    vendor_id: str = Field(min_length=1, max_length=160)
    period_start: date
    period_end: date
    building_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    limit: int = Field(default=100, ge=1, le=500)

    @model_validator(mode="after")
    def valid_period(self):
        if self.period_start > self.period_end:
            raise ValueError("period_start must not follow period_end")
        return self


class ScorecardQuery(Contract):
    scorecard_id: str = Field(min_length=1, max_length=160)
    building_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)


class ConfigProposal(Contract):
    weights: dict[str, StrictInt]
    recall_window_days: int = Field(ge=0, le=365)
    score_cap: int = Field(ge=0, le=100)
    ppm_tolerance_days: int = Field(ge=0, le=365)
    cost_variance_alert_pct: Decimal = Field(ge=0, le=1000)
    cost_variance_alert_jobs: int = Field(ge=1, le=100)
    invoice_flag_adversary_gbp: Decimal = Field(ge=0, le=1000000)
    reason: str = Field(min_length=3, max_length=1000)

    @model_validator(mode="after")
    def approved_weight_set(self):
        from ..homework_domains.calculations import DEFAULT_WEIGHTS
        if (set(self.weights) != set(DEFAULT_WEIGHTS) or sum(self.weights.values()) != 100
                or any(value < 0 or value > 100 for value in self.weights.values())):
            raise ValueError("Exactly five approved weights totaling 100 are required")
        return self


class ConfigConfirmation(Contract):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    expected_version: int = Field(ge=1)
    idempotency_key: str = Field(min_length=8, max_length=160)


class ToolRequest(Contract):
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


class Ask(Contract):
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = Field(default=None, max_length=160)
