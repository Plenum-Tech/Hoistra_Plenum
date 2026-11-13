"""The eval store's routes (11 Nov 2026, task plan Workstream B), under /api/trace-lab.

    GET    /eval/run-tags                     the run tags the caller can see sessions under
    POST   /eval/run-tags                     a new run tag                                              (curators)
    POST   /eval/ingest                       file the conversations the chat recorded as sessions       (curators)
    GET    /eval/sessions                     sessions, newest first, filtered
    GET    /eval/sessions/{session_id}        one session: its scenario, its traces in turn order, their scores
    GET    /eval/traces/{trace_id}/tree       one trace as the tree view reads it
    GET    /eval/traces/{trace_id}/notes      the chat record and everyone's notes on a trace's spans
    GET    /eval/labels?run_tag=              a run tag's sessions as the Labels grid: a row per turn (13 Nov 2026)
    GET    /eval/traces/{trace_id}/labels     the failure modes and everyone's label on a trace
    PUT    /eval/traces/{trace_id}/labels/{mode}  the caller's label: positive, negative or null to clear
    GET    /eval/traces/{trace_id}/expected-flag      who says this turn's expected outcome is wrong
    PUT    /eval/traces/{trace_id}/expected-flag      the caller's flag: {"flagged": true|false, "comment"}
    POST   /eval/scenarios/import             a scenario file, taken whole or refused with its lines     (curators)
    GET    /eval/scenarios                    the current version of every scenario
    GET    /eval/scenarios/export             the same, as a file that imports back unchanged
    POST   /eval/scenarios/approve            mark scenarios approved                                    (curators)
    POST   /eval/scenarios/generate           generate a batch for every area of the specification       (curators)
    POST   /eval/evaluate                     score a run tag's traces at the logic, ux and domain levels (curators)
    PUT    /eval/traces/{trace_id}/scores/{level}   a person's score on a trace: pass or fail, a score, a comment
    GET    /eval/compare                      two run tags side by side, scenario by scenario
    GET    /eval/spec                         the current specification's sections, and how many scenarios are out of date
    POST   /eval/spec/reload                  read the specification file again                          (curators)
    GET    /eval/sync                         is there another Trace Lab database to push to, and the last push
    POST   /eval/sync                         push the eval store there            (platform team, consultant)

Every route takes the caller the way the Runs page does (routes_runs._wide): an active member of one company, or the
platform team and a consultant across every company. The modules decide who may do what and raise review errors;
_call turns those into the house envelope.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from . import (db, eval_judges, eval_store, expected_flags, labelling, runs, scenario_file, scenario_gen, scores, spec_doc, sync_push, trace_labels, trace_tree)
from .review_errors import Forbidden
from .routes_labelling import _call
from .routes_runs import _wide

router = APIRouter(tags=["Trace Lab eval"])

INGEST_DAYS = 90
#: How far back "Read chat questions" goes when no date is given (eval_store.CHAT_SYNC_DAYS), not ninety days.
CHAT_INGEST_DAYS = eval_store.CHAT_SYNC_DAYS


class RunTagIn(BaseModel):
    tag: str = Field(..., min_length=1, max_length=40)
    kind: str = Field("adhoc", max_length=20)
    note: str | None = Field(None, max_length=500)


class IngestIn(BaseModel):
    #: Conversations with a turn since then. Ninety days back when not given.
    since: datetime | None = None
    #: With `sessions`: the run tag those chat sessions are filed under.
    run_tag: str | None = Field(None, max_length=40)
    #: {chat session id: scenario id or null}. Without it, scenario sessions are found by their session id.
    sessions: dict[str, str | None] | None = Field(None, max_length=eval_store.INGEST_SESSIONS)
    #: Also read the ordinary chat conversations (a week back unless `since` says otherwise), filed
    #: under the run tag "chat". Scenario sessions are read either way.
    chat: bool = False


class GenerateIn(BaseModel):
    #: 250 is the batch the task plan names: 100 single-domain, 60 multi-domain, 50 multi-turn, 40 failure.
    count: int = Field(250, ge=1, le=scenario_gen.MAX_COUNT)
    #: The same count, seed and space give the same scenarios with the same ids.
    seed: int = Field(0, ge=0, le=10_000_000)
    space: str = Field("full", max_length=20)


class EvaluateIn(BaseModel):
    run_tag: str = Field(..., min_length=1, max_length=40)
    levels: list[str] = Field(default_factory=lambda: list(scores.LEVELS), min_length=1, max_length=3)
    #: Traces scored in this call. A page or a script calls again until nothing remains.
    limit: int = Field(25, ge=1, le=eval_judges.PASS_MAX)
    #: Traces to pass over: those whose judge call failed earlier in the same run, so the pass moves on.
    skip: list[str] = Field(default_factory=list, max_length=5000)


class ExpectedFlagIn(BaseModel):
    #: True: the turn's expected outcome is wrong (the scenario needs correcting); False clears the caller's flag.
    flagged: bool
    comment: str | None = Field(None, max_length=2000)


class TraceLabelIn(BaseModel):
    #: positive (the failure mode is there), negative (it is not) or null to clear the caller's value.
    value: str | None = Field(None, max_length=20)


class ScoreIn(BaseModel):
    verdict: str = Field(..., max_length=20)
    value: float | None = Field(None, ge=0, le=1)
    comment: str | None = Field(None, max_length=scores.COMMENT_MAX)


class SyncIn(BaseModel):
    #: Count what would go and write nothing.
    dry_run: bool = False
    #: Read every row, not only those made since the last push that finished.
    full: bool = False


class ApproveIn(BaseModel):
    scenario_ids: list[str] = Field(..., min_length=1, max_length=scenario_file.MAX_SCENARIOS)


def _company(m: labelling.Member) -> str | None:
    """The one company a member acts for, or None for the platform team and a consultant across every company."""
    return None if m.all_companies else m.org


# ── run tags ─────────────────────────────────────────────────────────────────────────

@router.get("/eval/run-tags")
async def eval_run_tags(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "run_tags": await _call(eval_store.list_run_tags(m))}


@router.post("/eval/run-tags", status_code=201)
async def eval_new_run_tag(body: RunTagIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, "run_tag": await _call(eval_store.create_run_tag(m, body.tag, kind=body.kind, note=body.note))}


# ── sessions ─────────────────────────────────────────────────────────────────────────

async def _ingest(m: labelling.Member, body: IngestIn) -> dict[str, int]:
    runs.require_curator(m)
    since = body.since or (datetime.now(timezone.utc) - timedelta(days=CHAT_INGEST_DAYS if body.chat else INGEST_DAYS))
    return await eval_store.ingest_from_main(since=since, run_tag=body.run_tag, sessions=body.sessions,
                                             org=_company(m), by=m, **({"chat": True} if body.chat else {}))


@router.post("/eval/ingest")
async def eval_ingest(body: IngestIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Read the conversations the chat recorded into the store. The application database is only read."""
    return {"ok": True, **await _call(_ingest(m, body))}


