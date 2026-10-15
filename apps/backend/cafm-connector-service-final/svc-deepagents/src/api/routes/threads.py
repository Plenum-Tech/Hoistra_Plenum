"""The caller's conversations, kept on the server (services/chat_threads.py).

    GET    /api/threads?limit=&q=&before=   the caller's threads in the company they are acting for, a page at a time
    GET    /api/threads/{id}          one thread with its turns
    PATCH  /api/threads/{id}          rename
    DELETE /api/threads/{id}          hide it from the list (the rows are kept)

A thread belongs to the account that asked, in the company it was acting for at the time - the
same two keys the browser's navigator already filters on. Nobody else lists or opens it.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from ...http_client import caller_authorization, caller_organization_id
from ...services import chat_threads
from ...services.principal import Principal, caller_principal, current_principal

router = APIRouter(prefix="/api/threads", tags=["Threads"])


def _act(principal: Principal, organization_id: str | None) -> None:
    """The same scope the workflow routes set: own company, or the one a superadmin views as."""
    caller_principal.set(principal)
    if organization_id and principal.role != "superadmin" and str(principal.organization_id) != str(organization_id):
        raise HTTPException(status_code=403, detail={"ok": False, "error": "Not your company."})
    caller_organization_id.set(str(organization_id) if organization_id else
                               (str(principal.organization_id) if principal.organization_id else None))


@router.get("")
async def list_threads(limit: int = Query(60, ge=1, le=200), q: str | None = Query(None, max_length=120),
                       before: str | None = Query(None, max_length=40),
                       organization_id: str | None = Query(None),
                       principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    return {"ok": True, "available": chat_threads.ready(),
            "threads": await chat_threads.list_threads(limit=limit, q=q, before=before)}


@router.get("/{thread_id}")
async def get_thread(thread_id: str, organization_id: str | None = Query(None),
                     principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    t = await chat_threads.get_thread(thread_id)
    if t is None:
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Thread not found"})
    return {"ok": True, "thread": t}


class ThreadPatch(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)


@router.patch("/{thread_id}")
async def rename_thread(thread_id: str, body: ThreadPatch, organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    if not await chat_threads.rename_thread(thread_id, body.title):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Thread not found"})
    return {"ok": True}


@router.delete("/{thread_id}")
async def delete_thread(thread_id: str, organization_id: str | None = Query(None),
                        principal: Principal = Depends(current_principal)) -> dict[str, Any]:
    _act(principal, organization_id)
    if not await chat_threads.delete_thread(thread_id):
        raise HTTPException(status_code=404, detail={"ok": False, "error": "Thread not found"})
    return {"ok": True}
