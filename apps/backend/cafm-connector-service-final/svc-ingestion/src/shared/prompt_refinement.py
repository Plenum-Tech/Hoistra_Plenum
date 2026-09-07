"""
svc-ingestion/src/shared/prompt_refinement.py

Task 3.5 — Prompt Refinement Loop.

Weekly job that:
  1. Aggregates correction patterns from corrections_log (past 7 days)
  2. Groups by agent_id + field_path + correction_type to find recurring issues
  3. Sends patterns to Claude Haiku — asks for specific prompt improvements
  4. For each approved suggestion (confidence ≥ 0.80), creates a new PromptTemplate
     version and a PromptAbTest row to measure whether it actually helps

Triggered by:
  - ARQ cron job (weekly, Sunday 00:00 UTC) wired in worker.py
  - Manual POST /admin/prompt-refinement/run (for ops)

Threshold: minimum 5 corrections of the same type before a suggestion is generated
(avoids noise from one-off corrections).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import anthropic
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from cafm_shared.logging import get_logger

logger = get_logger(__name__)
tracer = trace.get_tracer(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────

_SUGGESTION_MODEL = "claude-haiku-4-5"
_MIN_CORRECTIONS_TO_SUGGEST = 5   # ignore patterns with fewer than this many hits
_SUGGESTION_CONFIDENCE_THRESHOLD = 0.80
_LOOKBACK_DAYS = 7
_MAX_PATTERNS_PER_RUN = 20        # cap to avoid huge prompts


# ── Data structures ────────────────────────────────────────────────────────────


@dataclass
class CorrectionPattern:
    """One recurring correction pattern found in corrections_log."""

    agent_id: str
    field_path: str
    correction_type: str       # wrong_value | missing_field | wrong_entity | hallucination | other
    count: int                 # how many times this pattern appeared in the lookback window
    sample_originals: list[str] = field(default_factory=list)   # up to 3 original values
    sample_corrected: list[str] = field(default_factory=list)   # up to 3 corrected values


@dataclass
class RefinementSuggestion:
    """Haiku's suggestion for improving a prompt based on a correction pattern."""

    agent_id: str
    field_path: str
    correction_type: str
    pattern_count: int
    suggested_addition: str    # text to add/change in the prompt
    reasoning: str             # why this change would help
    confidence: float          # Haiku's confidence that this will reduce the error
    approved: bool = False     # set True when auto-approved (confidence ≥ threshold)


@dataclass
class RefinementRunResult:
    """Summary of one weekly refinement run."""

    run_at: str
    lookback_days: int
    patterns_found: int
    suggestions_generated: int
    ab_tests_created: int
    low_confidence_skipped: int
    errors: list[str] = field(default_factory=list)


# ── Step 1: Aggregate correction patterns ─────────────────────────────────────


