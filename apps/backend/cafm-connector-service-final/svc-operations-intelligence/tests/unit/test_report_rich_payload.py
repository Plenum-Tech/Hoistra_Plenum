"""A refreshed report card kept the prose and threw away the structured answer.

The compliance preflight returns everything a report page draws as cards — kpis, groups,
actions, insights, certificates, pending — as the OUTPUT of the `compliance_response` tool.
run_card() stored tool calls as {"tool", "input"} only, dropping every output, and left the
`rich` column at the None it was initialised to. So a card could never render as anything
but a wall of markdown, no matter what the orchestrator actually returned.
"""
from __future__ import annotations

import json

from src.engines.reports.cards import rich_from_tool_calls

RESPONSE = {
    "narrative": "Six building certificates have lapsed.",
    "kpis": [{"label": "Lapsed", "count": 6, "unit": "certificates"}],
    "groups": [{"owner": "AN Other House", "scope": "building", "points": ["FRA lapsed 2006"]}],
    "actions": [{"title": "Commission a fresh FRA", "severity": "critical", "scope": "building"}],
    "insights": [{"type": "anomaly", "text": "Three EICR extracts share one source file."}],
    "certificates": [{"name": "Fire Risk Assessment", "company": "AN Other", "status": "Lapsed"}],
    "pending": [{"name": "EICR 00568607", "what_is_pending": "awaiting PM confirmation"}],
    "offers": [{"kind": "verify_now", "label": "Verify now", "cert_id": "c1"}],
    "validation": {"rows_fetched": 42, "certificates_kept": 6},
}
PIPELINE = {"steps": [{"stage": "data", "label": "Read the register"}], "cost": {"calls": 2}}


def _calls(**over):
    return [
        {"tool": "list_building_certificates", "input": {}, "output": {"rows": []}},
        {"tool": "compliance_response", "input": {}, "output": over.get("response", RESPONSE)},
        {"tool": "compliance_pipeline", "input": {}, "output": over.get("pipeline", PIPELINE)},
    ]


def test_every_card_field_survives_the_round_trip():
    rich = rich_from_tool_calls(_calls())
    assert rich is not None
    assert rich["narrative"] == "Six building certificates have lapsed."
    assert rich["kpis"][0]["label"] == "Lapsed"
    assert rich["groups"][0]["owner"] == "AN Other House"
    assert rich["actions"][0]["severity"] == "critical"
    assert rich["insights"][0]["type"] == "anomaly"
    assert rich["certificates"][0]["status"] == "Lapsed"
    assert rich["pending"][0]["name"] == "EICR 00568607"
    assert rich["offers"][0]["kind"] == "verify_now"
    assert rich["validation"]["rows_fetched"] == 42
    assert rich["steps"][0]["stage"] == "data"
    assert rich["cost"]["calls"] == 2


def test_a_json_string_output_is_tolerated():
    calls = _calls()
    calls[1]["output"] = json.dumps(RESPONSE)
    rich = rich_from_tool_calls(calls)
    assert rich["kpis"][0]["count"] == 6


def test_the_pipeline_alone_still_produces_a_payload():
    """compliance_response does not fire on every turn; the trace alone is still worth
    keeping, and the list fields default to [] rather than None so the client can map them."""
    rich = rich_from_tool_calls([{"tool": "compliance_pipeline", "input": {}, "output": PIPELINE}])
    assert rich is not None
    assert rich["steps"][0]["label"] == "Read the register"
    for key in ("kpis", "groups", "actions", "insights", "certificates", "pending", "offers", "sections"):
        assert rich[key] == [], f"{key} must be an empty list, never None"
    assert rich["narrative"] == ""


def test_a_turn_with_no_compliance_payload_stores_nothing():
    assert rich_from_tool_calls([{"tool": "get_sites", "input": {}, "output": {"rows": []}}]) is None
    assert rich_from_tool_calls([]) is None


def test_a_malformed_output_does_not_take_the_whole_run_down():
    calls = _calls()
    calls[1]["output"] = "{not json at all"
    # The response is unreadable, but the pipeline beside it is still good.
    rich = rich_from_tool_calls(calls)
    assert rich is not None
    assert rich["steps"][0]["stage"] == "data"
    assert rich["kpis"] == []


def test_run_card_actually_assigns_what_it_stores():
    """Guards the specific defect: `rich` was initialised to None and never reassigned, so
    the column was written empty on every successful run."""
    import inspect

    from src.engines.reports import cards

    src = inspect.getsource(cards.run_card)
    assert "rich = rich_from_tool_calls(tool_calls)" in src, "run_card must populate rich before storing the run"
