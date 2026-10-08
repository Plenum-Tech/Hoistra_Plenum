"""The ingest reply and the Vendors panel count the same ingest the same way.

On 17 Sep 2026 a 16-page contract was ingested. The chat said:

    "extracted 14 parameters ... 5 not stated by the contract and defaulted"

and the Vendors panel, reading the same row, said:

    "12 of 17 terms were read from the signed contract — 71% source coverage"

Both were internally consistent and they counted different things. The chat counted keys in
the model's raw output — which includes `vendor_name` and `signed_date`, neither of which is
a contract TERM the panel lists. The panel counted `field_sources` entries whose value is
"contract", against every term it displays.

A reader has one ingest and two numbers for it, and no way to tell which is the real coverage.
`field_sources` is the record both surfaces should speak from: it is what the panel already
uses, what the engine writes, and what "the contract said this" actually means.
"""
from __future__ import annotations

from src.agents.contract_performance_single_door import term_counts


def test_the_counts_come_from_field_sources_not_from_the_models_raw_keys():
    # The WKU ingest exactly: 12 terms sourced from the document, 5 defaulted, plus a
    # building link that matched nothing and is not a term at all.
    body = {
        "extracted": {
            "contract_ref": "Facilities Management Service Level Agreement 2022",
            "vendor_name": "Western Kentucky University Department of Facilities Management",
            "signed_date": "2022-02-01",
            "sla_response_p1_hours": 1, "sla_response_p2_hours": 4,
            "sla_response_p3_hours": 48, "sla_response_p4_hours": 720,
            "sla_completion_p1_hours": 24, "sla_completion_p2_hours": 48,
            "sla_completion_p3_hours": 120, "sla_completion_p4_hours": 720,
            "kpi_clauses_json": {"a": 1}, "ppm_obligations_json": {"b": 2},
            "task_criticality_json": {"c": 3},
        },
        "defaults_used": [
            "labour_day_rate: Default", "overtime_rate: Default", "call_out_rate: Default",
            "payment_terms: Default", "parts_pricing_json: Default",
        ],
        "field_sources": {
            "contract_ref": "contract",
            "sla_response_p1_hours": "contract", "sla_response_p2_hours": "contract",
            "sla_response_p3_hours": "contract", "sla_response_p4_hours": "contract",
            "sla_completion_p1_hours": "contract", "sla_completion_p2_hours": "contract",
            "sla_completion_p3_hours": "contract", "sla_completion_p4_hours": "contract",
            "kpi_clauses_json": "contract", "ppm_obligations_json": "contract",
            "task_criticality_json": "contract",
            "labour_day_rate": "default", "overtime_rate": "default",
            "call_out_rate": "default", "payment_terms": "default",
            "parts_pricing_json": "default",
            "building_link": "no_match",
        },
    }
    read, total = term_counts(body)
    assert (read, total) == (12, 17), (
        "the panel says 12 of 17 from this same row; the reply must not say 14 of anything"
    )


def test_a_document_that_yielded_nothing_says_zero_rather_than_counting_the_vendors_name():
    # Moreland: a property management agreement, not a service contract. The model correctly
    # returned only the company's name — which is not a term, and must not read as coverage.
    body = {
        "extracted": {"vendor_name": "Moreland Estate Property Management Limited"},
        "defaults_used": ["labour_day_rate: Default"] * 16,
        "field_sources": {
            "contract_ref": "filename", "building_link": "no_match",
            **{f"f{i}": "default" for i in range(16)},
        },
    }
    read, total = term_counts(body)
    assert read == 0, "nothing in that document was a contract term"
    assert total == 17


def test_without_field_sources_it_falls_back_rather_than_reporting_a_wrong_number():
    # An older engine, or a partial response. Counting is better skipped than guessed at.
    read, total = term_counts({"extracted": {"a": 1, "b": 2}, "defaults_used": ["x"]})
    assert (read, total) == (None, None)


def test_the_summary_sentence_quotes_the_panels_numbers():
    from src.agents.contract_performance_single_door import ingest_summary

    s = ingest_summary(
        name="04_WKU.pdf", read=12, total=17, defaults=5, vendor_note="", params_id="abc",
    )
    assert "12 of 17" in s, s
    assert "5" in s
    assert "14" not in s, "the raw-key count is what disagreed with the panel"


def test_the_summary_says_plainly_when_a_document_yielded_no_terms():
    from src.agents.contract_performance_single_door import ingest_summary

    s = ingest_summary(name="Sample-Management-Contract.pdf", read=0, total=17, defaults=16,
                       vendor_note="", params_id="abc")
    assert "no contract terms" in s.lower(), s
    assert "may not be a service contract" in s.lower(), (
        "zero terms read is a different event from a few gaps, and the reader has to be told "
        "before they confirm 17 platform defaults as agreed values"
    )


def test_a_partial_read_is_not_described_as_if_it_found_nothing():
    from src.agents.contract_performance_single_door import ingest_summary

    s = ingest_summary(name="x.pdf", read=12, total=17, defaults=5, vendor_note="", params_id="a")
    assert "may not be a service contract" not in s.lower()
