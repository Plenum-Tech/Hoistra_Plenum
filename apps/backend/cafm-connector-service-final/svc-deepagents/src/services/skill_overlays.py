"""Skill instructions that improved under measurement, held in the database over the shipped file.

The skill lab (agents/skill_lab.py) proposes a rewrite of one skill document, scores it against
the shipped text on replayed questions, and keeps a rewrite that wins as a *proposal*. An admin
approves it; from then on the approved text is what the agents read, without a deploy. The
shipped file stays the fallback, and the repo copy can be updated from the approved text later.

Two layers, read in this order by ``text_for``:

* CANDIDATE - a context variable set only inside a skill-lab replay, so the text being tested
  reaches that one replay and nothing else running in the process;
* the ACTIVE overlay for (skill, doc), refreshed from the table at most once a minute.

Only documents read at call time can be overlaid; a prompt assembled once at import keeps its
shipped text until the process restarts. The context-budget instructions are read on every model
step (agents/context_budget.py), which is why they are the first document the lab tunes.
"""
from __future__ import annotations

import time
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from sqlalchemy import text

from .. import database

log = structlog.get_logger(__name__)

TABLE = "plenum_cafm.skill_overlays"

_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS {TABLE} (
        id           UUID PRIMARY KEY,
        skill        TEXT NOT NULL,
        doc          TEXT NOT NULL,
        content      TEXT NOT NULL,
        status       TEXT NOT NULL DEFAULT 'proposed',
        evidence     JSONB,
        run_id       UUID,
        created_by   TEXT,
        created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
        reviewed_by  TEXT,
        reviewed_at  TIMESTAMPTZ
    )""",
    f"CREATE INDEX IF NOT EXISTS ix_skill_overlays_doc ON {TABLE} (skill, doc, status)",
]

#: 'testing' (5 Nov 2026, Bala): approved by a person, read only inside a Trace Lab rerun (CANDIDATE) while testing
#: repeats; 'active' is what every agent reads; nothing reaches the repo until it is exported at commit.
STATUSES = ("proposed", "testing", "active", "rejected", "retired")

#: Text under test in one replay: {(skill, doc): text}. Never set outside the skill lab.
CANDIDATE: ContextVar[dict[tuple[str, str], str] | None] = ContextVar("cafm_skill_candidate", default=None)

_active: dict[tuple[str, str], str] = {}
_loaded_at = 0.0
_ready = False
_TTL = 60.0


async def ensure_table() -> bool:
    global _ready
    if _ready:
        return True
    try:
        async with database.AsyncSessionLocal() as s:
            for ddl in _DDL:
                await s.execute(text(ddl))
            await s.commit()
        _ready = True
    except Exception as exc:  # noqa: BLE001 - no table means no overlays, never a failed turn
        # A read-only process (Trace Lab) cannot run the DDL, and need not: the table is there to read.
        try:
            async with database.AsyncSessionLocal() as s:
                _ready = (await s.execute(text(f"SELECT to_regclass('{TABLE}') IS NOT NULL"))).scalar() is True
        except Exception:  # noqa: BLE001
            _ready = False
        if not _ready:
            log.warning("skill_overlays.ddl_failed", error=str(exc)[:200])
    return _ready


async def refresh(force: bool = False) -> None:
    """Reload the active overlays when the cache is older than a minute. Never raises."""
    global _active, _loaded_at
    if not force and time.monotonic() - _loaded_at < _TTL:
        return
    _loaded_at = time.monotonic()
    if not await ensure_table():
        return
    try:
        async with database.AsyncSessionLocal() as s:
            rows = (await s.execute(text(
                f"SELECT skill, doc, content FROM {TABLE} WHERE status = 'active'"))).all()
        _active = {(r[0], r[1]): r[2] for r in rows}
    except Exception as exc:  # noqa: BLE001
        log.warning("skill_overlays.refresh_failed", error=str(exc)[:200])


def text_for(skill: str, doc: str) -> str | None:
    """The overlaid text for one document, or None to use the shipped file."""
    cand = CANDIDATE.get()
    if cand and (skill, doc) in cand:
        return cand[(skill, doc)]
    return _active.get((skill, doc))


def doc(skill: str, name: str) -> str:
    """One skill document as the agents should read it now: overlay first, shipped file else."""
    over = text_for(skill, name)
    if over:
        return over
    from ..agents.skills import _shipped_doc
    try:
        return _shipped_doc(skill, name)
    except RuntimeError:
        return ""


async def propose(*, skill: str, doc_name: str, content: str, evidence: dict[str, Any] | None,
                  run_id: str | None, by: str | None) -> str | None:
    if not await ensure_table():
        return None
    oid = str(uuid.uuid4())
    import json
    async with database.AsyncSessionLocal() as s:
        await s.execute(text(
            f"INSERT INTO {TABLE} (id, skill, doc, content, status, evidence, run_id, created_by) "
            "VALUES (CAST(:id AS uuid), :skill, :doc, :content, 'proposed', CAST(:ev AS jsonb), "
            "CAST(:run AS uuid), :by)"),
            {"id": oid, "skill": skill, "doc": doc_name, "content": content,
             "ev": json.dumps(evidence or {}, default=str), "run": run_id, "by": by})
        await s.commit()
    return oid


async def list_overlays(*, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
    if not await ensure_table():
        return []
    where, p = ("WHERE status = :st", {"st": status}) if status else ("", {})
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            f"SELECT id::text, skill, doc, content, status, evidence, run_id::text, created_by, created_at, "
            f"reviewed_by, reviewed_at FROM {TABLE} {where} ORDER BY created_at DESC LIMIT :lim"),
            {**p, "lim": limit})).mappings().all()
    out = []
    for r in rows:
        d = dict(r)
        for k in ("created_at", "reviewed_at"):
            d[k] = d[k].isoformat() if d.get(k) else None
        out.append(d)
    return out


async def review(overlay_id: str, *, approve: bool, by: str | None) -> dict[str, Any] | None:
    """Approve (the previous active text for that document is retired) or reject a proposal."""
    if not await ensure_table():
        return None
    async with database.AsyncSessionLocal() as s:
        row = (await s.execute(text(
            f"SELECT skill, doc, status FROM {TABLE} WHERE id = CAST(:id AS uuid)"),
            {"id": overlay_id})).first()
        if not row or row[2] != "proposed":
            return None
        if approve:
            await s.execute(text(
                f"UPDATE {TABLE} SET status = 'retired' WHERE skill = :sk AND doc = :dc AND status = 'active'"),
                {"sk": row[0], "dc": row[1]})
        await s.execute(text(
            f"UPDATE {TABLE} SET status = :st, reviewed_by = :by, reviewed_at = now() WHERE id = CAST(:id AS uuid)"),
            {"st": "active" if approve else "rejected", "by": by, "id": overlay_id})
        await s.commit()
    await refresh(force=True)
    return {"id": overlay_id, "skill": row[0], "doc": row[1], "status": "active" if approve else "rejected"}


async def retire_active(skill: str, doc_name: str, *, by: str | None) -> int:
    """Go back to the shipped file for one document."""
    if not await ensure_table():
        return 0
    async with database.AsyncSessionLocal() as s:
        res = await s.execute(text(
            f"UPDATE {TABLE} SET status = 'retired', reviewed_by = :by, reviewed_at = now() "
            "WHERE skill = :sk AND doc = :dc AND status = 'active'"),
            {"sk": skill, "dc": doc_name, "by": by})
        await s.commit()
    await refresh(force=True)
    return int(getattr(res, "rowcount", 0) or 0)


# ── the correction loop's lifecycle (Bala, 5 Nov 2026) ──────────────────────────────────────────
# proposed (an agent's suggestion) -> testing (a person approved it; only a Trace Lab rerun reads it) -> active (a person
# made it live; every agent reads it, no rebuild) -> exported to the repo at commit. Rejected and retired stay as history.

def known_doc(skill: str, doc_name: str) -> bool:
    """Only an existing instruction document can be changed: a skill's SKILL.md or one of its .md files, or the spec."""
    import re as _re

    from ..agents.skills import SPEC_DOC, SPEC_SKILL, skills_dir

    if (skill, doc_name) == (SPEC_SKILL, SPEC_DOC):
        return True
    if not _re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,60}", skill or "") or not _re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,80}", doc_name or ""):
        return False
    return (skills_dir() / skill / f"{doc_name}.md").is_file()


