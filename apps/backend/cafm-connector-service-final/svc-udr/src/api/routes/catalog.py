"""The table catalogue — which table is for what, searchable by meaning.

GET  /api/catalog                     summary by domain
GET  /api/catalog/search?q=&k=&domain= the tables most likely to answer a question
GET  /api/catalog/{table}             one table's card: purpose, questions, keys, links, columns, samples
POST /api/catalog/rebuild             (superadmin) rebuild every table, or ``{"tables": [...]}``

Reads are for any admin caller, as the rest of svc-udr is: the catalogue describes the schema,
not the data, and its sample rows are redacted. Rebuilding calls a model 221 times and writes,
so it is a superadmin's action.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...db import get_session
from ...services import catalog as cat
from ...services.principal import Principal, require_admin

router = APIRouter()
log = get_logger(__name__)


class RebuildRequest(BaseModel):
    tables: list[str] | None = Field(None, description="Only these tables; omit for every table")
    with_model: bool = Field(True, description="Write purposes with the model; false = heuristic only")


@router.get("")
async def catalog_summary(session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    return await cat.summary(session)


@router.get("/search")
async def catalog_search(
    q: str = Query(..., min_length=2, max_length=500, description="The user's question, as asked"),
    k: int = Query(8, ge=1, le=25),
    domain: str | None = Query(None, pattern="^[a-z]+$"),
    session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin),
) -> dict[str, Any]:
    out = await cat.search(session, q, k=k, domain=domain)
    log.info("catalog.search", method=out.get("method"), hits=len(out.get("tables", [])), q=q[:80])
    return out


@router.get("/{table}")
async def catalog_card(table: str, session: AsyncSession = Depends(get_session), _: Principal = Depends(require_admin)) -> dict[str, Any]:
    c = await cat.card(session, table)
    if c is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail={
            "ok": False, "error": f"No catalogue entry for '{table}'. It may not exist, or the catalogue has not been built.",
            "reason": "not_catalogued"})
    return c


@router.post("/rebuild")
async def catalog_rebuild(
    body: RebuildRequest | None = None,
    session: AsyncSession = Depends(get_session), principal: Principal = Depends(require_admin),
) -> dict[str, Any]:
    if not principal.is_superadmin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail={
            "ok": False, "error": "Rebuilding the catalogue is a superadmin's action.", "reason": "superadmin_required"})
    body = body or RebuildRequest()
    try:
        out = await cat.build(session, only=body.tables, with_model=body.with_model)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail={"ok": False, "error": str(exc)}) from exc
    log.info("catalog.rebuilt", **{k: v for k, v in out.items() if k != "failures"}, failures=len(out["failures"]))
    return {"ok": True, **out}
