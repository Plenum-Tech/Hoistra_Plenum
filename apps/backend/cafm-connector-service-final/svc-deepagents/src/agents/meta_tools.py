"""
Meta-capability tools for the DeepAgent orchestrator.

Implements the 4 built-in orchestration capabilities described in the system prompt:
  - write_todos   : explicit step-by-step planning before execution
  - task          : spawn a focused sub-agent scoped to one domain
  - write_file    : offload large datasets out of active context to temp storage
  - read_file     : retrieve previously written temp data
  - memory_set    : store a session-scoped key/value for recall later
  - memory_get    : retrieve a previously stored key/value

Call init_meta_tools(openai_api_key, model) once at startup (from orchestrator __init__).
Call set_session_context(session_id) before each orchestrator invocation so that temp
files and memory are namespaced per session.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from contextvars import ContextVar
from typing import Any

import structlog
from ..llm_factory import create_chat_model
from .compliance_router import compose, select_docs, selective_loading_enabled
from .skills import (
    agent_system_prompt,
    prompt_doc,
    route as route_to_skill,
    shared_skill,
)
from . import llm_cost
from langchain_core.messages import HumanMessage
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

log = structlog.get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Compliance sub-agent behavioural contract (tool routing).
# Attached as the system prompt on create_react_agent for agent="compliance".
# ──────────────────────────────────────────────────────────────────────────────

COMPLIANCE_SUBAGENT_PROMPT = prompt_doc("compliance", "tool-routing")
"""skills/compliance/tool-routing.md - which tool answers which question, and what a
status word means. Loaded between the shared query-builder discipline and the compliance
skill, where it keeps precedence on tool choice while the skill supplies the data layer."""

# ──────────────────────────────────────────────────────────────────────────────
# Session context — set by orchestrator before each ainvoke call so that temp
# files and memory entries are automatically namespaced per session.
# ──────────────────────────────────────────────────────────────────────────────

_session_ctx: ContextVar[str] = ContextVar("cafm_session", default="shared")


def set_session_context(session_id: str) -> None:
    """Namespace all file and memory operations under session_id for this invocation."""
    _session_ctx.set(session_id)


def get_session_context() -> str:
    """Current orchestrator / workflow session id (ContextVar)."""
    return _session_ctx.get()


# ──────────────────────────────────────────────────────────────────────────────
# Module-level state — initialized once at startup via init_meta_tools()
# ──────────────────────────────────────────────────────────────────────────────

_task_runner: "_TaskRunner | None" = None
_TEMP_DIR: str = os.path.join(tempfile.gettempdir(), "cafm_deepagent")
_MEMORY: dict[str, dict[str, str]] = {}  # {session_id: {key: value}}

os.makedirs(_TEMP_DIR, exist_ok=True)


def init_meta_tools(openai_api_key: str, model: str = "gpt-4o-mini") -> None:
    """Initialize the task runner for subagent spawning. Called once from orchestrator.__init__."""
    global _task_runner
    _task_runner = _TaskRunner(openai_api_key, model)
    log.info("meta_tools.ready", model=model)


async def run_phase2_engine(agent: str, prompt: str) -> str:
    """
    Invoke a Phase 2 domain sub-agent with ONLY that engine's tools.
    Used when extracted content / intent selects compliance | contract | energy.
    """
    if _task_runner is None:
        return json.dumps(
            {
                "error": "Task runner not initialised. Call init_meta_tools() at startup.",
            }
        )
    if agent not in (
        "compliance",
        "contract_performance",
        "energy_intelligence",
    ):
        return json.dumps(
            {
                "error": (
                    f"Not a Phase 2 engine '{agent}'. "
                    "Use compliance, contract_performance, or energy_intelligence."
                )
            }
        )
    return await _task_runner.run(agent, prompt)


def _tool_calls_from_messages(messages: list) -> list[dict]:
    """Extract [{tool, input, output}] from a LangGraph message list.

    Pairs each AIMessage.tool_calls entry with the ToolMessage carrying its output (by id),
    so the Orchestrator can forward real domain-tool results to the UI.
    """
    outputs: dict[str, object] = {}
    for msg in messages:
        is_tool = getattr(msg, "type", "") == "tool" or msg.__class__.__name__ == "ToolMessage"
        if is_tool:
            tcid = getattr(msg, "tool_call_id", None)
            if tcid is not None:
                outputs[str(tcid)] = getattr(msg, "content", None)
    calls: list[dict] = []
    for msg in messages:
        tcs = getattr(msg, "tool_calls", None)
        if not tcs:
            continue
        for tc in tcs:
            name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
            args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
            tcid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
            raw = outputs.get(str(tcid))
            output: object = raw
            if isinstance(raw, str):
                try:
                    output = json.loads(raw)
                except Exception:  # noqa: BLE001
                    output = raw
            calls.append({"tool": name, "input": args or {}, "output": output})
    return calls


async def run_phase2_engine_verbose(
    agent: str, prompt: str, on_event: Any = None
) -> tuple[str, list[dict]]:
    """Like run_phase2_engine but also returns the sub-agent's inner tool calls (for UI cards).

    ``on_event`` — awaited with a ``tool_started`` / ``tool_completed`` event dict the moment
    the agent issues or finishes a tool call, so the UI can show work in progress rather than
    a spinner until the whole turn is done."""
    if _task_runner is None:
        return (
            json.dumps({"error": "Task runner not initialised. Call init_meta_tools() at startup."}),
            [],
        )
    if agent not in ("compliance", "contract_performance", "energy_intelligence"):
        return json.dumps({"error": f"Not a Phase 2 engine '{agent}'."}), []
    return await _task_runner.run_verbose(agent, prompt, on_event=on_event)


class _TaskRunner:
    """
    Runs focused sub-agents scoped to a single domain.
    Each domain agent is a separate LangGraph ReAct graph with only that domain's tools,
    keeping the subagent focused and preventing cross-domain tool use.

    Note: Sub-agents run without a checkpointer. Migration gate decisions are handled
    naturally through conversation — run_migration surfaces gates as return values and
    submit_* tools submit decisions, so no LangGraph interrupt() is needed.
    """

    def __init__(self, openai_api_key: str, model: str) -> None:
        # Deferred imports to avoid circular imports at module load time
        from .compliance_agent import check_requirements, generate_compliance_report
        from .compliance_engine_agent import COMPLIANCE_ENGINE_TOOLS
        from .contract_performance_agent import CONTRACT_PERFORMANCE_TOOLS
        from .energy_intelligence_agent import ENERGY_INTELLIGENCE_TOOLS, list_building_documents
        from .doc_rag_agent import (
            delete_document,
            extract_text,
            get_document_metadata,
            index_document,
            query_docs,
            semantic_search,
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
        from .schema_mapper_agent import continue_schema_mapping_gate
        from .udr_agent import (
            find_asset,
            find_location,
            get_asset_documents,
            get_schema,
            lookup_user,
            query_table,
            udr_describe_table,
            udr_execute_select,
            udr_get_record,
            udr_list_tables,
            udr_read_records,
            udr_search_records,
        )
        from .udr_hybrid_tools import answer_with_graph_context, retrieve_vector_evidence
        from .wo_engine_agent import (
            approve_work_order,
            close_work_order,
            confirm_intelligent_work_order_creation,
            prepare_intelligent_work_order,
            create_intelligent_work_order,
            create_work_order,
            customize_approval_chain,
            find_ppm_schedules,
            get_approval_chain,
            get_asset_details,
            get_dashboard_stats,
            get_work_order,
            get_work_order_history,
            get_work_order_status_track,
            list_work_orders,
            process_email_work_order,
            request_approval_chain,
            send_approval_request_email,
            respond_to_approval_step,
            search_assets,
            search_locations,
            suggest_approval_chain,
            transition_work_order,
            trigger_ppm_work_order,
            update_work_order,
        )

        llm = create_chat_model(api_key=openai_api_key, model=model)

        # Kept so the compliance agent can be rebuilt per question when selective skill
        # loading is on: which documents it needs depends on what was asked, and that is
        # not known until the question arrives.
        self._llm = llm
        self._compliance_tools = [
            check_requirements,
            generate_compliance_report,
            *COMPLIANCE_ENGINE_TOOLS,
        ]
        self._agents: dict[str, Any] = {
            "migration": create_react_agent(llm, tools=[
                start_migration, run_migration,
                submit_pre_semantic, submit_field_mapping, submit_hierarchy,
                get_migration_status, get_migration_mappings, list_migrations,
                get_fiix_setup_status, configure_fiix_credentials,
                test_fiix_connection, fetch_fiix_schema, start_fiix_schema_mapping,
                get_schema_mapping_status, continue_schema_mapping_gate,
                start_fiix_ingestion, get_fiix_ingestion_status, list_fiix_ingestion_jobs,
            ], prompt=agent_system_prompt("migration")),
            "doc_rag": create_react_agent(llm, tools=[
                index_document, query_docs, semantic_search,
                extract_text, get_document_metadata, delete_document,
                # The structured answer to "which documents are linked to this building".
                # select_skill routes document questions here, and without this the only
                # tools in reach were semantic ones — so this sub-agent answered, correctly
                # and uselessly, "I can't access the building graph/linkage tools in this
                # session". Whether the question got a real answer then depended on the main
                # agent happening to call the tool itself, which it did about half the time.
                list_building_documents,
            ], prompt=agent_system_prompt("doc_rag")),
            "wo_engine": create_react_agent(llm, tools=[
                suggest_approval_chain, request_approval_chain, send_approval_request_email,
                get_approval_chain,
                customize_approval_chain, respond_to_approval_step,
                prepare_intelligent_work_order, confirm_intelligent_work_order_creation,
                create_intelligent_work_order,
                trigger_ppm_work_order, process_email_work_order,
                create_work_order, get_work_order, update_work_order, list_work_orders,
                transition_work_order, approve_work_order, close_work_order,
                get_work_order_history, get_work_order_status_track,
                search_assets, get_asset_details,
                search_locations, find_ppm_schedules, get_dashboard_stats,
            ], prompt=agent_system_prompt("wo_engine")),
            "compliance": create_react_agent(
                llm,
                tools=[
                    # Legacy heuristics + Phase 2 Compliance Engine (A1–A5).
                    # Main orchestrator ALL_TOOLS omits legacy tools to stay ≤128 OpenAI limit.
                    check_requirements,
                    generate_compliance_report,
                    *COMPLIANCE_ENGINE_TOOLS,
                ],
                prompt=agent_system_prompt("compliance", extra=COMPLIANCE_SUBAGENT_PROMPT),
            ),
            "contract_performance": create_react_agent(
                llm,
                tools=[*CONTRACT_PERFORMANCE_TOOLS],
                prompt=agent_system_prompt("contract_performance"),
            ),
            "energy_intelligence": create_react_agent(
                llm,
                tools=[*ENERGY_INTELLIGENCE_TOOLS],
                prompt=agent_system_prompt("energy_intelligence"),
            ),
            # Read-only by design: a question is answered with reads. udr_create_record /
            # udr_update_record / udr_delete_record stay on the orchestrator, where a write
            # needs an explicit instruction, not a sub-agent acting on an inferred intent.
            "udr": create_react_agent(
                llm,
                tools=[
                    get_schema, udr_list_tables, udr_describe_table,
                    find_asset, find_location, get_asset_documents,
                    query_table, udr_read_records, udr_get_record, udr_search_records,
                    udr_execute_select,
                    answer_with_graph_context, retrieve_vector_evidence,
                    lookup_user,
                ],
                prompt=agent_system_prompt("udr"),
            ),
        }

    async def run(self, agent: str, prompt: str) -> str:
        answer, _ = await self.run_verbose(agent, prompt)
        return answer

    async def run_verbose(
        self, agent: str, prompt: str, on_event: Any = None
    ) -> tuple[str, list[dict]]:
        """Run the sub-agent and return (final answer, inner tool calls with outputs).

        The inner tool calls let the Orchestrator surface real domain-tool results (e.g. the
        compliance saved-space summary) to the UI so it can render dashboard cards, instead of
        collapsing everything into an opaque `phase2_engine:*` wrapper.
        """
        runner = self._agents.get(agent)
        selection: dict[str, Any] | None = None
        if agent == "compliance" and selective_loading_enabled():
            # The contract is chosen from the question, so it cannot be built at startup.
            question = prompt.split("# User request", 1)[-1].strip() or prompt
            selection = await select_docs(question)
            contract, used = compose(selection.get("docs") or [])
            selection["loaded"] = used
            selection["chars"] = len(contract)
            if contract:
                shared = shared_skill()
                system = "\n\n---\n\n".join(
                    [p for p in ((shared.body if shared else ""), contract) if p]
                )
                runner = create_react_agent(
                    self._llm, tools=self._compliance_tools, prompt=system
                )
            log.info(
                "compliance.skills.selected",
                source=selection.get("source"),
                loaded=used,
                chars=selection.get("chars"),
                reason=str(selection.get("reason") or "")[:120],
            )
        if runner is None:
            return (
                json.dumps(
                    {
                        "error": (
                            f"Unknown agent '{agent}'. Valid values: migration, doc_rag, "
                            "wo_engine, compliance, contract_performance, energy_intelligence, udr"
                        )
                    }
                ),
                [],
            )
        try:
            _t0 = time.perf_counter()
            if on_event is None:
                result = await runner.ainvoke(
                    {"messages": [HumanMessage(content=prompt)]},
                    config={"recursion_limit": 45},
                )
            else:
                # Same graph, observed step by step: each state carries the messages so far,
                # so a new AIMessage with tool_calls is a tool starting and a new ToolMessage
                # is one finishing. The final state is what ainvoke would have returned.
                result = {}
                seen = 0
                names: dict[str, str] = {}
                async for state in runner.astream(
                    {"messages": [HumanMessage(content=prompt)]},
                    config={"recursion_limit": 45},
                    stream_mode="values",
                ):
                    result = state
                    msgs = state.get("messages", [])
                    for msg in msgs[seen:]:
                        tcs = getattr(msg, "tool_calls", None)
                        if tcs:
                            for tc in tcs:
                                name = tc.get("name") if isinstance(tc, dict) else getattr(tc, "name", None)
                                args = tc.get("args") if isinstance(tc, dict) else getattr(tc, "args", None)
                                tcid = tc.get("id") if isinstance(tc, dict) else getattr(tc, "id", None)
                                names[str(tcid)] = str(name)
                                await on_event(
                                    {"type": "tool_started", "tool": name, "domain": agent, "input": args or {}}
                                )
                        elif getattr(msg, "type", "") == "tool" or msg.__class__.__name__ == "ToolMessage":
                            raw = getattr(msg, "content", None)
                            out: object = raw
                            if isinstance(raw, str):
                                try:
                                    out = json.loads(raw)
                                except Exception:  # noqa: BLE001
                                    out = raw
                            await on_event(
                                {
                                    "type": "tool_completed",
                                    "tool": names.get(str(getattr(msg, "tool_call_id", None)), "?"),
                                    "domain": agent,
                                    "output": out,
                                }
                            )
                    seen = len(msgs)
            messages = result.get("messages", [])
            tool_calls = _tool_calls_from_messages(messages)
            _usage, _steps = llm_cost.usage_from_langchain_messages(messages)
            llm_cost.record(
                "sub_agent",
                str(getattr(self._llm, "model_name", None) or getattr(self._llm, "model", None) or "openai"),
                _usage,
                (time.perf_counter() - _t0) * 1000,
                steps=_steps,
                agent=agent,
                tools=[str(tc.get("tool")) for tc in tool_calls],
            )
            answer = ""
            for msg in reversed(messages):
                if (
                    hasattr(msg, "content")
                    and isinstance(msg.content, str)
                    and not getattr(msg, "tool_calls", None)
                ):
                    answer = msg.content
                    break
            return answer, tool_calls
        except Exception as exc:
            name = type(exc).__name__
            msg = str(exc)
            if "GraphRecursionError" in name or "recursion limit" in msg.lower():
                log.warning("meta.task.recursion_limit", agent=agent, error=msg[:300])
                return (
                    "This compliance question needed too many tool steps. "
                    "Please narrow the request (e.g. building-only, vendor-only, "
                    "or a single status like lapsed / blocked) and try again.",
                    [],
                )
            log.error("meta.task.error", agent=agent, error=str(exc))
            return json.dumps({"error": str(exc)}), []


# ──────────────────────────────────────────────────────────────────────────────
# Meta-capability tools (registered with @tool — included in ALL_TOOLS)
# ──────────────────────────────────────────────────────────────────────────────

@tool
async def write_todos(todos: list[str]) -> str:
    """Write out a step-by-step execution plan before beginning a multi-step task.

    Use before any task with 3 or more steps, or any task touching more than one
    agent domain. Writing todos makes reasoning explicit and visible in the tool
    call trace. Do NOT use for simple, single-tool lookups (Mode 1).

    Args:
        todos: Ordered list of planned steps. Each step should name the agent or
               tool to use and what result is expected.

    Returns:
        Confirmation echoing all steps, confirming the plan was logged.
    """
    log.info("meta.write_todos", step_count=len(todos))
    numbered = "\n".join(f"{i + 1}. {step}" for i, step in enumerate(todos))
    return f"Plan recorded ({len(todos)} steps):\n{numbered}"


@tool
async def task(agent: str, prompt: str) -> str:
    """Spawn a domain subagent to handle a focused piece of work independently.

    The subagent runs its own ReAct loop using only the tools for the named domain.
    Use this to delegate domain-specific work and to enable parallel execution of
    independent workstreams (fire multiple task() calls in the same turn).

    Note: Sub-agents run without a LangGraph checkpointer. Migration HITL is handled
    conversationally — run_migration returns gate payloads for user review and submit_*
    tools submit decisions, requiring no separate stateful workflow.

    Args:
        agent:  Domain to spawn. One of: migration, doc_rag, wo_engine, compliance,
                contract_performance, energy_intelligence, udr.
                Phase 2 routing (PRD intent keywords → agent):
                  compliance ← certificate, expiry, cert due, inspection, LOLER, EICR,
                    gas safety, compliance, accreditation, Gas Safe, NICEIC, lapsed,
                    renewal, statutory, fire risk, vendor cert, building cert
                  contract_performance ← SLA, contractor, performance, KPI,
                    PPM completion rate, vendor score, first fix, recall, invoice,
                    overrun, contract breach
                  energy_intelligence ← energy, meter, consumption, kWh, spike, anomaly,
                    EUI, NABERS, carbon, EPC, smart meter, utility, electricity, benchmark
        prompt: Self-contained instruction for the subagent. Include all context
                it needs — it cannot see the parent thread history.

    Returns:
        The subagent's synthesised answer as a string.
    """
    if _task_runner is None:
        return json.dumps({
            "error": "Task runner not initialised. Call init_meta_tools() at startup."
        })
    log.info("meta.task", agent=agent, prompt_len=len(prompt))
    return await _task_runner.run(agent, prompt)


@tool
async def write_file(path: str, content: str) -> str:
    """Write content to temporary storage to offload large data from active context.

    Use when a tool returns 50+ records that need to be preserved for a later step
    but should not clutter the current context. Also use when assembling a report
    section by section. Files are namespaced per session automatically.

    Args:
        path:    Relative file path (e.g. 'report/assets.json', 'tmp/step1.txt').
                 Sub-directories are created automatically.
        content: String content to write — JSON, plain text, CSV, etc.

    Returns:
        Confirmation with the byte count and path, or an error dict on failure.
    """
    session = _session_ctx.get()
    full_path = os.path.join(_TEMP_DIR, session, path)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    try:
        with open(full_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        log.info("meta.write_file", session=session, path=path, bytes=len(content))
        return f"Written {len(content)} bytes to '{path}'"
    except Exception as exc:
        log.error("meta.write_file.error", path=path, error=str(exc))
        return json.dumps({"error": str(exc)})


@tool
async def read_file(path: str) -> str:
    """Read a file previously written with write_file.

    Use to retrieve large datasets that were offloaded earlier in the workflow,
    or to read a customer-provided data file before passing it to start_migration.

    Args:
        path: Relative path used when write_file was called (e.g. 'report/assets.json').

    Returns:
        File contents as a string, or an error dict if the file is not found.
    """
    session = _session_ctx.get()
    full_path = os.path.join(_TEMP_DIR, session, path)
    try:
        with open(full_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        log.info("meta.read_file", session=session, path=path, bytes=len(content))
        return content
    except FileNotFoundError:
        return json.dumps({"error": f"File not found: '{path}'. Use write_file first."})
    except Exception as exc:
        log.error("meta.read_file.error", path=path, error=str(exc))
        return json.dumps({"error": str(exc)})


@tool
async def memory_set(key: str, value: str) -> str:
    """Store a value in session memory for recall later in the conversation.

    Use to remember user preferences, active site, last queried assets, or the
    result of an expensive query so it does not need to be repeated.

    Args:
        key:   Descriptive memory key (e.g. 'active_site', 'last_asset', 'user_role').
               Use consistent, readable names.
        value: String value to store. Serialise complex objects as JSON strings.

    Returns:
        Confirmation that the value was stored under the given key.
    """
    session = _session_ctx.get()
    if session not in _MEMORY:
        _MEMORY[session] = {}
    _MEMORY[session][key] = value
    log.info("meta.memory_set", session=session, key=key)
    return f"Stored '{key}' in session memory."


@tool
async def memory_get(key: str) -> str:
    """Retrieve a value previously stored with memory_set.

    Use to recall session context — user preferences, previously looked-up data,
    or cached query results — without making a redundant tool call.

    Args:
        key: The key used when memory_set was called.

    Returns:
        The stored value as a string, or an error dict if the key is not found.
    """
    session = _session_ctx.get()
    value = _MEMORY.get(session, {}).get(key)
    if value is None:
        return json.dumps({"error": f"Key '{key}' not found in session memory. Use memory_set first."})
    log.info("meta.memory_get", session=session, key=key)
    return value


@tool
async def select_skill(question: str) -> str:
    """Pick which domain skill answers this question, before calling task().

    Matches the user's own words against every agent's SKILL.md triggers and returns the
    agent to route to, plus any other domains the question also touches. Call this first for
    any data, reporting or status question — it is deterministic, so the same question always
    routes the same way, and it names the second domain on a cross-domain ask that would
    otherwise be answered half.

    Then: task(primary_agent, ...) — plus one task() per entry in also_relevant, fired in the
    same turn so they run in parallel — and summarise the results yourself.

    Args:
        question: The user's request, in their own words. Pass it through unedited;
                  paraphrasing it loses the terms the triggers match on.

    Returns:
        JSON with primary_agent, skill, confidence, matched_triggers, also_relevant
        (other agents worth spawning), clarify_first (ask the user instead of guessing),
        and reason.
    """
    decision = route_to_skill(question)
    log.info(
        "meta.select_skill",
        primary=decision.get("primary_agent"),
        confidence=decision.get("confidence"),
        also=[a.get("agent") for a in decision.get("also_relevant") or []],
    )
    if decision.get("clarify_first"):
        decision["next_step"] = (
            "No skill matched. If the message is a vague data/database request, ask which of "
            "the four capabilities they want (CSV/Excel migration, Word/PDF document, live "
            "Fiix, or reading CMMS data) before routing."
        )
    else:
        agents = [decision["primary_agent"]] + [
            a["agent"] for a in decision.get("also_relevant") or []
        ]
        decision["next_step"] = (
            "Call task() for: " + ", ".join(agents) + ". Fire them in the same turn when "
            "independent, then synthesise one answer — counts first, then detail, labelling "
            "which agent each figure came from."
        )
    return json.dumps(decision)
