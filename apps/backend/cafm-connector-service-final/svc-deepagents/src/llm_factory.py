"""Shared LangChain chat model setup for orchestrator and meta sub-agents."""
from __future__ import annotations

import os
from typing import Any

import httpx
import structlog
from langchain.chat_models import init_chat_model

from .config import settings

log = structlog.get_logger(__name__)


def _normalize_openai_base_url(url: str) -> str:
    """Ensure base URL has a scheme; OpenAI/httpx reject bare hostnames."""
    u = (url or "").strip().rstrip("/")
    if not u:
        return ""
    if not u.startswith(("http://", "https://")):
        u = f"https://{u}"
    return u


def _strip_empty_openai_env() -> None:
    """
    Remove empty OPENAI_* base URL vars from the process environment.

    Docker Compose often injects OPENAI_API_BASE= (empty), which LangChain/OpenAI
    still honor over the default api.openai.com URL and triggers:
    'Request URL is missing an http:// or https:// protocol'.
    """
    for key in ("OPENAI_API_BASE", "OPENAI_BASE_URL", "OPENAI_API_BASE_URL"):
        if not (os.environ.get(key) or "").strip():
            os.environ.pop(key, None)


def friendly_openai_error(exc: Exception) -> str:
    """Map low-level OpenAI/httpx errors to actionable messages."""
    msg = str(exc)
    upper = msg.upper()
    name = type(exc).__name__
    if "GraphRecursionError" in name or "RECURSION LIMIT" in upper:
        return (
            "This request needed too many tool steps. "
            "Please narrow it (one scope or status) and try again."
        )
    if "MISSING AN 'HTTP://" in upper or "MISSING AN 'HTTPS://" in upper or "UNSUPPORTEDPROTOCOL" in upper:
        return (
            "OpenAI connection failed: invalid API base URL (missing https://). "
            "Remove empty OPENAI_API_BASE / OPENAI_BASE_URL from docker-compose environment, "
            "or set OPENAI_API_BASE=https://api.openai.com/v1 in apps/backend/.env."
        )
    if "CERTIFICATE_VERIFY_FAILED" in upper or "HOSTNAME MISMATCH" in upper:
        return (
            "OpenAI connection failed (SSL certificate hostname mismatch). "
            "This usually means a corporate VPN/proxy is intercepting HTTPS. "
            "For local Docker dev, set OPENAI_SSL_VERIFY=false in apps/backend/.env and "
            "rebuild svc-deepagents. If you use Azure OpenAI, set AZURE_OPENAI_ENDPOINT "
            "and AZURE_OPENAI_DEPLOYMENT instead of public api.openai.com."
        )
    if "APIConnectionError" in name or "ConnectError" in name:
        return f"OpenAI connection failed: {msg}"
    return msg


def _http_clients() -> tuple[httpx.Client | None, httpx.AsyncClient | None]:
    if settings.openai_ssl_verify:
        return None, None
    log.warning(
        "openai.ssl_verify_disabled",
        hint="OPENAI_SSL_VERIFY=false — only use for local dev behind SSL-inspecting proxies",
    )
    return httpx.Client(verify=False), httpx.AsyncClient(verify=False)


def _provider_choice() -> str:
    choice = (settings.cloud_provider or "auto").strip().lower()
    if choice in {"azure", "tencent", "openai"}:
        return choice
    # auto mode
    if settings.azure_openai_endpoint.strip():
        return "azure"
    if settings.tencent_openai_base_url.strip():
        return "tencent"
    return "openai"


def active_provider_snapshot() -> dict[str, Any]:
    """Return active provider info without exposing secrets."""
    provider = _provider_choice()
    info: dict[str, Any] = {"provider": provider, "cloud_provider_setting": settings.cloud_provider}
    if provider == "azure":
        info.update(
            {
                "azure_endpoint_configured": bool(settings.azure_openai_endpoint.strip()),
                "azure_deployment": (settings.azure_openai_deployment or settings.openai_model),
                "azure_api_version": settings.azure_openai_api_version,
            }
        )
    elif provider == "tencent":
        info.update(
            {
                "tencent_base_configured": bool(settings.tencent_openai_base_url.strip()),
                "tencent_model": settings.tencent_openai_model,
            }
        )
    else:
        info.update(
            {
                "openai_model": settings.openai_model,
                "openai_base_configured": bool((settings.openai_api_base or "").strip()),
            }
        )
    return info


# Reasoning-tier models reject any temperature but the default (1); sending 0 is a hard 400.
_NO_TEMPERATURE_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _supports_temperature(model_name: str) -> bool:
    name = (model_name or "").strip().lower()
    return not name.startswith(_NO_TEMPERATURE_PREFIXES)


# The same families renamed the token ceiling: `max_tokens` is rejected outright with
# "Unsupported parameter ... use 'max_completion_tokens' instead", which is a 400 and not a
# warning. Kept as its own list rather than reusing the temperature one — they happen to
# match today, and treating two rules as one is how the next divergence becomes a bug.
_MAX_COMPLETION_TOKENS_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _token_limit_param(model_name: str) -> str:
    """The name this model wants for its output-token ceiling."""
    name = (model_name or "").strip().lower()
    return ("max_completion_tokens"
            if name.startswith(_MAX_COMPLETION_TOKENS_PREFIXES) else "max_tokens")


