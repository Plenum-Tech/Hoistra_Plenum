"""GET /api/trace-lab/status: is Trace Lab's schema verified in this process (always 200 for readers).

Company admins and superadmins only (auth.require_reader). The answer names no table or schema:
the page shows whether Trace Lab is available and which versions it expects and found; the log
says why not. A state that is not ready is verified again when it is older than 30 s, so a
version Hussain applies starts working without a restart. Every other Trace Lab route depends on
db.require_ready instead.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from src.services.principal import Principal

from . import auth, db

router = APIRouter(tags=["Trace Lab"])


@router.get("/status")
async def trace_lab_status(principal: Principal = auth.caller,
                           organization_id: str | None = Query(None)) -> dict[str, Any]:
    auth.require_reader(principal, organization_id)
    st = await db.refresh_state()
    return {"ok": True, "available": st.status == "ready", "status": st.status,
            "expected_version": st.expected_version, "applied_version": st.applied_version,
            "checked_at": st.checked_at}
