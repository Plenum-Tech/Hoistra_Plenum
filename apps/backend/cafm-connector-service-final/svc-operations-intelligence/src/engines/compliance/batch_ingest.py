"""CCC §3.1 — parallel bulk ingest of up to 5 compliance PDFs from the UDR chat.

Each file is classified → field-extracted → upserted as a **draft** (HITL, C4) in its
own ``AsyncSession`` so ``asyncio.gather`` is concurrency-safe (same pattern as the
nightly scan workers). One file failing must not block the other four — every worker
catches its own error and returns a per-file result. The draft upsert already chains
vector-membership pending (§3.2), the PM-confirm approval, and auto-verify (§8), so the
batch endpoint only orchestrates; it does not duplicate that logic.
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import uuid4

from ...core.logging import get_logger
from ...db import AsyncSessionLocal
from . import certificates as cert_svc
from . import classify as classify_svc
from . import extract as extract_svc

log = get_logger(__name__)

MAX_BATCH_FILES = 5


async def ingest_batch(
    *,
    files: list[dict[str, Any]],
    organization_id: Any | None = None,
    country_code: str = "UK",
) -> dict[str, Any]:
    """Ingest up to 5 compliance PDFs in parallel. Returns a per-file result list."""
    items = list(files or [])
    if not items:
        return {"ok": False, "error": "no files supplied"}
    if len(items) > MAX_BATCH_FILES:
        return {
            "ok": False,
            "error": f"at most {MAX_BATCH_FILES} PDFs per batch (got {len(items)})",
            "max_files": MAX_BATCH_FILES,
            "requested": len(items),
        }

    batch_id = str(uuid4())
    tasks = [
        _ingest_one_session(
            index=i,
            spec=spec,
            organization_id=organization_id,
            country_code=country_code,
            batch_id=batch_id,
        )
        for i, spec in enumerate(items)
    ]
    # Each worker guards itself; gather still gets return_exceptions so an unexpected
    # crash in one worker can never sink the whole batch.
    raw = await asyncio.gather(*tasks, return_exceptions=True)
    results: list[dict[str, Any]] = []
    for i, r in enumerate(raw):
        if isinstance(r, Exception):
            log.error("batch_ingest.worker_crashed", index=i, error=str(r)[:300])
            results.append(
                {
                    "index": i,
                    "ok": False,
                    "stage": "worker",
                    "file_name": (items[i] or {}).get("file_name"),
                    "document_id": _s((items[i] or {}).get("document_id")),
                    "error": str(r)[:300],
                }
            )
        else:
            results.append(r)

    succeeded = sum(1 for r in results if r.get("ok"))
    return {
        "ok": True,
        "batch_id": batch_id,
        "requested": len(items),
        "processed": len(results),
        "succeeded": succeeded,
        "failed": len(results) - succeeded,
        "results": results,
    }


async def _ingest_one_session(
    *,
    index: int,
    spec: dict[str, Any],
    organization_id: Any | None,
    country_code: str,
    batch_id: str,
) -> dict[str, Any]:
    """One file → own session. Classify, extract, upsert draft. Never raises."""
    document_id = _s(spec.get("document_id"))
    file_name = spec.get("file_name")
    base = {
        "index": index,
        "document_id": document_id,
        "file_name": file_name,
    }
    async with AsyncSessionLocal() as session:
        try:
            source_text = spec.get("source_text")
            pdf_base64 = spec.get("pdf_base64")
            country = spec.get("country_code") or country_code or "UK"

            # Step 1 — classify onto a pack type (skipped when caller supplied one).
            cls = await classify_svc.classify_certificate_type(
                session,
                source_text=source_text,
                file_name=file_name,
                pdf_base64=pdf_base64,
                country_code=country,
                certificate_type_code=spec.get("certificate_type_code"),
                cert_scope=spec.get("cert_scope"),
            )
            if not cls.get("ok") or not cls.get("certificate_type_code"):
                return {
                    **base,
                    "ok": False,
                    "stage": "classify",
                    "error": cls.get("error") or "could not classify certificate type",
                }
            type_code = cls["certificate_type_code"]
            cert_scope = cls.get("cert_scope")

            # Step 2 — extract key fields against the pack schema (Claude / heuristic).
            extracted = await extract_svc.extract_certificate_fields(
                session,
                certificate_type_code=type_code,
                source_text=source_text,
                country_code=country,
                cert_scope=cert_scope,
                pdf_base64=pdf_base64,
                extract_method=spec.get("extract_method"),
            )
            if not extracted.get("ok"):
                return {
                    **base,
                    "ok": False,
                    "stage": "extract",
                    "certificate_type_code": type_code,
                    "cert_scope": cert_scope,
                    "error": extracted.get("error") or "extraction failed",
                }
            fields = dict(extracted.get("extracted") or {})

            # Step 3 — upsert a DRAFT (confirmed_by_pm=False). This fires the
            # vector-membership pending item (§3.2), the PM-confirm approval, and
            # auto-verify for public_api / data_dump channels (§8).
            raw_metadata = dict(spec.get("raw_metadata") or {})
            raw_metadata.update(
                {
                    "batch_id": batch_id,
                    "batch_index": index,
                    "ingest_source": "udr_chat_batch",
                    "classification": {
                        "method": cls.get("method"),
                        "confidence": cls.get("confidence"),
                    },
                }
            )
            if file_name and not raw_metadata.get("source_filename"):
                raw_metadata["source_filename"] = file_name

            data: dict[str, Any] = {
                **fields,
                "certificate_type_code": type_code,
                "cert_scope": cert_scope,
                "country_code": country,
                "organization_id": _s(organization_id) or _s(spec.get("organization_id")),
                "document_id": document_id,
                "site_id": _s(spec.get("site_id")),
                "vendor_id": _s(spec.get("vendor_id")),
                "asset_id": _s(spec.get("asset_id")),
                "field_confidence": extracted.get("field_confidence") or {},
                "raw_metadata": raw_metadata,
                "source_filename": file_name,
            }
            if spec.get("vendor_name"):
                data["vendor_name"] = spec["vendor_name"]
            if spec.get("certificate_number"):
                data["certificate_number"] = spec["certificate_number"]

            up = await cert_svc.upsert_certificate(session, data, confirmed_by_pm=False)
            if not up.get("ok"):
                return {
                    **base,
                    "ok": False,
                    "stage": "upsert",
                    "certificate_type_code": type_code,
                    "cert_scope": cert_scope,
                    "error": up.get("error") or "upsert failed",
                }

            cert = up.get("certificate") or {}
            return {
                **base,
                "ok": True,
                "stage": "done",
                "certificate_id": up.get("id"),
                "certificate_type_code": type_code,
                "certificate_type_name": cls.get("certificate_type_name"),
                "cert_scope": cert_scope,
                "classification": data["raw_metadata"]["classification"],
                "status": cert.get("status"),
                "requires_pm_confirmation": up.get("requires_pm_confirmation", True),
                "missing_required": extracted.get("missing_required") or [],
                "extract_method": extracted.get("extract_method"),
                "authenticity_warning": up.get("authenticity_warning"),
                "membership_queue_item_id": up.get("membership_queue_item_id"),
                "queue_items": up.get("queue_items") or [],
                "verification": up.get("verification"),
            }
        except Exception as exc:  # noqa: BLE001 — isolate this file's failure
            await session.rollback()
            log.error(
                "batch_ingest.file_failed",
                index=index,
                document_id=document_id,
                error=str(exc)[:300],
            )
            return {
                **base,
                "ok": False,
                "stage": "exception",
                "error": str(exc)[:300],
            }


def _s(value: Any) -> str | None:
    """Coerce UUID / str / None to a plain string (upsert re-parses UUIDs)."""
    if value is None:
        return None
    s = str(value).strip()
    return s or None
