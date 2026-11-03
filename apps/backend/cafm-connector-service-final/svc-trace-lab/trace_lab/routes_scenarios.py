"""Generate a synthetic batch for one role space.

    POST /scenarios/generate     a batch of questions for one space, verified against the router
    POST /scenarios/run          ask a few of them for real and return the answers
    GET  /lab/companies          the companies this caller may act for, for the picker
    GET  /learning/draft         what the reviews say, as markdown to carry to a skill document
    GET  /learning/draft.md      the same, as a file
    GET  /scenarios/spaces       the spaces that can be generated for, and the engines each has
    GET  /scenarios/template.csv the batch as the golden-set upload takes it

Who: generating reads NO company data -- it is deterministic from (space, seed), calls no model
and touches no database -- so it asks for a reader (a company admin, or any superadmin), the same
gate /golden/grader uses. Requiring a company for it refused the platform view with "Choose a
company to view as first" for a batch that does not belong to a company anyway (3 Nov 2026).
Asking the questions is different: it reads one company's data through the chat, so /scenarios/run
keeps the reviewer gate and the company that comes with it.

The expected-answer column of the CSV is deliberately EMPTY. A golden case needs a known right
answer, and an expected answer has to be grounded in a real row and then read by a person --
scenario-generation/SKILL.md is explicit that the review pass is a person's, not a model's, and
that about a tenth of a generated batch should be thrown away. Filling that column here with
something a model invented would turn an evaluation set into a way of agreeing with itself.
"""
from __future__ import annotations

import csv
import io
from typing import Any, Literal

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from . import db, learning, scenario_run, scenario_store, scenarios
from .routes_golden import reader
from .routes_conversations import Reviewer, reviewer

router = APIRouter(tags=["Trace Lab scenarios"])

#: The golden-set upload's own header. The generator fills what it can and leaves the rest.
CSV_HEADER = ("id", "question", "expected_answer", "evaluation", "page_context", "notes")


class GenerateRequest(BaseModel):
    space: Literal["ops", "comp", "assets", "full"]
    #: The feedback asked for batches of 50-100; smaller is allowed for a look, larger refused
    #: because a batch nobody reads is not an evaluation.
    count: int = Field(50, ge=1, le=100)
    seed: int = Field(0, ge=0, le=10_000_000)
    #: Questions the space must REFUSE, as a fraction. A gate that stops working is silent
    #: otherwise, so a batch can carry its own check that the space still says no.
    challenge_ratio: float = Field(0.0, ge=0.0, le=0.5)


@router.get("/scenarios/spaces")
async def list_spaces(who: Any = Depends(reader)) -> dict[str, Any]:
    """Each space, what it is called, and which engines a batch for it can use."""
    from src.agents.spaces import SPACE_NAMES

    return {"ok": True, "spaces": [
        {"key": s, "name": SPACE_NAMES.get(s, s), "engines": list(scenarios.engines_of(s))}
        for s in scenarios.SPACES
    ]}


@router.post("/scenarios/generate")
async def generate(body: GenerateRequest, who: Any = Depends(reader)) -> dict[str, Any]:
    """A batch for one space. Every question is put to the real router before it is returned, and
    one that would route outside its own space is dropped rather than shipped."""
    out = scenarios.batch(body.space, count=body.count, seed=body.seed,
                          challenge_ratio=body.challenge_ratio)
    # Says so when the router could not fill the batch, instead of quietly returning fewer.
    out["short"] = out["generated"] < out["requested"]
    return {"ok": True, **out}


@router.post("/scenarios/template.csv")
async def template_csv(body: GenerateRequest, who: Any = Depends(reader)) -> Response:
    """The batch in the golden-set upload's shape, for a person to add expected answers to."""
    rows = scenarios.generate(body.space, count=body.count, seed=body.seed,
                              challenge_ratio=body.challenge_ratio)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(CSV_HEADER)
    for s in rows:
        note = (f"space={s.space} engine={s.engine} role={s.role} style={s.style} "
                f"difficulty={s.difficulty}" + ("" if s.in_module else " OUT-OF-MODULE: must be refused"))
        w.writerow([s.scenario_id, s.question, "", "judgment", "", note])
    name = f"scenarios-{body.space}-{body.seed}.csv"
    return Response(content=buf.getvalue(), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})


class RunRequest(BaseModel):
    space: Literal["ops", "comp", "assets", "full"]
    #: Small on purpose. A full batch belongs to the golden runner's job and step loop; this
    #: answers a handful in one request so the loop can be watched end to end.
    count: int = Field(2, ge=1, le=scenario_run.MAX_QUESTIONS)
    seed: int = Field(0, ge=0, le=10_000_000)
    challenge_ratio: float = Field(0.0, ge=0.0, le=0.5)
    #: Where in the batch to start, so a batch can be worked through a few at a time rather than
    #: the same first few being asked again.
    offset: int = Field(0, ge=0, le=1000)
    #: How many the batch holds. The whole batch is generated and then sliced, because a shorter
    #: batch is a different batch when any of it is challenge questions.
    batch: int = Field(0, ge=0, le=100)


