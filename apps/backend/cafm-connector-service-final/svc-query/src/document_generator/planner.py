"""
svc-query/src/document_generator/planner.py

Task 5.6 — Document Planner.

Flow:
  1. Receive user request + intent (document_generate)
  2. Fetch relevant rows from DB (passed in by caller)
  3. N=3 Sonnet runs (concurrent asyncio.gather) each produce a DocumentPlan JSON
  4. EL-6.AGG equivalent: each run validated as valid DocumentPlan JSON before vote
  5. Vote on sections list — majority wins (most common section order)
  6. Return winning DocumentPlan for EL-7.DOC.PLAN validation

Rules:
- Claude produces JSON plan ONLY — never generates file content
- Every data_source must reference a real plenum_cafm table
- N=3 concurrent via asyncio.gather — never sequential
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import anthropic
from opentelemetry import trace

from cafm_shared.logging import get_logger
from cafm_shared.metrics import claude_api_calls, claude_tokens_used

from .schemas import DocumentPlan, DocumentSection, PlanningRunResult

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

_MODEL = "claude-sonnet-4-6"
_MAX_TOKENS = 1024
_N_RUNS = 3

_PLANNING_SYSTEM = """\
You are a document planning assistant for a CAFM (facilities management) system.

Given a user request and available data, produce a DocumentPlan JSON structure.
The plan describes WHAT to put in the document — the renderer will fetch the data.

Available tables in plenum_cafm schema:
  assets(asset_code, asset_name, category, location_code, make, model, serial_number)
  work_orders(id, wo_code, asset_code, priority, status, description, created_at)
  spare_parts(id, part_code, stock_on_hand, minimum_allowed_stock, supplier, bom_group_name)
  scheduled_pm(id, sm_code, asset_code, trigger_type, schedule_interval, last_date, meter_reading)
  inspections(id, asset_code, inspector, inspection_date, section, finding_type,
              observations, risk_level, corrective_action, source_file)
  locations(location_code, location_name, building, floor)
  technicians(id, employee_id, name, email, specialisation)

Section types allowed:
  summary_table   — tabular summary of rows
  schedule_grid   — date/interval grid (PM calendars)
  task_checklist  — checkbox list (WO packages)
  parts_table     — inventory/parts listing
  findings_list   — inspection findings
  kpi_summary     — key performance indicators
  signature_block — sign-off section
  free_text_header — Claude-generated text header (the ONLY place free text is allowed)

Return ONLY valid JSON matching this schema (no markdown fences):
{
  "document_type": "<pm_schedule|wo_report|wo_package|parts_reorder|inspection_template|asset_health_summary|maintenance_calendar|inspection_report|custom>",
  "title": "<descriptive title>",
  "generated_for": "<scope description>",
  "output_format": "<docx|xlsx|pdf>",
  "sections": [
    {
      "type": "<section_type>",
      "heading": "<section heading>",
      "data_source": "<table name or SQL WHERE clause>",
      "columns": ["col1", "col2"] or null,
      "highlight_rule": "<rule description>" or null,
      "sort_by": "<column>" or null,
      "limit": <integer> or null
    }
  ],
  "footer": {
    "generated_by": "CAFM AI Platform",
    "timestamp": "<ISO timestamp>",
    "audit_id": "<uuid>"
  },
  "data_sources_required": ["table1", "table2"]
}

