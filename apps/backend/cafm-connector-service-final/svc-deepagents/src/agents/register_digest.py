"""The certificate register as the compliance analyst reads it: every certificate, sized by need.

The analyst, its reviewer and its revision each received every certificate as a full JSON object
(capped at 90,000 characters, then rows dropped from the end). Measured over 14 days of Hoist
Traces (6 Oct 2026): those single calls were 42% of all model spend, prompts of 42-55k tokens,
and the context budget cannot reach them - each is one call, not a loop.

Most of a register is certificates that are current and clean. The analyst needs to know they
exist and when they expire, not their twenty other fields. So:

* a certificate that needs attention - lapsed, expiring (the same rules as compliance_answer),
  blocked vendor, forged or suspect, a draft, or no expiry on record - stays a full row, with the
  reasons it is there;
* every other certificate becomes one line: id | scope | type | number | owner | building |
  expiry | status | days - still present, still quotable, about a tenth of the size;
* totals on top: by status, attention reasons and blocked vendors.

Nothing is dropped: a question about a current certificate still finds it. A register under
``MIN_ROWS`` is sent as before - there is nothing worth saving.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from .compliance_answer import _is_at_risk, _is_blocked, _is_forged, _is_lapsed

MIN_ROWS = 15
COLUMNS = "id | scope | type | number | owner | building | expiry | status | days_to_expiry"


def is_certificate_row(row: Any) -> bool:
    return isinstance(row, dict) and "status" in row and any(
        k in row for k in ("expiry_date", "certificate_type_code", "cert_type"))


def attention_reasons(row: dict[str, Any]) -> list[str]:
    """Why a certificate needs the analyst's full attention. Empty when it is current and clean."""
    why = []
    if _is_lapsed(row) or row.get("is_lapsed"):
        why.append("lapsed")
    elif _is_at_risk(row) or row.get("is_expiring_soon"):
        why.append("expiring")
    if _is_blocked(row) or row.get("is_blocked"):
        why.append("blocked vendor")
    if _is_forged(row) or row.get("is_forged") or row.get("is_suspect_authenticity"):
        why.append("authenticity")
    if row.get("is_draft") or str(row.get("status") or "").lower() == "draft":
        why.append("draft")
    if not row.get("expiry_date"):
        why.append("no expiry on record")
    return why


def _cell(v: Any) -> str:
    return "" if v is None else str(v).replace("|", "/").replace("\n", " ").strip()


def compact_line(row: dict[str, Any]) -> str:
    scope = str(row.get("cert_scope") or "")
    owner = row.get("vendor_name") if scope.lower() == "vendor" else (row.get("building_name") or row.get("site_name"))
    return " | ".join(_cell(x) for x in (
        row.get("id"), scope, row.get("certificate_type_code") or row.get("cert_type"),
        row.get("certificate_number"), owner, row.get("building_name") or row.get("site_name"),
        str(row.get("expiry_date") or "")[:10], row.get("status"), row.get("days_to_expiry")))


def digest(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """The register for the analyst. Pure; every certificate in ``rows`` is in the result."""
    attention, current = [], []
    reasons: Counter = Counter()
    for r in rows:
        why = attention_reasons(r)
        if why:
            attention.append({**r, "needs_attention_because": why})
            reasons.update(why)
        else:
            current.append(compact_line(r))
    blocked = sorted({str(r.get("vendor_name")) for r in rows
                      if (_is_blocked(r) or r.get("is_blocked")) and r.get("vendor_name")})
    return {
        "register_digest": True,
        "note": ("Every certificate is here. Those needing attention are full rows with the reasons; "
                 "every other one - current and clean - is one line in 'current' with the columns in "
                 "'current_columns'. Quote a current certificate from its line."),
        "total": len(rows),
        "by_status": dict(Counter(str(r.get("status") or "unknown") for r in rows)),
        "attention_count": len(attention),
        "attention_reasons": dict(reasons),
        "blocked_vendors": blocked,
        "attention": attention,
        "current_columns": COLUMNS,
        "current": current,
    }


def maybe_digest(rows: list[Any]) -> Any:
    """``rows`` digested when they are a certificate register worth digesting; else unchanged."""
    if len(rows) < MIN_ROWS or not all(is_certificate_row(r) for r in rows):
        return rows
    return digest(rows)
