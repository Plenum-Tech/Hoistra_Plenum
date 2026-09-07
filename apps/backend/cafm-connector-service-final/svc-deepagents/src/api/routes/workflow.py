"""
Workflow router — primary and HITL endpoints for the DeepAgent orchestrator.

Endpoints:
  POST /api/workflow/run                  — one-shot stateless request (rate limited: 20/min)
  POST /api/workflow/run-stateful         — HITL-capable request (persists state, rate limited: 20/min)
  POST /api/workflow/resume/{session_id}  — submit human decision to continue
  GET  /api/workflow/status/{session_id}  — check whether a session is interrupted
  GET  /api/workflow/tools                — list all 38 registered tools
  WS   /api/workflow/ws/{session_id}      — real-time streaming over WebSocket
"""
import json
import asyncio
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket
from pydantic import BaseModel, Field

from ...config import settings
from ...agents.single_door_flow import (
    ensure_upload_dir,
    extract_chat_citations,
    format_single_door_chat_preface,
    remove_files,
    run_single_door_ingestion_sequence,
    sanitize_filename,
)
from ...limiter import limiter
from ..deps import get_orchestrator
from ...agents import activity_log

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/workflow", tags=["Workflow"])
_WITH_FILES_CONCURRENCY = asyncio.Semaphore(8)


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class WorkflowRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=8000, description="Natural language request")
    session_id: str | None = Field(None, description="Optional session ID for grouping related calls")
    context: str | None = Field(None, description="Optional extra context appended to the system prompt")


class ToolCallRecord(BaseModel):
    tool: str
    input: dict[str, Any]
    output: Any = None


class RouteMetadata(BaseModel):
    route_intent: str = "general_query"
    selected_domain: str = "meta"
    selected_tool: str = ""
    next_step_prompt: str | None = None


class WorkspaceStatusResponse(BaseModel):
    session_id: str
    ingestion_complete: bool = False
    documents_ingested_count: int = 0
    mapping_status: str = "pending"
    hierarchy_status: str = "pending"
    mapping_pending: bool = False
    hierarchy_pending: bool = False
    wo_candidate_detected: bool = False
    pending_batch_ids: list[str] = Field(default_factory=list)
    active_batch_id: str = ""
    last_route_intent: str = "general_query"
    migration_ids: list[str] = Field(default_factory=list)
    active_schema_mapping_id: str = ""
    schema_mapping_ids: list[str] = Field(default_factory=list)
    pending_schema_gate_confirm: bool = False
    fiix_credentials_configured: bool = False
    fiix_subdomain: str = ""
    # Saved Spaces (Feature 2) — from session_workspace.workspace_snapshot / infer_saved_space
    saved_space: str | None = None
    last_domain: str | None = None
    last_tool: str | None = None


class WorkflowResponse(BaseModel):
    session_id: str
    answer: str
    tool_calls: list[ToolCallRecord]
    success: bool
    error: str | None = None
    # HITL fields — populated when a tool calls interrupt()
    interrupted: bool = False
    interrupt_payload: dict | None = None
    # Single-door routing (Phases 1–2, 7)
    route_metadata: RouteMetadata | None = None
    workspace_status: WorkspaceStatusResponse | None = None
    # Bulk ingest (Phase D3) — set when file count exceeds inline threshold
    batch_id: str | None = None
    batch_status: str | None = None
    batch_progress_pct: float | None = None
    # Document IDs from index_document during single-door upload (for UI row matching)
    ingested_document_ids: list[str] = Field(default_factory=list)
    ingested_migration_ids: list[str] = Field(default_factory=list)
    ingested_schema_mapping_ids: list[str] = Field(default_factory=list)
    # Structured citations for chat UI (source file + confidence + quote)
    citations: list[dict[str, Any]] = Field(default_factory=list)


class ResumeRequest(BaseModel):
    decision: dict[str, Any] = Field(
        ...,
        description=(
            "Human decision for the interrupt gate. "
            "mapping_approval: {'approved': bool, 'corrections': dict}. "
            "rollback_confirmation: {'confirmed': bool}."
        ),
    )


