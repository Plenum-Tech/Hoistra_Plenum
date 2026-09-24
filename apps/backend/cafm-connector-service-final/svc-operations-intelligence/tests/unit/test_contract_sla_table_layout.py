"""SLA hours stated in a column table, where the headers sit above the rows.

The Meridian Mechanical contract for Harbour Point (B-101) states all eight SLA times in a
four-column table. Extracted on 23 Sep 2026 it yielded null for every one of them, and the
row was confirmed with eight platform defaults standing in for terms the vendor never
agreed to — P1 completion recorded as 4 hours against the 8 the contract states, P4 as 168
against 336.

The heuristic's regex required the word "response" or "complet" to appear WITHIN the forty
characters following the priority code. That holds for prose ("P1 response: 2 hours") and
fails for a table, because a table names its columns once, above the rows. After "P1" comes
the priority's definition, then the bare numbers.

This is the text layout a PDF reader actually produces from that table.
"""
from __future__ import annotations

from src.engines.contract_performance.extract import _heuristic_contract_extract


MERIDIAN_SLA_TABLE = """
Mechanical Planned and Reactive Maintenance Contract
Contract reference: Mechanical PPM - Harbour Point . Vendor: Meridian Mechanical Ltd (MERI)

2. Service levels (SLA parameters)
Priority
Definition
Response time
Completion time
P1
Life safety / statutory / critical plant failure
2 hours
8 hours
P2
Business-critical comfort or operational continuity
8 hours
48 hours
P3
Routine / non-urgent
48 hours
120 hours
P4
Planned / cosmetic
120 hours
336 hours

3. Rates and payment
Labour day rate
GBP 380.00 per engineer day (8 hours)
Labour hourly rate
GBP 47.50 per hour
"""


def test_sla_table_with_headers_above_rows_yields_response_hours():
    out = _heuristic_contract_extract(MERIDIAN_SLA_TABLE)["extracted"]
    assert out.get("sla_response_p1_hours") == 2.0
    assert out.get("sla_response_p2_hours") == 8.0
    assert out.get("sla_response_p3_hours") == 48.0
    assert out.get("sla_response_p4_hours") == 120.0


def test_sla_table_with_headers_above_rows_yields_completion_hours():
    out = _heuristic_contract_extract(MERIDIAN_SLA_TABLE)["extracted"]
    assert out.get("sla_completion_p1_hours") == 8.0
    assert out.get("sla_completion_p2_hours") == 48.0
    assert out.get("sla_completion_p3_hours") == 120.0
    assert out.get("sla_completion_p4_hours") == 336.0


def test_prose_layout_still_parses():
    """The form the regex already handled must keep working."""
    prose = """
    Service levels
    P1 response within 1 hour and completion within 4 hours.
    P2 response within 4 hours and completion within 24 hours.
    """
    out = _heuristic_contract_extract(prose)["extracted"]
    assert out.get("sla_response_p1_hours") == 1.0
    assert out.get("sla_completion_p1_hours") == 4.0
    assert out.get("sla_response_p2_hours") == 4.0
    assert out.get("sla_completion_p2_hours") == 24.0


def test_a_priority_the_contract_does_not_state_stays_absent():
    """Silence is still null. The table below states P1 only."""
    partial = """
    Priority
    Response time
    Completion time
    P1
    Critical
    2 hours
    8 hours
    """
    out = _heuristic_contract_extract(partial)["extracted"]
    assert out.get("sla_response_p1_hours") == 2.0
    assert out.get("sla_response_p2_hours") is None
    assert out.get("sla_completion_p4_hours") is None


def test_a_priority_far_from_any_duration_is_not_guessed():
    """Positional reading is a table row's rule, not a whole document's.

    The UKRI FM contract (214k characters) names priorities in one schedule and durations in
    another, pages apart. Reading the next duration after a priority code there paired P4's
    response with an unrelated 48 hours and its completion with a 24 that came later still —
    a completion shorter than its own response, which is not a service level any contract
    states. A wrong number is worse than a null, because a null is visibly a gap.
    """
    scattered = (
        "P2 incidents shall be handled per the priority matrix at Schedule 4.\n"
        + "Filler clause carrying no duration at all. " * 40
        + "The Authority shall respond within 4 hours to a notice under clause 12.\n"
    )
    out = _heuristic_contract_extract(scattered)["extracted"]
    assert out.get("sla_response_p2_hours") is None
    assert out.get("sla_completion_p2_hours") is None


def test_model_nulls_are_filled_from_the_text_it_could_not_read():
    """The heuristic must supplement the model, not only replace it.

    `_extract_contract_fields` chooses ONE reader: Claude when an API key is set, the regex
    only when there is none. So on every real ingest the regex never runs, and a field the
    model returned null for stayed null even where the document states it plainly. That is
    exactly what happened to the Meridian contract — four fields read, eight SLA times null,
    and the regex that can read that table sat unused behind an elif.
    """
    from src.engines.contract_performance.extract import _fill_gaps_from_text

    model_said = {
        "contract_ref": "Mechanical PPM - Harbour Point",
        "labour_hour_rate": 47.5,
    }
    filled, conf = _fill_gaps_from_text(model_said, MERIDIAN_SLA_TABLE)

    # What the model did read is never overwritten.
    assert filled["labour_hour_rate"] == 47.5
    assert filled["contract_ref"] == "Mechanical PPM - Harbour Point"
    # What it missed is read from the text.
    assert filled["sla_response_p1_hours"] == 2.0
    assert filled["sla_completion_p4_hours"] == 336.0
    assert conf.get("sla_response_p1_hours") == "low"


def test_filling_gaps_needs_no_text_to_be_safe():
    from src.engines.contract_performance.extract import _fill_gaps_from_text

    filled, conf = _fill_gaps_from_text({"labour_hour_rate": 47.5}, None)
    assert filled == {"labour_hour_rate": 47.5}
    assert conf == {}
