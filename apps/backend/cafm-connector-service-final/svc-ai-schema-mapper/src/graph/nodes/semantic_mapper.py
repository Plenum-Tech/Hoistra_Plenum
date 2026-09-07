"""Node 3: Semantic mapper — Tier 2 embedding-based field mapping (MULTI-TABLE).

For fields unresolved after Tier 1, per source table:
1. Embed each unresolved field with context (name | description | sample values)
2. Compute cosine similarity vs all cached canonical field embeddings
3. Classify by confidence (bands from mapping_cutoff.py — F7 7.4-AC4/AC5, 7.7-AC2/AC3):
   - ≥ SEMANTIC_AUTO_MIN (0.85): auto-accept (tier2_auto_by_table)
   - ≥ SEMANTIC_REVIEW_MIN (0.70) and < 0.85: 'Suggested' — flag for one-click
     confirm (tier2_flagged_by_table)
   - < 0.70: 'Requires Review' — manual assign (tier2_unmappable_by_table)

EL-M.3: Embeddings computed, scores in 0–1, top-3 present.
"""

import difflib
import re
from datetime import datetime
from uuid import uuid4

from ...db import get_plenum_cafm_columns_by_table
from ...embeddings import embed_texts_batch, find_top_matches
from ...matchers.registry import registry_append
from ..state import FieldMapping, MigrationState
from ._timing import timed_node


# ── #6: per-component match score breakdown (display-only; never alters routing) ──
_NUM_RE = re.compile(r"^-?\d+(\.\d+)?$")
_CODE_RE = re.compile(r"^[A-Za-z]{1,5}[-_ ]?\d+[A-Za-z0-9-]*$")
_NUMERICISH_TARGET = re.compile(
    r"(id|code|no|num|count|qty|quantity|amount|cost|price|year|date|age|level|priority|stock|reading|meter)",
    re.I,
)
_TEXTISH_TARGET = re.compile(r"(name|title|desc|description|note|comment|address|email|remark|summary)", re.I)


