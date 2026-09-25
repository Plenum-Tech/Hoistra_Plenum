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

import httpx
import structlog
from uuid import UUID
from ... import database
from sqlalchemy import text

from fastapi import (
    APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile, WebSocket, status,
)
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
from ...services import building_binding, usage_events
from ...services import ingestion_validation as validation_gate
from ...services.principal import (
    WS_AUTH_SUBPROTOCOL,
    Principal,
    bearer_from_subprotocols,
    caller_principal,
    current_principal,
    resolve as resolve_principal,
)
from ...http_client import caller_authorization, caller_organization_id
from ...provider_errors import provider_refusal, refusal_detail

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
    # Superadmin-only override (viewAsCompany on the frontend) — same rule as
    # run-stateful-with-files' own organization_id form field: naming your own company is a
    # no-op, naming a different one as anyone but a superadmin is a 403, and a normal caller
    # never sends this at all. Default None reproduces today's behaviour exactly.
    organization_id: str | None = Field(None, description="Superadmin-only: view as this company")


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


async def _extraction_plan_for(text: str, authorization: str | None) -> dict | None:
    """What operations-intelligence would read this document for, per domain.

    Model-free on the other side, so it is cheap enough to run on every file. Returns None
    rather than raising when the service is unreachable — a receipt that cannot say what it
    looked for is worse than one that says so, but neither is worth losing the upload over.
    """
    if not (text or "").strip():
        return {"ok": True, "domains_present": [], "domains_absent": [],
                "note": "no text could be read from this file, so nothing could be looked for"}
    base = settings.operations_intelligence_base_url.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base}/api/ingestion/extraction-plan",
                json={"text": text[:2_000_000]},
                headers={"Authorization": authorization} if authorization else {},
            )
        if resp.status_code >= 400:
            return {"ok": False, "error": f"extraction-plan returned {resp.status_code}"}
        return resp.json()
    except httpx.HTTPError as exc:
        return {"ok": False, "error": str(exc)[:200]}


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
    # The ingestion check, one case per uploaded file: verdict, findings, the question put
    # to the uploader and the building suggested instead. `validation_held` names the cases
    # that stopped this upload — those files are indexed and registered but NOT bound to a
    # building, and will not be until somebody answers.
    validation_cases: list[dict[str, Any]] = Field(default_factory=list)
    validation_held: list[str] = Field(default_factory=list)
    # What each uploaded file was READ FOR, per domain, and for the domains it does not
    # mention, why not. A document is not one kind of thing — an FM contract names the
    # supplier, the assets it covers, the PPM frequency it commits to and the certificates
    # the contractor must hold. Routing picked one and ran one extractor, so the receipt said
    # "ingested" while three of those four were never looked at. This is what makes that
    # visible: one entry per file, with domains_present, domains_absent and the reason for
    # each absence. Absent-with-a-reason and never-checked look identical without it.
    extraction_plans: list[dict[str, Any]] = Field(default_factory=list)


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
        err = str(result["error"])
        refusal = provider_refusal(err)
        if refusal:
            provider, kind = refusal
            # 503, not 500: this service is up and the request was fine — the model provider
            # it depends on will not serve us. A 500 sends somebody to read a traceback that
            # says nothing they can act on.
            #
            # And the provider's own message does NOT go in the body. It shipped doing that,
            # so a failing report card stored
            #   orchestrator answered 500: {"detail":"Error code: 401 - {'error': {'message':
            #   'You do not have access to the organization tied to the API key.'…
            # on a record a user reads. Upstream error text is for the log, where it is
            # already written in full with the traceback.
            log.error("workflow.model_provider_refused", provider=provider, kind=kind,
                      detail=err[:400])
            raise HTTPException(status_code=503, detail=refusal_detail(provider, kind))
        log.error("workflow.run_failed", detail=err[:400])
        raise HTTPException(status_code=500, detail={
            "ok": False, "reason": "orchestrator_failed",
            "error": "The orchestrator could not complete this turn. The reason is in the "
                     "service log against this session id.",
        })
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
        validation_cases=list(result.get("validation_cases") or []),
        validation_held=[str(x) for x in (result.get("validation_held") or []) if x],
        extraction_plans=list(result.get("extraction_plans") or []),
    )


