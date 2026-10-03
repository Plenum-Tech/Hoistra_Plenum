"""
Full-document PDF extraction via Claude (native PDF) or OpenAI vision.

Used when pypdf/pdfplumber return little/no text (scanned certificates).
"""
from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any

import structlog

from ..config import settings
from . import activity_log

log = structlog.get_logger(__name__)

_MIN_TEXT_CHARS = 80
_MAX_PDF_BYTES = 20 * 1024 * 1024  # 20 MB
_MAX_VISION_PAGES = 6


async def extract_document_text(
    file_path: str,
    *,
    max_chars: int = 50_000,
    force_llm: bool = False,
) -> dict[str, Any]:
    """
    Return {text, method, page_count_hint}.

    method: pypdf | pdfplumber | claude_pdf | openai_vision | filename_stub
    """
    from .contract_performance_single_door import extract_text_from_upload

    path = Path(file_path)
    layered = extract_text_from_upload(file_path, max_chars=max_chars)
    # Strip the filename stub so we don't treat it as real content
    is_stub = layered.startswith("Document filename:") and "(User uploaded" in layered
    usable = (layered or "").strip()
    if is_stub:
        usable = ""

    if usable and len(usable) >= _MIN_TEXT_CHARS and not force_llm:
        return {
            "text": usable[:max_chars],
            "method": "text_layer",
            "chars": len(usable),
        }

    if path.suffix.lower() != ".pdf" or not path.exists():
        return {
            "text": (layered or "")[:max_chars],
            "method": "filename_stub" if is_stub else "text_layer",
            "chars": len(layered or ""),
        }

    # Prefer Claude native PDF document API (full pages, including scans)
    claude_text = await _extract_via_claude_pdf(path, max_chars=max_chars)
    if claude_text and len(claude_text.strip()) >= 40:
        return {
            "text": claude_text[:max_chars],
            "method": "claude_pdf",
            "chars": len(claude_text),
        }

    # OpenAI vision on rendered page images
    oai_text = await _extract_via_openai_vision(path, max_chars=max_chars)
    if oai_text and len(oai_text.strip()) >= 40:
        return {
            "text": oai_text[:max_chars],
            "method": "openai_vision",
            "chars": len(oai_text),
        }

    # Keep any thin layer text we had
    fallback = usable or layered or f"Document filename: {path.name}"
    return {
        "text": fallback[:max_chars],
        "method": "filename_stub" if not usable else "text_layer_thin",
        "chars": len(fallback),
        "warning": "LLM PDF extraction unavailable or returned empty — using thin text layer.",
    }


async def _extract_via_claude_pdf(path: Path, *, max_chars: int) -> str | None:
    key = (settings.anthropic_api_key or "").strip()
    if not key:
        return None
    try:
        raw = path.read_bytes()
        if not raw or len(raw) > _MAX_PDF_BYTES:
            log.warning(
                "pdf_vision.claude_skip_size",
                size=len(raw) if raw else 0,
                limit=_MAX_PDF_BYTES,
            )
            return None
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=key)
        b64 = base64.standard_b64encode(raw).decode("ascii")
        prompt = (
            "You are reading a UK compliance / accreditation certificate PDF "
            "(may be a scanned image). Extract ALL readable text faithfully, "
            "preserving labels and numbers. Especially capture: company/organisation "
            "name, BAFE/NICEIC/Gas Safe/NAPIT/SIA/BPCA registration or membership "
            "numbers, organisation IDs, certificate numbers, issue date, expiry date, "
            "scheme codes (e.g. SP101, SP203-1), inspector names, addresses, and "
            "Pass/Fail/result wording.\n"
            "Return plain text only (no markdown fences). Use lines like "
            "'Label: value' when labels are visible."
        )
        _t0 = time.perf_counter()
        resp = await client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=4096,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "document",
                            "source": {
                                "type": "base64",
                                "media_type": "application/pdf",
                                "data": b64,
                            },
                        },
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
        )
        parts = []
        for block in resp.content or []:
            t = getattr(block, "text", None)
            if t:
                parts.append(t)
        text = "\n".join(parts).strip()
        log.info("pdf_vision.claude_pdf_ok", chars=len(text), file=path.name)
        activity_log.fire_exchange(
            agent="compliance_intake", stage="extract_document", system=None,
            user={"prompt": prompt, "file": path.name, "pdf_bytes": len(raw)},
            output={"text": text[:max_chars], "chars": len(text)},
            model="claude-sonnet-4-20250514", usage=resp.usage,
            latency_ms=(time.perf_counter() - _t0) * 1000,
            summary_in=f"extract {path.name}", summary_out=f"{len(text)} chars extracted",
        )
        return text[:max_chars] if text else None
    except Exception as exc:  # noqa: BLE001
        log.warning("pdf_vision.claude_pdf_failed", error=str(exc)[:300], file=path.name)
        activity_log.fire_exchange(
            agent="compliance_intake", stage="extract_document", system=None,
            user={"file": path.name}, error=str(exc), model="claude-sonnet-4-20250514",
            summary_in=f"extract {path.name}",
        )
        return None


