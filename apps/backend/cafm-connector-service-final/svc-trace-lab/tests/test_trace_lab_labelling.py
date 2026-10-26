"""Step labelling rules that need no database: who is active or manages, the label and note checks, which steps of an
answer can be labelled, the company view a member reads with, and a CSV a spreadsheet cannot run."""
from __future__ import annotations

import csv
import io
import uuid

import pytest
from fastapi import HTTPException

from src.services.principal import Principal
from trace_lab import labelling
from trace_lab.review_errors import Invalid

ORG = "11111111-1111-4111-8111-111111111111"


def person(role="user", org=ORG, buildings=(uuid.uuid4(),)):
    return Principal(user_id=uuid.uuid4(), email="p@x.test", organization_id=uuid.UUID(org), role=role, can_ingest=False,
                     building_ids=buildings)


def member(status="active", role="tester", manages=False):
    p = person()
    return labelling.Member(p, ORG, str(p.user_id), p.email, role, status, manages)


def test_a_selected_text_is_trimmed_kept_under_the_limit_and_empty_means_the_whole_step():
    assert labelling.validate_quote("  758 rows ") == "758 rows"
    assert labelling.validate_quote("   ") is None and labelling.validate_quote(None) is None
    with pytest.raises(Invalid):
        labelling.validate_quote("x" * 2001)


def test_no_access_pending_and_removed_people_are_refused_with_their_own_reason():
    with pytest.raises(HTTPException) as e:
        labelling.require_active(member("none"))
    assert e.value.detail["reason"] == "lab_no_access"

    with pytest.raises(HTTPException) as e:
        labelling.require_active(member("pending"))
    assert e.value.status_code == 403 and e.value.detail["reason"] == "lab_pending"
    with pytest.raises(HTTPException) as e:
        labelling.require_active(member("removed"))
    assert e.value.detail["reason"] == "lab_removed"
    assert labelling.require_active(member()).active


def test_only_admins_manage_people():
    with pytest.raises(HTTPException) as e:
        labelling.require_manager(member())
    assert e.value.status_code == 403 and e.value.detail["reason"] == "lab_admins_only"
    assert labelling.require_manager(member(role="admin", manages=True)).manages


def test_a_label_is_right_wrong_or_unsure_with_a_trimmed_note_under_the_limit():
    assert labelling.validate_label("wrong", "  Looked in the wrong register. ", " Records query ") == (
        "Looked in the wrong register.", "Records query")
    assert labelling.validate_label("right", "   ", None) == (None, None)
    with pytest.raises(Invalid):
        labelling.validate_label("maybe", None, None)
    with pytest.raises(Invalid):
        labelling.validate_label("unsure", "x" * 2001, None)


def test_only_steps_of_the_answer_can_be_labelled():
    trace = {"messages": [{"id": "t:root:user", "role": "user", "text": "Q?"}, {"id": "t:m1:call", "role": "model_call"},
                          {"id": "t:q1:query", "role": "db_query"}, {"id": "t:root:answer", "role": "assistant"}]}
    assert labelling._step_kind(trace, "t:q1:query") == "db_query"
    assert labelling._step_kind(trace, "t:root:answer") == "assistant"
    with pytest.raises(Invalid):
        labelling._step_kind(trace, "t:other:call")
    assert labelling._question_of(trace) == "Q?"


def test_an_agent_the_orchestrator_called_is_a_step_too():
    trace = {"id": "t", "messages": [{"id": "t:root:user", "role": "user"}],
             "groups": [{"span_id": "g1", "name": "compliance"}]}
    assert labelling._step_kind(trace, "t:g1:agent") == "agent"
    with pytest.raises(Invalid):
        labelling._step_kind(trace, "t:g2:agent")
    with pytest.raises(Invalid):
        labelling._step_kind(dict(trace, id="other"), "t:g1:agent")


def test_a_member_reads_the_whole_company_and_a_superadmin_reads_as_themselves():
    p = person()
    view = labelling.company_view(p, ORG)
    assert (view.role, str(view.organization_id), view.building_ids, view.user_id) == ("admin", ORG, None, p.user_id)
    boss = person(role="superadmin")
    assert labelling.company_view(boss, ORG) is boss


def test_what_should_happen_and_when_are_trimmed_optional_and_bounded():
    assert labelling.validate_expected("  count only this company's vendors ", "  the question names a vendor ") == (
        "count only this company's vendors", "the question names a vendor")
    assert labelling.validate_expected("   ", None) == (None, None)
    with pytest.raises(Invalid):
        labelling.validate_expected("x" * 2001, None)
    with pytest.raises(Invalid):
        labelling.validate_expected(None, "y" * 2001)


def test_a_group_is_two_to_thirty_different_steps_each_with_its_own_rule():
    two = [{"step_key": "t:a:query", "expected": " filter by company ", "only_if": "a vendor is named"},
           {"step_key": "t:b:query", "step_title": " count "}]
    out = labelling.validate_group(two)
    assert [(x["key"], x["title"], x["expected"], x["only_if"]) for x in out] == [
        ("t:a:query", None, "filter by company", "a vendor is named"), ("t:b:query", "count", None, None)]
    for bad in ([two[0]], [two[0], dict(two[0])], [{"step_key": f"t:{i}:q"} for i in range(31)], [two[0], {"step_key": ""}]):
        with pytest.raises(Invalid):
            labelling.validate_group(bad)


def test_the_csv_names_who_did_what_and_cannot_run_formulas():
    rows = [{"at": "2026-10-20T10:00:00Z", "question": "=HYPERLINK(1)", "step_title": "Records query", "step_kind": "db_query",
             "label": "wrong", "note": "+cmd", "reviewer_email": "exp@x.test", "reviewer_role": "domain_expert",
             "turn_id": "t1", "step_key": "t1:q1:query"}]
    rows[0]["quote"] = "-1 rows"
    parsed = list(csv.reader(io.StringIO(labelling.csv_text(rows))))
    assert parsed[0] == list(labelling.CSV_HEADER)
    assert parsed[1][1] == "'=HYPERLINK(1)" and parsed[1][6] == "'+cmd" and parsed[1][4] == "'-1 rows"
    assert parsed[1][5] == "Wrong" and parsed[1][7] == "exp@x.test" and parsed[1][8] == "Domain expert"
    assert parsed[0][-3:] == ["What should happen", "Only if", "Group"], "new columns go last, so old columns keep their place"
