"""
svc-ingestion/src/data_agents/pm_agent.py

Task 4.5 — PM Agent (Layer 5 specialist).

SQL: SELECT sm.*, a.asset_code, a.category
     FROM scheduled_pm sm JOIN assets a USING(asset_code)

EL-5.BOUND:
  - trigger_type in ['t', 'm']
  - schedule_interval is positive integer
  - last_date is valid date and NOT in the future
  - for meter trigger: meter_reading may be null (handled by hard rule)

AGGREGATE: claude-haiku-4-5, N=3, vote on: overdue | due_soon | ok
EL-5.CONSTRAIN threshold: 0.75
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

_RULES_PATH = Path(__file__).parent / "rules" / "pm_rules.yaml"
_MODEL = "claude-haiku-4-5"
_CONFIDENCE_THRESHOLD = 0.75
_ALLOWED_STATUSES = ["overdue", "due_soon", "ok"]
_VALID_TRIGGER_TYPES = {"t", "m"}


# ── EL-5.BOUND schema ─────────────────────────────────────────────────────────


class PMBoundRow(BaseModel):
    """Pydantic schema for EL-5.BOUND validation of scheduled PM rows."""

    sm_code: str
    trigger_type: str
    schedule_interval: int
    asset_code: str
    is_overdue: bool = False
    meter_reading: float | None = None
    linked_part_stock: int | None = None

    @field_validator("trigger_type")
    @classmethod
    def trigger_type_valid(cls, v: str) -> str:
        if v not in _VALID_TRIGGER_TYPES:
            raise ValueError(f"trigger_type '{v}' must be one of {_VALID_TRIGGER_TYPES}")
        return v

    @field_validator("schedule_interval")
    @classmethod
    def interval_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"schedule_interval must be positive, got {v}")
        return v

    @field_validator("sm_code")
    @classmethod
    def sm_code_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("sm_code must not be empty")
        return v.strip()


# ── Agent function ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are a preventive maintenance analyst for a CAFM (facilities management) system.
Given scheduled PM data including trigger type (time or meter-based) and last
completion date, determine the PM status.

Rules:
- overdue: PM was due before today and has not been completed
- due_soon: PM is due within the next 14 days
- ok: PM is not due for more than 14 days

IMPORTANT: For time-based PMs (trigger_type='t'), date arithmetic determines
overdue status with certainty. For meter-based PMs (trigger_type='m'), use
current meter reading vs. threshold.

Return ONLY a JSON object. No extra text, no markdown fences.
"""


async def run_pm_agent(
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
    asset_code: str | None = None,
) -> AgentResult:
    """
    Run the PM Agent determinism cycle for scheduled maintenance.
    Optionally filtered to a specific asset_code.
    """
    with tracer.start_as_current_span("data_agent.pm") as span:
        span.set_attribute("cafm.agent_id", "pm-agent")
        if asset_code:
            span.set_attribute("cafm.asset_code", asset_code)

        rows = await _fetch_pm_rows(session, asset_code)

        cycle = AgentDeterminismCycle(
            allowed_statuses=_ALLOWED_STATUSES,
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            model=_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            rules_yaml_path=_RULES_PATH,
            bound_schema=PMBoundRow,
            vote_field="status",
        )

        return await cycle.run(
            raw_rows=rows,
            agent_id="pm-agent",
            domain="pm",
            context={
                "question": (
                    "Is this PM overdue, due soon, or OK given the trigger type "
                    "and last completion date?"
                )
            },
            session=session,
            client=client,
            asset_code=asset_code,
        )


async def _fetch_pm_rows(
    session: AsyncSession,
    asset_code: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch scheduled PM data with computed overdue flag."""
    where = "1=1"
    params: dict[str, Any] = {}
    if asset_code:
        where += " AND sm.asset_code = :asset_code"
        params["asset_code"] = asset_code

    result = await session.execute(
        text(
            f"""
            SELECT
                sm.id,
                sm.sm_code,
                sm.trigger_type,
                sm.schedule_interval,
                sm.asset_code,
                a.category AS asset_category,
                sm.last_date,
                sm.meter_reading,
                -- Compute overdue for time-based PMs
                CASE
                    WHEN sm.trigger_type = 't' AND sm.last_date IS NOT NULL
                    THEN (sm.last_date + (sm.schedule_interval || ' months')::interval < NOW())
                    ELSE false
                END AS is_overdue,
                -- Linked critical part stock (for generator rule)
                MIN(sp.stock_on_hand) AS linked_part_stock
            FROM plenum_cafm.scheduled_pm sm
            JOIN plenum_cafm.assets a USING (asset_code)
            LEFT JOIN plenum_cafm.work_order_parts wop
                ON wop.wo_id IN (
                    SELECT id FROM plenum_cafm.work_orders
                    WHERE asset_code = sm.asset_code AND status = 'Open'
                )
            LEFT JOIN plenum_cafm.spare_parts sp ON sp.id = wop.part_id
            WHERE {where}
            GROUP BY sm.id, sm.sm_code, sm.trigger_type, sm.schedule_interval,
                     sm.asset_code, a.category, sm.last_date, sm.meter_reading
            """
        ),
        params,
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]
