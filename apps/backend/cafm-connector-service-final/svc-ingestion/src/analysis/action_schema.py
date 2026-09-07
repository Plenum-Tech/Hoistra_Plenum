"""
svc-ingestion/src/analysis/action_schema.py

Task 5.1 — CMSDecision Pydantic schema.

The typed output of Layer 6 orchestration analysis.
Produced by: analysis/orchestrator.py (Layer 6 EL-6.CONSTRAIN)
Consumed by: Layer 7 (svc-query) for query answers, WO auto-raise, parts alerts.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class SingleOrchestratorRun(BaseModel):
    """Output from one of the N=3 concurrent Layer 6 Sonnet runs."""

    run_number: int
    action: str                    # create_wo | order_part | alert_critical | no_action
    priority: str                  # low | medium | high | critical
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                 # ≤ 60 words
    contributing_agents: list[str] = Field(default_factory=list)
    raw_response: str = ""
    valid: bool = True
    failure_reason: str = ""

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 3)

    @field_validator("reasoning")
    @classmethod
    def truncate_reasoning(cls, v: str) -> str:
        words = v.split()
        if len(words) > 60:
            return " ".join(words[:60]) + "…"
        return v


class CMSDecision(BaseModel):
    """
    The typed output of Layer 6 orchestration analysis.

    Produced after Bound → Aggregate → Vote → Constrain cycle.
    audit_id written to orchestration_audit_log before this is returned.

    Layer 7 checks:
      - action == create_wo AND confidence >= 0.85 → auto-raise work order
      - action == alert_critical → always human_review (EL-6.CONSTRAIN gate 2)
      - action == order_part → parts alert flag
    """

    action: Literal["create_wo", "order_part", "alert_critical", "no_action", "human_review"]
    asset_code: str
    priority: Literal["low", "medium", "high", "critical"]
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                         # max 60 words
    contributing_agents: list[str] = Field(default_factory=list)
    runs_agreed: int = Field(ge=0, le=3)

    # ── Audit fields ───────────────────────────────────────────────────────────
    audit_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # ── Individual run outputs (preserved for audit) ───────────────────────────
    runs: list[SingleOrchestratorRun] = Field(default_factory=list)

    # ── Safety metadata ────────────────────────────────────────────────────────
    safety_passed: bool = True
    hard_rules_fired: list[str] = Field(default_factory=list)
    agent_results_summary: dict[str, Any] = Field(default_factory=dict)

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 3)

    @field_validator("reasoning")
    @classmethod
    def truncate_reasoning(cls, v: str) -> str:
        words = v.split()
        if len(words) > 60:
            return " ".join(words[:60]) + "…"
        return v
