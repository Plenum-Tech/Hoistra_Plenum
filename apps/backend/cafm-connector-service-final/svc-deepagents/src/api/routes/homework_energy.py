"""Role- and building-guarded Energy reads for the isolated Homework service."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError
from ...services.energy_hw1.service import EnergyService, TOOLS

router = APIRouter(prefix="/api/homework/energy", tags=["Isolated Energy"])


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=100)
    arguments: dict = Field(default_factory=dict)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)
    conversation_id: str | None = None


def service(request):
    if not getattr(request.app.state, "hw1_energy_enabled", False):
        raise HTTPException(503, "Energy profile not enabled")
    return EnergyService()


@router.get("/tools")
async def tools(request: Request):
    instance = service(request)
    return {"tools": sorted(TOOLS) if request.state.test_principal.role in {"property_manager", "portfolio_manager"} else []}


@router.post("/tools/execute")
async def execute(body: ExecuteRequest, request: Request):
    try:
        return await service(request).execute(request.state.test_principal, body.tool,
            body.arguments, request.state.request_id)
    except SQLAlchemyError:
        raise HTTPException(503, "Energy source store unavailable; no result claimed") from None
    except (ValueError, TypeError, KeyError, ArithmeticError):
        raise HTTPException(409, "Energy source values are incompatible; no result claimed") from None


@router.post("/ask")
async def ask(body: AskRequest, request: Request):
    from ...agents.homework_energy_agent import ask as plan
    from ...llm_factory import create_chat_model
    try:
        return await plan(request.state.test_principal, body, service(request), create_chat_model(), request.state.request_id)
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(503, "Energy source store unavailable; no result claimed") from None
    except Exception:
        raise HTTPException(502, "Energy planner unavailable; no result claimed") from None
