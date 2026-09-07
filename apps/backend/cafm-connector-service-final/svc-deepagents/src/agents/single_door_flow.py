from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import structlog

from .meta_tools import get_session_context, set_session_context
from .session_workspace import (
    record_ingested_certificates,
    record_hierarchy_complete,
    record_mapping_complete,
    record_unstructured_register_ready,
    register_migration_file,
    register_migration_id,
    set_ingestion_mode_structured,
)
from ..config import settings
from .doc_rag_agent import (
    get_document_metadata,
    index_document,
    list_doc_rag_db_tables,
    list_row_index_tables,
    match_document_to_rows,
    query_docs,
    semantic_search,
)
from .migration_agent import (
    run_migration,
    start_migration,
    start_migration_multi,
    submit_field_mapping,
    submit_hierarchy,
    submit_classification_approval,
    submit_column_mapping_approval,
    submit_final_gate,
    submit_pk_approval,
    submit_pre_semantic,
    submit_unique_table_approval,
)
from .contract_performance_single_door import (
    classify_contract_performance_doc,
    route_contract_performance_upload,
)
from .energy_single_door import (
    classify_energy_document,
    route_energy_upload,
)
from .compliance_single_door import (
    classify_compliance_certificate_doc,
    classify_compliance_certificate_hybrid,
    route_compliance_certificate_upload,
)

log = structlog.get_logger(__name__)

# How long a plain-RAG document will wait for vector embedding before proceeding without
# chunks. Row-matching degrades to empty; a compliance certificate needs only the
# document_id, which upload already returned.
_INDEX_WAIT_SECONDS = 45.0

# Detached "drive migration to its first gate" tasks kicked from the INTERACTIVE hand-off
# (ingest_structured_batch). Held so the event loop doesn't GC them mid-flight; each removes
# itself on completion. They are best-effort — the Migration panel also auto-advances the run.
_detached_drive_tasks: set[asyncio.Task] = set()

STRUCTURED_EXTS = {".csv", ".xlsx", ".xls", ".xlsm"}
SCHEMA_EXTS = {".yaml", ".yml", ".json"}
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".gif"}
DOCUMENT_EXTS = {".pdf", ".docx", ".doc", ".txt", *IMAGE_EXTS}

DEFAULT_DOC_RAG_QUERY = (
    "Summarize this document for CAFM: assets, locations, maintenance issues, "
    "dates, work order references, and recommended schema field mappings."
)


def document_type_hint(file_path: str) -> str:
    """Doc RAG document_type hint for uploads (auto | scan | image)."""
    path = Path(file_path)
    ext = path.suffix.lower()
    name_l = path.name.lower()
    if ext in {".tif", ".tiff"} or "scan" in name_l:
        return "scan"
    if ext in IMAGE_EXTS:
        return "image"
    return "auto"


@dataclass
class SingleDoorResult:
    summary_text: str
    tool_calls: list[dict[str, Any]]
    context_note: str
    step_summaries: list[str] = field(default_factory=list)
    match_report: str = ""
    # Phase 2 engines that owned extraction for this upload (Feature A/B/C).
    # Orchestrator uses this to bind ONLY that engine's tools on the next turn.
    detected_engines: list[str] = field(default_factory=list)


def _infer_source_tables(user_query: str | None, indexed_tables: list[str]) -> list[str | None]:
    """If the user names table(s), match only those; otherwise match all indexed tables."""
    if not indexed_tables:
        return [None]
    if not user_query:
        return [None]
    msg = user_query.lower()
    picked = [
        t
        for t in indexed_tables
        if t.lower() in msg or t.lower().replace("_", " ") in msg
    ]
    if picked:
        return picked
    return [None]


def format_matched_rows_report(match_result: dict[str, Any], *, max_rows: int = 20) -> str:
    """Human-readable report aligned with Doc RAG UI Match tab (RowCard details)."""
    if match_result.get("error"):
        return f"Row matching failed: {match_result.get('error')}"

    rows = match_result.get("matched_rows") or []
    if not isinstance(rows, list):
        rows = []

    lines = [
        "## Document → row matching",
        f"- Chunks analyzed: {match_result.get('total_chunks_analyzed', 0)}",
        f"- Unique rows matched: {match_result.get('unique_rows_matched', len(rows))}",
        f"- Latency: {match_result.get('latency_ms', 0)} ms",
    ]
    by_table = match_result.get("by_table") or {}
    if by_table:
        lines.append(
            "- By table: "
            + ", ".join(f"{k} ({v})" for k, v in sorted(by_table.items(), key=lambda x: -x[1]))
        )
    if match_result.get("source_table"):
        lines.append(f"- Filter: `{match_result.get('source_table')}`")

    if not rows:
        lines.append(
            "\nNo rows matched above threshold. Import CMMS tables into the row index "
            "(Doc RAG → Index tab) or lower the confidence threshold."
        )
        return "\n".join(lines)

    for row in rows[:max_rows]:
        if not isinstance(row, dict):
            continue
        table = row.get("source_table", "?")
        pk = row.get("row_pk", "?")
        conf = float(row.get("confidence") or 0)
        method = row.get("match_method", "")
        lines.append(f"\n### {table} · `{pk}` — **{conf:.0%}** ({method})")

        meta_fields = row.get("matched_metadata_fields") or []
        if meta_fields:
            lines.append(f"- Matched columns: {', '.join(str(f) for f in meta_fields)}")

        details = row.get("match_details") or {}
        if isinstance(details, dict) and details:
            lines.append(
                "- Scores: "
                f"semantic {float(details.get('semantic_score', 0)):.3f}, "
                f"bm25 {float(details.get('bm25_overlap', details.get('bm25_score', 0))):.3f}, "
                f"metadata {float(details.get('metadata_overlap', details.get('metadata_score', 0))):.3f}"
            )

        row_data = row.get("row_data") or {}
        if isinstance(row_data, dict) and row_data:
            lines.append("- Row fields:")
            for key, val in list(row_data.items())[:12]:
                lines.append(f"  - `{key}`: {val}")

        evidence = row.get("evidence")
        if evidence:
            lines.append(f"- Evidence: {str(evidence)[:300]}")

        chunk_matches = row.get("chunk_matches") or []
        if chunk_matches:
            lines.append("- Chunk matches:")
            for cm in chunk_matches[:5]:
                if not isinstance(cm, dict):
                    continue
                fields = cm.get("matched_fields") or []
                lines.append(
                    f"  - chunk #{cm.get('chunk_index')} "
                    f"conf {float(cm.get('confidence', 0)):.0%} · "
                    f"sem {float(cm.get('semantic_score', 0)):.3f} · "
                    f"bm25 {float(cm.get('bm25_score', 0)):.3f} · "
                    f"meta {float(cm.get('metadata_score', 0)):.3f}"
                    + (f" · fields [{', '.join(str(f) for f in fields)}]" if fields else "")
                )
                preview = cm.get("chunk_text_preview")
                if preview:
                    lines.append(f"    > {str(preview)[:200]}")

    if len(rows) > max_rows:
        lines.append(f"\n_(Showing top {max_rows} of {len(rows)} matched rows.)_")

    return "\n".join(lines)