def _resolve_acting_org(named_org: str | None, principal: Principal) -> str | None:
    """Same authorization rule as run-stateful-with-files' own organization_id form field:
    naming your own company is a no-op, a normal caller naming any other company is a 403,
    and a superadmin naming one is honoured. Returns the org to scope this turn's
    operations-intelligence tool calls to (None = no override, i.e. today's behaviour —
    every tool call resolves org from the caller's own token, same as always)."""
    is_super = principal.role == "superadmin"
    named = (named_org or "").strip() or None
    if named and not is_super and named != str(principal.organization_id):
        raise HTTPException(status_code=403, detail={
            "ok": False, "error": "You can only ask about your own company.",
            "reason": "wrong_organization"})
    return named if (named and is_super) else None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/run", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def run_workflow(
    request: Request,
    body: WorkflowRequest,
    orchestrator=Depends(get_orchestrator),
    principal: Principal = Depends(current_principal),
) -> WorkflowResponse:
    """
    Execute a natural language CAFM request through the DeepAgent (stateless).

    Each call is independent — state is never shared between requests.
    Use /run-stateful when you need HITL interrupt support.
    Rate limited to 20 requests/minute per IP.
    """
    # Every call this turn makes to operations-intelligence carries the caller's own token,
    # so what comes back is scoped to their company and buildings by default — not to a
    # service credential that sees all, and not to just anything a client sends: a
    # superadmin's viewAsCompany() override is authorized below (_resolve_acting_org) before
    # it ever reaches caller_organization_id, same as every other route that honours it.
    caller_authorization.set(request.headers.get("authorization"))
    # And the caller themselves, for the reads that run SQL here rather than over HTTP.
    caller_principal.set(principal)
    caller_organization_id.set(_resolve_acting_org(body.organization_id, principal))
    activity_log.set_current_session(body.session_id, body.session_id)
    activity_log.start_turn()  # one transaction per request; every row below shares it
    log.info("workflow.run", message_len=len(body.message), session_id=body.session_id,
             user_id=str(principal.user_id), role=principal.role)
    result = await orchestrator.run(
        user_message=body.message,
        session_id=body.session_id,
        extra_context=body.context,
    )
    await usage_events.record_usage(
        kind="query", organization_id=principal.organization_id, user_id=principal.user_id,
        detail={"session_id": body.session_id, "chars": len(body.message)},
    )
    return _to_response(result)


