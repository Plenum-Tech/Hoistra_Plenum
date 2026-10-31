"""Runs (22 Oct 2026), no database: serial codes, who may curate or change failure modes, which runs a caller sees, and
reading an Excel / CSV import (headers in the coding agent's words, rows with a question code add an expected answer)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest

from trace_lab import labelling, runs
from trace_lab.review_errors import Forbidden, Invalid, NotFound

ORG = str(uuid.uuid4())


def member(role: str = "admin", *, status: str = "active", manages: bool = True, all_companies: bool = False,
           org: str | None = ORG) -> labelling.Member:
    p = SimpleNamespace(user_id=uuid.uuid4(), email="x@example.com", role="admin", organization_id=uuid.UUID(ORG))
    return labelling.Member(p, org, str(p.user_id), "x@example.com", role, status, manages, all_companies)


def test_codes_read_and_write_both_ways():
    assert runs.run_code(3) == "R3" and runs.question_code(3, 12) == "R3-12"
    assert runs.parse_run_code(" r7 ") == 7 and runs.parse_question_code("R7-1") == (7, 1)
    for bad in ("", "7", "R", "R-1", "R1-", "Q1-1", "R1-1-1"):
        with pytest.raises(NotFound):
            runs.parse_question_code(bad)
    with pytest.raises(NotFound):
        runs.parse_run_code("R1-1")


def test_who_sees_which_runs():
    where, p = runs._scope(member(all_companies=True, org=None))
    assert (where, p) == ("TRUE", {}), "the platform team and the consultant see every company"
    where, p = runs._scope(member(all_companies=True, org=None), company=ORG)
    assert "organization_id" in where and p == {"scope_org": ORG}
    where, p = runs._scope(member())
    assert "organization_id = CAST(:scope_org AS uuid)" in where and p == {"scope_org": ORG}


def test_curators_and_taxonomists():
    assert runs.can_curate(member()) and runs.can_curate(member("consultant", manages=False, all_companies=True))
    assert not runs.can_curate(member("domain_expert", manages=False))
    with pytest.raises(Forbidden):
        runs.require_curator(member("tester", manages=False))
    runs.require_taxonomist(member("consultant", manages=False, all_companies=True))
    with pytest.raises(Forbidden):
        runs.require_taxonomist(member())                       # a company admin does not change the shared taxonomy
    with pytest.raises(Forbidden):
        runs.require_platform(member("consultant", manages=False, all_companies=True))


def test_a_new_question_is_validated_and_follow_ups_split():
    q = runs._new_question({"question": "  What is the status of order 3722? ", "followups": "Double-check\n\n  Again ",
                            "role": "shopper", "expected": "Placed"}, 1)
    assert q["question"] == "What is the status of order 3722?" and q["followups"] == ["Double-check", "Again"]
    assert q["role"] == "shopper" and q["expected"] == "Placed" and q["intent"] is None
    with pytest.raises(Invalid):
        runs._new_question({"question": "  "}, 2)
    with pytest.raises(Invalid):
        runs._new_question({"question": "x" * 4001}, 3)


def test_an_import_reads_the_coding_agents_headers():
    csv_bytes = ("Question code,Expected outcome,Evidence\n"
                 "R2-1,All blocked vendors listed,\"{\"\"reference\"\": \"\"SELECT 1\"\", \"\"type\"\": \"\"sql\"\"}\"\n"
                 ",,\n"
                 "R9-9,Someone,plain text evidence\n").encode()
    rows, problems = runs.parse_rows("expected.csv", csv_bytes)
    assert [r["code"] for r in rows] == ["R2-1", "R9-9"] and problems == []
    assert rows[0]["evidence"] == {"reference": "SELECT 1", "type": "sql"}
    assert rows[1]["evidence"] == {"reference": "plain text evidence"}
    new = ("Opening message,Role,Intent,Expected answer,Follow-ups,Scenario id\n"
           "What is the status of my order 3722?,shopper,order status,Report placed,Please double-check,support-0011\n"
           ",shopper,,,,\n").encode()
    rows, problems = runs.parse_rows("batch.csv", new)
    assert rows[0]["question"].startswith("What is the status") and rows[0]["scenario_ref"] == "support-0011"
    assert rows[0]["role"] == "shopper" and rows[0]["expected"] == "Report placed"
    assert problems == [{"row": 3, "error": "This row has neither a question nor a question code."}]
    with pytest.raises(Invalid):
        runs.parse_rows("x.csv", b"name,value\na,b\n")
