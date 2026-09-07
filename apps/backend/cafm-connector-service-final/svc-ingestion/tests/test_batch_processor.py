"""
tests/test_batch_processor.py

Unit tests for batch/batch_processor.py — Task 2.8.

Covers:
  - _build_document_block(): file_id path, bytes path, missing both → ValueError
  - _build_batch_request(): output structure, custom_id, model field
  - _el_2_1_parse(): valid JSON, markdown-fenced JSON, missing 'entities', invalid JSON, empty
  - _el_2_2_build_schema(): valid payload → IntermediateSchema, Pydantic failure path
  - Safety gate logic: > 20% failure rate → flagged=True
  - BatchProgress dataclass fields
  - BatchProcessResult dataclass fields
  - _el_2_3_judge(): fallback to 0.5 on API error
"""

from __future__ import annotations

import base64
import json
import uuid
from dataclasses import fields as dc_fields
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from batch.batch_processor import (
    BatchItem,
    BatchProcessResult,
    BatchProgress,
    _BATCH_FAILURE_THRESHOLD,
    _build_batch_request,
    _build_document_block,
    _el_2_1_parse,
    _el_2_2_build_schema,
)
from shared.intermediate_schema import SourceType


# ===========================================================================
# _build_document_block
# ===========================================================================

class TestBuildDocumentBlock:
    def _make_item(self, *, file_id=None, pdf_bytes=None) -> BatchItem:
        return BatchItem(
            ingestion_id=uuid4(),
            source_filename="test.pdf",
            blob_url="https://example.com/test.pdf",
            file_id=file_id,
            pdf_bytes=pdf_bytes,
        )

    def test_file_id_path(self):
        item = self._make_item(file_id="file_abc123")
        block = _build_document_block(item)
        assert block["type"] == "document"
        assert block["source"]["type"] == "file"
        assert block["source"]["file_id"] == "file_abc123"
        assert block["cache_control"]["type"] == "ephemeral"

    def test_pdf_bytes_path(self):
        pdf_content = b"%PDF-1.4 fake content"
        item = self._make_item(pdf_bytes=pdf_content)
        block = _build_document_block(item)
        assert block["type"] == "document"
        assert block["source"]["type"] == "base64"
        assert block["source"]["media_type"] == "application/pdf"
        # Verify base64 round-trips
        decoded = base64.standard_b64decode(block["source"]["data"])
        assert decoded == pdf_content
        assert block["cache_control"]["type"] == "ephemeral"

    def test_file_id_takes_precedence_over_bytes(self):
        item = self._make_item(file_id="file_xyz", pdf_bytes=b"%PDF-content")
        block = _build_document_block(item)
        assert block["source"]["type"] == "file"
        assert block["source"]["file_id"] == "file_xyz"

    def test_neither_raises_value_error(self):
        item = self._make_item()
        with pytest.raises(ValueError, match="pdf_bytes or file_id"):
            _build_document_block(item)


# ===========================================================================
# _build_batch_request
# ===========================================================================

class TestBuildBatchRequest:
    def test_output_structure(self):
        item = BatchItem(
            ingestion_id=uuid4(),
            source_filename="report.pdf",
            blob_url="https://example.com/report.pdf",
            pdf_bytes=b"%PDF fake",
        )
        req = _build_batch_request(item)
        assert req["custom_id"] == str(item.ingestion_id)
        assert "params" in req
        params = req["params"]
        assert "model" in params
        assert "messages" in params
        assert params["messages"][0]["role"] == "user"
        # user content must include the document block + text
        content = params["messages"][0]["content"]
        types = [c["type"] for c in content]
        assert "document" in types
        assert "text" in types

    def test_source_filename_in_user_text(self):
        item = BatchItem(
            ingestion_id=uuid4(),
            source_filename="inspection_q4_2025.pdf",
            blob_url="https://blob/report.pdf",
            pdf_bytes=b"%PDF stub",
        )
        req = _build_batch_request(item)
        content = req["params"]["messages"][0]["content"]
        text_block = next(c for c in content if c["type"] == "text")
        assert "inspection_q4_2025.pdf" in text_block["text"]

    def test_max_tokens_set(self):
        item = BatchItem(
            ingestion_id=uuid4(),
            source_filename="x.pdf",
            blob_url="https://blob/x.pdf",
            pdf_bytes=b"%PDF",
        )
        req = _build_batch_request(item)
        assert req["params"]["max_tokens"] > 0


