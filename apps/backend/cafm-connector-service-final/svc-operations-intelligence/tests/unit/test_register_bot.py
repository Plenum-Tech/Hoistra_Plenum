"""Unit tests for register bot name matching + auto-verify selection."""
from __future__ import annotations

from src.engines.compliance.channels.register_bot import (
    name_match_confidence,
    select_auto_verify_hit,
)


def test_exact_name_match_is_high():
    assert name_match_confidence("PestClear Services", "PestClear Services Ltd") >= 0.9


def test_unrelated_names_low():
    assert name_match_confidence("Acme Corp", "Completely Different Ltd") < 0.6


def test_auto_verify_selects_confident_hit():
    hits = [
        {
            "company_name": "A & D Environmental Services Limited",
            "lookup_key": "082405279",
            "match_source": "dump_file",
        },
        {
            "company_name": "Other Asbestos Co",
            "lookup_key": "999",
            "match_source": "dump_file",
        },
    ]
    hit = select_auto_verify_hit("A & D Environmental Services", hits, min_confidence=0.85)
    assert hit is not None
    assert hit["company_name"].startswith("A & D")


def test_auto_verify_rejects_ambiguous():
    hits = [
        {"company_name": "Smith Pest Control", "lookup_key": "1"},
        {"company_name": "Smith Pest Solutions", "lookup_key": "2"},
    ]
    hit = select_auto_verify_hit("Smith Pest", hits, min_confidence=0.85)
    # Both contain query strongly → ambiguous → no auto verify
    assert hit is None
