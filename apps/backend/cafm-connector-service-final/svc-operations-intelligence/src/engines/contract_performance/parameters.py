"""Feature B1 — Contract parameter extraction + criticality HITL."""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...core.logging import get_logger
from ...models.contract_performance import AssetCriticality, ContractSlaParameters
from ...shared.approvals import enqueue_approval, write_audit
from ..compliance.certificates import default_org_native
from ..compliance.contractors import resolve_or_create_vendor

log = get_logger(__name__)

DEFAULT_LABEL = "Default — not contract-sourced"

# Hours in a contracted working day, used only to derive an hourly rate from a day rate.
HOURS_PER_CONTRACT_DAY = 8.0


def contracted_hourly_rate(
    day_rate: float | None, hour_rate: float | None = None
) -> float | None:
    """The hourly rate an invoice line is checked against.

    An explicit hourly rate wins. Many FM contracts — this platform's own test contract
    included — price labour by the hour and never state a day rate, and dividing an assumed
    day rate by eight to get one produces a threshold that belongs to no contract: every
    line on a £62/h invoice breaches a default £350 day rate (£43.75/h), and the lines that
    genuinely overcharge are lost among them.

    Falls back to ``day_rate / 8`` so contracts that really are priced per day keep working.
    Returns None when neither is known, and callers then skip the rate check rather than
    inventing a threshold.

    This lives here, and not in the two places that check rates, because
    ``match_invoice_line`` and the Adversary's independent re-computation must derive the
    SAME hourly figure. If they diverge, the Adversary rejects the delta as
    ``delta_arithmetic_mismatch`` and a genuine overcharge never reaches the PM queue.
    """
    if hour_rate is not None:
        try:
            value = float(hour_rate)
        except (TypeError, ValueError):
            value = 0.0
        if value > 0:
            return value
    if day_rate is None:
        return None
    try:
        day = float(day_rate)
    except (TypeError, ValueError):
        return None
    return day / HOURS_PER_CONTRACT_DAY if day > 0 else None

# PRD: system defaults where contract is silent
SYSTEM_DEFAULTS: dict[str, Any] = {
    "sla_response_p1_hours": 1,
    "sla_response_p2_hours": 4,
    "sla_response_p3_hours": 24,
    "sla_response_p4_hours": 72,
    "sla_completion_p1_hours": 4,
    "sla_completion_p2_hours": 24,
    "sla_completion_p3_hours": 72,
    "sla_completion_p4_hours": 168,
    "labour_day_rate": 350,
    "overtime_rate": 525,
    "call_out_rate": 150,
    "payment_terms": "Net 30",
}

STANDARD_TASK_CRITICALITY = {
    "L1": "Life safety / statutory / critical plant failure",
    "L2": "Business-critical comfort or operational continuity",
    "L3": "Routine / cosmetic / non-urgent",
}

# SLA score weighting by asset criticality (PRD: L1 failures weighted 3× vs L3)
CRITICALITY_SLA_WEIGHT = {"L1": 3.0, "L2": 1.0, "L3": 1.0 / 3.0}


