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

#: Questions that need MORE THAN ONE area to answer, which is how a facilities manager actually
#: asks. "What is this work order's status, did it meet its response SLA, and how has that vendor
#: been performing here overall?" is one question and three lookups, and the thing worth measuring
#: is whether the answer carries all three parts and ties them together -- a reply that answers
#: the first and quietly drops the other two looks fine and is wrong.
#:
#: `needs` is the areas the question spans. It is offered only to an instance that has ALL of
#: them: asked anywhere else it would be a refusal, or worse, a half-answer that reads as a whole
#: one. `covers` is what a good answer has to address, which is what a reviewer judges against --
#: it is NOT an expected answer, which only a person reading real rows can write.
CROSS_DOMAIN: tuple[dict[str, Any], ...] = (
    {"question": "take the work order that last breached its response SLA - what is its status now, "
                 "did it meet the SLA, and how has the vendor who attended been performing at that "
                 "building overall",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the work order's current status; whether the response SLA was met; that vendor's "
               "performance at that building, not across the portfolio"},
    {"question": "which inspection recommendations are still open, and has anyone raised work orders "
                 "against them",
     "needs": ("compliance", "wo_engine"),
     "covers": "the open recommendations; whether each has work raised against it; the ones with "
               "nothing raised named explicitly"},
    {"question": "for the vendor with the most lapsed certificates, how are they performing against "
                 "their contract and are they still inside sla",
     "needs": ("compliance", "contract_performance"),
     "covers": "which vendor; what has lapsed; their contract performance and SLA position, with "
               "the two set against each other rather than listed"},
    # Worded to lead with energy on purpose: "where is consumption rising fastest, and what are we
    # paying the contractor who maintains the plant there" routes to the WORK-ORDER engine, which
    # owns no part of it. The router reads the opening, so a cross-domain question has to open on
    # an area it actually needs.
    {"question": "where is energy consumption rising fastest and what do we pay that building's "
                 "contractor",
     "needs": ("energy_intelligence", "contract_performance"),
     "covers": "where consumption is rising; the contractor responsible for that plant; the spend, "
               "tied to that building rather than the whole contract"},
    {"question": "the building using the most energy per square metre - what reactive work has it "
                 "raised this quarter, and is any of it plant that would explain the consumption",
     "needs": ("energy_intelligence", "wo_engine"),
     "covers": "the building; its reactive work this quarter; which of that work is plant, and "
               "whether it accounts for the consumption or does not"},
    {"question": "take the building with the most overdue work orders - what is open there, are its "
                 "vendors inside sla, and how does its energy use compare with the rest",
     "needs": ("wo_engine", "contract_performance", "energy_intelligence"),
     "covers": "which building; its open work; its vendors' SLA position; its energy use against "
               "the portfolio, as a comparison and not a bare figure"},
    {"question": "how many jobs did we close last month, and did the vendors who did them meet their sla",
     "needs": ("wo_engine", "contract_performance"),
     "covers": "the count closed; the vendors who did them; each one's SLA position, not a single "
               "portfolio average"},
    {"question": "which contracts come up for renewal, and do those vendors have any lapsed "
                 "certificates",
     "needs": ("compliance", "contract_performance"),
     "covers": "the contracts due for renewal; each of those vendors' lapsed certificates; the "
               "renewals that carry a compliance problem called out as such"},
    {"question": "who can replace a blocked vendor, and what is that replacement costing us under "
                 "contract",
     "needs": ("compliance", "contract_performance"),
     "covers": "which vendor is blocked and why; who can replace them; what the replacement costs "
               "against the blocked vendor's contract"},
    {"question": "for any certificate lapsing in the next 30 days, has remedial work been raised and "
                 "is the vendor responsible inside their sla",
     "needs": ("compliance", "wo_engine", "contract_performance"),
     "covers": "the certificates lapsing; whether remedial work exists for each; the responsible "
               "vendor's SLA position; the ones with no work raised called out"},
)

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
    #: The areas a cross-domain question spans, empty for a single-area one. A reviewer needs this
    #: to judge the answer: one that covers the first part well and drops the rest reads as a good
    #: answer and is a failure.
    needs: tuple[str, ...] = ()
    #: What a good answer has to address. NOT an expected answer -- only a person reading real
    #: rows can write one of those -- but what to check it against.
    covers: str = ""

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
    # The BASE question, not the typed-out one. Dedup on the surface form let the same question
    # through three times in one batch of ten -- once plainly, once with a typo, once rambling --
    # because "is each vendor inside its sla", "is each endor inside its sla" and "sorry quick
    # one, ... is each vendor inside its sla" are three different strings and one question. A
    # reviewer reading the same question three times is being asked to grade one answer three
    # times, which is worse than a short batch.
    used: set[str] = set()
    seen: set[str] = set()
    n_challenge = int(count * challenge_ratio)
    n_main = count - n_challenge

    # Styles and roles are DEALT from a shuffled deck, not drawn independently. Independent draws
    # gave a batch of ten with three questions ending "this is the third time I am asking" and two
    # opening "sorry quick one, I know you get asked this a lot but" -- which reads as padding and
    # measures one writing style three times instead of five styles twice.
    styles = _deck(STYLES, rng)
    roles = _deck(ROLES, rng)
    hardness = _deck(DIFFICULTIES, rng)

    # -- the questions this space is for -------------------------------------------------
    #
    # One pass over the pool, dealt round-robin across the engines, rather than random draws with
    # an attempt budget. Random draws plus "skip a base already used" meant that once one engine's
    # questions were spent, the position that engine's turn fell on could never be filled, and the
    # batch came back short while unused questions sat in the other engines.
    pool: dict[str, list[str]] = {}
    for e in mine:
        deck = list(BY_ENGINE[e])
        rng.shuffle(deck)
        pool[e] = deck
    candidates: list[tuple[str, str]] = []
    while any(pool.values()):
        for e in mine:
            if pool[e]:
                candidates.append((e, pool[e].pop()))

    # Cross-domain questions FIRST, and only those whose every area this instance has. They are
    # the ones worth asking -- a single lookup is easy and a question with three parts is where an
    # answer quietly drops two of them -- so a batch too small to hold everything holds these.
    cross = [c for c in CROSS_DOMAIN if all(e in mine and allows(space, e) for e in c["needs"])]
    rng.shuffle(cross)
    # Not the whole batch: a batch of only multi-part questions stops measuring the plain ones
    # that make up most of what people ask. Roughly a third, and at least one where any fit.
    room = max(1, round(n_main / 3)) if cross else 0
    for c in cross[:room]:
        if len(out) >= n_main:
            break
        question = c["question"]
        if question in seen:
            continue
        routed = routes_to(question)
        # Stricter than for a single question: it must land in an area the question NEEDS. Routed
        # anywhere else, the engine that picks it up owns no part of the question, and the answer
        # measures the router rather than the engines.
        if routed not in c["needs"] or not allows(space, routed):
            continue
        seen.add(question)
        used.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=routed, question=question,
            role=roles(len(out)), style="neutral", difficulty="on a limit",
            needs=tuple(c["needs"]), covers=c["covers"]))

    for engine, base in candidates:
        if len(out) >= n_main:
            break
        if base in used:
            continue
        # The style for this position, with the plain question as a fallback. Roughening can break
        # routing -- dropped letters especially -- and the question matters more than the typing,
        # so a style that will not route gives way to the plain one rather than losing the
        # question.
        routed = None
        chosen = "neutral"
        for style in (styles(len(out)), "neutral"):
            question = _rough(base, style, rng)
            if question in seen:
                continue
            # The check that makes this a generator rather than a list. Both halves matter: the
            # router has to send it to a domain engine this space HAS, and the space has to allow
            # that engine.
            cand = routes_to(question)
            if cand in mine and allows(space, cand):
                routed, chosen = cand, style
                break
        if routed is None:
            continue
        seen.add(question)
        used.add(base)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=routed, question=question,
            role=roles(len(out)), style=chosen, difficulty=hardness(len(out))))

    # -- questions the space must refuse ---------------------------------------------------
    # Only as many as were ASKED for. This used to run until the batch reached `count`, so a main
    # loop that came up short was padded with questions the instance must REFUSE -- an Ops batch
    # requested with no refusals came back as seven refusals, every answer a refusal, which reads
    # as the engine failing rather than the generator running out.
    outside = sorted(e for e in BY_ENGINE if e not in mine)
    want = len(out) + n_challenge
    attempts = 0
    while len(out) < want and outside and attempts < n_challenge * 60 + 60:
        attempts += 1
        engine = outside[len(out) % len(outside)]
        question = rng.choice(BY_ENGINE[engine])
        if question in seen or question in used:
            continue
        if routes_to(question) != engine or allows(space, engine):
            continue
        seen.add(question)
        used.add(question)
        out.append(Scenario(
            scenario_id=_sid(space, seed, len(out)), space=space, engine=engine, question=question,
            role=roles(len(out)), style="neutral", difficulty="clear", in_module=False))
    return out


