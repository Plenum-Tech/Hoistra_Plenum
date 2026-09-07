"""Feature 7 ⇄ Feature 4 — the AL.6 emit hook.

Turns a completed :class:`udr.pipeline.UdrRunResult` into one Activity-Log entry: builds
the Activity Summary + Processing Log (``build_udr_activity_entry``) and records it
(``record_activity``), flipping the notification badge. One entry per UDR run (AL.6).

Called by the LangGraph node adapter AFTER the relationship graph has been persisted +
committed, so the "UDR script saved" line is never claimed for an uncommitted graph. If
Test 2 blocks the run, ``build_udr_activity_entry`` auto-sets status ``pending_human_input``
(red) — no branching needed here.
"""

from __future__ import annotations


async def emit_udr_activity(
    result,
    *,
    organization_id=None,
    session_id: str | None = None,
    script_ref=None,
    trigger: str = "query",
    status_override: str | None = None,
    preprocessing_report: dict | None = None,
    extra_stage_meta: dict | None = None,
    intent_confidence: float | None = None,
    session=None,
) -> str:
    """Build + record the Activity-Log entry for a UDR run. Returns the new entry id."""
    from .activity import build_stage_meta, build_summary_metrics, build_udr_activity_entry
    from .activity_persist import upsert_run_activity
    from .suggestions import udr_suggestions

    # Derive the per-stage Processing-Log detail (chips · mini-tables · action refs ·
    # confidence · timestamps) from the run's own data + the upstream pre-processing report,
    # unless the caller passed an explicit stage_meta override on the result.
    stage_meta = getattr(result, "stage_meta", None) or build_stage_meta(result, preprocessing_report)
    # Merge caller-supplied per-stage extras (e.g. deterministic/semantic node durations that
    # the UDR pipeline can't see — they ran in earlier graph nodes) without clobbering.
    for stage, extra in (extra_stage_meta or {}).items():
        if isinstance(extra, dict):
            stage_meta.setdefault(stage, {}).update({k: v for k, v in extra.items() if v is not None})

    entry = build_udr_activity_entry(
        result.run_id,
        documents_ingested=getattr(result, "documents_ingested", 0),
        table_count=result.table_count,
        column_count=result.column_count,
        test1_report=result.test1_report,
        test2_report=result.test2_report,
        script_ref=script_ref,
        mapping_decisions=result.mapping_decisions,
        trigger=trigger,
        status_override=status_override,
        # Processing-Log Section 2: per-stage CoT/CoA detail captured during the run.
        # Forward-compatible — renders the richer timeline as the pipeline populates these.
        stage_summaries=getattr(result, "stage_summaries", None),
        stage_meta=stage_meta,
        intent_confidence=intent_confidence if intent_confidence is not None else getattr(result, "intent_confidence", None),
        decomposition_text=getattr(result, "decomposition_text", None),
        # AL.6 point 7 — expanded Activity-Summary chips (PK/FK/Shared · relationships ·
        # auto-consolidated · review · columns mapped · chunks), de-duped against the base set.
        extra_metrics=build_summary_metrics(result),
    )

    # AL.5 — attach up to 3 refinements + 2 follow-ups for the completed outcome (in refs,
    # so it round-trips through the existing JSONB column without a schema change).
    table_meta = getattr(result, "table_metadata", None) or []
    sample_table = table_meta[0].get("table") if table_meta and isinstance(table_meta[0], dict) else None
    suggestions = udr_suggestions(
        table_count=result.table_count,
        relationship_count=result.relationship_count,
        test1_flags=int((result.test1_report or {}).get("failed", 0) or 0),
        test2_flags=int((result.test2_report or {}).get("flagged_unexplained", 0) or 0),
        blocked=getattr(result, "blocked", False),
        sample_table=sample_table,
    )
    # AL.5 — prepend concrete "Missing FK relationship detected → create a foreign key"
    # refinements built from the Test-2 unexplained-overlap flags (specific entity pairs),
    # so the evidence surfaces as actionable suggestions, not just a flag count.
    try:
        from .review_audit import build_missing_fk_suggestions

        _fk = build_missing_fk_suggestions(getattr(result, "test2_report", None))
        if _fk:
            suggestions["refinements"] = (_fk + list(suggestions.get("refinements") or []))[:6]
    except Exception:  # pragma: no cover — additive, never fatal
        pass

    if suggestions["refinements"] or suggestions["follow_ups"]:
        refs = entry.get("refs")
        if isinstance(refs, dict):
            refs["suggestions"] = suggestions

    # 7.3 — surface auto-consolidated source tables + candidates needing confirmation.
    groups = getattr(result, "consolidated_groups", None) or []
    candidates = getattr(result, "unique_table_candidates", None) or []
    if groups or candidates:
        refs = entry.get("refs")
        if isinstance(refs, dict):
            refs["table_consolidation"] = {"auto_consolidated": groups, "candidates": candidates}

    # Mark this as THE per-run entry so it FINALISES the same progressive row that was
    # upserted during the run (live → completed, one durable record). merge_decisions=False:
    # the completed run's authoritative mapping_decisions replace the partial live set.
    refs = entry.get("refs")
    if isinstance(refs, dict):
        refs["entry_kind"] = "run"

    return await upsert_run_activity(
        entry,
        organization_id=organization_id,
        session_id=session_id,
        merge_decisions=False,
        session=session,
    )
