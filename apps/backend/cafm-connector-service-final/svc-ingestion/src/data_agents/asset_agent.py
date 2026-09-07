"""
svc-ingestion/src/data_agents/asset_agent.py

Task 4.3 — Asset Agent (Layer 5 specialist).

SQL: SELECT * FROM assets LEFT JOIN work_orders USING(asset_code)
     WHERE asset_code = $1

EL-5.BOUND:
  - asset_code not null
  - category in known enum
  - location_code present

AGGREGATE: claude-haiku-4-5, N=3, vote on: operational | at_risk | critical
EL-5.CONSTRAIN threshold: 0.80
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

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

_RULES_PATH = Path(__file__).parent / "rules" / "asset_rules.yaml"
_MODEL = "claude-haiku-4-5"
_CONFIDENCE_THRESHOLD = 0.80
_ALLOWED_STATUSES = ["operational", "at_risk", "critical"]

KNOWN_CATEGORIES = [
    "Air Handler", "Boiler", "Chiller", "Cooling Tower", "Electrical Panel",
    "Elevator", "Fire Alarm", "Generator", "HVAC", "Lighting",
    "Plumbing", "Pump", "UPS", "Other",
]


# ── EL-5.BOUND schema ─────────────────────────────────────────────────────────


class AssetBoundRow(BaseModel):
    """Pydantic schema for EL-5.BOUND validation of asset rows."""

    asset_code: str
    category: str | None = None
    location_code: str | None = None

    @field_validator("asset_code")
    @classmethod
    def asset_code_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("asset_code must not be empty")
        return v.strip()


# ── Agent function ─────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """\
You are an asset health analyst for a CAFM (facilities management) system.
Given asset data including open work orders and last PM date, determine the
operational status of the asset.

Rules:
- operational: asset is functioning normally with no significant issues
- at_risk: asset has warning signs (overdue PM, multiple open WOs, ageing)
- critical: asset has immediate issues (Highest priority WOs, safety failures)

Return ONLY a JSON object. No extra text, no markdown fences.
"""


async def run_asset_agent(
    asset_code: str,
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> AgentResult:
    """
    Run the Asset Agent determinism cycle for a single asset.
    Fetches asset + work order data, runs EL-5.BOUND → AGG → VOTE → CONSTRAIN.
    """
    with tracer.start_as_current_span("data_agent.asset") as span:
        span.set_attribute("cafm.agent_id", "asset-agent")
        span.set_attribute("cafm.asset_code", asset_code)

        # Fetch data
        rows = await _fetch_asset_rows(asset_code, session)

        cycle = AgentDeterminismCycle(
            allowed_statuses=_ALLOWED_STATUSES,
            confidence_threshold=_CONFIDENCE_THRESHOLD,
            model=_MODEL,
            system_prompt=_SYSTEM_PROMPT,
            rules_yaml_path=_RULES_PATH,
            bound_schema=AssetBoundRow,
            vote_field="status",
        )

        return await cycle.run(
            raw_rows=rows,
            agent_id="asset-agent",
            domain="asset",
            context={
                "question": (
                    f"Given this asset's open WOs and last PM date, "
                    f"what is its operational status? Asset: {asset_code}"
                )
            },
            session=session,
            client=client,
            asset_code=asset_code,
        )


async def _fetch_asset_rows(
    asset_code: str,
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Fetch asset + open WO data for the given asset_code."""
    result = await session.execute(
        text(
            """
            SELECT
                a.asset_code,
                a.asset_name,
                a.category,
                a.location_code,
                a.make,
                a.model,
                COUNT(wo.id) FILTER (WHERE wo.status = 'Open') AS open_wo_count,
                MAX(CASE WHEN wo.priority = 'Highest' AND wo.status = 'Open'
                         THEN wo.priority END) AS has_highest_wo,
                STRING_AGG(DISTINCT wo.priority, ', ') FILTER (WHERE wo.status = 'Open')
                    AS open_wo_priorities
            FROM plenum_cafm.assets a
            LEFT JOIN plenum_cafm.work_orders wo USING (asset_code)
            WHERE a.asset_code = :asset_code
            GROUP BY a.asset_code, a.asset_name, a.category, a.location_code,
                     a.make, a.model
            """
        ),
        {"asset_code": asset_code},
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]