def _norm_field(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _keyword_score(a: str, b: str) -> float:
    na, nb = _norm_field(a), _norm_field(b)
    if not na or not nb:
        return 0.0
    return round(difflib.SequenceMatcher(None, na, nb).ratio(), 4)


def _numeric_pattern_score(samples: list) -> float:
    """How strongly the source column's values follow a numeric / code pattern.
    This is the 'numeric similarity' signal that was previously not surfaced."""
    vals = [str(v).strip() for v in (samples or []) if v is not None and str(v).strip()]
    if not vals:
        return 0.0
    numeric = sum(1 for v in vals if _NUM_RE.match(v))
    code = sum(1 for v in vals if _CODE_RE.match(v))
    return round(max(numeric, code) / len(vals), 4)


def _datatype_score(samples: list, target: str) -> float:
    vals = [str(v).strip() for v in (samples or []) if v is not None and str(v).strip()]
    if not vals:
        return 1.0
    src_numeric = (sum(1 for v in vals if _NUM_RE.match(v)) / len(vals)) >= 0.6
    t = str(target or "")
    tgt_numeric = bool(_NUMERICISH_TARGET.search(t))
    tgt_textual = bool(_TEXTISH_TARGET.search(t))
    if src_numeric and tgt_textual and not tgt_numeric:
        return 0.5
    if not src_numeric and tgt_numeric and not tgt_textual:
        return 0.6
    return 1.0


def _ontology_score(source_field: str, target_field: str) -> float:
    try:
        from ...matchers.fm_ontology import fm_field_lookup
        res = fm_field_lookup(source_field)
        parts = res if isinstance(res, (tuple, list)) else (res,)
        norm_t = _norm_field(target_field)
        if any(isinstance(p, str) and _norm_field(p) == norm_t for p in parts):
            return 0.97
        if any(isinstance(p, str) and p for p in parts):
            return 0.7
    except Exception:
        pass
    return 0.5


def build_score_breakdown(source_field: str, target_field: str, samples: list, cosine: float) -> dict:
    semantic = round(float(cosine), 4)
    keyword = _keyword_score(source_field, target_field)
    datatype = _datatype_score(samples, target_field)
    numeric_pattern = _numeric_pattern_score(samples)
    ontology = _ontology_score(source_field, target_field)
    final = round(
        0.45 * semantic + 0.20 * keyword + 0.10 * datatype + 0.10 * numeric_pattern + 0.15 * ontology,
        4,
    )
    return {
        "source_column": source_field,
        "target_column": target_field,
        "semantic_score": semantic,
        "keyword_score": keyword,
        "datatype_score": datatype,
        "numeric_pattern_score": numeric_pattern,
        "ontology_score": ontology,
        "final_score": final,
    }


def _norm_col(s: str) -> str:
    """Normalize a field/column name for cross-comparison (lowercase, alnum only)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())

_HARDCODED_CANONICAL_FIELDS: dict[str, str] = {
    "asset_code": "Unique identifier for equipment or asset",
    "asset_name": "Human-readable name of the asset",
    "category": "Equipment category or type",
    "location_code": "Site, building, or location identifier",
    "make": "Equipment manufacturer or brand",
    "model": "Equipment model number",
    "serial": "Equipment serial number",
    "wo_code": "Work order identifier",
    "wo_priority": "Work order priority level",
    "wo_status": "Work order status",
    "wo_type": "Work order type",
    "maintenance_type": "Maintenance plan or type",
    "sm_code": "Scheduled maintenance code",
    "trigger_type": "Time-based or meter-based trigger",
    "schedule_interval": "Maintenance frequency or interval",
    "sm_priority": "Maintenance priority",
    "part_code": "Spare part code",
    "stock_on_hand": "Current inventory quantity",
    "minimum_allowed_stock": "Reorder point or minimum stock level",
    "supplier": "Part supplier or vendor",
    "bom_group_name": "Bill of materials group",
    "user_full_name": "User or technician full name",
    "user_title": "User job title or role",
    "user_name": "Login name or username",
    "reports_to": "Manager or supervisor",
    "inspector_name": "Inspection personnel name",
    "inspection_date": "Date of inspection or survey",
    "inspection_location": "Inspection site or location",
    "finding_type": "Issue or defect type",
    "risk_level": "Risk or severity level",
}

from cafm_shared.logging import get_logger
logger = get_logger(__name__)

from .mapping_cutoff import SEMANTIC_AUTO_MIN, SEMANTIC_REVIEW_MIN  # F7 7.4/7.7 semantic bands


@timed_node("semantic")
async def semantic_mapper_node(state: MigrationState) -> MigrationState:
    """
    Node 3: Embed unresolved fields per table and match semantically — MULTI-TABLE.
    """

    _node_started_at = datetime.utcnow()
    migration_id = state.get("migration_id")
    unresolved_by_table = state.get("unresolved_by_table", {})
    column_descriptions = state.get("column_descriptions", {})
    parsed_tables = state.get("parsed_tables", {})

    total_unresolved = sum(len(fields) for fields in unresolved_by_table.values())
    logger.info(f"[Node 3] Starting semantic mapping (MULTI-TABLE): {total_unresolved} unresolved fields")
    # TEMP [tier-trace] — the Tier-1 baseline RECEIVED by semantic + the unresolved set it will
    # process. If tier1 count here is < what the pre-semantic gate approved, the approved
    # mappings were dropped in the gate→semantic state hand-off. Remove once verified.
    _t1_in = state.get("tier1_mappings_by_table", {}) or {}
    logger.info(
        "[tier-trace] semantic INPUT: tier1_total=%d tier1_by_table=%s | unresolved_total=%d unresolved_by_table=%s",
        sum(len(v) for v in _t1_in.values()),
        {k: [m.get("source_field") for m in v] for k, v in _t1_in.items()},
        total_unresolved,
        {k: list(v) for k, v in unresolved_by_table.items()},
    )

    if not total_unresolved:
        logger.info("[Node 3] No unresolved fields; skipping semantic mapper")
        state["tier2_auto_by_table"] = {}
        state["tier2_flagged_by_table"] = {}
        state["tier2_unmappable_by_table"] = {}
        state["current_step"] = 3
        return state

    try:
        from ...app import get_openai_client
        from ...embeddings import (
            _CANONICAL_EMBEDDINGS_CACHE,
            initialize_canonical_embeddings,
        )

        client = get_openai_client()

        # Safety net: if the cache is empty (worker process, or startup OpenAI failure),
        # initialise canonical embeddings now using the same client that already works.
        if not _CANONICAL_EMBEDDINGS_CACHE:
            logger.warning(
                "[Node 3] Canonical embeddings cache empty — initializing lazily"
            )
            try:
                from ...services.registry_cache import load_or_build
                from ...config import get_settings
                _cfg = await load_or_build(get_settings().db_url)
                _fields = _cfg.get("canonical_fields", {})
                if not _fields:
                    logger.warning(
                        "[Node 3] DB registry has 0 canonical fields — "
                        "falling back to hardcoded canonical field definitions"
                    )
                    _fields = _HARDCODED_CANONICAL_FIELDS
                await initialize_canonical_embeddings(client, _fields)
                logger.info(
                    f"[Node 3] Lazy init complete: "
                    f"{len(_CANONICAL_EMBEDDINGS_CACHE)} canonical embeddings cached"
                )
            except Exception as _lazy_err:
                logger.error(f"[Node 3] Lazy init failed: {_lazy_err}")

        tier2_auto_by_table = {}
        tier2_flagged_by_table = {}
        tier2_unmappable_by_table = {}
        all_confidences = []

        # Constrain matches to the routed target table's columns (generic per-table) so a
        # source column only maps to a column that exists on its target CAFM table.
        columns_by_table = await get_plenum_cafm_columns_by_table()
        table_routing = state.get("table_routing", {}) or {}
        node_log_lines: list[str] = []  # raw per-field decisions surfaced in the Process log

        # Tables the user explicitly created as new at the pre-semantic gate.
        # Their target tables don't exist in plenum_cafm yet, so semantic
        # matching has no canonical columns to compare against AND the
        # business rule says new-table columns are approved by definition.
        # Defensive — pre_semantic_review_node should have already drained
        # these from unresolved_by_table, but if anything slips through we
        # must NOT semantically score it.
        new_tables_set = {str(t).strip() for t in (state.get("new_tables") or []) if str(t).strip()}
        new_source_tables_set = {
            src for src, tgt in table_routing.items()
            if str(tgt).strip() in new_tables_set
        }

        # MULTI-TABLE: Process each source table's unresolved fields
        for table_name in sorted(unresolved_by_table.keys()):
            unresolved_fields = unresolved_by_table[table_name]
            if table_name in new_source_tables_set:
                logger.info(
                    f"[Node 3] Table {table_name}: source maps to a NEW table — "
                    f"skipping semantic mapping ({len(unresolved_fields)} fields)"
                )
                tier2_auto_by_table[table_name] = []
                tier2_flagged_by_table[table_name] = []
                tier2_unmappable_by_table[table_name] = []
                continue
            if not unresolved_fields:
                logger.info(f"[Node 3] Table {table_name}: no unresolved fields")
                tier2_auto_by_table[table_name] = []
                tier2_flagged_by_table[table_name] = []
                tier2_unmappable_by_table[table_name] = []
                continue

            logger.info(f"[Node 3] ► Processing table {table_name}: {len(unresolved_fields)} unresolved")

            # Allowed target columns for this source table's routed CAFM table.
            target_entity = (table_routing.get(table_name) or "").lower()
            allowed_norm = None
            if target_entity and columns_by_table.get(target_entity):
                allowed_norm = {_norm_col(c) for c in columns_by_table[target_entity]}
                logger.info(
                    f"[Node 3]   Constraining matches to '{target_entity}' columns "
                    f"({len(allowed_norm)} cols)"
                )

            tier2_auto = []
            tier2_flagged = []
            tier2_unmappable = []
            table_confidences = []

            # ── Batch embedding for this table's unresolved fields ──
            embedding_texts = {}
            texts_to_embed = []

            for source_field in unresolved_fields:
                description = column_descriptions.get(source_field, "Unknown field type")
                sample_values = _get_sample_values(parsed_tables, source_field, max_samples=3)
                sample_str = ", ".join(sample_values) if sample_values else "(no samples)"

                embedding_text = f"{source_field} | {description} | {sample_str}"
                embedding_texts[embedding_text] = (source_field, sample_values)
                texts_to_embed.append(embedding_text)

            # Batch API call for all this table's fields
            embeddings_dict = await embed_texts_batch(client, texts_to_embed)
            logger.info(f"[Node 3]   Batch embedding: {len(embeddings_dict)}/{len(texts_to_embed)} succeeded")

            # Process each field with its embedding
            for embedding_text, (source_field, sample_values) in embedding_texts.items():
                source_embedding = embeddings_dict.get(embedding_text)
                if source_embedding is None:
                    logger.warning(f"[Node 3]   Failed to embed {source_field}")
                    tier2_unmappable.append(source_field)
                    continue

                top_matches = find_top_matches(source_embedding, top_k=12 if allowed_norm else 3)
                if allowed_norm is not None:
                    # Drop candidates that aren't columns of this table's target CAFM table.
                    top_matches = [(t, c) for (t, c) in top_matches if _norm_col(t) in allowed_norm]
                top_matches = top_matches[:3]
                if not top_matches:
                    logger.info(
                        f"[Node 3]   ✗ No in-table match for {source_field} "
                        f"(target '{target_entity or 'unknown'}') — unmappable"
                    )
                    tier2_unmappable.append(source_field)
                    continue

                best_target, best_confidence = top_matches[0]
                langsmith_run_id = str(uuid4())
                # #6: per-component score breakdown (display-only — best match is
                # still chosen by cosine above; this never changes routing).
                breakdown = build_score_breakdown(
                    source_field, best_target, sample_values, best_confidence
                )

                logger.info(f"[Node 3]   {source_field} → {best_target} ({best_confidence:.3f})")

                if best_confidence >= SEMANTIC_AUTO_MIN:
                    # Auto-accept
                    mapping = FieldMapping(
                        source_field=source_field, target_field=best_target,
                        confidence=best_confidence, tier="T2_semantic",
                        rationale=f"Semantic embedding match ({best_confidence:.3f})",
                        sample_values=sample_values, langsmith_run_id=langsmith_run_id,
                    )
                    mapping["score_breakdown"] = breakdown
                    tier2_auto.append(mapping)
                    table_confidences.append(best_confidence)
                    node_log_lines.append(
                        f"✓ AUTO  {table_name}.{source_field} → {best_target} ({best_confidence:.2f})"
                    )
                    logger.info(f"[Node 3]   ✓ AUTO-ACCEPT")

                    # Append to registry so future runs skip LLM for this alias
                    try:
                        await registry_append(
                            alias=source_field,
                            canonical=best_target,
                            source_cmms=state.get("cmms_name", "Unknown"),
                            confidence=best_confidence,
                            approved_by="auto",
                            migration_id=state.get("migration_id"),
                        )
                    except Exception as _reg_err:
                        logger.debug(f"[Node 3]   Registry append failed (non-fatal): {_reg_err}")

                elif best_confidence >= SEMANTIC_REVIEW_MIN:
                    # >=70% and <85% — 'Suggested' (flag for one-click confirm)
                    top_for_ui = find_top_matches(source_embedding, top_k=20 if allowed_norm else 12)
                    if allowed_norm is not None:
                        top_for_ui = [m for m in top_for_ui if _norm_col(m[0]) in allowed_norm]
                    top_for_ui = top_for_ui[:12]
                    suggestions = [
                        {"target": match[0], "confidence": match[1]} for match in top_for_ui
                    ]
                    mapping = FieldMapping(
                        source_field=source_field, target_field=best_target,
                        confidence=best_confidence, tier="T2_semantic",
                        rationale=f"Semantic match flagged ({best_confidence:.3f})",
                        sample_values=sample_values, langsmith_run_id=langsmith_run_id,
                    )
                    mapping["suggestions"] = suggestions
                    mapping["score_breakdown"] = breakdown
                    tier2_flagged.append(mapping)
                    table_confidences.append(best_confidence)
                    node_log_lines.append(
                        f"⚠ FLAGGED  {table_name}.{source_field} → {best_target} ({best_confidence:.2f})"
                    )
                    logger.info(f"[Node 3]   ⚠ FLAGGED FOR REVIEW")

                else:
                    # Unmappable — store best-attempt target + confidence for UI display
                    node_log_lines.append(
                        f"✗ UNMAPPABLE  {table_name}.{source_field} "
                        f"(best {best_target} {best_confidence:.2f})"
                    )
                    logger.info(f"[Node 3]   ✗ UNMAPPABLE ({best_confidence:.3f})")
                    tier2_unmappable.append({
                        "source_field": source_field,
                        "best_target": best_target,
                        "best_confidence": best_confidence,
                    })

            tier2_auto_by_table[table_name] = tier2_auto
            tier2_flagged_by_table[table_name] = tier2_flagged
            tier2_unmappable_by_table[table_name] = tier2_unmappable
            all_confidences.extend(table_confidences)

            logger.info(
                f"[Node 3] ✓ Table {table_name}: {len(tier2_auto)} auto, {len(tier2_flagged)} flagged, {len(tier2_unmappable)} unmappable"
            )

        # ── Reconcile unmappable vs value/ontology evidence ──────────
        # The embedding mapper scores names, so a field whose NAME doesn't match (asset_no) lands
        # in unmappable even when the value-pattern/ontology analysis already found a confident
        # destination (column_intelligence.dest_mapping: asset_no → asset_id "suggested — confirm").
        # Promote those from unmappable → flagged so the field shows its suggested match (for the
        # user to confirm at the gate) instead of a bare "unmappable". Moving between buckets keeps
        # the total unchanged, so the EL-M.3 count check below still holds.
        _ci_dm = {}
        for _d in ((state.get("column_intelligence") or {}).get("dest_mapping") or []):
            if isinstance(_d, dict) and _d.get("source"):
                _ci_dm[str(_d["source"]).lower()] = _d
        if _ci_dm:
            _SUGGEST_OUTCOMES = {"suggested — confirm", "matched", "auto-resolved"}
            for _tbl, _unmap in list(tier2_unmappable_by_table.items()):
                if not _unmap:
                    continue
                _kept = []
                for _item in _unmap:
                    _field = _item if isinstance(_item, str) else (
                        _item.get("source_field") if isinstance(_item, dict) else None)
                    _d = _ci_dm.get(f"{_tbl}.{_field}".lower()) if _field else None
                    _matched = _d.get("matched_column") if _d else None
                    if _d and _matched and _d.get("outcome") in _SUGGEST_OUTCOMES:
                        tier2_flagged_by_table.setdefault(_tbl, []).append({
                            "source_field": _field,
                            "target_field": _matched,
                            "confidence": _d.get("confidence") or 0.0,
                            "tier": "T2_value_suggested",
                            "rationale": f"Value/ontology match to '{_matched}' ({_d.get('outcome')})",
                            "status": "flagged",
                        })
                        logger.info(
                            f"[Node 3] reconciled {_tbl}.{_field}: unmappable → flagged "
                            f"(suggested {_matched} @ {_d.get('confidence')})"
                        )
                    else:
                        _kept.append(_item)
                tier2_unmappable_by_table[_tbl] = _kept

        # ── EL-M.3 Validation ────────────────────────────────────────
        total_auto = sum(len(m) for m in tier2_auto_by_table.values())
        total_flagged = sum(len(m) for m in tier2_flagged_by_table.values())
        total_unmappable = sum(len(m) for m in tier2_unmappable_by_table.values())
        validated_total = total_auto + total_flagged + total_unmappable

        if validated_total != total_unresolved:
            logger.error(f"[Node 3] EL-M.3 FAILED: {validated_total} != {total_unresolved}")
            state["error_message"] = "Semantic mapping count mismatch"
            state["el_m3_passed"] = False
            return state

        for mappings in list(tier2_auto_by_table.values()) + list(tier2_flagged_by_table.values()):
            for mapping in mappings:
                conf = mapping.get("confidence", 0)
                if not (0 <= conf <= 1):
                    logger.error(f"[Node 3] EL-M.3 FAILED: Invalid confidence {conf}")
                    state["el_m3_passed"] = False
                    return state

        state["el_m3_passed"] = True
        logger.info("[Node 3] EL-M.3 PASSED")

        # ── Update state ───────────────────────────────────────────
        state["tier2_auto_by_table"] = tier2_auto_by_table
        state["tier2_flagged_by_table"] = tier2_flagged_by_table
        state["tier2_unmappable_by_table"] = tier2_unmappable_by_table
        # TEMP [tier-trace] — semantic OUTPUT + the Tier-1 baseline still on state (semantic must
        # NOT touch tier1). If tier1 is intact here but the next gate shows approved fields as
        # unmappable, the loss is in the field_mapping gate payload / frontend, not semantic.
        _t1_after = state.get("tier1_mappings_by_table", {}) or {}
        logger.info(
            "[tier-trace] semantic OUTPUT: tier1_preserved=%d | tier2 auto=%d flagged=%d unmappable=%d unmappable_by_table=%s",
            sum(len(v) for v in _t1_after.values()),
            sum(len(v) for v in tier2_auto_by_table.values()),
            sum(len(v) for v in tier2_flagged_by_table.values()),
            sum(len(v) for v in tier2_unmappable_by_table.values()),
            {k: v for k, v in tier2_unmappable_by_table.items() if v},
        )

        # Recalculate overall confidence (Tier 1 + Tier 2 auto)
        tier1_confidences = []
        for mappings in state.get("tier1_mappings_by_table", {}).values():
            tier1_confidences.extend([m.get("confidence", 0) for m in mappings])

        all_confidences.extend(tier1_confidences)
        overall_confidence = (sum(all_confidences) / len(all_confidences)) if all_confidences else 0.0
        state["overall_confidence"] = overall_confidence

        logger.info(f"[Node 3] ═══════════════════════════════════════════")
        logger.info(f"[Node 3] Total: {total_auto} auto, {total_flagged} flagged, {total_unmappable} unmappable")
        logger.info(f"[Node 3] Overall confidence updated to {overall_confidence:.2f}")

        state["current_step"] = 3
        state["event_log"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "event": "node_complete",
            "node": 3,
            "detail": f"{total_auto} auto, {total_flagged} flagged, {total_unmappable} unmappable (by table)"
        })

        migration_id = state.get("migration_id")
        if migration_id:
            from .db_writer import update_node_progress, write_step_pause
            await update_node_progress(
                migration_id, "3_semantic_mapping",
                t2_auto_count=total_auto,
            )
            # Build per-field results for UI display
            def _extract_fields(by_table: dict, status: str) -> list:
                out = []
                for tbl, mappings in by_table.items():
                    for m in mappings:
                        if isinstance(m, str):
                            # Legacy plain-string unmappable entry
                            out.append({"table": tbl, "source_field": m, "status": status})
                        else:
                            entry: dict = {
                                "table": tbl,
                                "source_field": m.get("source_field"),
                                "target_field": m.get("target_field"),
                                "confidence": m.get("confidence"),
                                "rationale": m.get("rationale"),
                                "status": status,
                            }
                            # For unmappable: include best-attempt info
                            if m.get("best_target") is not None:
                                entry["best_target"] = m.get("best_target")
                                entry["best_confidence"] = m.get("best_confidence")
                            out.append(entry)
                return out

            # The COMPLETE post-semantic state for the review screen: Tier-1 approved + Tier-2
            # auto are "auto accepted", not just the unresolved Tier-2 subset. Without the
            # Tier-1 rows here the step-pause showed "Auto accepted: 0" despite N approvals.
            from ...udr.mapping_view import build_combined_mapping_view

            _tier1_approved = state.get("tier1_approved_by_table") or state.get("tier1_mappings_by_table") or {}
            combined_mapping_view = build_combined_mapping_view(
                _tier1_approved,
                state.get("tier2_auto_by_table", {}),
                state.get("tier2_flagged_by_table", {}),
                state.get("tier2_unmappable_by_table", {}),
                table_routing=state.get("table_routing"),
                cafm_table_matches=state.get("cafm_table_matches"),
            )
            # Tier-1 approved rows (status "auto") so the snapshot's auto count + Auto tab reflect
            # the FULL picture (Tier-1 approved + Tier-2 auto), not just the Tier-2 auto subset.
            tier1_auto_results = [
                {"table": t, "source_field": m.get("source_field"), "target_field": m.get("target_field"),
                 "confidence": m.get("confidence"), "status": "auto", "tier": m.get("tier") or "T1_approved"}
                for t, maps in _tier1_approved.items() for m in (maps or []) if isinstance(m, dict)
            ]
            semantic_results = (
                tier1_auto_results
                + _extract_fields(state.get("tier2_auto_by_table", {}), "auto")
                + _extract_fields(state.get("tier2_flagged_by_table", {}), "flagged")
                + _extract_fields(state.get("tier2_unmappable_by_table", {}), "unmappable")
            )
            # Existing plenum_cafm tables + columns so the step-pause snapshot can tell a source
            # routed to an EXISTING table (works→assets, WorkOrders_2→work_orders) apart from a
            # genuinely new table. Without these the Node3Semantic snapshot mislabels matched
            # columns as "new table column" — the pre-semantic gate already carries them, the
            # semantic pause must too for the frontend guard to fire. Reuse columns_by_table
            # already fetched above (no second DB call, no local re-import — re-importing the
            # module-level name here would shadow it function-wide and break its earlier use).
            _existing_tables: list = sorted((columns_by_table or {}).keys())
            _canon_cols: dict = {t: sorted(c) for t, c in (columns_by_table or {}).items()}
            if not _existing_tables:  # fall back to the matched targets already in state
                _existing_tables = sorted(
                    {str(v) for v in (state.get("cafm_table_matches") or {}).values() if v}
                )
            await write_step_pause(
                migration_id,
                "step_3_semantic_mapping",
                {
                    "node": 3,
                    "label": "Semantic Mapping (Tier 2)",
                    "t2_auto": total_auto,
                    "flagged": total_flagged,
                    "unmappable": total_unmappable,
                    # COMPLETE-state counts + view (Tier-1 approved + Tier-2) for the review UI.
                    "auto_accepted_count": len(combined_mapping_view["auto_accepted"]),
                    "flagged_count": len(combined_mapping_view["flagged"]),
                    "unmappable_count": len(combined_mapping_view["unmappable"]),
                    "combined_mapping_view": combined_mapping_view,
                    "semantic_results": semantic_results,
                    # Routing + existing-table context so Node3Semantic's snapshot doesn't
                    # mislabel a source routed to an existing table as "new table column".
                    "table_routing": state.get("table_routing") or {},
                    "existing_canonical_tables": _existing_tables,
                    "canonical_columns_by_table": _canon_cols,
                },
            )
            from .schema_db_writer import migration_append_node_log_auto
            await migration_append_node_log_auto(
                migration_id, 4, "Semantic Mapping", _node_started_at, datetime.utcnow(),
                output={"tier2_auto_mapped": total_auto, "tier2_flagged": total_flagged,
                        "unmappable": total_unmappable,
                        "overall_confidence": round(state.get("overall_confidence", 0.0), 3)},
                logs=[f"Ran embedding cosine similarity on {total_unresolved} unresolved fields",
                      f"{total_auto} auto · {total_flagged} flagged · {total_unmappable} unmappable",
                      *node_log_lines,
                      f"EL-M.3: {'PASSED' if state.get('el_m3_passed') else 'FAILED'}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 3] Unhandled exception: {e}")
        state["error_message"] = str(e)
        state["error_node"] = 3
        state["error_timestamp"] = datetime.utcnow()
        state["status"] = "failed"
        return state


def _get_sample_values(
    parsed_tables: dict[str, list[dict]],
    field_name: str,
    max_samples: int = 3,
) -> list[str]:
    """Extract sample values for a field from parsed tables."""
    samples = set()
    for table_records in parsed_tables.values():
        if not table_records:
            continue
        for record in table_records:
            if field_name in record:
                value = record[field_name]
                if value and str(value).strip():
                    samples.add(str(value).strip())
                    if len(samples) >= max_samples:
                        return list(samples)[:max_samples]
    return list(samples)[:max_samples]
