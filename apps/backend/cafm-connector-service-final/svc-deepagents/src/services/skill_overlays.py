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

STATUSES = ("proposed", "active", "rejected", "retired")

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
    from ..agents.skills import prompt_doc
    try:
        return prompt_doc(skill, name)
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
