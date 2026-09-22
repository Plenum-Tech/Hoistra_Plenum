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

import csv
import io
import json
import zipfile
from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...db import get_session
from ...engines.admin import org_export
from ...engines.auth import access
from ...engines.auth import ingestion_audit
from ...engines.auth import invitations as invite_engine
from ...engines.auth import roles as role_engine
from ...engines.auth import usage as usage_engine
from ...shared.approvals import PLATFORM_FEATURE, write_audit
from .auth import current_principal, require_admin, token_engine

log = get_logger(__name__)
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


# ── the company's data, as a file ────────────────────────────────────────────

#: Rows pulled from the database per round trip while writing a table's CSV. The readings
#: tables run past half a million rows, so the export streams in batches rather than
#: materialising a table in memory and falling over on the largest customer.
_EXPORT_BATCH = 2000

#: Column types whose values can each be megabytes: LangGraph/job state, pgvector
#: embeddings, extracted document text.
_WIDE_TYPES = frozenset({"jsonb", "json", "ARRAY", "USER-DEFINED"})

#: Rows per round trip for a table carrying one of those. The gateway drops a response that
#: goes 300s without bytes, and that timer only resets when a batch is handed out — so the
#: bound that matters is how long ONE batch takes, not how long the export takes. A narrow
#: table moves fast at 2000; a table of jsonb blobs has to ask for far fewer at a time or a
#: single batch can outlast the timeout on its own.
_EXPORT_BATCH_WIDE = 200


def _batch_for(table: str, heavy: set[str]) -> int:
    return _EXPORT_BATCH_WIDE if table in heavy else _EXPORT_BATCH


async def _export_manifest(
    session: AsyncSession, organization_id: UUID,
) -> tuple[dict[str, Any], dict[str, org_export.Rule], dict[str, str], set[str]]:
    """What this export would contain, table by table, counted before anything is written."""
    # BASE TABLE only. information_schema.columns lists views too, and plenum_cafm has
    # three (`contracts` and `invoices` are views over contract_sla_parameters and the
    # invoice tables, which are themselves exported). Including them shipped the same rows
    # twice under two names, which reads as a discrepancy in the customer's own data.
    rows = (
        await session.execute(
            text(
                """SELECT c.table_name, c.column_name, c.data_type
                     FROM information_schema.columns c
                     JOIN information_schema.tables t
                       ON t.table_schema = c.table_schema AND t.table_name = c.table_name
                    WHERE c.table_schema = 'plenum_cafm'
                      AND t.table_type = 'BASE TABLE'"""
            )
        )
    ).all()
    shape: dict[str, set[str]] = {}
    heavy: set[str] = set()
    for table, column, data_type in rows:
        shape.setdefault(str(table), set()).add(str(column))
        # Tables whose rows can each be very large: jsonb state, embedding vectors, long
        # extracted text. These are read in much smaller batches — see _batch_for.
        if str(data_type) in _WIDE_TYPES:
            heavy.add(str(table))

    included, excluded = org_export.plan_export(shape)

    tables: dict[str, Any] = {}
    for name, rule in sorted(included.items()):
        try:
            n = (
                await session.execute(
                    text(f"SELECT count(*) FROM plenum_cafm.{rule.table} WHERE {rule.where}"),
                    {"org": str(organization_id)},
                )
            ).scalar()
        except Exception as exc:  # noqa: BLE001 — one unreadable table must not lose the file
            log.warning("org_export.count_failed", table=name, error=str(exc)[:200])
            tables[name] = {"rows": None, "error": str(exc)[:160],
                            "scoped_by": rule.kind}
            continue
        tables[name] = {
            "rows": int(n or 0),
            "scoped_by": rule.kind if rule.kind != "parent" else f"parent: {rule.parent}",
        }

    manifest = {
        "organization_id": str(organization_id),
        "taken_at": datetime.utcnow().isoformat() + "Z",
        "schema": "plenum_cafm",
        "tables": tables,
        "excluded": dict(sorted(excluded.items())),
        "row_total": sum(t["rows"] or 0 for t in tables.values()),
        "notes": [
            "Every table here is filtered to this organization. Tables that cannot be "
            "filtered are listed under `excluded` with the reason, and are not in the file.",
            "Credentials (sessions, one-time codes, action tokens) are never exported.",
            "Buildings are identified by the organization-scoped rows that reference them, "
            "because plenum_cafm.sites.organization_id is an integer and cannot be joined to "
            "an organization uuid. A building with nothing recorded against it will not "
            "appear here.",
        ],
    }
    return manifest, included, excluded, heavy


