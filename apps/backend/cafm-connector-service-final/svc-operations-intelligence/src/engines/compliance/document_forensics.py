"""Document forensics / authenticity checks for uploaded certificate PDFs.

Pure signal scoring (no live register scrape). DeepAgents extracts PDF metadata
locally and posts signals here; results gate trust before/alongside extract.
PM retains override authority (soft block) — high risk drafts still persist
with a strong authenticity_warning.

CCC §7 — merges per-format checklist from forgery_prompts/*.yaml when present.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

_PROMPTS_DIR = Path(__file__).resolve().parent / "forgery_prompts"

# Suspicious PDF producers / editors commonly used for tampering / re-exports
_SUSPICIOUS_PRODUCERS = re.compile(
    r"(ilovepdf|smallpdf|sejda|pdf24|nitro\s*pro|foxit\s*phantom|"
    r"pdf\s*escape|hipdf|sodapdf|pdfcandy|easeus|wonder|sharex|"
    r"microsoft\s*print\s*to\s*pdf|chrome\s*pdf|"
    r"acrobat\s*distiller|ghostscript)",
    re.I,
)

_SAMPLE_WATERMARK = re.compile(
    r"\b(sample|specimen|draft\s+only|not\s+valid|invalid\s+copy|"
    r"for\s+demonstration|preview\s+only|watermark|do\s+not\s+use|"
    r"unofficial\s+copy|training\s+copy)\b",
    re.I,
)

_CERT_SIGNAL = re.compile(
    r"\b(certificate|accreditation|licence|license|inspection|"
    r"eicr|gas\s+safe|niceic|bpca|bafe|expiry|valid\s+until|"
    r"certificate\s*(?:no|number)|member\s*(?:no|number))\b",
    re.I,
)

_DATE_TOKEN = re.compile(
    r"\b(\d{1,2}[\/\-.]\d{1,2}[\/\-.]\d{2,4}|\d{4}-\d{2}-\d{2})\b"
)


def _parse_pdf_date(raw: Any) -> datetime | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    # PDF date: D:YYYYMMDDHHmmSS
    m = re.match(r"D:(\d{4})(\d{2})(\d{2})(\d{2})?(\d{2})?(\d{2})?", s)
    if m:
        try:
            return datetime(
                int(m.group(1)),
                int(m.group(2) or 1),
                int(m.group(3) or 1),
                int(m.group(4) or 0),
                int(m.group(5) or 0),
                int(m.group(6) or 0),
                tzinfo=timezone.utc,
            )
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _add(
    findings: list[dict[str, Any]],
    *,
    code: str,
    severity: str,
    weight: int,
    message: str,
) -> int:
    findings.append(
        {
            "code": code,
            "severity": severity,
            "weight": weight,
            "message": message,
        }
    )
    return weight


def run_document_forensics(
    *,
    source_text: str | None = None,
    file_name: str | None = None,
    certificate_type_code: str | None = None,
    pdf_metadata: dict[str, Any] | None = None,
    page_count: int | None = None,
    text_char_count: int | None = None,
    is_encrypted: bool | None = None,
    has_text_layer: bool | None = None,
    file_size_bytes: int | None = None,
    digital_signature: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Score authenticity risk from PDF + text signals.

    Returns:
      ok, verdict (pass|review|fail), risk_score (0-100), findings[],
      authenticity_warning, allow_extract, requires_pm_review
    """
    text = (source_text or "").strip()
    meta = dict(pdf_metadata or {})
    findings: list[dict[str, Any]] = []
    score = 0
    signature_boost = 0  # applied to the FINAL score for a valid, tamper-free signature

    chars = text_char_count if text_char_count is not None else len(text)
    pages = page_count if page_count is not None else meta.get("page_count")
    encrypted = (
        is_encrypted
        if is_encrypted is not None
        else bool(meta.get("encrypted") or meta.get("is_encrypted"))
    )
    text_layer = (
        has_text_layer
        if has_text_layer is not None
        else meta.get("has_text_layer")
    )
    if text_layer is None:
        text_layer = chars > 40

    # ── Structural / PDF integrity ────────────────────────────────────
    if encrypted:
        score += _add(
            findings,
            code="pdf_encrypted",
            severity="critical",
            weight=40,
            message="PDF is encrypted/password-protected — authenticity cannot be fully assessed.",
        )

    producer = str(meta.get("producer") or meta.get("/Producer") or "")
    creator = str(meta.get("creator") or meta.get("/Creator") or "")
    combo = f"{producer} {creator}".strip()
    if combo and _SUSPICIOUS_PRODUCERS.search(combo):
        score += _add(
            findings,
            code="suspicious_producer",
            severity="high",
            weight=35,
            message=f"PDF producer/creator looks like a re-export tool ({combo[:120]}).",
        )
    elif not combo and (file_name or "").lower().endswith(".pdf"):
        score += _add(
            findings,
            code="missing_producer",
            severity="medium",
            weight=10,
            message="PDF metadata missing producer/creator — common on stripped or rebuilt files.",
        )

    created = _parse_pdf_date(meta.get("creation_date") or meta.get("/CreationDate"))
    modified = _parse_pdf_date(meta.get("mod_date") or meta.get("/ModDate"))
    now = datetime.now(timezone.utc)
    if created and created > now:
        score += _add(
            findings,
            code="future_creation_date",
            severity="high",
            weight=30,
            message="PDF creation date is in the future.",
        )
    if created and modified and modified < created:
        score += _add(
            findings,
            code="mod_before_create",
            severity="high",
            weight=25,
            message="PDF modification date is earlier than creation date.",
        )
    if created and modified:
        delta_h = abs((modified - created).total_seconds()) / 3600.0
        # Large gap can indicate later edits; short gap after "official" issue is normal
        if delta_h > 24 * 30:  # > 30 days
            score += _add(
                findings,
                code="late_modification",
                severity="medium",
                weight=15,
                message=(
                    f"PDF was modified ~{int(delta_h / 24)} days after creation — "
                    "review for post-issue edits."
                ),
            )

    if meta.get("incremental_updates") or meta.get("has_incremental_updates"):
        score += _add(
            findings,
            code="incremental_updates",
            severity="medium",
            weight=15,
            message="PDF shows incremental update history (possible post-issue edits).",
        )

    # ── Cryptographic digital signature (§7) ──────────────────────────
    # Only meaningful when the PDF is actually PKI-signed. Absence is NEUTRAL — most UK
    # compliance certs use an image signature, so we never penalise "not signed".
    sig = digital_signature if isinstance(digital_signature, dict) else None
    if sig and sig.get("signed"):
        signer = sig.get("signer")
        by = f" by {signer}" if signer else ""
        if sig.get("intact") is False or sig.get("valid") is False:
            score += _add(
                findings,
                code="signature_invalid",
                severity="critical",
                weight=65,
                message=(
                    f"Digital signature is INVALID{by} — the document was modified "
                    "after it was signed (tamper-evident)."
                ),
            )
        elif sig.get("coverage") == "partial":
            score += _add(
                findings,
                code="signature_partial_coverage",
                severity="high",
                weight=25,
                message=(
                    "Digital signature covers only part of the document — content was "
                    "added or changed after signing."
                ),
            )
        elif sig.get("intact") and sig.get("valid"):
            # Valid, tamper-free signature → strong authenticity boost.
            trust = "trusted issuer" if sig.get("trusted") else "signer not chained to a trusted root"
            _add(
                findings,
                code="signature_valid",
                severity="info",
                weight=0,
                message=f"Valid, tamper-free digital signature{by} ({trust}).",
            )
            # A valid cryptographic signature is strong authenticity evidence — reduce the
            # FINAL score so it can offset minor content-noise findings.
            signature_boost = 25
        elif sig.get("method") == "presence_only":
            _add(
                findings,
                code="signature_present_unverified",
                severity="info",
                weight=0,
                message=(
                    "Document is digitally signed, but the signature-validation library "
                    "is unavailable to verify it — recommend manual check."
                ),
            )

    if pages is not None:
        try:
            pages_i = int(pages)
        except (TypeError, ValueError):
            pages_i = None
        if pages_i is not None and pages_i <= 0:
            score += _add(
                findings,
                code="empty_pdf",
                severity="critical",
                weight=50,
                message="PDF reports zero pages.",
            )
        elif pages_i is not None and pages_i > 0 and chars < 20:
            score += _add(
                findings,
                code="image_only_or_empty_text",
                severity="high",
                weight=20,
                message=(
                    "Little or no extractable text layer — scanned/image PDF; "
                    "OCR authenticity checks are limited."
                ),
            )
        elif (
            pages_i is not None
            and pages_i >= 2
            and chars > 0
            and (chars / max(pages_i, 1)) < 30
        ):
            score += _add(
                findings,
                code="sparse_text_layer",
                severity="medium",
                weight=12,
                message="Very sparse text relative to page count — possible image-heavy or stripped PDF.",
            )

    if text_layer is False and not encrypted:
        score += _add(
            findings,
            code="no_text_layer",
            severity="medium",
            weight=10,
            message="No usable text layer detected.",
        )

    if file_size_bytes is not None and file_size_bytes < 800:
        score += _add(
            findings,
            code="tiny_file",
            severity="high",
            weight=25,
            message="File is unusually small for a certificate PDF.",
        )

    # ── Content authenticity ──────────────────────────────────────────
    if text:
        if _SAMPLE_WATERMARK.search(text):
            score += _add(
                findings,
                code="sample_watermark",
                severity="critical",
                weight=70,
                message="Document text contains SAMPLE/DRAFT/NOT VALID style wording.",
            )
        if not _CERT_SIGNAL.search(text) and certificate_type_code:
            score += _add(
                findings,
                code="weak_certificate_language",
                severity="medium",
                weight=15,
                message=(
                    f"Little certificate language found for declared type "
                    f"{certificate_type_code}."
                ),
            )
        dates = _DATE_TOKEN.findall(text)
        if len(dates) == 0 and certificate_type_code:
            score += _add(
                findings,
                code="no_dates_in_text",
                severity="medium",
                weight=12,
                message="No dates found in extracted text — unusual for a live certificate.",
            )
    elif (file_name or "").lower().endswith(".pdf"):
        score += _add(
            findings,
            code="no_source_text",
            severity="high",
            weight=20,
            message="No text could be extracted from the upload for authenticity checks.",
        )

    name = (file_name or "").lower()
    if name and not name.endswith((".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff")):
        score += _add(
            findings,
            code="unexpected_extension",
            severity="medium",
            weight=10,
            message=f"Unexpected file extension for a certificate upload ({file_name}).",
        )

    # CCC §7 — per-format checklist overlay
    prompt = _load_forgery_prompt(certificate_type_code)
    format_score = _apply_format_checklist(text or "", prompt, findings)
    score += format_score

    score = max(0, min(100, int(score) - signature_boost))
    # Internal ladder (legacy UI) + CCC §7 vocabulary (genuine / suspect / edited)
    if score >= 70:
        verdict = "fail"
        ccc_verdict = "edited"
    elif score >= 30:
        verdict = "review"
        ccc_verdict = "suspect"
    else:
        verdict = "pass"
        ccc_verdict = "genuine"

    # Soft gate: still allow extract unless encrypted empty / zero pages
    hard_codes = {f["code"] for f in findings}
    allow_extract = "empty_pdf" not in hard_codes
    if encrypted and chars < 20:
        allow_extract = False

    warning = compose_forensics_warning(
        {"verdict": verdict, "ccc_verdict": ccc_verdict, "risk_score": score, "findings": findings}
    )

    return {
        "ok": True,
        "verdict": verdict,
        "ccc_verdict": ccc_verdict,
        "risk_score": score,
        "findings": findings,
        "authenticity_warning": warning,
        "allow_extract": allow_extract,
        "requires_pm_review": verdict != "pass",
        "certificate_type_code": certificate_type_code,
        "file_name": file_name,
        "format_prompt": prompt.get("_code") or "_default",
        "signals": {
            "page_count": pages,
            "text_char_count": chars,
            "is_encrypted": encrypted,
            "has_text_layer": text_layer,
            "producer": producer or None,
            "creator": creator or None,
            "creation_date": meta.get("creation_date") or meta.get("/CreationDate"),
            "mod_date": meta.get("mod_date") or meta.get("/ModDate"),
            "file_size_bytes": file_size_bytes,
            "digital_signature": sig,
        },
        "mode": "document_forensics",
        "message": (
            "Document forensics complete. Soft authenticity gate — "
            "PM retains override authority."
            if verdict != "fail"
            else (
                "Document forensics flagged high authenticity risk. "
                "Draft may still be saved for PM review (soft gate)."
            )
        ),
    }


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """Minimal YAML subset loader (lists + scalars) — no PyYAML required."""
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    out: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if re.match(r"^[A-Za-z0-9_]+:\s*$", line):
            if current_key and current_list is not None:
                out[current_key] = current_list
            current_key = line.split(":", 1)[0].strip()
            current_list = []
            continue
        m = re.match(r"^([A-Za-z0-9_]+):\s+(.+)$", line)
        if m:
            if current_key and current_list is not None:
                out[current_key] = current_list
            current_key, current_list = None, None
            key, val = m.group(1), m.group(2).strip().strip('"').strip("'")
            out[key] = val
            continue
        m = re.match(r"^\s*-\s+(.+)$", line)
        if m and current_key is not None:
            if current_list is None:
                current_list = []
            current_list.append(m.group(1).strip().strip('"').strip("'"))
    if current_key and current_list is not None:
        out[current_key] = current_list
    return out


