"""Feature 4 — Activity Log Section 3 persistence (inline actions + timeout sweep).

Async writer/reader for ``ActivityAction`` plus the server-side 15-minute timeout sweep
(AL.4): it marks overdue ``pending`` actions ``timed_out`` and raises a 'Pending Human
Response' Activity Summary entry (red) — WITHOUT ever choosing an option, so the system
never proceeds autonomously on a human-gated item (AL.4 AC3). DB + model imports are lazy
so the module imports clean for unit tests. Mirrors :mod:`udr.activity_persist`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from .actions import build_pending_response_entry, deadline_from, is_valid_option, normalize_options
from .activity_persist import _run_in_session  # shared session helper (lazy DB import)


# ── pure: build row columns from a built inline action ──────────────────────────
def action_to_columns(
    action: dict,
    *,
    entry_id,
    organization_id: Any = None,
    created_at: datetime | None = None,
) -> dict:
    """Map a built inline action (see :func:`udr.actions.build_inline_action`) to
    ``ActivityAction`` columns, stamping the 15-min ``deadline_at``. Pure — no DB."""
    created = created_at or datetime.utcnow()
    return {
        "entry_id": entry_id,
        "organization_id": organization_id,
        "kind": action.get("kind", "approval"),
        "label": action.get("label", ""),
        "options": normalize_options(action.get("options")),
        "status": "pending",
        "deadline_at": deadline_from(created),
        "created_at": created,
    }


def _row_to_dict(r) -> dict:
    return {
        "id": str(r.id),
        "entry_id": str(r.entry_id),
        "kind": r.kind,
        "label": r.label,
        "options": r.options or [],
        "status": r.status,
        "chosen_option_id": r.chosen_option_id,
        "resolution_note": r.resolution_note,
        "deadline_at": r.deadline_at.isoformat() if r.deadline_at else None,
        "resolved_at": r.resolved_at.isoformat() if r.resolved_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ── write: record one inline action for an activity entry ───────────────────────
async def record_action(
    action: dict,
    *,
    entry_id,
    organization_id: Any = None,
    created_at: datetime | None = None,
    session=None,
) -> str:
    """Insert one ``ActivityAction`` (pending, with its 15-min deadline). Returns its id."""
    from ..models.migration import ActivityAction

    new_id = uuid4()
    cols = action_to_columns(
        action, entry_id=entry_id, organization_id=organization_id, created_at=created_at
    )
    row = ActivityAction(id=new_id, **cols)

    async def _do(s):
        s.add(row)
        await s.flush()
        return str(new_id)

    return await _run_in_session(_do, session)


# ── read: actions for one activity entry (Section 3 inline render) ──────────────
async def list_actions(entry_id, *, session=None) -> list[dict]:
    from uuid import UUID

    from sqlalchemy import select

    from ..models.migration import ActivityAction

    eid = UUID(entry_id) if isinstance(entry_id, str) else entry_id

    async def _do(s):
        res = await s.execute(
            select(ActivityAction)
            .where(ActivityAction.entry_id == eid)
            .order_by(ActivityAction.created_at)
        )
        return [_row_to_dict(r) for r in res.scalars().all()]

    return await _run_in_session(_do, session)


# ── read: all still-pending actions (org-scoped) ────────────────────────────────
async def list_pending_actions(*, organization_id: Any = None, session=None) -> list[dict]:
    from sqlalchemy import select

    from ..models.migration import ActivityAction

    async def _do(s):
        stmt = select(ActivityAction).where(ActivityAction.status == "pending")
        if organization_id is not None:
            stmt = stmt.where(ActivityAction.organization_id == organization_id)
        res = await s.execute(stmt.order_by(ActivityAction.deadline_at))
        return [_row_to_dict(r) for r in res.scalars().all()]

    return await _run_in_session(_do, session)


# ── resolve: record the chosen option + resume (AL.3) ───────────────────────────
async def resolve_action(
    action_id,
    option_id: str,
    *,
    note: str | None = None,
    resolved_at: datetime | None = None,
    session=None,
) -> dict | None:
    """Record a human's chosen option. Returns the updated row, ``None`` if not found, and
    raises ``ValueError('invalid_option')`` if ``option_id`` isn't one the action offered."""
    from uuid import UUID

    from sqlalchemy import select

    from ..models.migration import ActivityAction

    aid = UUID(action_id) if isinstance(action_id, str) else action_id

    async def _do(s):
        row = (await s.execute(select(ActivityAction).where(ActivityAction.id == aid))).scalar_one_or_none()
        if row is None:
            return None
        if not is_valid_option({"options": row.options or []}, option_id):
            raise ValueError("invalid_option")
        row.status = "resolved"
        row.chosen_option_id = str(option_id)
        row.resolution_note = note
        row.resolved_at = resolved_at or datetime.utcnow()
        await s.flush()
        return _row_to_dict(row)

    return await _run_in_session(_do, session)


