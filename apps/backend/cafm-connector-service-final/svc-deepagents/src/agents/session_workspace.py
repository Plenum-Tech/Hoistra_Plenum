"""
Per-session workspace state for Single Door (Phases 1–2, 7).

Tracks ingestion, UDR mapping/hierarchy progress, batch IDs, and route metadata.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Normalized route intents (Phase 1)
ROUTE_UDR_INGEST = "udr_ingest_documents"
ROUTE_UDR_MAP = "udr_run_mapping_hierarchy"
ROUTE_WO_INTAKE = "wo_intake_or_create"
ROUTE_WO_CLARIFY = "wo_clarify_candidate"
ROUTE_GENERAL = "general_query"
ROUTE_FIIX_SYNC = "fiix_sync"
ROUTE_BULK_INGEST = "bulk_ingest"

_SESSION_STATE: dict[str, dict[str, Any]] = {}

CONVERSATION_MAX_TURNS = 24
CONVERSATION_TURN_MAX_CHARS = 4000
CONVERSATION_CONTEXT_TURNS = 10


def default_session_state() -> dict[str, Any]:
    return {
        "ingested_documents": 0,
        "last_flow_summary": "",
        "pending_wo_clarification": False,
        "pending_wo_text": "",
        "wo_clarification_confirmed": False,
        "fiix_ingestion_id": "",
        "pending_fiix_confirm": False,
        "pending_fiix_action": "",  # schema_mapping | ingestion
        "fiix_credentials": {
            "subdomain": "",
            "configured": False,
        },
        "schema_mapping_ids": [],
        "active_schema_mapping_id": "",
        "pending_schema_gate_confirm": False,
        "last_fiix_display_summary": "",
        "last_fiix_schema_comparison": None,
        "active_batch_id": "",
        "pending_batch_ids": [],
        "ingestion_mode": "",  # "" | "structured" | "unstructured" | "mixed"
        "mapping_status": "pending",  # pending | in_progress | complete
        "hierarchy_status": "pending",  # pending | in_progress | complete
        "migration_ids": [],  # schema-mapper jobs tied to this session (for live status sync)
        "migration_by_file": {},  # normalized filename -> migration_id (one job per uploaded file)
        "last_route_intent": ROUTE_GENERAL,
        "last_domain": "meta",
        "last_tool": "",
        "conversation_turns": [],  # [{role: user|assistant, content: str}]
    }


def get_session_state(session_id: str) -> dict[str, Any]:
    return _SESSION_STATE.setdefault(session_id, default_session_state())


def infer_saved_space(session_state: dict[str, Any]) -> str:
    """FM-facing saved space id for LHS indexing (work_orders, udr, schema, …)."""
    route = session_state.get("last_route_intent") or ROUTE_GENERAL
    domain = (session_state.get("last_domain") or "meta").lower()
    tool = (session_state.get("last_tool") or "").lower()
    route_map = {
        ROUTE_WO_INTAKE: "work_orders",
        ROUTE_WO_CLARIFY: "work_orders",
        ROUTE_UDR_INGEST: "udr",
        ROUTE_UDR_MAP: "udr",
        ROUTE_FIIX_SYNC: "schema",
        ROUTE_BULK_INGEST: "migration",
    }
    if route in route_map:
        return route_map[route]
    domain_map = {
        "wo_engine": "work_orders",
        "work_order": "work_orders",
        "doc_rag": "documents",
        "migration": "migration",
        "fiix": "schema",
        "udr": "udr",
        "compliance": "compliance",
    }
    if domain in domain_map:
        return domain_map[domain]
    if tool.startswith("start_fiix") or "schema_mapping" in tool:
        return "schema"
    if tool.startswith("start_migration") or tool == "run_migration":
        return "migration"
    if tool in ("index_document", "query_documents", "match_document_rows"):
        return "documents"
    return "general"


def workspace_snapshot(session_id: str) -> dict[str, Any]:
    """Public workspace view for API / UI status cards (Phase 7)."""
    s = get_session_state(session_id)
    ingested = workspace_has_ingestion(s)
    return {
        "session_id": session_id,
        "ingestion_complete": ingested,
        "documents_ingested_count": int(s.get("ingested_documents", 0)),
        "pending_batch_ids": list(s.get("pending_batch_ids") or []),
        "active_batch_id": s.get("active_batch_id") or "",
        "mapping_status": s.get("mapping_status") or "pending",
        "hierarchy_status": s.get("hierarchy_status") or "pending",
        "mapping_pending": ingested and s.get("mapping_status") != "complete",
        "hierarchy_pending": (
            ingested
            and s.get("mapping_status") == "complete"
            and s.get("hierarchy_status") != "complete"
        ),
        "wo_candidate_detected": bool(s.get("pending_wo_clarification")),
        "fiix_ingestion_id": s.get("fiix_ingestion_id") or "",
        "fiix_credentials_configured": bool(
            (s.get("fiix_credentials") or {}).get("configured")
        ),
        "fiix_subdomain": (s.get("fiix_credentials") or {}).get("subdomain") or "",
        "schema_mapping_ids": list(s.get("schema_mapping_ids") or []),
        "active_schema_mapping_id": s.get("active_schema_mapping_id") or "",
        "pending_schema_gate_confirm": bool(s.get("pending_schema_gate_confirm")),
        "last_route_intent": s.get("last_route_intent") or ROUTE_GENERAL,
        "last_domain": s.get("last_domain") or "meta",
        "last_tool": s.get("last_tool") or "",
        "saved_space": infer_saved_space(s),
        "last_flow_summary": s.get("last_flow_summary") or "",
        "migration_ids": resolve_session_migration_ids(session_id),
    }


def workspace_has_ingestion(session_state: dict[str, Any]) -> bool:
    return (
        int(session_state.get("ingested_documents", 0)) > 0
        or bool(session_state.get("fiix_ingestion_id"))
        or bool(session_state.get("schema_mapping_ids"))
    )


def set_pending_fiix_confirm(
    session_id: str,
    *,
    action: str = "schema_mapping",
) -> None:
    """User said yes / proceed — next Fiix step depends on credentials state."""
    state = get_session_state(session_id)
    state["pending_fiix_confirm"] = True
    state["pending_fiix_action"] = action if action in ("schema_mapping", "ingestion") else "schema_mapping"
    state["last_route_intent"] = ROUTE_FIIX_SYNC


def clear_pending_fiix_confirm(session_id: str) -> None:
    state = get_session_state(session_id)
    state["pending_fiix_confirm"] = False
    state["pending_fiix_action"] = ""


def record_fiix_ingestion_started(session_id: str, ingestion_id: str) -> None:
    state = get_session_state(session_id)
    state["ingested_documents"] = max(int(state.get("ingested_documents", 0)), 1)
    state["fiix_ingestion_id"] = ingestion_id
    state["last_route_intent"] = ROUTE_FIIX_SYNC


def fiix_credentials_payload(session_id: str) -> dict[str, str]:
    """Return stored Fiix credentials for API calls (session-scoped, not logged)."""
    creds = get_session_state(session_id).get("fiix_credentials") or {}
    return {
        "fiix_subdomain": str(creds.get("subdomain") or "").strip(),
        "fiix_app_key": str(creds.get("app_key") or "").strip(),
        "fiix_access_key": str(creds.get("access_key") or "").strip(),
        "fiix_secret_key": str(creds.get("secret_key") or "").strip(),
    }


def fiix_credentials_configured(session_id: str) -> bool:
    p = fiix_credentials_payload(session_id)
    return all(p.values())


def set_fiix_credentials(
    session_id: str,
    *,
    fiix_subdomain: str,
    fiix_app_key: str,
    fiix_access_key: str,
    fiix_secret_key: str,
) -> dict[str, str]:
    state = get_session_state(session_id)
    subdomain = fiix_subdomain.strip()
    state["fiix_credentials"] = {
        "subdomain": subdomain,
        "app_key": fiix_app_key.strip(),
        "access_key": fiix_access_key.strip(),
        "secret_key": fiix_secret_key.strip(),
        "configured": bool(
            subdomain and fiix_app_key.strip() and fiix_access_key.strip() and fiix_secret_key.strip()
        ),
    }
    state["last_route_intent"] = ROUTE_FIIX_SYNC
    return {
        "subdomain": subdomain,
        "configured": state["fiix_credentials"]["configured"],
    }


def record_schema_mapping_started(session_id: str, schema_mapping_id: str) -> None:
    state = get_session_state(session_id)
    ids: list[str] = list(state.get("schema_mapping_ids") or [])
    if schema_mapping_id and schema_mapping_id not in ids:
        ids.append(schema_mapping_id)
    state["schema_mapping_ids"] = ids
    if schema_mapping_id:
        state["active_schema_mapping_id"] = schema_mapping_id
        state["pending_schema_gate_confirm"] = True
    state["last_route_intent"] = ROUTE_FIIX_SYNC


def resolve_active_schema_mapping_id(session_id: str) -> str:
    state = get_session_state(session_id)
    active = str(state.get("active_schema_mapping_id") or "").strip()
    if active:
        return active
    ids = state.get("schema_mapping_ids") or []
    return str(ids[-1]).strip() if ids else ""


def set_pending_schema_gate_confirm(session_id: str, *, schema_mapping_id: str | None = None) -> None:
    state = get_session_state(session_id)
    state["pending_schema_gate_confirm"] = True
    if schema_mapping_id:
        state["active_schema_mapping_id"] = schema_mapping_id


def clear_pending_schema_gate_confirm(session_id: str) -> None:
    state = get_session_state(session_id)
    state["pending_schema_gate_confirm"] = False


def stash_fiix_schema_summary(
    session_id: str,
    *,
    display_summary: str = "",
    schema_comparison: dict[str, Any] | None = None,
) -> None:
    state = get_session_state(session_id)
    if display_summary:
        state["last_fiix_display_summary"] = display_summary
    if schema_comparison is not None:
        state["last_fiix_schema_comparison"] = schema_comparison


def record_conversation_turn(session_id: str, role: str, content: str) -> None:
    """Append a user or assistant turn for multi-turn solution continuity."""
    text = (content or "").strip()
    if not text:
        return
    if len(text) > CONVERSATION_TURN_MAX_CHARS:
        text = text[:CONVERSATION_TURN_MAX_CHARS] + "…"
    state = get_session_state(session_id)
    turns: list[dict[str, str]] = list(state.get("conversation_turns") or [])
    turns.append({"role": role if role in ("user", "assistant") else "user", "content": text})
    state["conversation_turns"] = turns[-CONVERSATION_MAX_TURNS:]


def build_conversation_context(session_id: str, *, max_turns: int = CONVERSATION_CONTEXT_TURNS) -> str:
    """Recent chat turns — survives shortcut paths and stateless WebSocket threads."""
    turns = list(get_session_state(session_id).get("conversation_turns") or [])
    recent = turns[-max_turns:] if max_turns > 0 else turns
    if not recent:
        return ""
    lines = [
        "## Recent conversation (continue this thread; do not ask for context already given)",
    ]
    for t in recent:
        label = "User" if t.get("role") == "user" else "Assistant"
        lines.append(f"**{label}:** {t.get('content', '')}")
    return "\n".join(lines)


def build_session_runtime_context(session_id: str) -> str:
    """Inject into every stateful turn so the LLM retains session facts."""
    s = get_session_state(session_id)
    ws_lines: list[str] = []
    sid = resolve_active_schema_mapping_id(session_id)
    if sid:
        ws_lines.append(f"- **active_schema_mapping_id:** `{sid}` (use for get_schema_mapping_status)")
    if s.get("schema_mapping_ids"):
        ws_lines.append(f"- **schema_mapping_ids (session):** {', '.join(s['schema_mapping_ids'])}")
    if s.get("pending_schema_gate_confirm"):
        ws_lines.append(
            "- **pending_schema_gate_confirm:** true — if the user replies yes/proceed/continue, "
            "call get_schema_mapping_status(active_schema_mapping_id) and guide the current gate; "
            "do not ask what they want to proceed with."
        )
    if s.get("pending_fiix_confirm"):
        ws_lines.append(f"- **pending_fiix_confirm:** action={s.get('pending_fiix_action') or 'schema_mapping'}")
    if (s.get("fiix_credentials") or {}).get("configured"):
        sub = (s.get("fiix_credentials") or {}).get("subdomain") or ""
        ws_lines.append(f"- **Fiix credentials:** configured ({sub})")
    if s.get("last_fiix_display_summary"):
        ws_lines.append("- **Last Fiix schema overview (show when discussing mapping):**")
        ws_lines.append(str(s["last_fiix_display_summary"]))
    mig_ids = resolve_session_migration_ids(session_id)
    if mig_ids:
        ws_lines.append(f"- **migration_ids (session):** {', '.join(mig_ids)}")
    conv = build_conversation_context(session_id)
    if not conv and not ws_lines:
        return ""
    parts: list[str] = []
    if conv:
        parts.append(conv)
    if ws_lines:
        parts.append("## Session workspace (retain across turns)\n" + "\n".join(ws_lines))
    return "\n\n".join(parts)


def record_batch_ingestion_complete(
    session_id: str,
    *,
    batch_id: str,
    succeeded_count: int,
) -> None:
    state = get_session_state(session_id)
    state["active_batch_id"] = batch_id
    batches: list[str] = list(state.get("pending_batch_ids") or [])
    if batch_id in batches:
        batches.remove(batch_id)
    state["pending_batch_ids"] = batches
    if succeeded_count > 0:
        state["ingested_documents"] = max(
            int(state.get("ingested_documents", 0)), succeeded_count
        )
        state["mapping_status"] = "in_progress"


def append_pending_batch(session_id: str, batch_id: str) -> None:
    state = get_session_state(session_id)
    state["active_batch_id"] = batch_id
    batches: list[str] = list(state.get("pending_batch_ids") or [])
    if batch_id not in batches:
        batches.append(batch_id)
    state["pending_batch_ids"] = batches
    state["last_route_intent"] = ROUTE_BULK_INGEST


def mark_documents_ingested(session_id: str, count: int, summary: str = "") -> None:
    state = get_session_state(session_id)
    state["ingested_documents"] = max(int(state.get("ingested_documents", 0)), count)
    if summary:
        state["last_flow_summary"] = summary
    state["last_route_intent"] = ROUTE_UDR_INGEST


def record_ingested_certificates(session_id: str, certificate_ids: list[str]) -> None:
    """Remember which certificates this turn created, so the next question can stay on them.

    Without this a follow-up like "is it expired?" runs the whole register: the user is asking
    about the five documents they just watched land, and gets an answer about all sixteen.
    """
    if not certificate_ids:
        return
    state = get_session_state(session_id)
    state["last_ingested_certificate_ids"] = [str(c) for c in certificate_ids if c]


def get_ingested_certificates(session_id: str) -> list[str]:
    """The certificates created by the most recent ingestion in this session."""
    state = get_session_state(session_id)
    ids = state.get("last_ingested_certificate_ids") or []
    return [str(c) for c in ids if c]


def clear_ingested_certificates(session_id: str) -> None:
    """Drop the ingestion scope once the user has moved on to a portfolio-wide question."""
    get_session_state(session_id).pop("last_ingested_certificate_ids", None)


def normalize_migration_file_key(filename: str) -> str:
    """Stable key per upload basename (strip orchestrator session prefix on saved paths)."""
    name = Path(str(filename or "").strip()).name.lower()
    if not name:
        return ""
    parts = name.split("_", 1)
    if len(parts) == 2 and len(parts[0]) == 36 and parts[0].count("-") >= 4:
        return parts[1]
    return name


def register_migration_file(
    session_id: str,
    migration_id: str,
    source_filename: str,
    content_key: str | None = None,
) -> None:
    """
    One schema-mapper migration per uploaded file.

    Multi-sheet Excel is a single file → one migration_id; sheets are tables inside Node 1.
    Keyed on ``content_key`` (a hash of the file DATA) when the caller provides one — so an edited
    file with the same name is a NEW migration and a renamed identical file reuses the existing
    one; falls back to the filename key when no content hash is available.
    """
    mid = str(migration_id or "").strip()
    key = str(content_key or "").strip() or normalize_migration_file_key(source_filename)
    if not mid or not key:
        register_migration_id(session_id, mid)
        return
    state = get_session_state(session_id)
    by_file: dict[str, str] = dict(state.get("migration_by_file") or {})
    if key in by_file:
        if by_file[key] != mid:
            log.info(
                "session_workspace.migration_file_ignored_duplicate",
                session_id=session_id,
                file_key=key,
                kept=by_file[key],
                ignored=migration_id,
            )
        return
    by_file[key] = mid
    state["migration_by_file"] = by_file
    register_migration_id(session_id, mid)


def get_migration_id_for_file(
    session_id: str, source_filename: str, content_key: str | None = None
) -> str | None:
    """Return existing migration for this file in the session, if any. Prefers the content-hash
    key (dedup by file DATA); falls back to the filename key when no hash is supplied."""
    key = str(content_key or "").strip() or normalize_migration_file_key(source_filename)
    if not key:
        return None
    state = get_session_state(session_id)
    return (state.get("migration_by_file") or {}).get(key)


def register_migration_id(session_id: str, migration_id: str) -> None:
    """Track a schema-mapper migration job for workspace status refresh."""
    mid = str(migration_id or "").strip()
    if not mid:
        return
    state = get_session_state(session_id)
    ids: list[str] = list(state.get("migration_ids") or [])
    if mid not in ids:
        ids.append(mid)
    state["migration_ids"] = ids
    set_ingestion_mode_structured(session_id)
    if state.get("mapping_status") == "pending":
        state["mapping_status"] = "in_progress"


def extract_start_migration_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Collect migration_id values only from start_migration tool results (ordered)."""
    ids: list[str] = []
    seen: set[str] = set()
    for tc in tool_calls:
        if tc.get("tool") != "start_migration":
            continue
        out = tc.get("output")
        if not isinstance(out, dict):
            continue
        mid = str(out.get("migration_id") or "").strip()
        if mid and mid not in seen:
            seen.add(mid)
            ids.append(mid)
    return ids