async def run_document_rag_pipeline(
    *,
    file_path: str,
    index_result: dict[str, Any],
    user_query: str | None = None,
    skip_row_match: bool = False,
) -> tuple[list[dict[str, Any]], str]:
    """
    Deterministic Doc RAG follow-up (mirrors dedicated Doc RAG UI pipeline):
      index → verify → match rows to CMMS tables → grounded query → semantic evidence.
    """
    tool_calls: list[dict[str, Any]] = []
    match_reports: list[str] = []
    query = (user_query or "").strip() or DEFAULT_DOC_RAG_QUERY
    doc_id = str(
        index_result.get("document_id")
        or index_result.get("doc_id")
        or index_result.get("id")
        or ""
    )
    threshold = settings.doc_match_confidence_threshold
    max_rows = settings.doc_match_max_rows_in_report

    if doc_id:
        meta = await get_document_metadata.ainvoke({"document_id": doc_id})
        tool_calls.append(
            {
                "tool": "get_document_metadata",
                "input": {"document_id": doc_id},
                "output": meta,
            }
        )

        indexed_raw = await list_row_index_tables.ainvoke({})
        indexed_tables: list[str] = []
        if isinstance(indexed_raw, list):
            for entry in indexed_raw:
                if isinstance(entry, dict) and entry.get("source_table"):
                    indexed_tables.append(str(entry["source_table"]))
        tool_calls.append(
            {"tool": "list_row_index_tables", "input": {}, "output": indexed_raw}
        )

        if skip_row_match:
            match_reports.append(
                "## Row matching (UI)\n"
                "Use the orchestrator **Row match** panel to select a CMMS table, "
                "review chunk similarity scores, and confirm `document_id` on selected rows."
            )
        elif not indexed_tables:
            db_tables = await list_doc_rag_db_tables.ainvoke({})
            tool_calls.append(
                {"tool": "list_doc_rag_db_tables", "input": {}, "output": db_tables}
            )
            match_reports.append(
                "## Row index empty\n"
                "No tables in the row semantic index yet. In Doc RAG UI use **Index → "
                "Import DB table** (or upload CSV) before document-to-row matching works."
            )
        else:
            for source_table in _infer_source_tables(user_query, indexed_tables):
                match_input = {
                    "document_id": doc_id,
                    "confidence_threshold": threshold,
                    "group_by_table": True,
                }
                if source_table:
                    match_input["source_table"] = source_table
                match_out = await match_document_to_rows.ainvoke(match_input)
                tool_calls.append(
                    {
                        "tool": "match_document_to_rows",
                        "input": match_input,
                        "output": match_out,
                    }
                )
                if isinstance(match_out, dict):
                    label = source_table or "all indexed tables"
                    report = format_matched_rows_report(match_out, max_rows=max_rows)
                    match_reports.append(f"### Table scope: `{label}`\n\n{report}")

    rag = await query_docs.ainvoke({"query": query, "top_k": 8})
    tool_calls.append(
        {"tool": "query_docs", "input": {"query": query, "top_k": 8}, "output": rag}
    )

    evidence = await semantic_search.ainvoke({"query": query})
    tool_calls.append(
        {"tool": "semantic_search", "input": {"query": query}, "output": evidence}
    )

    parts: list[str] = [f"Doc RAG pipeline complete for {Path(file_path).name}"]
    if doc_id:
        parts.append(f"[{Path(file_path).name}](doc:{doc_id})")
    if isinstance(rag, dict) and not rag.get("error"):
        answer = rag.get("answer") or rag.get("response") or rag.get("text")
        if answer:
            parts.append(f"Answer: {str(answer)[:2500]}")
        sources = rag.get("sources") or rag.get("citations") or []
        if isinstance(sources, list) and sources:
            parts.append(f"{len(sources)} source chunk(s)")
        conf = rag.get("confidence")
        if isinstance(conf, (int, float)):
            parts.append(f"confidence {float(conf):.0%}")
    elif isinstance(rag, dict) and rag.get("error"):
        parts.append(f"query_docs: {rag.get('error')}")

    summary = " | ".join(parts)
    match_report = "\n\n".join(match_reports).strip()
    return tool_calls, summary, match_report


