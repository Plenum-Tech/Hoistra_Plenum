"""The small pure pieces under the queue, the decisions and the bulk approval: the proposal as the page reads it, the
facts attached to a row, the asker's question, which label a decision writes, the bulk request's limits, and the
sentence table. No database, no network."""
from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

import pytest

import trace_lab_m4_fixtures as fx
from trace_lab import normalize, proposals
from trace_lab.review_errors import Forbidden, Invalid

ID = uuid.uuid4()
ROW = {"id": ID, "kind": "cell", "run_id": uuid.uuid4(), "turn_id": "turn-1", "organization_id": uuid.UUID(fx.ORG_A),
       "mode_id": uuid.uuid4(), "suggestion": "present", "annotation_ids": [], "draft": None, "quote": "12 open",
       "span_id": None, "field": "answer", "basis": "answer", "evidence": "Counted.", "status": "pending",
       "closed_reason": None, "flags": '{"spot_check_seed": "abcd"}', "usd": None,
       "created_at": datetime(2026, 10, 15, tzinfo=timezone.utc), "decided_by_email": None, "decided_at": None,
       "label_id": None}


def test_a_proposal_is_plain_json_with_its_ids_as_text():
    p = proposals.proposal_payload(ROW)
    assert p["id"] == str(ID) and p["run_id"] == str(ROW["run_id"]) and p["mode_id"] == str(ROW["mode_id"])
    assert p["organization_id"] == fx.ORG_A and p["span_id"] is None and p["label_id"] is None
    assert p["flags"] == {"spot_check_seed": "abcd"} and p["usd"] is None and p["notes"] == []
    assert p["created_at"] == "2026-10-15T00:00:00+00:00" and p["decided_at"] is None
    import json

    json.dumps(p)                                                    # nothing in it needs a custom encoder


def test_a_proposal_carries_what_was_read_apart_and_nothing_it_was_not_given():
    bare = proposals.proposal_payload(ROW)
    assert bare["question"] is None and bare["conversation_key"] is None and bare["base"] is None and bare["mode_name"] is None
    full = proposals.proposal_payload(dict(ROW, question="Q?", conversation_key="s:1", label_head="h1", mode_name="m",
                                           mode_definition="Present when.", usd="0.001200",
                                           annotation_ids=[ID], span_id=ID, label_id=ID), notes=[{"id": "n"}])
    assert (full["question"], full["conversation_key"], full["base"], full["mode_name"]) == ("Q?", "s:1", "h1", "m")
    assert full["usd"] == 0.0012 and full["annotation_ids"] == [str(ID)] and full["span_id"] == str(ID) and full["notes"] == [{"id": "n"}]


def test_a_grouping_proposal_has_no_company_turn_or_mode():
    p = proposals.proposal_payload(dict(ROW, kind="group", organization_id=None, turn_id=None, mode_id=None,
                                        annotation_ids=[ID]))
    assert (p["organization_id"], p["turn_id"], p["mode_id"]) == (None, None, None) and p["annotation_ids"] == [str(ID)]


def test_facts_attach_only_to_a_row_of_the_same_company():
    facts = {"t1": {"organization_id": fx.ORG_A, "question": "Q?", "conversation_key": "s:1"}}
    rows = [{"turn_id": "t1", "organization_id": uuid.UUID(fx.ORG_A)}, {"turn_id": "t1", "organization_id": fx.ORG_B},
            {"turn_id": "t2", "organization_id": fx.ORG_A}, {"turn_id": None, "organization_id": None}]
    out = proposals._with_facts(rows, facts)
    assert [(r["question"], r["conversation_key"]) for r in out] == [("Q?", "s:1"), (None, None), (None, None), (None, None)]
    assert "question" not in rows[0]                                  # the inputs are left alone


def test_the_question_is_the_askers_own_words_cut_and_redacted():
    wrapped = "context " * 50 + normalize.QUESTION_MARKER + "Why is the token Bearer abcdefghij0123456789xyz here?"
    q = proposals._question(wrapped)
    assert q.startswith("Why is the token") and "abcdefghij0123456789xyz" not in q
    assert len(proposals._question("x" * 5000)) == proposals.QUESTION_CHARS
    assert proposals._question(None) == "" and proposals._question("") == ""


