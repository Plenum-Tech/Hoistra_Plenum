"""Thin, flag-gated adapter between the FM ontology and the LangGraph mapper.

Additive only: when ``UDR_ONTOLOGY_ENABLED`` is not truthy (the default) every
function returns None, so the existing pipeline behaviour is unchanged. Nodes
opt in by calling these helpers as a supplementary strategy.

The actual ontology lookups live in ``src/ontology_loader.py`` (Phase I-B).
"""
from __future__ import annotations

import os
from typing import Optional

from cafm_shared.logging import get_logger

logger = get_logger(__name__)


def ontology_enabled() -> bool:
    """Feature flag. Off by default — keeps the live pipeline unchanged."""
    return (os.environ.get("UDR_ONTOLOGY_ENABLED", "") or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _loader():
    """Import the loader lazily so a missing ontology dir never breaks the node."""
    try:
        from ... import ontology_loader  # src/ontology_loader.py
        return ontology_loader
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning(f"[ontology] loader unavailable: {exc}")
        return None


def ontology_table_match(source_table: str) -> Optional[dict]:
    """Resolve a source table name to a canonical table via the ontology.

    Returns {canonical_table, confidence, method[, cmms_system]} or None. Always
    None when the flag is off.
    """
    if not ontology_enabled() or not source_table:
        return None
    ldr = _loader()
    if not ldr:
        return None
    try:
        return ldr.resolve_table(source_table)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[ontology] resolve_table({source_table!r}) failed: {exc}")
        return None


def ontology_column_match(source_field: str, dest_table: Optional[str]) -> Optional[dict]:
    """Resolve a source column to a canonical column, constrained to dest_table
    when known. Returns {canonical_table, canonical_column, confidence, method
    [, cmms_system]} or None. Always None when the flag is off."""
    if not ontology_enabled() or not source_field:
        return None
    ldr = _loader()
    if not ldr:
        return None
    try:
        return ldr.resolve_column(source_field, dest_table)
    except Exception as exc:  # pragma: no cover
        logger.warning(f"[ontology] resolve_column({source_field!r}) failed: {exc}")
        return None
