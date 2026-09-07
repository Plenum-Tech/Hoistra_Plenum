"""Node 10 — UDR run (Feature 7) + Activity-Log emit (Feature 4 / AL.6).

Runs AFTER write_node, on the COMMITTED data, as a post-write reporting side-effect:
  1. runs the value-centric UDR pipeline over ``full_tables`` (the complete rows),
  2. persists the relationship graph (UdrRunGraph + UdrEntityRelationship), COMMITTING
     it before any Activity-Log claim of success,
  3. emits one Activity-Log entry (build_udr_activity_entry → record_activity), flipping
     the notification badge.

This node is deliberately NON-FATAL: any failure is caught and logged — a UDR/Activity
problem must never fail an otherwise-successful migration. It is not in ``interrupt_after``
(no human gate). It no-ops unless the write completed (``state["status"] == "complete"``).
"""

from __future__ import annotations

import logging
from uuid import UUID

logger = logging.getLogger(__name__)


def _coerce_uuid(value):
    if value is None:
        return None
    if isinstance(value, UUID):
        return value
    try:
        return UUID(str(value))
    except Exception:  # never let a malformed org id escape the non-fatal node
        return None


def _build_mapping_decisions(state) -> list[dict]:
    """Join the confirmed field mappings (T1 + T2 auto + human) with the table routing
    into the AL.6 ``source → confidence → destination`` traceability feed."""
    dest_by_src = state.get("cafm_table_matches") or {}
    decisions: list[dict] = []
    for bucket in ("tier1_mappings_by_table", "tier2_auto_by_table", "tier2_human_decisions_by_table"):
        for src_tbl, fms in (state.get(bucket) or {}).items():
            for fm in fms or []:
                if not isinstance(fm, dict):
                    continue
                decisions.append(
                    {
                        "source_table": src_tbl,
                        "source_column": fm.get("source_field"),
                        "confidence": fm.get("confidence"),
                        "dest_table": dest_by_src.get(src_tbl),
                        "dest_column": fm.get("target_field"),
                    }
                )
    return decisions