def resolve_session_migration_ids(
    session_id: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> list[str]:
    """
    Migration IDs for UI — prefer one id per uploaded file.

    Collapses duplicate start_migration calls (e.g. agent re-starting the same Excel).
    """
    state = get_session_state(session_id)
    by_file: dict[str, str] = dict(state.get("migration_by_file") or {})
    if by_file:
        seen: set[str] = set()
        ordered: list[str] = []
        for mid in by_file.values():
            if mid not in seen:
                seen.add(mid)
                ordered.append(mid)
        return ordered

    ids = extract_start_migration_ids(tool_calls or [])
    if len(ids) > 1:
        log.warning(
            "session_workspace.multiple_migrations_collapsed",
            session_id=session_id,
            count=len(ids),
            kept=ids[0],
        )
        return [ids[0]]
    return ids


def sync_migration_ids_from_tool_calls(
    session_id: str, tool_calls: list[dict[str, Any]]
) -> None:
    """Register migration IDs from start_migration only (not run_migration polls)."""
    for tc in tool_calls:
        if tc.get("tool") != "start_migration":
            continue
        out = tc.get("output")
        if not isinstance(out, dict):
            continue
        mid = str(out.get("migration_id") or "").strip()
        if not mid:
            continue
        inp = tc.get("input") if isinstance(tc.get("input"), dict) else {}
        fname = str(inp.get("file_path") or "")
        if fname:
            register_migration_file(session_id, mid, fname)
        else:
            register_migration_id(session_id, mid)


def phases_from_migration_status(migration: dict[str, Any]) -> tuple[str, str] | None:
    """
    Derive workspace mapping/hierarchy phases from schema-mapper /status.

    Returns None when status cannot be interpreted (errors / terminal failures).
    """
    if migration.get("error"):
        return None
    status = str(migration.get("status") or "").lower()
    step = int(migration.get("current_step") or 0)
    gate = str(migration.get("pending_gate_type") or "").lower()

    if status == "complete":
        return "complete", "complete"
    if status in ("failed", "cancelled", "ddl_failed"):
        return None

    if "hierarchy" in gate and "field" not in gate:
        return "complete", "in_progress"
    if gate in ("pre_semantic", "field_mapping") or "pre_semantic" in gate or "field_mapping" in gate:
        return "in_progress", "pending"
    if gate.startswith("step_") and any(
        token in gate for token in ("1_", "2_", "3_", "4_", "5_", "ingest", "deterministic", "semantic")
    ):
        return "in_progress", "pending"
    if gate in ("final", "write") or "write" in gate:
        return "complete", "complete"

    if step >= 9:
        return "complete", "complete"
    if step >= 7:
        return "complete", "in_progress"
    if step >= 6:
        return "complete", "in_progress"
    if step >= 1:
        return "in_progress", "pending"
    if status in ("running", "awaiting_review", "step_paused", "paused"):
        return "in_progress", "pending"
    return None


def _phase_rank(mapping: str, hierarchy: str) -> int:
    """Lower rank = earlier pipeline stage (used when multiple migrations exist)."""
    if mapping != "complete":
        return 1 if mapping == "pending" else 2
    if hierarchy != "complete":
        return 3 if hierarchy == "pending" else 4
    return 5


async def refresh_workspace_from_migrations(session_id: str) -> None:
    """Poll schema-mapper and align mapping/hierarchy pills with real job progress."""
    state = get_session_state(session_id)
    ids: list[str] = list(state.get("migration_ids") or [])
    if not ids:
        return

    from .migration_agent import get_migration_status

    best: tuple[str, str] | None = None
    best_rank = 999
    for mid in ids:
        try:
            st = await get_migration_status.ainvoke({"migration_id": mid})
        except Exception as exc:
            log.warning(
                "session_workspace.migration_status.failed",
                session_id=session_id,
                migration_id=mid,
                error=str(exc)[:200],
            )
            continue
        phases = phases_from_migration_status(st if isinstance(st, dict) else {})
        if not phases:
            continue
        rank = _phase_rank(phases[0], phases[1])
        if rank < best_rank:
            best_rank = rank
            best = phases

    if best:
        state["mapping_status"] = best[0]
        state["hierarchy_status"] = best[1]


def record_mapping_complete(session_id: str) -> None:
    state = get_session_state(session_id)
    state["mapping_status"] = "complete"
    if state.get("hierarchy_status") == "pending":
        state["hierarchy_status"] = "in_progress"


def record_hierarchy_complete(session_id: str) -> None:
    state = get_session_state(session_id)
    state["hierarchy_status"] = "complete"


def record_unstructured_register_ready(session_id: str) -> None:
    """
  PDF/TXT/image ingest uses Doc RAG vector register — not migration field mapping.
  Marks mapping/hierarchy complete so UDR gates do not block doc-rag follow-up.
  """
    state = get_session_state(session_id)
    mode = state.get("ingestion_mode") or ""
    if mode == "structured":
        state["ingestion_mode"] = "mixed"
    else:
        state["ingestion_mode"] = "unstructured"
    state["mapping_status"] = "complete"
    state["hierarchy_status"] = "complete"


def set_ingestion_mode_structured(session_id: str) -> None:
    state = get_session_state(session_id)
    mode = state.get("ingestion_mode") or ""
    if mode == "unstructured":
        state["ingestion_mode"] = "mixed"
    else:
        state["ingestion_mode"] = "structured"


def set_route_metadata(
    session_id: str,
    *,
    route_intent: str,
    domain: str = "meta",
    tool: str = "",
    next_step_prompt: str | None = None,
) -> dict[str, Any]:
    state = get_session_state(session_id)
    state["last_route_intent"] = route_intent
    state["last_domain"] = domain
    if tool:
        state["last_tool"] = tool
    meta: dict[str, Any] = {
        "route_intent": route_intent,
        "selected_domain": domain,
        "selected_tool": tool or state.get("last_tool") or "",
    }
    if next_step_prompt:
        meta["next_step_prompt"] = next_step_prompt
    log.info(
        "session_workspace.route",
        session_id=session_id,
        route_intent=route_intent,
        domain=domain,
        tool=tool,
    )
    return meta


FORCED_ROUTE_PREFIX = "plenum_forced_route="

_VALID_FORCED_ROUTES = frozenset(
    {
        ROUTE_UDR_INGEST,
        ROUTE_UDR_MAP,
        ROUTE_WO_INTAKE,
        ROUTE_WO_CLARIFY,
        ROUTE_GENERAL,
        ROUTE_FIIX_SYNC,
        ROUTE_BULK_INGEST,
    }
)


def parse_forced_route_intent(extra_context: str | None) -> str | None:
    """UI pins send plenum_forced_route=<intent> in workflow context to bypass misrouting."""
    if not extra_context:
        return None
    for line in extra_context.splitlines():
        stripped = line.strip()
        if not stripped.lower().startswith(FORCED_ROUTE_PREFIX):
            continue
        intent = stripped.split("=", 1)[-1].strip().lower()
        if intent in _VALID_FORCED_ROUTES:
            return intent
    return None


def resolve_route_intent(
    msg_l: str,
    session_state: dict[str, Any],
    extra_context: str | None = None,
) -> str:
    forced = parse_forced_route_intent(extra_context)
    if forced:
        return forced
    return classify_route_intent(msg_l, session_state)


# ── Top-level UDR intent (Single Door) ──────────────────────────────────────────
# The SDO must invoke the UDR from EITHER register of language. A technical user says
# "data migration / create database / data unification"; a non-technical FM says
# "centralize my assets / unify my workspace / knowledge centralization". Both express
# ONE canonical intent — bring the org's data into the Unified Data Register — and both
# resolve to the existing UDR routes (ingest vs run mapping/hierarchy), so every
# downstream guard and the ingest-first prompt are reused unchanged.
UDR_TECHNICAL_TERMS: tuple[str, ...] = (
    "unified data register",
    "unified database",
    "unified data",
    "create database",
    "create a database",
    "database creation",
    "data unification",
    "database unification",
    "unify data",
    "unify the data",
    "unify database",
    "unify my data",
    "data migration",
    "migrate data",
    "migrate the data",
    "migrate my data",
    "udr creation",
    "create udr",
)
UDR_NONTECHNICAL_TERMS: tuple[str, ...] = (
    "workspace unification",
    "workspace centralization",
    "workspace centralisation",
    "centralize workspace",
    "centralise workspace",
    "knowledge unification",
    "knowledge centralization",
    "knowledge centralisation",
    "centralize knowledge",
    "centralise knowledge",
    "data connection",
    "connect my data",
    "maintenance centralization",
    "maintenance centralisation",
    "centralize maintenance",
    "asset centralization",
    "asset centralisation",
    "centralize assets",
    "centralise assets",
    "centralized asset management",
    "centralised asset management",
    "work centralization",
    "work centralisation",
    "centralize work",
    "centralized work management",
    "centralised work management",
)
UDR_UNIFY_TERMS: tuple[str, ...] = UDR_TECHNICAL_TERMS + UDR_NONTECHNICAL_TERMS

# Phrasings that mean the user is HANDING OVER / connecting data (→ ingest) rather than
# asking to RUN the register over already-present data (→ mapping/hierarchy).
_UDR_INGEST_CUES: tuple[str, ...] = (
    "ingest",
    "upload",
    "import",
    "connect",
    "attach",
    "add document",
    "add the document",
    "add files",
    "add the files",
)


def score_udr_intent(msg_l: str) -> float:
    """Confidence 0..1 that a message is a top-level UDR ('unify / centralize my data')
    intent, for paraphrases not in :data:`UDR_UNIFY_TERMS`. Work-order phrasings score 0
    so an FM 'create work order' never mis-routes to the UDR."""
    if "work order" in msg_l or "create wo" in msg_l or "wo-" in msg_l:
        return 0.0
    if "unified data register" in msg_l or " udr" in f" {msg_l}":
        return 1.0
    verbs = (
        "unify",
        "unification",
        "centralize",
        "centralise",
        "centralization",
        "centralisation",
        "consolidate",
        "consolidation",
        "migrate",
        "migration",
        "integrate",
        "integration",
    )
    nouns = (
        "data",
        "database",
        "workspace",
        "knowledge",
        "records",
        "operations",
        "asset",
        "assets",
        "maintenance",
        "register",
        "stack",
    )
    has_verb = any(v in msg_l for v in verbs)
    has_noun = any(n in msg_l for n in nouns)
    score = 0.0
    if has_verb and has_noun:
        score += 0.7
    elif has_verb:
        score += 0.4
    if any(
        p in msg_l
        for p in ("create database", "create a database", "build a database", "database creation")
    ):
        score += 0.6
    return min(score, 1.0)


def classify_route_intent(msg_l: str, session_state: dict[str, Any]) -> str:
    if any(
        t in msg_l
        for t in (
            "re-run mapping",
            "rerun mapping",
            "re-run hierarchy",
            "rerun hierarchy",
            "re-run deterministic",
            "rerun deterministic",
        )
    ):
        return ROUTE_UDR_MAP
    if any(
        t in msg_l
        for t in (
            "run udr",
            "run mapping",
            "run hierarchy",
            "universal data register",
        )
    ):
        if "ingest" in msg_l or "upload" in msg_l:
            return ROUTE_UDR_INGEST
        return ROUTE_UDR_MAP
    # Top-level UDR intent — both technical and non-technical phrasings. Skip when the
    # message is about Fiix (handled by the Fiix branch below) so "connect Fiix" is not
    # swallowed as a generic "data connection".
    if "fiix" not in msg_l and (
        any(t in msg_l for t in UDR_UNIFY_TERMS) or score_udr_intent(msg_l) >= 0.6
    ):
        if any(c in msg_l for c in _UDR_INGEST_CUES):
            return ROUTE_UDR_INGEST
        return ROUTE_UDR_MAP
    if "fiix" in msg_l and any(
        t in msg_l
        for t in (
            "sync",
            "pull",
            "connect",
            "ingest",
            "schema",
            "mapping",
            "live",
            "cmms",
            "credential",
            "subdomain",
        )
    ):
        return ROUTE_FIIX_SYNC
    if "fiix" in msg_l:
        return ROUTE_FIIX_SYNC
    if sum(1 for k in ("subdomain", "app key", "access key", "secret key") if k in msg_l) >= 2:
        return ROUTE_FIIX_SYNC
    if "work order" in msg_l or "create wo" in msg_l:
        return ROUTE_WO_INTAKE
    if session_state.get("pending_wo_clarification"):
        return ROUTE_WO_CLARIFY
    if score_work_request(msg_l) >= 0.4:
        return ROUTE_WO_CLARIFY
    return ROUTE_GENERAL


def score_work_request(msg_l: str) -> float:
    """Phase 3 — confidence score 0..1 for implicit work-request detection."""
    if "work order" in msg_l or "wo-" in msg_l:
        return 0.0
    high = (
        "leak",
        "broken",
        "not working",
        "malfunction",
        "urgent",
        "critical",
        "hvac",
        "chiller",
        "pump",
        "generator",
        "elevator",
        "alarm",
        "fault",
        "failure",
        "tripping",
        "offline",
        "no cooling",
        "smoke",
        "flood",
    )
    medium = ("repair", "fix", "maintenance", "inspect", "noisy", "vibration", "temperature")
    score = 0.0
    for t in high:
        if t in msg_l:
            score += 0.35
    for t in medium:
        if t in msg_l:
            score += 0.15
    if any(t in msg_l for t in ("please send", "need technician", "onsite", "asap", "today")):
        score += 0.2
    return min(score, 1.0)


def work_request_confidence_band(msg_l: str) -> str:
    score = score_work_request(msg_l)
    if score >= 0.65:
        return "high"
    if score >= 0.4:
        return "medium"
    return "low"


def extract_ingested_schema_mapping_ids(tool_calls: list[dict[str, Any]]) -> list[str]:
    """Collect schema_mapping_id values from Fiix/schema mapper tool outputs."""
    ids: list[str] = []
    schema_tools = {
        "start_schema_mapping",
        "start_fiix_schema_mapping",
        "continue_schema_mapping_gate",
        "get_schema_mapping_status",
    }
    for tc in tool_calls:
        if tc.get("tool") not in schema_tools:
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
        sid = str(out.get("schema_mapping_id") or "").strip()
        if not sid and tc.get("tool") == "continue_schema_mapping_gate":
            status = out.get("status")
            if isinstance(status, dict):
                sid = str(status.get("schema_mapping_id") or "").strip()
        if sid and sid not in ids:
            ids.append(sid)
    return ids


def workflow_stream_completion_payload(
    session_id: str,
    *,
    answer: str,
    tool_calls: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Enrich WebSocket workflow_completed with REST-parity fields for the orchestrator UI."""
    tcs = tool_calls or []
    if tcs:
        sync_schema_mapping_from_tool_calls(session_id, tcs)
    schema_ids = extract_ingested_schema_mapping_ids(tcs)
    ws = workspace_snapshot(session_id)
    active = str(ws.get("active_schema_mapping_id") or "").strip()
    if active and active not in schema_ids:
        schema_ids.append(active)
    for sid in ws.get("schema_mapping_ids") or []:
        s = str(sid).strip()
        if s and s not in schema_ids:
            schema_ids.append(s)
    return {
        "type": "workflow_completed",
        "answer": answer,
        "session_id": session_id,
        "tool_calls": tcs,
        "workspace_status": ws,
        "ingested_schema_mapping_ids": schema_ids,
    }


def sync_schema_mapping_from_tool_calls(
    session_id: str, tool_calls: list[dict[str, Any]]
) -> None:
    """Persist schema_mapping_id from agent tool results into session workspace."""
    for tc in tool_calls:
        if tc.get("tool") not in ("start_schema_mapping", "start_fiix_schema_mapping"):
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
        sid = str(out.get("schema_mapping_id") or "").strip()
        if sid:
            record_schema_mapping_started(session_id, sid)
            if out.get("pending_schema_gate_confirm"):
                set_pending_schema_gate_confirm(session_id, schema_mapping_id=sid)


def attach_route_to_result(
    result: dict[str, Any],
    session_id: str,
    *,
    intent: str | None = None,
    domain: str | None = None,
    tool: str | None = None,
    next_step_prompt: str | None = None,
) -> dict[str, Any]:
    state = get_session_state(session_id)
    tool_calls = result.get("tool_calls") or []
    if tool_calls:
        sync_schema_mapping_from_tool_calls(session_id, tool_calls)
    if tool and not domain:
        domain = "meta"
    if tool_calls and not tool:
        last = tool_calls[-1]
        tool = str(last.get("tool") or "")
    if not intent:
        intent = classify_route_intent(
            " ".join((result.get("answer") or "").lower().split()), state
        )
    meta = set_route_metadata(
        session_id,
        route_intent=intent or state.get("last_route_intent") or ROUTE_GENERAL,
        domain=domain or state.get("last_domain") or "meta",
        tool=tool or "",
        next_step_prompt=next_step_prompt,
    )
    result["route_metadata"] = meta
    result["workspace_status"] = workspace_snapshot(session_id)
    answer = str(result.get("answer") or "").strip()
    if answer:
        record_conversation_turn(session_id, "assistant", answer)
    return result
