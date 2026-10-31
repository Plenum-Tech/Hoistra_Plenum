"""The only enabled business-tool boundary in isolated Homework 1 mode."""
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from uuid import uuid4

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from ..api.homework_identity import TestPrincipal

MANAGERS = frozenset({"property_manager", "portfolio_manager"})
ALL_ROLES = MANAGERS | {"general_user", "compliance_officer"}


@dataclass(frozen=True)
class ReadCapability:
    roles: frozenset[str]
    fields: tuple[str, ...]
    document: bool = False


# A deliberately small allowlist replaces arbitrary SQL and generic CRUD access.
CAPABILITIES = {
    "building_records": ReadCapability(MANAGERS,
        ("record_type", "title", "details", "source_reference")),
    "building_documents": ReadCapability(MANAGERS,
        ("document_type", "title", "content", "source_reference"), True),
    "asset_information": ReadCapability(MANAGERS,
        ("asset_id", "model", "details", "source_reference")),
    "work_order_status": ReadCapability(MANAGERS | {"general_user"},
        ("work_order_id", "status", "asset_id", "due_at", "source_reference")),
    "asset_conditions": ReadCapability(MANAGERS | {"general_user"},
        ("asset_id", "condition", "observed_at", "source_reference")),
    "certificate_status": ReadCapability(ALL_ROLES,
        ("certificate_id", "subject_type", "subject_id", "status", "expiry_date", "source_reference")),
    "inspection_reports": ReadCapability(MANAGERS,
        ("title", "content", "inspection_date", "source_reference"), True),
    "certificate_documents": ReadCapability(MANAGERS | {"compliance_officer"},
        ("title", "content", "expiry_date", "source_reference"), True),
    "compliance_evidence": ReadCapability(MANAGERS | {"compliance_officer"},
        ("title", "content", "certificate_id", "source_reference"), True),
    "contract_documents": ReadCapability(MANAGERS,
        ("title", "content", "vendor_id", "source_reference"), True),
    "invoice_documents": ReadCapability(MANAGERS,
        ("title", "content", "vendor_id", "source_reference"), True),
    "energy_findings": ReadCapability(MANAGERS,
        ("meter_id", "finding", "period", "source_reference")),
    "invoice_findings": ReadCapability(MANAGERS,
        ("invoice_id", "finding", "source_reference")),
    "vendor_performance": ReadCapability(MANAGERS,
        ("vendor_id", "finding", "source_reference")),
    "work_order_sla": ReadCapability(MANAGERS,
        ("work_order_id", "finding", "source_reference")),
    "usage_reports": ReadCapability(ALL_ROLES,
        ("period", "category", "summary", "source_reference")),
}


class ReadArguments(BaseModel):
    # Neither a model nor an HTTP client can introduce identity, SQL or scope overrides.
    model_config = ConfigDict(extra="forbid")
    building_ids: list[str] | None = Field(default=None, min_length=1, max_length=100)
    record_id: str | None = Field(default=None, min_length=1, max_length=160)
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0, le=10000)


