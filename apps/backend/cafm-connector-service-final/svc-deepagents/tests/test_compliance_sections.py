"""Multi-question decomposition: sections and the EL gate that keeps them honest."""

from src.agents.orchestrator import DeepAgentOrchestrator as _O

_validate = _O._validate_compliance_response
_brief = _O._sub_question_brief


def _rows():
    return [
        {"id": "c1", "status": "Lapsed"},
        {"id": "c2", "status": "Current"},
    ]


def test_sections_kept_when_planner_asked_for_them():
    subs = [{"id": "q1", "text": "lapsed certs"}, {"id": "q2", "text": "blocked vendors"}]
    analysis = {
        "narrative": "overall",
        "sections": [
            {"id": "q1", "question": "lapsed certs", "narrative": "one"},
            {"id": "q2", "question": "blocked vendors", "narrative": "two"},
        ],
        "certificates": [
            {"id": "c1", "status": "Lapsed", "sub_question_id": "q1"},
            {"id": "c2", "status": "Current", "sub_question_id": "q2"},
        ],
        "actions": [{"title": "chase", "cert_ids": ["c1"], "sub_question_id": "q2"}],
        "insights": [{"type": "risk", "text": "x", "sub_question_id": "q1"}],
    }
    out, issues = _validate(analysis, _rows(), subs)
    assert [s["id"] for s in out["sections"]] == ["q1", "q2"]
    assert out["validation"]["sections"] == ["q1", "q2"]
    assert not issues


def test_invented_section_id_is_dropped_and_orphans_reassigned():
    subs = [{"id": "q1", "text": "lapsed certs"}]
    analysis = {
        "narrative": "overall",
        "sections": [
            {"id": "q1", "question": "lapsed certs", "narrative": "one"},
            {"id": "q9", "question": "made up", "narrative": "two"},
        ],
        "certificates": [],
        "actions": [{"title": "chase", "cert_ids": [], "sub_question_id": "q9"}],
        "insights": [],
    }
    out, issues = _validate(analysis, _rows(), subs)
    assert [s["id"] for s in out["sections"]] == ["q1"]
    assert out["actions"][0]["sub_question_id"] == "q1"
    assert any("unplanned id" in i for i in issues)
    assert any("unknown sub_question_id" in i for i in issues)


def test_missing_sections_fall_back_to_one_section():
    """A model that skipped `sections` must not leave the interface with nothing to group."""
    subs = [{"id": "q1", "text": "lapsed certs"}]
    out, _ = _validate({"narrative": "overall", "certificates": []}, _rows(), subs)
    assert out["sections"] == [
        {"id": "q1", "question": "lapsed certs", "narrative": "overall"}
    ]


def test_sub_question_brief_lists_ids_for_the_analyst():
    text = _brief(
        [{"id": "q1", "text": "lapsed?"}, {"id": "q2", "text": "blocked?"}], "whole question"
    )
    assert text == "- q1: lapsed?\n- q2: blocked?"
    assert _brief(None, "just one thing") == "- q1: just one thing"


def _kpi(count, unit="certificates", ids=(), label="Current"):
    return {
        "count": count,
        "label": label,
        "sublabel": "",
        "severity": "ok",
        "unit": unit,
        "cert_ids": list(ids),
    }


def test_kpi_undercount_is_corrected_to_the_rows_it_cites():
    """The headline said 8 above a list of 9 and read as authoritative — catch it both ways."""
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(1, ids=["c1", "c2"])]}
    out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert out["kpis"][0]["count"] == 2
    assert any("said 1 but cites 2" in i for i in issues)


def test_kpi_overcount_is_corrected_too():
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(5, ids=["c1"])]}
    out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert out["kpis"][0]["count"] == 1
    assert any("said 5 but cites 1" in i for i in issues)


def test_kpi_ungrounded_ids_are_dropped_before_the_count_is_judged():
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(2, ids=["c1", "nope"])]}
    out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert out["kpis"][0]["cert_ids"] == ["c1"]
    assert out["kpis"][0]["count"] == 1
    assert any("unknown cert id" in i for i in issues)


def test_vendor_unit_is_flagged_not_corrected():
    """Two certificates can belong to one vendor, so a vendor count must not be overwritten."""
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(1, "vendors", ["c1", "c2"])]}
    out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert out["kpis"][0]["count"] == 1
    assert not any("corrected" in i for i in issues)


def test_vendor_count_above_its_certificates_is_flagged():
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(4, "vendors", ["c1"])]}
    _out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert any("counts 4 vendors from only 1" in i for i in issues)


def test_genuine_zero_needs_no_certificates():
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(0, ids=[])]}
    _out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert not issues


def test_untraceable_count_is_flagged():
    analysis = {"narrative": "n", "certificates": [], "kpis": [_kpi(3, "other", [])]}
    _out, issues = _validate(analysis, _rows(), [{"id": "q1", "text": "t"}])
    assert any("no certificate to trace it to" in i for i in issues)
