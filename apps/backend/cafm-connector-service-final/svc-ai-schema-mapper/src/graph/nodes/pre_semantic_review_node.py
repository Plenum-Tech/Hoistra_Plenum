"""Pre-Semantic Human Review Gate (between Node 2 and Node 3).

EVERY deterministic match produced by Node 2 is shown to the human for an explicit
Approve / Semantic decision before the pipeline proceeds (default = approve):

    T1_exact, T1_table_exact, T1_table_alias, T1_identity, T1_variation,
    T1_regex, T1_alias, T1_registry, T1_llm  →  shown to human for yes/no

Per-column decision:
    "approve"  →  mapping is finalised; stays in tier1_mappings_by_table
    "semantic" →  mapping is rejected; source field is added back to
                  unresolved_by_table so Node 3 attempts a semantic match

After this gate Node 3 processes:
    • Fields that were already unresolved after Node 2
    • Fields rejected ("semantic") at this gate

Approved T1 fields never touch Node 3 and flow straight through to Node 5
via the existing conditional edge.

Expected interrupt resume payload (from external API caller):
{
  "work_orders": [
    {"source_field": "WO_ID",       "decision": "approve"},
    {"source_field": "FAULT_DESC",  "decision": "semantic"}
  ],
  "assets": [
    {"source_field": "EQUIP_NUM",   "decision": "approve"}
  ]
}

Omitted tables default to approve for all their reviewable mappings.
Omitted fields within a table also default to approve.
"""

from datetime import datetime

from langgraph.types import interrupt
from sqlalchemy import text

from cafm_shared.logging import get_logger
from ...db import get_async_session_factory
from ..state import FieldMapping, MigrationState

logger = get_logger(__name__)

# Cap the rows fed to the gate's column-intelligence REPORT. Its per-column format /
# value-shape / similar-column / FK-RI scans are O(rows) (and O(columns² × rows) across
# tables), so a large export (e.g. 120k rows × 60+ cols) made the pre-semantic gate take a
# minute+ to open. These are statistical heuristics for a PREVIEW, so a representative sample
# is enough; PK detection is kept on the FULL deduped data (uniqueness must see every row).
_CI_PREVIEW_ROW_CAP = 10_000


async def _list_plenum_cafm_tables() -> list[str]:
    """All base tables in plenum_cafm — populates the target-table dropdown at this gate."""
    try:
        factory = get_async_session_factory()
        async with factory() as session:
            res = await session.execute(
                text(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'plenum_cafm' AND table_type = 'BASE TABLE' "
                    "ORDER BY table_name"
                )
            )
            return [str(r[0]) for r in res.fetchall()]
    except Exception as exc:  # pragma: no cover - dropdown is best-effort
        logger.warning(f"[Pre-Semantic Gate] Could not list plenum_cafm tables: {exc}")
        return []

# Tiers that require human sign-off at this gate. EVERY deterministic match — including
# identity (site_ref→id), alias, and table-alias — is shown with Approve/Semantic so the
# user explicitly confirms it before the pipeline proceeds (default decision = approve).
_TIERS_REQUIRING_REVIEW: frozenset[str] = frozenset(
    {
        "T1_exact", "T1_table_exact", "T1_table_alias", "T1_identity",
        "T1_variation", "T1_levenshtein", "T1_token_containment",
        "T1_regex", "T1_alias", "T1_registry", "T1_llm",
    }
)


