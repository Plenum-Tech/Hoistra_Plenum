"""Rule-based document type classifier.

Uses keyword priors over the first few pages. Fast, deterministic, and
requires no LLM — exactly what section 8 of the spec recommends for MVP.
"""
from __future__ import annotations

from app.core.logger import logger
from app.services.extraction_service import ExtractedDocument
from app.utils.text_normalization import normalize_text

# keyword → document type votes
_KEYWORD_RULES: dict[str, list[str]] = {
    "invoice": [
        "invoice number", "bill to", "line items", "subtotal", "tax invoice",
        "amount due", "remit to",
    ],
    "sla": [
        "service level agreement", "service credits", "uptime", "response time",
        "resolution time", "kpi", "escalation matrix",
    ],
    "contract": [
        "this agreement", "termination", "parties hereto", "obligations",
        "governing law", "confidentiality", "whereas",
    ],
    "asset_manual": [
        "troubleshooting", "maintenance", "safety", "installation",
        "operating instructions", "spare parts", "warning",
    ],
    "inspection_report": [
        "inspection report", "findings", "observations", "inspector",
        "condition rating", "defect",
    ],
    "work_order": [
        "work order", "assigned technician", "scheduled date", "task list",
        "completion status",
    ],
    "policy": [
        "policy statement", "scope of this policy", "policy owner",
        "effective date", "review cycle",
    ],
}


class DocumentClassifier:
    def classify(self, document: ExtractedDocument) -> tuple[str, float]:
        """Return (document_type, confidence)."""
        # Use first 3 pages as evidence — classification should be fast.
        sample = "\n".join(p.text for p in document.pages[:3])
        sample_norm = normalize_text(sample)

        if not sample_norm:
            logger.warning("Classifier got empty document: {}", document.file_name)
            return "unknown", 0.0

        scores: dict[str, int] = {}
        for doc_type, keywords in _KEYWORD_RULES.items():
            hits = sum(1 for kw in keywords if kw in sample_norm)
            if hits:
                scores[doc_type] = hits

        if not scores:
            return "unknown", 0.0

        best = max(scores.items(), key=lambda kv: kv[1])
        total = sum(scores.values())
        confidence = best[1] / max(total, 1)
        logger.info(
            "Classified | file={} | type={} | confidence={:.2f} | scores={}",
            document.file_name, best[0], confidence, scores,
        )
        return best[0], confidence


document_classifier = DocumentClassifier()
