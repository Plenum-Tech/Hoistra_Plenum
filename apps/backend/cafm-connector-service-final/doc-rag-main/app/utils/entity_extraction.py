"""Lightweight regex-based entity extraction.

This gives the pipeline something useful out of the box with zero deps.
In production you'd plug in spaCy / a trained NER model here.
"""
import re
from datetime import date

# Common enterprise ID patterns
_PATTERNS = {
    "asset_code": re.compile(r"\b([A-Z]{2,5}[-_]?\d{2,6})\b"),
    "contract_number": re.compile(r"\bCTR[-_]?\d{2,4}[-_]?\d{2,6}\b", re.IGNORECASE),
    "invoice_number": re.compile(r"\bINV[-_]?\d{3,8}\b", re.IGNORECASE),
    "po_number": re.compile(r"\bPO[-_]?\d{3,8}\b", re.IGNORECASE),
    "sla_code": re.compile(r"\bSLA[-_]?\d{2,6}\b", re.IGNORECASE),
    "serial_number": re.compile(r"\bSN[-_]?[A-Z0-9]{4,12}\b", re.IGNORECASE),
    "date_iso": re.compile(r"\b(\d{4}-\d{2}-\d{2})\b"),
    "date_slash": re.compile(r"\b(\d{1,2}/\d{1,2}/\d{2,4})\b"),
    "money": re.compile(r"\b(?:USD|EUR|AED|\$|€)\s?\d[\d,]*(?:\.\d{2})?\b", re.IGNORECASE),
    "percent": re.compile(r"\b\d{1,3}(?:\.\d+)?\s?%"),
    "duration_days": re.compile(r"\b(\d{1,4})\s?days?\b", re.IGNORECASE),
}


def extract_entities(text: str) -> dict[str, list[str]]:
    """Return a mapping of entity_type -> list of unique string matches."""
    if not text:
        return {}
    out: dict[str, list[str]] = {}
    for name, pat in _PATTERNS.items():
        matches = pat.findall(text)
        # .findall may return tuples if the pattern has groups
        flat = []
        for m in matches:
            if isinstance(m, tuple):
                m = next((x for x in m if x), "")
            if m:
                flat.append(m)
        if flat:
            # dedupe, preserve order
            seen = set()
            uniq = []
            for x in flat:
                if x not in seen:
                    seen.add(x)
                    uniq.append(x)
            out[name] = uniq
    return out


def extract_keys(text: str) -> list[str]:
    """Shortcut: return all ID-like keys in a flat list (for row matching)."""
    ents = extract_entities(text)
    keys: list[str] = []
    for key_type in ("asset_code", "contract_number", "invoice_number",
                     "po_number", "sla_code", "serial_number"):
        keys.extend(ents.get(key_type, []))
    return keys
