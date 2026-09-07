"""
Migration agent tools — wraps svc-ai-schema-mapper (port 8003).

The Schema Mapper runs a 9-node LangGraph pipeline with 3 HITL gates + 1 auto-confirmed
write gate. The orchestrator drives this pipeline exactly like the frontend UI:
auto-advancing step_paused nodes, auto-confirming the write gate, and surfacing only
the three real user-decision gates.

Typical flow:
  1. start_migration(file_path, cmms_name) → migration_id
  2. run_migration(migration_id) → drives pipeline automatically
     - Auto-advances all step_paused nodes (no user input needed)
     - Auto-confirms the write gate (Gate 3) once reached
     - Returns {"status": "gate", ...} when a user-decision gate fires
     - Returns {"status": "complete"} when done
  3. Show gate payload to the user; call the appropriate submit_* tool
  4. Call run_migration again — repeat until status == "complete"
"""
from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from pathlib import Path

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from .meta_tools import get_session_context
from .session_workspace import get_migration_id_for_file, register_migration_file

log = structlog.get_logger(__name__)

_TIMEOUT_SHORT = 1800.0   # 30 min — status polls, gate submissions, advances
_TIMEOUT_UPLOAD = 1800.0  # 30 min — file upload + pipeline kickoff
_BASE = settings.migration_base_url

# Per-migration set of pipeline node_ids already streamed as a Processing-Log step.
# Persists ACROSS run_migration calls (gate cycles) so an earlier node is not re-emitted
# on the next call. Keyed by migration_id; cleared on terminal status.
_EMITTED_STEPS: dict[str, set] = {}


async def _emit_processing_step(migration_id: str, node: dict) -> None:
    """Push one Section-2 Processing-Log step to the live WS stream as a pipeline node
    completes. Uses a LangChain custom event so it surfaces through the orchestrator's
    astream_events loop without any side-channel. Best-effort — never breaks the run."""
    try:
        from langchain_core.callbacks.manager import adispatch_custom_event

        name = str(node.get("node_name") or "")
        lname = name.lower()
        # Gates / verification = chain-of-thought (review prep); execution nodes = chain-of-action.
        kind = "thought" if ("gate" in lname or "review" in lname or "verif" in lname) else "action"
        await adispatch_custom_event(
            "processing_step",
            {
                "migration_id": migration_id,
                "node_id": node.get("node_id"),
                "label": name,
                "kind": kind,
                "status": "completed",
                "at": node.get("completed_at"),
                "duration_ms": node.get("duration_ms"),
                "outcome": node.get("outcome") if isinstance(node, dict) else None,
            },
        )
    except Exception as exc:  # pragma: no cover - streaming is best-effort
        log.debug("run_migration.step_emit_failed", migration_id=migration_id, error=str(exc))


async def _stream_completed_steps(migration_id: str, resp: dict) -> None:
    """Emit a Processing-Log step for every pipeline node that has newly completed since
    the last poll (dedup via _EMITTED_STEPS, in pipeline order)."""
    emitted = _EMITTED_STEPS.setdefault(migration_id, set())
    for node in resp.get("nodes") or []:
        if not isinstance(node, dict):
            continue
        nid = node.get("node_id")
        if node.get("status") == "complete" and nid is not None and nid not in emitted:
            emitted.add(nid)
            await _emit_processing_step(migration_id, node)


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        log.error(f"migration.{op}.http_error", status=exc.response.status_code, body=exc.response.text[:300])
        return {"error": exc.response.text[:300], "status_code": exc.response.status_code}
    log.error(f"migration.{op}.error", error=str(exc)[:300])
    return {"error": str(exc)[:300]}


async def _get(path: str) -> dict:
    async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT_SHORT, follow_redirects=True) as client:
        resp = await client.get(path)
        resp.raise_for_status()
        return resp.json()


