"""Feature 7 — UDR pipeline orchestrator (Stage 0 → Stage 10), PURE.

Composes the value-centric primitives into one run over a single in-memory batch of
tables (``dict[table_name, list[dict]]`` — the migration ``full_tables``). No DB / LLM /
LangGraph state here, so the whole pipeline is unit-testable in isolation; the async
side-effects (persisting the relationship graph, emitting the Activity-Log entry) live in
:mod:`udr.persistence` / :mod:`udr.emit` and are invoked by the LangGraph node adapter
(``graph/nodes/udr_node.py``) AFTER this returns.

Sequence (see FEATURE7_UDR_UNDERSTANDING.md):
  S0/S1  optional pre-processing (off by default — Node 5 already deduped/cleaned)
  S2     per-table primary-key detection
  S7     optional shared-attribute → reference-table promotion
  S5/S8  PK / FK / Shared-Attribute classification (referential integrity)
  S8/S9  Test 1 (chunk → PK; vacuous for the structured flow) + Test 2 (similarity → FK)
  S10    relationship graph + final table/column metadata
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import logging

from .graph import build_relationship_graph
from .primitives import build_table_metadata, classify_columns, detect_primary_key
from .reference_tables import promote_shared_attribute
from .unique_tables import apply_consolidation, identify_unique_tables
from .validation import run_coverage_check, run_test1_chunk_pk, run_test2_column_fk


logger = logging.getLogger(__name__)

# Per-stage SLA targets (ms) from the Feature-7 metric ACs. Not hard-enforced (a wall-clock
# SLA can't be guaranteed in code) — measured and surfaced so an overrun is observable.
STAGE_SLA_MS: dict[str, int] = {
    "preprocessing": 30_000,    # 7.2 AC7 — pre-processing summary within 30s
    "prefix_columns": 10_000,   # 7.5 AC4 — prefixing within 10s (≤500 columns)
    "test1": 60_000,            # 7.9 AC4 — Test 1 report within 60s
    "test2": 120_000,           # 7.10 AC5 — Test 2 report within 120s
    "hierarchy": 300_000,       # 7.11 AC2 — relationship graph within 5min
}


def check_stage_slas(stage_durations: dict) -> dict:
    """Compare measured stage durations against their SLA targets → {stage: {duration_ms,
    target_ms, exceeded}}. Surfaces overruns (observability) without blocking the run."""
    out: dict[str, dict] = {}
    for stage, target in STAGE_SLA_MS.items():
        dur = stage_durations.get(stage)
        if dur is None:
            continue
        out[stage] = {
            "duration_ms": dur,
            "target_ms": target,
            "exceeded": bool(dur > target),
        }
    return out


@dataclass
class UdrRunResult:
    """Everything a run produces — fed to persistence (graph) + emit (activity)."""

    run_id: str
    graph: dict                       # {tables, columns, relationships} (JSON-safe)
    classification: dict              # {(table, column): {classification, ...}} (internal)
    test1_report: dict
    test2_report: dict
    table_count: int
    column_count: int
    relationship_count: int
    coverage_report: dict = field(default_factory=dict)   # 7.6 AC6 + 7.7 AC5
    sla_report: dict = field(default_factory=dict)        # stage durations vs metric-AC targets
    table_metadata: list = field(default_factory=list)   # == graph["tables"]
    column_metadata: list = field(default_factory=list)   # == graph["columns"]
    mapping_decisions: list = field(default_factory=list)
    documents_ingested: int = 0
    blocked: bool = False
    status: str = "completed"         # completed | pending_human_input
    # 7.3 — source tables auto-collapsed as the same entity, + candidates needing confirmation
    consolidated_groups: list = field(default_factory=list)
    unique_table_candidates: list = field(default_factory=list)
    # B7.1→B12.1 — structured table-resolution report (metadata cards · PK detection ·
    # deterministic / RAG / semantic table mapping · final decisions) for the Migration
    # Analysis stepper. Empty dict when there are no source tables.
    table_resolution: dict = field(default_factory=dict)
    # B13.1→B21.1 — column-intelligence report (prefixing · metadata · FK candidates · format/
    # value gates · grouping · unified names · classification · dest mapping). + the flat
    # (table.col → canonical) unified-name map (B19/B20 closing line).
    column_intelligence: dict = field(default_factory=dict)
    unified_names: dict = field(default_factory=dict)
    # Section 2 — wall-clock time each pipeline stage produced its result (stage → ISO)
    # and how long that stage took (stage → ms; elapsed since the previous stage).
    stage_times: dict = field(default_factory=dict)
    stage_durations: dict = field(default_factory=dict)


def _ordered_columns(rows: list[dict]) -> list[str]:
    """First-seen-order union of keys across (possibly ragged) rows."""
    cols: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r.keys():
            if k not in seen:
                seen.add(k)
                cols.append(k)
    return cols


def _decisions_from_columns(graph_columns: list[dict]) -> list[dict]:
    """Derive the traceability feed (AL.6 AC4) from final column metadata when the
    caller supplies none. ``full_tables`` columns are already renamed to the destination
    names, so ``column`` is both the source key and the dest column; confidence is unknown
    here (the node adapter can override with mapper-joined confidences)."""
    return [
        {
            "source_table": c.get("source_table"),
            "source_column": c.get("column"),
            "confidence": None,
            "dest_table": c.get("dest_udr_table"),
            "dest_column": c.get("column"),
        }
        for c in graph_columns
    ]


def run_udr_pipeline(
    tables_in: dict[str, list[dict]],
    *,
    run_id: str,
    dest_table_by_source: dict[str, str] | None = None,
    documents_ingested: int = 0,
    run_preprocessing: bool = False,
    shared_attribute_columns: list | None = None,
    mapping_decisions: list[dict] | None = None,
    chunks: list[dict] | None = None,
    consolidate_tables: bool = True,
    confidence_by_source: dict[str, float] | None = None,
    canonical_overrides: dict[str, str] | None = None,
    pk_override_by_table: dict[str, list[str]] | None = None,
    classification_decisions: dict | None = None,
    alias_resolver=None,
    field_resolver=None,
    clock=None,
) -> UdrRunResult:
    """Run the value-centric UDR pipeline over one batch of tables.

    ``tables_in`` is ``{table_name: list[dict]}`` (use the migration ``full_tables`` — the
    COMPLETE rows; never the 5-row sample or the deduped ``cleaned_tables`` subset, or PK
    uniqueness / referential-integrity ratios will be computed against a subset).

    ``chunks`` defaults to ``[]`` — the structured CSV/Excel flow has no document chunks,
    so Test 1 is a vacuous pass (this is correct, not a failure). Pass real chunks only for
    a doc-sourced run.
    """
    tables_in = tables_in or {}
    now = clock or datetime.utcnow
    stage_times: dict[str, str] = {}
    stage_durations: dict[str, float] = {}
    _perf = [time.perf_counter()]

    def _stamp(stage: str) -> None:
        """Record stage wall-clock time + its elapsed ms since the previous stamp,
        and emit a per-stage processing log line (DEBUG)."""
        t = time.perf_counter()
        stage_durations[stage] = round((t - _perf[0]) * 1000, 2)
        _perf[0] = t
        stage_times[stage] = now().isoformat()
        logger.debug("[UDR] stage '%s' done (%sms)", stage, stage_durations[stage])

    logger.debug(
        "[UDR] IN run_id=%s tables=%d names=%s docs=%d",
        run_id, len(tables_in), list(tables_in.keys())[:25], int(documents_ingested or 0),
    )

    _stamp("preprocessing")  # ~0ms here — clean/dedup ran upstream (Node 5), not in this pass

    # ── 7.3 — collapse source sheets that are the SAME entity (different names) ──
    # Auto-consolidates name>=95% AND metadata>=80%; surfaces lower-name-similarity matches
    # as candidates for user confirmation (not auto-merged). Per-table metadata + the unique
    # report are built unconditionally so the B7.1→B12.1 Migration-Analysis report can be
    # produced from the PRE-consolidation source tables (the cards mirror the upload).
    consolidated_groups: list = []
    unique_candidates: list = []
    table_resolution: dict = {}
    source_tables_in = dict(tables_in)  # snapshot before consolidation for the report/cards
    if tables_in:
        metas = [
            build_table_metadata(name, list(rows or []), _ordered_columns(list(rows or [])))
            for name, rows in tables_in.items()
        ]
        unique_report = identify_unique_tables(metas) if len(metas) > 1 else {
            "auto_consolidated": [], "candidates": [], "unique": [m["table"] for m in metas]
        }
        consolidated_groups = unique_report.get("auto_consolidated", [])
        unique_candidates = unique_report.get("candidates", [])
        try:
            from .table_resolution import build_table_resolution

            table_resolution = build_table_resolution(
                source_tables_in,
                table_metas=metas,
                unique_report=unique_report,
                dest_table_by_source=dest_table_by_source or {},
                confidence_by_source=confidence_by_source or {},
                alias_resolver=alias_resolver,
                pk_override_by_table=pk_override_by_table or {},
            )
        except Exception as e:  # pragma: no cover — the report must never break the run
            logger.warning("[UDR] table_resolution report skipped: %s", e)
            table_resolution = {}
        if consolidate_tables and consolidated_groups:
            tables_in = apply_consolidation(tables_in, consolidated_groups)
    logger.debug(
        "[UDR] unique_tables: auto_consolidated=%d candidates=%d -> %d tables",
        len(consolidated_groups), len(unique_candidates), len(tables_in),
    )
    _stamp("unique_tables")

    # ── S0/S1 (optional) + S2: normalise each table to {rows, columns, pk} ──────
    tables: dict[str, dict] = {}
    for name, rows in tables_in.items():
        rows = list(rows or [])
        if run_preprocessing:
            from .preprocessing import remove_exact_duplicate_rows

            rows, _ = remove_exact_duplicate_rows(rows)
        columns = _ordered_columns(rows)
        pk = detect_primary_key(rows, columns).get("columns", [])
        tables[name] = {"rows": rows, "columns": columns, "pk": pk}

    # ── S7 (optional): promote a shared attribute to its own reference table ────
    if shared_attribute_columns:
        ref = promote_shared_attribute(tables, shared_attribute_columns)
        ref_table = ref.get("table") if isinstance(ref, dict) else None
        ref_rows = ref.get("rows") if isinstance(ref, dict) else None
        if ref_table and ref_rows is not None:
            cols = _ordered_columns(ref_rows)
            tables[ref_table] = {
                "rows": ref_rows,
                "columns": cols,
                "pk": detect_primary_key(ref_rows, cols).get("columns", []),
            }

    # ── S5/S8: classify every column PK / FK / Shared-Attribute ─────────────────
    pk_by_table = {name: meta["pk"] for name, meta in tables.items()}
    classification = classify_columns(tables, pk_by_table)

    _stamp("prefix_columns")

    logger.debug("[UDR] classify_columns done across %d tables", len(tables))

    # ── B13.1→B21.1: column-intelligence report (prefixing · metadata · FK candidates ·
    # format/value gates · grouping · unified names · classification · dest mapping) for the
    # Processing-Log Section 2. Pure; best-effort — never breaks the run.
    column_intelligence: dict = {}
    unified_names: dict = {}
    try:
        from .column_intelligence import build_column_intelligence

        _dest_col = {}
        for d in mapping_decisions or []:
            st, sc, dc = d.get("source_table"), d.get("source_column"), d.get("dest_column")
            if st and sc and dc:
                _dest_col[(st, sc)] = dc
        column_intelligence = build_column_intelligence(
            tables,
            dest_table_by_source=dest_table_by_source or {},
            dest_col_by_source_col=_dest_col,
            field_resolver=field_resolver,
            canonical_overrides=canonical_overrides,
        )
        unified_names = column_intelligence.get("column_canonical", {})
        # Re-apply the B20.1 classification-gate decisions (rejections + FK ↔ Shared
        # re-classifications) so the post-write report always matches what the human approved —
        # without this the fresh rebuild would resurrect classifications the user overrode.
        if column_intelligence and isinstance(classification_decisions, dict) and (
            classification_decisions.get("rejected") or classification_decisions.get("overrides")
        ):
            from .column_intelligence import apply_classification_decisions

            _gate_applied = apply_classification_decisions(
                column_intelligence, tables,
                rejected=classification_decisions.get("rejected"),
                overrides=classification_decisions.get("overrides"),
            )
            logger.debug("[UDR] classification-gate decisions re-applied: %s", _gate_applied)
    except Exception as e:  # pragma: no cover — additive, never fatal
        logger.warning("[UDR] column_intelligence report skipped: %s", e)

    # ── S10: relationship graph + final metadata ────────────────────────────────
    graph = build_relationship_graph(tables, classification, dest_table_by_source)
    relationships = graph.get("relationships", [])
    logger.debug("[UDR] relationship_graph: %d columns, %d relationships",
                 len(graph.get("columns", [])), len(relationships))

    # ── S8/S9: Test 1 (chunk→PK) + Test 2 (similarity→FK) ───────────────────────
    _stamp("vector_chunking")
    test1 = run_test1_chunk_pk(list(chunks or []), tables)
    _stamp("test1")
    defined_fks = [
        (r["src_entity"], r["src_column"], r["dst_entity"], r["dst_column"]) for r in relationships
    ]
    test2 = run_test2_column_fk(tables, defined_fks)
    _stamp("test2")
    logger.debug("[UDR] test1 pass=%s blocks=%s | test2 pass=%s blocks=%s",
                 test1.get("passed"), test1.get("blocks_udr"),
                 test2.get("passed"), test2.get("blocks_udr"))
    # 7.6 AC6 + 7.7 AC5 — every column must be classified AND assigned before finalising.
    coverage = run_coverage_check(tables, graph)
    _stamp("hierarchy")

    # Stage durations vs the Feature-7 metric-AC targets (observability; never blocks).
    sla_report = check_stage_slas(stage_durations)
    for _stage, _s in sla_report.items():
        if _s["exceeded"]:
            logger.warning(
                "[UDR] stage '%s' exceeded its target: %dms > %dms",
                _stage, _s["duration_ms"], _s["target_ms"],
            )

    blocked = (
        bool(test1.get("blocks_udr"))
        or bool(test2.get("blocks_udr"))
        or bool(coverage.get("blocks_udr"))
    )
    decisions = mapping_decisions if mapping_decisions is not None else _decisions_from_columns(
        graph.get("columns", [])
    )

    logger.info(
        "[UDR] OUT run_id=%s tables=%d columns=%d relationships=%d blocked=%s total=%sms",
        run_id, len(tables), len(graph.get("columns", [])), len(relationships),
        blocked, round(sum(stage_durations.values()), 1),
    )

    return UdrRunResult(
        run_id=run_id,
        graph=graph,
        classification=classification,
        test1_report=test1,
        test2_report=test2,
        coverage_report=coverage,
        sla_report=sla_report,
        table_count=len(graph.get("tables", [])),
        column_count=len(graph.get("columns", [])),
        relationship_count=len(relationships),
        table_metadata=graph.get("tables", []),
        column_metadata=graph.get("columns", []),
        mapping_decisions=decisions,
        documents_ingested=documents_ingested,
        blocked=blocked,
        status="pending_human_input" if blocked else "completed",
        consolidated_groups=consolidated_groups,
        unique_table_candidates=unique_candidates,
        table_resolution=table_resolution,
        column_intelligence=column_intelligence,
        unified_names=unified_names,
        stage_times=stage_times,
        stage_durations=stage_durations,
    )
