"""Protected read entry points sharing one tool-level authorization dispatcher."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy.exc import SQLAlchemyError

from ...services.homework_reads import (
    ReadArguments, plan_and_read, runtime_dispatcher,
)

router = APIRouter(prefix="/api/homework", tags=["Homework scoped reads"])


class ExecuteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tool: str = Field(min_length=1, max_length=160)
    arguments: ReadArguments = Field(default_factory=ReadArguments)


class AskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    message: str = Field(min_length=1, max_length=8000)


def get_dispatcher(request):
    # Tests inject a repository; runtime uses the existing isolated DB connection factory.
    custom = getattr(request.app.state, "homework_read_dispatcher", None)
    if custom is not None:
        return custom
    return runtime_dispatcher()


async def run_read(request, tool, arguments, document_only=False):
    try:
        return await get_dispatcher(request).execute(request.state.test_principal, tool, arguments,
            request.state.request_id, "document" if document_only else "direct_tool", document_only)
    except SQLAlchemyError:
        raise HTTPException(503, "Homework read store unavailable") from None


@router.get("/tools")
async def tools(request: Request):
    return {"tools": get_dispatcher(request).allowed_tools(request.state.test_principal),
            "mode": "read_only"}


@router.post("/tools/execute")
async def execute(body: ExecuteRequest, request: Request):
    return await run_read(request, body.tool, body.arguments)


@router.get("/documents/{tool}/{record_id}")
async def document(tool: str, record_id: str, request: Request):
    # Viewing and downloading use the SAME record and capability checks as tool calls.
    return await run_read(request, tool, ReadArguments(record_id=record_id), document_only=True)


@router.post("/ask")
async def ask(body: AskRequest, request: Request):
    from ...llm_factory import create_chat_model
    try:
        return await plan_and_read(body.message, request.state.test_principal,
                                   get_dispatcher(request), create_chat_model(), request.state.request_id)
    except ValidationError:
        await get_dispatcher(request).repository.audit(request.state.test_principal, "", {},
            "invalid_tool_arguments", request.state.request_id, "model_planner", "validation")
        raise HTTPException(422, "Planner supplied invalid read arguments") from None
    except HTTPException:
        raise
    except SQLAlchemyError:
        raise HTTPException(503, "Homework read store unavailable") from None
    except Exception:
        raise HTTPException(502, "Homework read planner unavailable") from None