@router.get("/eval/sessions")
async def eval_sessions(run_tag: str | None = Query(None, max_length=40),
                        type: str | None = Query(None, max_length=40),  # noqa: A002
                        status: str | None = Query(None, max_length=20),
                        level: str | None = Query(None, max_length=20),
                        verdict: str | None = Query(None, max_length=20),
                        needs_sme: bool = Query(False), stale: bool = Query(False),
                        origin: str | None = Query(None, max_length=64), all_attempts: bool = Query(False),
                        before: datetime | None = Query(None), limit: int = Query(50, ge=1, le=eval_store.LIST_MAX),
                        m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    out = await _call(eval_store.list_sessions(
        m, run_tag=run_tag, type=type, status=status, level=level, verdict=verdict, needs_sme=needs_sme, stale=stale,
        origin=origin, all_attempts=all_attempts, before=before, limit=limit))
    return {"ok": True, **out}


@router.get("/eval/sessions/{session_id}")
async def eval_session(session_id: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(eval_store.get_session(m, session_id))}


@router.get("/eval/traces/{trace_id}/tree")
async def eval_trace_tree(trace_id: str, request: Request, m: labelling.Member = Depends(_wide)) -> Response:
    """One trace's tree. Gzipped here, as the Trace tab's own route is: a tree can be megabytes."""
    tree = await _call(eval_store.trace_tree_of(m, trace_id))
    return trace_tree.json_answer({"ok": True, "tree": tree}, request.headers.get("accept-encoding"))


# ── scenarios ────────────────────────────────────────────────────────────────────────

@router.post("/eval/scenarios/import")
async def eval_import_scenarios(file: UploadFile = File(...), dry_run: bool = Query(False),
                                m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """A scenario file. Read whole first: one bad line refuses the file and names every line that needs fixing."""
    data = await file.read(scenario_file.MAX_BYTES + 1)
    scenarios, errors = scenario_file.parse_jsonl(data, await scenario_file.allowed_sections())
    if errors:
        raise db.http_error(422, "bad_rows", "Some lines need fixing before this file can be stored.",
                            errors=errors[:200])
    if dry_run:
        return {"ok": True, "dry_run": True, "scenarios": len(scenarios)}
    out = await _call(scenario_file.save_scenarios(m, scenarios, source="import"))
    return {"ok": True, "dry_run": False, "scenarios": len(scenarios), **out}


@router.get("/eval/scenarios")
async def eval_scenarios(type: str | None = Query(None, max_length=40),  # noqa: A002
                         section: str | None = Query(None, max_length=120),
                         status: str | None = Query(None, max_length=20), stale: bool = Query(False),
                         m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    rows = await _call(scenario_file.list_scenarios(m, type=type, section=section, status=status, stale=stale))
    return {"ok": True, "scenarios": rows}


@router.get("/eval/scenarios/export")
async def eval_export_scenarios(m: labelling.Member = Depends(_wide)) -> Response:
    rows = await _call(scenario_file.list_scenarios(m))
    return Response(content=scenario_file.export_jsonl(rows), media_type="application/x-ndjson",
                    headers={"Content-Disposition": 'attachment; filename="scenarios.jsonl"'})


@router.post("/eval/scenarios/approve")
async def eval_approve_scenarios(body: ApproveIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(scenario_file.approve(m, body.scenario_ids))}


@router.post("/eval/scenarios/generate")
async def eval_generate_scenarios(body: GenerateIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Generate a batch that covers every area of the specification and store it as drafts. No model is called: the
    same seed gives the same scenarios, so generating twice stores nothing new."""
    try:
        scenarios_, dropped = scenario_gen.generate(count=body.count, seed=body.seed, space=body.space)
    except ValueError as exc:
        raise db.http_error(422, "bad_request", str(exc)) from None
    out = await _call(scenario_file.save_scenarios(m, scenarios_, source="generator")) if scenarios_ else {
        "added": 0, "new_versions": 0, "unchanged": 0}
    by_type: dict[str, int] = {}
    for s in scenarios_:
        by_type[s["type"]] = by_type.get(s["type"], 0) + 1
    return {"ok": True, "generated": len(scenarios_), "seed": body.seed, "space": body.space, "by_type": by_type,
            "dropped": len(dropped), "dropped_examples": dropped[:20], **out}


# ── evaluating, scoring, comparing ────────────────────────────────────────────────────

@router.post("/eval/evaluate")
async def eval_evaluate(body: EvaluateIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """One pass of the evaluators over a run tag: an automatic score per trace per level. The logic level costs
    nothing; the ux and domain levels call a model and stop at their spending limit."""
    out = await _call(eval_judges.evaluate_run(m, body.run_tag, levels=tuple(body.levels), limit=body.limit,
                                               skip=tuple(body.skip)))
    return {"ok": True, **out}


@router.get("/eval/traces/{trace_id}/notes")
async def eval_trace_notes(trace_id: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """The chat's record of a trace's turn and everyone's notes on its spans, for the session page."""
    return {"ok": True, **await _call(eval_store.trace_notes(m, trace_id))}


@router.get("/eval/traces/{trace_id}/expected-flag")
async def eval_trace_expected_flag(trace_id: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Who says this turn's expected outcome is wrong."""
    return {"ok": True, **await _call(expected_flags.flags_of(m, trace_id))}


@router.put("/eval/traces/{trace_id}/expected-flag")
async def eval_set_trace_expected_flag(trace_id: str, body: ExpectedFlagIn,
                                       m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """The caller's flag on this turn's expected outcome."""
    return {"ok": True, "flag": await _call(expected_flags.set_flag(m, trace_id, flagged=body.flagged,
                                                                    comment=body.comment))}


@router.get("/eval/labels")
async def eval_tag_labels(run_tag: str = Query(..., min_length=1, max_length=40),
                          m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """The Labels page for a run tag's sessions: a row per turn, the failure modes as columns (13 Nov 2026)."""
    return {"ok": True, **await _call(trace_labels.tag_matrix(m, run_tag))}


@router.get("/eval/traces/{trace_id}/labels")
async def eval_trace_labels(trace_id: str, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """The live failure modes and everyone's label on one trace."""
    return {"ok": True, **await _call(trace_labels.labels_of(m, trace_id))}


@router.put("/eval/traces/{trace_id}/labels/{mode}")
async def eval_set_trace_label(trace_id: str, mode: str, body: TraceLabelIn,
                               m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """The caller's label for one failure mode on one trace."""
    return {"ok": True, "label": await _call(trace_labels.set_label(m, trace_id, mode=mode, value=body.value))}


@router.put("/eval/traces/{trace_id}/scores/{level}")
async def eval_score_trace(trace_id: str, level: str, body: ScoreIn,
                           m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """A person's score. It becomes the trace's current score at that level; nothing is overwritten."""
    row = await _call(scores.annotate(m, trace_id, level, verdict=body.verdict, value=body.value, comment=body.comment))
    return {"ok": True, "score": row}


@router.get("/eval/compare")
async def eval_compare(base: str = Query(..., min_length=1, max_length=40),
                       head: str = Query(..., min_length=1, max_length=40),
                       m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(scores.compare(m, base, head))}


# ── the specification ────────────────────────────────────────────────────────────────

@router.get("/eval/spec")
async def eval_spec(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    return {"ok": True, **await _call(spec_doc.overview(m))}


@router.post("/eval/spec/reload")
async def eval_reload_spec(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Read the specification file again. A changed specification becomes the current one, and every scenario that
    cites a changed section shows as out of date until it is stored or approved again."""
    return {"ok": True, **await _call(spec_doc.load(m))}


# ── pushing to another Trace Lab database ─────────────────────────────────────────────

def _may_push(m: labelling.Member) -> None:
    """A push copies every company's eval rows, so it is for those who already see every company."""
    labelling.require_active(m)
    if not m.all_companies:
        raise Forbidden("platform_only", "Only the platform team and the consultant can push to another Trace Lab.")


@router.get("/eval/sync")
async def eval_sync_status(m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Whether DEV_DB_URL is set, where it points (host and database only) and how the last push there ended. A push
    carries every company's rows, so only those who may push are told any of it; anyone else reads "nowhere"."""
    labelling.require_active(m)
    if not m.all_companies:
        return {"ok": True, "enabled": False, "peer": None, "last": None}
    return {"ok": True, **await sync_push.status()}


async def _push(m: labelling.Member, body: SyncIn) -> dict[str, Any]:
    _may_push(m)
    return await sync_push.push(dry_run=body.dry_run, full=body.full, by=m)


@router.post("/eval/sync")
async def eval_sync(body: SyncIn, m: labelling.Member = Depends(_wide)) -> dict[str, Any]:
    """Push the eval store to the database in DEV_DB_URL and say how many records went."""
    try:
        out = await _call(_push(m, body))
    except sync_push.Refused as refused:
        raise db.http_error(409, refused.reason, refused.error) from None
    line = sync_push.summary_line(out)
    if out["status"] != "done":
        raise db.http_error(502, "sync_failed", line, pushed=out["pushed"], peer=out["peer"])
    return {"ok": True, **out, "line": line}