async def pre_semantic_review_node(state: MigrationState) -> MigrationState:
    """
    Pre-Semantic Human Review Gate — interrupts for per-column approve/semantic.

    Logic:
    1. Partition tier1_mappings_by_table into:
       - reviewable (T1_exact / T1_variation / T1_regex / T1_llm)
       - auto_pass  (T1_alias and any unknown tier)
    2. If nothing is reviewable, skip the gate entirely (no interrupt).
    3. Otherwise interrupt → wait for external decisions.
    4. Process decisions:
       - approve  → mapping stays in tier1_mappings_by_table
       - semantic → mapping removed; source_field added to unresolved_by_table
    5. Store updated tier1_mappings_by_table and unresolved_by_table in state.
    """

    migration_id = state.get("migration_id")
    tier1_mappings_by_table: dict = state.get("tier1_mappings_by_table", {})
    unresolved_by_table: dict = dict(state.get("unresolved_by_table", {}))  # shallow copy

    # ── Already answered this run? Never re-open a decided gate ─────────
    # A table-routing override applied on resume re-partitions tier-1 mappings, so a second
    # entry to this node would otherwise find reviewable items and interrupt() AGAIN — briefly
    # re-showing the gate the user just answered. The decisions are already applied on the first
    # pass, so proceed straight through. (Cleared at the start of deterministic_mapper_node, so a
    # deliberate restart from an earlier node still re-opens the gate for a fresh review.)
    if state.get("pre_semantic_reviewed"):
        logger.info(
            "[Pre-Semantic Gate] Already answered in this run — skipping re-interrupt; "
            "proceeding with the decisions applied earlier."
        )
        return state

    # ── Partition mappings ─────────────────────────────────────────────
    reviewable_by_table: dict[str, list[FieldMapping]] = {}
    auto_pass_by_table: dict[str, list[FieldMapping]] = {}

    for table_name, mappings in tier1_mappings_by_table.items():
        reviewable = [m for m in mappings if m.get("tier") in _TIERS_REQUIRING_REVIEW]
        auto_pass = [m for m in mappings if m.get("tier") not in _TIERS_REQUIRING_REVIEW]
        reviewable_by_table[table_name] = reviewable
        auto_pass_by_table[table_name] = auto_pass

    total_reviewable = sum(len(v) for v in reviewable_by_table.values())

    # ── Fast-path: nothing to field-review ─────────────────────────────
    # Primary-key approval now runs in the dedicated pk_review_node (Group A) BEFORE this gate,
    # so when no T1 mapping needs review there's nothing left to confirm here — proceed.
    if total_reviewable == 0:
        logger.info(
            "[Pre-Semantic Gate] No reviewable T1 mappings — routing/mapping gate skipped "
            "(primary keys already confirmed in the pk-review gate)."
        )
        state["tier1_approved_by_table"] = tier1_mappings_by_table
        state["pre_semantic_review_payload"] = None
        state["pre_semantic_reviewed"] = True  # nothing to review == answered; don't re-open
        state["event_log"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "gate_skipped",
            "gate": "pre_semantic",
            "detail": "No reviewable T1 mappings; gate bypassed",
        })
        return state

    # ── Build interrupt payload ────────────────────────────────────────
    review_items_by_table: dict[str, list[dict]] = {}

    # Routed destination UDR table per source table (4/4a: column metadata must carry
    # the destination UDR table). table_routing wins; else the matched canonical; else the
    # source name (a new table will be created for it).
    _routing = state.get("table_routing") or {}
    _matches = state.get("cafm_table_matches") or {}

    def _dest_table(src: str) -> str:
        return _routing.get(src) or _matches.get(src) or src

    # Authoritative PK per SOURCE table for the gate's "PK" badge. The mapping's own
    # ``is_primary_key`` comes from an identity-like heuristic (high-uniqueness code/integer),
    # which flags EVERY unique numeric column — gfa_sqm, labor_minutes, vendor_id — not the real
    # primary key. Use the user-confirmed PK from the PK-approval gate (which ran before this),
    # falling back to structural PK detection on the full data. Only these columns get the badge.
    from ...udr.primitives import detect_primary_key as _detect_pk
    _pk_confirmed = state.get("pk_confirmed_by_table") or {}
    _full_tables = state.get("full_tables") or {}

    def _real_pk_cols(src_table: str) -> set[str]:
        confirmed = _pk_confirmed.get(src_table)
        if isinstance(confirmed, list):
            # [] is a deliberate "surrogate key" answer — no source column is the PK.
            return {str(c) for c in confirmed}
        _rows = list(_full_tables.get(src_table) or [])
        if not _rows:
            return set()
        _cols: list[str] = []
        _seen: set[str] = set()
        for _r in _rows:
            if isinstance(_r, dict):
                for _k in _r:
                    if _k not in _seen:
                        _seen.add(_k); _cols.append(_k)
        try:
            return {str(c) for c in _detect_pk(_rows, _cols).get("columns", [])}
        except Exception:
            return set()

    _pk_cols_cache: dict[str, set[str]] = {}

    def _is_real_pk(src_table: str, source_field) -> bool:
        if src_table not in _pk_cols_cache:
            _pk_cols_cache[src_table] = _real_pk_cols(src_table)
        return str(source_field) in _pk_cols_cache[src_table]

    for table_name, mappings in reviewable_by_table.items():
        if not mappings:
            continue
        dest_table = _dest_table(table_name)
        items = []
        for m in mappings:
            items.append({
                "source_table": table_name,
                # 4/4a column metadata: destination UDR table for this column.
                "dest_table": dest_table,
                "source_field": m.get("source_field"),
                "target_field": m.get("target_field"),
                "confidence": m.get("confidence"),
                "tier": m.get("tier"),
                "rationale": m.get("rationale"),
                "sample_values": m.get("sample_values", []),
                # Feature 5: alternative target fits + confidence, and source data type.
                "candidates": m.get("candidates", []),
                "data_type": m.get("data_type"),
                # value-merge: other source headers whose values were identical to this one.
                "merged_source_fields": m.get("merged_source_fields", []),
                # 4a column metadata: the table's REAL primary key (confirmed/detected), not the
                # identity-like heuristic that flags every unique numeric column.
                "is_primary_key": _is_real_pk(table_name, m.get("source_field")),
            })
        review_items_by_table[table_name] = items

    # Auto-approved deterministic mappings (identity / alias / table-alias / registry) — they
    # don't need human sign-off, but the gate must still SHOW them so every source column is
    # visible (e.g. site_ref → id). Rendered read-only with an "auto-approved" badge.
    auto_approved_by_table: dict[str, list[dict]] = {}
    for table_name, mappings in auto_pass_by_table.items():
        if not mappings:
            continue
        auto_approved_by_table[table_name] = [
            {
                "source_table": table_name,
                "dest_table": _dest_table(table_name),
                "source_field": m.get("source_field"),
                "target_field": m.get("target_field"),
                "confidence": m.get("confidence"),
                "tier": m.get("tier"),
                "rationale": m.get("rationale"),
                "sample_values": m.get("sample_values", []),
                "data_type": m.get("data_type"),
                "is_primary_key": _is_real_pk(table_name, m.get("source_field")),
            }
            for m in mappings
        ]

    # Full plenum_cafm table list so the target-table dropdown can route a source
    # table to ANY existing CAFM table (fall back to the reviewed tables).
    db_tables = await _list_plenum_cafm_tables()
    existing_canonical_tables = db_tables or sorted(review_items_by_table.keys())

    # Columns for each candidate target table. Lets the frontend re-match a source
    # table's columns the instant the user picks a different target table, without
    # another round-trip. Best-effort — empty map just disables the live re-match.
    canonical_columns_by_table: dict[str, list[str]] = {}
    _cols_by_tbl: dict = {}  # destination columns by table — reused by the B21 value-check
    try:
        from ...db import get_plenum_cafm_columns_by_table
        _cols_by_tbl = await get_plenum_cafm_columns_by_table()
        for _t in existing_canonical_tables:
            _cols = _cols_by_tbl.get(_t.lower())
            if _cols:
                canonical_columns_by_table[_t] = sorted(_cols)
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning(f"[Pre-Semantic Gate] Could not load canonical columns: {exc}")

    # 7.4 AC6 — for each source table with no confident destination match, suggest a target:
    # assign an existing canonical table (via the FM ontology) or create a new table named for
    # the FM entity (e.g. Resources -> 'resource'). Lets Step 1 replace a bare 'pick a table'
    # with an actionable suggestion. Additive + best-effort.
    table_routing_suggestion_by_table: dict = {}
    try:
        from ...matchers.fm_ontology import suggest_table_routing
        _matches = state.get("cafm_table_matches") or {}
        _confs = state.get("cafm_table_match_confidence") or {}
        _src_tables = set(_matches) | set(review_items_by_table) | set(state.get("full_tables") or {})
        for _src in _src_tables:
            table_routing_suggestion_by_table[_src] = suggest_table_routing(
                _src, existing_canonical_tables,
                matched=_matches.get(_src), confidence=_confs.get(_src, 0.0),
            )
    except Exception as exc:  # pragma: no cover — suggestion is additive, never fatal
        logger.warning(f"[Pre-Semantic Gate] table routing suggestion failed: {exc}")

    # #10 — document/table inventory shown before mapping begins (best-effort).
    try:
        from ..migration_metadata import build_document_inventory
        _doc_inventory = build_document_inventory(state)
    except Exception as exc:  # pragma: no cover — additive, never fatal
        logger.warning(f"[Pre-Semantic Gate] document inventory failed: {exc}")
        _doc_inventory = None

    # B7.1→B12.1 — the structured table-resolution report (metadata cards · highest pairwise
    # score + verdict · PK detection · deterministic / RAG-alias / semantic table mapping ·
    # final decisions) so Step 1 shows HOW each sheet was routed, exactly like the Migration
    # Analysis sample. Same pure builder the post-write UDR pass uses, fed the FM-ontology
    # alias resolver. Additive + best-effort — never fails the gate.
    # The pre-semantic gate runs BEFORE Node 5's dedup, so the raw full_tables can still carry
    # exact-duplicate rows. Dedup per table for the analysis reports so primary-key detection
    # sees the true uniqueness — otherwise a single duplicate row makes a real natural key
    # (e.g. id / tag_id) fail the unique test and fall back to a surrogate _udr_id.
    # Both reports below are already on state after pass 1. LangGraph re-executes this node body on
    # every resume (and the graph loops back for pass 2), so recomputing them — a full
    # exact-duplicate dedup of every table plus the table-resolution scan — was pure repeated work.
    # Skip the dedup entirely when nothing downstream still needs the deduped rows.
    _ci_cached = bool(state.get("pre_semantic_ci_built") and state.get("column_intelligence"))
    _tr_cached = state.get("table_resolution_report") if _ci_cached else None

    _analysis_tables: dict = {}
    if not (_ci_cached and _tr_cached):
        try:
            from ...udr.preprocessing import remove_exact_duplicate_rows
            for _n, _r in (state.get("full_tables") or {}).items():
                _deduped, _ = remove_exact_duplicate_rows(list(_r or []))
                _analysis_tables[_n] = _deduped
        except Exception:  # pragma: no cover — fall back to the raw rows
            _analysis_tables = {n: list(r or []) for n, r in (state.get("full_tables") or {}).items()}

    _table_resolution = _tr_cached
    try:
        from ...udr.table_resolution import build_table_resolution
        try:
            from ...matchers.fm_ontology import fm_table_lookup as _alias_resolver
        except Exception:  # pragma: no cover — RAG step degrades to semantic
            _alias_resolver = None
        if _analysis_tables and not _table_resolution:
            _dest_map = {src: _dest_table(src) for src in _analysis_tables}
            _table_resolution = build_table_resolution(
                _analysis_tables,
                dest_table_by_source=_dest_map,
                confidence_by_source=state.get("cafm_table_match_confidence") or {},
                alias_resolver=_alias_resolver,
                duplicate_column_report=state.get("duplicate_column_report") or {},
            )
    except Exception as exc:  # pragma: no cover — additive, never fatal
        logger.warning(f"[Pre-Semantic Gate] table resolution report failed: {exc}")
        _table_resolution = None

    # B13.1→B21.1 — the Column-Intelligence Pipeline (table prefixing · 4 metadata dimensions ·
    # FK-candidate detection · format + value-pattern similarity gates · similar-column grouping ·
    # unified names · PK/FK/Shared classification · destination column mapping) so the user sees
    # HOW columns were analysed before confirming routing. Same pure builder the UDR pass uses,
    # fed the FM-ontology field resolver + the tier-1 destination mappings. Additive + best-effort.
    _column_intelligence = None

    async def _build_ci_report():
        """Build the B13.1→B21.1 column-intelligence report.

        Deliberately NOT built before the routing gate. Every column is analysed against its
        DESTINATION table, so the report is only meaningful once the human has CONFIRMED table
        routing — and building it up-front was what made the routing gate take ~13s to open.
        Called once, immediately after routing confirmation (pass 1); pass 2 reuses the result.
        """
        try:
            from ...udr.column_intelligence import build_column_intelligence
            from ...udr.primitives import detect_primary_key as _dpk
            try:
                from ...matchers.fm_ontology import fm_field_lookup as _field_resolver
            except Exception:  # pragma: no cover — FM tiers degrade to name/format heuristics
                _field_resolver = None
            if not _analysis_tables:
                return None
            # deduped rows (see above) — so PK detection matches the data
            def _ci_cols(rows):
                seen, out = set(), []
                for r in rows or []:
                    for k in r:
                        if k not in seen:
                            seen.add(k); out.append(k)
                return out
            _ci_tables = {}
            for _n, _r in _analysis_tables.items():
                _rows = list(_r or [])
                _cols = _ci_cols(_rows)
                # PK detection needs EVERY row (uniqueness); the value/format/similar-column
                # analyses inside build_column_intelligence only need a representative sample,
                # so cap the rows handed to the report (large files otherwise stall the gate).
                _pk = _dpk(_rows, _cols).get("columns", [])
                _ci_rows = _rows if len(_rows) <= _CI_PREVIEW_ROW_CAP else _rows[:_CI_PREVIEW_ROW_CAP]
                _ci_tables[_n] = {"rows": _ci_rows, "columns": _cols, "pk": _pk}
            # destination column + confidence per (source_table, source_field) from every
            # confirmed/auto tier-1 mapping the gate has so far.
            _dest_col: dict = {}
            _conf_col: dict = {}
            for _bucket in ("tier1_mappings_by_table", "tier2_auto_by_table"):
                for _tbl, _maps in (state.get(_bucket) or {}).items():
                    for _m in _maps or []:
                        if not isinstance(_m, dict):
                            continue
                        _sf = _m.get("source_field")
                        _tf = _m.get("target_field")
                        if _sf and _tf:
                            _dest_col[(_tbl, _sf)] = _tf
                            _conf_col[(_tbl, _sf)] = _m.get("confidence")
            # Unresolved best-guess suggestions (the same the gate shows in its dropdown): feed
            # them so B21 mirrors Step 2 for unresolved columns (wo_status → status, date_raised →
            # updated_at) instead of flatly showing 'new column'. Kept SEPARATE from _dest_col so
            # build_column_intelligence applies them only as a LOWER-priority fallback (after the
            # canonical-driven resolution, so property_ref → site_id still beats its weak
            # document_ids suggestion).
            _unres_sg: dict = {}
            for _tbl, _sugg in (state.get("unresolved_suggestions_by_table") or {}).items():
                for _sf, _s in (_sugg or {}).items():
                    _tf = (((_s or {}).get("target_field")) or "").strip()
                    if _sf and _tf:
                        _unres_sg[(_tbl, _sf)] = _tf
                        _conf_col.setdefault((_tbl, _sf), (_s or {}).get("confidence"))
            # HITL: user-pinned canonical name overrides survive across re-runs
            # of the pre-semantic gate (kept on MigrationState alongside the
            # other gate decisions). Empty / missing → auto-derived.
            _canon_overrides = (state.get("canonical_overrides") or {}) or None
            # Fetch the DESTINATION tables' existing sample values so B21 can value-check each
            # mapping (a name match alone must not auto-merge unrelated entities). Best-effort:
            # empty/unreadable targets → {} and B21 falls back to the confidence band.
            _dest_samples: dict = {}
            try:
                from ...db import get_plenum_cafm_sample_values_by_table
                _dest_tbls = {_dest_table(src) for src in _ci_tables if _dest_table(src)}
                _dest_samples = await get_plenum_cafm_sample_values_by_table(
                    _dest_tbls, allow=_cols_by_tbl
                )
            except Exception:  # pragma: no cover — value check is additive
                _dest_samples = {}
            return build_column_intelligence(
                _ci_tables,
                dest_table_by_source={src: _dest_table(src) for src in _ci_tables},
                dest_col_by_source_col=_dest_col,
                conf_by_source_col=_conf_col,
                field_resolver=_field_resolver,
                canonical_overrides=_canon_overrides,
                dest_samples_by_table=_dest_samples,
                # Real destination columns so B21 can reject a match onto a column the
                # routed table doesn't have (e.g. 'asset id' → 'asset_code' on 'sites').
                dest_columns_by_table=_cols_by_tbl,
                # Unresolved best-guess suggestions → B21 mirrors Step 2 (lower-priority fallback).
                unresolved_suggest_by_source_col=_unres_sg,
            )
        except Exception as exc:  # pragma: no cover — additive, never fatal
            logger.warning(f"[Pre-Semantic Gate] column intelligence report failed: {exc}")
            return None

    # Pass 2 / resume: the report was built right after routing confirmation on pass 1. Reuse it so
    # the B20 / B14.1 human decisions baked into it persist into Step 2 and the write — and so a
    # resume costs nothing (LangGraph re-executes this node body on every resume).
    if state.get("pre_semantic_ci_built"):
        _column_intelligence = state.get("column_intelligence")

    # ── Step 2 ↔ B21.1 alignment ───────────────────────────────────────
    # Step 2 (this gate) shows the deterministic tier-1 target per column; B21.1
    # (column_intelligence.dest_mapping) is the ENTITY-AWARE authority that can DEMOTE a tier-1
    # match to a NEW column — e.g. vendors.id holds asset codes (canonical 'asset_id'), so it must
    # NOT merge into the vendor UUID PK. Align Step 2 TO B21: retarget such columns to B21's
    # canonical (created as a new column). Mutating the tier-1 mappings aligns BOTH the gate display
    # (review_items/auto_approved are rebuilt from them) AND the approved write (resume re-partitions
    # the same tier-1 mappings). Only columns the mapper actually mapped are touched. Best-effort.
    if _column_intelligence:
        try:
            from ...udr.gate_alignment import align_buckets_to_b21
            _aligned = align_buckets_to_b21(
                _column_intelligence.get("dest_mapping"),
                tier1_mappings_by_table,      # authoritative — drives the write path
                review_items_by_table,        # display copy (reviewable rows)
                auto_approved_by_table,        # display copy (auto-approved rows)
            )
            if _aligned:
                state["tier1_mappings_by_table"] = tier1_mappings_by_table
                logger.info(
                    f"[Pre-Semantic Gate] aligned {_aligned} column(s) to B21 new-column outcome"
                )

            # Reverse direction: columns B21 RESOLVED (via canonical-driven grouping, e.g.
            # property_ref -> site_id) that the mapper left UNRESOLVED. Surface B21's destination
            # as the Step-2 suggestion so the gate shows the SAME match B21.1 shows, one-click
            # approvable — instead of the column reading as "unresolved -> semantic". Additive:
            # only fills columns with no existing suggestion (a stronger name suggestion wins).
            from ...udr.gate_alignment import b21_suggestions_for_unresolved
            _b21_sugg = b21_suggestions_for_unresolved(
                _column_intelligence.get("dest_mapping"),
                tier1_mappings_by_table,
                is_pk=_is_real_pk,
            )
            if _b21_sugg:
                _sugg_state = dict(state.get("unresolved_suggestions_by_table") or {})
                _added = 0
                for _tbl, _cols in _b21_sugg.items():
                    _existing = dict(_sugg_state.get(_tbl) or {})
                    for _col, _entry in _cols.items():
                        if _col not in _existing:  # don't overwrite a stronger name suggestion
                            _existing[_col] = _entry
                            _added += 1
                    _sugg_state[_tbl] = _existing
                state["unresolved_suggestions_by_table"] = _sugg_state
                if _added:
                    logger.info(
                        f"[Pre-Semantic Gate] surfaced {_added} B21-resolved column(s) as Step-2 "
                        f"suggestions so Step 2 mirrors B21.1"
                    )
        except Exception as exc:  # pragma: no cover — additive, never fatal
            logger.warning(f"[Pre-Semantic Gate] Step-2 ↔ B21 alignment skipped: {exc}")

    review_payload = {
        "migration_id": migration_id,
        "gate": "pre_semantic",
        "total_reviewable": total_reviewable,
        "review_items_by_table": review_items_by_table,
        # Deterministic mappings already auto-approved (identity/alias) — shown read-only so
        # the user sees ALL mapped columns, not just the ones needing review.
        "auto_approved_by_table": auto_approved_by_table,
        "existing_canonical_tables": existing_canonical_tables,
        # 7.4 AC6 — actionable routing suggestion per source table (assign / create-new).
        "table_routing_suggestion_by_table": table_routing_suggestion_by_table,
        # Per UNMAPPED field: best-guess target + confidence + candidates, so the gate shows
        # "suggested: X (Y%)" with one-click approve (else the user sends it to semantic).
        "unresolved_suggestion_by_table": dict(state.get("unresolved_suggestions_by_table") or {}),
        # Content-aware table candidates per source sheet, ranked by column overlap:
        # {source_table: [{table, mapped, total, pct}]} — the gate offers these as one-click
        # "best match" options so the user can pick the right destination table.
        "table_match_candidates_by_table": dict(state.get("table_match_candidates_by_table") or {}),
        # Value-centric merge: {source_table: {representative_col: [merged_cols]}} — columns
        # with identical VALUES collapsed to one (ASSETNUM + asset_ref → one column). Shown
        # on the representative row so the user sees which headers were merged.
        "merged_columns_by_table": dict(state.get("merged_columns_by_table") or {}),
        # Challenge 1: partial-similarity column pairs (60–95% overlap) for the user to
        # review/merge — {source_table: [{column_a, column_b, overlap, sample_a, sample_b}]}.
        "near_duplicate_columns_by_table": dict(state.get("near_duplicate_columns_by_table") or {}),
        # table → its columns, for live column re-matching when the target changes.
        "canonical_columns_by_table": canonical_columns_by_table,
        # #10 document/table inventory (files → extracted tables) shown before mapping.
        "document_inventory": _doc_inventory,
        # B7.1→B12.1 — table-resolution report driving the Migration-Analysis step panels
        # (unique table identification → PK detection → deterministic / RAG / semantic mapping).
        "table_resolution": _table_resolution,
        # B13.1→B21.1 — column-intelligence report driving the Column-Intelligence Pipeline panels
        # (prefixing → metadata → FK candidates → format/value gates → grouping → classification →
        # unified names → destination column mapping).
        "column_intelligence": _column_intelligence,
        # Mapper's best-guess CAFM target table per source table (assets_test2 → assets),
        # so the dropdown can default to the matching table instead of the source name.
        "suggested_target_by_table": dict(state.get("table_routing") or {}),
        # Feature: per-table match confidence (0-1) for Step 1 — 1.0 = exact name
        # match, Haiku % for semantic guesses, 0 when unmatched.
        "table_match_confidence_by_table": dict(state.get("cafm_table_match_confidence") or {}),
        # Primary keys are confirmed in the separate pk_review gate (Group A) BEFORE this gate;
        # this routing/mapping gate no longer carries the PK proposal.
        "instructions": (
            "Review each deterministically matched field. "
            "Decision options per field:\n"
            "  'approve'  — accept the mapping as final (skips semantic matching)\n"
            "  'semantic' — reject and run through semantic matching instead\n"
            "Every deterministic match (incl. identity/alias) is shown; default is approve."
        ),
    }

    logger.info(
        f"[Pre-Semantic Gate] Interrupting for review: {total_reviewable} fields across "
        f"{len(review_items_by_table)} tables. migration_id={migration_id}"
    )
    state["pre_semantic_review_payload"] = review_payload

    # ── Split gate — PASS 1: "Confirm table routing" (phase=tables) ──────────────────────────────
    # The routing gate runs FIRST; it only CAPTURES the routing choice (pending_table_overrides).
    # Then B20 (grouping_review) and B14.1 (column_mapping_review) run, and the graph loops back for
    # PASS 2, which shows "Column matching" (phase=columns) and applies routing + matching together
    # (the intertwined resume logic below runs ONCE, on pass 2). Order: routing → B20 → B14.1 →
    # column matching.
    if not state.get("pre_semantic_ci_built"):
        # Cache the table-resolution report so the pass-2 / resume re-entry reuses it instead of
        # re-deduping every table and re-running the resolution scan (see the fast-path above).
        if _table_resolution:
            state["table_resolution_report"] = _table_resolution
        # Emit the TABLE analysis only. Column intelligence is NOT built yet — it is analysed against
        # the CONFIRMED destination table, so it runs after the human approves routing (below). This
        # is what lets the routing gate open immediately instead of waiting ~13s for column work.
        if migration_id:
            _pass1_output: dict = {
                # Marker so the FE does NOT render this as a "Pre-semantic review gate — completed"
                # snapshot (routing isn't the full pre-semantic step). It exists ONLY to carry the
                # analysis (udr_*) so the table cards stay visible; the real completed snapshot is
                # emitted at pass 2 (after column matching).
                "pre_semantic_routing_pass": True,
                "table_routing": dict(state.get("table_routing") or {}),
                "new_tables": list(state.get("new_tables") or []),
            }
            if _table_resolution:
                _pass1_output["udr_table_resolution"] = _table_resolution
            try:
                from .schema_db_writer import migration_append_node_log_auto
                await migration_append_node_log_auto(
                    migration_id, 3, "Pre-Semantic Review", datetime.utcnow(), datetime.utcnow(),
                    output=_pass1_output,
                    logs=["Table routing gate (pass 1) — awaiting routing confirmation"],
                )
            except Exception as _exc:  # pragma: no cover — best-effort; never block the gate
                logger.warning(f"[Pre-Semantic Gate] pass-1 node-log emit failed: {_exc}")
        # Routing gate: same payload but locked to the ROUTING phase so the FE shows Step 1 only.
        _routing_payload = {**review_payload, "locked_phase": "tables", "gate_step": "table_routing"}
        state["pre_semantic_review_payload"] = _routing_payload
        if migration_id:
            from .db_writer import write_gate_payload
            await write_gate_payload(migration_id, "pre_semantic", _routing_payload)
        logger.info("[Pre-Semantic Gate] Pass 1 — interrupting for TABLE ROUTING confirmation.")
        _routing_resume = interrupt(_routing_payload)
        # Capture the routing choice; the intertwined routing+matching processing runs on pass 2.
        _r_overrides = _routing_resume.get("table_overrides") if isinstance(_routing_resume, dict) else None
        _r_overrides = _r_overrides if isinstance(_r_overrides, dict) else {}
        state["pending_table_overrides"] = _r_overrides
        # Apply the simple destination update now (source → target) so the pass-2 column-matching
        # gate builds its review items against the CONFIRMED routing. The full reroute-to-unresolved
        # handling (which is intertwined with the column decisions) still runs once, on pass 2.
        if _r_overrides:
            _routing_now = dict(state.get("table_routing") or {})
            for _src, _ovr in _r_overrides.items():
                _tgt = (_ovr.get("target_table") if isinstance(_ovr, dict) else "") or ""
                if _tgt.strip():
                    _routing_now[_src] = _tgt.strip()
            state["table_routing"] = _routing_now
        if migration_id:
            from .db_writer import clear_gate_payload
            await clear_gate_payload(migration_id)
        logger.info(
            f"[Pre-Semantic Gate] Table routing confirmed — {len(state['pending_table_overrides'])} "
            "override(s) captured; building column intelligence against the confirmed routing."
        )

        # ── Routing is CONFIRMED — only now analyse the columns ──────────────────────────────────
        # Rebind the routing lookup so _dest_table() (and therefore every dest_table in the report)
        # reflects the human's confirmed destinations, not the pre-gate guesses.
        _routing = dict(state.get("table_routing") or {})
        _ci_started_at = datetime.utcnow()
        _column_intelligence = await _build_ci_report()
        if _column_intelligence is not None:
            state["column_intelligence"] = _column_intelligence
        state["pre_semantic_ci_built"] = True
        _ci_secs = (datetime.utcnow() - _ci_started_at).total_seconds()
        logger.info(
            f"[Pre-Semantic Gate] Column intelligence built after routing confirmation "
            f"({_ci_secs:.1f}s) — B20 / B14.1 next."
        )
        # Emit the column analysis so the column-intelligence cards appear now (post-routing).
        if migration_id and _column_intelligence:
            _ci_output: dict = {
                "pre_semantic_routing_pass": True,
                "table_routing": dict(state.get("table_routing") or {}),
                "new_tables": list(state.get("new_tables") or []),
                "udr_column_intelligence": _column_intelligence,
            }
            if _table_resolution:
                _ci_output["udr_table_resolution"] = _table_resolution
            try:
                from .schema_db_writer import migration_append_node_log_auto
                await migration_append_node_log_auto(
                    migration_id, 3, "Pre-Semantic Review", _ci_started_at, datetime.utcnow(),
                    output=_ci_output,
                    logs=[
                        "Table routing confirmed",
                        f"Column intelligence built against confirmed routing ({_ci_secs:.1f}s)",
                    ],
                )
            except Exception as _exc:  # pragma: no cover — best-effort; never block the gate
                logger.warning(f"[Pre-Semantic Gate] post-routing node-log emit failed: {_exc}")
        return state

    # ── PASS 2 — "Column matching" (phase=columns): write payload + interrupt ──────────
    _matching_payload = {**review_payload, "locked_phase": "columns", "gate_step": "column_matching"}
    state["pre_semantic_review_payload"] = _matching_payload
    if migration_id:
        from .db_writer import write_gate_payload
        await write_gate_payload(migration_id, "pre_semantic", _matching_payload)

    # ── Interrupt — resume with decisions dict ──────────────────────────
    # decisions_by_table: dict[table_name, list[{source_field, decision}]]
    _gate_started_at = datetime.utcnow()
    resumed = interrupt(_matching_payload)
    # Structured resume (WP-5): {"decisions": {...}, "table_overrides": {...},
    # "canonical_overrides": {...}}. Legacy flat resume: {table_name: [{source_field, decision}]}.
    if isinstance(resumed, dict) and "decisions" in resumed:
        decisions_by_table: dict = resumed.get("decisions") or {}
        table_overrides: dict = resumed.get("table_overrides") or {}
        canonical_overrides_in: dict = resumed.get("canonical_overrides") or {}
    else:
        # Flat/legacy resume. Keep ONLY per-table decision LISTS. A bulk / auto-drive resume is the
        # sentinel {"approve_all": True} — dropping non-list values leaves {}, which the loop below
        # treats as "approve every reviewable mapping as-is" (omitted tables/fields default to
        # approve). Without this filter "approve_all" was treated as a source-table name whose
        # decision list was the bool True, and iterating it raised "'bool' object is not iterable"
        # — failing every bulk-ingested file at this gate.
        raw = resumed if isinstance(resumed, dict) else {}
        decisions_by_table = {k: v for k, v in raw.items() if isinstance(v, list)}
        table_overrides = {}
        canonical_overrides_in = {}

    # Merge the routing captured at the PASS-1 "Confirm table routing" gate. The column-matching
    # gate (pass 2) submits only column decisions, so the routing overrides come from pass 1 —
    # apply them here with the (intertwined) routing + matching processing that follows.
    _pending_routing = state.get("pending_table_overrides") or {}
    if _pending_routing:
        _merged_overrides = dict(_pending_routing)
        if isinstance(table_overrides, dict):
            _merged_overrides.update(table_overrides)  # a pass-2 override (rare) wins
        table_overrides = _merged_overrides

    # Primary keys were confirmed in the separate pk_review gate (Group A); no PK handling here.

    # ── Clear gate payload now that we have decisions ──────────────────
    if migration_id:
        from .db_writer import clear_gate_payload
        await clear_gate_payload(migration_id)

    logger.info(
        f"[Pre-Semantic Gate] Resumed with decisions for {len(decisions_by_table)} tables"
    )

    # ── Identify source tables that the user is creating as NEW tables. ─────
    # Their columns are deterministically approved here and MUST skip the
    # semantic mapper — otherwise the same column ends up in both Approved
    # and Semantic queues, which is the bug the New Table rule prevents.
    source_is_new_lookup: dict[str, bool] = {}
    if isinstance(table_overrides, dict):
        for _src, _ovr in table_overrides.items():
            if isinstance(_ovr, dict) and bool(_ovr.get("is_new_table", False)):
                source_is_new_lookup[_src] = True

    def _snake_case(field: str) -> str:
        """Conservative fallback when the frontend omits target_field."""
        out = []
        prev_lower = False
        for ch in (field or "").strip():
            if ch.isalnum():
                if ch.isupper() and prev_lower:
                    out.append("_")
                out.append(ch.lower())
                prev_lower = ch.islower() or ch.isdigit()
            else:
                if out and out[-1] != "_":
                    out.append("_")
                prev_lower = False
        return "".join(out).strip("_") or "column"

    # ── Process decisions ──────────────────────────────────────────────
    updated_tier1_by_table: dict[str, list[FieldMapping]] = {}
    total_approved = 0
    total_sent_to_semantic = 0
    node_log_lines: list[str] = []  # raw per-field decisions surfaced in the Process log

    # Iterate over the UNION of:
    #   1. tables that produced T1 candidates (pre-existing behaviour), and
    #   2. tables that have decisions submitted at this gate, and
    #   3. tables flagged as new in table_overrides.
    # Without (2) and (3), a brand-new source table (zero T1 matches) would
    # have its approve decisions silently dropped — its unresolved fields
    # would then flow into the semantic mapper and double-count as both
    # Approved (UI) and Semantic (backend), which is exactly the bug fixed
    # by the "+ New Table" rule.
    ordered_tables: list[str] = list(tier1_mappings_by_table.keys())
    for t in decisions_by_table.keys():
        if t not in ordered_tables:
            ordered_tables.append(t)
    for t in source_is_new_lookup.keys():
        if t not in ordered_tables:
            ordered_tables.append(t)

    for table_name in ordered_tables:
        auto_pass_mappings = auto_pass_by_table.get(table_name, [])
        reviewable_mappings = reviewable_by_table.get(table_name, [])
        table_decisions = decisions_by_table.get(table_name, [])
        is_new_source = source_is_new_lookup.get(table_name, False)

        # Build decision lookup: source_field → "approve" | "semantic",
        # plus optional per-field target-column rename (WP-5 Node 2).
        decision_map: dict[str, str] = {}
        rename_map: dict[str, str] = {}
        data_type_map: dict[str, str] = {}  # source_field → user-chosen SQL type (new tables)
        for d in table_decisions:
            sf = d.get("source_field")
            if not sf:
                continue
            # New tables are approval-by-definition — defensively coerce any
            # accidental "semantic" decision back to "approve" so a stale
            # client payload can't leak fields into the semantic queue.
            raw_decision = d.get("decision", "approve")
            decision_map[sf] = "approve" if is_new_source else raw_decision
            tf = (d.get("target_field") or "").strip()
            if tf:
                rename_map[sf] = tf
            dt = (d.get("data_type") or "").strip()
            if dt:
                data_type_map[sf] = dt

        approved_mappings: list[FieldMapping] = []

        for m in reviewable_mappings:
            source_field = m.get("source_field")
            # New tables short-circuit: every field is auto-approved regardless
            # of what the decision_map says.
            decision = "approve" if is_new_source else decision_map.get(source_field, "approve")

            if decision == "approve":
                if is_new_source:
                    # When the user picks "+ New Table" for a source that had
                    # pre-existing T1 matches, those matches were scored
                    # against a DIFFERENT canonical table — preserving their
                    # target_field would create the new table with columns
                    # that don't belong here. Always overwrite with the
                    # snake_case of the source (or the user's explicit
                    # rename) and stamp T1_new_table provenance.
                    new_target = rename_map.get(source_field) or _snake_case(source_field or "")
                    m = {
                        **m,
                        "target_field": new_target,
                        "tier": "T1_new_table",
                        "confidence": 1.0,
                        "rationale": "Auto-approved column on a user-created new table",
                        "langsmith_run_id": None,
                    }
                elif source_field in rename_map:
                    # Apply a user column rename (changes the canonical column name that
                    # write_node will CREATE/ALTER for this field).
                    m = {**m, "target_field": rename_map[source_field]}
                approved_mappings.append(m)
                total_approved += 1
                _line = (
                    f"✓ APPROVED  {table_name}.{source_field} "
                    f"→ {m.get('target_field')} ({m.get('confidence', 0):.2f})"
                )
                node_log_lines.append(_line)
                logger.info(f"[Pre-Semantic Gate]   {_line}")
            else:  # "semantic"
                # Push back into unresolved so Node 3 picks it up
                if table_name not in unresolved_by_table:
                    unresolved_by_table[table_name] = []
                unresolved_by_table[table_name] = list(unresolved_by_table[table_name])
                unresolved_by_table[table_name].append(source_field)
                total_sent_to_semantic += 1
                _line = f"→ SEMANTIC  {table_name}.{source_field} (was {m.get('tier')})"
                node_log_lines.append(_line)
                logger.info(f"[Pre-Semantic Gate]   {_line}")

        # Manually-assigned unresolved ("left-out") fields: the gate lets the user
        # map a previously-unresolved source field to a leftover target column. These
        # arrive as approve decisions for fields NOT in reviewable_mappings — promote
        # them to T1 mappings and drop them from the unresolved list so they skip Node 3.
        reviewable_fields = {m.get("source_field") for m in reviewable_mappings}
        for sf, decision in decision_map.items():
            if sf in reviewable_fields or decision != "approve":
                continue
            # For new tables, fall back to a snake-case auto-target so an
            # accidentally-empty target_field on the wire still produces a
            # mapping rather than dropping the column.
            tf = rename_map.get(sf) or (_snake_case(sf) if is_new_source else None)
            if not tf:
                continue
            approved_mappings.append(FieldMapping(
                source_field=sf,
                target_field=tf,
                confidence=1.0,
                tier="T1_new_table" if is_new_source else "T1_manual",
                rationale=(
                    "Auto-approved column on a user-created new table"
                    if is_new_source
                    else "User-assigned target column at the pre-semantic gate"
                ),
                langsmith_run_id=None,
            ))
            if table_name in unresolved_by_table:
                unresolved_by_table[table_name] = [
                    f for f in unresolved_by_table[table_name] if f != sf
                ]
            total_approved += 1
            _line = (
                f"✓ NEW_TABLE {table_name}.{sf} → {tf}"
                if is_new_source
                else f"✓ ASSIGNED  {table_name}.{sf} → {tf} (user)"
            )
            node_log_lines.append(_line)
            logger.info(f"[Pre-Semantic Gate]   {_line}")

        # For new-table source tables, sweep up ANY unresolved field that
        # didn't get an explicit decision (defensive — the frontend should
        # always send all of them, but if anything slips through it must
        # still be auto-approved, never sent to semantic).
        if is_new_source and table_name in unresolved_by_table:
            already_approved = {a.get("source_field") for a in approved_mappings}
            remaining = [f for f in unresolved_by_table[table_name] if f not in already_approved]
            for sf in remaining:
                tf = rename_map.get(sf) or _snake_case(sf)
                approved_mappings.append(FieldMapping(
                    source_field=sf,
                    target_field=tf,
                    confidence=1.0,
                    tier="T1_new_table",
                    rationale="Auto-approved column on a user-created new table",
                    langsmith_run_id=None,
                ))
                total_approved += 1
                _line = f"✓ NEW_TABLE {table_name}.{sf} → {tf} (auto)"
                node_log_lines.append(_line)
                logger.info(f"[Pre-Semantic Gate]   {_line}")
            # Drop the entire entry so the semantic mapper never iterates it.
            unresolved_by_table.pop(table_name, None)

        # New table: stamp each approved column with the user-chosen SQL type so
        # write_node's CREATE TABLE uses it instead of a sample-inferred type.
        if is_new_source and data_type_map:
            for am in approved_mappings:
                _dt = data_type_map.get(am.get("source_field"))
                if _dt:
                    am["data_type"] = _dt

        # Final tier1 for this table = alias auto-pass + human-approved
        # (auto-pass should be empty for fully-new source tables, but the
        # merge is safe either way.)
        updated_tier1_by_table[table_name] = auto_pass_mappings + approved_mappings

    # ── Apply table-level overrides — rename / create new table (WP-5 Node 2) ──
    # These reuse the exact state keys write_node consumes (table_routing,
    # new_tables, extra_fields_config) and are merged with any existing values so
    # they survive the later field-mapping gate and flow through to the DB write.
    if table_overrides:
        table_routing: dict = dict(state.get("table_routing") or {})
        new_tables_list: list = list(state.get("new_tables") or [])
        extra_fields_config: list = list(state.get("extra_fields_config") or [])
        # The target each source table's tier-1 mappings were computed against
        # (Node 2). Used to detect a reroute so stale mappings get re-matched.
        detected_matches: dict = state.get("cafm_table_matches") or {}

        for source_table, override in table_overrides.items():
            if not isinstance(override, dict):
                continue
            target_table = (override.get("target_table") or "").strip()
            if not target_table:
                continue
            is_new = bool(override.get("is_new_table", False))
            table_routing[source_table] = target_table

            if is_new:
                if target_table not in new_tables_list:
                    new_tables_list.append(target_table)
                # A trigger entry tells write_node to CREATE this table; its columns
                # are filled from the (possibly renamed) mapped fields of the source.
                already = any(
                    e.get("source_table") == source_table
                    and e.get("target_table") == target_table
                    and e.get("is_new_table")
                    for e in extra_fields_config
                )
                if not already:
                    extra_fields_config.append({
                        "source_field": "",
                        "source_table": source_table,
                        "storage_strategy": "custom",
                        "target_table": target_table,
                        "custom_column_name": "",
                        "data_type": "",
                        "is_new_table": True,
                        "new_table_pk": "id",
                        "nullable": True,
                        "user_approved": True,
                    })
            else:
                # Rerouted to a DIFFERENT existing target than Node 2 mapped against:
                # the tier-1 column mappings are stale (they reference the OLD
                # target's columns). Drop them and push every source column back to
                # unresolved so the semantic mapper (Node 3) re-matches the WHOLE
                # table against the NEW target table's columns — otherwise the
                # column-matching step keeps showing the old target's columns.
                _prev = (detected_matches.get(source_table) or "").strip().lower()
                if target_table.strip().lower() != _prev:
                    _moved = list(unresolved_by_table.get(source_table) or [])
                    for _m in updated_tier1_by_table.get(source_table) or []:
                        _sf = _m.get("source_field") if isinstance(_m, dict) else None
                        if _sf and _sf not in _moved:
                            _moved.append(_sf)
                    updated_tier1_by_table[source_table] = []
                    unresolved_by_table[source_table] = _moved
                    _rl = (
                        f"reroute: re-mapping {len(_moved)} column(s) of "
                        f"'{source_table}' against '{target_table}'"
                    )
                    node_log_lines.append(_rl)
                    logger.info(f"[Pre-Semantic Gate]   {_rl}")
            _line = f"table override: '{source_table}' → '{target_table}' (new_table={is_new})"
            node_log_lines.append(_line)
            logger.info(f"[Pre-Semantic Gate]   {_line}")

        state["table_routing"] = table_routing
        state["new_tables"] = new_tables_list
        state["extra_fields_config"] = extra_fields_config

    # ── Persist HITL canonical-name overrides on state ────────────────────────
    # The Unified-Column-Names panel lets the user pin canonical names per group
    # (or per column). When set, every subsequent re-run of build_column_intelligence
    # honours these pins and the column_canonical map flows to downstream nodes
    # (writer / RAG / work-cloud) under the user-chosen identifier.
    if isinstance(canonical_overrides_in, dict) and canonical_overrides_in:
        existing = dict(state.get("canonical_overrides") or {})
        for k, v in canonical_overrides_in.items():
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            v = v.strip()
            if v:
                existing[k] = v
            else:
                existing.pop(k, None)
        state["canonical_overrides"] = existing
        logger.info(f"[Pre-Semantic Gate] applied canonical overrides: {existing}")

    # ── Update state ───────────────────────────────────────────────────
    state["tier1_mappings_by_table"] = updated_tier1_by_table
    state["tier1_approved_by_table"] = updated_tier1_by_table
    state["unresolved_by_table"] = unresolved_by_table
    # Persist the column_intelligence report (column_canonical map etc.) onto
    # state so downstream gates (human_review / Tier-2) and the
    # /state-canonicals endpoint can read it. Without this it lived only in
    # the interrupt payload and was lost on resume.
    #
    # CRITICAL: _column_intelligence was built (above) from state.canonical_overrides as it was
    # BEFORE this submit's pins were merged — the build runs before interrupt(), but THIS submit's
    # pins arrive via interrupt and were only merged into state.canonical_overrides just above.
    # Re-stamp the report with the now-current overrides so a pin made in this submit (e.g.
    # vendors.id → asset_id) actually lands in state.column_intelligence; otherwise the Tier-2
    # gate + /state-canonicals endpoint keep returning the stale source name.
    if _column_intelligence is not None:
        from ...udr.column_intelligence import apply_canonical_overrides

        apply_canonical_overrides(_column_intelligence, state.get("canonical_overrides") or {})
        state["column_intelligence"] = _column_intelligence

    # Recalculate tier1_mapped_count to reflect what actually survived
    state["tier1_mapped_count"] = sum(
        len(m) for m in updated_tier1_by_table.values()
    )

    # TEMP [tier-trace] — the gate OUTPUT it writes to state: the approved Tier-1 baseline and
    # the unresolved set going to semantic. Compare against the semantic INPUT trace: if the
    # tier1 totals differ, the loss is the LangGraph state hand-off; if equal, the gate retains
    # the approvals correctly. Remove once verified.
    logger.info(
        "[tier-trace] pre-semantic gate OUTPUT: approved=%d sent_to_semantic=%d | "
        "tier1_total=%d tier1_by_table=%s | unresolved_total=%d unresolved_by_table=%s",
        total_approved, total_sent_to_semantic,
        sum(len(v) for v in updated_tier1_by_table.values()),
        {k: [m.get("source_field") for m in v] for k, v in updated_tier1_by_table.items()},
        sum(len(v) for v in unresolved_by_table.values()),
        {k: list(v) for k, v in unresolved_by_table.items()},
    )

    logger.info(
        f"[Pre-Semantic Gate] ══ Summary ══ "
        f"approved={total_approved}, sent_to_semantic={total_sent_to_semantic}"
    )

    state["event_log"].append({
        "timestamp": datetime.utcnow().isoformat(),
        "event": "gate_complete",
        "gate": "pre_semantic",
        "detail": (
            f"{total_approved} approved, {total_sent_to_semantic} sent to semantic matching"
        ),
    })

    migration_id = state.get("migration_id")
    if migration_id:
        from .schema_db_writer import migration_append_node_log_auto

        # Persist the slim subset of state that the frontend's semantic-snapshot
        # relabel needs (see buildNewColumnLookup in step-pause.tsx).
        #
        # Without this, a historical Node-3 snapshot that captured a field as
        # "unmappable" can never be relabeled to "new column" — buildNewColumnLookup
        # walks each node's output looking for new_tables / table_routing /
        # tier1_mappings_by_table / extra_fields_config, and finds nothing because
        # those slots only live on the LangGraph state, never on the node log.
        #
        # We write only the new-column / new-table mappings (T1_new_table,
        # T1_manual) to keep the row small, since that's all the relabel
        # consumes — the full tier1 set is already available via state.
        _slim_tier1_for_relabel: dict[str, list[dict]] = {}
        for _tbl, _maps in (state.get("tier1_mappings_by_table") or {}).items():
            _kept: list[dict] = []
            for _m in _maps:
                _tier = (_m.get("tier") or "") if isinstance(_m, dict) else ""
                if _tier in ("T1_new_table", "T1_manual"):
                    _kept.append({
                        "source_field": _m.get("source_field"),
                        "target_field": _m.get("target_field"),
                        "tier": _tier,
                    })
            if _kept:
                _slim_tier1_for_relabel[_tbl] = _kept

        _output_for_relabel: dict = {
            "approved": total_approved,
            "sent_to_semantic": total_sent_to_semantic,
            "updated_tier1_count": state.get("tier1_mapped_count", 0),
            "new_tables": list(state.get("new_tables") or []),
            "table_routing": dict(state.get("table_routing") or {}),
            "extra_fields_config": list(state.get("extra_fields_config") or []),
            "tier1_mappings_by_table": _slim_tier1_for_relabel,
        }

        # B7.1→B12.1 / B13.1→B21.1 — persist the FULL table-resolution + column-intelligence
        # reports to the DB node log too, not just the transient gate payload (which is wiped on
        # resume). Without this they only re-appear at node 11 (post-write): between the gate
        # resolving and completion the status response would fall back to the node-1 B7.1 PARTIAL
        # and a null column report, so the left-panel step sequence would lose its detail through
        # nodes 4–9. Node 11 later overwrites these with the identical final reports — the status
        # reader (_udr_node_output_field) takes the LAST node-log match, so newer always wins.
        if _table_resolution:
            _output_for_relabel["udr_table_resolution"] = _table_resolution
        if _column_intelligence:
            _output_for_relabel["udr_column_intelligence"] = _column_intelligence

        await migration_append_node_log_auto(
            migration_id, 3, "Pre-Semantic Review", _gate_started_at, datetime.utcnow(),
            output=_output_for_relabel,
            logs=[
                "Gate: pre-semantic human review",
                f"{total_approved} approved · {total_sent_to_semantic} → semantic",
                *node_log_lines,
            ],
        )

    # Mark the gate answered so a re-entry (e.g. after a table-routing override re-partitions
    # tier-1 mappings) proceeds straight through instead of re-opening the same gate.
    state["pre_semantic_reviewed"] = True
    return state