def extract_chat_citations(tool_calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build structured citation payloads for the chat UI (source + confidence)."""
    citations: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(
        *,
        document_id: str | None,
        file_name: str,
        confidence: float | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        section: str | None = None,
        quote: str | None = None,
        kind: str = "document",
    ) -> None:
        key = f"{document_id or ''}|{file_name}|{page_start}|{(quote or '')[:40]}"
        if key in seen:
            return
        seen.add(key)
        citations.append(
            {
                "index": len(citations) + 1,
                "document_id": document_id or "",
                "file_name": file_name,
                "confidence": confidence,
                "page_start": page_start,
                "page_end": page_end,
                "section": section,
                "quote": (quote or "")[:280] or None,
                "kind": kind,
                "url": f"doc:{document_id}" if document_id else None,
            }
        )

    for tc in tool_calls or []:
        tool = str(tc.get("tool") or "")
        out = tc.get("output")
        if not isinstance(out, dict):
            continue

        if tool in ("index_document", "get_document_metadata"):
            did = str(out.get("document_id") or out.get("doc_id") or out.get("id") or "")
            name = str(
                out.get("file_name")
                or out.get("original_filename")
                or out.get("filename")
                or (f"Document {did[:8]}" if did else "Document")
            )
            if did or name:
                _add(document_id=did or None, file_name=Path(name).name, kind="document")

        if tool == "index_documents_batch":
            for d in out.get("documents") or []:
                if not isinstance(d, dict):
                    continue
                did = str(d.get("document_id") or d.get("id") or "")
                name = str(d.get("file_name") or d.get("original_filename") or "Document")
                if did:
                    _add(document_id=did, file_name=Path(name).name, kind="document")

        if tool in ("query_docs", "semantic_search", "answer_with_graph_context"):
            overall = out.get("confidence")
            overall_f = float(overall) if isinstance(overall, (int, float)) else None
            sources = out.get("citations") or out.get("sources") or []
            if isinstance(sources, list):
                for s in sources:
                    if not isinstance(s, dict):
                        continue
                    did = str(
                        s.get("document_id")
                        or s.get("doc_id")
                        or s.get("id")
                        or ""
                    )
                    name = str(
                        s.get("file_name")
                        or s.get("filename")
                        or s.get("source")
                        or (f"Source {did[:8]}" if did else "Source")
                    )
                    conf = s.get("confidence")
                    if conf is None:
                        conf = s.get("score") or s.get("vector_score") or overall_f
                    conf_f = float(conf) if isinstance(conf, (int, float)) else overall_f
                    # Doc RAG often returns 0–1 similarity; clamp display range
                    if conf_f is not None and conf_f > 1.5:
                        conf_f = min(1.0, conf_f / 100.0)
                    page = s.get("page_start")
                    page_i = int(page) if isinstance(page, (int, float)) else None
                    quote = s.get("quote") or s.get("text") or s.get("text_content") or s.get("chunk_text_preview")
                    _add(
                        document_id=did or None,
                        file_name=Path(str(name)).name,
                        confidence=conf_f,
                        page_start=page_i,
                        page_end=int(s["page_end"]) if isinstance(s.get("page_end"), (int, float)) else None,
                        section=str(s["section"]) if s.get("section") else None,
                        quote=str(quote) if quote else None,
                        kind="rag",
                    )

        if tool == "extract_compliance_certificate":
            did = str(out.get("document_id") or "")
            upsert = out.get("upsert") if isinstance(out.get("upsert"), dict) else {}
            if not did and isinstance(upsert, dict):
                did = str(upsert.get("document_id") or "")
            name = str(out.get("source_filename") or "")
            if not name and isinstance(upsert, dict):
                raw = upsert.get("raw_metadata")
                if isinstance(raw, dict):
                    name = str(raw.get("source_filename") or "")
            if not name and isinstance(tc.get("input"), dict):
                name = Path(str(tc["input"].get("file_path") or "Certificate")).name
            if not name:
                name = "Certificate"
            conf = out.get("confidence")
            if conf is None:
                fc = out.get("field_confidence")
                if not isinstance(fc, dict) and isinstance(out.get("result"), dict):
                    fc = (out.get("result") or {}).get("field_confidence")
                if isinstance(fc, dict) and fc:
                    vals = [float(v) for v in fc.values() if isinstance(v, (int, float))]
                    conf = sum(vals) / len(vals) if vals else None
            conf_f = float(conf) if isinstance(conf, (int, float)) else None
            _add(
                document_id=did or None,
                file_name=Path(name).name,
                confidence=conf_f,
                kind="compliance",
            )

    return citations


def format_single_door_chat_preface(
    *,
    step_summaries: list[str],
    summary_text: str,
    match_report: str = "",
    citations: list[dict[str, Any]] | None = None,
) -> str:
    """Turn raw pipeline dumps into clean chat markdown with Sources."""
    parts: list[str] = ["## Ingestion complete", ""]

    steps = [s for s in (step_summaries or []) if s]
    if steps:
        parts.append("### Steps")
        for i, line in enumerate(steps, 1):
            # Soften bracket tags: [Doc RAG] foo → **Doc RAG** — foo
            pretty = re.sub(r"^\[([^\]]+)\]\s*", r"**\1** — ", line.strip())
            # Never dump full Windows paths in the chat preface
            pretty = re.sub(r"[A-Za-z]:\\[^\s]+", lambda m: Path(m.group(0)).name, pretty)
            parts.append(f"{i}. {pretty}")
        parts.append("")

    body = (summary_text or "").strip()
    if body:
        if "### " in body or body.startswith("#"):
            parts.append(body)
        else:
            parts.append("### Results")
            parts.append("")
            parts.append(body)
        parts.append("")

    if match_report and match_report.strip():
        parts.append(match_report.strip())
        parts.append("")

    cites = citations or []
    if cites:
        parts.append("### Sources")
        for c in cites:
            idx = c.get("index") or ""
            name = c.get("file_name") or "Source"
            did = c.get("document_id") or ""
            conf = c.get("confidence")
            page = c.get("page_start")
            conf_q = f"?c={float(conf):.2f}" if isinstance(conf, (int, float)) else ""
            if isinstance(page, int):
                conf_q += ("&" if conf_q else "?") + f"p={page}"
            link = f"[{name}](doc:{did}{conf_q})" if did else f"**{name}**"
            meta: list[str] = []
            if isinstance(conf, (int, float)):
                meta.append(f"confidence **{float(conf):.0%}**")
            if isinstance(page, int):
                meta.append(f"p.{page}")
            suffix = f" · {' · '.join(meta)}" if meta else ""
            parts.append(f"{idx}. {link}{suffix}")
        parts.append("")

    # The pointer follows what was actually ingested. This used to be appended to every
    # upload with the words "when certificates were ingested" attached — a condition
    # written as a caveat rather than as code, so a contract upload was told to review
    # compliance drafts that do not exist. Sending someone to the wrong space to look for
    # something that was never created is worse than sending them nowhere.
    haystack = " ".join([summary_text or "", *(step_summaries or [])]).lower()
    if "compliance" in haystack or "certificate" in haystack:
        parts.append("Review drafts in [Compliance](/compliance).")
    elif "contract performance" in haystack or "feature b" in haystack:
        parts.append(
            "Review and confirm the extracted parameters in "
            "[Vendors → Contract Performance](/vendors?space=contract_performance). "
            "Scoring stays blocked until they are confirmed."
        )
    elif "energy intelligence" in haystack or "meter readings" in haystack:
        parts.append("Readings are on file — see [Energy](/energy).")
    return "\n".join(parts).strip()


def _file_kind(file_path: str) -> str:
    ext = Path(file_path).suffix.lower()
    if ext in STRUCTURED_EXTS:
        return "structured"
    if ext in SCHEMA_EXTS:
        return "schema"
    if ext in DOCUMENT_EXTS:
        return "document"
    return "skipped"


async def start_schema_mapping_from_file(
    *,
    file_path: str,
    organization_id: str,
    cmms_name: str = "Custom",
) -> dict[str, Any]:
    """POST /api/schema-mapping with YAML/JSON schema file content."""
    path = Path(file_path)
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"error": f"Cannot read schema file: {exc}"}
    fmt = path.suffix.lstrip(".").lower() or "yaml"
    body = {
        "connector_type": "yaml",
        "external_cmms_name": cmms_name,
        "organization_id": organization_id,
        "schema_content": content,
        "schema_source": path.name,
        "schema_format": fmt,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.migration_base_url,
            timeout=1800.0,
            follow_redirects=True,
        ) as client:
            resp = await client.post("/api/schema-mapping", json=body)
            resp.raise_for_status()
            return resp.json()
    except Exception as exc:
        log.warning("single_door.schema_start.failed", error=str(exc)[:200])
        if isinstance(exc, httpx.HTTPStatusError):
            return {"error": exc.response.text[:300], "status_code": exc.response.status_code}
        return {"error": str(exc)[:300]}


async def _drive_migration_gates(
    *,
    migration_id: str,
    interactive_migration: bool = False,
) -> dict[str, Any]:
    """Drive run_migration + auto gate-approvals for one migration_id.

    Returns {status: 'done'|'error'|'awaiting_gate', summary, gate_type?, error?, tool_calls}.
    Shared by single-file (ingest_single_file) and multi-file (ingest_structured_batch).
    """
    tool_calls: list[dict[str, Any]] = []
    max_rounds = 15
    for _ in range(max_rounds):
        status = await run_migration.ainvoke({"migration_id": migration_id})
        tool_calls.append(
            {"tool": "run_migration", "input": {"migration_id": migration_id}, "output": status}
        )
        run_status = str(status.get("status") or "").lower()
        if run_status == "complete":
            return {
                "status": "done",
                "summary": f"Migration completed ({migration_id})",
                "error": None,
                "tool_calls": tool_calls,
            }
        if run_status == "failed":
            return {
                "status": "error",
                "summary": "Migration failed",
                "error": str(status.get("error", "unknown error")),
                "tool_calls": tool_calls,
            }
        if run_status != "gate":
            continue
        gate_type = str(status.get("gate_type") or "").lower()
        if interactive_migration:
            return {
                "status": "awaiting_gate",
                "summary": (
                    f"Migration paused at {gate_type.replace('_', ' ')} gate — "
                    f"review in Migration panel ({migration_id})"
                ),
                "gate_type": gate_type,
                "error": None,
                "tool_calls": tool_calls,
            }
        # Every gate the migration pipeline can raise, each answered with "accept what
        # was detected". Only three were handled before, and the pipeline opens with
        # pk_approval — so a migration driven from the single door stopped at the first
        # gate every time and ingested nothing. The auto-drive path exists precisely so a
        # PM does not have to answer eight prompts to load a spreadsheet; leaving gaps in
        # it turned an unattended flow into a dead end with no data and no error a user
        # could act on.
        _AUTO_GATES = {
            "pk_approval": (submit_pk_approval, {"pk_overrides": {}}),
            "unique_table_approval": (submit_unique_table_approval, {"approved": True}),
            "classification_approval": (
                submit_classification_approval,
                {"rejected_groups": [], "verdict_overrides": {}},
            ),
            "column_mapping_approval": (submit_column_mapping_approval, {"overrides": {}}),
            "final": (submit_final_gate, {"confirm": True}),
        }
        if gate_type in _AUTO_GATES:
            fn, payload = _AUTO_GATES[gate_type]
            args = {"migration_id": migration_id, **payload}
            gate_resp = await fn.ainvoke(args)
            tool_calls.append(
                {"tool": fn.name, "input": args, "output": gate_resp}
            )
            continue
        if gate_type == "pre_semantic":
            gate_resp = await submit_pre_semantic.ainvoke(
                {"migration_id": migration_id, "approve_all": True}
            )
            tool_calls.append(
                {
                    "tool": "submit_pre_semantic",
                    "input": {"migration_id": migration_id, "approve_all": True},
                    "output": gate_resp,
                }
            )
            continue
        if gate_type == "field_mapping":
            gate_resp = await submit_field_mapping.ainvoke(
                {"migration_id": migration_id, "approve_all": True}
            )
            tool_calls.append(
                {
                    "tool": "submit_field_mapping",
                    "input": {"migration_id": migration_id, "approve_all": True},
                    "output": gate_resp,
                }
            )
            continue
        if gate_type == "hierarchy":
            gate_resp = await submit_hierarchy.ainvoke(
                {"migration_id": migration_id, "approve_all": True}
            )
            tool_calls.append(
                {
                    "tool": "submit_hierarchy",
                    "input": {"migration_id": migration_id, "approve_all": True},
                    "output": gate_resp,
                }
            )
            continue
        return {
            "status": "error",
            "summary": f"Unhandled gate {gate_type}",
            "error": gate_type,
            "tool_calls": tool_calls,
        }
    return {
        "status": "error",
        "summary": "Migration did not complete in allotted rounds",
        "error": "max_rounds",
        "tool_calls": tool_calls,
    }


async def ingest_structured_batch(
    *,
    file_paths: list[str],
    organization_id: str | None = None,
    cmms_name: str = "Custom",
    interactive_migration: bool = False,
) -> dict[str, Any]:
    """TRACK 1 — start ONE migration covering ALL structured files, then drive its gates.

    All CSV/Excel files become source tables inside a single migration_id (via
    start_migration_multi). Never one migration per spreadsheet.
    """
    org = organization_id or "00000000-0000-0000-0000-000000000001"
    names = [Path(p).name for p in file_paths]
    tool_calls: list[dict[str, Any]] = []

    started = await start_migration_multi.ainvoke(
        {"file_paths": file_paths, "cmms_name": cmms_name, "organization_id": org}
    )
    tool_calls.append(
        {"tool": "start_migration_multi", "input": {"file_paths": file_paths}, "output": started}
    )
    migration_id = str(started.get("migration_id") or "")
    if not migration_id:
        return {
            "kind": "structured",
            "status": "error",
            "file_names": names,
            "summary": "Migration start failed",
            "error": str(started.get("error", "start_migration_multi failed")),
            "tool_calls": tool_calls,
        }

    session_id = get_session_context()
    if session_id and session_id != "shared":
        for name in names:
            register_migration_file(session_id, migration_id, name)

    if interactive_migration:
        # INTERACTIVE hand-off — do NOT block the caller (the bulk ingest worker) driving the
        # migration to its first gate. That drive is a long poll (run_migration auto-advances
        # every step_paused, up to ~5 min) and it kept the bulk batch marked "running" for the
        # WHOLE drive; worse, if the worker task died mid-drive (process restart / OOM) the batch
        # orphaned at 0/N "running" forever while the migration itself reached its gate. Return
        # awaiting_gate NOW (files handed to the migration) and let it reach the first user gate
        # on its own: the Migration panel auto-advances step_paused states, and we also kick a
        # detached best-effort driver so the gate is reached even if the panel isn't open. The
        # migration is already registered above, so the panel / workflow queue picks it up.
        try:
            drive_task = asyncio.create_task(
                _drive_migration_gates(migration_id=migration_id, interactive_migration=True)
            )
            _detached_drive_tasks.add(drive_task)
            drive_task.add_done_callback(_detached_drive_tasks.discard)
        except RuntimeError:
            # No running loop (defensive — we're inside an async call) — the panel drives it.
            pass
        return {
            "kind": "structured",
            "status": "awaiting_gate",
            "file_names": names,
            "summary": (
                f"Migration started ({migration_id}) — {len(names)} file(s) combined into one "
                f"migration; review at the first gate in the Migration panel"
            ),
            "migration_id": migration_id,
            "gate_type": None,
            "error": None,
            "tool_calls": tool_calls,
        }

    # NON-interactive — drive the full pipeline to completion (auto-approve every gate) inline.
    driven = await _drive_migration_gates(
        migration_id=migration_id,
        interactive_migration=interactive_migration,
    )
    return {
        "kind": "structured",
        "status": driven["status"],
        "file_names": names,
        "summary": driven["summary"],
        "migration_id": migration_id,
        "gate_type": driven.get("gate_type"),
        "error": driven.get("error"),
        "tool_calls": tool_calls + driven["tool_calls"],
    }


async def ingest_single_file(
    *,
    file_path: str,
    organization_id: str | None = None,
    cmms_name: str = "Custom",
    user_query: str | None = None,
    run_doc_rag_pipeline: bool = True,
    skip_row_match: bool = False,
    interactive_migration: bool = False,
    preindexed: dict | None = None,
) -> dict[str, Any]:
    """Process one uploaded file through migration or doc-rag. Used inline and in bulk batches.

    ``preindexed`` (documents only): the index result from a prior batch index — when set,
    the per-file ``index_document`` step is skipped (the embedding was already done as part
    of the consolidated batch, 7.1 AC1/AC4) and only the downstream RAG steps run.
    """
    path = Path(file_path)
    kind = _file_kind(file_path)
    org = organization_id or "00000000-0000-0000-0000-000000000001"
    tool_calls: list[dict[str, Any]] = []

    if kind == "skipped":
        return {
            "file_name": path.name,
            "kind": kind,
            "status": "error",
            "summary": f"Unsupported file type: {path.suffix}",
            "error": "unsupported_extension",
            "tool_calls": tool_calls,
        }

    if kind == "schema":
        started = await start_schema_mapping_from_file(
            file_path=file_path,
            organization_id=org,
            cmms_name=cmms_name,
        )
        tool_calls.append(
            {
                "tool": "start_schema_mapping",
                "input": {"file_path": file_path},
                "output": started,
            }
        )
        schema_id = str(started.get("schema_mapping_id") or "")
        if not schema_id:
            return {
                "file_name": path.name,
                "kind": kind,
                "status": "error",
                "summary": "Schema mapping start failed",
                "error": str(started.get("error", "start_schema_mapping failed")),
                "tool_calls": tool_calls,
            }
        return {
            "file_name": path.name,
            "kind": kind,
            "status": "awaiting_gate",
            "summary": f"Schema mapping started ({schema_id}) — continue in Schema panel",
            "schema_mapping_id": schema_id,
            "error": None,
            "tool_calls": tool_calls,
        }

    if kind == "structured":
        # Feature B: spreadsheet named/described as invoice or contract → extract first
        if classify_contract_performance_doc(file_path, user_query):
            cp_route = await route_contract_performance_upload(
                file_path=file_path,
                organization_id=org,
                user_query=user_query,
            )
            if cp_route:
                tool_calls.append(
                    {
                        "tool": (
                            "extract_contract_from_document"
                            if cp_route.get("kind") == "contract"
                            else "extract_and_verify_invoice"
                        ),
                        "input": {
                            "file_path": file_path,
                            "feature_b_kind": cp_route.get("kind"),
                        },
                        "output": cp_route.get("result") or cp_route,
                    }
                )
                # Invoice/contract spreadsheets: Feature B is primary; skip migration
                # unless the user also asked for CMMS migration.
                msg_l = (user_query or "").lower()
                force_migration = any(
                    k in msg_l for k in ("migrate", "migration", "cmms import", "schema map")
                )
                if not force_migration:
                    return {
                        "file_name": path.name,
                        "kind": "contract_performance",
                        "status": "done" if cp_route.get("ok") else "error",
                        "summary": cp_route.get("summary") or "Feature B extract complete",
                        "contract_performance": cp_route,
                        "error": cp_route.get("error"),
                        "tool_calls": tool_calls,
                    }

        started = await start_migration.ainvoke(
            {"file_path": file_path, "cmms_name": cmms_name, "organization_id": org}
        )
        tool_calls.append(
            {"tool": "start_migration", "input": {"file_path": file_path}, "output": started}
        )
        migration_id = str(started.get("migration_id") or "")
        if not migration_id:
            return {
                "file_name": path.name,
                "kind": kind,
                "status": "error",
                "summary": "Migration start failed",
                "error": str(started.get("error", "start_migration failed")),
                "tool_calls": tool_calls,
            }

        session_id = get_session_context()
        if session_id and session_id != "shared":
            register_migration_file(session_id, migration_id, path.name)

        driven = await _drive_migration_gates(
            migration_id=migration_id,
            interactive_migration=interactive_migration,
        )
        return {
            "file_name": path.name,
            "kind": kind,
            "status": driven["status"],
            "summary": driven["summary"],
            "migration_id": migration_id,
            "gate_type": driven.get("gate_type"),
            "error": driven.get("error"),
            "tool_calls": tool_calls + driven["tool_calls"],
        }

    if preindexed is not None and not preindexed.get("error"):
        # Already indexed as part of a consolidated batch (7.1 AC1/AC4) — skip re-indexing.
        indexed = preindexed
    else:
        doc_type = document_type_hint(file_path)
        indexed = await index_document.ainvoke(
            {"file_path": file_path, "document_type": doc_type}
        )
        tool_calls.append(
            {"tool": "index_document", "input": {"file_path": file_path}, "output": indexed}
        )
    if indexed.get("error"):
        return {
            "file_name": path.name,
            "kind": kind,
            "status": "error",
            "summary": "Document indexing failed",
            "error": str(indexed.get("error")),
            "tool_calls": tool_calls,
        }

    # Classify Feature A/B BEFORE Doc RAG Q&A so certificate/contract uploads
    # trigger the engine path instead of looking like "documents only".
    from .contract_performance_single_door import extract_text_from_upload

    peek_text = extract_text_from_upload(file_path, max_chars=4_000)
    is_feature_b = bool(classify_contract_performance_doc(file_path, user_query))
    compliance_hint = None
    if not is_feature_b:
        # Hybrid: fast keyword pass first, LLM fallback only if keyword finds nothing
        # (catches scanned / oddly-named certs the regex misses).
        compliance_hint = await classify_compliance_certificate_hybrid(
            file_path, user_query, source_text=peek_text, peek_file=False
        )
    is_feature_a = compliance_hint is not None

    summary = f"Document indexed: {path.name}"
    match_report = ""
    # Skip Doc RAG query/match when A/B owns the turn — still indexed for later Q&A.
    run_rag = run_doc_rag_pipeline and not is_feature_a and not is_feature_b
    if run_rag:
        pipeline_calls, pipeline_summary, match_report = await run_document_rag_pipeline(
            file_path=file_path,
            index_result=indexed,
            user_query=user_query,
            skip_row_match=skip_row_match,
        )
        tool_calls.extend(pipeline_calls)
        summary = pipeline_summary

    # Feature B — Contract Performance auto-route (contracts / POs / invoices)
    # Feature A — Compliance certificate auto-route (certs / accreditations)
    # Prefer B when both match (invoice/contract names often include "certificate")
    cp_route = None
    compliance_route = None
    if is_feature_b:
        doc_id = None
        if isinstance(indexed, dict):
            doc_id = indexed.get("document_id") or indexed.get("id")
        cp_route = await route_contract_performance_upload(
            file_path=file_path,
            organization_id=org,
            user_query=user_query,
            document_id=str(doc_id) if doc_id else None,
        )
        if cp_route:
            tool_calls.append(
                {
                    "tool": (
                        "extract_contract_from_document"
                        if cp_route.get("kind") == "contract"
                        else "extract_and_verify_invoice"
                    ),
                    "input": {
                        "file_path": file_path,
                        "feature_b_kind": cp_route.get("kind"),
                    },
                    "output": cp_route.get("result") or cp_route,
                }
            )
            summary = f"{summary} | {cp_route.get('summary')}"
    elif is_feature_a:
        doc_id = None
        if isinstance(indexed, dict):
            doc_id = indexed.get("document_id") or indexed.get("id")
        compliance_route = await route_compliance_certificate_upload(
            file_path=file_path,
            organization_id=org,
            user_query=user_query,
            document_id=str(doc_id) if doc_id else None,
            source_text=peek_text,
            hint=compliance_hint,
        )
        if compliance_route:
            tool_calls.append(
                {
                    "tool": "run_document_forensics",
                    "input": {
                        "file_path": file_path,
                        "certificate_type_code": compliance_route.get(
                            "certificate_type_code"
                        )
                        or (compliance_hint or {}).get("certificate_type_code"),
                    },
                    "output": compliance_route.get("forensics") or {},
                }
            )
            tool_calls.append(
                {
                    "tool": "extract_compliance_certificate",
                    "input": {
                        "file_path": file_path,
                        "certificate_type_code": compliance_route.get(
                            "certificate_type_code"
                        )
                        or (compliance_hint or {}).get("certificate_type_code"),
                    },
                    "output": compliance_route,
                }
            )
            summary = (
                compliance_route.get("summary")
                or f"Feature A compliance extract for {path.name}"
            )

    return {
        "file_name": path.name,
        "kind": kind,
        "status": "done",
        "summary": summary,
        "match_report": match_report,
        "contract_performance": cp_route,
        "compliance": compliance_route,
        "error": None,
        "tool_calls": tool_calls,
    }


async def _reconcile_feature_b_migration(file_paths: list[str]) -> dict[str, Any] | None:
    """After a Feature B migration, link the rows to their vendor and drop duplicates.

    The migration writes work_orders.vendor_id as NULL and does not keep vendor_name on
    that table, so scoring — which filters by vendor — finds nothing at all for a vendor
    whose work orders were just loaded. The name is still in the file we were given, so
    the mapping is read back out of it here and sent with the request. ppm_visits does
    keep the name and resolves itself.

    Best-effort: a failure here leaves rows unlinked, which is the behaviour we already
    had, and must not fail an upload whose data landed correctly.
    """
    import csv as _csv

    links: dict[str, dict[str, str]] = {}
    for path in file_paths:
        if Path(path).suffix.lower() != ".csv":
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                rows = list(_csv.DictReader(fh))
        except OSError:
            continue
        if not rows:
            continue
        cols = {c.lower() for c in rows[0]}
        if "vendor_name" not in cols:
            continue
        for table, code_col in (("work_orders", "wo_code"), ("ppm_visits", "ppm_code")):
            if code_col not in cols:
                continue
            m = links.setdefault(table, {})
            for r in rows:
                code, name = (r.get(code_col) or "").strip(), (r.get("vendor_name") or "").strip()
                if code and name:
                    m[code] = name
    if not links:
        return None
    try:
        base = settings.operations_intelligence_base_url.rstrip("/")
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                f"{base}/api/contract-performance/migration/reconcile",
                json={"vendor_links": links},
            )
            resp.raise_for_status()
            out = resp.json()
        log.info("single_door.feature_b_reconciled", tables=out.get("tables"))
        return out
    except Exception as exc:  # noqa: BLE001
        log.warning("single_door.feature_b_reconcile_failed", error=str(exc)[:200])
        return None


async def run_single_door_ingestion_sequence(
    *,
    session_id: str,
    file_paths: list[str],
    organization_id: str | None = None,
    cmms_name: str = "Custom",
    user_message: str | None = None,
    skip_row_match: bool = False,
    interactive_migration: bool = False,
) -> SingleDoorResult:
    """
    Execute the "single-door" sequence for uploaded files:
      1) structured files => migration flow (auto gate approvals)
      2) document files   => doc-rag indexing
      2b) contract / PO / invoice docs => Feature B extract (alongside Doc RAG)
      2c) certificate / accreditation docs => Feature A extract + draft upsert
      3) return context summary so orchestrator can continue in same chat window
    """
    set_session_context(session_id)
    tool_calls: list[dict[str, Any]] = []
    notes: list[str] = []
    step_summaries: list[str] = []
    n_structured = 0
    n_documents = 0
    n_schema = 0
    n_skipped = 0
    n_structured_done = 0
    n_documents_done = 0
    n_schema_started = 0
    n_migration_awaiting = 0
    n_contract_perf = 0
    n_energy = 0
    n_compliance = 0
    user_query = (user_message or "").strip() or None
    combined_match_report: list[str] = []
    cp_notes: list[str] = []
    compliance_notes: list[str] = []
    detected_engines: list[str] = []

    # Partition files into the two independent tracks (+ schema/skipped), order preserved.
    structured_all = [p for p in file_paths if _file_kind(p) == "structured"]
    # Feature B structured (invoice/contract xlsx) — route out of CMMS migration batch
    msg_l = (user_query or "").lower()
    force_migration = any(
        k in msg_l for k in ("migrate", "migration", "cmms import", "schema map")
    )
    cp_structured_paths = [
        p
        for p in structured_all
        if classify_contract_performance_doc(p, user_query) and not force_migration
    ]
    # Feature C — smart-meter exports. Recognised by their header (MPAN/MPRN + timestamp +
    # kWh), so a work-order CSV cannot be mistaken for one. Taken out of the migration set
    # for the same reason contracts are: mapping half-hourly readings against the asset and
    # work-order schemas writes no MeterReading at all.
    energy_paths = [
        p
        for p in structured_all
        if p not in set(cp_structured_paths)
        and classify_energy_document(p, user_query)
        and not force_migration
    ]
    structured_paths = [
        p for p in structured_all
        if p not in set(cp_structured_paths) and p not in set(energy_paths)
    ]
    schema_paths = [p for p in file_paths if _file_kind(p) == "schema"]
    document_paths = [p for p in file_paths if _file_kind(p) == "document"]
    skipped_paths = [p for p in file_paths if _file_kind(p) == "skipped"]
    n_skipped = len(skipped_paths)
    mixed_tracks = bool(structured_paths) and bool(document_paths)

    if mixed_tracks:
        step_summaries.append(
            "[Mixed upload] Two independent tracks — "
            f"Structured (Migration): {', '.join(Path(p).name for p in structured_paths)}  |  "
            f"Documents (Doc RAG): {', '.join(Path(p).name for p in document_paths)}"
        )

    # Feature B has an order dependency the tracks did not respect. An invoice is verified
    # against ingested work orders and the vendor's confirmed contract, and BOTH arrive in
    # later tracks — the work orders in the migration below, the contract with the documents
    # after it. Verifying invoices here meant a single upload of contract + work orders +
    # invoice checked the invoice against nothing: every line unmatched, rate checks skipped,
    # and a confident-looking result that was almost entirely artefact.
    #
    # Contracts still run first, because everything downstream reads them. Invoices are held
    # until the migration has loaded the work orders they cite.
    cp_contract_paths = [
        p
        for p in cp_structured_paths
        if classify_contract_performance_doc(p, user_query) != "invoice"
    ]
    cp_invoice_paths = [p for p in cp_structured_paths if p not in set(cp_contract_paths)]

    async def _run_feature_b(paths: list[str]) -> None:
        nonlocal n_contract_perf
        for file_path in paths:
            cp_kind = classify_contract_performance_doc(file_path, user_query) or "contract"
            step_summaries.append(
                f"[Feature B · {cp_kind}] {Path(file_path).name}: "
                "Relationships extract → PM confirm"
            )
            result = await ingest_single_file(
                file_path=file_path,
                organization_id=organization_id,
                cmms_name=cmms_name,
                user_query=user_query,
                run_doc_rag_pipeline=False,
                interactive_migration=False,
            )
            tool_calls.extend(result.get("tool_calls") or [])
            summary = str(result.get("summary") or "")
            if summary:
                notes.append(summary)
            cp = result.get("contract_performance")
            if isinstance(cp, dict):
                # The extraction card reads tool_calls, and only the INNER calls from
                # ingest_single_file were forwarded — never this payload. So the extraction
                # reached the chat as one line of prose while the card that shows the
                # parameters, their sources and the defaults had nothing to render from.
                # Reported here the same way the energy track reports its ingest.
                tool_calls.append(
                    {
                        "tool": (
                            "extract_contract_from_document"
                            if cp.get("kind") == "contract"
                            else "extract_and_verify_invoice"
                        ),
                        "input": {"file": Path(file_path).name},
                        "output": {"contract_performance": cp},
                    }
                )
                n_contract_perf += 1
                if cp.get("summary"):
                    cp_notes.append(str(cp["summary"]))
            elif result.get("kind") == "contract_performance":
                n_contract_perf += 1

    # ── TRACK 0 — Feature B contracts (parameters everything else is measured against) ──
    await _run_feature_b(cp_contract_paths)

    # ── TRACK 0b — Feature C meter readings ──
    for _energy_path in energy_paths:
        step_summaries.append(
            f"[Feature C · meter readings] {Path(_energy_path).name}: "
            "half-hourly ingest → gap detection"
        )
        _er = await route_energy_upload(
            file_path=_energy_path,
            organization_id=organization_id,
            user_query=user_query,
        )
        if _er:
            n_energy += 1
            if _er.get("summary"):
                notes.append(str(_er["summary"]))
            tool_calls.append(
                {
                    "tool": "ingest_meter_readings",
                    "input": {"file": Path(_energy_path).name},
                    "output": _er,
                }
            )
    if n_energy and "energy_intelligence" not in detected_engines:
        detected_engines.append("energy_intelligence")

    # ── TRACK 1 — structured batch FIRST: ONE migration for all spreadsheets ──
    if structured_paths:
        if len(structured_paths) > 1:
            step_summaries.append(
                f"[Migration · structured batch] {len(structured_paths)} files → ONE migration: "
                + ", ".join(Path(p).name for p in structured_paths)
            )
        else:
            step_summaries.append(
                f"[Migration] {Path(structured_paths[0]).name}: start → gates → mapping/hierarchy"
            )
        batch = await ingest_structured_batch(
            file_paths=structured_paths,
            organization_id=organization_id,
            cmms_name=cmms_name,
            interactive_migration=interactive_migration,
        )
        tool_calls.extend(batch.get("tool_calls") or [])
        n_structured = len(structured_paths)
        summary = str(batch.get("summary") or "")
        if summary:
            notes.append(summary)
        if batch.get("status") == "done":
            n_structured_done = len(structured_paths)
            set_ingestion_mode_structured(session_id)
            record_mapping_complete(session_id)
            record_hierarchy_complete(session_id)
        if batch.get("migration_id") and batch.get("status") == "awaiting_gate":
            n_migration_awaiting += 1
            register_migration_id(session_id, str(batch.get("migration_id") or ""))

    # ── Schema files (YAML/JSON) ──
    for file_path in schema_paths:
        step_summaries.append(
            f"[Schema] {Path(file_path).name}: ingest → gates → hierarchy → output"
        )
        result = await ingest_single_file(
            file_path=file_path,
            organization_id=organization_id,
            cmms_name=cmms_name,
            user_query=user_query,
            run_doc_rag_pipeline=False,
            interactive_migration=False,
        )
        tool_calls.extend(result.get("tool_calls") or [])
        n_schema += 1
        summary = str(result.get("summary") or "")
        if summary:
            notes.append(summary)
        if result.get("schema_mapping_id"):
            n_schema_started += 1

    # ── TRACK 2 — documents. 7.1 AC1/AC4: when there are MULTIPLE documents, index them
    # all in ONE batch first (a single consolidated embedding pass) instead of one
    # embedding pass per file; the per-document RAG steps then run against the pre-indexed
    # docs. A single document keeps the simple inline path.
    #
    # CCC §3.1 — when EVERY staged document is a compliance certificate PDF, cap at 5.
    # Mixed Doc-RAG / Feature B batches keep the general multi-file limit.
    MAX_COMPLIANCE_BATCH = 5
    if document_paths:
        compliance_flags = [
            classify_compliance_certificate_doc(p, user_query) is not None
            and classify_contract_performance_doc(p, user_query) is None
            for p in document_paths
        ]
        if compliance_flags and all(compliance_flags) and len(document_paths) > MAX_COMPLIANCE_BATCH:
            dropped = document_paths[MAX_COMPLIANCE_BATCH:]
            document_paths = document_paths[:MAX_COMPLIANCE_BATCH]
            step_summaries.append(
                f"[Feature A · Compliance] batch capped at {MAX_COMPLIANCE_BATCH} PDFs "
                f"(dropped {len(dropped)}: "
                + ", ".join(Path(p).name for p in dropped)
                + ")"
            )
            notes.append(
                f"Compliance mass-import limit is {MAX_COMPLIANCE_BATCH} PDFs per batch; "
                f"{len(dropped)} file(s) were not processed."
            )

    compliance_only_batch = bool(document_paths) and all(
        classify_compliance_certificate_doc(p, user_query) is not None
        and classify_contract_performance_doc(p, user_query) is None
        for p in document_paths
    )

    # A "plain" RAG doc (neither Feature A compliance nor Feature B contract) is the only
    # kind whose row-matching needs embeddings ready; A/B extraction needs only the
    # document_id, which is available at upload time.
    def _is_plain_rag_doc(p: str) -> bool:
        return (
            classify_contract_performance_doc(p, user_query) is None
            and classify_compliance_certificate_doc(p, user_query) is None
        )

    preindexed_by_path: dict[str, dict] = {}
    index_wait_task: asyncio.Task | None = None
    if len(document_paths) >= 2:
        try:
            from .doc_rag_agent import await_documents_indexed, index_documents_batch

            # Upload non-blocking: get document_ids immediately so compliance / contract
            # extraction can start right away instead of waiting ~2-5 min for embedding.
            batch = await index_documents_batch.ainvoke(
                {"file_paths": document_paths, "wait": False}
            )
            tool_calls.append(
                {
                    "tool": "index_documents_batch",
                    "input": {"count": len(document_paths), "wait": False},
                    "output": batch,
                }
            )
            step_summaries.append(
                f"[Doc RAG] batch-indexed {len(document_paths)} documents in one embedding "
                f"pass (non-blocking — embedding overlaps extraction)"
            )
            _by_name = {d.get("file_name"): d for d in (batch.get("documents") or [])}
            for _fp in document_paths:
                _d = _by_name.get(Path(_fp).name)
                if _d and _d.get("document_id"):
                    preindexed_by_path[_fp] = _d
            # Only spin the embedding-completion wait when a plain RAG doc actually needs
            # it; it runs concurrently with extraction and is awaited per-doc below.
            if any(_is_plain_rag_doc(p) for p in document_paths):
                _doc_ids = [
                    d.get("document_id")
                    for d in (batch.get("documents") or [])
                    if d.get("document_id")
                ]
                if _doc_ids:
                    index_wait_task = asyncio.create_task(
                        await_documents_indexed(_doc_ids)
                    )
        except Exception as _bx:  # fall back to per-file indexing on any error
            log.warning("single_door.batch_index_failed", error=str(_bx))

    async def _ingest_one_document(file_path: str) -> dict[str, Any]:
        # Classification runs before the guarded ingest below, so it needs its own guard:
        # an unreadable or malformed PDF raising here would otherwise escape the worker and
        # take every sibling's result with it.
        try:
            cp_kind = classify_contract_performance_doc(file_path, user_query)
        except Exception as exc:  # noqa: BLE001 — an unclassifiable file is still ingestable
            log.warning(
                "single_door.cp_classify_failed", path=file_path, error=str(exc)[:200]
            )
            cp_kind = None
        a_hint = None
        if not cp_kind:
            try:
                a_hint = classify_compliance_certificate_doc(file_path, user_query)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "single_door.a_classify_failed", path=file_path, error=str(exc)[:200]
                )
        step = ""
        if cp_kind:
            step = (
                f"[Doc RAG + Feature B · {cp_kind}] {Path(file_path).name}: "
                f"index → Relationships extract → PM confirm in Vendors space"
            )
        elif a_hint:
            code = a_hint.get("certificate_type_code") or "CERT"
            a_scope = a_hint.get("cert_scope") or "Vendor"
            scope_word = "Vendor Compliance" if a_scope == "Vendor" else "Building Compliance"
            step = (
                f"[Feature A · {scope_word} · {code}] {Path(file_path).name}: "
                f"matched {a_scope} pack → extract fields → draft upsert → "
                f"{a_scope} Certificates Saved Space"
            )
        else:
            step = (
                f"[Doc RAG] {Path(file_path).name}: index → verify → match rows → query → evidence"
            )
        # Plain RAG docs row-match against the vector index, so they must wait for embedding
        # to finish. Feature A/B docs (cp_kind / a_hint) skip this and run immediately —
        # their extraction needs only the document_id, already captured at upload.
        if index_wait_task is not None and not cp_kind and not a_hint:
            try:
                # Bounded. Embedding can take minutes, and a certificate that simply failed
                # to classify does not need chunks at all — it needs the document_id, which
                # upload already returned. Waiting unboundedly turned a missed
                # classification into a five-minute stall with nothing on screen.
                await asyncio.wait_for(
                    asyncio.shield(index_wait_task), timeout=_INDEX_WAIT_SECONDS
                )
            except asyncio.TimeoutError:
                log.warning(
                    "single_door.index_wait_timeout",
                    path=file_path,
                    seconds=_INDEX_WAIT_SECONDS,
                )
            except Exception as _wx:  # embedding wait failed — proceed; row-match may be empty
                log.warning("single_door.index_wait_failed", error=str(_wx))
        try:
            result = await ingest_single_file(
                file_path=file_path,
                organization_id=organization_id,
                cmms_name=cmms_name,
                user_query=user_query,
                run_doc_rag_pipeline=True,
                skip_row_match=skip_row_match,
                interactive_migration=False,
                preindexed=preindexed_by_path.get(file_path),
            )
        except Exception as exc:  # noqa: BLE001 — one file must not block siblings
            log.warning("single_door.document_ingest_failed", path=file_path, error=str(exc))
            result = {
                "status": "error",
                "summary": f"{Path(file_path).name}: ingest failed — {exc}",
                "tool_calls": [],
            }
        return {"file_path": file_path, "step": step, "result": result}

    # Always process documents CONCURRENTLY — each _ingest_one_document isolates its own
    # failures, so up to 5 PDFs run their full pipeline (Doc-RAG index → classify → extract
    # → verify → draft upsert) in parallel via asyncio.gather, regardless of whether the
    # batch is compliance-only or mixed.
    if document_paths:
        if len(document_paths) >= 2:
            step_summaries.append(
                f"[Feature A · Compliance] parallel ingest/extract of "
                f"{len(document_paths)} PDFs (asyncio.gather; failures isolated)"
            )
        # return_exceptions is what makes "failures isolated" true. Without it a single
        # worker raising cancels the gather and discards every sibling's completed result,
        # so four successfully extracted certificates vanish from the chat because the
        # fifth PDF was malformed. Same guarantee the engine's own batch ingest gives.
        settled = await asyncio.gather(
            *[_ingest_one_document(fp) for fp in document_paths],
            return_exceptions=True,
        )
        doc_results = []
        for fp, outcome in zip(document_paths, settled):
            if isinstance(outcome, BaseException):
                log.error(
                    "single_door.document_worker_crashed",
                    path=fp,
                    error=str(outcome)[:300],
                )
                doc_results.append(
                    {
                        "file_path": fp,
                        "step": f"[Ingest] {Path(fp).name}: failed — {outcome}",
                        "result": {
                            "status": "error",
                            "summary": f"{Path(fp).name}: ingest failed — {outcome}",
                            "tool_calls": [],
                        },
                    }
                )
            else:
                doc_results.append(outcome)
    else:
        doc_results = []

    ingested_certificate_ids: list[str] = []
    for item in doc_results:
        file_path = str(item["file_path"])
        if item.get("step"):
            step_summaries.append(str(item["step"]))
        result = item.get("result") or {}
        tool_calls.extend(result.get("tool_calls") or [])
        n_documents += 1
        summary = str(result.get("summary") or "")
        if summary:
            notes.append(summary)
        cp = result.get("contract_performance")
        if isinstance(cp, dict):
            n_contract_perf += 1
            # A contract arrives as a PDF, so it comes through THIS track, not the
            # structured Feature B one. The extraction card reads tool_calls and only the
            # inner Doc RAG calls were forwarded, so the card had nothing to render and the
            # extraction reached the chat as a single line of prose. Reported here the same
            # way the energy ingest is.
            tool_calls.append(
                {
                    "tool": (
                        "extract_contract_from_document"
                        if cp.get("kind") == "contract"
                        else "extract_and_verify_invoice"
                    ),
                    "input": {"file": Path(file_path).name},
                    "output": {"contract_performance": cp},
                }
            )
            if cp.get("summary"):
                cp_notes.append(str(cp["summary"]))
            if "contract_performance" not in detected_engines:
                detected_engines.append("contract_performance")
        compliance = result.get("compliance")
        if isinstance(compliance, dict):
            n_compliance += 1
            cert_id = compliance.get("certificate_id") or (
                compliance.get("upsert") or {}
            ).get("certificate_id")
            if cert_id:
                ingested_certificate_ids.append(str(cert_id))
            if compliance.get("summary"):
                compliance_notes.append(str(compliance["summary"]))
            if "compliance" not in detected_engines:
                detected_engines.append("compliance")
            # Surface pending vector membership for chat cards
            doc_id = compliance.get("document_id") or (compliance.get("upsert") or {}).get(
                "document_id"
            )
            # Only surface the Keep/Remove prompt when membership is genuinely pending.
            # Auto-keep (the default) sets membership_pending=False, so ingest indexes the
            # PDF without prompting.
            if doc_id and compliance.get("membership_pending"):
                mem_qid = None
                if isinstance(compliance.get("upsert"), dict):
                    mem_qid = compliance["upsert"].get("membership_queue_item_id")
                notes.append(
                    f"VECTOR_MEMBERSHIP_PENDING document_id={doc_id} "
                    f"file={Path(file_path).name}"
                    + (f" queue_item_id={mem_qid}" if mem_qid else "")
                    + " — Keep adds to vector DB; Remove discards (held until confirm)"
                )
        mr = str(result.get("match_report") or "").strip()
        if mr:
            combined_match_report.append(f"## {Path(file_path).name}\n\n{mr}")
        if result.get("status") == "done":
            n_documents_done += 1

    if n_documents_done > 0 and n_structured_done == 0 and n_schema_started == 0:
        record_unstructured_register_ready(session_id)

    if n_documents_done > 0 or n_structured_done > 0:
        # Skip Hybrid UDR corpus noise when this upload was Feature A certificates only
        # (BPCA / Gas Safe / building certs) — those go to Compliance Saved Space, not UDR.
        feature_a_only = (
            n_compliance > 0
            and n_structured_done == 0
            and n_contract_perf == 0
            and n_schema_started == 0
        )
        if not feature_a_only:
            try:
                from .udr_hybrid_tools import retrieve_workspace_corpus_summary

                corpus = await retrieve_workspace_corpus_summary.ainvoke({})
                tool_calls.append(
                    {
                        "tool": "retrieve_workspace_corpus_summary",
                        "input": {},
                        "output": corpus,
                    }
                )
                if isinstance(corpus, dict) and not corpus.get("error"):
                    step_summaries.append("[Hybrid UDR] Workspace corpus summary ready")
            except Exception as exc:
                log.warning("single_door.corpus_summary.failed", error=str(exc)[:200])

    # ── TRACK 3 — Feature B invoices, LAST on purpose ──
    # An invoice is only meaningful against the work orders it cites and the contract it
    # bills under. Both land in the tracks above, so this runs after them: the migration has
    # loaded the work orders, and the contract has been ingested. Verifying earlier — which
    # is what happened when invoices shared Track 0 with contracts — checked the invoice
    # against an empty database and reported every line as unmatched.
    # Repair the migration's output BEFORE invoices are verified: B3 matches against work
    # orders, and duplicated or unlinked rows change what it finds.
    if structured_paths:
        _rec = await _reconcile_feature_b_migration(structured_paths)
        if _rec and _rec.get("tables"):
            _t = _rec["tables"]
            _dupes = sum(int(v.get("duplicates_removed") or 0) for v in _t.values())
            _linked = sum(int(v.get("vendor_linked") or 0) for v in _t.values())
            if _dupes or _linked:
                step_summaries.append(
                    f"[Feature B] Post-migration: {_linked} row(s) linked to their vendor, "
                    f"{_dupes} duplicate row(s) removed"
                )

    if cp_invoice_paths:
        step_summaries.append(
            f"[Feature B · invoice] verifying {len(cp_invoice_paths)} invoice(s) after "
            "work orders and contracts are in place"
        )
        await _run_feature_b(cp_invoice_paths)
    if n_contract_perf > 0 and "contract_performance" not in detected_engines:
        detected_engines.append("contract_performance")

    # Second pass, deliberately. The migration can report failure, recover through its
    # schema-aligned fallback and finish writing AFTER the first reconcile has run — the
    # observed sequence was reconcile at 19:16:59, migration complete at 19:17:15, and a
    # full duplicate set landing in between. Reconciling again once everything else is
    # done catches that write. The operation is idempotent, so a second pass with nothing
    # to do costs one query per table.
    if structured_paths:
        _rec2 = await _reconcile_feature_b_migration(structured_paths)
        _t2 = (_rec2 or {}).get("tables") or {}
        _late = sum(int(v.get("duplicates_removed") or 0) for v in _t2.values())
        _late_links = sum(int(v.get("vendor_linked") or 0) for v in _t2.values())
        if _late or _late_links:
            step_summaries.append(
                f"[Feature B] Late migration write reconciled: {_late_links} linked, "
                f"{_late} duplicate row(s) removed"
            )

    if not notes:
        notes.append("No recognized files were provided for ingestion.")

    summary = "\n\n".join(notes)
    context_parts = [
        f"Single-door preprocessing for session {session_id}: {summary}",
    ]
    if n_documents_done > 0:
        if skip_row_match:
            context_parts.append(
                "DOCUMENT REGISTER: Files are indexed in Doc RAG. The user will match "
                "rows in the orchestrator Row match panel (table select, similarity, "
                "confirm document_id). Continue with query_docs / hybrid UDR as needed; "
                "do not re-run index_document unless asked."
            )
        else:
            context_parts.append(
                "DOCUMENT REGISTER: Files are indexed in Doc RAG. Row matching results "
                "are already in the response — present matched table columns, row_data, "
                "and chunk similarity scores. Continue with query_docs / hybrid UDR as needed; "
                "do not re-run index_document or match_document_to_rows unless asked."
            )
    if n_contract_perf > 0:
        context_parts.append(
            "FEATURE B — CONTRACT PERFORMANCE: "
            + " ".join(cp_notes)
            + " Use contract_performance tools / Vendors Saved Space for PM confirm, "
            "scorecards, and invoice line decisions. Do not re-extract unless asked."
        )
    if n_compliance > 0:
        context_parts.append(
            "FEATURE A — COMPLIANCE ENGINE: "
            + " ".join(compliance_notes)
            + " Draft certificate rows (if any) are already persisted — call "
            "list_building_certificates / get_compliance_saved_space_summary to show them "
            "(draft=true until PM confirms). Do NOT say 'no certificates found' if a draft "
            "id was just created. Use Compliance Saved Space for confirm; do not re-extract "
            "unless asked."
        )
    if n_structured_done > 0:
        context_parts.append(
            "STRUCTURED DATA: Migration mapping/hierarchy completed for CSV/Excel. "
            "Use udr_* / query_table for tabular answers."
        )
    if n_schema_started > 0 or n_migration_awaiting > 0:
        context_parts.append(
            "SCHEMA / MIGRATION UI: Pipeline paused for human gates. The user completes "
            "pre-semantic, field mapping, and hierarchy review in the orchestrator side "
            "panels (same as dedicated Schema Mapper / Migration Ingestor UIs). "
            "Do not auto-submit gates unless the user asks."
        )
    if mixed_tracks:
        context_parts.append(
            "MIXED UPLOAD (two independent tracks): The CSV/Excel files were migrated as "
            "ONE structured job (Migration panel); the PDF/Word/TXT/image files were indexed "
            "separately in Doc RAG (Documents / Row match panel), one document_id each. "
            "Structured ran first, then the documents."
        )
    context_parts.append(
        "Do NOT call index_document, start_migration, or start_migration_multi again for "
        "these same files. For multiple CSV/Excel files in one upload, ONE migration "
        "(start_migration_multi) was used — each file and each Excel sheet is a source table "
        "inside that single migration_id (not one migration per file or per sheet). "
        "Execute the user's full multi-step request in this turn."
    )
    context_note = " ".join(context_parts)
    log.info(
        "single_door.sequence.done",
        session_id=session_id,
        structured=n_structured,
        documents=n_documents,
        skipped=n_skipped,
        detected_engines=detected_engines,
    )
    # Scope the next question to what was just ingested. A user who has watched five
    # certificates land and asks "is it expired?" means those five, not the register.
    record_ingested_certificates(session_id, ingested_certificate_ids)
    return SingleDoorResult(
        summary_text=summary,
        tool_calls=tool_calls,
        context_note=context_note,
        step_summaries=step_summaries,
        match_report="\n\n".join(combined_match_report).strip(),
        detected_engines=detected_engines,
    )


def ensure_upload_dir(path_str: str) -> Path:
    path = Path(path_str)
    path.mkdir(parents=True, exist_ok=True)
    return path


def sanitize_filename(name: str) -> str:
    safe = "".join(ch for ch in name if ch.isalnum() or ch in ("-", "_", ".", " "))
    safe = safe.strip().replace(" ", "_")
    return safe or "upload.bin"


def remove_files(paths: list[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass
