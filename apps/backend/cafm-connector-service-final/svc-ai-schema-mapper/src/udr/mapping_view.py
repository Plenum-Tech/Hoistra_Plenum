"""Combined mapping view for the field-mapping (semantic review) gate.

The Tier-2 gate must represent the COMPLETE post-semantic mapping state — not just the
unresolved Tier-2 subset. Previously the gate payload carried only ``tier2_flagged`` +
``tier2_unmappable``, so a run with 24 Tier-1 approvals + 5 unmappable rendered as
"Auto Accepted: 0 · Flagged: 0 · Unmappable: 5". This pure builder folds the full state into
three review buckets:

    auto_accepted = Tier-1 approved + Tier-2 auto-accepted
    flagged       = Tier-2 flagged (needs review)
    unmappable    = Tier-2 unmappable (no valid destination)

Pure (no DB / LangGraph / state access) → unit-tested in ``tests/test_udr_mapping_view.py``.
"""

from __future__ import annotations

from typing import Any


def build_combined_mapping_view(
    tier1_approved_by_table: Any,
    tier2_auto_by_table: Any,
    tier2_flagged_by_table: Any,
    tier2_unmappable_by_table: Any,
    *,
    table_routing: dict | None = None,
    cafm_table_matches: dict | None = None,
) -> dict:
    """Build ``{auto_accepted, flagged, unmappable}`` review cards from the full mapping state.

    Each row is normalised to ``{source_table, source_field, target_table, target_field,
    confidence, tier}``; the destination table falls back to the user's pre-semantic routing
    (``table_routing`` → ``cafm_table_matches``) when a row carries no explicit target_table.
    Entries are de-duplicated by ``(source_table, source_field)`` across ALL buckets in priority
    order auto_accepted → flagged → unmappable, so a field never appears twice and an approved
    mapping can never reappear as unmappable."""
    routing = {**(cafm_table_matches or {}), **(table_routing or {})}
    seen: set = set()

    def _row(src_table: str, m: Any, default_tier: str) -> dict:
        if isinstance(m, str):
            sf, tf, conf, tier, dst = m, None, None, default_tier, None
        elif isinstance(m, dict):
            sf = m.get("source_field") or m.get("field_name")
            tf = m.get("target_field")
            conf = m.get("confidence")
            tier = m.get("tier") or default_tier
            dst = m.get("target_table") or m.get("dest_table")
        else:
            return {}
        return {
            "source_table": src_table,
            "source_field": sf,
            "target_table": dst or routing.get(src_table),
            "target_field": tf,
            "confidence": conf,
            "tier": tier,
        }

    def _collect(by_table: Any, default_tier: str) -> list[dict]:
        out: list[dict] = []
        if not isinstance(by_table, dict):
            return out
        for src_table, maps in by_table.items():
            for m in (maps or []):
                row = _row(src_table, m, default_tier)
                sf = row.get("source_field")
                if not sf:
                    continue
                key = (src_table, sf)
                if key in seen:
                    continue
                seen.add(key)
                out.append(row)
        return out

    # Order matters: Tier-1 approvals claim their (table, field) first, so an approved field is
    # never re-emitted as Tier-2 auto / flagged / unmappable.
    auto_accepted = _collect(tier1_approved_by_table, "T1_approved") + _collect(tier2_auto_by_table, "T2_auto")
    flagged = _collect(tier2_flagged_by_table, "T2_flagged")
    unmappable = _collect(tier2_unmappable_by_table, "T2_unmappable")
    return {"auto_accepted": auto_accepted, "flagged": flagged, "unmappable": unmappable}
