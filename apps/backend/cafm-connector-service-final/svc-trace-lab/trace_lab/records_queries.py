"""Which records queries a live question's turn gets (18 Nov 2026, Hussain and Bala).

For a live question the expected answer's values are read by running a records query. The query comes, in this order,
from:

    1. Hoistra's own - the plan its records tool (answer_from_records) wrote, found in the turn's trace, one per call;
    2. Trace Lab's fixed query - an answer-key shape that matches the question (answer_key.match);
    3. a proposal - Hoistra's own planner (ontology_qa.plan_question, its prompt, its map, its retries) asked for one,
       only when the turn called a data tool other than the records tool, and only when the caller asks for it;
    4. none, with the reason: not about records, no map of the records yet, or the planner could not be reached.

The nodes are the tree view's, as eval_store.nodes_of returns them. A records call keeps its LAST plan (the planner
retries until its plan validates), and a call that found nothing or failed is dropped when a later call follows it:
that call was a retry. Nothing here reads the records or changes anything in the orchestrator; a proposal is a model
call and is always labelled as one (SOURCE_WORDS).
"""
from __future__ import annotations

import json
import math
from typing import Any

from src.agents import ontology_qa as oq

from . import answer_key as ak
from . import records_run

#: Hoistra's records tool (svc-deepagents ontology_qa.answer_from_records).
RECORDS_TOOL = "answer_from_records"
#: A data tool READS the company's records or an engine's computed data (compliance, vendor scores and scorecards,
#: contract performance, energy, maintenance and decisions, inspections, documents / RAG lookups, work-order reads). Only
#: a turn that called one can be about records, so only such a turn may get a proposed query. Everything below is NOT a
#: data tool: a tool is listed by name, with why. A tool Hoistra adds later counts as a data tool until it is listed here;
#: test_every_tool_hoistra_registers_is_classified fails when one appears, so someone decides.
NOT_DATA_TOOLS = frozenset({
    # The records tool itself, the sub-agent hand-off and the final answer (final_answer is a stage, not a tool, today).
    RECORDS_TOOL, "task", "final_answer",
    # Meta: the orchestrator's own planning, skill choice, session memory, scratch files and context housekeeping.
    "write_todos", "select_skill", "memory_set", "memory_get", "write_file", "read_file", "compact_context",
    # Work orders, writes and actions: create, change, close, approve, trigger, email, and the drafting that precedes a
    # create (the AI assessment and the approval-chain preview change or prepare a change; none reads data for an answer).
    "create_work_order", "update_work_order", "transition_work_order", "approve_work_order", "close_work_order",
    "create_intelligent_work_order", "confirm_intelligent_work_order_creation", "prepare_intelligent_work_order",
    "trigger_ppm_work_order", "process_email_work_order", "suggest_approval_chain", "request_approval_chain",
    "customize_approval_chain", "respond_to_approval_step", "send_approval_request_email",
    # UDR writes: create, update and delete a record.
    "udr_create_record", "udr_update_record", "udr_delete_record",
    # UDR / index catalogues: they describe the database's structure and the row index; no record is read.
    "get_schema", "udr_list_tables", "udr_describe_table", "find_tables", "table_card", "list_row_index_tables",
    "list_doc_rag_db_tables",
    # Documents: index, remove or batch-index a document; read raw text from an upload; match a document to rows for the
    # schema-mapping tab. They act on a document, they do not look up what the company's documents say.
    "index_document", "index_documents_batch", "delete_document", "extract_text", "match_document_to_rows",
    "manage_vector_membership",
    # Compliance writes and actions: record a filing, upsert a certificate, set a remedial status, seed the country pack,
    # draft a renewal email, flag a building change, decide a queue item, share a vendor passport, point to an evidence
    # pack download; run_document_forensics checks an uploaded certificate (a check, like verify_*, reads no record).
    "record_regulatory_filing", "upsert_compliance_certificate", "set_remedial_status", "seed_uk_country_pack",
    "draft_certificate_renewal", "flag_building_change", "decide_compliance_approval", "share_vendor_passport",
    "generate_compliance_evidence_pack", "run_document_forensics",
    # Contract performance writes and actions: ingest, extract-and-draft, update or confirm contract parameters; propose
    # or approve asset criticality; change the score weights; decide an invoice line or a queue item;
    # extract_and_verify_invoice verifies an invoice (a check, like verify_vendor_invoice).
    "ingest_contract_parameters", "extract_contract_from_document", "update_contract_parameters",
    "confirm_contract_parameters", "propose_asset_criticality", "propose_asset_criticality_from_udr",
    "approve_asset_criticality", "update_score_weights", "extract_and_verify_invoice", "decide_invoice_line",
    "decide_contract_approval",
    # Energy writes and actions: register a meter, pull / ingest readings, degree days, BMS trends and chiller samples,
    # retry gaps, set a building's profile, log occupancy, record a chiller design, act on an anomaly, decide a queue item;
    # the condition deduced and written from inspection vectors and the remediation-queue cross-reference store a result.
    "upsert_energy_meter", "pull_smart_meter_readings", "ingest_meter_readings", "process_meter_gap_retries",
    "upsert_building_energy_profile", "log_site_occupancy_change", "record_chiller_design", "ingest_chiller_readings",
    "ingest_degree_days", "ingest_bms_trends", "act_on_energy_anomaly", "decide_energy_approval",
    "deduce_condition_from_inspection_vectors", "cross_ref_condition_consumption",
    # Platform operations, not the company's records: data migration (start, run, the gates, status, mappings, list),
    # the Fiix connection and sync (credentials, tests, schema, mapping, ingestion and their status), bulk ingest batches,
    # source connectors and the workspace ingestion summary.
    "start_migration", "start_migration_multi", "run_migration", "submit_pre_semantic", "submit_field_mapping",
    "submit_hierarchy", "submit_classification_approval", "submit_column_mapping_approval", "submit_final_gate",
    "submit_pk_approval", "submit_unique_table_approval", "get_migration_status", "get_migration_mappings",
    "list_migrations", "get_fiix_setup_status", "configure_fiix_credentials", "test_fiix_connection", "fetch_fiix_schema",
    "start_fiix_schema_mapping", "get_schema_mapping_status", "continue_schema_mapping_gate", "start_fiix_ingestion",
    "get_fiix_ingestion_status", "list_fiix_ingestion_jobs", "get_ingest_batch_status", "list_session_ingest_batches",
    "list_available_source_connectors", "test_source_connector_connection", "retrieve_workspace_corpus_summary",
})
#: ...and the checks (verify_vendor_invoice, verify_accreditation_now): they test a record, they do not read data.
NOT_DATA_PREFIX = "verify"

