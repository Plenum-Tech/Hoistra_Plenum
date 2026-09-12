"""Ask before you write: the mandatory-field gate every ingested PDF passes through.

Extraction is good and it is not certain. A scanned certificate with a smudged expiry date,
an invoice whose reference sits in a logo, a contract that never states its start — these
arrive as an absence, and an absence written into the register looks exactly like a fact
nobody recorded. The cost lands months later, when a certificate with no expiry never
appears in a renewal ladder and the first anyone knows is an inspection.

So the write is held. What extraction could not read becomes a question, the person answers
it, and the record is created complete — or not at all.

Two rules shape everything here.

**Ask only about what is actually missing.** A gate that re-asks for the eleven fields
extraction read correctly trains people to click through it, and a gate people click through
is worse than no gate: it adds friction and delivers no accuracy. Fields that came back with
low confidence are shown for confirmation rather than re-typed, because reading "expiry
2026-07-14, is that right?" is a second of work where an empty box is a minute of squinting
at a PDF.

**A field a person typed is not a field the document stated.** Answers are recorded with
their origin, so `expiry_date` supplied by a PM at ingest is distinguishable forever from
one read off the certificate. Both are usable; they are not the same evidence, and a
register that conflates them cannot later tell you which of its dates were actually seen.

For compliance certificates the mandatory list is **not hardcoded here** — every one of the
55 types in the UK pack declares its own ``key_fields_schema.required``, and that is the
authority. Invoices and contracts have no such declaration, so their specs live below and
are stated as what the record is meaningless without, not as everything one could want.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logging import get_logger

log = get_logger(__name__)

#: Confidence values that mean "read it, but check me". Anything at or below is surfaced
#: for confirmation rather than trusted silently.
UNSURE = {"low", "medium", "unknown", ""}

#: What each field is called on screen, and why it is being asked for. The reason is not
#: decoration: a person asked for an expiry date with no explanation types something to get
#: past the box, and a person told it drives the renewal ladder types the right thing.
FIELD_PROMPTS: dict[str, dict[str, str]] = {
    "certificate_number": {
        "label": "Certificate number",
        "why": "It is how this certificate is matched on re-upload, and how a duplicate is "
               "recognised instead of filed twice.",
    },
    "issue_date": {
        "label": "Issue date",
        "why": "Fixes the point the certificate speaks from. Without it a later "
               "certificate cannot be told from an earlier one.",
    },
    "expiry_date": {
        "label": "Expiry date",
        "why": "Drives the renewal ladder. A certificate with no expiry never becomes due, "
               "so it silently drops out of compliance reporting.",
    },
    "next_due_date": {
        "label": "Next due date",
        "why": "When the next inspection falls, where that is not simply the expiry.",
    },
    "issuer": {
        "label": "Issued by",
        "why": "The body that issued it — needed to check the accreditation behind it.",
    },
    "vendor_name": {
        "label": "Contractor",
        "why": "Who carried out the work. Vendor performance is scored on this.",
    },
    "inspector_name": {
        "label": "Inspector",
        "why": "The individual who signed it.",
    },
    "result": {
        "label": "Result",
        "why": "Pass, fail or satisfactory. A certificate recording a failure is an action, "
               "not a filing.",
    },
    "invoice_ref": {
        "label": "Invoice number",
        "why": "How this invoice is matched, and how a duplicate submission is caught.",
    },
    "contract_ref": {
        "label": "Contract reference",
        "why": "Ties the terms to the contract they came from, and to the invoices billed "
               "under it.",
    },
    "signed_date": {
        "label": "Signed date",
        "why": "When the terms took effect.",
    },
    "cert_scope": {
        "label": "What does this certificate cover?",
        "why": "A certificate covers a building or it accredits a contractor. The register "
               "files and scores the two differently, and the document rarely says which.",
    },
    "lines": {
        "label": "Invoice lines",
        "why": "The invoice total is the sum of its lines. With none, there is nothing to "
               "verify against the contract.",
    },
}

#: Invoices and contracts declare no required set anywhere, so it is stated here — and kept
#: to what the record is MEANINGLESS without, not everything one could wish for. Every extra
#: mandatory field is a person stopped at a form, and a gate that asks too much gets clicked
#: through.
DOC_REQUIRED: dict[str, list[str]] = {
    "vendor_invoice": ["invoice_ref", "lines"],
    "service_contract": ["contract_ref"],
}

#: Fields the write refuses without, which the document itself never states. The pack
#: declares what is ON a certificate; this is what the register needs to file one. Leaving
#: these out of the gate is how "ready to file" turns into a rejected write further down —
#: which is worse than asking, because by then the person has left the screen.
PLATFORM_REQUIRED: dict[str, list[str]] = {
    "compliance_certificate": ["cert_scope"],
}

#: Where a field has a fixed set of answers, the question carries them. A free-text box for
#: a value with exactly two valid answers invites a third.
FIELD_OPTIONS: dict[str, list[str]] = {
    "cert_scope": ["Building", "Vendor"],
    "result": ["Pass", "Fail", "Satisfactory", "Unsatisfactory"],
}

#: Fields worth confirming when read with low confidence, even though they do not block.
DOC_CONFIRM: dict[str, list[str]] = {
    "vendor_invoice": ["vendor_name", "contract_ref"],
    "service_contract": ["vendor_name", "signed_date"],
    "compliance_certificate": ["vendor_name", "issuer", "result"],
}

DOC_LABELS = {
    "compliance_certificate": "compliance certificate",
    "vendor_invoice": "vendor invoice",
    "service_contract": "service contract",
}


def _blank(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, (list, dict, tuple, set)):
        return len(v) == 0
    return str(v).strip() == "" or str(v).strip().lower() in {"none", "null", "n/a", "unknown"}


def _prompt(field: str) -> dict[str, str]:
    return FIELD_PROMPTS.get(
        field,
        {"label": field.replace("_", " ").capitalize(),
         "why": "Required for this document type."},
    )


def _kind(field: str) -> str:
    if field.endswith("_date") or field in {"issue_date", "expiry_date", "signed_date"}:
        return "date"
    if field == "lines":
        return "lines"
    if field in FIELD_OPTIONS:
        return "choice"
    return "text"


def _looks_like_a_date(v: Any) -> bool:
    if isinstance(v, (date, datetime)):
        return True
    s = str(v or "").strip()[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
        return True
    except ValueError:
        return False


async def required_fields(
    session: AsyncSession,
    doc_type: str,
    *,
    certificate_type_code: str | None = None,
    country_code: str | None = None,
) -> tuple[list[str], str | None]:
    """The mandatory fields for this document, and where that list came from.

    For a certificate the pack is the authority — it declares them per type, and 55 of 55
    UK types do. Hardcoding a list here would be a second answer to a question the pack
    already answers, and the two would drift.
    """
    if doc_type == "compliance_certificate":
        if not certificate_type_code:
            return [], None
        try:
            from .compliance.country_pack import get_pack_type

            pack = await get_pack_type(
                session, certificate_type_code, country_code=country_code
            )
        except Exception as exc:  # noqa: BLE001 — a gate must not break an ingest
            log.warning("ingest_gate.pack_read_failed", error=str(exc)[:200])
            return [], None
        if not pack:
            return [], None
        schema = pack.key_fields_schema or {}
        return (
            list(schema.get("required") or []) + PLATFORM_REQUIRED.get(doc_type, []),
            f"{certificate_type_code} in the country pack, plus what the register needs to file one",
        )
    return (
        DOC_REQUIRED.get(doc_type, []) + PLATFORM_REQUIRED.get(doc_type, []),
        "the document type's declared minimum",
    )


async def check_document(
    session: AsyncSession,
    doc_type: str,
    extracted: dict[str, Any] | None,
    *,
    certificate_type_code: str | None = None,
    country_code: str | None = None,
    field_confidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """What must be answered before this document can be written. Reads only.

    ``ready`` is the whole verdict: true means every mandatory field is present and the
    ingest may proceed. ``questions`` is what to put to the person, and nothing that is
    already known appears in it.
    """
    data = dict(extracted or {})
    conf = {k: str(v or "").lower() for k, v in (field_confidence
                                                 or data.get("field_confidence") or {}).items()}

    required, authority = await required_fields(
        session, doc_type,
        certificate_type_code=certificate_type_code, country_code=country_code,
    )

    questions: list[dict[str, Any]] = []
    for f in required:
        if not _blank(data.get(f)):
            continue
        p = _prompt(f)
        questions.append({
            "field": f, "label": p["label"], "why": p["why"],
            "kind": _kind(f), "blocking": True,
            "options": FIELD_OPTIONS.get(f),
            "found": None, "confidence": conf.get(f),
            "prompt": (p["label"] if f in FIELD_OPTIONS
                       else f"{p['label']} could not be read from the document."),
        })

    # Present, but the extractor was not sure. Shown as "is this right?" rather than an
    # empty box: confirming a value is a second's work, retyping it is a minute of
    # squinting at a PDF.
    for f in DOC_CONFIRM.get(doc_type, []) + [r for r in required if r not in DOC_CONFIRM.get(doc_type, [])]:
        v = data.get(f)
        if _blank(v) or conf.get(f) not in UNSURE or any(q["field"] == f for q in questions):
            continue
        p = _prompt(f)
        questions.append({
            "field": f, "label": p["label"], "why": p["why"],
            "kind": _kind(f), "blocking": False,
            "found": str(v), "confidence": conf.get(f),
            "prompt": f"{p['label']} read as “{v}”, but not confidently. Is that right?",
        })

    # A date that is not a date is worse than a date that is missing: it writes, and it is
    # wrong wherever it is later compared.
    for f in required:
        if _kind(f) == "date" and not _blank(data.get(f)) and not _looks_like_a_date(data.get(f)):
            p = _prompt(f)
            questions.append({
                "field": f, "label": p["label"], "why": p["why"], "kind": "date",
                "blocking": True, "found": str(data.get(f)), "confidence": conf.get(f),
                "prompt": f"{p['label']} was read as “{data.get(f)}”, which is not a "
                          "date. What should it be?",
            })

    blocking = [q for q in questions if q["blocking"]]
    return {
        "ok": True,
        "doc_type": doc_type,
        "document_label": DOC_LABELS.get(doc_type, doc_type),
        "required": required,
        "required_from": authority,
        "ready": not blocking,
        "blocking_count": len(blocking),
        "questions": questions,
        "message": (
            "Ready to file." if not blocking else
            f"{len(blocking)} field{'' if len(blocking) == 1 else 's'} could not be read. "
            "Nothing has been written."
        ),
    }


def apply_answers(
    extracted: dict[str, Any] | None,
    answers: dict[str, Any] | None,
    *,
    answered_by: str | None = None,
) -> dict[str, Any]:
    """Merge a person's answers into the extraction, recording that they came from a person.

    The origin travels with the value. A date a PM typed at ingest is usable, and it is not
    the same evidence as one read off the certificate — a register that cannot tell the two
    apart cannot later say which of its dates were actually seen on a document. Answers land
    in ``raw_metadata.answered_fields`` and the per-field confidence for anything supplied
    this way is set to ``stated`` rather than left looking like an extraction.
    """
    data = dict(extracted or {})
    given = {k: v for k, v in (answers or {}).items() if not _blank(v)}
    if not given:
        return data

    meta = dict(data.get("raw_metadata") or {})
    prior = dict(meta.get("answered_fields") or {})
    conf = dict(data.get("field_confidence") or {})

    for k, v in given.items():
        prior[k] = {
            "value": v,
            "by": answered_by or "user",
            "was": None if _blank(data.get(k)) else str(data.get(k)),
        }
        data[k] = v
        conf[k] = "stated"

    meta["answered_fields"] = prior
    meta["completed_at_ingest"] = True
    data["raw_metadata"] = meta
    data["field_confidence"] = conf
    return data
