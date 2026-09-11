"""Compliance Engine API routes — Features A1–A5."""
from __future__ import annotations

from uuid import UUID

from typing import Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...engines.compliance import certificates as cert_svc
from ...engines.compliance import country_pack as pack_svc
from ...engines.compliance import scan as scan_svc
from ...engines.compliance import verification as verify_svc
from ...engines.compliance import verification_sources as sources_svc
from ...engines.compliance import ccc_verify as ccc_verify_svc
from ...engines.compliance import document_forensics as forensics_svc
from ...engines.compliance import extract as extract_svc
from ...engines.compliance import batch_ingest as batch_ingest_svc
from ...engines.compliance import table_match as table_match_svc
from ...engines.compliance import building_pack as building_pack_svc
from ...engines.compliance import resource_skills as skill_svc
from ...engines.compliance import contractors as contractor_svc
from ...engines.compliance import tokens as token_svc
from ...engines.compliance import coverage as coverage_svc
from ...engines.compliance import evidence_pack as evidence_svc
from ...engines.compliance import passport as passport_svc
from ...engines.compliance import membership as membership_svc
from ...engines.compliance import register_dump as dump_svc
from ...engines.compliance.channels import register_search as register_search_svc
from ...engines.compliance import reverify as reverify_svc
from ...shared import approvals as approvals_svc
from ...swarm import adversary as adversary_svc
from ..schemas.compliance import (
    FilingRequest,
    ActivateBuildingPackRequest,
    AdversaryRequest,
    ArchiveCertificatesRequest,
    AutoVerifyRequest,
    BatchIngestRequest,
    BuildingChangeRequest,
    CccVerifyRequest,
    CertificateUpsertRequest,
    ConfirmCertificateRequest,
    CreateVendorForCertificateRequest,
    LinkCertificateDocumentRequest,
    CountryPackLoadRequest,
    DocumentForensicsRequest,
    EvidencePackRequest,
    ExtractRequest,
    MembershipRequest,
    VendorRegistrationCheckRequest,
    PackThresholdsUpdateRequest,
    PackVersionNotifyRequest,
    PassportShareRequest,
    QueueDecisionRequest,
    RecommendContractorsRequest,
    RemedialStatusRequest,
    RenewalEmailRequest,
    ResourceSkillRequest,
    RegisterSearchRequest,
    ReverifyRequest,
    ScanRequest,
    TableMatchRequest,
    UnarchiveCertificatesRequest,
    VerificationDumpIngestRequest,
    VerifyNowRequest,
)

from ...engines import ingest_gate
from ...engines.auth import access
from ...engines.compliance import epc_rating as epc_svc
from ...engines.compliance import filings as filings_svc
from ...engines.energy import ratings_position as position_svc
from .auth import scope

router = APIRouter(prefix="/api/compliance", tags=["compliance"],
                   # Every route here needs a signed-in caller, and a company named in
                   # the query string must be the caller's own (or the caller a
                   # superadmin). Before this, every endpoint was open and tenancy
                   # was whatever organization_id the client chose to send.
                   dependencies=[Depends(scope)])


class ValidateDocumentRequest(BaseModel):
    """What extraction produced, before anything is written."""
    doc_type: str = Field(
        ..., description="compliance_certificate | vendor_invoice | service_contract"
    )
    extracted: dict[str, Any] = Field(default_factory=dict)
    certificate_type_code: str | None = Field(
        None, description="Required for a certificate — the pack declares its mandatory fields per type."
    )
    country_code: str | None = None
    field_confidence: dict[str, Any] | None = Field(
        None, description="Per-field confidence from extraction. Anything low is surfaced to confirm rather than retype."
    )
    answers: dict[str, Any] | None = Field(
        None, description="A person's answers to a previous round. Merged before re-checking."
    )
    answered_by: str | None = None


@router.post("/documents/validate")
async def validate_document(
    body: ValidateDocumentRequest,
    session: AsyncSession = Depends(get_session),
):
    """What must be answered before this document can be written. Reads only; writes nothing.

    Call it with what extraction produced. `ready: false` means a mandatory field could not
    be read, and `questions` is what to put to the person — only the fields that are actually
    missing, plus any read with low confidence shown for confirmation rather than retyping.

    Send the answers back in `answers` to re-check. When `ready` is true the merged payload
    in `document` is what to ingest, and it records which fields a person supplied: a date
    typed at ingest is usable evidence, and it is not the same as one read off the
    certificate.

    For a certificate the mandatory list comes from the country pack, which declares it per
    certificate type — this endpoint does not hold a second copy of that answer.
    """
    merged = ingest_gate.apply_answers(
        body.extracted, body.answers, answered_by=body.answered_by
    )
    out = await ingest_gate.check_document(
        session,
        body.doc_type,
        merged,
        certificate_type_code=body.certificate_type_code,
        country_code=body.country_code,
        field_confidence=body.field_confidence,
    )
    out["document"] = merged
    out["answered"] = sorted((merged.get("raw_metadata") or {}).get("answered_fields") or {})
    return out


