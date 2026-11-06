"""Whether a certificate has FAILED A CHECK - one rule for the chat and the Compliance page (3 Nov 2026).

"Which certificates failed authenticity checks?" was answered "None" from forensics verdicts alone, while two of the
company's certificates (EPC 0310-4471-2090-6655-2281 and TM44-B-301-2022) had failed their register check, and the
Compliance page counts a warning on a document with no clean verdict as suspect. Hussain (3 Nov 2026): a failed check is
the page's authenticity rule PLUS the register check.

* failed_authenticity - the forensics verdict flags the document, or an authenticity warning stands with no clean
  verdict beside it (an advisory note beside a passed verdict is a task, not doubt about the paper).
* failed_registration - the register check failed: a person found it not on the register or with different details,
  or the platform's own register lookup failed.
* failed_check - either.

The same rule in SQL (alias c on plenum_cafm.compliance_certificates) lets a question filter on it, so the answer reads
the rows that failed instead of the whole register. Mirrored in the frontend's complianceLive.js authOf / verChipOf.
"""
from __future__ import annotations

import json
import re
from typing import Any

FLAGGED = r"suspect|forg|fail|reject|high|review|edited"
CLEAN = r"genuine|pass|verified|clear|low"
_FLAGGED_RE = re.compile(FLAGGED)
_CLEAN_RE = re.compile(CLEAN)
REGISTER_FAILED = ("human_not_found", "human_mismatch", "failed")
REGISTER_CONFIRMED = ("human_verified", "verified")
REGISTER_WORDS = {"human_not_found": "not on the register", "human_mismatch": "register details differ",
                  "failed": "register check failed"}

#: The boolean fields a compliance question may filter on, as SQL over alias c.
_VERDICT_SQL = ("lower(coalesce(c.raw_metadata->>'forensics_verdict', c.raw_metadata->>'forensics_ccc_verdict', "
                "''))")
_WARNING_SQL = "coalesce(nullif(btrim(c.authenticity_warning::text), ''), '')"
_REG_SQL = ("coalesce(c.raw_metadata->'verification'->>'status', c.raw_metadata->'verification'->>'result', "
            "c.raw_metadata->'ccc_verification'->>'status', c.raw_metadata->'ccc_verification'->>'result', "
            "c.raw_metadata->>'verification_status', '')")
FAILED_AUTHENTICITY_SQL = (f"({_VERDICT_SQL} ~ '({FLAGGED})' OR ({_WARNING_SQL} NOT IN ('', 'null', '[]') "
                           f"AND {_VERDICT_SQL} !~ '({CLEAN})'))")
FAILED_REGISTRATION_SQL = (f"({_REG_SQL} IN ('human_not_found', 'human_mismatch', 'failed') OR "
                           "coalesce(c.raw_metadata->'verification'->>'verified', "
                           "c.raw_metadata->'ccc_verification'->>'verified', '') = 'false')")
SQL = {"failed_authenticity": FAILED_AUTHENTICITY_SQL, "failed_registration": FAILED_REGISTRATION_SQL,
       "failed_check": f"({FAILED_AUTHENTICITY_SQL} OR {FAILED_REGISTRATION_SQL})"}


def _meta(row: dict[str, Any]) -> dict[str, Any]:
    m = row.get("raw_metadata")
    if isinstance(m, str):
        try:
            m = json.loads(m)
        except ValueError:
            m = None
    return m if isinstance(m, dict) else {}


def _get(row: dict[str, Any], key: str) -> Any:
    v = row.get(key)
    return v if v not in (None, "") else _meta(row).get(key)


def verdict(row: dict[str, Any]) -> str:
    return str(_get(row, "forensics_verdict") or _get(row, "forensics_ccc_verdict") or "").strip().lower()


def authenticity(row: dict[str, Any]) -> str:
    """'suspect', 'genuine' or 'not checked' - the page's authOf."""
    v = verdict(row)
    warning = _get(row, "authenticity_warning")
    warning = "" if warning in (None, [], {}) else str(warning).strip()
    flagged, cleared = bool(_FLAGGED_RE.search(v)), bool(_CLEAN_RE.search(v))
    if flagged or (warning and warning not in ("null", "[]") and not cleared):
        return "suspect"
    return "genuine" if cleared else "not checked"


def registration(row: dict[str, Any]) -> str:
    """The register check's status word ('failed', 'human_not_found', 'verified', 'needs_human' ...) or ''."""
    v = _get(row, "verification") or _get(row, "ccc_verification") or {}
    v = v if isinstance(v, dict) else {}
    st = str(v.get("status") or v.get("result") or _get(row, "verification_status") or "").strip()
    if not st and v.get("verified") is False:
        return "failed"
    if not st and v.get("verified") is True:
        return "verified"
    return st


def flags(row: dict[str, Any]) -> dict[str, Any]:
    reg = registration(row)
    v = _get(row, "verification") or _get(row, "ccc_verification") or {}
    failed_reg = reg in REGISTER_FAILED or (isinstance(v, dict) and v.get("verified") is False)
    failed_auth = authenticity(row) == "suspect"
    return {"failed_authenticity": failed_auth, "failed_registration": failed_reg,
            "failed_check": failed_auth or failed_reg, "registration_status": reg or "not checked",
            "registration_finding": REGISTER_WORDS.get(reg) if failed_reg else None}
