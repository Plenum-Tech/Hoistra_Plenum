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

router = APIRouter()
log = get_logger(__name__)

_SCHEMA = settings.db_schema
_COLS = "id, organization_id, name, kind, created_by, created_at"


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
    organization_id: str | None = Query(None),
    session: AsyncSession = Depends(get_session),
) -> dict:
    if organization_id:
        sql = text(
            f"SELECT {_COLS} FROM {_SCHEMA}.saved_spaces "
            f"WHERE organization_id = CAST(:org AS UUID) ORDER BY created_at DESC"
        )
        params: dict[str, Any] = {"org": organization_id}
    else:
        sql = text(
            f"SELECT {_COLS} FROM {_SCHEMA}.saved_spaces "
            f"WHERE organization_id IS NULL ORDER BY created_at DESC"
        )
        params = {}
    rows = (await session.execute(sql, params)).fetchall()
    return {"spaces": [_row_to_dict(r) for r in rows]}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Create a customer-named saved space")
async def create_space(body: SavedSpaceCreateRequest, session: AsyncSession = Depends(get_session)) -> dict:
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
                "org": body.organization_id,
                "name": body.name.strip(),
                "created_by": body.created_by,
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
) -> dict:
    row = (
        await session.execute(
            text(
                f"UPDATE {_SCHEMA}.saved_spaces SET name = :name "
                f"WHERE id = CAST(:id AS UUID) RETURNING {_COLS}"
            ),
            {"id": space_id, "name": body.name.strip()},
        )
    ).first()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Saved space not found")
    await session.commit()
    return _row_to_dict(row)


@router.delete("/{space_id}", summary="Delete a saved space")
async def delete_space(space_id: str, session: AsyncSession = Depends(get_session)) -> dict:
    res = await session.execute(
        text(f"DELETE FROM {_SCHEMA}.saved_spaces WHERE id = CAST(:id AS UUID)"),
        {"id": space_id},
    )
    await session.commit()
    if res.rowcount == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Saved space not found")
    log.info("saved_space.deleted", space_id=space_id)
    return {"deleted": True, "id": space_id}
