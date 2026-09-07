"""CCC §8.6 — 90-day re-verification of active certificates."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models import ComplianceCertificate
from . import ccc_verify as ccc_verify_svc

log = get_logger(__name__)

REVERIFY_DAYS = 90
# Only currently-active ladder statuses (§5 / §8.6)
ACTIVE_STATUSES = {
    "Current",
    "Expiring Soon",
    "Due for Renewal",
    "Overdue",
    "Critical",
}
ACTIVE_STATUSES_NORM = {s.lower() for s in ACTIVE_STATUSES}
SKIP_STATUSES = {"lapsed", "closed", "archived", "superseded", "inactive"}


def _checked_at(meta: dict[str, Any] | None) -> datetime | None:
    if not meta:
        return None
    v = (meta.get("verification") or {}) if isinstance(meta, dict) else {}
    raw = v.get("checked_at")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_active(cert: ComplianceCertificate) -> bool:
    st = (cert.status or "").strip()
    if not st:
        # No status yet — include if expiry is in the future or missing
        if cert.expiry_date is None:
            return True
        return cert.expiry_date >= datetime.now(timezone.utc).date()
    low = st.lower()
    if low in SKIP_STATUSES:
        return False
    return low in ACTIVE_STATUSES_NORM or st in ACTIVE_STATUSES


async def reverify_due_certificates(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 50,
    force: bool = False,
) -> dict[str, Any]:
    cutoff = datetime.now(timezone.utc) - timedelta(days=REVERIFY_DAYS)
    # Fetch a wider window then filter — status values vary by casing
    fetch_n = max(1, min(limit * 4, 500))
    q = select(ComplianceCertificate).order_by(ComplianceCertificate.updated_at.asc()).limit(
        fetch_n
    )
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    certs = list((await session.execute(q)).scalars().all())

    due: list[ComplianceCertificate] = []
    for cert in certs:
        if not _is_active(cert):
            continue
        checked = _checked_at(cert.raw_metadata if isinstance(cert.raw_metadata, dict) else {})
        if force or checked is None or checked < cutoff:
            due.append(cert)

    results = []
    for cert in due[:limit]:
        try:
            res = await ccc_verify_svc.verify_stored_certificate(session, cert.id)
            results.append(
                {
                    "certificate_id": str(cert.id),
                    "status": res.get("status"),
                    "verified": res.get("verified"),
                    "ok": res.get("ok"),
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning("reverify.failed", certificate_id=str(cert.id), error=str(exc))
            results.append(
                {"certificate_id": str(cert.id), "ok": False, "error": str(exc)[:200]}
            )

    return {
        "ok": True,
        "due_count": len(due),
        "processed": len(results),
        "results": results,
        "cutoff": cutoff.isoformat(),
    }


def _already_verified(meta: dict[str, Any] | None) -> bool:
    if not isinstance(meta, dict):
        return False
    v = meta.get("verification") or {}
    if not isinstance(v, dict):
        return False
    if v.get("verified") is True:
        return True
    return str(v.get("status") or "").lower() == "verified"


async def auto_verify_listed_certificates(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 200,
    skip_verified: bool = True,
) -> dict[str, Any]:
    """
    Dashboard load path — run CCC verify (dump + register bot) for all listed
    active certificates. Skip already-verified unless skip_verified=False.
    """
    fetch_n = max(1, min(int(limit) * 2, 500))
    q = (
        select(ComplianceCertificate)
        .order_by(ComplianceCertificate.updated_at.desc())
        .limit(fetch_n)
    )
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    certs = list((await session.execute(q)).scalars().all())

    targets: list[ComplianceCertificate] = []
    skipped = 0
    for cert in certs:
        if not _is_active(cert):
            continue
        meta = cert.raw_metadata if isinstance(cert.raw_metadata, dict) else {}
        if skip_verified and _already_verified(meta):
            skipped += 1
            continue
        targets.append(cert)
        if len(targets) >= limit:
            break

    results: list[dict[str, Any]] = []
    verified_count = 0
    for cert in targets:
        try:
            res = await ccc_verify_svc.verify_stored_certificate(session, cert.id)
            ok = bool(res.get("ok"))
            is_v = res.get("verified") is True
            if is_v:
                verified_count += 1
            results.append(
                {
                    "certificate_id": str(cert.id),
                    "certificate_type_code": cert.certificate_type_code,
                    "status": res.get("status"),
                    "verified": res.get("verified"),
                    "channel": res.get("channel"),
                    "ok": ok,
                }
            )
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "auto_verify.failed",
                certificate_id=str(cert.id),
                error=str(exc)[:200],
            )
            results.append(
                {
                    "certificate_id": str(cert.id),
                    "ok": False,
                    "error": str(exc)[:200],
                }
            )

    return {
        "ok": True,
        "listed_count": len(targets) + skipped,
        "skipped_already_verified": skipped,
        "processed": len(results),
        "verified_count": verified_count,
        "results": results,
    }