@router.post("/scenarios/run")
async def run_scenarios(request: Request, body: RunRequest,
                        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """Ask the generated questions for real, in the instance the batch names.

    Each one is a real chat turn: a model is called and it costs money, which is why the count is
    capped. What came back is written to the tables the review page already reads, so the batch
    appears in Runs with its answers and steps. A write that fails does not lose the answers --
    `persisted` says so in every reply rather than leaving the caller to assume.

    `offset` and `batch` are how a batch is worked through a few at a time: the whole batch is
    generated from the seed and then sliced, so asking 6 to 10 asks THOSE questions rather than
    regenerating a shorter batch, which would be a different five.
    """
    try:
        out = await scenario_run.run(
            body.space, count=body.count, seed=body.seed, challenge_ratio=body.challenge_ratio,
            offset=body.offset, batch=body.batch,
            principal=who.principal, organization_id=who.org,
            authorization=request.headers.get("authorization"))
    except scenario_run.NotSafe as exc:
        # The same refusal the golden runner gives: a question is never asked from a process
        # that could write something.
        raise db.http_error(409, "not_safe", "This process is not isolated enough to run questions.",
                            problems=exc.problems)
    except ValueError as exc:
        raise db.http_error(400, "bad_request", str(exc))

    # Written as one batch, into the tables the review page already reads: the run appears in Runs
    # beside every other, and each question opens with its answer, its steps and a place to say
    # what the answer should have been. A write that fails must not lose the answers, which cost
    # real money to get, so it is reported beside them rather than raised.
    captured = out.pop("captured", [])
    if captured:
        try:
            out.update(await scenario_store.save({**out, "captured": captured},
                                                 organization_id=who.org, principal=who.principal))
            out["persisted"] = True
        except Exception as exc:                  # noqa: BLE001 - the answers are still returned
            out["persisted"] = False
            out["save_error"] = str(exc)[:300]
    return {"ok": True, **out}


@router.get("/learning/draft")
async def learning_draft(run: str | None = None, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """What reviewers have left, drafted into markdown with its evidence.

    It applies nothing. The documents the agents read live in plenum_cafm, which this service may
    never write (G0), so the draft is for a person to read and put into a skill document through
    the Skill lab. That is also the right shape on its own merits: an annotation is one reviewer's
    opinion, and a loop that taught the agents straight from opinions would drift with whoever
    reviewed last.
    """
    found = await learning.gather(who.org, run_code=run)
    return {"ok": True, "counts": found["counts"], "markdown": learning.draft(found),
            "applies_anything": False}


@router.get("/learning/draft.md")
async def learning_draft_file(run: str | None = None, who: Reviewer = Depends(reviewer)) -> Response:
    """The same draft as a file, so it can be opened beside the skill document it is about."""
    found = await learning.gather(who.org, run_code=run)
    return Response(content=learning.draft(found), media_type="text/markdown",
                    headers={"Content-Disposition": 'attachment; filename="what-the-reviews-say.md"'})


@router.get("/lab/companies")
async def list_companies(who: Any = Depends(reader)) -> dict[str, Any]:
    """The companies this caller may work in, for the picker.

    The lab had no way to choose one: the company was taken from whichever run happened to be
    open, so the platform view -- which opens on "All runs" -- had none, and asking a question was
    refused with a sentence about viewing (3 Nov 2026).

    A company admin gets their own and nothing else. The platform team and a consultant get every
    company, read from plenum_cafm through the read-only engine: Trace Lab reads that database and
    never writes it (G0).
    """
    from sqlalchemy import text as _text

    from . import conversations

    principal, org = who
    own = getattr(principal, "organization_id", None)
    if str(getattr(principal, "role", "")) != "superadmin":
        # Not a superadmin: their own company, whatever they asked for.
        if not own:
            return {"ok": True, "companies": []}
        async with conversations._read() as conn:
            name = (await conn.execute(_text(
                "SELECT name FROM plenum_cafm.organizations WHERE id = CAST(:o AS uuid)"),
                {"o": str(own)})).scalar()
        return {"ok": True, "companies": [{"id": str(own), "name": name or "Your company"}]}

    async with conversations._read() as conn:
        rows = (await conn.execute(_text(
            "SELECT id::text AS id, name FROM plenum_cafm.organizations"
            " WHERE name IS NOT NULL ORDER BY lower(name) LIMIT 500"))).mappings().all()
    return {"ok": True, "companies": [{"id": r["id"], "name": r["name"]} for r in rows]}
