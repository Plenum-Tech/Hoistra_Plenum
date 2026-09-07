"""Shared DB write utilities for all schema mapping graph nodes.

Mirrors db_writer.py but targets SchemaMappingJob instead of MigrationJob.

Every node calls these helpers so the frontend can track live progress by
polling GET /api/schema-mapping/{id}/status.

Pattern per regular node:
    db_session = state.get("db_session")
    schema_mapping_id = state.get("schema_mapping_id")
    if db_session and schema_mapping_id:
        await schema_update_node_progress(db_session, schema_mapping_id, 2, "deterministic")

Pattern per gate node (before interrupt, then after resume):
    if db_session and schema_mapping_id:
        await schema_write_gate_payload(db_session, schema_mapping_id, "field_mapping", payload)
    decisions = interrupt(payload)
    if db_session and schema_mapping_id:
        await schema_clear_gate_payload(db_session, schema_mapping_id)
"""

import logging
from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from ...models.migration import SchemaMappingJob

from cafm_shared.logging import get_logger
logger = get_logger(__name__)


async def schema_update_node_progress(
    db_session: AsyncSession,
    schema_mapping_id: str,
    current_node: int,
    status: str = "running",
    **extra_fields: Any,
) -> None:
    """
    Write node completion to schema_mapping_jobs.

    Args:
        db_session:         Active AsyncSession (passed through state).
        schema_mapping_id:  SchemaMappingJob UUID string.
        current_node:       Node number (0–8).
        status:             Job status string (default "running").
        **extra_fields:     Any additional SchemaMappingJob columns to update
                            (e.g. tier1_mapped=42, progress_pct=50.0).
    """
    try:
        progress_pct = extra_fields.pop("progress_pct", ((current_node + 1) / 9.0) * 100.0)
        values: dict[str, Any] = {
            "current_node": current_node,
            "status": status,
            "progress_pct": progress_pct,
            **extra_fields,
        }
        await db_session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(**values)
        )
        await db_session.commit()
        logger.debug(
            f"[schema_db_writer] node={current_node} status={status} "
            f"schema_mapping={schema_mapping_id}"
        )
    except Exception as e:
        logger.warning(f"[schema_db_writer] schema_update_node_progress failed (non-fatal): {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass


async def schema_write_gate_payload(
    db_session: AsyncSession,
    schema_mapping_id: str,
    gate_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Write HITL gate payload to DB and flip status to 'awaiting_review'.

    Called by gate nodes BEFORE interrupt() so the frontend can read the payload
    via GET /api/schema-mapping/{id}/status → pending_gate_payload and render
    the review UI.

    gate_type values:
        "field_mapping"  — Node 4 (schema human review gate)
        "hierarchy"      — Node 6 (verify hierarchy gate)
    """
    try:
        await db_session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(
                status="awaiting_review",
                pending_gate_type=gate_type,
                pending_gate_payload=payload,
            )
        )
        await db_session.commit()
        logger.info(
            f"[schema_db_writer] Gate payload written: gate={gate_type} "
            f"schema_mapping={schema_mapping_id}"
        )
    except Exception as e:
        logger.warning(f"[schema_db_writer] schema_write_gate_payload failed (non-fatal): {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass


async def schema_clear_gate_payload(
    db_session: AsyncSession,
    schema_mapping_id: str,
) -> None:
    """
    Clear gate payload and flip status back to 'running' after graph resumes.

    Called by gate nodes immediately after interrupt() returns with decisions.
    """
    try:
        await db_session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(
                status="running",
                pending_gate_type=None,
                pending_gate_payload=None,
            )
        )
        await db_session.commit()
        logger.debug(
            f"[schema_db_writer] Gate payload cleared schema_mapping={schema_mapping_id}"
        )
    except Exception as e:
        logger.warning(f"[schema_db_writer] schema_clear_gate_payload failed (non-fatal): {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass


async def schema_write_step_pause(
    db_session: AsyncSession,
    schema_mapping_id: str,
    node_num: int,
    step_key: str,
    payload: dict[str, Any],
) -> None:
    """
    Write step-pause state to schema_mapping_jobs.

    Called by regular (non-gate) nodes at the end of their execution, before
    returning from the node function. interrupt_after will then pause the graph
    after the node returns. The frontend polls status=="step_paused" and renders
    a node summary before the user clicks "Next Node →".

    Args:
        db_session:          Active AsyncSession (passed through state).
        schema_mapping_id:   SchemaMappingJob UUID string.
        node_num:            Node number (0–8).
        step_key:            Identifies which step paused (e.g. "step_0_canonical").
        payload:             Rich node-output data displayed in the UI.
    """
    try:
        progress_pct = ((node_num + 1) / 9.0) * 100.0
        await db_session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(
                current_node=node_num,
                status="step_paused",
                progress_pct=progress_pct,
                pending_gate_type=step_key,
                pending_gate_payload=payload,
            )
        )
        await db_session.commit()
        logger.debug(
            f"[schema_db_writer] step_pause node={node_num} step={step_key} "
            f"schema_mapping={schema_mapping_id}"
        )
    except Exception as e:
        logger.warning(f"[schema_db_writer] schema_write_step_pause failed (non-fatal): {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass


async def schema_write_gate_payload_auto(
    schema_mapping_id: str,
    gate_type: str,
    payload: dict[str, Any],
) -> None:
    """
    Write HITL gate payload using a self-managed DB session.

    Use this in gate nodes instead of schema_write_gate_payload() so that
    no AsyncSession is placed in graph state (which breaks MemorySaver checkpointing).
    """
    from ...db import get_async_session_factory
    sf = get_async_session_factory()
    async with sf() as db_session:
        await schema_write_gate_payload(db_session, schema_mapping_id, gate_type, payload)


async def schema_clear_gate_payload_auto(
    schema_mapping_id: str,
) -> None:
    """
    Clear gate payload using a self-managed DB session.

    Called by gate nodes immediately after interrupt() returns with decisions.
    """
    from ...db import get_async_session_factory
    sf = get_async_session_factory()
    async with sf() as db_session:
        await schema_clear_gate_payload(db_session, schema_mapping_id)


async def schema_write_step_pause_auto(
    schema_mapping_id: str,
    node_num: int,
    step_key: str,
    payload: dict[str, Any],
) -> None:
    """
    Write step-pause state using a self-managed DB session.

    Use this in LangGraph nodes instead of schema_write_step_pause() so that
    the AsyncSession is never placed in the graph state (which would fail msgpack
    serialization when MemorySaver checkpoints the state).
    """
    from ...db import get_async_session_factory
    sf = get_async_session_factory()
    async with sf() as db_session:
        await schema_write_step_pause(db_session, schema_mapping_id, node_num, step_key, payload)


async def schema_append_node_log_auto(
    schema_mapping_id: str,
    node_id: int,
    node_name: str,
    started_at: "datetime",
    completed_at: "datetime",
    output: dict,
    logs: list,
) -> None:
    """
    Append a node completion entry to SchemaMappingJob.node_logs using a self-managed session.

    Use this in LangGraph nodes instead of passing an AsyncSession through graph state
    (which would break MemorySaver checkpointing).
    """
    from datetime import datetime as _dt
    from uuid import UUID as _UUID
    from ...db import get_async_session_factory
    from ...services.job_progress import append_node_log

    sf = get_async_session_factory()
    async with sf() as db_session:
        await append_node_log(
            db_session,
            _UUID(schema_mapping_id),
            node_id,
            node_name,
            started_at,
            completed_at,
            output,
            logs,
        )


async def migration_append_node_log_auto(
    migration_id: str,
    node_id: int,
    node_name: str,
    started_at: "datetime",
    completed_at: "datetime",
    output: dict,
    logs: list,
) -> None:
    """
    Append a node completion entry to MigrationJob.node_logs using a self-managed session.

    Use this in LangGraph ingestor nodes instead of passing an AsyncSession through state.
    """
    from uuid import UUID as _UUID
    from ...db import get_async_session_factory
    from ...services.job_progress import append_migration_node_log

    sf = get_async_session_factory()
    async with sf() as db_session:
        await append_migration_node_log(
            db_session,
            _UUID(migration_id),
            node_id,
            node_name,
            started_at,
            completed_at,
            output,
            logs,
        )

    # Feature 4 — keep the durable per-run Activity entry in step with the pipeline: after
    # each LIVE migration node (1–9) completes, rebuild the progressive run entry. The
    # post-write Node-10 UDR run + its node-11 "UDR Quality" log are owned by
    # emit_udr_activity (which finalises the SAME row to completed); syncing on those would
    # rebuild it as "running" and clobber the finalised entry. Best-effort + non-fatal.
    if isinstance(node_id, int) and node_id <= 9:
        try:
            await _sync_run_activity(migration_id, caller=f"node{node_id}-complete")
        except Exception:  # non-fatal audit sync — but log loudly so a silent failure is visible
            logger.warning(
                "[schema_db_writer] progressive run-activity sync FAILED node=%s migration=%s",
                node_id, migration_id, exc_info=True,
            )


async def _sync_run_activity(
    migration_id: str, *, caller: str = "?", force: bool = False
) -> "str | None":
    """Rebuild + upsert the progressive per-run Activity entry from the migration's current
    state. No-op once the run is terminal — the Node-10 emit owns the finalised entry.

    Returns the ui_status that was persisted (or None if it no-op'd) so callers can log the
    exact running↔pending transition chain. ``caller`` tags the log line with the call site.

    ``force=True`` bypasses the ``complete`` no-op so a terminal card can be FINALISED here
    when the Node-10 emit path did not (a UDR emit that raised after the graph/post-write
    stages already ran) — otherwise the card would stay frozen at the last node-9 sync with
    the post-write UDR stages hidden. Only pass ``force`` from a Node-10 fallback where those
    stages genuinely executed, so ``terminal_ok`` marking them completed stays accurate.
    """
    from uuid import UUID as _UUID
    from sqlalchemy import select

    from ...db import get_async_session_factory
    from ...models.migration import MigrationJob
    from ...udr.activity_persist import get_run_entry_state, upsert_run_activity
    from ...udr.run_activity import (
        build_run_activity_entry,
        mapping_decisions_from_gate,
        stage_extras_from_column_intelligence,
        summary_metrics_from_column_intelligence,
    )

    sf = get_async_session_factory()
    async with sf() as s:
        res = await s.execute(select(MigrationJob).where(MigrationJob.id == _UUID(migration_id)))
        job = res.scalar_one_or_none()
        if job is None:
            logger.warning("[run-activity] sync(%s) migration=%s: job not found", caller, migration_id)
            return None
        # READ the live job state that drives the activity status. Logging job.status AND
        # pending_gate_type here is the single most useful signal for the running↔pending
        # question: _ui_status() returns pending_human_input if EITHER status is a gate state
        # OR pending_gate_type is still set, so a non-None gate type here = the row stays pending.
        logger.info(
            "[run-activity] sync(%s) migration=%s read job.status=%s pending_gate_type=%s",
            caller, migration_id, job.status, job.pending_gate_type,
        )
        # Only the SUCCESSFUL-complete row is owned by the Node-10 emit (it finalises the SAME row
        # with the full UDR trail); syncing here would clobber it with a "running"-style rebuild.
        # failed / ddl_failed / cancelled / error are NOT emitted by Node-10 on every path — the
        # worker error handler and the cancel endpoint never reach it — so finalise the card HERE
        # to the terminal ui_status instead of leaving it stuck at the last running/pending sync.
        if not force and str(job.status or "").lower() == "complete":
            logger.info(
                "[run-activity] sync(%s) migration=%s SKIP (complete — Node-10 emit owns the finalised row)",
                caller, migration_id,
            )
            return None

        node_logs = job.node_logs or []
        done_ids: set[int] = set()
        node_meta: dict[int, dict] = {}
        tables = 0
        for nl in node_logs:
            if not isinstance(nl, dict):
                continue
            nid = nl.get("node_id")
            if isinstance(nid, int) and str(nl.get("status") or "").lower() in ("complete", "completed", "done"):
                done_ids.add(nid)
                node_meta[nid] = {
                    "at": nl.get("completed_at") or nl.get("started_at"),
                    "duration_ms": nl.get("duration_ms"),
                }
            if nid == 1 and isinstance(nl.get("output"), dict):
                out = nl["output"]
                for k in ("parsed_tables", "table_names", "cafm_table_matches", "table_routing"):
                    v = out.get(k)
                    if isinstance(v, dict):
                        tables = max(tables, len(v))
                    elif isinstance(v, list):
                        tables = max(tables, len([x for x in v if x]))

        # Live richness (so the in-flight card reads like the completed one): the currently
        # running stage (from current_step, e.g. "2_deterministic_mapping" → node 2) renders
        # with status "running", and the deterministic mapper's node output supplies its
        # confidence + source→destination routing table as per-stage extras.
        import re as _re

        running_ids: set[int] = set()
        _m = _re.search(r"\d+", str(job.current_step or ""))
        if _m:
            _rid = int(_m.group())
            if 1 <= _rid <= 9 and _rid not in done_ids:
                running_ids.add(_rid)
        # Run-start fallback: the moment a run begins (status running, no node completed yet,
        # current_step not set), Node 1 (File Ingestion) IS the in-flight stage. Mark it running
        # so the Activity Log shows "File Ingestion · running" the instant the Migration Panel
        # does — instead of only the bare "Run started" line — matching the panel's live state.
        if not done_ids and not running_ids and not job.pending_gate_type \
                and str(job.status or "").lower() == "running":
            running_ids.add(1)

        stage_extras: dict[str, dict] = {}
        _det = next((nl for nl in node_logs if isinstance(nl, dict) and nl.get("node_id") == 2), None)
        if _det and isinstance(_det.get("output"), dict):
            _out = _det["output"]
            _ex: dict = {}
            for _k in ("overall_confidence", "confidence", "mapping_confidence", "avg_confidence"):
                _v = _out.get(_k)
                if isinstance(_v, (int, float)):
                    if 0 <= _v <= 1:
                        _ex["confidence"] = float(_v)
                        break
                    if 1 < _v <= 100:
                        _ex["confidence"] = _v / 100.0
                        break
            _rows: list[list[str]] = []
            for _k in ("cafm_table_matches", "table_routing", "table_matches"):
                _rt = _out.get(_k)
                if isinstance(_rt, dict):
                    for _src, _dest in _rt.items():
                        if isinstance(_src, str) and _src.strip() and isinstance(_dest, str) and _dest.strip():
                            _rows.append([_src.strip(), _dest.strip()])
                    if _rows:
                        break
            if _rows:
                _ex["table"] = {"columns": ["Source table", "→ Destination"], "rows": _rows[:12]}
            if _ex:
                stage_extras["deterministic"] = _ex

        # Column Intelligence (PK/FK/Shared counts · similar-column groups · format-gate
        # survivors · FK candidates · unified canonical-name table · columns mapped) is computed
        # at the PRE-SEMANTIC gate (node 3) — the SAME build_column_intelligence the Node-10 pass
        # uses — so it exists mid-run. Source it from the live gate payload, else from the run
        # row's persisted refs (the gate payload is wiped on resume); persist it back via
        # extra_refs so the column-stage detail survives nodes 4-9 through to completion.
        _persisted = await get_run_entry_state(
            organization_id=job.organization_id, session_id=str(job.id), session=s
        )
        _gp = job.pending_gate_payload if isinstance(job.pending_gate_payload, dict) else {}

        # Deterministic stage fallback: if node 2's completion log isn't visible yet (it isn't
        # in done_ids while paused at the pre-semantic gate), surface the table routing +
        # per-table confidence straight from the gate payload so the deterministic stage + its
        # source→destination table still render at the gate.
        if "deterministic" not in stage_extras:
            _routing = _gp.get("suggested_target_by_table")
            if isinstance(_routing, dict) and _routing:
                _drows = [
                    [str(_s).strip(), str(_d).strip()]
                    for _s, _d in _routing.items()
                    if str(_s).strip() and isinstance(_d, str) and _d.strip()
                ]
                _dex: dict = {}
                if _drows:
                    _dex["table"] = {"columns": ["Source table", "→ Destination"], "rows": _drows[:12]}
                _tconf = [
                    _c for _c in (_gp.get("table_match_confidence_by_table") or {}).values()
                    if isinstance(_c, (int, float))
                ]
                if _tconf:
                    _dex["confidence"] = sum(_tconf) / len(_tconf)
                if _dex:
                    stage_extras["deterministic"] = _dex

        _ci = _gp.get("column_intelligence")
        if not (isinstance(_ci, dict) and _ci):
            _ci = (_persisted.get("refs") or {}).get("column_intelligence")
        # Mapping decisions for the column→destination table: the run row's accumulated set
        # (append-only) plus anything in the current gate payload.
        _md_current = mapping_decisions_from_gate(job.pending_gate_payload)
        _md_all = list(_persisted.get("mapping_decisions") or []) + list(_md_current or [])

        extra_metrics: list[dict] = []
        extra_refs: dict = {}
        if isinstance(_ci, dict) and _ci:
            # Compact the CI to ONLY the fields the run card consumes (summary + trimmed groups
            # + fk_candidate count) before persisting it on the row — the full report carries
            # large format-gate / value-pattern / metadata arrays we never render. Idempotent:
            # re-compacting an already-compact (persisted) CI is a no-op.
            _ci = {
                "summary": _ci.get("summary") if isinstance(_ci.get("summary"), dict) else {},
                "groups": [
                    {"group_id": _g.get("group_id"),
                     "members": list(_g.get("members") or [])[:3],
                     "canonical_name": _g.get("canonical_name")}
                    for _g in (_ci.get("groups") or [])[:12] if isinstance(_g, dict)
                ],
                "fk_candidates": list(_ci.get("fk_candidates") or [])[:50],
            }
            for _stage, _sx in stage_extras_from_column_intelligence(_ci, mapping_decisions=_md_all).items():
                stage_extras.setdefault(_stage, {}).update(_sx)
            extra_metrics.extend(summary_metrics_from_column_intelligence(_ci))
            extra_refs["column_intelligence"] = _ci

        # Relationship/hierarchy count — computed at the hierarchy node (7) BEFORE the write,
        # logged as output.hierarchy_count. Emit "N relationships mapped" on the hierarchy stage
        # + a Section-1 "relationships" chip the moment node 7 has run.
        _hier = next(
            (nl for nl in node_logs if isinstance(nl, dict) and nl.get("node_id") == 7 and isinstance(nl.get("output"), dict)),
            None,
        )
        if _hier:
            _rc = _hier["output"].get("hierarchy_count")
            if isinstance(_rc, int) and _rc:
                stage_extras.setdefault("hierarchy", {}).setdefault(
                    "chips", []
                ).append(f"{_rc} relationship{'' if _rc == 1 else 's'} mapped")
                extra_metrics.append({"label": "relationships", "value": _rc})

        # Preprocess & Validate (node 6) — dedup + data-quality counts feed the center-step-4 mirror
        # ("Preprocess & Validate") the moment node 6 has run, so the log's count chips read like the
        # panel's ("N rows after dedup", "N quality warnings").
        _pp = next(
            (nl for nl in node_logs if isinstance(nl, dict) and nl.get("node_id") == 6 and isinstance(nl.get("output"), dict)),
            None,
        )
        if _pp:
            _po = _pp["output"]
            _cleaned = _po.get("total_cleaned_rows")
            _orig = _po.get("total_original_rows")
            _warn = _po.get("warning_count")
            _ppchips: list[str] = []
            if isinstance(_cleaned, int):
                _ppchips.append(f"{_cleaned:,} rows after dedup")
            if isinstance(_orig, int) and isinstance(_cleaned, int) and _orig > _cleaned:
                _ppchips.append(f"{_orig - _cleaned:,} duplicates removed")
            if isinstance(_warn, int) and _warn:
                _ppchips.append(f"{_warn} quality warning{'' if _warn == 1 else 's'}")
            if _ppchips:
                stage_extras.setdefault("preprocess_validate", {}).setdefault("chips", []).extend(_ppchips)

        # Data artifacts (node 9) — generated/uploaded output counts feed the center-step-6 mirror.
        _outnl = next(
            (nl for nl in node_logs if isinstance(nl, dict) and nl.get("node_id") == 9 and isinstance(nl.get("output"), dict)),
            None,
        )
        if _outnl:
            _oo = _outnl["output"]
            _up = _oo.get("artifacts_uploaded")
            _tc = _oo.get("table_count")
            _outchips: list[str] = []
            if isinstance(_up, int) and _up:
                _outchips.append(f"{_up} artifact{'' if _up == 1 else 's'} uploaded")
            if isinstance(_tc, int) and _tc:
                _outchips.append(f"{_tc} table{'' if _tc == 1 else 's'}")
            if _outchips:
                stage_extras.setdefault("data_artifacts", {}).setdefault("chips", []).extend(_outchips)

        entry = build_run_activity_entry(
            str(job.id),
            migration_status=job.status,
            completed_node_ids=done_ids,
            node_meta=node_meta,
            documents=1,
            tables=tables,
            columns=int(job.total_fields or 0),
            t1_mapped=int(job.t1_mapped_count or 0),
            t2_auto=int(job.t2_auto_count or 0),
            t2_human=int(job.t2_human_count or 0),
            unmapped=int(job.unmapped_count or 0),
            pending_gate_type=job.pending_gate_type,
            mapping_decisions=_md_current,
            cmms_name=job.cmms_name,
            source_filename=job.source_filename,
            running_node_ids=running_ids,
            stage_extras=stage_extras,
            extra_metrics=extra_metrics,
            extra_refs=extra_refs,
            error_message=job.error_message,
        )
        # The computed status is what actually lands in the activity row. If this says
        # "running" but the row stays pending, a LATER writer is clobbering it; if this says
        # "pending_human_input" while the job is mid-run, the gate fields weren't cleared.
        computed_status = entry.get("status")
        logger.info(
            "[run-activity] sync(%s) migration=%s computed ui_status=%s "
            "(from job.status=%s pending_gate_type=%s, done_nodes=%s)",
            caller, migration_id, computed_status, job.status, job.pending_gate_type,
            sorted(done_ids),
        )
        await upsert_run_activity(
            entry,
            organization_id=job.organization_id,
            session_id=str(job.id),
            merge_decisions=True,
            session=s,
        )
        await s.commit()
        logger.info(
            "[run-activity] sync(%s) migration=%s PERSISTED status=%s",
            caller, migration_id, computed_status,
        )
        return computed_status


async def schema_update_artifact_urls_auto(
    schema_mapping_id: str,
    output_json_url: str,
    output_csv_url: str,
    output_sql_url: str,
) -> None:
    """
    Persist artifact URLs to schema_mapping_jobs using a self-managed session.

    Called by schema_output_node after uploading artifacts to Azure Blob.
    """
    from ...db import get_async_session_factory
    sf = get_async_session_factory()
    async with sf() as db_session:
        try:
            await db_session.execute(
                update(SchemaMappingJob)
                .where(SchemaMappingJob.id == UUID(schema_mapping_id))
                .values(
                    output_json_url=output_json_url or None,
                    output_csv_url=output_csv_url or None,
                    output_sql_url=output_sql_url or None,
                )
            )
            await db_session.commit()
            logger.info(
                f"[schema_db_writer] Artifact URLs persisted for "
                f"schema_mapping={schema_mapping_id}"
            )
        except Exception as e:
            logger.warning(
                f"[schema_db_writer] schema_update_artifact_urls_auto failed (non-fatal): {e}"
            )
            try:
                await db_session.rollback()
            except Exception:
                pass


async def schema_write_error(
    db_session: AsyncSession,
    schema_mapping_id: str,
    error_message: str,
    error_node: Optional[int] = None,
    status: str = "error",
) -> None:
    """
    Write error state to schema_mapping_jobs.

    Args:
        status: "error" for general failures, "ddl_failed" for DDL rollbacks
                (DDL rollbacks expose the failing SQL to the frontend so the
                user can correct their field definitions and re-submit).
    """
    try:
        values: dict[str, Any] = {
            "status": status,
            "error_message": error_message[:2000],  # DDL errors can be verbose
            "error_timestamp": datetime.utcnow(),
        }
        if error_node is not None:
            values["current_node"] = error_node
        await db_session.execute(
            update(SchemaMappingJob)
            .where(SchemaMappingJob.id == UUID(schema_mapping_id))
            .values(**values)
        )
        await db_session.commit()
    except Exception as e:
        logger.warning(f"[schema_db_writer] schema_write_error failed (non-fatal): {e}")
        try:
            await db_session.rollback()
        except Exception:
            pass
