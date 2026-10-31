"""Homework 2 tracing tests run without Langfuse, Docker, or an LLM key."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from test_ingestion_corrections import MemoryStore

from src.agents.homework_router import ask
from src.api.homework_identity import HomeworkIdentityMiddleware
from src.api.homework_identity import TestIdentityStore as IdentityStore
from src.api.homework_identity import TestPrincipal as Principal
from src.api.routes.homework_agent import router
from src.observability.homework import (
    effective_prompt_version,
    invoke_traced_model,
    model_trace_config,
    observe_session,
    rendered_prompt_sha256,
    setup_homework_tracing,
    traced_tool_call,
)


@pytest.fixture
def identities(tmp_path):
    path = tmp_path / "identities.json"
    expires = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    data = {
        "buildings": {"b-1": "org-1"},
        "users": [
            {
                "user_id": user,
                "role": "property_manager",
                "organization_id": "org-1",
                "building_ids": ["b-1"],
            }
            for user in ("owner-a", "owner-b")
        ],
        "sessions": [
            {
                "user_id": user,
                "token_sha256": hashlib.sha256(f"{user}-token".encode()).hexdigest(),
                "expires_at": expires,
            }
            for user in ("owner-a", "owner-b")
        ],
    }
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return path


@pytest.fixture
def client(identities, monkeypatch):
    monkeypatch.setenv("HW1_COMPLIANCE_ENABLED", "false")
    app = FastAPI()
    app.include_router(router)
    app.add_middleware(HomeworkIdentityMiddleware, store=IdentityStore(str(identities)))
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def spans(monkeypatch):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "true")
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    with patch(
        "src.observability.homework._get_tracer",
        side_effect=lambda: provider.get_tracer("hoistra.homework.test"),
    ):
        yield exporter
    provider.shutdown()


def _headers(owner="owner-a"):
    return {"Authorization": f"Bearer {owner}-token"}


def test_invalid_bearer_stops_before_dispatch(client):
    with patch("src.api.routes.homework_agent.route_request", new_callable=AsyncMock) as dispatch:
        response = client.post(
            "/api/homework/agent/ask",
            headers={"Authorization": "Bearer invalid"},
            json={"message": "Show work-order status."},
        )
    assert response.status_code == 401
    dispatch.assert_not_awaited()


@pytest.mark.parametrize("field,value", [
    ("role", "compliance_officer"),
    ("user_id", "owner-b"),
    ("organization_id", "other-org"),
    ("building_ids", ["other-building"]),
])
def test_agent_body_rejects_identity_claims(client, field, value):
    body = {"message": "Show work-order status.", field: value}
    with patch("src.api.routes.homework_agent.route_request", new_callable=AsyncMock) as dispatch:
        response = client.post("/api/homework/agent/ask", headers=_headers(), json=body)
    assert response.status_code == 422
    dispatch.assert_not_awaited()


def test_agent_body_rejects_invalid_scenario_id(client):
    with patch("src.api.routes.homework_agent.route_request", new_callable=AsyncMock) as dispatch:
        response = client.post(
            "/api/homework/agent/ask",
            headers=_headers(),
            json={"message": "Show work-order status.", "scenario_id": "bad scenario/id"},
        )
    assert response.status_code == 422
    dispatch.assert_not_awaited()


def test_foreign_principal_cannot_continue_owned_conversation(client):
    store = MemoryStore()
    store.repo.tables.update(sessions={}, events={}, outbox={})
    store.audit = store.repo.audit
    dispatched = AsyncMock(return_value={
        "answer": "Which permitted task?",
        "outcome": "needs_clarification",
        "selected_route": None,
        "tool_calls": [],
    })
    with patch("src.services.compliance_hw1.store.runtime_store", return_value=store), patch(
        "src.agents.homework_router._dispatch", dispatched
    ):
        created = client.post(
            "/api/homework/agent/ask", headers=_headers(), json={"message": "Help"}
        )
        assert created.status_code == 200
        denied = client.post(
            "/api/homework/agent/ask",
            headers=_headers("owner-b"),
            json={
                "message": "Continue",
                "conversation_id": created.json()["conversation_id"],
            },
        )
    assert denied.status_code == 404
    assert dispatched.await_count == 1


@pytest.mark.asyncio
async def test_root_span_records_identity_messages_and_prompt_version(spans):
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))
    store = MemoryStore()
    store.repo.tables.update(sessions={}, events={}, outbox={})
    store.audit = store.repo.audit
    request = SimpleNamespace(
        state=SimpleNamespace(test_principal=principal, request_id="request-1"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    body = SimpleNamespace(
        message="Show work-order status.", conversation_id=None, scenario_id="HW2-ROOT-01"
    )
    dispatched = AsyncMock(return_value={
        "answer": "Which work order?",
        "outcome": "needs_clarification",
        "selected_route": "reads",
        "tool_calls": [],
    })
    with patch("src.services.compliance_hw1.store.runtime_store", return_value=store), patch(
        "src.agents.homework_router._dispatch", dispatched
    ):
        result = await ask(request, body)

    root = next(span for span in spans.get_finished_spans() if span.name == "hoistra.session_message")
    attributes = root.attributes
    assert json.loads(attributes["gen_ai.input.messages"])[0]["role"] == "user"
    assert json.loads(attributes["gen_ai.output.messages"])[0]["role"] == "assistant"
    assert attributes["hoistra.user_role"] == "property_manager"
    assert attributes["hoistra.user_id"] == "owner-a"
    assert attributes["hoistra.organization_id"] == "org-1"
    assert attributes["hoistra.building_ids"] == ("b-1",)
    assert attributes["hoistra.scenario_id"] == "HW2-ROOT-01"
    assert attributes["hoistra.prompt_version"] == result["prompt_version"]
    assert result["otel_trace_id"] == format(root.context.trace_id, "032x")
    stored = next(iter(store.repo.tables["history"].values()))["data"]
    assert stored["otel_trace_id"] == result["otel_trace_id"]
    assert stored["scenario_id"] == "HW2-ROOT-01"


@pytest.mark.asyncio
async def test_allowed_tool_span_is_a_child_and_records_scope(spans):
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))

    async def execute():
        return {"result": {"status": "open"}}

    with observe_session(
        principal, "session-1", "request-1", "Show work-order status."
    ) as root_observation:
        result = await traced_tool_call(
            principal,
            "work_order_status",
            {"work_order_id": "WO-1"},
            execute,
            allowed_tools={"work_order_status"},
        )
        root_observation.finish(
            {
                "answer": "Open.",
                "selected_route": "reads",
                "outcome": "answered",
                "execution_outcome": "answered",
                "actual_tools": [{"tool": "work_order_status"}],
            },
            "prompt-version",
        )

    assert result["result"]["status"] == "open"
    finished = spans.get_finished_spans()
    root = next(span for span in finished if span.name == "hoistra.session_message")
    tool = next(span for span in finished if span.name == "execute_tool work_order_status")
    assert tool.parent.span_id == root.context.span_id
    assert tool.context.trace_id == root.context.trace_id
    assert tool.attributes["gen_ai.operation.name"] == "execute_tool"
    assert tool.attributes["gen_ai.tool.name"] == "work_order_status"
    assert tool.attributes["hoistra.permission_denied"] is False
    assert tool.attributes["hoistra.user_id"] == "owner-a"
    assert json.loads(tool.attributes["gen_ai.tool.call.arguments"])["work_order_id"] == "WO-1"


@pytest.mark.asyncio
async def test_denied_tool_span_records_reason_on_tool_and_root(spans):
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))

    async def deny():
        raise HTTPException(403, "Building outside authenticated scope")

    with (
        observe_session(principal, "session-1", "request-1", "Show another building."),
        pytest.raises(HTTPException),
    ):
        await traced_tool_call(
            principal,
            "work_order_status",
            {"building_id": "b-2"},
            deny,
            allowed_tools={"work_order_status"},
        )

    finished = spans.get_finished_spans()
    root = next(span for span in finished if span.name == "hoistra.session_message")
    tool = next(span for span in finished if span.name == "execute_tool work_order_status")
    assert tool.attributes["hoistra.permission_denied"] is True
    assert tool.attributes["hoistra.permission_denied.reason"] == (
        "Building outside authenticated scope"
    )
    assert root.attributes["hoistra.permission_denied"] is True
    assert tool.status.status_code.name == "ERROR"


@pytest.mark.asyncio
async def test_content_flag_disables_manual_root_and_tool_payloads(spans, monkeypatch):
    monkeypatch.setenv("TRACELOOP_TRACE_CONTENT", "false")
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))

    async def execute():
        return {"result": {"status": "open", "secret": "do-not-export"}}

    with observe_session(
        principal, "session-1", "request-1", "Sensitive request text"
    ) as root_observation:
        await traced_tool_call(
            principal,
            "work_order_status",
            {"authorization": "Bearer secret"},
            execute,
        )
        root_observation.finish(
            {"answer": "Sensitive answer", "actual_tools": []}, "prompt-version"
        )

    finished = spans.get_finished_spans()
    root = next(span for span in finished if span.name == "hoistra.session_message")
    tool = next(span for span in finished if span.name == "execute_tool work_order_status")
    assert root.attributes["hoistra.content_capture_enabled"] is False
    assert "gen_ai.input.messages" not in root.attributes
    assert "gen_ai.output.messages" not in root.attributes
    assert "gen_ai.tool.call.arguments" not in tool.attributes
    assert "gen_ai.tool.call.result" not in tool.attributes


@pytest.mark.asyncio
async def test_tool_payloads_are_redacted_and_bounded(spans):
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))

    async def execute():
        return {"result": "x" * 20_000}

    with observe_session(principal, "session-1", "request-1", "Run tool"):
        await traced_tool_call(
            principal,
            "work_order_status",
            {"authorization": "Bearer secret", "work_order_id": "WO-1"},
            execute,
        )

    tool = next(
        span for span in spans.get_finished_spans()
        if span.name == "execute_tool work_order_status"
    )
    arguments = json.loads(tool.attributes["gen_ai.tool.call.arguments"])
    result = json.loads(tool.attributes["gen_ai.tool.call.result"])
    assert arguments["authorization"] == "[REDACTED]"
    assert arguments["work_order_id"] == "WO-1"
    assert result["capture"] == "omitted_over_limit"
    assert result["bytes"] > 16_384
    assert len(result["sha256"]) == 64


@pytest.mark.asyncio
async def test_failed_request_finalizes_root_metadata(spans):
    principal = Principal("owner-a", "property_manager", "org-1", ("b-1",))
    store = MemoryStore()
    store.repo.tables.update(sessions={}, events={}, outbox={})
    store.audit = store.repo.audit
    request = SimpleNamespace(
        state=SimpleNamespace(test_principal=principal, request_id="request-failed"),
        app=SimpleNamespace(state=SimpleNamespace()),
    )
    body = SimpleNamespace(
        message="Show work-order status.", conversation_id=None, scenario_id="HW2-FAIL-01"
    )
    with patch("src.services.compliance_hw1.store.runtime_store", return_value=store), patch(
        "src.agents.homework_router._dispatch",
        AsyncMock(side_effect=RuntimeError("provider detail must not be exported")),
    ), pytest.raises(RuntimeError):
        await ask(request, body)

    root = next(
        span for span in spans.get_finished_spans() if span.name == "hoistra.session_message"
    )
    assert root.attributes["hoistra.outcome"] == "failed"
    assert root.attributes["hoistra.execution_outcome"] == "failed"
    assert root.attributes["hoistra.selected_route"] == "reads"
    assert root.attributes["hoistra.tool_count"] == 0
    assert root.attributes["hoistra.failure.type"] == "RuntimeError"
    assert len(root.attributes["hoistra.prompt_version"]) == 64
    assert "provider detail" not in str(root.attributes)
    assert root.status.status_code.name == "ERROR"


def test_model_trace_config_exposes_only_valid_rendered_prompt_hash():
    assert model_trace_config("a" * 64) == {
        "metadata": {"rendered_prompt_sha256": "a" * 64}
    }
    with pytest.raises(ValueError, match="lowercase SHA-256"):
        model_trace_config("not-a-hash")
    assert rendered_prompt_sha256("prompt", {}) == hashlib.sha256(b"prompt").hexdigest()


@pytest.mark.asyncio
async def test_model_adapter_passes_trace_config_without_breaking_legacy_test_models():
    class ModernModel:
        def __init__(self):
            self.call = AsyncMock(return_value="modern result")

        async def ainvoke(self, messages, config=None):
            return await self.call(messages, config=config)

    modern = ModernModel()
    assert await invoke_traced_model(modern, ["message"], "a" * 64) == "modern result"
    modern.call.assert_awaited_once_with(
        ["message"], config=model_trace_config("a" * 64)
    )

    class LegacyModel:
        async def ainvoke(self, messages):
            return messages

    assert await invoke_traced_model(LegacyModel(), ["legacy"], "b" * 64) == ["legacy"]


def test_effective_prompt_version_is_stable_and_sensitive_to_prompt_sources():
    registry = {
        "manifest_sha256": "unified-hash",
        "answer_instruction_sha256": "answer-hash",
        "enabled_profiles": {"energy": {"manifest_sha256": "energy-hash"}},
    }
    baseline = effective_prompt_version(registry, "energy", answer_prompt_used=True)
    assert baseline == effective_prompt_version(registry, "energy", answer_prompt_used=True)
    changed = {**registry, "answer_instruction_sha256": "changed-answer-hash"}
    assert baseline != effective_prompt_version(changed, "energy", answer_prompt_used=True)


def test_disabled_tracing_is_noop_and_incomplete_enabled_config_fails(monkeypatch):
    for key in (
        "HW2_TRACING_ENABLED",
        "LANGFUSE_PUBLIC_KEY",
        "LANGFUSE_SECRET_KEY",
        "LANGFUSE_HOST",
    ):
        monkeypatch.delenv(key, raising=False)
    assert setup_homework_tracing().enabled is False
    monkeypatch.setenv("HW2_TRACING_ENABLED", "true")
    with pytest.raises(ValueError, match="LANGFUSE_PUBLIC_KEY"):
        setup_homework_tracing()
