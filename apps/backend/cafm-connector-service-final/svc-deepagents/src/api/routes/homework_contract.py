"""Guarded Contract Performance routes for the isolated Homework profile."""
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from ...agents.contract_prompt import allowed_tools
from ...services.contract_hw1.contracts import Ask, ConfigConfirmation, ToolRequest
from ...services.contract_hw1.service import ContractService

router = APIRouter(prefix="/api/homework/contract", tags=["Isolated Contract Performance"])


def service(request):
    if not getattr(request.app.state, "hw1_contract_enabled", False):
        raise HTTPException(503, "Contract Performance profile not enabled")
    return ContractService()


@router.get("/status")
async def status(request: Request):
    service(request)
    return {"enabled": True, "profile": "hw1-contract-performance",
            "specification_version": "0.37.0", "manifest_version": "1.0.0",
            "policy_version": "hw1-contract-v1", "max_tool_calls": 4,
            "confirmation": "Portfolio Manager human API/CLI only",
            "simulation": "fictional sources; no payment, external WO operation or real mail"}


@router.get("/tools")
async def tools(request: Request):
    service(request)
    return {"tools": allowed_tools(request.state.test_principal),
            "profile": "hw1-contract-performance"}


@router.post("/tools/execute")
async def execute(body: ToolRequest, request: Request):
    return await service(request).execute(request.state.test_principal,
        body.tool, body.arguments, request.state.request_id)


@router.get("/scorecards/{identifier}")
async def scorecard(identifier: str, request: Request):
    return await service(request).execute(request.state.test_principal,
        "contract_get_scorecard", {"scorecard_id": identifier}, request.state.request_id)


@router.post("/configuration/proposals/{identifier}/confirm")
async def confirm(identifier: str, body: ConfigConfirmation, request: Request):
    return await service(request).confirm_configuration(request.state.test_principal,
        identifier, body.model_dump(mode="json"), request.state.request_id)


@router.post("/ask")
async def ask(body: Ask, request: Request):
    from ...llm_factory import create_chat_model
    from ...agents.homework_contract_agent import ask as plan
    instance = service(request)
    try:
        return await plan(request.state.test_principal, body, instance,
                          create_chat_model(), request.state.request_id)
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(503, "Contract Performance store unavailable; no success claimed") from None
    except Exception:
        raise HTTPException(502, "Contract Performance planner unavailable; no success claimed") from None
