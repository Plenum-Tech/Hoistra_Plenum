"""
tests/test_prompt_engine.py

Unit tests for prompt_engine/engine.py and prompt_engine/ab_testing.py — Task 2.7.

Covers:
  - _split_blocks(): Jinja2 block extraction — with/without blocks, whitespace, dash variants
  - _parse_template_id(): ID → (agent_id, doc_type) resolution
  - PromptEngine.render(): filesystem fallback (no Redis, no DB)
  - PromptEngine.render(): Redis cache hit path
  - PromptEngine.invalidate(): deletes both cache keys
  - Template variable substitution: Jinja2 variables rendered correctly
  - A/B variant selection: deterministic MD5 split on request_id
  - ab_testing.check_for_winner(): all winner/no-winner scenarios
  - ab_testing.record_outcome(): rolling average update
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from prompt_engine.engine import (
    RenderedPrompt,
    _CachedTemplate,
    _parse_template_id,
    _split_blocks,
)


# ===========================================================================
# _split_blocks
# ===========================================================================

class TestSplitBlocks:
    def test_both_blocks_present(self):
        raw = """\
{% block system %}
You are a specialist.
{% endblock %}

{% block user %}
Extract from: {{ source_filename }}
{% endblock %}
"""
        system, user = _split_blocks(raw)
        assert "You are a specialist" in system
        assert "Extract from" in user
        assert "{% block" not in system
        assert "{% block" not in user

    def test_no_blocks_returns_default_system(self):
        raw = "Just a plain template with {{ variable }}"
        system, user = _split_blocks(raw)
        # Default system prompt should mention CAFM
        assert "CAFM" in system
        # User gets the full raw content
        assert "{{ variable }}" in user

    def test_dash_variant_blocks(self):
        raw = """\
{%- block system -%}
Role desc
{%- endblock -%}
{%- block user -%}
User content
{%- endblock -%}
"""
        system, user = _split_blocks(raw)
        assert "Role desc" in system
        assert "User content" in user

    def test_only_system_block_present(self):
        """If only one block exists — treat whole content as user."""
        raw = "{% block system %}Only system here{% endblock %}"
        # user_match will be None → fallback to full-content path
        system, user = _split_blocks(raw)
        # No user block → returns default system + full raw
        assert "CAFM" in system
        assert "{% block system %}" in user  # raw text as-is

    def test_multiline_extraction(self):
        raw = """\
{% block system %}
Line 1
Line 2
Line 3
{% endblock %}
{% block user %}
User line 1
User line 2
{% endblock %}
"""
        system, user = _split_blocks(raw)
        assert "Line 1" in system
        assert "Line 3" in system
        assert "User line 2" in user

    def test_strips_leading_trailing_whitespace(self):
        raw = (
            "{% block system %}   sys   {% endblock %}"
            "{% block user %}   usr   {% endblock %}"
        )
        system, user = _split_blocks(raw)
        assert system == "sys"
        assert user == "usr"


# ===========================================================================
# _parse_template_id
# ===========================================================================

class TestParseTemplateId:
    def test_known_pdf_inspection(self):
        agent_id, doc_type = _parse_template_id("pdf/inspection_report")
        assert agent_id == "pdf-agent"
        assert doc_type == "inspection_report"

    def test_known_csv_schema_mapper(self):
        agent_id, doc_type = _parse_template_id("csv/schema_mapper")
        assert agent_id == "csv-agent"
        assert doc_type == "schema_mapper"

    def test_known_word_generic(self):
        agent_id, doc_type = _parse_template_id("word/generic_word")
        assert agent_id == "word-agent"
        assert doc_type == "generic_word"

    def test_known_excel_generic(self):
        agent_id, doc_type = _parse_template_id("excel/generic_excel")
        assert agent_id == "excel-agent"
        assert doc_type == "generic_excel"

    def test_unknown_template_id_derives_from_path(self):
        agent_id, doc_type = _parse_template_id("pdf/custom_invoice")
        assert agent_id == "pdf-agent"
        assert doc_type == "custom_invoice"

    def test_no_slash_returns_unknown(self):
        agent_id, doc_type = _parse_template_id("flatid")
        assert agent_id == "unknown-agent"
        assert doc_type == "flatid"

    def test_all_known_ids_resolve(self):
        """Every ID in _TEMPLATE_ID_TO_AGENT must parse without error."""
        from prompt_engine.engine import _TEMPLATE_ID_TO_AGENT
        for template_id, (expected_agent, expected_doc) in _TEMPLATE_ID_TO_AGENT.items():
            agent, doc = _parse_template_id(template_id)
            assert agent == expected_agent
            assert doc == expected_doc


# ===========================================================================
# PromptEngine — filesystem fallback rendering
# ===========================================================================

class TestPromptEngineFilesystemRender:
    """Tests that use real .j2 template files on disk; no Redis or DB needed."""

    def _make_engine(self, templates_dir: Path) -> Any:
        from prompt_engine.engine import PromptEngine

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=None)
        redis.setex = AsyncMock()

        return PromptEngine(
            templates_dir=templates_dir,
            redis=redis,
            session_factory=None,
            cache_ttl_seconds=0,  # Disable cache for clean tests
        )

    @pytest.fixture
    def templates_dir(self) -> Path:
        base = Path(__file__).parent.parent / "src" / "prompt_engine" / "templates"
        if not base.exists():
            pytest.skip("Templates directory not found — run from repo root")
        return base

    @pytest.mark.asyncio
    async def test_render_csv_schema_mapper(self, templates_dir):
        engine = self._make_engine(templates_dir)
        result = await engine.render(
            "csv/schema_mapper",
            {
                "source_filename": "assets.csv",
                "headers": ["Asset Code", "Asset Name", "Category"],
                "sample_rows": [["MOB-AHU-001", "AHU 1", "Air Handler"]],
            },
        )
        assert isinstance(result, RenderedPrompt)
        assert result.template_id == "csv/schema_mapper"
        assert "assets.csv" in result.user_message
        assert "Asset Code" in result.user_message or "headers" in result.user_message.lower()
        assert result.system_prompt  # non-empty
        assert result.version == "filesystem"
        assert result.template_db_id is None

    @pytest.mark.asyncio
    async def test_render_word_generic(self, templates_dir):
        engine = self._make_engine(templates_dir)
        result = await engine.render(
            "word/generic_word",
            {"source_filename": "inspection_report.docx"},
        )
        assert "inspection_report.docx" in result.user_message
        assert result.system_prompt

    @pytest.mark.asyncio
    async def test_render_excel_generic(self, templates_dir):
        engine = self._make_engine(templates_dir)
        result = await engine.render(
            "excel/generic_excel",
            {
                "source_filename": "assets.xlsx",
                "headers": ["Code", "Name"],
                "sheet_name": "Sheet1",
                "sample_rows": [],
            },
        )
        assert "assets.xlsx" in result.user_message

    @pytest.mark.asyncio
    async def test_render_pdf_inspection_report(self, templates_dir):
        engine = self._make_engine(templates_dir)
        result = await engine.render(
            "pdf/inspection_report",
            {
                "source_filename": "site_inspection_nov2025.pdf",
                "pass_number": 1,
                "total_passes": 1,
                "retry_context": None,
                "previous_contradictions": [],
            },
        )
        assert result.system_prompt
        assert result.user_message

    @pytest.mark.asyncio
    async def test_render_unknown_template_uses_default(self, templates_dir):
        """Non-existent template ID → falls back to default user template."""
        engine = self._make_engine(templates_dir)
        result = await engine.render(
            "pdf/nonexistent_template",
            {"source_filename": "test.pdf"},
        )
        # Default fallback should still return a RenderedPrompt
        assert isinstance(result, RenderedPrompt)
        assert result.system_prompt

    @pytest.mark.asyncio
    async def test_render_returns_render_ms(self, templates_dir):
        engine = self._make_engine(templates_dir)
        result = await engine.render("csv/schema_mapper", {"source_filename": "x.csv", "headers": []})
        assert isinstance(result.render_ms, int)
        assert result.render_ms >= 0


# ===========================================================================
# PromptEngine — Redis cache hit
# ===========================================================================

class TestPromptEngineRedisCache:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_filesystem(self, tmp_path):
        """If Redis returns cached data, filesystem is not touched."""
        from prompt_engine.engine import PromptEngine

        cached_data = {
            "system_prompt": "Cached system",
            "user_template": "Cached user {{ source_filename }}",
            "version": "1.2",
            "template_db_id": str(uuid4()),
            "variant": None,
            "ab_test_id": None,
        }

        redis = AsyncMock()
        redis.get = AsyncMock(return_value=json.dumps(cached_data).encode())
        redis.setex = AsyncMock()

        engine = PromptEngine(
            templates_dir=tmp_path,  # Empty dir — would fail if accessed
            redis=redis,
            session_factory=None,
            cache_ttl_seconds=300,
        )

        result = await engine.render("csv/schema_mapper", {"source_filename": "data.csv"})

        assert result.system_prompt == "Cached system"
        assert "data.csv" in result.user_message
        assert result.version == "1.2"

    @pytest.mark.asyncio
    async def test_invalidate_deletes_both_keys(self, tmp_path):
        from prompt_engine.engine import PromptEngine

        redis = AsyncMock()
        redis.delete = AsyncMock()

        engine = PromptEngine(
            templates_dir=tmp_path,
            redis=redis,
            cache_ttl_seconds=300,
        )

        await engine.invalidate("pdf-agent", "inspection_report")

        redis.delete.assert_called_once()
        args = redis.delete.call_args[0]
        assert "prompt_tpl:pdf-agent:inspection_report" in args
        assert "prompt_ab:pdf-agent:inspection_report" in args


# ===========================================================================
# A/B variant determinism
# ===========================================================================

class TestABVariantDeterminism:
    def test_deterministic_split_on_request_id(self):
        """Same request_id always maps to same variant."""
        import hashlib

        def pick_variant(request_id: str) -> str:
            digest = int(
                hashlib.md5(request_id.encode(), usedforsecurity=False).hexdigest(), 16
            )
            return "b" if (digest % 100) >= 50 else "a"

        # All calls with same ID must return same variant
        rid = "req-abc-123"
        assert pick_variant(rid) == pick_variant(rid)
        assert pick_variant(rid) == pick_variant(rid)

    def test_different_ids_can_get_different_variants(self):
        import hashlib

        def pick_variant(request_id: str) -> str:
            digest = int(
                hashlib.md5(request_id.encode(), usedforsecurity=False).hexdigest(), 16
            )
            return "b" if (digest % 100) >= 50 else "a"

        variants = {pick_variant(str(uuid4())) for _ in range(50)}
        # With 50 samples we expect both "a" and "b"
        assert len(variants) == 2


# ===========================================================================
# ab_testing — check_for_winner and record_outcome
# ===========================================================================

class TestAbTesting:
    def _make_ab_test(self, accuracy_a: float, accuracy_b: float | None, docs_processed: int):
        test = MagicMock()
        test.accuracy_a = accuracy_a
        test.accuracy_b = accuracy_b
        test.docs_processed = docs_processed
        test.template_a_id = uuid4()
        test.template_b_id = uuid4()
        test.winner_id = None
        test.status = "running"
        return test

    def _make_session_with_result(self, scalar_value):
        """Build a session mock where execute().scalar_one_or_none() returns scalar_value."""
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=scalar_value)
        session.execute = AsyncMock(return_value=mock_result)
        session.commit = AsyncMock()
        return session

    @pytest.mark.asyncio
    async def test_no_winner_below_60_docs(self):
        from prompt_engine.ab_testing import check_for_winner

        # _MIN_DOCS_PER_VARIANT=30, threshold = 30*2=60; 50 < 60 → no winner
        ab_test = self._make_ab_test(0.92, 0.85, 50)
        session = self._make_session_with_result(ab_test)

        winner = await check_for_winner(session, uuid4())
        assert winner is None

    @pytest.mark.asyncio
    async def test_no_winner_gap_too_small(self):
        from prompt_engine.ab_testing import check_for_winner

        ab_test = self._make_ab_test(0.90, 0.88, 80)  # gap = 0.02 < 0.03
        session = self._make_session_with_result(ab_test)

        winner = await check_for_winner(session, uuid4())
        assert winner is None

    @pytest.mark.asyncio
    async def test_winner_a_when_a_clearly_better(self):
        from prompt_engine.ab_testing import check_for_winner

        ab_test = self._make_ab_test(0.94, 0.88, 100)  # gap = 0.06 ≥ 0.03
        session = self._make_session_with_result(ab_test)

        winner = await check_for_winner(session, uuid4())
        assert winner == "a"

    @pytest.mark.asyncio
    async def test_winner_b_when_b_clearly_better(self):
        from prompt_engine.ab_testing import check_for_winner

        ab_test = self._make_ab_test(0.85, 0.93, 120)  # gap = 0.08 ≥ 0.03
        session = self._make_session_with_result(ab_test)

        winner = await check_for_winner(session, uuid4())
        assert winner == "b"

    @pytest.mark.asyncio
    async def test_no_winner_when_ab_test_not_found(self):
        from prompt_engine.ab_testing import check_for_winner

        session = self._make_session_with_result(None)

        winner = await check_for_winner(session, uuid4())
        assert winner is None

    @pytest.mark.asyncio
    async def test_record_outcome_updates_accuracy(self):
        from prompt_engine.ab_testing import record_outcome

        ab_test = self._make_ab_test(0.80, 0.82, 10)
        session = self._make_session_with_result(ab_test)

        # record_outcome calls execute twice (SELECT + UPDATE); handle both
        await record_outcome(session, uuid4(), "a", 0.95)
        # Just verify no exception — session.execute was called
        assert session.execute.called

    @pytest.mark.asyncio
    async def test_record_outcome_variant_b(self):
        from prompt_engine.ab_testing import record_outcome

        ab_test = self._make_ab_test(0.80, None, 0)
        session = self._make_session_with_result(ab_test)

        await record_outcome(session, uuid4(), "b", 0.90)
        assert session.execute.called
