"""What a PM can actually DO about one certificate — one rule set, both entry points.

A user meets a certificate in two places: they ask a question and get rows back, or they
upload one and it lands as a draft. The answer to "what can I do about this?" is the same
question in both cases, so it is answered here once rather than twice. When the two paths
each owned their own copy, an offer added to the query path was simply absent at ingest —
and an absent button looks exactly like a button that was never meant to be there.

Deterministic on purpose: the analyst reasons about what matters, code decides what is
invocable. Letting the model propose operations would put a "send renewal" button on rows
with nothing to renew. Every kind here maps to a real endpoint.
"""
from __future__ import annotations

from typing import Any

# Kinds the front end knows how to render and invoke. Anything else is a bug, not a feature.
OFFER_KINDS = (
    "confirm_draft",
    "renewal_email",
    "verify_now",
    "attach_certificate",
    "log_outstanding",
)


def offers_for_row(row: dict[str, Any]) -> list[dict[str, Any]]:
    """The offers one certificate row earns, from its own state.

    Expects the flags ``_derive_row_flags`` produces (``is_lapsed``, ``is_forged``, …).
    Rows that carry raw fields instead should go through ``row_from_ingest`` first.
    """
    cid = str(row.get("id") or "")
    if not cid:
        return []

    name = str(row.get("certificate_type_code") or row.get("name") or "Certificate")
    owner = str(
        row.get("vendor_name") or row.get("building_name") or row.get("site_name") or ""
    )

    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _add(kind: str, label: str, reason: str) -> None:
        if kind in seen:
            return
        seen.add(kind)
        out.append(
            {
                "cert_id": cid,
                "cert_name": name,
                "owner": owner,
                "kind": kind,
                "label": label,
                "reason": reason,
            }
        )

    if row.get("draft") and not row.get("confirmed_by_pm"):
        _add(
            "confirm_draft",
            "Mark reviewed",
            "Extracted automatically and never signed off by you.",
        )
    if row.get("is_lapsed"):
        _add(
            "renewal_email",
            "Draft renewal email",
            "Cover has already lapsed — chase a replacement certificate.",
        )
    elif row.get("is_expiring_soon"):
        days = row.get("days_to_expiry")
        _add(
            "renewal_email",
            "Draft renewal email",
            f"Expires in {days} days — start the renewal before it lapses."
            if isinstance(days, int)
            else "Expiring soon — start the renewal before it lapses.",
        )
    if row.get("is_forged") or row.get("is_suspect_authenticity"):
        _add(
            "verify_now",
            "Verify with the issuer",
            "Document authenticity is in question — check it against the register.",
        )
    elif str(row.get("authenticity_warning") or "").strip():
        _add(
            "verify_now",
            "Verify with the issuer",
            "Carries an authenticity warning that has not been cleared.",
        )
    return out


def row_from_ingest(
    *,
    cert_id: str,
    certificate_type_code: str,
    type_name: str | None = None,
    owner: str | None = None,
    status: str | None = None,
    expiry_date: str | None = None,
    forensics: dict[str, Any] | None = None,
    persisted: bool = True,
) -> dict[str, Any]:
    """Shape a just-ingested certificate like a fetched row so it earns the same offers.

    A certificate that failed to persist has no id to act on, so it earns nothing — the
    caller gets an empty list rather than buttons pointing at a record that isn't there.
    """
    if not persisted or not cert_id:
        return {}

    f = forensics or {}
    verdict = str(f.get("verdict") or "").strip().lower()

    days: int | None = None
    if expiry_date:
        from datetime import date

        try:
            days = (date.fromisoformat(str(expiry_date)[:10]) - date.today()).days
        except (ValueError, TypeError):
            days = None

    status_lc = str(status or "").strip().lower()
    is_lapsed = status_lc in {"lapsed", "expired"} or (days is not None and days < 0)

    return {
        "id": cert_id,
        "certificate_type_code": type_name or certificate_type_code,
        "vendor_name": owner or "",
        # Freshly ingested certificates are always unconfirmed drafts — that is precisely
        # why "Mark reviewed" is the offer that matters most at this moment.
        "draft": True,
        "confirmed_by_pm": False,
        "days_to_expiry": days,
        "is_lapsed": is_lapsed,
        "is_expiring_soon": bool(not is_lapsed and days is not None and 0 <= days <= 90),
        "is_forged": verdict == "fail",
        "is_suspect_authenticity": verdict == "review",
        "authenticity_warning": f.get("authenticity_warning") or "",
    }


def offers_for_missing_type(
    pack_type: dict[str, Any],
    owner: str | None = None,
    owner_kind: str | None = None,
) -> list[dict[str, Any]]:
    """What a PM can do about a required type that has NO certificate behind it.

    Every offer until now hung off a certificate row, so the one situation with no row —
    a statutory duty with nothing on record — was the one situation the interface offered
    nothing for. The answer named 41 such duties and left the reader to go and find the
    Compliance screen themselves.

    Two offers, because the two cases are genuinely different. Sometimes the certificate
    exists and simply is not in the system: attach it, and it goes through extraction and
    forensics like every other document, so nothing enters the register unevidenced.
    Sometimes it does not exist yet and has to be commissioned: log it as outstanding, and
    the obligation becomes trackable with a target date instead of living in a chat answer
    that scrolls away.

    Keyed by pack type rather than certificate id — there is no id yet, and inventing one
    would put a button on a record that does not exist.
    """
    code = str(pack_type.get("certificate_type_code") or "").strip()
    if not code:
        return []
    name = str(pack_type.get("certificate_type_name") or code)
    scope = str(pack_type.get("certificate_scope") or "").strip()
    base = {
        "cert_id": "",
        "certificate_type_code": code,
        "cert_name": name,
        "cert_scope": scope,
        "trade_category": pack_type.get("trade_category"),
        "regulation_reference": pack_type.get("regulation_reference"),
        "frequency_months": pack_type.get("frequency_months"),
        "owner": owner or "",
        "owner_kind": owner_kind or ("vendor" if scope == "Vendor" else "building"),
    }
    return [
        {
            **base,
            "kind": "attach_certificate",
            "label": "Attach certificate",
            "reason": f"{name} is required and nothing is on record. Upload the document if "
            "it already exists — it will be extracted and checked like any other.",
        },
        {
            **base,
            "kind": "log_outstanding",
            "label": "Log as outstanding",
            "reason": f"{name} has to be commissioned. Record it as an outstanding "
            "obligation with a target date so it appears in the renewal pipeline.",
        },
    ]