async def _post(path: str, json: dict | None = None) -> dict:
    async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT_SHORT, follow_redirects=True) as client:
        resp = await client.post(path, json=json or {})
        resp.raise_for_status()
        return resp.json()


# ── Tool 1 — Start ────────────────────────────────────────────────────────────

@tool
async def start_migration(
    file_path: str,
    cmms_name: str = "Custom",
    organization_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict:
    """Upload a CSV or Excel file and start the schema mapper pipeline.

    Always the first step. After this succeeds, call run_migration(migration_id)
    to drive the pipeline automatically — it handles step pauses and the write gate,
    and returns only when a real user-decision gate fires or the run completes.

    One migration per file. Multi-sheet Excel (.xlsx) is ONE migration — each sheet
    becomes a source table (sites, assets, work_orders, etc.) inside that job.
    Do not call start_migration again for the same file or per sheet.

    Args:
        file_path: Absolute path to the CSV/Excel file on the local machine.
        cmms_name: Source CMMS name hint (e.g. 'Maximo', 'Fiix', 'Generic').
        organization_id: UUID of the customer organization (default: test org).

    Returns dict with migration_id (UUID), status, progress_pct, message.
    """
    path = Path(file_path)
    if not path.exists():
        return {"error": f"File not found: {file_path}"}
    if not path.is_file():
        return {"error": f"Not a file: {file_path}"}

    # Read + hash the file up front so dedup is keyed on file CONTENT, not filename: an edited
    # same-named file starts a NEW migration; a renamed identical file reuses the existing one.
    try:
        file_bytes = path.read_bytes()
    except OSError as e:
        return {"error": f"Cannot read file: {e}"}
    content_key = "sha256:" + hashlib.sha256(file_bytes).hexdigest()

    session_id = get_session_context()
    if session_id and session_id != "shared":
        existing = get_migration_id_for_file(session_id, path.name, content_key=content_key)
        if existing:
            progress_pct = 0.0
            status = "running"
            try:
                st = await _get(f"/api/migration/{existing}/status")
                status = str(st.get("status") or "running")
                progress_pct = float(st.get("progress_pct") or 0.0)
            except Exception:
                pass
            log.info(
                "migration.start.reused",
                migration_id=existing,
                file=path.name,
                session_id=session_id,
            )
            return {
                "migration_id": existing,
                "status": status,
                "progress_pct": progress_pct,
                "reused": True,
                "message": (
                    f"Reusing migration {existing} for '{path.name}'. "
                    "Multi-sheet Excel is one migration — sheets are tables, not separate jobs."
                ),
            }

    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"

    try:
        async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT_UPLOAD, follow_redirects=True) as client:
            resp = await client.post(
                "/api/migration/start-with-upload",
                data={"cmms_name": cmms_name, "organization_id": organization_id},
                files={"file": (path.name, file_bytes, mime_type)},
            )
            resp.raise_for_status()
            data = resp.json()
            mid = str(data.get("migration_id") or "")
            if session_id and session_id != "shared" and mid:
                register_migration_file(session_id, mid, path.name, content_key=content_key)
            log.info("migration.started", migration_id=mid)
            return data
    except Exception as exc:
        return _err(exc, "start_migration")


# ── Tool 1b — Start MULTI (one migration from many structured files) ──────────

# Uploads are saved on disk as "{session_id}_{original_name}"; strip that prefix so
# the source table name is the user's real file/sheet name (not a UUID). Excel sheet
# names are capped at 31 chars, so the UUID prefix would otherwise eat the real name.
_SESSION_PREFIX_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}_"
)


def _original_upload_name(disk_name: str, session_id: str | None) -> str:
    """Best-effort recovery of the original filename from the on-disk upload name."""
    if session_id and disk_name.startswith(f"{session_id}_"):
        return disk_name[len(session_id) + 1:] or disk_name
    stripped = _SESSION_PREFIX_RE.sub("", disk_name)
    return stripped or disk_name


