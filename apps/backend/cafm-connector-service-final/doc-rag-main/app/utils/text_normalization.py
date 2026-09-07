"""Text normalization utilities.

Used by the chunker, BM25 indexer, and row matcher to make lexical
matching robust against OCR noise, whitespace, and case.
"""
import re
import unicodedata

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\s\-./]")


def normalize_text(text: str) -> str:
    """Return a lowercased, whitespace-collapsed, accent-stripped version."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = _WS_RE.sub(" ", text).strip()
    return text


def normalize_key(key: str) -> str:
    """Aggressive normalization for ID-like keys (asset codes, contract nums).

    Strips separators and fixes OCR confusions so 'AHU-17' and 'ahu 17'
    and 'AHU_17' all normalize to the same string.
    """
    if not key:
        return ""
    key = key.upper().strip()
    # Fix common OCR confusions in numeric contexts
    key = key.replace("O", "0") if any(ch.isdigit() for ch in key) else key
    key = re.sub(r"[\s\-_/\\.]", "", key)
    return key


def tokenize(text: str) -> list[str]:
    """Simple whitespace + punctuation tokenizer for BM25."""
    text = normalize_text(text)
    text = _PUNCT_RE.sub(" ", text)
    return [t for t in text.split() if t]


def token_overlap(a: str, b: str) -> float:
    """Jaccard token overlap between two texts (0.0 - 1.0)."""
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)
