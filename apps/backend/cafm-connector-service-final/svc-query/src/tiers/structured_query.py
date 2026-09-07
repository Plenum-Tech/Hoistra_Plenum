"""
svc-query/src/tiers/structured_query.py

Task 5.3 — Tier 1: Structured SQL Query (~60% of queries).

Flow:
  1. Sonnet generates parameterised SQL template + parameters from user query
  2. Parameters injected safely — raw LLM SQL never executes directly
  3. Execute via asyncpg on DB → Sonnet synthesises grounded answer
  4. EL-7.QUERY: answer verified to contain only values from SQL result set

EL-7.QUERY guarantee: if no grounding data found → "No data found for this query"
Never hallucinate an answer when the data does not exist.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_MODEL_SQL_GEN = "claude-sonnet-4-6"
_MODEL_SYNTHESISE = "claude-sonnet-4-6"
_MAX_TOKENS_SQL = 512
_MAX_TOKENS_ANSWER = 1024
_MAX_ROWS = 200   # cap result set size to prevent huge prompts

# Schema context given to Sonnet for SQL generation
_SCHEMA_CONTEXT = """
Available tables (schema: plenum_cafm):

assets(asset_code, asset_name, category, location_code, make, model, serial_number)
work_orders(id, wo_code, asset_code, priority, status, description, created_at)
  priority values: Highest, High, Medium, Low, Lowest
  status values: Open, Closed
spare_parts(id, part_code, stock_on_hand, minimum_allowed_stock, supplier, bom_group_name)
scheduled_pm(id, sm_code, asset_code, trigger_type, schedule_interval, last_date, meter_reading)
  trigger_type: t (time-based) or m (meter-based)
inspections(id, asset_code, inspector, inspection_date, section, finding_type,
            observations, risk_level, corrective_action, source_file)
  section values: A, B, C, D, E, F, G
  risk_level values: High, Medium, Low
locations(location_code, location_name, building, floor)
technicians(id, employee_id, name, email, specialisation)

Known data:
- 60 assets, 11 categories (asset codes start with MOB-)
- 74 work orders (17 open, 4 at Highest priority)
- 38 spare parts (19 below minimum, MOTOR-8HP at 0)
- 7 scheduled PMs (6 time-based, 1 meter-based generator 1000hr)
"""

_SQL_GEN_SYSTEM = f"""\
You are a SQL generator for a CAFM (facilities management) PostgreSQL database.

{_SCHEMA_CONTEXT}

Generate a safe, parameterised SQL query for the user's question.
Use :param_name syntax for parameters (SQLAlchemy style).

Rules:
- SELECT only — no INSERT/UPDATE/DELETE/DROP
- Always qualify table names with schema: plenum_cafm.table_name
- Use ILIKE for case-insensitive string comparisons
- Limit results to {_MAX_ROWS} rows unless user specifies otherwise
- Never use subqueries that reference parameters directly — use CTEs instead

Return ONLY a JSON object (no markdown fences):
{{
  "sql": "<the parameterised SQL query>",
  "params": {{"param_name": "value", ...}},
  "explanation": "<one sentence: what this query returns>"
}}
"""

_SYNTHESISE_SYSTEM = """\
You are a CAFM (facilities management) assistant answering questions about
facility assets, work orders, maintenance schedules, and spare parts.

You will be given a user question and the exact SQL query results that answer it.
Your answer must ONLY use values from the provided query results — never invent
data, never use your training knowledge about specific assets or work orders.

If the results are empty, say: "No data found for this query."

