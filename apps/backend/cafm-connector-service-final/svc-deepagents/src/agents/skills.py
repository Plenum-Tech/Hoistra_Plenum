"""Skill registry — the orchestrator's map from a user question to one agent.

Each sub-agent owns a ``skills/<slug>/SKILL.md`` file holding its behavioural contract: the
tables it reads, the join keys out of them, the recipes for turning a question into rows, and
the shape of the summary it returns. Two things read those files:

* the **orchestrator**, which matches the question against every skill's triggers and routes to
  the winning agent (``select_skill`` in ``meta_tools``);
* the **sub-agent itself**, which runs with its skill as its system prompt
  (``agent_system_prompt``), so the contract that chose it is the contract it executes.

Keeping both on one file is the point. A routing table in code and a prompt in a string
drift apart; a routing table derived FROM the prompt cannot.

``skills/query-builder/SKILL.md`` is marked ``shared: true`` — it is prepended to every agent's
prompt and never routed to on its own.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

_DEFAULT_DIR = Path(__file__).resolve().parents[2] / "skills"

#: The agent that answers anything no specialist claims. It is ranked like any other skill but
#: never outranks a specialist — see ``select_skills``.
FALLBACK_AGENT = "udr"

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class Skill:
    """One SKILL.md: its routing metadata and its body."""

    slug: str
    name: str
    agent: str
    description: str
    triggers: tuple[str, ...] = ()
    shared: bool = False
    body: str = ""
    path: str = ""

    @property
    def is_routable(self) -> bool:
        return not self.shared and bool(self.agent) and self.agent != "shared"


@dataclass
class SkillMatch:
    """A skill the question matched, and why."""

    skill: Skill
    score: float
    matched: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "agent": self.skill.agent,
            "skill": self.skill.name,
            "score": round(self.score, 2),
            "matched_triggers": self.matched,
            "description": self.skill.description,
        }


def skills_dir() -> Path:
    """Where the SKILL.md files live. ``SKILLS_DIR`` overrides for tests and containers."""
    return Path(os.getenv("SKILLS_DIR") or _DEFAULT_DIR)


# ──────────────────────────────────────────────────────────────────────────────
# Parsing
# ──────────────────────────────────────────────────────────────────────────────

def _parse_front_matter(text: str) -> tuple[dict, str]:
    """Split ``---`` front matter from the body.

    Deliberately a small hand-rolled reader rather than a YAML dependency: the front matter is
    a fixed shape (scalars plus one list of strings) and a missing PyYAML must not be able to
    stop the orchestrator from loading its own skills.
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    head = text[3:end].strip("\n")
    body = text[end + 4 :].lstrip("\n")

    meta: dict = {}
    current_list: str | None = None
    for raw in head.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        if current_list and line.lstrip().startswith("- "):
            meta[current_list].append(line.lstrip()[2:].strip().strip("\"'"))
            continue
        current_list = None
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value in ("", "[]"):
            meta[key] = []
            current_list = key if value == "" else None
            if value == "[]":
                current_list = None
            continue
        meta[key] = value.strip("\"'")
    return meta, body


def _as_bool(value) -> bool:
    return str(value).strip().lower() in {"true", "yes", "1"}


