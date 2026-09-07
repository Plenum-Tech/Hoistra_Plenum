"""Which actions a compliance answer offers, and on which rows.

Offers are computed from row state rather than proposed by the model: a "send renewal"
button on a row with nothing to renew is worse than no button at all.
"""

from src.agents.orchestrator import DeepAgentOrchestrator as _O

_offers = _O._certificate_offers


def _kinds(rows, analysis=None):
    analysis = analysis or {"certificates": [{"id": r["id"]} for r in rows]}
    return {(o["cert_id"], o["kind"]) for o in _offers(analysis, rows)}


def test_unconfirmed_draft_is_offered_a_sign_off():
    rows = [{"id": "c1", "draft": True, "confirmed_by_pm": False}]
    assert ("c1", "confirm_draft") in _kinds(rows)


def test_draft_already_confirmed_is_not_offered_again():
    rows = [{"id": "c1", "draft": True, "confirmed_by_pm": True}]
    assert _kinds(rows) == set()


def test_lapsed_row_is_offered_a_renewal():
    rows = [{"id": "c1", "is_lapsed": True}]
    assert ("c1", "renewal_email") in _kinds(rows)


def test_expiring_row_is_offered_a_renewal():
    rows = [{"id": "c1", "is_expiring_soon": True, "days_to_expiry": 6}]
    offers = _offers({"certificates": [{"id": "c1"}]}, rows)
    assert offers[0]["kind"] == "renewal_email"
    assert "6 days" in offers[0]["reason"]


def test_healthy_row_is_offered_nothing():
    rows = [{"id": "c1", "is_lapsed": False, "is_expiring_soon": False, "draft": False}]
    assert _kinds(rows) == set()


def test_suspect_authenticity_is_offered_a_verification():
    rows = [{"id": "c1", "is_suspect_authenticity": True}]
    assert ("c1", "verify_now") in _kinds(rows)


def test_a_row_can_carry_several_offers():
    rows = [{"id": "c1", "draft": True, "is_lapsed": True, "is_forged": True}]
    assert _kinds(rows) == {
        ("c1", "confirm_draft"),
        ("c1", "renewal_email"),
        ("c1", "verify_now"),
    }


def test_rows_the_answer_never_names_get_no_offers():
    """The register is not a to-do list — only what the answer cites is actionable."""
    rows = [{"id": "c1", "is_lapsed": True}, {"id": "c2", "is_lapsed": True}]
    got = _offers({"certificates": [{"id": "c1"}]}, rows)
    assert [o["cert_id"] for o in got] == ["c1"]


def test_offers_come_from_pending_and_actions_too():
    rows = [{"id": "c1", "is_lapsed": True}, {"id": "c2", "draft": True}]
    analysis = {
        "certificates": [],
        "pending": [{"cert_id": "c2"}],
        "actions": [{"cert_ids": ["c1"]}],
    }
    assert _kinds(rows, analysis) == {("c1", "renewal_email"), ("c2", "confirm_draft")}


def test_an_ungrounded_id_is_never_offered_an_action():
    rows = [{"id": "c1", "is_lapsed": True}]
    analysis = {"certificates": [{"id": "ghost"}]}
    assert _offers(analysis, rows) == []