async def aggregate_correction_patterns(
    session: AsyncSession,
    lookback_days: int = _LOOKBACK_DAYS,
) -> list[CorrectionPattern]:
    """
    Query corrections_log for the past `lookback_days` days.
    Groups by (agent_id via ingestion_documents, field_path, correction_type).
    Returns only patterns with count ≥ _MIN_CORRECTIONS_TO_SUGGEST.
    """
    from models.ingestion import CorrectionsLog, IngestionDocument

    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    with tracer.start_as_current_span("prompt_refinement.aggregate") as span:
        # Join corrections_log → ingestion_documents to get agent_id
        result = await session.execute(
            select(
                IngestionDocument.agent_id,
                CorrectionsLog.field_path,
                CorrectionsLog.correction_type,
                func.count(CorrectionsLog.id).label("cnt"),
            )
            .join(IngestionDocument, CorrectionsLog.ingestion_id == IngestionDocument.id)
            .where(CorrectionsLog.timestamp >= cutoff)
            .group_by(
                IngestionDocument.agent_id,
                CorrectionsLog.field_path,
                CorrectionsLog.correction_type,
            )
            .having(func.count(CorrectionsLog.id) >= _MIN_CORRECTIONS_TO_SUGGEST)
            .order_by(func.count(CorrectionsLog.id).desc())
            .limit(_MAX_PATTERNS_PER_RUN)
        )
        rows = result.all()

        patterns: list[CorrectionPattern] = []
        for agent_id, field_path, correction_type, cnt in rows:
            # Fetch up to 3 sample corrections for this pattern
            samples_result = await session.execute(
                select(CorrectionsLog.original_value, CorrectionsLog.corrected_value)
                .join(IngestionDocument, CorrectionsLog.ingestion_id == IngestionDocument.id)
                .where(
                    IngestionDocument.agent_id == agent_id,
                    CorrectionsLog.field_path == field_path,
                    CorrectionsLog.correction_type == correction_type,
                    CorrectionsLog.timestamp >= cutoff,
                )
                .limit(3)
            )
            samples = samples_result.all()

            patterns.append(
                CorrectionPattern(
                    agent_id=agent_id,
                    field_path=field_path,
                    correction_type=correction_type,
                    count=cnt,
                    sample_originals=[s[0] or "" for s in samples],
                    sample_corrected=[s[1] or "" for s in samples],
                )
            )

        span.set_attribute("cafm.patterns_found", len(patterns))
        logger.info(
            "prompt_refinement.patterns_aggregated",
            count=len(patterns),
            lookback_days=lookback_days,
        )
        return patterns


# ── Step 2: Generate suggestions via Haiku ────────────────────────────────────


async def suggest_prompt_edits(
    patterns: list[CorrectionPattern],
    client: anthropic.AsyncAnthropic,
) -> list[RefinementSuggestion]:
    """
    Send aggregated correction patterns to Claude Haiku.
    Haiku suggests specific text to add or modify in the extraction prompt
    for each pattern.

    Returns a list of RefinementSuggestion objects.
    Suggestions with confidence < _SUGGESTION_CONFIDENCE_THRESHOLD are kept
    but not auto-approved.
    """
    if not patterns:
        return []

    with tracer.start_as_current_span("prompt_refinement.suggest") as span:
        # Build a compact description of all patterns
        patterns_text = "\n".join(
            f"- Agent: {p.agent_id} | Field: {p.field_path} | "
            f"Error type: {p.correction_type} | Occurrences: {p.count}\n"
            f"  Examples: original={p.sample_originals[:2]} → corrected={p.sample_corrected[:2]}"
            for p in patterns
        )

        prompt = f"""You are an expert at improving LLM extraction prompts for a CAFM (facilities management) system.

The following correction patterns were found in the past 7 days of human review.
Each pattern represents a recurring extraction error that reviewers had to fix manually.

CORRECTION PATTERNS:
{patterns_text}

For each pattern, suggest a specific addition or change to the extraction prompt that would
prevent this type of error. Be concrete — write the actual text that should be added to the prompt.

Return ONLY a JSON array (no markdown fences):
[
  {{
    "agent_id": "<agent_id>",
    "field_path": "<field_path>",
    "correction_type": "<correction_type>",
    "suggested_addition": "<exact text to add to the prompt>",
    "reasoning": "<why this addition would prevent the error — 1-2 sentences>",
    "confidence": <float 0.0-1.0>
  }}
]

Only include suggestions you are confident will help. Omit patterns where the error
seems random or unrelated to prompt wording."""

        suggestions: list[RefinementSuggestion] = []

        try:
            response = await client.messages.create(
                model=_SUGGESTION_MODEL,
                max_tokens=2048,
                messages=[{"role": "user", "content": prompt}],
            )
            raw = response.content[0].text.strip() if response.content else "[]"

            # Strip fences if present
            if raw.startswith("```"):
                parts = raw.split("```", 2)
                inner = parts[1]
                if inner.startswith("json"):
                    inner = inner[4:]
                raw = inner.strip()

            items: list[dict[str, Any]] = json.loads(raw)
            if not isinstance(items, list):
                items = []

            for item in items:
                confidence = float(item.get("confidence", 0.0))
                confidence = max(0.0, min(1.0, confidence))
                suggestion = RefinementSuggestion(
                    agent_id=str(item.get("agent_id", "")),
                    field_path=str(item.get("field_path", "")),
                    correction_type=str(item.get("correction_type", "")),
                    pattern_count=next(
                        (p.count for p in patterns
                         if p.agent_id == item.get("agent_id")
                         and p.field_path == item.get("field_path")),
                        0,
                    ),
                    suggested_addition=str(item.get("suggested_addition", "")),
                    reasoning=str(item.get("reasoning", "")),
                    confidence=round(confidence, 3),
                    approved=confidence >= _SUGGESTION_CONFIDENCE_THRESHOLD,
                )
                suggestions.append(suggestion)

            span.set_attribute("cafm.suggestions_generated", len(suggestions))
            span.set_attribute(
                "cafm.suggestions_approved",
                sum(1 for s in suggestions if s.approved),
            )
            logger.info(
                "prompt_refinement.suggestions_generated",
                total=len(suggestions),
                approved=sum(1 for s in suggestions if s.approved),
            )

        except (json.JSONDecodeError, anthropic.APIError, ValueError, TypeError) as exc:
            logger.warning("prompt_refinement.suggest_error", error=str(exc))
            span.set_status(StatusCode.ERROR, str(exc))

        return suggestions


