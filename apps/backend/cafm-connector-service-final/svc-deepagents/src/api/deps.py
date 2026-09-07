"""
FastAPI dependency that provides the singleton DeepAgentOrchestrator instance.
The orchestrator is initialised once during application lifespan and shared
across all requests — it holds no per-request state.
"""
from fastapi import HTTPException, Request

from ..agents.orchestrator import DeepAgentOrchestrator


def get_orchestrator(request: Request) -> DeepAgentOrchestrator:
    orchestrator: DeepAgentOrchestrator | None = getattr(request.app.state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(status_code=503, detail="Orchestrator not initialised")
    return orchestrator
