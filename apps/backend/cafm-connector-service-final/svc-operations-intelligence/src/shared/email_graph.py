"""Microsoft Graph email send (Azure app-only Mail.Send)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

import httpx

from ..config import settings
from ..core.logging import get_logger

log = get_logger(__name__)


def graph_configured() -> bool:
    return bool(
        settings.azure_tenant_id
        and settings.azure_client_id
        and settings.azure_client_secret
        and settings.outlook_user_mail
    )


async def graph_access_token() -> str:
    tenant = settings.azure_tenant_id.strip()
    url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    data = {
        "client_id": settings.azure_client_id.strip(),
        "client_secret": settings.azure_client_secret,
        "scope": "https://graph.microsoft.com/.default",
        "grant_type": "client_credentials",
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post(url, data=data)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Azure token HTTP {resp.status_code}: {resp.text[:500]}"
            )
        payload = resp.json()
    token = payload.get("access_token")
    if not token:
        raise RuntimeError("Azure token response missing access_token")
    return str(token)


async def send_via_microsoft_graph(
    *,
    to_address: str,
    subject: str,
    body: str,
    cc_address: str | None = None,
    attachments: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Send mail as OUTLOOK_USER_MAIL via Graph /users/{id}/sendMail.

    attachments: optional list of {name, content_type, content_base64}
    """
    token = await graph_access_token()
    sender = settings.outlook_user_mail.strip()
    to_recipients = [
        {"emailAddress": {"address": addr.strip()}}
        for addr in to_address.split(",")
        if addr.strip()
    ]
    cc_recipients: list[dict[str, Any]] = []
    if cc_address:
        cc_recipients = [
            {"emailAddress": {"address": addr.strip()}}
            for addr in cc_address.split(",")
            if addr.strip()
        ]
    message: dict[str, Any] = {
        "subject": subject,
        "body": {"contentType": "Text", "content": body},
        "toRecipients": to_recipients,
    }
    if cc_recipients:
        message["ccRecipients"] = cc_recipients
    if attachments:
        message["attachments"] = [
            {
                "@odata.type": "#microsoft.graph.fileAttachment",
                "name": a.get("name") or "attachment",
                "contentType": a.get("content_type") or "application/octet-stream",
                "contentBytes": a.get("content_base64") or "",
            }
            for a in attachments
            if a.get("content_base64")
        ]

    url = f"https://graph.microsoft.com/v1.0/users/{quote(sender, safe='@.+_-')}/sendMail"
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"message": message, "saveToSentItems": True},
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph sendMail HTTP {resp.status_code}: {resp.text[:800]}")
    log.info("email.graph_sent", from_addr=sender, to=to_address)
    return {"ok": True, "provider": "microsoft_graph", "from": sender}
