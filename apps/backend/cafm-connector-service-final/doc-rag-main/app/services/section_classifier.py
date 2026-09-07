"""Heuristic section label classifier.

Given a chunk of text, suggest a section label (e.g. "termination",
"line_items", "maintenance"). This is used for routing and reranking.

Key improvements:
  - Word-boundary matching (prevents "tax" matching inside "taxonomy").
  - Minimum-score threshold to avoid weak spurious matches.
  - More section labels for exhibits, equipment, and asset details.
"""
from __future__ import annotations

import re

from app.core.logger import logger
from app.utils.text_normalization import normalize_text

_SECTION_RULES: dict[str, list[str]] = {
    # Contract
    "definitions": ["definitions", "interpretation", "defined terms"],
    "parties": ["parties hereto", "between", "party a", "party b"],
    "scope": ["scope of work", "scope of services", "services provided"],
    "pricing": ["fees", "charges", "pricing", "payment schedule", "rate card"],
    "term": ["term of this agreement", "commencement", "effective date"],
    "renewal": ["renewal", "auto-renew", "extension"],
    "obligations": ["obligations", "responsibilities", "duties"],
    "penalties": ["penalty", "penalties", "liquidated damages", "service credits"],
    "termination": ["termination", "terminate", "cancellation"],
    "annexure": ["annexure", "annex", "appendix", "schedule"],
    # Exhibit / asset details
    "exhibit": ["exhibit", "attachment"],
    "equipment_details": [
        "elevator", "escalator", "hvac", "equipment details",
        "station type", "lift details", "load kw", "total load",
        "single unit load", "total quantity",
    ],
    "asset_details": [
        "asset code", "asset name", "serial number", "manufacturer",
        "model number", "equipment list",
    ],
    # SLA
    "service_scope": ["service scope", "covered services"],
    "kpi": ["kpi", "key performance", "metrics", "performance indicator"],
    "response_targets": ["response time", "target response"],
    "resolution_targets": ["resolution time", "target resolution"],
    "service_credits": ["service credit", "credit calculation", "uptime credit"],
    "escalation": ["escalation matrix", "escalation procedure", "escalate"],
    # Invoice
    "line_items": ["line items", "description of services", "item description"],
    "totals": ["subtotal", "total amount", "grand total", "amount due", "net total"],
    "taxes": ["tax invoice", "tax amount", "vat amount", "gst amount",
              "tax rate", "withholding tax"],
    "payment_terms": ["payment terms", "due date", "net 30", "net 60"],
    # Manual
    "warnings": ["warning", "caution", "danger", "safety precaution"],
    "installation": ["installation", "setup procedure"],
    "operation": ["operating instructions", "operation manual", "operating procedure"],
    "maintenance": ["maintenance", "preventive maintenance", "servicing",
                     "maintenance schedule", "maintenance frequency"],
    "troubleshooting": ["troubleshooting", "problem", "fault diagnosis", "error code"],
    "spare_parts": ["spare parts", "replacement parts", "parts list"],
    # Inspection
    "inspection": ["inspection report", "findings", "observations",
                   "condition rating", "defect", "non-compliance"],
    # Work order
    "work_order": ["work order", "assigned technician", "task list",
                   "completion status", "scheduled date"],
}


def _word_boundary_match(keyword: str, text: str) -> bool:
    """Check if `keyword` appears in `text` as a whole-word match.

    For multi-word keywords like 'preventive maintenance', we check that
    the full phrase appears. For single-word keywords, we require word
    boundaries so 'tax' doesn't match inside 'extraction'.
    """
    pattern = r"\b" + re.escape(keyword) + r"\b"
    return bool(re.search(pattern, text))


class SectionClassifier:
    # Minimum total hits required to commit to a label.
    MIN_CONFIDENCE_HITS = 1

    def classify(self, text: str) -> tuple[str | None, float]:
        if not text:
            return None, 0.0
        norm = normalize_text(text)
        if not norm:
            return None, 0.0

        scores: dict[str, int] = {}
        for label, keywords in _SECTION_RULES.items():
            hits = sum(1 for kw in keywords if _word_boundary_match(kw, norm))
            if hits >= self.MIN_CONFIDENCE_HITS:
                scores[label] = hits

        if not scores:
            return None, 0.0

        best_label, best_hits = max(scores.items(), key=lambda kv: kv[1])
        total_hits = sum(scores.values())
        confidence = best_hits / max(total_hits, 1)

        # If the best label only has 1 hit and there are competing labels
        # with the same score, the classification is ambiguous — still
        # return it but with low confidence.
        ambiguous = best_hits == 1 and len(scores) > 1
        if ambiguous:
            confidence *= 0.5

        logger.debug(
            "SectionClassifier | label={} | confidence={:.3f} | hits={} | "
            "competing_labels={} | ambiguous={} | scores={}",
            best_label, round(confidence, 3), best_hits,
            len(scores), ambiguous,
            {k: v for k, v in sorted(scores.items(), key=lambda x: -x[1])[:5]},
        )
        return best_label, round(confidence, 3)


section_classifier = SectionClassifier()
