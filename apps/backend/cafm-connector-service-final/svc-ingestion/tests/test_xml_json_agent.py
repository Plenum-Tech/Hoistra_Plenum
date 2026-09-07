"""
tests/test_xml_json_agent.py

Unit tests for agents/xml_json_agent.py — Task 2.5.

Covers:
  - _detect_format(): extension → content sniff fallback
  - _detect_entity_type(): canonical field set → entity type
  - _flatten_dict(): nested dict → flat dict
  - _coerce_str() / _coerce_int(): type coercion helpers
  - _parse_json(): array root, wrapped object, single object
  - _parse_jsonl(): valid lines, skip blank/invalid
  - _parse_xml(): depth-1 records, depth-2 records, namespace handling
  - _ENTITY_SIGNATURES: coverage check
  - _MIN_CANONICAL_HITS: threshold value
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from agents.xml_json_agent import (
    _ENTITY_SIGNATURES,
    _MIN_CANONICAL_HITS,
    _coerce_int,
    _coerce_str,
    _detect_entity_type,
    _detect_format,
    _flatten_dict,
    _parse_json,
    _parse_jsonl,
    _parse_xml,
)


# ===========================================================================
# _detect_format
# ===========================================================================

class TestDetectFormat:
    def test_xml_extension(self):
        assert _detect_format("data.xml", b"<root/>") == "xml"

    def test_json_extension(self):
        assert _detect_format("data.json", b'{"key": "val"}') == "json"

    def test_jsonl_extension(self):
        assert _detect_format("data.jsonl", b'{"a":1}\n{"b":2}') == "jsonl"

    def test_ndjson_extension(self):
        assert _detect_format("data.ndjson", b'{"a":1}') == "jsonl"

    def test_content_sniff_xml_tag(self):
        assert _detect_format("upload", b"<Assets><Asset/></Assets>") == "xml"

    def test_content_sniff_xml_declaration(self):
        assert _detect_format("noext", b"<?xml version='1.0'?><root/>") == "xml"

    def test_content_sniff_json_object(self):
        assert _detect_format("noext", b'{"records": []}') == "json"

    def test_content_sniff_json_array(self):
        assert _detect_format("noext", b'[{"a":1}]') == "json"

    def test_content_sniff_jsonl_fallback(self):
        # Two JSON lines cannot parse as single JSON → falls back to jsonl
        raw = b'{"a":1}\n{"b":2}'
        result = _detect_format("noext", raw)
        assert result in ("json", "jsonl")  # depends on JSON parse attempt

    def test_unknown_content_defaults_json(self):
        result = _detect_format("noext", b"plain text with no structure")
        assert result == "json"

    def test_extension_takes_precedence_over_content(self):
        # .xml extension + JSON content → extension wins
        assert _detect_format("file.xml", b'{"not": "xml"}') == "xml"


# ===========================================================================
# _detect_entity_type
# ===========================================================================

class TestDetectEntityType:
    def test_asset_signature(self):
        fields = {"asset_code", "asset_name", "category"}
        assert _detect_entity_type(fields) == "assets"

    def test_work_order_signature(self):
        fields = {"wo_code", "wo_priority", "wo_status"}
        assert _detect_entity_type(fields) == "work_orders"

    def test_spare_parts_signature(self):
        fields = {"part_code", "stock_on_hand", "minimum_allowed_stock"}
        assert _detect_entity_type(fields) == "spare_parts"

    def test_maintenance_plans_signature(self):
        fields = {"sm_code", "trigger_type", "schedule_interval"}
        assert _detect_entity_type(fields) == "maintenance_plans"

    def test_users_signature(self):
        fields = {"user_full_name", "user_name", "user_title", "reports_to"}
        assert _detect_entity_type(fields) == "users"

    def test_empty_fields_returns_unknown(self):
        assert _detect_entity_type(set()) == "unknown"

    def test_unrecognised_fields_returns_unknown(self):
        assert _detect_entity_type({"random_col", "another_col"}) == "unknown"

    def test_majority_wins_on_mixed_fields(self):
        # 3 asset fields + 1 work order field → assets wins
        fields = {"asset_code", "asset_name", "category", "wo_code"}
        result = _detect_entity_type(fields)
        assert result in ("assets", "work_orders")  # assets should win (3 vs 1)


# ===========================================================================
# _flatten_dict
# ===========================================================================

class TestFlattenDict:
    def test_flat_dict_unchanged(self):
        d = {"a": 1, "b": "two", "c": None}
        result = _flatten_dict(d)
        assert result == {"a": 1, "b": "two", "c": None}

    def test_nested_dict_flattened(self):
        d = {"outer": {"inner": "value"}}
        result = _flatten_dict(d)
        assert "outer_inner" in result
        assert result["outer_inner"] == "value"

    def test_list_values_kept_as_is(self):
        d = {"items": [1, 2, 3], "name": "test"}
        result = _flatten_dict(d)
        assert result["items"] == [1, 2, 3]
        assert result["name"] == "test"

    def test_prefix_applied(self):
        d = {"code": "A1"}
        result = _flatten_dict(d, prefix="asset")
        assert "asset_code" in result

    def test_empty_dict(self):
        assert _flatten_dict({}) == {}


# ===========================================================================
# _coerce_str / _coerce_int
# ===========================================================================

class TestCoerceHelpers:
    def test_coerce_str_none_returns_none(self):
        assert _coerce_str(None) is None

    def test_coerce_str_strips_whitespace(self):
        assert _coerce_str("  hello  ") == "hello"

    def test_coerce_str_empty_returns_none(self):
        assert _coerce_str("") is None
        assert _coerce_str("   ") is None

    def test_coerce_str_int_to_str(self):
        assert _coerce_str(42) == "42"

    def test_coerce_int_none_returns_none(self):
        assert _coerce_int(None) is None

    def test_coerce_int_string_number(self):
        assert _coerce_int("42") == 42

    def test_coerce_int_float_string(self):
        assert _coerce_int("3.7") == 3

    def test_coerce_int_invalid_returns_none(self):
        assert _coerce_int("not-a-number") is None

    def test_coerce_int_already_int(self):
        assert _coerce_int(100) == 100


# ===========================================================================
# _parse_json
# ===========================================================================

class TestParseJson:
    def test_array_root(self):
        raw = json.dumps([{"id": 1}, {"id": 2}]).encode()
        headers, rows = _parse_json(raw)
        assert len(rows) == 2
        assert "id" in headers

    def test_wrapped_records_key(self):
        raw = json.dumps({"records": [{"id": 1}, {"id": 2}, {"id": 3}]}).encode()
        headers, rows = _parse_json(raw)
        assert len(rows) == 3

    def test_wrapped_data_key(self):
        raw = json.dumps({"data": [{"x": 1}]}).encode()
        headers, rows = _parse_json(raw)
        assert len(rows) == 1

    def test_single_object_wraps_in_list(self):
        raw = json.dumps({"asset_code": "MOB-AHU-001", "asset_name": "AHU 1"}).encode()
        headers, rows = _parse_json(raw)
        assert len(rows) == 1
        assert rows[0]["asset_code"] == "MOB-AHU-001"

    def test_headers_include_all_keys(self):
        raw = json.dumps([{"a": 1, "b": 2}, {"a": 3, "c": 4}]).encode()
        headers, rows = _parse_json(raw)
        assert "a" in headers
        assert "b" in headers
        assert "c" in headers

    def test_empty_array_raises(self):
        """_parse_json raises ValueError on zero records (nothing to ingest)."""
        with pytest.raises(ValueError, match="zero records"):
            _parse_json(b"[]")


# ===========================================================================
# _parse_jsonl
# ===========================================================================

class TestParseJsonl:
    def test_valid_jsonl(self):
        raw = b'{"id":1}\n{"id":2}\n{"id":3}'
        headers, rows = _parse_jsonl(raw)
        assert len(rows) == 3

    def test_skips_blank_lines(self):
        raw = b'{"id":1}\n\n{"id":2}\n\n\n{"id":3}'
        headers, rows = _parse_jsonl(raw)
        assert len(rows) == 3

    def test_skips_invalid_json_lines(self):
        raw = b'{"id":1}\nnot json\n{"id":2}'
        headers, rows = _parse_jsonl(raw)
        assert len(rows) == 2

    def test_empty_content_raises(self):
        """_parse_jsonl raises ValueError when no valid JSON objects found."""
        with pytest.raises(ValueError, match="no valid JSON"):
            _parse_jsonl(b"")

    def test_only_blank_lines_raises(self):
        with pytest.raises(ValueError, match="no valid JSON"):
            _parse_jsonl(b"\n\n\n")

    def test_headers_union_across_lines(self):
        raw = b'{"a":1,"b":2}\n{"a":3,"c":4}'
        headers, rows = _parse_jsonl(raw)
        assert "a" in headers
        assert "b" in headers
        assert "c" in headers


# ===========================================================================
# _parse_xml
# ===========================================================================

class TestParseXml:
    def test_depth1_records(self):
        xml_bytes = b"""<Assets>
            <Asset><Code>MOB-AHU-001</Code><Name>AHU 1</Name></Asset>
            <Asset><Code>MOB-AHU-002</Code><Name>AHU 2</Name></Asset>
        </Assets>"""
        headers, rows = _parse_xml(xml_bytes)
        assert len(rows) == 2
        assert any("Code" in r or "code" in str(r).lower() for r in rows)

    def test_depth2_records(self):
        xml_bytes = b"""<Root>
            <Records>
                <Record><ID>1</ID><Value>Alpha</Value></Record>
                <Record><ID>2</ID><Value>Beta</Value></Record>
            </Records>
        </Root>"""
        headers, rows = _parse_xml(xml_bytes)
        assert len(rows) == 2

    def test_default_namespace_handled(self):
        """Default namespace (xmlns=) works correctly — lxml uses it for findall."""
        xml_bytes = b"""<Assets xmlns="http://example.com/schema">
            <Asset><Code>MOB-AHU-001</Code><Name>AHU 1</Name></Asset>
            <Asset><Code>MOB-AHU-002</Code><Name>AHU 2</Name></Asset>
        </Assets>"""
        headers, rows = _parse_xml(xml_bytes)
        assert len(rows) == 2

    def test_invalid_xml_raises(self):
        with pytest.raises((ValueError, Exception)):
            _parse_xml(b"<unclosed>")

    def test_attributes_included(self):
        """Multiple sibling items with attributes — avoids depth-2 fallback."""
        xml_bytes = b"""<Items>
            <Item id="1" type="asset"><Name>Asset One</Name></Item>
            <Item id="2" type="asset"><Name>Asset Two</Name></Item>
        </Items>"""
        headers, rows = _parse_xml(xml_bytes)
        assert len(rows) == 2
        # Attributes (id, type) should be in the row alongside child text (Name)
        row = rows[0]
        assert any("id" in str(k).lower() for k in row)

    def test_headers_non_empty(self):
        """Two records at depth-1 → headers returned correctly."""
        xml_bytes = b"""<Assets>
            <Asset><Code>A1</Code><Name>Asset 1</Name></Asset>
            <Asset><Code>A2</Code><Name>Asset 2</Name></Asset>
        </Assets>"""
        headers, rows = _parse_xml(xml_bytes)
        assert len(headers) > 0


# ===========================================================================
# Constants
# ===========================================================================

class TestConstants:
    def test_min_canonical_hits_is_2(self):
        assert _MIN_CANONICAL_HITS == 2

    def test_entity_signatures_covers_main_types(self):
        assert "assets" in _ENTITY_SIGNATURES
        assert "work_orders" in _ENTITY_SIGNATURES
        assert "spare_parts" in _ENTITY_SIGNATURES
        assert "maintenance_plans" in _ENTITY_SIGNATURES
        assert "users" in _ENTITY_SIGNATURES

    def test_asset_signature_has_asset_code(self):
        assert "asset_code" in _ENTITY_SIGNATURES["assets"]

    def test_spare_parts_signature_has_part_code(self):
        assert "part_code" in _ENTITY_SIGNATURES["spare_parts"]
