"""Company admin: users, access, and the ingestion audit trail. Admin only, own company only.

    GET    /api/admin/users                      users with buildings, ingestion right, usage, status
    POST   /api/admin/users/invite               invite a user, allocate buildings, set can_ingest
    PATCH  /api/admin/users/{id}                 change allocation, ingestion right, title, status
    DELETE /api/admin/users/{id}                 deactivate (never delete — the audit trail names them)
    GET    /api/admin/buildings                  the company's buildings, for the allocation chips
    GET    /api/admin/usage                      per-building and per-user usage
    GET    /api/admin/ingestion-audit            the audit trail, filterable by outcome

The building is the access boundary. A user sees and ingests only within the buildings
assigned here; whether they can ingest at all is a per-user setting made here. A superadmin
may act on another company by passing ?organization_id=; a company admin who tries gets 403.
"""
from __future__ import annotations

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
    status: str | None = Field(None, description="active | suspended")


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
        if body.status not in ("active", "suspended"):
            raise HTTPException(status_code=400, detail={"ok": False, "error": "status must be active or suspended"})
        if user_id == scope.user_id and body.status != "active":
            raise HTTPException(status_code=400, detail={"ok": False, "error": "You cannot suspend yourself."})
        sets.append("status = :st"); params["st"] = body.status; changed["status"] = body.status
    if sets:
        await session.execute(
            text(f"UPDATE plenum_cafm.users SET {', '.join(sets)}, updated_at = now() WHERE id = :i"),
            params,
        )
    if body.status == "suspended":
        # Their sessions end now, not when the token expires.
        await session.execute(
            text("""UPDATE plenum_cafm.auth_sessions SET revoked_at = now(), revoked_reason = 'suspended'
                     WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id})
    await write_audit(
        session, actor=str(scope.user_id), action_type="admin.user.updated",
        source_feature=PLATFORM_FEATURE, organization_id=scope.organization_id,
        input_payload=changed, output_payload={"user_id": str(user_id)},
    )
    await session.commit()
    return {"ok": True, "user_id": str(user_id), "changed": changed}


@router.delete("/users/{user_id}")
async def deactivate_user(
    user_id: UUID,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Suspend, never delete. The audit trail names this person and must keep doing so."""
    if user_id == scope.user_id:
        raise HTTPException(status_code=400, detail={"ok": False, "error": "You cannot deactivate yourself."})
    n = (await session.execute(
        text("""UPDATE plenum_cafm.users SET status = 'suspended', updated_at = now()
                 WHERE id = :i AND organization_id = :o RETURNING 1"""),
        {"i": user_id, "o": scope.organization_id},
    )).scalar()
    if not n:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "user_not_found"})
    await session.execute(
        text("""UPDATE plenum_cafm.auth_sessions SET revoked_at = now(), revoked_reason = 'suspended'
                 WHERE user_id = :u AND revoked_at IS NULL"""), {"u": user_id})
    await session.commit()
    return {"ok": True, "user_id": str(user_id), "status": "suspended"}


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
    limit: int = Query(100, le=500),
    offset: int = 0,
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """Every flagged ingestion, clarification, override and approval for the company."""
    out = await ingestion_audit.list_events(
        session, organization_id=scope.organization_id, outcome=outcome,
        building_ids=None, limit=limit, offset=offset,
    )
    if not out.get("ok"):
        raise HTTPException(status_code=400, detail=out)
    return out