@tool
async def start_migration_multi(
    file_paths: list[str],
    cmms_name: str = "Custom",
    organization_id: str = "00000000-0000-0000-0000-000000000001",
) -> dict:
    """Upload SEVERAL CSV/Excel files and start ONE migration covering all of them.

    Use this for a mixed/batch upload where the user provides multiple spreadsheets
    in a single operation. All files are combined into ONE migration_id — each CSV
    file and each Excel sheet becomes a source table inside that single job.
    Do NOT call start_migration once per file when the files belong to the same
    upload — use this tool instead.

    After this succeeds, call run_migration(migration_id) to drive the pipeline,
    exactly as with start_migration.

    Args:
        file_paths: Absolute paths to the CSV/Excel files on the local machine.
        cmms_name: Source CMMS name hint (e.g. 'Maximo', 'Fiix', 'Generic').
        organization_id: UUID of the customer organization (default: test org).

    Returns dict with migration_id (UUID), status, progress_pct, message.
    """
    paths: list[Path] = []
    for fp in file_paths:
        p = Path(fp)
        if not p.exists() or not p.is_file():
            return {"error": f"File not found: {fp}"}
        paths.append(p)
    if not paths:
        return {"error": "No files provided to start_migration_multi"}

    # Single file → defer to the standard single-file path (one migration anyway).
    if len(paths) == 1:
        return await start_migration.ainvoke(
            {
                "file_path": str(paths[0]),
                "cmms_name": cmms_name,
                "organization_id": organization_id,
            }
        )

    session_id = get_session_context()
    upload_files = []
    for p in paths:
        try:
            data = p.read_bytes()
        except OSError as e:
            return {"error": f"Cannot read file {p.name}: {e}"}
        mime_type = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        upload_name = _original_upload_name(p.name, session_id)
        upload_files.append(("files", (upload_name, data, mime_type)))

    try:
        async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT_UPLOAD, follow_redirects=True) as client:
            resp = await client.post(
                "/api/migration/start-with-upload-multi",
                data={"cmms_name": cmms_name, "organization_id": organization_id},
                files=upload_files,
            )
            resp.raise_for_status()
            data = resp.json()
            mid = str(data.get("migration_id") or "")
            if session_id and session_id != "shared" and mid:
                for p in paths:
                    register_migration_file(session_id, mid, p.name)
            log.info("migration.started_multi", migration_id=mid, files=len(paths))
            return data
    except Exception as exc:
        return _err(exc, "start_migration_multi")


# ── Tool 2 — Run (polling loop) ───────────────────────────────────────────────