Rules:
- Every data_source must reference a real plenum_cafm table
- Include a footer with generated_by, timestamp, and audit_id always
- Never invent asset codes, part codes, or work order numbers
- Be concise — maximum 8 sections per document
"""


async def run_document_planner(
    request: str,
    document_type: str,
    client: anthropic.AsyncAnthropic,
    context_rows: dict[str, list[dict[str, Any]]] | None = None,
) -> DocumentPlan | None:
    """
    Plan a document using N=3 concurrent Sonnet runs.

    Returns the winning DocumentPlan, or None if < 2 valid plans produced.
    The caller (validator.py) runs EL-7.DOC.PLAN on the returned plan.
    """
    with tracer.start_as_current_span("document.plan") as span:
        span.set_attribute("cafm.document_type", document_type)
        span.set_attribute("cafm.request_length", len(request))

        user_prompt = _build_planning_prompt(request, document_type, context_rows)

        # N=3 concurrent runs — never sequential
        tasks = [
            _single_planning_run(i + 1, user_prompt, client)
            for i in range(_N_RUNS)
        ]
        runs: list[PlanningRunResult] = await asyncio.gather(*tasks)

        valid_runs = [r for r in runs if r.valid and r.plan is not None]
        span.set_attribute("cafm.plan_runs_valid", len(valid_runs))

        logger.info(
            "document_planning_complete",
            document_type=document_type,
            valid_runs=len(valid_runs),
            total_runs=_N_RUNS,
        )

        if len(valid_runs) < 2:
            logger.warning(
                "document_planning_insufficient_valid_runs",
                valid_runs=len(valid_runs),
                document_type=document_type,
            )
            span.set_attribute("cafm.plan_runs_agreed", 0)
            return None

        # Vote on structure — pick plan with most common section order
        winner = _vote_on_plan(valid_runs)
        section_types = [s.type for s in winner.sections]
        span.set_attribute("cafm.sections_count", len(winner.sections))
        span.set_attribute("cafm.plan_runs_agreed", len(valid_runs))
        span.set_attribute("cafm.winner_section_types", ",".join(section_types))

        return winner


async def _single_planning_run(
    run_number: int,
    user_prompt: str,
    client: anthropic.AsyncAnthropic,
) -> PlanningRunResult:
    """One planning run — validated as DocumentPlan JSON before returning."""
    with tracer.start_as_current_span("document.plan.run") as span:
        span.set_attribute("cafm.run_number", run_number)
        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_PLANNING_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = response.content[0].text.strip() if response.content else "{}"

            claude_api_calls.add(1, {"agent_id": "doc-planner", "model": _MODEL})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "doc-planner", "model": _MODEL},
            )

            # Strip markdown fences if present
            if raw.startswith("```"):
                parts = raw.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw = inner.strip()

            parsed = json.loads(raw)

            # EL-6.AGG equivalent: validate as DocumentPlan
            plan = DocumentPlan.model_validate(parsed)

            # Ensure footer always has required fields
            if "generated_by" not in plan.footer:
                plan.footer["generated_by"] = "CAFM AI Platform"
            if "timestamp" not in plan.footer:
                plan.footer["timestamp"] = datetime.now(timezone.utc).isoformat()
            if "audit_id" not in plan.footer:
                plan.footer["audit_id"] = str(uuid4())

            span.set_attribute("cafm.run_valid", True)
            return PlanningRunResult(
                run_number=run_number,
                plan=plan,
                raw_response=raw,
                valid=True,
            )

        except Exception as exc:
            logger.warning(
                "document_planning_run_failed",
                run_number=run_number,
                error=str(exc),
            )
            span.set_attribute("cafm.run_valid", False)
            return PlanningRunResult(
                run_number=run_number,
                raw_response="",
                valid=False,
                failure_reason=str(exc),
            )


def _vote_on_plan(valid_runs: list[PlanningRunResult]) -> DocumentPlan:
    """
    Vote on document structure — return the plan with the most comprehensive
    section list. Tiebreak: most sections wins (most complete structure).
    """
    assert valid_runs  # caller guarantees >= 2 valid runs

    # Count occurrences of each section-type tuple (the structure fingerprint)
    from collections import Counter
    fingerprints = Counter(
        tuple(s.type for s in r.plan.sections)  # type: ignore[union-attr]
        for r in valid_runs
    )
    # Most common structure fingerprint
    winner_fp = fingerprints.most_common(1)[0][0]

    # Find the plan matching the winning fingerprint
    for run in valid_runs:
        fp = tuple(s.type for s in run.plan.sections)  # type: ignore[union-attr]
        if fp == winner_fp:
            return run.plan  # type: ignore[return-value]

    # Fallback: most sections
    return max(valid_runs, key=lambda r: len(r.plan.sections)).plan  # type: ignore[union-attr]


def _build_planning_prompt(
    request: str,
    document_type: str,
    context_rows: dict[str, list[dict[str, Any]]] | None,
) -> str:
    """Build the user prompt with request context."""
    parts = [
        f"USER REQUEST: {request}",
        f"DOCUMENT TYPE: {document_type}",
    ]

    if context_rows:
        parts.append("\nAVAILABLE DATA SUMMARY:")
        for table, rows in context_rows.items():
            parts.append(f"  {table}: {len(rows)} rows available")
            if rows:
                # Show a sample of column names
                sample_keys = list(rows[0].keys())[:6]
                parts.append(f"  columns: {sample_keys}")

    parts.append(
        "\nProduce a DocumentPlan JSON to satisfy this request. "
        "Return ONLY the JSON object — no explanation, no markdown fences."
    )

    return "\n".join(parts)