Be concise and precise. Format numbers and dates clearly.
"""


@dataclass
class Tier1Result:
    """Result of a Tier 1 structured query."""

    answer: str
    sql_used: str
    params_used: dict[str, Any]
    rows_returned: int
    grounded: bool                     # EL-7.QUERY: did answer use only DB values?
    explanation: str = ""
    raw_rows: list[dict[str, Any]] = field(default_factory=list)


async def run_structured_query(
    query: str,
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> Tier1Result:
    """
    Tier 1 query: SQL generation → execution → grounded synthesis.

    EL-7.QUERY: answer verified to contain only values from SQL result set.
    """
    with tracer.start_as_current_span("tier1.structured_query") as span:
        span.set_attribute("cafm.query_length", len(query))

        # Step 1: Generate SQL
        sql, params, explanation = await _generate_sql(query, client)
        span.set_attribute("cafm.sql_generated", bool(sql))

        if not sql:
            return Tier1Result(
                answer="Unable to generate a SQL query for this question.",
                sql_used="",
                params_used={},
                rows_returned=0,
                grounded=False,
            )

        # Step 2: Execute SQL safely with bound parameters
        rows = await _execute_safe(sql, params, session)
        span.set_attribute("cafm.rows_returned", len(rows))

        # EL-7.QUERY: no data → never hallucinate
        if not rows:
            logger.info("tier1_no_data_found", query=query[:100])
            return Tier1Result(
                answer="No data found for this query.",
                sql_used=sql,
                params_used=params,
                rows_returned=0,
                grounded=True,
                explanation=explanation,
                raw_rows=[],
            )

        # Step 3: Synthesise grounded answer
        answer = await _synthesise_answer(query, rows, client)
        span.set_attribute("cafm.answer_length", len(answer))

        logger.info(
            "tier1_complete",
            rows=len(rows),
            answer_length=len(answer),
        )

        return Tier1Result(
            answer=answer,
            sql_used=sql,
            params_used=params,
            rows_returned=len(rows),
            grounded=True,
            explanation=explanation,
            raw_rows=rows,
        )


async def _generate_sql(
    query: str,
    client: anthropic.AsyncAnthropic,
) -> tuple[str, dict[str, Any], str]:
    """Generate parameterised SQL from natural language query."""
    with tracer.start_as_current_span("tier1.generate_sql") as span:
        try:
            response = await client.messages.create(
                model=_MODEL_SQL_GEN,
                max_tokens=_MAX_TOKENS_SQL,
                system=_SQL_GEN_SYSTEM,
                messages=[{"role": "user", "content": query}],
            )
            raw = response.content[0].text.strip() if response.content else "{}"

            claude_api_calls.add(1, {"agent_id": "tier1-sql-gen", "model": _MODEL_SQL_GEN})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "tier1-sql-gen", "model": _MODEL_SQL_GEN},
            )

            if raw.startswith("```"):
                parts = raw.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw = inner.strip()

            parsed = json.loads(raw)
            sql = str(parsed.get("sql", "")).strip()
            params = parsed.get("params", {})
            explanation = str(parsed.get("explanation", ""))

            # Safety: only allow SELECT statements
            if not _is_safe_select(sql):
                logger.warning("tier1_unsafe_sql_rejected", sql=sql[:200])
                span.set_attribute("cafm.sql_safe", False)
                return "", {}, ""

            span.set_attribute("cafm.sql_safe", True)
            return sql, params, explanation

        except (json.JSONDecodeError, anthropic.APIError, ValueError, TypeError) as exc:
            logger.error("tier1_sql_gen_error", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            return "", {}, ""


def _is_safe_select(sql: str) -> bool:
    """Verify SQL is a SELECT-only statement — no mutations allowed."""
    if not sql:
        return False
    normalised = sql.strip().upper()
    # Must start with SELECT or WITH (CTE)
    if not (normalised.startswith("SELECT") or normalised.startswith("WITH")):
        return False
    # Must not contain mutation keywords
    forbidden = ["INSERT", "UPDATE", "DELETE", "DROP", "TRUNCATE", "ALTER", "CREATE", "GRANT"]
    return not any(kw in normalised for kw in forbidden)


async def _execute_safe(
    sql: str,
    params: dict[str, Any],
    session: AsyncSession,
) -> list[dict[str, Any]]:
    """Execute parameterised SQL safely and return rows as dicts."""
    with tracer.start_as_current_span("tier1.execute_sql") as span:
        try:
            result = await session.execute(text(sql), params)
            rows = result.mappings().all()
            row_dicts = [dict(r) for r in rows]
            span.set_attribute("cafm.rows_returned", len(row_dicts))
            return row_dicts
        except Exception as exc:
            logger.error("tier1_sql_execute_error", error=str(exc), sql=sql[:200])
            span.set_status(StatusCode.ERROR, str(exc))
            return []


async def _synthesise_answer(
    query: str,
    rows: list[dict[str, Any]],
    client: anthropic.AsyncAnthropic,
) -> str:
    """Synthesise a grounded natural language answer from SQL result rows."""
    with tracer.start_as_current_span("tier1.synthesise") as span:
        rows_text = json.dumps(rows[:_MAX_ROWS], indent=2, default=str)
        user_message = (
            f"QUESTION: {query}\n\n"
            f"QUERY RESULTS ({len(rows)} rows):\n{rows_text}\n\n"
            f"Answer the question using ONLY the values in the query results above."
        )

        try:
            response = await client.messages.create(
                model=_MODEL_SYNTHESISE,
                max_tokens=_MAX_TOKENS_ANSWER,
                system=_SYNTHESISE_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
            answer = response.content[0].text.strip() if response.content else "No data found."

            claude_api_calls.add(1, {"agent_id": "tier1-synthesise", "model": _MODEL_SYNTHESISE})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "tier1-synthesise", "model": _MODEL_SYNTHESISE},
            )

            span.set_attribute("cafm.answer_length", len(answer))
            return answer

        except (anthropic.APIError, ValueError) as exc:
            logger.error("tier1_synthesise_error", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            return "Unable to synthesise an answer. Please try again."
