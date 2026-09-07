"""F5-5 — the explicit deterministic→semantic confidence cutoff (single source of truth).

A field auto-resolves in the deterministic node ONLY at >= this confidence; a lower-confidence
deterministic match (regex / Haiku / low-confidence alias) is routed to semantic review
instead of being silently auto-applied. Used by BOTH the migration deterministic mapper and
the Fiix schema deterministic node so the boundary is identical across flows.
"""

from __future__ import annotations

DETERMINISTIC_CUTOFF = 0.95  # >= auto-approves deterministically; < routes to semantic

# Semantic confidence bands (F7 7.4 AC4/AC5, 7.7 AC2/AC3): >=70% -> 'Suggested' (one-click
# confirm); <70% -> 'Requires Review' (manual assign). The >=85% auto-accept is a product tier
# above the spec's Suggested floor (it reduces review burden for very-high-confidence matches).
SEMANTIC_REVIEW_MIN = 0.70
SEMANTIC_AUTO_MIN = 0.85


def _conf(mapping) -> float:
    try:
        return float(mapping.get("confidence", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def is_auto_deterministic(confidence, cutoff: float = DETERMINISTIC_CUTOFF) -> bool:
    """Whether a confidence qualifies for deterministic auto-approval (F5-5)."""
    try:
        return float(confidence or 0.0) >= cutoff
    except (TypeError, ValueError):
        return False


def partition_by_cutoff(mappings, cutoff: float = DETERMINISTIC_CUTOFF):
    """Split deterministic mappings into ``(kept, reroute_sources)``:

    - ``kept``            — mappings at >= cutoff (stay auto-resolved in Tier 1),
    - ``reroute_sources`` — source field names of mappings below cutoff (go to semantic).

    A source kept under one target is never rerouted even if a second, lower-confidence
    mapping for it falls below the cutoff.
    """
    kept = [m for m in (mappings or []) if _conf(m) >= cutoff]
    kept_sources = {m.get("source_field") for m in kept}
    reroute: list = []
    seen: set = set()
    for m in mappings or []:
        if _conf(m) >= cutoff:
            continue
        src = m.get("source_field")
        if src and src not in kept_sources and src not in seen:
            seen.add(src)
            reroute.append(src)
    return kept, reroute
