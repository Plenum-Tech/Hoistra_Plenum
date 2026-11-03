"""Synthetic question batches, one role space at a time.

An evaluation batch has to be asked somewhere. A question asked in Ops Brain cannot reach the
compliance engine (svc-deepagents agents/spaces.py), so "50-100 synthetic questions" is not one
set: it is a set per space, and a batch generated without knowing its space will contain
questions the space refuses. A refusal is a correct answer there, which makes the batch measure
the gate rather than the engines, and nothing in the result says which happened.

So generation starts from the space. Each space names the domain engines it may use; each engine
has questions a facilities manager actually asks of it; every generated question is then CHECKED
against the real router before it is handed out, and one that would route outside its own space
is dropped rather than shipped. That check is the point of this module -- without it the
generator is a list of strings somebody has to read.

Pure: no database, no model, no network. Seeded, so a batch is reproducible and a failure can be
re-run exactly. The dimensions follow docs/superpowers/skills/scenario-generation/SKILL.md --
role, goal, difficulty, writing style -- in the small: that skill describes a 250-scenario batch
with grounding in real rows, which needs a database and a person's review pass. This is the part
that can be generated and verified with neither.

G0: imports svc-deepagents, changes nothing in it.
"""
from __future__ import annotations

import hashlib
import random
from dataclasses import asdict, dataclass
from typing import Any, Iterable

#: Every space a batch can be generated for. "maintenance" is not a space of its own -- planned
#: and reactive maintenance is the work-order engine, which Ops Brain and the Assets Operating
#: System both have; ask for "ops" or "assets".
SPACES = ("ops", "comp", "assets", "full")

#: Questions a facilities manager asks of each domain engine, as they would actually type them.
#: Grouped by the engine that should answer, which is what makes the space check below possible.
BY_ENGINE: dict[str, tuple[str, ...]] = {
    "wo_engine": (
        "what is open across my buildings this week",
        "is planned maintenance running to plan",
        "which work orders breached sla",
        "list overdue work orders",
        "show the ppm deviations",
        "which inspection recommendations were never actioned",
        "what needs my approval today",
        "how many jobs did we close last month",
        "which building raises the most reactive work",
        "what is still open from last quarter",
    ),
    "contract_performance": (
        "which vendor is costing me money",
        "is each vendor inside its sla",
        "what service credit can i claim",
        "which vendors are blocked right now",
        "who can replace a blocked vendor",
        "which contracts come up for renewal",
        "what did we spend with each contractor this year",
        "which vendor misses its response times most",
    ),
    "compliance": (
        "which buildings are out of compliance today",
        "what certificates lapse in the next 30 days",
        "which certificates failed authenticity checks",
        "which vendors are not eligible to work here",
        "what documents evidence each certificate",
        "which approvals expire this month",
        "when is the next loler inspection due",
        "show the eicr certificate for each building",
    ),
    "energy_intelligence": (
        "which assets are losing value",
        "what is energy telling me about asset health",
        "which assets should i remediate first",
        "what will break in the next 90 days",
        "which building uses the most energy per square metre",
        "where is consumption rising fastest",
    ),
}

#: Who is asking. From the skill's Role dimension.
ROLES = ("operative", "building manager", "vendor", "consultant", "superadmin")

#: How the question is typed. The point of these is that a real question is rarely tidy.
STYLES = ("neutral", "short", "typos", "rambling", "frustrated")

#: How hard the question is to answer, from the skill's Difficulty dimension.
DIFFICULTIES = ("clear", "vague", "missing information", "on a limit")