class PostgresReadRepository:
    def __init__(self, session_factory):
        self.session_factory = session_factory

    async def audit(self, principal, tool, arguments, outcome, request_id, entry_path, event_type="authorization"):
        # Log the attempted target, never token values or inaccessible record contents.
        async with self.session_factory() as session:
            await session.execute(text("""
                INSERT INTO hw1_access.authorization_audit
                    (event_id, request_id, user_id, organization_id, role, tool, target,
                     outcome, entry_path, event_type, policy_version)
                VALUES (:event, :request, :user, :organization, :role, :tool,
                        CAST(:target AS jsonb), :outcome, :entry, :event_type, 'hw1-point2-v1')
            """), {"event": str(uuid4()), "request": request_id,
                    "user": principal.user_id if principal else None,
                    "organization": principal.organization_id if principal else None,
                    "role": principal.role if principal else None,
                    "tool": tool if tool in CAPABILITIES else "unregistered_or_not_applicable",
                    "target": json.dumps(arguments), "outcome": outcome,
                    "entry": entry_path, "event_type": event_type})
            await session.commit()

    async def read(self, principal, tool, arguments):
        capability = CAPABILITIES[tool]
        params = {"organization": principal.organization_id, "kind": tool,
                  "buildings": arguments.building_ids, "record_id": arguments.record_id,
                  "limit": arguments.limit, "offset": arguments.offset,
                  "owner": principal.user_id if principal.role == "general_user" and tool == "usage_reports" else None,
                  "category": "compliance" if principal.role == "compliance_officer" and tool == "usage_reports" else None}
        # These predicates apply to the count and page BEFORE any records are fetched.
        # Mixed-scope documents are withheld in full rather than partially leaked.
        where = """
            organization_id = :organization AND kind = :kind
            AND building_id = ANY(CAST(:buildings AS text[]))
            AND content_building_ids <@ CAST(:buildings AS text[])
            AND cardinality(content_building_ids) > 0
            AND building_id = ANY(content_building_ids)
            AND scope_verified = true AND contains_sensitive_data = false
            AND (CAST(:record_id AS text) IS NULL OR record_id = :record_id)
            AND (CAST(:owner AS text) IS NULL OR owner_user_id = :owner)
            AND (CAST(:category AS text) IS NULL OR category = :category)
        """
        # Field names come only from the server registry; values remain SQL parameters.
        projection = []
        for index, field in enumerate(capability.fields):
            params[f"field{index}"] = field
            projection.append(f"payload -> :field{index} AS {field}")
        async with self.session_factory() as session:
            # Count and page must describe the same snapshot, even during concurrent writes.
            await session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            count = (await session.execute(text(
                "SELECT count(*) FROM hw1_access.read_records WHERE " + where), params)).scalar_one()
            rows = (await session.execute(text(
                "SELECT record_id, building_id, " + ", ".join(projection) +
                " FROM hw1_access.read_records WHERE " + where +
                " ORDER BY record_id LIMIT :limit OFFSET :offset"), params)).mappings().all()
        return {"records": [dict(row) for row in rows], "total_matching": count,
                "returned": len(rows), "offset": arguments.offset,
                "complete": arguments.offset == 0 and len(rows) == count,
                "next_offset": arguments.offset + arguments.limit
                if arguments.offset + arguments.limit < count else None,
                "data_source": "fictional_homework_permission_fixtures"}


class HomeworkReadDispatcher:
    """All direct calls and model-selected tools must execute through this class."""

    def __init__(self, repository):
        self.repository = repository

    def allowed_tools(self, principal: TestPrincipal):
        return [name for name, spec in CAPABILITIES.items() if principal.role in spec.roles]

    async def execute(self, principal: TestPrincipal, tool: str, arguments: ReadArguments,
                      request_id=None, entry_path="direct_function", document_only=False):
        request_id = request_id or str(uuid4())
        if not isinstance(principal, TestPrincipal):
            raise HTTPException(401, "Trusted principal required")
        if not isinstance(arguments, ReadArguments):
            raise HTTPException(422, "Validated read arguments required")
        buildings = list(principal.building_ids) if arguments.building_ids is None else arguments.building_ids
        target = {"building_ids": buildings, "record_id": arguments.record_id}
        capability = CAPABILITIES.get(tool)
        if (capability is None or principal.role not in capability.roles
                or (document_only and not capability.document)):
            await self.repository.audit(principal, tool, target, "denied_capability", request_id, entry_path)
            raise HTTPException(403, "Tool operation not permitted")
        if not buildings or any(b not in principal.building_ids for b in buildings):
            await self.repository.audit(principal, tool, target, "denied_scope", request_id, entry_path)
            raise HTTPException(403, "Building access denied")
        scoped = arguments.model_copy(update={"building_ids": sorted(set(buildings))})
        # Persist permission BEFORE executing; an audit failure must never expose a result.
        await self.repository.audit(principal, tool, target, "allowed", request_id, entry_path)
        try:
            result = await self.repository.read(principal, tool, scoped)
        except Exception:
            await self.repository.audit(principal, tool, target, "failed", request_id, entry_path, "execution")
            raise HTTPException(503, "Homework read unavailable") from None
        if scoped.record_id and not result["records"]:
            await self.repository.audit(principal, tool, target, "not_found_or_inaccessible", request_id, entry_path, "execution")
            raise HTTPException(404, "Record unavailable")
        # A failed audit prevents returning data; silently dropping audit is not acceptable.
        await self.repository.audit(principal, tool, target, "succeeded", request_id, entry_path, "execution")
        return {"tool": tool, "scope": scoped.building_ids, "request_id": request_id,
                "policy_version": "hw1-point2-v1", **result}