def token_limit_kwargs(model_name: str, limit: int) -> dict[str, int]:
    """``{"max_tokens": n}`` or ``{"max_completion_tokens": n}``, for **-splatting."""
    return {_token_limit_param(model_name): limit}


# Only the gpt-5.6 family needs reasoning_effort="none" to accept function tools. Measured
# against the live endpoint with tools bound:
#
#                     omitted   "none"   "minimal"   "low"
#   gpt-5.6-terra      FAIL      OK        FAIL       FAIL
#   gpt-5.6-luna       FAIL      OK        FAIL       FAIL
#   gpt-5.6-sol        FAIL      OK        FAIL       FAIL
#   gpt-5-mini          OK      FAIL        OK         OK
#   gpt-5               OK      FAIL        OK         OK
#   o4-mini             OK      FAIL       FAIL        OK
#
# So the rule cannot be "every reasoning-tier model": gpt-5, gpt-5-mini and o4-mini reject
# "none" outright and are fine with the field omitted. Widening this to all of gpt-5*/o*
# breaks them.
_NEEDS_NO_REASONING_PREFIXES = ("gpt-5.6",)


def _needs_explicit_no_reasoning(model_name: str) -> bool:
    """Whether this model must be told reasoning_effort="none" to accept function tools.

    The same trap as temperature. LangChain omits reasoning_effort when it is None, the
    endpoint then applies its own default, and for the gpt-5.6 family that default is
    incompatible with function tools:

        400 - Function tools with reasoning_effort are not supported for gpt-5.6-terra
              in /v1/chat/completions. To use function tools, use /v1/responses or set
              reasoning_effort to 'none'.

    Every tool-bound agent 400s before doing any work, which reads as "extraction failed"
    rather than as a configuration error.
    """
    return (model_name or "").strip().lower().startswith(_NEEDS_NO_REASONING_PREFIXES)


def create_chat_model(
    *,
    api_key: str | None = None,
    model: str | None = None,
) -> Any:
    """
    Build the orchestrator LLM from settings.

    Priority:
      1. Azure OpenAI when AZURE_OPENAI_ENDPOINT is set
      2. Public OpenAI with optional OPENAI_API_BASE override
    """
    provider = _provider_choice()
    key = (api_key or settings.openai_api_key or "").strip()
    model_name = (model or settings.openai_model or "gpt-4o-mini").strip()
    sync_client, async_client = _http_clients()
    # Reasoning-tier models accept only the default temperature, and LangChain fills in 0.7
    # when the field is left out — so it must be set explicitly to 1 rather than omitted.
    extra: dict[str, Any] = {
        "api_key": key,
        "temperature": 0 if _supports_temperature(model_name) else 1,
    }
    if _needs_explicit_no_reasoning(model_name):
        extra["reasoning_effort"] = "none"
    if sync_client is not None:
        extra["http_client"] = sync_client
    if async_client is not None:
        extra["http_async_client"] = async_client

    if provider == "azure":
        deployment = (settings.azure_openai_deployment or model_name).strip()
        log.info(
            "openai.using_azure",
            endpoint=settings.azure_openai_endpoint[:64],
            deployment=deployment,
        )
        return init_chat_model(
            f"azure_openai:{deployment}",
            azure_endpoint=settings.azure_openai_endpoint.strip().rstrip("/"),
            azure_deployment=deployment,
            api_version=settings.azure_openai_api_version,
            **extra,
        )

    if provider == "tencent":
        tencent_base = _normalize_openai_base_url(settings.tencent_openai_base_url)
        tencent_key = (settings.tencent_openai_api_key or key).strip()
        tencent_model = (model or settings.tencent_openai_model or model_name).strip()
        log.info("openai.using_tencent", base_url=tencent_base[:80], model=tencent_model)
        return init_chat_model(
            f"openai:{tencent_model}",
            api_key=tencent_key,
            base_url=tencent_base,
            temperature=0 if _supports_temperature(tencent_model) else 1,
            **(
                {"reasoning_effort": "none"}
                if _needs_explicit_no_reasoning(tencent_model)
                else {}
            ),
            **({} if sync_client is None else {"http_client": sync_client}),
            **({} if async_client is None else {"http_async_client": async_client}),
        )

    base = _normalize_openai_base_url(settings.openai_api_base)
    if not base:
        env_raw = (
            os.environ.get("OPENAI_BASE_URL", "").strip()
            or os.environ.get("OPENAI_API_BASE", "").strip()
        )
        base = _normalize_openai_base_url(env_raw)

    if base:
        log.info("openai.using_custom_base", base_url=base[:80])
        extra["base_url"] = base

    log.info("openai.using_public_api", model=model_name, ssl_verify=settings.openai_ssl_verify)
    return init_chat_model(f"openai:{model_name}", **extra)