@tool
async def run_migration(migration_id: str) -> dict:
    """Drive the migration pipeline until a user-decision gate fires or completion.

    Mirrors the frontend UI behaviour exactly:
    - Auto-advances ALL step_paused states (ingest, deterministic mapping, semantic
      mapping, preprocessing, output generation — these never need user input)
    - Auto-confirms the write gate ('write' gate_type) — it executes automatically
      after all data-decision gates have been approved
    - Returns when a user-decision gate fires so the user can review and decide:
        {"status": "gate", "gate_type": "pre_semantic"|"field_mapping"|"hierarchy",
         "payload": {...}, "migration_id": ..., "message": "...instructions..."}
    - Returns when done:
        {"status": "complete", "migration_id": ...}
    - Returns on failure:
        {"status": "failed", "error": "...", "migration_id": ...}

    After receiving gate_type "pre_semantic" → call submit_pre_semantic
    After receiving gate_type "field_mapping" → call submit_field_mapping
    After receiving gate_type "hierarchy"     → call submit_hierarchy
    Then call run_migration again to continue.

    Args:
        migration_id: UUID returned by start_migration.
    """
    max_polls = 150  # ~5 minutes at 2 s/poll
    consecutive_status_errors = 0
    consecutive_action_errors = 0  # auto-advance / write-gate POSTs — same transient-blip tolerance

    for _ in range(max_polls):
        await asyncio.sleep(2)

        try:
            resp = await _get(f"/api/migration/{migration_id}/status")
        except Exception as exc:
            # A single status poll can blip (DB timeout, connection reset, transient 500) while the
            # migration itself keeps running on the schema-mapper. Tolerate a few CONSECUTIVE
            # failures and retry the next poll instead of failing the whole run — a transient error
            # here is exactly what made the run report "failed" yet recover on a page refresh.
            consecutive_status_errors += 1
            if consecutive_status_errors >= 5:
                return _err(exc, "run_migration.status")
            log.warning(
                "migration.run.status_retry",
                migration_id=migration_id,
                attempt=consecutive_status_errors,
                error=str(exc),
            )
            continue
        consecutive_status_errors = 0

        if "error" in resp:
            return {"status": "failed", "error": resp["error"], "migration_id": migration_id}

        raw_status: str = resp.get("status", "")
        gate_type: str = (resp.get("pending_gate_type") or "").lower()
        gate_payload: dict = resp.get("pending_gate_payload") or {}
        error_msg: str = resp.get("error_message") or resp.get("message") or raw_status

        # Stream a live Processing-Log step for each node that completed this poll —
        # so Activity Log Section 2 fills in node-by-node instead of all at once.
        await _stream_completed_steps(migration_id, resp)

        # ── Terminal: complete ──────────────────────────────────────────────
        if raw_status == "complete":
            log.info("migration.run.complete", migration_id=migration_id)
            _EMITTED_STEPS.pop(migration_id, None)
            return {"status": "complete", "migration_id": migration_id}

        # ── Terminal: failed / cancelled ────────────────────────────────────
        if raw_status in ("failed", "ddl_failed", "cancelled"):
            log.warning("migration.run.failed", migration_id=migration_id, status=raw_status)
            _EMITTED_STEPS.pop(migration_id, None)
            return {"status": "failed", "error": error_msg, "migration_id": migration_id}

        # ── Auto-advance: non-gate step pause ───────────────────────────────
        # Mirrors frontend: step_paused → auto-advance (no user review needed)
        if raw_status == "step_paused":
            log.info("migration.run.auto_advance", migration_id=migration_id)
            try:
                await _post(f"/api/migration/{migration_id}/advance")
            except Exception as exc:
                # Same reasoning as the status poll: a single advance POST can blip while the run is
                # fine. Retry on the next poll (which re-reads state) instead of failing the whole run;
                # if the advance actually landed, the next status shows progression and we won't retry.
                consecutive_action_errors += 1
                if consecutive_action_errors >= 5:
                    return _err(exc, "run_migration.advance")
                log.warning(
                    "migration.run.advance_retry",
                    migration_id=migration_id,
                    attempt=consecutive_action_errors,
                    error=str(exc),
                )
                continue
            consecutive_action_errors = 0
            continue

        # ── Gate handling ───────────────────────────────────────────────────
        if raw_status == "awaiting_review":

            # Auto-confirm write gate — mirrors frontend shouldAutoFinalizeWriteGate
            if "write" in gate_type:
                log.info("migration.run.auto_confirm_write", migration_id=migration_id)
                try:
                    await _post(
                        f"/api/migration/{migration_id}/gate/final",
                        json={"confirmed": True},
                    )
                except Exception as exc:
                    # Transient blip on the auto-confirm POST shouldn't fail the run — retry on the
                    # next poll. If the confirm landed, the next status leaves awaiting_review.
                    consecutive_action_errors += 1
                    if consecutive_action_errors >= 5:
                        return _err(exc, "run_migration.write_gate")
                    log.warning(
                        "migration.run.write_gate_retry",
                        migration_id=migration_id,
                        attempt=consecutive_action_errors,
                        error=str(exc),
                    )
                    continue
                consecutive_action_errors = 0
                continue

            # Gate 0: pre-semantic — user must review T1 mappings
            if "pre" in gate_type and "semantic" in gate_type:
                log.info("migration.run.gate", gate="pre_semantic", migration_id=migration_id)
                return {
                    "status": "gate",
                    "gate_type": "pre_semantic",
                    "payload": gate_payload,
                    "migration_id": migration_id,
                    "message": (
                        "Gate 0 (Pre-Semantic): Review the Tier-1 field mappings below. "
                        "Call submit_pre_semantic(migration_id, approve_all=True) to accept all, "
                        "or pass decisions={{table: [{source_field, decision: 'approve'|'semantic'}]}} "
                        "to reroute specific fields to semantic embedding."
                    ),
                }

            # Gate 1: field mapping — user must review low-confidence mappings
            if "field" in gate_type or "mapping" in gate_type:
                log.info("migration.run.gate", gate="field_mapping", migration_id=migration_id)
                return {
                    "status": "gate",
                    "gate_type": "field_mapping",
                    "payload": gate_payload,
                    "migration_id": migration_id,
                    "message": (
                        "Gate 1 (Field Mapping): Review low-confidence field mappings and unmapped fields. "
                        "Call submit_field_mapping(migration_id, approve_all=True) to accept all automatically, "
                        "or pass flagged_decisions / unmapped_decisions for specific overrides."
                    ),
                }

            # Gate 2: hierarchy — user must confirm FK relationships
            if "hier" in gate_type:
                log.info("migration.run.gate", gate="hierarchy", migration_id=migration_id)
                return {
                    "status": "gate",
                    "gate_type": "hierarchy",
                    "payload": gate_payload,
                    "migration_id": migration_id,
                    "message": (
                        "Gate 2 (Hierarchy): Review detected FK relationships "
                        "(sites → locations → assets → work orders). "
                        "Call submit_hierarchy(migration_id, approve_all=True) to confirm all, "
                        "or pass approved_hierarchies / corrections for adjustments."
                    ),
                }

            # Unknown gate — surface it for manual handling
            log.warning("migration.run.unknown_gate", gate_type=gate_type, migration_id=migration_id)
            return {
                "status": "gate",
                "gate_type": gate_type,
                "payload": gate_payload,
                "migration_id": migration_id,
                "message": f"Unknown gate type '{gate_type}'. Inspect the payload and respond manually.",
            }

        # Still running — continue polling
        log.debug("migration.run.polling", migration_id=migration_id, status=raw_status)

    return {
        "status": "failed",
        "error": f"Migration {migration_id} timed out after {max_polls * 2}s",
        "migration_id": migration_id,
    }


