"""Count the country pack against the register — once, in code, for every path.

Counting is not reasoning. Asked to tally 55 pack rows in prose, the model returned "31
vendor types" against a 28-type pack and contradicted its own "19 with nothing on record"
in the same answer. These numbers are therefore computed here and handed over as facts: the
preflight path reads them from `pack_facts` in its context, the sub-agent path fetches them
through the `get_pack_facts` tool, and both get the same arithmetic from the same function.
The model still decides what to say about them.
"""
from __future__ import annotations

from typing import Any


def compute_pack_facts(
    types: list[Any], rows: list[dict[str, Any]], country_code: str = "UK"
) -> dict[str, Any] | None:
    """Per-scope totals, held vs. unevidenced codes, and rows that match no pack type."""
    types = [t for t in (types or []) if isinstance(t, dict)]
    if not types:
        return None

    held_by_scope: dict[str, set[str]] = {"Building": set(), "Vendor": set()}
    for r in rows or []:
        code = str(r.get("certificate_type_code") or "")
        scope = str(r.get("cert_scope") or "").title()
        if code and scope in held_by_scope:
            held_by_scope[scope].add(code)

    facts: dict[str, Any] = {
        "country_code": country_code or "UK",
        "total_types": len(types),
        "note": (
            "These counts are computed from the pack rows. Use them verbatim — do not "
            "re-derive them. A type with nothing on record is still required."
        ),
        "by_scope": {},
    }
    for scope in ("Building", "Vendor"):
        in_scope = [t for t in types if str(t.get("certificate_scope")) == scope]
        codes = {str(t.get("certificate_type_code")) for t in in_scope}
        held = held_by_scope[scope] & codes
        trades: dict[str, list[str]] = {}
        for t in in_scope:
            trades.setdefault(str(t.get("trade_category") or "Other"), []).append(
                str(t.get("certificate_type_code"))
            )
        facts["by_scope"][scope] = {
            "required_types": len(in_scope),
            "trade_categories": len(trades),
            "types_held": len(held),
            "types_with_nothing_on_record": len(codes - held),
            "by_trade": {k: len(v) for k, v in sorted(trades.items())},
            "codes_held": sorted(held),
            "codes_with_nothing_on_record": sorted(codes - held),
        }

    # Rows that answer to no pack type at all — a US or legacy instrument cannot evidence a
    # UK duty, and counting it as coverage overstates the file.
    all_codes = {str(t.get("certificate_type_code")) for t in types}
    facts["rows_not_matching_any_pack_type"] = [
        {
            "certificate_type_code": str(r.get("certificate_type_code") or ""),
            "country_code": r.get("country_code"),
            "certificate_type_name": r.get("certificate_type_name"),
        }
        for r in rows or []
        if str(r.get("certificate_type_code") or "") not in all_codes
    ]
    facts["rows_from_another_country"] = sorted(
        {
            str(r.get("country_code"))
            for r in rows or []
            if r.get("country_code") and str(r.get("country_code")) != facts["country_code"]
        }
    )
    return facts