class ThreadStatusResponse(BaseModel):
    session_id: str
    interrupted: bool
    interrupt_payload: dict | None = None


class ToolInfo(BaseModel):
    name: str
    description: str
    domain: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_ingested_document_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for tc in tool_calls:
        if tc.get("tool") != "index_document":
            continue
        out = tc.get("output")
        if not isinstance(out, dict):
            continue
        did = out.get("document_id") or out.get("doc_id") or out.get("id")
        if did:
            ids.append(str(did))
    return ids


def _extract_ingested_migration_ids(
    tool_calls: list[dict[str, Any]],
    session_id: str | None = None,
) -> list[str]:
    from src.agents.session_workspace import resolve_session_migration_ids

    if session_id:
        return resolve_session_migration_ids(session_id, tool_calls)
    from src.agents.session_workspace import extract_start_migration_ids

    return extract_start_migration_ids(tool_calls)


def _extract_ingested_schema_mapping_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    ids: list[str] = []
    for tc in tool_calls:
        if tc.get("tool") not in ("start_schema_mapping", "start_fiix_schema_mapping", "continue_schema_mapping_gate"):
            continue
        out = tc.get("output")
        if isinstance(out, str):
            try:
                import json as _json

                out = _json.loads(out)
            except Exception:
                continue
        if not isinstance(out, dict):
            continue
        sid = out.get("schema_mapping_id")
        if not sid and tc.get("tool") == "continue_schema_mapping_gate":
            status = out.get("status")
            if isinstance(status, dict):
                sid = status.get("schema_mapping_id")
        if sid:
            ids.append(str(sid))
    return ids