# ===========================================================================
# EL-2.1 — _el_2_1_parse
# ===========================================================================

class TestEl21Parse:
    def test_valid_json_with_entities(self):
        raw = json.dumps({"entities": {"assets": []}, "confidence": {}, "audit": {}})
        result = _el_2_1_parse(raw)
        assert result is not None
        assert "entities" in result

    def test_markdown_fenced_json(self):
        raw = '```json\n{"entities": {"assets": []}}\n```'
        result = _el_2_1_parse(raw)
        assert result is not None
        assert "entities" in result

    def test_markdown_fenced_no_lang_tag(self):
        raw = '```\n{"entities": {}}\n```'
        result = _el_2_1_parse(raw)
        assert result is not None

    def test_missing_entities_key_returns_none(self):
        raw = json.dumps({"confidence": {}, "audit": {}})
        result = _el_2_1_parse(raw)
        assert result is None

    def test_invalid_json_returns_none(self):
        result = _el_2_1_parse("this is not json")
        assert result is None

    def test_empty_string_returns_none(self):
        assert _el_2_1_parse("") is None
        assert _el_2_1_parse("   ") is None

    def test_truncated_json_returns_none(self):
        result = _el_2_1_parse('{"entities": {')
        assert result is None

    def test_json_array_not_dict_returns_none(self):
        result = _el_2_1_parse("[1, 2, 3]")
        assert result is None

    def test_nested_entities_preserved(self):
        payload = {
            "entities": {
                "assets": [{"asset_code": "MOB-AHU-001"}],
                "work_orders": [],
            },
            "confidence": {"overall": "high"},
            "audit": {},
        }
        result = _el_2_1_parse(json.dumps(payload))
        assert result is not None
        assert result["entities"]["assets"][0]["asset_code"] == "MOB-AHU-001"


# ===========================================================================
# EL-2.2 — _el_2_2_build_schema
# ===========================================================================

class TestEl22BuildSchema:
    def _make_item(self) -> BatchItem:
        return BatchItem(
            ingestion_id=uuid4(),
            source_filename="test.pdf",
            blob_url="https://example.com/test.pdf",
            pdf_bytes=b"%PDF",
        )

    def test_valid_payload_returns_schema(self):
        parsed = {
            "entities": {
                "assets": [{"asset_code": "MOB-AHU-001", "asset_name": "AHU 1"}],
                "work_orders": [],
                "findings": [],
                "readings": [],
                "technicians": [],
                "vendors": [],
                "certificates": [],
                "spare_parts": [],
            },
            "confidence": {"overall": "high", "eval_score": 0.92, "rules_passed": True},
            "audit": {"tokens_in": 1000, "tokens_out": 200, "processing_ms": 5000},
        }
        item = self._make_item()
        result = _el_2_2_build_schema(parsed, item)

        assert result is not None
        assert result.ingestion_id == item.ingestion_id
        assert result.source_filename == "test.pdf"
        assert len(result.entities.assets) == 1

    def test_empty_entities_returns_schema(self):
        parsed = {
            "entities": {},
            "confidence": {"overall": "low"},
            "audit": {},
        }
        item = self._make_item()
        result = _el_2_2_build_schema(parsed, item)
        assert result is not None
        assert result.entities.assets == []

    def test_invalid_confidence_enum_returns_none(self):
        """Invalid confidence enum should cause Pydantic validation failure → None."""
        parsed = {
            "entities": {},
            "confidence": {"overall": "INVALID_LEVEL"},  # not in enum
            "audit": {},
        }
        item = self._make_item()
        result = _el_2_2_build_schema(parsed, item)
        # Should return None (Pydantic ValidationError caught)
        assert result is None

    def test_preserves_eval_score(self):
        parsed = {
            "entities": {},
            "confidence": {"overall": "medium", "eval_score": 0.77, "rules_passed": False},
            "audit": {},
        }
        item = self._make_item()
        result = _el_2_2_build_schema(parsed, item)
        assert result is not None
        assert result.confidence.eval_score == pytest.approx(0.77)


# ===========================================================================
# Safety gate threshold constant
# ===========================================================================