# ── Tools 3–5 — Gate submission ───────────────────────────────────────────────

@tool
async def submit_pk_approval(
    migration_id: str,
    pk_overrides: dict | None = None,
) -> dict:
    """Submit Gate A — primary-key approval, the first gate a migration pauses at.

    Call this when run_migration returns gate_type == 'pk_approval', then call
    run_migration(migration_id) again to continue.

    Args:
        migration_id: UUID of the migration run.
        pk_overrides: {table: column | [columns]} to override the detected primary key.
            Empty (the default) accepts what was detected, which is the right answer for
            an export whose key column is unambiguous — wo_code, asset_code, ppm_code.

    Returns dict with status.
    """
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/pk-approval",
            json={"pk_overrides": pk_overrides or {}},
        )
        log.info(
            "migration.submit_pk_approval",
            migration_id=migration_id,
            overrides=len(pk_overrides or {}),
        )
        return result
    except Exception as exc:
        return _err(exc, "submit_pk_approval")


@tool
async def submit_unique_table_approval(migration_id: str, approved: bool = True) -> dict:
    """Submit Gate A step 2 — confirm which tables are unique entities.

    Call when run_migration returns gate_type == 'unique_table_approval'.
    """
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/unique-table-approval",
            json={"approved": approved},
        )
        log.info("migration.submit_unique_table", migration_id=migration_id)
        return result
    except Exception as exc:
        return _err(exc, "submit_unique_table_approval")