async def udr_node(state) -> dict:
    """Post-write UDR run + Activity-Log emit. Returns only the UDR state channels."""
    # Only run on a successfully written migration (GATE 3 approved, handoff done).
    if state.get("status") != "complete" or not state.get("full_tables"):
        logger.info("[Node 10/UDR] skipped (write not complete or no data)")
        return {"udr_status": "skipped"}

    try:
        from ...udr import emit_udr_activity, persist_udr_run, run_udr_pipeline

        migration_id = state.get("migration_id")
        run_id = str(migration_id) if migration_id else "udr-run"
        org = _coerce_uuid(state.get("organization_id"))
        full_tables = state.get("full_tables") or {}
        # FM-ontology alias resolver for the B10.1 RAG/alias table-mapping classification —
        # imported here (matchers layer) so the pure udr.* package never depends on it.
        _alias_resolver = None
        _field_resolver = None
        try:
            from ...matchers.fm_ontology import fm_table_lookup as _alias_resolver
            from ...matchers.fm_ontology import fm_field_lookup as _field_resolver
        except Exception:  # pragma: no cover — RAG/FM tiers gracefully degrade
            _alias_resolver = _alias_resolver or None
        result = run_udr_pipeline(
            full_tables,
            run_id=run_id,
            dest_table_by_source=state.get("cafm_table_matches") or {},
            # count of ingested source datasets (sheets/tables) — the available proxy for
            # "documents ingested" in the structured migration flow (no separate file count).
            documents_ingested=len(full_tables),
            mapping_decisions=_build_mapping_decisions(state) or None,
            confidence_by_source=state.get("cafm_table_match_confidence") or {},
            # User-approved PK from the pre-semantic confirmation gate → overlays the B7.1/B8.1
            # report so the final analysis reflects the signed-off PK, not the auto-detected one.
            pk_override_by_table=state.get("pk_confirmed_by_table") or {},
            # B20.1 classification-gate decisions (rejections + FK ↔ Shared re-classifications) —
            # re-applied to the rebuilt column-intelligence report so the post-write analysis
            # reflects the human-approved classification, not the raw auto-detection.
            classification_decisions={
                "rejected": state.get("classification_rejected") or [],
                "overrides": state.get("classification_overrides") or {},
            },
            alias_resolver=_alias_resolver,
            field_resolver=_field_resolver,
        )
    except Exception as e:  # pragma: no cover — never fail the migration on a UDR error
        logger.exception(f"[Node 10/UDR] pipeline failed: {e}")
        return {"udr_status": "failed"}

    # Persist the graph + edges and COMMIT before claiming success in the Activity Log.
    try:
        await persist_udr_run(result, organization_id=org)
    except Exception as e:
        logger.exception(f"[Node 10/UDR] graph persistence failed: {e}")
        # Record the run as failed so the Activity Log reflects reality, then stop.
        try:
            await emit_udr_activity(
                result,
                organization_id=org,
                session_id=run_id,
                trigger="external_input",
                status_override="failed",
            )
        except Exception:
            logger.exception("[Node 10/UDR] failed-activity emit also failed")
        return {
            "udr_run_id": run_id,
            "udr_status": "failed",
            "udr_table_count": result.table_count,
            "udr_column_count": result.column_count,
            "udr_relationship_count": result.relationship_count,
            "udr_blocked": result.blocked,
        }

    # Feature 7 — grow the GLOBAL canonical registry with any tables/columns THIS run created
    # that aren't in the base plenum_cafm schema, so future runs and future client deployments
    # inherit them as canonical targets (incremental, not rebuilt per client). Non-fatal: a
    # registry hiccup must never fail a migration. Default lands in a pending (admin-approval)
    # area; UDR_REGISTRY_AUTO_APPROVE=1 promotes straight into canonical_fields.
    try:
        import os

        from ...config import get_settings
        from ...db import get_plenum_cafm_columns_by_table
        from ...services.registry_promotion import (
            collect_canonical_additions,
            promote_canonical_additions,
        )

        _base_cols = await get_plenum_cafm_columns_by_table()
        _additions = collect_canonical_additions(result.graph, _base_cols)
        if _additions:
            # 7.11 AC5 — additions are appended to the global canonical structure at UDR
            # completion (within 24h) by DEFAULT. Set UDR_REGISTRY_REQUIRE_APPROVAL=1 to hold
            # them for admin review instead (a 24h sweep then auto-approves the backlog).
            _require_approval = os.getenv("UDR_REGISTRY_REQUIRE_APPROVAL", "false").lower() in (
                "1", "true", "yes",
            )
            _auto = not _require_approval
            _rep = await promote_canonical_additions(
                get_settings().db_url,
                _additions,
                auto_approve=_auto,
                source_run_id=run_id,
                source_org=org,
            )
            logger.info(f"[Node 10/UDR] canonical registry growth: {_rep}")
    except Exception as e:  # pragma: no cover - registry growth is best-effort
        logger.warning(f"[Node 10/UDR] canonical registry promotion skipped: {e}")

    # Feature 7 Job 1 — optional LLM semantic relationship discovery over the run's free text
    # (recurring faults / same-defect / causal chains the schema can't encode), written back as
    # provenance='llm_inferred' edges. OFF by default (LLM cost/latency); UDR_SEMANTIC_DISCOVERY=1.
    inferred_edges: list = []
    try:
        import os

        if os.getenv("UDR_SEMANTIC_DISCOVERY", "false").lower() in ("1", "true", "yes"):
            from ...udr.llm_discovery import run_semantic_discovery

            _disc = await run_semantic_discovery(
                full_tables, result.graph, run_id=run_id, organization_id=org
            )
            inferred_edges = (_disc.get("edges") if isinstance(_disc, dict) else None) or []
            logger.info(f"[Node 10/UDR] semantic relationship discovery: {_disc}")
    except Exception as e:  # pragma: no cover - discovery is best-effort
        logger.warning(f"[Node 10/UDR] semantic discovery skipped: {e}")

    # Pre-processing report for Section 2: duplicate rows removed (Node 5) + value-merged
    # columns / near-duplicate pairs flagged for review (deterministic mapper).
    _dedup = state.get("dedup_drop_count_by_table") or {}
    _merged = state.get("merged_columns_by_table") or {}
    _neardup = state.get("near_duplicate_columns_by_table") or {}
    preprocessing_report = {
        "duplicates_removed": sum(int(v or 0) for v in _dedup.values()) if isinstance(_dedup, dict) else 0,
        "auto_merged_count": sum(
            len(cols) for tbl in (_merged.values() if isinstance(_merged, dict) else [])
            for cols in (tbl.values() if isinstance(tbl, dict) else [])
        ),
        "flagged_count": sum(
            len(v) for v in (_neardup.values() if isinstance(_neardup, dict) else []) if isinstance(v, list)
        ),
    }

    # Deterministic + semantic mapper node durations (captured by @timed_node in those
    # earlier graph nodes) — attach to the corresponding Section-2 stages.
    extra_stage_meta = {
        "deterministic": {
            "duration_ms": state.get("deterministic_duration_ms"),
            "at": state.get("deterministic_at"),
        },
        "semantic": {
            "duration_ms": state.get("semantic_duration_ms"),
            "at": state.get("semantic_at"),
        },
    }
    # Intent confidence for the decomposition step: a migration-triggered run IS a UDR task,
    # so use the real aggregate table-routing confidence (how confidently the source was
    # recognised as the canonical UDR schema); fall back to certain (1.0) when unavailable.
    _match_conf = state.get("cafm_table_match_confidence") or {}
    _conf_vals = [v for v in _match_conf.values() if isinstance(v, (int, float))]
    intent_confidence = round(sum(_conf_vals) / len(_conf_vals), 4) if _conf_vals else 1.0

    activity_id = None
    try:
        activity_id = await emit_udr_activity(
            result,
            organization_id=org,
            session_id=run_id,
            script_ref=state.get("output_sql_url"),
            trigger="external_input",
            preprocessing_report=preprocessing_report,
            extra_stage_meta=extra_stage_meta,
            intent_confidence=intent_confidence,
        )
    except Exception as e:
        logger.exception(f"[Node 10/UDR] activity emit failed (graph already persisted): {e}")
        # The completed full trail (build_udr_activity_entry) never landed, and the progressive
        # sync no-ops at status=complete — so without this the Activity card would stay frozen at
        # the last node-9 sync with the post-write UDR stages (vectors / Tests / registry) hidden,
        # i.e. "logs missing after the tail stages". The graph + post-write ran (they precede this
        # emit), so force one terminal finalise: build_run_activity_entry marks every executed +
        # post-write stage completed via terminal_ok. Best-effort — never fail the migration.
        try:
            if migration_id:
                from .schema_db_writer import _sync_run_activity

                await _sync_run_activity(
                    str(migration_id), caller="node10-emit-failed-finalize", force=True
                )
        except Exception:
            logger.warning(
                "[Node 10/UDR] terminal activity finalise fallback (emit-failed) also failed",
                exc_info=True,
            )

    # F7 7.9/7.10/7.12/7.13 — surface the Test 1/Test 2 reports + the relationship
    # report (schema vs LLM-inferred edges for the 7.12 review queue, plus any
    # recorded sanctity flags for the 7.13 'Relationship Quality' section) onto the
    # migration node log so the status response can expose them to the FE results
    # panel. Non-fatal: a logging hiccup must never fail the run.
    _udr_tests = state.get("udr_test_results")
    _rel_report = None
    try:
        _rels = (result.graph or {}).get("relationships", []) if isinstance(result.graph, dict) else []
        _schema_rels = [r for r in _rels if (r.get("provenance") or "schema") != "llm_inferred"]
        _inferred = [r for r in _rels if r.get("provenance") == "llm_inferred"] or list(inferred_edges)
        _flags = state.get("sanctity_flags") or []
        _rel_report = {
            "schema_relationship_count": len(_schema_rels),
            "inferred_count": len(_inferred),
            "inferred": _inferred[:50],
            "sanctity_flag_count": len(_flags),
            "sanctity_flags": list(_flags)[:50],
        }
    except Exception:  # pragma: no cover - best-effort
        _rel_report = None
    _table_resolution = getattr(result, "table_resolution", None) or None
    _column_intelligence = getattr(result, "column_intelligence", None) or None
    if _udr_tests or _rel_report or _table_resolution or _column_intelligence:
        try:
            from datetime import datetime as _dt
            from .schema_db_writer import migration_append_node_log_auto

            _out: dict = {}
            if _udr_tests:
                _out["udr_test_results"] = _udr_tests
            if _rel_report:
                _out["udr_relationship_report"] = _rel_report
            if _table_resolution:
                # B7.1→B12.1 — the Migration-Analysis table-resolution report (cards · PK ·
                # deterministic / RAG / semantic mapping · final decisions).
                _out["udr_table_resolution"] = _table_resolution
            if _column_intelligence:
                # B13.1→B21.1 — the Column-Intelligence Pipeline (prefixing · metadata · FK
                # candidates · format + value-pattern grouping · unified names · classification ·
                # dest column mapping) so the post-write view matches the pre-semantic gate.
                _out["udr_column_intelligence"] = _column_intelligence
            _t = _dt.utcnow()
            await migration_append_node_log_auto(
                run_id, 11, "UDR Quality & Relationships", _t, _t,
                output=_out,
                logs=["Test 1/2 reports + schema vs inferred relationships + sanctity flags"],
            )
        except Exception as e:  # pragma: no cover - best-effort surfacing
            logger.warning(f"[Node 10/UDR] quality/relationship node log skipped: {e}")

    logger.info(
        f"[Node 10/UDR] complete: {result.table_count} tables, {result.column_count} columns, "
        f"{result.relationship_count} edges, status={result.status}"
    )
    return {
        "udr_run_id": run_id,
        "udr_table_count": result.table_count,
        "udr_column_count": result.column_count,
        "udr_relationship_count": result.relationship_count,
        "udr_blocked": result.blocked,
        "udr_status": result.status,
        "udr_activity_id": str(activity_id) if activity_id else None,
    }
