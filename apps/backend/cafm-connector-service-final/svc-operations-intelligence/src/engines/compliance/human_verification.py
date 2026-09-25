"""A person checks a certificate against its register, and the platform records what they found.

The platform cannot verify most UK certificates itself: the registers are websites with no
API (BAFE, NICEIC, Gas Safe search pages), or a partner API nobody has configured. The CCC
path says so — `website_only`, `needs_human`, `needs_partner` — and queues a
`verification_human` item. What it never had was the other half: a way for the person who
opened the register to say what it showed. Deciding that queue item only sends an email, so
a certificate someone had checked by hand read "needs human" for ever.

Two pieces:

* `certificate_verify_link` — the register link for one certificate, built without touching
  the database. The certificate list carries it, so the Verify link is a plain hyperlink.
  (`POST /verify-now` builds the same link but also writes it onto the certificate, which a
  link rendered on every row must not do.)
* `record_human_verification` — writes the person's finding into
  `raw_metadata.verification`, merged, and closes the pending `verification_human` items for
  that certificate. It never changes `status` or the expiry: a register check says whether the
  certificate is real, not whether it is in date.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.compliance import ApprovalsQueueItem, ComplianceCertificate
from ...shared.approvals import write_audit
from .verification import (
    _GOVUK_ENERGY_HOST,
    _append_register_search_params,
    resolve_verification_register,
)

log = get_logger(__name__)

#: What a person can report after looking a certificate up on its register.
OUTCOMES: dict[str, dict[str, Any]] = {
    "confirmed": {"status": "human_verified", "verified": True},
    "not_found": {"status": "human_not_found", "verified": False},
    "mismatch": {"status": "human_mismatch", "verified": False},
}


def _is_web_url(url: Any) -> bool:
    from urllib.parse import urlparse

    try:
        p = urlparse(str(url or "").strip())
    except ValueError:
        return False
    return p.scheme in ("https", "http") and bool(p.netloc)


def certificate_verify_link(
    *,
    register_url: str | None,
    certificate_type_code: str | None,
    issuing_body: str | None = None,
    certificate_number: str | None = None,
    accreditation_number: str | None = None,
    vendor_name: str | None = None,
    stored: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Where to check this certificate, or None when its type has no public register.

    `stored` is `raw_metadata.verification`: a link a verify run already built (possibly
    prefilled from fields the list row does not carry) is preferred over rebuilding one.
    """
    reg = resolve_verification_register(
        certificate_type_code=certificate_type_code,
        issuing_body=issuing_body,
        verification_url=register_url,
    )
    base = register_url or (reg or {}).get("verification_url")
    st = stored or {}
    url = st.get("verification_url") or st.get("source_url")
    built = None
    if base:
        built = _append_register_search_params(
            base,
            accreditation_number=accreditation_number,
            vendor_name=vendor_name,
            prefills_number=bool((reg or {}).get("prefills_number", True)),
            certificate_number=certificate_number,
        )
        # A stored GOV.UK link may predate the 20-digit fix and point at the error page.
        if not url or (_GOVUK_ENERGY_HOST in str(url) and "reference_number=" in str(url)):
            url = built
    # Only a web address is a link. The stored URL is client-writable (the certificate
    # upsert accepts raw_metadata), and a "javascript:" value rendered into <a href> would
    # run in the app when someone clicked Verify.
    if url and not _is_web_url(url):
        url = built if base and _is_web_url(built) else None
    if not url:
        return None

    # Said about the link actually built, not about the number: a 20-digit Gas Safe number
    # is not a GOV.UK reference, and a GOV.UK page reached through the accreditation number
    # is still that certificate's own page.
    direct = str(url).startswith(_GOVUK_ENERGY_HOST + "/energy-certificate/")
    if direct:
        note = "Opens this certificate's own page on the GOV.UK register."
    elif _GOVUK_ENERGY_HOST in str(url):
        note = (
            "Opens the GOV.UK search. The number on file is not a 20-digit lodgement "
            "reference, so it cannot be looked up directly — search by address instead."
            if certificate_number
            else "Opens the GOV.UK search — no certificate number is on file."
        )
    elif url != base:
        note = "Opens the register with the number or company name filled in."
    else:
        note = "Opens the register's search page — enter the number from the certificate."
    return {
        "url": url,
        "register": (reg or {}).get("register") or issuing_body,
        "direct": direct,
        "note": note,
    }


