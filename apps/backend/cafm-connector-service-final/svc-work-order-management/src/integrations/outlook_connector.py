"""
Microsoft Graph API connector for Outlook email.
Uses the OAuth2 client credentials flow (app-only) so the token is fetched
and refreshed automatically — no manual token updates required.
"""
import re
import time
import httpx
from typing import Dict, Any, List, Optional

from ..core.logging import get_logger

log = get_logger(__name__)

_GRAPH_BASE = "https://graph.microsoft.com/v1.0"
_TOKEN_URL = "https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
_GRAPH_SCOPE = "https://graph.microsoft.com/.default"


class OutlookConnector:
    """
    Reads from and writes to an Outlook mailbox via Microsoft Graph API.
    Uses the OAuth2 client credentials flow — tokens are fetched and cached
    automatically, refreshing 60 seconds before expiry.
    Requires Application permissions (not delegated):
      Mail.Read, Mail.Send, Mail.ReadWrite
    """

    def __init__(self, tenant_id: str, client_id: str, client_secret: str, user_email: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.user_email = user_email
        # app-only flow must address the mailbox by UPN/email, not /me
        self._base = f"{_GRAPH_BASE}/users/{user_email}"
        self._cached_token: str = ""
        self._token_expires_at: float = 0.0

    # ── Token management ─────────────────────────────────────────────────────

    async def _get_token(self) -> str:
        """Return a valid access token, fetching a new one if the cached one is near expiry."""
        if time.monotonic() < self._token_expires_at - 60:
            return self._cached_token

        url = _TOKEN_URL.format(tenant_id=self.tenant_id)
        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "scope": _GRAPH_SCOPE,
            "grant_type": "client_credentials",
        }
        log.debug("outlook.token.refresh")
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, data=data)
            resp.raise_for_status()
            result = resp.json()

        self._cached_token = result["access_token"]
        self._token_expires_at = time.monotonic() + result.get("expires_in", 3600)
        log.info("outlook.token.refreshed", expires_in=result.get("expires_in", 3600))
        return self._cached_token

    async def _headers(self) -> Dict[str, str]:
        token = await self._get_token()
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    # ── Read ─────────────────────────────────────────────────────────────────

    async def get_unread_emails(self, max_count: int = 20) -> List[Dict[str, Any]]:
        """
        Fetch unread emails from the Inbox, newest first.
        Returns a list normalised to the shape EmailParser expects.
        """
        log.debug("outlook.get_unread_emails", max_count=max_count)
        url = (
            f"{self._base}/mailFolders/Inbox/messages"
            f"?$filter=isRead eq false"
            f"&$top={max_count}"
            f"&$orderby=receivedDateTime desc"
            f"&$select=id,subject,from,body,receivedDateTime,hasAttachments,isRead"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=await self._headers())
            resp.raise_for_status()
            messages = resp.json().get("value", [])

        log.info("outlook.get_unread_emails.result", count=len(messages))
        return [self._normalise(m) for m in messages]

    async def list_inbox_messages(self, max_count: int = 30) -> List[Dict[str, Any]]:
        """Fetch recent Inbox messages (read + unread) for the email-inbox UI."""
        log.debug("outlook.list_inbox_messages", max_count=max_count)
        url = (
            f"{self._base}/mailFolders/Inbox/messages"
            f"?$top={max_count}"
            f"&$orderby=receivedDateTime desc"
            f"&$select=id,subject,from,receivedDateTime,bodyPreview,body,importance,isRead"
        )
        headers = await self._headers()
        headers["Prefer"] = 'outlook.body-content-type="text"'
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            messages = resp.json().get("value", [])

        out: List[Dict[str, Any]] = []
        for msg in messages:
            sender = msg.get("from", {}).get("emailAddress", {})
            body_obj = msg.get("body") or {}
            body_content = body_obj.get("content", "") or ""
            if (body_obj.get("contentType") or "").lower() == "html":
                body_content = self._strip_html(body_content)
            importance = (msg.get("importance") or "normal").lower()
            priority = "high" if importance == "high" else "low" if importance == "low" else "medium"
            out.append({
                "id": msg["id"],
                "from": sender.get("name") or sender.get("address") or "Unknown",
                "fromEmail": sender.get("address", ""),
                "subject": msg.get("subject") or "(No subject)",
                "preview": msg.get("bodyPreview") or "",
                "body": body_content,
                "receivedAt": msg.get("receivedDateTime", ""),
                "read": bool(msg.get("isRead")),
                "priority": priority,
            })
        log.info("outlook.list_inbox_messages.result", count=len(out))
        return out

    async def get_email(self, message_id: str) -> Dict[str, Any]:
        """Fetch a single email by Graph message ID."""
        log.debug("outlook.get_email", message_id=message_id)
        url = (
            f"{self._base}/messages/{message_id}"
            f"?$select=id,subject,from,body,receivedDateTime,hasAttachments"
        )
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, headers=await self._headers())
            resp.raise_for_status()
        return self._normalise(resp.json())

    # ── Write ────────────────────────────────────────────────────────────────

    async def send_email(
        self,
        to: str,
        subject: str,
        body: str,
        reply_to_id: Optional[str] = None,
        is_html: bool = False,
    ) -> None:
        """
        Send an email from the service mailbox.
        When is_html=True, body must be a complete HTML document.
        For HTML replies, uses createReply → PATCH body → send (preserves thread + HTML).
        Falls back to sendMail on any Graph API error.
        """
        content_type = "HTML" if is_html else "Text"
        log.info(
            "outlook.send_email",
            to=to, subject=subject[:60],
            content_type=content_type,
            is_reply=reply_to_id is not None,
        )
        headers = await self._headers()

        async with httpx.AsyncClient(timeout=20) as client:

            # ── HTML reply: createReply draft → patch body → send ────────────
            if reply_to_id and is_html:
                try:
                    draft_resp = await client.post(
                        f"{self._base}/messages/{reply_to_id}/createReply",
                        headers=headers,
                        json={},
                    )
                    draft_resp.raise_for_status()
                    draft_id = draft_resp.json()["id"]

                    await client.patch(
                        f"{self._base}/messages/{draft_id}",
                        headers=headers,
                        json={"body": {"contentType": "HTML", "content": body}},
                    )

                    send_r = await client.post(
                        f"{self._base}/messages/{draft_id}/send",
                        headers=headers,
                    )
                    send_r.raise_for_status()
                    log.info("outlook.send_email.html_reply_sent", to=to)
                    return
                except httpx.HTTPStatusError as e:
                    log.warning(
                        "outlook.send_email.html_reply_failed_fallback",
                        to=to, status=e.response.status_code,
                    )
                    # Fall through to sendMail below

            # ── Plain-text reply ─────────────────────────────────────────────
            elif reply_to_id and not is_html:
                reply_resp = await client.post(
                    f"{self._base}/messages/{reply_to_id}/reply",
                    json={"comment": body},
                    headers=headers,
                )
                try:
                    reply_resp.raise_for_status()
                    log.info("outlook.send_email.reply_sent", to=to)
                    return
                except httpx.HTTPStatusError:
                    log.warning(
                        "outlook.send_email.reply_failed_fallback_sendmail",
                        to=to, status_code=reply_resp.status_code,
                    )
                    # Fall through to sendMail below

            # ── sendMail (new email or fallback from reply) ──────────────────
            send_resp = await client.post(
                f"{self._base}/sendMail",
                headers=headers,
                json={
                    "message": {
                        "subject": subject,
                        "body": {"contentType": content_type, "content": body},
                        "toRecipients": [{"emailAddress": {"address": to}}],
                    },
                    "saveToSentItems": True,
                },
            )
            send_resp.raise_for_status()

        log.info("outlook.send_email.sent", to=to, status_code=send_resp.status_code)

    async def mark_as_read(self, message_id: str) -> None:
        """Mark a message as read so it won't be picked up again."""
        log.debug("outlook.mark_as_read", message_id=message_id)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.patch(
                f"{self._base}/messages/{message_id}",
                json={"isRead": True},
                headers=await self._headers(),
            )
            resp.raise_for_status()
        log.debug("outlook.mark_as_read.done", message_id=message_id)

    async def move_to_folder(self, message_id: str, folder_name: str = "AIMMS-Processed") -> None:
        """
        Move a processed email out of Inbox.
        Creates the destination folder if it doesn't exist.
        """
        log.info("outlook.move_to_folder", message_id=message_id, folder_name=folder_name)
        folder_id = await self._get_or_create_folder(folder_name)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                f"{self._base}/messages/{message_id}/move",
                json={"destinationId": folder_id},
                headers=await self._headers(),
            )
            resp.raise_for_status()
        log.info("outlook.move_to_folder.done", message_id=message_id, folder_name=folder_name)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _normalise(self, msg: Dict[str, Any]) -> Dict[str, Any]:
        """
        Convert a Graph API message object to the flat dict EmailParser expects:
          id, from, from_name, subject, body, received_at, attachments
        """
        sender = msg.get("from", {}).get("emailAddress", {})
        body_content = msg.get("body", {}).get("content", "")

        if msg.get("body", {}).get("contentType", "").lower() == "html":
            body_content = self._strip_html(body_content)

        return {
            "id":          msg["id"],
            "from":        sender.get("address", ""),
            "from_name":   sender.get("name", ""),
            "subject":     msg.get("subject", ""),
            "body":        body_content,
            "received_at": msg.get("receivedDateTime", ""),
            "attachments": [],
        }

    def _strip_html(self, html: str) -> str:
        """Lightweight HTML stripper — removes tags, decodes common entities."""
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"&nbsp;", " ", text)
        text = re.sub(r"&amp;",  "&", text)
        text = re.sub(r"&lt;",   "<", text)
        text = re.sub(r"&gt;",   ">", text)
        text = re.sub(r"&quot;", '"', text)
        return re.sub(r"\s{2,}", " ", text).strip()

    async def _get_or_create_folder(self, name: str) -> str:
        """Return the folder ID for `name`, creating it under Inbox if absent."""
        list_url = f"{self._base}/mailFolders/Inbox/childFolders"
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(list_url, headers=await self._headers())
            resp.raise_for_status()
            folders = resp.json().get("value", [])

        for f in folders:
            if f["displayName"].lower() == name.lower():
                log.debug("outlook.folder.found", name=name, folder_id=f["id"])
                return f["id"]

        log.info("outlook.folder.creating", name=name)
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(
                list_url,
                json={"displayName": name},
                headers=await self._headers(),
            )
            resp.raise_for_status()
            folder_id = resp.json()["id"]
        log.info("outlook.folder.created", name=name, folder_id=folder_id)
        return folder_id

    async def check_connection(self) -> Dict[str, Any]:
        """
        Quick connectivity test — probes the Inbox folder (requires Mail.Read).
        Call GET /api/email/status to verify credentials are working.
        """
        log.info("outlook.check_connection")
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self._base}/mailFolders/Inbox",
                    headers=await self._headers(),
                )
            if resp.status_code == 200:
                result = {
                    "connected": True,
                    "display_name": None,
                    "email": self.user_email,
                }
                log.info("outlook.check_connection.ok", email=self.user_email)
                return result
            log.warning("outlook.check_connection.failed", status_code=resp.status_code)
            return {
                "connected": False,
                "error": f"HTTP {resp.status_code}",
            }
        except Exception as exc:
            log.warning("outlook.check_connection.error", error=str(exc))
            return {"connected": False, "error": str(exc)}