@lru_cache(maxsize=1)
def runtime_dispatcher():
    value = os.environ.get("HW1_READ_DB_URL", "")
    try:
        url = make_url(value)
        if url.drivername != "postgresql+asyncpg" or not url.password or url.username != "hw1_reader":
            raise ValueError()
    except Exception:
        raise ValueError("HW1_READ_DB_URL must configure the dedicated hw1_reader connection") from None
    # The DB role has SELECT on fixture reads and INSERT-only access on audit events.
    engine = create_async_engine(url, pool_pre_ping=True, pool_size=3, max_overflow=2)
    return HomeworkReadDispatcher(PostgresReadRepository(async_sessionmaker(engine, expire_on_commit=False)))


async def audit_boundary(principal, request_id, entry_path, outcome, event_type="security"):
    await runtime_dispatcher().repository.audit(principal, "", {}, outcome,
                                               request_id, entry_path, event_type)


async def plan_and_read(message, principal, dispatcher, model, request_id=None):
    """Stateless read-only planner; no legacy specialists, shortcuts or tool fallback."""
    tools = [{"type": "function", "function": {"name": name,
              "description": "Read scoped " + name.replace("_", " ") + ". No writes or approvals.",
              "parameters": ReadArguments.model_json_schema()}}
             for name in dispatcher.allowed_tools(principal)]
    from langchain_core.messages import HumanMessage, SystemMessage
    from ..agents.homework_read_prompt import assemble_homework_read_prompt
    from ..agents.prompt_assembly import PromptAssemblyError
    try:
        prompt = assemble_homework_read_prompt(principal)
    except PromptAssemblyError:
        raise HTTPException(503, "Homework prompt configuration unavailable") from None
    response = await model.bind_tools(tools).ainvoke([
        SystemMessage(content=prompt.text),
        HumanMessage(content=message)])
    from .usage_hw1.service import record_measurement
    await record_measurement(principal, request_id or str(uuid4()), "read_only", None,
                             getattr(response, "usage_metadata", None))
    calls = response.tool_calls
    if not calls:
        return {"answer": "Please request work-order status, asset conditions, certificates or another permitted read.",
                "tool_calls": [], "success": True, "prompt_metadata": prompt.metadata(),
                "provider_execution":{"successful_responses":1,"selected_tools":[]}}
    if len(calls) != 1:
        await dispatcher.repository.audit(principal, "", {}, "multiple_tool_calls_rejected",
            request_id or str(uuid4()), "model_planner", "validation")
        raise HTTPException(422, "Request one read operation at a time")
    call = calls[0]
    try:
        args = ReadArguments.model_validate(call["args"])
    except ValidationError:
        await dispatcher.repository.audit(principal, "", {}, "invalid_tool_arguments",
            request_id or str(uuid4()), "model_planner", "validation")
        raise HTTPException(422, "Planner supplied invalid read arguments") from None
    from ..agents.unified_prompt import CONTEXT
    unified=CONTEXT.get()
    if unified is not None:
        unified['read_attempt']={'tool':call['name'],'succeeded':False}
        unified['read_prompt_metadata']=prompt.metadata()
        unified['read_provider']={'successful_responses':1,'selected_tools':[call['name']]}
    result = await dispatcher.execute(principal, call["name"], args, request_id, "model_planner")
    if unified is not None:unified['read_attempt']['succeeded']=True
    # Render only verified results, not unsupported free-text claims from the model.
    label = (f"Showing {result['returned']} of {result['total_matching']} matching records "
             f"in scope {', '.join(result['scope'])}. "
             + ("Complete result. " if result['complete'] else "Partial result; this is not a whole-scope summary. "))
    return {"answer": label + json.dumps(result, default=str), "success": True,
            "prompt_metadata": prompt.metadata(),
            "provider_execution":{"successful_responses":1,"selected_tools":[call["name"]]},
            "tool_calls": [{"tool": call["name"], "input": args.model_dump(), "output": result}]}
