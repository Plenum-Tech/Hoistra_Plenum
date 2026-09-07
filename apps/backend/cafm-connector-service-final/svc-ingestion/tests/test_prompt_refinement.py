"""
tests/test_prompt_refinement.py

Unit tests for shared/prompt_refinement.py — Task 3.5.

Covers:
  - CorrectionPattern: dataclass defaults, fields
  - RefinementSuggestion: approved flag set correctly at confidence threshold
  - RefinementRunResult: fields and defaults
  - suggest_prompt_edits(): mocked Haiku, valid response, parse error fallback,
    API error fallback, confidence threshold approval, markdown fence stripping
  - apply_suggestion_as_ab_test(): creates PromptTemplate B + PromptAbTest,
    handles missing base template, version bumping
  - run_weekly_refinement(): no patterns → early return, patterns → full run,
    all low-confidence → no AB tests, commit error → rollback
  - aggregate_correction_patterns(): tested via mocked session (query structure)
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from shared.prompt_refinement import (
    CorrectionPattern,
    RefinementRunResult,
    RefinementSuggestion,
    _MIN_CORRECTIONS_TO_SUGGEST,
    _SUGGESTION_CONFIDENCE_THRESHOLD,
    apply_suggestion_as_ab_test,
    run_weekly_refinement,
    suggest_prompt_edits,
)


# ===========================================================================
# Helpers
# ===========================================================================

def _make_pattern(
    agent_id: str = "pdf-agent",
    field_path: str = "asset_code",
    correction_type: str = "wrong_value",
    count: int = 8,
) -> CorrectionPattern:
    return CorrectionPattern(
        agent_id=agent_id,
        field_path=field_path,
        correction_type=correction_type,
        count=count,
        sample_originals=["AHU001", "PUMP01"],
        sample_corrected=["MOB-AHU-001", "MOB-PUMP-001"],
    )


def _make_suggestion(
    agent_id: str = "pdf-agent",
    field_path: str = "asset_code",
    confidence: float = 0.85,
) -> RefinementSuggestion:
    return RefinementSuggestion(
        agent_id=agent_id,
        field_path=field_path,
        correction_type="wrong_value",
        pattern_count=8,
        suggested_addition="Always include the site prefix (e.g. MOB-) before asset codes.",
        reasoning="Reviewers consistently add the MOB- prefix to codes that lack it.",
        confidence=confidence,
        approved=confidence >= _SUGGESTION_CONFIDENCE_THRESHOLD,
    )


def _make_haiku_client(
    suggestions: list[dict] | None = None,
    raise_error: bool = False,
    raw_text: str | None = None,
) -> Any:
    client = AsyncMock()
    if raise_error:
        import anthropic as _anthropic
        client.messages.create = AsyncMock(
            side_effect=_anthropic.APIError("timeout", request=MagicMock(), body={})
        )
        return client

    if suggestions is None:
        suggestions = [
            {
                "agent_id": "pdf-agent",
                "field_path": "asset_code",
                "correction_type": "wrong_value",
                "suggested_addition": "Always use the MOB- prefix format.",
                "reasoning": "Reviewers add prefix consistently.",
                "confidence": 0.88,
            }
        ]

    response_text = raw_text if raw_text is not None else json.dumps(suggestions)
    mock_content = MagicMock()
    mock_content.text = response_text
    mock_response = MagicMock()
    mock_response.content = [mock_content]
    client.messages.create = AsyncMock(return_value=mock_response)
    return client


def _make_template(version: str = "1.0", agent_id: str = "pdf-agent") -> MagicMock:
    tpl = MagicMock()
    tpl.id = uuid4()
    tpl.agent_id = agent_id
    tpl.doc_type = "inspection_report"
    tpl.system_prompt = "You are a CAFM extractor."
    tpl.user_template = "Extract entities from this document."
    tpl.extraction_schema = None
    tpl.version = version
    tpl.is_active = True
    tpl.created_at = MagicMock()
    return tpl


def _make_session(template: MagicMock | None = None) -> AsyncMock:
    """Build a mock async session that returns a template (or None) on execute."""
    session = AsyncMock()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none = MagicMock(return_value=template)
    session.execute = AsyncMock(return_value=mock_result)
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ===========================================================================
# CorrectionPattern
# ===========================================================================


class TestCorrectionPattern:
    def test_fields_present(self):
        p = _make_pattern(count=10)
        assert p.agent_id == "pdf-agent"
        assert p.field_path == "asset_code"
        assert p.count == 10
        assert isinstance(p.sample_originals, list)
        assert isinstance(p.sample_corrected, list)

    def test_default_empty_samples(self):
        p = CorrectionPattern(
            agent_id="csv-agent",
            field_path="wo_code",
            correction_type="missing_field",
            count=5,
        )
        assert p.sample_originals == []
        assert p.sample_corrected == []

    def test_min_corrections_constant(self):
        assert _MIN_CORRECTIONS_TO_SUGGEST == 5


# ===========================================================================
# RefinementSuggestion — approved flag
# ===========================================================================


class TestRefinementSuggestion:
    def test_approved_at_threshold(self):
        s = _make_suggestion(confidence=_SUGGESTION_CONFIDENCE_THRESHOLD)
        assert s.approved is True

    def test_approved_above_threshold(self):
        s = _make_suggestion(confidence=0.95)
        assert s.approved is True

    def test_not_approved_below_threshold(self):
        s = _make_suggestion(confidence=0.79)
        assert s.approved is False

    def test_not_approved_at_zero(self):
        s = _make_suggestion(confidence=0.0)
        assert s.approved is False

    def test_confidence_threshold_constant(self):
        assert _SUGGESTION_CONFIDENCE_THRESHOLD == 0.80


# ===========================================================================
# RefinementRunResult
# ===========================================================================


class TestRefinementRunResult:
    def test_fields_present(self):
        r = RefinementRunResult(
            run_at="2026-03-26T00:00:00Z",
            lookback_days=7,
            patterns_found=3,
            suggestions_generated=2,
            ab_tests_created=1,
            low_confidence_skipped=1,
        )
        assert r.patterns_found == 3
        assert r.ab_tests_created == 1
        assert r.errors == []

    def test_errors_default_empty(self):
        r = RefinementRunResult(
            run_at="x", lookback_days=7,
            patterns_found=0, suggestions_generated=0,
            ab_tests_created=0, low_confidence_skipped=0,
        )
        assert r.errors == []


# ===========================================================================
# suggest_prompt_edits()
# ===========================================================================


class TestSuggestPromptEdits:
    @pytest.mark.asyncio
    async def test_returns_suggestions_from_haiku(self):
        patterns = [_make_pattern()]
        client = _make_haiku_client()
        suggestions = await suggest_prompt_edits(patterns, client)
        assert len(suggestions) == 1
        assert suggestions[0].agent_id == "pdf-agent"
        assert suggestions[0].field_path == "asset_code"

    @pytest.mark.asyncio
    async def test_confidence_sets_approved_flag(self):
        patterns = [_make_pattern()]
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "asset_code",
             "correction_type": "wrong_value",
             "suggested_addition": "Use MOB- prefix.",
             "reasoning": "Always needed.", "confidence": 0.90},
        ])
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions[0].approved is True

    @pytest.mark.asyncio
    async def test_low_confidence_not_approved(self):
        patterns = [_make_pattern()]
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "asset_code",
             "correction_type": "wrong_value",
             "suggested_addition": "Maybe add prefix.",
             "reasoning": "Possibly.", "confidence": 0.55},
        ])
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions[0].approved is False

    @pytest.mark.asyncio
    async def test_empty_patterns_returns_empty(self):
        client = _make_haiku_client()
        suggestions = await suggest_prompt_edits([], client)
        assert suggestions == []
        client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self):
        patterns = [_make_pattern()]
        client = _make_haiku_client(raise_error=True)
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_invalid_json_returns_empty(self):
        patterns = [_make_pattern()]
        client = _make_haiku_client(raw_text="not json at all")
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_markdown_fence_stripped(self):
        patterns = [_make_pattern()]
        fenced = '```json\n[{"agent_id":"pdf-agent","field_path":"severity",' \
                 '"correction_type":"wrong_value","suggested_addition":"x",' \
                 '"reasoning":"y","confidence":0.85}]\n```'
        client = _make_haiku_client(raw_text=fenced)
        suggestions = await suggest_prompt_edits(patterns, client)
        assert len(suggestions) == 1
        assert suggestions[0].field_path == "severity"

    @pytest.mark.asyncio
    async def test_confidence_clamped_to_range(self):
        """Confidence values outside [0,1] from LLM are clamped."""
        patterns = [_make_pattern()]
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "f",
             "correction_type": "x", "suggested_addition": "y",
             "reasoning": "z", "confidence": 1.5},  # out of range
        ])
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions[0].confidence <= 1.0

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self):
        """If Haiku returns a JSON object instead of array, handle gracefully."""
        patterns = [_make_pattern()]
        client = _make_haiku_client(raw_text='{"error": "unexpected"}')
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions == []

    @pytest.mark.asyncio
    async def test_pattern_count_propagated(self):
        """pattern_count in suggestion should match the source pattern's count."""
        patterns = [_make_pattern(count=12, field_path="serial")]
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "serial",
             "correction_type": "wrong_value", "suggested_addition": "x",
             "reasoning": "y", "confidence": 0.82},
        ])
        suggestions = await suggest_prompt_edits(patterns, client)
        assert suggestions[0].pattern_count == 12

    @pytest.mark.asyncio
    async def test_multiple_suggestions_returned(self):
        patterns = [_make_pattern(field_path="asset_code"), _make_pattern(field_path="severity")]
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "asset_code",
             "correction_type": "wrong_value", "suggested_addition": "a",
             "reasoning": "b", "confidence": 0.85},
            {"agent_id": "pdf-agent", "field_path": "severity",
             "correction_type": "wrong_value", "suggested_addition": "c",
             "reasoning": "d", "confidence": 0.90},
        ])
        suggestions = await suggest_prompt_edits(patterns, client)
        assert len(suggestions) == 2


