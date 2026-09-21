"""Company admin: users, access, and the ingestion audit trail. Admin only, own company only.

    GET    /api/admin/users                      users with buildings, ingestion right, usage, status
    POST   /api/admin/users/invite               invite a user, allocate buildings, set can_ingest
    PATCH  /api/admin/users/{id}                 change allocation, ingestion right, title, status (active|inactive)
    POST   /api/admin/users/{id}/deactivate      stop them signing in; sessions end now; reversible
    POST   /api/admin/users/{id}/reactivate      undo a deactivation (never a deletion)
    DELETE /api/admin/users/{id}                 soft delete: scrub the person, keep the row the audit trail names
    GET    /api/admin/buildings                  the company's buildings, for the allocation chips
    GET    /api/admin/usage                      per-building and per-user usage
    GET    /api/admin/ingestion-audit            the audit trail, filterable by outcome

The building is the access boundary. A user sees and ingests only within the buildings
assigned here; whether they can ingest at all is a per-user setting made here. A superadmin
may act on another company by passing ?organization_id=; a company admin who tries gets 403.
"""
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import access
from ...engines.auth import ingestion_audit
from ...engines.auth import invitations as invite_engine
from ...engines.auth import roles as role_engine
from ...engines.auth import usage as usage_engine
from ...shared.approvals import PLATFORM_FEATURE, write_audit
from .auth import current_principal, require_admin, token_engine

router = APIRouter(prefix="/api/admin", tags=["company-admin"])


async def admin_scope(
    organization_id: UUID | None = Query(
        None, description="Superadmin only: act on this company."),
    principal: token_engine.Principal = Depends(require_admin),
) -> access.Scope:
    s = access.scope_for(principal, organization_id)
    if s.organization_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "This account belongs to no company.",
                    "reason": "no_organization"},
        )
    return s


class InviteUser(BaseModel):
    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    building_ids: list[UUID] = Field(default_factory=list)
    can_ingest: bool = False
    job_title: str | None = Field(None, max_length=120)
    role: str = Field(role_engine.USER, description="user | admin")


class PatchUser(BaseModel):
    building_ids: list[UUID] | None = None
    can_ingest: bool | None = None
    job_title: str | None = None
    full_name: str | None = None
    #: active | inactive | deleted. "suspended" is accepted and stored as inactive — it was
    #: the old word for the same state, and a client still sending it must not be refused.
    #: deleted through PATCH is refused: deleting scrubs a person's details, and that goes
    #: through DELETE so it cannot happen as a side effect of an edit.
    status: str | None = Field(None, description="active | inactive (suspended = inactive)")



#: The account states an administrator may set. Everything else is refused by name.
ACTIVE, INACTIVE, DELETED = "active", "inactive", "deleted"
_SETTABLE = {ACTIVE, INACTIVE}
_ALIASES = {"suspended": INACTIVE, "disabled": INACTIVE, "deactivated": INACTIVE}


def _normalise_status(value: str) -> str:
    v = str(value or "").strip().lower()
    v = _ALIASES.get(v, v)
    if v == DELETED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={
            "ok": False, "error": "Delete a user with DELETE /api/admin/users/{id}, not by setting status.",
            "reason": "use_delete"})
    if v not in _SETTABLE:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={
            "ok": False, "error": f"status must be one of {sorted(_SETTABLE)}", "reason": "bad_status"})
    return v


async def _set_status(session: AsyncSession, scope: access.Scope, user_id: UUID, new_status: str) -> dict:
    """Move an account between active and inactive. Inactive revokes every live session so
    the change bites now, not at the next token refresh; the account is kept whole so it can
    be reactivated with everything it had."""
    if user_id == scope.user_id:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "You cannot change your own status."})
    n = (await session.execute(
        text("""UPDATE plenum_cafm.users SET status = :s, updated_at = now()
                 WHERE id = :i AND organization_id = :o AND status <> 'deleted' RETURNING 1"""),
        {"s": new_status, "i": user_id, "o": scope.organization_id},
    )).scalar()
    if not n:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user_not_found_or_deleted"})
    if new_status != ACTIVE:
        await session.execute(
            text("""UPDATE plenum_cafm.auth_sessions SET revoked_at = now(), revoked_reason = :r
                     WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id, "r": new_status})
    await session.commit()
    return {"ok": True, "user_id": str(user_id), "status": new_status}


async def _company_buildings(session: AsyncSession, org: UUID) -> dict[str, dict]:
    rows = (await session.execute(
        text("""SELECT building_id::text AS id, name, building_code
                  FROM plenum_cafm.buildings WHERE organization_id = :o ORDER BY name"""),
        {"o": org},
    )).mappings().all()
    return {r["id"]: dict(r) for r in rows}


def _check_buildings(requested: list[UUID], owned: dict[str, dict]) -> list[str]:
    """Every allocated building must belong to this company. Allocating a building the
    company does not own would hand a user someone else's records."""
    ids = [str(b) for b in requested]
    foreign = [b for b in ids if b not in owned]
    if foreign:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": "Some buildings do not belong to this company.",
                    "reason": "foreign_buildings", "building_ids": foreign},
        )
    return ids