def _to_response(result: dict[str, Any]) -> WorkflowResponse:
    if not result["success"] and result.get("error"):
        raise HTTPException(status_code=500, detail=result["error"])
    rm = result.get("route_metadata")
    ws = result.get("workspace_status")
    tool_calls_raw = result.get("tool_calls") or []
    ingested = result.get("ingested_document_ids")
    if ingested is None:
        ingested = _extract_ingested_document_ids(tool_calls_raw)
    migration_ids = result.get("ingested_migration_ids")
    if migration_ids is None:
        migration_ids = _extract_ingested_migration_ids(
            tool_calls_raw, session_id=result.get("session_id")
        )
    schema_ids = result.get("ingested_schema_mapping_ids")
    if schema_ids is None:
        schema_ids = _extract_ingested_schema_mapping_ids(tool_calls_raw)
    return WorkflowResponse(
        session_id=result["session_id"],
        answer=result["answer"],
        tool_calls=[ToolCallRecord(**tc) for tc in result["tool_calls"]],
        success=result["success"],
        error=result["error"],
        interrupted=result.get("interrupted", False),
        interrupt_payload=result.get("interrupt_payload"),
        route_metadata=RouteMetadata(**rm) if isinstance(rm, dict) else None,
        workspace_status=WorkspaceStatusResponse(**ws) if isinstance(ws, dict) else None,
        batch_id=result.get("batch_id"),
        batch_status=result.get("batch_status"),
        batch_progress_pct=result.get("batch_progress_pct"),
        ingested_document_ids=list(ingested or []),
        ingested_migration_ids=list(migration_ids or []),
        ingested_schema_mapping_ids=list(schema_ids or []),
        citations=list(result.get("citations") or []),
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def run_workflow(
    request: Request,
    body: WorkflowRequest,
    orchestrator=Depends(get_orchestrator),
) -> WorkflowResponse:
    """
    Execute a natural language CAFM request through the DeepAgent (stateless).

    Each call is independent — state is never shared between requests.
    Use /run-stateful when you need HITL interrupt support.
    Rate limited to 20 requests/minute per IP.
    """
    activity_log.set_current_session(body.session_id, body.session_id)
    activity_log.start_turn()  # one transaction per request; every row below shares it
    log.info("workflow.run", message_len=len(body.message), session_id=body.session_id)
    result = await orchestrator.run(
        user_message=body.message,
        session_id=body.session_id,
        extra_context=body.context,
    )
    return _to_response(result)


@router.post("/run-stateful", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def run_stateful_workflow(
    request: Request,
    body: WorkflowRequest,
    orchestrator=Depends(get_orchestrator),
) -> WorkflowResponse:
    """
    Execute a CAFM request with persistent state (HITL-capable).

    If a tool calls interrupt() (e.g. map_fields with low confidence or
    rollback_migration), the response will have interrupted=true and an
    interrupt_payload describing what the human needs to decide.

    Submit the human decision via POST /api/workflow/resume/{session_id}.

    Requires HITL_ENABLED=true and a Postgres checkpointer to be configured.
    Rate limited to 20 requests/minute per IP.
    """
    sid = body.session_id
    if not sid:
        raise HTTPException(
            status_code=400,
            detail="session_id is required for stateful (HITL-capable) workflow runs.",
        )
    activity_log.set_current_session(sid, sid)
    activity_log.start_turn()  # one transaction per request; every row below shares it
    log.info("workflow.run_stateful", session_id=sid, message_len=len(body.message))
    result = await orchestrator.run_stateful(
        user_message=body.message,
        session_id=sid,
        extra_context=body.context,
    )
    return _to_response(result)


@router.post("/run-stateful-with-files", response_model=WorkflowResponse)
@limiter.limit("120/minute")
async def run_stateful_workflow_with_files(
    request: Request,
    message: str = Form(...),
    session_id: str = Form(...),
    context: str | None = Form(None),
    organization_id: str | None = Form(None),
    cmms_name: str = Form("Custom"),
    ingest_source: str = Form("files"),
    schema_mapping_id: str | None = Form(None),
    interactive_doc_match: bool = Form(False),
    interactive_migration: bool = Form(False),
    files: list[UploadFile] = File(default_factory=list),
    orchestrator=Depends(get_orchestrator),
) -> WorkflowResponse:
    """
    Single-door endpoint for chat + batch file ingestion.

    Flow:
      1) Save uploaded files (or trigger live Fiix sync when ingest_source=fiix)
      2) Run sequence:
         - CSV/Excel => migration flow (auto gates)
         - PDF/Word/images => doc-rag indexing
      3) Continue orchestrator chat in same session
    """
    source = (ingest_source or "files").strip().lower()
    org = organization_id or "00000000-0000-0000-0000-000000000001"

    if source == "fiix":
        from ...agents.session_workspace import ROUTE_FIIX_SYNC, set_pending_fiix_confirm

        set_pending_fiix_confirm(
            session_id,
            action="ingestion" if schema_mapping_id else "schema_mapping",
        )
        extra = (context or "").strip()
        runtime_context = (
            f"Single-door Fiix for session {session_id}. "
            "Do NOT call start_fiix_ingestion until Fiix credentials are collected. "
            "Follow Schema Mapper UI flow: get_fiix_setup_status → ask for subdomain, "
            "App Key, Access Key, Secret Key → configure_fiix_credentials → "
            "test_fiix_connection → fetch_fiix_schema → start_fiix_schema_mapping. "
            f"organization_id={org}. "
            + (f"schema_mapping_id={schema_mapping_id} for optional ingestion after mapping." if schema_mapping_id else "")
        )
        combined_context = f"{extra}\n\n{runtime_context}".strip() if extra else runtime_context
        user_msg = (message or "").strip() or "Connect to Fiix and fetch live schema"
        result = await orchestrator.run_stateful(
            user_message=user_msg,
            session_id=session_id,
            extra_context=combined_context,
        )
        if not result.get("route_intent"):
            result["route_intent"] = ROUTE_FIIX_SYNC
        return _to_response(result)

    if not files:
        raise HTTPException(status_code=400, detail="At least one file is required.")

    use_bulk = len(files) > settings.ingest_batch_inline_threshold

    async with _WITH_FILES_CONCURRENCY:
        upload_dir = ensure_upload_dir(settings.deep_agents_upload_dir)
        saved_paths: list[str] = []
        try:
            # Stream each upload to disk in bounded chunks and enforce a cumulative size cap.
            # The previous `await f.read()` buffered the ENTIRE file in RAM with NO ceiling, so a
            # large CMMS export (or several) could OOM-crash this shared service — the front door —
            # before the schema-mapper's own 500 MB / HTTP 413 guard ever ran. (An nginx gateway
            # caps the body at 200m, but in direct-access deployments there is no such gate, so the
            # app must protect itself.) Streaming keeps peak memory at one chunk; the cumulative cap
            # returns a clean 413 that tells the user to split the export. NB: the cap is the ONLY
            # multi-file-affecting change here — it rejects only when the COMBINED files exceed the
            # limit (well above any normal spreadsheet set), and it's env-overridable.
            max_upload_bytes = settings.deep_agents_max_upload_mb * 1024 * 1024
            chunk_size = 1024 * 1024  # 1 MB
            total_bytes = 0
            for f in files:
                safe_name = sanitize_filename(f.filename or "upload.bin")
                dest = upload_dir / f"{session_id}_{safe_name}"
                with dest.open("wb") as out:
                    while True:
                        chunk = await f.read(chunk_size)
                        if not chunk:
                            break
                        total_bytes += len(chunk)
                        if total_bytes > max_upload_bytes:
                            out.close()
                            remove_files(saved_paths + [str(dest)])
                            raise HTTPException(
                                status_code=413,
                                detail=(
                                    f"Upload too large — the combined files exceed the "
                                    f"{settings.deep_agents_max_upload_mb} MB migration limit. "
                                    f"Split the export into smaller files and upload them separately."
                                ),
                            )
                        out.write(chunk)
                saved_paths.append(str(dest))

            if use_bulk:
                from ...services.ingest_batch_service import create_ingest_batch
                from ...workers.ingest_batch_worker import schedule_ingest_batch

                batch = await create_ingest_batch(
                    session_id=session_id,
                    organization_id=org,
                    cmms_name=cmms_name,
                    file_paths=saved_paths,
                )
                batch_id = str(batch["batch_id"])
                orchestrator.register_active_batch(session_id, batch_id, len(files))
                schedule_ingest_batch(batch_id)
                log.info(
                    "workflow.bulk_batch.started",
                    session_id=session_id,
                    batch_id=batch_id,
                    file_count=len(files),
                )
                answer = (
                    f"Bulk ingestion started for {len(files)} file(s).\n"
                    f"Batch ID: {batch_id}\n"
                    "Status: pending — processing in the background. Spreadsheets are combined "
                    "into a single migration for review; any documents are indexed in parallel.\n"
                    "Ask for batch status or poll GET /api/ingest/batches/{batch_id}."
                )
                ws = await orchestrator.get_workspace_status(session_id)
                return WorkflowResponse(
                    session_id=session_id,
                    answer=answer,
                    tool_calls=[
                        ToolCallRecord(
                            tool="ingest_batch",
                            input={"batch_id": batch_id, "file_count": len(files)},
                            output={"status": "pending", "batch_id": batch_id},
                        )
                    ],
                    success=True,
                    error=None,
                    interrupted=False,
                    interrupt_payload=None,
                    route_metadata=RouteMetadata(
                        route_intent="bulk_ingest",
                        selected_domain="ingest_batch",
                        selected_tool="ingest_batch",
                    ),
                    workspace_status=WorkspaceStatusResponse(**ws),
                    batch_id=batch_id,
                    batch_status="pending",
                    batch_progress_pct=0.0,
                )

            flow = await run_single_door_ingestion_sequence(
                session_id=session_id,
                file_paths=saved_paths,
                organization_id=organization_id,
                cmms_name=cmms_name,
                user_message=message,
                skip_row_match=interactive_doc_match,
                interactive_migration=interactive_migration,
            )
            orchestrator.mark_single_door_ingestion(
                session_id=session_id,
                ingested_count=len(files),
                flow_summary=flow.summary_text,
            )
            from src.agents.session_workspace import (
                refresh_workspace_from_migrations,
                sync_migration_ids_from_tool_calls,
            )

            sync_migration_ids_from_tool_calls(session_id, flow.tool_calls)
            await refresh_workspace_from_migrations(session_id)
            extra = (context or "").strip()
            msg_l = " ".join((message or "").strip().lower().split())
            wants_full_flow = any(
                k in msg_l
                for k in (
                    "phase",
                    "mapping",
                    "hierarchy",
                    "hybrid",
                    "evidence",
                    "single door",
                    "complete",
                    "udr",
                    "extract",
                    "summarize",
                )
            )
            already_processed_note = (
                "In this request, uploaded files were already processed by the single-door pipeline. "
                "Do not call index_document or start_migration again for these same files."
            )
            if wants_full_flow:
                already_processed_note += (
                    " MANDATORY: Continue the user's original multi-step request in this same "
                    "response — use Doc RAG and hybrid UDR tools for PDF/TXT/images, migration/UDR "
                    "for CSV/Excel. Provide answers with evidence; do not reply with only "
                    "'how can I help' or ask to re-upload."
                )
            else:
                already_processed_note += (
                    " Continue with routing, clarifications, and next-step guidance."
                )
            runtime_context = f"{flow.context_note}\n\n{already_processed_note}"
            combined_context = (
                f"{extra}\n\n{runtime_context}".strip()
                if extra
                else runtime_context
            )
            result = await orchestrator.run_stateful(
                user_message=message,
                session_id=session_id,
                extra_context=combined_context,
                detected_engines=list(flow.detected_engines or []),
                pipeline_tool_calls=list(flow.tool_calls or []),
                filenames=[Path(p).name for p in saved_paths],
            )
            result_tool_calls = list(result.get("tool_calls") or [])
            merged_tool_calls = [*flow.tool_calls, *result_tool_calls]
            result["tool_calls"] = merged_tool_calls
            result["ingested_document_ids"] = _extract_ingested_document_ids(merged_tool_calls)
            result["ingested_migration_ids"] = _extract_ingested_migration_ids(
                merged_tool_calls, session_id=session_id
            )
            result["ingested_schema_mapping_ids"] = _extract_ingested_schema_mapping_ids(
                merged_tool_calls
            )
            if schema_mapping_id and schema_mapping_id not in result["ingested_schema_mapping_ids"]:
                result["ingested_schema_mapping_ids"].append(schema_mapping_id)
            if flow.summary_text or flow.step_summaries or flow.match_report:
                citations = extract_chat_citations(merged_tool_calls)
                # Also attach confidence from compliance notes when index docs exist
                result["citations"] = citations
                preface = format_single_door_chat_preface(
                    step_summaries=flow.step_summaries or [],
                    summary_text=flow.summary_text or "",
                    match_report=flow.match_report or "",
                    citations=citations,
                )
                # The structured preface is the authoritative, deduplicated summary of the
                # single-door ingestion. The orchestrator's model answer, given the same
                # certificates in context, re-lists all of them (plus its own Verification
                # block) — a verbatim duplicate of the cards. Only append it when the user
                # explicitly asked for further multi-step analysis (wants_full_flow); for a
                # plain ingest the preface stands alone.
                model_answer = (result.get("answer") or "").strip()
                if model_answer and wants_full_flow:
                    result["answer"] = f"{preface}\n\n---\n\n{model_answer}".strip()
                else:
                    result["answer"] = preface
            else:
                result["citations"] = extract_chat_citations(merged_tool_calls)
            return _to_response(result)
        finally:
            if not use_bulk:
                remove_files(saved_paths)


@router.post("/resume/{session_id}", response_model=WorkflowResponse)
async def resume_workflow(
    session_id: str,
    body: ResumeRequest,
    orchestrator=Depends(get_orchestrator),
) -> WorkflowResponse:
    """
    Resume an interrupted workflow by submitting the human decision.

    The session_id must match the one used in the original /run-stateful call.
    The decision format depends on the interrupt type shown in interrupt_payload:

    mapping_approval gate:
        {"approved": true, "corrections": {"Source Col": "canonical_field"}}
        {"approved": false}  ← cancels the mapping

    rollback_confirmation gate:
        {"confirmed": true}   ← proceeds with the destructive rollback
        {"confirmed": false}  ← cancels the rollback
    """
    activity_log.set_current_session(session_id, session_id)
    activity_log.start_turn()  # one transaction per request; every row below shares it
    activity_log.set_current_session(session_id, session_id)
    activity_log.start_turn()  # one transaction per request; every row below shares it
    log.info("workflow.resume", session_id=session_id, decision_keys=list(body.decision.keys()))
    result = await orchestrator.resume(
        session_id=session_id,
        decision=body.decision,
    )
    return _to_response(result)


@router.get("/workspace/{session_id}", response_model=WorkspaceStatusResponse)
async def get_workspace_status(
    session_id: str,
    migration_id: list[str] | None = Query(
        None,
        description="Optional migration UUID(s) from the UI to sync mapping/hierarchy pills",
    ),
    orchestrator=Depends(get_orchestrator),
) -> WorkspaceStatusResponse:
    """Phase 7 — workspace status cards for ingestion / mapping / hierarchy / WO candidate."""
    if migration_id:
        from ...agents.session_workspace import register_migration_id

        for mid in migration_id:
            register_migration_id(session_id, mid)
    snap = await orchestrator.get_workspace_status(session_id)
    return WorkspaceStatusResponse(**snap)


@router.get("/status/{session_id}", response_model=ThreadStatusResponse)
async def get_workflow_status(
    session_id: str,
    orchestrator=Depends(get_orchestrator),
) -> ThreadStatusResponse:
    """
    Check whether a stateful workflow session is currently interrupted.

    Returns interrupted=true and the interrupt_payload if the session is
    waiting for a human decision. Returns interrupted=false if the session
    completed or doesn't exist.
    """
    state = await orchestrator.get_thread_state(session_id)
    if state is None:
        return ThreadStatusResponse(
            session_id=session_id,
            interrupted=False,
            interrupt_payload=None,
        )
    return ThreadStatusResponse(**state)


@router.get("/tools", response_model=list[ToolInfo])
async def list_tools() -> list[ToolInfo]:
    """
    Return all 38 registered tools with their names, descriptions, and agent domains.

    Domains: meta · udr · wo_engine · migration · doc_rag · compliance ·
    contract_performance · energy_intelligence
    """
    from ...agents.orchestrator import TOOL_CATALOG, _TOOL_DOMAIN

    return [
        ToolInfo(
            name=t.name,
            description=t.description or "",
            domain=_TOOL_DOMAIN.get(t.name, "unknown"),
        )
        for t in TOOL_CATALOG
    ]


@router.websocket("/ws/{session_id}")
async def ws_workflow(session_id: str, websocket: WebSocket) -> None:
    """
    Stream workflow events in real time over WebSocket.

    After connecting, send a single JSON message:
        {"message": "your request", "context": "optional extra context"}

    Events are streamed back as JSON objects with a `type` field:

        tool_started       {"type": "tool_started", "tool": "...", "domain": "...", "input": {...}}
        tool_completed     {"type": "tool_completed", "tool": "...", "domain": "...", "output": ...}
        agent_switch       {"type": "agent_switch", "from_domain": "...", "to_domain": "..."}
        gate_interrupt     {"type": "gate_interrupt", "payload": {...}, "session_id": "..."}
        workflow_completed {"type": "workflow_completed", "answer": "...", "session_id": "..."}
        error              {"type": "error", "error": "...", "session_id": "..."}

    The server closes the connection after workflow_completed, gate_interrupt, or error.
    HITL gates surface as gate_interrupt events — to resume them use POST /resume/{session_id}.
    """
    await websocket.accept()
    orchestrator = getattr(websocket.app.state, "orchestrator", None)
    if orchestrator is None:
        await websocket.send_text(json.dumps({
            "type": "error", "error": "Orchestrator not initialised", "session_id": session_id,
        }))
        await websocket.close(code=1011)
        return

    try:
        raw = await websocket.receive_text()
        body = json.loads(raw)
        message: str = body.get("message", "").strip()
        context: str | None = body.get("context")

        if not message:
            await websocket.send_text(json.dumps({
                "type": "error", "error": "message field is required", "session_id": session_id,
            }))
            return

        activity_log.set_current_session(session_id, session_id)
        activity_log.start_turn()  # one transaction per request; every row below shares it
        log.info("ws_workflow.start", session_id=session_id, message_len=len(message))

        async for event in orchestrator.stream(
            user_message=message,
            session_id=session_id,
            extra_context=context,
        ):
            await websocket.send_text(json.dumps(event, default=str))

    except Exception as exc:
        log.error("ws_workflow.error", session_id=session_id, error=str(exc))
        try:
            await websocket.send_text(json.dumps({
                "type": "error", "error": str(exc), "session_id": session_id,
            }))
        except Exception:
            pass
    finally:
        try:
            await websocket.close()
        except Exception:
            pass



# ── Activity log — the replayable trail of every agent input and output ───────────────


class ActivityEntry(BaseModel):
    """One entry posted by a client (the UI's own compliance actions), stored alongside the
    server-side stages so a session's trail is complete end to end."""

    session_id: str = Field(..., max_length=120)
    turn_id: str | None = Field(None, max_length=64, description="Groups the rows of one action")
    agent: str = Field("frontend", max_length=60)
    stage: str = Field(..., max_length=60)
    direction: str = Field(..., pattern="^(input|output|error)$")
    summary: str | None = Field(None, max_length=4000)
    payload: dict[str, Any] | None = None
    ok: bool = True
    error: str | None = Field(None, max_length=4000)
    latency_ms: float | None = None


@router.get("/activity")
async def activity_sessions(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    """Sessions with recorded activity, newest first."""
    return {"ok": True, "sessions": await activity_log.recent_sessions(limit=limit)}


@router.get("/activity/{session_id}")
async def activity_for_session(
    session_id: str,
    agent: str | None = Query(None, description="orchestrator | compliance | compliance_router | tool:<domain> | frontend"),
    stage: str | None = Query(None, description="turn | plan | fetch | summary | analyst | review | router | tool | action"),
    turn_id: str | None = Query(None, description="Only the rows of one transaction"),
    limit: int = Query(200, ge=1, le=2000),
) -> dict[str, Any]:
    """Every recorded input/output for one session, oldest first — for troubleshooting a turn."""
    rows = await activity_log.list_activity(session_id, agent=agent, stage=stage, turn_id=turn_id, limit=limit)
    return {"ok": True, "session_id": session_id, "turn_id": turn_id, "count": len(rows), "entries": rows}


@router.get("/activity/{session_id}/turns")
async def activity_turns(session_id: str, limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    """The transactions of one session: per turn, its first input, final output, the stages it
    passed through, token totals and whether every step succeeded."""
    turns = await activity_log.list_turns(session_id, limit=limit)
    return {"ok": True, "session_id": session_id, "count": len(turns), "turns": turns}


@router.post("/activity", status_code=201)
async def activity_append(entry: ActivityEntry) -> dict[str, Any]:
    """Append one client-side activity entry (e.g. the compliance console's scan / verify /
    renewal calls with their responses)."""
    row_id = await activity_log.record(
        agent=entry.agent, stage=entry.stage, direction=entry.direction,
        summary=entry.summary, payload=entry.payload, ok=entry.ok, error=entry.error,
        latency_ms=entry.latency_ms, session_id=entry.session_id, thread_id=entry.session_id,
        turn_id=entry.turn_id,
    )
    return {"ok": row_id is not None, "id": row_id}