class GatedCertificateRequest(ValidateDocumentRequest):
    """A certificate ingest that refuses to write an incomplete record."""
    confirmed_by_pm: bool = False
    link_targets: list[str] | None = None


@router.post("/documents/ingest")
async def ingest_document_gated(
    body: GatedCertificateRequest,
    response: Response,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Validate, then write — and refuse to write until the mandatory fields are there.

    422 with the questions when something required could not be read. Nothing is written on
    that path: a certificate with no expiry never becomes due, so it drops silently out of
    compliance reporting, and an absence is far more expensive to find later than a question
    is to answer now.

    Send the answers back in `answers` and call again. Only the certificate type is wired
    here so far; invoices and contracts validate through /documents/validate and ingest
    through their own routes.
    """
    access.assert_can_ingest(s)
    merged = ingest_gate.apply_answers(
        body.extracted, body.answers, answered_by=body.answered_by
    )
    gate = await ingest_gate.check_document(
        session,
        body.doc_type,
        merged,
        certificate_type_code=body.certificate_type_code,
        country_code=body.country_code,
        field_confidence=body.field_confidence,
    )
    if not gate["ready"]:
        response.status_code = 422
        return {**gate, "written": False, "document": merged}

    if body.doc_type != "compliance_certificate":
        response.status_code = 400
        return {
            **gate, "written": False,
            "error": f"No gated ingest for {body.doc_type} yet — it validates here and "
                     "ingests through its own route.",
        }

    payload = dict(merged)
    payload.setdefault("certificate_type_code", body.certificate_type_code)
    payload.setdefault("country_code", body.country_code)
    result = await cert_svc.upsert_certificate(
        session, payload,
        confirmed_by_pm=body.confirmed_by_pm,
        link_targets=body.link_targets,
    )
    # Read the outcome rather than assuming it. Reporting a write that the engine refused
    # is the one failure a gate must not have: the caller stops asking, and nothing is
    # there.
    wrote = bool((result or {}).get("ok", True))
    if not wrote:
        response.status_code = 422
    return {**gate, "written": wrote, "certificate": result,
            "error": None if wrote else (result or {}).get("error")}


@router.post("/certificates")
async def upsert_certificate(
    body: CertificateUpsertRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    data = body.model_dump()
    data["organization_id"] = access.organization_for(s, body.organization_id)
    confirmed = data.pop("confirmed_by_pm", False)
    return await cert_svc.upsert_certificate(session, data, confirmed_by_pm=confirmed)


@router.get("/certificates")
async def list_certificates(
    cert_scope: str | None = None,
    status: str | None = None,
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    asset_id: UUID | None = None,
    site_id: UUID | None = None,
    site_ref: str | None = Query(
        None,
        description="Site key for a non-UUID-keyed sites table (see site_links).",
    ),
    risk_filter: str | None = Query(
        None,
        description="blocked|high|medium|lapsed|active|expiring_90|expiring_30",
    ),
    draft: bool | None = Query(
        None,
        description="true = only unconfirmed draft certificates awaiting PM confirmation; "
        "false = only finalised. Draft is a flag, not a lifecycle status.",
    ),
    certificate_type_code: str | None = Query(
        None,
        description="Filter by type code or synonym (e.g. FRA, 'Gas Safe') — used by the "
        "remove-with-confirmation picker to list one type.",
    ),
    trade_category: str | None = Query(
        None, description="Filter by trade/category (e.g. Fire, Electrical, Energy, Security)."
    ),
    vendor_name: str | None = Query(
        None, description="Filter by vendor / company name (partial, case-insensitive)."
    ),
    expiring_within_days: int | None = Query(
        None, description="Only certs expiring within N days (not already lapsed)."
    ),
    expiry_month: str | None = Query(
        None, description="Only certs whose expiry falls in this calendar month, YYYY-MM."
    ),
    include_archived: bool = Query(
        False, description="Include soft-archived certificates (default hidden)."
    ),
    limit: int = Query(200, le=1000),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    # The company is the caller's, not the query string's.
    organization_id = s.organization_id
    rows = await cert_svc.list_certificates(
        session,
        cert_scope=cert_scope,
        status=status,
        organization_id=organization_id,
        vendor_id=vendor_id,
        asset_id=asset_id,
        site_id=site_id,
        site_ref=site_ref,
        risk_filter=risk_filter,
        draft=draft,
        certificate_type_code=certificate_type_code,
        trade_category=trade_category,
        vendor_name=vendor_name,
        expiring_within_days=expiring_within_days,
        expiry_month=expiry_month,
        include_archived=include_archived,
        limit=limit,
    )
    if s.restricted:
        # A plain user sees certificates on their buildings, plus vendor accreditations
        # (which name no property) — never another building's certificates.
        rows = [r for r in rows
                if s.allows_building(r.get("building_id"))
                or (not r.get("building_id") and str(r.get("cert_scope") or "").lower() == "vendor")]
    return {
        "ok": True,
        "count": len(rows),
        "certificates": rows,
        "risk_filter": risk_filter,
        "draft": draft,
        "certificate_type_code": certificate_type_code,
        "trade_category": trade_category,
        "vendor_name": vendor_name,
        "expiring_within_days": expiring_within_days,
        "expiry_month": expiry_month,
    }


@router.get("/certificates/count")
async def count_certificates(
    cert_scope: str | None = None,
    status: str | None = None,
    cert_type: str | None = Query(
        None,
        description="Type code or brand synonym (e.g. Gas Safe → GAS_SAFE)",
    ),
    certificate_number: str | None = None,
    inspector_name: str | None = None,
    result: str | None = None,
    remedial_status: str | None = None,
    insurance_risk_flag: bool | None = Query(
        None,
        description="true = only certificates with insurance_risk_flag set",
    ),
    country_code: str | None = None,
    draft: bool | None = Query(None),
    issuer_contains: str | None = Query(
        None,
        description="Substring match on issuer / issuing_body (e.g. SIA, BAFE)",
    ),
    vendor_name_contains: str | None = None,
    certificate_name_contains: str | None = None,
    organization_id: UUID | None = None,
    risk_filter: str | None = Query(
        None,
        description="blocked|high|medium|lapsed|active|expiring_90|expiring_30",
    ),
    limit: int = Query(500, le=1000),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Deterministic attribute count — use for 'how many have X' questions."""
    organization_id = access.organization_for(s, organization_id)
    return await cert_svc.count_certificates(
        session,
        cert_scope=cert_scope,
        status=status,
        cert_type=cert_type,
        certificate_number=certificate_number,
        inspector_name=inspector_name,
        result=result,
        remedial_status=remedial_status,
        insurance_risk_flag=insurance_risk_flag,
        country_code=country_code,
        draft=draft,
        issuer_contains=issuer_contains,
        vendor_name_contains=vendor_name_contains,
        certificate_name_contains=certificate_name_contains,
        organization_id=organization_id,
        risk_filter=risk_filter,
        limit=limit,
    )


@router.get("/vendors/certificate-counts")
async def vendors_by_certificate_count(
    min_count: int = Query(2, ge=0),
    comparison: str = Query(
        "gt",
        pattern="^(gt|gte|eq)$",
        description="gt = more than, gte = at least, eq = exactly",
    ),
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Return vendors grouped by their number of live accreditation certificates."""
    organization_id = access.organization_for(s, organization_id)
    return await cert_svc.list_vendors_by_certificate_count(
        session,
        min_count=min_count,
        comparison=comparison,
        organization_id=organization_id,
    )


@router.patch("/certificates/{certificate_id}/remedial-status")
async def set_remedial_status(
    certificate_id: UUID,
    body: RemedialStatusRequest,
    session: AsyncSession = Depends(get_session),
):
    return await cert_svc.set_remedial_status(
        session, certificate_id, body.remedial_status
    )


@router.post("/certificates/{certificate_id}/confirm")
async def confirm_certificate(
    certificate_id: UUID,
    body: ConfirmCertificateRequest | None = None,
    session: AsyncSession = Depends(get_session),
):
    """PM confirms a draft certificate — finalises it (clears the draft flag) and links the
    source document to the vendor/site/asset records the PM approved (body.link_targets)."""
    return await cert_svc.confirm_certificate(
        session, certificate_id, link_targets=body.link_targets if body else None
    )


@router.get("/certificates/{certificate_id}/link-candidates")
async def certificate_link_candidates(
    certificate_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """Match a certificate's document against the vendor / site / asset tables and return the
    top candidate rows for each (with the currently-linked row flagged). Read-only."""
    cert = await session.get(cert_svc.ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    return {"ok": True, **(await cert_svc.resolve_link_candidates(session, cert))}


@router.post("/certificates/{certificate_id}/link")
async def link_certificate_document(
    certificate_id: UUID,
    body: LinkCertificateDocumentRequest,
    session: AsyncSession = Depends(get_session),
):
    """Auto-link the cert's document_id to the PM-chosen vendor/site/asset rows."""
    return await cert_svc.link_certificate_document(
        session,
        certificate_id,
        vendor_id=body.vendor_id,
        site_id=body.site_id,
        asset_id=body.asset_id,
    )


@router.post("/certificates/{certificate_id}/create-vendor")
async def create_vendor_for_certificate(
    certificate_id: UUID,
    body: CreateVendorForCertificateRequest,
    session: AsyncSession = Depends(get_session),
):
    """Create a vendor row from the chat form (vendor cert with no extractable name) and link
    this certificate + its document to the new vendor, populating the rich profile fields."""
    profile = {
        k: v
        for k, v in {
            "trade": body.trade,
            "address": body.address,
            "city": body.city,
            "postal_code": body.postal_code,
            "country": body.country,
            "phone": body.phone,
            "fax": body.fax,
            "email": body.email,
            "website": body.website,
            "specialty": body.specialty,
            "status": body.status,
        }.items()
        if v
    }
    return await cert_svc.create_vendor_for_certificate(
        session,
        certificate_id,
        vendor_name=body.vendor_name,
        trade_category=body.trade_category,
        profile=profile,
    )


@router.post("/certificates/{certificate_id}/extract-vendor-profile")
async def extract_vendor_profile(
    certificate_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    """LLM-extract a rich vendor profile (name, trade, address, contact details) from the
    certificate document, to pre-fill the center-chat create-vendor form."""
    cert = await session.get(cert_svc.ComplianceCertificate, certificate_id)
    if not cert:
        return {"ok": False, "error": "certificate not found"}
    profile = await cert_svc.extract_vendor_profile(session, cert)
    return {"ok": True, "certificate_id": str(cert.id), "profile": profile}


@router.post("/certificates/archive")
async def archive_certificates(
    body: ArchiveCertificatesRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """CCC §3 — soft-archive the PM-confirmed certificates (reversible; multi-select)."""
    org_id = access.organization_for(s, body.organization_id)
    return await cert_svc.archive_certificates(
        session,
        certificate_ids=body.certificate_ids,
        reason=body.reason,
        archived_by=body.archived_by,
        organization_id=org_id,
    )


@router.post("/certificates/unarchive")
async def unarchive_certificates(
    body: UnarchiveCertificatesRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Reverse a soft-archive."""
    org_id = access.organization_for(s, body.organization_id)
    return await cert_svc.unarchive_certificates(
        session,
        certificate_ids=body.certificate_ids,
        restored_by=body.restored_by,
        organization_id=org_id,
    )


@router.post("/certificates/renewal-email")
async def draft_renewal_email(
    body: RenewalEmailRequest,
    session: AsyncSession = Depends(get_session),
):
    """Draft a renewal email for one selected certificate (by id or number) and queue it."""
    return await cert_svc.draft_renewal_email(
        session,
        certificate_id=body.certificate_id,
        certificate_number=body.certificate_number,
    )


@router.post("/building-change")
async def building_change(
    body: BuildingChangeRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await cert_svc.building_change_invalidate(
        session,
        site_id=body.site_id,
        change_description=body.change_description,
        organization_id=org_id,
    )


@router.post("/scan")
async def run_scan(
    body: ScanRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await scan_svc.run_compliance_scan(
        session,
        organization_id=org_id,
        scope=body.scope,
        site_id=body.site_id,
        certificate_type_code=body.certificate_type_code,
    )


@router.get("/saved-space/summary")
async def saved_space_summary(
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    return {
        "ok": True,
        **(await cert_svc.saved_space_summary(session, organization_id=organization_id)),
    }


@router.get("/country-pack")
async def list_country_pack(
    country_code: str = "UK",
    scope: str | None = None,
    pack_version: str | None = None,
    trade_category: str | None = Query(
        None,
        description="Optional trade filter (e.g. Fire, Gas) — case-insensitive",
    ),
    session: AsyncSession = Depends(get_session),
):
    rows = await pack_svc.list_pack_types(
        session,
        country_code=country_code,
        scope=scope,
        pack_version=pack_version,
    )
    if trade_category and trade_category.strip():
        needle = trade_category.strip().lower()
        rows = [
            r
            for r in rows
            if needle in (getattr(r, "trade_category", None) or "").lower()
        ]
    return {
        "ok": True,
        "entity": "CountryPack",
        "count": len(rows),
        "trade_category": trade_category,
        "types": [pack_svc.pack_to_dict(r) for r in rows],
    }


@router.get("/country-pack/entity")
async def country_pack_entity(
    country_code: str = "UK",
    pack_version: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """A4 CountryPack aggregate entity (pack_id, version, type_counts, types)."""
    return await pack_svc.get_country_pack_entity(
        session, country_code=country_code, pack_version=pack_version
    )


@router.post("/country-pack/seed-uk")
async def seed_uk_pack(
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await pack_svc.seed_uk_pack(session, force=force)


@router.post("/country-pack/seed-uae")
async def seed_uae_pack(
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await pack_svc.seed_uae_pack(session, force=force)


@router.post("/country-pack/seed-us")
async def seed_us_pack(
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await pack_svc.seed_us_pack(session, force=force)


@router.post("/country-pack/seed-all")
async def seed_all_packs(
    force: bool = False,
    session: AsyncSession = Depends(get_session),
):
    return await pack_svc.seed_all_packs(session, force=force)


@router.post("/country-pack/load")
async def load_country_pack(
    body: CountryPackLoadRequest,
    session: AsyncSession = Depends(get_session),
):
    return await pack_svc.load_country_pack_from_json_admin(session, body.pack)


@router.patch("/country-pack/types/{certificate_type_code}/thresholds")
async def update_pack_type_thresholds(
    certificate_type_code: str,
    body: PackThresholdsUpdateRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """PM admin — edit A1 alert ladder thresholds for one Country Pack type."""
    org_id = access.organization_for(s, body.organization_id)
    return await pack_svc.update_pack_type_thresholds(
        session,
        certificate_type_code=certificate_type_code,
        alert_thresholds=body.alert_thresholds,
        country_code=body.country_code,
        pack_version=body.pack_version,
        organization_id=org_id,
    )


@router.get("/verification-sources")
async def list_verification_sources(
    cert_scope: str | None = None,
    channel: str | None = None,
    session: AsyncSession = Depends(get_session),
):
    """CCC §8.8 — compliance_verification_sources config rows."""
    rows = await sources_svc.list_verification_sources(
        session, cert_scope=cert_scope, channel=channel
    )
    return {"ok": True, "count": len(rows), "sources": rows}


@router.post("/verification-sources/seed")
async def seed_verification_sources_route(
    session: AsyncSession = Depends(get_session),
):
    return await sources_svc.seed_verification_sources(session)


@router.get("/catalogue")
async def compliance_catalogue(
    session: AsyncSession = Depends(get_session),
):
    """54-card certificate catalogue with verification channel badges."""
    return await sources_svc.list_catalogue(session)


@router.post("/verify")
async def ccc_verify(
    body: CccVerifyRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """CCC Step 4 — public_api when available, else Verify-now / needs_human."""
    org_id = access.organization_for(s, body.organization_id)
    return await ccc_verify_svc.verify_certificate_type(
        session,
        certificate_type_code=body.certificate_type_code,
        certificate_number=body.certificate_number,
        accreditation_number=body.accreditation_number,
        vendor_name=body.vendor_name,
        company_number=body.company_number,
        postcode=body.postcode,
        insurer_name=body.insurer_name,
        certificate_id=body.certificate_id,
        organization_id=org_id,
        persist=body.persist,
    )


@router.post("/certificates/{certificate_id}/verify")
async def verify_certificate(
    certificate_id: UUID,
    session: AsyncSession = Depends(get_session),
):
    return await ccc_verify_svc.verify_stored_certificate(session, certificate_id)


@router.post("/documents/{document_id}/membership")
async def document_membership(
    document_id: UUID,
    body: MembershipRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """CCC §3.2 — confirm Keep or Remove for a compliance PDF in the vector DB."""
    org_id = access.organization_for(s, body.organization_id)
    return await membership_svc.decide_membership(
        session,
        document_id=document_id,
        action=body.action,
        actor=body.actor,
        organization_id=org_id,
        confirmed_by=body.confirmed_by,
    )


@router.post("/verification-dumps/ingest")
async def ingest_verification_dump(
    body: VerificationDumpIngestRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """CCC §8.2 — ingest CSV/JSON weekly dump content into local register rows."""
    access.assert_can_ingest(s)
    return await dump_svc.ingest_dump_text(
        session,
        certificate_type_code=body.certificate_type_code,
        content=body.content,
        source_file=body.source_file,
        format=body.format,
    )


@router.post("/verification-dumps/run-cron")
async def run_verification_dump_cron(session: AsyncSession = Depends(get_session)):
    """CCC §8.2 — manually trigger weekly dump-dir ingest (smoke / ops)."""
    return await dump_svc.weekly_dump_cron(session)


@router.get("/register-search")
async def register_search_get(
    register: str,
    name: str = "",
    q: str = "",
    mode: str = "auto",
    limit: int = 25,
    session: AsyncSession = Depends(get_session),
):
    """Search by company name (dump + optional live bot). Confident hit → verified."""
    return await register_search_svc.run_register_search(
        session,
        register=register,
        name=name or q,
        query=q,
        mode=mode,
        limit=limit,
    )


@router.post("/register-search")
async def register_search_post(
    body: RegisterSearchRequest,
    session: AsyncSession = Depends(get_session),
):
    return await register_search_svc.run_register_search(
        session,
        register=body.register,
        name=body.name or body.query,
        query=body.query,
        mode=body.mode,
        limit=body.limit,
    )


@router.post("/reverify")
async def reverify_certificates(
    body: ReverifyRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """CCC §8.6 — re-verify certificates due for 90-day check (manual trigger)."""
    org_id = access.organization_for(s, body.organization_id)
    return await reverify_svc.reverify_due_certificates(
        session,
        organization_id=org_id,
        limit=body.limit,
        force=body.force,
    )


@router.post("/auto-verify")
async def auto_verify_listed(
    body: AutoVerifyRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Dashboard load — run register bot / dump verify for all listed certificates."""
    org_id = access.organization_for(s, body.organization_id)
    return await reverify_svc.auto_verify_listed_certificates(
        session,
        organization_id=org_id,
        limit=body.limit,
        skip_verified=body.skip_verified,
    )


@router.get("/verification-registers")
async def verification_registers():
    """UK pack Verify-now register table (trade, prefills_number, URL)."""
    rows = verify_svc.list_verification_registers()
    return {"ok": True, "count": len(rows), "registers": rows}


@router.post("/verify-now")
async def verify_now(
    body: VerifyNowRequest,
    session: AsyncSession = Depends(get_session),
):
    return await verify_svc.build_verify_now_link(
        session,
        certificate_type_code=body.certificate_type_code,
        accreditation_number=body.accreditation_number,
        country_code=body.country_code,
        vendor_name=body.vendor_name,
        # Name the certificate and the link is written onto it, so "Verify now (SIA)" is
        # still there on reload instead of living only in the turn that built it.
        certificate_id=getattr(body, "certificate_id", None),
    )


@router.post("/forensics")
async def document_forensics(body: DocumentForensicsRequest):
    """Document forensics / authenticity check (soft gate — no live register scrape)."""
    return forensics_svc.run_document_forensics(
        source_text=body.source_text,
        file_name=body.file_name,
        certificate_type_code=body.certificate_type_code,
        pdf_metadata=body.pdf_metadata,
        page_count=body.page_count,
        text_char_count=body.text_char_count,
        is_encrypted=body.is_encrypted,
        has_text_layer=body.has_text_layer,
        file_size_bytes=body.file_size_bytes,
        digital_signature=body.digital_signature,
    )


@router.post("/vendor-registration-check")
async def vendor_registration_check(
    body: VendorRegistrationCheckRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """
    Search whether vendor is registered on-platform and currently compliant,
    and return the Verify-now register URL (navigation only — no scrape).
    """
    org_id = access.organization_for(s, body.organization_id)
    return await verify_svc.check_vendor_registration_compliance(
        session,
        vendor_name=body.vendor_name,
        certificate_type_code=body.certificate_type_code,
        accreditation_number=body.accreditation_number,
        cert_scope=body.cert_scope,
        country_code=body.country_code,
        organization_id=org_id,
    )


@router.get("/approvals")
async def list_approvals(
    status: str | None = "pending",
    source_feature: str | None = "A",
    organization_id: UUID | None = None,
    limit: int = Query(100, le=500),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    organization_id = access.organization_for(s, organization_id)
    items = await approvals_svc.list_queue(
        session,
        organization_id=organization_id,
        status=status,
        source_feature=source_feature,
        limit=limit,
    )
    return {
        "ok": True,
        "count": len(items),
        "items": [approvals_svc.queue_item_to_dict(i) for i in items],
    }


@router.post("/approvals/{item_id}/decide")
async def decide_approval(
    item_id: UUID,
    body: QueueDecisionRequest,
    session: AsyncSession = Depends(get_session),
):
    return await approvals_svc.decide_queue_item(
        session,
        item_id,
        decision=body.decision,
        pm_notes=body.pm_notes,
        edited_payload=body.edited_payload,
        decided_by=body.decided_by,
        prepare_email_handoff=body.prepare_email_handoff,
    )


@router.post("/adversary")
async def adversary_check(
    body: AdversaryRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await adversary_svc.run_adversary(
        session,
        check_type=body.check_type,
        payload=body.payload,
        organization_id=org_id,
    )


@router.post("/extract")
async def extract_fields(
    body: ExtractRequest,
    session: AsyncSession = Depends(get_session),
):
    return await extract_svc.extract_certificate_fields(
        session,
        certificate_type_code=body.certificate_type_code,
        source_text=body.source_text,
        extracted_fields=body.extracted_fields,
        country_code=body.country_code,
        cert_scope=body.cert_scope,
        pdf_base64=body.pdf_base64,
        extract_method=body.extract_method,
    )


@router.post("/ingest-batch")
async def ingest_batch(
    body: BatchIngestRequest,
    s: access.Scope = Depends(scope),
):
    """CCC §3.1 — ingest up to 5 compliance PDFs from the UDR chat in parallel.

    Each file is classified, field-extracted, and saved as a draft (one document_id
    each) in its own session; one file failing does not block the others. Drafts await
    PM confirmation and vector-membership Keep/Remove in the Compliance Saved Space.
    """
    access.assert_can_ingest(s)
    org_id = access.organization_for(s, body.organization_id)
    return await batch_ingest_svc.ingest_batch(
        files=[f.model_dump() for f in body.files],
        organization_id=org_id,
        country_code=body.country_code,
    )


@router.post("/table-match")
async def table_match(
    body: TableMatchRequest,
    session: AsyncSession = Depends(get_session),
):
    """Match an ingested document to its plenum_cafm target table(s).

    Compliance certificates get BOTH the deterministic compliance target
    (certificate type -> compliance_certificates) and a ranked candidate list; any
    other document gets the ranked candidate list only.
    """
    return await table_match_svc.match_document(
        session,
        source_text=body.source_text,
        pdf_base64=body.pdf_base64,
        file_name=body.file_name,
        country_code=body.country_code,
        certificate_type_code=body.certificate_type_code,
        top_k=body.top_k,
    )


@router.post("/buildings/activate-pack")
async def activate_building_pack(
    body: ActivateBuildingPackRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await building_pack_svc.activate_building_pack(
        session,
        site_id=body.site_id,
        country_code=body.country_code,
        pack_version=body.pack_version,
        organization_id=org_id,
    )


@router.post("/country-pack/notify-version")
async def notify_pack_version(
    body: PackVersionNotifyRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await building_pack_svc.notify_pack_version_change(
        session,
        country_code=body.country_code,
        new_version=body.new_version,
        change_notes=body.change_notes,
        organization_id=org_id,
    )


@router.post("/resource-skills")
async def upsert_resource_skill(
    body: ResourceSkillRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    data = body.model_dump()
    data["organization_id"] = access.organization_for(s, body.organization_id)
    return await skill_svc.upsert_resource_skill(session, data)


@router.get("/resource-skills")
async def list_resource_skills(
    vendor_id: UUID | None = None,
    limit: int = Query(200, le=500),
    session: AsyncSession = Depends(get_session),
):
    rows = await skill_svc.list_vendor_operative_skills(
        session, vendor_id, limit=limit
    )
    return {"ok": True, "count": len(rows), "skills": rows}


@router.post("/contractors/recommend")
async def recommend_contractors(
    body: RecommendContractorsRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    rows = await contractor_svc.recommend_contractors(
        session,
        required_accreditation=body.required_accreditation,
        organization_id=org_id,
        limit=body.limit,
    )
    return {"ok": True, "count": len(rows), "contractors": rows}


@router.get("/coverage/buildings")
async def coverage_buildings(
    organization_id: UUID | None = None,
    country_code: str = "UK",
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Per-building CountryPack coverage %."""
    organization_id = access.organization_for(s, organization_id)
    return await coverage_svc.building_coverage(
        session, organization_id=organization_id, country_code=country_code
    )


@router.post("/coverage/backfill-site-links")
async def backfill_site_links(
    organization_id: UUID | None = None,
    dry_run: bool = Query(
        True,
        description="true (default) reports what would be linked and writes nothing.",
    ),
    limit: int = Query(1000, ge=1, le=5000),
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Link building certificates already in the register to the site they belong to.

    Certificates ingested before site resolution worked carry a building name but no site
    link, which is why per-site coverage had never been exercised. Defaults to a dry run:
    the report names every certificate that would be linked and on what evidence, every one
    that matches two sites (never linked — a certificate filed against the wrong building
    misstates both buildings' obligations), and every building that matches no site row.
    """
    organization_id = access.organization_for(s, organization_id)
    from ...engines.compliance.site_links import backfill_site_links as _backfill

    return await _backfill(
        session, organization_id=organization_id, dry_run=dry_run, limit=limit
    )


@router.get("/coverage/vendors")
async def coverage_vendors(
    organization_id: UUID | None = None,
    country_code: str = "UK",
    trade_category: str | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Per-vendor CountryPack coverage % (optional trade filter)."""
    organization_id = access.organization_for(s, organization_id)
    return await coverage_svc.vendor_coverage(
        session,
        organization_id=organization_id,
        country_code=country_code,
        trade_category=trade_category,
    )


@router.get("/evidence-pack")
async def evidence_pack_get(
    organization_id: UUID | None = None,
    site_id: UUID | None = None,
    vendor_id: UUID | None = None,
    include_approvals: bool = True,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """One-click PDF evidence pack for insurers / auditors / BSA."""
    organization_id = access.organization_for(s, organization_id)
    return await evidence_svc.generate_evidence_pack_file(
        session,
        organization_id=organization_id,
        site_id=site_id,
        vendor_id=vendor_id,
        include_approvals=include_approvals,
    )


@router.post("/evidence-pack")
async def evidence_pack_post(
    body: EvidencePackRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    org_id = access.organization_for(s, body.organization_id)
    return await evidence_svc.generate_evidence_pack_file(
        session,
        organization_id=org_id,
        site_id=body.site_id,
        vendor_id=body.vendor_id,
        include_approvals=body.include_approvals,
    )


@router.get("/vendors/{vendor_id}/passport")
async def vendor_passport(
    vendor_id: str,
    organization_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Pre-engagement vendor passport (accreditations + insurance + posture)."""
    organization_id = access.organization_for(s, organization_id)
    return await passport_svc.build_vendor_passport(
        session, vendor_id, organization_id=organization_id
    )


@router.post("/vendors/{vendor_id}/passport/share")
async def vendor_passport_share(
    vendor_id: str,
    body: PassportShareRequest | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Create a time-limited shareable passport link."""
    org_id = access.organization_for(s, body.organization_id)
    body = body or PassportShareRequest()
    return await passport_svc.share_vendor_passport(
        session,
        vendor_id,
        organization_id=org_id,
        ttl_hours=body.ttl_hours,
        recipient=body.recipient,
    )


@router.get("/passport/share/{token}")
async def vendor_passport_by_token(
    token: str,
    session: AsyncSession = Depends(get_session),
):
    """Public/read-only passport resolve from share token."""
    return await passport_svc.passport_from_share_token(session, token)


@router.get("/approvals/one-click/{token}")
async def one_click_approve(
    token: str,
    redeem: bool = Query(False, description="If true, redeem immediately; else redirect to platform"),
    session: AsyncSession = Depends(get_session),
):
    """
    Email links previously hit this API. Browsers now redirect into Orchestrator
    `/ai?space=compliance&approvalToken=…` so the PM approves in-platform.
    Pass ?redeem=1 to keep the old auto-approve JSON behaviour.
    """
    from fastapi.responses import RedirectResponse

    if redeem:
        return await token_svc.redeem_approval_token(session, token)

    peeked = await token_svc.peek_approval_token(session, token)
    if not peeked.get("ok"):
        return peeked
    item_id = peeked["queue_item_id"]
    action = peeked.get("action") or "approve"
    status = peeked.get("ladder_status")
    # Rebuild canonical platform URL (same shape as create_approval_token)
    from ...config import settings
    from urllib.parse import urlencode

    q = {
        "space": "compliance",
        "approvalToken": token,
        "approvalItem": item_id,
        "approvalAction": action,
    }
    if status:
        q["ladderStatus"] = str(status)
    url = f"{settings.frontend_public_url.rstrip('/')}/ai?{urlencode(q)}"
    return RedirectResponse(url=url, status_code=302)


@router.get("/approvals/one-click/{token}/peek")
async def one_click_peek(token: str, session: AsyncSession = Depends(get_session)):
    """Load approval details for the platform deep-link UI (does not redeem)."""
    return await token_svc.peek_approval_token(session, token)


@router.post("/approvals/one-click/{token}/redeem")
async def one_click_redeem(
    token: str,
    body: QueueDecisionRequest = QueueDecisionRequest(decision="approve"),
    session: AsyncSession = Depends(get_session),
):
    """Redeem token from platform Approve booking / Acknowledge button."""
    return await token_svc.redeem_approval_token(
        session,
        token,
        decision=body.decision,
        pm_notes=body.pm_notes,
    )


# ── B5 · MEES from the EPC register ─────────────────────────────────────────────────────

@router.get("/mees")
async def mees_summary(
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Which buildings sit below EPC E now and below B for 2030, from the EPCs on file."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await epc_svc.mees_summary(session, organization_id=s.organization_id, building_ids=ids)


# ── B8 · filings: LL84, BCA benchmarking, Green Mark ────────────────────────────────────

@router.post("/filings", status_code=201)
async def record_filing(
    body: FilingRequest,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Record that a filing was made (or a certification awarded) for a building and year.
    Idempotent on (building, scheme, year): filing the same year again updates the record."""
    from datetime import date as _date
    org_id = access.organization_for(s, body.organization_id)
    access.assert_building(s, body.building_id, action="record a filing for")
    try:
        return await filings_svc.record_filing(
            session, organization_id=org_id, building_id=body.building_id, scheme=body.scheme,
            period_year=body.period_year, status=body.status,
            filed_at=_date.fromisoformat(body.filed_at) if body.filed_at else None,
            reference=body.reference, certification_level=body.certification_level,
            valid_until=_date.fromisoformat(body.valid_until) if body.valid_until else None,
            submitted_by=body.submitted_by, evidence_document_id=body.evidence_document_id,
            detail=body.detail, actor=str(s.user_id), created_by=s.user_id)
    except filings_svc.FilingError as exc:
        raise HTTPException(status_code=exc.http_status,
                            detail={"ok": False, "error": exc.message, "reason": exc.reason})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail={"ok": False, "error": str(exc), "reason": "bad_date"})


@router.get("/filings")
async def list_filings(
    building_id: UUID | None = None,
    scheme: str | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    ids = await position_svc.building_ids_for(session, s, building_id)
    rows = await filings_svc.list_filings(session, organization_id=s.organization_id, building_ids=ids, scheme=scheme)
    return {"ok": True, "count": len(rows), "filings": rows, "schemes": {
        k: {"label": v["label"], "country": v["country"], "kind": v["kind"], "note": v["note"]}
        for k, v in filings_svc.SCHEMES.items()}}


@router.get("/filings/position")
async def filings_position(
    country_code: str = Query(..., description="US | SG"),
    building_id: UUID | None = None,
    session: AsyncSession = Depends(get_session),
    s: access.Scope = Depends(scope),
):
    """Where each building stands on the country's filing obligations: filed / due / overdue,
    certified / lapsed / none."""
    ids = await position_svc.building_ids_for(session, s, building_id)
    return await filings_svc.filing_positions(session, organization_id=s.organization_id, building_ids=ids,
                                              country_code=country_code)
