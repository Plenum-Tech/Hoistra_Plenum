"""
svc-ingestion/src/data_agents/base_data_agent.py

Task 4.1 — AgentResult contract.

AgentResult is the typed object that every specialist data agent (Layer 5)
returns to Layer 6. Layer 6 ONLY receives AgentResult objects — never raw
data or strings.

Every AgentResult must have:
  - A valid audit_id written to agent_audit_log BEFORE this object is returned
  - requires_human_review set correctly (gates Layer 6 EL-6.BOUND)
  - All 3 individual run outputs preserved in .runs (for audit + replay)
"""

from __future__ import annotations

from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class SingleRunResult(BaseModel):
    """Output from one of the N=3 concurrent Claude runs."""

    run_number: int                    # 1, 2, or 3
    status: str                        # agent-specific status enum value
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                     # ≤ 60 words
    raw_response: str = ""             # full Claude response text (for audit)
    valid: bool = True                 # False if EL-5.AGG rejected this run
    failure_reason: str = ""           # populated when valid=False

    @field_validator("confidence")
    @classmethod
    def round_confidence(cls, v: float) -> float:
        return round(v, 3)

    @field_validator("reasoning")
    @classmethod
    def truncate_reasoning(cls, v: str) -> str:
        """Enforce ≤ 60 words — truncate silently if over."""
        words = v.split()
        if len(words) > 60:
            return " ".join(words[:60]) + "…"
        return v


class AgentResult(BaseModel):
    """
    The typed contract between Layer 5 specialist agents and Layer 6 orchestration.

    Produced by: AgentDeterminismCycle.run()
    Consumed by: analysis/orchestrator.py (Layer 6 EL-6.BOUND checks this)

    IMPORTANT: audit_id must be written to agent_audit_log before this is returned.
    Layer 6 EL-6.BOUND verifies audit_ids are resolvable.
    """

    agent_id: str
    domain: Literal["asset", "wo", "pm", "parts", "inspection"]

    # ── Primary result ────────────────────────────────────────────────────────
    status: str                        # domain-specific: operational|at_risk|critical etc.
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str                     # max 60 words — from winning run

    # ── Determinism audit ─────────────────────────────────────────────────────
    runs: list[SingleRunResult] = Field(default_factory=list)  # all 3 run outputs
    runs_agreed: int = Field(ge=0, le=3)  # how many valid runs agreed on winner status

    # ── Hard rule overrides ───────────────────────────────────────────────────
    hard_rules_fired: list[str] = Field(default_factory=list)  # YAML rule names that fired

    # ── Review gate ───────────────────────────────────────────────────────────
    requires_human_review: bool = False

    # ── Raw data reference ────────────────────────────────────────────────────
    raw_data: dict[str, Any] = Field(default_factory=dict)  # validated SQL rows

    # ── Audit trail ───────────────────────────────────────────────────────────
    audit_id: UUID = Field(default_factory=uuid4)

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