@dataclass(frozen=True)
class Scenario:
    """One generated question, and everything needed to judge the answer afterwards."""
    scenario_id: str
    space: str
    engine: str
    question: str
    role: str
    style: str
    difficulty: str
    #: False for a challenge scenario: asked in a space that must REFUSE it. Kept deliberately,
    #: because a gate that stops working is a silent failure otherwise.
    in_module: bool = True

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _rough(question: str, style: str, rng: random.Random) -> str:
    """The same question as somebody would really type it."""
    if style == "short":
        words = question.split()
        return " ".join(words[:4]) if len(words) > 4 else question
    if style == "typos":
        out = list(question)
        for _ in range(max(1, len(out) // 25)):
            i = rng.randrange(len(out))
            if out[i].isalpha():
                out[i] = ""
        return "".join(out)
    if style == "rambling":
        return ("sorry quick one, I know you get asked this a lot but " + question
                + " - just need it for the morning meeting")
    if style == "frustrated":
        return question + "? this is the third time I am asking"
    return question


def engines_of(space: str) -> tuple[str, ...]:
    """The engines a space may use, in a fixed order so a seed reproduces a batch."""
    from src.agents.spaces import engines_for

    allowed = engines_for(space)
    names = sorted(BY_ENGINE) if allowed is None else sorted(e for e in BY_ENGINE if e in allowed)
    return tuple(names)


def routes_to(question: str) -> str:
    """Where the real router sends this question. The generator never guesses it."""
    from src.agents.phase2_intents import resolve_phase2_engine

    return resolve_phase2_engine(user_message=question, context_note=None)


def generate(space: str, *, count: int = 50, seed: int = 0,
             challenge_ratio: float = 0.0) -> list[Scenario]:
    """A batch for one space: `count` questions it should answer, verified against the router.

    A question whose ROUTED engine is not one this space has is dropped, not shipped -- it would
    be refused, and a refusal scored as an answer measures the gate. challenge_ratio adds
    deliberate out-of-module questions, marked in_module=False, for checking the gate itself.
    """
    from src.agents.spaces import allows

    if space not in SPACES:
        raise ValueError(f"unknown space {space!r}; one of {SPACES}")
    rng = random.Random(f"{space}:{seed}")
    mine = engines_of(space)
    if not mine:
        return []

    out: list[Scenario] = []
    seen: set[str] = set()
    n_challenge = int(count * challenge_ratio)
    n_main = count - n_challenge

    # -- the questions this space is for -------------------------------------------------
    attempts = 0
    while len(out) < n_main and attempts < n_main * 60:
        attempts += 1
        engine = mine[len(out) % len(mine)]
        base = rng.choice(BY_ENGINE[engine])
        style = rng.choice(STYLES)
        question = _rough(base, style, rng)
        if question in seen:
            continue
        # The check that makes this a generator rather than a list. Both halves matter: the
        # router has to send it to a domain engine this space HAS, and the space has to allow
        # that engine. A rough-typed question that no longer routes anywhere is dropped too.
        routed = routes_to(question)
        if routed not in mine or not allows(space, routed):
            continue
        seen.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=routed, question=question,
            role=rng.choice(ROLES), style=style, difficulty=rng.choice(DIFFICULTIES)))

    # -- questions the space must refuse ---------------------------------------------------
    outside = sorted(e for e in BY_ENGINE if e not in mine)
    attempts = 0
    while len(out) < count and outside and attempts < n_challenge * 60 + 60:
        attempts += 1
        engine = outside[len(out) % len(outside)]
        question = rng.choice(BY_ENGINE[engine])
        if question in seen:
            continue
        if routes_to(question) != engine or allows(space, engine):
            continue
        seen.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=engine, question=question,
            role=rng.choice(ROLES), style="neutral", difficulty="clear", in_module=False))
    return out


def _sid(space: str, seed: int, i: int) -> str:
    return f"{space.upper()}-{seed}-{i + 1:03d}"


def batch(space: str, *, count: int = 50, seed: int = 0,
          challenge_ratio: float = 0.0) -> dict[str, Any]:
    """A generated batch as the golden-set upload takes it: one row per question."""
    rows = generate(space, count=count, seed=seed, challenge_ratio=challenge_ratio)
    return {
        "space": space, "seed": seed, "requested": count, "generated": len(rows),
        "engines": list(engines_of(space)),
        "by_engine": {e: sum(1 for r in rows if r.engine == e) for e in engines_of(space)},
        "out_of_module": sum(1 for r in rows if not r.in_module),
        "scenarios": [r.as_dict() for r in rows],
        "fingerprint": fingerprint(rows),
    }


def fingerprint(rows: Iterable[Scenario]) -> str:
    """Twelve hex of the batch, so a run can say which batch it measured."""
    h = hashlib.sha256()
    for r in rows:
        h.update((r.scenario_id + "|" + r.question + "|" + r.engine).encode("utf-8"))
    return h.hexdigest()[:12]