def merge_extraction_with_defaults(
    extracted: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[str], dict[str, str]]:
    """
    Fill silent fields with system defaults.
    Returns (merged_values, defaults_used labels, field_sources).
    """
    extracted = extracted or {}
    merged: dict[str, Any] = {}
    defaults_used: list[str] = []
    field_sources: dict[str, str] = {}

    for key, default_val in SYSTEM_DEFAULTS.items():
        if extracted.get(key) is not None and extracted.get(key) != "":
            merged[key] = extracted[key]
            field_sources[key] = "contract"
        else:
            merged[key] = default_val
            defaults_used.append(f"{key}: {DEFAULT_LABEL}")
            field_sources[key] = "default"

    for key in (
        "parts_pricing_json",
        "kpi_clauses_json",
        "ppm_obligations_json",
        "task_criticality_json",
        "payment_terms",
        "contract_ref",
        # Deliberately NOT in SYSTEM_DEFAULTS: there is no sensible default hourly rate, and
        # inventing one would silently override a day rate the contract really does state.
        "labour_hour_rate",
    ):
        if key == "payment_terms":
            continue  # already in SYSTEM_DEFAULTS
        if key == "task_criticality_json":
            if extracted.get(key):
                merged[key] = extracted[key]
                field_sources[key] = "contract"
            else:
                merged[key] = dict(STANDARD_TASK_CRITICALITY)
                defaults_used.append(f"{key}: {DEFAULT_LABEL}")
                field_sources[key] = "default"
            continue
        val = extracted.get(key)
        if val is not None and val != "" and val != {}:
            merged[key] = val
            field_sources[key] = "contract"
        else:
            merged[key] = {} if key.endswith("_json") else None
            if key.endswith("_json"):
                defaults_used.append(f"{key}: {DEFAULT_LABEL}")
                field_sources[key] = "default"

    if extracted.get("payment_terms"):
        merged["payment_terms"] = extracted["payment_terms"]
        field_sources["payment_terms"] = "contract"

    return merged, defaults_used, field_sources


def _dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


def _text(v: Any) -> str | None:
    """Coerce a free-form extracted value to text for a VARCHAR/Text column.

    payment_terms is whatever the contract says, so the model returns "30 days from date of
    correctly rendered invoice" from one contract and the number 30 from another. The column
    is Text either way, and passing the int straight through made asyncpg reject the whole
    insert — losing an otherwise complete extraction over a type the caller never promised.
    """
    if v is None or v == "":
        return None
    return v if isinstance(v, str) else str(v)


def params_to_dict(row: ContractSlaParameters) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "organization_id": str(row.organization_id) if row.organization_id else None,
        "vendor_id": str(row.vendor_id) if row.vendor_id else None,
        "contract_id": str(row.contract_id) if row.contract_id else None,
        "document_id": str(row.document_id) if row.document_id else None,
        "contract_ref": row.contract_ref,
        "signed_date": row.signed_date.isoformat() if row.signed_date else None,
        "status": row.status,
        "sla_response_p1_hours": float(row.sla_response_p1_hours) if row.sla_response_p1_hours is not None else None,
        "sla_response_p2_hours": float(row.sla_response_p2_hours) if row.sla_response_p2_hours is not None else None,
        "sla_response_p3_hours": float(row.sla_response_p3_hours) if row.sla_response_p3_hours is not None else None,
        "sla_response_p4_hours": float(row.sla_response_p4_hours) if row.sla_response_p4_hours is not None else None,
        "sla_completion_p1_hours": float(row.sla_completion_p1_hours) if row.sla_completion_p1_hours is not None else None,
        "sla_completion_p2_hours": float(row.sla_completion_p2_hours) if row.sla_completion_p2_hours is not None else None,
        "sla_completion_p3_hours": float(row.sla_completion_p3_hours) if row.sla_completion_p3_hours is not None else None,
        "sla_completion_p4_hours": float(row.sla_completion_p4_hours) if row.sla_completion_p4_hours is not None else None,
        "labour_day_rate": float(row.labour_day_rate) if row.labour_day_rate is not None else None,
        "labour_hour_rate": (
            float(row.labour_hour_rate) if row.labour_hour_rate is not None else None
        ),
        "overtime_rate": float(row.overtime_rate) if row.overtime_rate is not None else None,
        "call_out_rate": float(row.call_out_rate) if row.call_out_rate is not None else None,
        "parts_pricing_json": row.parts_pricing_json or {},
        "payment_terms": row.payment_terms,
        "kpi_clauses_json": row.kpi_clauses_json or {},
        "ppm_obligations_json": row.ppm_obligations_json or {},
        "task_criticality_json": row.task_criticality_json or {},
        "defaults_used": row.defaults_used or [],
        "overrides_log": row.overrides_log or [],
        "field_sources": row.field_sources or {},
        "confirmed_at": row.confirmed_at.isoformat() if row.confirmed_at else None,
    }


