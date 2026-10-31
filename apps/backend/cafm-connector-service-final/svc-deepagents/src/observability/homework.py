"""OpenTelemetry tracing for the isolated Homework agent.

The Homework deployment sends ordinary OpenTelemetry spans directly to
Langfuse's OTLP endpoint.  Keeping the exporter standards-based avoids tying
authorization or agent execution to the availability of a vendor SDK.
"""

from __future__ import annotations

import base64
import hashlib
import inspect
import json
import logging
import os
import re
from collections.abc import Awaitable, Callable, Collection, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import Span, SpanKind, Status, StatusCode

log = logging.getLogger(__name__)

_INSTRUMENTATION_NAME = "hoistra.homework"
_MAX_CAPTURE_BYTES = 16_384
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|cookie|password|secret|token|api[_-]?key)", re.IGNORECASE
)


def _strict_bool(name: str, default: bool = False) -> bool:
    """Parse a flag strictly so a typo cannot silently enable content export."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _required_environment(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required when HW2_TRACING_ENABLED=true")
    return value


def _redact(value: Any) -> Any:
    """Remove credentials recursively before serializing tool evidence."""
    if isinstance(value, Mapping):
        return {
            str(key): "[REDACTED]" if _SENSITIVE_KEY.search(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    return value


def _bounded_json(value: Any) -> str:
    """Return valid bounded JSON rather than truncating structured evidence."""
    raw = json.dumps(_redact(value), ensure_ascii=False, default=str, separators=(",", ":"))
    encoded = raw.encode()
    if len(encoded) <= _MAX_CAPTURE_BYTES:
        return raw
    return json.dumps(
        {
            "capture": "omitted_over_limit",
            "bytes": len(encoded),
            "sha256": hashlib.sha256(encoded).hexdigest(),
        },
        separators=(",", ":"),
    )


def _content_capture_enabled() -> bool:
    """Use one flag for both automatic and manual telemetry content."""
    return _strict_bool("TRACELOOP_TRACE_CONTENT")


def _message_json(role: str, content: str) -> str:
    return json.dumps(
        [{"role": role, "parts": [{"type": "text", "content": content}]}],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _get_tracer():
    """Resolve at call time so tests can provide an isolated tracer provider."""
    return trace.get_tracer(_INSTRUMENTATION_NAME)


@dataclass
class TracingRuntime:
    """Own resources installed during the FastAPI lifespan."""

    enabled: bool = False
    provider: TracerProvider | None = None
    instrumentor: Any | None = None

    def shutdown(self) -> None:
        if not self.enabled:
            return
        # Telemetry cleanup must never change an already-completed business result.
        try:
            if self.instrumentor is not None:
                self.instrumentor.uninstrument()
        except Exception:
            log.exception("Failed to remove LangChain instrumentation")
        try:
            if self.provider is not None:
                self.provider.shutdown()
        except Exception:
            log.exception("Failed to flush the Homework trace provider")


def setup_homework_tracing() -> TracingRuntime:
    """Install one OTLP exporter when tracing is explicitly enabled."""
    if not _strict_bool("HW2_TRACING_ENABLED"):
        return TracingRuntime()

    public_key = _required_environment("LANGFUSE_PUBLIC_KEY")
    secret_key = _required_environment("LANGFUSE_SECRET_KEY")
    host = _required_environment("LANGFUSE_HOST").rstrip("/")
    capture_content = _strict_bool("TRACELOOP_TRACE_CONTENT")
    os.environ["TRACELOOP_TRACE_CONTENT"] = "true" if capture_content else "false"

    credential = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    exporter = OTLPSpanExporter(
        endpoint=f"{host}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {credential}"},
    )
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": os.environ.get("OTEL_SERVICE_NAME", "hoistra-homework-agent"),
                "deployment.environment.name": os.environ.get(
                    "OTEL_DEPLOYMENT_ENVIRONMENT", "homework-2"
                ),
            }
        )
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    if trace.get_tracer_provider() is not provider:
        provider.shutdown()
        raise RuntimeError("An OpenTelemetry tracer provider is already configured")

    from opentelemetry.instrumentation.langchain import LangchainInstrumentor

    instrumentor = LangchainInstrumentor(
        use_attributes=True,
        metadata_key_prefix="hoistra",
    )
    try:
        instrumentor.instrument(tracer_provider=provider)
    except Exception:
        provider.shutdown()
        raise
    log.info("Homework tracing enabled for service %s", os.environ.get("OTEL_SERVICE_NAME"))
    return TracingRuntime(enabled=True, provider=provider, instrumentor=instrumentor)


@dataclass(frozen=True)
class _RequestContext:
    user_role: str
    user_id: str
    organization_id: str
    building_ids: tuple[str, ...]
    session_id: str
    request_id: str
    root_span: Span


_REQUEST_CONTEXT: ContextVar[_RequestContext | None] = ContextVar(
    "hoistra_homework_trace_context", default=None
)


def _set_identity(span: Span, principal: Any, session_id: str, request_id: str) -> None:
    span.set_attribute("hoistra.user_role", str(principal.role))
    span.set_attribute("hoistra.user_id", str(principal.user_id))
    span.set_attribute("hoistra.organization_id", str(principal.organization_id))
    span.set_attribute("hoistra.building_ids", tuple(sorted(map(str, principal.building_ids))))
    span.set_attribute("hoistra.session_id", session_id)
    span.set_attribute("hoistra.request_id", request_id)


def _set_permission_denied(span: Span, denied: bool, reason: str | None = None) -> None:
    span.set_attribute("hoistra.permission_denied", denied)
    if denied and reason is not None:
        span.set_attribute("hoistra.permission_denied.reason", reason)


def _is_permission_denial(value: Any) -> tuple[bool, str | None]:
    if not isinstance(value, Mapping):
        return False, None
    denied = (
        value.get("error") == "permission_denied"
        or value.get("status") == 403
        or value.get("status_code") == 403
    )
    reason = value.get("reason") or value.get("detail") or value.get("error")
    return denied, str(reason) if denied and reason is not None else None


def _mark_root_denial(reason: str) -> None:
    current = _REQUEST_CONTEXT.get()
    if current is not None:
        _set_permission_denied(current.root_span, True, reason)


@dataclass
class SessionObservation:
    span: Span

    @property
    def trace_id(self) -> str | None:
        context = self.span.get_span_context()
        if not context.is_valid:
            return None
        return format(context.trace_id, "032x")

    def finish(self, result: Mapping[str, Any], prompt_version: str) -> None:
        output = str(result.get("answer", ""))
        if _content_capture_enabled():
            output_messages = _message_json("assistant", output)
            self.span.set_attribute("gen_ai.output.messages", output_messages)
            self.span.set_attribute("langfuse.observation.output", output_messages)
        self.span.set_attribute("hoistra.prompt_version", prompt_version)
        self.span.set_attribute("hoistra.selected_route", str(result.get("selected_route") or ""))
        self.span.set_attribute("hoistra.outcome", str(result.get("outcome") or ""))
        self.span.set_attribute(
            "hoistra.execution_outcome", str(result.get("execution_outcome") or "")
        )
        self.span.set_attribute("hoistra.tool_count", len(result.get("actual_tools", [])))

    def fail(
        self,
        error: Exception,
        prompt_version: str,
        selected_route: str | None,
        tool_count: int,
    ) -> None:
        """Finalize a failed request without leaking exception or request content."""
        self.span.set_attribute("hoistra.prompt_version", prompt_version)
        self.span.set_attribute("hoistra.selected_route", selected_route or "")
        self.span.set_attribute("hoistra.outcome", "failed")
        self.span.set_attribute("hoistra.execution_outcome", "failed")
        self.span.set_attribute("hoistra.tool_count", tool_count)
        self.span.set_attribute("hoistra.failure.type", type(error).__name__)
        self.span.set_status(Status(StatusCode.ERROR, type(error).__name__))


@contextmanager
def observe_session(
    principal: Any,
    session_id: str,
    request_id: str,
    message: str,
    scenario_id: str | None = None,
) -> Iterator[SessionObservation]:
    """Create the root span after the server has resolved session ownership."""
    with _get_tracer().start_as_current_span(
        "hoistra.session_message", kind=SpanKind.SERVER
    ) as span:
        _set_identity(span, principal, session_id, request_id)
        _set_permission_denied(span, False)
        span.set_attribute("hoistra.content_capture_enabled", _content_capture_enabled())
        if _content_capture_enabled():
            input_messages = _message_json("user", message)
            span.set_attribute("gen_ai.input.messages", input_messages)
            span.set_attribute("langfuse.observation.input", input_messages)
        span.set_attribute("langfuse.observation.type", "agent")
        span.set_attribute("langfuse.session.id", session_id)
        span.set_attribute("langfuse.user.id", str(principal.user_id))
        if scenario_id:
            span.set_attribute("hoistra.scenario_id", scenario_id)
        request_context = _RequestContext(
            user_role=str(principal.role),
            user_id=str(principal.user_id),
            organization_id=str(principal.organization_id),
            building_ids=tuple(sorted(map(str, principal.building_ids))),
            session_id=session_id,
            request_id=request_id,
            root_span=span,
        )
        token = _REQUEST_CONTEXT.set(request_context)
        try:
            yield SessionObservation(span)
        except Exception as error:
            if getattr(error, "status_code", None) == 403:
                _set_permission_denied(span, True, str(getattr(error, "detail", "")))
            raise
        finally:
            _REQUEST_CONTEXT.reset(token)


@dataclass
class ToolObservation:
    span: Span

    def set_result(self, result: Any) -> None:
        if _content_capture_enabled():
            self.span.set_attribute("gen_ai.tool.call.result", _bounded_json(result))
        denied, reason = _is_permission_denial(result)
        if denied:
            _set_permission_denied(self.span, True, reason)
            _mark_root_denial(reason or "Permission denied")


@contextmanager
def observe_tool(principal: Any, tool_name: str, arguments: Any) -> Iterator[ToolObservation]:
    """Record the real tool operation while the authorization decision is active."""
    current = _REQUEST_CONTEXT.get()
    session_id = current.session_id if current else ""
    request_id = current.request_id if current else ""
    with _get_tracer().start_as_current_span(
        f"execute_tool {tool_name}", kind=SpanKind.INTERNAL
    ) as span:
        span.set_attribute("gen_ai.operation.name", "execute_tool")
        span.set_attribute("gen_ai.tool.name", tool_name)
        if _content_capture_enabled():
            span.set_attribute("gen_ai.tool.call.arguments", _bounded_json(arguments))
        span.set_attribute("langfuse.observation.type", "tool")
        _set_identity(span, principal, session_id, request_id)
        _set_permission_denied(span, False)
        try:
            yield ToolObservation(span)
        except Exception as error:
            if getattr(error, "status_code", None) == 403:
                reason = str(getattr(error, "detail", ""))
                _set_permission_denied(span, True, reason)
                _mark_root_denial(reason)
            span.set_status(Status(StatusCode.ERROR, type(error).__name__))
            raise


async def traced_tool_call[T](
    principal: Any,
    tool_name: str,
    arguments: Any,
    call: Callable[[], Awaitable[T]],
    *,
    allowed_tools: Collection[str] | None = None,
    unbound_reason: str = "Unbound model tool",
) -> T:
    """Execute one tool once and preserve its result and authorization outcome."""
    with observe_tool(principal, tool_name, arguments) as observation:
        if allowed_tools is not None and tool_name not in allowed_tools:
            from fastapi import HTTPException

            raise HTTPException(403, unbound_reason)
        result = await call()
        observation.set_result(result)
        return result


def model_trace_config(rendered_prompt_sha256: str) -> dict[str, Any]:
    """Attach only the reviewed prompt hash to the auto-instrumented model span."""
    if re.fullmatch(r"[0-9a-f]{64}", rendered_prompt_sha256) is None:
        raise ValueError("rendered prompt hash must be a lowercase SHA-256 digest")
    return {"metadata": {"rendered_prompt_sha256": rendered_prompt_sha256}}


def rendered_prompt_sha256(prompt: str, metadata: Mapping[str, Any]) -> str:
    """Use the assembled hash or derive it when a profile runs outside the router."""
    existing = metadata.get("rendered_prompt_sha256")
    if isinstance(existing, str) and re.fullmatch(r"[0-9a-f]{64}", existing):
        return existing
    return hashlib.sha256(prompt.encode()).hexdigest()


async def invoke_traced_model[T](
    model: Any,
    messages: Any,
    rendered_prompt_sha256: str,
    **kwargs: Any,
) -> T:
    """Pass trace metadata when the model supports LangChain run configuration."""
    invoke = model.ainvoke
    signature = inspect.signature(invoke)
    if "config" in signature.parameters:
        return await invoke(
            messages,
            config=model_trace_config(rendered_prompt_sha256),
            **kwargs,
        )
    # Small local test doubles predate LangChain's optional run configuration.
    return await invoke(messages, **kwargs)


def effective_prompt_version(
    registry: Mapping[str, Any],
    selected_route: str | None,
    *,
    answer_prompt_used: bool,
) -> str:
    """Hash only manifest-protected system instructions used by the request."""
    profile = (registry.get("enabled_profiles") or {}).get(selected_route or "")
    payload = {
        "schema": "hoistra-effective-prompt-v1",
        "unified_manifest_sha256": registry.get("manifest_sha256"),
        "selected_profile": selected_route,
        "selected_profile_manifest_sha256": (
            profile.get("manifest_sha256") if isinstance(profile, Mapping) else None
        ),
        "answer_instruction_sha256": (
            registry.get("answer_instruction_sha256") if answer_prompt_used else None
        ),
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()
