from fastapi import APIRouter
from pydantic import BaseModel

from ...llm_factory import active_provider_snapshot

router = APIRouter(tags=["Health"])


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str


class LLMProviderHealthResponse(BaseModel):
    status: str
    service: str
    provider: str
    details: dict


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    """Liveness probe — returns 200 when the service is up."""
    return HealthResponse(status="ok", service="svc-deepagents", version="1.0.0")


@router.get("/health/llm-provider", response_model=LLMProviderHealthResponse)
async def health_llm_provider() -> LLMProviderHealthResponse:
    """Runtime LLM provider visibility (no secrets)."""
    details = active_provider_snapshot()
    return LLMProviderHealthResponse(
        status="ok",
        service="svc-deepagents",
        provider=str(details.get("provider", "unknown")),
        details=details,
    )