async def _existing_draft_for(
    session: AsyncSession,
    *,
    contract_ref: str | None,
    vendor_id: UUID | None,
) -> ContractSlaParameters | None:
    """The open draft this upload should update, or None to create one.

    Matched on contract_ref, which is the contract's own identity. A draft with no vendor
    is also a candidate: the first upload of a contract may not have resolved its vendor,
    and adopting that row is better than leaving an orphan next to a linked duplicate.
    Only drafts — a confirmed contract is never reopened by an upload.
    """
    if not contract_ref:
        return None
    q = select(ContractSlaParameters).where(
        ContractSlaParameters.contract_ref == contract_ref,
        ContractSlaParameters.status == "draft",
    )
    if vendor_id is not None:
        q = q.where(
            (ContractSlaParameters.vendor_id == vendor_id)
            | (ContractSlaParameters.vendor_id.is_(None))
        )
    q = q.order_by(ContractSlaParameters.created_at.desc()).limit(1)
    return (await session.execute(q)).scalar_one_or_none()


async def _has_confirmed_contract(
    session: AsyncSession,
    *,
    contract_ref: str | None,
    vendor_id: UUID | None,
) -> bool:
    """Whether a confirmed contract already governs this reference."""
    if not contract_ref:
        return False
    q = select(ContractSlaParameters.id).where(
        ContractSlaParameters.contract_ref == contract_ref,
        ContractSlaParameters.status == "confirmed",
    )
    if vendor_id is not None:
        q = q.where(ContractSlaParameters.vendor_id == vendor_id)
    return (await session.execute(q.limit(1))).scalar_one_or_none() is not None


