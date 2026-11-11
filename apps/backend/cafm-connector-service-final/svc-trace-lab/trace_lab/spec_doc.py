"""The specification as sections, and what is out of date because of it (11 Nov 2026, task plan B2.3).

A scenario's expected outcome is written from the specification. When the specification changes, an expected outcome
written from the old text may be wrong, and nothing would say so. So the specification is read as sections: every
heading of level two to four is one, with an id derived from the heading and the sha256 of its text. A scenario is
stamped with the hash of each section it cites when it is stored (scenario_file.py); it is OUT OF DATE when a cited
section's hash is different today, or the section is gone.

    "## Retry rules"                          -> retry_rules
    "### A2.3 Context passing"                -> context_passing        (leading numbering is not part of the id)
    "### Anything {#context_passing}"         -> context_passing        (an explicit id wins)

Whitespace is not a change: line endings, trailing spaces and runs of blank lines are normalised before hashing. A
section holds the sections under it, so a change deep down also changes its parents.

Nothing is rewritten. Each load of a changed specification is new rows (tracelab.spec_sections is append-only); the
current specification is the newest load. Bringing a scenario up to date is storing or approving it again, which
stamps the specification as it reads then.

The file is read from TRACE_LAB_SPEC_PATH, else svc-deepagents/skills/spec/SPEC.md beside this service.
G0: reads one file, writes only Trace Lab's own table.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import text as sa_text

from . import conversations, db, labelling, runs
from .review_errors import Invalid

ENV = "TRACE_LAB_SPEC_PATH"
_HEADING = re.compile(r"^(#{2,4})\s+(.+?)\s*$")
_EXPLICIT = re.compile(r"\{#([A-Za-z0-9_.-]+)\}\s*$")
_NUMBERING = re.compile(r"^[A-Za-z]?\d+(?:\.\d+)*[.)]?\s+")
_FENCE = re.compile(r"^\s*(```|~~~)")
BODY_MAX = 200_000


@dataclass(frozen=True)
class Section:
    section_id: str
    title: str
    body: str
    sha256: str


def spec_path(env: Mapping[str, str] | None = None) -> Path:
    """Where the specification is read from: TRACE_LAB_SPEC_PATH, else svc-deepagents' skills/spec/SPEC.md."""
    raw = ((os.environ if env is None else env).get(ENV) or "").strip()
    if raw:
        return Path(raw)
    return Path(__file__).resolve().parents[2] / "svc-deepagents" / "skills" / "spec" / "SPEC.md"


