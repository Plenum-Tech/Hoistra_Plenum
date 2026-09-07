"""CCC §9 — email draft builders for forgery (class 2) and verification (class 3)."""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from ...config import settings


def _mailto(to: str, subject: str, body: str) -> str:
    return f"mailto:{quote(to)}?subject={quote(subject)}&body={quote(body)}"


def forgery_email_draft(
    *,
    certificate_type_code: str | None,
    certificate_name: str | None = None,
    authenticity_warning: str | None = None,
    verdict: str | None = None,
    certificate_id: str | None = None,
    document_id: str | None = None,
) -> dict[str, Any]:
    to = settings.default_pm_email
    name = certificate_name or certificate_type_code or "certificate"
    subject = f"[Forgery alert] Authenticity review required — {name}"
    body = (
        f"FM Admin,\n\n"
        f"A compliance document failed authenticity checks and needs follow-up.\n\n"
        f"Certificate: {name}\n"
        f"Type code: {certificate_type_code or '—'}\n"
        f"Verdict: {verdict or 'review'}\n"
        f"Reasoning: {authenticity_warning or 'See platform Approvals for details.'}\n"
        f"Certificate id: {certificate_id or '—'}\n"
        f"Document id: {document_id or '—'}\n\n"
        f"Please contact the vendor and request a newly issued genuine certificate.\n\n"
        f"— Plenum Compliance Certification Center\n"
    )
    return {
        "to": to,
        "subject": subject,
        "body": body,
        "mailto_uri": _mailto(to, subject, body),
        "alert_class": 2,
        "alert_type": "forgery",
    }


def verification_human_email_draft(
    *,
    certificate_type_code: str | None,
    status: str | None = None,
    source_url: str | None = None,
    certificate_id: str | None = None,
    evidence_message: str | None = None,
) -> dict[str, Any]:
    to = settings.default_pm_email
    subject = (
        f"[Accreditation] Human verification required — "
        f"{certificate_type_code or certificate_id or 'certificate'}"
    )
    body = (
        f"FM Admin / Verifier,\n\n"
        f"Register verification could not be completed automatically.\n\n"
        f"Type code: {certificate_type_code or '—'}\n"
        f"Status: {status or 'needs_human'}\n"
        f"Certificate id: {certificate_id or '—'}\n"
        f"Register: {source_url or '—'}\n"
        f"Notes: {evidence_message or 'Open the register link and confirm accreditation.'}\n\n"
        f"— Plenum Compliance Certification Center\n"
    )
    return {
        "to": to,
        "subject": subject,
        "body": body,
        "mailto_uri": _mailto(to, subject, body),
        "alert_class": 3,
        "alert_type": "verification_human",
        "source_url": source_url,
    }