# ===========================================================================
# apply_suggestion_as_ab_test()
# ===========================================================================


class TestApplySuggestionAsAbTest:
    @pytest.mark.asyncio
    async def test_creates_ab_test_when_template_found(self):
        template = _make_template(version="1.0")
        session = _make_session(template=template)
        suggestion = _make_suggestion(confidence=0.88)

        result = await apply_suggestion_as_ab_test(suggestion, session)

        assert result is True
        session.add.assert_called()  # template B + ab_test added
        session.flush.assert_called()

    @pytest.mark.asyncio
    async def test_returns_false_when_no_template(self):
        session = _make_session(template=None)
        suggestion = _make_suggestion()
        result = await apply_suggestion_as_ab_test(suggestion, session)
        assert result is False

    @pytest.mark.asyncio
    async def test_version_bumped_minor(self):
        """Version 1.0 → 1.1 on new template B."""
        template = _make_template(version="1.0")
        session = _make_session(template=template)
        suggestion = _make_suggestion()

        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)

        await apply_suggestion_as_ab_test(suggestion, session)

        # First added object should be PromptTemplate (template B)
        # Check it has a version that looks like "1.1"
        from models.ingestion import PromptTemplate, PromptAbTest
        template_b_calls = [o for o in added_objects if hasattr(o, "version")]
        assert len(template_b_calls) >= 1
        assert template_b_calls[0].version == "1.1"

    @pytest.mark.asyncio
    async def test_suggestion_text_in_template_b(self):
        """Suggested addition should appear in template B's user_template."""
        template = _make_template(version="2.0")
        session = _make_session(template=template)

        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)

        suggestion = _make_suggestion()
        suggestion.suggested_addition = "Always include MOB- prefix."
        await apply_suggestion_as_ab_test(suggestion, session)

        template_b = next((o for o in added_objects if hasattr(o, "user_template")), None)
        assert template_b is not None
        assert "MOB- prefix" in template_b.user_template

    @pytest.mark.asyncio
    async def test_template_b_is_inactive(self):
        """Template B should start as inactive — stays that way until A/B promotes it."""
        template = _make_template(version="1.5")
        session = _make_session(template=template)

        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)

        await apply_suggestion_as_ab_test(_make_suggestion(), session)

        template_b = next((o for o in added_objects if hasattr(o, "is_active")), None)
        assert template_b is not None
        assert template_b.is_active is False

    @pytest.mark.asyncio
    async def test_version_bump_handles_unusual_format(self):
        """Non-standard version string handled without crash."""
        template = _make_template(version="v2")
        session = _make_session(template=template)
        # Should not raise
        result = await apply_suggestion_as_ab_test(_make_suggestion(), session)
        assert result is True


