"""
Customer-named saved spaces (WP-3).

Lets the FM create their own LHS buckets (e.g. "Tower 3 certificates", "Vendor X")
beyond the fixed built-in spaces. Persisted server-side so the named spaces show up
across devices. Raw SQL, consistent with the rest of svc-udr.
"""
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...config import settings
from ...core.logging import get_logger
from ...services.principal import Principal, current_principal, organization_for

router = APIRouter()
log = get_logger(__name__)

_SCHEMA = settings.db_schema
_COLS = "id, organization_id, name, kind, created_by, created_at"


def _visible(principal: Principal) -> tuple[str, dict[str, Any]]:
    """The predicate that limits saved spaces to ones this caller may see.

    A superadmin sees all of them. Everyone else sees their own company's, plus the rows
    that carry no company at all: those predate this scoping and were visible to everyone,
    and hiding them would empty the panel for every existing user rather than protect
    anything. New spaces are stamped with the caller's company, so the null set does not grow.
    """
    if principal.is_superadmin:
        return "", {}
    return (" AND (organization_id = CAST(:scope_org AS UUID) OR organization_id IS NULL)",
            {"scope_org": str(principal.organization_id) if principal.organization_id else None})


class SavedSpaceCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    organization_id: str | None = None
    created_by: str | None = None


class SavedSpaceRenameRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


def _row_to_dict(row: Any) -> dict:
    m = row._mapping
    created = m["created_at"]
    return {
        "id": str(m["id"]),
        "organization_id": str(m["organization_id"]) if m["organization_id"] else None,
        "name": m["name"],
        "kind": m["kind"],
        "created_by": m["created_by"],
        "created_at": created.isoformat() if hasattr(created, "isoformat") else str(created),
    }


@router.get("", summary="List customer-named saved spaces")
async def list_spaces(
    organization_id: str | None = Query(
        None, description="Superadmin only; anyone else gets their own company."),
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    """The caller's saved spaces.

    The company used to come from the query string, so asking for another one returned it.
    It now comes from the token; naming a different company is 403 unless you are a
    superadmin, rather than silently answered.
    """
    organization_for(principal, organization_id)
    clause, params = _visible(principal)
    if principal.is_superadmin and organization_id:
        clause = " AND organization_id = CAST(:scope_org AS UUID)"
        params = {"scope_org": organization_id}
    sql = text(
        f"SELECT {_COLS} FROM {_SCHEMA}.saved_spaces WHERE TRUE{clause} ORDER BY created_at DESC"
    )
    rows = (await session.execute(sql, params)).fetchall()
    return {"spaces": [_row_to_dict(r) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a customer-named saved space")
async def create_space(
    body: SavedSpaceCreateRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Create a saved space in the caller's company.

    ``organization_id`` in the body is honoured only for a superadmin; for anyone else it
    must be their own company or absent. ``created_by`` is the signed-in caller, not a
    string the request chose for itself.
    """
    org = organization_for(principal, body.organization_id)
    space_id = str(uuid.uuid4())
    row = (
        await session.execute(
            text(
                f"""
                INSERT INTO {_SCHEMA}.saved_spaces ({_COLS})
                VALUES (CAST(:id AS UUID), CAST(:org AS UUID), :name, 'custom', :created_by, now())
                RETURNING {_COLS}
                """
            ),
            {
                "id": space_id,
                "org": str(org) if org else None,
                "name": body.name.strip(),
                "created_by": principal.email or str(principal.user_id),
            },
        )
    ).first()
    await session.commit()
    log.info("saved_space.created", space_id=space_id, name=body.name)
    return _row_to_dict(row)


@router.patch("/{space_id}", summary="Rename a saved space")
async def rename_space(
    space_id: str,
    body: SavedSpaceRenameRequest,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Rename a saved space the caller may see. One in another company is 404, not 403 —
    the answer to "does this id exist elsewhere" is not the caller's to have."""
    clause, params = _visible(principal)
    row = (
        await session.execute(
            text(
                f"UPDATE {_SCHEMA}.saved_spaces SET name = :name "
                f"WHERE id = CAST(:id AS UUID){clause} RETURNING {_COLS}"
            ),
            {"id": space_id, "name": body.name.strip(), **params},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Saved space not found")
    await session.commit()
    return _row_to_dict(row)


@router.delete("/{space_id}", summary="Delete a saved space")
async def delete_space(
    space_id: str,
    session: AsyncSession = Depends(get_session),
    principal: Principal = Depends(current_principal),
) -> dict:
    """Delete a saved space the caller may see. One in another company is 404."""
    clause, params = _visible(principal)
    res = await session.execute(
        text(f"DELETE FROM {_SCHEMA}.saved_spaces WHERE id = CAST(:id AS UUID){clause}"),
        {"id": space_id, **params},
    )
    await session.commit()
    if res.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Saved space not found")
    log.info("saved_space.deleted", space_id=space_id)
    return {"deleted": True, "id": space_id}