def _deck(values: tuple[str, ...], rng: random.Random):
    """Deal `values` evenly, shuffling again each time the deck runs out.

    Every value appears once before any appears twice, so a batch of ten over five styles is two
    of each rather than whatever five independent draws happened to give. The shuffle keeps the
    order unpredictable and keeps it a function of the seed, so a batch is still reproducible.
    """
    order: list[str] = []

    def take(i: int) -> str:
        while len(order) <= i:
            nxt = list(values)
            rng.shuffle(nxt)
            order.extend(nxt)
        return order[i]

    return take


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
        #: How many need more than one area. A batch with none of these measures only the easy
        #: half of what people ask.
        "cross_domain": sum(1 for r in rows if r.needs),
        # The ceiling, so "7 of 25" reads as "this instance has seven" rather than as a failure.
        # Every question is used at most once in a batch, so the pool IS the largest batch.
        "pool": sum(len(BY_ENGINE[e]) for e in engines_of(space)),
        "scenarios": [r.as_dict() for r in rows],
        "fingerprint": fingerprint(rows),
    }


def fingerprint(rows: Iterable[Scenario]) -> str:
    """Twelve hex of the batch, so a run can say which batch it measured."""
    h = hashlib.sha256()
    for r in rows:
        h.update((r.scenario_id + "|" + r.question + "|" + r.engine).encode("utf-8"))
    return h.hexdigest()[:12]
