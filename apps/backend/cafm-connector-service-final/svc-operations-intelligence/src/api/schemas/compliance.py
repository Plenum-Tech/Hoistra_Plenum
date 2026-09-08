from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class CertificateUpsertRequest(BaseModel):
    # The building and location fields below exist as columns, are populated by document
    # extraction, and are what the dashboard groups by — but they were absent from this
    # request model, so anything sent through the API was dropped by Pydantic without a
    # word. A certificate added by hand could not be given the building it belongs to or
    # the region it sits in, and the caller got a 200 saying it had worked.
    cert_scope: str = Field(..., description="Building | Vendor")
    building_name: str | None = None
    building_reference: str | None = None
    state: str | None = None
    region: str | None = None
    certificate_type_code: str
    certificate_number: str | None = None
    certificate_ref: str | None = None
    organization_id: UUID | None = None
    asset_id: UUID | None = None
    site_id: UUID | None = None
    vendor_id: UUID | None = None
    # Company / trading name — used to resolve vendors.id when vendor_id omitted (A3)
    vendor_name: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    next_due_date: str | None = None
    inspection_frequency_months: int | None = None
    inspector_name: str | None = None
    inspector_accreditation_number: str | None = None
    result: str | None = None
    defects_found: str | None = None
    remedial_actions: str | None = None
    remedial_status: str | None = None
    document_id: UUID | None = None
    country_code: str = "UK"
    issuer: str | None = None
    field_confidence: dict[str, Any] = Field(default_factory=dict)
    raw_metadata: dict[str, Any] = Field(default_factory=dict)
    confirmed_by_pm: bool = False
    id: UUID | None = None


class RemedialStatusRequest(BaseModel):
    remedial_status: str


class ConfirmCertificateRequest(BaseModel):
    """PM confirm/Review body — carries the approve/skip choices for the document→entity links.

    ``link_targets`` is the subset of ["vendor", "site", "asset"] the PM approved for linking.
    Omit (null) to link every resolvable target; [] to finalise without linking any."""

    link_targets: list[str] | None = None


class LinkCertificateDocumentRequest(BaseModel):
    """Auto-link the cert's document_id to the PM-chosen vendor/site/asset table rows.

    Each id is the candidate row the PM picked in the center-chat match card. Supply only the
    targets to link; omitted targets are left unchanged."""

    vendor_id: str | None = None
    site_id: str | None = None
    asset_id: str | None = None


class CreateVendorForCertificateRequest(BaseModel):
    """Seed a vendor row from the center-chat 'create vendor' form, then link this certificate.

    Beyond the name, the form carries the rich profile fields extracted from the document
    (trade, address, contact details) so the vendors table is populated as fully as possible."""

    vendor_name: str
    trade_category: str | None = None
    trade: str | None = None
    address: str | None = None
    city: str | None = None
    postal_code: str | None = None
    country: str | None = None
    phone: str | None = None
    fax: str | None = None
    email: str | None = None
    website: str | None = None
    specialty: str | None = None
    status: str | None = None


class ArchiveCertificatesRequest(BaseModel):
    """CCC §3 — soft-archive the chosen certificates (multi-select, reversible)."""

    certificate_ids: list[UUID] = Field(
        ..., min_length=1, description="Certificate ids the PM confirmed for removal."
    )
    reason: str | None = None
    archived_by: UUID | None = None
    organization_id: UUID | None = None


class UnarchiveCertificatesRequest(BaseModel):
    """Reverse a soft-archive."""

    certificate_ids: list[UUID] = Field(..., min_length=1)
    restored_by: UUID | None = None
    organization_id: UUID | None = None


class RenewalEmailRequest(BaseModel):
    certificate_id: UUID | None = None
    certificate_number: str | None = None


class BuildingChangeRequest(BaseModel):
    site_id: UUID
    change_description: str
    organization_id: UUID | None = None


class ScanRequest(BaseModel):
    organization_id: UUID | None = None
    scope: str = "all"
    site_id: UUID | None = None
    certificate_type_code: str | None = None


class QueueDecisionRequest(BaseModel):
    decision: str
    pm_notes: str | None = None
    edited_payload: dict[str, Any] | None = None
    decided_by: UUID | None = None
    prepare_email_handoff: bool = True
    send_email: bool | None = None


class AdversaryRequest(BaseModel):
    check_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    organization_id: UUID | None = None


class VerifyNowRequest(BaseModel):
    certificate_type_code: str
    accreditation_number: str | None = None
    country_code: str = "UK"
    vendor_name: str | None = None
    certificate_id: UUID | None = Field(
        None,
        description="Name the certificate and the register link is stored on it, so it "
                    "survives a reload instead of living only in the turn that built it. "
                    "Storing a link never marks the certificate verified — a link to a "
                    "register says where to look, not that anyone looked.",
    )


class CccVerifyRequest(BaseModel):
    """CCC §8 — channel-aware verification against compliance_verification_sources."""

    certificate_type_code: str
    certificate_number: str | None = None
    accreditation_number: str | None = None
    vendor_name: str | None = None
    company_number: str | None = None
    postcode: str | None = None
    insurer_name: str | None = None
    certificate_id: UUID | None = None
    organization_id: UUID | None = None
    persist: bool = True


