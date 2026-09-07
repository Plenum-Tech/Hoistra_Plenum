"""Phases 1–7 — session workspace, routing, WO confidence, connectors."""
import pytest

from src.agents.session_workspace import (
    ROUTE_UDR_INGEST,
    ROUTE_UDR_MAP,
    ROUTE_WO_CLARIFY,
    ROUTE_WO_INTAKE,
    classify_route_intent,
    default_session_state,
    score_udr_intent,
    score_work_request,
    work_request_confidence_band,
    workspace_has_ingestion,
)
from src.integrations.source_connectors import list_source_connectors


def test_route_intent_udr_mapping():
    state = default_session_state()
    intent = classify_route_intent("please run udr mapping now", state)
    assert intent == ROUTE_UDR_MAP


# ── Top-level UDR intent: technical + non-technical phrasings ────────────────────
@pytest.mark.parametrize(
    "msg",
    [
        "create a unified database from my data",
        "i want data unification across systems",
        "let's do a data migration",
        "centralize my assets",
        "centralized asset management please",
        "i need maintenance centralization",
        "set up centralized work management",
        "knowledge centralization for the portfolio",
        "workspace centralization",
        "consolidate all my operations data",
    ],
)
def test_route_intent_udr_unify_terms(msg):
    assert classify_route_intent(msg, default_session_state()) == ROUTE_UDR_MAP


@pytest.mark.parametrize(
    "msg",
    [
        "ingest these files and unify my data",
        "connect my data to the platform",
        "import and centralize my asset records",
    ],
)
def test_route_intent_udr_unify_ingest_cue(msg):
    assert classify_route_intent(msg, default_session_state()) == ROUTE_UDR_INGEST


def test_udr_intent_does_not_hijack_work_order():
    # 'create a work order' must reach the WO route, never the UDR umbrella.
    assert classify_route_intent("create a work order for the chiller", default_session_state()) == ROUTE_WO_INTAKE
    assert score_udr_intent("create a work order for the chiller") == 0.0


def test_udr_intent_does_not_hijack_fiix():
    # 'connect fiix' is a Fiix action, not a generic UDR 'data connection'.
    from src.agents.session_workspace import ROUTE_FIIX_SYNC

    assert classify_route_intent("connect fiix and pull schema", default_session_state()) == ROUTE_FIIX_SYNC


def test_udr_intent_does_not_hijack_plain_maintenance_request():
    # An implicit work request should still go to WO clarification, not the UDR.
    assert classify_route_intent("the pump is broken and leaking, urgent", default_session_state()) == ROUTE_WO_CLARIFY


def test_route_intent_wo_clarify_when_pending():
    state = default_session_state()
    state["pending_wo_clarification"] = True
    assert classify_route_intent("hello", state) == ROUTE_WO_CLARIFY


def test_work_request_confidence_high():
    msg = "chiller at tower a keeps tripping urgent need technician today"
    assert work_request_confidence_band(msg) in ("high", "medium")
    assert score_work_request(msg) >= 0.4


def test_workspace_ingestion_fiix():
    state = default_session_state()
    state["fiix_ingestion_id"] = "abc"
    assert workspace_has_ingestion(state)


def test_source_connectors_list():
    connectors = list_source_connectors()
    types = {c["source_type"] for c in connectors}
    assert "fiix" in types
    assert "file_upload" in types