SOURCE_WORDS = {"hoistra": "Hoistra’s query", "answer_key": "Trace Lab’s fixed query",
                "proposed": "Proposed for this question - check it before accepting"}


def _nodes(nodes: Any) -> list[dict]:
    return [n for n in (nodes or []) if isinstance(n, dict)]


def _is_tool(n: dict) -> bool:
    return n.get("type") == "tool" or n.get("kind") == "tool"


def _object(v: Any) -> dict | None:
    """A recorded input or output as a dict: it is one, or a JSON string of one (the tool's message content); a value
    Hoist Traces cut ({"_truncated": ...} or text ending in "… [N more chars]") is not parsed."""
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except ValueError:
            return None
    return v if isinstance(v, dict) else None


def _question_of(call: dict) -> str | None:
    q = (_object(call.get("input")) or {}).get("question")
    return q.strip() or None if isinstance(q, str) else None


def _failed(call: dict) -> bool:
    """The call found nothing or failed: the tool says found false, ok false or an error, or its node is marked failed."""
    if call.get("ok") is False or call.get("error"):
        return True
    out = _object(call.get("output")) or {}
    return out.get("found") is False or out.get("ok") is False or bool(out.get("error"))


def _order(nodes: list[dict]):
    """The key that puts nodes in the order they happened: their seq (one number per recorded step, in the order the
    steps began) when every node has one, else their place in the list."""
    if nodes and all(isinstance(n.get("seq"), int) for n in nodes):
        return lambda n: n["seq"]
    place = {id(n): i for i, n in enumerate(nodes)}
    return lambda n: place[id(n)]


def _kids(nodes: list[dict]) -> dict[Any, list[dict]]:
    """The nodes grouped under the id of their parent."""
    kids: dict[Any, list[dict]] = {}
    for n in nodes:
        if n.get("parent_id") is not None:
            kids.setdefault(n["parent_id"], []).append(n)
    return kids


def _below(kids: dict[Any, list[dict]], root_id: Any, order) -> list[dict]:
    """Every node under `root_id`, at any depth, in the order they happened."""
    out, stack, seen = [], [root_id], set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        for c in kids.get(pid, []):
            out.append(c)
            stack.append(c.get("id"))
    return sorted(out, key=order)


def _last_plan(nodes: list[dict]) -> dict | None:
    """The plan the planner wrote last among these nodes. A node whose output the tree view filled in from a step
    below it (filled.output_from; an agent group shows its last model call's output) is a copy, not a plan."""
    for n in reversed(nodes):
        if (n.get("filled") or {}).get("output_from"):
            continue
        found = ak.extract_plans([n])
        if found:
            return found[0]
    return None