@router.post("/run-stateful", response_model=WorkflowResponse)
@limiter.limit("20/minute")
async def run_stateful_workflow(
    request: Request,
    body: WorkflowRequest,
    orchestrator=Depends(get_orchestrator),
    principal: Principal = Depends(current_principal),
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
    caller_authorization.set(request.headers.get("authorization"))
    # And the caller themselves, for the reads that run SQL here rather than over HTTP.
    caller_principal.set(principal)
    caller_organization_id.set(_resolve_acting_org(body.organization_id, principal))
    await usage_events.record_usage(
        kind="query", organization_id=principal.organization_id, user_id=principal.user_id,
        detail={"session_id": sid, "chars": len(body.message), "stateful": True},
    )
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
    building_id: str | None = Form(None),
    cmms_name: str = Form("Custom"),
    ingest_source: str = Form("files"),
    schema_mapping_id: str | None = Form(None),
    interactive_doc_match: bool = Form(False),
    interactive_migration: bool = Form(False),
    files: list[UploadFile] = File(default_factory=list),
    orchestrator=Depends(get_orchestrator),
    principal: Principal = Depends(current_principal),
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
    caller_authorization.set(request.headers.get("authorization"))
    # And the caller themselves, for the reads that run SQL here rather than over HTTP.
    caller_principal.set(principal)

    # The company is the caller's. It used to be whatever organization_id the form carried,
    # with a hard-coded default when it carried none — so any client could file documents
    # into any tenant. A superadmin may still name one; anyone else naming another is a bug
    # worth a 403 rather than a silent substitution.
    is_super = principal.role == "superadmin"
    if principal.organization_id is None and not is_super:
        raise HTTPException(status_code=400, detail={
            "ok": False, "error": "This account belongs to no company.",
            "reason": "no_organization"})
    named_org = (organization_id or "").strip() or None
    if named_org and not is_super and named_org != str(principal.organization_id):
        raise HTTPException(status_code=403, detail={
            "ok": False, "error": "You can only ingest into your own company.",
            "reason": "wrong_organization"})
    org = named_org if (named_org and is_super) else str(principal.organization_id)
    # Was only ever embedded as prose in the Fiix-sync context below (organization_id={org}),
    # which a model has no mechanism to turn into an actual query filter — the orchestrator's
    # own tool calls to operations-intelligence never carried this scope. Set it structurally
    # too, same as /run and /run-stateful, so a superadmin's named override (or a normal
    # caller's own org, resolved the same way it always was) actually reaches those calls.
    caller_organization_id.set(org)

    # Whether this person may add data at all. Read-only users see their buildings and stop
    # there; the message says who can change that rather than just refusing.
    if files and not principal.can_ingest:
        raise HTTPException(status_code=403, detail={
            "ok": False, "reason": "cannot_ingest",
            "error": "Your account can view its buildings but cannot ingest data. "
                     "Ask your company administrator to grant ingestion."})

    # Checked before a byte is written to disk. A building that does not exist is a caller
    # mistake worth an error, not something to discover after the file has been indexed and
    # the row has nowhere to hang.
    building = (building_id or "").strip() or None
    if building:
        try:
            building = str(UUID(building))
        except ValueError:
            raise HTTPException(status_code=400,
                                detail="building_id is not a UUID.") from None
        if not await building_binding.building_exists(building):
            raise HTTPException(
                status_code=404,
                detail=f"No building {building} — nothing to file this against.")
        # And it must be one of theirs. This is the boundary: a user allocated to Riverside
        # Court cannot file a document against Bishopsgate Tower however the form is filled.
        if not principal.allows_building(building):
            await usage_events.record_ingestion_audit(
                outcome="rejected", organization_id=principal.organization_id,
                actor_user_id=principal.user_id, actor_role=principal.role,
                document_name=", ".join((f.filename or "?") for f in files)[:500] or None,
                building_id=UUID(building), warning="building not allocated to this user",
                detail={"session_id": session_id},
            )
            raise HTTPException(status_code=403, detail={
                "ok": False, "reason": "building_not_allocated", "building_id": building,
                "error": "You are not allocated to that building, so you cannot ingest "
                         "its data."})
    elif files and principal.building_ids is not None:
        # A restricted user must say which of their buildings this is for. Filing against
        # nothing would leave the document unbound, which for them is invisible.
        raise HTTPException(status_code=400, detail={
            "ok": False, "reason": "building_required",
            "error": "Choose which of your buildings these files belong to.",
            "buildings": list(principal.buildings)})

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
                # The building travels with the batch. Without it, "multiple documents"
                # would bind up to the inline threshold and silently stop above it.
                schedule_ingest_batch(
                    batch_id, building_id=building,
                    authorization=request.headers.get("authorization"),
                    actor={"user_id": str(principal.user_id), "role": principal.role,
                           "email": principal.email},
                )
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
                # The building the uploader chose. Documents are bound to it after the
                # fact by the validation gate below; a meter has no document to bind, so
                # it has to travel with the ingest or the readings land on nothing.
                building_id=building,
            )
            # ── the check that runs before the building owns it ──────────────────────
            # Indexing is reversible; binding is what makes a document evidence — from that
            # moment it counts in the building's compliance position and its reports. So
            # each file is checked against the building that was selected first, and a file
            # that does not clearly belong there is HELD: registered, indexed, not bound,
            # and put to the uploader as a question. The protocol is the same for an admin.
            validation_cases: list[dict] = []
            if building:
                _extracted = building_binding.extracted_fields_by_file(flow.tool_calls)
                for _p in saved_paths:
                    _name = Path(_p).name
                    _case = await validation_gate.validate(
                        building_id=building, document_name=_name,
                        doc_type=building_binding.doc_type_for(_name, flow.tool_calls),
                        extracted=_extracted.get(_name) or {},
                        text=building_binding.document_text(_p),
                        session_id=session_id,
                        authorization=request.headers.get("authorization"),
                    )
                    _case.setdefault("document_name", _name)
                    validation_cases.append(_case)
            held = validation_gate.blocking(validation_cases)

            # Read every file for every domain, not just the one routing picked. Advisory and
            # never fatal: a plan that cannot be produced must not cost the upload, so each
            # failure is recorded against its own file rather than raised.
            extraction_plans: list[dict] = []
            for _p in saved_paths:
                _n = Path(_p).name
                try:
                    _text = building_binding.document_text(_p) or ""
                    _plan = await _extraction_plan_for(_text, request.headers.get("authorization"))
                    extraction_plans.append({"document_name": _n, **(_plan or {})})
                except Exception as exc:  # noqa: BLE001 — see the comment above
                    log.warning("single_door.extraction_plan_failed", file=_n, error=str(exc)[:200])
                    extraction_plans.append({"document_name": _n, "ok": False,
                                             "error": str(exc)[:300]})

            # The link the caller asked for, made by the caller. Failure here is reported
            # and does not fail the ingest: the file is indexed either way, and an unbound
            # document is recoverable while a lost upload is not.
            bound = {} if held else await building_binding.bind_and_log(
                building, flow.tool_calls, where="inline", session_id=session_id,
                file_paths=saved_paths)
            if held:
                # Registration and hashing still happen — the rows must exist for the
                # release to find them — but no building_id is written.
                await building_binding.bind_and_log(
                    None, flow.tool_calls, where="inline-held", session_id=session_id,
                    file_paths=saved_paths)
            # The receipt. One ingest event for the turn and one audit row per file, so the
            # trail can answer "who put this here" and the bill "what did it cost".
            await usage_events.record_usage(
                kind="ingest", organization_id=principal.organization_id,
                user_id=principal.user_id,
                building_id=UUID(building) if building else None,
                detail={"session_id": session_id, "files": len(saved_paths),
                        "bound": {k: v for k, v in (bound or {}).items()
                                  if isinstance(v, int)}},
            )
            _held_names = {c.get("document_name") for c in held}
            for _p in saved_paths:
                if Path(_p).name in _held_names:
                    continue  # the validation service already recorded why it was held
                await usage_events.record_ingestion_audit(
                    outcome="accepted", organization_id=principal.organization_id,
                    actor_user_id=principal.user_id, actor_role=principal.role,
                    document_name=Path(_p).name,
                    building_id=UUID(building) if building else None,
                    detail={"session_id": session_id, "where": "inline"},
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
            # A held document is the first thing the uploader needs to read, and the cases
            # travel with the answer so the interface can run the conversation without
            # going looking for them.
            if held:
                notice = validation_gate.summarise(validation_cases)
                _prev = (result.get("answer") or "").strip()
                result["answer"] = notice + ('\n\n---\n\n' + _prev if _prev else "")
            if validation_cases:
                result["validation_cases"] = validation_cases
                result["extraction_plans"] = extraction_plans
                result["validation_held"] = [c.get("id") for c in held]
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
        {"message": "your request", "context": "optional extra context",
         "organization_id": "optional, superadmin-only viewAsCompany override"}

    Events are streamed back as JSON objects with a `type` field:

        tool_started       {"type": "tool_started", "tool": "...", "domain": "...", "input": {...}}
        tool_completed     {"type": "tool_completed", "tool": "...", "domain": "...", "output": ...}
        agent_switch       {"type": "agent_switch", "from_domain": "...", "to_domain": "..."}
        gate_interrupt     {"type": "gate_interrupt", "payload": {...}, "session_id": "..."}
        workflow_completed {"type": "workflow_completed", "answer": "...", "session_id": "..."}
        error              {"type": "error", "error": "...", "session_id": "..."}

    The server closes the connection after workflow_completed, gate_interrupt, or error.
    HITL gates surface as gate_interrupt events — to resume them use POST /resume/{session_id}.

    Authentication. A browser cannot put a header on a socket, so the caller's token arrives
    in the handshake as `Sec-WebSocket-Protocol: hoistra.auth.bearer, <token>`; from there on
    this is the same identity path the POST routes take. A socket that carries no usable
    token is closed before the orchestrator is reached — 1008 when the credential is the
    problem and the client should sign in again, 1011 when identity itself is unreachable
    and signing in again could not help.
    """
    # Before accept, and before anything is logged against a session: a route that asks for
    # no credential answers to anyone, and the reads that run SQL in-process read an absent
    # caller as "nobody is restricting this one" — which is every building of every company.
    authorization = bearer_from_subprotocols(websocket.scope.get("subprotocols"))
    try:
        principal = await resolve_principal(authorization)
    except HTTPException as exc:
        # Whose problem it is decides what to say: a bad or absent credential is the
        # caller's and signing in again fixes it; identity being unreachable is ours, and
        # telling them to sign in again would send them round a loop that cannot end.
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        theirs = exc.status_code == status.HTTP_401_UNAUTHORIZED
        log.info("ws_workflow.refused", session_id=session_id,
                 status=exc.status_code, reason=detail.get("reason"))
        await websocket.close(
            code=1008 if theirs else 1011,
            reason="Sign in and reconnect." if theirs else "Identity service unavailable.",
        )
        return

    # Everything this turn fetches on the caller's behalf carries their own token, and the
    # direct-SQL reads get the same boundary operations-intelligence would have applied.
    caller_authorization.set(authorization)
    caller_principal.set(principal)

    # Agreeing to the marker, never to the token: the agreed subprotocol is echoed in the
    # handshake response and kept on the client's socket object.
    await websocket.accept(subprotocol=WS_AUTH_SUBPROTOCOL)
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

        # Same authorization as /run and /run-stateful: an override is honoured only for a
        # superadmin naming a company, everyone else naming anything but their own is
        # refused rather than the socket silently ignoring it and answering from their own
        # company anyway.
        try:
            caller_organization_id.set(_resolve_acting_org(body.get("organization_id"), principal))
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {"error": str(exc.detail)}
            await websocket.send_text(json.dumps({
                "type": "error", "session_id": session_id, **detail,
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
