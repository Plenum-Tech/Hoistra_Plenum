"""The gate between "the file arrived" and "the building owns it".

Indexing a document is reversible; binding it to a building is what makes it evidence. From
that moment it counts in the building's compliance position, its renewal ladder and its
reports — so the check runs in between, and nothing binds while a case is open.

The check itself lives in operations-intelligence, which holds the building's ontology: its
names and codes, where it is, which vendors work there, what plant it has, what is already
filed against it. This module is the client for that protocol and the release that follows
a decision.

Failure here is deliberately **closed**, not open. If the validation service cannot be
reached, the document is held and the person is told — the opposite of the usual rule in
this codebase, because an unbound document is recoverable from the row it already is, while
a wrongly bound one silently moves a compliance position that somebody will act on.
"""
from __future__ import annotations

from typing import Any

import httpx
import structlog

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_SERVICE = "operations_intelligence"
_TIMEOUT = 60.0


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _headers(authorization: str | None) -> dict[str, str]:
    return {"Authorization": authorization} if authorization else {}


def _unavailable(op: str, exc: Exception) -> dict[str, Any]:
    log.warning("ingestion_validation.unavailable", op=op, error=str(exc)[:200])
    return {
        "ok": False, "reason": "validation_unavailable",
        "error": ("The document could not be checked against this building just now, so it "
                  "has not been filed. Nothing was lost — try again, or ask an administrator "
                  "to release it."),
        "held": True,
    }


async def validate(
    *,
    building_id: str,
    document_name: str | None,
    doc_type: str | None = None,
    extracted: dict[str, Any] | None = None,
    text: str | None = None,
    document_id: str | None = None,
    session_id: str | None = None,
    authorization: str | None = None,
) -> dict[str, Any]:
    """Check one document against the selected building. Returns the case."""
    try:
        resp = await _request(
            "POST", _base(), "/api/ingestion/validate", service=_SERVICE, timeout=_TIMEOUT,
            headers=_headers(authorization),
            json={"building_id": building_id, "document_name": document_name,
                  "doc_type": doc_type, "extracted": extracted or {},
                  "text": (text or "")[:200_000] or None, "document_id": document_id,
                  "session_id": session_id},
        )
        return resp.json()
    except httpx.HTTPStatusError as exc:
        try:
            return {"ok": False, **(exc.response.json() or {}), "held": True}
        except Exception:  # noqa: BLE001
            return _unavailable("validate", exc)
    except Exception as exc:  # noqa: BLE001
        return _unavailable("validate", exc)


async def _case_action(
    case_id: str, action: str, payload: dict[str, Any], authorization: str | None
) -> dict[str, Any]:
    try:
        resp = await _request(
            "POST", _base(), f"/api/ingestion/cases/{case_id}/{action}", service=_SERVICE,
            timeout=_TIMEOUT, headers=_headers(authorization), json=payload)
        return resp.json()
    except httpx.HTTPStatusError as exc:
        try:
            return {"ok": False, **(exc.response.json() or {})}
        except Exception:  # noqa: BLE001
            return _unavailable(action, exc)
    except Exception as exc:  # noqa: BLE001
        return _unavailable(action, exc)


async def clarify(case_id: str, explanation: str, authorization: str | None = None) -> dict[str, Any]:
    return await _case_action(case_id, "clarify", {"explanation": explanation}, authorization)


async def reassign(case_id: str, building_id: str, authorization: str | None = None) -> dict[str, Any]:
    return await _case_action(case_id, "reassign", {"building_id": building_id}, authorization)


async def decide(
    case_id: str, approve: bool, note: str | None = None, authorization: str | None = None
) -> dict[str, Any]:
    return await _case_action(case_id, "decide", {"approve": approve, "note": note}, authorization)


async def get_case(case_id: str, authorization: str | None = None) -> dict[str, Any]:
    try:
        resp = await _request("GET", _base(), f"/api/ingestion/cases/{case_id}",
                              service=_SERVICE, timeout=_TIMEOUT,
                              headers=_headers(authorization))
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return _unavailable("get_case", exc)


async def list_cases(
    *, open_only: bool = True, building_id: str | None = None, limit: int = 50,
    authorization: str | None = None,
) -> dict[str, Any]:
    try:
        params: dict[str, Any] = {"open_only": str(open_only).lower(), "limit": limit}
        if building_id:
            params["building_id"] = building_id
        resp = await _request("GET", _base(), "/api/ingestion/cases", service=_SERVICE,
                              timeout=_TIMEOUT, headers=_headers(authorization), params=params)
        return resp.json()
    except Exception as exc:  # noqa: BLE001
        return _unavailable("list_cases", exc)


def blocking(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The cases that stop this upload: anything not already cleared to bind."""
    return [c for c in cases if not (c.get("verdict") == "matched" or c.get("may_ingest"))]


def summarise(cases: list[dict[str, Any]]) -> str:
    """What to tell the uploader when a document is held. Plain, specific, and it says what
    happens next rather than what went wrong."""
    held = blocking(cases)
    if not held:
        return ""
    lines = ["**Held for a check — nothing has been filed yet.**", ""]
    for c in held:
        name = c.get("document_name") or "the document"
        lines.append(f"- **{name}** — {c.get('message') or c.get('question') or 'needs confirmation'}")
        sug = c.get("suggestion") or {}
        if sug.get("name"):
            lines.append(f"  Suggested instead: **{sug['name']}**.")
        lines.append(f"  (case `{c.get('id')}`)")
    lines += [
        "",
        "Reply with your reason and I will put it to the check, or confirm to file it as it "
        "is — either way the decision is recorded against your name.",
    ]
    return "\n".join(lines)