async def ingest_contract_parameters(
    session: AsyncSession,
    *,
    extracted: dict[str, Any] | None = None,
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    vendor_name: str | None = None,
    contract_id: UUID | None = None,
    document_id: UUID | None = None,
    contract_ref: str | None = None,
    signed_date: date | str | None = None,
    building_name: str | None = None,
    building_reference: str | None = None,
    site_name: str | None = None,
    site_id: str | None = None,
    file_name: str | None = None,
) -> dict[str, Any]:
    """
    B1 — Relationships Agent handoff: merge extraction + defaults into draft
    editable table for PM confirmation.
    """
    merged, defaults_used, field_sources = merge_extraction_with_defaults(extracted)

    # A contract names its vendor; it does not carry the platform's id for them. Resolve
    # that name to an existing vendor, and register one with a fresh id when the vendor is
    # genuinely new — otherwise the contract stores vendor_id NULL and is unreachable by
    # scoring, which is how contracts went missing from vendor performance before.
    vendor_resolved_as: str | None = None
    if vendor_id is None:
        name = _text(vendor_name) or _text((extracted or {}).get("vendor_name"))
        if name:
            vendor_org = (
                organization_id
                if organization_id is not None
                else await default_org_native(session)
            )
            found = await resolve_or_create_vendor(
                session,
                company_name=name,
                organization_id=vendor_org,
                create_if_missing=bool(vendor_org),
            )
            if found:
                vendor_id = found
                vendor_resolved_as = name
            else:
                log.warning(
                    "contract.vendor_unresolved",
                    vendor_name=name[:80],
                    had_org=bool(vendor_org),
                )

    # contract_ref precedence: what the DOCUMENT says wins. The caller supplies a fallback
    # derived from the filename, and that was overriding the extracted value — so this
    # contract was stored as "UKRI-2938_FM_Contract_Gough-and-Kelly" rather than UKRI-2938,
    # labelled as contract-sourced when it came from a filename, and the same contract
    # uploaded under two names could never be recognised as the same contract.
    extracted_ref = _text((extracted or {}).get("contract_ref"))
    effective_ref = extracted_ref or _text(contract_ref)
    merged["contract_ref"] = effective_ref
    field_sources["contract_ref"] = "contract" if extracted_ref else "filename"

    # Resolve signed_date: caller value takes precedence over extraction.
    resolved_signed_date: date | None = None
    if signed_date is not None:
        if isinstance(signed_date, date):
            resolved_signed_date = signed_date
        else:
            try:
                resolved_signed_date = date.fromisoformat(str(signed_date)[:10])
            except (ValueError, TypeError):
                pass

    values: dict[str, Any] = dict(
        contract_ref=effective_ref,
        signed_date=resolved_signed_date,
        sla_response_p1_hours=_dec(merged.get("sla_response_p1_hours")),
        sla_response_p2_hours=_dec(merged.get("sla_response_p2_hours")),
        sla_response_p3_hours=_dec(merged.get("sla_response_p3_hours")),
        sla_response_p4_hours=_dec(merged.get("sla_response_p4_hours")),
        sla_completion_p1_hours=_dec(merged.get("sla_completion_p1_hours")),
        sla_completion_p2_hours=_dec(merged.get("sla_completion_p2_hours")),
        sla_completion_p3_hours=_dec(merged.get("sla_completion_p3_hours")),
        sla_completion_p4_hours=_dec(merged.get("sla_completion_p4_hours")),
        labour_day_rate=_dec(merged.get("labour_day_rate")),
        labour_hour_rate=_dec(merged.get("labour_hour_rate")),
        overtime_rate=_dec(merged.get("overtime_rate")),
        call_out_rate=_dec(merged.get("call_out_rate")),
        parts_pricing_json=merged.get("parts_pricing_json") or {},
        payment_terms=_text(merged.get("payment_terms")),
        kpi_clauses_json=merged.get("kpi_clauses_json") or {},
        ppm_obligations_json=merged.get("ppm_obligations_json") or {},
        task_criticality_json=merged.get("task_criticality_json") or STANDARD_TASK_CRITICALITY,
        raw_extraction=extracted or {},
    )

    # ── the building graph ───────────────────────────────────────────────────────────
    # The contracts view reaches a building through plenum_cafm.documents, joined on
    # document_id. With no row there the column is NULL for every contract, so the source
    # file is recorded and placed on a building here. Best-effort: a contract that cannot
    # be placed is still ingested, with the basis of the attempt kept beside it.
    async def _attach_graph(target: Any, doc_id: Any) -> dict[str, Any]:
        if doc_id is None:
            return {}
        try:
            from ..energy.graph_ingest import attach_to_graph

            out = await attach_to_graph(
                session,
                document_id=doc_id,
                building_name=building_name,
                building_reference=building_reference,
                site_name=site_name,
                site_id=site_id,
                doc_type="service_contract",
                title=effective_ref,
                file_name=file_name,
            )
            sources = dict(target.field_sources or {})
            sources["building_link"] = out.get("building_link_reason") or "unresolved"
            target.field_sources = sources
            return out
        except Exception as exc:  # noqa: BLE001 — the graph must never fail an ingest
            log.warning("contract_params.graph_attach_failed", error=str(exc)[:200])
            return {}

    existing = await _existing_draft_for(
        session, contract_ref=effective_ref, vendor_id=vendor_id
    )
    has_confirmed = existing is None and await _has_confirmed_contract(
        session, contract_ref=effective_ref, vendor_id=vendor_id
    )

    if existing is not None:
        # Re-ingesting the same contract UPDATES its draft rather than stacking another.
        # Uploading a document twice is a normal thing to do — a resend, a retry after a
        # failure, a colleague repeating the step — and every repeat was creating a rival
        # draft of the same contract, with no way to tell which one a PM should confirm.
        prior_sources = dict(existing.field_sources or {})
        pm_decided = {k for k, v in prior_sources.items() if v == "pm_override"}
        for key, value in values.items():
            # A field the PM has already ruled on is theirs. A re-upload must not quietly
            # reverse an override by overwriting it with a fresh extraction.
            if key in pm_decided:
                continue
            setattr(existing, key, value)
        if vendor_id is not None:
            existing.vendor_id = vendor_id
        if document_id is not None:
            existing.document_id = document_id
        if organization_id is not None:
            existing.organization_id = organization_id
        merged_sources = {**field_sources}
        for key in pm_decided:
            merged_sources[key] = "pm_override"
        existing.field_sources = merged_sources
        existing.defaults_used = [
            d for d in defaults_used if d.split(":", 1)[0].strip() not in pm_decided
        ]
        await session.flush()
        await _attach_graph(existing, existing.document_id)
        await write_audit(
            session,
            actor="system",
            action_type="contract_params.reingest",
            source_feature="B",
            organization_id=organization_id,
            output_payload={"id": str(existing.id)},
            detail={
                "contract_ref": effective_ref,
                "pm_overrides_preserved": sorted(pm_decided),
                "defaults_count": len(existing.defaults_used or []),
            },
        )
        await session.commit()
        return {
            "ok": True,
            "reingested": True,
            "pm_overrides_preserved": sorted(pm_decided),
            "vendor_resolved_from_name": vendor_resolved_as,
            "parameters": params_to_dict(existing),
        }

    row = ContractSlaParameters(
        id=uuid4(),
        organization_id=organization_id,
        vendor_id=vendor_id,
        contract_id=contract_id,
        document_id=document_id,
        status="draft",
        defaults_used=defaults_used,
        overrides_log=[],
        field_sources=field_sources,
        **values,
    )
    session.add(row)
    await session.flush()
    row.document_id = row.document_id or uuid4()
    await _attach_graph(row, row.document_id)

    if has_confirmed:
        # A confirmed contract already governs this reference. It is not overwritten — the
        # confirmation is a PM decision with their overrides in it — so this becomes a
        # separate draft revision for them to compare against and confirm deliberately.
        log.info(
            "contract_performance.revision_of_confirmed_contract",
            contract_ref=effective_ref,
            new_draft_id=str(row.id),
        )

    await enqueue_approval(
        session,
        source_feature="B",
        item_type="contract_params_review",
        summary=(
            f"Review revised contract SLA parameters — {row.contract_ref or row.id} "
            f"(a confirmed contract already exists for this reference)"
            if has_confirmed
            else f"Review contract SLA parameters — {row.contract_ref or row.id}"
        ),
        severity="medium",
        payload={
            "contract_sla_parameters_id": str(row.id),
            "defaults_used": defaults_used,
            "revises_confirmed_contract": has_confirmed,
        },
        organization_id=organization_id,
        related_entity_type="contract_sla_parameters",
        related_entity_id=row.id,
    )
    await write_audit(
        session,
        actor="system",
        action_type="contract_params.ingest",
        source_feature="B",
        organization_id=organization_id,
        output_payload={"id": str(row.id)},
        detail={"defaults_count": len(defaults_used)},
    )
    await session.commit()
    return {
        "ok": True,
        "reingested": False,
        "revises_confirmed_contract": has_confirmed,
        "vendor_resolved_from_name": vendor_resolved_as,
        "parameters": params_to_dict(row),
    }


