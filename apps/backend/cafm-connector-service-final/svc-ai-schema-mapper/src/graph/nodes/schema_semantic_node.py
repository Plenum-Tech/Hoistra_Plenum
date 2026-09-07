"""Node 3: Schema Semantic Mapper — Embedding-based field mapping (Tier 2).

For fields unresolved after Tier 1:
1. Embed unresolved field name + description
2. Compute cosine similarity vs cached canonical field embeddings
3. Classify by confidence:
   - ≥ 0.85: auto-accept (tier2_auto_mapped)
   - 0.65–0.84: flagged for human review (tier2_flagged)
   - < 0.65: unmappable (tier2_unmappable)
"""

import logging

from ...embeddings import embed_texts_batch, find_top_matches, score_canonical_fields
from ..schema_state import CanonicalFieldMapping, SchemaMappingState, SchemaMappingFieldInfo
from .mapping_cutoff import SEMANTIC_AUTO_MIN, SEMANTIC_REVIEW_MIN  # F7 7.7 semantic bands

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_semantic_node(state: SchemaMappingState) -> SchemaMappingState:
    """
    Node 3: Embed unresolved schema columns and match semantically via cosine similarity.
    """

    from datetime import datetime as _dt
    _node_started_at = _dt.utcnow()
    schema_mapping_id = state.get("schema_mapping_id")
    unmapped_after_t1 = state.get("unmapped_after_t1", [])
    external_tables = state.get("external_tables", {})

    total_unresolved = len(unmapped_after_t1)
    logger.info(f"[Node 3] Starting semantic mapping: {total_unresolved} unresolved fields")

    if not total_unresolved:
        logger.info("[Node 3] No unresolved fields; skipping semantic mapper")
        state["tier2_auto_mapped"] = []
        state["tier2_flagged"] = []
        state["tier2_unmappable"] = []
        state["overall_mapping_confidence"] = 1.0
        state["status"] = "hierarchy"
        return state

    try:
        from ...app import get_openai_client

        client = get_openai_client()

        tier2_auto_mapped: list[CanonicalFieldMapping] = []
        tier2_flagged: list[CanonicalFieldMapping] = []
        tier2_unmappable: list[SchemaMappingFieldInfo] = []
        all_confidences = []

        # ── Batch embedding for unresolved fields ──────────────────────
        logger.info(f"[Node 3] Embedding {total_unresolved} unresolved fields...")

        embedding_texts = {}
        texts_to_embed = []

        for field_info in unmapped_after_t1:
            source_field = field_info.get("field_name")
            description = field_info.get("description", "Unknown field")
            data_type = field_info.get("data_type", "")

            # Construct embedding text with context
            embedding_text = f"{source_field} | {description} | {data_type}"
            embedding_texts[embedding_text] = field_info
            texts_to_embed.append(embedding_text)

        # Call embedding API for all fields
        try:
            embeddings_dict = await embed_texts_batch(client, texts_to_embed)
            logger.info(f"[Node 3] Batch embedding: {len(embeddings_dict)}/{len(texts_to_embed)} succeeded")
        except Exception as e:
            logger.warning(f"[Node 3] Embedding batch failed: {e}")
            # Mark all as unmappable if embedding fails
            tier2_unmappable = unmapped_after_t1
            state["tier2_auto_mapped"] = []
            state["tier2_flagged"] = []
            state["tier2_unmappable"] = tier2_unmappable
            state["overall_mapping_confidence"] = 0.0
            state["status"] = "hierarchy"
            return state

        # ── Build a field→table lookup once (used in the loop below) ──
        _ext_tables_lookup: dict[str, str] = {}
        for _tn, _ti in external_tables.items():
            for _col in _ti.get("columns", []):
                _fn = _col.get("field_name")
                if _fn:
                    _ext_tables_lookup[_fn] = _tn

        # ── Constrain candidates to each source table's RESOLVED target table ──
        # A column must be matched against the columns of the table its source
        # table maps to (e.g. WorkOrders → work_orders), not the global best across
        # every canonical table — otherwise unmapped columns surface spurious
        # cross-table "best attempts" (e.g. → maintenance_type) and get marked
        # unmappable, which then wrongly prompts new-column creation. Mirrors the
        # migration semantic mapper. target_table_by_source isn't persisted to
        # state, so resolve it the same way schema_pre_semantic did (overrides win).
        canonical_tables = state.get("canonical_tables", {})
        table_overrides = state.get("table_overrides") or {}
        try:
            from ...connectors.fiix_plenum_mappings import (
                plenum_table_for_fiix_object as _fiix_table_for,
            )
            from .schema_pre_semantic_node import (
                schema_candidate_table_names as _candidate_tables,
            )
        except Exception:  # pragma: no cover - keep semantic mapping working if import fails
            _fiix_table_for = None
            _candidate_tables = None

        def _resolve_target_table(src_table: str):
            tgt = table_overrides.get(src_table) or (
                _fiix_table_for(src_table) if _fiix_table_for else None
            )
            if tgt and tgt in canonical_tables:
                return tgt
            if _candidate_tables:
                try:
                    return next(
                        (c for c in _candidate_tables(src_table) if c in canonical_tables),
                        None,
                    )
                except Exception:
                    return None
            return None

        _allowed_cache: dict[str, set] = {}

        def _allowed_fields(src_table: str):
            if src_table in _allowed_cache:
                return _allowed_cache[src_table]
            tgt = _resolve_target_table(src_table)
            info = canonical_tables.get(tgt) if tgt else None
            cols = info.get("columns", []) if isinstance(info, dict) else []
            allowed = {
                c.get("field_name")
                for c in cols
                if isinstance(c, dict) and c.get("field_name")
            }
            result = allowed or None
            _allowed_cache[src_table] = result
            return result

        # ── Process each field's embedding ─────────────────────────────
        for embedding_text, field_info in embedding_texts.items():
            source_field = field_info.get("field_name")
            # Use source_table already tagged by Node 2 (fallback: lookup in external_tables)
            source_table = (
                field_info.get("source_table")
                or _ext_tables_lookup.get(source_field, "unknown")
            )

            source_embedding = embeddings_dict.get(embedding_text)
            if source_embedding is None:
                logger.warning(f"[Node 3] Failed to embed {source_field}")
                tier2_unmappable.append(field_info)
                continue

            # Find top matches — constrained to the source table's resolved target
            # table's columns when we can (graceful fallback to global otherwise).
            allowed = _allowed_fields(source_table)
            if allowed:
                _scored = score_canonical_fields(source_embedding, list(allowed))
                top_matches = (
                    sorted(_scored.items(), key=lambda kv: kv[1], reverse=True)[:3]
                    if _scored
                    else find_top_matches(source_embedding, top_k=3)
                )
            else:
                top_matches = find_top_matches(source_embedding, top_k=3)
            if not top_matches:
                logger.warning(f"[Node 3] No matches for {source_field}")
                tier2_unmappable.append(field_info)
                continue

            best_target, best_confidence = top_matches[0]

            logger.info(f"[Node 3] {source_field} → {best_target} ({best_confidence:.3f})")

            # Classify by confidence (F7 7.7 AC2/AC3): >=70% Suggested, <70% Requires Review.
            if best_confidence >= SEMANTIC_AUTO_MIN:
                # Auto-accept
                mapping = CanonicalFieldMapping(
                    source_field=source_field,
                    source_table=source_table,
                    target_field=best_target,
                    confidence=best_confidence,
                    tier="T2_semantic",
                    rationale=f"Semantic embedding match ({best_confidence:.3f})",
                    auto_mappable=True,
                    human_review_needed=False,
                )
                tier2_auto_mapped.append(mapping)
                all_confidences.append(best_confidence)
                logger.info(f"[Node 3] ✓ AUTO-ACCEPT")

            elif best_confidence >= SEMANTIC_REVIEW_MIN:
                # >=70% and <85% — 'Suggested' (flag for one-click confirm)
                suggestions = [
                    {"target": match[0], "confidence": match[1]}
                    for match in top_matches
                ]
                mapping = CanonicalFieldMapping(
                    source_field=source_field,
                    source_table=source_table,
                    target_field=best_target,
                    confidence=best_confidence,
                    tier="T2_semantic",
                    rationale=f"Semantic match flagged ({best_confidence:.3f})",
                    auto_mappable=False,
                    human_review_needed=True,
                )
                mapping["suggestions"] = suggestions
                tier2_flagged.append(mapping)
                all_confidences.append(best_confidence)
                logger.info(f"[Node 3] ⚠ FLAGGED FOR REVIEW")

            else:
                # Unmappable — store best attempt for UI display
                logger.info(f"[Node 3] ✗ UNMAPPABLE ({best_confidence:.3f})")
                tagged = dict(field_info)
                tagged["source_table"] = source_table
                tagged["best_target"] = best_target
                tagged["best_confidence"] = round(best_confidence, 3)
                tier2_unmappable.append(tagged)

        # ── Calculate overall confidence ───────────────────────────────
        if all_confidences:
            overall_confidence = sum(all_confidences) / len(all_confidences)
        else:
            overall_confidence = 0.0

        # ── Summary ────────────────────────────────────────────────────
        total_auto = len(tier2_auto_mapped)
        total_flagged = len(tier2_flagged)
        total_unmappable = len(tier2_unmappable)

        logger.info(
            f"[Node 3] ✓ Semantic mapping complete: "
            f"{total_auto} auto, {total_flagged} flagged, {total_unmappable} unmappable"
        )

        # ── Update state ───────────────────────────────────────────────
        state["tier2_auto_mapped"] = tier2_auto_mapped
        state["tier2_flagged"] = tier2_flagged
        state["tier2_unmappable"] = tier2_unmappable
        state["overall_mapping_confidence"] = overall_confidence
        state["status"] = "hierarchy"
        state["notes"] = state.get("notes", []) + [
            f"Tier 2 semantic: {total_auto} auto-mapped, {total_flagged} flagged, {total_unmappable} unmappable. "
            f"Overall confidence: {overall_confidence:.2f}"
        ]

        schema_mapping_id = state.get("schema_mapping_id")
        if schema_mapping_id:
            from .schema_db_writer import schema_write_step_pause_auto
            # Build semantic_results array (same format as StepPause.tsx expects)
            def _find_table(field_name: str) -> str:
                for tn, ti in external_tables.items():
                    if any(c.get("field_name") == field_name for c in ti.get("columns", [])):
                        return tn
                return "unknown"

            semantic_results = []
            for m in tier2_auto_mapped:
                fn = m.get("source_field")
                semantic_results.append({
                    "source_field": fn,
                    "table": m.get("source_table") or (_find_table(fn) if fn else "unknown"),
                    "target_field": m.get("target_field"),
                    "confidence": round(m.get("confidence", 0.0), 3),
                    "status": "auto",
                })
            for m in tier2_flagged:
                fn = m.get("source_field")
                suggestions = m.get("suggestions", [])
                semantic_results.append({
                    "source_field": fn,
                    "table": m.get("source_table") or (_find_table(fn) if fn else "unknown"),
                    "target_field": m.get("target_field"),
                    "confidence": round(m.get("confidence", 0.0), 3),
                    "status": "flagged",
                    "suggestions": suggestions,
                })
            for f in tier2_unmappable:
                fn = f.get("field_name")
                best_conf = f.get("best_confidence")
                semantic_results.append({
                    "source_field": fn,
                    "table": f.get("source_table") or (_find_table(fn) if fn else "unknown"),
                    "target_field": None,
                    "best_target": f.get("best_target"),
                    "best_confidence": round(best_conf, 3) if best_conf else None,
                    "confidence": None,
                    "status": "unmappable",
                })
            payload = {
                "node": 3,
                "title": "Semantic Mapping Complete",
                "t2_auto": total_auto,
                "flagged": total_flagged,
                "unmappable": total_unmappable,
                "overall_confidence": round(overall_confidence, 3),
                "semantic_results": semantic_results,
            }
            await schema_write_step_pause_auto(
                schema_mapping_id, 3, "step_3_semantic", payload
            )
            from datetime import datetime as _dt
            from .schema_db_writer import schema_append_node_log_auto
            await schema_append_node_log_auto(
                schema_mapping_id, 4, "Semantic Mapping", _node_started_at, _dt.utcnow(),
                output={"tier2_auto_mapped": total_auto, "tier2_flagged": total_flagged,
                        "unmappable": total_unmappable, "overall_confidence": round(overall_confidence, 3)},
                logs=[f"Ran embedding cosine similarity on unresolved fields",
                      f"{total_auto} fields auto-accepted (confidence ≥ 0.85)",
                      f"{total_flagged} fields flagged for human review (0.65–0.85)",
                      f"{total_unmappable} fields unmappable (confidence < 0.65)",
                      f"Overall confidence: {overall_confidence:.2f}"],
            )

        return state

    except Exception as e:
        logger.exception(f"[Node 3] ✗ Error: {e}")
        state["status"] = "error"
        state["error_message"] = f"Semantic mapping failed: {str(e)}"
        return state