def extract_queries(nodes: list | None) -> list[dict]:
    """The records queries Hoistra's records tool made in this turn, in call order: [{"plan", "question", "tool_id"}].

    One per answer_from_records call: its last plan, the question the model passed (None when the input was cut) and the
    id of the call's node (the SQL Hoistra recorded for it hangs under that node: recorded_sql).
    The plan is a model call of the planner; Hoistra's trace usually hangs it under a "sub-agent" group rather than
    under the tool (trace.py on_model_start), so a call with no plan below it takes the plans recorded after it and
    before the next call. A call with no plan gives no query. A call that found nothing or failed is dropped when a
    later call (one that has a plan) follows it - a retry."""
    nodes = _nodes(nodes)
    order = _order(nodes)
    calls = sorted((n for n in nodes if _is_tool(n) and n.get("name") == RECORDS_TOOL), key=order)
    kids = _kids(nodes)
    own = {id(call): _below(kids, call.get("id"), order) for call in calls}
    taken = {id(c) for under in own.values() for c in under} | {id(c) for c in calls}
    nearby = [n for n in nodes if id(n) not in taken]
    made = []
    for i, call in enumerate(calls):
        plan = _last_plan(own[id(call)])
        if plan is None:
            lo = order(call)
            hi = order(calls[i + 1]) if i + 1 < len(calls) else math.inf
            plan = _last_plan([n for n in nearby if lo < order(n) < hi])
        if plan is not None:
            made.append((call, plan))
    return [{"plan": plan, "question": _question_of(call), "tool_id": call.get("id")}
            for i, (call, plan) in enumerate(made) if i == len(made) - 1 or not _failed(call)]


def data_tools(nodes: list | None) -> list[str]:
    """The data tools this turn called, each name once in call order: the tool nodes that READ the company's records or
    an engine's computed data. Not the records tool, `task`, `final_answer`, the checks (names starting with "verify"),
    and none of NOT_DATA_TOOLS (meta tools and write / action tools)."""
    out: list[str] = []
    for n in _nodes(nodes):
        name = n.get("name")
        if (_is_tool(n) and isinstance(name, str) and name not in NOT_DATA_TOOLS
                and not name.startswith(NOT_DATA_PREFIX) and name not in out):
            out.append(name)
    return out


def recorded_sql(nodes: list | None, tool_id: Any) -> list[dict]:
    """The SQL statements Hoistra's trace recorded under one records-tool call, in the order they ran:
    [{"label", "row_count"}], the shape records_values.changed_since reads as what Hoistra read then.

    trace.py on_sql writes each statement as a span below the tool whose input holds `sql` and `label` and whose output
    holds `row_count`; a statement with no label is named by the span. A statement that failed has no row count. An input
    Hoist Traces cut ({"_truncated": ...}) holds no statement and is left out."""
    nodes = _nodes(nodes)
    if tool_id is None or not nodes:
        return []
    out = []
    for n in _below(_kids(nodes), tool_id, _order(nodes)):
        i = _object(n.get("input")) or {}
        if not isinstance(i.get("sql"), str):
            continue
        label = i.get("label") or n.get("name")
        count = (_object(n.get("output")) or {}).get("row_count")
        if label:
            out.append({"label": str(label), "row_count": count})
    return out


async def queries_for(nodes: list | None, question: str, *, propose: bool) -> dict:
    """The queries a live question's turn gets: {"queries": [{"source", "plan", "question"}], "none": reason}.

    source is "hoistra", "answer_key" or "proposed" (SOURCE_WORDS says it in words); a Hoistra query also carries the
    "tool_id" of the records call it came from (recorded_sql finds that call's SQL). With no query, `none` says why:
    None when no proposal was asked for, "not_about_records" (the turn called no data tool, or the planner says there is
    no plan for it), "no_map" (Hoistra's map of the records is not built) or "planner_unavailable" (the planner or
    the map failed otherwise). The planner runs only for propose=True, only when the turn called a data tool, and
    never when Hoistra's own or the answer key's query is known."""
    got = extract_queries(nodes)
    if got:
        return {"queries": [{"source": "hoistra", "plan": q["plan"], "question": q["question"] or question,
                             "tool_id": q["tool_id"]} for q in got], "none": None}
    found = ak.match(question)
    if found:
        key, params = found
        return {"queries": [{"source": "answer_key", "plan": key.plan(params), "question": question}], "none": None}
    if not propose:
        return {"queries": [], "none": None}
    if not data_tools(nodes):
        return {"queries": [], "none": "not_about_records"}
    try:
        onto = await records_run.load_map()
    except records_run.NotReady:
        return {"queries": [], "none": "no_map"}
    except Exception:  # noqa: BLE001 - a map that cannot be loaded is not the question's fault
        return {"queries": [], "none": "planner_unavailable"}
    try:
        plan, _attempts = await oq.plan_question(onto, question)
    except oq.OntologyError:
        return {"queries": [], "none": "not_about_records"}
    except Exception:  # noqa: BLE001 - no key, no network, a model error: no proposal, and the page says so
        return {"queries": [], "none": "planner_unavailable"}
    return {"queries": [{"source": "proposed", "plan": plan, "question": question}], "none": None}
