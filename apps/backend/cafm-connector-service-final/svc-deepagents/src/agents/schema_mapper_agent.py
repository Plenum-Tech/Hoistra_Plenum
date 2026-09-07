"""
Schema Mapper agent tools — same HITL gates as the Schema Mapper UI / DeepAgentSchemaPanel.

Pipeline (Fiix or upload):
  Node 0 canonical → Node 1 ingest → Node 2 deterministic → Gate pre-semantic →
  semantic → Gate field mapping → hierarchy → Gate hierarchy → output → Gate artifacts
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from .meta_tools import get_session_context

log = structlog.get_logger(__name__)

_BASE = settings.migration_base_url
_TIMEOUT = 120.0


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = (exc.response.text or "")[:400]
        return {"error": body, "status_code": exc.response.status_code}
    return {"error": str(exc)[:300]}


def _resolve_schema_mapping_id(schema_mapping_id: str | None) -> str:
    explicit = (schema_mapping_id or "").strip()
    if explicit:
        return explicit
    sid = get_session_context() or ""
    if not sid:
        return ""
    from .session_workspace import resolve_active_schema_mapping_id

    return resolve_active_schema_mapping_id(sid)


def _normalize_gate_type(raw: str | None) -> str:
    g = (raw or "").strip().lower()
    if "pre" in g and "semantic" in g:
        return "pre_semantic"
    if "field" in g and "map" in g:
        return "field_mapping"
    if "hier" in g:
        return "hierarchy"
    if "artifact" in g:
        return "artifacts_review"
    return g


def _build_pre_semantic_body(payload: dict[str, Any]) -> dict[str, Any]:
    items_by_table = payload.get("items_by_table") or {}
    decisions: list[dict[str, str]] = []
    if isinstance(items_by_table, dict):
        for tbl, items in items_by_table.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                decisions.append(
                    {
                        "source_table": str(tbl),
                        "source_field": str(item.get("source_field") or ""),
                        "decision": "approve",
                    }
                )
    return {"decisions": decisions}


def _build_field_mapping_body(payload: dict[str, Any]) -> dict[str, Any]:
    decisions: list[dict[str, Any]] = []
    for key in ("low_confidence_tier1", "low_confidence_tier2", "flagged"):
        by_table = payload.get(key)
        if not isinstance(by_table, dict):
            continue
        for tbl, items in by_table.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                sf = str(item.get("source_field") or "")
                if not sf:
                    continue
                tgt = str(item.get("suggested_target") or item.get("target_field") or "")
                decisions.append(
                    {
                        "action": "accept",
                        "source_field": sf,
                        "source_table": str(tbl),
                        **({"target_field": tgt} if tgt else {}),
                    }
                )
    unmapped = payload.get("unmapped_fields") or {}
    if isinstance(unmapped, dict):
        for tbl, items in unmapped.items():
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                sf = str(item.get("source_field") or "")
                if sf:
                    decisions.append(
                        {
                            "action": "skip",
                            "source_field": sf,
                            "source_table": str(tbl),
                        }
                    )
    return {"decisions": decisions}


def _build_hierarchy_body(payload: dict[str, Any]) -> dict[str, Any]:
    fks = payload.get("detected_fks") or []
    approved: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    if isinstance(fks, list):
        for fk in fks:
            if not isinstance(fk, dict):
                continue
            conf = float(fk.get("confidence") or 1.0)
            entry = {
                "source_table": fk.get("source_table"),
                "source_column": fk.get("source_column"),
                "target_table": fk.get("target_table"),
                "target_column": fk.get("target_column"),
                "confirmed": conf >= 0.7,
            }
            if conf >= 0.7:
                approved.append(entry)
            else:
                rejected.append(entry)
    return {"approved_hierarchies": approved, "rejected_hierarchies": rejected}


@tool
async def continue_schema_mapping_gate(
    schema_mapping_id: str | None = None,
) -> dict:
    """Submit the current Schema Mapper HITL gate with the same defaults as the UI (approve-all path).

    Call when the user says yes / proceed to continue gates in chat after start_fiix_schema_mapping.
    Processes **one** gate per invocation, then returns updated status.

    Args:
        schema_mapping_id: UUID from start_fiix_schema_mapping; uses session active id if omitted.
    """
    sm_id = _resolve_schema_mapping_id(schema_mapping_id)
    if not sm_id:
        return {
            "error": "No schema_mapping_id — start Fiix schema mapping first.",
            "action": "Call start_fiix_schema_mapping or pass schema_mapping_id.",
        }

    from .fiix_agent import get_schema_mapping_status

    status_out = await get_schema_mapping_status.ainvoke({"schema_mapping_id": sm_id})
    if not isinstance(status_out, dict) or status_out.get("error"):
        return status_out if isinstance(status_out, dict) else {"error": "status failed"}

    st = str(status_out.get("status") or "").lower()
    if st == "complete":
        from .session_workspace import clear_pending_schema_gate_confirm

        sid = get_session_context() or ""
        if sid:
            clear_pending_schema_gate_confirm(sid)
        return {
            **status_out,
            "message": "Schema mapping is complete. Open Schema Mapper UI for artifacts or start Fiix ingestion.",
        }

    progress = float(status_out.get("progress_pct") or 0)
    if st in ("ingest", "running", "processing", "pending", "advancing") and progress < 5.0:
        try:
            async with httpx.AsyncClient(
                base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                kick_resp = await client.post(f"/api/schema-mapping/{sm_id}/kick")
                if kick_resp.status_code < 400:
                    follow_up = await get_schema_mapping_status.ainvoke(
                        {"schema_mapping_id": sm_id}
                    )
                    if isinstance(follow_up, dict) and not follow_up.get("error"):
                        status_out = follow_up
                        st = str(follow_up.get("status") or "").lower()
                        progress = float(status_out.get("progress_pct") or 0)
        except Exception as exc:
            log.warning("schema_mapping.kick.failed", schema_mapping_id=sm_id, error=str(exc)[:200])

    if st == "step_paused":
        try:
            async with httpx.AsyncClient(
                base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True
            ) as client:
                resp = await client.post(f"/api/schema-mapping/{sm_id}/advance", json={})
                resp.raise_for_status()
                advance_result = resp.json()
        except Exception as exc:
            return _err(exc, "advance_step")
        follow_up = await get_schema_mapping_status.ainvoke({"schema_mapping_id": sm_id})
        comparison = (follow_up or {}).get("schema_comparison") if isinstance(follow_up, dict) else {}
        display = (follow_up or {}).get("display_summary") or (
            comparison.get("markdown") if isinstance(comparison, dict) else ""
        )
        step_key = status_out.get("pending_gate_type") or advance_result.get("message") or "step"
        return {
            "schema_mapping_id": sm_id,
            "gate_submitted": None,
            "advance_result": advance_result,
            "status": follow_up,
            "display_summary": display,
            "message": (
                f"Advanced past **{step_key}**. "
                + (
                    f"Current status: {(follow_up or {}).get('status')} "
                    f"({(follow_up or {}).get('progress_pct', 0)}%). "
                    f"Next gate: {(follow_up or {}).get('pending_gate_type')}."
                    if isinstance(follow_up, dict)
                    else "Check the Schema tab for the next step."
                )
            ),
        }

    if st != "awaiting_review":
        cn = status_out.get("current_node")
        node_hint = ""
        if cn is not None:
            nodes = status_out.get("nodes") or []
            name = None
            if isinstance(nodes, list):
                for n in nodes:
                    if isinstance(n, dict) and n.get("node_id") == cn:
                        name = n.get("node_name")
                        break
            node_hint = f" Current step: **{name or f'node {cn}'}**."
        return {
            **status_out,
            "message": (
                f"Pipeline status is **{st}** ({status_out.get('progress_pct', 0)}%)."
                f"{node_hint} "
                "Open the **Schema** tab in the right rail (refreshes every few seconds). "
                "When status is **step_paused**, reply **yes** to advance; "
                "when **awaiting_review**, reply **yes** to submit the current gate."
            ),
        }

    gate = _normalize_gate_type(str(status_out.get("pending_gate_type") or ""))
    payload = status_out.get("pending_gate_payload")
    if not isinstance(payload, dict):
        payload = {}

    path_by_gate = {
        "pre_semantic": ("/gate/pre-semantic", _build_pre_semantic_body),
        "field_mapping": ("/gate/field-mapping", _build_field_mapping_body),
        "hierarchy": ("/gate/hierarchy", _build_hierarchy_body),
    }

    if gate == "artifacts_review":
        name = str(payload.get("suggested_schema_name") or "fiix_mapped_schema").strip()
        body = {"new_schema_name": name[:63]}
        path = "/gate/artifacts-review"
    elif gate in path_by_gate:
        path, builder = path_by_gate[gate]
        body = builder(payload)
    else:
        return {
            **status_out,
            "message": (
                f"No automated submit for gate '{gate}'. "
                "Use the Schema tab in the right rail (same UI as Schema Mapper)."
            ),
        }

    try:
        async with httpx.AsyncClient(
            base_url=_BASE, timeout=_TIMEOUT, follow_redirects=True
        ) as client:
            resp = await client.post(
                f"/api/schema-mapping/{sm_id}{path}",
                json=body,
            )
            resp.raise_for_status()
            submit_result = resp.json()
    except Exception as exc:
        return _err(exc, f"submit_{gate}")

    log.info("schema_mapping.gate.submitted", schema_mapping_id=sm_id, gate=gate)
    follow_up = await get_schema_mapping_status.ainvoke({"schema_mapping_id": sm_id})
    comparison = (follow_up or {}).get("schema_comparison") if isinstance(follow_up, dict) else {}
    display = (follow_up or {}).get("display_summary") or (
        comparison.get("markdown") if isinstance(comparison, dict) else ""
    )
    return {
        "schema_mapping_id": sm_id,
        "gate_submitted": gate,
        "submit_result": submit_result,
        "status": follow_up,
        "display_summary": display,
        "message": (
            f"Submitted **{gate}** gate for `{sm_id}`. "
            + (
                f"Next gate: {(follow_up or {}).get('pending_gate_type')}."
                if isinstance(follow_up, dict) and follow_up.get("pending_gate_type")
                else "Pipeline advanced — check status or say yes again for the next gate."
            )
        ),
    }