async def _move(overlay_id: str, *, frm: str, to: str, by: str | None) -> dict[str, Any] | None:
    if not await ensure_table():
        return None
    async with database.AsyncSessionLocal() as s:
        row = (await s.execute(text(
            f"SELECT skill, doc, status FROM {TABLE} WHERE id = CAST(:id AS uuid)"), {"id": overlay_id})).first()
        if not row or row[2] != frm:
            return None
        if to == "active":
            await s.execute(text(
                f"UPDATE {TABLE} SET status = 'retired' WHERE skill = :sk AND doc = :dc AND status = 'active'"),
                {"sk": row[0], "dc": row[1]})
        await s.execute(text(
            f"UPDATE {TABLE} SET status = :st, reviewed_by = :by, reviewed_at = now() WHERE id = CAST(:id AS uuid)"),
            {"st": to, "by": by, "id": overlay_id})
        await s.commit()
    await refresh(force=True)
    return {"id": overlay_id, "skill": row[0], "doc": row[1], "status": to}


async def approve_for_testing(overlay_id: str, *, by: str | None) -> dict[str, Any] | None:
    """A person approved an agent's change: it is now under test (only a Trace Lab rerun reads it)."""
    return await _move(overlay_id, frm="proposed", to="testing", by=by)


async def go_live(overlay_id: str, *, by: str | None) -> dict[str, Any] | None:
    """A person confirmed a change that passed its tests: every agent reads it from now on, no rebuild."""
    return await _move(overlay_id, frm="testing", to="active", by=by)


