"""
BE3 — DeepAgent orchestrator.
Wraps a LangGraph ReAct agent over all 54 CAFM tools with the CAFM system prompt.

Tool breakdown:
  Meta (6)        : write_todos, task, write_file, read_file, memory_set, memory_get
  UDR (13)        : get_schema, lookup_user, query_table, udr_agent_query,
                    udr_list_tables, udr_describe_table, udr_read_records, udr_get_record,
                    udr_search_records, udr_create_record, udr_update_record,
                    udr_delete_record, udr_execute_select
  WO Engine (22)  : 5 dynamic approval + 4 intelligent pipeline + 8 CRUD + 5 reference lookups
  Migration (8)   : start_migration, run_migration,
                    submit_pre_semantic, submit_field_mapping, submit_hierarchy,
                    get_migration_status, get_migration_mappings, list_migrations
  Fiix (10)       : get_fiix_setup_status, configure_fiix_credentials, test_fiix_connection,
                    fetch_fiix_schema, start_fiix_schema_mapping, get_schema_mapping_status,
                    continue_schema_mapping_gate, start_fiix_ingestion, get_fiix_ingestion_status,
                    list_fiix_ingestion_jobs
  Doc RAG (6)     : index_document, query_docs, semantic_search, extract_text,
                    get_document_metadata, delete_document
  Compliance Engine + Contract + Energy (flattened; legacy compliance via task())

Migration flow (mirrors frontend UI):
  run_migration() drives the pipeline automatically — auto-advances step_paused nodes,
  auto-confirms the write gate, and returns only when a user-decision gate fires.
  No LangGraph interrupt() gates in migration tools — the orchestrator handles HITL
  naturally through conversation (gate payload shown to user → user decides → submit_*).
"""
from __future__ import annotations

import asyncio
import json
import time
import re
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.messages.utils import count_tokens_approximately, trim_messages
from langgraph.errors import GraphInterrupt
from langgraph.prebuilt import create_react_agent
from langgraph.types import Command

from ..config import settings
from ..llm_factory import create_chat_model, friendly_openai_error
from .compliance_engine_agent import COMPLIANCE_ENGINE_TOOLS
from .compliance_offers import offers_for_missing_type, offers_for_row
from .contract_performance_agent import CONTRACT_PERFORMANCE_TOOLS
from .energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS
from .doc_rag_agent import (
    delete_document,
    extract_text,
    get_document_metadata,
    index_document,
    list_doc_rag_db_tables,
    list_row_index_tables,
    match_document_to_rows,
    query_docs,
    semantic_search,
)
from .meta_tools import (
    init_meta_tools,
    memory_get,
    memory_set,
    read_file,
    run_phase2_engine_verbose,
    select_skill,
    set_session_context,
    task,
    write_file,
    write_todos,
)
from .migration_agent import (
    start_migration,
    run_migration,
    submit_pre_semantic,
    submit_field_mapping,
    submit_hierarchy,
    get_migration_status,
    get_migration_mappings,
    list_migrations,
)
from .schema_mapper_agent import continue_schema_mapping_gate
from .fiix_agent import (
    configure_fiix_credentials,
    fetch_fiix_schema,
    get_fiix_ingestion_status,
    get_fiix_setup_status,
    get_schema_mapping_status,
    list_fiix_ingestion_jobs,
    start_fiix_ingestion,
    start_fiix_schema_mapping,
    test_fiix_connection,
)
from .fiix_credential_parse import (
    fiix_setup_status_snapshot,
    merge_fiix_credentials_from_message,
    message_looks_like_fiix_credentials,
)
from .ingest_batch_agent import get_ingest_batch_status, list_session_ingest_batches
from .connector_tools import list_available_source_connectors, test_source_connector_connection
from .udr_hybrid_tools import (
    retrieve_workspace_corpus_summary,
    retrieve_vector_evidence,
    resolve_cross_source_links,
    answer_with_graph_context,
)
from .udr_response_evaluator import evaluate_udr_response, has_udr_tool_calls
from .session_workspace import (
    ROUTE_UDR_INGEST,
    ROUTE_UDR_MAP,
    ROUTE_WO_CLARIFY,
    ROUTE_WO_INTAKE,
    ROUTE_GENERAL,
    attach_route_to_result,
    workflow_stream_completion_payload,
    resolve_route_intent,
    default_session_state,
    get_session_state,
    mark_documents_ingested,
    record_fiix_ingestion_started,
    ROUTE_FIIX_SYNC,
    build_session_runtime_context,
    record_conversation_turn,
    clear_pending_fiix_confirm,
    clear_pending_schema_gate_confirm,
    fiix_credentials_configured,
    resolve_active_schema_mapping_id,
    set_pending_fiix_confirm,
    set_pending_schema_gate_confirm,
    workspace_has_ingestion,
    workspace_snapshot,
    work_request_confidence_band,
    append_pending_batch,
)
from .phase2_intents import Phase2AgentId, resolve_phase2_engine
from .agent_router import as_phase2_engine, select_agent
from .compliance_facts import compute_pack_facts
from .compliance_router import compliance_skill_path_enabled
from . import llm_cost
from .skills import prompt_doc
from .system_prompt import build_system_prompt
from .udr_agent import (
    get_schema,
    lookup_user,
    query_table,
    find_asset,
    find_location,
    get_asset_documents,
    udr_agent_query,
    udr_list_tables,
    udr_describe_table,
    udr_read_records,
    udr_get_record,
    udr_search_records,
    udr_create_record,
    udr_update_record,
    udr_delete_record,
    udr_execute_select,
)
from .wo_engine_agent import (
    # Dynamic approval (5)
    suggest_approval_chain,
    request_approval_chain,
    send_approval_request_email,
    get_approval_chain,
    customize_approval_chain,
    respond_to_approval_step,
    # Intelligent pipeline (4)
    prepare_intelligent_work_order,
    confirm_intelligent_work_order_creation,
    create_intelligent_work_order,
    trigger_ppm_work_order,
    process_email_work_order,
    # CRUD + lifecycle (8)
    create_work_order,
    get_work_order,
    update_work_order,
    list_work_orders,
    transition_work_order,
    approve_work_order,
    close_work_order,
    get_work_order_history,
    get_work_order_status_track,
    # Reference lookups (5)
    search_assets,
    get_asset_details,
    search_locations,
    find_ppm_schedules,
    get_dashboard_stats,
)

log = structlog.get_logger(__name__)

# Re-export for backward compatibility (tests, workers)
_SESSION_UDR_STATE = {}  # unused; state in session_workspace


def _default_session_state() -> dict[str, Any]:
    return default_session_state()


_AFFIRMATIVE_TOKENS = {
    "yes",
    "y",
    "ok",
    "okay",
    "sure",
    "proceed",
    "go ahead",
    "create it",
    "confirm",
    "continue",
    "yes continue",
}
_NEGATIVE_TOKENS = {
    "no",
    "n",
    "nope",
    "cancel",
    "stop",
    "not now",
    "don't",
    "dont",
}

ALL_TOOLS = [
    # Meta-capabilities (7)
    write_todos,
    select_skill,
    task,
    write_file,
    read_file,
    memory_set,
    memory_get,
    # UDR (11+)
    get_schema,
    lookup_user,
    query_table,
    find_asset,
    find_location,
    get_asset_documents,
    udr_agent_query,
    udr_list_tables,
    udr_describe_table,
    udr_read_records,
    udr_get_record,
    udr_search_records,
    udr_create_record,
    udr_update_record,
    udr_delete_record,
    udr_execute_select,
    # WO Engine — intelligent pipeline (4) — prepare before create
    prepare_intelligent_work_order,
    confirm_intelligent_work_order_creation,
    create_intelligent_work_order,
    trigger_ppm_work_order,
    process_email_work_order,
    # WO Engine — CRUD + lifecycle (8)
    create_work_order,
    # WO Engine — dynamic approval (5)
    suggest_approval_chain,
    request_approval_chain,
    send_approval_request_email,
    get_approval_chain,
    customize_approval_chain,
    respond_to_approval_step,
    get_work_order,
    update_work_order,
    list_work_orders,
    transition_work_order,
    approve_work_order,
    close_work_order,
    get_work_order_history,
    get_work_order_status_track,
    # WO Engine — reference lookups (5)
    search_assets,
    get_asset_details,
    search_locations,
    find_ppm_schedules,
    get_dashboard_stats,
    # Migration (8)
    start_migration,
    run_migration,
    submit_pre_semantic,
    submit_field_mapping,
    submit_hierarchy,
    get_migration_status,
    get_migration_mappings,
    list_migrations,
    # Fiix CMMS live schema + sync (10)
    get_fiix_setup_status,
    configure_fiix_credentials,
    test_fiix_connection,
    fetch_fiix_schema,
    start_fiix_schema_mapping,
    get_schema_mapping_status,
    continue_schema_mapping_gate,
    start_fiix_ingestion,
    get_fiix_ingestion_status,
    list_fiix_ingestion_jobs,
    # Bulk ingest (2)
    get_ingest_batch_status,
    list_session_ingest_batches,
    # Hybrid UDR (4)
    retrieve_workspace_corpus_summary,
    retrieve_vector_evidence,
    resolve_cross_source_links,
    answer_with_graph_context,
    # Source connectors (2)
    list_available_source_connectors,
    test_source_connector_connection,
    # Doc RAG (9)
    index_document,
    query_docs,
    semantic_search,
    extract_text,
    get_document_metadata,
    delete_document,
    list_row_index_tables,
    list_doc_rag_db_tables,
    match_document_to_rows,
]

# Phase 2 engines are NOT on the main ReAct tool list.
# After single-door extraction (or intent keywords), the orchestrator invokes
# ONLY the matching engine via run_phase2_engine / task() — keeps OpenAI ≤128
# and avoids cross-engine tool noise.
PHASE2_ENGINE_TOOLS: dict[str, list] = {
    "compliance": list(COMPLIANCE_ENGINE_TOOLS),
    "contract_performance": list(CONTRACT_PERFORMANCE_TOOLS),
    "energy_intelligence": list(ENERGY_INTELLIGENCE_TOOLS),
}

# Catalog for GET /tools (discovery) — includes Phase 2 engines even though
# they are bound only when content selects that engine.
TOOL_CATALOG = [
    *ALL_TOOLS,
    *COMPLIANCE_ENGINE_TOOLS,
    *CONTRACT_PERFORMANCE_TOOLS,
    *ENERGY_INTELLIGENCE_TOOLS,
]

# OpenAI Chat Completions rejects tool arrays longer than 128.
_OPENAI_MAX_TOOLS = 128
if len(ALL_TOOLS) > _OPENAI_MAX_TOOLS:
    raise RuntimeError(
        f"DeepAgent ALL_TOOLS has {len(ALL_TOOLS)} tools; OpenAI max is "
        f"{_OPENAI_MAX_TOOLS}. Move domain tools behind task() subagents or "
        f"trim the registry before create_react_agent."
    )

# Maps every tool name to its agent domain — used for agent_switch streaming events.
_TOOL_DOMAIN: dict[str, str] = {
    # Meta (7)
    "write_todos": "meta", "select_skill": "meta", "task": "meta", "write_file": "meta",
    "read_file": "meta", "memory_set": "meta", "memory_get": "meta",
    # UDR (11+)
    "get_schema": "udr", "lookup_user": "udr", "query_table": "udr",
    "find_asset": "udr", "find_location": "udr",
    "get_asset_documents": "udr",
    "udr_agent_query": "udr",
    "udr_list_tables": "udr",
    "udr_describe_table": "udr",
    "udr_read_records": "udr",
    "udr_get_record": "udr",
    "udr_search_records": "udr",
    "udr_create_record": "udr",
    "udr_update_record": "udr",
    "udr_delete_record": "udr",
    "udr_execute_select": "udr",
    # WO Engine — dynamic approval (6)
    "suggest_approval_chain": "wo_engine",
    "request_approval_chain": "wo_engine",
    "send_approval_request_email": "wo_engine",
    "get_approval_chain": "wo_engine",
    "customize_approval_chain": "wo_engine",
    "respond_to_approval_step": "wo_engine",
    # WO Engine — intelligent pipeline (3)
    "prepare_intelligent_work_order": "wo_engine",
    "confirm_intelligent_work_order_creation": "wo_engine",
    "create_intelligent_work_order": "wo_engine",
    "trigger_ppm_work_order": "wo_engine",
    "process_email_work_order": "wo_engine",
    # WO Engine — CRUD + lifecycle (8)
    "create_work_order": "wo_engine", "get_work_order": "wo_engine",
    "update_work_order": "wo_engine", "list_work_orders": "wo_engine",
    "transition_work_order": "wo_engine", "approve_work_order": "wo_engine",
    "close_work_order": "wo_engine", "get_work_order_history": "wo_engine",
    "get_work_order_status_track": "wo_engine",
    # WO Engine — reference lookups (5)
    "search_assets": "wo_engine", "get_asset_details": "wo_engine",
    "search_locations": "wo_engine", "find_ppm_schedules": "wo_engine",
    "get_dashboard_stats": "wo_engine",
    # Migration (8)
    "start_migration": "migration", "run_migration": "migration",
    "submit_pre_semantic": "migration", "submit_field_mapping": "migration",
    "submit_hierarchy": "migration", "get_migration_status": "migration",
    "get_migration_mappings": "migration", "list_migrations": "migration",
    # Fiix (9)
    "get_fiix_setup_status": "fiix",
    "configure_fiix_credentials": "fiix",
    "test_fiix_connection": "fiix",
    "fetch_fiix_schema": "fiix",
    "start_fiix_schema_mapping": "fiix",
    "get_schema_mapping_status": "fiix",
    "continue_schema_mapping_gate": "fiix",
    "start_fiix_ingestion": "fiix",
    "get_fiix_ingestion_status": "fiix",
    "list_fiix_ingestion_jobs": "fiix",
    # Bulk ingest (2)
    "get_ingest_batch_status": "ingest_batch",
    "list_session_ingest_batches": "ingest_batch",
    "retrieve_workspace_corpus_summary": "udr",
    "retrieve_vector_evidence": "udr",
    "resolve_cross_source_links": "udr",
    "answer_with_graph_context": "udr",
    "list_available_source_connectors": "connector",
    "test_source_connector_connection": "connector",
    # Doc RAG (10)
    "index_document": "doc_rag", "query_docs": "doc_rag",
    "semantic_search": "doc_rag", "extract_text": "doc_rag",
    "get_document_metadata": "doc_rag", "delete_document": "doc_rag",
    "list_row_index_tables": "doc_rag", "list_doc_rag_db_tables": "doc_rag",
    "match_document_to_rows": "doc_rag",
    # Compliance — legacy (2)
    "check_requirements": "compliance", "generate_compliance_report": "compliance",
    # Compliance Engine A1–A5 (11)
    "run_compliance_scan": "compliance",
    "list_building_certificates": "compliance",
    "list_vendor_accreditations": "compliance",
    "upsert_compliance_certificate": "compliance",
    "set_remedial_status": "compliance",
    "get_compliance_saved_space_summary": "compliance",
    "list_country_pack": "compliance",
    "seed_uk_country_pack": "compliance",
    "list_verification_registers": "compliance",
    "verify_accreditation_now": "compliance",
    "check_vendor_registration_compliance": "compliance",
    "run_document_forensics": "compliance",
    "list_compliance_approvals": "compliance",
    "decide_compliance_approval": "compliance",
    # Contract Performance B1–B3 (14)
    "ingest_contract_parameters": "contract_performance",
    "extract_contract_from_document": "contract_performance",
    "update_contract_parameters": "contract_performance",
    "confirm_contract_parameters": "contract_performance",
    "propose_asset_criticality": "contract_performance",
    "propose_asset_criticality_from_udr": "contract_performance",
    "approve_asset_criticality": "contract_performance",
    "score_vendor_work_orders": "contract_performance",
    "generate_vendor_scorecard": "contract_performance",
    "list_vendor_scorecards": "contract_performance",
    "get_score_weights": "contract_performance",
    "update_score_weights": "contract_performance",
    "verify_vendor_invoice": "contract_performance",
    "extract_and_verify_invoice": "contract_performance",
    "decide_invoice_line": "contract_performance",
    "list_contract_approvals": "contract_performance",
    "decide_contract_approval": "contract_performance",
    # Energy Intelligence C (15)
    "upsert_energy_meter": "energy_intelligence",
    "pull_smart_meter_readings": "energy_intelligence",
    "ingest_meter_readings": "energy_intelligence",
    "process_meter_gap_retries": "energy_intelligence",
    "upsert_building_energy_profile": "energy_intelligence",
    "compute_site_eui": "energy_intelligence",
    "list_tm46_benchmarks": "energy_intelligence",
    "deduce_asset_condition": "energy_intelligence",
    "deduce_condition_from_inspection_vectors": "energy_intelligence",
    "cross_ref_condition_consumption": "energy_intelligence",
    "log_site_occupancy_change": "energy_intelligence",
    "scan_energy_anomalies": "energy_intelligence",
    "list_energy_anomalies": "energy_intelligence",
    "act_on_energy_anomaly": "energy_intelligence",
    "generate_monthly_energy_report": "energy_intelligence",
    "get_energy_saved_space_summary": "energy_intelligence",
    "list_energy_approvals": "energy_intelligence",
    "decide_energy_approval": "energy_intelligence",
}


def _extract_interrupt(result: dict[str, Any]) -> dict | None:
    """Return the first interrupt payload if the graph paused, else None."""
    interrupts = result.get("__interrupt__", ())
    if interrupts:
        iv = interrupts[0]
        return iv.value if hasattr(iv, "value") else iv
    return None


def _extract_tool_calls(messages: list) -> list[dict[str, Any]]:
    """Extract the tool call trace from LangGraph message history."""
    tool_calls: list[dict[str, Any]] = []
    for msg in messages:
        if hasattr(msg, "tool_calls") and msg.tool_calls:
            for tc in msg.tool_calls:
                tool_calls.append({
                    "tool": tc.get("name"),
                    "input": tc.get("args", {}),
                })
        if hasattr(msg, "name") and msg.name and hasattr(msg, "content"):
            if tool_calls and "output" not in tool_calls[-1]:
                tool_calls[-1]["output"] = msg.content
    return tool_calls


def _extract_answer(messages: list) -> str:
    for msg in reversed(messages):
        if (
            hasattr(msg, "content")
            and isinstance(msg.content, str)
            and not getattr(msg, "tool_calls", None)
        ):
            return msg.content
    return ""


def _latest_user_message(input_: Any) -> str:
    if not isinstance(input_, dict):
        return ""
    messages = input_.get("messages")
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        if isinstance(message, HumanMessage) and isinstance(message.content, str):
            return message.content
    return ""


def _trim_history_hook(state: dict[str, Any]) -> dict[str, Any]:
    """pre_model_hook: cap the message window fed to the LLM on every ReAct step.

    A long stateful session (or a single turn with many large tool results) can
    push the running message list past the model's context window (gpt-4o-mini =
    128k), which surfaces as `context_length_exceeded`. We trim the OLDEST
    messages, always keeping the system prompt and the most recent turns.

    Returning `llm_input_messages` trims only what is SENT to the model for this
    step — the full history stays persisted in the checkpointer, so nothing is
    permanently lost and later turns still see prior context (up to the budget).

    `start_on="human"` guarantees the window begins on a HumanMessage, so an
    AIMessage tool-call is never left orphaned from (or without) its ToolMessage
    result — which OpenAI would otherwise reject.
    """
    messages = state.get("messages") or []
    if not messages:
        return {}
    try:
        trimmed = trim_messages(
            messages,
            strategy="last",
            token_counter=count_tokens_approximately,
            max_tokens=settings.orchestrator_history_max_tokens,
            start_on="human",
            end_on=("human", "tool", "ai"),
            include_system=True,
            allow_partial=False,
        )
    except Exception:  # never let trimming break a turn — fall back to full history
        return {}
    if trimmed and len(trimmed) < len(messages):
        log.info(
            "orchestrator.history.trimmed",
            kept=len(trimmed),
            dropped=len(messages) - len(trimmed),
            budget_tokens=settings.orchestrator_history_max_tokens,
        )
        return {"llm_input_messages": trimmed}
    return {}


class _ZoneStreamer:
    """Incremental reader of a streamed top-level JSON object, zone by zone.

    Feed it text deltas; it returns ``(key, value, final)`` tuples. A zone is ``final`` when
    its value has closed. Before that, two kinds of partial are emitted so the UI can paint
    while the model is still writing: a text zone (the narrative) as a growing string every
    few dozen characters, and an array zone as a growing list each time one element closes.
    Every character is read once — the previous implementation re-scanned the open zone on
    every delta, which for a large ``groups`` array cost minutes on the event loop.

    Emission is serialisation only. Which zones exist and what goes in them is the model's.
    """

    _TEXT_EVERY = 40

    def __init__(self, keys: set[str], text_zones: set[str] | None = None) -> None:
        self.keys = keys
        self.text_zones = text_zones if text_zones is not None else {"narrative"}
        self.buf = ""
        self.pos = 0
        self.stage = "root"  # root | key | keystr | colon | value_start | value | after | end
        self.key_start = 0
        self.cur_key: str | None = None
        self.val_start = 0
        self.val_kind = ""  # str | arr | obj | scalar
        self.depth = 0
        self.in_str = False
        self.esc = False
        self._last_text_len = 0
        self._last_partial_len = -1
        self.done: set[str] = set()

    # -- helpers -------------------------------------------------------------------------
    def _emit_final(self, out: list, end: int) -> None:
        text = self.buf[self.val_start:end]
        unparsed = object()  # distinct from a legitimate JSON null
        try:
            value = json.loads(text)
        except Exception:  # noqa: BLE001 — a zone we cannot parse is not emitted
            value = unparsed
        if self.cur_key in self.keys and value is not unparsed:
            out.append((self.cur_key, value, True))
            self.done.add(self.cur_key)
        self.stage = "after"
        self._last_text_len = 0
        self._last_partial_len = -1

    def _emit_partial_array(self, out: list) -> None:
        text = self.buf[self.val_start : self.pos].rstrip()
        if text.endswith(","):
            text = text[:-1]
        try:
            value = json.loads(text + "]")
        except Exception:  # noqa: BLE001 — element not actually closed yet
            return
        if len(value) == self._last_partial_len or not value:
            return
        self._last_partial_len = len(value)
        if self.cur_key in self.keys:
            out.append((self.cur_key, value, False))

    def _emit_partial_text(self, out: list) -> None:
        raw = self.buf[self.val_start + 1 : self.pos]
        if raw.endswith(chr(92)):
            raw = raw[:-1]
        try:
            value = json.loads('"' + raw + '"')
        except Exception:  # noqa: BLE001 — cut inside an escape; wait for more
            return
        self._last_text_len = len(raw)
        if self.cur_key in self.keys:
            out.append((self.cur_key, value, False))

    # -- feed ------------------------------------------------------------------------------
    def feed(self, delta: str) -> list[tuple[str, Any, bool]]:
        out: list[tuple[str, Any, bool]] = []
        self.buf += delta
        n = len(self.buf)
        while self.pos < n:
            ch = self.buf[self.pos]
            self.pos += 1
            st = self.stage
            if st == "root":
                if ch == "{":
                    self.stage = "key"
            elif st == "key":
                if ch == '"':
                    self.key_start = self.pos
                    self.esc = False
                    self.stage = "keystr"
                elif ch == "}":
                    self.stage = "end"
            elif st == "keystr":
                if self.esc:
                    self.esc = False
                elif ch == chr(92):
                    self.esc = True
                elif ch == '"':
                    try:
                        self.cur_key = json.loads('"' + self.buf[self.key_start : self.pos - 1] + '"')
                    except Exception:  # noqa: BLE001
                        self.cur_key = self.buf[self.key_start : self.pos - 1]
                    self.stage = "colon"
            elif st == "colon":
                if ch == ":":
                    self.stage = "value_start"
            elif st == "value_start":
                if ch in " \t\r\n":
                    continue
                self.val_start = self.pos - 1
                self.in_str = False
                self.esc = False
                self._last_text_len = 0
                self._last_partial_len = -1
                if ch == '"':
                    self.val_kind = "str"
                    self.in_str = True
                elif ch == "[":
                    self.val_kind = "arr"
                    self.depth = 1
                elif ch == "{":
                    self.val_kind = "obj"
                    self.depth = 1
                else:
                    self.val_kind = "scalar"
                self.stage = "value"
            elif st == "value":
                kind = self.val_kind
                if kind == "str":
                    if self.esc:
                        self.esc = False
                    elif ch == chr(92):
                        self.esc = True
                    elif ch == '"':
                        self._emit_final(out, self.pos)
                    elif (
                        self.cur_key in self.text_zones
                        and (self.pos - self.val_start - 1) - self._last_text_len >= self._TEXT_EVERY
                        and ch in " .,;:!?"
                    ):
                        self._emit_partial_text(out)
                elif kind == "scalar":
                    if ch in ",}":
                        self._emit_final(out, self.pos - 1)
                        self.stage = "key" if ch == "," else "end"
                else:  # arr | obj
                    if self.in_str:
                        if self.esc:
                            self.esc = False
                        elif ch == chr(92):
                            self.esc = True
                        elif ch == '"':
                            self.in_str = False
                    elif ch == '"':
                        self.in_str = True
                    elif ch in "[{":
                        self.depth += 1
                    elif ch in "]}":
                        self.depth -= 1
                        if self.depth == 0:
                            self._emit_final(out, self.pos)
                        elif kind == "arr" and self.depth == 1:
                            self._emit_partial_array(out)
                    elif ch == "," and kind == "arr" and self.depth == 1:
                        self._emit_partial_array(out)
            elif st == "after":
                if ch == ",":
                    self.stage = "key"
                elif ch == "}":
                    self.stage = "end"
            else:  # end
                break
        return out