def _load_one(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        log.warning("skills.unreadable", path=str(path), error=str(exc))
        return None
    meta, body = _parse_front_matter(text)
    name = str(meta.get("name") or path.parent.name)
    agent = str(meta.get("agent") or "")
    triggers = meta.get("triggers") or []
    if isinstance(triggers, str):
        triggers = [t.strip() for t in triggers.split(",") if t.strip()]
    return Skill(
        slug=path.parent.name,
        name=name,
        agent=agent,
        description=str(meta.get("description") or "").strip(),
        triggers=tuple(t.lower() for t in triggers if t),
        shared=_as_bool(meta.get("shared")) or agent == "shared",
        body=body.strip(),
        path=str(path),
    )


@lru_cache(maxsize=1)
def load_skills() -> tuple[Skill, ...]:
    """Every SKILL.md under ``skills/``, sorted by slug. Cached for the process."""
    root = skills_dir()
    found: list[Skill] = []
    if not root.is_dir():
        log.warning("skills.dir_missing", path=str(root))
        return ()
    for path in sorted(root.glob("*/SKILL.md")):
        skill = _load_one(path)
        if skill is not None:
            found.append(skill)
    log.info("skills.loaded", count=len(found), path=str(root))
    return tuple(found)


def reload_skills() -> tuple[Skill, ...]:
    """Drop the cache and re-read from disk. For tests and hot reload."""
    load_skills.cache_clear()
    return load_skills()


def shared_skill() -> Skill | None:
    for skill in load_skills():
        if skill.shared:
            return skill
    return None


def routable_skills() -> tuple[Skill, ...]:
    return tuple(s for s in load_skills() if s.is_routable)


def skill_for_agent(agent: str) -> Skill | None:
    for skill in load_skills():
        if skill.is_routable and skill.agent == agent:
            return skill
    return None


# ──────────────────────────────────────────────────────────────────────────────
# Routing
# ──────────────────────────────────────────────────────────────────────────────

def _stem(token: str) -> str:
    """Crude plural fold, applied to both sides so they agree.

    "open urgent work orders" must match the trigger "work order". Only words longer than
    three characters lose a trailing "s", which keeps "gas" as "gas".
    """
    return token[:-1] if len(token) > 3 and token.endswith("s") else token


def _normalise(text: str) -> str:
    return " " + " ".join(_stem(t) for t in _WORD_RE.findall(text.lower())) + " "


def _trigger_hit(haystack: str, trigger: str) -> bool:
    """Whole-word containment, so ``wo`` does not match ``work`` and ``gas`` not ``gasket``."""
    needle = _normalise(trigger)
    return needle.strip() != "" and needle in haystack


def select_skills(question: str) -> list[SkillMatch]:
    """Rank skills against a question, most relevant first.

    A trigger scores its own word count, so a specific phrase ("baseline drift", "first fix")
    outweighs a generic word ("due", "score") that happens to appear in several domains.

    The fallback agent is ranked like any other, but never returned ahead of a specialist that
    scored: UDR exists to answer what no engine owns, and a generic opener like "show me" must
    not pull a certificate question away from Compliance.
    """
    haystack = _normalise(question or "")
    matches: list[SkillMatch] = []
    for skill in routable_skills():
        hits = [t for t in skill.triggers if _trigger_hit(haystack, t)]
        if not hits:
            continue
        score = sum(len(_WORD_RE.findall(t)) for t in hits)
        matches.append(SkillMatch(skill=skill, score=float(score), matched=sorted(hits)))

    matches.sort(key=lambda m: (-m.score, m.skill.agent))
    specialists = [m for m in matches if m.skill.agent != FALLBACK_AGENT]
    fallback = [m for m in matches if m.skill.agent == FALLBACK_AGENT]
    return specialists + fallback


def route(question: str) -> dict:
    """The routing decision the orchestrator acts on.

    Returns the primary agent, the other domains the question also touches (so a cross-domain
    ask fans out instead of answering half), and the primary skill's routing guidance.
    """
    matches = select_skills(question)
    if not matches:
        fallback = skill_for_agent(FALLBACK_AGENT)
        return {
            "primary_agent": FALLBACK_AGENT,
            "skill": fallback.name if fallback else FALLBACK_AGENT,
            "confidence": "fallback",
            "reason": (
                "No skill trigger matched. Routing to the universal database reader, which "
                "resolves names to keys and reads any plenum_cafm table."
            ),
            "matched_triggers": [],
            "also_relevant": [],
            "clarify_first": True,
            "skill_summary": fallback.description if fallback else "",
        }

    primary = matches[0]
    others = [m for m in matches[1:] if m.score >= 1]
    confidence = "high" if primary.score >= 3 else "medium" if primary.score >= 2 else "low"
    return {
        "primary_agent": primary.skill.agent,
        "skill": primary.skill.name,
        "confidence": confidence,
        "reason": (
            f"Matched {primary.skill.name} on: {', '.join(primary.matched)}."
        ),
        "matched_triggers": primary.matched,
        "also_relevant": [m.as_dict() for m in others],
        "clarify_first": False,
        "skill_summary": primary.skill.description,
    }


# ──────────────────────────────────────────────────────────────────────────────
# Prompt assembly
# ──────────────────────────────────────────────────────────────────────────────

def agent_system_prompt(agent: str, extra: str | None = None) -> str | None:
    """The system prompt for one sub-agent: shared query discipline, then its own skill.

    ``extra`` is an existing in-code contract (Compliance has one) and is placed between the
    two — it keeps precedence on tool routing while the skill supplies the data layer.
    Returns ``None`` when nothing is defined, so callers can leave the agent's default prompt
    alone rather than handing it an empty string.
    """
    parts: list[str] = []
    shared = shared_skill()
    if shared:
        parts.append(shared.body)
    if extra:
        parts.append(extra.strip())
    own = skill_for_agent(agent)
    if own:
        parts.append(own.body)
    if not parts:
        return None
    return "\n\n---\n\n".join(parts)


@lru_cache(maxsize=64)
def prompt_doc(agent: str, name: str) -> str:
    """One named instruction file from an agent's skill directory, front matter stripped.

    Behavioural contracts used to live as Python string literals — 19,311 chars of them in
    orchestrator.py alone, which meant the people who write compliance policy could not read
    or change the instructions the model actually runs on, and the same rule ended up stated
    in three places that then drifted.

    Missing files raise. A prompt is not optional decoration: an agent silently running on an
    empty contract produces confident answers with nothing behind them, which is far worse
    than a service that refuses to start and names the file it wanted.
    """
    path = skills_dir() / agent / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(
            f"Required prompt document {agent}/{name}.md is missing or unreadable "
            f"({path}): {exc}. The agent cannot run without its contract."
        ) from exc
    _meta, body = _parse_front_matter(text)
    body = body.strip()
    if not body:
        raise RuntimeError(
            f"Prompt document {agent}/{name}.md is empty. Refusing to run an agent on an "
            f"empty contract."
        )
    return body


def skill_index_markdown() -> str:
    """The routing table injected into the orchestrator's system prompt."""
    rows = ["| Skill | Agent | Route here when |", "|-------|-------|-----------------|"]
    for skill in routable_skills():
        rows.append(f"| `{skill.name}` | `{skill.agent}` | {skill.description} |")
    return "\n".join(rows)
