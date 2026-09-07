"""
svc-query/tests/test_document_generator.py

Tests for Tasks 5.6–5.12:
  - DocumentPlan schemas (schemas.py)
  - Planner vote logic (planner.py)
  - Validator (validator.py) — EL-7.DOC.PLAN
  - Renderer section/format logic (renderer.py)
  - Eval layer (eval_layer.py) — EL-7.DOC.RENDER + EL-7.DOC.EVAL
  - Filler (filler.py) — EL-7.TEMPLATE.PRE + EL-7.TEMPLATE.POST
  - Output renderer (output_renderer.py)
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Add svc-query/src to path
_SVC_QUERY_SRC = str(Path(__file__).parent.parent / "src")
if _SVC_QUERY_SRC not in sys.path:
    sys.path.insert(0, _SVC_QUERY_SRC)

# Add shared-lib to path
_SHARED_LIB = str(Path(__file__).parent.parent.parent / "shared-lib")
if _SHARED_LIB not in sys.path:
    sys.path.insert(0, _SHARED_LIB)

# Patch telemetry before imports
os.environ.setdefault("OTEL_SDK_DISABLED", "true")


# ── Schema tests ───────────────────────────────────────────────────────────────

class TestDocumentSection:
    def test_valid_section(self):
        from document_generator.schemas import DocumentSection
        s = DocumentSection(
            type="summary_table",
            heading="Asset Summary",
            data_source="assets",
        )
        assert s.type == "summary_table"
        assert s.columns is None
        assert s.limit is None

    def test_section_with_all_fields(self):
        from document_generator.schemas import DocumentSection
        s = DocumentSection(
            type="parts_table",
            heading="Parts",
            data_source="spare_parts WHERE stock_on_hand < minimum_allowed_stock",
            columns=["part_code", "stock_on_hand"],
            highlight_rule="stock_on_hand == 0 → red",
            sort_by="stock_on_hand",
            limit=50,
        )
        assert s.columns == ["part_code", "stock_on_hand"]
        assert s.limit == 50

    def test_invalid_section_type(self):
        from document_generator.schemas import DocumentSection
        with pytest.raises(Exception):
            DocumentSection(
                type="invalid_type",
                heading="Bad",
                data_source="assets",
            )

    def test_free_text_header(self):
        from document_generator.schemas import DocumentSection
        s = DocumentSection(type="free_text_header", heading="Intro", data_source="This quarter's report.")
        assert s.type == "free_text_header"


class TestDocumentPlan:
    def _make_plan(self, **kwargs):
        from document_generator.schemas import DocumentPlan, DocumentSection
        defaults = dict(
            document_type="pm_schedule",
            title="PM Schedule Q1",
            generated_for="All AHUs",
            output_format="docx",
            sections=[
                DocumentSection(type="summary_table", heading="Assets", data_source="assets"),
                DocumentSection(type="schedule_grid", heading="Schedule", data_source="scheduled_pm"),
            ],
            footer={"generated_by": "CAFM AI Platform", "timestamp": "2026-03-27T10:00:00Z", "audit_id": "abc-123"},
            data_sources_required=["assets", "scheduled_pm"],
        )
        defaults.update(kwargs)
        return DocumentPlan(**defaults)

    def test_valid_plan(self):
        plan = self._make_plan()
        assert plan.document_type == "pm_schedule"
        assert len(plan.sections) == 2

    def test_invalid_output_format(self):
        with pytest.raises(Exception):
            self._make_plan(output_format="html")

    def test_invalid_document_type(self):
        with pytest.raises(Exception):
            self._make_plan(document_type="unknown_type")

    def test_empty_sections_rejected(self):
        from document_generator.schemas import DocumentPlan
        with pytest.raises(Exception):
            DocumentPlan(
                document_type="pm_schedule",
                title="T",
                generated_for="S",
                output_format="docx",
                sections=[],
                footer={},
                data_sources_required=[],
            )

    def test_all_document_types(self):
        from document_generator.schemas import DocumentSection
        types = [
            "pm_schedule", "wo_report", "wo_package", "parts_reorder",
            "inspection_template", "asset_health_summary",
            "maintenance_calendar", "inspection_report", "custom",
        ]
        from document_generator.schemas import DocumentPlan
        section = DocumentSection(type="summary_table", heading="H", data_source="assets")
        for doc_type in types:
            plan = DocumentPlan(
                document_type=doc_type,
                title="T",
                generated_for="S",
                output_format="docx",
                sections=[section],
                footer={"generated_by": "X", "timestamp": "T", "audit_id": "A"},
                data_sources_required=["assets"],
            )
            assert plan.document_type == doc_type

    def test_all_output_formats(self):
        from document_generator.schemas import DocumentPlan, DocumentSection
        section = DocumentSection(type="summary_table", heading="H", data_source="assets")
        for fmt in ["docx", "xlsx", "pdf"]:
            p = DocumentPlan(
                document_type="custom",
                title="T",
                generated_for="S",
                output_format=fmt,
                sections=[section],
                footer={"generated_by": "X", "timestamp": "T", "audit_id": "A"},
                data_sources_required=["assets"],
            )
            assert p.output_format == fmt


# ── Planner vote logic ──────────────────────────────────────────────────────────

class TestPlannerVote:
    def _make_plan_run(self, section_types, valid=True):
        from document_generator.schemas import DocumentSection, DocumentPlan, PlanningRunResult
        sections = [
            DocumentSection(type=t, heading=f"Section {i}", data_source="assets")
            for i, t in enumerate(section_types)
        ]
        plan = DocumentPlan(
            document_type="pm_schedule",
            title="T",
            generated_for="S",
            output_format="docx",
            sections=sections,
            footer={"generated_by": "X", "timestamp": "T", "audit_id": "A"},
            data_sources_required=["assets"],
        )
        return PlanningRunResult(run_number=1, plan=plan, valid=valid)

    def test_majority_vote_simple(self):
        from document_generator.planner import _vote_on_plan
        r1 = self._make_plan_run(["summary_table", "schedule_grid"])
        r2 = self._make_plan_run(["summary_table", "schedule_grid"])
        r3 = self._make_plan_run(["parts_table"])
        winner = _vote_on_plan([r1, r2, r3])
        assert len(winner.sections) == 2
        assert winner.sections[0].type == "summary_table"

    def test_tiebreak_uses_first_matching(self):
        from document_generator.planner import _vote_on_plan
        # All different fingerprints — tiebreak: first matching
        r1 = self._make_plan_run(["summary_table"])
        r2 = self._make_plan_run(["schedule_grid"])
        winner = _vote_on_plan([r1, r2])
        assert winner is not None

    def test_vote_on_single_valid_run(self):
        from document_generator.planner import _vote_on_plan
        r1 = self._make_plan_run(["summary_table", "kpi_summary", "signature_block"])
        winner = _vote_on_plan([r1])
        assert len(winner.sections) == 3

    def test_build_planning_prompt_basic(self):
        from document_generator.planner import _build_planning_prompt
        prompt = _build_planning_prompt("Build a PM schedule", "pm_schedule", None)
        assert "PM schedule" in prompt
        assert "pm_schedule" in prompt

    def test_build_planning_prompt_with_context(self):
        from document_generator.planner import _build_planning_prompt
        context = {"assets": [{"asset_code": "MOB-AHU-001", "asset_name": "AHU 1"}]}
        prompt = _build_planning_prompt("Create report", "wo_report", context)
        assert "assets" in prompt
        assert "1 rows" in prompt


# ── Validator (EL-7.DOC.PLAN) ──────────────────────────────────────────────────

class TestDocumentPlanValidator:
    def _make_plan(self, sections=None, output_format="docx", data_sources=None, footer=None):
        from document_generator.schemas import DocumentPlan, DocumentSection
        if sections is None:
            sections = [DocumentSection(type="summary_table", heading="Assets", data_source="assets")]
        if footer is None:
            footer = {"generated_by": "X", "timestamp": "T", "audit_id": "A"}
        return DocumentPlan(
            document_type="pm_schedule",
            title="T",
            generated_for="S",
            output_format=output_format,
            sections=sections,
            footer=footer,
            data_sources_required=data_sources or ["assets"],
        )

    def test_extract_table_name_plain(self):
        from document_generator.validator import _extract_table_name
        assert _extract_table_name("assets") == "assets"

    def test_extract_table_name_sql_where(self):
        from document_generator.validator import _extract_table_name
        assert _extract_table_name("work_orders WHERE status = 'Open'") == "work_orders"

    def test_extract_table_name_schema_qualified(self):
        from document_generator.validator import _extract_table_name
        assert _extract_table_name("plenum_cafm.assets") == "assets"

    def test_extract_table_name_empty(self):
        from document_generator.validator import _extract_table_name
        assert _extract_table_name("") is None

    def test_extract_table_name_from_clause(self):
        from document_generator.validator import _extract_table_name
        assert _extract_table_name("SELECT * FROM inspections WHERE id = 1") == "inspections"

    @pytest.mark.asyncio
    async def test_validation_passes_with_good_plan(self):
        from document_generator.validator import validate_document_plan
        plan = self._make_plan()

        session = AsyncMock()
        # _dry_run_table returns a row → table has data
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        session.execute = AsyncMock(return_value=mock_result)

        result = await validate_document_plan(plan, session)
        assert result.passed is True
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_validation_fails_missing_footer_keys(self):
        from document_generator.validator import validate_document_plan
        plan = self._make_plan(footer={"generated_by": "X"})  # missing timestamp + audit_id

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = 1
        session.execute = AsyncMock(return_value=mock_result)

        result = await validate_document_plan(plan, session)
        assert result.passed is False
        assert any("Footer" in e for e in result.errors)

    @pytest.mark.asyncio
    async def test_validation_warns_when_table_empty(self):
        from document_generator.validator import validate_document_plan
        plan = self._make_plan()

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = None  # empty table
        session.execute = AsyncMock(return_value=mock_result)

        result = await validate_document_plan(plan, session)
        # Empty table generates a warning, not an error
        assert result.data_sources_resolved == 0
        assert len(result.warnings) > 0

    def test_format_validation_errors(self):
        from document_generator.validator import ValidationResult, format_validation_errors_for_replanning
        r = ValidationResult(passed=False, errors=["Error A", "Error B"], warnings=["Warn C"])
        msg = format_validation_errors_for_replanning(r)
        assert "Error A" in msg
        assert "Error B" in msg
        assert "corrected DocumentPlan" in msg


# ── Renderer ───────────────────────────────────────────────────────────────────

class TestRenderer:
    def test_format_cell_value_none(self):
        from document_generator.renderer import _format_cell_value
        assert _format_cell_value(None) == ""

    def test_format_cell_value_bool_true(self):
        from document_generator.renderer import _format_cell_value
        assert _format_cell_value(True) == "Yes"

    def test_format_cell_value_bool_false(self):
        from document_generator.renderer import _format_cell_value
        assert _format_cell_value(False) == "No"

    def test_format_cell_value_dict(self):
        from document_generator.renderer import _format_cell_value
        import json
        result = _format_cell_value({"key": "value"})
        assert "key" in result

    def test_format_cell_value_string(self):
        from document_generator.renderer import _format_cell_value
        assert _format_cell_value("hello") == "hello"

    def test_format_cell_value_int(self):
        from document_generator.renderer import _format_cell_value
        assert _format_cell_value(42) == "42"

    def test_sample_values_empty_rows(self):
        from document_generator.renderer import _sample_values
        result = _sample_values([], "assets")
        assert result == []

    def test_sample_values_returns_at_most_10(self):
        from document_generator.renderer import _sample_values
        rows = [{"col1": f"val{i}", "col2": f"other{i}"} for i in range(30)]
        result = _sample_values(rows, "assets")
        assert len(result) <= 10

    def test_sample_values_structure(self):
        from document_generator.renderer import _sample_values
        rows = [{"asset_code": "MOB-001", "asset_name": "AHU 1"}]
        result = _sample_values(rows, "assets")
        for item in result:
            assert "value" in item
            assert "table" in item
            assert "column" in item
        assert all(item["table"] == "assets" for item in result)

    def test_now_ms(self):
        from document_generator.renderer import _now_ms
        t = _now_ms()
        assert isinstance(t, int)
        assert t > 0

    @pytest.mark.asyncio
    async def test_fetch_section_rows_free_text(self):
        from document_generator.renderer import _fetch_section_rows
        from document_generator.schemas import DocumentSection
        section = DocumentSection(type="free_text_header", heading="H", data_source="Some text")
        session = AsyncMock()
        rows, sampled = await _fetch_section_rows(section, session)
        assert rows == []
        assert sampled == []

    @pytest.mark.asyncio
    async def test_fetch_section_rows_signature_block(self):
        from document_generator.renderer import _fetch_section_rows
        from document_generator.schemas import DocumentSection
        section = DocumentSection(type="signature_block", heading="Sign", data_source="assets")
        session = AsyncMock()
        rows, sampled = await _fetch_section_rows(section, session)
        assert rows == []

    @pytest.mark.asyncio
    async def test_render_docx_produces_bytes(self):
        from document_generator.renderer import _render_docx
        from document_generator.schemas import DocumentPlan, DocumentSection

        section = DocumentSection(
            type="summary_table",
            heading="Assets",
            data_source="assets",
            columns=["asset_code", "asset_name"],
        )
        plan = DocumentPlan(
            document_type="pm_schedule",
            title="Test Plan",
            generated_for="All assets",
            output_format="docx",
            sections=[section],
            footer={"generated_by": "CAFM", "timestamp": "2026-03-27", "audit_id": "test-123"},
            data_sources_required=["assets"],
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [
            {"asset_code": "MOB-AHU-001", "asset_name": "AHU 1"}
        ]
        session.execute = AsyncMock(return_value=mock_result)

        content, sampled = await _render_docx(plan, session)
        assert isinstance(content, bytes)
        assert len(content) > 0
        # DOCX files start with PK (zip signature)
        assert content[:2] == b"PK"

    @pytest.mark.asyncio
    async def test_render_xlsx_produces_bytes(self):
        from document_generator.renderer import _render_xlsx
        from document_generator.schemas import DocumentPlan, DocumentSection

        section = DocumentSection(
            type="parts_table",
            heading="Parts",
            data_source="spare_parts",
        )
        plan = DocumentPlan(
            document_type="parts_reorder",
            title="Parts Report",
            generated_for="All parts",
            output_format="xlsx",
            sections=[section],
            footer={"generated_by": "CAFM", "timestamp": "2026-03-27", "audit_id": "test-456"},
            data_sources_required=["spare_parts"],
        )

        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.mappings.return_value.all.return_value = [
            {"part_code": "MOTOR-8HP", "stock_on_hand": 0}
        ]
        session.execute = AsyncMock(return_value=mock_result)

        content, sampled = await _render_xlsx(plan, session)
        assert isinstance(content, bytes)
        assert len(content) > 0
        assert content[:2] == b"PK"  # XLSX is also a zip


# ── Eval Layer (EL-7.DOC.RENDER + EL-7.DOC.EVAL) ──────────────────────────────

class TestEvalLayer:
    def test_rule_based_verify_match(self):
        from eval_layer import _rule_based_verify
        candidates = [{"value": "MOB-AHU-001", "table": "assets", "column": "asset_code"}]
        source_rows = {"assets": [{"asset_code": "MOB-AHU-001", "asset_name": "AHU 1"}]}
        results = _rule_based_verify(candidates, source_rows)
        assert results == [True]

    def test_rule_based_verify_no_match(self):
        from eval_layer import _rule_based_verify
        candidates = [{"value": "FAKE-001", "table": "assets", "column": "asset_code"}]
        source_rows = {"assets": [{"asset_code": "MOB-AHU-001"}]}
        results = _rule_based_verify(candidates, source_rows)
        assert results == [False]

    def test_rule_based_verify_empty_source(self):
        from eval_layer import _rule_based_verify
        candidates = [{"value": "anything", "table": "assets", "column": "asset_code"}]
        source_rows = {}
        # No source rows → benefit of doubt → pass
        results = _rule_based_verify(candidates, source_rows)
        assert results == [True]

    def test_rule_based_verify_empty_value(self):
        from eval_layer import _rule_based_verify
        candidates = [{"value": "", "table": "assets", "column": "asset_code"}]
        source_rows = {"assets": [{"asset_code": "MOB-AHU-001"}]}
        results = _rule_based_verify(candidates, source_rows)
        assert results == [True]  # no value to check → pass

    def test_eval_result_pass_threshold(self):
        from eval_layer import EvalResult, _PASS_THRESHOLD
        r = EvalResult(eval_score=0.85, spot_checks_run=10, spot_checks_passed=9, held_for_review=False)
        assert not r.held_for_review
        assert r.eval_score >= _PASS_THRESHOLD

    def test_eval_result_fail_threshold(self):
        from eval_layer import EvalResult, _PASS_THRESHOLD
        r = EvalResult(eval_score=0.70, spot_checks_run=10, spot_checks_passed=7, held_for_review=True)
        assert r.held_for_review
        assert r.eval_score < _PASS_THRESHOLD

    @pytest.mark.asyncio
    async def test_evaluate_no_sampled_values(self):
        from eval_layer import evaluate_rendered_document
        session = AsyncMock()
        client = AsyncMock()

        result = await evaluate_rendered_document([], {}, session, client)
        assert result.eval_score == 1.0
        assert result.held_for_review is False

    @pytest.mark.asyncio
    async def test_evaluate_all_verified(self):
        from eval_layer import evaluate_rendered_document

        candidates = [
            {"value": "MOB-AHU-001", "table": "assets", "column": "asset_code"},
            {"value": "Air Handler", "table": "assets", "column": "category"},
        ]
        source_rows = {
            "assets": [
                {"asset_code": "MOB-AHU-001", "category": "Air Handler"}
            ]
        }

        session = AsyncMock()
        client = AsyncMock()

        result = await evaluate_rendered_document(candidates, source_rows, session, client)
        assert result.spot_checks_run > 0
        assert result.eval_score > 0


# ── Filler (EL-7.TEMPLATE) ────────────────────────────────────────────────────

class TestFillerParsingAndValidation:
    def test_parse_placeholders_basic(self):
        from document_generator.filler import _parse_placeholders
        text = "Asset: {{assets.asset_code}} Name: {{assets.asset_name}}"
        result = _parse_placeholders(text)
        assert len(result) == 2
        assert result[0].table == "assets"
        assert result[0].column == "asset_code"
        assert result[1].column == "asset_name"

    def test_parse_placeholders_deduplicates(self):
        from document_generator.filler import _parse_placeholders
        text = "{{assets.asset_code}} and again {{assets.asset_code}}"
        result = _parse_placeholders(text)
        assert len(result) == 1

    def test_parse_placeholders_with_filter(self):
        from document_generator.filler import _parse_placeholders
        text = "{{assets.asset_name:asset_code=MOB-AHU-001}}"
        result = _parse_placeholders(text)
        assert len(result) == 1
        assert result[0].filter_expr == "asset_code=MOB-AHU-001"

    def test_parse_placeholders_empty(self):
        from document_generator.filler import _parse_placeholders
        result = _parse_placeholders("No placeholders here.")
        assert result == []

    def test_parse_placeholders_multiple_tables(self):
        from document_generator.filler import _parse_placeholders
        text = "{{assets.asset_code}} {{work_orders.wo_code}} {{spare_parts.part_code}}"
        result = _parse_placeholders(text)
        assert len(result) == 3
        tables = {p.table for p in result}
        assert tables == {"assets", "work_orders", "spare_parts"}

    def test_placeholder_info_defaults(self):
        from document_generator.filler import PlaceholderInfo
        ph = PlaceholderInfo(raw="{{assets.asset_code}}", table="assets", column="asset_code", filter_expr=None)
        assert ph.resolved is False
        assert ph.resolved_value == ""
        assert ph.error == ""

    @pytest.mark.asyncio
    async def test_resolve_unknown_table_fails(self):
        from document_generator.filler import PlaceholderInfo, _resolve_placeholders
        ph = PlaceholderInfo(
            raw="{{bad_table.col}}", table="bad_table", column="col", filter_expr=None
        )
        session = AsyncMock()
        result = await _resolve_placeholders([ph], None, session)
        assert result[0].resolved is False
        assert "Unknown table" in result[0].error

    @pytest.mark.asyncio
    async def test_resolve_unknown_column_fails(self):
        from document_generator.filler import PlaceholderInfo, _resolve_placeholders
        ph = PlaceholderInfo(
            raw="{{assets.nonexistent}}", table="assets", column="nonexistent", filter_expr=None
        )
        session = AsyncMock()
        result = await _resolve_placeholders([ph], None, session)
        assert result[0].resolved is False
        assert "Unknown column" in result[0].error

    @pytest.mark.asyncio
    async def test_resolve_valid_placeholder(self):
        from document_generator.filler import PlaceholderInfo, _resolve_placeholders
        ph = PlaceholderInfo(
            raw="{{assets.asset_code}}", table="assets", column="asset_code", filter_expr=None
        )
        session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = "MOB-AHU-001"
        session.execute = AsyncMock(return_value=mock_result)

        result = await _resolve_placeholders([ph], None, session)
        assert result[0].resolved is True
        assert result[0].resolved_value == "MOB-AHU-001"

    @pytest.mark.asyncio
    async def test_fill_template_no_file(self):
        from document_generator.filler import fill_template
        session = AsyncMock()
        result = await fill_template(None, None, None, session)
        assert result.held_for_review is True
        assert result.eval_score == 0.0

    def test_fill_result_defaults(self):
        from document_generator.filler import FillResult
        r = FillResult(
            content=b"",
            output_format="docx",
            placeholders_total=5,
            placeholders_resolved=5,
            placeholders_missing=0,
            eval_score=0.9,
            held_for_review=False,
        )
        assert not r.held_for_review
        assert r.errors == []

    def test_extract_text_fallback(self):
        from document_generator.filler import _extract_text
        # Non-DOCX bytes → fallback to UTF-8
        result = _extract_text(b"hello world", "docx")
        # Should not crash (may return empty if docx parsing fails on raw bytes)
        assert isinstance(result, str)

    def test_table_column_map_coverage(self):
        from document_generator.filler import _TABLE_COLUMN_MAP
        assert "assets" in _TABLE_COLUMN_MAP
        assert "asset_code" in _TABLE_COLUMN_MAP["assets"]
        assert "work_orders" in _TABLE_COLUMN_MAP
        assert "wo_code" in _TABLE_COLUMN_MAP["work_orders"]
        assert "spare_parts" in _TABLE_COLUMN_MAP
        assert "inspections" in _TABLE_COLUMN_MAP


# ── Output Renderer ────────────────────────────────────────────────────────────

class TestOutputRenderer:
    def test_render_text_answer(self):
        from output_renderer import render_text_answer
        result = render_text_answer("17 open work orders.")
        assert result.format == "text"
        assert result.content_type == "text/plain"
        assert result.content == "17 open work orders."

    def test_render_text_with_audit_id(self):
        from output_renderer import render_text_answer
        result = render_text_answer("Answer.", audit_id="abc-123")
        assert result.audit_id == "abc-123"

    def test_render_json_answer(self):
        from output_renderer import render_json_answer
        import json
        result = render_json_answer({"rows": 17, "status": "ok"})
        assert result.format == "json"
        assert result.content_type == "application/json"
        parsed = json.loads(result.content)
        assert parsed["rows"] == 17

    def test_render_document_docx(self):
        from output_renderer import render_document_output
        result = render_document_output(b"PK...", "docx", "pm_schedule")
        assert result.format == "docx"
        assert "wordprocessingml" in result.content_type
        assert result.filename is not None
        assert result.filename.endswith(".docx")

    def test_render_document_xlsx(self):
        from output_renderer import render_document_output
        result = render_document_output(b"PK...", "xlsx", "parts_reorder")
        assert result.format == "xlsx"
        assert "spreadsheetml" in result.content_type
        assert result.filename.endswith(".xlsx")

    def test_render_document_pdf(self):
        from output_renderer import render_document_output
        result = render_document_output(b"%PDF...", "pdf", "wo_report")
        assert result.format == "pdf"
        assert result.content_type == "application/pdf"
        assert result.filename.endswith(".pdf")

    def test_render_held_for_review(self):
        from output_renderer import render_held_for_review
        import json
        result = render_held_for_review("pm_schedule", 0.72)
        assert result.format == "json"
        parsed = json.loads(result.content)
        assert parsed["status"] == "held_for_review"
        assert parsed["eval_score"] == 0.72
        assert "held for human review" in parsed["message"]

    def test_render_held_for_review_with_errors(self):
        from output_renderer import render_held_for_review
        import json
        result = render_held_for_review("wo_report", 0.50, errors=["Value not found"])
        parsed = json.loads(result.content)
        assert "Value not found" in parsed["errors"]

    def test_render_error(self):
        from output_renderer import render_error
        import json
        result = render_error("Something went wrong", "Detail here")
        parsed = json.loads(result.content)
        assert parsed["error"] == "Something went wrong"
        assert parsed["detail"] == "Detail here"

    def test_render_clarifying_question(self):
        from output_renderer import render_clarifying_question
        import json
        result = render_clarifying_question("Are you asking about assets or work orders?")
        parsed = json.loads(result.content)
        assert parsed["status"] == "needs_clarification"
        assert "assets or work orders" in parsed["question"]

    def test_get_content_type(self):
        from output_renderer import get_content_type
        assert get_content_type("text") == "text/plain"
        assert get_content_type("json") == "application/json"
        assert get_content_type("docx") == "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        assert get_content_type("xlsx") == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        assert get_content_type("pdf") == "application/pdf"
        assert get_content_type("unknown") == "application/octet-stream"

    def test_rendered_output_filename_contains_document_type(self):
        from output_renderer import render_document_output
        result = render_document_output(b"data", "docx", "asset_health_summary")
        assert "asset_health_summary" in result.filename

    def test_render_text_no_audit_id(self):
        from output_renderer import render_text_answer
        result = render_text_answer("No data found.")
        assert result.audit_id is None


# ── Base templates existence ───────────────────────────────────────────────────

class TestBaseTemplatesExist:
    BASE = Path(__file__).parent.parent / "src" / "document_generator" / "base_templates"

    def test_pm_schedule_exists(self):
        assert (self.BASE / "pm_schedule.docx").exists()

    def test_wo_report_exists(self):
        assert (self.BASE / "wo_report.docx").exists()

    def test_wo_package_exists(self):
        assert (self.BASE / "wo_package.docx").exists()

    def test_parts_reorder_exists(self):
        assert (self.BASE / "parts_reorder.xlsx").exists()

    def test_inspection_template_exists(self):
        assert (self.BASE / "inspection_template.docx").exists()

    def test_asset_health_summary_exists(self):
        assert (self.BASE / "asset_health_summary.docx").exists()

    def test_maintenance_calendar_exists(self):
        assert (self.BASE / "maintenance_calendar.xlsx").exists()

    def test_inspection_report_exists(self):
        assert (self.BASE / "inspection_report.docx").exists()

    def test_all_docx_are_valid_zip(self):
        """DOCX files are zip archives."""
        import zipfile
        for f in self.BASE.glob("*.docx"):
            assert zipfile.is_zipfile(f), f"{f.name} is not a valid DOCX (zip)"

    def test_all_xlsx_are_valid_zip(self):
        import zipfile
        for f in self.BASE.glob("*.xlsx"):
            assert zipfile.is_zipfile(f), f"{f.name} is not a valid XLSX (zip)"
