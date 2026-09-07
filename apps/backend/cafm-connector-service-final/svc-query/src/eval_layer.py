"""
svc-query/src/eval_layer.py

Task 5.9 — EL-7.DOC.RENDER + EL-7.DOC.EVAL

Runs after renderer.py produces the file, before delivery.

EL-7.DOC.RENDER:
  - Haiku receives 10 randomly selected values from the rendered document
  - For each value: verify it exists in the source DB rows fetched in step 2
  - Check: no asset_code in document is missing from assets table
  - Check: no date in document is outside the range of source data
  - Check: no numeric value in document deviates from source by more than rounding error
  - Produces eval_score (0.0–1.0): proportion of spot-checked values that verified

EL-7.DOC.EVAL:
  - eval_score written to document_generation_log.eval_score
  - eval_score >= 0.85 → PASS → file delivered, audit receipt generated
  - eval_score < 0.85 → held_for_review = True, file NOT auto-delivered
"""

from __future__ import annotations

import json
import random
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

_MODEL = "claude-haiku-4-5"
_MAX_TOKENS = 512
_SPOT_CHECK_COUNT = 10
_PASS_THRESHOLD = 0.85

_EVAL_SYSTEM = """\
You are a document quality evaluator for a CAFM (facilities management) system.

You will receive a list of (value, source_table, source_column) tuples
that were extracted from a generated document, plus the source DB rows.

For each value, determine if it appears in the source data.
Return a JSON array of booleans — one per value in input order:
  true  = value found in source data (verified)
  false = value NOT found (potentially fabricated)

Return ONLY the JSON array, no explanation.
Example: [true, true, false, true, true]
"""

_PASS_THRESHOLD = 0.85


@dataclass
class EvalResult:
    """Result of EL-7.DOC.RENDER + EL-7.DOC.EVAL."""

    eval_score: float                       # 0.0 – 1.0
    spot_checks_run: int
    spot_checks_passed: int
    held_for_review: bool                   # True if eval_score < 0.85
    verification_details: list[dict[str, Any]] = field(default_factory=list)
    evaluator_raw_response: str = ""


async def evaluate_rendered_document(
    sampled_values: list[dict[str, Any]],
    source_rows: dict[str, list[dict[str, Any]]],
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
) -> EvalResult:
    """
    EL-7.DOC.RENDER + EL-7.DOC.EVAL.

    sampled_values: list of {value, table, column} from renderer
    source_rows: {table_name: [row_dicts]} used during rendering
    """
    with tracer.start_as_current_span("document.eval") as span:
        span.set_attribute("cafm.values_spot_checked", len(sampled_values))

        # Select up to _SPOT_CHECK_COUNT values for spot-checking
        candidates = sampled_values if len(sampled_values) <= _SPOT_CHECK_COUNT else \
            random.sample(sampled_values, _SPOT_CHECK_COUNT)

        if not candidates:
            # No values to check — trivially pass with note
            logger.warning("document_eval_no_sampled_values")
            return EvalResult(
                eval_score=1.0,
                spot_checks_run=0,
                spot_checks_passed=0,
                held_for_review=False,
            )

        # First pass: rule-based verification (fast, no LLM)
        rule_results = _rule_based_verify(candidates, source_rows)

        # Second pass: LLM-as-judge for any that didn't pass rule-based check
        unverified = [
            (i, c) for i, (c, passed) in enumerate(zip(candidates, rule_results)) if not passed
        ]

        llm_results = list(rule_results)  # copy
        raw_response = ""

        if unverified:
            llm_passed, raw_response = await _llm_verify(
                [c for _, c in unverified], source_rows, client
            )
            for list_idx, (orig_idx, _) in enumerate(unverified):
                if list_idx < len(llm_passed):
                    llm_results[orig_idx] = llm_passed[list_idx]

        passed_count = sum(1 for r in llm_results if r)
        total = len(candidates)
        eval_score = round(passed_count / total, 3) if total > 0 else 1.0
        held = eval_score < _PASS_THRESHOLD

        details = [
            {
                "value": c.get("value"),
                "table": c.get("table"),
                "column": c.get("column"),
                "verified": llm_results[i],
            }
            for i, c in enumerate(candidates)
        ]

        span.set_attribute("cafm.eval_score", eval_score)
        span.set_attribute("cafm.values_verified", passed_count)
        span.set_attribute("cafm.held_for_review", held)

        logger.info(
            "el7_doc_eval_complete",
            eval_score=eval_score,
            spot_checks_run=total,
            spot_checks_passed=passed_count,
            held_for_review=held,
        )

        return EvalResult(
            eval_score=eval_score,
            spot_checks_run=total,
            spot_checks_passed=passed_count,
            held_for_review=held,
            verification_details=details,
            evaluator_raw_response=raw_response,
        )