@router.get("/buildings")
async def list_buildings(
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """The company's buildings — the chips on the invite form."""
    owned = await _company_buildings(session, scope.organization_id)
    return {"ok": True, "count": len(owned), "buildings": list(owned.values())}


@router.get("/users")
async def list_users(
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Every account in the company with what the table shows: buildings, ingestion, usage, status."""
    org = scope.organization_id
    users = (await session.execute(
        text("""SELECT u.id::text, u.full_name, u.email, u.job_title, u.status,
                       u.platform_role AS role, COALESCE(u.can_ingest, FALSE) AS can_ingest,
                       u.last_login_at, u.invited_at, u.activated_at, u.created_at
                  FROM plenum_cafm.users u
                 WHERE u.organization_id = :o
                 ORDER BY lower(u.full_name), u.email"""),
        {"o": org},
    )).mappings().all()
    alloc = (await session.execute(
        text("""SELECT ub.user_id::text AS user_id, b.building_id::text AS id, b.name
                  FROM plenum_cafm.user_buildings ub
                  JOIN plenum_cafm.buildings b ON b.building_id = ub.building_id
                 WHERE b.organization_id = :o ORDER BY b.name"""),
        {"o": org},
    )).mappings().all()
    by_user: dict[str, list[dict]] = {}
    for a in alloc:
        by_user.setdefault(a["user_id"], []).append({"id": a["id"], "name": a["name"]})
    usage = await usage_engine.usage_by_user(session, org)
    owned_count = len(await _company_buildings(session, org))

    out = []
    for u in users:
        admin = role_engine.at_least(str(u["role"] or "user"), role_engine.ADMIN)
        buildings = by_user.get(u["id"], [])
        out.append({
            **dict(u),
            "can_ingest": bool(u["can_ingest"]) or admin,
            # An admin is not allocated; they see the company. Said explicitly so the table
            # does not render "0 buildings" against the person who administers all of them.
            "buildings": buildings if not admin else [],
            "building_count": len(buildings) if not admin else owned_count,
            "all_buildings": admin,
            "usage": usage.get(u["id"], {"queries": 0, "ingests": 0, "last_active": None}),
            "last_login_at": u["last_login_at"].isoformat() if u["last_login_at"] else None,
            "invited_at": u["invited_at"].isoformat() if u["invited_at"] else None,
            "activated_at": u["activated_at"].isoformat() if u["activated_at"] else None,
            "created_at": u["created_at"].isoformat() if u["created_at"] else None,
        })
    return {
        "ok": True,
        "count": len(out),
        "summary": {
            "users": len(out),
            "can_ingest": sum(1 for u in out if u["can_ingest"]),
            "pending_invites": sum(1 for u in out if str(u["status"]).lower() == "invited"),
            "access_boundary": "building",
        },
        "users": out,
    }


@router.post("/users/invite", status_code=status.HTTP_201_CREATED)
async def invite_user(
    body: InviteUser,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Invite by email; the allocation and ingestion right apply on activation."""
    if body.role not in (role_engine.USER, role_engine.ADMIN):
        raise HTTPException(status_code=400, detail={"ok": False, "error": "role must be user or admin"})
    if body.role == role_engine.ADMIN and not scope.is_admin:
        raise HTTPException(status_code=403, detail={"ok": False, "error": "forbidden"})
    owned = await _company_buildings(session, scope.organization_id)
    ids = _check_buildings(body.building_ids, owned)
    if body.role == role_engine.USER and not ids:
        # Allowed, but said out loud: this person will sign in and see nothing.
        note = "No buildings allocated: this user will see no data until some are."
    else:
        note = None
    try:
        out = await invite_engine.create(
            session, organization_id=scope.organization_id, email=str(body.email),
            full_name=body.full_name, role=body.role, can_ingest=body.can_ingest,
            building_ids=[UUID(b) for b in ids], invited_by=scope.user_id,
            job_title=body.job_title,
        )
    except invite_engine.InvitationError as exc:
        raise HTTPException(status_code=exc.http_status,
                            detail={"ok": False, "error": exc.message, "reason": exc.reason})
    await write_audit(
        session, actor=str(scope.user_id), action_type="admin.user.invited",
        source_feature=PLATFORM_FEATURE, organization_id=scope.organization_id,
        input_payload={"email": str(body.email), "buildings": ids, "can_ingest": body.can_ingest},
        output_payload={"user_id": out["user_id"]},
    )
    await session.commit()
    if note:
        out["note"] = note
    out["buildings"] = [owned[b] for b in ids]
    return out


@router.patch("/users/{user_id}")
async def patch_user(
    user_id: UUID,
    body: PatchUser,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Change what a user may see and do. Takes effect on their next request — the
    principal is read from the database every call, not from the token."""
    row = (await session.execute(
        text("""SELECT id, platform_role, status FROM plenum_cafm.users
                 WHERE id = :i AND organization_id = :o"""),
        {"i": user_id, "o": scope.organization_id},
    )).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user_not_found"})

    changed: dict = {}
    if body.building_ids is not None:
        owned = await _company_buildings(session, scope.organization_id)
        ids = _check_buildings(body.building_ids, owned)
        await session.execute(
            text("DELETE FROM plenum_cafm.user_buildings WHERE user_id = :u"), {"u": user_id})
        for b in ids:
            await session.execute(
                text("""INSERT INTO plenum_cafm.user_buildings (user_id, building_id, granted_by)
                        VALUES (:u, :b, :g) ON CONFLICT DO NOTHING"""),
                {"u": user_id, "b": b, "g": scope.user_id},
            )
        changed["building_ids"] = ids
    sets, params = [], {"i": user_id}
    if body.can_ingest is not None:
        sets.append("can_ingest = :ci"); params["ci"] = body.can_ingest; changed["can_ingest"] = body.can_ingest
    if body.job_title is not None:
        sets.append("job_title = :jt"); params["jt"] = body.job_title; changed["job_title"] = body.job_title
    if body.full_name is not None:
        sets.append("full_name = :fn"); params["fn"] = body.full_name; changed["full_name"] = body.full_name
    if body.status is not None:
        # _normalise_status already refused anything but active / inactive (and folded the
        # old word "suspended" into inactive). The check that used to sit here compared
        # against the OLD vocabulary and would have refused the normalised value it was
        # just handed.
        body.status = _normalise_status(body.status)
        if user_id == scope.user_id and body.status != ACTIVE:
            raise HTTPException(status_code=400, detail={"ok": False, "error": "You cannot deactivate yourself."})
        sets.append("status = :st"); params["st"] = body.status; changed["status"] = body.status
        # Deactivation must bite now, not at the next token refresh — the same rule the
        # dedicated /deactivate route applies.
        if body.status == INACTIVE:
            await session.execute(
                text("""UPDATE plenum_cafm.auth_sessions SET revoked_at = now(), revoked_reason = 'inactive'
                         WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id})
    if sets:
        await session.execute(
            text(f"UPDATE plenum_cafm.users SET {', '.join(sets)}, updated_at = now() WHERE id = :i"),
            params,
        )
    await write_audit(
        session, actor=str(scope.user_id), action_type="admin.user.updated",
        source_feature=PLATFORM_FEATURE, organization_id=scope.organization_id,
        input_payload=changed, output_payload={"user_id": str(user_id)},
    )
    await session.commit()
    return {"ok": True, "user_id": str(user_id), "changed": changed}


@router.post("/users/{user_id}/deactivate")
async def deactivate_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Deactivate: the person can no longer sign in, and their live sessions end now. Their
    allocation, history and details are kept, so reactivating restores exactly what they had."""
    return await _set_status(session, scope, user_id, INACTIVE)


@router.post("/users/{user_id}/reactivate")
async def reactivate_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Reactivate a deactivated account. A deleted one cannot come back — its details are gone."""
    return await _set_status(session, scope, user_id, ACTIVE)


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Delete a user — softly, and for a reason that is not caution.

    Five tables name a user by id with no cascade (work orders' created_by and requested_by,
    technicians, approver routing, maintenance history), and the append-only audit log names
    them thousands of times. A hard DELETE would be refused by the database for anyone who
    has ever done anything, and succeed only for someone who never signed in — the one case
    where it does not matter.

    So the row stays and the PERSON goes: email, name and phone are scrubbed to values that
    identify nobody, the status becomes deleted, every session is revoked, and the building
    allocation is dropped. History keeps resolving to an id; nothing about who it was remains
    on the row. This is not reversible, and reactivate will refuse it.
    """
    if user_id == scope.user_id:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "You cannot delete yourself."})
    row = (await session.execute(
        text("""SELECT email, status FROM plenum_cafm.users WHERE id = :i AND organization_id = :o"""),
        {"i": user_id, "o": scope.organization_id})).mappings().first()
    if row is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user_not_found"})
    if row["status"] == DELETED:
        return {"ok": True, "user_id": str(user_id), "status": DELETED, "already": True}
    scrubbed = f"deleted+{str(user_id)[:8]}@invalid.local"
    await session.execute(
        text("""UPDATE plenum_cafm.users
                   SET status = 'deleted', email = :e, full_name = 'Deleted user',
                       phone = NULL, phone2 = NULL, job_title = NULL,
                       password_hash = NULL, selected_building_id = NULL, updated_at = now()
                 WHERE id = :i"""), {"e": scrubbed, "i": user_id})
    await session.execute(
        text("""UPDATE plenum_cafm.auth_sessions SET revoked_at = now(), revoked_reason = 'deleted'
                 WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id})
    await session.execute(text("DELETE FROM plenum_cafm.user_buildings WHERE user_id = :u"), {"u": user_id})
    await session.commit()
    return {"ok": True, "user_id": str(user_id), "status": DELETED, "previous_email": row["email"]}


