"""Export only the authenticated principal's bounded tool execution metadata."""
from fastapi import HTTPException
from .energy import export_tools

from ..compliance_hw1.store import runtime_store
from ...api.homework_identity import TestPrincipal


class TraceService:
    def __init__(self, store=None):
        self.store = store or runtime_store()

    async def export(self, principal: TestPrincipal, conversation_id=None, limit=50):
        if not isinstance(principal, TestPrincipal):
            raise HTTPException(401, "Trusted principal required")
        if not 1 <= limit <= 100:
            raise HTTPException(422, "Trace export limit must be between 1 and 100")
        if hasattr(self.store,'audit'):await self.store.audit(principal,'homework_trace_export','capability_authorized')
        async with self.store.transaction(principal, repeatable=True) as repo:
            if conversation_id:
                session = await repo.get("sessions", conversation_id)
                if (session["data"].get("owner") != principal.user_id
                        or session["data"].get("role") != principal.role
                        or not set(session["buildings"]) <= set(principal.building_ids)):
                    raise HTTPException(404, "Conversation unavailable")
            central=await repo.list('history','unified_trace')
            central_requests={r['data'].get('request_id') for r in central}
            legacy=[r for r in await repo.list('history','trace') if r['data'].get('request_id') not in central_requests]
            rows = [r for r in central+legacy
                    if r["data"].get("actor") == principal.user_id
                    and r["data"].get("role") == principal.role
                    and (conversation_id is None or r["data"].get("conversation_id") == conversation_id)
                    and set(r["buildings"]) <= set(principal.building_ids)]
            rows.sort(key=lambda r: r["data"].get("generated_at", ""), reverse=True)
            result={"traces": [{"trace_id": r["id"], "conversation_id": r["data"].get("conversation_id"),
                "request_id": r["data"].get("request_id"), "intent": r["data"].get("intent"),
                "selected_route": r["data"].get("selected_route"),
                "actual_tools": export_tools(principal, r["data"].get("actual_tools", [])), "outcome": r["data"].get("outcome"),
                "prompt_metadata": r["data"].get("prompt_metadata"),
                "unified_metadata":r['data'].get('unified_metadata'),
                "provider_execution":r['data'].get('provider_execution'),
                "answer_generation":r["data"].get("answer_generation"),
                "execution_outcome":r["data"].get("execution_outcome", r["data"].get("outcome")),
                "policy_version": r["data"].get("policy_version"),
                "generated_at": r["data"].get("generated_at")} for r in rows[:limit]],
                "returned": min(len(rows), limit), "total_matching": len(rows),
                "conversation_content_included": False, "owner": principal.user_id}
            await repo.audit("homework_trace_export","succeeded",{
                "conversation_id":conversation_id,"returned":result["returned"],"limit":limit})
            return result