# ── Step 3: Apply approved suggestions as A/B tests ───────────────────────────


async def apply_suggestion_as_ab_test(
    suggestion: RefinementSuggestion,
    session: AsyncSession,
) -> bool:
    """
    For an approved suggestion:
    1. Find the current active PromptTemplate for this agent
    2. Create a new PromptTemplate version B with the suggested addition
    3. Create a PromptAbTest linking A (current) → B (new)

    Returns True if AB test was created, False if no base template found.
    """
    from models.ingestion import PromptAbTest, PromptTemplate

    with tracer.start_as_current_span("prompt_refinement.create_ab_test") as span:
        span.set_attribute("cafm.agent_id", suggestion.agent_id)
        span.set_attribute("cafm.field_path", suggestion.field_path)

        # Find the current active template for this agent
        result = await session.execute(
            select(PromptTemplate)
            .where(
                PromptTemplate.agent_id == suggestion.agent_id,
                PromptTemplate.is_active == True,  # noqa: E712
            )
            .order_by(PromptTemplate.created_at.desc())
            .limit(1)
        )
        template_a = result.scalar_one_or_none()

        if template_a is None:
            logger.warning(
                "prompt_refinement.no_base_template",
                agent_id=suggestion.agent_id,
            )
            return False

        # Parse current version and bump it
        try:
            major, minor = template_a.version.split(".")
            new_version = f"{major}.{int(minor) + 1}"
        except (ValueError, AttributeError):
            new_version = f"{template_a.version}.1"

        # Create template B — same as A but with suggestion appended to user_template
        refinement_note = (
            f"\n\n[REFINEMENT v{new_version}] "
            f"Pay special attention to {suggestion.field_path}: "
            f"{suggestion.suggested_addition}"
        )
        template_b = PromptTemplate(
            agent_id=suggestion.agent_id,
            doc_type=template_a.doc_type,
            system_prompt=template_a.system_prompt,
            user_template=template_a.user_template + refinement_note,
            extraction_schema=template_a.extraction_schema,
            version=new_version,
            is_active=False,   # stays inactive until A/B test promotes it
        )
        session.add(template_b)
        await session.flush()  # get template_b.id

        # Create A/B test row
        ab_test = PromptAbTest(
            template_a_id=template_a.id,
            template_b_id=template_b.id,
            status="running",
        )
        session.add(ab_test)
        await session.flush()

        span.set_attribute("cafm.ab_test_created", True)
        span.set_attribute("cafm.template_b_version", new_version)
        logger.info(
            "prompt_refinement.ab_test_created",
            agent_id=suggestion.agent_id,
            field_path=suggestion.field_path,
            template_a_version=template_a.version,
            template_b_version=new_version,
            ab_test_id=str(ab_test.id),
        )
        return True