def _slug(words: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", words.lower()).strip("_")


def section_id_of(heading: str) -> str:
    """The id of the section a heading starts: its explicit {#id} when it has one, else its words, lower-cased, with
    leading numbering dropped and everything that is not a letter or digit turned into an underscore."""
    h = str(heading or "").strip()
    explicit = _EXPLICIT.search(h)
    if explicit:
        return explicit.group(1).lower()
    return _slug(_NUMBERING.sub("", h)) or _slug(h) or "section"


def _lines(text_: str) -> list[str]:
    return [line.rstrip() for line in text_.replace("\r\n", "\n").replace("\r", "\n").split("\n")]


def _normal(lines: list[str]) -> str:
    """Lines as the text that is hashed: no leading or trailing blank lines, runs of blank lines as one."""
    out: list[str] = []
    for line in lines:
        if line == "" and (not out or out[-1] == ""):
            continue
        out.append(line)
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def version_of(text_: str) -> str:
    """The first twelve hex of the sha256 of the whole specification, whitespace normalised."""
    return hashlib.sha256(_normal(_lines(text_)).encode("utf-8")).hexdigest()[:12]


def parse(text_: str) -> list[Section]:
    """The specification's sections, in the order they stand. A '#' line inside a code block is not a heading."""
    lines = _lines(text_)
    heads: list[tuple[int, int, str]] = []          # (line index, level, heading text)
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _HEADING.match(line)
        if m:
            heads.append((i, len(m.group(1)), m.group(2)))
    out: list[Section] = []
    seen: dict[str, int] = {}
    for n, (at, level, heading) in enumerate(heads):
        end = next((j for j, lv, _ in heads[n + 1:] if lv <= level), len(lines))
        body = _normal(lines[at + 1:end])[:BODY_MAX]
        sid = section_id_of(heading)
        seen[sid] = seen.get(sid, 0) + 1
        if seen[sid] > 1:
            sid = f"{sid}_{seen[sid]}"
        title = _EXPLICIT.sub("", heading).strip()[:300] or sid
        out.append(Section(section_id=sid[:120], title=title, body=body,
                           sha256=hashlib.sha256(body.encode("utf-8")).hexdigest()))
    return out


def stale_sections(stamped: Mapping[str, str] | None, current: Mapping[str, str] | None,
                   cited: Sequence[str] | None = None) -> list[str]:
    """The sections a scenario cites whose text today is not the text it was written against: changed, gone, or
    arrived since it was written. In the order cited (then any stamped section it no longer cites). Empty when no
    specification is loaded: there is nothing to compare with.

    A section that neither the scenario was stamped with nor the specification has today is not out of date; it
    cannot be compared at all, and uncovered_sections names it."""
    if not current:
        return []
    was = stamped or {}
    return [s for s in dict.fromkeys([*(cited or []), *was]) if was.get(s) != current.get(s)]


def uncovered_sections(cited: Sequence[str] | None, current: Mapping[str, str] | None) -> list[str]:
    """The sections a scenario cites that the current specification does not have, so a change to the specification
    can never flag the scenario for them. Empty when no specification is loaded."""
    if not current:
        return []
    return [s for s in dict.fromkeys(cited or []) if s not in current]


# ── stored ───────────────────────────────────────────────────────────────────────────

async def current_hashes(conn: Any) -> tuple[str | None, dict[str, str]]:
    """(the current specification's version, {section id: sha256}); (None, {}) when none has been loaded. The current
    one is the newest load."""
    version = (await conn.execute(sa_text(
        "SELECT spec_version FROM tracelab.spec_sections ORDER BY created_at DESC, spec_version DESC LIMIT 1"))).scalar()
    if version is None:
        return None, {}
    rows = (await conn.execute(sa_text(
        "SELECT section_id, sha256 FROM tracelab.spec_sections WHERE spec_version = :v"), {"v": version})).all()
    return version, {r[0]: r[1] for r in rows}


def stale_sql(alias: str, version: str | None) -> str:
    """SQL that is true for a scenario row out of date against the current specification (bind :spec_current): a
    section it cites reads differently today than the hash it was stamped with, is gone, or has arrived since."""
    if version is None:
        return "FALSE"
    return (f"EXISTS (SELECT 1 FROM unnest({alias}.spec_sections) AS cited(section) WHERE"
            f" ({alias}.spec_hashes ->> cited.section) IS DISTINCT FROM"
            " (SELECT ss.sha256 FROM tracelab.spec_sections ss WHERE ss.spec_version = :spec_current"
            " AND ss.section_id = cited.section))")


async def load(m: labelling.Member, *, text: str | None = None) -> dict[str, Any]:  # noqa: A002
    """Read the specification (the file, or `text`) and store it as the current one when it differs from the current
    one. Returns {"spec_version", "sections", "new", "changed": the section ids whose text changed, came or went}.
    Curators only."""
    runs.require_curator(m)
    body = text
    if body is None:
        try:
            body = spec_path().read_text(encoding="utf-8")
        except OSError:
            raise Invalid("spec_missing", "No specification file was found on this machine.") from None
    sections = parse(body)
    if not sections:
        raise Invalid("spec_empty", "The specification has no sections. Give each area its own heading.")
    mine = {s.section_id: s.sha256 for s in sections}
    version = version_of(body)
    async with db.lab_engine().begin() as conn:
        current_version, current = await current_hashes(conn)
        if current_version is not None and current == mine:
            return {"spec_version": current_version, "sections": len(sections), "new": False, "changed": []}
        # The same text loaded again after a different one (a revert) is a load of its own, so it becomes current.
        earlier = (await conn.execute(sa_text(
            "SELECT count(DISTINCT spec_version) FROM tracelab.spec_sections WHERE spec_version = :v"
            " OR spec_version LIKE :like"), {"v": version, "like": version + "-r%"})).scalar() or 0
        if earlier:
            version = f"{version}-r{earlier}"
        for position, s in enumerate(sections, start=1):
            await conn.execute(sa_text(
                "INSERT INTO tracelab.spec_sections (spec_version, section_id, position, title, sha256, body)"
                " VALUES (:v, :id, :pos, :title, :sha, :body)"),
                {"v": version, "id": s.section_id, "pos": position, "title": s.title, "sha": s.sha256,
                 "body": s.body.replace("\x00", "")})
    changed = sorted(k for k in set(current) | set(mine) if current.get(k) != mine.get(k)) if current_version else []
    return {"spec_version": version, "sections": len(sections), "new": True, "changed": changed}


async def overview(m: labelling.Member) -> dict[str, Any]:
    """The current specification's sections in document order, and of the current scenarios the caller can see: how
    many are out of date against it, how many cite a section it does not have ("uncovered": a change can never flag
    those), and which sections those are."""
    from . import scenario_file

    labelling.require_active(m)
    async with conversations._lab_read() as conn:
        version, _ = await current_hashes(conn)
        sections = [dict(r) for r in (await conn.execute(sa_text(
            "SELECT section_id, title, sha256 FROM tracelab.spec_sections WHERE spec_version = :v ORDER BY position"),
            {"v": version})).mappings().all()] if version else []
        rows = await scenario_file.latest(conn, org_scope=scenario_file._visible(m)) if version else []
    uncovered = [r for r in rows if r["uncovered"]]
    return {"spec_version": version, "sections": sections, "stale_scenarios": sum(1 for r in rows if r["stale"]),
            "uncovered_scenarios": len(uncovered),
            "uncovered_sections": sorted({s for r in uncovered for s in r["uncovered"]})}