@pytest.mark.parametrize("p,decision,present,expected", [
    ({"kind": "depth_scan", "suggestion": "present"}, "accept", None, (True, "accepted_suggestion")),
    ({"kind": "depth_scan", "suggestion": "present"}, "dismiss", None, (False, "rejected_suggestion")),
    ({"kind": "cell", "suggestion": "present"}, "accept", None, (True, "accepted_label_proposal")),
    ({"kind": "cell", "suggestion": "absent"}, "accept", None, (False, "accepted_label_proposal")),
    ({"kind": "cell", "suggestion": "present"}, "override", True, (True, "accepted_label_proposal")),
    ({"kind": "cell", "suggestion": "absent"}, "override", False, (False, "accepted_label_proposal")),
    ({"kind": "cell", "suggestion": "present"}, "override", False, (False, "human_structured_label")),
    ({"kind": "cell", "suggestion": "absent"}, "override", True, (True, "human_structured_label")),
])
def test_which_label_each_decision_writes(p, decision, present, expected):
    assert proposals._label_plan(p, decision, present) == expected


@pytest.mark.parametrize("p,decision,present", [({"kind": "cell", "suggestion": "present"}, "dismiss", None),
                                                ({"kind": "cell", "suggestion": "present"}, "override", None),
                                                ({"kind": "depth_scan", "suggestion": "present"}, "override", True),
                                                ({"kind": "depth_scan", "suggestion": "present"}, "approve", None)])
def test_a_decision_that_does_not_fit_is_refused_with_a_sentence(p, decision, present):
    with pytest.raises(Invalid) as e:
        proposals._label_plan(p, decision, present)
    assert e.value.reason == "decision_not_allowed" and e.value.extra["kind"] == p["kind"] and e.value.error.endswith(".")


def test_the_queue_kinds_and_statuses():
    assert proposals.QUEUE_KINDS == {"group": ["group"], "depth_scan": ["depth_scan"], "cell": ["cell"],
                                     "suggestion": ["depth_scan", "cell"]}
    assert proposals.QUEUE_STATUSES == ("pending", "accepted", "dismissed", "closed") and proposals.DECISIONS == (
        "accept", "dismiss", "override")


@pytest.mark.parametrize("who_role,org,expect", [("user", fx.ORG_A, Forbidden)])
async def test_a_plain_user_is_refused_before_anything_is_read(who_role, org, expect):
    admin = fx.admin()
    user = type(admin)(user_id=admin.user_id, email=admin.email, organization_id=admin.organization_id, role=who_role,
                       can_ingest=False, building_ids=None)
    for call in (lambda: proposals.queue(user, org, kind="cell"),
                 lambda: proposals.decide(user, org, str(uuid.uuid4()), decision="accept"),
                 lambda: proposals.bulk_check(user, org, action="accept", proposal_ids=[], served_at=datetime.now(timezone.utc)),
                 lambda: proposals.bulk_approve(user, org, action="accept", proposal_ids=[],
                                                served_at=datetime.now(timezone.utc), bases={})):
        with pytest.raises(expect) as e:
            await call()
        assert e.value.reason == "reviewers_only"


def test_a_bulk_request_names_its_limits():
    when = datetime(2026, 10, 15, tzinfo=timezone.utc)
    org, ids, served = proposals._bulk_request(fx.ORG_A, fx.admin(), "dismiss", [str(ID).upper(), "plain"], when)
    assert org == fx.ORG_A and ids == [str(ID), "plain"] and served == when
    with pytest.raises(Invalid) as e:
        proposals._bulk_request(None, fx.owner(), "accept", [], "2026-10-15")
    assert e.value.reason == "bad_served_at"
    with pytest.raises(Invalid) as e:
        proposals._bulk_request(None, fx.owner(), "approve", [], when)
    assert e.value.reason == "decision_not_allowed"


def test_every_new_reason_has_a_plain_sentence_the_page_can_show():
    for reason in ("proposal_not_found", "unknown_queue_kind", "unknown_queue_status", "decision_not_allowed",
                   "conflicts_with_label", "spot_check_incomplete", "bulk_refused", "bad_served_at"):
        sentence = proposals.SENTENCES[reason]
        assert sentence[0].isupper() and sentence.endswith(".") and not re.search(r"tracelab|plenum|_id|uuid|sql", sentence, re.I)


def test_what_a_suggestion_cost_is_left_out_unless_the_caller_is_the_platform_team():
    row = dict(ROW, usd="0.012300")
    assert proposals.proposal_payload(row)["usd"] == 0.0123
    assert proposals.proposal_payload(row, with_cost=True)["usd"] == 0.0123
    assert proposals.proposal_payload(row, with_cost=False)["usd"] is None


@pytest.mark.parametrize("decision", ["approve", "", None, "ACCEPT"])
async def test_an_unknown_decision_is_refused_before_anything_is_read(decision):
    """No database is configured here: only a refusal made before any read can answer."""
    for proposal_id in (str(uuid.uuid4()), "not-a-uuid"):
        with pytest.raises(Invalid) as e:
            await proposals.decide(fx.owner(), None, proposal_id, decision=decision)
        assert e.value.reason == "decision_not_allowed" and e.value.status == 422
