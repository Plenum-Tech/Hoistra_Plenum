"""Super admin: onboard companies and observe them. Deliberately light.

    POST /api/superadmin/companies                       create a company (optionally invite its admin)
    GET  /api/superadmin/companies                       every company with credits this month
    GET  /api/superadmin/companies/{id}                  one company's usage card
    POST /api/superadmin/companies/{id}/invite-admin     send the administrator invitation
    GET  /api/superadmin/credits                         credit consumption across companies

Everything here requires the superadmin role. Day-to-day data management is not exposed:
the permission model would allow it, but this console onboards and observes, and giving it
buttons it does not need is how a platform operator ends up editing a tenant's records by
accident.
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.auth import invitations as invite_engine
from ...engines.auth import roles as role_engine
from ...engines.auth import tokens as token_engine
from ...engines.auth import usage as usage_engine
from ...engines.energy.buildings import country_code_for
from ...shared.approvals import write_audit
from .auth import require_superadmin

router = APIRouter(prefix="/api/superadmin", tags=["superadmin"])


class CreateCompany(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    country_code: str | None = Field(None, description="UK | US | AE | SG or a country name")
    admin_email: EmailStr | None = Field(
        None, description="If given, the administrator is invited immediately.")
    admin_name: str | None = None
    industry: str | None = None
    timezone: str | None = None


class InviteAdmin(BaseModel):
    email: EmailStr
    full_name: str | None = None


def _country(raw: str | None) -> str | None:
    if not raw:
        return None
    code = country_code_for(raw)
    if not code:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"ok": False, "error": f"Unknown country {raw!r}. Use UK, US, AE or SG.",
                    "reason": "bad_country"},
        )
    return code


@router.post("/companies", status_code=status.HTTP_201_CREATED)
async def create_company(
    body: CreateCompany,
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(require_superadmin),
):
    """Create the company account. With admin_email, invite its administrator in one step."""
    code = _country(body.country_code)
    dup = (await session.execute(
        text("SELECT id::text FROM plenum_cafm.organizations WHERE lower(name) = lower(:n)"),
        {"n": body.name.strip()},
    )).scalar()
    if dup:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"ok": False, "error": "A company with that name already exists.",
                    "reason": "duplicate_name", "organization_id": dup},
        )
    org_id = (await session.execute(
        text("""INSERT INTO plenum_cafm.organizations
                    (name, industry, country, country_code, timezone, status, lifecycle,
                     admin_email, created_by, created_at, updated_at)
                VALUES (:n, :ind, :c, :cc, :tz, 'active', 'created', :ae, :by, now(), now())
                RETURNING id"""),
        {"n": body.name.strip(), "ind": body.industry, "c": body.country_code, "cc": code,
         "tz": body.timezone, "ae": str(body.admin_email) if body.admin_email else None,
         "by": principal.user_id},
    )).scalar()
    await write_audit(
        session, actor=str(principal.email), action_type="superadmin.company.created",
        source_feature="platform", organization_id=org_id,
        input_payload={"name": body.name, "country_code": code},
        output_payload={"organization_id": str(org_id)},
    )
    await session.commit()

    invitation = None
    if body.admin_email:
        try:
            invitation = await invite_engine.create(
                session, organization_id=org_id, email=str(body.admin_email),
                full_name=body.admin_name, role=role_engine.ADMIN, can_ingest=True,
                building_ids=[], invited_by=principal.user_id, inviter_name=None,
            )
            await session.execute(
                text("UPDATE plenum_cafm.organizations SET lifecycle = 'onboarding' WHERE id = :o"),
                {"o": org_id},
            )
            await session.commit()
        except invite_engine.InvitationError as exc:
            invitation = {"ok": False, "error": exc.message, "reason": exc.reason}

    return {"ok": True, "organization_id": str(org_id), "name": body.name.strip(),
            "country_code": code, "lifecycle": "onboarding" if invitation and invitation.get("ok") else "created",
            "admin_invitation": invitation}


@router.get("/companies")
async def list_companies(
    session: AsyncSession = Depends(get_session),
    _: token_engine.Principal = Depends(require_superadmin),
):
    """Every company on the platform, with its credits this month and lifecycle."""
    rows = await usage_engine.credits_by_company(session)
    unplaced = (await session.execute(
        text("SELECT count(*) FROM plenum_cafm.buildings WHERE organization_id IS NULL"),
    )).scalar()
    return {"ok": True, "count": len(rows), "companies": rows,
            # Buildings no company owns. Reported here because they are invisible to every
            # company admin until somebody assigns them, and a superadmin is who can.
            "buildings_without_company": int(unplaced or 0)}


@router.get("/companies/{organization_id}")
async def company_detail(
    organization_id: UUID,
    session: AsyncSession = Depends(get_session),
    _: token_engine.Principal = Depends(require_superadmin),
):
    """One company's usage card: buildings, graphs, activity, UDR volume, certificates,
    countries, API requests, credits, users."""
    out = await usage_engine.company_usage(session, organization_id)
    if not out.get("ok"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=out)
    out["pending_invitations"] = await invite_engine.pending_for(session, organization_id)
    return out


@router.post("/companies/{organization_id}/invite-admin")
async def invite_company_admin(
    organization_id: UUID,
    body: InviteAdmin,
    session: AsyncSession = Depends(get_session),
    principal: token_engine.Principal = Depends(require_superadmin),
):
    exists = (await session.execute(
        text("SELECT 1 FROM plenum_cafm.organizations WHERE id = :o"), {"o": organization_id},
    )).scalar()
    if not exists:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail={"ok": False, "error": "company_not_found"})
    try:
        out = await invite_engine.create(
            session, organization_id=organization_id, email=str(body.email),
            full_name=body.full_name, role=role_engine.ADMIN, can_ingest=True,
            building_ids=[], invited_by=principal.user_id,
        )
    except invite_engine.InvitationError as exc:
        raise HTTPException(status_code=exc.http_status,
                            detail={"ok": False, "error": exc.message, "reason": exc.reason})
    await session.execute(
        text("""UPDATE plenum_cafm.organizations
                   SET admin_email = :e, lifecycle = CASE WHEN lifecycle = 'created'
                                                          THEN 'onboarding' ELSE lifecycle END,
                       updated_at = now()
                 WHERE id = :o"""),
        {"e": str(body.email).lower(), "o": organization_id},
    )
    await write_audit(
        session, actor=str(principal.email), action_type="superadmin.company.admin_invited",
        source_feature="platform", organization_id=organization_id,
        input_payload={"email": str(body.email)}, output_payload={"user_id": out["user_id"]},
    )
    await session.commit()
    return out


@router.get("/credits")
async def credits(
    session: AsyncSession = Depends(get_session),
    _: token_engine.Principal = Depends(require_superadmin),
):
    """Credit consumption across companies this month — the bars on the console."""
    rows = await usage_engine.credits_by_company(session)
    total = sum(r["credits_this_month"] for r in rows)
    return {"ok": True, "month_total": total, "tariff": usage_engine.TARIFF,
            "companies": [{"organization_id": r["organization_id"], "name": r["name"],
                           "credits_this_month": r["credits_this_month"]} for r in rows],
            "billing_note": ("Usage-led: platform activity, API requests and credits, with no "
                             "seat limit. More users simply consume more.")}
