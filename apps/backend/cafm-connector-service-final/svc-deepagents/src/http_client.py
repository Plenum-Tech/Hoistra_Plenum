"""
Shared async HTTP client with retry and circuit breaker for all downstream services.

Retry strategy (tenacity):
  - Up to `max_attempts` attempts per call (default 3).
  - Retries on: connection errors, timeouts, 5xx responses.
  - Does NOT retry on 4xx — those are client errors that won't resolve on retry.
  - Exponential backoff: 1s → 2s → 4s (capped at 8s).

Circuit breaker (built-in, no extra dependency):
  - Per-service state, keyed by the `service` name passed to request().
  - Opens after 5 consecutive failures; blocks all calls with RuntimeError while open.
  - Half-open probe attempt after 30 seconds to test recovery.
  - Resets fully on a successful response.

Usage:
    from .http_client import request as _request

    resp = await _request(
        "GET",
        settings.wo_management_base_url,
        f"/api/work-orders/{work_order_id}",
        service="wo_management",
        timeout=45.0,
    )
    return resp.json()

Pass max_attempts=1 for non-idempotent operations (import, rollback) where
retrying on a timeout could cause duplicate side effects.
"""
from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

log = structlog.get_logger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Circuit breaker
# ──────────────────────────────────────────────────────────────────────────────

_FAILURE_THRESHOLD = 5
_RECOVERY_TIMEOUT = 30.0  # seconds before half-open probe


class _CircuitBreaker:
    def __init__(self, service: str) -> None:
        self.service = service
        self._failures = 0
        self._open = False
        self._opened_at = 0.0

    def allow_request(self) -> bool:
        if not self._open:
            return True
        # Allow one probe after recovery window — half-open state
        if time.monotonic() - self._opened_at >= _RECOVERY_TIMEOUT:
            log.info("circuit_breaker.half_open", service=self.service)
            return True
        return False

    def record_success(self) -> None:
        if self._open:
            log.info("circuit_breaker.closed", service=self.service)
        self._failures = 0
        self._open = False

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= _FAILURE_THRESHOLD and not self._open:
            self._open = True
            self._opened_at = time.monotonic()
            log.warning(
                "circuit_breaker.opened",
                service=self.service,
                failures=self._failures,
            )


_breakers: dict[str, _CircuitBreaker] = {}


def _breaker(service: str) -> _CircuitBreaker:
    if service not in _breakers:
        _breakers[service] = _CircuitBreaker(service)
    return _breakers[service]


# ──────────────────────────────────────────────────────────────────────────────
# Retry predicate
# ──────────────────────────────────────────────────────────────────────────────

def _is_transient(exc: BaseException) -> bool:
    """True for errors that are worth retrying: transport failures and 5xx responses."""
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


# ──────────────────────────────────────────────────────────────────────────────
# Public request function
# ──────────────────────────────────────────────────────────────────────────────

async def request(
    method: str,
    base_url: str,
    path: str,
    *,
    service: str,
    timeout: float = 30.0,
    max_attempts: int = 3,
    **kwargs: Any,
) -> httpx.Response:
    """Make an async HTTP request with retry and circuit breaker.

    Args:
        method:       HTTP method — 'GET', 'POST', 'PATCH', 'DELETE'.
        base_url:     Service root URL (e.g. 'http://localhost:8007').
        path:         Request path (e.g. '/api/work-orders/').
        service:      Short name for circuit breaker tracking (e.g. 'wo_management').
        timeout:      Per-attempt timeout in seconds.
        max_attempts: Total attempts including the first. Use 1 to disable retries
                      for non-idempotent operations that must not run twice.
        **kwargs:     Forwarded to httpx.AsyncClient.request (json, params, etc.).

    Returns:
        httpx.Response with status already checked (raise_for_status called).

    Raises:
        RuntimeError:          When the circuit is open for this service.
        httpx.HTTPStatusError: On 4xx or persistent 5xx after all attempts.
        httpx.TransportError:  On persistent connection/timeout failures.
    """
    cb = _breaker(service)

    if not cb.allow_request():
        raise RuntimeError(
            f"Service '{service}' is unavailable (circuit open). "
            f"Will retry automatically in ~{_RECOVERY_TIMEOUT:.0f}s."
        )

    async for attempt in AsyncRetrying(
        retry=retry_if_exception(_is_transient),
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=1, max=8),
        reraise=True,
    ):
        with attempt:
            attempt_num = attempt.retry_state.attempt_number
            if attempt_num > 1:
                log.info(
                    "http_client.retry",
                    service=service,
                    method=method,
                    path=path,
                    attempt=attempt_num,
                )
            try:
                async with httpx.AsyncClient(base_url=base_url, timeout=timeout, follow_redirects=True) as client:
                    resp = await client.request(method, path, **kwargs)
                    resp.raise_for_status()
                    cb.record_success()
                    return resp
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code >= 500:
                    cb.record_failure()
                    log.warning(
                        "http_client.server_error",
                        service=service,
                        status=exc.response.status_code,
                        attempt=attempt_num,
                    )
                raise
            except httpx.TransportError as exc:
                cb.record_failure()
                log.warning(
                    "http_client.transport_error",
                    service=service,
                    error=type(exc).__name__,
                    attempt=attempt_num,
                )
                raise

    # unreachable — tenacity reraises after exhausting attempts
    raise RuntimeError("Unreachable")  # pragma: no cover
