"""
svc-ingestion/src/data_agents/parts_agent.py

Task 4.6 — Parts Agent (Layer 5 specialist).

SQL: SELECT * FROM parts WHERE stock_on_hand < minimum_allowed_stock

EL-5.BOUND:
  - stock_on_hand >= 0 (negative stock rejected)
  - minimum_allowed_stock > 0
  - part_code not null
  - supplier field present

AGGREGATE: claude-haiku-4-5, N=3, vote on: critical | severe | low | ok
EL-5.CONSTRAIN threshold: 0.78
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

_RULES_PATH = Path(__file__).parent / "rules" / "parts_rules.yaml"
_MODEL = "claude-haiku-4-5"
_CONFIDENCE_THRESHOLD = 0.78
_ALLOWED_STATUSES = ["critical", "severe", "low", "ok"]


# ── EL-5.BOUND schema ─────────────────────────────────────────────────────────


class PartsBoundRow(BaseModel):
    """Pydantic schema for EL-5.BOUND validation of parts rows."""

    part_code: str
    stock_on_hand: int
    minimum_allowed_stock: int
    supplier: str | None = None
    below_25_pct_minimum: bool = False
    linked_asset_has_highest_wo: bool = False

    @field_validator("stock_on_hand")
    @classmethod
    def stock_non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"stock_on_hand must be >= 0, got {v}")
        return v

    @field_validator("minimum_allowed_stock")
    @classmethod
    def minimum_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError(f"minimum_allowed_stock must be > 0, got {v}")
        return v

    @field_validator("part_code")
    @classmethod
    def part_code_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("part_code must not be empty")
        return v.strip()


# ── Agent function ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are a spare parts inventory analyst for a CAFM (facilities management) system.
Given parts that are at or below minimum stock levels, determine reorder urgency.

Rules:
- critical: stock = 0 or critically low with linked high-priority WO
- severe: stock < 25% of minimum, or part linked to asset with major open WO
- low: stock below minimum but not severely low
- ok: stock at or above minimum (should not appear in this dataset)

Known context: 19 parts are currently below minimum stock. MOTOR-8HP is at 0.

Return ONLY a JSON object. No extra text, no markdown fences.
"""


async def run_parts_agent(
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> AgentResult:
    """
    Run the Parts Agent determinism cycle for below-minimum stock parts.
    """
    with tracer.start_as_current_span("data_agent.parts") as span:
        span.set_attribute("cafm.agent_id", "parts-agent")

        rows = await _fetch_parts_rows(session)

        cycle = AgentDeterminismCycle(
            allowed_statuses=_ALLOWED_STATUSES,
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            model=_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            rules_yaml_path=_RULES_PATH,
            bound_schema=PartsBoundRow,
            vote_field="status",
        )

        return await cycle.run(
            raw_rows=rows,
            agent_id="parts-agent",
            domain="parts",
            context={
                "question": (
                    "What is the overall reorder urgency across these below-minimum "
                    "stock parts, considering linked asset criticality?"
                )
            },
            session=session,
            client=client,
        )


async def _fetch_parts_rows(
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Fetch parts below minimum stock with linked asset WO context."""
    result = await session.execute(
        text(
            """
            SELECT
                sp.part_code,
                sp.stock_on_hand,
                sp.minimum_allowed_stock,
                sp.supplier,
                sp.bom_group_name,
                -- Flag: stock < 25% of minimum
                (sp.stock_on_hand < sp.minimum_allowed_stock * 0.25) AS below_25_pct_minimum,
                -- Flag: linked asset has open Highest priority WO
                EXISTS (
                    SELECT 1 FROM plenum_cafm.work_order_parts wop
                    JOIN plenum_cafm.work_orders wo ON wo.id = wop.wo_id
                    WHERE wop.part_id = sp.id
                      AND wo.priority = 'Highest'
                      AND wo.status = 'Open'
                ) AS linked_asset_has_highest_wo
            FROM plenum_cafm.spare_parts sp
            WHERE sp.stock_on_hand < sp.minimum_allowed_stock
            ORDER BY sp.stock_on_hand ASC
            """
        )
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]