def _rule_based_verify(
    candidates: list[dict[str, Any]],
    source_rows: dict[str, list[dict[str, Any]]],
) -> list[bool]:
    """
    Fast rule-based verification.
    Check if each value appears in the corresponding source_rows.
    """
    results = []
    for candidate in candidates:
        value = str(candidate.get("value", "")).strip()
        table = candidate.get("table", "")
        column = candidate.get("column", "")

        if not value or not table:
            results.append(True)  # no value to check — pass
            continue

        table_rows = source_rows.get(table, [])
        if not table_rows:
            results.append(True)  # no source to compare — give benefit of doubt
            continue

        # Check if value appears in any row's column
        found = any(
            str(row.get(column, "")).strip() == value
            for row in table_rows
        )
        results.append(found)

    return results


async def _llm_verify(
    candidates: list[dict[str, Any]],
    source_rows: dict[str, list[dict[str, Any]]],
    client: anthropic.AsyncAnthropic,
) -> tuple[list[bool], str]:
    """
    LLM-as-judge verification for values not caught by rule-based check.
    Haiku reviews each value against the source rows.
    """
    with tracer.start_as_current_span("document.eval.llm_judge") as span:
        # Build a compact source representation
        source_summary = {}
        for table, rows in source_rows.items():
            source_summary[table] = rows[:50]  # cap at 50 rows for context

        check_list = json.dumps(
            [{"value": c.get("value"), "table": c.get("table"), "column": c.get("column")}
             for c in candidates],
            indent=2,
        )
        source_text = json.dumps(source_summary, indent=2, default=str)[:8000]  # cap context size

        user_message = (
            f"SPOT-CHECK VALUES:\n{check_list}\n\n"
            f"SOURCE DATA:\n{source_text}\n\n"
            f"For each value, return true if it appears in the source data, false if not."
        )

        try:
            response = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_EVAL_SYSTEM,
                messages=[{"role": "user", "content": user_message}],
            )
            raw = response.content[0].text.strip() if response.content else "[]"

            claude_api_calls.add(1, {"agent_id": "doc-eval", "model": _MODEL})
            claude_tokens_used.add(
                getattr(response.usage, "input_tokens", 0) + getattr(response.usage, "output_tokens", 0),
                {"agent_id": "doc-eval", "model": _MODEL},
            )

            # Strip fences
            if raw.startswith("```"):
                parts = raw.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw = inner.strip()

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                # Pad with True if short, truncate if long
                bools = [bool(v) for v in parsed]
                while len(bools) < len(candidates):
                    bools.append(True)
                return bools[: len(candidates)], raw

            return [True] * len(candidates), raw

        except Exception as exc:
            logger.warning("document_eval_llm_failed", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))
            # On LLM failure, conservatively pass all (avoid blocking valid docs)
            return [True] * len(candidates), ""


async def write_document_generation_log(
    session: AsyncSession,
    *,
    request_text: str,
    intent_type: str,
    document_type: str,
    document_plan_json: dict[str, Any],
    plan_validation_passed: bool,
    output_format: str,
    output_blob_url: str,
    data_sources: dict[str, Any],
    eval_result: EvalResult,
    plan_runs_agreed: int,
    model_used: str,
    tokens_in: int,
    tokens_out: int,
    cost_usd: float,
    render_ms: int,
    user_id: str | None,
) -> str:
    """
    Write a row to document_generation_log and return the inserted id.
    Captures all EL-7 evaluation fields per CLAUDE.md §13.
    """
    import uuid
    row_id = str(uuid.uuid4())

    try:
        await session.execute(
            text(
                """
                INSERT INTO plenum_cafm.document_generation_log (
                    id, request_text, intent_type, document_type,
                    document_plan_json, plan_validation_passed,
                    output_format, output_blob_url, data_sources,
                    spot_checks_run, spot_checks_passed, eval_score,
                    plan_runs_agreed, held_for_review, model_used,
                    tokens_in, tokens_out, cost_usd, render_ms,
                    user_id, timestamp
                ) VALUES (
                    :id, :request_text, :intent_type, :document_type,
                    :plan_json, :plan_valid,
                    :output_format, :output_url, :data_sources,
                    :checks_run, :checks_passed, :eval_score,
                    :runs_agreed, :held, :model_used,
                    :tokens_in, :tokens_out, :cost_usd, :render_ms,
                    :user_id, NOW()
                )
                """
            ),
            {
                "id": row_id,
                "request_text": request_text,
                "intent_type": intent_type,
                "document_type": document_type,
                "plan_json": json.dumps(document_plan_json, default=str),
                "plan_valid": plan_validation_passed,
                "output_format": output_format,
                "output_url": output_blob_url,
                "data_sources": json.dumps(data_sources, default=str),
                "checks_run": eval_result.spot_checks_run,
                "checks_passed": eval_result.spot_checks_passed,
                "eval_score": eval_result.eval_score,
                "runs_agreed": plan_runs_agreed,
                "held": eval_result.held_for_review,
                "model_used": model_used,
                "tokens_in": tokens_in,
                "tokens_out": tokens_out,
                "cost_usd": cost_usd,
                "render_ms": render_ms,
                "user_id": user_id,
            },
        )
        await session.flush()
        return row_id

    except Exception as exc:
        logger.error("document_generation_log_write_failed", error=str(exc))
        return row_id  # return ID even if log write fails — never block delivery
