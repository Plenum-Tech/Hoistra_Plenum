"""Parse Fiix credentials from free-form chat text (Schema Mapper UI parity)."""

from __future__ import annotations

import re

from .session_workspace import (
    fiix_credentials_configured,
    get_session_state,
    set_fiix_credentials,
)

# Line-oriented labels users paste in chat (case-insensitive). Tolerant of an
# optional "Fiix"/"API" prefix and Fiix's real wording ("Application Key",
# "API Application Key", "API Secret", "Access Key", etc.).
_LABEL_PREFIX = r"(?:(?:fiix|api)[_\s-]*)*"
_CRED_LINE_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(rf"(?im)^\s*{_LABEL_PREFIX}sub[_\s-]*domain\s*[:=]\s*(.+?)\s*$"), "subdomain"),
    # "App Key" / "Application Key" / "API Application Key" / "App" (key word optional)
    (
        re.compile(
            rf"(?im)^\s*{_LABEL_PREFIX}app(?:lication)?\s*(?:[_\s-]*(?:key|id|client))?\s*[:=]\s*(.+?)\s*$"
        ),
        "app_key",
    ),
    # "Access Key" / "API Access Key" / "Access"
    (
        re.compile(rf"(?im)^\s*{_LABEL_PREFIX}access\s*(?:[_\s-]*key)?\s*[:=]\s*(.+?)\s*$"),
        "access_key",
    ),
    # "Secret Key" / "API Secret" / "Secret"
    (
        re.compile(rf"(?im)^\s*{_LABEL_PREFIX}secret\s*(?:[_\s-]*key)?\s*[:=]\s*(.+?)\s*$"),
        "secret_key",
    ),
]


def parse_fiix_credentials_from_text(text: str) -> dict[str, str]:
    """Extract subdomain / keys from a user message (last match wins per field)."""
    if not (text or "").strip():
        return {}
    found: dict[str, str] = {}
    for pattern, key in _CRED_LINE_PATTERNS:
        for match in pattern.finditer(text):
            value = match.group(1).strip().strip("\"'")
            if value:
                found[key] = value
    return found


def message_looks_like_fiix_credentials(text: str) -> bool:
    """True when the user is clearly sending Fiix connection fields."""
    # Most reliable: if the line patterns already extract ≥2 fields, it's credentials.
    if len(parse_fiix_credentials_from_text(text)) >= 2:
        return True
    msg_l = (text or "").lower()
    labels = (
        "subdomain",
        "app key",
        "application key",
        "application",
        "access key",
        "secret key",
        "api secret",
        "secret",
    )
    hits = sum(1 for label in labels if label in msg_l)
    return hits >= 2


def fiix_setup_status_snapshot(session_id: str) -> dict:
    """Same shape as get_fiix_setup_status tool, without LangGraph session context."""
    from .session_workspace import set_pending_fiix_confirm

    if not session_id:
        return {
            "configured": False,
            "missing_fields": [
                "Subdomain (e.g. plenumtechnology)",
                "App Key",
                "Access Key",
                "Secret Key",
            ],
            "required_prompt": "No session context — credentials cannot be stored yet.",
        }
    creds = get_session_state(session_id).get("fiix_credentials") or {}
    missing: list[str] = []
    if not (creds.get("subdomain") or "").strip():
        missing.append("Subdomain (e.g. plenumtechnology)")
    if not (creds.get("app_key") or "").strip():
        missing.append("App Key")
    if not (creds.get("access_key") or "").strip():
        missing.append("Access Key")
    if not (creds.get("secret_key") or "").strip():
        missing.append("Secret Key")
    configured = fiix_credentials_configured(session_id)
    if not configured:
        set_pending_fiix_confirm(session_id, action="schema_mapping")
    return {
        "configured": configured,
        "subdomain": (creds.get("subdomain") or "") if configured else "",
        "missing_fields": missing,
        "required_prompt": (
            "Please provide Fiix credentials to connect (same as Schema Mapper UI):\n"
            "1. Subdomain (e.g. plenumtechnology)\n"
            "2. App Key\n"
            "3. Access Key\n"
            "4. Secret Key"
            if missing
            else None
        ),
    }


def merge_fiix_credentials_from_message(session_id: str, text: str) -> bool:
    """
    Merge parsed credential lines into session state.

    Returns True when all four fields are present after merge.
    """
    parsed = parse_fiix_credentials_from_text(text)
    if not parsed:
        return fiix_credentials_configured(session_id)
    creds = get_session_state(session_id).get("fiix_credentials") or {}
    set_fiix_credentials(
        session_id,
        fiix_subdomain=parsed.get("subdomain") or str(creds.get("subdomain") or ""),
        fiix_app_key=parsed.get("app_key") or str(creds.get("app_key") or ""),
        fiix_access_key=parsed.get("access_key") or str(creds.get("access_key") or ""),
        fiix_secret_key=parsed.get("secret_key") or str(creds.get("secret_key") or ""),
    )
    return fiix_credentials_configured(session_id)
