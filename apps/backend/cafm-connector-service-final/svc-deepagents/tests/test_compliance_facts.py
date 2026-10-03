"""The compliance analyst gets its counts from code, not from reading 90 KB of rows.

turn-aff1e9826ca44ec1 (2 Oct 2026): the first draft said eight lapsed certificates where the rows
held nine - the Manchester Town Hall DEC was missed - and the reviewer's revision cost 75 s."""
from __future__ import annotations

from src.agents.orchestrator import DeepAgentOrchestrator as O


def _row(i, scope, status, owner, typ, days, blocked=False):
    return {"id": f"id-{i}", "cert_scope": scope, "status": status, "certificate_type_code": typ, "days_to_expiry": days,
            "expiry_date": "2026-06-29" if days < 0 else "2026-11-15",
            "vendor_name": owner if scope == "Vendor" else None, "building_name": owner if scope != "Vendor" else None,
            "is_lapsed": status in ("Lapsed", "Expired") or days < 0, "is_expiring_soon": 0 <= days <= 90 and status not in ("Lapsed",),
            "is_blocked": blocked}


ROWS = [
    _row(1, "Vendor", "Lapsed", "Pennard Fire Services", "BAFE_SP203_1", -60, blocked=True),
    _row(2, "Vendor", "Lapsed", "Ostley Power Services", "NICEIC", -391, blocked=True),
    _row(3, "Vendor", "Current", "Apex Mechanical", "CHAS_SSIP", 23),
    _row(4, "Building", "Lapsed", "Bishopsgate Tower", "FRA", -95),
    _row(5, "Building", "Lapsed", "Manchester Town Hall", "DEC", -4750),
    _row(6, "Building", "Current", "Bishopsgate Tower", "EICR", 400),
]


def test_the_facts_block_carries_the_totals_the_analyst_must_not_recount():
    facts = O._compliance_facts(ROWS)
    assert facts.startswith("FACTS (computed by code")
    assert "- rows: 6 (3 building-scope, 3 vendor-scope)" in facts
    assert "- lapsed/expired: 4 total = 2 vendor accreditation(s) + 2 building certificate(s)" in facts
    assert "lapsed by vendor: Ostley Power Services: 1; Pennard Fire Services: 1" in facts
    assert "lapsed by building: Bishopsgate Tower: 1; Manchester Town Hall: 1" in facts
    assert "id-5 · DEC · Manchester Town Hall · expiry 2026-06-29" in facts      # the one the draft missed
    assert "- expiring within 90 days (not yet lapsed): 1 - id-3 · CHAS_SSIP · Apex Mechanical" in facts
    assert "- on a blocked vendor: 2 - Ostley Power Services; Pennard Fire Services" in facts
    assert "by status: Lapsed 4, Current 2" in facts
    assert facts.endswith("\n\n")


def test_no_rows_means_no_block_and_the_budget_is_bounded():
    assert O._compliance_facts([]) == "" and O._compliance_facts(None) == ""
    many = [_row(i, "Vendor", "Lapsed", f"V{i}", "T", -1) for i in range(O._FACTS_MAX_IDS + 5)]
    facts = O._compliance_facts(many)
    assert f"… and 5 more (all in the rows)" in facts
    assert O._ANALYST_MAX_TOKENS == 6000


def test_the_prompt_doc_tells_the_analyst_counts_are_given():
    assert "COUNTS ARE GIVEN, NOT COUNTED" in O._ANALYST_PROMPT