def _uuid_or_none(v: Any) -> UUID | None:
    if isinstance(v, UUID):
        return v
    try:
        return UUID(str(v)) if v not in (None, "") else None
    except (ValueError, TypeError, AttributeError):
        return None


async def account_label(session: AsyncSession, user_id: Any) -> str | None:
    """The signed-in account's email (or name) for the "Checked by" line, or None."""
    if not user_id:
        return None
    from sqlalchemy import text

    try:
        async with session.begin_nested():
            row = (await session.execute(
                text("SELECT to_jsonb(u)->>'email' AS email, "
                     "COALESCE(to_jsonb(u)->>'full_name', to_jsonb(u)->>'name') AS name "
                     "FROM plenum_cafm.users u WHERE u.id::text = :id"),
                {"id": str(user_id)},
            )).mappings().first()
    except Exception as exc:  # noqa: BLE001 — a missing label must not fail the write
        log.warning("human_verification.account_lookup_failed", error=str(exc)[:200])
        return None
    return (row and (row.get("email") or row.get("name"))) or None


async def record_human_verification(
    session: AsyncSession,
    certificate_id: UUID,
    *,
    outcome: str,
    note: str | None = None,
    register_url: str | None = None,
    checked_by: Any = None,
    checked_by_label: str | None = None,
) -> dict[str, Any]:
    """Record what a person found on the register. Merged; never touches status or expiry."""
    key = (outcome or "").strip().lower()
    if key not in OUTCOMES:
        return {"ok": False, "error": "outcome must be one of: " + ", ".join(OUTCOMES)}
    note = (note or "").strip() or None
    # A negative finding is an accusation against a document; it has to say what was seen.
    if key != "confirmed" and not note:
        return {"ok": False, "error": "a note is required when the register does not confirm it"}

    cert = await session.get(ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "not_found"}

    now = datetime.now(timezone.utc)
    meta = dict(cert.raw_metadata or {})
    ver = dict(meta.get("verification") or {})
    previous = ver.get("status")
    ver.update({
        **OUTCOMES[key],
        "channel": "human",
        "checked_at": now.isoformat(),
        "checked_by": str(checked_by) if checked_by else None,
        "checked_by_label": checked_by_label,
        "human_note": note,
        "human_register_url": register_url or ver.get("verification_url") or ver.get("source_url"),
        "previous_status": previous,
    })
    meta["verification"] = ver
    cert.raw_metadata = meta
    cert.updated_at = now

    # The queue item that asked for this check is answered by it.
    items = (
        await session.execute(
            select(ApprovalsQueueItem).where(
                ApprovalsQueueItem.item_type == "verification_human",
                ApprovalsQueueItem.status == "pending",
                ApprovalsQueueItem.related_entity_id == cert.id,
            )
        )
    ).scalars().all()
    # The queue item asked for a check; its status says how the check came out. A negative
    # finding closed as "approved" read, to everything that counts approvals by status, as a
    # certificate that passed.
    closed_as = "approved" if key == "confirmed" else "rejected"
    # decided_by is a uuid column, but production's plenum_cafm.users is integer-keyed, so
    # the signed-in id is often an int — binding it there fails the commit (asyncpg calls
    # .bytes on it) and the whole check 500s. The id goes in only when it is a uuid; who
    # checked is always kept in the verification block and the audit row as text.
    decided_by = _uuid_or_none(checked_by)
    for item in items:
        item.status = closed_as
        item.decided_at = now
        item.decided_by = decided_by
        item.pm_notes = f"Checked on the register: {key}" + (f" — {note}" if note else "")

    await write_audit(
        session,
        actor=f"user:{checked_by}" if checked_by else "user:pm",
        action_type="compliance.human_verification",
        source_feature="A",
        organization_id=cert.organization_id or cert.org_id,
        output_payload={"certificate_id": str(cert.id), "outcome": key, "status": ver["status"]},
        detail={"note": note, "register_url": ver["human_register_url"], "previous_status": previous,
                "queue_items_closed": [str(i.id) for i in items]},
    )
    await session.commit()
    log.info("compliance.human_verification", certificate_id=str(cert.id), outcome=key)
    return {
        "ok": True,
        "certificate_id": str(cert.id),
        "verification": ver,
        "queue_items_closed": len(items),
    }