class DeepAgentOrchestrator:
    """
    Main orchestrator for the Plenum CAFM platform.

    Wraps a LangGraph ReAct agent (gpt-4o-mini) with all 46 CAFM tools.

    When a checkpointer is provided (HITL mode):
      - run() and run_stateful() detect interrupt() calls and surface them
        as interrupted=True in the response.
      - resume() continues an interrupted workflow with a human decision.

    Without a checkpointer the agent runs statelessly and HITL gates are
    skipped automatically (settings.hitl_enabled controls this).
    """

    def __init__(
        self,
        openai_api_key: str,
        model: str = "gpt-4o-mini",
        checkpointer: Any = None,
    ) -> None:
        self._model_id = model
        self._has_hitl = checkpointer is not None
        self._llm = create_chat_model(api_key=openai_api_key, model=model)
        self._agent = create_react_agent(
            model=self._llm,
            tools=ALL_TOOLS,
            checkpointer=checkpointer,
            pre_model_hook=_trim_history_hook,
        )
        init_meta_tools(openai_api_key=openai_api_key, model=model)
        log.info(
            "orchestrator.ready",
            model=model,
            tool_count=len(ALL_TOOLS),
            phase2_engine_tools={
                k: len(v) for k, v in PHASE2_ENGINE_TOOLS.items()
            },
            hitl=self._has_hitl,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _config(self, thread_id: str) -> dict:
        """Build the LangGraph run config for a given thread."""
        return {"configurable": {"thread_id": thread_id}, "recursion_limit": 60}

    @staticmethod
    def _wrap_stateful_user_message(
        session_id: str,
        user_message: str,
        extra_context: str | None,
    ) -> str:
        """Prefix workspace + recent chat so every turn retains solution context."""
        runtime = build_session_runtime_context(session_id)
        parts: list[str] = []
        if extra_context and extra_context.strip():
            parts.append(extra_context.strip())
        if runtime:
            parts.append(runtime)
        if not parts:
            return user_message
        return "\n\n".join(parts) + f"\n\n---\n\n**Current user message:**\n{user_message}"

    async def _thread_has_prior_messages(self, thread_id: str) -> bool:
        if not self._has_hitl:
            return False
        try:
            snap = await self._agent.aget_state(self._config(thread_id))
            if snap is None:
                return False
            msgs = (snap.values or {}).get("messages") or []
            return len(msgs) > 0
        except Exception:
            return False

    async def _build_stateful_input(
        self,
        session_id: str,
        user_message: str,
        extra_context: str | None,
    ) -> dict[str, Any]:
        wrapped = self._wrap_stateful_user_message(session_id, user_message, extra_context)
        system_prompt = build_system_prompt(extra_context)
        if await self._thread_has_prior_messages(session_id):
            return {"messages": [HumanMessage(content=wrapped)]}
        return {
            "messages": [
                SystemMessage(content=system_prompt),
                HumanMessage(content=wrapped),
            ],
        }

    async def _maybe_apply_fiix_credentials_from_message(
        self,
        session_id: str,
        user_message: str,
        session_state: dict[str, Any],
        route_intent: str,
    ) -> dict[str, Any] | None:
        """Parse pasted credentials and auto-advance test → fetch → mapping."""
        if not message_looks_like_fiix_credentials(user_message) and route_intent != ROUTE_FIIX_SYNC:
            if not session_state.get("pending_fiix_confirm"):
                return None
        was_configured = fiix_credentials_configured(session_id)
        merge_fiix_credentials_from_message(session_id, user_message)
        if not fiix_credentials_configured(session_id):
            if message_looks_like_fiix_credentials(user_message):
                setup = fiix_setup_status_snapshot(session_id)
                return attach_route_to_result(
                    {
                        "session_id": session_id,
                        "answer": self._fiix_credential_prompt_text(setup),
                        "tool_calls": [],
                        "success": True,
                        "error": None,
                        "interrupted": False,
                        "interrupt_payload": None,
                    },
                    session_id,
                    intent=ROUTE_FIIX_SYNC,
                    domain="fiix",
                    next_step_prompt="Provide any missing Fiix fields in one message.",
                )
            return None
        if (
            not was_configured
            or route_intent == ROUTE_FIIX_SYNC
            or session_state.get("pending_fiix_confirm")
            or message_looks_like_fiix_credentials(user_message)
        ):
            action = str(session_state.get("pending_fiix_action") or "schema_mapping")
            if "ingest" in user_message.lower() or "sync" in user_message.lower():
                action = "ingestion"
            log.info("orchestrator.fiix_credentials_applied", session_id=session_id)
            return await self._fiix_continue_after_credentials(session_id, action=action)
        return None

    async def _stateful_preflight_shortcut(
        self,
        user_message: str,
        session_id: str,
        session_state: dict[str, Any],
        route_intent: str,
        msg_l: str,
        on_zone: Any = None,
    ) -> dict[str, Any] | None:
        """Deterministic short paths shared by REST and WebSocket.

        `on_zone` is awaited with (key, value) as each zone of a compliance analysis closes,
        so the WebSocket path can paint the answer while the model is still writing it. The
        REST path leaves it unset and behaves exactly as before.
        """
        creds_out = await self._maybe_apply_fiix_credentials_from_message(
            session_id, user_message, session_state, route_intent
        )
        if creds_out is not None:
            return creds_out

        if route_intent == ROUTE_FIIX_SYNC and self._is_affirmative_message(msg_l):
            set_pending_fiix_confirm(
                session_id,
                action=(
                    "ingestion"
                    if any(t in msg_l for t in ("ingest", "sync", "pull data"))
                    else "schema_mapping"
                ),
            )

        fiix_proactive = await self._maybe_fiix_proactive_setup(
            session_id, session_state, route_intent
        )
        if fiix_proactive is not None and not self._is_affirmative_message(msg_l):
            return fiix_proactive

        shortcut = await self._maybe_schema_gate_short_reply(
            user_message, session_id, session_state
        )
        if shortcut is None:
            shortcut = await self._maybe_fiix_short_reply(
                user_message, session_id, session_state
            )
        if shortcut is None:
            shortcut = await self._maybe_status_query_shortcut(user_message, session_id)
        if shortcut is None:
            shortcut = await self._maybe_compliance_posture_shortcut(
                user_message, session_id, on_zone=on_zone
            )
        if shortcut is None:
            shortcut = await self._maybe_short_reply_wo_action(user_message, session_id)
        return shortcut

    def mark_single_door_ingestion(
        self,
        session_id: str,
        ingested_count: int,
        flow_summary: str,
    ) -> None:
        mark_documents_ingested(session_id, ingested_count, flow_summary)

    def mark_fiix_ingestion(self, session_id: str, ingestion_id: str) -> None:
        record_fiix_ingestion_started(session_id, ingestion_id)

    def register_active_batch(self, session_id: str, batch_id: str, file_count: int) -> None:
        append_pending_batch(session_id, batch_id)
        get_session_state(session_id)["last_flow_summary"] = (
            f"Bulk ingest batch {batch_id} queued ({file_count} files)."
        )

    @staticmethod
    async def get_workspace_status(session_id: str) -> dict[str, Any]:
        from .session_workspace import refresh_workspace_from_migrations

        await refresh_workspace_from_migrations(session_id)
        return workspace_snapshot(session_id)

    @staticmethod
    def _is_affirmative_message(user_message: str) -> bool:
        msg = " ".join((user_message or "").strip().lower().split())
        if not msg:
            return False
        if msg in _AFFIRMATIVE_TOKENS:
            return True
        return msg.startswith("yes")

    @staticmethod
    def _is_negative_message(user_message: str) -> bool:
        msg = " ".join((user_message or "").strip().lower().split())
        if not msg:
            return False
        return msg in _NEGATIVE_TOKENS or msg.startswith("no")

    @staticmethod
    def _approval_intent(msg: str) -> bool:
        return (
            "approval chain" in msg
            or " request approval" in msg
            or " confirm approval" in msg
            or " proceed with approval" in msg
            or " continue with approval" in msg
            or " continue with this approval" in msg
            or " confirming to continue" in msg
            or "confirming to continue" in msg
            or " send email" in msg
            or "send email" in msg
            or " email for approval" in msg
            or "notify approver" in msg
            or ("approve" in msg and "work order" in msg)
            or ("approval" in msg and "confirm" in msg)
        )

    @staticmethod
    def _is_approval_followup_message(msg: str) -> bool:
        """Longer approval/email confirmations that do not start with 'yes'."""
        return DeepAgentOrchestrator._approval_intent(msg) and (
            "confirm" in msg
            or "continue" in msg
            or "send" in msg
            or "email" in msg
            or "notify" in msg
        )

    async def _approval_shortcut_response(
        self,
        session_id: str,
        wo_id: str,
        user_message: str = "",
    ) -> dict[str, Any]:
        msg = " ".join((user_message or "").strip().lower().split())
        want_explicit_email = "email" in msg or "send" in msg or "notify" in msg

        out = await request_approval_chain.ainvoke(
            {"work_order_id": wo_id, "session_id": session_id}
        )
        tool_calls: list[dict[str, Any]] = [
            {
                "tool": "request_approval_chain",
                "input": {"work_order_id": wo_id, "session_id": session_id},
                "output": json.dumps(out, default=str),
            }
        ]

        if isinstance(out, dict) and (out.get("error") or out.get("success") is False):
            answer = out.get("message") or out.get("error") or "Approval request failed."
            return {
                "session_id": session_id,
                "answer": answer,
                "tool_calls": tool_calls,
                "success": False,
                "error": answer,
                "interrupted": False,
                "interrupt_payload": None,
            }

        wo_id_out = out.get("work_order_id") or wo_id
        email_sent = bool(out.get("email_sent"))
        email_out: dict[str, Any] | None = None

        if want_explicit_email or not email_sent or out.get("already_exists"):
            email_out = await send_approval_request_email.ainvoke(
                {"work_order_id": wo_id_out, "session_id": session_id, "step_order": 1}
            )
            tool_calls.append(
                {
                    "tool": "send_approval_request_email",
                    "input": {"work_order_id": wo_id_out, "session_id": session_id},
                    "output": json.dumps(email_out, default=str),
                }
            )
            if isinstance(email_out, dict) and email_out.get("email_sent"):
                email_sent = True

        if email_sent:
            approver_name = (
                (email_out or {}).get("approver_name")
                or out.get("approver_name")
            )
            approver_email = (
                (email_out or {}).get("approver")
                or (email_out or {}).get("approver_email")
                or out.get("approver")
            )
            if approver_name and approver_email:
                recipient = f"{approver_name} ({approver_email})"
            else:
                recipient = approver_name or approver_email or "the step 1 approver"
            answer = (
                f"Approval chain is active for {wo_id_out}. "
                f"Approval request email sent to {recipient} via Outlook."
            )
        elif out.get("already_exists"):
            answer = out.get("message") or f"Approval chain is already active for {wo_id_out}."
        else:
            answer = (
                out.get("message")
                or f"Approval chain saved for {wo_id_out}. "
                "Outlook is not configured — set OUTLOOK_USER_EMAIL and Azure Graph credentials."
            )

        return {
            "session_id": session_id,
            "answer": answer,
            "tool_calls": tool_calls,
            "success": True,
            "error": None,
            "interrupted": False,
            "interrupt_payload": None,
        }

    @staticmethod
    def _is_wo_status_query(msg: str) -> bool:
        if not msg:
            return False
        from .wo_engine_agent import extract_work_order_id_from_text

        has_wo = (
            "work order" in msg
            or "wo-" in msg
            or bool(extract_work_order_id_from_text(msg))
        )
        if not has_wo:
            return False
        status_phrases = (
            "status",
            "progress",
            "track",
            "where is",
            "where's",
            "approval status",
            "who approved",
            "technician",
            "assigned to",
            "work order details",
            "wo details",
            "lifecycle",
            "on hold",
            "in progress",
            "pending approval",
            "completed",
            "timeline",
        )
        return any(p in msg for p in status_phrases)

    async def _maybe_status_query_shortcut(
        self,
        user_message: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        from .wo_engine_agent import (
            _LAST_WORK_ORDER_ID as _GLOBAL_WO_ID,
            _SESSION_WORK_ORDER_MAP,
            extract_work_order_id_from_text,
            format_work_order_status_track,
            get_work_order_status_track,
        )

        msg = " ".join((user_message or "").strip().lower().split())
        if not self._is_wo_status_query(msg):
            return None

        wo_id = (
            extract_work_order_id_from_text(user_message)
            or _SESSION_WORK_ORDER_MAP.get(session_id)
            or _GLOBAL_WO_ID
        )
        if not wo_id:
            return None

        out = await get_work_order_status_track.ainvoke({"work_order_id": wo_id})
        tool_calls = [
            {
                "tool": "get_work_order_status_track",
                "input": {"work_order_id": wo_id},
                "output": json.dumps(out, default=str),
            }
        ]
        if isinstance(out, dict) and out.get("error"):
            answer = out.get("error") or "Could not load work order status."
            return {
                "session_id": session_id,
                "answer": answer,
                "tool_calls": tool_calls,
                "success": False,
                "error": answer,
                "interrupted": False,
                "interrupt_payload": None,
            }

        answer = (
            (out.get("formatted_summary") if isinstance(out, dict) else None)
            or format_work_order_status_track(out if isinstance(out, dict) else {})
            or out.get("summary_message")
            or "Status loaded."
        )
        return {
            "session_id": session_id,
            "answer": answer,
            "tool_calls": tool_calls,
            "success": True,
            "error": None,
            "interrupted": False,
            "interrupt_payload": None,
        }

    # Distinct lifecycle/risk states a compliance posture question can name.
    _COMPLIANCE_STATUS_WORDS = (
        "non-compliant",
        "non compliant",
        "noncompliant",
        "blocked",
        "lapsed",
        "expired",
        "at risk",
        "at-risk",
        "overdue",
        "expiring",
        "due for renewal",
        "critical",
        "compliant",
    )

    @classmethod
    def _is_compliance_posture_query(cls, msg: str) -> bool:
        """A 'show the whole compliance portfolio' question — not a single-status/single-vendor
        list. These are the questions where the LLM tends to pick ONE status filter and drop the
        other flagged vendors; we answer them deterministically from the full portfolio instead.
        """
        if not msg:
            return False
        # Must be about accreditation/compliance, not e.g. a work-order or asset query.
        if not any(w in msg for w in ("complian", "accreditation", "accredited")):
            return False
        # Posture signal: an explicit "summarise/overview/every/all …" ask, OR the question
        # names two or more distinct states at once (the union case the single filter breaks on).
        distinct_states = len({w for w in cls._COMPLIANCE_STATUS_WORDS if w in msg})
        broad = any(
            w in msg
            for w in ("summar", "overview", "posture", "every", "all vendor", "all building")
        )
        return broad or distinct_states >= 2

    @staticmethod
    def _is_compliance_both_scope_status_query(msg: str) -> bool:
        """A certificate-status question that explicitly spans BOTH the building and vendor
        levels — e.g. "show expired certificates for buildings and at the vendor level". The LLM
        path tends to answer only one level and silently drop the other, so we pull BOTH portfolios
        here and let build_deterministic_compliance_answer split them into two sections."""
        if not msg:
            return False
        mentions_building = any(
            w in msg for w in ("building", "site", "premises", "property", "landlord")
        )
        mentions_vendor = any(
            w in msg
            for w in ("vendor", "accreditation", "accredited", "contractor", "supplier")
        )
        if not (mentions_building and mentions_vendor):
            return False
        mentions_cert = any(
            w in msg for w in ("certificate", "certification", "compliance", "complian")
        )
        # Only the lapsed/expired family — that is what the deterministic both-scope branch in
        # build_deterministic_compliance_answer renders. Other statuses keep the LLM path.
        mentions_status = any(
            w in msg for w in ("expired", "expir", "lapsed", "expiry past", "past expiry")
        )
        return mentions_cert and mentions_status

    @staticmethod
    def _is_compliance_forgery_query(msg: str) -> bool:
        """A document-authenticity / forgery question — answered from the whole portfolio so
        both building and vendor flagged certificates can be split out."""
        if not msg:
            return False
        return any(
            w in msg
            for w in (
                "forged",
                "forgery",
                "fake",
                "fraud",
                "tamper",
                "doctored",
                "altered",
                "manipulat",
                "authenticity",
                "genuine",
                "forensic",
            )
        )

    async def _llm_classify_engine(self, user_message: str) -> Phase2AgentId | None:
        """Comprehension-based routing fallback. When keyword matching finds no Phase 2 engine,
        ask the LLM which specialised engine (if any) should own the turn. Returns None for
        'general' so the core orchestrator (assets/work-orders/parts/docs/migration) still handles
        those. Any failure falls back to None so the turn is never blocked by the classifier."""
        text = (user_message or "").strip()
        if not text:
            return None
        system = SystemMessage(
            content=(
                "You route one facilities-management question to a single engine. "
                "Reply with EXACTLY one lowercase word, nothing else:\n"
                "compliance — certificates, accreditations, inspections, expiry/lapsed/renewal, "
                "statutory, insurance, document forgery/authenticity, blocked vendors, coverage.\n"
                "contract — SLAs, contractor/vendor performance, KPIs, PPM completion, invoices, "
                "overruns, contract breaches, vendor scorecards.\n"
                "energy — energy/meter/consumption/kWh, spikes/anomalies, EUI/NABERS, carbon, "
                "EPC ratings, utilities/electricity, benchmarking.\n"
                "general — anything else: assets, work orders, parts, locations, documents, "
                "data migration, greetings, or small talk.\n"
                "Answer with one of: compliance, contract, energy, general."
            )
        )
        try:
            resp = await self._llm.ainvoke([system, HumanMessage(content=text[:2000])])
        except Exception as exc:  # noqa: BLE001 — never let routing crash the turn
            log.warning("orchestrator.llm_route.failed", error=str(exc)[:200])
            return None
        out = getattr(resp, "content", "")
        if isinstance(out, list):
            out = " ".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in out
            )
        out = str(out).strip().lower()
        engine: Phase2AgentId | None = None
        if "compliance" in out:
            engine = "compliance"
        elif "contract" in out:
            engine = "contract_performance"
        elif "energy" in out:
            engine = "energy_intelligence"
        log.info("orchestrator.llm_route", classified=out[:40], engine=engine)
        return engine

    @staticmethod
    def _is_compliance_data_question(msg: str) -> bool:
        """Any question that should be answered from the live compliance tables — certificates,
        accreditations, coverage, forgery, expiry, blocked vendors, etc. We fetch the WHOLE
        portfolio for these and let the LLM summarise it (grounded in the rows), rather than
        depending on the model to hand-pick tools (which drops whole sections)."""
        if not msg:
            return False
        # Never hijack the action flows — those need their specific tools, not a read-only summary.
        if "draft" in msg and ("renewal" in msg or "email" in msg):
            return False
        if any(
            w in msg
            for w in (
                "upload",
                "ingest",
                "generate",  # generate evidence pack / report
                "evidence pack",
                "create a work order",
                "verify ",
                "run a scan",
                "run compliance scan",
                "passport",
                "approve",
                "reject",
                "seed ",
                "remedial",
                "share ",
            )
        ):
            return False
        compliance_signal = any(
            w in msg
            for w in (
                "compliance",
                "complian",
                "certificate",
                "certification",
                "accreditation",
                "accredited",
                "lapsed",
                "expired",
                "expir",
                "forged",
                "forgery",
                "authenticity",
                "not on record",
                "coverage",
            )
        )
        scope = any(
            w in msg
            for w in (
                "vendor",
                "building",
                "site",
                "premises",
                "landlord",
                "supplier",
                "contractor",
            )
        )
        status_or_verb = any(
            w in msg
            for w in (
                "blocked",
                "current",
                "overdue",
                "at risk",
                "at-risk",
                "valid",
                "expiring",
                "due",
                "status",
                "compliant",
                "which",
                "show",
                "list",
                "summar",
                "how many",
            )
        )
        return compliance_signal or (scope and status_or_verb)

    @staticmethod
    def _prune_row(row: dict[str, Any]) -> dict[str, Any]:
        """Drop heavy/base64 fields so the whole portfolio fits the LLM context budget."""
        heavy = {
            "raw_metadata",
            "pdf_base64",
            "document_base64",
            "file_base64",
            "content_base64",
            "embedding",
            "findings",
            "extracted_json",
            "source_text",
        }
        return {k: v for k, v in row.items() if k not in heavy}

    # raw_metadata sub-objects that are large and add nothing the LLM can filter on.
    # Everything else in raw_metadata is flattened onto the row so the model sees it.
    _META_DROP = frozenset(
        {
            "forensics",           # full forensic report; the score + verdict are kept
            "verification",        # full verify payload; status is kept below
            "vendor_registration",
            "field_confidence",
            "vector_membership",
            "single_door",
            "source_file_path",
            "pm_action_track",
        }
    )

    # ---- LLM-built dynamic queries -------------------------------------------------
    # The model decides WHAT to select from the user's words; code decides whether that is
    # safe and turns it into parameterised SQL. The model never writes SQL.
    _SAFE_IDENT = re.compile(r"^[a-z_][a-z0-9_]{0,63}$")

    # Fixed operator set — anything outside this never reaches a query.
    _SPEC_OPS: dict[str, str] = {
        "eq": "=",
        "neq": "<>",
        "lt": "<",
        "lte": "<=",
        "gt": ">",
        "gte": ">=",
        "in": "IN",
        "not_in": "NOT IN",
        "contains": "ILIKE",
        "is_null": "IS NULL",
        "not_null": "IS NOT NULL",
    }

    # Columns that live on a joined table rather than the certificate row. Everything else is
    # resolved against compliance_certificates via information_schema.
    _SPEC_JOIN_FIELDS: dict[str, str] = {
        "vendor_name": "v.vendor_name",
        "vendor_block_state": "v.block_state",
        "vendor_status": "v.status",
        # Same expression the SELECT uses, so filtering by building name finds the
        # buildings that are named on a certificate but not registered as a site.
        "site_name": "COALESCE(s.site_name, c.building_name)",
        "site_city": "s.city",
        "site_country": "s.country",
        "asset_name": "a.asset_name",
        "asset_code": "a.asset_code",
        "location_name": "l.name",
        "source_document_filename": "d.original_filename",
    }

    # raw_metadata is JSONB; these are the keys the model may filter on, read out as text.
    _SPEC_META_FIELDS: frozenset[str] = frozenset(
        {
            "forensics_verdict",
            "forensics_risk_score",
            "vendor_name",
            "company_name",
            "draft",
            "confirmed_by_pm",
            "insurer_name",
            "vendor_is_compliant",
        }
    )

    _cert_columns_cache: set[str] | None = None

    async def _compliance_column_allowlist(self) -> set[str]:
        """Columns the model may filter on, read live from information_schema (cached).

        This is the allow-list half of the pattern CLAUDE.md mandates for any query whose
        shape is not fixed in code (see table_customizer.py).
        """
        if DeepAgentOrchestrator._cert_columns_cache is not None:
            return DeepAgentOrchestrator._cert_columns_cache
        from sqlalchemy import text as _sql

        from .. import database

        if database.AsyncSessionLocal is None:
            database.init_session_factory()
        async with database.AsyncSessionLocal() as session:
            result = await session.execute(
                _sql(
                    """
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema = 'plenum_cafm'
                      AND table_name = 'compliance_certificates'
                    """
                )
            )
            cols = {str(r[0]) for r in result.all()}
        DeepAgentOrchestrator._cert_columns_cache = cols
        return cols

    # Low-cardinality columns whose real values the planner needs before it can write a
    # filter. Without these it guesses plausible-sounding words ("Compliant") that are not in
    # the data, and the query silently matches nothing.
    _VOCAB_COLUMNS = (
        "status",
        "cert_scope",
        "forensics_verdict",
        "vendor_block_state",
        "remedial_status",
        "result",
        "certificate_type_code",
        "country_code",
        "state",
        "region",
    )
    _cert_vocab_cache: dict[str, list[str]] | None = None

    async def _compliance_value_vocabulary(self) -> dict[str, list[str]]:
        """The values that actually occur in the enum-like columns (cached per process)."""
        if DeepAgentOrchestrator._cert_vocab_cache is not None:
            return DeepAgentOrchestrator._cert_vocab_cache
        from sqlalchemy import text as _sql

        from .. import database

        allow = await self._compliance_column_allowlist()
        cols = [c for c in self._VOCAB_COLUMNS if c in allow]
        vocab: dict[str, list[str]] = {}
        if database.AsyncSessionLocal is None:
            database.init_session_factory()
        try:
            async with database.AsyncSessionLocal() as session:
                for col in cols:  # col comes from a fixed tuple ∩ information_schema
                    result = await session.execute(
                        _sql(
                            f"SELECT DISTINCT {col}::text FROM "  # noqa: S608 — allow-listed
                            "plenum_cafm.compliance_certificates "
                            f"WHERE {col} IS NOT NULL LIMIT 25"
                        )
                    )
                    values = sorted({str(r[0]) for r in result.all() if r[0] is not None})
                    if values and len(values) <= 25:
                        vocab[col] = values
        except Exception as exc:  # noqa: BLE001 — planning must never break the turn
            log.warning("compliance.vocab.failed", error=str(exc)[:200])
            return {}
        DeepAgentOrchestrator._cert_vocab_cache = vocab
        return vocab

    async def _validate_query_spec(
        self, spec: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Drop anything the model asked for that is not provably safe.

        Returns (accepted_filters, rejected_descriptions). A rejected filter never reaches
        SQL; the query still runs on whatever validated, so one bad clause cannot break the
        turn — and the rejections are logged so a bad spec is visible.
        """
        allow = await self._compliance_column_allowlist()
        accepted: list[dict[str, Any]] = []
        rejected: list[str] = []
        for f in (spec.get("filters") or [])[:12]:
            if not isinstance(f, dict):
                rejected.append(f"non-object filter: {str(f)[:40]}")
                continue
            field = str(f.get("field") or "").strip().lower()
            op = str(f.get("op") or "").strip().lower()
            if not self._SAFE_IDENT.match(field):
                rejected.append(f"unsafe identifier: {field[:40]}")
                continue
            if op not in self._SPEC_OPS:
                rejected.append(f"unknown op: {op[:20]} on {field}")
                continue
            known = (
                field in allow
                or field in self._SPEC_JOIN_FIELDS
                or field in self._SPEC_META_FIELDS
            )
            if not known:
                rejected.append(f"unknown column: {field}")
                continue
            accepted.append({"field": field, "op": op, "value": f.get("value")})
        return accepted, rejected

    def _spec_where_clause(
        self, filters: list[dict[str, Any]], logic: str
    ) -> tuple[str, dict[str, Any]]:
        """Build a parameterised WHERE. Every value is bound — nothing is interpolated."""
        parts: list[str] = []
        params: dict[str, Any] = {}
        for i, f in enumerate(filters):
            field, op = f["field"], f["op"]
            if field in self._SPEC_JOIN_FIELDS:
                col = self._SPEC_JOIN_FIELDS[field]
            elif field in self._SPEC_META_FIELDS:
                # JSONB key read as text; the key itself is bound, not interpolated.
                key = f"mk{i}"
                params[key] = field
                col = f"(c.raw_metadata->>:{key})"
            else:
                col = f"c.{field}"  # already matched _SAFE_IDENT and the allow-list
            sqlop = self._SPEC_OPS[op]
            if op in ("is_null", "not_null"):
                parts.append(f"{col} {sqlop}")
                continue
            p = f"p{i}"
            if op in ("in", "not_in"):
                vals = f.get("value")
                vals = vals if isinstance(vals, list) else [vals]
                params[p] = [str(v) for v in vals if v is not None]
                if not params[p]:
                    continue
                # CAST(...) not :p::text[] — SQLAlchemy's text() bind parser reads the "::"
                # straight after a bind name as part of the name and the SQL fails to parse.
                parts.append(
                    f"lower({col}::text) {sqlop} "
                    f"(SELECT lower(x) FROM unnest(CAST(:{p} AS text[])) x)"
                )
            elif op == "contains":
                params[p] = f"%{f.get('value')}%"
                parts.append(f"{col}::text {sqlop} :{p}")
            elif field in ("days_to_expiry", "forensics_risk_score") or isinstance(
                f.get("value"), (int, float)
            ):
                params[p] = f.get("value")
                parts.append(f"NULLIF({col}::text,'')::numeric {sqlop} :{p}")
            else:
                params[p] = str(f.get("value"))
                parts.append(f"lower({col}::text) {sqlop} lower(:{p})")
        if not parts:
            return "", {}
        joiner = " OR " if str(logic).lower() == "or" else " AND "
        return "(" + joiner.join(parts) + ")", params

    async def _fetch_compliance_table_direct(
        self,
        limit: int = 1000,
        spec: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Read the compliance table STRAIGHT from Postgres — every row, every field.

        No status/scope/country filtering happens here on purpose: the LLM decides what the
        question asks for and filters the rows itself. Code only makes the data complete and
        small enough to send.

        Everything the certificate row REFERENCES is joined in, so the LLM can answer about
        the related entity too (and filter on it) without a second trip:
          * vendors      — a vendor is "blocked" via plenum_cafm.vendors.block_state,
                           not via the certificate row.
          * sites        — site name/code/city/country for site-scoped questions.
          * assets       — the asset a certificate covers.
          * locations    — building/floor record behind the asset or site.
          * ingestion_documents — the source file the certificate was extracted from.
        site_id / asset_id are not populated on every row yet; LEFT JOIN keeps those rows
        and simply leaves the joined fields null.

        raw_metadata JSONB is also flattened in — it holds forensics_verdict /
        forensics_risk_score (forged vs not), draft, confirmed_by_pm, verification status,
        insurer/company name.
        """
        from sqlalchemy import text as _sql

        from .. import database

        if database.AsyncSessionLocal is None:
            database.init_session_factory()

        sql_template = (
            """
            SELECT c.*,
                   v.vendor_name              AS vendor_name_ref,
                   v.block_state              AS vendor_block_state,
                   v.blocked_accreditation_type,
                   v.status                   AS vendor_status,
                   COALESCE(s.site_name, c.building_name) AS site_name,
                   s.site_code                AS site_code,
                   s.city                     AS site_city,
                   s.country                  AS site_country,
                   s.site_type                AS site_type,
                   a.asset_name               AS asset_name,
                   a.asset_code               AS asset_code,
                   a.manufacturer             AS asset_manufacturer,
                   a.model                    AS asset_model,
                   a.serial_number            AS asset_serial_number,
                   l.name                     AS location_name,
                   l.type                     AS location_type,
                   l.address                  AS location_address,
                   l.city                     AS location_city,
                   d.original_filename        AS source_document_filename,
                   d.status                   AS source_document_status,
                   d.eval_score               AS source_document_eval_score
            FROM plenum_cafm.compliance_certificates c
            -- NOTE: the vendor_id / site_id / asset_id columns are uuid, but vendors.id,
            -- sites.id and assets.id are varchar/text, so these joins must compare as text.
            -- document_id and location_id already match their targets.
            -- A building certificate's site link is site_id when the sites table is
            -- UUID-keyed and site_ref when it is not, so the join reads both; and the
            -- certificate's own building_name stands in as the site name when neither
            -- resolves, so a building certificate always says which building it is for.
            -- (No curly braces in this template outside the format placeholders below.)
            LEFT JOIN plenum_cafm.vendors             v ON v.id = c.vendor_id::text
            LEFT JOIN plenum_cafm.sites               s ON s.id = COALESCE(c.site_id::text, c.site_ref)
            LEFT JOIN plenum_cafm.assets              a ON a.id = c.asset_id::text
            LEFT JOIN plenum_cafm.locations           l ON l.id = a.location_id
            LEFT JOIN plenum_cafm.ingestion_documents d ON d.id = c.document_id
            {where}
            ORDER BY {sort}
            LIMIT :limit
            """
        )

        # The model's spec decides the WHERE; code decides whether each clause is safe.
        where_sql, where_params, rejected = "", {}, []
        sort_sql = "c.expiry_date ASC NULLS LAST"
        if spec:
            accepted, rejected = await self._validate_query_spec(spec)
            scope = str(spec.get("scope") or "").strip().lower()
            if scope in ("vendor", "building"):
                accepted = accepted + [
                    {"field": "cert_scope", "op": "eq", "value": scope}
                ]
            clause, where_params = self._spec_where_clause(
                accepted, spec.get("logic") or "and"
            )
            if clause:
                where_sql = f"WHERE {clause}"
            sort = spec.get("sort") or {}
            sfield = str(sort.get("field") or "").strip().lower()
            allow = await self._compliance_column_allowlist()
            if self._SAFE_IDENT.match(sfield) and sfield in allow:
                direction = "DESC" if str(sort.get("dir")).lower() == "desc" else "ASC"
                sort_sql = f"c.{sfield} {direction} NULLS LAST"

        sql = _sql(sql_template.format(where=where_sql, sort=sort_sql))
        params: dict[str, Any] = {"limit": limit, **where_params}
        async with database.AsyncSessionLocal() as session:
            result = await session.execute(sql, params)
            raw_rows = [dict(r) for r in result.mappings().all()]
        if spec:
            log.info(
                "compliance.stage1.query",
                filters=[f"{f['field']} {f['op']} {f.get('value')}" for f in accepted],
                logic=spec.get("logic") or "and",
                sort=sort_sql,
                rejected=rejected,
                matched_rows=len(raw_rows),
            )

        rows: list[dict[str, Any]] = []
        for r in raw_rows:
            meta = r.pop("raw_metadata", None)
            row = {k: v for k, v in r.items()}
            if isinstance(meta, dict):
                for mk, mv in meta.items():
                    if mk in self._META_DROP:
                        continue
                    # never let metadata clobber a real column
                    row.setdefault(mk, mv)
                verification = meta.get("verification")
                if isinstance(verification, dict):
                    row.setdefault("verification_status", verification.get("status"))
                    row.setdefault("verification_verified", verification.get("verified"))
                    row.setdefault("verification_channel", verification.get("channel"))
            # vendor_name: the vendors table wins, else whatever the extract recorded
            row["vendor_name"] = (
                row.pop("vendor_name_ref", None)
                or row.get("vendor_name")
                or row.get("company_name")
            )
            row.update(self._derive_row_flags(row))
            rows.append(row)

        # STAGE 1 LOG — what came back from Postgres.
        by_status: dict[str, int] = {}
        for r in rows:
            key = str(r.get("status") or "unknown")
            by_status[key] = by_status.get(key, 0) + 1
        log.info(
            "compliance.stage1.db_result",
            table="plenum_cafm.compliance_certificates",
            rows=len(rows),
            columns=len(rows[0]) if rows else 0,
            building=sum(
                1 for r in rows if str(r.get("cert_scope") or "").lower() != "vendor"
            ),
            vendor=sum(
                1 for r in rows if str(r.get("cert_scope") or "").lower() == "vendor"
            ),
            by_status=by_status,
            lapsed=sum(1 for r in rows if r.get("is_lapsed")),
            compliant=sum(1 for r in rows if r.get("is_compliant")),
            forged=sum(1 for r in rows if r.get("is_forged")),
            blocked=sum(1 for r in rows if r.get("is_blocked")),
            drafts=sum(1 for r in rows if r.get("is_draft")),
        )
        if settings.compliance_debug_payloads:
            for r in rows:
                log.info(
                    "compliance.stage1.db_row",
                    scope=r.get("cert_scope"),
                    type=r.get("certificate_type_code"),
                    number=r.get("certificate_number"),
                    vendor=r.get("vendor_name"),
                    building=r.get("building_name"),
                    status=r.get("status"),
                    days=r.get("days_to_expiry"),
                    verdict=r.get("forensics_verdict"),
                    risk=r.get("forensics_risk_score"),
                    is_compliant=r.get("is_compliant"),
                    is_forged=r.get("is_forged"),
                )
        return rows

    @staticmethod
    def _derive_row_flags(row: dict[str, Any]) -> dict[str, Any]:
        """Add unambiguous boolean flags alongside the raw fields.

        NO row is dropped and no question is answered here — every row still goes to the
        LLM, which still decides which flags the user's question maps to. This only removes
        the two things the model kept getting wrong on raw values: case ("fail" vs "FAIL")
        and the lapsed/authenticity distinction (a Current certificate can be forensically
        failed, and those are exactly the rows a "forged but compliant" question wants).
        """
        status = str(row.get("status") or "").strip().lower()
        verdict = str(row.get("forensics_verdict") or "").strip().lower()
        try:
            days = int(row.get("days_to_expiry"))
        except (TypeError, ValueError):
            days = None
        try:
            score = float(row.get("forensics_risk_score"))
        except (TypeError, ValueError):
            score = None

        is_lapsed = status in {"lapsed", "expired"} or (days is not None and days < 0)
        is_forged = verdict == "fail"
        is_suspect = verdict == "review"
        block_state = str(row.get("vendor_block_state") or "").strip().lower()
        return {
            "is_lapsed": is_lapsed,
            "is_compliant": not is_lapsed,          # Current / Due for Renewal / Critical
            "is_expiring_soon": bool(
                not is_lapsed and days is not None and 0 <= days <= 90
            ),
            "is_forged": is_forged,
            "is_suspect_authenticity": is_suspect,
            "is_authentic": verdict == "pass",
            "authenticity_known": bool(verdict),
            "forensics_risk_score_num": score,
            # Two DIFFERENT signals, deliberately not merged. Conflating them made the chat
            # report 3 blocked vendors while the dashboard counted 2.
            #   is_blocked                 - the vendor is explicitly barred (vendors.block_state)
            #   has_lapsed_accreditation   - this vendor certificate is out of date, which is a
            #                                risk but is NOT the same as the vendor being blocked
            "is_blocked": block_state == "blocked",
            "has_lapsed_accreditation": bool(
                is_lapsed and str(row.get("cert_scope") or "").lower() == "vendor"
            ),
            "is_draft": bool(row.get("draft")),
        }

    # The pack fields the answer actually uses. A full pack row carries the alert threshold
    # ladder, the evidence-field template and the renewal copy — ~1.3KB each, 73KB for 55
    # types, none of it readable in an answer. Keeping the whole row cost more context than
    # the entire certificate register.
    _PACK_KEEP = (
        "certificate_type_code",
        "certificate_type_name",
        "certificate_scope",
        "trade_category",
        "regulation_reference",
        "regulation_url",
        "frequency_months",
        "applies_when",
        "is_active",
    )

    @classmethod
    def _compact_pack_types(cls, types: list[Any]) -> list[Any]:
        """Reduce pack rows to what an answer can cite, so all 55 fit alongside the register."""
        out: list[Any] = []
        for t in types:
            if not isinstance(t, dict):
                out.append(t)
                continue
            out.append({k: t[k] for k in cls._PACK_KEEP if k in t})
        return out

    # Which block gets sacrificed when the payload will not fit. The pack is the ANSWER on a
    # taxonomy question and the register is the context; on every other question it is the
    # other way round. Lowest number is dropped last.
    _BLOCK_PRIORITY_TAXONOMY = {
        "pack_facts": 0,
        "list_country_pack": 1,
        "get_compliance_coverage": 2,
        "get_compliance_saved_space_summary": 3,
    }

    def _compliance_data_json(
        self, tool_calls: list[dict[str, Any]], taxonomy: bool = False
    ) -> str:
        """Serialise the fetched rows for the model: heavy fields pruned, nothing filtered.

        Budgeting is per-block and row-wise, not a slice of the finished string. Slicing
        produced invalid JSON at the cut and — because the extras are appended after the
        register — silently removed the country pack and the coverage report from a taxonomy
        answer while leaving the register whole. The model then reconstructed the missing
        pack types from its own knowledge of UK compliance, which is how a 28-type pack was
        reported as 31 with two types absent. Whatever is dropped is now named in the payload
        so the omission is visible rather than inferred.
        """
        blocks: list[dict[str, Any]] = []
        for tc in tool_calls:
            if tc.get("tool") == "compliance_response":
                continue  # our own typed output — never feed it back in
            out = tc.get("output")
            payload = out
            if isinstance(out, dict) and isinstance(out.get("result"), dict):
                payload = out["result"]
            if isinstance(payload, dict):
                pruned: dict[str, Any] = {}
                for key, value in payload.items():
                    if isinstance(value, list):
                        rows = [
                            self._prune_row(r) if isinstance(r, dict) else r for r in value
                        ]
                        if tc.get("tool") == "list_country_pack" and key == "types":
                            rows = self._compact_pack_types(rows)
                        pruned[key] = rows
                    else:
                        pruned[key] = value
                payload = pruned
            blocks.append({"source": tc.get("tool"), "data": payload})

        budget = 90000  # guard the token budget on very large portfolios
        data_json = json.dumps(blocks, default=str, ensure_ascii=False)
        if len(data_json) <= budget:
            return data_json

        # Over budget: shed rows from the least important block first, and say so in the
        # payload. Order is by question type — see _BLOCK_PRIORITY_TAXONOMY.
        priority = self._BLOCK_PRIORITY_TAXONOMY if taxonomy else {}
        order = sorted(
            range(len(blocks)),
            key=lambda i: -priority.get(str(blocks[i].get("source")), 50),
        )
        dropped: list[str] = []
        for idx in order:
            if len(json.dumps(blocks, default=str, ensure_ascii=False)) <= budget:
                break
            data = blocks[idx].get("data")
            if not isinstance(data, dict):
                continue
            for key, value in list(data.items()):
                if not isinstance(value, list) or not value:
                    continue
                while value and len(
                    json.dumps(blocks, default=str, ensure_ascii=False)
                ) > budget:
                    value.pop()
                kept = len(value)
                data[key] = value
                data[f"{key}_truncated_note"] = (
                    f"{kept} row(s) shown; the rest were dropped to fit the context budget"
                )
                dropped.append(f"{blocks[idx].get('source')}.{key}")
        if dropped:
            log.warning("compliance.context.truncated", blocks=dropped[:8], taxonomy=taxonomy)
        return json.dumps(blocks, default=str, ensure_ascii=False)

    @staticmethod
    def _pack_facts(
        tool_calls: list[dict[str, Any]], rows: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        """Count the pack for the analyst instead of asking it to tally 55 JSON objects.

        Counting is not reasoning. Left to the model, the vendor total came back as 31
        against a 28-type pack — a number nothing in the data supports and which contradicted
        the same answer's own "19 with nothing on record". The model still decides what to
        say, which types matter and how to group them; it no longer has to do arithmetic to
        say it, and the gate downstream has something real to check the answer against.
        """
        pack = next(
            (
                tc.get("output")
                for tc in tool_calls
                if tc.get("tool") == "list_country_pack"
                and isinstance(tc.get("output"), dict)
            ),
            None,
        )
        # One arithmetic, shared with the get_pack_facts tool the sub-agent calls, so both
        # paths state the same totals from the same code.
        return compute_pack_facts(
            (pack or {}).get("types") or [], rows, (pack or {}).get("country_code") or "UK"
        )

    async def _llm_summarize_compliance(
        self,
        user_message: str,
        tool_calls: list[dict[str, Any]],
    ) -> str | None:
        """Grounded LLM summary: hand the LLM every fetched row (all columns) and let it answer
        the specific question. No hardcoded answer templates — but the model may only use values
        present in the data, so nothing is invented."""
        blocks = [
            {"source": tc.get("tool")} for tc in tool_calls
        ]  # shape retained for the stage-2 log below
        data_json = self._compliance_data_json(tool_calls)

        system_text = (
                "You are the Plenum CAFM compliance assistant. Answer the user's question "
                "STRICTLY from the DATA JSON below. It is a straight read of the Postgres "
                "table plenum_cafm.compliance_certificates — EVERY row and EVERY field, "
                "nothing pre-filtered — joined with the related vendor, site, asset, location "
                "and source-document records, and with raw_metadata flattened onto each row.\n"
                "Fields you will filter and report on include: status, days_to_expiry, "
                "expiry_date, issue_date, next_due_date, cert_scope (Building/Vendor), "
                "certificate_type_code, certificate_number, country_code, state, region, "
                "building_name, "
                "building_reference, vendor_name, vendor_block_state (Blocked/Clear), "
                "blocked_accreditation_type, forensics_verdict (PASS/FAIL/Review) and "
                "forensics_risk_score (0-100, higher = riskier), authenticity_warning, "
                "insurance_risk_flag, remedial_status, result, defects_found, draft, "
                "confirmed_by_pm, verification_status / verification_verified, inspector_name, "
                "issuer, site_name, asset_name, location_name, source_document_filename.\n\n"
                "## STEP 1 — WORK OUT THE FILTER, THEN APPLY IT (most important rule)\n"
                "Decide from the question which rows are being asked for. Then go through the "
                "rows ONE BY ONE and test each against that filter — include the ones that "
                "match, drop the ones that do not. Do not skim and do not answer \"none\" "
                "without having checked every row. Never list a row whose own status "
                "contradicts what was asked — that is the worst error you can make.\n"
                "All field values are CASE-INSENSITIVE: status \"Current\" and \"current\", "
                "forensics_verdict \"FAIL\" and \"fail\" are the same value. Compare lowercased.\n"
                "Every row also carries pre-computed BOOLEAN FLAGS — prefer these, they are "
                "authoritative and already handle case and edge cases:\n"
                "  is_lapsed · is_compliant (= not lapsed) · is_expiring_soon (0-90 days) ·\n"
                "  is_forged (forensics failed) · is_suspect_authenticity (review) ·\n"
                "  is_authentic (passed) · is_blocked · is_draft\n"
                "So \"forged and compliant\" = rows where is_forged AND is_compliant are both "
                "true. \"Lapsed building certs\" = is_lapsed AND cert_scope Building. Use the "
                "raw fields for anything the flags do not cover.\n"
                "Judge each row by its own status and days_to_expiry:\n"
                "- Lapsed / expired / non-compliant = status Lapsed or Expired, OR "
                "days_to_expiry < 0.\n"
                "- Current / compliant / valid / in-date = NOT lapsed (days_to_expiry >= 0).\n"
                "- At-risk / expiring soon / due for renewal = not lapsed but "
                "days_to_expiry <= 90. Needs renewal = that, plus anything already lapsed.\n"
                "- Blocked = the vendor is explicitly barred (is_blocked / vendor_block_state "
                "Blocked). A lapsed vendor certificate is NOT the same thing — that is "
                "has_lapsed_accreditation. Report them as separate groups and never merge the "
                "counts: \"2 vendors blocked\" and \"3 vendors holding a lapsed accreditation\" "
                "are two different statements. If a question just says \"blocked\", lead with "
                "the explicitly blocked vendors and mention the lapsed-accreditation group "
                "underneath as a related risk.\n"
                "- Forged / fake / tampered / suspect = forensics_verdict FAIL (treat Review "
                "as suspect-but-unconfirmed and label it as such), or a high "
                "forensics_risk_score, or an authenticity_warning present.\n"
                "- Not forged / genuine / authentic = forensics_verdict PASS.\n"
                "- Draft / unconfirmed = draft true (or confirmed_by_pm false) — a metadata "
                "flag, never a lifecycle status.\n"
                "- Verified = verification_verified true / verification_status verified.\n"
                "Forged and compliant are INDEPENDENT axes: authenticity (forensics_verdict) "
                "is separate from lifecycle (status). A certificate can be perfectly in-date "
                "AND forensically failed — those rows are the whole point of the question.\n"
                "Worked example — \"forged and compliant certificates\": a row with "
                "status \"Current\" (or any non-lapsed status such as \"Due for Renewal\" or "
                "\"Critical\", i.e. days_to_expiry >= 0) AND forensics_verdict \"fail\" "
                "(or \"review\", or a high forensics_risk_score) MATCHES and MUST be listed. "
                "Do not answer \"none\" for this: check the forensics_verdict of every "
                "non-lapsed row before concluding.\n"
                "Worked example — \"certificates which are compliant but not lapsed\" means "
                "include ONLY Current rows and exclude EVERY Lapsed/Expired row and every row "
                "with a negative days_to_expiry. A row showing \"Status: Lapsed\" must NOT "
                "appear in that answer.\n"
                "If no row passes the filter for a section, say so plainly — never pad the "
                "answer with rows that fail it.\n\n"
                "## STEP 2 — STRUCTURE\n"
                "- If the question spans both levels (buildings and vendors), produce BOTH a "
                "Building section and a Vendor section — never drop one, even if empty.\n"
                "- Head each section with the count of matching rows, e.g. "
                "\"Building certificates — 3 current\".\n\n"
                "## STEP 3 — FIELDS\n"
                "- Per row give: certificate type name, building name (buildings) or "
                "vendor/company name (vendors), certificate number, status, expiry date, and "
                "days remaining/overdue.\n"
                "- If the user asks for any extra field, include it for EVERY row listed. The "
                "risk assessment / forensics score is forensics_risk_score (0-100, higher = "
                "riskier) with forensics_verdict as its PASS/FAIL/Review label; country is "
                "country_code. If a requested field is null, print \"not recorded\" rather "
                "than omitting it.\n\n"
                "## STEP 4 — GROUNDING\n"
                "- Use ONLY values present in DATA. Never invent certificate numbers, dates, "
                "names, scores, or counts. If something is not in the data, say it is not on "
                "record.\n"
                "- Any count you state must equal the number of rows you actually listed.\n\n"
                "## STEP 5 — HOW TO WRITE IT (a property manager reads this, not a developer)\n"
                "Open with a SUMMARY of one to three sentences in plain English: how many, how "
                "bad, and what needs doing. That summary is the whole answer for most readers — "
                "make it stand alone.\n"
                "Then the detail, WORST FIRST (lapsed longest / highest risk / explicitly "
                "blocked before merely at-risk).\n"
                "Hard rules for the visible text:\n"
                "- NEVER print internal field names, flag names or filter expressions. Write "
                "\"authenticity check failed\", not \"forensics_verdict = FAIL\"; \"not "
                "registered on the platform\", not \"vendor_platform_compliance: "
                "unknown_vendor\"; \"flagged as an insurance risk\", not "
                "\"insurance_risk_flag = true\". Do NOT open with the filter you applied.\n"
                "- Humanise durations: \"expired 16 years ago\" or \"expired 5 months ago\", not "
                "\"-6019 days\". Keep the exact date alongside it.\n"
                "- One short line per certificate stating what it is, whose it is, and why it "
                "matters; put supporting facts under it. Do not emit a flat wall of bullets "
                "where every fact has equal weight.\n"
                "- If two numbers in the data disagree, do not narrate the discrepancy at the "
                "reader — report the figure the question asked for and stay silent on the other.\n"
                "- End with a single concrete next step.\n\n"
        )
        human_text = f"QUESTION:\n{user_message}\n\nDATA (JSON):\n{data_json}"

        # STAGE 2 LOG — exactly what is being sent to the model.
        log.info(
            "compliance.stage2.prompt",
            question=(user_message or "")[:300],
            system_chars=len(system_text),
            data_chars=len(data_json),
            prompt_chars=len(system_text) + len(human_text),
            approx_tokens=(len(system_text) + len(human_text)) // 4,
            truncated=data_json.endswith("…[truncated]"),
            blocks=[b.get("source") for b in blocks],
        )
        if settings.compliance_debug_payloads:
            log.info("compliance.stage2.prompt_system", system=system_text)
            log.info("compliance.stage2.prompt_data", data=data_json)

        # Claude first — this call has to apply compound row filters ("forged AND still
        # compliant") across the whole table, and the cheap general-purpose model used for
        # routing gets those wrong. Adaptive thinking + high effort is what makes the
        # row-by-row test reliable. Falls back to the orchestrator's default model.
        answer = await self._claude_summarize(system_text, human_text)
        if answer:
            return answer

        try:
            resp = await self._llm.ainvoke(
                [SystemMessage(content=system_text), HumanMessage(content=human_text)]
            )
        except Exception as exc:  # noqa: BLE001 — fall back to the deterministic builder
            log.warning(
                "orchestrator.compliance.llm_summary_failed", error=str(exc)[:300]
            )
            return None
        text = getattr(resp, "content", None)
        if isinstance(text, list):  # some providers return content parts
            text = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in text
            )
        out = text.strip() if isinstance(text, str) and text.strip() else None
        if out:
            # STAGE 3 LOG — fallback model path.
            log.info(
                "compliance.stage3.llm_summary",
                provider="openai_fallback",
                model=getattr(self._llm, "model_name", None) or settings.openai_model,
                answer_chars=len(out),
                preview=out[:400],
            )
            if settings.compliance_debug_payloads:
                log.info("compliance.stage3.llm_answer_full", answer=out)
        return out

    # Typed response contract. The sub-agent REASONS and returns this; the frontend renders
    # each key as its own component. No markdown parsing anywhere.
    _COMPLIANCE_RESPONSE_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "narrative": {
                "type": "string",
                "description": (
                    "One paragraph of analyst prose. No bullet lists, no field names, no "
                    "filter echo. Lead with the operational bottom line."
                ),
            },
            "sections": {
                "type": "array",
                "description": (
                    "One entry per sub-question the planner identified, in the order asked. "
                    "A single-part question yields exactly one section."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "question": {"type": "string"},
                        "narrative": {
                            "type": "string",
                            "description": (
                                "ONE sentence directly answering this part — the count and "
                                "the headline finding, nothing more. The owner groups carry "
                                "the detail; do not preview them here."
                            ),
                        },
                    },
                    "required": ["id", "question", "narrative"],
                    "additionalProperties": False,
                },
            },
            "groups": {
                "type": "array",
                "description": (
                    "The body of the answer, one entry per vendor or building, worst first. "
                    "This is what the reader scans — not the narrative."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "The vendor or building this group is about.",
                        },
                        "scope": {"type": "string", "enum": ["Building", "Vendor"]},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info", "ok"],
                        },
                        "headline": {
                            "type": "string",
                            "description": (
                                "One short line saying what this owner's situation is."
                            ),
                        },
                        "points": {
                            "type": "array",
                            "description": (
                                "Two to five short bullets. One fact each, in plain English, "
                                "no field names. Name the certificate, what is wrong or right "
                                "with it, and the date or number that proves it."
                            ),
                            "items": {"type": "string"},
                        },
                        "cert_ids": {"type": "array", "items": {"type": "string"}},
                        "sub_question_id": {"type": "string"},
                    },
                    "required": [
                        "owner",
                        "scope",
                        "severity",
                        "headline",
                        "points",
                        "cert_ids",
                        "sub_question_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "kpis": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "count": {"type": "integer"},
                        "label": {"type": "string"},
                        "sublabel": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info", "ok"],
                        },
                        "unit": {
                            "type": "string",
                            "enum": ["certificates", "vendors", "buildings", "other"],
                            "description": "What the number counts, so it can be checked.",
                        },
                        "cert_ids": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "Every certificate this number is derived from. For a count "
                                "of certificates this must have exactly `count` entries."
                            ),
                        },
                    },
                    "required": [
                        "count",
                        "label",
                        "sublabel",
                        "severity",
                        "unit",
                        "cert_ids",
                    ],
                    "additionalProperties": False,
                },
            },
            "actions": {
                "type": "array",
                "description": (
                    "Priority actions ranked by OPERATIONAL urgency, worst first. Each title "
                    "is an INSTRUCTION — what to do and to whom. The evidence is already in "
                    "the owner groups, so do not repeat scores, dates or findings here."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "scope": {"type": "string", "enum": ["Building", "Vendor", "Mixed"]},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info"],
                        },
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "cert_ids": {"type": "array", "items": {"type": "string"}},
                        "sub_question_id": {
                            "type": "string",
                            "description": "Which section this belongs to (e.g. q1).",
                        },
                    },
                    "required": [
                        "title",
                        "scope",
                        "severity",
                        "tags",
                        "cert_ids",
                        "sub_question_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "insights": {
                "type": "array",
                "description": (
                    "Analyst observations: cross-module correlations, anomalies, or items "
                    "disproportionately overdue relative to the rest."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["correlation", "anomaly", "risk"],
                        },
                        "text": {"type": "string"},
                        "sub_question_id": {"type": "string"},
                    },
                    "required": ["type", "text", "sub_question_id"],
                    "additionalProperties": False,
                },
            },
            "certificates": {
                "type": "array",
                "description": "Every row the answer covers, in the order presented.",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "name": {"type": "string"},
                        "scope": {"type": "string", "enum": ["Building", "Vendor"]},
                        "company": {"type": "string"},
                        "status": {"type": "string"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info", "ok"],
                        },
                        "reason": {
                            "type": "string",
                            "description": (
                                "A SHORT qualifier for this row — at most fifteen words, no "
                                "sentence. This is a reference table beside the owner "
                                "groups, so give the one fact that places the row "
                                "(\"forensics 100/100, expired 2015\"), never a restatement "
                                "of the group bullets."
                            ),
                        },
                        "sub_question_id": {"type": "string"},
                    },
                    "required": [
                        "id",
                        "name",
                        "scope",
                        "company",
                        "status",
                        "severity",
                        "reason",
                        "sub_question_id",
                    ],
                    "additionalProperties": False,
                },
            },
            "expiry_events": {
                "type": "array",
                "description": (
                    "Timeline of what expires next, soonest first. Only rows that are not "
                    "already lapsed."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "cert_id": {"type": "string"},
                        "cert_name": {"type": "string"},
                        "owner": {
                            "type": "string",
                            "description": "Vendor for vendor certs, building for building certs.",
                        },
                        "expiry_date": {"type": "string"},
                        "days_remaining": {"type": "integer"},
                        "severity": {
                            "type": "string",
                            "enum": ["critical", "warning", "info", "ok"],
                        },
                    },
                    "required": [
                        "cert_id",
                        "cert_name",
                        "owner",
                        "expiry_date",
                        "days_remaining",
                        "severity",
                    ],
                    "additionalProperties": False,
                },
            },
            "pending": {
                "type": "array",
                "description": (
                    "Items waiting on the property manager, not on a vendor. Say only what "
                    "DECISION they must make — the evidence for it is in the owner groups "
                    "and must not be restated."
                ),
                "items": {
                    "type": "object",
                    "properties": {
                        "cert_id": {"type": "string"},
                        "name": {"type": "string"},
                        "what_is_pending": {
                            "type": "string",
                            "description": "What the PM must do, in plain English.",
                        },
                    },
                    "required": ["cert_id", "name", "what_is_pending"],
                    "additionalProperties": False,
                },
            },
        },
        # Only the answer itself is required. Every other zone is optional, because a
        # question with one fact as its answer has no groups, no KPI tiles and no priority
        # actions — and requiring them produced exactly that: a dashboard in reply to "how
        # many types are in the pack?", which reads as a template being filled rather than a
        # question being answered. The model now decides which zones the question earns.
        # Every consumer already reads these with `.get(key) or []`, so absence is safe.
        "required": [
            "narrative",
            "sections",
        ],
        "additionalProperties": False,
    }

    _ANALYST_PROMPT = prompt_doc("compliance", "answering")
    #: What the words mean in the data and what the tables measure. These used to reach only
    #: the sub-agent, whose prose is notes; the analyst writes the answer and must read them too.
    _ANALYST_CONTEXT_DOCS = "\n\n---\n\n".join(
        [
            prompt_doc("compliance", "vocabulary"),
            prompt_doc("compliance", "tables"),
            prompt_doc("compliance", "domain_compliance_knowledge"),
        ]
    )
    """skills/compliance/answering.md - how an answer is shaped. Prose belongs in a file the people who write compliance policy can read and change, not in a Python string literal."""

    # Appended to the analyst prompt when the question asks what is REQUIRED. Without it the
    # analyst does what its main prompt says — reasons about the register rows — and returns a
    # portfolio status report to someone who asked what the law obliges them to hold.
    _TAXONOMY_DIRECTIVE = prompt_doc("compliance", "taxonomy")
    """skills/compliance/taxonomy.md - appended when the question asks what is REQUIRED."""

    # Pseudo-zones used to push non-answer information up the same channel as the zones.
    _PLAN_ZONE = "__plan__"
    _STEP_ZONE = "__step__"
    _EVENT_ZONE = "__event__"  # a ready-made WebSocket event riding the zone queue
    #: Every zone the client paints, in paint order. The stream tail re-emits all of them
    #: from the validated final, so the page never keeps a raw streamed value.
    _ZONE_KEYS = (
        "narrative", "sections", "groups", "kpis", "actions",
        "insights", "certificates", "expiry_events", "pending",
    )

    # What the planner may ask for beyond the certificate register itself (which is always read).
    _PLAN_SOURCES = ("regulations", "coverage", "work_orders")

    # A question about what the LAW requires, not about what is on file. These two are
    # different questions with different sources, and answering the first from the register
    # is the worst failure this engine has: "what must a UK building hold?" is answered from
    # the country pack, and a portfolio with nothing on record must still get the full list.
    # Zero certificates of a type is not evidence the type is not required.
    _TAXONOMY_SUBJECTS = (
        "regulation", "regulations", "regulatory", "statutory", "legislation", "law",
        "legal requirement", "legal requirements", "requirement", "requirements",
        "certificate", "certificates", "certification", "certifications",
        "certificate type", "certificate types",
        "compliance requirement", "compliance requirements",
        "country pack", "obligation", "obligations", "duty", "duties",
    )
    _TAXONOMY_VERBS = (
        "required", "require", "requires", "mandatory", "obligatory", "must hold",
        "need to hold", "needs to hold", "should hold", "have to hold", "what is needed",
        "what do we need", "by law", "legally", "obliged", "applicable", "apply to",
    )
    # Words that pull it back to the portfolio: the user is asking about THEIR records.
    _REGISTER_SUBJECTS = (
        "on record", "on file", "we hold", "our certificate", "our certificates",
        "lapsed", "expired", "expiring", "blocked", "draft", "forensic", "forged",
        "renewal", "which vendor", "which building", "status of",
    )

    @classmethod
    def _is_taxonomy_question(cls, text: str) -> bool:
        """True when the question asks what is REQUIRED rather than what is HELD.

        Deliberately deterministic rather than left to the planner. The planner is an LLM
        call that falls back to "read the register" on any failure, so a taxonomy question
        arriving during a planner outage would silently be answered from the wrong table.
        """
        t = f" {(text or '').lower()} "
        if not any(f" {w} " in t or t.startswith(f" {w} ") for w in cls._TAXONOMY_SUBJECTS):
            return False
        if not any(w in t for w in cls._TAXONOMY_VERBS):
            return False
        # "which of our required certificates are lapsed" is a register question wearing
        # taxonomy words — the user wants their rows, not the statutory list.
        return not any(w in t for w in cls._REGISTER_SUBJECTS)

    @staticmethod
    def _loads_lenient(raw: str) -> dict[str, Any]:
        """Parse the planner's JSON, tolerating the ways models commonly malform it.

        A parse failure here silently drops every filter and re-reads the whole register, so
        the answer still arrives but the query behind it is gone. Recovering the object is
        worth more than being strict about a trailing comma.
        """
        try:
            return json.loads(raw)
        except Exception:  # noqa: BLE001 — fall through to the repairs below
            pass
        text = raw.strip()
        # Prose either side of the object.
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end > start:
            text = text[start : end + 1]
        # Trailing commas before a close, and single-quoted keys/values.
        repaired = re.sub(r",\s*([}\]])", r"\1", text)
        for candidate in (repaired, repaired.replace("'", '"')):
            try:
                out = json.loads(candidate)
            except Exception:  # noqa: BLE001
                continue
            if isinstance(out, dict):
                return out
        # Truncated mid-object: close what is still open, innermost first. Counting braces
        # and brackets separately would close them in the wrong order on nested output.
        stack: list[str] = []
        in_string = escaped = False
        for ch in repaired:
            if in_string:
                if escaped:
                    escaped = False
                elif ch == "\\":
                    escaped = True
                elif ch == '"':
                    in_string = False
                continue
            if ch == '"':
                in_string = True
            elif ch in "{[":
                stack.append(ch)
            elif ch in "}]" and stack:
                stack.pop()
        if stack or in_string:
            patched = repaired + ('"' if in_string else "")
            # Drop a dangling key or comma the truncation left behind.
            patched = re.sub(r'[,\s]*("[^"]*"\s*:)?[,\s]*$', "", patched)
            patched += "".join("}" if c == "{" else "]" for c in reversed(stack))
            try:
                out = json.loads(patched)
            except Exception:  # noqa: BLE001
                out = None
            if isinstance(out, dict):
                return out
        raise ValueError("planner JSON unrecoverable")

    async def _plan_compliance_query(self, user_message: str) -> dict[str, Any]:
        """Decide what this question needs BEFORE any tool runs.

        Without a planning step the agent can only answer the literal question — it cannot
        pull in what else is relevant. Deliberately fast and low-stakes: the certificate
        register is always read, so a planner failure degrades to "register only" rather than
        breaking the turn.
        """
        text = (user_message or "").strip()
        taxonomy = self._is_taxonomy_question(text)
        default = {
            "reason": (
                "Answer from the country certificate pack — the question is about what is "
                "required, not what is on file — and read the register to show what is held "
                "against it."
                if taxonomy
                else "Read the certificate register and answer from it."
            ),
            "needs": ["regulations", "coverage"] if taxonomy else [],
            "taxonomy": taxonomy,
            "query": {},
            "sub_questions": [
                {"id": "q1", "text": (user_message or "").strip()[:400], "query": {}}
            ],
        }
        if not text:
            return default
        # Outside the LLM guard below, this is the planner's first database touch — and
        # `_compliance_column_allowlist` inside it has no handler of its own, so a timeout
        # raised straight out of the planner rather than degrading. The vocabulary only
        # supplies real column VALUES so the planner does not invent filter words; without it
        # the planner still plans, it just writes filters more cautiously.
        try:
            vocab = await self._compliance_value_vocabulary()
        except Exception as exc:  # noqa: BLE001 — planning must never break the turn
            log.warning("compliance.vocab.unavailable", error=str(exc)[:200])
            vocab = {}
        vocab_text = (
            (
                "The values these columns actually hold:\n"
                + "".join(
                    f"- {col}: {', '.join(vals)}\n" for col, vals in sorted(vocab.items())
                )
            )
            if vocab
            else ""
        )
        system = SystemMessage(
            content=(
                "You plan what a compliance question needs before any data is fetched, you "
                "SPLIT IT if the user asked more than one thing, and you BUILD THE DATABASE "
                "QUERY for each part. Reply with JSON only:\n"
                '{"reason":"<one short sentence on what you will gather and why>",'
                '"needs":["regulations","coverage","work_orders"],'
                '"sub_questions":[{"id":"q1","text":"<the single thing being asked>",'
                '"query":{"scope":"vendor|building|both",'
                '"filters":[{"field":"<column>","op":"<op>","value":<v>}],'
                '"logic":"and|or","sort":{"field":"expiry_date","dir":"asc"}}}]}\n'
                "\nSPLIT ONLY WHERE IT CHANGES THE ANSWER. One question -> exactly one entry "
                "in sub_questions (this is the normal case). Split when the user genuinely "
                "asks separate things needing different data, e.g. \"which certificates are "
                "lapsed, and which vendors are blocked?\" -> q1 lapsed certificates, q2 "
                "blocked vendors. Do NOT split a single question that merely has two "
                "conditions (\"forged AND still valid\" is ONE question with two filters).\n"
                "\nBuild the query from the user's own words — read what they are asking for "
                "and express it as filters. Do not return an empty filter list just to fetch "
                "everything, unless the question really is a broad posture question.\n"
                "Columns you may filter on: status, days_to_expiry, expiry_date, issue_date, "
                "cert_scope, certificate_type_code, certificate_number, country_code, "
                "state (the nation/state/emirate within country_code, spelled out in "
                "full — England, Scotland, Wales, Northern Ireland for the UK), "
                "region (the city or county below state — London, Norfolk), "
                "building_name, issuer, inspector_name, result, remedial_status, "
                "insurance_risk_flag, authenticity_warning, vendor_name, vendor_block_state, "
                "site_name, asset_name, forensics_verdict, forensics_risk_score, draft, "
                "confirmed_by_pm.\n"
                "Operators: eq, neq, in, not_in, lt, lte, gt, gte, contains, is_null, "
                "not_null.\n"
                + vocab_text +
                "Use ONLY values that appear in that list for those columns. If the user's "
                "word is not one of them, express the question a different way — "
                "\"compliant\" and \"valid\" are NOT status values, they mean "
                'days_to_expiry gte 0; "expired" and "lapsed" mean days_to_expiry lt 0.\n'
                "Examples:\n"
                "- which vendor certificates are lapsed -> scope vendor, "
                '[{"field":"status","op":"in","value":["Lapsed","Expired"]}]\n'
                "- anything expiring in the next month -> "
                '[{"field":"days_to_expiry","op":"gte","value":0},'
                '{"field":"days_to_expiry","op":"lte","value":30}]\n'
                "- expiring within 7 days -> "
                '[{"field":"days_to_expiry","op":"gte","value":0},'
                '{"field":"days_to_expiry","op":"lte","value":7}]\n'
                "ALWAYS pair an expiry window with the lower bound "
                'days_to_expiry gte 0. days_to_expiry is NEGATIVE for anything already '
                "lapsed, so an upper bound on its own (lte 7) matches every expired "
                "certificate in the register as well as the ones about to expire.\n"
                "- forged certificates that are still valid -> "
                '[{"field":"forensics_verdict","op":"eq","value":"fail"},'
                '{"field":"days_to_expiry","op":"gte","value":0}]\n'
                "- which vendors are blocked -> scope vendor, "
                '[{"field":"vendor_block_state","op":"eq","value":"Blocked"}]\n'
                "\nTWO DIFFERENT QUESTIONS. \"What certificates does a UK building/vendor "
                "have to hold?\" asks what the LAW requires — that is answered from the "
                "country pack, so include \"regulations\" (and \"coverage\" to show what is "
                "held against it) and leave filters empty. \"Which of ours are lapsed?\" "
                "asks about the register. Never answer the first from the register: a type "
                "with no certificate on file is still required.\n"
                "Include a source only when the question genuinely needs it:\n"
                "- regulations: the question is about what is legally required, or the "
                "answer should cite why something is required per certificate.\n"
                "- coverage: the question is about gaps, what is missing, or what is not on "
                "record.\n"
                "- work_orders: the question touches scheduled work, jobs, or whether a "
                "vendor problem affects operations.\n"
                "Use an empty needs list for a plain status/list question."
            )
        )
        # Bound before the call: when ainvoke itself raises — a bad model name, a 400, a
        # timeout — the handler below still logs `raw`, and an unbound local would turn a
        # recoverable planning failure into an exception that takes the whole turn with it.
        raw: Any = ""
        try:
            resp = await self._llm.ainvoke(
                [system, HumanMessage(content=text[:1200])]
            )
            raw = getattr(resp, "content", "") or ""
            if isinstance(raw, list):
                raw = " ".join(
                    p.get("text", "") if isinstance(p, dict) else str(p) for p in raw
                )
            raw = re.sub(r"^```(?:json)?|```$", "", str(raw).strip(), flags=re.M).strip()
            plan = self._loads_lenient(raw)
        except Exception as exc:  # noqa: BLE001 — planning must never break the turn
            log.warning(
                "compliance.plan.failed", error=str(exc)[:200], raw=str(raw)[:600]
            )
            return default
        needs = [n for n in (plan.get("needs") or []) if n in self._PLAN_SOURCES]
        if taxonomy:
            # The planner may or may not have asked for it; the pack is not optional here.
            needs = list(dict.fromkeys([*needs, "regulations", "coverage"]))

        # Normalise to sub_questions. A single-part ask yields one entry, so everything
        # downstream takes the same path whether or not the question was split.
        subs: list[dict[str, Any]] = []
        raw_subs = plan.get("sub_questions")
        if isinstance(raw_subs, list):
            for i, sq in enumerate(raw_subs[:5]):
                if not isinstance(sq, dict):
                    continue
                subs.append(
                    {
                        "id": str(sq.get("id") or f"q{i + 1}"),
                        "text": str(sq.get("text") or user_message)[:400],
                        "query": sq.get("query") if isinstance(sq.get("query"), dict) else {},
                    }
                )
        if not subs:
            # Older shape (a bare "query") or nothing usable — treat as one question.
            subs = [
                {
                    "id": "q1",
                    "text": text[:400],
                    "query": plan.get("query") if isinstance(plan.get("query"), dict) else {},
                }
            ]

        out = {
            "reason": str(plan.get("reason") or default["reason"])[:400],
            "needs": needs,
            "taxonomy": taxonomy,
            "sub_questions": subs,
            # Kept for the single-question path and for logging.
            "query": subs[0]["query"],
        }
        log.info(
            "compliance.stage0.plan",
            reason=out["reason"],
            needs=needs,
            taxonomy=taxonomy,
            sub_questions=[
                {
                    "id": sq["id"],
                    "text": sq["text"][:80],
                    "scope": (sq["query"] or {}).get("scope"),
                    "filters": [
                        f"{f.get('field')} {f.get('op')} {f.get('value')}"
                        for f in ((sq["query"] or {}).get("filters") or [])
                        if isinstance(f, dict)
                    ],
                }
                for sq in subs
            ],
        )
        return out

    # Words that mean "look wider than what I just uploaded". A question carrying any of
    # these releases the ingestion pin for the rest of the session.
    _WIDEN_SCOPE_WORDS = (
        "all certificate", "all the certificate", "every certificate", "the register",
        "whole register", "entire register", "portfolio", "across the board",
        "all vendors", "all buildings", "all documents", "everything we hold",
        "on file overall", "not just", "other than the",
    )

    @classmethod
    def _ingestion_scope(cls, session_id: str, user_message: str) -> list[str]:
        """Certificate ids this answer should be limited to, or [] for the whole register.

        Returns the ids created by the most recent ingestion in this session. A question that
        explicitly asks to look wider clears the pin permanently — otherwise the user would be
        stuck inside their upload for the rest of the conversation with no way out.
        """
        from .session_workspace import clear_ingested_certificates, get_ingested_certificates

        ids = get_ingested_certificates(session_id)
        if not ids:
            return []
        msg = (user_message or "").lower()
        if any(w in msg for w in cls._WIDEN_SCOPE_WORDS):
            clear_ingested_certificates(session_id)
            log.info("compliance.scope.widened", session_id=session_id, released=len(ids))
            return []
        log.info("compliance.scope.pinned_to_ingestion", session_id=session_id, ids=len(ids))
        return ids

    @staticmethod
    def _compliance_plain_text(analysis: dict[str, Any]) -> str:
        """Render the typed answer as text for consumers that never see the components.

        The narrative is only the closing summary now, so returning it alone would hand the
        REST caller and the stored transcript a summary with the answer missing.
        """
        lines: list[str] = []
        for g in (analysis.get("groups") or []):
            if not isinstance(g, dict):
                continue
            owner = str(g.get("owner") or "").strip()
            headline = str(g.get("headline") or "").strip()
            lines.append(f"**{owner}** — {headline}" if headline else f"**{owner}**")
            for point in (g.get("points") or []):
                lines.append(f"- {point}")
            lines.append("")
        summary = str(analysis.get("narrative") or "").strip()
        if summary:
            lines.append(summary)
        return "\n".join(lines).strip()

    @staticmethod
    def _certificate_offers(
        analysis: dict[str, Any], rows: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """What the PM can actually DO about each row the answer names.

        Deterministic on purpose: the analyst reasons about what matters, code decides what
        is invocable. Letting the model propose operations would put a "send renewal" button
        on rows with nothing to renew, and every offer here maps to a real endpoint.

        Only rows the answer actually cites get offers — the register is not a to-do list.
        """
        by_id = {str(r.get("id")): r for r in rows if r.get("id")}
        cited: list[str] = []
        for c in (analysis.get("certificates") or []):
            if isinstance(c, dict) and str(c.get("id")) in by_id:
                cited.append(str(c["id"]))
        for p_ in (analysis.get("pending") or []):
            if isinstance(p_, dict) and str(p_.get("cert_id")) in by_id:
                cited.append(str(p_["cert_id"]))
        for a in (analysis.get("actions") or []):
            if isinstance(a, dict):
                cited.extend(str(i) for i in (a.get("cert_ids") or []) if str(i) in by_id)
        for g in (analysis.get("groups") or []):
            if isinstance(g, dict):
                cited.extend(str(i) for i in (g.get("cert_ids") or []) if str(i) in by_id)

        offers: list[dict[str, Any]] = []
        seen: set[str] = set()
        for cid in cited:
            if cid in seen:
                continue
            seen.add(cid)
            # Same rules the ingest path uses — see agents/compliance_offers.py.
            offers.extend(offers_for_row(by_id[cid]))
        return offers

    @staticmethod
    def _asked_scopes(plan: dict[str, Any]) -> tuple[str, ...]:
        """Which duty holders the question was about, from the plan the model already made.

        The planner records a scope per sub-question — "building", "vendor" or "both" — so
        the question's own scope is already decided by a model reading it. Nothing here needs
        to guess from keywords; it only has to stop ignoring the answer.
        """
        found = {
            str((sq.get("query") or {}).get("scope") or sq.get("scope") or "").lower()
            for sq in (plan.get("sub_questions") or [])
            if isinstance(sq, dict)
        }
        if "both" in found or not found - {"", "both"}:
            return ("Building", "Vendor")
        out = [s_.title() for s_ in ("building", "vendor") if s_ in found]
        return tuple(out) or ("Building", "Vendor")

    @staticmethod
    def _missing_type_offers(
        tool_calls: list[dict[str, Any]],
        pack_facts: dict[str, Any] | None,
        scopes: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        """Offers for the required types with no certificate behind them.

        The answer that started this named 41 statutory duties with nothing on record and
        gave the reader no way to act on a single one of them, because every offer in the
        system hangs off a certificate id and these have no certificate. The gap the answer
        exists to surface was the one thing it could not do anything about.

        Derived from the pack, not from the prose: the analyst decides what to say about a
        missing duty, code decides that a missing duty is actionable.
        """
        if not pack_facts:
            return []
        pack = next(
            (
                tc.get("output")
                for tc in tool_calls
                if tc.get("tool") == "list_country_pack"
                and isinstance(tc.get("output"), dict)
            ),
            None,
        )
        types = (pack or {}).get("types")
        if not isinstance(types, list):
            return []
        by_code = {
            str(t.get("certificate_type_code")): t for t in types if isinstance(t, dict)
        }
        # Only the duty holders the question asked about. A question about what a building
        # OWNER must hold was answered with 22 owner duties and 28 contractor accreditations,
        # because this loop ran both scopes unconditionally — code deciding to show more than
        # was asked for, which is the whole complaint about generated answers.
        wanted = [s_ for s_ in ("Building", "Vendor") if s_ in (scopes or ("Building", "Vendor"))]
        out: list[dict[str, Any]] = []
        for scope in wanted:
            for code in ((pack_facts.get("by_scope") or {}).get(scope) or {}).get(
                "codes_with_nothing_on_record"
            ) or []:
                t = by_code.get(str(code))
                if t:
                    out.extend(offers_for_missing_type(t))
        return out

    async def _relax_empty_spec(
        self, spec: dict[str, Any]
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Widen a spec that matched nothing by the smallest step that finds rows.

        A compound AND matching nothing usually means the conditions are individually fine
        and simply never co-occur — which is the answer to questions like "blocked vendors
        whose certificate is still valid". Dropping one condition at a time and keeping the
        result with the FEWEST rows gives the analyst the evidence the question is about
        (the blocked vendors) rather than the whole register.

        Returns the rows and a note describing what was dropped, or ([], {}) when nothing
        narrower than a full read matches — the caller falls back to the register then.
        """
        filters = [f for f in (spec.get("filters") or []) if isinstance(f, dict)]
        if len(filters) < 2 or str(spec.get("logic") or "and").lower() != "and":
            # One condition matching nothing is far more likely to be a wrong value than a
            # true empty, and there is nothing to relax anyway.
            return [], {}

        variants = [
            (j, {**spec, "filters": filters[:j] + filters[j + 1 :]})
            for j in range(len(filters))
        ]
        results = await asyncio.gather(
            *(self._fetch_compliance_table_direct(spec=v) for _, v in variants),
            return_exceptions=True,
        )
        best: tuple[int, list[dict[str, Any]]] | None = None
        for (j, _v), res in zip(variants, results):
            if isinstance(res, Exception) or not res:
                continue
            if best is None or len(res) < len(best[1]):
                best = (j, res)
        if best is None:
            return [], {}

        j, rows = best
        dropped = filters[j]
        kept = [
            f"{f.get('field')} {f.get('op')} {f.get('value')}"
            for f in filters[:j] + filters[j + 1 :]
        ]
        return rows, {
            "dropped": f"{dropped.get('field')} {dropped.get('op')} {dropped.get('value')}",
            "kept": kept,
            "reason": "no row satisfied every condition",
        }

    async def _fetch_planned_extras(
        self, needs: list[str]
    ) -> list[dict[str, Any]]:
        """Run the planner's extra fetches concurrently. Each is best-effort."""
        if not needs:
            return []
        from .compliance_engine_agent import get_compliance_coverage, list_country_pack

        async def _regulations() -> dict[str, Any] | None:
            # Regulation text comes from the country pack (regulation_reference), so the
            # analyst cites a stored regulation rather than inventing one.
            out = await list_country_pack.ainvoke({"country_code": "UK"})
            return {
                "tool": "list_country_pack",
                "input": {"country_code": "UK"},
                "output": out,
            }

        async def _coverage() -> dict[str, Any] | None:
            out = await get_compliance_coverage.ainvoke({})
            return {"tool": "get_compliance_coverage", "input": {}, "output": out}

        async def _work_orders() -> dict[str, Any] | None:
            rows = await self._fetch_open_work_orders()
            return {
                "tool": "list_open_work_orders",
                "input": {"scope": "open"},
                "output": {"work_orders": rows},
            }

        jobs = {
            "regulations": _regulations,
            "coverage": _coverage,
            "work_orders": _work_orders,
        }
        # Zip the FILTERED names, not `needs` — an unrecognised entry shifts the pairing and
        # the failure below is logged against the wrong source.
        selected = [(n, jobs[n]) for n in needs if n in jobs]
        results = await asyncio.gather(
            *(fn() for _, fn in selected), return_exceptions=True
        )
        out: list[dict[str, Any]] = []
        for (need, _fn), res in zip(selected, results):
            if isinstance(res, Exception):
                log.warning("compliance.plan.extra_failed", need=need, error=str(res)[:200])
                continue
            if res:
                out.append(res)
        return out

    @staticmethod
    def _planned_extra_step(extra: dict[str, Any]) -> dict[str, Any] | None:
        """Name an extra source in the trace, so "how this answer was produced" is honest.

        Without this the panel showed plan → register → analyst, and a taxonomy answer built
        from the country pack read as though it came from the register alone. The pack was
        being fetched all along; the trace simply never said so, which is the same thing to
        anyone reading it.
        """
        tool = extra.get("tool")
        out = extra.get("output")
        out = out if isinstance(out, dict) else {}
        if tool == "list_country_pack":
            types = out.get("types") or []
            building = sum(
                1
                for t in types
                if isinstance(t, dict)
                and str(t.get("certificate_scope", "")).lower() == "building"
            )
            vendor = sum(
                1
                for t in types
                if isinstance(t, dict)
                and str(t.get("certificate_scope", "")).lower() == "vendor"
            )
            count = out.get("count") if isinstance(out.get("count"), int) else len(types)
            return {
                "stage": "data",
                "label": "Read the country certificate pack",
                "detail": (
                    f"{count} statutory certificate types ({building} building, "
                    f"{vendor} vendor) from plenum_cafm.country_certificate_packs — what "
                    "the law requires, whether or not a certificate is on file"
                ),
            }
        if tool == "get_compliance_coverage":
            buildings = (out.get("buildings") or {}).get("buildings") or []
            vendors = (out.get("vendors") or {}).get("vendors") or []
            return {
                "stage": "data",
                "label": "Read pack coverage",
                "detail": (
                    f"{len(buildings)} building(s) and {len(vendors)} vendor(s) scored "
                    "against the pack, to show which required types have no record"
                ),
            }
        if tool == "list_open_work_orders":
            rows = out.get("work_orders") or []
            return {
                "stage": "data",
                "label": "Read open work orders",
                "detail": f"{len(rows)} open work order(s) from plenum_cafm.work_orders",
            }
        return None

    async def _fetch_open_work_orders(self, limit: int = 200) -> list[dict[str, Any]]:
        """Open work orders with whatever vendor identity they carry, for cross-module checks.

        NOTE on current data: work_orders.vendor_id is unpopulated and the free-text vendor
        names do not match the certificate vendors, so this returns rows that legitimately
        correlate with nothing. Kept because the join is correct the moment that linkage is
        filled in — and the analyst is told not to claim a correlation the data can't support.
        """
        from sqlalchemy import text as _sql

        from .. import database

        if database.AsyncSessionLocal is None:
            database.init_session_factory()
        sql = _sql(
            """
            SELECT id::text, work_order_id, title, status, priority, scheduled_date,
                   vendor_id::text AS vendor_id,
                   COALESCE(assigned_vendor, vendor) AS vendor_name,
                   asset, location, site_id::text AS site_id
            FROM plenum_cafm.work_orders
            WHERE status IN ('Open','InProgress','In Progress','pending_approval')
            ORDER BY scheduled_date ASC NULLS LAST
            LIMIT :limit
            """
        )
        async with database.AsyncSessionLocal() as session:
            result = await session.execute(sql, {"limit": limit})
            return [dict(r) for r in result.mappings().all()]

    @staticmethod
    def _validate_compliance_response(
        analysis: dict[str, Any],
        rows: list[dict[str, Any]],
        sub_questions: list[dict[str, Any]] | None = None,
        taxonomy: bool = False,
    ) -> tuple[dict[str, Any], list[str]]:
        """EL gate: check the analyst's answer against the rows it was actually given.

        The sub-agent reasons; the orchestrator verifies before anything reaches the user.
        Every certificate the answer names must trace to a fetched row (CLAUDE.md EL-7.QUERY:
        "every value traces to a DB row"), and stated counts must match what was listed.
        Anything ungrounded is stripped rather than shown, and every correction is logged.
        """
        issues: list[str] = []
        by_id = {str(r.get("id")): r for r in rows if r.get("id")}
        valid_ids = set(by_id)

        # 1. certificates[] — drop any row the fetch did not return, and correct drifted
        #    identity fields back to the database values.
        certs = [c for c in (analysis.get("certificates") or []) if isinstance(c, dict)]
        kept: list[dict[str, Any]] = []
        seen_cert_keys: set[tuple[str, str]] = set()
        for c in certs:
            cid = str(c.get("id") or "")
            if cid not in valid_ids:
                issues.append(f"dropped ungrounded certificate id={cid[:12] or '(none)'}")
                continue
            row = by_id[cid]
            db_status = row.get("status")
            if db_status and str(c.get("status") or "").lower() != str(db_status).lower():
                issues.append(
                    f"status corrected for {cid[:8]}: "
                    f"{c.get('status')!r} -> {db_status!r}"
                )
                c["status"] = db_status
            key = (cid, str(c.get("sub_question_id") or ""))
            if key in seen_cert_keys:
                issues.append(f"dropped duplicate certificate id={cid[:8]}")
                continue
            seen_cert_keys.add(key)
            kept.append(c)
        analysis["certificates"] = kept

        # 2. actions[].cert_ids must reference fetched rows.
        for a in (analysis.get("actions") or []):
            if not isinstance(a, dict):
                continue
            ids = [str(i) for i in (a.get("cert_ids") or [])]
            good = [i for i in ids if i in valid_ids]
            if len(good) != len(ids):
                issues.append(
                    f"action {str(a.get('title'))[:40]!r}: dropped "
                    f"{len(ids) - len(good)} unknown cert id(s)"
                )
            a["cert_ids"] = good

        # 3. every KPI number must be traceable to the rows it claims to count. Bounding the
        #    count by the fetched total only caught overstatements — an understatement (a
        #    headline of 8 above a list of 9) read as authoritative and passed straight
        #    through. Each KPI now carries the ids it was derived from, so the number can be
        #    checked against them in both directions.
        total = len(rows)
        for k in (analysis.get("kpis") or []):
            if not isinstance(k, dict):
                continue
            label = str(k.get("label"))[:40]
            try:
                count = int(k.get("count"))
            except (TypeError, ValueError):
                continue
            ids = [str(i) for i in (k.get("cert_ids") or [])]
            grounded = {i for i in ids if i in valid_ids}
            if len(grounded) != len(set(ids)):
                issues.append(
                    f"KPI {label!r}: dropped {len(set(ids)) - len(grounded)} unknown cert id(s)"
                )
                k["cert_ids"] = sorted(grounded)
            unit = str(k.get("unit") or "certificates").lower()
            # `other` means the number is NOT a count of rows — "19 years since the oldest
            # lapse", "55 required pack types". Bounding such a number by the row count is a
            # category error, and it fired as one: "counts 19 other from only 1
            # certificate(s)" and "claimed 19 of 6 fetched" were both reported against a
            # correct answer. A magnitude has no cardinality to check, so the two comparisons
            # below are skipped for it — while the untraceable check still applies, except on
            # a taxonomy answer where a required type legitimately has no certificate to cite.
            is_magnitude = unit == "other"
            if is_magnitude and taxonomy:
                continue
            if unit == "certificates":
                # The only unit where the number IS the row count, so it can be corrected.
                if count != len(grounded):
                    issues.append(
                        f"KPI {label!r} said {count} but cites {len(grounded)} "
                        f"certificate(s) — corrected"
                    )
                    k["count"] = len(grounded)
            elif not is_magnitude and count > len(grounded) and grounded:
                # A vendor or building count cannot exceed the certificates behind it.
                issues.append(
                    f"KPI {label!r} counts {count} {unit} from only "
                    f"{len(grounded)} certificate(s)"
                )
            elif not ids and count > 0:
                issues.append(f"KPI {label!r} claims {count} with no certificate to trace it to")
            if not is_magnitude and count > total:
                issues.append(f"KPI {label!r} claimed {count} of {total} fetched")

        # 4. sections[] must line up with the parts the planner actually identified, and
        #    every sub_question_id used elsewhere must name one of them. A drifted id would
        #    silently orphan a card in the interface.
        known_ids = {
            str(sq.get("id"))
            for sq in (sub_questions or [])
            if isinstance(sq, dict) and sq.get("id")
        } or {"q1"}
        sections = [
            sec
            for sec in (analysis.get("sections") or [])
            if isinstance(sec, dict) and str(sec.get("id")) in known_ids
        ]
        if len(sections) != len(analysis.get("sections") or []):
            issues.append("dropped section(s) with an unplanned id")
        # Two sections with the same id make the page render every card under each of them.
        # It happened when a revision was told to "add one sentence" and the model added a
        # second section instead. Keep the first; fold a later duplicate's narrative into it.
        merged: list[dict[str, Any]] = []
        seen_ids: dict[str, dict[str, Any]] = {}
        for sec in sections:
            sid_ = str(sec.get("id"))
            if sid_ in seen_ids:
                extra = str(sec.get("narrative") or "").strip()
                if extra and extra not in str(seen_ids[sid_].get("narrative") or ""):
                    seen_ids[sid_]["narrative"] = (
                        str(seen_ids[sid_].get("narrative") or "").strip() + " " + extra
                    ).strip()
                issues.append(f"merged duplicate section id {sid_!r}")
                continue
            seen_ids[sid_] = sec
            merged.append(sec)
        sections = merged
        if not sections:
            # Never leave the interface with nothing to group under.
            first = (sub_questions or [{}])[0]
            sections = [
                {
                    "id": str(first.get("id") or "q1"),
                    "question": str(first.get("text") or "")[:300],
                    "narrative": str(analysis.get("narrative") or ""),
                }
            ]
        analysis["sections"] = sections
        default_sid = sections[0]["id"]
        for g in (analysis.get("groups") or []):
            if not isinstance(g, dict):
                continue
            ids = [str(i) for i in (g.get("cert_ids") or [])]
            good = [i for i in ids if i in valid_ids]
            if len(good) != len(ids):
                issues.append(
                    f"group {str(g.get('owner'))[:40]!r}: dropped "
                    f"{len(ids) - len(good)} unknown cert id(s)"
                )
            g["cert_ids"] = good

        for key in ("actions", "insights", "certificates", "groups"):
            for item in (analysis.get(key) or []):
                if not isinstance(item, dict):
                    continue
                sid = str(item.get("sub_question_id") or "")
                if sid not in known_ids:
                    if sid:
                        issues.append(
                            f"{key}: unknown sub_question_id {sid[:12]!r} -> {default_sid}"
                        )
                    item["sub_question_id"] = default_sid

        analysis["validation"] = {
            "rows_fetched": total,
            "sections": [s_["id"] for s_ in sections],
            "certificates_kept": len(kept),
            "issues": issues,
        }
        return analysis, issues

    # "27 building types and 31 vendor types" — the shape of a stated pack total in prose.
    _TYPE_COUNT_RE = re.compile(
        r"(\d+)\s+(?:required\s+)?(building|vendor)\b[^.;]{0,60}?\btypes?\b", re.I
    )

    @classmethod
    def _taxonomy_findings(
        cls, analysis: dict[str, Any], pack_facts: dict[str, Any]
    ) -> list[str]:
        """Check the numbers in a taxonomy answer against the pack they claim to count.

        Numbers only. Whether the answer covers every required type, and whether it actually
        answered the question asked, are judgements — they go to the reviewer, which can read
        prose. What code can settle exactly, code settles exactly, so the reviewer spends its
        attention on the part that needs reasoning.
        """
        findings: list[str] = []
        by_scope = pack_facts.get("by_scope") or {}
        truth = {
            scope.lower(): (by_scope.get(scope) or {}).get("required_types")
            for scope in ("Building", "Vendor")
        }

        narrative = str(analysis.get("narrative") or "")
        for text, scope in cls._TYPE_COUNT_RE.findall(narrative):
            real = truth.get(scope.lower())
            if real is None:
                continue
            try:
                claimed = int(text)
            except ValueError:
                continue
            # A count of what is MISSING is a different number from the pack total; only flag
            # a claim that reads as the total itself.
            if claimed != real and claimed > real:
                findings.append(
                    f"narrative says {claimed} {scope.lower()} types; the pack holds {real}"
                )

        for k in (analysis.get("kpis") or []):
            if not isinstance(k, dict):
                continue
            label = str(k.get("label") or "")
            low = label.lower()
            if "type" not in low:
                continue
            scope = "building" if "building" in low else "vendor" if "vendor" in low else ""
            real = truth.get(scope)
            if real is None:
                continue
            try:
                count = int(k.get("count"))
            except (TypeError, ValueError):
                continue
            missing = (by_scope.get(scope.title()) or {}).get(
                "types_with_nothing_on_record"
            )
            if count not in {real, missing}:
                findings.append(
                    f"KPI {label[:48]!r} says {count}; the pack holds {real} {scope} "
                    f"types ({missing} with nothing on record)"
                )

        return findings

    #: Panels attached to an answer after the model has written it. They carry ids so the
    #: eval agent can name one and code can remove it by lookup — the alternative is reading
    #: the model's prose to work out what it meant, which is a judgement wearing a regex.
    _PANEL_MISSING_DUTIES = "required_nothing_on_record"
    _PANEL_IDS: frozenset[str] = frozenset({_PANEL_MISSING_DUTIES})

    _REVIEW_SCHEMA: dict[str, Any] = {
        "type": "object",
        "properties": {
            "verdict": {"type": "string", "enum": ["pass", "revise"]},
            "reason": {"type": "string"},
            "findings": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "severity": {"type": "string", "enum": ["error", "warning"]},
                        "detail": {"type": "string"},
                    },
                    "required": ["severity", "detail"],
                    "additionalProperties": False,
                },
            },
            "missing_type_codes": {"type": "array", "items": {"type": "string"}},
            "drop_panels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of attached panels the question did not ask for.",
            },
        },
        "required": ["verdict", "reason", "findings", "missing_type_codes", "drop_panels"],
        "additionalProperties": False,
    }

    _REVIEWER_PROMPT = "\n\n---\n\n".join(
        [
            prompt_doc("compliance", "review"),
            prompt_doc("compliance", "scope-eval"),
            prompt_doc("compliance", "vocabulary"),
            prompt_doc("compliance", "domain_compliance_knowledge"),
        ]
    )
    """skills/compliance/review.md - the reviewer's contract."""

    @staticmethod
    def _tool_results_digest(tool_calls: list[dict[str, Any]] | None) -> str:
        """Every tool call the author made, as the reviewer's evidence. Serialisation only."""
        if not tool_calls:
            return "(none)"
        parts: list[str] = []
        budget = 24000
        for tc in tool_calls:
            if not isinstance(tc, dict) or str(tc.get("tool", "")).startswith("phase2_engine:"):
                continue
            body = json.dumps(
                {"tool": tc.get("tool"), "input": tc.get("input"), "output": tc.get("output")},
                default=str,
                ensure_ascii=False,
            )
            if len(body) > budget:
                body = body[:budget] + " …[truncated]"
            budget -= len(body)
            parts.append("- " + body)
            if budget <= 0:
                break
        return chr(10).join(parts) or "(none)"

    @staticmethod
    def _pack_names_from_tool_results(tool_results: list[dict[str, Any]] | None) -> dict[str, str]:
        """code -> name for every pack type any tool returned. A scan, not a decision."""
        names: dict[str, str] = {}

        def walk(o: Any) -> None:
            if isinstance(o, dict):
                c, n = o.get("certificate_type_code"), o.get("certificate_type_name")
                if c and n:
                    names.setdefault(str(c), str(n))
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(tool_results or [])
        return names

    @staticmethod
    def _verified_missing_codes(
        review: dict[str, Any] | None, answer_text: str, names: dict[str, str]
    ) -> list[str]:
        """Keep a reviewer-named missing code only if neither the code nor its pack name is in
        the answer. The reviewer once reported 23 types missing from an answer that named
        them all; the author, told to add 23, padded the list with duplicates and dropped four
        real types to keep the count. Whether a string appears in a text is a lookup, so code
        does it; what to do about a genuinely missing type stays the model's call."""
        norm = lambda v: re.sub(r"[^a-z0-9]", "", str(v).lower())  # noqa: E731
        text = norm(answer_text or "")
        kept: list[str] = []
        for c in (review or {}).get("missing_type_codes") or []:
            code = str(c).strip()
            if not code or norm(code) in text:
                continue
            name = names.get(code)
            if name and norm(name) in text:
                continue
            kept.append(code)
        return kept

    @staticmethod
    def _cached_system(text: str) -> list[dict[str, Any]]:
        """The system prompt as a cacheable block.

        answering.md, taxonomy.md, review.md and scope-eval.md are byte-identical on every
        call, and together they are the largest fixed cost of a turn. Marking them lets the
        API serve the prefix from cache: less time to first token and fewer input tokens
        billed. The question and the data follow the block, so they never break the prefix.
        """
        return [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]

    @staticmethod
    def _step_cost(role: str) -> dict[str, Any]:
        """Time, cost and model of the last model call in this role, for a pipeline step."""
        ledger = llm_cost.current()
        e = ledger.last(role) if ledger else None
        if not e:
            return {}
        out: dict[str, Any] = {"ms": e.get("ms"), "usd": e.get("usd"), "model": e.get("model")}
        if e.get("effort"):
            out["effort"] = e["effort"]
        if e.get("cache_hit"):
            out["cache_hit"] = True
        return out

    @classmethod
    def _early_pipeline_steps(cls, tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """The three steps before the analyst — routing, document choice, fetch — rebuilt from
        the ledger so the pipeline panel shows the whole turn, not its second half."""
        ledger = llm_cost.current()
        if not ledger:
            return []
        steps: list[dict[str, Any]] = []
        e = ledger.last("agent_router")
        if e:
            steps.append(
                {
                    "stage": "route",
                    "label": f"Routed the question — {e.get('agent') or 'compliance'}",
                    "detail": str(e.get("reason") or ""),
                    **cls._step_cost("agent_router"),
                }
            )
        e = ledger.last("doc_router")
        if e:
            steps.append(
                {
                    "stage": "docs",
                    "label": "Chose the skill documents",
                    "detail": ", ".join(str(d) for d in (e.get("docs") or [])) or str(e.get("reason") or ""),
                    **cls._step_cost("doc_router"),
                }
            )
        e = ledger.last("sub_agent")
        if e:
            tools = [
                str(tc.get("tool"))
                for tc in tool_calls
                if isinstance(tc, dict)
                and not str(tc.get("tool", "")).startswith(("phase2_engine:", "compliance_"))
            ]
            rows = 0
            for tc in tool_calls:
                out_ = tc.get("output") if isinstance(tc, dict) and isinstance(tc.get("output"), dict) else {}
                # Row lists sit at the top level or one level down (the coverage tool nests
                # its per-scope report under "buildings" / "vendors").
                for key in ("certificates", "buildings", "vendors", "types"):
                    v = out_.get(key)
                    if isinstance(v, list):
                        rows += len(v)
                    elif isinstance(v, dict):
                        inner = v.get(key)
                        if isinstance(inner, list):
                            rows += len(inner)
            steps.append(
                {
                    "stage": "data",
                    "label": (
                        f"Fetched the data — {len(tools)} tool{'s' if len(tools) != 1 else ''}, "
                        f"{rows} row{'s' if rows != 1 else ''}"
                    ),
                    "detail": ", ".join(tools),
                    **cls._step_cost("sub_agent"),
                }
            )
        return steps

    @staticmethod
    def _register_index(rows: list[dict[str, Any]]) -> str:
        """One line per register row — what a reviewer needs to tell cited from invented."""
        if not rows:
            return "(no rows — the answer should cite no certificate)"
        lines = []
        for r in rows[:120]:
            # A Building-scope row belongs to the building; the vendor on it is who issued
            # the document. Listing the vendor first made the reviewer see "RDM Electrical"
            # where the answer said "Building 5" and hedge on a name that was correct.
            if str(r.get("cert_scope") or "").lower() == "building":
                owner = (
                    r.get("building_name")
                    or r.get("site_label")
                    or (f"{r.get('vendor_name')} (issuer; no building named)" if r.get("vendor_name") else None)
                    or "—"
                )
            else:
                owner = r.get("vendor_name") or r.get("building_name") or r.get("site_label") or "—"
            lines.append(
                f"- {r.get('certificate_type_code')} | {owner} | expiry "
                f"{str(r.get('expiry_date'))[:10]} | {r.get('status')} | forensics "
                f"{r.get('forensics_risk_score')}/{r.get('forensics_verdict')}"
            )
        if len(rows) > 120:
            lines.append(f"- …and {len(rows) - 120} further row(s)")
        return "\n".join(lines)

    async def _review_compliance_answer(
        self,
        user_message: str,
        analysis: dict[str, Any],
        pack_facts: dict[str, Any] | None,
        code_findings: list[str],
        rows: list[dict[str, Any]] | None = None,
        tool_results: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """Second opinion before the answer ships. Returns None if unavailable.

        ``tool_results`` is every call the author made, verbatim. The reviewer used to see only
        pack facts and register rows, so an answer built from get_compliance_coverage was judged
        with no data at all: it named one building "the worst" when four were tied, and the
        reviewer could not know. Code does not pick which results matter; it hands over all of
        them and the reviewer reads.

        A reviewer rather than more rules, because the questions are open-ended: the failure
        we caught was a dropped requirement and an invented total, but the next one will have
        a shape no gate anticipated. Code checks what it can prove and hands the rest to a
        reader whose only job is to disbelieve the answer.

        It is given the register index as well as the pack. Told to check grounding while
        holding only the pack counts, it reported real vendor names and real expiry dates as
        unsupported — a reviewer that cannot see the evidence rejects the evidence, and its
        corrections then damage a correct answer.
        """
        api_key = (getattr(settings, "anthropic_api_key", "") or "").strip()
        if not api_key:
            return None
        model = (getattr(settings, "compliance_summary_model", "") or "claude-opus-5").strip()
        # Everything the reader will see, including the panels code attaches after the model
        # has finished. The missing-duty panel was invisible here, so a statutory-list
        # question shipped with 22 "nothing on record" rows and 44 buttons beneath it and the
        # reviewer reported nothing to correct — it had not been shown the thing to correct.
        missing = [
            o
            for o in (analysis.get("offers") or [])
            if isinstance(o, dict) and o.get("kind") in {"attach_certificate", "log_outstanding"}
        ]
        panels: list[dict[str, Any]] = []
        if missing:
            types = sorted({str(o.get("certificate_type_code")) for o in missing})
            scopes = sorted({str(o.get("cert_scope")) for o in missing if o.get("cert_scope")})
            panels.append(
                {
                    "id": self._PANEL_MISSING_DUTIES,
                    "title": "Required, nothing on record",
                    "shows": (
                        f"{len(types)} required {'/'.join(scopes) or 'pack'} types the "
                        "portfolio holds no certificate for, each with Attach and "
                        "Log-outstanding buttons. It reports what is MISSING from the "
                        "register. Judge whether the question asked for that."
                    ),
                }
            )
        answer_text = json.dumps(
            {
                "narrative": analysis.get("narrative"),
                "kpis": analysis.get("kpis"),
                "groups": analysis.get("groups"),
                "actions": analysis.get("actions"),
                "insights": analysis.get("insights"),
                "panels_attached_after_writing": panels,
            },
            default=str,
            ensure_ascii=False,
        )[:60000]
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            # A narrative-only answer (one fact, one paragraph) gives the reviewer little to
            # weigh, so it reads at low effort; anything with tiles, groups or a table gets
            # the configured effort. Which zones exist is a fact of the answer, not a judgement.
            zones = [
                k for k in ("kpis", "groups", "certificates", "actions", "insights")
                if analysis.get(k)
            ]
            review_effort = (
                "low" if not zones
                else (getattr(settings, "compliance_review_effort", "") or "medium").strip()
            )
            _t0 = time.perf_counter()
            message = await client.messages.create(
                model=model,
                max_tokens=4000,
                output_config={
                    "effort": review_effort,
                    "format": {"type": "json_schema", "schema": self._REVIEW_SCHEMA},
                },
                system=self._cached_system(self._REVIEWER_PROMPT),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"QUESTION:\n{user_message[:1000]}\n\n"
                            f"COMPUTED PACK FACTS (authoritative):\n"
                            f"{json.dumps(pack_facts or {}, default=str, ensure_ascii=False)[:20000]}\n\n"
                            f"REGISTER INDEX — every row the answer may cite. A name, date "
                            f"or score that appears here IS grounded:\n"
                            f"{self._register_index(rows or [])}\n\n"
                            f"TOOL RESULTS THE AUTHOR FETCHED (verbatim; the answer must agree "
                            f"with these, including any tie they show):\n"
                            f"{self._tool_results_digest(tool_results)}\n\n"
                            f"CONTRADICTIONS ALREADY FOUND BY CODE:\n"
                            + ("\n".join(f"- {f}" for f in code_findings) or "- none")
                            + f"\n\nANSWER UNDER REVIEW:\n{answer_text}"
                        ),
                    }
                ],
            )
        except Exception as exc:  # noqa: BLE001 — review must never break the turn
            log.warning("compliance.review.failed", model=model, error=str(exc)[:300])
            return None
        llm_cost.record(
            "reviewer",
            model,
            llm_cost.usage_from_anthropic(message.usage),
            (time.perf_counter() - _t0) * 1000,
            effort=review_effort,
        )
        raw = next(
            (b.text for b in (message.content or []) if getattr(b, "type", "") == "text"),
            "",
        )
        try:
            return json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("compliance.review.unparsable", error=str(exc)[:200])
            return None

    @staticmethod
    def _revision_brief(
        code_findings: list[str], review: dict[str, Any] | None, pack_facts: dict[str, Any] | None
    ) -> str:
        """What the analyst is told to fix. Specific defects, not "try again"."""
        lines: list[str] = [
            "YOUR PREVIOUS ANSWER WAS REVIEWED AND MUST BE CORRECTED BEFORE IT IS SHOWN.",
            "Keep everything that was right — the structure, the ranking, the judgement. Fix "
            "only what is listed, and do not drop anything to make the list shorter.",
        ]
        for f in code_findings:
            lines.append(f"- {f}")
        for f in (review or {}).get("findings") or []:
            if isinstance(f, dict) and f.get("detail"):
                lines.append(f"- [{f.get('severity', 'error')}] {str(f['detail'])[:400]}")
        missing = [str(c) for c in ((review or {}).get("missing_type_codes") or [])][:60]
        if missing:
            lines.append(
                "- These required pack types are missing from the answer and must appear, "
                "each named with what it is: " + ", ".join(missing) + ". Take each one's name "
                "and scope from the pack list you fetched. Every type appears exactly once, under "
                "its own scope. Do not repeat a type, move one across scopes, or invent a name to "
                "reach a total: if your list and the total disagree, the list is what is wrong."
            )
        if pack_facts:
            lines.append(
                "- Use these totals verbatim: "
                + json.dumps(
                    {
                        s: {
                            "required_types": (v or {}).get("required_types"),
                            "types_with_nothing_on_record": (v or {}).get(
                                "types_with_nothing_on_record"
                            ),
                        }
                        for s, v in (pack_facts.get("by_scope") or {}).items()
                    },
                    ensure_ascii=False,
                )
            )
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def _sub_question_brief(
        sub_questions: list[dict[str, Any]] | None, user_message: str
    ) -> str:
        """The section ids the analyst must use, one per line."""
        subs = sub_questions or [{"id": "q1", "text": user_message}]
        return "\n".join(
            f"- {sq.get('id') or 'q1'}: {str(sq.get('text') or user_message)[:300]}"
            for sq in subs
        )

    async def _claude_analyse_compliance(
        self,
        user_message: str,
        data_json: str,
        on_zone: Any = None,
        sub_questions: list[dict[str, Any]] | None = None,
        query_notes: str = "",
        taxonomy: bool = False,
        role: str = "analyst",
    ) -> dict[str, Any] | None:
        """Reasoning pass: returns the typed compliance response object, or None.

        When `on_zone` is given it is awaited with (key, value) as each top-level zone
        finishes streaming, so the UI can paint the narrative and KPIs first.
        """
        api_key = (getattr(settings, "anthropic_api_key", "") or "").strip()
        if not api_key:
            return None
        model = (
            getattr(settings, "compliance_summary_model", "") or "claude-opus-5"
        ).strip()
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            # The blank line belongs to the join, not to either document: taxonomy.md is
            # appended to answering.md only when the question asks what the law requires.
            effort = (getattr(settings, "compliance_analyst_effort", "") or "medium").strip()
            _t0 = time.perf_counter()
            system_text = "\n\n---\n\n".join(
                [self._ANALYST_CONTEXT_DOCS, self._ANALYST_PROMPT]
                + ([self._TAXONOMY_DIRECTIVE] if taxonomy else [])
            )
            async with client.messages.stream(
                model=model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={
                    "effort": effort,
                    "format": {
                        "type": "json_schema",
                        "schema": self._COMPLIANCE_RESPONSE_SCHEMA,
                    },
                },
                system=self._cached_system(system_text),
                messages=[
                    {
                        "role": "user",
                        "content": (
                            f"QUESTION:\n{user_message}\n\n"
                            f"PARTS OF THE QUESTION (use these ids verbatim):\n"
                            f"{self._sub_question_brief(sub_questions, user_message)}\n\n"
                            f"{query_notes}"
                            f"CERTIFICATE REGISTER (JSON):\n{data_json}"
                        ),
                    }
                ],
            ) as stream:
                if on_zone is None:
                    message = await stream.get_final_message()
                else:
                    # Progressive: emit each zone the moment it closes, rather than waiting
                    # for the whole object.
                    scanner = _ZoneStreamer(set(self._COMPLIANCE_RESPONSE_SCHEMA["properties"]))
                    async for event in stream:
                        delta = getattr(getattr(event, "delta", None), "text", None)
                        if not delta:
                            continue
                        for key, value, _final in scanner.feed(delta):
                            await on_zone(key, value)
                    message = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "compliance.analyst.failed", model=model, error=str(exc)[:300]
            )
            return None

        llm_cost.record(
            role,
            model,
            llm_cost.usage_from_anthropic(message.usage),
            (time.perf_counter() - _t0) * 1000,
            effort=effort,
            taxonomy=taxonomy,
        )
        raw = next(
            (b.text for b in (message.content or []) if getattr(b, "type", "") == "text"),
            "",
        )
        try:
            payload = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.warning("compliance.analyst.unparsable", error=str(exc)[:200])
            return None

        log.info(
            "compliance.stage3.analyst",
            model=model,
            input_tokens=getattr(message.usage, "input_tokens", None),
            output_tokens=getattr(message.usage, "output_tokens", None),
            kpis=len(payload.get("kpis") or []),
            actions=len(payload.get("actions") or []),
            insights=len(payload.get("insights") or []),
            certificates=len(payload.get("certificates") or []),
            sections=len(payload.get("sections") or []),
            narrative_chars=len(payload.get("narrative") or ""),
        )
        return payload

    async def _claude_summarize(self, system_text: str, human_text: str) -> str | None:
        """Run the compliance summary on Claude. Returns None if unavailable, so the caller
        can fall back to the orchestrator's default model."""
        api_key = (getattr(settings, "anthropic_api_key", "") or "").strip()
        if not api_key:
            return None
        model = (
            getattr(settings, "compliance_summary_model", "") or "claude-opus-5"
        ).strip()
        try:
            import anthropic

            client = anthropic.AsyncAnthropic(api_key=api_key)
            # Stream: the portfolio JSON can be ~90 KB, and streaming keeps a long request
            # from hitting the HTTP timeout.
            async with client.messages.stream(
                model=model,
                max_tokens=16000,
                thinking={"type": "adaptive"},
                output_config={"effort": "high"},
                system=system_text,
                messages=[{"role": "user", "content": human_text}],
            ) as stream:
                message = await stream.get_final_message()
        except Exception as exc:  # noqa: BLE001 — never let this break the turn
            log.warning(
                "orchestrator.compliance.claude_summary_failed",
                model=model,
                error=str(exc)[:300],
            )
            return None

        parts = [
            b.text for b in (message.content or []) if getattr(b, "type", "") == "text"
        ]
        out = "\n".join(p for p in parts if p).strip()
        if not out:
            return None
        # STAGE 3 LOG — the summary the model produced.
        log.info(
            "compliance.stage3.llm_summary",
            provider="anthropic",
            model=model,
            input_tokens=getattr(message.usage, "input_tokens", None),
            output_tokens=getattr(message.usage, "output_tokens", None),
            stop_reason=getattr(message, "stop_reason", None),
            answer_chars=len(out),
            # rows the model actually listed, so the log shows what the user will see
            listed_rows=out.count("- **") + out.count("\n- "),
            preview=out[:400],
        )
        if settings.compliance_debug_payloads:
            log.info("compliance.stage3.llm_answer_full", answer=out)
        return out

    async def _maybe_compliance_posture_shortcut(
        self,
        user_message: str,
        session_id: str,
        on_zone: Any = None,
    ) -> dict[str, Any] | None:
        """Answer a full compliance-posture question deterministically from the whole portfolio.

        The LLM path filters the list to a single lifecycle status (e.g. at-risk), which silently
        drops the blocked/lapsed vendors the same question asked to highlight. Here we always pull
        the summary plus the UNFILTERED vendor (and building, when in scope) portfolios, then let
        build_deterministic_compliance_answer group them into Blocked / Lapsed / At-risk sections.
        """
        if compliance_skill_path_enabled():
            # COMPLIANCE_SKILL_PATH=1 — stand down so the question reaches the orchestrator
            # and, through it, the compliance sub-agent with its own skill files. This
            # shortcut answers before the orchestrator model is ever asked, which is why six
            # of eight test questions never reached a sub-agent; the flag exists so both
            # paths can be run against the same question rather than argued about.
            log.info(
                "orchestrator.compliance.preflight_stood_down",
                session_id=session_id,
                reason="COMPLIANCE_SKILL_PATH",
            )
            return None

        msg = " ".join((user_message or "").strip().lower().split())
        # This shortcut fires on loose patterns ("which vendor …"), which also match
        # contract-performance and energy questions ("which vendor has SLA completion
        # under 25h for P2"). When the PRD intent-keyword table routes the message to
        # ANOTHER Phase 2 engine and the message carries no compliance vocabulary,
        # stand down and let engine routing answer from the right tables. (The
        # non-streaming path already gates this by engine; this aligns preflight.)
        _other_engine = resolve_phase2_engine(user_message=user_message)
        if _other_engine in ("contract_performance", "energy_intelligence") and not any(
            w in msg
            for w in (
                "complian",
                "certificat",
                "accredit",
                "lapsed",
                "expir",
                "forg",
                "authenticity",
                "not on record",
                "coverage",
            )
        ):
            return None
        is_forgery = self._is_compliance_forgery_query(msg)
        is_both_scope = self._is_compliance_both_scope_status_query(msg)
        is_data_question = self._is_compliance_data_question(msg)
        if (
            not self._is_compliance_posture_query(msg)
            and not is_forgery
            and not is_both_scope
            and not is_data_question
        ):
            return None

        from .compliance_answer import build_deterministic_compliance_answer
        from .compliance_engine_agent import (
            get_compliance_saved_space_summary,
            list_building_certificates,
            list_vendor_accreditations,
        )

        # Scope: honour an explicit vendor/building ask; otherwise cover both. Forgery questions
        # always span both levels so they can be split out.
        wants_vendor = any(
            w in msg for w in ("vendor", "accreditation", "accredited", "contractor", "supplier")
        )
        wants_building = any(
            w in msg for w in ("building", "site", "premises", "property", "landlord")
        )
        if is_forgery or (not wants_vendor and not wants_building):
            wants_vendor = wants_building = True

        tool_calls: list[dict[str, Any]] = []
        source = "db_direct"
        # Filters that had to be widened because nothing matched all of them at once.
        relaxed_notes: list[dict[str, Any]] = []

        # PLANNING STEP — decide what this question needs before any tool runs, so the answer
        # can include what is relevant rather than only what was literally asked.
        plan = await self._plan_compliance_query(user_message)

        # How the answer was produced, shown in the chat itself rather than buried in the
        # activity log: what was planned, what was actually queried, what the analyst was
        # given, and what the validation gate did. Streamed live where a socket is attached,
        # and returned on the result so the non-streaming path shows the same thing.
        pipeline: list[dict[str, Any]] = []

        async def _step(step: dict[str, Any]) -> None:
            pipeline.append(step)
            if on_zone is not None:
                await on_zone(self._STEP_ZONE, step)

        if on_zone is not None and plan.get("reason"):
            # Streamed ahead of the data so the activity log shows the intent first.
            await on_zone(self._PLAN_ZONE, str(plan["reason"]))
        await _step(
            {
                "stage": "plan",
                "label": "Planned the question",
                "detail": str(plan.get("reason") or ""),
                "parts": [
                    {
                        "id": sq.get("id"),
                        "text": sq.get("text"),
                        "scope": (sq.get("query") or {}).get("scope") or "both",
                        "filters": [
                            f"{f.get('field')} {f.get('op')} {f.get('value')}"
                            for f in ((sq.get("query") or {}).get("filters") or [])
                            if isinstance(f, dict)
                        ],
                    }
                    for sq in (plan.get("sub_questions") or [])
                ],
            }
        )

        try:
            # PRIMARY — read plenum_cafm.compliance_certificates straight from Postgres:
            # every row, every field, plus the joined vendor / site / asset / location /
            # source-document context. NOTHING is filtered here by status, scope, country or
            # forensics — the LLM decides what the question asks for and filters it itself.
            # One query per sub-question, run concurrently, then unioned by certificate id.
            # Identical specs are executed once. A sub-question with no usable filters falls
            # back to the whole register, so a weak plan degrades to previous behaviour.
            subs = plan.get("sub_questions") or [{"id": "q1", "query": plan.get("query") or {}}]

            # If documents were just ingested in this session, a follow-up question is about
            # THOSE documents. Answering it from the whole register tells the user about
            # sixteen certificates when they asked about the five they watched land. Every
            # sub-question is pinned to the ingested ids unless the question widens the scope
            # itself ("all", "every", "the register", "portfolio"), which clears the pin.
            pinned_ids = self._ingestion_scope(session_id, user_message)
            if pinned_ids:
                for sq in subs:
                    q = dict(sq.get("query") or {})
                    q["filters"] = list(q.get("filters") or []) + [
                        {"field": "id", "op": "in", "value": pinned_ids}
                    ]
                    q["logic"] = "and"
                    sq["query"] = q
                await _step(
                    {
                        "stage": "scope",
                        "label": "Answering about the documents you just uploaded",
                        "detail": (
                            f"{len(pinned_ids)} certificate(s) from this session's ingestion. "
                            "Ask about \"all certificates\" or \"the register\" to widen it."
                        ),
                    }
                )

            seen_specs: dict[str, list[str]] = {}
            unique: list[tuple[str, dict[str, Any] | None]] = []
            for sq in subs:
                spec = sq.get("query") or None
                key = json.dumps(spec, sort_keys=True, default=str)
                if key in seen_specs:
                    seen_specs[key].append(sq["id"])
                    continue
                seen_specs[key] = [sq["id"]]
                unique.append((key, spec))
            results = await asyncio.gather(
                *(self._fetch_compliance_table_direct(spec=spec) for _, spec in unique),
                return_exceptions=True,
            )
            # An empty row set has two very different causes, and they need different
            # recoveries. Either the planner wrote a value the data does not contain, or the
            # conditions are all valid and simply never co-occur — "blocked vendors whose
            # certificate is still valid" is a real question whose real answer is none.
            # Widening straight to the whole register handles the first and is wasteful for
            # the second, which is the common one: it buried two relevant rows in sixteen.
            results = list(results)
            relaxed: dict[int, dict[str, Any]] = {}
            retries: list[int] = []
            for i, res in enumerate(results):
                if isinstance(res, Exception) or res or not unique[i][1]:
                    continue
                rows_r, note = await self._relax_empty_spec(unique[i][1])
                if rows_r:
                    results[i] = rows_r
                    relaxed[i] = note
                else:
                    retries.append(i)
            if relaxed:
                log.info(
                    "compliance.stage1.relaxed_spec",
                    session_id=session_id,
                    relaxations=[
                        {"parts": seen_specs[unique[i][0]], **note}
                        for i, note in relaxed.items()
                    ],
                )
            if retries:
                # Nothing narrower survived, so the filter itself is suspect — read the whole
                # register for that part and let the analyst do the filtering, as before.
                log.info(
                    "compliance.stage1.empty_spec_fallback",
                    session_id=session_id,
                    sub_questions=[seen_specs[unique[i][0]] for i in retries],
                )
                refetched = await asyncio.gather(
                    *(self._fetch_compliance_table_direct(spec=None) for _ in retries),
                    return_exceptions=True,
                )
                for i, res in zip(retries, refetched):
                    results[i] = res
                    relaxed[i] = {
                        "dropped": "all filters",
                        "kept": [],
                        "reason": "no narrower filter matched anything",
                    }
            rows_by_id: dict[str, dict[str, Any]] = {}
            for (key, _spec), res in zip(unique, results):
                if isinstance(res, Exception):
                    raise res
                for r in res:
                    rid = str(r.get("id"))
                    if rid not in rows_by_id:
                        # Tag which sub-question(s) selected this row so the analyst can
                        # attribute it to the right section.
                        r = {**r, "matched_sub_questions": list(seen_specs[key])}
                        rows_by_id[rid] = r
                    else:
                        rows_by_id[rid]["matched_sub_questions"] = sorted(
                            set(rows_by_id[rid].get("matched_sub_questions", []))
                            | set(seen_specs[key])
                        )
            relaxed_notes = [n for n in relaxed.values() if n.get("kept")]
            rows = list(rows_by_id.values())
            fetched_rows = rows
            await _step(
                {
                    "stage": "data",
                    "label": "Read the certificate register",
                    "detail": (
                        f"{len(rows)} row{'' if len(rows) == 1 else 's'} from "
                        "plenum_cafm.compliance_certificates, joined with vendor, site, "
                        "asset, location and source document"
                    ),
                    "queries": [
                        {
                            "parts": seen_specs[key],
                            # Scope narrows the read as surely as a filter does, and it is
                            # where the planner puts "building" or "vendor". Reading only
                            # `filters` here labelled a scoped read "(whole register)" — so a
                            # query that returned 8 building rows was reported to the user as
                            # having read all 27. The trace has to describe the query that
                            # ran, not the half of it this list happened to look at.
                            "filters": (
                                (
                                    [f"cert_scope eq {(spec or {}).get('scope')}"]
                                    if str((spec or {}).get("scope") or "").lower()
                                    in {"building", "vendor"}
                                    else []
                                )
                                + [
                                    f"{f.get('field')} {f.get('op')} {f.get('value')}"
                                    for f in ((spec or {}).get("filters") or [])
                                    if isinstance(f, dict)
                                ]
                            )
                            or ["(whole register)"],
                            "matched_rows": (0 if isinstance(res, Exception) else len(res)),
                            **(
                                {
                                    "relaxed": (
                                        "no row satisfied every condition — dropped "
                                        f"{relaxed[i]['dropped']}, kept "
                                        f"{', '.join(relaxed[i]['kept']) or 'nothing'}"
                                    )
                                }
                                if i in relaxed
                                else {}
                            ),
                        }
                        for i, ((key, spec), res) in enumerate(zip(unique, results))
                    ],
                }
            )
            # Partition by scope purely so the existing frontend cards keep working; every row
            # lands in exactly one bucket, so the model still sees the complete table.
            building_rows = [
                r for r in rows if str(r.get("cert_scope") or "").lower() != "vendor"
            ]
            vendor_rows = [
                r for r in rows if str(r.get("cert_scope") or "").lower() == "vendor"
            ]
            tool_calls.append(
                {
                    "tool": "list_building_certificates",
                    "input": {"source": "db_direct"},
                    "output": {"certificates": building_rows},
                }
            )
            tool_calls.append(
                {
                    "tool": "list_vendor_accreditations",
                    "input": {"source": "db_direct"},
                    "output": {"certificates": vendor_rows},
                }
            )
            # KPI buckets for the dashboard card — an aggregate read, not a row filter.
            try:
                summary = await get_compliance_saved_space_summary.ainvoke({})
                tool_calls.append(
                    {
                        "tool": "get_compliance_saved_space_summary",
                        "input": {},
                        "output": summary,
                    }
                )
            except Exception as sexc:  # noqa: BLE001 — KPI card is optional
                log.warning(
                    "orchestrator.compliance.summary_failed", error=str(sexc)[:200]
                )
            # Whatever the plan asked for beyond the register, fetched concurrently.
            planned_extras = await self._fetch_planned_extras(plan.get("needs") or [])
            tool_calls.extend(planned_extras)
            for extra in planned_extras:
                extra_step = self._planned_extra_step(extra)
                if extra_step:
                    await _step(extra_step)
            log.info(
                "orchestrator.compliance.db_direct",
                session_id=session_id,
                rows=len(rows),
                building=len(building_rows),
                vendor=len(vendor_rows),
                planned_extras=plan.get("needs") or [],
            )
        except Exception as exc:  # noqa: BLE001 — fall back to the HTTP tools
            log.warning(
                "orchestrator.compliance.db_direct_failed",
                session_id=session_id,
                error=str(exc)[:300],
            )
            source = "engine_tools"
            fetched_rows = []
            tool_calls = []
            try:
                summary = await get_compliance_saved_space_summary.ainvoke({})
                tool_calls.append(
                    {
                        "tool": "get_compliance_saved_space_summary",
                        "input": {},
                        "output": summary,
                    }
                )
                vendors = await list_vendor_accreditations.ainvoke({"limit": 200})
                tool_calls.append(
                    {
                        "tool": "list_vendor_accreditations",
                        "input": {"limit": 200},
                        "output": vendors,
                    }
                )
                buildings = await list_building_certificates.ainvoke({"limit": 200})
                tool_calls.append(
                    {
                        "tool": "list_building_certificates",
                        "input": {"limit": 200},
                        "output": buildings,
                    }
                )
            except Exception as texc:  # noqa: BLE001
                log.warning(
                    "orchestrator.compliance.posture_shortcut_failed",
                    session_id=session_id,
                    error=str(texc),
                )
                return None

        # Reasoning pass first: the analyst returns a TYPED object (narrative + kpis + actions
        # + insights + certificates), which the frontend renders as components. The narrative
        # doubles as the plain-text answer so every existing consumer still works.
        # A relaxed filter has to be declared to the analyst, or it reads the widened rows as
        # satisfying the whole question and answers "here they are" where the honest answer is
        # "none — and here is why".
        query_notes = ""
        for n in relaxed_notes:
            query_notes += (
                "IMPORTANT — THE ROWS ARE WIDER THAN THE QUESTION: no row satisfied every "
                f"condition, so {n['dropped']} was dropped and only "
                f"{', '.join(n['kept'])} was applied. The answer to the question as asked is "
                "NONE. Say that plainly first, then use these rows as the evidence for why.\n"
            )
        if query_notes:
            query_notes += "\n"

        answer_source = "analyst"
        answer: str | None = None
        await _step(
            {
                "stage": "analyse",
                "label": "Compliance analyst reasoning",
                "detail": (
                    f"{len(fetched_rows)} row(s) handed to "
                    f"{(getattr(settings, 'compliance_summary_model', '') or 'claude-opus-5')}"
                    " to rank by operational urgency and write the answer"
                    if fetched_rows
                    else "Answering from the compliance engine tools"
                ),
            }
        )
        is_taxonomy = bool(plan.get("taxonomy"))
        # Counted here, once, and handed to the analyst as fact — and kept for the gate, which
        # needs the same numbers to check the answer against.
        pack_facts = self._pack_facts(tool_calls, fetched_rows)
        if pack_facts:
            tool_calls.append(
                {"tool": "pack_facts", "input": {}, "output": pack_facts}
            )
        analysis = await self._claude_analyse_compliance(
            user_message,
            self._compliance_data_json(tool_calls, taxonomy=is_taxonomy),
            on_zone=on_zone,
            sub_questions=plan.get("sub_questions"),
            query_notes=query_notes,
            taxonomy=is_taxonomy,
        )
        if analysis and (analysis.get("narrative") or "").strip():
            # EL gate — the orchestrator re-checks the sub-agent's answer against the rows it
            # was given before any of it reaches the user. Only meaningful on the direct-read
            # path: on the HTTP fallback we have no row set to check against, and validating
            # against an empty set would strip a perfectly good answer.
            if fetched_rows:
                analysis, issues = self._validate_compliance_response(
                    analysis, fetched_rows, plan.get("sub_questions"), taxonomy=is_taxonomy
                )
                log.info(
                    "compliance.stage4.validation",
                    session_id=session_id,
                    rows_fetched=len(fetched_rows),
                    certificates_kept=len(analysis.get("certificates") or []),
                    issues=issues[:8],
                    passed=not issues,
                )
                # What the PM can do about these rows, decided from row state after the gate
                # has settled which rows the answer legitimately names.
                analysis["offers"] = self._certificate_offers(
                    analysis, fetched_rows
                ) + self._missing_type_offers(
                    tool_calls, pack_facts, self._asked_scopes(plan)
                )
                await _step(
                    {
                        "stage": "validate",
                        "label": "Checked the answer against the rows",
                        "detail": (
                            f"{len({str(c.get('id')) for c in (analysis.get('certificates') or []) if isinstance(c, dict)})}"
                            f" of {len(fetched_rows)} row(s) cited; "
                            + (
                                "nothing ungrounded"
                                if not issues
                                else f"{len(issues)} correction(s) applied"
                            )
                            + (
                                f"; {len(analysis['offers'])} action(s) available"
                                if analysis.get("offers")
                                else ""
                            )
                        ),
                        "issues": issues[:8],
                    }
                )
            else:
                log.info(
                    "compliance.stage4.validation",
                    session_id=session_id,
                    skipped="no direct row set (HTTP fallback path)",
                )

            # Stage 5 — review. The EL gate proves each cited row exists; it cannot tell that
            # a required certificate type was never mentioned, or that a total was invented.
            # Those need a reader. One revision round only: a reviewer that can keep asking
            # becomes the author.
            # Scope discipline is not a taxonomy concern — every answer can carry
            # material nobody asked for, and the question that exposed this was not
            # classified as taxonomy at all.
            if pack_facts or is_taxonomy:
                code_findings = self._taxonomy_findings(analysis, pack_facts)
                review = await self._review_compliance_answer(
                    user_message, analysis, pack_facts, code_findings, fetched_rows
                )
                # The model decides; code looks the decision up. Matching on the words the
                # model happened to use would put the judgement back in Python — an id it
                # picked from a list we supplied is a lookup, and an id we do not recognise
                # is a routing miss to log, not a verdict to interpret.
                dropped_panels = {
                    str(x).strip() for x in ((review or {}).get("drop_panels") or []) if str(x).strip()
                }
                unknown = dropped_panels - self._PANEL_IDS
                if unknown:
                    log.warning(
                        "compliance.eval.unknown_panel_id",
                        session_id=session_id,
                        named=sorted(unknown),
                        known=sorted(self._PANEL_IDS),
                    )
                if self._PANEL_MISSING_DUTIES in dropped_panels:
                    kept = [
                        o
                        for o in (analysis.get("offers") or [])
                        if not (
                            isinstance(o, dict)
                            and o.get("kind") in {"attach_certificate", "log_outstanding"}
                        )
                    ]
                    log.info(
                        "compliance.eval.panel_dropped",
                        session_id=session_id,
                        panel="required_nothing_on_record",
                        removed=len(analysis.get("offers") or []) - len(kept),
                        reason=str((review or {}).get("reason") or "")[:160],
                    )
                    analysis["offers"] = kept
                verdict = str((review or {}).get("verdict") or "")
                needs_revision = bool(code_findings) or verdict == "revise"
                log.info(
                    "compliance.stage5.review",
                    session_id=session_id,
                    verdict=verdict or ("unavailable" if review is None else "?"),
                    code_findings=code_findings[:6],
                    review_findings=len((review or {}).get("findings") or []),
                    findings=[
                        str(f.get("detail"))[:220]
                        for f in ((review or {}).get("findings") or [])
                        if isinstance(f, dict)
                    ][:5],
                    missing_types=len((review or {}).get("missing_type_codes") or []),
                    revising=needs_revision,
                )
                await _step(
                    {
                        "stage": "review",
                        "label": "Reviewed the answer before showing it",
                        "detail": (
                            "Review unavailable; answer shown as written"
                            if review is None and not code_findings
                            else (
                                "Nothing to correct"
                                if not needs_revision
                                else f"{len(code_findings) + len((review or {}).get('findings') or [])}"
                                " defect(s) found — sending back for one correction pass"
                            )
                        ),
                        "issues": (
                            code_findings
                            + [
                                str(f.get("detail"))[:200]
                                for f in ((review or {}).get("findings") or [])
                                if isinstance(f, dict)
                            ]
                        )[:8],
                    }
                )
                if needs_revision:
                    revised = await self._claude_analyse_compliance(
                        user_message,
                        self._compliance_data_json(tool_calls, taxonomy=True),
                        on_zone=on_zone,
                        sub_questions=plan.get("sub_questions"),
                        query_notes=self._revision_brief(code_findings, review, pack_facts)
                        + query_notes,
                        taxonomy=True,
                    )
                    if revised and (revised.get("narrative") or "").strip():
                        if fetched_rows:
                            revised, _ = self._validate_compliance_response(
                                revised,
                                fetched_rows,
                                plan.get("sub_questions"),
                                taxonomy=True,
                            )
                            # Rebuilding offers here re-attached the panel the eval agent
                            # had just ordered dropped — the trace said "Applied the
                            # corrections" while the correction was silently undone. A
                            # decision the model made about THIS question holds across
                            # the rewrite of THIS answer.
                            revised["offers"] = self._certificate_offers(revised, fetched_rows)
                            if self._PANEL_MISSING_DUTIES not in dropped_panels:
                                revised["offers"] += self._missing_type_offers(
                                    tool_calls, pack_facts, self._asked_scopes(plan)
                                )
                        remaining = self._taxonomy_findings(revised, pack_facts)
                        log.info(
                            "compliance.stage5.revised",
                            session_id=session_id,
                            remaining=remaining[:6],
                            clean=not remaining,
                        )
                        analysis = revised
                        await _step(
                            {
                                "stage": "review",
                                "label": "Applied the corrections",
                                "detail": (
                                    "The corrected answer passes every numeric check"
                                    if not remaining
                                    else f"{len(remaining)} numeric issue(s) still "
                                    "disagree with the pack — shown below the answer"
                                ),
                                "issues": remaining[:8],
                            }
                        )
                        analysis.setdefault("validation", {})["review"] = {
                            "code_findings": code_findings,
                            "reviewer": (review or {}).get("reason"),
                            "remaining": remaining,
                        }

            answer = self._compliance_plain_text(analysis) or str(
                analysis["narrative"]
            ).strip()
            tool_calls.append(
                {
                    "tool": "compliance_response",
                    "input": {"question": user_message[:300]},
                    "output": analysis,
                }
            )
        if not answer:
            answer_source = "llm_summary"
            answer = await self._llm_summarize_compliance(user_message, tool_calls)
        if not answer:
            answer_source = "deterministic"
            answer = build_deterministic_compliance_answer(user_message, tool_calls)
        if not answer:
            return None

        if pipeline:
            tool_calls.append(
                {
                    "tool": "compliance_pipeline",
                    "input": {"question": user_message[:300]},
                    "output": {"steps": pipeline},
                }
            )
        log.info(
            "orchestrator.compliance.posture_shortcut",
            session_id=session_id,
            scope_vendor=wants_vendor,
            scope_building=wants_building,
            data_source=source,
            answer_source=answer_source,
            tools=[tc["tool"] for tc in tool_calls],
        )
        return {
            "session_id": session_id,
            "answer": answer,
            "tool_calls": tool_calls,
            "success": True,
            "error": None,
            "interrupted": False,
            "interrupt_payload": None,
            # Surfaced as the first streamed event so the user sees intent before data lands.
            "plan_reason": plan.get("reason"),
            # How the answer was produced, for the chat to show alongside it. The streaming
            # path has already sent these one at a time; this is what the REST path uses.
            "pipeline": pipeline,
        }

    async def _maybe_short_reply_wo_action(
        self,
        user_message: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        """
        Fast-path short confirmations in same session without forcing LLM reasoning.

        - "yes"/"proceed" before WO exists -> confirm_intelligent_work_order_creation
        - "yes" after WO exists (or approval phrases) -> request_approval_chain
        """
        from .wo_engine_agent import (
            _LAST_WORK_ORDER_ID as _GLOBAL_WO_ID,
            _PENDING_APPROVAL_CONFIRM as _GLOBAL_PENDING_APPROVAL,
            _SESSION_CREATE_ARGS_MAP,
            _SESSION_WORK_ORDER_MAP,
        )

        from .wo_engine_agent import extract_work_order_id_from_text

        msg = " ".join((user_message or "").strip().lower().split())
        approval_followup = self._is_approval_followup_message(msg)
        if not self._is_affirmative_message(msg) and not approval_followup:
            return None

        wo_id = (
            _SESSION_WORK_ORDER_MAP.get(session_id)
            or _GLOBAL_WO_ID
            or extract_work_order_id_from_text(user_message)
        )
        has_draft = session_id in _SESSION_CREATE_ARGS_MAP
        awaiting_approval = _GLOBAL_PENDING_APPROVAL == session_id

        if has_draft:
            out = await confirm_intelligent_work_order_creation.ainvoke({"session_id": session_id})
            if isinstance(out, dict) and not out.get("error"):
                answer = out.get("reply") or out.get("message") or "Work order created successfully."
                return {
                    "session_id": session_id,
                    "answer": answer,
                    "tool_calls": [
                        {
                            "tool": "confirm_intelligent_work_order_creation",
                            "input": {"session_id": session_id},
                            "output": json.dumps(out, default=str),
                        }
                    ],
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                }
            return None

        if wo_id and (
            awaiting_approval
            or self._approval_intent(msg)
            or approval_followup
        ):
            return await self._approval_shortcut_response(session_id, wo_id, user_message)

        if wo_id and self._is_affirmative_message(msg):
            return await self._approval_shortcut_response(session_id, wo_id, user_message)

        return None

    @staticmethod
    def _fiix_credential_prompt_text(setup: dict) -> str:
        missing = setup.get("missing_fields") or []
        if missing:
            # Show ONLY what's still missing (already-provided fields are accepted),
            # so the user isn't asked again for keys they just entered.
            lines = "\n".join(f"  • {m}" for m in missing)
            plural = "s" if len(missing) != 1 else ""
            return (
                f"Fiix connection — still need the following field{plural} "
                f"(other credentials accepted):\n{lines}\n\n"
                "Paste as `Field: value` — e.g. `Subdomain: plenumtechnology`."
            )
        return setup.get("required_prompt") or (
            "Please provide your Fiix credentials (Subdomain, App Key, Access Key, Secret Key)."
        )

    async def _fiix_continue_after_credentials(
        self, session_id: str, *, action: str
    ) -> dict[str, Any]:
        """Run test → fetch → schema mapping or ingestion after credentials are stored."""
        tool_calls: list[dict[str, Any]] = []

        test_out = await test_fiix_connection.ainvoke({})
        tool_calls.append(
            {"tool": "test_fiix_connection", "input": {}, "output": json.dumps(test_out, default=str)}
        )
        if isinstance(test_out, dict) and test_out.get("error"):
            clear_pending_fiix_confirm(session_id)
            return {
                "session_id": session_id,
                "answer": f"Fiix connection test failed: {test_out.get('error')}",
                "tool_calls": tool_calls,
                "success": False,
                "error": str(test_out.get("error")),
                "interrupted": False,
                "interrupt_payload": None,
            }

        fetch_out = await fetch_fiix_schema.ainvoke({})
        tool_calls.append(
            {"tool": "fetch_fiix_schema", "input": {}, "output": json.dumps(fetch_out, default=str)}
        )
        if isinstance(fetch_out, dict) and fetch_out.get("error"):
            clear_pending_fiix_confirm(session_id)
            return {
                "session_id": session_id,
                "answer": f"Live Fiix schema fetch failed: {fetch_out.get('error')}",
                "tool_calls": tool_calls,
                "success": False,
                "error": str(fetch_out.get("error")),
                "interrupted": False,
                "interrupt_payload": None,
            }

        summary = (fetch_out or {}).get("summary") or {}
        comparison = (fetch_out or {}).get("schema_comparison") or {}
        display = (fetch_out or {}).get("display_summary") or comparison.get("markdown") or ""
        table_count = int(summary.get("table_count") or 0)
        sample = summary.get("sample_tables") or []
        lines: list[str] = []
        if display:
            lines.append(display.strip())
        else:
            lines.append("Fiix connection is working.")
            lines.append(f"Live schema: **{table_count}** Fiix object(s) detected.")
            if sample:
                lines.append("Sample objects: " + ", ".join(str(t) for t in sample[:10]))
        if table_count == 0:
            clear_pending_fiix_confirm(session_id)
            lines.append(
                "No tables were returned — check credentials and Fiix tenant permissions before mapping."
            )
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": "\n".join(lines),
                    "tool_calls": tool_calls,
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_FIIX_SYNC,
                domain="fiix",
            )

        if action == "ingestion":
            map_out = await start_fiix_ingestion.ainvoke({})
            tool_calls.append(
                {
                    "tool": "start_fiix_ingestion",
                    "input": {},
                    "output": json.dumps(map_out, default=str),
                }
            )
            clear_pending_fiix_confirm(session_id)
            ing_id = (map_out or {}).get("ingestion_id") or ""
            if ing_id:
                record_fiix_ingestion_started(session_id, str(ing_id))
            lines.append(
                f"Started Fiix data sync (ingestion_id={ing_id}). "
                "Ask for status anytime to poll progress."
                if ing_id
                else f"Could not start ingestion: {(map_out or {}).get('error', map_out)}"
            )
        else:
            map_out = await start_fiix_schema_mapping.ainvoke({})
            tool_calls.append(
                {
                    "tool": "start_fiix_schema_mapping",
                    "input": {},
                    "output": json.dumps(map_out, default=str),
                }
            )
            schema_id = (map_out or {}).get("schema_mapping_id") or ""
            if schema_id:
                set_pending_schema_gate_confirm(session_id, schema_mapping_id=str(schema_id))
                clear_pending_fiix_confirm(session_id)
                intro = str(get_session_state(session_id).get("last_fiix_display_summary") or "").strip()
                lines = [intro] if intro else []
                lines.append(
                    f"Schema mapping started (`{schema_id}`).\n\n"
                    "Reply **yes** to load the current gate here in chat, "
                    "or open the **Schema Mapping** UI to review gates visually."
                )
            else:
                clear_pending_fiix_confirm(session_id)
                lines.append(f"Could not start schema mapping: {(map_out or {}).get('error', map_out)}")

        return attach_route_to_result(
            {
                "session_id": session_id,
                "answer": "\n".join(lines),
                "tool_calls": tool_calls,
                "success": True,
                "error": None,
                "interrupted": False,
                "interrupt_payload": None,
            },
            session_id,
            intent=ROUTE_FIIX_SYNC,
            domain="fiix",
        )

    @staticmethod
    def _format_schema_mapping_status_answer(status: dict[str, Any], schema_id: str) -> str:
        st = str(status.get("status") or "").lower()
        gate = str(status.get("pending_gate_type") or "").replace("_", " ")
        progress = status.get("progress_pct")
        comparison = status.get("schema_comparison") or {}
        display = status.get("display_summary") or (
            comparison.get("markdown") if isinstance(comparison, dict) else ""
        )
        lines: list[str] = []
        if display:
            lines.append(str(display).strip())
        lines.append(f"**Schema mapping** `{schema_id}` — status: **{st or 'unknown'}**")
        if progress is not None:
            lines.append(f"Progress: {progress}%")
        if st in ("complete", "failed", "error", "ddl_failed"):
            if st == "complete":
                lines.append("All gates are complete. You can start Fiix data ingestion when ready.")
            else:
                err = status.get("error_message") or status.get("ddl_error") or ""
                if err:
                    lines.append(f"Error: {err}")
            return "\n\n".join(lines)
        if st == "awaiting_review" and gate:
            lines.append(
                f"**Current gate:** {gate} — review mappings in the Schema Mapping UI "
                f"(session `{schema_id}`), or tell me what to approve and I will guide next steps."
            )
        elif st in ("running", "step_paused", "mapping"):
            lines.append(
                "The pipeline is still running. I can poll again in a moment, "
                "or you can watch progress in the Schema Mapping UI."
            )
        else:
            lines.append(
                "Say **status** anytime and I will poll again. "
                "Use the Schema Mapping UI for full gate forms."
            )
        return "\n\n".join(lines)

    def _schema_gate_followup(self, msg: str, session_state: dict[str, Any]) -> bool:
        if session_state.get("pending_schema_gate_confirm"):
            return True
        active = str(session_state.get("active_schema_mapping_id") or "").strip()
        ids = session_state.get("schema_mapping_ids") or []
        if not (active or ids):
            return False
        if self._is_affirmative_message(msg):
            return True
        return self._is_schema_gate_intent(msg)

    @staticmethod
    def _is_schema_gate_intent(msg: str) -> bool:
        return any(
            t in msg
            for t in (
                "gate",
                "schema mapping",
                "field mapping",
                "hierarchy",
                "pre-semantic",
                "pre semantic",
                "artifacts",
                "mapping status",
                "schema status",
            )
        )

    async def _maybe_schema_gate_short_reply(
        self,
        user_message: str,
        session_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle yes / proceed after Fiix schema mapping gate offer."""
        msg = " ".join((user_message or "").strip().lower().split())
        state_with_sid = {**session_state, "session_id": session_id}
        if not self._schema_gate_followup(msg, state_with_sid):
            return None
        if not (self._is_affirmative_message(msg) or self._is_schema_gate_intent(msg)):
            return None

        schema_id = resolve_active_schema_mapping_id(session_id)
        if not schema_id:
            return None

        status_out = await get_schema_mapping_status.ainvoke(
            {"schema_mapping_id": schema_id}
        )
        tool_calls = [
            {
                "tool": "get_schema_mapping_status",
                "input": {"schema_mapping_id": schema_id},
                "output": json.dumps(status_out, default=str),
            }
        ]
        if isinstance(status_out, dict) and status_out.get("error"):
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": f"Could not load schema mapping status: {status_out.get('error')}",
                    "tool_calls": tool_calls,
                    "success": False,
                    "error": str(status_out.get("error")),
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_FIIX_SYNC,
                domain="fiix",
            )

        st = str((status_out or {}).get("status") or "").lower()
        if self._is_affirmative_message(msg) and (
            st in ("awaiting_review", "step_paused")
            or session_state.get("pending_schema_gate_confirm")
        ):
            gate_out = await continue_schema_mapping_gate.ainvoke(
                {"schema_mapping_id": schema_id}
            )
            tool_calls.append(
                {
                    "tool": "continue_schema_mapping_gate",
                    "input": {"schema_mapping_id": schema_id},
                    "output": json.dumps(gate_out, default=str),
                }
            )
            if isinstance(gate_out, dict) and not gate_out.get("error"):
                answer = str(gate_out.get("message") or "")
                display = gate_out.get("display_summary") or ""
                if display and display not in answer:
                    answer = f"{display.strip()}\n\n{answer}".strip()
                answer += (
                    "\n\nUse the **Schema** tab in the right rail for the full gate UI "
                    "(same as standalone Schema Mapper)."
                )
                follow = gate_out.get("status")
                if isinstance(follow, dict) and str(follow.get("status") or "").lower() == "complete":
                    clear_pending_schema_gate_confirm(session_id)
                return attach_route_to_result(
                    {
                        "session_id": session_id,
                        "answer": answer,
                        "tool_calls": tool_calls,
                        "success": True,
                        "error": None,
                        "interrupted": False,
                        "interrupt_payload": None,
                    },
                    session_id,
                    intent=ROUTE_FIIX_SYNC,
                    domain="fiix",
                )

        if st == "complete":
            clear_pending_schema_gate_confirm(session_id)

        answer = self._format_schema_mapping_status_answer(
            status_out if isinstance(status_out, dict) else {},
            schema_id,
        )
        answer += (
            "\n\nOpen the **Schema** tab in the right rail to use the same gate forms as "
            "Schema Mapper, or reply **yes** to submit the current gate with approve-all defaults."
        )
        return attach_route_to_result(
            {
                "session_id": session_id,
                "answer": answer,
                "tool_calls": tool_calls,
                "success": True,
                "error": None,
                "interrupted": False,
                "interrupt_payload": None,
            },
            session_id,
            intent=ROUTE_FIIX_SYNC,
            domain="fiix",
            next_step_prompt="Reply yes to submit current gate, or use Schema rail panel.",
        )

    async def _maybe_fiix_short_reply(
        self,
        user_message: str,
        session_id: str,
        session_state: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Handle 'yes' / proceed for Fiix schema sync without ambiguous LLM replies."""
        msg = " ".join((user_message or "").strip().lower().split())

        if resolve_active_schema_mapping_id(session_id) and self._is_affirmative_message(msg):
            return None

        fiix_context = (
            session_state.get("last_route_intent") == ROUTE_FIIX_SYNC
            or bool(session_state.get("pending_fiix_confirm"))
            or "fiix" in msg
        )
        if not fiix_context:
            return None
        if not self._is_affirmative_message(msg):
            return None

        set_pending_fiix_confirm(
            session_id,
            action=str(session_state.get("pending_fiix_action") or "schema_mapping"),
        )
        state = get_session_state(session_id)
        action = str(state.get("pending_fiix_action") or "schema_mapping")

        if not fiix_credentials_configured(session_id):
            setup = fiix_setup_status_snapshot(session_id)
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": self._fiix_credential_prompt_text(setup),
                    "tool_calls": [
                        {
                            "tool": "get_fiix_setup_status",
                            "input": {},
                            "output": json.dumps(setup, default=str),
                        }
                    ],
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_FIIX_SYNC,
                domain="fiix",
                next_step_prompt="Provide all four Fiix credential fields, then I will test and fetch live schema.",
            )

        return await self._fiix_continue_after_credentials(session_id, action=action)

    async def _maybe_fiix_proactive_setup(
        self,
        session_id: str,
        session_state: dict[str, Any],
        route_intent: str,
    ) -> dict[str, Any] | None:
        """On Fiix intent, ask for credentials first (Schema Mapper UI parity)."""
        if route_intent != ROUTE_FIIX_SYNC:
            return None
        if fiix_credentials_configured(session_id):
            return None
        setup = fiix_setup_status_snapshot(session_id)
        if setup.get("configured"):
            return None
        return attach_route_to_result(
            {
                "session_id": session_id,
                "answer": self._fiix_credential_prompt_text(setup),
                "tool_calls": [
                    {
                        "tool": "get_fiix_setup_status",
                        "input": {},
                        "output": json.dumps(setup, default=str),
                    }
                ],
                "success": True,
                "error": None,
                "interrupted": False,
                "interrupt_payload": None,
            },
            session_id,
            intent=ROUTE_FIIX_SYNC,
            domain="fiix",
            next_step_prompt="Reply with subdomain, App Key, Access Key, and Secret Key.",
        )

    @staticmethod
    def _extract_priority_from_text(msg_l: str) -> str:
        if any(k in msg_l for k in ("critical", "asap", "immediately", "emergency")):
            return "critical"
        if any(k in msg_l for k in ("urgent", "priority high", "high priority")):
            return "urgent"
        if "low priority" in msg_l or "minor" in msg_l:
            return "low"
        if "medium" in msg_l:
            return "medium"
        return "high" if "not working" in msg_l or "broken" in msg_l else "medium"

    @staticmethod
    def _extract_location_from_text(raw: str) -> str:
        text = raw.strip()
        patterns = [
            r"\b(?:in|at)\s+([A-Za-z0-9\-\s]+?)(?:,|\.|;| and | with | is | not | please | urgent |$)",
            r"\b(?:tower|building|block|floor|room|zone)\s+([A-Za-z0-9\-\s]+?)(?:,|\.|;| and | with | is | not | please | urgent |$)",
        ]
        for p in patterns:
            m = re.search(p, text, flags=re.IGNORECASE)
            if m:
                loc = m.group(1).strip(" -")
                if loc:
                    return loc
        return "Unknown"

    @staticmethod
    def _extract_requester_from_text(raw: str) -> tuple[str, str]:
        text = raw.strip()
        m = re.search(r"\b(?:i am|this is|my name is)\s+([A-Za-z][A-Za-z\s]{1,40})", text, flags=re.IGNORECASE)
        if m:
            name = " ".join(m.group(1).split())[:50]
            local = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".")
            if local:
                return name, f"{local}@plenum-tech.com"
        return "System", "system@plenum-tech.com"

    @staticmethod
    def _extract_asset_from_text(raw: str) -> str:
        text = raw.strip()
        # Prefer explicit equipment-style codes first (AHU-12, PUMP-7, CHLR-01)
        m = re.search(r"\b([A-Z]{2,6}-\d{1,6})\b", text)
        if m:
            return m.group(1)
        # Next, look for known equipment nouns.
        nouns = ("ahu", "hvac", "chiller", "pump", "generator", "elevator", "boiler", "motor", "fan", "valve")
        low = text.lower()
        for n in nouns:
            if n in low:
                return n.upper()
        return "Unknown Asset"

    async def _prepare_from_implicit_work_request(
        self,
        *,
        session_id: str,
        original_request: str,
    ) -> dict[str, Any]:
        msg_l = " ".join((original_request or "").strip().lower().split())
        asset = self._extract_asset_from_text(original_request)
        location = self._extract_location_from_text(original_request)
        priority = self._extract_priority_from_text(msg_l)
        requester_name, requester_email = self._extract_requester_from_text(original_request)
        out = await prepare_intelligent_work_order.ainvoke(
            {
                "source": "chat",
                "asset": asset,
                "location": location,
                "issue_description": original_request,
                "priority": priority,
                "request_type": "repair",
                "requester_name": requester_name,
                "requester_email": requester_email,
                "session_id": session_id,
            }
        )
        answer = out.get("reply") if isinstance(out, dict) else None
        if not isinstance(answer, str) or not answer.strip():
            answer = "I prepared the work-order assessment. Please review and confirm to continue."
        return {
            "session_id": session_id,
            "answer": answer,
            "tool_calls": [
                {
                    "tool": "prepare_intelligent_work_order",
                    "input": {
                        "source": "chat",
                        "asset": asset,
                        "location": location,
                        "issue_description": original_request,
                        "priority": priority,
                        "request_type": "repair",
                        "requester_name": requester_name,
                        "requester_email": requester_email,
                        "session_id": session_id,
                    },
                    "output": json.dumps(out, default=str),
                }
            ],
            "success": True,
            "error": None,
            "interrupted": False,
            "interrupt_payload": None,
        }

    @classmethod
    def _compliance_zone_event(cls, key: str, value: Any) -> dict[str, Any]:
        """One streamed zone as a WebSocket event. The plan sentence rides the same channel
        so it reaches the activity log before the data it explains."""
        if key == cls._PLAN_ZONE:
            return {
                "type": "reasoning",
                "label": "Plan",
                "text": str(value),
                "domain": "compliance",
            }
        if key == cls._STEP_ZONE:
            return {"type": "compliance_step", "domain": "compliance", "step": value}
        if key == cls._EVENT_ZONE:
            return dict(value)  # already an event (tool_started / tool_completed / reasoning)
        return {
            "type": "compliance_zone",
            "zone": key,
            "domain": "compliance",
            "data": value,
        }

    async def _stream_compliance_progressive(
        self,
        shortcut: dict[str, Any],
        session_id: str,
        already_streamed: set[str] | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Replay a completed compliance turn as progressive zone events.

        The zones were produced in order while the model streamed (narrative first, then the
        KPI strip, then the action cards), so emitting them separately lets the UI paint the
        summary immediately and fill in the detail as it arrives — never a spinner for the
        whole response.
        """
        for tc in shortcut.get("tool_calls") or []:
            tool_name = tc.get("tool", "")
            if tool_name in ("compliance_response", "compliance_pipeline"):
                continue  # carried as zones / step events, not as raw tool payloads
            domain = _TOOL_DOMAIN.get(tool_name, "compliance")
            yield {
                "type": "tool_started",
                "tool": tool_name,
                "domain": domain,
                "input": tc.get("input") or {},
            }
            yield {
                "type": "tool_completed",
                "tool": tool_name,
                "domain": domain,
                "output": tc.get("output"),
            }

        analysis = next(
            (
                tc.get("output")
                for tc in (shortcut.get("tool_calls") or [])
                if tc.get("tool") == "compliance_response"
            ),
            None,
        )
        if isinstance(analysis, dict):
            for key in (
                "narrative",
                "sections",
                "groups",
                "kpis",
                "actions",
                "insights",
                "certificates",
                "expiry_events",
                "pending",
            ):
                if key in (already_streamed or set()):
                    continue  # already painted live while the model was writing
                if key in analysis:
                    yield {
                        "type": "compliance_zone",
                        "zone": key,
                        "domain": "compliance",
                        "data": analysis[key],
                    }
        log.info("orchestrator.stream.compliance_progressive", session_id=session_id)
        yield workflow_stream_completion_payload(
            session_id,
            answer=str(shortcut.get("answer") or ""),
            tool_calls=list(shortcut.get("tool_calls") or []),
        )

    async def _stream_shortcut_events(
        self, shortcut: dict[str, Any], session_id: str
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Emit WebSocket events for a deterministic short-reply tool path."""
        for tc in shortcut.get("tool_calls") or []:
            tool_name = tc.get("tool", "")
            domain = _TOOL_DOMAIN.get(tool_name, "wo_engine")
            tool_input = tc.get("input") or {}
            yield {
                "type": "tool_started",
                "tool": tool_name,
                "domain": domain,
                "input": tool_input,
            }
            yield {
                "type": "tool_completed",
                "tool": tool_name,
                "domain": domain,
                "output": tc.get("output"),
            }
        log.info("orchestrator.stream.shortcut", session_id=session_id)
        yield workflow_stream_completion_payload(
            session_id,
            answer=str(shortcut.get("answer") or ""),
            tool_calls=list(shortcut.get("tool_calls") or []),
        )

    async def _compose_structured_compliance(
        self,
        user_message: str,
        tool_calls: list[dict[str, Any]],
        session_id: str,
        agent_draft: str,
        on_zone: Any = None,
    ) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        """Write the typed compliance answer from the data the sub-agent fetched.

        The sub-agent path returned the data-gathering model's prose and stopped, so a
        coverage question came back as a paragraph naming 3 of the 26 gaps it claimed, and
        the UI had nothing to render as cards. The preflight path had a second stage — the
        analyst writes a schema-checked object, code validates it against the rows, the
        reviewer reads it — and this is that stage, fed by tool results instead of a register
        read the code chose. The agent decides what to fetch; the analyst decides what to say;
        the reviewer decides whether it answered the question. Code here only carries data.
        Returns (analysis, pipeline_steps); analysis is None when the analyst is unavailable.
        """
        steps: list[dict[str, Any]] = []

        async def _step(step: dict[str, Any]) -> None:
            steps.append(step)
            if on_zone is not None:
                await on_zone(self._STEP_ZONE, step)

        data_calls = [
            tc
            for tc in tool_calls
            if isinstance(tc, dict) and not str(tc.get("tool", "")).startswith("phase2_engine:")
        ]
        if not data_calls:
            return None, steps
        # Which documents the analyst reads follows the tools the agent chose to call — the
        # model already decided this was a pack question by fetching the pack.
        is_taxonomy = any(
            tc.get("tool") in {"list_country_pack", "get_pack_facts"} for tc in data_calls
        )
        rows: list[dict[str, Any]] = []
        for tc in data_calls:
            if tc.get("tool") in {"list_building_certificates", "list_vendor_accreditations"}:
                out_ = tc.get("output") if isinstance(tc.get("output"), dict) else {}
                rows.extend(r for r in (out_.get("certificates") or []) if isinstance(r, dict))
        pack_facts = next(
            (
                tc.get("output")
                for tc in data_calls
                if tc.get("tool") == "get_pack_facts"
                and isinstance(tc.get("output"), dict)
                and tc["output"].get("by_scope")
            ),
            None,
        ) or self._pack_facts(data_calls, rows)
        # The scopes the agent asked its tools for are the scopes the question was about.
        scope_set: set[str] = set()
        for tc in data_calls:
            inp = tc.get("input") if isinstance(tc.get("input"), dict) else {}
            sc = str(inp.get("scope") or "").lower()
            if sc in {"building", "vendor"}:
                scope_set.add(sc)
            elif sc == "both":
                scope_set.update({"building", "vendor"})
        scopes = tuple(sorted(scope_set)) or ("building", "vendor")
        notes = (
            "NOTES FROM THE DATA-GATHERING AGENT — its reading of the same data. The JSON below "
            "is the evidence; these notes are not, and where they disagree the data wins:\n"
            + str(agent_draft or "")[:3000]
            + "\n\n"
        )
        data_json = self._compliance_data_json(data_calls, taxonomy=is_taxonomy)
        analysis = await self._claude_analyse_compliance(
            user_message, data_json, on_zone=on_zone, query_notes=notes, taxonomy=is_taxonomy
        )
        if not analysis or not str(analysis.get("narrative") or "").strip():
            log.info("compliance.compose.unavailable", session_id=session_id)
            return None, steps
        await _step(
            {
                "stage": "compose",
                **self._step_cost("analyst"),
                "label": "Wrote the answer from the data the agent fetched",
                "detail": f"{len(data_calls)} tool result(s), {len(rows)} certificate row(s)"
                + (", country pack included" if is_taxonomy else ""),
            }
        )
        _tv0 = time.perf_counter()
        issues: list[str] = []
        if rows:
            analysis, issues = self._validate_compliance_response(
                analysis, rows, None, taxonomy=is_taxonomy
            )
        analysis["offers"] = self._certificate_offers(analysis, rows) + self._missing_type_offers(
            data_calls, pack_facts, scopes
        )
        await _step(
            {
                "stage": "validate",
                "ms": int((time.perf_counter() - _tv0) * 1000),
                "label": "Checked the answer against the rows",
                "detail": ("nothing ungrounded" if not issues else f"{len(issues)} correction(s) applied")
                + (f"; {len(analysis['offers'])} action(s) available" if analysis.get("offers") else ""),
                "issues": issues[:8],
            }
        )
        code_findings = self._taxonomy_findings(analysis, pack_facts) if pack_facts else []
        review = await self._review_compliance_answer(
            user_message, analysis, pack_facts, code_findings, rows, tool_results=data_calls
        )
        raw_missing = list((review or {}).get("missing_type_codes") or [])
        if review is not None:
            review["missing_type_codes"] = self._verified_missing_codes(
                review,
                json.dumps(analysis, default=str, ensure_ascii=False),
                self._pack_names_from_tool_results(data_calls),
            )
        dropped_panels = {
            str(x).strip() for x in ((review or {}).get("drop_panels") or []) if str(x).strip()
        }
        unknown = dropped_panels - self._PANEL_IDS
        if unknown:
            log.warning(
                "compliance.eval.unknown_panel_id", session_id=session_id, named=sorted(unknown)
            )
        if self._PANEL_MISSING_DUTIES in dropped_panels:
            analysis["offers"] = [
                o
                for o in (analysis.get("offers") or [])
                if not (
                    isinstance(o, dict)
                    and o.get("kind") in {"attach_certificate", "log_outstanding"}
                )
            ]
        verdict = str((review or {}).get("verdict") or "")
        needs_revision = bool(code_findings) or verdict == "revise"
        findings_text = [
            str(f.get("detail"))[:220]
            for f in ((review or {}).get("findings") or [])
            if isinstance(f, dict)
        ]
        log.info(
            "compliance.stage5.review",
            session_id=session_id,
            path="sub_agent_structured",
            verdict=verdict or ("unavailable" if review is None else "?"),
            code_findings=code_findings[:6],
            review_findings=len(findings_text),
            findings=findings_text[:5],
            missing_types_named_by_reviewer=len(raw_missing),
            missing_types=len((review or {}).get("missing_type_codes") or []),
            revising=needs_revision,
        )
        await _step(
            {
                "stage": "review",
                "label": "Reviewed the answer before showing it",
                **self._step_cost("reviewer"),
                "detail": (
                    "Review unavailable; answer shown as written"
                    if review is None and not code_findings
                    else "Nothing to correct"
                    if not needs_revision
                    else f"{len(code_findings) + len(findings_text)} defect(s) found — "
                    "sending back for one correction pass"
                ),
                "issues": (code_findings + findings_text)[:8],
            }
        )
        if needs_revision:
            if on_zone is not None:
                # The draft's zones were painted live. Clear every one before the revision
                # streams, so nothing from the draft can outlive it on the page whatever the
                # client does with zone keys the revision happens not to re-emit.
                for _zone in self._ZONE_KEYS:
                    await on_zone(_zone, "" if _zone == "narrative" else [])
            revised = await self._claude_analyse_compliance(
                user_message,
                data_json,
                on_zone=on_zone,
                query_notes=self._revision_brief(code_findings, review, pack_facts) + notes,
                taxonomy=is_taxonomy,
                role="analyst_revision",
            )
            if revised and str(revised.get("narrative") or "").strip():
                if rows:
                    revised, _ = self._validate_compliance_response(
                        revised, rows, None, taxonomy=is_taxonomy
                    )
                revised["offers"] = self._certificate_offers(revised, rows)
                if self._PANEL_MISSING_DUTIES not in dropped_panels:
                    revised["offers"] += self._missing_type_offers(data_calls, pack_facts, scopes)
                remaining = self._taxonomy_findings(revised, pack_facts) if pack_facts else []
                log.info(
                    "compliance.stage5.revised",
                    session_id=session_id,
                    path="sub_agent_structured",
                    remaining=remaining[:6],
                    clean=not remaining,
                )
                analysis = revised
                await _step(
                    {
                        "stage": "review",
                        "label": "Applied the corrections",
                        **self._step_cost("analyst_revision"),
                        "detail": (
                            "The corrected answer passes every numeric check"
                            if not remaining
                            else f"{len(remaining)} numeric issue(s) still disagree with the pack"
                        ),
                        "issues": remaining[:8],
                    }
                )
                analysis.setdefault("validation", {})["review"] = {
                    "code_findings": code_findings,
                    "reviewer": (review or {}).get("reason"),
                    "remaining": remaining,
                }
        return analysis, steps

    async def _invoke_phase2_engine(
        self,
        *,
        engine: Phase2AgentId,
        user_message: str,
        session_id: str,
        extra_context: str | None,
        on_zone: Any = None,
    ) -> dict[str, Any]:
        """
        Run ONLY the selected Phase 2 engine's tools (not the full ALL_TOOLS set).

        ``on_zone(key, value)`` — when given, the turn is streamed: the agent's tool calls
        arrive as events while it works, the composer's pipeline steps as they happen, and the
        answer's zones as the analyst closes them.
        Used when single-door extraction or intent keywords identify A/B/C.
        """
        set_session_context(session_id)
        if llm_cost.current() is None:
            llm_cost.begin_turn(session_id)
        engine_tools = PHASE2_ENGINE_TOOLS.get(engine) or []
        tool_names = [
            getattr(t, "name", getattr(t, "__name__", str(t))) for t in engine_tools
        ]
        prompt_parts = [
            f"You are the Plenum CAFM Phase 2 `{engine}` engine agent.",
            f"You may ONLY use these tools: {', '.join(tool_names)}.",
            "Do not invent data. Prefer tools that confirm/list/decide over re-extracting "
            "when the single-door pipeline already extracted content.",
            # Nothing about presentation goes here. The old sentence "Always include proactive
            # next steps ... not counts alone" put a "Next Steps: renew" paragraph under a
            # one-line answer and told the agent to prefer the LIST tool where the skill docs
            # say to prefer get_compliance_coverage. How an answer is shaped, which tool a
            # question earns and what to quote are the skill documents' to say — and they do.
        ]
        if extra_context and extra_context.strip():
            prompt_parts.append("# Extraction / runtime context\n" + extra_context.strip())
        prompt_parts.append("# User request\n" + (user_message or "").strip())
        prompt = "\n\n".join(prompt_parts)

        log.info(
            "orchestrator.phase2_engine.start",
            session_id=session_id,
            engine=engine,
            tool_count=len(engine_tools),
            tools=tool_names,
        )
        inner_tool_calls: list[dict[str, Any]] = []

        async def _live(event: dict[str, Any]) -> None:
            if on_zone is not None:
                await on_zone(self._EVENT_ZONE, event)

        if on_zone is not None:
            await _live(
                {
                    "type": "reasoning",
                    "label": f"{engine} agent",
                    "text": "Reading the question, choosing tools and fetching the data.",
                    "domain": engine,
                }
            )
        try:
            answer, inner_tool_calls = await run_phase2_engine_verbose(
                engine, prompt, on_event=_live if on_zone is not None else None
            )
        except Exception as exc:
            err = friendly_openai_error(exc)
            log.error(
                "orchestrator.phase2_engine.error",
                session_id=session_id,
                engine=engine,
                error=err,
                exc_info=True,
            )
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": "",
                    "tool_calls": [],
                    "success": False,
                    "error": err,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent="phase2_engine",
                domain=engine,
            )

        # Compliance: the sub-agent answers; the template is a FALLBACK, not an override.
        #
        # This branch used to run build_deterministic_compliance_answer first and, whenever it
        # produced anything at all, throw the sub-agent's answer away and ship the template
        # instead. The template picks its text by keyword — `"building" in msg` at
        # compliance_answer.py:837, fourteen such branches in all — so every building question
        # that returned lapsed rows got the same output no matter what was asked. The agent
        # read the question, reasoned about it, and its answer was discarded before anyone
        # saw it. Two different questions could not produce two different answers, because
        # the answer was never written from the question.
        #
        # The template still earns its place in two cases, and only two: the agent produced
        # nothing, or the agent contradicted the rows it was looking at. Both are failures of
        # the answer. Preferring a template to a correct answer is not.
        if engine == "compliance":
            from .compliance_answer import (
                build_deterministic_compliance_answer,
                compliance_answer_looks_wrong,
            )

            agent_answer = str(answer or "").strip()
            reason = (
                "sub_agent_returned_nothing"
                if not agent_answer
                else (
                    "sub_agent_contradicted_its_rows"
                    if compliance_answer_looks_wrong(agent_answer, inner_tool_calls)
                    else ""
                )
            )
            if reason:
                # The agent's PROSE is suspect. That no longer decides the turn: the analyst
                # writes from the tool results, not from the prose, so it runs first and the
                # deterministic template is applied below only if the analyst produced nothing.
                log.info(
                    "orchestrator.compliance.agent_prose_suspect",
                    session_id=session_id,
                    reason=reason,
                    tools=[tc.get("tool") for tc in inner_tool_calls],
                )
            else:
                log.info(
                    "orchestrator.compliance.agent_answer_kept",
                    session_id=session_id,
                    chars=len(agent_answer),
                    tools=[tc.get("tool") for tc in inner_tool_calls],
                )

        # Stage 5 on the sub-agent path. The eval agent lived inside the preflight function,
        # so when the preflight stood down the review went with it and path C ran without its
        # last step — Q1 came back over-scoped and mislabelled with nothing to say so. The
        # review needs the same three things here it has on the other path: the question,
        # the answer, and facts computed by code. All three are available from the tool
        # calls the agent itself made.
        structured: dict[str, Any] | None = None
        if engine == "compliance":
            early_steps = self._early_pipeline_steps(inner_tool_calls)
            if on_zone is not None:
                for st in early_steps:
                    await on_zone(self._STEP_ZONE, st)
            structured, pipeline_steps = await self._compose_structured_compliance(
                user_message,
                inner_tool_calls,
                session_id,
                answer if isinstance(answer, str) else str(answer),
                on_zone=on_zone,
            )
            if structured is not None:
                answer = (
                    self._compliance_plain_text(structured)
                    or str(structured.get("narrative") or "").strip()
                    or answer
                )
                inner_tool_calls = [
                    *inner_tool_calls,
                    {
                        "tool": "compliance_response",
                        "input": {"question": user_message[:300]},
                        "output": structured,
                    },
                    {
                        "tool": "compliance_pipeline",
                        "input": {"question": user_message[:300]},
                        "output": {
                            "steps": [*early_steps, *pipeline_steps],
                            "cost": (
                                llm_cost.current().log_summary(user_message)
                                if llm_cost.current()
                                else None
                            ),
                        },
                    },
                ]
                log.info(
                    "orchestrator.compliance.structured_answer",
                    session_id=session_id,
                    sections=[str(s_.get("id")) for s_ in (structured.get("sections") or []) if isinstance(s_, dict)],
                    kpis=len(structured.get("kpis") or []),
                    groups=len(structured.get("groups") or []),
                    certificates=len(structured.get("certificates") or []),
                    offers=len(structured.get("offers") or []),
                )
        # Last resort: the analyst produced nothing AND the agent's prose was suspect. Only now
        # does the deterministic template replace the answer.
        if engine == "compliance" and structured is None and reason:
            fallback = build_deterministic_compliance_answer(user_message, inner_tool_calls)
            if fallback:
                answer = fallback
            log.warning(
                "orchestrator.compliance.fell_back_to_template",
                session_id=session_id,
                reason=reason,
                rebuilt=bool(fallback),
                tools=[tc.get("tool") for tc in inner_tool_calls],
            )
        # Prose review — only when the analyst was unavailable and the agent's own text ships.
        if engine == "compliance" and not reason and structured is None:
            review_facts = next(
                (
                    tc.get("output")
                    for tc in inner_tool_calls
                    if tc.get("tool") == "get_pack_facts"
                    and isinstance(tc.get("output"), dict)
                    and tc["output"].get("by_scope")
                ),
                None,
            )
            review_rows: list[dict[str, Any]] = []
            for tc in inner_tool_calls:
                if tc.get("tool") in {"list_building_certificates", "list_vendor_accreditations"}:
                    out_ = tc.get("output") if isinstance(tc.get("output"), dict) else {}
                    review_rows.extend(
                        r for r in (out_.get("certificates") or []) if isinstance(r, dict)
                    )
            review = await self._review_compliance_answer(
                user_message,
                {"narrative": answer},
                review_facts,
                [],
                review_rows,
                tool_results=inner_tool_calls,
            )
            verdict = str((review or {}).get("verdict") or "")
            raw_missing = list((review or {}).get("missing_type_codes") or [])
            if review is not None:
                review["missing_type_codes"] = self._verified_missing_codes(
                    review, answer if isinstance(answer, str) else str(answer),
                    self._pack_names_from_tool_results(inner_tool_calls),
                )
            log.info(
                "compliance.stage5.review",
                session_id=session_id,
                path="sub_agent",
                verdict=verdict or ("unavailable" if review is None else "?"),
                review_findings=len((review or {}).get("findings") or []),
                findings=[
                    str(f.get("detail"))[:220]
                    for f in ((review or {}).get("findings") or [])
                    if isinstance(f, dict)
                ][:5],
                missing_types_named_by_reviewer=len(raw_missing),
                missing_types=len((review or {}).get("missing_type_codes") or []),
                revising=verdict == "revise",
            )
            if verdict == "revise":
                # One correction round, same rule as the other path: a reviewer that can
                # keep asking becomes the author.
                brief = self._revision_brief([], review, review_facts)
                try:
                    revised, more_calls = await run_phase2_engine_verbose(
                        engine, prompt + (chr(10) * 2) + brief
                    )
                except Exception as rexc:  # noqa: BLE001 — a failed rewrite keeps the original
                    log.warning("compliance.stage5.revise_failed", error=str(rexc)[:200])
                    revised, more_calls = "", []
                if str(revised or "").strip():
                    answer = revised
                    inner_tool_calls = [*inner_tool_calls, *more_calls]
                    log.info(
                        "compliance.stage5.revised",
                        session_id=session_id,
                        path="sub_agent",
                        chars=len(revised),
                    )

        # Parse synthetic tool record so FE route_metadata shows the engine
        out = {
            "session_id": session_id,
            "answer": answer if isinstance(answer, str) else str(answer),
            # Real inner tool calls first (so the UI can read e.g. the compliance summary output
            # and render dashboard cards), then the synthetic wrapper for route metadata/domain.
            "tool_calls": [
                *inner_tool_calls,
                {
                    "tool": f"phase2_engine:{engine}",
                    "input": {
                        "engine": engine,
                        "tools": tool_names,
                        "message_len": len(user_message or ""),
                    },
                    "output": {"bound_tools": tool_names, "tool_count": len(tool_names)},
                },
            ],
            "success": True,
            "error": None,
            "interrupted": False,
            "interrupt_payload": None,
        }
        log.info(
            "orchestrator.phase2_engine.done",
            session_id=session_id,
            engine=engine,
            tool_count=len(tool_names),
        )
        return attach_route_to_result(
            out,
            session_id,
            intent="phase2_engine",
            domain=engine,
            tool=f"phase2_engine:{engine}",
            next_step_prompt=f"Continue with {engine} Saved Space / Approvals as needed.",
        )

    async def _invoke(
        self,
        input_: Any,
        thread_id: str,
        session_id: str,
    ) -> dict[str, Any]:
        """Invoke the agent and normalise the result into our response shape."""
        config = self._config(thread_id)
        set_session_context(thread_id)
        try:
            result = await self._agent.ainvoke(input_, config)
        except Exception as exc:
            err = friendly_openai_error(exc)
            log.error("orchestrator.invoke.error", thread_id=thread_id, error=err, exc_info=True)
            return {
                "session_id": session_id,
                "answer": "",
                "tool_calls": [],
                "success": False,
                "error": err,
                "interrupted": False,
                "interrupt_payload": None,
            }

        messages = result.get("messages", [])
        interrupt_payload = _extract_interrupt(result)

        log.info(
            "orchestrator.invoke.done",
            session_id=session_id,
            interrupted=interrupt_payload is not None,
            tool_call_count=len(_extract_tool_calls(messages)),
        )

        tool_calls = _extract_tool_calls(messages)
        answer = _extract_answer(messages)
        if has_udr_tool_calls(tool_calls) and interrupt_payload is None:
            answer, _ = await evaluate_udr_response(
                user_message=_latest_user_message(input_),
                answer=answer,
                tool_calls=tool_calls,
                llm=self._llm,
            )
        out = {
            "session_id": session_id,
            "answer": answer,
            "tool_calls": tool_calls,
            "success": True,
            "error": None,
            "interrupted": interrupt_payload is not None,
            "interrupt_payload": interrupt_payload,
        }
        domain = "meta"
        tool_name = ""
        if tool_calls:
            tool_name = str(tool_calls[-1].get("tool") or "")
            domain = _TOOL_DOMAIN.get(tool_name, "meta")
        return attach_route_to_result(
            out,
            session_id,
            domain=domain,
            tool=tool_name,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(
        self,
        user_message: str,
        session_id: str | None = None,
        extra_context: str | None = None,
    ) -> dict[str, Any]:
        """
        Run the orchestrator on a single user request (stateless).

        Each call gets a unique thread_id so state never bleeds between
        unrelated requests, even when a checkpointer is configured.

        Returns a dict with keys:
            session_id, answer, tool_calls, success, error,
            interrupted, interrupt_payload
        """
        sid = session_id or str(uuid.uuid4())
        thread_id = str(uuid.uuid4())   # fresh thread — stateless
        system_prompt = build_system_prompt(extra_context)
        messages = [
            SystemMessage(content=system_prompt),
            HumanMessage(content=user_message),
        ]
        log.info("orchestrator.run.start", session_id=sid, message_len=len(user_message))
        return await self._invoke({"messages": messages}, thread_id, sid)

    async def run_stateful(
        self,
        user_message: str,
        session_id: str,
        extra_context: str | None = None,
        preferred_engine: Phase2AgentId | None = None,
        detected_engines: list[str] | None = None,
        pipeline_tool_calls: list[dict[str, Any]] | None = None,
        filenames: list[str] | None = None,
    ) -> dict[str, Any]:
        """
        Run the orchestrator with a persistent thread (HITL-capable).

        Uses session_id as the LangGraph thread_id so the graph state
        is saved to the Postgres checkpointer after each step.  If a
        tool calls interrupt(), this returns with interrupted=True and
        the interrupt_payload for human review.

        When preferred_engine / extracted-content resolution selects a
        Phase 2 engine (compliance | contract_performance | energy_intelligence),
        only that engine's tools are invoked — not the full ALL_TOOLS set.

        Call resume() with the same session_id to continue.
        """
        set_session_context(session_id)
        record_conversation_turn(session_id, "user", user_message)
        session_state = get_session_state(session_id)
        msg_l = " ".join((user_message or "").strip().lower().split())
        route_intent = resolve_route_intent(msg_l, session_state, extra_context)

        # Content-based Phase 2 engine selection (Feature A/B/C).
        # Skip when the turn is clearly UDR/Fiix/WO — those stay on the core agent.
        core_routes = {
            ROUTE_UDR_INGEST,
            ROUTE_UDR_MAP,
            ROUTE_FIIX_SYNC,
            ROUTE_WO_INTAKE,
            ROUTE_WO_CLARIFY,
        }
        engine = preferred_engine
        if engine is None and route_intent not in core_routes:
            engine = as_phase2_engine(
                (await select_agent(user_message, extra_context)).get("agent")
            )
            # Keyword routing missed (paraphrase / typo / natural phrasing). Fall back to an LLM
            # intent classifier so the orchestrator comprehends meaning, not just exact keywords.
            if engine is None:
                engine = await self._llm_classify_engine(user_message)
        if engine is not None and route_intent not in core_routes:
            # Any compliance data question is answered from the WHOLE portfolio: the shortcut
            # fetches every building certificate + vendor accreditation (all columns) and lets the
            # LLM summarise them for the specific question. The plain phase2 engine tends to filter
            # the list to a single status/level and drop the rest; the shortcut never does.
            if engine == "compliance" and (
                self._is_compliance_posture_query(msg_l)
                or self._is_compliance_forgery_query(msg_l)
                or self._is_compliance_both_scope_status_query(msg_l)
                or self._is_compliance_data_question(msg_l)
            ):
                posture = await self._maybe_compliance_posture_shortcut(
                    user_message, session_id
                )
                if posture is not None:
                    return attach_route_to_result(
                        posture,
                        session_id,
                        intent="phase2_engine",
                        domain="compliance",
                    )
            return await self._invoke_phase2_engine(
                engine=engine,
                user_message=user_message,
                session_id=session_id,
                extra_context=extra_context,
            )

        wo_clarification_confirmed = False
        asks_udr_run = route_intent == ROUTE_UDR_MAP or (
            route_intent == ROUTE_UDR_INGEST and "run" in msg_l
        )
        if asks_udr_run and not workspace_has_ingestion(session_state):
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": (
                        "No data is ingested yet for this workspace. Upload CSV/Excel/PDF/Word/image "
                        "files, or connect Fiix (collect subdomain + API keys, then start_fiix_schema_mapping "
                        "or start_fiix_ingestion), then I can run UDR mapping and hierarchy."
                    ),
                    "tool_calls": [],
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_UDR_INGEST,
                domain="udr",
                next_step_prompt="Upload files or complete Fiix schema mapping / sync, then ask to run mapping and hierarchy.",
            )

        if asks_udr_run and workspace_has_ingestion(session_state):
            unstructured_only = session_state.get("ingestion_mode") == "unstructured"
            if (
                not unstructured_only
                and session_state.get("mapping_status") != "complete"
            ):
                return attach_route_to_result(
                    {
                        "session_id": session_id,
                        "answer": (
                            "Files are ingested. Next, run schema mapping on your structured data "
                            "(migration flow), then hierarchy review. Say: run mapping and hierarchy "
                            "or upload CSV/Excel if you have not migrated yet."
                        ),
                        "tool_calls": [],
                        "success": True,
                        "error": None,
                        "interrupted": False,
                        "interrupt_payload": None,
                    },
                    session_id,
                    intent=ROUTE_UDR_MAP,
                    domain="migration",
                    next_step_prompt="Complete field mapping and hierarchy gates via migration tools.",
                )
        if bool(session_state.get("pending_wo_clarification")):
            if self._is_affirmative_message(msg_l):
                original = str(session_state.get("pending_wo_text") or user_message).strip()
                session_state["pending_wo_clarification"] = False
                session_state["pending_wo_text"] = ""
                return await self._prepare_from_implicit_work_request(
                    session_id=session_id,
                    original_request=original,
                )
            elif self._is_negative_message(msg_l):
                session_state["pending_wo_clarification"] = False
                session_state["pending_wo_text"] = ""
                return attach_route_to_result(
                    {
                        "session_id": session_id,
                        "answer": (
                            "Understood. I will not create a work order from that request. "
                            "Tell me what you want to do next."
                        ),
                        "tool_calls": [],
                        "success": True,
                        "error": None,
                        "interrupted": False,
                        "interrupt_payload": None,
                    },
                    session_id,
                    intent=ROUTE_GENERAL,
                    domain="meta",
                )

        wo_band = work_request_confidence_band(msg_l)
        if (not wo_clarification_confirmed) and wo_band in ("high", "medium"):
            session_state["pending_wo_clarification"] = True
            session_state["pending_wo_text"] = user_message
            prompt = (
                "This looks like a work request (high confidence). "
                "Should I plan and create a work order?"
                if wo_band == "high"
                else "This may be a work request. Should I create and plan a work order from your message?"
            )
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": prompt,
                    "tool_calls": [],
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_WO_CLARIFY,
                domain="wo_engine",
                next_step_prompt="Reply yes to proceed with prepare_intelligent_work_order, or no to cancel.",
            )
        if (not wo_clarification_confirmed) and wo_band == "low" and self._looks_like_work_request_without_wo_keyword(msg_l):
            return attach_route_to_result(
                {
                    "session_id": session_id,
                    "answer": (
                        "I am not sure if this is a work request, a document question, or a data query. "
                        "Please clarify: create a work order, search documents, or query the register?"
                    ),
                    "tool_calls": [],
                    "success": True,
                    "error": None,
                    "interrupted": False,
                    "interrupt_payload": None,
                },
                session_id,
                intent=ROUTE_GENERAL,
                domain="meta",
            )

        shortcut = await self._stateful_preflight_shortcut(
            user_message, session_id, session_state, route_intent, msg_l
        )
        if shortcut is not None:
            log.info("orchestrator.run_stateful.shortcut", session_id=session_id)
            if not shortcut.get("route_metadata"):
                return attach_route_to_result(shortcut, session_id)
            return shortcut

        input_ = await self._build_stateful_input(session_id, user_message, extra_context)
        log.info("orchestrator.run_stateful.start", session_id=session_id)
        return await self._invoke(input_, session_id, session_id)

    @staticmethod
    def _looks_like_work_request_without_wo_keyword(msg_l: str) -> bool:
        if "work order" in msg_l or "wo-" in msg_l:
            return False
        issue_terms = (
            "leak",
            "broken",
            "not working",
            "malfunction",
            "repair",
            "urgent",
            "hvac",
            "chiller",
            "pump",
            "generator",
            "elevator",
            "inspection finding",
            "alarm",
            "fault",
            "failure",
            "trip",
            "down",
            "overheat",
            "smell",
            "water",
            "temperature",
            "pressure",
        )
        intent_verbs = (
            "fix",
            "repair",
            "replace",
            "check",
            "inspect",
            "service",
            "resolve",
            "attend",
        )
        infra_terms = (
            "ahu",
            "hvac",
            "chiller",
            "pump",
            "generator",
            "elevator",
            "ac",
            "air handling",
            "boiler",
            "motor",
            "fan",
            "valve",
        )
        has_issue = any(t in msg_l for t in issue_terms)
        has_intent = any(v in msg_l for v in intent_verbs)
        has_asset_hint = any(a in msg_l for a in infra_terms)
        return has_issue or (has_intent and has_asset_hint)

    async def resume(
        self,
        session_id: str,
        decision: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Resume an interrupted stateful workflow with a human decision.

        Args:
            session_id: The session_id used in the original run_stateful() call.
            decision:   The human's answer to the interrupt payload.
                        For mapping_approval:   {"approved": bool, "corrections": dict}
                        For rollback_confirmation: {"confirmed": bool}

        Returns the same response shape as run_stateful().
        """
        if not self._has_hitl:
            return {
                "session_id": session_id,
                "answer": "",
                "tool_calls": [],
                "success": False,
                "error": "HITL is not enabled — no checkpointer configured",
                "interrupted": False,
                "interrupt_payload": None,
            }

        log.info("orchestrator.resume", session_id=session_id, decision_keys=list(decision.keys()))
        return await self._invoke(Command(resume=decision), session_id, session_id)

    async def stream(
        self,
        user_message: str,
        session_id: str | None = None,
        extra_context: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Stream workflow events as typed dicts for WebSocket delivery.

        Yields event dicts with `type` in:
          tool_started       — a tool call is beginning
          tool_completed     — a tool call finished
          agent_switch       — the active domain changed between consecutive tools
          gate_interrupt     — a HITL interrupt() fired (map_fields or rollback)
          workflow_completed — the agent returned its final answer
          error              — an unexpected exception occurred

        Uses session_id as LangGraph thread_id (same as run_stateful) so checkpoint
        history accumulates. Session conversation_turns backstop when HITL is off.
        """
        sid = session_id or str(uuid.uuid4())
        thread_id = sid
        config = self._config(thread_id)
        set_session_context(sid)
        record_conversation_turn(sid, "user", user_message)

        session_state = get_session_state(sid)
        msg_l = " ".join((user_message or "").strip().lower().split())
        route_intent = resolve_route_intent(msg_l, session_state, extra_context)

        # Chain-of-thought step: surface the orchestrator's intent classification so the
        # activity log's Section 2 shows the reasoning, not just the tool calls (CoA).
        _route_reason = {
            ROUTE_UDR_MAP: ("udr", "Classified as UDR mapping — run schema/field mapping over the ingested data."),
            ROUTE_UDR_INGEST: ("udr", "Classified as UDR ingestion — register and prepare source data."),
            ROUTE_WO_INTAKE: ("wo_engine", "Classified as work-order intake — create/triage a maintenance work order."),
            ROUTE_WO_CLARIFY: ("wo_engine", "Implicit maintenance request — confirm intent before creating a work order."),
            ROUTE_FIIX_SYNC: ("fiix", "Classified as Fiix sync — connect/map the live Fiix CMMS."),
            ROUTE_GENERAL: ("meta", "General query — decompose and route to the best-fit tools."),
        }
        _r_domain, _r_text = _route_reason.get(route_intent, ("meta", "Decompose the request and route to the best-fit tools."))
        yield {
            "type": "reasoning",
            "label": "Query understanding",
            "text": _r_text,
            "domain": _r_domain,
        }

        # Run the preflight shortcuts as a task so a compliance analysis can paint zone by
        # zone WHILE the model writes, rather than all at once when it finishes. Non-compliance
        # shortcuts never push a zone, so they simply run to completion as before.
        zone_queue: asyncio.Queue[tuple[str, Any]] = asyncio.Queue()
        streamed_zones: set[str] = set()

        async def _push_zone(key: str, value: Any) -> None:
            await zone_queue.put((key, value))

        preflight = asyncio.create_task(
            self._stateful_preflight_shortcut(
                user_message, sid, session_state, route_intent, msg_l, on_zone=_push_zone
            )
        )
        while True:
            pending_get = asyncio.create_task(zone_queue.get())
            done, _ = await asyncio.wait(
                {preflight, pending_get}, return_when=asyncio.FIRST_COMPLETED
            )
            if pending_get in done:
                zone_key, zone_value = pending_get.result()
                streamed_zones.add(zone_key)
                yield self._compliance_zone_event(zone_key, zone_value)
                continue
            pending_get.cancel()
            break
        shortcut = await preflight
        while not zone_queue.empty():  # anything that closed as the model finished
            zone_key, zone_value = zone_queue.get_nowait()
            streamed_zones.add(zone_key)
            yield self._compliance_zone_event(zone_key, zone_value)
        if shortcut is not None:
            if not shortcut.get("route_metadata"):
                shortcut = attach_route_to_result(shortcut, sid)
            # A compliance turn carries a typed analysis — stream it zone by zone so the
            # narrative and KPI strip paint before the action cards are ready.
            has_analysis = any(
                tc.get("tool") == "compliance_response"
                for tc in (shortcut.get("tool_calls") or [])
            )
            if has_analysis:
                if shortcut.get("plan_reason") and self._PLAN_ZONE not in streamed_zones:
                    yield {
                        "type": "reasoning",
                        "label": "Plan",
                        "text": str(shortcut["plan_reason"]),
                        "domain": "compliance",
                    }
                async for ev in self._stream_compliance_progressive(
                    shortcut, sid, streamed_zones
                ):
                    yield ev
                return
            async for ev in self._stream_shortcut_events(shortcut, sid):
                yield ev
            return

        # Phase 2 engine routing — mirror run_stateful so the STREAMING UI path also honours the
        # intent-keyword table (compliance / contract_performance / energy_intelligence). Without
        # this the streaming agent is handed ALL tools and can mis-pick a wrong-domain tool
        # (e.g. the WO dashboard for a "compliance status" query, then wrongly ask for Fiix).
        phase2_core_routes = {
            ROUTE_UDR_INGEST,
            ROUTE_UDR_MAP,
            ROUTE_FIIX_SYNC,
            ROUTE_WO_INTAKE,
            ROUTE_WO_CLARIFY,
        }
        if route_intent not in phase2_core_routes:
            # A reading model routes first; the keyword tables are its fallback. The order
            # used to be the reverse, and "what must a contractor hold" matched both the
            # contract and compliance keyword lists at once — a tie-break sent a statutory
            # question to the contract agent while the model that could read the sentence
            # was never asked.
            llm_cost.begin_turn(sid)
            routing = await select_agent(user_message, extra_context)
            phase2_engine = as_phase2_engine(routing.get("agent"))
            if phase2_engine is not None:
                yield {
                    "type": "reasoning",
                    "label": "Domain routing",
                    "text": (
                        f"{'Read the question' if routing.get('source') == 'llm' else 'Matched intent keywords'}"
                        f" → {phase2_engine} engine"
                        + (f" — {routing.get('reason')}" if routing.get("reason") else "")
                        + ". Answering with its tools only (live from plenum_cafm, not Fiix)."
                    ),
                    "domain": phase2_engine,
                }
                # Run the engine as a task and forward whatever it pushes — tool calls as the
                # agent makes them, pipeline steps, answer zones as the analyst closes them —
                # so the UI paints while the turn runs instead of after a minute of silence.
                engine_task = asyncio.create_task(
                    self._invoke_phase2_engine(
                        engine=phase2_engine,
                        user_message=user_message,
                        session_id=sid,
                        extra_context=extra_context,
                        on_zone=_push_zone,
                    )
                )
                meta_keys = {self._EVENT_ZONE, self._STEP_ZONE, self._PLAN_ZONE}
                while True:
                    pending_get = asyncio.create_task(zone_queue.get())
                    done, _ = await asyncio.wait(
                        {engine_task, pending_get}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if pending_get in done:
                        zone_key, zone_value = pending_get.result()
                        if zone_key not in meta_keys:
                            streamed_zones.add(zone_key)
                        yield self._compliance_zone_event(zone_key, zone_value)
                        continue
                    pending_get.cancel()
                    break
                phase2_result = await engine_task
                while not zone_queue.empty():
                    zone_key, zone_value = zone_queue.get_nowait()
                    if zone_key not in meta_keys:
                        streamed_zones.add(zone_key)
                    yield self._compliance_zone_event(zone_key, zone_value)
                if not phase2_result.get("route_metadata"):
                    phase2_result = attach_route_to_result(phase2_result, sid)
                # Tool events already went out live; only zones the analyst never streamed
                # (or that a revision replaced) and the completion payload remain.
                analysis = next(
                    (
                        tc.get("output")
                        for tc in (phase2_result.get("tool_calls") or [])
                        if tc.get("tool") == "compliance_response"
                    ),
                    None,
                )
                if isinstance(analysis, dict):
                    # The stream carried the analyst's raw output; the payload carries the
                    # validated version (sections normalised, ids re-tagged, unknown certs
                    # dropped). Re-emit every zone from the final so the last value the page
                    # holds for each key is the one that shipped.
                    for key in self._ZONE_KEYS:
                        yield self._compliance_zone_event(key, analysis.get(key) if key in analysis else ("" if key == "narrative" else []))
                log.info("orchestrator.stream.phase2_live", session_id=sid, engine=phase2_engine)
                yield workflow_stream_completion_payload(
                    sid,
                    answer=str(phase2_result.get("answer") or ""),
                    tool_calls=list(phase2_result.get("tool_calls") or []),
                )
                return

        input_ = await self._build_stateful_input(sid, user_message, extra_context)

        last_domain: str | None = None
        final_answer = ""
        streamed_tool_calls: list[dict[str, Any]] = []
        log.info("orchestrator.stream.start", session_id=sid, message_len=len(user_message))

        try:
            async for event in self._agent.astream_events(input_, config, version="v2"):
                kind = event["event"]

                if kind == "on_tool_start":
                    tool_name = event.get("name", "")
                    domain = _TOOL_DOMAIN.get(tool_name, "unknown")
                    tool_input = event.get("data", {}).get("input", {})

                    if last_domain is not None and domain != last_domain:
                        yield {
                            "type": "agent_switch",
                            "from_domain": last_domain,
                            "to_domain": domain,
                        }
                    last_domain = domain

                    yield {
                        "type": "tool_started",
                        "tool": tool_name,
                        "domain": domain,
                        "input": tool_input,
                    }

                elif kind == "on_tool_end":
                    tool_name = event.get("name", "")
                    domain = _TOOL_DOMAIN.get(tool_name, "unknown")
                    raw_out = event.get("data", {}).get("output")
                    output = getattr(raw_out, "content", raw_out)
                    if domain == "wo_engine":
                        from .wo_engine_agent import capture_work_order_from_tool_output

                        capture_work_order_from_tool_output(sid, output)

                    streamed_tool_calls.append(
                        {
                            "tool": tool_name,
                            "input": event.get("data", {}).get("input", {}),
                            "output": output,
                        }
                    )
                    yield {
                        "type": "tool_completed",
                        "tool": tool_name,
                        "domain": domain,
                        "output": output,
                    }

                elif kind == "on_custom_event":
                    # Per-node Processing-Log steps dispatched from inside long-running tools
                    # (e.g. run_migration), so Activity Log Section 2 streams node-by-node.
                    if event.get("name") == "processing_step":
                        yield {"type": "processing_step", **(event.get("data") or {})}

                elif kind == "on_chat_model_end":
                    output_msg = event.get("data", {}).get("output")
                    if output_msg and not getattr(output_msg, "tool_calls", None):
                        content = getattr(output_msg, "content", "")
                        if isinstance(content, str) and content:
                            final_answer = content

        except GraphInterrupt as gi:
            payload = gi.args[0] if gi.args else {}
            log.info("orchestrator.stream.gate_interrupt", session_id=sid)
            yield {"type": "gate_interrupt", "payload": payload, "session_id": sid}
            return

        except Exception as exc:
            err = friendly_openai_error(exc)
            log.error("orchestrator.stream.error", session_id=sid, error=err, exc_info=True)
            yield {"type": "error", "error": err, "session_id": sid}
            return

        if has_udr_tool_calls(streamed_tool_calls):
            final_answer, _ = await evaluate_udr_response(
                user_message=user_message,
                answer=final_answer,
                tool_calls=streamed_tool_calls,
                llm=self._llm,
            )
        log.info("orchestrator.stream.done", session_id=sid)
        if final_answer.strip():
            record_conversation_turn(sid, "assistant", final_answer)
        yield workflow_stream_completion_payload(
            sid,
            answer=final_answer,
            tool_calls=streamed_tool_calls,
        )

    async def get_thread_state(self, session_id: str) -> dict[str, Any] | None:
        """
        Return the saved LangGraph state for a thread, or None if not found.
        Used by the /status endpoint to check whether a session is interrupted.
        """
        if not self._has_hitl:
            return None
        config = self._config(session_id)
        try:
            snapshot = await self._agent.aget_state(config)
            if snapshot is None:
                return None
            interrupts = snapshot.tasks  # pending interrupt tasks
            return {
                "session_id": session_id,
                "interrupted": bool(interrupts),
                "interrupt_payload": interrupts[0].interrupts[0].value if interrupts else None,
            }
        except Exception as exc:
            log.warning("orchestrator.get_thread_state.error", session_id=session_id, error=str(exc))
            return None