@tool
async def submit_classification_approval(
    migration_id: str,
    rejected_groups: list | None = None,
    verdict_overrides: dict | None = None,
) -> dict:
    """Submit the classification gate — FK and shared-entity assignments.

    Empty lists accept the detected classification.
    """
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/classification-approval",
            json={
                "rejected_groups": rejected_groups or [],
                "verdict_overrides": verdict_overrides or {},
            },
        )
        log.info("migration.submit_classification", migration_id=migration_id)
        return result
    except Exception as exc:
        return _err(exc, "submit_classification_approval")


@tool
async def submit_column_mapping_approval(
    migration_id: str, overrides: dict | None = None
) -> dict:
    """Submit the column-mapping gate — destination columns for each source column.

    Empty overrides accept the matched destinations.
    """
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/column-mapping-approval",
            json={"overrides": overrides or {}},
        )
        log.info("migration.submit_column_mapping", migration_id=migration_id)
        return result
    except Exception as exc:
        return _err(exc, "submit_column_mapping_approval")


@tool
async def submit_final_gate(migration_id: str, confirm: bool = True) -> dict:
    """Submit Gate 3 — final confirmation before the data is written."""
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/final",
            json={"confirm": confirm, "approved": confirm},
        )
        log.info("migration.submit_final", migration_id=migration_id)
        return result
    except Exception as exc:
        return _err(exc, "submit_final_gate")


@tool
async def submit_pre_semantic(
    migration_id: str,
    approve_all: bool = True,
    decisions: dict | None = None,
) -> dict:
    """Submit decisions for Gate 0 — Pre-Semantic T1 mapping review.

    Call this after run_migration returns gate_type == 'pre_semantic'.
    Then call run_migration(migration_id) again to continue the pipeline.

    Args:
        migration_id: UUID of the migration run.
        approve_all: If True, accept all T1 mappings as-is. Default True.
        decisions: Per-table field decisions (only when approve_all=False):
            {
              "table_name": [
                {"source_field": "RAW_COL", "decision": "approve" | "semantic"}
              ]
            }
            "approve" keeps the T1 mapping. "semantic" reroutes to embedding phase.

    Returns dict with status and decisions_processed count.
    """
    # Important: do not send an empty "decisions" object when approve_all=True.
    # The downstream resume path expects a non-empty command payload.
    req_payload: dict = {"approve_all": approve_all}
    if not approve_all:
        req_payload["decisions"] = decisions or {}
    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/pre-semantic",
            json=req_payload,
        )
        log.info("migration.submit_pre_semantic", migration_id=migration_id, approve_all=approve_all)
        return result
    except Exception as exc:
        return _err(exc, "submit_pre_semantic")


@tool
async def submit_field_mapping(
    migration_id: str,
    approve_all: bool = True,
    flagged_decisions: dict | None = None,
    unmapped_decisions: dict | None = None,
) -> dict:
    """Submit decisions for Gate 1 — Field mapping approval.

    Call this after run_migration returns gate_type == 'field_mapping'.
    Then call run_migration(migration_id) again to continue the pipeline.

    Args:
        migration_id: UUID of the migration run.
        approve_all: If True, accept all flagged mappings and route unmapped fields
            to raw_metadata automatically. Default True.
        flagged_decisions: Per-table decisions for low-confidence fields:
            {
              "table_name": [
                {
                  "action": "accept" | "reject" | "override",
                  "source_field": "RAW_COL",
                  "target_field": "canonical_name",  # required for override
                  "rationale": "reason"
                }
              ]
            }
        unmapped_decisions: Per-table decisions for fully unmapped fields:
            {
              "table_name": [
                {
                  "action": "custom" | "raw_metadata" | "skip",
                  "source_field": "RAW_COL",
                  "target_table": "...",       # for custom only
                  "custom_column_name": "...", # for custom only
                  "data_type": "text"          # for custom only
                }
              ]
            }

    Returns dict with status and decisions_processed count.
    """
    req_payload: dict = {"approve_all": approve_all}
    if not approve_all:
        decisions_payload: dict = {}
        if flagged_decisions:
            decisions_payload["flagged"] = flagged_decisions
        if unmapped_decisions:
            decisions_payload["unmapped"] = unmapped_decisions
        req_payload["decisions"] = decisions_payload

    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/field-mapping",
            json=req_payload,
        )
        log.info("migration.submit_field_mapping", migration_id=migration_id, approve_all=approve_all)
        return result
    except Exception as exc:
        return _err(exc, "submit_field_mapping")


