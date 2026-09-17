"""Re-ask which building a certificate belongs to, for certificates that never got an answer.

``attach_to_graph`` resolves a certificate's building once, at ingest. If the answer was no —
the building had not been created yet, the name did not match, the PDF named no building at
all — nothing ever asks again. The certificate sits in the register, correct and complete,
and is invisible to every building-scoped view and every coverage figure, permanently and
silently. ``site_links.backfill_site_links`` exists for the separate ``site_id`` link; this is
the same contract for ``building_id``.

It answers from evidence, in order of how much the evidence actually claims:

1. **What the certificate says** — building name, building reference, site. The same
   resolver the ingest uses, so a certificate that would link today links now.
2. **Where its source document sits** — a document placed on a building was placed there by
   whoever uploaded it, who said which building they meant. Weaker than the certificate
   naming its own building, stronger than nothing, and recorded as such.
3. **Nothing**, which is the honest answer for most of them and the useful part of the
   report: a certificate that names no building, whose document names no building, cannot
   be placed by any rule. Inventing one from a filename would put a legal obligation on a
   building on no evidence. These come back listed, with the fields that are empty, so the
   gap is visible and fixable by someone who knows the answer.

Vendor-scope certificates are reported separately as ``not_applicable``: an ISO 9001 or a
public liability insurance covers a contractor, not a building, and a null there is correct
rather than missing. Counting them as failures would make the report cry wolf.

Defaults to a dry run, like every other backfill here: the report says what would change
and on what basis before a row moves.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger

log = get_logger(__name__)

#: Certificates whose scope names a contractor rather than a building. A null building_id on
#: these is the right answer, not a gap.
VENDOR_SCOPES = ("vendor", "contractor", "company", "organisation", "organization")

#: Metadata flags that mark a row as not really part of the register.
_EXCLUDE_FLAGS = ("a1_test_fixture", "superseded_duplicate", "archived")


def _excluded(meta: dict[str, Any] | None) -> bool:
    meta = meta or {}
    return any(meta.get(flag) for flag in _EXCLUDE_FLAGS)


async def _document_building(session: AsyncSession, document_id: Any) -> str | None:
    """The building the certificate's source document sits on, if it sits on one."""
    if not document_id:
        return None
    try:
        async with session.begin_nested():
            row = (
                await session.execute(
                    text(
                        """SELECT building_id::text FROM plenum_cafm.documents
                            WHERE document_id::text = :doc AND building_id IS NOT NULL
                            LIMIT 1"""
                    ),
                    {"doc": str(document_id)},
                )
            ).first()
    except Exception as exc:  # noqa: BLE001 — a missing table is an absent answer, not a failure
        log.warning("compliance.backfill.document_lookup_failed", error=str(exc)[:200])
        return None
    return row[0] if row else None


async def backfill_building_links(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    dry_run: bool = True,
    limit: int = 1000,
) -> dict[str, Any]:
    """Link certificates in the register to the building they belong to.

    Returns the four outcomes separately, because they need different actions: ``linked`` is
    done, ``ambiguous`` needs a person to choose, ``no_evidence`` needs someone to say which
    building the certificate is for, and ``not_applicable`` needs nothing at all.
    """
    from sqlalchemy import select

    from ...models import ComplianceCertificate
    from ..energy.graph_ingest import resolve_building_for

    q = select(ComplianceCertificate).where(
        ComplianceCertificate.building_id.is_(None)
    ).limit(int(limit))
    if organization_id:
        q = q.where(
            (ComplianceCertificate.organization_id == organization_id)
            | (ComplianceCertificate.org_id == organization_id)
        )
    certs = [c for c in (await session.execute(q)).scalars().all()
             if not _excluded(c.raw_metadata)]

    linked: list[dict[str, Any]] = []
    ambiguous: list[dict[str, Any]] = []
    no_evidence: list[dict[str, Any]] = []
    not_applicable: list[dict[str, Any]] = []

    for cert in certs:
        meta = dict(cert.raw_metadata or {})
        name = cert.building_name or meta.get("building_name")
        reference = cert.building_reference or meta.get("building_reference")
        site = cert.site_ref or (str(cert.site_id) if cert.site_id else None)
        entry: dict[str, Any] = {
            "certificate_id": str(cert.id),
            "certificate_type_code": cert.certificate_type_code or cert.cert_type,
            "cert_scope": cert.cert_scope,
            "building_name": name,
            "building_reference": reference,
            "site": site,
        }

        if (cert.cert_scope or "").strip().lower() in VENDOR_SCOPES:
            entry["reason"] = "covers a contractor, not a building"
            not_applicable.append(entry)
            continue

        resolved = await resolve_building_for(
            session, building_name=name, building_reference=reference,
            site_name=meta.get("site_label"), site_id=site,
        )
        building_id = resolved.get("building_id")
        basis = "certificate"
        if not building_id and resolved.get("outcome") != "ambiguous":
            doc_building = await _document_building(
                session, cert.document_id or cert.source_document_id
            )
            if doc_building:
                building_id = doc_building
                basis = "document"

        entry["reason"] = resolved.get("reason")
        if building_id:
            entry["building_id"] = str(building_id)
            entry["building"] = resolved.get("building_label")
            entry["basis"] = basis
            linked.append(entry)
            if not dry_run:
                cert.building_id = UUID(str(building_id))
                meta["building_link"] = {
                    "outcome": "resolved", "basis": basis,
                    "reason": resolved.get("reason"), "via": "backfill",
                }
                cert.raw_metadata = meta
        elif resolved.get("outcome") == "ambiguous":
            ambiguous.append(entry)
        else:
            # Say which of the three things a resolver could have used is actually absent.
            entry["missing"] = [
                field for field, value in (
                    ("building_name", name),
                    ("building_reference", reference),
                    ("site", site),
                    ("document_on_a_building", None),
                ) if not value
            ]
            no_evidence.append(entry)

    if not dry_run and linked:
        await session.commit()

    return {
        "ok": True,
        "dry_run": dry_run,
        "examined": len(certs),
        "counts": {
            "linked": len(linked),
            "ambiguous": len(ambiguous),
            "no_evidence": len(no_evidence),
            "not_applicable": len(not_applicable),
        },
        "linked": linked,
        "ambiguous": ambiguous,
        "no_evidence": no_evidence,
        "not_applicable": not_applicable,
        "note": (
            "not_applicable certificates cover a contractor rather than a building; a null "
            "building_id on those is correct. no_evidence certificates name no building, no "
            "reference and no site, and their source document is on no building either — "
            "nothing in the data says where they belong, so nothing here guesses."
        ),
    }
