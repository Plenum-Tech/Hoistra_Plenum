"""A failed check is a failed authenticity check OR a failed register check - one rule (3 Nov 2026).

"Which certificates failed authenticity checks?" was answered "None. ... not one carries a failed or suspect forensics
verdict" for Plenum Technologies. Read-only check the same day: no forensics verdict was flagged, but two certificates
had FAILED their register check - EPC 0310-4471-2090-6655-2281 and TM44-B-301-2022 (raw_metadata.verification status
'failed', verified false). Hussain chose: the Compliance page's authenticity rule plus the register check, in the chat
and on the page alike. The planner had also filtered on forensics_verdict alone, matched nothing, and the empty filter
was widened to the whole register: 224 rows, 200,735 input tokens to the analyst, $0.445.
"""
from __future__ import annotations

import pytest

from src.agents import cert_checks
import src.agents.orchestrator as O

EPC = {"certificate_number": "0310-4471-2090-6655-2281", "cert_scope": "Building",
       "raw_metadata": {"verification": {"status": "failed", "verified": False}}}
TM44 = {"certificate_number": "TM44-B-301-2022", "verification": {"status": "failed", "verified": False}}
LOLER_PASSED = {"certificate_number": "LOLER-HLE-1", "forensics_verdict": "PASS", "forensics_risk_score": 0,
                "authenticity_warning": "Confirm against the register via Verify now"}
WARNED = {"certificate_number": "GAS-9", "authenticity_warning": "Incremental PDF edits after signing"}
FORGED = {"certificate_number": "FRA-1", "raw_metadata": {"forensics_verdict": "FAIL", "forensics_risk_score": 91}}
REVIEW = {"certificate_number": "EICR-2", "forensics_verdict": "review"}
CONFIRMED = {"certificate_number": "NICEIC-1", "raw_metadata": {"verification": {"status": "human_verified"}}}
NOT_FOUND = {"certificate_number": "BAFE-1", "raw_metadata": {"verification": {"status": "human_not_found"}}}
UNCHECKED = {"certificate_number": "DEC-1"}


@pytest.mark.parametrize("row,auth,reg,failed", [
    (EPC, False, True, True),
    (TM44, False, True, True),
    (LOLER_PASSED, False, False, False),     # an advisory note beside a passed verdict is a task, not doubt
    (WARNED, True, False, True),             # a warning with no verdict at all: the page calls it suspect
    (FORGED, True, False, True),
    (REVIEW, True, False, True),
    (CONFIRMED, False, False, False),
    (NOT_FOUND, False, True, True),
    (UNCHECKED, False, False, False),
])
def test_one_rule_for_a_failed_check(row, auth, reg, failed):
    f = cert_checks.flags(row)
    assert (f["failed_authenticity"], f["failed_registration"], f["failed_check"]) == (auth, reg, failed)


def test_the_finding_is_said_in_words():
    assert cert_checks.flags(NOT_FOUND)["registration_finding"] == "not on the register"
    assert cert_checks.flags(EPC)["registration_finding"] == "register check failed"
    assert cert_checks.flags(UNCHECKED)["registration_status"] == "not checked"


def test_every_row_the_analyst_reads_carries_the_flags():
    f = O.DeepAgentOrchestrator._derive_row_flags(dict(EPC))
    assert f["failed_check"] is True and f["failed_registration"] is True and f["failed_authenticity"] is False


def test_a_question_may_filter_on_a_failed_check():
    orch = O.DeepAgentOrchestrator.__new__(O.DeepAgentOrchestrator)
    sql, params = orch._spec_where_clause([{"field": "failed_check", "op": "eq", "value": True}], "and")
    assert "human_not_found" in sql and "forensics_verdict" in sql and params == {}
    no, _ = orch._spec_where_clause([{"field": "failed_registration", "op": "eq", "value": False}], "and")
    assert "(NOT (" in no


def test_the_check_fields_pass_validation():
    import asyncio

    orch = O.DeepAgentOrchestrator.__new__(O.DeepAgentOrchestrator)

    async def allow():
        return frozenset({"status", "days_to_expiry"})

    orch._compliance_column_allowlist = allow
    ok, bad = asyncio.run(orch._validate_query_spec({"filters": [{"field": "failed_check", "op": "eq", "value": True}]}))
    assert ok and not bad


def test_the_planner_is_told_what_a_failed_check_is():
    # The planner's instructions are skills/compliance/planner.md since 5 Nov 2026, read at runtime.
    assert '{"field":"failed_check","op":"eq","value":true}' in O.compliance_planner_prompt("")
    assert "failed_check" in O.DeepAgentOrchestrator._SPEC_CHECK_FIELDS


def test_an_empty_failed_check_filter_is_the_answer_not_a_reason_to_read_everything():
    """A filter on a failed-check field is computed from the records themselves: nothing matching means none failed."""
    assert O.DeepAgentOrchestrator._empty_is_the_answer(
        {"filters": [{"field": "failed_check", "op": "eq", "value": True}]})
    assert O.DeepAgentOrchestrator._empty_is_the_answer(
        {"filters": [{"field": "failed_authenticity", "op": "eq", "value": True},
                     {"field": "days_to_expiry", "op": "gte", "value": 0}]})
    assert not O.DeepAgentOrchestrator._empty_is_the_answer(
        {"filters": [{"field": "issuer", "op": "contains", "value": "Acme"}]})
