"""Trace Lab taxonomy routes (Phase 5): failure modes, unfiled notes, labels, AI proposals and the requirement register.
trace_lab.app includes this router under /api/trace-lab (ROUTERS):

    GET    /modes  /modes/{id}                          the modes (agent and level labels, who owns the taxonomy), one mode
    POST   /modes   PATCH /modes/{id}                   create, edit                        (platform owner)
    POST   /modes/merge  /modes/{id}/rename|split|retire|restore                             (platform owner)
    GET    /notes/unfiled                               notes nobody has filed or set aside
    POST   /notes/{id}/file                             file a note under a mode            (company or platform owner)
    POST   /notes/{id}/dispose | undispose              set a note aside, bring it back     (platform owner)
    GET    /labels/matrix  /turns/{turn_id}/labels  /labels/{turn_id}/{mode_id}/history      the grid, an answer's chips, a chain
    PUT    /labels/{turn_id}/{mode_id}                  decide or clear one cell            (company or platform owner)
    GET    /proposals/estimate                          what a run would cost               (platform owner)
    POST   /proposal-runs   GET /proposal-runs[/{id}]   start, list, read                   (platform owner)
    POST   /proposal-runs/{id}/resume | pause           (platform owner)
    GET    /proposals                                   a queue: grouping drafts, depth-scan or cell suggestions
    POST   /proposals/{id}/decide                       one decision                        (company or platform owner)
    POST   /proposals/bulk/check | bulk/approve         the nine-rule audit, then the approval (company or platform owner)
    GET    /requirements                                the register, with area labels
    POST   /requirements  /requirements/seed  /requirements/{code}/revisions|withdraw        (platform owner)

Who (auth.py's one rule; this module adds none): a READER is a company admin, or a superadmin acting for a company,
or - acting for none - the platform view of every company. A WRITER is the same person: a company's reviewers write for their
company, the platform owner for any (the store decides what a company may touch). The OWNER is a superadmin acting for no
company: changing the taxonomy, setting notes aside, running proposals and the requirement register. Callers are checked
before readiness (db.require_ready, else 503), so a stranger learns nothing about Trace Lab's state. Refusals keep the
house envelope (db.http_error via review_errors.http_error_for). Only the owner can start spending (proposals.start_run,
capped and refused when it cannot be capped); no other route here calls a model.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from . import annotations, labels, proposals, requirements, taxonomy
from .review_errors import Invalid, ReviewError, Stale, http_error_for
from .routes_mode_proposals import Platform, platform
from .routes_review import AnchorIn, Reader, reader

router = APIRouter(tags=["Trace Lab taxonomy"])

#: A writer is a reader: the same caller rule (a company's admin, a superadmin acting for it, or the platform owner
#: acting for none). What each of them may touch is decided by the store, as for every other Trace Lab write.
writer = reader

LEVEL_PATTERN = f"^({'|'.join(taxonomy.LEVELS)})$"
NAME_PATTERN = r"^[a-z][a-z0-9_]{2,63}$"
ID_MAX = 64
TURN_ID_MAX = 200
KIND_PATTERN = "^(group|depth_scan|cell|suggestion)$"
STATUS_PATTERN = "^(pending|accepted|dismissed|closed)$"


async def _call(coro: Any) -> Any:
    """Await a store call; a review error is the house envelope, a unique-index race is the same 409 the store would
    have given, anything else is not ours to hide."""
    try:
        return await coro
    except ReviewError as exc:
        raise http_error_for(exc) from exc
    except IntegrityError as exc:
        if "ux_tl_modes_live_name" in str(exc):
            raise http_error_for(taxonomy.conflict("name_taken")) from exc
        if labels.is_head_race(exc):
            raise http_error_for(Stale(None)) from exc
        raise


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


# ── bodies ────────────────────────────────────────────────────────────────────────────

class ModeDraft(BaseModel):
    name: str = Field(..., pattern=NAME_PATTERN)
    definition: str = Field(..., min_length=10, max_length=1000)
    boundary: str = Field("", max_length=1000)
    nearest_mode_id: str | None = Field(None, max_length=ID_MAX)
    level: str = Field(..., pattern=LEVEL_PATTERN)
    agent: str = Field(..., max_length=40)
    evaluator_type: str | None = Field(None, pattern="^(code|judge)$")
    requirement_ids: list[str] = Field(default_factory=list, max_length=10)


class ModeCreateBody(ModeDraft):
    created_from: list[str] = Field(..., min_length=1, max_length=50)


class ModeEditBody(BaseModel):
    base_version: int = Field(..., ge=1)
    definition: str | None = Field(None, min_length=10, max_length=1000)
    boundary: str | None = Field(None, max_length=1000)
    nearest_mode_id: str | None = Field(None, max_length=ID_MAX)
    level: str | None = Field(None, pattern=LEVEL_PATTERN)
    agent: str | None = Field(None, max_length=40)
    evaluator_type: str | None = Field(None, pattern="^(code|judge)$")
    requirement_ids: list[str] | None = Field(None, max_length=10)
    positive_turn_ids: list[str] | None = Field(None, max_length=50)
    close_negative_turn_ids: list[str] | None = Field(None, max_length=50)
    close_negatives_waiver: str | None = Field(None, max_length=500)
    status: str | None = Field(None, pattern="^(candidate|confirmed|frozen)$")
    rescope: bool = False


class RenameBody(BaseModel):
    name: str = Field(..., pattern=NAME_PATTERN)
    base_version: int = Field(..., ge=1)


class RetireBody(BaseModel):
    reason: str = Field(..., min_length=1, max_length=taxonomy.REASON_MAX)
    base_version: int = Field(..., ge=1)


class RestoreBody(BaseModel):
    base_version: int = Field(..., ge=1)


class MergeBody(BaseModel):
    source_ids: list[str] = Field(..., min_length=1, max_length=10)
    into: str | None = Field(None, max_length=ID_MAX)
    new: ModeDraft | None = None
    base_versions: dict[str, int]


class SplitChild(ModeDraft):
    created_from: list[str] = Field(..., min_length=1, max_length=50)


class SplitBody(BaseModel):
    base_version: int = Field(..., ge=1)
    children: list[SplitChild] = Field(..., min_length=2, max_length=5)
    assignments: dict[str, list[int]] = Field(default_factory=dict)


class FileBody(BaseModel):
    mode_id: str = Field(..., max_length=ID_MAX)
    base: str | None = Field(None, max_length=ID_MAX)
    override_label: bool = False


class DisposeBody(BaseModel):
    outcome: str = Field(..., pattern="^(no_mode_fits|not_a_failure)$")
    note: str = Field(..., min_length=1, max_length=annotations.DISPOSE_NOTE_MAX)


class LabelBody(BaseModel):
    present: bool | None
    base: str | None = Field(None, max_length=ID_MAX)
    note: str | None = Field(None, max_length=2000)
    anchor: AnchorIn | None = None
    spot_check_seed: str | None = Field(None, max_length=32)


class RunBody(BaseModel):
    kind: str = Field(..., pattern="^(grouping|depth_scan|cells)$")
    mode_id: str | None = Field(None, max_length=ID_MAX)
    since: datetime | None = None
    until: datetime | None = None
    usd_cap: float = Field(..., gt=0)
    filter_org: str | None = Field(None, max_length=ID_MAX)
    limit: int = Field(proposals.CELL_LIMIT_DEFAULT, ge=1, le=proposals.CELL_LIMIT_MAX)
    include_candidates: bool = False


class ResumeBody(BaseModel):
    usd_cap: float | None = Field(None, gt=0)


class EditsBody(BaseModel):
    """What the platform owner changes in an AI draft before accepting it: only the fields named."""

    name: str | None = Field(None, pattern=NAME_PATTERN)
    definition: str | None = Field(None, min_length=10, max_length=1000)
    boundary: str | None = Field(None, max_length=1000)
    nearest_mode_id: str | None = Field(None, max_length=ID_MAX)
    level: str | None = Field(None, pattern=LEVEL_PATTERN)
    agent: str | None = Field(None, max_length=40)
    evaluator_type: str | None = Field(None, pattern="^(code|judge)$")
    requirement_ids: list[str] | None = Field(None, max_length=10)


class DecideBody(BaseModel):
    decision: str = Field(..., pattern="^(accept|dismiss|override)$")
    present: bool | None = None
    edits: EditsBody | None = None
    base: str | None = Field(None, max_length=ID_MAX)
    override_labels: bool = False


class BulkBody(BaseModel):
    action: str = Field(..., pattern="^(accept|dismiss)$")
    proposal_ids: list[str] = Field(..., min_length=1, max_length=proposals.BULK_MAX)
    served_at: datetime
    bases: dict[str, str | None] = Field(default_factory=dict)


class Implementation(BaseModel):
    doc: str = Field(..., max_length=300)
    section: str = Field("", max_length=120)
    line: int | None = Field(None, ge=1)
    quote: str = Field(..., min_length=8, max_length=600)


class ReqCreateBody(BaseModel):
    area: str = Field(..., pattern=f"^({'|'.join(requirements.AREAS)})$")
    statement: str = Field(..., min_length=10, max_length=600)
    level: str = Field(..., pattern=LEVEL_PATTERN)
    implementations: list[Implementation] = Field(default_factory=list, max_length=10)
    motivating_turn_ids: list[str] = Field(default_factory=list, max_length=20)
    activate: bool = True


class ReqReviseBody(BaseModel):
    statement: str = Field(..., min_length=10, max_length=600)
    implementations: list[Implementation] = Field(default_factory=list, max_length=10)
    motivating_turn_ids: list[str] = Field(default_factory=list, max_length=20)
    note: str = Field(..., min_length=3, max_length=1000)
    activate: bool = True


class ReqWithdrawBody(BaseModel):
    note: str = Field(..., min_length=3, max_length=1000)


# ── modes ─────────────────────────────────────────────────────────────────────────────

@router.get("/modes")
async def list_modes(include_retired: bool = Query(False), who: Reader = Depends(reader)) -> dict[str, Any]:
    modes = await _call(taxonomy.list_modes(who.principal, who.org, include_retired=include_retired))
    return {"ok": True, "modes": modes,
            "agents": [{"key": k, "label": taxonomy.AGENT_LABELS[k]} for k in taxonomy.AGENTS],
            "levels": [{"key": k, "label": taxonomy.LEVEL_LABELS[k]} for k in taxonomy.LEVELS],
            "statuses": list(taxonomy.STATUSES), "fix_test": taxonomy.FIX_TEST, "fix_test_text": taxonomy.FIX_TEST_TEXT,
            "grouping_note": taxonomy.GROUPING_CARD_NOTE, "is_owner": labels.is_owner(who.principal, who.org)}


@router.get("/modes/{mode_id}")
async def get_mode(mode_id: str = Path(..., max_length=ID_MAX), who: Reader = Depends(reader)) -> dict[str, Any]:
    out = await _call(taxonomy.get_mode(who.principal, who.org, mode_id))
    if out is None:
        raise http_error_for(taxonomy.not_found("mode_not_found"))
    return {"ok": True, **out}


@router.post("/modes", status_code=201)
async def create_mode(body: ModeCreateBody, who: Platform = Depends(platform)) -> dict[str, Any]:
    draft = body.model_dump(exclude={"created_from"})
    return {"ok": True, **await _call(taxonomy.create(who.principal, draft, created_from=body.created_from))}


@router.post("/modes/merge")
async def merge_modes(body: MergeBody, who: Platform = Depends(platform)) -> dict[str, Any]:
    new = body.new.model_dump() if body.new is not None else None
    return {"ok": True, **await _call(taxonomy.merge(who.principal, body.source_ids, into=body.into, new=new,
                                                     base_versions=body.base_versions))}


@router.patch("/modes/{mode_id}")
async def edit_mode(body: ModeEditBody, mode_id: str = Path(..., max_length=ID_MAX),
                    who: Platform = Depends(platform)) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True, exclude={"base_version", "rescope"})
    return {"ok": True, **await _call(taxonomy.edit(who.principal, mode_id, changes, base_version=body.base_version,
                                                    rescope=body.rescope))}


@router.post("/modes/{mode_id}/rename")
async def rename_mode(body: RenameBody, mode_id: str = Path(..., max_length=ID_MAX),
                      who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(taxonomy.rename(who.principal, mode_id, body.name, base_version=body.base_version))}


@router.post("/modes/{mode_id}/split")
async def split_mode(body: SplitBody, mode_id: str = Path(..., max_length=ID_MAX),
                     who: Platform = Depends(platform)) -> dict[str, Any]:
    children = [c.model_dump() for c in body.children]
    return {"ok": True, **await _call(taxonomy.split(who.principal, mode_id, children, body.assignments,
                                                     base_version=body.base_version))}


@router.post("/modes/{mode_id}/retire")
async def retire_mode(body: RetireBody, mode_id: str = Path(..., max_length=ID_MAX),
                      who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(taxonomy.retire(who.principal, mode_id, reason=body.reason,
                                                      base_version=body.base_version))}


@router.post("/modes/{mode_id}/restore")
async def restore_mode(body: RestoreBody, mode_id: str = Path(..., max_length=ID_MAX),
                       who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(taxonomy.restore(who.principal, mode_id, base_version=body.base_version))}


# ── notes ─────────────────────────────────────────────────────────────────────────────

@router.get("/notes/unfiled")
async def unfiled_notes(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
                        who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(annotations.unfiled_notes(who.principal, who.org, limit=limit, offset=offset))}


@router.post("/notes/{annotation_id}/file")
async def file_note(body: FileBody, annotation_id: str = Path(..., max_length=ID_MAX),
                    who: Reader = Depends(writer)) -> dict[str, Any]:
    return {"ok": True, **await _call(annotations.file_note(who.principal, who.org, annotation_id, body.mode_id,
                                                            base=body.base, override_label=body.override_label))}


@router.post("/notes/{annotation_id}/dispose")
async def dispose_note(body: DisposeBody, annotation_id: str = Path(..., max_length=ID_MAX),
                       who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(annotations.dispose_note(who.principal, annotation_id, outcome=body.outcome,
                                                               note=body.note))}


@router.post("/notes/{annotation_id}/undispose")
async def undispose_note(annotation_id: str = Path(..., max_length=ID_MAX),
                         who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(annotations.undispose_note(who.principal, annotation_id))}


# ── labels ────────────────────────────────────────────────────────────────────────────

@router.get("/labels/matrix")
async def labels_matrix(batch_id: str | None = Query(None, max_length=ID_MAX), since: datetime | None = Query(None),
                        until: datetime | None = Query(None), state: str = Query("all", pattern="^(all|missing)$"),
                        statuses: str = Query("confirmed,frozen", max_length=60),
                        mode_ids: str | None = Query(None, max_length=2000), limit: int = Query(50, ge=1, le=200),
                        offset: int = Query(0, ge=0), filter_org: str | None = Query(None, max_length=ID_MAX),
                        who: Reader = Depends(reader)) -> dict[str, Any]:
    wanted = tuple(s for s in _csv(statuses) if s in taxonomy.LIVE_STATUSES) or taxonomy.COMPLETENESS_STATUSES
    return await _call(labels.matrix(who.principal, who.org, batch_id=batch_id, since=since, until=until, state=state,
                                     statuses=wanted, mode_ids=_csv(mode_ids) or None, limit=limit, offset=offset,
                                     filter_org=filter_org))


@router.get("/turns/{turn_id}/labels")
async def turn_labels(turn_id: str = Path(..., max_length=TURN_ID_MAX), who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(labels.cells_for_turn(who.principal, who.org, turn_id))}


@router.put("/labels/{turn_id}/{mode_id}")
async def set_label(body: LabelBody, turn_id: str = Path(..., max_length=TURN_ID_MAX),
                    mode_id: str = Path(..., max_length=ID_MAX), who: Reader = Depends(writer)) -> dict[str, Any]:
    return {"ok": True, **await _call(labels.set_label(who.principal, who.org, turn_id, mode_id, present=body.present,
                                                       base=body.base, note=body.note, anchor=body.anchor,
                                                       spot_check_seed=body.spot_check_seed))}


@router.get("/labels/{turn_id}/{mode_id}/history")
async def label_history(turn_id: str = Path(..., max_length=TURN_ID_MAX), mode_id: str = Path(..., max_length=ID_MAX),
                        who: Reader = Depends(reader)) -> dict[str, Any]:
    return {"ok": True, **await _call(labels.history(who.principal, who.org, turn_id, mode_id))}


# ── proposals ─────────────────────────────────────────────────────────────────────────

@router.get("/proposals/estimate")
async def proposal_estimate(kind: str = Query(..., pattern="^(grouping|depth_scan|cells)$"),
                            mode_id: str | None = Query(None, max_length=ID_MAX), since: datetime | None = Query(None),
                            until: datetime | None = Query(None), filter_org: str | None = Query(None, max_length=ID_MAX),
                            limit: int = Query(proposals.CELL_LIMIT_DEFAULT, ge=1, le=proposals.CELL_LIMIT_MAX),
                            include_candidates: bool = Query(False), who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.estimate(who.principal, kind=kind, mode_id=mode_id, since=since,
                                                         until=until, filter_org=filter_org, limit=limit,
                                                         include_candidates=include_candidates))}


@router.post("/proposal-runs", status_code=202)
async def start_run(body: RunBody, who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.start_run(who.principal, kind=body.kind, mode_id=body.mode_id,
                                                          since=body.since, until=body.until, usd_cap=body.usd_cap,
                                                          filter_org=body.filter_org, limit=body.limit,
                                                          include_candidates=body.include_candidates))}


@router.get("/proposal-runs")
async def list_runs(who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, "runs": await _call(proposals.list_runs(who.principal))}


@router.get("/proposal-runs/{run_id}")
async def get_run(run_id: str = Path(..., max_length=ID_MAX), who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.get_run(who.principal, run_id))}


@router.post("/proposal-runs/{run_id}/resume", status_code=202)
async def resume_run(run_id: str = Path(..., max_length=ID_MAX), body: ResumeBody | None = None,
                     who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.resume_run(who.principal, run_id, usd_cap=body.usd_cap if body else None))}


@router.post("/proposal-runs/{run_id}/pause")
async def pause_run(run_id: str = Path(..., max_length=ID_MAX), who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.pause_run(who.principal, run_id))}


@router.get("/proposals")
async def list_proposals(kind: str | None = Query(None, pattern=KIND_PATTERN),
                         status: str = Query("pending", pattern=STATUS_PATTERN),
                         mode_id: str | None = Query(None, max_length=ID_MAX),
                         run_id: str | None = Query(None, max_length=ID_MAX),
                         turn_id: str | None = Query(None, max_length=TURN_ID_MAX), limit: int = Query(50, ge=1, le=200),
                         offset: int = Query(0, ge=0), who: Reader = Depends(reader)) -> dict[str, Any]:
    if kind is None and not turn_id:
        raise http_error_for(Invalid("kind_required", "Say which suggestions to list."))
    return {"ok": True, **await _call(proposals.queue(who.principal, who.org, kind=kind or "suggestion", status=status,
                                                      mode_id=mode_id, run_id=run_id, turn_id=turn_id, limit=limit,
                                                      offset=offset))}


@router.post("/proposals/bulk/check")
async def bulk_check(body: BulkBody, who: Reader = Depends(writer)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.bulk_check(who.principal, who.org, action=body.action,
                                                           proposal_ids=body.proposal_ids, served_at=body.served_at))}


@router.post("/proposals/bulk/approve")
async def bulk_approve(body: BulkBody, who: Reader = Depends(writer)) -> dict[str, Any]:
    return {"ok": True, **await _call(proposals.bulk_approve(who.principal, who.org, action=body.action,
                                                             proposal_ids=body.proposal_ids, served_at=body.served_at,
                                                             bases=body.bases))}


@router.post("/proposals/{proposal_id}/decide")
async def decide(body: DecideBody, proposal_id: str = Path(..., max_length=ID_MAX),
                 who: Reader = Depends(writer)) -> dict[str, Any]:
    edits = body.edits.model_dump(exclude_none=True) if body.edits is not None else None
    return {"ok": True, **await _call(proposals.decide(who.principal, who.org, proposal_id, decision=body.decision,
                                                       present=body.present, edits=edits, base=body.base,
                                                       override_labels=body.override_labels))}


# ── requirements ──────────────────────────────────────────────────────────────────────

@router.get("/requirements")
async def list_requirements(status: str | None = Query(None, pattern="^(draft|active|superseded|withdrawn)$"),
                            who: Reader = Depends(reader)) -> dict[str, Any]:
    rows = await _call(requirements.list_requirements(status=status, org=who.org))
    return {"ok": True, "requirements": rows,
            "areas": [{"key": a, "label": requirements.AREA_LABELS[a]} for a in requirements.AREAS],
            "levels": [{"key": k, "label": taxonomy.LEVEL_LABELS[k]} for k in taxonomy.LEVELS]}


@router.post("/requirements", status_code=201)
async def create_requirement(body: ReqCreateBody, who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(requirements.create(
        who.principal, area=body.area, statement=body.statement, level=body.level,
        implementations=[i.model_dump() for i in body.implementations],
        motivating_turn_ids=body.motivating_turn_ids, activate=body.activate))}


@router.post("/requirements/seed")
async def seed_requirements(who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(requirements.seed(who.principal))}


@router.post("/requirements/{code}/revisions", status_code=201)
async def revise_requirement(body: ReqReviseBody, code: str = Path(..., max_length=20),
                             who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(requirements.revise(
        who.principal, code, statement=body.statement, implementations=[i.model_dump() for i in body.implementations],
        motivating_turn_ids=body.motivating_turn_ids, note=body.note, activate=body.activate))}


@router.post("/requirements/{code}/withdraw")
async def withdraw_requirement(body: ReqWithdrawBody, code: str = Path(..., max_length=20),
                               who: Platform = Depends(platform)) -> dict[str, Any]:
    return {"ok": True, **await _call(requirements.withdraw(who.principal, code, note=body.note))}