# ── Top-level orchestrator ─────────────────────────────────────────────────────


async def run_weekly_refinement(
    session: AsyncSession,
    client: anthropic.AsyncAnthropic,
    lookback_days: int = _LOOKBACK_DAYS,
) -> RefinementRunResult:
    """
    Full weekly refinement run.

    Steps:
      1. Aggregate correction patterns from the past lookback_days
      2. Generate suggestions via Haiku
      3. Create A/B tests for approved suggestions
      4. Commit all changes
      5. Return a summary RefinementRunResult

    Called by ARQ cron job (weekly) and by the admin endpoint.
    """
    run_at = datetime.now(timezone.utc).isoformat()
    errors: list[str] = []
    ab_tests_created = 0
    low_confidence_skipped = 0

    with tracer.start_as_current_span("prompt_refinement.weekly_run") as span:
        logger.info("prompt_refinement.run_started", lookback_days=lookback_days)

        # Step 1: Aggregate
        try:
            patterns = await aggregate_correction_patterns(session, lookback_days)
        except Exception as exc:
            logger.error("prompt_refinement.aggregate_error", error=str(exc))
            errors.append(f"aggregate: {exc}")
            patterns = []

        if not patterns:
            logger.info("prompt_refinement.no_patterns_found")
            return RefinementRunResult(
                run_at=run_at,
                lookback_days=lookback_days,
                patterns_found=0,
                suggestions_generated=0,
                ab_tests_created=0,
                low_confidence_skipped=0,
                errors=errors,
            )

        # Step 2: Suggest
        try:
            suggestions = await suggest_prompt_edits(patterns, client)
        except Exception as exc:
            logger.error("prompt_refinement.suggest_error", error=str(exc))
            errors.append(f"suggest: {exc}")
            suggestions = []

        # Step 3: Create A/B tests for approved suggestions
        for suggestion in suggestions:
            if not suggestion.approved:
                low_confidence_skipped += 1
                logger.info(
                    "prompt_refinement.suggestion_skipped",
                    agent_id=suggestion.agent_id,
                    field_path=suggestion.field_path,
                    confidence=suggestion.confidence,
                    reason="below_threshold",
                )
                continue

            try:
                created = await apply_suggestion_as_ab_test(suggestion, session)
                if created:
                    ab_tests_created += 1
            except Exception as exc:
                logger.error(
                    "prompt_refinement.ab_test_error",
                    agent_id=suggestion.agent_id,
                    error=str(exc),
                )
                errors.append(f"ab_test({suggestion.agent_id}/{suggestion.field_path}): {exc}")

        # Commit all new templates + AB tests
        try:
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error("prompt_refinement.commit_error", error=str(exc))
            errors.append(f"commit: {exc}")
            ab_tests_created = 0

        result = RefinementRunResult(
            run_at=run_at,
            lookback_days=lookback_days,
            patterns_found=len(patterns),
            suggestions_generated=len(suggestions),
            ab_tests_created=ab_tests_created,
            low_confidence_skipped=low_confidence_skipped,
            errors=errors,
        )

        span.set_attribute("cafm.patterns_found", len(patterns))
        span.set_attribute("cafm.ab_tests_created", ab_tests_created)
        span.set_attribute("cafm.errors_count", len(errors))

        logger.info(
            "prompt_refinement.run_complete",
            patterns_found=len(patterns),
            suggestions_generated=len(suggestions),
            ab_tests_created=ab_tests_created,
            low_confidence_skipped=low_confidence_skipped,
            errors=len(errors),
        )
        return result
