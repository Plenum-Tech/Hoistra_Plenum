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



def _flagless(r):
    """The same row as another fetch path hands it: status and dates, no is_* flags."""
    return {k: v for k, v in r.items() if not k.startswith("is_")}


def test_the_facts_hold_without_the_flags_a_path_may_not_set():
    """The cron run of 4 Oct 2026: "by status: Lapsed 9" and "lapsed/expired: 0" in one block."""
    facts = O._compliance_facts([_flagless(r) for r in ROWS])
    assert "- lapsed/expired: 4 total = 2 vendor accreditation(s) + 2 building certificate(s)" in facts
    assert "- expiring within 90 days (not yet lapsed): 1 - id-3" in facts
    assert "- on a blocked vendor: 2" in facts or "- on a blocked vendor: 0" in facts  # blocked needs its own column
    # a row with only an expiry date in the past is lapsed too
    only_date = [{"id": "x", "cert_scope": "Building", "status": "Current", "expiry_date": "2025-01-01", "building_name": "B"}]
    assert "- lapsed/expired: 1 total" in O._compliance_facts(only_date)


def test_a_failed_revision_ships_the_register_counts_not_the_rejected_draft():
    draft = {"narrative": "No certificates are lapsed.", "kpis": [{"count": 0, "label": "Expired"}], "certificates": [{"id": "id-1"}]}
    out = O._facts_fallback(draft, [_flagless(r) for r in ROWS], {"verdict": "revise", "reason": "contradicts the rows"})
    assert out["narrative"].startswith("4 certificates have lapsed or expired out of 6 on the register; 1 more expire within 90 days")
    assert "DEC — Manchester Town Hall (expired 2026-06-29)" in out["narrative"]
    assert out["kpis"][0] == {"count": 4, "label": "Lapsed or expired", "sublabel": "status Lapsed/Expired or past expiry", "severity": "critical", "unit": "certificates", "cert_ids": ["id-1", "id-2", "id-4", "id-5"]}
    assert [g["owner"] for g in out["groups"]] == ["Pennard Fire Services", "Ostley Power Services", "Bishopsgate Tower", "Manchester Town Hall"]
    assert out["actions"][0]["title"].startswith("Renew or reassign: BAFE_SP203_1 — Pennard Fire Services")
    assert out["validation"]["review"]["revision"].startswith("failed to parse") and out["certificates"] == [{"id": "id-1"}]
    assert O._facts_fallback(draft, [], None) is None