async def update_contract_parameters(
    session: AsyncSession,
    parameters_id: UUID,
    updates: dict[str, Any],
    *,
    actor: str = "pm",
) -> dict[str, Any]:
    """Inline PM overrides — each override logged."""
    row = await session.get(ContractSlaParameters, parameters_id)
    if not row:
        return {"ok": False, "error": "not_found"}

    scalar_fields = {
        "sla_response_p1_hours",
        "sla_response_p2_hours",
        "sla_response_p3_hours",
        "sla_response_p4_hours",
        "sla_completion_p1_hours",
        "sla_completion_p2_hours",
        "sla_completion_p3_hours",
        "sla_completion_p4_hours",
        "labour_day_rate",
        "labour_hour_rate",
        "overtime_rate",
        "call_out_rate",
        "payment_terms",
        "contract_ref",
    }
    json_fields = {
        "parts_pricing_json",
        "kpi_clauses_json",
        "ppm_obligations_json",
        "task_criticality_json",
    }

    log_entries = list(row.overrides_log or [])
    sources = dict(row.field_sources or {})
    now = datetime.now(timezone.utc).isoformat()

    for key, value in updates.items():
        if key in scalar_fields:
            old = getattr(row, key)
            if key.endswith("_hours") or key.endswith("_rate"):
                setattr(row, key, _dec(value))
            else:
                setattr(row, key, value)
            log_entries.append(
                {"field": key, "old": str(old) if old is not None else None, "new": value, "at": now, "actor": actor}
            )
            sources[key] = "pm_override"
        elif key in json_fields:
            old = getattr(row, key)
            setattr(row, key, value or {})
            log_entries.append(
                {"field": key, "old": old, "new": value, "at": now, "actor": actor}
            )
            sources[key] = "pm_override"

    row.overrides_log = log_entries
    row.field_sources = sources
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor=actor,
        action_type="contract_params.override",
        source_feature="B",
        organization_id=row.organization_id,
        input_payload=updates,
        output_payload={"id": str(row.id)},
    )
    await session.commit()
    return {"ok": True, "parameters": params_to_dict(row)}


