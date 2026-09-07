"""Feature 4 — Activity Log: UDR-run activity entry builder (User Flow AL.6).

Pure functions (no DB/LLM) that turn a completed UDR run into:
  - the one-line Activity Summary outcome (AL.6 AC2 format) + Test 1/2 flag counts
    (AL.6 AC5) + status + notification colour, and
  - the structured Processing Log covering every Feature-7 stage (AL.6 AC3) plus the
    per-mapping traceability ``source → confidence → destination`` (AL.6 AC4).

The emit hook (persisting an ``ActivityLogEntry`` + flipping the notification) is wired
into the Feature-7 pipeline once its node-wiring lands; this module is the testable core.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# AL.6 AC3 — the processing log covers the FULL logical UDR process, in order:
# Layer 1 (structured) → Layer 2 (vectors + reference tables + Tests) → Layer 3 (graph)
# → global registry update → Jobs 1/2 (LLM enrichment).
UDR_STAGES: list[tuple[str, str]] = [
    ("preprocessing", "Layer 1 · Pre-processing (NaN / duplicates / column merge)"),
    ("unique_tables", "Layer 1 · Unique table identification (name + metadata)"),
    ("deterministic", "Layer 1 · Deterministic table mapping (exact + Levenshtein + RAG/alias)"),
    ("semantic", "Layer 1 · Semantic table mapping (NLP + metadata)"),
    # Column-intelligence stages — one per left-panel "Column Intelligence Pipeline" card, in the
    # SAME order and with the SAME names the cards use (B13→B21), so each card has a 1:1 matching
    # Activity-Log entry. (column_within_source used to also cover grouping + classification; those
    # are now their own stages — column_grouping / column_classification — and column_metadata was
    # split out of column_to_destination.)
    ("table_prefixing", "B13.1 · Table prefixing"),
    ("column_within_source", "B17.1 · B18.1 · Column similarity within source · format + value-pattern"),
    ("column_grouping", "B19.1 · Group similar columns (name-agnostic) + unified name"),
    ("column_classification", "B20.1 · Classify each group · PK / FK / Shared"),
    ("column_canonicalisation", "Unified column names"),
    ("column_metadata", "B14.1 · B15.1 · Column metadata — 4 dimensions + canonical name"),
    ("column_to_destination", "B21.1 · Destination column mapping"),
    ("vector_chunking", "Layer 2 · Vector chunking + primary-key association"),
    ("reference_promotion", "Layer 2 · Shared-attribute → reference table"),
    ("test1", "Test 1 · Chunk associations → primary key"),
    ("test2", "Test 2 · Column similarity → foreign key"),
    ("hierarchy", "Layer 3 · Hierarchy / relationship graph"),
    ("incremental_registry", "Global · Incremental UDR registry update"),
    ("semantic_discovery", "Job 1 · Semantic relationship discovery (LLM)"),
    ("sanctity_check", "Job 2 · Sanctity checking (LLM)"),
]

# Chain-of-thought vs chain-of-action per stage: planning/decision = CoT, data-movement = CoA.
_STAGE_CHAIN: dict[str, str] = {
    "preprocessing": "action",
    "unique_tables": "thought",
    "deterministic": "action",
    "semantic": "action",
    "table_prefixing": "action",
    "column_within_source": "thought",
    "column_grouping": "action",
    "column_classification": "action",
    "column_canonicalisation": "action",
    "column_metadata": "action",
    "column_to_destination": "action",
    "vector_chunking": "action",
    "reference_promotion": "action",
    "test1": "action",
    "test2": "action",
    "hierarchy": "action",
    "incremental_registry": "action",
    "semantic_discovery": "thought",
    "sanctity_check": "thought",
}

# Per-stage execution metrics (AL.6 — stage metrics): which agent ran the stage and the
# concrete tool/strategy it used. These describe the real pipeline executors (not invented
# run data) so every Section-2 step reads as an audit record (agent · tool · in→out · status)
# rather than a bare log line.
_STAGE_AGENT: dict[str, str] = {
    "decomposition": "Query Planner",
    "preprocessing": "Pre-processing Agent",
    "unique_tables": "Table Identity Agent",
    "deterministic": "Deterministic Mapper",
    "semantic": "Semantic Mapper",
    "table_prefixing": "Column Prefixer",
    "column_within_source": "Column Intelligence",
    "column_grouping": "Column Intelligence",
    "column_classification": "Column Intelligence",
    "column_canonicalisation": "Column Intelligence",
    "column_metadata": "Column Intelligence",
    "column_to_destination": "Column Mapper",
    "vector_chunking": "Vector Engine",
    "reference_promotion": "Reference Builder",
    "test1": "Validation Engine",
    "test2": "Validation Engine",
    "hierarchy": "Relationship Grapher",
    "incremental_registry": "Registry Service",
    "semantic_discovery": "LLM Discovery (Job 1)",
    "sanctity_check": "LLM Sanctity (Job 2)",
}

_STAGE_TOOL: dict[str, str] = {
    "decomposition": "intent classifier",
    "preprocessing": "NaN/duplicate scan + value-merge",
    "unique_tables": "name + metadata similarity",
    "deterministic": "exact + Levenshtein + RAG/alias",
    "semantic": "NLP semantic match",
    "table_prefixing": "destination-table prefixing",
    "column_within_source": "format + value-pattern similarity",
    "column_grouping": "name-agnostic value/format grouping",
    "column_classification": "PK / FK / Shared classifier",
    "column_canonicalisation": "5-tier canonical naming",
    "column_metadata": "4-dimension metadata + canonical name",
    "column_to_destination": "column → UDR column matcher",
    "vector_chunking": "chunker + PK anchoring",
    "reference_promotion": "shared-attr → reference table",
    "test1": "chunk → PK verifier",
    "test2": "similarity → FK verifier",
    "hierarchy": "PK/FK graph builder",
    "incremental_registry": "canonical registry append",
    "semantic_discovery": "LLM relationship reader",
    "sanctity_check": "LLM cross-check",
}

# completed → green, escalated → orange, failed / pending → red (AL.1 AC4).
_NOTIF_BY_STATUS = {
    "completed": "green",
    "escalated": "orange",
    "failed": "red",
    "pending_human_input": "red",
}


def notif_color(status: str) -> str:
    """Notification-icon colour for a status (AL.1 AC4)."""
    return _NOTIF_BY_STATUS.get(status, "green")


def format_activity_timestamp(dt: datetime) -> str:
    """AL.1 AC3 timestamp format: ``HH:MM am/pm DD Month YY`` (e.g. '2:32 pm 24 April 26')."""
    hour12 = dt.strftime("%I").lstrip("0") or "12"
    minute = dt.strftime("%M")
    ampm = dt.strftime("%p").lower()
    return f"{hour12}:{minute} {ampm} {dt.day} {dt.strftime('%B')} {dt.strftime('%y')}"


def _clean(step: dict) -> dict:
    """Drop None/empty fields so the streamed step payload stays compact."""
    return {k: v for k, v in step.items() if v not in (None, "", [], {})}


def build_processing_steps(
    stages: list[dict],
    *,
    decomposition_text: str | None = None,
    intent_confidence: float | None = None,
    stage_meta: dict[str, dict] | None = None,
) -> list[dict]:
    """Ordered Chain-of-thought / Chain-of-action step list for the Processing Log
    (Activity-Log Section 2). One leading CoT "Query decomposition" step, then one step
    per Feature-7 stage classified CoT/CoA. ``stage_meta[stage]`` supplies the real
    per-stage extras the pipeline captured: ``at`` (timestamp), ``confidence``, ``chips``
    (badge labels), ``action_ref`` ({id,label}), ``table`` ({columns,rows}), ``status``,
    and may override ``chain``/``text``. Absent extras are simply omitted.
    """
    meta = stage_meta or {}
    plan = " → ".join(label for _, label in UDR_STAGES)
    decomp = meta.get("decomposition", {})
    steps: list[dict] = [
        _clean({
            "kind": "thought",
            "stage": "decomposition",
            "label": "Query decomposition",
            "text": decomposition_text
            or f"Matched to top-level UDR intent. Decomposing into {len(UDR_STAGES)} sub-tasks: {plan}.",
            "at": decomp.get("at"),
            "confidence": intent_confidence if intent_confidence is not None else decomp.get("confidence"),
            # Stage metrics (AL.6 point 12): agent · tool · in→out · status on the lead step too.
            "agent": decomp.get("agent") or _STAGE_AGENT["decomposition"],
            "tool": decomp.get("tool") or _STAGE_TOOL["decomposition"],
            "input_count": decomp.get("input_count", 1),
            "output_count": decomp.get("output_count", len(UDR_STAGES)),
            "status": decomp.get("status") or "completed",
        })
    ]
    for st in stages:
        key = st.get("stage")
        m = meta.get(key, {}) if key else {}
        steps.append(
            _clean({
                "kind": m.get("chain") or _STAGE_CHAIN.get(key, "action"),
                "stage": key,
                "label": st.get("label"),
                "text": m.get("text") or st.get("summary"),
                "at": m.get("at"),
                "duration_ms": m.get("duration_ms"),
                "confidence": m.get("confidence"),
                "chips": m.get("chips"),
                "action_ref": m.get("action_ref"),
                "table": m.get("table"),
                "status": m.get("status"),
                # Stage metrics (AL.6 point 12) — emitted by build_stage_meta per stage.
                "agent": m.get("agent"),
                "tool": m.get("tool"),
                "input_count": m.get("input_count"),
                "output_count": m.get("output_count"),
            })
        )
    return steps


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


def build_stage_meta(result: Any, preprocessing_report: dict | None = None) -> dict[str, dict]:
    """Derive the per-stage Processing-Log extras (chips · mini-tables · action refs ·
    confidence · timestamps) from a finished ``UdrRunResult`` — pure, reads only attributes
    the pipeline already produces. Keyed by UDR stage so :func:`build_processing_steps` can
    attach them. ``preprocessing_report`` (from the upstream clean/dedupe node) supplies the
    pre-processing NaN/duplicate/column-merge counts. Stages with nothing to add still get a
    timestamp if the run captured one.
    """
    meta: dict[str, dict] = {}

    # preprocessing (CoA): NaN/duplicate removal + column auto-merge vs flagged-for-review,
    # surfaced from the upstream pre-processing node's report.
    pp = preprocessing_report or {}
    pp_chips: list[str] = []
    auto_merged = int(pp.get("auto_merged_count", pp.get("auto_merged", 0)) or 0)
    flagged = int(pp.get("flagged_count", pp.get("merge_candidates", 0)) or 0)
    nan_removed = int(pp.get("nan_removed", 0) or 0)
    dups_removed = int(pp.get("duplicates_removed", pp.get("dup_removed", 0)) or 0)
    if nan_removed:
        pp_chips.append(f"{nan_removed} NaN removed")
    if dups_removed:
        pp_chips.append(_plural(dups_removed, "duplicate") + " removed")
    if auto_merged:
        pp_chips.append(_plural(auto_merged, "column") + " auto-merged")
    if flagged:
        pp_chips.append(_plural(flagged, "flagged"))
    pre: dict[str, Any] = {}
    if pp_chips:
        pre["chips"] = pp_chips
    if flagged:
        pre["action_ref"] = {"id": "pp_merge", "label": _plural(flagged, "column merge") + " — confirmation required"}
    if pre:
        meta["preprocessing"] = pre

    # unique_tables (CoT): table consolidation outcome + candidates needing confirmation.
    groups = list(getattr(result, "consolidated_groups", None) or [])
    cands = list(getattr(result, "unique_table_candidates", None) or [])
    ut: dict[str, Any] = {}
    ut_chips = []
    if groups:
        ut_chips.append(_plural(len(groups), "auto-consolidated"))
    if cands:
        ut_chips.append(_plural(len(cands), "candidate"))
        ut["action_ref"] = {"id": "udr_consolidation", "label": _plural(len(cands), "table consolidation candidate") + " — confirm"}
    if ut_chips:
        ut["chips"] = ut_chips
    if ut:
        meta["unique_tables"] = ut

    # prefix_columns (CoA): PK/FK/Shared counts + the source→dest column-mapping table.
    classification = getattr(result, "classification", None) or {}
    counts = {"PK": 0, "FK": 0, "SHARED_ATTRIBUTE": 0}
    for v in classification.values():
        k = (v or {}).get("classification")
        if k in counts:
            counts[k] += 1
    cls_chips = []
    if counts["PK"]:
        cls_chips.append(_plural(counts["PK"], "Primary Key"))
    if counts["FK"]:
        cls_chips.append(_plural(counts["FK"], "Foreign Key"))
    if counts["SHARED_ATTRIBUTE"]:
        cls_chips.append(_plural(counts["SHARED_ATTRIBUTE"], "Shared Attribute"))

    decisions = list(getattr(result, "mapping_decisions", None) or [])
    rows = []
    # Show up to 200 rows so the B21.1 destination-mapping table lists EVERY mapped column across
    # all tables on the completed card too (was capped at 10 — a 4-table/44-column run truncated).
    for d in decisions[:200]:
        st, sc = d.get("source_table") or "", d.get("source_column") or ""
        dt, dc = d.get("dest_table") or "", d.get("dest_column") or ""
        conf = d.get("confidence")
        conf_s = f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
        src = f"{sc} ({st})" if st else sc
        dst = f"{dt}.{dc}" if dt and dc else (dc or dt or "—")
        rows.append([src, dst, conf_s])
    # Step 1 (within source): the PK/FK/Shared classification chips.
    if cls_chips:
        meta["column_within_source"] = {"chips": cls_chips}
    # Step 2 (to destination): the source → destination column mapping table + confidence.
    if rows:
        meta["column_to_destination"] = {
            "table": {"columns": ["Source column", "→ Destination UDR column", "Confidence"], "rows": rows},
        }

    # B13.1→B21.1 — surface the Column-Intelligence Pipeline summary onto the Section-2 stages
    # (group counts, FK candidates, format-gate survivors, unified names, columns mapped).
    ci = getattr(result, "column_intelligence", None) or {}
    if ci:
        ci_sum = ci.get("summary", {}) or {}
        ci_groups = ci.get("groups", []) or []
        cws_chips = list(cls_chips)
        if ci_sum.get("groups"):
            cws_chips.append(_plural(int(ci_sum["groups"]), "similar-column group"))
        if ci_sum.get("format_survivors"):
            cws_chips.append(
                f"{ci_sum['format_survivors']} of {ci_sum.get('format_pairs_scored', 0)} pairs survive format gate"
            )
        if ci.get("fk_candidates"):
            cws_chips.append(_plural(len(ci["fk_candidates"]), "FK candidate"))
        meta.setdefault("column_within_source", {})["chips"] = cws_chips
        # column canonicalisation — N groups unified + up to 3 (group → canonical) sample rows.
        if ci_groups:
            uni_rows = [
                [g.get("group_id", ""), ", ".join((g.get("members") or [])[:3]), g.get("canonical_name", "")]
                for g in ci_groups[:3]
            ]
            meta["column_canonicalisation"] = {
                "chips": [_plural(len(ci_groups), "group") + " unified"],
                "table": {"columns": ["Group", "Members", "Canonical name"], "rows": uni_rows},
                "text": (
                    "All similar columns within a value/format group are given ONE canonical name "
                    "(PK name → destination → naming consensus → FM ontology / format) so downstream "
                    "stages reference the same identifier."
                ),
            }
        if ci_sum.get("columns_mapped"):
            meta.setdefault("column_to_destination", {}).setdefault("chips", []).append(
                _plural(int(ci_sum["columns_mapped"]), "column") + " mapped to destination"
            )
        # CAFM-005 — prefer the RICH dest_mapping table (Source · Destination · Confidence · Outcome),
        # keeping new-column / unmatched rows, so the completed "last step" B21.1 matches the
        # gate / analysis and live-run views EXACTLY (they all read column_intelligence.dest_mapping).
        # Overrides the 3-column mapping_decisions projection set above; that stays as the fallback
        # when a run has no dest_mapping. Mirrors run_activity._dest_mapping_rows (can't import it —
        # run_activity imports THIS module, so the reverse would be circular).
        dm = ci.get("dest_mapping")
        if isinstance(dm, list) and dm:
            dm_rows: list[list[str]] = []
            for m in dm:
                if not isinstance(m, dict) or len(dm_rows) >= 200:
                    continue
                _src = m.get("canonical_source") or m.get("source") or ""
                _dt = m.get("dest_table") or ""
                _mc = m.get("matched_column") or ""
                _dest = f"{_dt}.{_mc}" if (_dt and _mc) else "—"
                _conf = m.get("confidence")
                _conf_s = f"{round(_conf * 100)}%" if isinstance(_conf, (int, float)) else "—"
                _outcome = str(m.get("outcome") or "").replace("_", " ").strip()
                dm_rows.append([str(_src), _dest, _conf_s, _outcome or "—"])
            if dm_rows:
                meta.setdefault("column_to_destination", {})["table"] = {
                    "columns": ["Source column", "→ Destination UDR column", "Confidence", "Outcome"],
                    "rows": dm_rows,
                }

    # vector_chunking + test1 (CoA): chunk→PK anchoring outcome.
    t1 = getattr(result, "test1_report", None) or {}
    if t1.get("total_chunks"):
        vc_chips = [_plural(int(t1.get("passed", 0) or 0), "chunk") + " anchored to PK"]
        failed = int(t1.get("failed", 0) or 0)
        if failed:
            vc_chips.append(f"{failed} unresolvable")
        vc: dict[str, Any] = {"chips": vc_chips}
        if t1.get("blocks_udr"):
            vc["action_ref"] = {"id": "udr_test1", "label": "Test 1 failure — UDR blocked"}
            meta["test1"] = {"status": "error"}
        meta["vector_chunking"] = vc

    # test2 (CoA): unexplained column-similarity pairs + missing-FK action.
    t2 = getattr(result, "test2_report", None) or {}
    flags = list(t2.get("flags") or [])
    if flags:
        t2_rows = []
        for fl in flags[:6]:
            pair = f"{fl.get('table_a')}.{fl.get('column_a')} ↔ {fl.get('table_b')}.{fl.get('column_b')}"
            ov = fl.get("overlap")
            ov_s = f"{round(ov * 100)}%" if isinstance(ov, (int, float)) else "—"
            t2_rows.append([pair, ov_s, "Missing FK — review"])
        meta["test2"] = {
            "table": {"columns": ["Column pair", "Overlap", "Assessment"], "rows": t2_rows},
            "action_ref": {"id": "udr_test2", "label": _plural(len(flags), "missing FK relationship")},
        }
        if t2.get("blocks_udr"):
            meta["test2"]["status"] = "blocked"

    # hierarchy (CoA): relationship-graph size.
    rc = int(getattr(result, "relationship_count", 0) or 0)
    if rc:
        meta["hierarchy"] = {"chips": [_plural(rc, "relationship") + " mapped"]}

    # Guarantee a human-readable input -> output line on EVERY Layer-1 step so the whole
    # sequence is reviewable in the Processing Log even when a step has no chips/table.
    _pk, _fk, _sh = counts.get("PK", 0), counts.get("FK", 0), counts.get("SHARED_ATTRIBUTE", 0)
    _tcount = int(getattr(result, "table_count", 0) or 0)
    _ccount = int(getattr(result, "column_count", 0) or 0)
    _texts = {
        "preprocessing": (
            "Scanned every source table for NaN/empty cells and exact duplicate rows; removed them. "
            "Columns with different names but matching value patterns are merged (name inferred from "
            "values); 60-94% partial matches are flagged for the user."
        ),
        "unique_tables": (
            f"Built per-table metadata (primary key, column names, 3 sample values, column count — no FKs yet) "
            f"and identified unique tables on name + metadata similarity — {len(groups)} auto-consolidated, "
            f"{len(cands)} candidate(s) for confirmation."
        ),
        "deterministic": "Mapped source tables to the destination UDR by exact match + Levenshtein<=2, then RAG/alias for CMMS/CAFM-native terms (>=95% auto-resolved).",
        "semantic": "Resolved the remaining tables by semantic NLP match on table NAME + METADATA (multi-dimension), FM-domain tuned.",
        "table_prefixing": "Prefixed every source column with its assigned destination-UDR table so identically-named columns in different tables stay distinct.",
        "column_within_source": (
            f"Compared each column against every column in other tables on cell-value FORMAT, then VALUE pattern; "
            f"grouped similar columns name-agnostically and classified them — {_pk} Primary Key, {_fk} Foreign Key "
            f"(referential integrity verified), {_sh} Shared Attribute."
        ),
        "column_canonicalisation": (
            "Gave every column in each value/format group ONE canonical name (B19/B20) — PK name "
            "wins, else destination column, else naming consensus, else FM ontology / format — so "
            "tag_id, tagnum and asset_no (same asset codes) all reference one identifier downstream."
        ),
        "column_to_destination": "Matched each column group to destination-UDR columns of the same table (format + values + name); created a new column where none matched.",
        "vector_chunking": "Chunked unstructured documents and anchored each chunk to its parent entity's PRIMARY KEY (never a FK/shared attribute).",
        "reference_promotion": "Where a chunk could only anchor on a shared attribute, auto-created a reference table with that value as primary key, turning it into a valid foreign-key anchor.",
        "test1": (
            f"Verified every chunk association references a primary key — "
            f"{int(t1.get('passed', 0) or 0)} passed, {int(t1.get('failed', 0) or 0)} failed (target <1%)."
        ),
        "test2": f"Checked cross-table column similarities (>=30% value overlap) are each explained by a foreign key — {len(flags)} unexplained pair(s) flagged for review.",
        "hierarchy": (
            f"Pre-computed the Layer-3 relationship graph from primary/foreign-key metadata — {rc} relationship(s) "
            f"across {_tcount} tables / {_ccount} columns."
        ),
        "incremental_registry": "Appended this client's new tables/columns to the global canonical UDR registry (incremental, not rebuilt).",
        "semantic_discovery": "Job 1 (async): LLM reads across entity clusters to surface implicit relationships, written back labelled 'LLM-inferred' with confidence; >=0.75 surfaced for review.",
        "sanctity_check": "Job 2 (async): LLM cross-checks structured links against linked document content and flags likely misallocations (confidence >=0.80) as red action items.",
    }
    for _stage, _txt in _texts.items():
        meta.setdefault(_stage, {}).setdefault("text", _txt)

    # ── Per-stage execution metrics (AL.6 point 12): agent · tool · input/output counts ·
    # status on EVERY Layer-1 step, so each Section-2 row reads as an audit record. Counts
    # are derived ONLY from data already computed above (never invented); a stage with no
    # clean count simply omits input/output. Status defaults to "completed" and is preserved
    # for the blocking tests (test1/test2 already set their own status above).
    def _members_len(g: Any) -> int:
        if isinstance(g, dict):
            m = g.get("members") or g.get("tables") or []
            return len(m) if isinstance(m, list) else 0
        return 0

    _classified = _pk + _fk + _sh
    _collapsed = sum(max(_members_len(g) - 1, 0) for g in groups)
    _ci_mapped = int((ci.get("summary", {}) or {}).get("columns_mapped", 0) or 0) if ci else 0
    _t1_total = int(t1.get("total_chunks", 0) or 0)
    _t1_pass = int(t1.get("passed", 0) or 0)
    _t2_hits = int(t2.get("similarity_hits", 0) or 0)
    _t2_expl = int(t2.get("explained", 0) or 0)
    # stage → (input_count, output_count); None means "no clean count, omit".
    _io: dict[str, tuple[int | None, int | None]] = {
        "unique_tables": ((_tcount + _collapsed) or None, _tcount or None),
        "column_within_source": (_ccount or None, _classified or None),
        "column_to_destination": (_ccount or None, _ci_mapped or None),
        "vector_chunking": (_t1_total or None, _t1_pass or None),
        "test1": (_t1_total or None, _t1_pass or None),
        "test2": (_t2_hits or None, _t2_expl or None),
        "hierarchy": (_tcount or None, rc or None),
    }
    for _stage, _label in UDR_STAGES:
        sm = meta.setdefault(_stage, {})
        sm.setdefault("agent", _STAGE_AGENT.get(_stage))
        sm.setdefault("tool", _STAGE_TOOL.get(_stage))
        sm.setdefault("status", "completed")
        _inp, _outp = _io.get(_stage, (None, None))
        if _inp is not None:
            sm.setdefault("input_count", _inp)
        if _outp is not None:
            sm.setdefault("output_count", _outp)

    # Per-stage timestamps + durations captured during the run (creates an entry for stages
    # that otherwise have no extras, so the step still shows when it ran and how long it took).
    stage_times = getattr(result, "stage_times", None) or {}
    stage_durations = getattr(result, "stage_durations", None) or {}
    for stage, ts in stage_times.items():
        if ts:
            meta.setdefault(stage, {})["at"] = ts
    for stage, ms in stage_durations.items():
        if isinstance(ms, (int, float)):
            meta.setdefault(stage, {})["duration_ms"] = ms
    # The decomposition (intent) step happens at run start — anchor it to the first stamp.
    if stage_times:
        first_ts = next(iter(stage_times.values()), None)
        if first_ts:
            meta.setdefault("decomposition", {})["at"] = first_ts

    return meta


def build_summary_metrics(result: Any) -> list[dict[str, Any]]:
    """AL.6 point 7 — the *extra* Activity-Summary metric chips beyond the base
    documents/tables/columns/Test-1 set: PK / FK / Shared-Attribute counts, relationship
    edges, auto-consolidated source tables, tables flagged for review, columns mapped, and
    document chunks. Pure — derived entirely from a finished ``UdrRunResult``; a metric with
    a zero/absent value is simply omitted (no empty chips)."""
    classification = getattr(result, "classification", None) or {}
    counts = {"PK": 0, "FK": 0, "SHARED_ATTRIBUTE": 0}
    for v in classification.values():
        k = (v or {}).get("classification")
        if k in counts:
            counts[k] += 1

    ci = getattr(result, "column_intelligence", None) or {}
    ci_sum = ci.get("summary", {}) or {}
    t1 = getattr(result, "test1_report", None) or {}
    groups = list(getattr(result, "consolidated_groups", None) or [])
    cands = list(getattr(result, "unique_table_candidates", None) or [])
    rc = int(getattr(result, "relationship_count", 0) or 0)
    cm = int(ci_sum.get("columns_mapped", 0) or 0)
    chunks = int(t1.get("total_chunks", 0) or 0)

    metrics: list[dict[str, Any]] = []
    if counts["PK"]:
        metrics.append({"label": "primary keys", "value": counts["PK"]})
    if counts["FK"]:
        metrics.append({"label": "foreign keys", "value": counts["FK"]})
    if counts["SHARED_ATTRIBUTE"]:
        metrics.append({"label": "shared attrs", "value": counts["SHARED_ATTRIBUTE"]})
    if rc:
        metrics.append({"label": "relationships", "value": rc})
    if groups:
        metrics.append({"label": "auto-consolidated", "value": len(groups), "tone": "green"})
    if cands:
        metrics.append({"label": "review", "value": len(cands), "tone": "red"})
    if cm:
        metrics.append({"label": "columns mapped", "value": cm})
    if chunks:
        metrics.append({"label": "chunks", "value": chunks})
    return metrics


def build_udr_activity_entry(
    run_id: str,
    *,
    documents_ingested: int = 0,
    table_count: int = 0,
    column_count: int = 0,
    test1_report: dict | None = None,
    test2_report: dict | None = None,
    script_ref: Any = None,
    stage_summaries: dict[str, str] | None = None,
    mapping_decisions: list[dict] | None = None,
    trigger: str = "query",
    status_override: str | None = None,
    decomposition_text: str | None = None,
    intent_confidence: float | None = None,
    stage_meta: dict[str, dict] | None = None,
    extra_metrics: list[dict] | None = None,
) -> dict:
    """Build the Activity Summary entry + Processing Log for a completed UDR run.

    Outcome line (AL.6 AC2): ``"3 documents ingested, UDR run successful, UDR script
    saved — 20 tables, 101 columns"`` with Test 1/2 flag counts appended (AL.6 AC5).
    A blocking test (≥1% fail) sets status ``pending_human_input`` (red) since the run
    needs remediation before it can finalise. ``mapping_decisions`` carries the
    per-field ``source → confidence → destination`` traceability (AL.6 AC4).
    """
    t1 = test1_report or {}
    t2 = test2_report or {}
    t1_flags = int(t1.get("failed", 0) or 0)
    t2_flags = int(t2.get("flagged_unexplained", 0) or 0)
    blocked = bool(t1.get("blocks_udr")) or bool(t2.get("blocks_udr"))

    status = status_override or ("pending_human_input" if blocked else "completed")
    status_word = "blocked — review required" if blocked else "successful"

    outcome = (
        f"{documents_ingested} documents ingested, "
        f"UDR run {status_word}, "
        f"UDR script saved — {table_count} tables, {column_count} columns"
    )
    flag_parts = []
    if t1_flags:
        flag_parts.append(f"{t1_flags} Test 1 item{'s' if t1_flags != 1 else ''} flagged for review")
    if t2_flags:
        flag_parts.append(f"{t2_flags} Test 2 item{'s' if t2_flags != 1 else ''} flagged for review")
    if flag_parts:
        outcome += ", " + ", ".join(flag_parts)

    # ── processing log: one node per Feature-7 stage, with Test summaries filled in ──
    summaries = dict(stage_summaries or {})
    summaries.setdefault(
        "test1",
        f"{t1.get('passed', 0)}/{t1.get('total_chunks', 0)} chunks anchored to a primary key"
        + (f" — {t1_flags} flagged" if t1_flags else ""),
    )
    summaries.setdefault(
        "test2",
        f"{t2.get('explained', 0)}/{t2.get('similarity_hits', 0)} column similarities explained by a FK"
        + (f" — {t2_flags} flagged" if t2_flags else ""),
    )
    # The completed Processing Log must read in the SAME numbered sequence as the LIVE card
    # (build_run_activity_entry): 1 → 2.1…2.12 → 3 (review) → 4 (Preprocess & Validate) → 5
    # (Hierarchy) → 6 (Data artifacts) → post-write. UDR_STAGES names the mapping + Layer-2/3
    # stages but NOT the review gate (step 3) or the migration center steps Preprocess & Validate
    # (node 6 → step 4) / Data artifacts (node 9 → step 6); hierarchy (step 5) is pulled out of its
    # late Layer-3 slot into migration-flow position. Without these, the Node-10 finalise — which
    # REPLACES processing_log.steps wholesale — would DROP steps 3/4/5/6 the live card showed.
    _keys = [k for k, _ in UDR_STAGES]
    _cut = (_keys.index("column_to_destination") + 1) if "column_to_destination" in _keys else len(UDR_STAGES)
    _hier_label = dict(UDR_STAGES).get("hierarchy", "Layer 3 · Hierarchy / relationship graph")
    _pre = list(UDR_STAGES[:_cut])
    _post = [s for s in UDR_STAGES[_cut:] if s[0] != "hierarchy"]
    _flow = [
        ("semantic_review", "Schema Analysis — Table & Column Mapping"),
        ("semantic_tier2", "Semantic Mapping (Tier 2)"),
        ("field_mapping_review", "Field mapping review"),
        ("preprocess_validate", "Preprocess & Validate"),
        ("hierarchy", _hier_label),
        ("hierarchy_confirmation", "Hierarchy confirmation gate"),
        ("data_artifacts", "Data artifacts"),
    ]
    stages = [
        {"stage": key, "label": label, "summary": summaries.get(key, "")}
        for key, label in (_pre + _flow + _post)
    ]

    # Seed per-step extras for the injected flow steps — build_stage_meta only seeds status/chips
    # for UDR_STAGES keys, which the review / preprocess / artifacts steps are not; without this
    # they'd render status-less (and could be dropped by the FE's executed-only filter). hierarchy
    # keeps its build_stage_meta chips (relationship count) since we copy the incoming meta.
    _meta = {k: dict(v) for k, v in (stage_meta or {}).items() if isinstance(v, dict)}

    def _seed(stage: str, text: str, *, chain: str = "action", chips: list | None = None) -> None:
        m = dict(_meta.get(stage) or {})
        m.setdefault("status", "completed")
        m.setdefault("text", text)
        m.setdefault("chain", chain)
        if chips and not m.get("chips"):
            m["chips"] = chips
        _meta[stage] = m

    _md_ct = len(mapping_decisions or [])
    _seed(
        "semantic_tier2",
        "Tier-2 semantic field mapping — embedding similarity resolved the columns the deterministic "
        "pass left open; confident matches auto-accepted, the rest flagged for review.",
    )
    _seed(
        "semantic_review",
        "Human-in-the-loop review of the field mappings — approved auto-matched fields, resolved "
        "flagged / unmapped columns and created new destination columns where needed.",
        chain="thought",
        chips=[f"{_md_ct} field decision{'' if _md_ct == 1 else 's'}"] if _md_ct else None,
    )
    _seed(
        "field_mapping_review",
        "Human-in-the-loop review of the Tier-2 field mappings — accept / override / reject the "
        "flagged columns and decide how to handle any unmapped fields.",
        chain="thought",
    )
    _seed(
        "preprocess_validate",
        "De-duplicated rows, resolved nulls, coerced column types and ran JSON-Schema + foreign-key "
        "validation on the mapped data before hierarchy detection.",
    )
    _seed(
        "hierarchy",
        "Detected foreign-key relationships and the site → location → asset → work-order hierarchy "
        "(Layer 3 relationship graph).",
    )
    _seed(
        "hierarchy_confirmation",
        "Human-in-the-loop confirmation of the detected sites → locations → assets → work-order "
        "hierarchy before the data artifacts are generated.",
        chain="thought",
    )
    _seed(
        "data_artifacts",
        "Generated the nested JSON, flat CSV and SQL artifacts (plus the PDF summary) and uploaded "
        "them to Blob for handoff to the platform.",
        chips=[f"{table_count} table{'' if table_count == 1 else 's'}"] if table_count else None,
    )

    refs: dict[str, Any] = {"udr_run_id": run_id}
    if script_ref is not None:
        refs["script"] = script_ref
        # Run code (e.g. UDR-TF001-3-1-23-147-…) for the Section-1 summary grid.
        if isinstance(script_ref, str):
            refs["run_code"] = script_ref

    # Section-1 metric chips (AL.6) — structured so the Activity-Log summary card can
    # render the big-number chips from the sample instead of parsing the outcome line.
    metrics: list[dict[str, Any]] = [
        {"label": "documents", "value": documents_ingested},
        {"label": "dest. tables", "value": table_count},
        {"label": "dest. columns", "value": column_count},
    ]
    t1_total = int(t1.get("total_chunks", 0) or 0)
    if t1_total:
        t1_passed = int(t1.get("passed", 0) or 0)
        t1_pct = round(100.0 * t1_passed / t1_total, 1)
        metrics.append(
            {"label": "Test 1 pass", "value": f"{t1_pct}%", "tone": "red" if blocked else "green"}
        )
    # AL.6 point 7 — the expanded Activity-Summary set (PK/FK/Shared · relationships ·
    # auto-consolidated · review · columns mapped · chunks) derived from the run by
    # build_summary_metrics and threaded in by emit_udr_activity. De-duped by label so the
    # base chips above always win.
    if extra_metrics:
        _seen = {m.get("label") for m in metrics}
        for m in extra_metrics:
            if isinstance(m, dict) and m.get("label") not in _seen:
                metrics.append(m)
                _seen.add(m.get("label"))
    refs["metrics"] = metrics

    # Ordered CoT/CoA reasoning steps for the Processing Log (Section 2). Keep the
    # grouped thought/action lists too for back-compat with the existing detail view.
    steps = build_processing_steps(
        stages,
        decomposition_text=decomposition_text,
        intent_confidence=intent_confidence,
        stage_meta=_meta,
    )
    # Job lifecycle bookends so the FINALISED card reads as a full job log, matching the live card:
    # "Run started" right after the decomposition thought, and a terminal event at the end.
    _started = {
        "kind": "action", "stage": "run_started", "label": "Run started",
        "text": "Migration run started."
        + (f" Ingested {documents_ingested} document(s); {table_count} table(s) detected."
           if (documents_ingested or table_count) else ""),
        "status": "completed",
    }
    steps.insert(1 if (steps and steps[0].get("stage") == "decomposition") else 0, _started)
    if status == "completed":
        steps.append({
            "kind": "action", "stage": "run_completed", "label": "Migration completed",
            "text": f"All {len(UDR_STAGES)} stages executed and the mapped data was written to the CAFM store.",
            "status": "completed",
        })
    elif status == "failed":
        steps.append({
            "kind": "action", "stage": "run_failed", "label": "Run failed",
            "text": "The migration run stopped with an error before completing.",
            "status": "failed",
        })
    elif status == "pending_human_input":
        steps.append({
            "kind": "thought", "stage": "awaiting_review", "label": "Awaiting your review",
            "text": "Test flags require your review before the run can finalise.",
            "status": "running",
        })
    thought = [s for s in steps if s.get("kind") == "thought"]
    action = [s for s in steps if s.get("kind") == "action"]

    return {
        "run_id": run_id,
        "trigger": trigger,
        "outcome": outcome,
        "status": status,
        "notif_color": notif_color(status),
        "refs": refs,
        "processing_log": {
            "stages": stages,
            "steps": steps,
            "thought": thought,
            "action": action,
            # AL.6 AC4 — 100% of mapped entities traceable source → confidence → dest.
            "mapping_decisions": [_norm_decision(d) for d in (mapping_decisions or [])],
        },
        "test1_flags": t1_flags,
        "test2_flags": t2_flags,
    }


def _norm_decision(d: dict) -> dict:
    """Coerce a mapping decision from any producer to the exact contract the Activity-Log
    detail view consumes (``source_table · source_column · confidence · dest_table ·
    dest_column``). Bridges the column-metadata producer (``column`` / ``dest_udr_table``)
    and the relationship-graph edges (``src_column`` / ``dst_column`` / ``dst_entity``) so a
    raw producer dict never renders as ``undefined.undefined`` in the UI (AL.6 AC4)."""
    return {
        "source_table": d.get("source_table") or d.get("src_entity") or d.get("source"),
        "source_column": d.get("source_column") or d.get("column") or d.get("src_column"),
        "confidence": d.get("confidence"),
        "dest_table": d.get("dest_table") or d.get("dest_udr_table") or d.get("dst_entity"),
        "dest_column": d.get("dest_column") or d.get("dst_column"),
    }