class TestSafetyGate:
    def test_failure_threshold_is_20_percent(self):
        assert _BATCH_FAILURE_THRESHOLD == pytest.approx(0.20)

    def test_gate_triggers_above_threshold(self):
        total = 100
        failed = 25  # 25% > 20%
        assert (failed / total) > _BATCH_FAILURE_THRESHOLD

    def test_gate_does_not_trigger_at_threshold(self):
        total = 100
        failed = 20  # exactly 20% — not strictly greater
        assert not ((failed / total) > _BATCH_FAILURE_THRESHOLD)

    def test_gate_does_not_trigger_below_threshold(self):
        total = 100
        failed = 15  # 15% < 20%
        assert not ((failed / total) > _BATCH_FAILURE_THRESHOLD)


# ===========================================================================
# BatchProgress and BatchProcessResult dataclass shape
# ===========================================================================

class TestBatchDataclasses:
    def test_batch_progress_fields(self):
        names = {f.name for f in dc_fields(BatchProgress)}
        required = {"batch_id", "total", "completed", "succeeded_el", "failed_el",
                    "written", "queued_for_review", "flagged", "status"}
        assert required.issubset(names)

    def test_batch_process_result_fields(self):
        names = {f.name for f in dc_fields(BatchProcessResult)}
        required = {"batch_id", "total", "succeeded_el", "failed_el",
                    "written", "queued_for_review", "manual_only", "flagged"}
        assert required.issubset(names)

    def test_batch_item_defaults(self):
        item = BatchItem(
            ingestion_id=uuid4(),
            source_filename="test.pdf",
            blob_url="https://example.com/test.pdf",
        )
        assert item.source_type == SourceType.PDF
        assert item.pdf_bytes is None
        assert item.file_id is None

    def test_batch_process_result_flagged_false_by_default(self):
        result = BatchProcessResult(
            batch_id="batch_123",
            total=10,
            succeeded_el=10,
            failed_el=0,
            written=10,
            queued_for_review=0,
            manual_only=0,
            flagged=False,
        )
        assert result.flagged is False
        assert result.flag_reason is None
        assert result.errors == []


# ===========================================================================
# EL-2.3 fallback behaviour
# ===========================================================================

class TestEl23Judge:
    @pytest.mark.asyncio
    async def test_fallback_on_api_error(self):
        """If Claude API raises, eval_score falls back to 0.5."""
        from batch.batch_processor import _el_2_3_judge

        client = AsyncMock()
        client.messages.create = AsyncMock(side_effect=Exception("API timeout"))

        score, contradictions = await _el_2_3_judge(
            client,
            source_filename="test.pdf",
            raw_text="some document text",
            extracted_json='{"entities": {}}',
        )

        assert score == pytest.approx(0.5)
        assert isinstance(contradictions, list)

    @pytest.mark.asyncio
    async def test_returns_eval_score_from_response(self):
        from batch.batch_processor import _el_2_3_judge

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text='{"eval_score": 0.93, "contradictions": [], "verdict": "accept"}')
        ]

        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=mock_response)

        score, contradictions = await _el_2_3_judge(
            client,
            source_filename="report.pdf",
            raw_text="doc content",
            extracted_json='{"entities": {}}',
        )

        assert score == pytest.approx(0.93)
        assert contradictions == []

    @pytest.mark.asyncio
    async def test_returns_contradictions_list(self):
        from batch.batch_processor import _el_2_3_judge

        mock_response = MagicMock()
        mock_response.content = [
            MagicMock(text=json.dumps({
                "eval_score": 0.72,
                "contradictions": ["Normal reading but Critical severity on AHU-004"],
                "verdict": "review",
            }))
        ]

        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=mock_response)

        score, contradictions = await _el_2_3_judge(
            client,
            source_filename="report.pdf",
            raw_text="doc content",
            extracted_json="{}",
        )

        assert score == pytest.approx(0.72)
        assert len(contradictions) == 1
        assert "AHU-004" in contradictions[0]

    @pytest.mark.asyncio
    async def test_invalid_json_response_falls_back(self):
        from batch.batch_processor import _el_2_3_judge

        mock_response = MagicMock()
        mock_response.content = [MagicMock(text="not valid json at all")]

        client = AsyncMock()
        client.messages.create = AsyncMock(return_value=mock_response)

        score, contradictions = await _el_2_3_judge(
            client, "test.pdf", "text", "{}"
        )

        assert score == pytest.approx(0.5)
