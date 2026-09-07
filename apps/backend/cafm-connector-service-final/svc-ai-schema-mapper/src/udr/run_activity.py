"""Feature 4 — progressive per-run Activity entry (the backend audit stream).

The Activity Log is an AUDIT SYSTEM, not a UI component: a single durable entry per
migration run is created/updated server-side as the run progresses, so the LIVE view and
the COMPLETED view render from the SAME persisted row (it simply gains status + stages over
time). This module is the pure, offline-testable core:

  * ``mapping_decisions_from_gate`` — gate payload (auto-approved + review items) →
    ``source → confidence → destination`` rows (review items flagged "Review Required" /
    "New Column Suggested"). The async upsert MERGES these append-only so they're immutable.
  * ``build_run_activity_entry`` — migration snapshot (status + completed stages + counts +
    mapping decisions) → an Activity entry whose Processing-Log mirrors the completed trail's
    UDR-stage vocabulary, showing only the stages that have actually executed.

The async persistence (upsert by run) lives in ``activity_persist.upsert_run_activity``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .activity import UDR_STAGES, notif_color
from .errors import classify_migration_error

# Which migration STEP (LangGraph node_id 1–9) covers each UDR stage. 10 = the post-write
# UDR run (Node 10) — those stages only complete once the run finishes (the Node-10 emit
# replaces this progressive entry with the full 17-stage trail).
_STAGE_STEP: dict[str, int] = {
    "preprocessing": 1, "unique_tables": 1, "deterministic": 2, "semantic": 4,
    "table_prefixing": 4, "column_within_source": 5, "column_grouping": 5,
    "column_classification": 5, "column_canonicalisation": 5, "column_metadata": 5,
    "column_to_destination": 5, "vector_chunking": 10, "reference_promotion": 10,
    "test1": 10, "test2": 10, "hierarchy": 7, "incremental_registry": 10,
    "semantic_discovery": 10, "sanctity_check": 10,
}
# CoT vs CoA per stage (mirrors the completed trail's classification).
_STAGE_CHAIN: dict[str, str] = {
    "preprocessing": "action", "unique_tables": "thought", "deterministic": "action",
    "semantic": "action", "table_prefixing": "action", "column_within_source": "thought",
    "column_grouping": "action", "column_classification": "action",
    "column_canonicalisation": "action", "column_metadata": "action", "column_to_destination": "action",
    "vector_chunking": "action", "reference_promotion": "action", "test1": "action",
    "test2": "action", "hierarchy": "action", "incremental_registry": "action",
    "semantic_discovery": "thought", "sanctity_check": "thought",
}

_RUNNING_STATES = {"running", "step_paused", "awaiting_review"}
_GATE_STATES = {"step_paused", "awaiting_review"}

# CAFM-004/006 — loop-linking: which Section-2 processing stages deep-link into which Section-3
# HITL decision card (by card-id type). Mirrors the sample's "→ Action Item N" references — the
# stage where a decision was surfaced links to that decision's card in the Actions-required tab.
_STEP_ACTION_REF = {
    "preprocessing":         ("merge", "Action required — confirm column merges"),
    "column_grouping":       ("merge", "Action required — confirm column groups"),
    "column_classification": ("fk",    "Action required — FK / shared-attribute decisions"),
    "column_to_destination": ("field", "Action required — review column mapping"),
}

# Human-readable gate names for the "Awaiting review" lifecycle step.
_GATE_LABELS: dict[str, str] = {
    "pre_semantic": "pre-semantic review",
    "field_mapping": "field mapping review",
    "hierarchy": "hierarchy verification",
    "write": "final write confirmation",
    "step_paused": "review",
    "awaiting_review": "review",
}


def _gate_label(gate: Any) -> str:
    g = str(gate or "").lower()
    return _GATE_LABELS.get(g, g.replace("_", " ").strip())

# Static business descriptions per stage — the SAME prose the completed trail shows, but
# WITHOUT the post-write counts (PK/FK/shared/test results) that only exist after Node 10.
# This gives the LIVE run card the completed card's richness: every executed/running stage
# reads as an audit record, not a bare label. At completion the Node-10 emit replaces the
# row with build_udr_activity_entry's count-filled descriptions.
_STAGE_DESC: dict[str, str] = {
    "preprocessing": "Scanned every source table for NaN/empty cells and exact duplicate rows; columns with matching value patterns are merged (name inferred from values), 60-94% partial matches flagged for review.",
    "unique_tables": "Built per-table metadata (primary key, column names, sample values, column count) and identified unique tables on name + metadata similarity.",
    "deterministic": "Mapped source tables to the destination UDR by exact match + Levenshtein<=2, then RAG/alias for CMMS/CAFM-native terms (>=95% auto-resolved).",
    "semantic": "Resolved the remaining tables by semantic NLP match on table NAME + METADATA (multi-dimension), FM-domain tuned.",
    "table_prefixing": "Prefixed every source column with its assigned destination-UDR table so identically-named columns in different tables stay distinct.",
    "column_within_source": "Compared each column against every column in other tables on cell-value FORMAT, then VALUE pattern.",
    "column_grouping": "Grouped similar columns name-agnostically into value/format groups and derived a unified name for each.",
    "column_classification": "Classified each group as Primary Key / Foreign Key / Shared Attribute.",
    "column_canonicalisation": "Gave every column in each value/format group ONE canonical name (PK name -> destination -> naming consensus -> FM ontology / format) so downstream stages reference the same identifier.",
    "column_metadata": "Captured 4-dimension metadata per column (destination · class · format · sample values) alongside its canonical name.",
    "column_to_destination": "Matched each column group to destination-UDR columns of the same table (format + values + name); created a new column where none matched.",
    "vector_chunking": "Chunked unstructured documents and anchored each chunk to its parent entity's PRIMARY KEY (never a FK / shared attribute).",
    "reference_promotion": "Where a chunk could only anchor on a shared attribute, auto-created a reference table with that value as primary key, turning it into a valid foreign-key anchor.",
    "test1": "Verifies every document-chunk association references a primary key (target <1% fail).",
    "test2": "Checks cross-table column similarities (>=30% value overlap) are each explained by a foreign key; flags any that aren't.",
    "hierarchy": "Builds the Layer-3 relationship graph from primary/foreign-key metadata (sites -> assets -> work orders).",
    "incremental_registry": "Appends this client's new tables/columns to the global canonical UDR registry (incremental, not rebuilt).",
    "semantic_discovery": "Job 1 (async LLM): reads across entity clusters to surface implicit relationships, written back labelled 'LLM-inferred' with confidence.",
    "sanctity_check": "Job 2 (async LLM): cross-checks structured links against linked document content and flags likely misallocations.",
}


def _stage_chips(
    key: str, *, tables: int, columns: int, t1_mapped: int, t2_auto: int, t2_human: int, unmapped: int
) -> list[str]:
    """Live count chips per stage from the TYPED migration counters (reliable — never output
    shape guessing). Mirrors what the completed card shows, using the data available mid-run."""
    def plural(n: int, w: str) -> str:
        return f"{n} {w}{'' if n == 1 else 's'}"

    chips: list[str] = []
    if key == "unique_tables" and tables:
        chips.append(f"{plural(tables, 'table')} identified")
    elif key == "preprocessing" and columns:
        chips.append(f"{plural(columns, 'column')} identified")
    elif key == "deterministic" and t1_mapped:
        chips.append(f"{plural(t1_mapped, 'field')} auto-mapped (Tier 1)")
    elif key == "semantic":
        if t2_auto:
            chips.append(f"{plural(t2_auto, 'field')} auto-accepted")
        if t2_human:
            chips.append(f"{plural(t2_human, 'field')} flagged for review")
    elif key == "column_to_destination" and unmapped:
        chips.append(plural(unmapped, "unmappable field"))
    return chips


def _plural(n: int, w: str) -> str:
    return f"{n} {w}{'' if n == 1 else 's'}"


def _column_mapping_rows(mapping_decisions: Any) -> list[list[str]]:
    """Format mapping decisions into the completed card's column→destination table rows:
    ``["asset_code (Assets)", "assets.asset_code", "98%"]`` — real targets only (review markers
    like "Review Required" carry no dest_table and are skipped here). Shows up to 200 rows so the
    B21.1 destination-mapping table lists EVERY mapped column across all tables (a 4-table / 44-
    column migration was truncated to 10 before), matching the "N columns mapped" chip."""
    rows: list[list[str]] = []
    for d in (mapping_decisions or []):
        if not isinstance(d, dict) or len(rows) >= 200:
            continue
        st, sc = d.get("source_table") or "", d.get("source_column") or ""
        dt, dc = d.get("dest_table") or "", d.get("dest_column") or ""
        if not (dt and dc):  # only resolved source→destination mappings in this table
            continue
        conf = d.get("confidence")
        conf_s = f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
        rows.append([f"{sc} ({st})" if st else sc, f"{dt}.{dc}", conf_s])
    return rows


def _dest_mapping_rows(ci: Any) -> list[list[str]]:
    """Rich B21.1 rows from ``column_intelligence.dest_mapping`` — the SAME source the gate /
    Table-&-Column-Analysis cards render, so the Activity Log's destination-mapping step reads
    consistently with the earlier views (CAFM-005). Columns: source (canonical) · destination ·
    confidence · outcome. UNLIKE ``_column_mapping_rows`` it KEEPS new-column / unmatched rows
    (their state lives in the Outcome cell), so every analysed column is listed."""
    dm = ci.get("dest_mapping") if isinstance(ci, dict) else None
    if not isinstance(dm, list):
        return []
    rows: list[list[str]] = []
    for m in dm:
        if not isinstance(m, dict) or len(rows) >= 200:
            continue
        src = m.get("canonical_source") or m.get("source") or ""
        dt = m.get("dest_table") or ""
        mc = m.get("matched_column") or ""
        dest = f"{dt}.{mc}" if (dt and mc) else "—"
        conf = m.get("confidence")
        conf_s = f"{round(conf * 100)}%" if isinstance(conf, (int, float)) else "—"
        outcome = str(m.get("outcome") or "").replace("_", " ").strip()
        rows.append([str(src), dest, conf_s, outcome or "—"])
    return rows


def stage_extras_from_column_intelligence(ci: Any, mapping_decisions: Any = None) -> dict[str, dict]:
    """Map a ``column_intelligence`` report (the build_column_intelligence output carried in the
    pre-semantic gate payload — the SAME builder the Node-10 pass uses) into per-stage extras for
    the LIVE run card. Extras are split so each Activity-Log stage carries the detail of its
    matching left-panel card (one card ⇄ one stage ⇄ one log entry):

      * ``table_prefixing``        (B13.1)       → "N columns prefixed".
      * ``column_within_source``   (B17.1·B18.1) → format-gate survivors + FK candidates.
      * ``column_grouping``        (B19.1)       → "N similar-column groups" + Group/Members/Canonical table.
      * ``column_classification``  (B20.1)       → PK / FK / Shared chips.
      * ``column_canonicalisation`` (Unified)    → "N groups unified".
      * ``column_metadata``        (B14.1·B15.1) → "N columns analysed" (dest · class · format · canonical).
      * ``column_to_destination``  (B21.1)       → "N columns mapped" + source→destination→confidence table.

    Pure. Returns {} for a non-dict / empty CI."""
    out: dict[str, dict] = {}
    ci = ci if isinstance(ci, dict) else {}
    summ = ci.get("summary") if isinstance(ci.get("summary"), dict) else {}

    # B13.1 — table prefixing. Every source column is prefixed with its destination-UDR table,
    # so the count = columns analysed. Without this chip the stage carries NO evidence and, since
    # its covering node hasn't run at the gate, it was dropped from the log ("B13.1 not there").
    if isinstance(summ.get("columns_analyzed"), int) and summ["columns_analyzed"]:
        out["table_prefixing"] = {"chips": [_plural(summ["columns_analyzed"], "column") + " prefixed"]}

    # B17.1·B18.1 — column similarity within source (format + value-pattern).
    cws: list[str] = []
    if isinstance(summ.get("format_pairs_scored"), int):
        cws.append(
            f"{summ.get('format_survivors', 0)} of {summ['format_pairs_scored']} cross-table pairs "
            f"≥{summ.get('format_threshold_pct', 80)}% format"
        )
    wt = ci.get("within_table_similarity") if isinstance(ci.get("within_table_similarity"), dict) else {}
    wt_sum = wt.get("summary") if isinstance(wt.get("summary"), dict) else {}
    if isinstance(wt_sum.get("pairs_scored"), int) and wt_sum["pairs_scored"]:
        cws.append(
            f"{wt_sum.get('format_survivors', 0)} of {wt_sum['pairs_scored']} within-table pairs "
            f"≥{wt_sum.get('format_threshold_pct', 80)}% format"
        )
    if isinstance(summ.get("cross_table_groups"), int) and summ["cross_table_groups"]:
        cws.append(_plural(summ["cross_table_groups"], "cross-table group"))
    if isinstance(wt_sum.get("groups"), int) and wt_sum["groups"]:
        cws.append(_plural(wt_sum["groups"], "within-table group"))
    fkc = ci.get("fk_candidates")
    if isinstance(fkc, list) and fkc:
        cws.append(_plural(len(fkc), "FK candidate"))
    if cws:
        out["column_within_source"] = {"chips": cws}
        # Grouped detail tables — B17.1 format groups + B18.1 value-shape groups.
        fmt_rows: list[list[str]] = []
        fg = ci.get("format_groups") if isinstance(ci.get("format_groups"), dict) else {}
        for r in (fg.get("within_table") or []):
            if not isinstance(r, dict) or len(fmt_rows) >= 200:
                continue
            fmt_rows.append([
                f"within · {r.get('table', '')}",
                str(r.get("format", "")),
                " · ".join(r.get("columns") or []),
            ])
        for r in (fg.get("cross_table") or []):
            if not isinstance(r, dict) or len(fmt_rows) >= 200:
                continue
            fmt_rows.append([
                "cross-table",
                str(r.get("format", "")),
                " · ".join(r.get("columns") or []),
            ])
        val_rows: list[list[str]] = []
        vpg = ci.get("value_pattern_groups") if isinstance(ci.get("value_pattern_groups"), dict) else {}
        for r in (vpg.get("within_table") or []):
            if not isinstance(r, dict) or len(val_rows) >= 200:
                continue
            val_rows.append([
                f"within · {r.get('table', '')}",
                str(r.get("value_shape", "")),
                " · ".join(r.get("columns") or []),
            ])
        for r in (vpg.get("cross_table") or []):
            if not isinstance(r, dict) or len(val_rows) >= 200:
                continue
            val_rows.append([
                "cross-table",
                str(r.get("value_shape", "")),
                " · ".join(r.get("columns") or []),
            ])
        tables_out: list[dict] = []
        if fmt_rows:
            tables_out.append({
                "columns": ["Scope", "Format", "Columns (B17.1)"],
                "rows": fmt_rows,
            })
        if val_rows:
            tables_out.append({
                "columns": ["Scope", "Value shape", "Columns (B18.1)"],
                "rows": val_rows,
            })
        if tables_out:
            out["column_within_source"]["tables"] = tables_out
        elif fmt_rows:
            out["column_within_source"]["table"] = {
                "columns": ["Scope", "Format", "Columns (B17.1)"],
                "rows": fmt_rows,
            }

    groups = ci.get("groups")

    # B19.1 — group similar columns (name-agnostic) + unified name.
    if isinstance(summ.get("groups"), int) and summ["groups"]:
        grp: dict = {"chips": [_plural(summ["groups"], "similar-column group")]}
        if isinstance(groups, list) and groups:
            grp_rows = [
                [g.get("group_id", ""), ", ".join((g.get("members") or [])[:3]), g.get("canonical_name", "")]
                for g in groups[:3] if isinstance(g, dict)
            ]
            if grp_rows:
                grp["table"] = {"columns": ["Group", "Members", "Canonical name"], "rows": grp_rows}
        out["column_grouping"] = grp

    # B20.1 — classify each group · PK / FK / Shared.
    cls_chips: list[str] = []
    for key, label in (("pk", "Primary Key"), ("fk", "Foreign Key"), ("shared", "Shared Attribute")):
        n = summ.get(key)
        if isinstance(n, int) and n:
            cls_chips.append(_plural(n, label))
    if cls_chips:
        out["column_classification"] = {"chips": cls_chips}

    # Unified column names — one canonical name per value/format group.
    if isinstance(summ.get("groups"), int) and summ["groups"]:
        out["column_canonicalisation"] = {"chips": [_plural(summ["groups"], "group") + " unified"]}

    # B14.1·B15.1 — column metadata (4 dimensions + canonical name).
    if isinstance(summ.get("columns_analyzed"), int) and summ["columns_analyzed"]:
        out["column_metadata"] = {"chips": [_plural(summ["columns_analyzed"], "column") + " analysed · dest · class · format · canonical"]}

    # B21.1 — destination column mapping. Prefer the RICH dest_mapping (the SAME data the gate /
    # Table-&-Column-Analysis cards render: canonical source · destination · confidence · outcome,
    # INCLUDING new-column + unmatched rows) so the Activity Log's B21.1 step reads consistently with
    # the earlier steps (CAFM-005 — previously the log showed a poorer 3-column mapping_decisions
    # projection that also DROPPED new-column rows). Fall back to mapping_decisions when CI has none.
    c2d: dict = {}
    dm_rows = _dest_mapping_rows(ci)
    if dm_rows:
        c2d["table"] = {
            "columns": ["Source column", "→ Destination UDR column", "Confidence", "Outcome"],
            "rows": dm_rows,
        }
    else:
        rows = _column_mapping_rows(mapping_decisions)
        if rows:
            c2d["table"] = {"columns": ["Source column", "→ Destination UDR column", "Confidence"], "rows": rows}
    if isinstance(summ.get("columns_mapped"), int) and summ["columns_mapped"]:
        c2d["chips"] = [_plural(summ["columns_mapped"], "column") + " mapped to destination"]
    if c2d:
        out["column_to_destination"] = c2d

    return out


def summary_metrics_from_column_intelligence(ci: Any) -> list[dict]:
    """Section-1 metric chips from a ``column_intelligence`` summary — primary keys · foreign
    keys · shared attrs · columns mapped (mirrors build_summary_metrics on the completed card),
    so the live run card's big-number chips grow as the gate's CI lands. Pure."""
    ci = ci if isinstance(ci, dict) else {}
    summ = ci.get("summary") if isinstance(ci.get("summary"), dict) else {}
    out: list[dict] = []
    for key, label in (("pk", "primary keys"), ("fk", "foreign keys"), ("shared", "shared attrs")):
        n = summ.get(key)
        if isinstance(n, int) and n:
            out.append({"label": label, "value": n})
    cm = summ.get("columns_mapped")
    if isinstance(cm, int) and cm:
        out.append({"label": "columns mapped", "value": cm, "tone": "green"})
    return out


# ── B7.1 EARLY (during-run) — partial table-resolution from ingestion preview rows ──────
#
# The full table-resolution report (PK detection · deterministic / RAG / semantic mapping ·
# final decisions) is only built at node 3 (the pre-semantic gate) because it needs the
# destination routing + confidences from node 2. But the B7.1 "Unique table identification"
# CARDS depend on nothing more than the parsed source tables, so we build them at node 1 and
# surface them on the migration status response DURING the run — the left panel then shows
# each table card in step with the right-rail Activity Log's "Unique table identification"
# entry, instead of the whole block appearing only at the gate. The node-3 gate payload and
# the node-11 emit both carry the FULL report, which replaces this partial once it exists.
#
# Pure + cheap: samples come from the ingestion PREVIEW rows (a small subset), and there is
# NO primary-key scan over the full rows here (that stays at node 3). Shapes match
# table_resolution._metadata_card / build_table_resolution so the same frontend renders both.
def build_partial_table_resolution(
    tables_preview: dict[str, list[dict]],
    *,
    row_counts: dict[str, int] | None = None,
    max_cards: int = 40,
) -> dict:
    """B7.1-only partial report ({metadata_cards, pairwise, counts, partial:true}) from the
    ingestion preview rows. Returns {} when there are no usable tables (caller then emits
    nothing, so the status field stays None until the full report lands)."""
    tables_preview = tables_preview if isinstance(tables_preview, dict) else {}
    row_counts = row_counts or {}
    cards: list[dict] = []
    for name, rows in list(tables_preview.items())[:max_cards]:
        rows = rows if isinstance(rows, list) else []
        # Ordered unique columns across the preview rows (first-seen order).
        seen: set = set()
        cols: list[str] = []
        for r in rows:
            if isinstance(r, dict):
                for k in r:
                    if k not in seen:
                        seen.add(k)
                        cols.append(k)
        samples: list[dict] = []
        for c in cols:
            vals: list[str] = []
            for r in rows:
                if not isinstance(r, dict):
                    continue
                v = r.get(c)
                if v is None or str(v) == "" or str(v).lower() == "nan":
                    continue
                vals.append(str(v))
                if len(vals) >= 3:
                    break
            samples.append({"column": c, "values": vals})
        cards.append({
            "table": name,
            # PK is detected at node 3 (needs the full rows) — the card renders "PK —" until then.
            "primary_key": [],
            "primary_key_kind": None,
            "column_count": len(cols),
            "row_count": int(row_counts.get(name, len(rows)) or 0),
            "samples": samples,
        })
    if not cards:
        return {}
    n = len(cards)
    # Pre-B7.1 duplicate-table detection from the preview columns (a column appearing in
    # more than one table → the tables are duplicate candidates; count them).
    try:
        from .table_resolution import detect_duplicate_tables
        _dup = detect_duplicate_tables(
            [{"table": c["table"], "column_names": [s["column"] for s in c.get("samples", [])]}
             for c in cards]
        )
    except Exception:  # pragma: no cover — additive, never break the partial report
        _dup = {"groups": [], "duplicate_count": 0, "checked": n}
    return {
        "duplicate_tables": _dup,
        "metadata_cards": cards,
        "pairwise": {
            "highest_pair": None,
            "auto_consolidated": [],
            "candidates": [],
            "verdict": (
                f"{n} source table{'' if n == 1 else 's'} identified — full name + metadata "
                "comparison and primary-key detection run at the pre-semantic review gate."
            ),
        },
        "counts": {"tables": n},
        # Marks this as the during-run B7.1 slice so the full node-3 / node-11 report supersedes it.
        "partial": True,
    }


# Pipeline STEP pauses the migration advances on its OWN (no human input) — mirrors the FE
# ORCHESTRATOR_AUTO_ADVANCE_STEPS. A pause at one of these is NOT "action required": it stays
# "running" so the Activity header / notification / Section-3 review card don't flag a decision that
# never existed. Everything else that pauses (pre-semantic · field-mapping · hierarchy verification ·
# final write) is a genuine HITL gate → pending_human_input.
_AUTO_ADVANCE_STEPS = {
    "step_1_ingest",
    "step_2_deterministic",
    "step_2_deterministic_mapping",
    "step_7_hierarchy",
    "step_7_hierarchy_detection",
    "step_9_output",
    "step_9_output_generation",
    "step_9_write",
}


def _ui_status(migration_status: str, pending_gate: Any) -> str:
    """Map a migration status → the Activity-entry status vocabulary."""
    s = (migration_status or "").lower()
    if s == "complete":
        return "completed"
    if s in ("failed", "ddl_failed", "error"):
        return "failed"
    if s in ("cancelled", "canceled"):
        return "cancelled"
    # A pause at an auto-advancing step (ingest / deterministic / hierarchy-detection / output) is
    # not a human gate — the run continues on its own — so it's "running", never pending.
    if str(pending_gate or "").strip().lower() in _AUTO_ADVANCE_STEPS:
        return "running"
    if s in _GATE_STATES or pending_gate:
        return "pending_human_input"
    return "running"


def mapping_decisions_from_gate(gate_payload: Any) -> list[dict]:
    """Build ``source → confidence → destination`` rows from a pre-semantic gate payload.
    Auto-approved items resolve to their destination column; review items are flagged
    "Review Required" / "New Column Suggested". Returns [] for any other shape."""
    if not isinstance(gate_payload, dict):
        return []
    suggested = gate_payload.get("suggested_target_by_table")
    suggested = suggested if isinstance(suggested, dict) else {}
    rows: list[dict] = []

    def collect(by_table: Any) -> None:
        if not isinstance(by_table, dict):
            return
        for src_table, items in by_table.items():
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict) or not it.get("source_field"):
                    continue
                if len(rows) >= 60:
                    return
                target = it.get("target_field")
                dest_table = suggested.get(src_table)
                # Show the ACTUAL mapping: pre-semantic review items are deterministic
                # matches awaiting approval and DO carry a target_field. Reserve
                # "Review Required" for genuinely untargeted fields, "New Column Suggested"
                # for the b21 new-column case.
                if it.get("b21_new_column") is True:
                    dest_tbl, dest_col = None, "New Column Suggested"
                elif isinstance(target, str) and target.strip():
                    dest_tbl = dest_table if isinstance(dest_table, str) and dest_table.strip() else None
                    dest_col = target
                else:
                    dest_tbl, dest_col = None, "Review Required"
                rows.append({
                    "source_table": src_table,
                    "source_column": it["source_field"],
                    "confidence": it.get("confidence") if isinstance(it.get("confidence"), (int, float)) else None,
                    "dest_table": dest_tbl,
                    "dest_column": dest_col,
                })

    collect(gate_payload.get("auto_approved_by_table"))  # confident deterministic mappings first
    collect(gate_payload.get("review_items_by_table"))   # then matches awaiting approval
    return rows


# ── Human-review resolution → Processing-Log step (folded INTO the single run row) ──────
#
# A human-review resolution is NOT a separate Activity card: it is appended to the run row's
# ``refs.review_resolutions`` (append-only, immutable) and woven into THIS run's Processing
# Log as one chain-of-thought step, positioned right after the gate's covering stage so the
# whole migration lifecycle reads as one timeline (e.g. a pre-semantic resolution lands
# between Deterministic and Semantic table mapping — exactly where the review happened).
_RESOLUTION_ANCHOR: dict[str, str] = {
    "pre_semantic": "deterministic",
    "field_mapping": "semantic",
    "hierarchy": "hierarchy",
    "write": "column_to_destination",
}


def _resolution_key(rec: Any) -> str:
    r = rec if isinstance(rec, dict) else {}
    return f"{r.get('gate_type')}:{r.get('at')}"


def review_resolution_step(rec: dict) -> dict:
    """Build the Processing-Log STEP for one human-review-resolution record — a chain-of-
    thought step that reads as a narrative (N decisions resolved · the approved fields ·
    "Migration resumed."), with a Field/Decision/By detail table and a stable
    ``resolution_key`` so it de-dupes across run-row rebuilds. Pure."""
    n = int(rec.get("total") or 0)
    by = rec.get("user") or "Reviewer"
    approved = list(rec.get("approved") or [])
    lines = [f"{n} field decision{'s' if n != 1 else ''} resolved by {by}."]
    if approved:
        lines.append("")
        lines.append("Approved:")
        lines.extend(f"- {a}" for a in approved[:40])
        if len(approved) > 40:
            lines.append(f"…and {len(approved) - 40} more")
    lines.append("")
    lines.append("Migration resumed.")
    step: dict = {
        "kind": "thought",
        "stage": "human_review_resolution",
        "label": "Human review resolution",
        "text": "\n".join(lines),
        "status": "completed",
        "at": rec.get("at"),
        "resolution_key": _resolution_key(rec),
    }
    rows = rec.get("rows")
    if isinstance(rows, list) and rows:
        step["table"] = {"columns": ["Field", "Decision", "By"], "rows": rows}
    return step


def weave_review_resolution_steps(steps: list[dict], records: Any) -> list[dict]:
    """Insert human-review-resolution steps into an ordered Processing-Log step list at the
    right chronological anchor (after the gate's covering stage). Pure + idempotent — a
    record whose step is already present (keyed by ``gate_type:at``) is skipped, so calling
    this on every run-row rebuild (progressive sync AND the Node-10 finalise) is safe.
    Returns a NEW list; the input is not mutated."""
    out = list(steps or [])
    if not isinstance(records, list) or not records:
        return out
    present = {
        s.get("resolution_key")
        for s in out
        if isinstance(s, dict) and s.get("stage") == "human_review_resolution"
    }
    for rec in records:
        if not isinstance(rec, dict):
            continue
        key = _resolution_key(rec)
        if key in present:
            continue
        present.add(key)
        step = review_resolution_step(rec)
        anchor = _RESOLUTION_ANCHOR.get(rec.get("gate_type"), "deterministic")
        # Index of the LAST step of the anchor stage; insert AFTER it (and after any
        # resolution steps already placed there, preserving insertion order).
        idx = None
        for i, s in enumerate(out):
            if isinstance(s, dict) and s.get("stage") == anchor:
                idx = i
        if idx is None:
            out.append(step)  # anchor not present yet → tail (repositions on a later rebuild)
            continue
        j = idx + 1
        while j < len(out) and isinstance(out[j], dict) and out[j].get("stage") == "human_review_resolution":
            j += 1
        out.insert(j, step)
    return out


def build_run_activity_entry(
    run_id: str,
    *,
    migration_status: str,
    completed_node_ids: set[int] | list[int],
    node_meta: dict[int, dict] | None = None,
    documents: int = 0,
    tables: int = 0,
    columns: int = 0,
    t1_mapped: int = 0,
    t2_auto: int = 0,
    t2_human: int = 0,
    unmapped: int = 0,
    pending_gate_type: Any = None,
    mapping_decisions: list[dict] | None = None,
    cmms_name: str | None = None,
    source_filename: str | None = None,
    script_ref: Any = None,
    running_node_ids: set[int] | list[int] | None = None,
    stage_extras: dict[str, dict] | None = None,
    extra_metrics: list[dict] | None = None,
    extra_refs: dict | None = None,
    error_message: str | None = None,
    clock=None,
) -> dict:
    """Build the progressive per-run Activity entry — the LIVE card, structured to read like
    the COMPLETED card. Processing-Log shows the decomposition lead step + every UDR stage
    whose covering migration step has executed OR is in flight (executed-only — never a
    queued placeholder), each carrying the completed trail's business DESCRIPTION plus the
    live detail available mid-run: count chips (from the typed counters) and any per-stage
    ``stage_extras`` (e.g. the deterministic mapper's confidence + source→destination routing
    table, derived by the caller from node output). The currently-running stage renders with
    status ``running``. At completion the Node-10 emit replaces this row with the full
    count-filled trail (build_udr_activity_entry)."""
    done = set(completed_node_ids or [])
    running = set(running_node_ids or [])
    meta = node_meta or {}
    extras = stage_extras or {}
    ui_status = _ui_status(migration_status, pending_gate_type)
    now = (clock or datetime.utcnow)().isoformat()

    steps: list[dict] = [{
        "kind": "thought", "stage": "decomposition", "label": "Query decomposition",
        "text": (
            f"Matched to the top-level UDR migration intent. Decomposing into {len(UDR_STAGES)} "
            "pipeline stages across Layer 1 (structured mapping), Layer 2 (vectors + reference "
            "tables + Tests), Layer 3 (hierarchy), the global registry update, and the LLM jobs."
        ),
        "status": "completed", "confidence": 1.0, "at": now,
    }]

    # ── Job lifecycle: RUN STARTED ──────────────────────────────────────
    # A concrete activity that anchors the timeline even before the first pipeline stage runs, so a
    # run that errors early still shows what job it was and what it had ingested.
    _src = source_filename or cmms_name or run_id
    _detected = []
    if documents:
        _detected.append(_plural(documents, "document"))
    if tables:
        _detected.append(f"{_plural(tables, 'source table')} detected")
    steps.append({
        "kind": "action", "stage": "run_started", "label": "Run started",
        "text": f"Migration run started for '{_src}'." + (f" Ingested {', '.join(_detected)}." if _detected else ""),
        "status": "completed", "at": meta.get(1, {}).get("at") or now,
    })

    terminal_ok = ui_status == "completed"
    _UDR_KEYS = {k for k, _ in UDR_STAGES}
    _UDR_LABELS = dict(UDR_STAGES)
    # Show the FULL pipeline plan — every UDR stage, marked completed / running / queued. The
    # post-write stages (vector chunking, reference tables, Test 1/2, registry, LLM jobs) run only
    # AFTER write, so mid-run they read as "queued"; a failed/cancelled run leaves the stages it
    # never reached queued too. This is the "each job's full activity list in one card" view.
    for key, label in UDR_STAGES:
        # hierarchy (node 7) is rendered in the migration-flow block below (…3 → 4 → 5 → 6), NOT
        # here: its UDR_STAGES slot is late (Layer 3, after the Layer-2 vector/Test stages), which
        # would place it out of migration order relative to the review gate (step 3) and the
        # Preprocess & Validate step (4). Skip it in the loop so it renders in flow position.
        if key == "hierarchy":
            continue
        cover = _STAGE_STEP.get(key, 10)
        # Post-write stages (cover>=10) only complete on SUCCESSFUL completion — a failed/cancelled
        # run never reached them, so they stay queued.
        is_done = (cover in done) or (cover >= 10 and terminal_ok)
        is_running = (cover in running) and not is_done
        ex = extras.get(key) if isinstance(extras.get(key), dict) else {}
        # A stage carrying CONCRETE evidence (a table / chips / confidence derived from node output
        # or the pre-semantic gate's column_intelligence) has demonstrably executed — mark it done
        # even when its nominal covering node isn't flagged yet (e.g. the column stages land at the
        # pre-semantic gate, node 3, but are nominally covered by node 5).
        has_evidence = bool(
            ex.get("chips") or ex.get("table") or isinstance(ex.get("confidence"), (int, float))
        )
        if is_done or (has_evidence and not is_running):
            _st = "completed"
        elif is_running:
            _st = "running"
        else:
            _st = "pending"  # queued — upcoming stage (incl. the post-write UDR jobs)
        m = meta.get(cover, {})
        chips = _stage_chips(
            key, tables=tables, columns=columns, t1_mapped=t1_mapped,
            t2_auto=t2_auto, t2_human=t2_human, unmapped=unmapped,
        )
        if isinstance(ex.get("chips"), list):
            chips = [*ex["chips"], *chips]
        step: dict[str, Any] = {
            "kind": _STAGE_CHAIN.get(key, "action"),
            "stage": key,
            "label": label,
            "text": _STAGE_DESC.get(key),
            "status": _st,
            "at": m.get("at"),
            "duration_ms": m.get("duration_ms"),
        }
        # Only attach live evidence to stages that have run — a queued stage carries no counts yet.
        if _st != "pending":
            if chips:
                step["chips"] = chips
            if isinstance(ex.get("confidence"), (int, float)):
                step["confidence"] = ex["confidence"]
            if isinstance(ex.get("table"), dict):
                step["table"] = ex["table"]
        # CAFM-004/006 — loop-linking (sample's "→ Action Item N"): while the run is awaiting review,
        # the decision-bearing stages carry an inline deep-link into Section 3 (Actions required). The
        # FE resolves the ref to the matching HITL card by type (merge/fk/field), else the first
        # pending one — so clicking a processing-log step jumps to its action card.
        if ui_status == "pending_human_input" and _st != "pending" and key in _STEP_ACTION_REF:
            _ref_id, _ref_label = _STEP_ACTION_REF[key]
            step["action_ref"] = {"id": _ref_id, "label": _ref_label}
        steps.append(step)

    # ── Post-mapping migration steps — mirror the center panel node-for-node (nodes 3 → 9) ──
    # The log reads in the SAME numbered sequence as the center steps:
    #   3 Schema Analysis — Semantic Review (pre-semantic gate) · 4 Semantic Mapping (Tier 2) ·
    #   5 Field mapping review (field-mapping gate) · 6 Preprocess & Validate ·
    #   7 Layer 3 · Hierarchy / relationship graph · 8 Hierarchy confirmation gate · 9 Data artifacts
    # Gate nodes (3/5/8) INTERRUPT, so they never enter `done` while paused — they render "running"
    # (the async card) while the run sits at them (pending_gate_type match) and flip to "completed"
    # once a LATER node has run. Regular nodes (4/6/7/9) use done/running directly. Executed-only
    # throughout: a step appears only once it has run / is running (never a queued placeholder), so
    # the async-running card follows the current step from node 3 all the way to completion.
    _max_done = max(done) if done else 0
    _cur_gate = str(pending_gate_type or "").lower()
    _GATE_OF = {3: "pre_semantic", 5: "field_mapping", 8: "hierarchy"}

    def _emit_node(
        node_id: int, stage: str, label: str, text: str, *,
        kind: str = "action", chips: list | None = None, table: dict | None = None,
        evidence: bool = False,
    ) -> None:
        if node_id in _GATE_OF:
            _running = (_cur_gate == _GATE_OF[node_id]) and (_max_done <= node_id) and (node_id not in done)
            _done = (_max_done > node_id) or (node_id in done)
        else:
            _done = node_id in done
            _running = (node_id in running) and not _done
        # Node 3 ("Schema Analysis — Table & Column Mapping") is only a background job AFTER the user
        # continues from table/column routing. While the run is PAUSED at that gate the user is on the
        # step in the center panel (nothing is executing), so the Activity-Log card is removed — for a
        # gate node, `_running` is true ONLY during that pause. Once the user continues, the gate
        # clears (`_running` → False) and the step re-appears via the `evidence` path below as
        # status "running" → the FE's "Async — running" card. It flips to "completed" when node 3 lands.
        if node_id == 3 and _running:
            return
        # `evidence` forces inclusion for a node whose own DONE/RUNNING state lags but which already
        # carries concrete data to show — e.g. node 3's source→destination decisions, made at the
        # pre-semantic gate, must persist through LATER gates (field-mapping/hierarchy) even if node 3
        # isn't in done_ids yet. Evidence-only reads "running": the decisions are the live review
        # state, not a finished node. (Once the node is genuinely done, `_done` wins → "completed".)
        if not _done and not _running and not evidence:
            return
        _fm = meta.get(node_id, {})
        _fx = extras.get(stage) if isinstance(extras.get(stage), dict) else {}
        _ch = list(chips or [])
        if isinstance(_fx.get("chips"), list):
            _ch = [*_fx["chips"], *_ch]
        _tbl = table if isinstance(table, dict) else (
            _fx.get("table") if isinstance(_fx.get("table"), dict) else None
        )
        # A gate node the run is CURRENTLY paused at (3/5/8) is NOT an async backend process — the
        # pipeline is stopped, waiting on the user. Emit "awaiting" (the FE renders an "Awaiting your
        # review" pill) so a HITL gate never reads as "Async — running". Genuine in-flight work
        # (non-gate `_running`) and node-3's decision persistence at a LATER gate (`evidence`) keep
        # "running".
        if _done:
            _step_status = "completed"
        elif (node_id in _GATE_OF) and _running:
            _step_status = "awaiting"
        elif _running or evidence:
            _step_status = "running"
        else:
            _step_status = "completed"
        _st: dict[str, Any] = {
            "kind": kind,
            "stage": stage,
            "label": label,
            "text": text,
            "status": _step_status,
            "at": _fm.get("at") or now,
            "duration_ms": _fm.get("duration_ms"),
        }
        if _ch:
            _st["chips"] = _ch
        if _tbl:
            _st["table"] = _tbl
        steps.append(_st)

    # 3 — pre-semantic review gate. Labelled "Table & Column Mapping" (NOT "Semantic Review") so it
    # doesn't read as node 4's "Semantic Mapping (Tier 2)": this gate is where source tables are
    # routed to destinations and their columns matched — the column-mapping flow lives here (CAFM-007).
    _emit_node(
        3, "semantic_review", "Schema Analysis — Table & Column Mapping",
        "Human-in-the-loop review of the table routing and column mappings — confirm each sheet's "
        "destination table, approve auto-matched columns, resolve flagged / unmapped ones, and create "
        "new destination columns where needed.",
        kind="thought",
        # Keep the node-3 step (and its decision table) visible at LATER gates even when node 3 has
        # dropped out of done_ids — the routing/column decisions made here persist (CAFM-007).
        evidence=bool(mapping_decisions),
        table=(
            {"columns": ["Source column", "→ Destination UDR column", "Confidence"],
             "rows": _column_mapping_rows(mapping_decisions)}
            if mapping_decisions else None
        ),
    )
    # 4 — Tier-2 semantic field mapping
    _t2_chips: list[str] = []
    if t2_auto:
        _t2_chips.append(f"{t2_auto} auto-mapped")
    if t2_human:
        _t2_chips.append(f"{t2_human} flagged")
    if unmapped:
        _t2_chips.append(f"{unmapped} unmappable")
    _emit_node(
        4, "semantic_tier2", "Semantic Mapping (Tier 2)",
        "Tier-2 semantic field mapping — embedding similarity resolved the columns the deterministic "
        "pass left open; confident matches auto-accepted, the rest flagged for review.",
        chips=_t2_chips,
    )
    # 5 — field-mapping review gate
    _fm_chips: list[str] = []
    if t2_human:
        _fm_chips.append(f"{t2_human} flagged for review")
    if unmapped:
        _fm_chips.append(f"{unmapped} unmapped")
    _emit_node(
        5, "field_mapping_review", "Field mapping review",
        "Human-in-the-loop review of the Tier-2 field mappings — accept / override / reject the "
        "flagged columns and decide how to handle any unmapped fields.",
        kind="thought", chips=_fm_chips,
    )

    # 6 — preprocess & validate
    _emit_node(
        6, "preprocess_validate", "Preprocess & Validate",
        "De-duplicated rows, resolved nulls, coerced column types and ran JSON-Schema + "
        "foreign-key validation on the mapped data before hierarchy detection.",
    )
    # 7 — Layer 3 hierarchy / relationship graph
    _emit_node(
        7, "hierarchy", _UDR_LABELS.get("hierarchy") or "Layer 3 · Hierarchy / relationship graph",
        _STAGE_DESC.get("hierarchy")
        or "Detected foreign-key relationships and the site → location → asset → work-order hierarchy.",
    )
    # 8 — hierarchy confirmation gate
    _emit_node(
        8, "hierarchy_confirmation", "Hierarchy confirmation gate",
        "Human-in-the-loop confirmation of the detected sites → locations → assets → work-order "
        "hierarchy before the data artifacts are generated.",
        kind="thought",
    )
    # 9 — data artifacts
    _emit_node(
        9, "data_artifacts", "Data artifacts",
        "Generated the nested JSON, flat CSV and SQL artifacts (plus the PDF summary) and "
        "uploaded them to Blob for handoff to the platform.",
    )

    # ── Job lifecycle: PAUSED / TERMINAL ────────────────────────────────
    # The closing activity so the card reads as a complete job log, not just stages.
    error_info: dict[str, Any] | None = None
    if ui_status == "pending_human_input":
        _gl = _gate_label(pending_gate_type)
        steps.append({
            "kind": "thought", "stage": "awaiting_review", "label": "Awaiting your review",
            "text": (f"Paused at the {_gl} gate — your review is required before the run continues."
                     if _gl else "Paused — your review is required before the run continues."),
            # Paused on the user, not an async backend job → "awaiting" (never "Async — running").
            "status": "awaiting", "at": now,
        })
    elif ui_status == "completed":
        steps.append({
            "kind": "action", "stage": "run_completed", "label": "Migration completed",
            "text": f"All {len(UDR_STAGES)} stages executed and the mapped data was written to the CAFM store.",
            "status": "completed", "at": now,
        })
    elif ui_status == "failed":
        # Classify the raw error into the structured, user-facing failure shape (category, code,
        # user message, technical reason, recoverable/retry flags, suggested actions, impact) that
        # the rich failure card renders — never a raw stack trace.
        error_info = classify_migration_error(error_message, step="Migration")
        steps.append({
            "kind": "action", "stage": "run_failed", "label": "Run failed",
            "text": error_info["user_message"],
            "status": "failed", "at": now,
            "error": error_info,
        })
    elif ui_status == "cancelled":
        steps.append({
            "kind": "action", "stage": "run_cancelled", "label": "Run cancelled",
            "text": "The migration run was cancelled by the user.",
            "status": "cancelled", "at": now,
        })

    outcome = (
        f"Migration complete — {len(UDR_STAGES)} stages executed" if ui_status == "completed"
        else (error_info["user_message"] if error_info else "Migration failed") if ui_status == "failed"
        else "Migration cancelled" if ui_status == "cancelled"
        else "Migration paused — review required" if ui_status == "pending_human_input"
        else "Migration in progress"
    )

    # "stages" metric = the number of PIPELINE stages completed so far (excludes the lifecycle
    # event steps: decomposition / run_started / terminal).
    _stages_done = len([s for s in steps if s.get("stage") in _UDR_KEYS and s["status"] == "completed"])
    metrics: list[dict[str, Any]] = [{"label": "stages", "value": f"{_stages_done}"}]
    if tables:
        metrics.append({"label": "dest. tables", "value": tables})
    if columns:
        metrics.append({"label": "dest. columns", "value": columns})
    auto = (t1_mapped or 0) + (t2_auto or 0)
    if auto:
        metrics.append({"label": "auto-mapped", "value": auto, "tone": "green"})
    if t2_human:
        metrics.append({"label": "review", "value": t2_human, "tone": "red"})
    if unmapped:
        metrics.append({"label": "unmappable", "value": unmapped, "tone": "red"})
    # Section-1 chips that progressively appear as evidence lands (primary keys · foreign keys
    # · shared attrs · columns mapped · relationships) — deduped by label so the base chips win.
    if extra_metrics:
        _seen = {m.get("label") for m in metrics}
        for m in extra_metrics:
            if isinstance(m, dict) and m.get("label") not in _seen:
                metrics.append(m)
                _seen.add(m.get("label"))

    # entry_kind="run" marks THE single progressive per-run entry the upsert finds/updates
    # (distinct from the separate Human-Review-Resolution events that share this session_id).
    refs: dict[str, Any] = {"entry_kind": "run", "udr_run_id": run_id, "run_code": run_id, "metrics": metrics}
    # Actual processing start = node-1 (ingest) start time, NOT the row's created_at. created_at is
    # stamped when the run-activity row is FIRST inserted — at the run-start sync, BEFORE the file
    # parse/combine window — so on a bulk upload it can read minutes early (CAFM: 12:39 vs 12:43).
    # Surface the real start so Section-1's "Timestamp" matches when processing actually began.
    _node1_at = meta.get(1, {}).get("at")
    if _node1_at:
        refs["started_at"] = _node1_at
    if script_ref is not None:
        refs["script"] = script_ref
    # Structured failure — surfaced at the ENTRY level so the chat error card + Migration Panel can
    # render the rich card (category / root cause / impact / suggested actions) without walking steps.
    if error_info is not None:
        refs["error"] = error_info
    # extra_refs lets the caller stash data that must SURVIVE rebuilds (e.g. the pre-semantic
    # gate's column_intelligence, gone from pending_gate_payload once the gate resolves) so the
    # live card keeps showing the column-stage detail through to completion.
    if extra_refs:
        for k, v in extra_refs.items():
            if v is not None:
                refs[k] = v

    return {
        "run_id": run_id,
        "trigger": "external_input",
        "trigger_detail": f"Migration ingest · {source_filename}" if source_filename else (cmms_name or "Migration ingest"),
        "outcome": outcome,
        "status": ui_status,
        "notif_color": notif_color(ui_status),
        "refs": refs,
        "processing_log": {
            "steps": steps,
            "thought": [s for s in steps if s.get("kind") == "thought"],
            "action": [s for s in steps if s.get("kind") == "action"],
            "stages": [],
            "mapping_decisions": list(mapping_decisions or []),
        },
    }
