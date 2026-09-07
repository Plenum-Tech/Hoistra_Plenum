"""
svc-ingestion/src/data_agents/wo_agent.py

Task 4.4 — WO Agent (Layer 5 specialist).

SQL: SELECT * FROM work_orders WHERE status = 'Open' ORDER BY priority DESC

EL-5.BOUND:
  - priority in [Highest, High, Medium, Low, Lowest]
  - status in [Open, Closed]
  - wo_code matches expected pattern
  - asset_code resolves in assets table

AGGREGATE: claude-haiku-4-5, N=3, vote on: escalate | monitor | routine
EL-5.CONSTRAIN threshold: 0.82
"""

from __future__ import annotations

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

_RULES_PATH = Path(__file__).parent / "rules" / "wo_rules.yaml"
_MODEL = "claude-haiku-4-5"
_CONFIDENCE_THRESHOLD = 0.82
_ALLOWED_STATUSES = ["escalate", "monitor", "routine"]
_VALID_PRIORITIES = {"Highest", "High", "Medium", "Low", "Lowest"}
_VALID_STATUSES = {"Open", "Closed"}


# ── EL-5.BOUND schema ─────────────────────────────────────────────────────────


class WOBoundRow(BaseModel):
    """Pydantic schema for EL-5.BOUND validation of work order rows."""

    wo_code: str
    priority: str
    status: str
    asset_code: str | None = None

    @field_validator("priority")
    @classmethod
    def priority_in_enum(cls, v: str) -> str:
        if v not in _VALID_PRIORITIES:
            raise ValueError(f"priority '{v}' not in {_VALID_PRIORITIES}")
        return v

    @field_validator("status")
    @classmethod
    def status_in_enum(cls, v: str) -> str:
        if v not in _VALID_STATUSES:
            raise ValueError(f"status '{v}' not in {_VALID_STATUSES}")
        return v

    @field_validator("wo_code")
    @classmethod
    def wo_code_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("wo_code must not be empty")
        return v.strip()


# ── Agent function ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are a work order triage analyst for a CAFM (facilities management) system.
Given open work orders with their priorities, ages, and asset categories,
determine the triage classification for the most critical work.

Rules:
- escalate: immediate action required (Highest priority, long-overdue, safety risk)
- monitor: needs attention within the week (High priority, moderate age)
- routine: can be handled in normal scheduling (Medium/Low priority, recent)

Return ONLY a JSON object. No extra text, no markdown fences.
"""


async def run_wo_agent(
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
    asset_code: str | None = None,
) -> AgentResult:
    """
    Run the WO Agent determinism cycle for open work orders.
    Optionally filtered to a specific asset_code.
    """
    with tracer.start_as_current_span("data_agent.wo") as span:
        span.set_attribute("cafm.agent_id", "wo-agent")
        if asset_code:
            span.set_attribute("cafm.asset_code", asset_code)

        rows = await _fetch_wo_rows(session, asset_code)

        cycle = AgentDeterminismCycle(
            allowed_statuses=_ALLOWED_STATUSES,
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            model=_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            rules_yaml_path=_RULES_PATH,
            bound_schema=WOBoundRow,
            vote_field="status",
        )

        return await cycle.run(
            raw_rows=rows,
            agent_id="wo-agent",
            domain="wo",
            context={
                "question": (
                    "Should these WOs be escalated given their age, priority, "
                    "and asset category? Return the triage for the overall set."
                )
            },
            session=session,
            client=client,
            asset_code=asset_code,
        )


async def _fetch_wo_rows(
    session: AsyncSession,
    asset_code: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch open work orders, optionally filtered by asset_code."""
    where = "wo.status = 'Open'"
    params: dict[str, Any] = {}
    if asset_code:
        where += " AND wo.asset_code = :asset_code"
        params["asset_code"] = asset_code

    result = await session.execute(
        text(
            f"""
            SELECT
                wo.id,
                wo.wo_code,
                wo.priority,
                wo.status,
                wo.asset_code,
                a.category AS asset_category,
                EXTRACT(DAY FROM (NOW() - wo.created_at))::int AS age_days
            FROM plenum_cafm.work_orders wo
            LEFT JOIN plenum_cafm.assets a USING (asset_code)
            WHERE {where}
            ORDER BY
                CASE wo.priority
                    WHEN 'Highest' THEN 1
                    WHEN 'High' THEN 2
                    WHEN 'Medium' THEN 3
                    WHEN 'Low' THEN 4
                    WHEN 'Lowest' THEN 5
                    ELSE 6
                END
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]
