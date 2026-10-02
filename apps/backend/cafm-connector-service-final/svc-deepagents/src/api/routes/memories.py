"""What the chat remembers for the caller's company (services/chat_memories.py).

    GET    /api/memories          the memories the caller can see: the company's shared facts and
                                  corrections, plus their own preferences
    DELETE /api/memories/{id}     forget one (own, or any as an admin)
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from ...http_client import caller_organization_id
from ...services import chat_memories
from ...services.principal import Principal, caller_principal, current_principal

router = APIRouter(prefix="/api/memories", tags=["Memories"])


def _act(principal: Principal, organization_id: str | None) -> None:
    caller_principal.set(principal)
    if organization_id and principal.role != "superadmin" and str(principal.organization_id) != str(organization_id):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Not your company."})
    caller_organization_id.set(str(organization_id) if organization_id else
                               (str(principal.organization_id) if principal.organization_id else None))


@router.get("")
async def list_memories(limit: int = Query(200, ge=1, le=2000), organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    rows = await chat_memories.visible(limit=limit)
    for r in rows:
        r.pop("embedding", None)
        for k in ("created_at", "last_used_at"):
            if r.get(k) is not None and hasattr(r[k], "isoformat"):
                r[k] = r[k].isoformat()
    return {"ok": True, "available": chat_memories.ready(), "memories": rows, "can_manage": principal.is_admin}


@router.delete("/{memory_id}")
async def forget_memory(memory_id: str, organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    if not await chat_memories.forget(memory_id):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Memory not found, or not yours to remove."})
    return {"ok": True}