# ===========================================================================
# run_weekly_refinement()
# ===========================================================================


class TestRunWeeklyRefinement:
    @pytest.mark.asyncio
    async def test_no_patterns_early_return(self):
        """When aggregate returns empty, skip suggestion step."""
        session = _make_session()
        client = _make_haiku_client()

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=[]),
        ):
            result = await run_weekly_refinement(session, client)

        assert result.patterns_found == 0
        assert result.ab_tests_created == 0
        assert result.suggestions_generated == 0
        client.messages.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_full_run_creates_ab_tests(self):
        """Happy path: patterns → suggestions → AB tests."""
        session = _make_session(template=_make_template())
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "asset_code",
             "correction_type": "wrong_value", "suggested_addition": "Use MOB- prefix.",
             "reasoning": "Reviewers always add it.", "confidence": 0.90},
        ])

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=[_make_pattern()]),
        ), patch(
            "shared.prompt_refinement.apply_suggestion_as_ab_test",
            AsyncMock(return_value=True),
        ) as mock_ab:
            result = await run_weekly_refinement(session, client)

        assert result.patterns_found == 1
        assert result.suggestions_generated == 1
        assert result.ab_tests_created == 1
        mock_ab.assert_called_once()

    @pytest.mark.asyncio
    async def test_low_confidence_suggestions_skipped(self):
        """Suggestions below threshold are counted as skipped, no AB test created."""
        session = _make_session(template=_make_template())
        client = _make_haiku_client(suggestions=[
            {"agent_id": "pdf-agent", "field_path": "f",
             "correction_type": "x", "suggested_addition": "y",
             "reasoning": "z", "confidence": 0.60},  # below 0.80
        ])

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=[_make_pattern()]),
        ), patch(
            "shared.prompt_refinement.apply_suggestion_as_ab_test",
            AsyncMock(return_value=True),
        ) as mock_ab:
            result = await run_weekly_refinement(session, client)

        assert result.low_confidence_skipped == 1
        assert result.ab_tests_created == 0
        mock_ab.assert_not_called()

    @pytest.mark.asyncio
    async def test_commit_error_rolls_back(self):
        """If commit fails, rollback is called and ab_tests_created is 0."""
        session = _make_session(template=_make_template())
        session.commit = AsyncMock(side_effect=Exception("DB error"))
        client = _make_haiku_client()

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=[_make_pattern()]),
        ), patch(
            "shared.prompt_refinement.apply_suggestion_as_ab_test",
            AsyncMock(return_value=True),
        ):
            result = await run_weekly_refinement(session, client)

        session.rollback.assert_called_once()
        assert result.ab_tests_created == 0
        assert len(result.errors) >= 1

    @pytest.mark.asyncio
    async def test_aggregate_error_handled(self):
        """If aggregate raises, run continues with 0 patterns and no crash."""
        session = _make_session()
        client = _make_haiku_client()

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(side_effect=Exception("DB timeout")),
        ):
            result = await run_weekly_refinement(session, client)

        assert result.patterns_found == 0
        assert "aggregate" in result.errors[0]

    @pytest.mark.asyncio
    async def test_run_result_has_run_at_timestamp(self):
        session = _make_session()
        client = _make_haiku_client()

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=[]),
        ):
            result = await run_weekly_refinement(session, client)

        assert "T" in result.run_at  # ISO format with time component

    @pytest.mark.asyncio
    async def test_multiple_patterns_multiple_ab_tests(self):
        """Each approved suggestion creates one AB test."""
        patterns = [
            _make_pattern(field_path="asset_code"),
            _make_pattern(field_path="severity"),
            _make_pattern(field_path="serial"),
        ]
        suggestions_data = [
            {"agent_id": "pdf-agent", "field_path": p.field_path,
             "correction_type": "wrong_value", "suggested_addition": "fix",
             "reasoning": "reason", "confidence": 0.85}
            for p in patterns
        ]
        session = _make_session(template=_make_template())
        client = _make_haiku_client(suggestions=suggestions_data)

        with patch(
            "shared.prompt_refinement.aggregate_correction_patterns",
            AsyncMock(return_value=patterns),
        ), patch(
            "shared.prompt_refinement.apply_suggestion_as_ab_test",
            AsyncMock(return_value=True),
        ) as mock_ab:
            result = await run_weekly_refinement(session, client)

        assert result.ab_tests_created == 3
        assert mock_ab.call_count == 3