class DocumentForensicsRequest(BaseModel):
    """Signals for document authenticity / forensics scoring."""

    source_text: str | None = None
    file_name: str | None = None
    certificate_type_code: str | None = None
    pdf_metadata: dict[str, Any] = Field(default_factory=dict)
    page_count: int | None = None
    text_char_count: int | None = None
    is_encrypted: bool | None = None
    has_text_layer: bool | None = None
    file_size_bytes: int | None = None
    # CCC §7 — cryptographic PDF digital-signature result (from svc-deepagents).
    digital_signature: dict[str, Any] | None = None


class VendorRegistrationCheckRequest(BaseModel):
    """Search on-platform vendor registration + compliance; build Verify-now URL."""

    vendor_name: str | None = None
    certificate_type_code: str
    accreditation_number: str | None = None
    cert_scope: str | None = "Vendor"
    country_code: str = "UK"
    organization_id: UUID | None = None


class CountryPackLoadRequest(BaseModel):
    pack: dict[str, Any]


class ExtractRequest(BaseModel):
    certificate_type_code: str
    country_code: str = "UK"
    cert_scope: str | None = None
    source_text: str | None = None
    extracted_fields: dict[str, Any] | None = None
    # Full PDF (base64) for Claude native document extract — scanned certs
    pdf_base64: str | None = None
    extract_method: str | None = None


class BatchIngestFile(BaseModel):
    """One PDF in a CCC §3.1 batch (own document_id, indexed independently)."""

    document_id: UUID | None = None
    file_name: str | None = None
    # At least one of source_text / pdf_base64 should be present to classify + extract.
    source_text: str | None = None
    pdf_base64: str | None = None
    # Optional — skips classification when the PM already picked the type.
    certificate_type_code: str | None = None
    cert_scope: str | None = None
    site_id: UUID | None = None
    vendor_id: UUID | None = None
    asset_id: UUID | None = None
    vendor_name: str | None = None
    certificate_number: str | None = None
    extract_method: str | None = None
    country_code: str | None = None
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class BatchIngestRequest(BaseModel):
    """CCC §3.1 — up to 5 compliance PDFs uploaded from the UDR chat, ingested in parallel."""

    files: list[BatchIngestFile] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="Up to 5 compliance PDFs; each processed in parallel (one document_id each).",
    )
    organization_id: UUID | None = None
    country_code: str = "UK"


class TableMatchRequest(BaseModel):
    """Match an ingested document to its best plenum_cafm target table(s).

    Compliance certificates also return the deterministic compliance target
    (certificate type -> compliance_certificates); every document returns a ranked
    list of candidate tables with confidence.
    """

    source_text: str | None = None
    pdf_base64: str | None = None
    file_name: str | None = None
    certificate_type_code: str | None = None
    country_code: str = "UK"
    top_k: int = Field(5, ge=1, le=10)


class ActivateBuildingPackRequest(BaseModel):
    site_id: UUID
    country_code: str = "UK"
    pack_version: str | None = None
    organization_id: UUID | None = None


class PackVersionNotifyRequest(BaseModel):
    country_code: str
    new_version: str
    change_notes: str | None = None
    organization_id: UUID | None = None


class PackThresholdsUpdateRequest(BaseModel):
    """PM admin — edit A1 alert ladder for one certificate type."""

    alert_thresholds: dict[str, int]
    country_code: str = "UK"
    pack_version: str | None = None
    organization_id: UUID | None = None


class ResourceSkillRequest(BaseModel):
    vendor_id: UUID
    skill_type_code: str
    organization_id: UUID | None = None
    resource_id: UUID | None = None
    operative_name: str | None = None
    certificate_number: str | None = None
    issue_date: str | None = None
    expiry_date: str | None = None
    id: UUID | None = None


class RecommendContractorsRequest(BaseModel):
    required_accreditation: str
    organization_id: UUID | None = None
    limit: int = 5


class PassportShareRequest(BaseModel):
    ttl_hours: int = 168
    recipient: str | None = None
    organization_id: UUID | None = None


class EvidencePackRequest(BaseModel):
    organization_id: UUID | None = None
    site_id: UUID | None = None
    vendor_id: UUID | None = None
    include_approvals: bool = True


class MembershipRequest(BaseModel):
    """CCC §3.2 — keep or remove a compliance PDF from the vector DB."""

    action: str  # keep | remove
    confirmed_by: UUID | None = None
    organization_id: UUID | None = None
    actor: str | None = None


class VerificationDumpIngestRequest(BaseModel):
    """CCC §8.2 — ingest CSV/JSON dump rows for a certificate type."""

    certificate_type_code: str
    content: str
    source_file: str | None = None
    format: str = "auto"  # auto | csv | json


class ReverifyRequest(BaseModel):
    """CCC §8.6 — re-verify active certificates due for 90-day check."""

    organization_id: UUID | None = None
    limit: int = 50
    force: bool = False


class AutoVerifyRequest(BaseModel):
    """Dashboard load — auto-verify all listed active certificates."""

    organization_id: UUID | None = None
    limit: int = 200
    skip_verified: bool = True


class RegisterSearchRequest(BaseModel):
    """Search dumps / live bot by company name; auto-verify when confident."""

    register: str  # ukas | bpca | basis | hse | sia
    name: str = ""  # company / organisation name
    query: str = ""  # alias for name
    mode: str = "auto"  # auto | dump | bot
    limit: int = 25