# ── sweep: AL.4 server-side 15-min timeout (never proceeds autonomously) ─────────
async def sweep_timeouts(*, now: datetime | None = None, organization_id: Any = None, session=None) -> dict:
    """Mark overdue pending actions ``timed_out`` and raise a 'Pending Human Response'
    summary entry (red) for each. Does NOT choose an option (AL.4 AC3). Returns
    ``{swept, created_entries}``.

    Multi-worker safe: claims rows with a single atomic
    ``UPDATE ... WHERE status='pending' ... RETURNING`` rather than SELECT-then-mutate. Under
    READ COMMITTED, a concurrent sweeper's UPDATE blocks on the locked row and then re-checks
    the predicate — the now-``timed_out`` row no longer qualifies, so each timed-out action is
    claimed (and gets its Pending entry) by exactly one worker. No duplicate notifications."""
    from sqlalchemy import update

    from ..models.migration import ActivityAction
    from .activity_persist import record_activity

    when = now or datetime.utcnow()

    async def _do(s):
        stmt = update(ActivityAction).where(
            ActivityAction.status == "pending", ActivityAction.deadline_at <= when
        )
        if organization_id is not None:
            stmt = stmt.where(ActivityAction.organization_id == organization_id)
        stmt = stmt.values(status="timed_out", updated_at=when).returning(
            ActivityAction.id,
            ActivityAction.entry_id,
            ActivityAction.label,
            ActivityAction.organization_id,
        )
        claimed = (await s.execute(stmt)).all()  # only the rows THIS worker flipped

        created: list[str] = []
        for row in claimed:
            entry = build_pending_response_entry(
                {"id": str(row.id), "entry_id": str(row.entry_id), "label": row.label}
            )
            new_id = await record_activity(
                entry, organization_id=row.organization_id, session=s
            )
            created.append(new_id)
        return {"swept": len(claimed), "created_entries": created}

    return await _run_in_session(_do, session)


# ── F7-5/F7-6 — record a flagged sanctity verdict as an escalatable entry + action ──
async def record_sanctity_flag(
    verdict: dict, *, wo_id: str, asset_id: str, organization_id: Any = None, session=None
) -> dict:
    """Persist a FLAGGED sanctity verdict (implausible link) as an Activity-Log entry +
    inline action (approve / reassign / mark coincidental). No-op for a plausible verdict."""
    if verdict.get("plausible", True):
        return {"flagged": False}

    from .activity_persist import record_activity
    from .sanctity import build_sanctity_activity

    built = build_sanctity_activity(verdict, wo_id=wo_id, asset_id=asset_id)

    async def _do(s):
        entry_id = await record_activity(built["entry"], organization_id=organization_id, session=s)
        action_id = await record_action(
            built["action"], entry_id=entry_id, organization_id=organization_id, session=s
        )
        return {"flagged": True, "activity_id": entry_id, "action_id": action_id}

    return await _run_in_session(_do, session)