def _render_pdf_png_b64(path: Path, *, max_pages: int = _MAX_VISION_PAGES) -> list[str]:
    """Render PDF pages to PNG base64 strings (pymupdf if available)."""
    try:
        import fitz  # pymupdf
    except ImportError:
        log.warning("pdf_vision.pymupdf_missing")
        return []
    out: list[str] = []
    try:
        doc = fitz.open(str(path))
        for i in range(min(len(doc), max_pages)):
            page = doc[i]
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0), alpha=False)
            out.append(base64.standard_b64encode(pix.tobytes("png")).decode("ascii"))
        doc.close()
    except Exception as exc:  # noqa: BLE001
        log.warning("pdf_vision.render_failed", error=str(exc)[:200])
        return []
    return out


async def _extract_via_openai_vision(path: Path, *, max_chars: int) -> str | None:
    key = (settings.openai_api_key or settings.azure_openai_api_key or "").strip()
    if not key:
        return None
    images = _render_pdf_png_b64(path)
    if not images:
        return None
    try:
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": key}
        base = (settings.openai_api_base or "").strip()
        if base:
            kwargs["base_url"] = base
        # Azure path: use azure endpoint if configured
        if (settings.azure_openai_endpoint or "").strip():
            from openai import AsyncAzureOpenAI

            client = AsyncAzureOpenAI(
                api_key=(settings.azure_openai_api_key or key).strip(),
                api_version=settings.azure_openai_api_version,
                azure_endpoint=settings.azure_openai_endpoint.strip(),
            )
            model = (
                settings.azure_openai_deployment or settings.openai_model or "gpt-4o"
            ).strip()
        else:
            client = AsyncOpenAI(**kwargs)
            # Prefer a vision-capable model
            model = settings.openai_model or "gpt-4o"
            if "mini" in model and "4o" not in model:
                model = "gpt-4o"

        content: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "Extract ALL text from these UK compliance certificate PDF page "
                    "images. Preserve labels and numbers (registration, membership, "
                    "organisation ID, certificate no., dates, company name, scheme). "
                    "Plain text only, 'Label: value' lines where possible."
                ),
            }
        ]
        for b64 in images:
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                }
            )
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=4096,
            messages=[{"role": "user", "content": content}],
        )
        text = (resp.choices[0].message.content or "").strip()
        log.info(
            "pdf_vision.openai_ok",
            chars=len(text),
            pages=len(images),
            file=path.name,
            model=model,
        )
        return text[:max_chars] if text else None
    except Exception as exc:  # noqa: BLE001
        log.warning("pdf_vision.openai_failed", error=str(exc)[:300], file=path.name)
        return None
