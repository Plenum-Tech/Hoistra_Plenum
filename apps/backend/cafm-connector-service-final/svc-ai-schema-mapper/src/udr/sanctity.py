"""Feature 7 F7-5 — relationship sanctity check (PURE, flag-with-confidence).

A POC plausibility check for a claimed entity link, e.g. a work order linked to an asset:
if the WO's evidence says "replaced pump on Level 5 AHU" but the linked asset is a
"Level 3 chiller", the link is implausible. It extracts salient signals (floor/level +
asset type) from the WO evidence and the asset record, compares them, and returns a verdict
with a confidence + reason. It NEVER mutates data — it only flags (the plan: ship as
flag-with-confidence, never auto-mutating).

Pure (no DB/LLM) so the heuristics are unit-testable; the evidence-gathering (RowMatch /
vector retrieval) is the integration layer that feeds this. ``build_sanctity_activity``
turns a flagged verdict into an escalatable Activity-Log entry + inline action (F7-6),
reusing the Section-3 machinery.
"""

from __future__ import annotations

import re

from .actions import build_inline_action

# canonical asset type -> recognisable synonyms (CMMS-common equipment)
ASSET_TYPES: dict[str, list[str]] = {
    "chiller": ["chiller"],
    "ahu": ["ahu", "air handling unit", "air handler"],
    "fcu": ["fcu", "fan coil"],
    "pump": ["pump"],
    "lift": ["lift", "elevator", "escalator"],
    "boiler": ["boiler"],
    "fan": ["exhaust fan", "supply fan", "fan"],
    "compressor": ["compressor"],
    "cooling_tower": ["cooling tower"],
    "generator": ["generator", "genset"],
    "transformer": ["transformer"],
    "valve": ["valve"],
    "tank": ["tank"],
}

_LEVEL_PATTERNS = [
    re.compile(r"\b(?:level|lvl|floor|fl)\s*\.?\s*(\d{1,3})\b", re.I),
    re.compile(r"\bL[\s-]?(\d{1,2})\b"),
    re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)\s+floor\b", re.I),
]


def _as_text(evidence) -> str:
    if isinstance(evidence, dict):
        return " ".join(str(v) for v in evidence.values() if v is not None)
    return str(evidence or "")


def extract_level(text: str) -> int | None:
    """The floor/level referenced in free text (ground floor -> 0, basement -> -1)."""
    low = (text or "").lower()
    if "ground floor" in low or re.search(r"\bg/?f\b", low):
        return 0
    if "basement" in low:
        return -1
    for pat in _LEVEL_PATTERNS:
        m = pat.search(text or "")
        if m:
            try:
                return int(m.group(1))
            except (TypeError, ValueError):
                continue
    return None


def extract_asset_types(text: str) -> set[str]:
    """Canonical asset types mentioned in free text."""
    low = (text or "").lower()
    found: set[str] = set()
    for canonical, syns in ASSET_TYPES.items():
        if any(s in low for s in syns):
            found.add(canonical)
    return found


def extract_signals(evidence) -> dict:
    text = _as_text(evidence)
    return {"level": extract_level(text), "asset_types": extract_asset_types(text)}


def validate_relationship(wo_evidence, asset_record) -> dict:
    """Compare a WO's evidence against the linked asset record. Returns
    ``{plausible, confidence, reason, mismatches, signals}``. Never mutates anything."""
    wo = extract_signals(wo_evidence)
    asset = extract_signals(asset_record)
    mismatches: list[dict] = []

    # floor/level disagreement is a strong, concrete mismatch
    if wo["level"] is not None and asset["level"] is not None and wo["level"] != asset["level"]:
        mismatches.append({"signal": "level", "wo_value": wo["level"], "asset_value": asset["level"]})

    # asset-type disagreement (both name a type, with no overlap)
    if wo["asset_types"] and asset["asset_types"] and not (wo["asset_types"] & asset["asset_types"]):
        mismatches.append(
            {
                "signal": "asset_type",
                "wo_value": sorted(wo["asset_types"]),
                "asset_value": sorted(asset["asset_types"]),
            }
        )

    has_level_mismatch = any(m["signal"] == "level" for m in mismatches)
    has_type_mismatch = any(m["signal"] == "asset_type" for m in mismatches)

    if mismatches:
        if has_level_mismatch and has_type_mismatch:
            confidence = 0.95
        elif has_level_mismatch:
            confidence = 0.9
        else:
            confidence = 0.85
        reason = _mismatch_reason(mismatches)
        return {"plausible": False, "confidence": round(confidence, 2), "reason": reason,
                "mismatches": mismatches, "signals": {"wo": _ser(wo), "asset": _ser(asset)}}

    # no mismatch — gauge how much agreeing evidence supported the link
    agreements = 0
    if wo["level"] is not None and asset["level"] is not None and wo["level"] == asset["level"]:
        agreements += 1
    if wo["asset_types"] and asset["asset_types"] and (wo["asset_types"] & asset["asset_types"]):
        agreements += 1

    if agreements == 0:
        return {"plausible": True, "confidence": 0.3,
                "reason": "Insufficient overlapping evidence to assess the link.",
                "mismatches": [], "signals": {"wo": _ser(wo), "asset": _ser(asset)}}

    return {"plausible": True, "confidence": round(min(0.9, 0.6 + 0.15 * agreements), 2),
            "reason": "WO evidence is consistent with the linked asset.",
            "mismatches": [], "signals": {"wo": _ser(wo), "asset": _ser(asset)}}


def _ser(sig: dict) -> dict:
    return {"level": sig["level"], "asset_types": sorted(sig["asset_types"])}


def _mismatch_reason(mismatches: list[dict]) -> str:
    parts = []
    for m in mismatches:
        if m["signal"] == "level":
            parts.append(f"WO references level {m['wo_value']} but the asset is on level {m['asset_value']}")
        elif m["signal"] == "asset_type":
            parts.append(
                f"WO describes a {'/'.join(m['wo_value'])} but the asset is a {'/'.join(m['asset_value'])}"
            )
    return "Possible misallocation: " + "; ".join(parts) + "."


def build_sanctity_activity(verdict: dict, *, wo_id: str, asset_id: str) -> dict:
    """F7-6 — turn a FLAGGED sanctity verdict into an escalatable Activity-Log entry + an
    inline action (approve / reassign / mark coincidental). Returns ``{entry, action}``.
    Only meaningful when ``verdict['plausible']`` is False."""
    reason = verdict.get("reason", "Relationship flagged for review")
    label = f"WO {wo_id} ↔ asset {asset_id}: {reason}"
    action = build_inline_action(
        label,
        kind="reassignment",
        options=[
            {"id": "confirm", "label": "Link is correct", "value": "confirm"},
            {"id": "reassign", "label": "Reassign the work order", "value": "reassign"},
            {"id": "coincidental", "label": "Mark coincidental", "value": "coincidental"},
        ],
    )
    entry = {
        "trigger": "threshold",  # a detected deviation
        "trigger_detail": reason,
        "outcome": f"Sanctity check flagged WO {wo_id} ↔ asset {asset_id} "
        f"({int(round(verdict.get('confidence', 0) * 100))}% confidence) — review required",
        "status": "escalated",
        "notif_color": "orange",
        "refs": {"wo_id": wo_id, "asset_id": asset_id, "sanctity": verdict},
        "processing_log": None,
    }
    return {"entry": entry, "action": action}