async def confirm_contract_parameters(
    session: AsyncSession,
    parameters_id: UUID,
    *,
    confirmed_by: UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(ContractSlaParameters, parameters_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    row.status = "confirmed"
    row.confirmed_by = confirmed_by
    row.confirmed_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor=str(confirmed_by) if confirmed_by else "pm",
        action_type="contract_params.confirm",
        source_feature="B",
        organization_id=row.organization_id,
        output_payload={"id": str(row.id)},
    )
    await session.commit()
    return {"ok": True, "parameters": params_to_dict(row)}


async def get_contract_parameters(
    session: AsyncSession,
    parameters_id: UUID,
) -> dict[str, Any]:
    row = await session.get(ContractSlaParameters, parameters_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    return {"ok": True, "parameters": params_to_dict(row)}


async def list_contract_parameters(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    vendor_id: UUID | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    q = select(ContractSlaParameters).order_by(ContractSlaParameters.created_at.desc()).limit(limit)
    if organization_id:
        q = q.where(ContractSlaParameters.organization_id == organization_id)
    if vendor_id:
        q = q.where(ContractSlaParameters.vendor_id == vendor_id)
    if status:
        q = q.where(ContractSlaParameters.status == status)
    rows = list((await session.execute(q)).scalars().all())
    out = [params_to_dict(r) for r in rows]
    # Attach the vendor's display name so consumers (chat agent, dashboards) can
    # speak in names rather than UUIDs. One query for the whole page of rows.
    vendor_ids = sorted({str(d.get("vendor_id")) for d in out if d.get("vendor_id")})
    if vendor_ids:
        try:
            from sqlalchemy import text as _text

            res = await session.execute(
                _text(
                    "SELECT id::text AS id, vendor_name FROM plenum_cafm.vendors "
                    "WHERE id::text = ANY(:ids)"
                ),
                {"ids": vendor_ids},
            )
            names = {row.id: row.vendor_name for row in res}
            for d in out:
                vid = str(d.get("vendor_id") or "")
                d["vendor_name"] = names.get(vid)
        except Exception:  # noqa: BLE001 — names are a convenience, never fail the list
            for d in out:
                d.setdefault("vendor_name", None)
    # Source document filename, so the UI can show WHICH file these parameters
    # were extracted from (contract_documents is the B1 one-to-many mapping).
    doc_ids = sorted({str(d.get("document_id")) for d in out if d.get("document_id")})
    if doc_ids:
        try:
            from sqlalchemy import text as _text

            res = await session.execute(
                _text(
                    "SELECT DISTINCT ON (document_id) document_id::text AS did, document_name "
                    "FROM plenum_cafm.contract_documents "
                    "WHERE document_id::text = ANY(:ids) AND document_name IS NOT NULL "
                    "ORDER BY document_id, created_at DESC NULLS LAST"
                ),
                {"ids": doc_ids},
            )
            doc_names = {row.did: row.document_name for row in res}
            # Fall back to the upload record — every ingested file has its
            # original filename in ingestion_documents even when no
            # contract_documents mapping row was written.
            missing = [i for i in doc_ids if i not in doc_names]
            if missing:
                res2 = await session.execute(
                    _text(
                        "SELECT id::text AS did, original_filename FROM "
                        "plenum_cafm.ingestion_documents WHERE id::text = ANY(:ids)"
                    ),
                    {"ids": missing},
                )
                for row in res2:
                    if row.original_filename:
                        doc_names[row.did] = row.original_filename
            for d in out:
                d["document_name"] = doc_names.get(str(d.get("document_id") or ""))
        except Exception:  # noqa: BLE001
            for d in out:
                d.setdefault("document_name", None)
    return out


def propose_asset_criticality(
    *,
    load_dependence: str | None = None,
    function_type: str | None = None,
    sub_meter_high: bool = False,
) -> tuple[str, str]:
    """Heuristic from UDR signals → proposed L1/L2/L3 + rationale."""
    load = (load_dependence or "").lower()
    func = (function_type or "").lower()
    if any(k in load or k in func for k in ("life", "fire", "emergency", "critical", "statutory")):
        return "L1", "UDR signals indicate life-safety / statutory criticality"
    if sub_meter_high or any(k in load or k in func for k in ("ahu", "chiller", "boiler", "lift", "data")):
        return "L2", "UDR signals indicate operational / plant criticality"
    return "L3", "UDR signals indicate routine criticality"


async def upsert_asset_criticality(
    session: AsyncSession,
    *,
    asset_id: UUID,
    asset_code: str | None = None,
    organization_id: UUID | None = None,
    proposed: str | None = None,
    load_dependence: str | None = None,
    function_type: str | None = None,
    sub_meter_high: bool = False,
    source: str = "system",
) -> dict[str, Any]:
    """Propose criticality; unapproved assets effective default L2 for scoring."""
    if not proposed:
        proposed, rationale = propose_asset_criticality(
            load_dependence=load_dependence,
            function_type=function_type,
            sub_meter_high=sub_meter_high,
        )
    else:
        rationale = "Provided by caller / contract"

    q = select(AssetCriticality).where(AssetCriticality.asset_id == asset_id)
    row = (await session.execute(q)).scalar_one_or_none()
    if row is None:
        row = AssetCriticality(
            id=uuid4(),
            organization_id=organization_id,
            asset_id=asset_id,
            asset_code=asset_code,
            criticality="L2",  # effective until approved
            proposed_criticality=proposed,
            source=source,
            rationale=rationale,
            approved=False,
        )
        session.add(row)
        await enqueue_approval(
            session,
            source_feature="B",
            item_type="asset_criticality_review",
            summary=f"Approve asset criticality {asset_code or asset_id} → {proposed}",
            severity="medium",
            payload={
                "asset_id": str(asset_id),
                "proposed_criticality": proposed,
                "rationale": rationale,
            },
            organization_id=organization_id,
            related_entity_type="asset_criticality",
            related_entity_id=row.id,
        )
    else:
        row.proposed_criticality = proposed
        row.rationale = rationale
        row.source = source
        row.updated_at = datetime.now(timezone.utc)
        if not row.approved:
            row.criticality = "L2"

    await session.flush()
    await session.commit()
    return {
        "ok": True,
        "asset_criticality": {
            "id": str(row.id),
            "asset_id": str(row.asset_id),
            "asset_code": row.asset_code,
            "criticality": row.criticality,
            "proposed_criticality": row.proposed_criticality,
            "approved": row.approved,
            "rationale": row.rationale,
            "effective_for_scoring": row.criticality if row.approved else "L2",
        },
    }


async def approve_asset_criticality(
    session: AsyncSession,
    asset_criticality_id: UUID,
    *,
    criticality: str | None = None,
    approved_by: UUID | None = None,
) -> dict[str, Any]:
    row = await session.get(AssetCriticality, asset_criticality_id)
    if not row:
        return {"ok": False, "error": "not_found"}
    chosen = criticality or row.proposed_criticality or "L2"
    if chosen not in {"L1", "L2", "L3"}:
        return {"ok": False, "error": "invalid_criticality"}
    row.criticality = chosen
    row.approved = True
    row.approved_by = approved_by
    row.approved_at = datetime.now(timezone.utc)
    row.source = "pm"
    row.updated_at = datetime.now(timezone.utc)
    await write_audit(
        session,
        actor=str(approved_by) if approved_by else "pm",
        action_type="asset_criticality.approve",
        source_feature="B",
        organization_id=row.organization_id,
        output_payload={"id": str(row.id), "criticality": chosen},
    )
    await session.commit()
    return {
        "ok": True,
        "asset_criticality": {
            "id": str(row.id),
            "criticality": row.criticality,
            "approved": True,
        },
    }


async def effective_criticality(
    session: AsyncSession,
    asset_id: UUID | None,
) -> str:
    """Unapproved → L2 (PRD)."""
    if not asset_id:
        return "L2"
    q = select(AssetCriticality).where(AssetCriticality.asset_id == asset_id)
    row = (await session.execute(q)).scalar_one_or_none()
    if not row or not row.approved:
        return "L2"
    return row.criticality or "L2"


async def propose_criticality_from_udr(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Read assets from UDR/plenum_cafm and propose L1/L2/L3 for HITL.
    Uses category/name/description as load/function signals; sub-meter if present.
    """
    from sqlalchemy import text

    proposed_rows: list[dict[str, Any]] = []
    try:
        rows = (
            await session.execute(
                text(
                    """
                    SELECT a.id::text AS asset_id,
                           a.asset_code,
                           COALESCE(a.category, '') AS category,
                           COALESCE(a.asset_name, a.name, '') AS asset_name,
                           COALESCE(a.description, '') AS description
                    FROM plenum_cafm.assets a
                    ORDER BY a.asset_code
                    LIMIT :lim
                    """
                ),
                {"lim": limit},
            )
        ).mappings().all()
    except Exception as exc:  # noqa: BLE001
        log.warning("asset_criticality.udr_read_failed", error=str(exc)[:200])
        return {"ok": False, "error": str(exc)[:300], "proposed": []}

    for r in rows:
        signals = f"{r['category']} {r['asset_name']} {r['description']}"
        level, rationale = propose_asset_criticality(
            load_dependence=signals,
            function_type=signals,
            sub_meter_high=False,
        )
        result = await upsert_asset_criticality(
            session,
            asset_id=UUID(str(r["asset_id"])),
            asset_code=r["asset_code"],
            organization_id=organization_id,
            proposed=level,
            load_dependence=signals[:200],
            function_type=r["category"] or None,
            source="udr",
        )
        proposed_rows.append(result.get("asset_criticality") or {})

    return {
        "ok": True,
        "count": len(proposed_rows),
        "proposed": proposed_rows,
        "message": "Human approval mandatory — unapproved assets score as L2.",
    }


async def list_asset_criticalities(
    session: AsyncSession,
    *,
    organization_id: UUID | None = None,
    approved: bool | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    q = select(AssetCriticality).order_by(AssetCriticality.updated_at.desc()).limit(limit)
    if organization_id:
        q = q.where(AssetCriticality.organization_id == organization_id)
    if approved is not None:
        q = q.where(AssetCriticality.approved.is_(approved))
    rows = list((await session.execute(q)).scalars().all())
    return [
        {
            "id": str(r.id),
            "asset_id": str(r.asset_id),
            "asset_code": r.asset_code,
            "criticality": r.criticality,
            "proposed_criticality": r.proposed_criticality,
            "approved": r.approved,
            "rationale": r.rationale,
            "source": r.source,
            "effective_for_scoring": r.criticality if r.approved else "L2",
        }
        for r in rows
    ]
