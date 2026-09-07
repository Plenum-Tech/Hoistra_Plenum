"""
Document forensics / authenticity-check agent.

Extracts PDF structural signals locally (pypdf), then scores authenticity via
svc-operations-intelligence POST /api/compliance/forensics.

Used as a soft gate on Feature A certificate uploads before extract/upsert.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import structlog
from langchain_core.tools import tool

from ..config import settings
from ..http_client import request as _request

log = structlog.get_logger(__name__)

_TIMEOUT = 60.0
_SERVICE = "operations_intelligence"


def _base() -> str:
    return settings.operations_intelligence_base_url.rstrip("/")


def _err(exc: Exception, op: str) -> dict:
    if isinstance(exc, httpx.HTTPStatusError):
        body = exc.response.text or ""
        log.error(
            "document_forensics.http_error",
            operation=op,
            status_code=exc.response.status_code,
            body=body[:2000],
        )
        return {"ok": False, "error": body[:2000], "status_code": exc.response.status_code}
    log.error("document_forensics.error", operation=op, error=str(exc), exc_info=True)
    return {"ok": False, "error": str(exc)[:2000]}


def _check_pdf_signature(path: Path) -> dict[str, Any] | None:
    """CCC §7 — validate any embedded PDF digital signature (cryptographic tamper-evidence).

    Returns ``None`` when the PDF carries no signature (neutral — most UK certs use an
    image signature, not PKI). When signed, returns integrity + coverage + signer via
    pyhanko; falls back to presence-only if pyhanko isn't installed. Never raises.

    Fields: ``signed``, ``intact`` (bytes unchanged since signing), ``valid`` (signature
    cryptographically well-formed), ``coverage`` (``full``/``partial``), ``signer``,
    ``trusted`` (chains to a trust root — informational), ``method``.
    """
    try:
        raw = path.read_bytes()
    except Exception:  # noqa: BLE001
        return None
    if b"/ByteRange" not in raw:
        return None  # not digitally signed
    result: dict[str, Any] = {
        "signed": True,
        "signature_count": 0,
        "intact": None,
        "valid": None,
        "coverage": None,
        "signer": None,
        "trusted": None,
        "method": "presence_only",
    }
    try:
        import io

        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import validate_pdf_signature
        from pyhanko.sign.validation.status import SignatureCoverageLevel
        from pyhanko_certvalidator import ValidationContext

        reader = PdfFileReader(io.BytesIO(raw), strict=False)
        sigs = list(reader.embedded_signatures)
        result["signature_count"] = len(sigs)
        if not sigs:
            return result  # /ByteRange present but no parseable signature
        vc = ValidationContext(allow_fetching=False)  # no network / no trust store
        all_intact = True
        all_valid = True
        coverage_full = True
        trusted_any = False
        signer = None
        for sig in sigs:
            try:
                st = validate_pdf_signature(sig, vc)
            except Exception as sexc:  # noqa: BLE001 — one bad sig must not crash
                result.setdefault("errors", []).append(str(sexc)[:150])
                continue
            if getattr(st, "intact", None) is False:
                all_intact = False
            if getattr(st, "valid", None) is False:
                all_valid = False
            cov = getattr(st, "coverage", None)
            if cov is not None and cov != SignatureCoverageLevel.ENTIRE_FILE:
                coverage_full = False
            if getattr(st, "trusted", False):
                trusted_any = True
            cert = getattr(st, "signing_cert", None)
            if signer is None and cert is not None:
                try:
                    signer = cert.subject.human_friendly
                except Exception:  # noqa: BLE001
                    signer = str(getattr(cert, "subject", ""))[:120]
        result.update(
            {
                "intact": all_intact,
                "valid": all_valid,
                "coverage": "full" if coverage_full else "partial",
                "signer": signer,
                "trusted": trusted_any,
                "method": "pyhanko",
            }
        )
    except ImportError:
        pass  # pyhanko not installed → presence_only
    except Exception as exc:  # noqa: BLE001
        result["error"] = str(exc)[:200]
    return result


def extract_pdf_signals(file_path: str) -> dict[str, Any]:
    """Read local PDF metadata / structure for forensics scoring."""
    path = Path(file_path)
    signals: dict[str, Any] = {
        "file_name": path.name,
        "file_size_bytes": path.stat().st_size if path.exists() else None,
        "pdf_metadata": {},
        "page_count": None,
        "is_encrypted": None,
        "has_text_layer": None,
        "text_char_count": None,
        "incremental_updates": False,
        "digital_signature": None,
    }
    if not path.exists():
        return signals

    suffix = path.suffix.lower()
    if suffix != ".pdf":
        return signals

    try:
        raw = path.read_bytes()
        # Heuristic: incremental update xref chain
        if b"/Prev" in raw[: min(len(raw), 2_000_000)]:
            signals["incremental_updates"] = True
            signals["pdf_metadata"]["has_incremental_updates"] = True
    except Exception as exc:  # noqa: BLE001
        log.warning("document_forensics.read_bytes_failed", error=str(exc)[:160])

    # CCC §7 — cryptographic digital-signature validation (tamper-evidence)
    try:
        signals["digital_signature"] = _check_pdf_signature(path)
    except Exception as exc:  # noqa: BLE001 — never block the forensics path
        log.warning("document_forensics.signature_check_failed", error=str(exc)[:160])

    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(path))
        signals["is_encrypted"] = bool(reader.is_encrypted)
        if reader.is_encrypted:
            try:
                reader.decrypt("")  # empty password attempt
            except Exception:  # noqa: BLE001
                pass
        try:
            signals["page_count"] = len(reader.pages)
        except Exception:  # noqa: BLE001
            signals["page_count"] = None

        meta_raw = getattr(reader, "metadata", None) or {}
        meta: dict[str, Any] = {}
        for key in (
            "/Producer",
            "/Creator",
            "/CreationDate",
            "/ModDate",
            "/Title",
            "/Author",
        ):
            try:
                val = meta_raw.get(key) if hasattr(meta_raw, "get") else getattr(meta_raw, key, None)
            except Exception:  # noqa: BLE001
                val = None
            if val is not None:
                meta[key] = str(val)
        # Friendly aliases for ops scorer
        if meta.get("/Producer"):
            meta["producer"] = meta["/Producer"]
        if meta.get("/Creator"):
            meta["creator"] = meta["/Creator"]
        if meta.get("/CreationDate"):
            meta["creation_date"] = meta["/CreationDate"]
        if meta.get("/ModDate"):
            meta["mod_date"] = meta["/ModDate"]
        if signals.get("incremental_updates"):
            meta["has_incremental_updates"] = True
        signals["pdf_metadata"] = meta

        # Sample text layer from first pages
        chunks: list[str] = []
        try:
            for page in reader.pages[:3]:
                try:
                    chunks.append(page.extract_text() or "")
                except Exception:  # noqa: BLE001
                    continue
        except Exception:  # noqa: BLE001
            pass
        sample = "\n".join(chunks).strip()
        signals["text_char_count"] = len(sample)
        signals["has_text_layer"] = len(sample) > 40
        signals["text_sample"] = sample[:4000]
    except Exception as exc:  # noqa: BLE001
        log.warning("document_forensics.pypdf_failed", error=str(exc)[:200])
        signals["error"] = str(exc)[:200]

    return signals


async def analyze_certificate_document(
    *,
    file_path: str,
    source_text: str | None = None,
    certificate_type_code: str | None = None,
) -> dict[str, Any]:
    """
    Run document forensics for a certificate upload.
    Prefer caller-provided source_text (already peeked); fall back to PDF sample.
    """
    signals = extract_pdf_signals(file_path)
    text = (source_text or "").strip() or (signals.get("text_sample") or "")
    payload = {
        "source_text": text[:20_000],
        "file_name": signals.get("file_name") or Path(file_path).name,
        "certificate_type_code": certificate_type_code,
        "pdf_metadata": signals.get("pdf_metadata") or {},
        "page_count": signals.get("page_count"),
        "text_char_count": signals.get("text_char_count")
        if not text
        else len(text),
        "is_encrypted": signals.get("is_encrypted"),
        "has_text_layer": signals.get("has_text_layer")
        if signals.get("has_text_layer") is not None
        else (len(text) > 40),
        "file_size_bytes": signals.get("file_size_bytes"),
        "digital_signature": signals.get("digital_signature"),
    }
    try:
        resp = await _request(
            "POST",
            _base(),
            "/api/compliance/forensics",
            service=_SERVICE,
            timeout=_TIMEOUT,
            json=payload,
        )
        body = resp.json()
        body["local_signals"] = {
            k: signals.get(k)
            for k in (
                "page_count",
                "is_encrypted",
                "has_text_layer",
                "file_size_bytes",
                "incremental_updates",
            )
        }
        return body
    except Exception as exc:  # noqa: BLE001
        # Soft fallback — never hard-crash the upload path
        fallback = {
            "ok": False,
            "verdict": "review",
            "risk_score": 35,
            "findings": [
                {
                    "code": "forensics_service_unavailable",
                    "severity": "medium",
                    "weight": 35,
                    "message": f"Forensics service unavailable: {str(exc)[:160]}",
                }
            ],
            "authenticity_warning": (
                "Document forensics could not be completed — please review "
                "certificate authenticity manually. PM retains override authority "
                "(soft authenticity gate)."
            ),
            "allow_extract": True,
            "requires_pm_review": True,
            "error": str(exc)[:200],
        }
        log.warning("document_forensics.fallback", error=str(exc)[:200])
        return fallback


def forensics_summary_line(forensics: dict[str, Any] | None) -> str | None:
    if not forensics:
        return None
    verdict = (forensics.get("verdict") or "unknown").upper()
    score = forensics.get("risk_score")
    score_s = f"{score}/100" if score is not None else "n/a"
    return f"**Document forensics:** {verdict} (risk {score_s})"


@tool
async def run_document_forensics(
    file_path: str,
    source_text: str | None = None,
    certificate_type_code: str | None = None,
) -> dict:
    """Run document forensics / authenticity checks on an uploaded certificate PDF.

    Scores PDF metadata anomalies, encryption, sample/draft watermarks, sparse
    text layers, and weak certificate language. Soft gate — PM retains override.
    Call before extracting/upserting compliance certificate fields.
    """
    try:
        return await analyze_certificate_document(
            file_path=file_path,
            source_text=source_text,
            certificate_type_code=certificate_type_code,
        )
    except Exception as exc:
        return _err(exc, "run_document_forensics")


DOCUMENT_FORENSICS_TOOLS = [
    run_document_forensics,
]