@lru_cache(maxsize=64)
def _load_forgery_prompt(certificate_type_code: str | None) -> dict[str, Any]:
    from .verification_sources import canonicalize_type_code

    code = canonicalize_type_code(certificate_type_code) or (certificate_type_code or "").upper()
    path = _PROMPTS_DIR / f"{code}.yaml"
    if not path.exists():
        path = _PROMPTS_DIR / "_default.yaml"
    try:
        data = _parse_simple_yaml(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["_code"] = path.stem
    return data


def _apply_format_checklist(
    text: str,
    prompt: dict[str, Any],
    findings: list[dict[str, Any]],
) -> int:
    """Return additional risk score from per-format token/field checks."""
    if not text or not prompt:
        return 0
    lower = text.lower()
    score = 0
    required = [str(t).lower() for t in (prompt.get("required_tokens") or []) if t]
    if required:
        hits = sum(1 for t in required if t in lower)
        if hits == 0:
            score += _add(
                findings,
                code="format_tokens_missing",
                severity="high",
                weight=18,
                message=(
                    f"Format checklist ({prompt.get('_code')}): none of the expected "
                    f"tokens matched ({', '.join(required[:4])})."
                ),
            )
        elif hits < max(1, len(required) // 2):
            score += _add(
                findings,
                code="format_tokens_weak",
                severity="medium",
                weight=10,
                message=f"Format checklist ({prompt.get('_code')}): weak token match ({hits}/{len(required)}).",
            )
    body_tokens = [str(t).lower() for t in (prompt.get("issuing_body_tokens") or []) if t]
    if body_tokens and not any(t in lower for t in body_tokens):
        score += _add(
            findings,
            code="issuing_body_missing",
            severity="medium",
            weight=12,
            message=(
                f"Format checklist ({prompt.get('_code')}): expected issuing-body "
                f"language not found ({', '.join(body_tokens[:3])})."
            ),
        )
    field_hints = [str(t).lower() for t in (prompt.get("required_field_hints") or []) if t]
    if field_hints:
        fhits = sum(1 for t in field_hints if t in lower)
        if fhits == 0:
            score += _add(
                findings,
                code="format_fields_missing",
                severity="medium",
                weight=10,
                message=f"Format checklist ({prompt.get('_code')}): no expected field hints in text.",
            )
    return score


def compose_forensics_warning(forensics: dict[str, Any] | None) -> str | None:
    """Human-readable warning for authenticity_warning / UI."""
    if not forensics:
        return None
    verdict = (forensics.get("verdict") or "").lower()
    ccc_verdict = (forensics.get("ccc_verdict") or "").lower()
    score = forensics.get("risk_score")
    findings = forensics.get("findings") or []
    if verdict in {"", "pass", "genuine"} and not findings:
        return None
    if verdict in {"pass", "genuine"} and (score is None or int(score) < 30):
        return None

    top = [
        f.get("message")
        for f in findings
        if f.get("severity") in {"critical", "high", "medium"}
    ][:3]
    detail = "; ".join(str(t) for t in top if t)
    label = {
        "fail": "HIGH RISK (edited)",
        "edited": "HIGH RISK (edited)",
        "review": "REVIEW REQUIRED (suspect)",
        "suspect": "REVIEW REQUIRED (suspect)",
        "pass": "LOW RISK (genuine)",
        "genuine": "LOW RISK (genuine)",
    }.get(ccc_verdict or verdict, (ccc_verdict or verdict).upper() or "UNKNOWN")
    base = (
        f"Document forensics: {label}"
        + (f" (score {score}/100)" if score is not None else "")
    )
    if detail:
        base = f"{base} — {detail}"
    return (
        f"{base} PM retains override authority (soft authenticity gate)."
    )


def merge_authenticity_warnings(
    soft_register_warning: str | None,
    forensics: dict[str, Any] | None,
    vendor_registration: dict[str, Any] | None = None,
) -> str | None:
    """Combine forensics + vendor registration search + A5 soft register warning."""
    parts: list[str] = []
    forensic_w = None
    if forensics:
        forensic_w = forensics.get("authenticity_warning") or compose_forensics_warning(
            forensics
        )
    vendor_w = None
    if vendor_registration:
        vendor_w = vendor_registration.get("authenticity_warning") or vendor_registration.get(
            "status_message"
        )
    for part in (forensic_w, vendor_w, soft_register_warning):
        if part and str(part).strip() and str(part).strip() not in parts:
            parts.append(str(part).strip())
    if not parts:
        return None
    return " | ".join(parts)
