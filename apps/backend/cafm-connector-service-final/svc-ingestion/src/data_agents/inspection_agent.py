"""
svc-ingestion/src/data_agents/inspection_agent.py

Task 4.7 — Inspection Agent (Layer 5 specialist).

Uses claude-sonnet-4-6 (not Haiku) — natural language findings require
more nuanced reasoning.

SQL: SELECT * FROM inspections
     WHERE corrective_action = true
     ORDER BY inspection_date DESC

EL-5.BOUND:
  - section in ['A','B','C','D','E','F','G']
  - finding_type in known enum
  - inspection_date not in the future
  - asset_code resolves in assets table

AGGREGATE: claude-sonnet-4-6, N=3, vote on: High | Medium | Low
EL-5.CONSTRAIN threshold: 0.85 (highest — compliance impact)
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from opentelemetry import trace
from pydantic import BaseModel, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

import anthropic
from cafm_shared.logging import get_logger
from data_agents.base_data_agent import AgentResult
from shared.agent_determinism import AgentDeterminismCycle

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_RULES_PATH = Path(__file__).parent / "rules" / "inspection_rules.yaml"
_MODEL = "claude-sonnet-4-6"
_CONFIDENCE_THRESHOLD = 0.85
_ALLOWED_STATUSES = ["High", "Medium", "Low"]
_VALID_SECTIONS = {"A", "B", "C", "D", "E", "F", "G"}


# ── EL-5.BOUND schema ─────────────────────────────────────────────────────────


class InspectionBoundRow(BaseModel):
    """Pydantic schema for EL-5.BOUND validation of inspection rows."""

    id: Any  # UUID
    asset_code: str | None = None
    section: str
    observations: str | None = None
    risk_level: str | None = None
    inspection_date: date | None = None
    corrective_action: bool = False

    @field_validator("section")
    @classmethod
    def section_in_enum(cls, v: str) -> str:
        if v not in _VALID_SECTIONS:
            raise ValueError(f"section '{v}' not in {_VALID_SECTIONS}")
        return v

    @field_validator("inspection_date")
    @classmethod
    def date_not_future(cls, v: date | None) -> date | None:
        if v is not None and v > date.today():
            raise ValueError(f"inspection_date {v} is in the future")
        return v


# ── Agent function ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are a site inspection risk analyst for a CAFM (facilities management) system.
Given inspection findings that have been flagged for corrective action, determine
the overall risk level based on the nature and severity of the findings.

Risk levels:
- High: immediate safety or regulatory risk, requires urgent corrective action
- Medium: significant issue that needs attention within the week
- Low: minor issue, can be addressed in normal maintenance schedule

Sections B (Erosion/Sediment Controls) and C (Pollution Prevention) are
particularly high-risk when flagged for corrective action.

Be conservative — when in doubt, assign the higher risk level.

Return ONLY a JSON object. No extra text, no markdown fences.
"""


async def run_inspection_agent(
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
    asset_code: str | None = None,
) -> AgentResult:
    """
    Run the Inspection Agent determinism cycle for flagged inspection findings.
    Optionally filtered to a specific asset_code.
    """
    with tracer.start_as_current_span("data_agent.inspection") as span:
        span.set_attribute("cafm.agent_id", "inspection-agent")
        if asset_code:
            span.set_attribute("cafm.asset_code", asset_code)

        rows = await _fetch_inspection_rows(session, asset_code)

        cycle = AgentDeterminismCycle(
            allowed_statuses=_ALLOWED_STATUSES,
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            model=_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            rules_yaml_path=_RULES_PATH,
            bound_schema=InspectionBoundRow,
            vote_field="status",
        )

        return await cycle.run(
            raw_rows=rows,
            agent_id="inspection-agent",
            domain="inspection",
            context={
                "question": (
                    "What is the risk level of these inspection findings "
                    "and do they require corrective action?"
                )
            },
            session=session,
            client=client,
            asset_code=asset_code,
        )


async def _fetch_inspection_rows(
    session: AsyncSession,
    asset_code: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch inspection findings flagged for corrective action."""
    where = "i.corrective_action = true"
    params: dict[str, Any] = {}
    if asset_code:
        where += " AND i.asset_code = :asset_code"
        params["asset_code"] = asset_code

    result = await session.execute(
        text(
            f"""
            SELECT
                i.id,
                i.asset_code,
                i.inspector,
                i.inspection_date,
                i.section,
                i.finding_type,
                i.observations,
                i.risk_level,
                i.corrective_action,
                i.source_file
            FROM plenum_cafm.inspections i
            WHERE {where}
            ORDER BY i.inspection_date DESC
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]