async def drop(overlay_id: str, *, by: str | None) -> dict[str, Any] | None:
    """A person turned down a proposal or a change under test."""
    out = await _move(overlay_id, frm="proposed", to="rejected", by=by)
    return out or await _move(overlay_id, frm="testing", to="rejected", by=by)


async def testing_texts() -> dict[tuple[str, str], str]:
    """{(skill, doc): text} under test - what a Trace Lab rerun sets as CANDIDATE. Newest per document."""
    if not await ensure_table():
        return {}
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            f"SELECT DISTINCT ON (skill, doc) skill, doc, content FROM {TABLE} WHERE status = 'testing' "
            "ORDER BY skill, doc, created_at DESC"))).all()
    return {(r[0], r[1]): r[2] for r in rows}


async def export_active() -> list[dict[str, Any]]:
    """Every live document with the repo path it is committed to - the only way a change reaches the repo."""
    if not await ensure_table():
        return []
    async with database.AsyncSessionLocal() as s:
        rows = (await s.execute(text(
            f"SELECT skill, doc, content FROM {TABLE} WHERE status = 'active' ORDER BY skill, doc"))).all()
    return [{"skill": r[0], "doc": r[1], "path": f"skills/{r[0]}/{r[1]}.md", "content": _with_front_matter(r[0], r[1], r[2])}
            for r in rows]


def _with_front_matter(skill: str, doc_name: str, content: str) -> str:
    """The file as it is committed: the shipped file's front matter (a SKILL.md's name, agent and triggers) kept above
    the changed text, which the agents read without it."""
    from ..agents.skills import skills_dir

    try:
        raw = (skills_dir() / skill / f"{doc_name}.md").read_text(encoding="utf-8")
    except OSError:
        return content
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end > 0:
            return raw[: end + 4].rstrip() + "\n\n" + content.strip() + "\n"
    return content