@router.get("/export", summary="Download everything this company holds, as a zip of CSVs")
async def export_organization(
    preview: bool = Query(
        False, description="Return the manifest only — what the file would contain, and what it would not."),
    session: AsyncSession = Depends(get_session),
    scope: access.Scope = Depends(admin_scope),
):
    """One zip: a CSV per table, plus `manifest.json` saying what is in it and what is not.

    Read-only. Admin and superadmin only, and always scoped to the caller's company —
    `admin_scope` owns the act-as-another-company override, so an admin cannot widen this by
    adding a parameter to the URL.

    `?preview=true` returns the manifest alone, with a row count per table and a reason per
    exclusion. It is the honest answer to "is everything really in there", and it costs a
    count rather than a download.
    """
    organization_id = scope.organization_id
    manifest, included, _, heavy = await _export_manifest(session, organization_id)

    if preview:
        return {"ok": True, **manifest}

    # Streamed, not buffered — and the difference is the whole feature working or not.
    #
    # Measured against the live database: building this zip takes ~406 seconds, and the
    # gateway in front of this service sets `proxy_read_timeout 300s`. Buffering the file
    # first meant nginx saw no bytes for 406s and cut the connection at 300, so the download
    # failed every time on anything but a small tenant. That timeout measures the gap
    # BETWEEN reads, so a zip written out as it is produced never approaches it: the first
    # bytes leave within a second and keep coming.
    #
    # zipfile writes to a non-seekable sink by emitting data descriptors, so the archive
    # stays valid without ever seeking back to patch a header. The cost is that the total
    # size is unknown when the response headers go out, so there is no Content-Length —
    # the browser shows a progressing download rather than a percentage.
    org = str(organization_id)
    written: dict[str, int] = {}

    class _Sink:
        """Collects what zipfile writes so it can be handed out between batches."""

        def __init__(self) -> None:
            self._parts: list[bytes] = []
            self._pos = 0

        def write(self, data) -> int:
            b = bytes(data)
            self._parts.append(b)
            self._pos += len(b)
            return len(b)

        def tell(self) -> int:
            return self._pos

        def flush(self) -> None:
            return None

        def seekable(self) -> bool:
            return False

        def drain(self) -> bytes:
            out = b"".join(self._parts)
            self._parts.clear()
            return out

    async def _zip_stream():
        sink = _Sink()
        with zipfile.ZipFile(sink, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, rule in sorted(included.items()):
                count = 0
                try:
                    result = await session.stream(
                        text(f"SELECT * FROM plenum_cafm.{rule.table} WHERE {rule.where}"),
                        {"org": org},
                    )
                    # A table with no rows still gets its file, empty. "Not in the zip" and
                    # "you have none of these" are different findings, and a reader cannot
                    # tell them apart from an absence.
                    with zf.open(f"{name}.csv", "w") as entry:
                        header_done = False
                        async for batch in result.mappings().partitions(_batch_for(name, heavy)):
                            buf = io.StringIO()
                            writer = None
                            for row in batch:
                                if writer is None:
                                    writer = csv.DictWriter(buf, fieldnames=list(row.keys()))
                                    if not header_done:
                                        writer.writeheader()
                                        header_done = True
                                writer.writerow(
                                    {k: ("" if v is None else v) for k, v in row.items()}
                                )
                                count += 1
                            entry.write(buf.getvalue().encode("utf-8"))
                            # Hand the finished bytes to the client before reading the next
                            # batch — this is what keeps the connection alive on a table
                            # like meter_readings, which is half a million rows on its own.
                            chunk = sink.drain()
                            if chunk:
                                yield chunk
                except Exception as exc:  # noqa: BLE001 — one bad table must not lose the file
                    log.warning("org_export.table_failed", table=name, error=str(exc)[:200])
                    manifest["tables"].setdefault(name, {})["error"] = str(exc)[:160]
                    continue
                written[name] = count
                chunk = sink.drain()
                if chunk:
                    yield chunk

            # Written last, so it reports what actually went in rather than what was planned.
            manifest["rows_written"] = written
            manifest["row_total"] = sum(written.values())
            manifest["tables_written"] = len(written)
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, default=str))

        # The central directory is only emitted when ZipFile closes, so this last drain is
        # what makes the archive openable. Dropping it yields a file that looks complete and
        # will not open.
        tail = sink.drain()
        if tail:
            yield tail

        log.info("org_export.done", organization_id=org,
                 tables=len(written), rows=sum(written.values()))

    # Audited before the bytes go out. The alternative is auditing inside the generator,
    # where a client that disconnects halfway leaves no record that an export was started.
    try:
        async with session.begin_nested():
            await write_audit(
                session,
                actor="hoistra-ui",
                action_type="organization.export",
                source_feature=PLATFORM_FEATURE,
                organization_id=organization_id,
                input_payload={"organization_id": org},
                output_payload={"tables_planned": len(included),
                                "rows_planned": manifest["row_total"]},
                detail={"planned": {k: v.get("rows") for k, v in manifest["tables"].items()},
                        "excluded": list(manifest["excluded"])},
            )
        await session.commit()
    except Exception as exc:  # noqa: BLE001 — failing to log must not withhold the export
        log.error("org_export.audit_failed", error=str(exc)[:200])

    stamp = datetime.utcnow().strftime("%Y-%m-%d")
    filename = f"hoistra-export-{org[:8]}-{stamp}.zip"
    return StreamingResponse(
        _zip_stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            # Planned figures: the real ones are only known once the stream ends, and
            # headers go out first. The manifest inside the zip carries what was written.
            "X-Export-Tables-Planned": str(len(included)),
            "X-Export-Rows-Planned": str(manifest["row_total"]),
        },
    )
