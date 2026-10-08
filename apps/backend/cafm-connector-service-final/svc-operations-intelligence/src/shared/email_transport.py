"""Which transport sends platform email: Microsoft Graph, SMTP, or none (dry run).

EMAIL_PROVIDER picks it. "auto" (the default) keeps the order the platform always had - Graph
when its Azure app credentials are set, else SMTP when a mailbox is. "smtp" and "graph" force
one; a forced transport that is not configured is "none", so mail is logged as a dry run
rather than sent through the other one behind the operator's back.
"""
from __future__ import annotations

from ..config import settings
from .email_graph import graph_configured


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_user and settings.smtp_password)


def email_transport() -> str:
    """"graph" | "smtp" | "none"."""
    choice = (settings.email_provider or "auto").strip().lower()
    if choice == "smtp":
        return "smtp" if smtp_configured() else "none"
    if choice == "graph":
        return "graph" if graph_configured() else "none"
    if graph_configured():
        return "graph"
    return "smtp" if smtp_configured() else "none"