@tool
async def submit_hierarchy(
    migration_id: str,
    approve_all: bool = True,
    approved_hierarchies: list | None = None,
    corrections: list | None = None,
) -> dict:
    """Submit decisions for Gate 2 — Hierarchy verification.

    Call this after run_migration returns gate_type == 'hierarchy'.
    Then call run_migration(migration_id) again to continue the pipeline.

    Args:
        migration_id: UUID of the migration run.
        approve_all: If True, confirm all detected FK hierarchies as-is. Default True.
        approved_hierarchies: List of hierarchy objects to confirm (copy from
            pending_gate_payload.confirmed_hierarchies when approve_all=False).
        corrections: List of hierarchy corrections:
            [{"action": "confirm" | "reject" | "modify", ...}]

    Returns dict with status and decisions_processed count.
    """
    payload: dict = {}
    if not approve_all:
        payload = {
            "approved_hierarchies": approved_hierarchies or [],
            "corrections": corrections or [],
        }

    try:
        result = await _post(
            f"/api/migration/{migration_id}/gate/hierarchy",
            json={"approve_all": approve_all, **payload},
        )
        log.info("migration.submit_hierarchy", migration_id=migration_id, approve_all=approve_all)
        return result
    except Exception as exc:
        return _err(exc, "submit_hierarchy")


# ── Tools 6–8 — Status / audit / list ─────────────────────────────────────────

@tool
async def get_migration_status(migration_id: str) -> dict:
    """Get the current status and progress of a migration run (one-off check).

    Returns status, progress_pct, current_step, pending_gate_type, pending_gate_payload.
    Status values: running | awaiting_review | step_paused | complete | failed | cancelled

    Use this for ad-hoc status checks. To drive the pipeline automatically,
    use run_migration(migration_id) instead.

    Args:
        migration_id: UUID returned by start_migration.
    """
    try:
        return await _get(f"/api/migration/{migration_id}/status")
    except Exception as exc:
        return _err(exc, "status")


@tool
async def get_migration_mappings(migration_id: str) -> dict:
    """Get the complete field mapping audit trail for a finished migration.

    Returns all source_field → canonical_field mappings with confidence scores,
    tier (T1/T2/custom), and reviewer decisions. Only meaningful when
    status == 'complete'.

    Args:
        migration_id: UUID returned by start_migration.
    """
    try:
        return await _get(f"/api/migration/{migration_id}/mappings")
    except Exception as exc:
        return _err(exc, "get_mappings")


@tool
async def list_migrations(organization_id: str = "00000000-0000-0000-0000-000000000001") -> dict:
    """List all migration runs for an organization, most recent first.

    Useful to check past runs and their statuses without knowing specific migration IDs.

    Args:
        organization_id: UUID of the organization (default: test org).
    """
    try:
        async with httpx.AsyncClient(base_url=_BASE, timeout=_TIMEOUT_SHORT, follow_redirects=True) as client:
            resp = await client.get(
                "/api/migration",
                params={"organization_id": organization_id},
            )
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        return _err(exc, "list_migrations")