@router.get("/usage")
async def company_usage(
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Usage across the company's buildings and users."""
    card = await usage_engine.company_usage(session, scope.organization_id)
    return {
        "ok": True,
        "company": card.get("company"),
        "totals": {k: card.get(k) for k in (
            "buildings_created", "hoist_graphs", "compliance_certificates", "queries",
            "ingests", "credits_this_month", "credits_total", "last_activity")},
        "by_building": await usage_engine.usage_by_building(session, scope.organization_id),
        "by_user": await usage_engine.usage_by_user(session, scope.organization_id),
    }


@router.get("/ingestion-audit")
async def ingestion_audit_trail(
    outcome: str | None = Query(None, description="accepted | reassigned | overridden | rejected | approved_on_confirmation"),
    q: str | None = Query(None, description="Matches document, uploader name or email, and building."),
    building_id: UUID | None = Query(None, description="One building — the one it was filed against or moved to."),
    actor_user_id: UUID | None = Query(None, description="One uploader."),
    actor_role: str | None = Query(None, description="admin (includes superadmin) | user"),
    since: datetime | None = Query(None, description="Inclusive lower bound on occurred_at."),
    until: datetime | None = Query(None, description="Exclusive upper bound on occurred_at."),
    limit: int = Query(100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Every flagged ingestion, clarification, override and approval for the company.

    The company is taken from the verified scope and is never a parameter of this handler:
    `admin_scope` already owns `organization_id` as the superadmin act-as override, and a
    second one here would let any admin read another tenant's trail by adding it to the URL.

    `building_ids=None` below is deliberate and is NOT the building filter — it is the
    caller's allocation, and an admin reading their own company sees all of it. The reader's
    chosen building arrives separately as `building_id`, which can only narrow further.
    """
    out = await ingestion_audit.list_events(
        session, organization_id=scope.organization_id, outcome=outcome,
        building_ids=None, building_id=building_id, actor_user_id=actor_user_id,
        actor_role=actor_role, q=q, since=since, until=until,
        limit=limit, offset=offset,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out
