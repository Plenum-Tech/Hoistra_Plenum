"""Usage is billed to the company the work was done in.

Hussain, 29 Sep 2026: the Super Admin console showed 667 credits on "Your Organisation" and
0 on Plenum Technologies, where the work was actually done. Every query turn and ingest was
written against the caller's HOME company (principal.organization_id), although the workflow
routes had just resolved the company in view (a superadmin's "View as this company"). His
decision: credits land on the company worked in. The person's own company is kept in the
entry's detail (by_org), so who did it from where is not lost.

The ingestion audit trail had the same fault and gets the same rule.
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from src.services import usage_events

HOME = UUID("11111111-1111-1111-1111-111111111111")
ACTING = "22222222-2222-2222-2222-222222222222"
WORKFLOW = Path(__file__).resolve().parents[1] / "src" / "api" / "routes" / "workflow.py"


def test_the_company_in_view_is_billed():
    assert usage_events.billing_org(HOME, ACTING) == UUID(ACTING)


def test_with_no_company_in_view_the_callers_own_is_billed():
    assert usage_events.billing_org(HOME, None) == HOME
    assert usage_events.billing_org(HOME, "") == HOME


def test_a_malformed_company_in_view_falls_back_to_the_callers_own():
    assert usage_events.billing_org(HOME, "not-a-uuid") == HOME


def test_the_entry_says_whose_company_the_person_came_from_when_it_differs():
    assert usage_events.billed_detail({"session_id": "s"}, HOME, ACTING) == {
        "session_id": "s", "by_org": str(HOME)}
    assert usage_events.billed_detail({"session_id": "s"}, HOME, None) == {"session_id": "s"}


def test_no_workflow_receipt_is_written_against_the_home_company():
    # Every usage and ingestion-audit receipt in the workflow routes goes through billing_org.
    src = WORKFLOW.read_text(encoding="utf-8")
    calls = re.findall(r"usage_events\.record_(?:usage|ingestion_audit)\((.*?)\n\s*\)", src, flags=re.S)
    assert calls, "the workflow routes write receipts"
    for body in calls:
        assert "organization_id=principal.organization_id" not in body, body


def test_a_request_refused_for_having_no_session_is_not_billed():
    # Re-review, 29 Sep 2026: run-stateful wrote its receipt before its own 400 check, so a
    # refused request was billed — now to the company in view.
    src = WORKFLOW.read_text(encoding="utf-8")
    body = src.split("async def run_stateful", 1)[1].split("\nasync def ", 1)[0]
    assert body.index("if not sid:") < body.index("usage_events.record_usage(")
