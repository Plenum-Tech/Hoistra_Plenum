"""Rule-based document type classifier.

Uses keyword priors over the first few pages. Fast, deterministic, and
requires no LLM — exactly what section 8 of the spec recommends for MVP.

There was no ``certificate`` type here, and that is the whole reason vendor accreditation
coverage is what it is. Measured on the live database: 253 documents ingested, and the types
recorded were asset_manual 100, unknown 100, inspection_report 25, null 21, contract 6, sla 1.
Never one certificate — because a certificate could not be named. 65 of the unclassified ones
are plainly accreditations by filename (bpca-membership-certificate, sia_acs_certificate_of_
approval, SSAIB_certificate_of_...). They ingested fine, indexed fine, and produced no
compliance_certificates row, so only 7 of the 807 vendors reachable from a building hold any
accreditation at all.

Two changes:

  * ``certificate`` is a type, with the scheme names that actually appear in this estate.
  * The FILENAME is evidence. It was ignored entirely, which is a strange thing to discard
    when the file is called "bpca-membership-certificate.pdf". Text still outweighs it — a
    filename is a hint and a body is proof — but a hint beats nothing, and it rescues the
    scanned certificates whose text layer is thin.

A note on why they landed on asset_manual rather than unknown: that rule votes on
"maintenance", "safety" and "warning", which any certificate mentioning safe working or a
maintenance regime will hit. Certificate keywords are deliberately narrower than that.
"""
from __future__ import annotations

import re

from app.core.logger import logger
from app.services.extraction_service import ExtractedDocument
from app.utils.text_normalization import normalize_text

# keyword → document type votes
_KEYWORD_RULES: dict[str, list[str]] = {
    "certificate": [
        # What a certificate says about itself.
        "this is to certify", "certificate of", "certificate number", "certificate no",
        "is certified", "hereby certifies", "valid until", "expiry date", "date of expiry",
        "accreditation", "accredited", "registration number", "membership number",
        "policy number", "insured", "certificate of approval", "scope of certification",
        # The schemes this estate actually files, which name themselves on the page.
        "bpca", "sia acs", "ssaib", "gas safe", "niceic", "loler", "bafe", "safecontractor",
        "chas", "iso 9001", "iso 14001", "iso 45001", "eicr", "tm44", "public liability",
        "employers liability", "professional indemnity", "thorough examination",
    ],
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


#: Filename fragments that name a type outright. Matched against the name with every
#: separator removed, so "gas_safe", "gas-safe" and "GasSafe" are one fragment, and the UUID
#: prefix the store adds ("b9224317-…_bpca-membership-certificate-2.pdf") does not hide the
#: part a person actually wrote. Write fragments with no separators for the same reason.
_FILENAME_RULES: dict[str, list[str]] = {
    "certificate": [
        "certificate", "certification", "accreditation", "acs", "eicr", "tm44",
        "bpca", "ssaib", "niceic", "gassafe", "loler", "bafe", "chas",
        "safecontractor", "insurance", "liability", "iso9001", "iso14001", "iso45001",
    ],
    "invoice": ["invoice", "creditnote"],
    "contract": ["contract", "agreement"],
    "sla": ["servicelevel"],
    "inspection_report": ["inspection", "survey", "conditionreport"],
    "asset_manual": ["manual", "datasheet", "handbook"],
}

#: Words that swallow a shorter fragment and mean something else. Removed from the name before
#: that type is tested, never globally — "contractor" contains "contract", so
#: CONTRACTOR_PL_INSURANCE.docx read as both a contract and a certificate. It is a contractor's
#: public liability insurance: a certificate, and not a contract at all. Eight documents in the
#: live store are exactly this.
_FILENAME_NEGATIVE: dict[str, list[str]] = {
    "contract": ["contractor"],
}

#: Everything that is not a letter or a digit, for comparing names written a dozen ways.
_SEPARATORS = re.compile(r"[^a-z0-9]+")


def _name_key(file_name: str | None) -> str:
    """A filename reduced to letters and digits, lowercased."""
    return _SEPARATORS.sub("", (file_name or "").lower())


def _name_matches(name_key: str, doc_type: str, fragments: list[str]) -> bool:
    """Whether the filename argues for this type, after removing words that only look like it."""
    for swallowed in _FILENAME_NEGATIVE.get(doc_type, ()):
        name_key = name_key.replace(swallowed, "")
    return any(f in name_key for f in fragments)

#: A filename is a hint, a body is proof — so a name is worth less than a page of matching
#: text, but more than nothing. One weighted vote, not enough to overturn a confident body.
_FILENAME_WEIGHT = 2


class DocumentClassifier:
    def classify(self, document: ExtractedDocument) -> tuple[str, float]:
        """Return (document_type, confidence)."""
        # Use first 3 pages as evidence — classification should be fast.
        sample = "\n".join(p.text for p in document.pages[:3])
        sample_norm = normalize_text(sample)
        name_key = _name_key(document.file_name)

        scores: dict[str, int] = {}
        for doc_type, keywords in _KEYWORD_RULES.items():
            hits = sum(1 for kw in keywords if kw in sample_norm)
            if hits:
                scores[doc_type] = hits

        # The filename votes too. This is what rescues a scanned certificate whose text layer
        # is thin or missing — previously an empty body returned unknown before the name was
        # ever looked at, and the store is full of exactly those.
        name_votes: dict[str, int] = {}
        for doc_type, fragments in _FILENAME_RULES.items():
            if _name_matches(name_key, doc_type, fragments):
                name_votes[doc_type] = _FILENAME_WEIGHT
                scores[doc_type] = scores.get(doc_type, 0) + _FILENAME_WEIGHT

        if not scores:
            if not sample_norm:
                logger.warning("Classifier got empty document: {}", document.file_name)
            return "unknown", 0.0

        best = max(scores.items(), key=lambda kv: kv[1])
        total = sum(scores.values())
        confidence = best[1] / max(total, 1)
        logger.info(
            "Classified | file={} | type={} | confidence={:.2f} | scores={} | from_name={}",
            document.file_name, best[0], confidence, scores, sorted(name_votes),
        )
        return best[0], confidence


document_classifier = DocumentClassifier()
