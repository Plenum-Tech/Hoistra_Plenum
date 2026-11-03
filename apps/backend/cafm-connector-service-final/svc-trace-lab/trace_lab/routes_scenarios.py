"""Generate a synthetic batch for one role space.

    POST /scenarios/generate     a batch of questions for one space, verified against the router
    POST /scenarios/run          ask a few of them for real and return the answers
    GET  /scenarios/spaces       the spaces that can be generated for, and the engines each has
    GET  /scenarios/template.csv the batch as the golden-set upload takes it

Who: a reviewer of the company (routes_conversations.reviewer), the same gate as every other
golden route -- plain users never open Trace Lab. Generation calls no model and touches no
database: it is deterministic from (space, seed), so the same batch can be produced again to
re-run a failure exactly.

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

from . import db, scenario_run, scenarios
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
async def list_spaces(who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """Each space, what it is called, and which engines a batch for it can use."""
    from src.agents.spaces import SPACE_NAMES

    return {"ok": True, "spaces": [
        {"key": s, "name": SPACE_NAMES.get(s, s), "engines": list(scenarios.engines_of(s))}
        for s in scenarios.SPACES
    ]}


@router.post("/scenarios/generate")
async def generate(body: GenerateRequest, who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """A batch for one space. Every question is put to the real router before it is returned, and
    one that would route outside its own space is dropped rather than shipped."""
    out = scenarios.batch(body.space, count=body.count, seed=body.seed,
                          challenge_ratio=body.challenge_ratio)
    # Says so when the router could not fill the batch, instead of quietly returning fewer.
    out["short"] = out["generated"] < out["requested"]
    return {"ok": True, **out}


@router.post("/scenarios/template.csv")
async def template_csv(body: GenerateRequest, who: Reviewer = Depends(reviewer)) -> Response:
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


@router.post("/scenarios/run")
async def run_scenarios(request: Request, body: RunRequest,
                        who: Reviewer = Depends(reviewer)) -> dict[str, Any]:
    """Ask the generated questions for real, in the instance the batch names.

    Each one is a real chat turn: a model is called and it costs money, which is why the count is
    capped. The results are returned, NOT stored -- keeping them needs a scenario table, and a
    Trace Lab migration is applied by a person. `persisted` says so in every reply rather than
    leaving the caller to assume.
    """
    try:
        out = await scenario_run.run(
            body.space, count=body.count, seed=body.seed, challenge_ratio=body.challenge_ratio,
            principal=who.principal, organization_id=who.org,
            authorization=request.headers.get("authorization"))
    except scenario_run.NotSafe as exc:
        # The same refusal the golden runner gives: a question is never asked from a process
        # that could write something.
        raise db.http_error(409, "not_safe", "This process is not isolated enough to run questions.",
                            problems=exc.problems)
    except ValueError as exc:
        raise db.http_error(400, "bad_request", str(exc))
    return {"ok": True, **out}
