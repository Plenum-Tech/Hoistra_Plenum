"""The answer takes the shape of the question, and a missing duty can be acted on.

Two complaints, one cause and one gap.

Every answer came back as a dashboard — narrative, KPI tiles, owner groups, charts,
priority actions, insights — whether the question was "where are our gaps" or "how many
types are in the pack". It read as a template being filled rather than a question being
answered, and it was: all nine zones were `required` in the response schema, so the model
had no way to return an answer that was just an answer.

Separately, the thing those answers were best at surfacing — a statutory duty with nothing
on record — was the one thing they offered no way to act on, because every action in the
system hangs off a certificate id and a missing certificate has none.
"""
from __future__ import annotations

import pytest

from src.agents.compliance_offers import OFFER_KINDS, offers_for_missing_type
from src.agents.orchestrator import DeepAgentOrchestrator as O


class TestOnlyTheAnswerIsRequired:
    def test_a_one_fact_answer_is_valid_with_no_zones(self):
        schema = O._COMPLIANCE_RESPONSE_SCHEMA
        assert schema["required"] == ["narrative", "sections"], (
            "requiring groups/kpis/actions forces a dashboard onto a one-line question"
        )

    @pytest.mark.parametrize(
        "zone",
        ["groups", "kpis", "actions", "insights", "certificates", "expiry_events", "pending"],
    )
    def test_every_other_zone_stays_offerable(self, zone):
        # Optional must not mean removed — a portfolio question still earns all of them.
        assert zone in O._COMPLIANCE_RESPONSE_SCHEMA["properties"]

    def test_the_prompt_tells_the_model_to_choose(self):
        p = O._ANALYST_PROMPT
        assert "DECIDE THE SHAPE OF THE ANSWER" in p
        assert "ONE FACT" in p
        # The instruction that used to make groups unconditional is now conditioned.
        assert "WHEN the question earns groups at all" in p

    def test_a_narrative_only_answer_still_renders_as_text(self):
        # The chat message is built from the typed answer; with no groups it must fall back
        # to the narrative rather than returning an empty string.
        out = O._compliance_plain_text(
            {"narrative": "The UK pack contains 55 certificate types: 27 building and 28 vendor."}
        )
        assert out.startswith("The UK pack contains 55")


class TestMissingDutyOffers:
    PACK_TYPE = {
        "certificate_type_code": "L8_RISK",
        "certificate_type_name": "Legionella Risk Assessment",
        "certificate_scope": "Building",
        "trade_category": "Water/Legionella",
        "regulation_reference": "ACOP L8 (4th ed); COSHH 2002",
        "frequency_months": 24,
    }

    def test_both_routes_are_offered(self):
        offers = offers_for_missing_type(self.PACK_TYPE)
        assert [o["kind"] for o in offers] == ["attach_certificate", "log_outstanding"]
        assert all(k in OFFER_KINDS for k in (o["kind"] for o in offers))

    def test_an_offer_carries_the_type_not_a_certificate_id(self):
        # There is no certificate yet; a cert_id here would point at a record that does not
        # exist, which is how a button ends up doing nothing.
        for o in offers_for_missing_type(self.PACK_TYPE):
            assert o["cert_id"] == ""
            assert o["certificate_type_code"] == "L8_RISK"
            assert o["cert_scope"] == "Building"
            assert o["frequency_months"] == 24

    def test_a_type_with_no_code_earns_nothing(self):
        assert offers_for_missing_type({"certificate_type_name": "Nameless"}) == []

    def test_offers_are_built_for_every_unevidenced_type_on_both_duties(self):
        tool_calls = [
            {
                "tool": "list_country_pack",
                "output": {
                    "types": [
                        self.PACK_TYPE,
                        {
                            "certificate_type_code": "LCA",
                            "certificate_type_name": "LCA Registration",
                            "certificate_scope": "Vendor",
                            "trade_category": "Water",
                        },
                        {
                            "certificate_type_code": "FRA",
                            "certificate_type_name": "Fire Risk Assessment",
                            "certificate_scope": "Building",
                            "trade_category": "Fire",
                        },
                    ]
                },
            }
        ]
        facts = {
            "by_scope": {
                "Building": {"codes_with_nothing_on_record": ["L8_RISK"]},
                "Vendor": {"codes_with_nothing_on_record": ["LCA"]},
            }
        }
        offers = O._missing_type_offers(tool_calls, facts)
        codes = {o["certificate_type_code"] for o in offers}
        assert codes == {"L8_RISK", "LCA"}, "FRA is held, so it earns no add offer"
        assert len(offers) == 4, "two routes per missing duty"

    def test_no_pack_means_no_offers(self):
        assert O._missing_type_offers([], {"by_scope": {}}) == []
        assert O._missing_type_offers([{"tool": "list_country_pack", "output": {}}], None) == []


class TestTaxonomyKpisAreNotDemandedToCiteCertificates:
    ROWS = [{"id": "r1", "status": "Lapsed"}]

    @staticmethod
    def _kpi(count: int, unit: str):
        return {
            "count": count,
            "label": "Required vendor certificate types",
            "sublabel": "",
            "severity": "info",
            "unit": unit,
            "cert_ids": [],
        }

    def test_a_pack_type_count_is_left_alone_on_a_taxonomy_answer(self):
        analysis = {
            "narrative": "n",
            "sections": [{"id": "q1", "question": "q", "narrative": "n"}],
            "kpis": [self._kpi(28, "other")],
        }
        _out, issues = O._validate_compliance_response(
            analysis, self.ROWS, [{"id": "q1", "text": "t"}], taxonomy=True
        )
        assert not any("trace it to" in i or "fetched" in i for i in issues), (
            "a required type has no certificate to cite — demanding one reported six "
            "contradictions against a correct answer"
        )

    def test_the_same_kpi_is_still_challenged_on_a_register_answer(self):
        analysis = {
            "narrative": "n",
            "sections": [{"id": "q1", "question": "q", "narrative": "n"}],
            "kpis": [self._kpi(3, "other")],
        }
        _out, issues = O._validate_compliance_response(
            analysis, self.ROWS, [{"id": "q1", "text": "t"}]
        )
        assert any("trace it to" in i for i in issues), (
            "outside a taxonomy answer an untraceable number is still a defect"
        )


class TestReviewerSeesTheEvidence:
    def test_the_register_index_names_owners_dates_and_scores(self):
        idx = O._register_index(
            [
                {
                    "certificate_type_code": "GAS_SAFE",
                    "vendor_name": "Apex Mechanical Services Ltd",
                    "expiry_date": "2026-08-31",
                    "status": "Lapsed",
                    "forensics_risk_score": 12,
                    "forensics_verdict": "pass",
                }
            ]
        )
        # Given only the pack counts, the reviewer called real vendor names and real expiry
        # dates unsupported, and its "corrections" damaged a correct answer.
        assert "Apex Mechanical Services Ltd" in idx
        assert "2026-08-31" in idx
        assert "Lapsed" in idx

    def test_an_empty_register_says_so_rather_than_going_blank(self):
        assert "no rows" in O._register_index([])

    def test_a_long_register_is_capped_and_says_what_it_hid(self):
        idx = O._register_index([{"certificate_type_code": f"T{i}"} for i in range(130)])
        assert "further row(s)" in idx


class TestWhichOneQuestions:
    """"Which building has the highest risk?" has one building as its answer.

    The first shape rules sorted questions by how much DATA they needed, so a superlative —
    which scans the estate to name one item — read as a portfolio question and came back as
    the full dashboard: six owner groups, five priority actions, a chart and a table, for a
    question whose answer is a building name and two sentences. The size of the answer is not
    the size of the scan.
    """

    def test_the_prompt_names_the_superlative_case(self):
        p = O._ANALYST_PROMPT
        assert "asks WHICH ONE" in p
        assert "NAME IT in the first sentence" in p

    def test_it_forbids_the_dashboard_that_was_returned(self):
        p = O._ANALYST_PROMPT
        assert "do not open a group for every one of them" in p
        assert "do not raise an action for each" in p

    def test_scanning_widely_does_not_make_it_a_portfolio_question(self):
        p = O._ANALYST_PROMPT
        assert "judge the shape by the size of the ANSWER" in p
        # And the portfolio rule now says what it is FOR, so the two do not overlap.
        assert "characterise a set, not to pick from it" in p


class TestMagnitudeKpisAreNotBoundedByRowCount:
    """A KPI in years is not a count of certificates.

    "Years since the oldest lapse: 19" was reported twice as a contradiction — "counts 19
    other from only 1 certificate(s)" and "claimed 19 of 6 fetched" — against an answer where
    both the 19 and the single cited row were right. `other` means the number is a magnitude,
    and a magnitude has no cardinality to check.
    """

    ROWS = [{"id": f"r{i}", "status": "Lapsed"} for i in range(6)]

    @staticmethod
    def _kpi(count, unit, ids):
        return {
            "count": count,
            "label": "Years since the oldest lapse",
            "sublabel": "",
            "severity": "warning",
            "unit": unit,
            "cert_ids": ids,
        }

    def _run(self, kpi, taxonomy=False):
        analysis = {
            "narrative": "n",
            "sections": [{"id": "q1", "question": "q", "narrative": "n"}],
            "kpis": [kpi],
        }
        _out, issues = O._validate_compliance_response(
            analysis, self.ROWS, [{"id": "q1", "text": "t"}], taxonomy=taxonomy
        )
        return issues

    def test_a_magnitude_exceeding_the_row_count_is_not_a_defect(self):
        issues = self._run(self._kpi(19, "other", ["r0"]))
        assert not any("from only" in i or "fetched" in i for i in issues), issues

    def test_a_vendor_count_is_still_bounded_by_its_certificates(self):
        issues = self._run(self._kpi(9, "vendors", ["r0"]))
        assert any("from only" in i for i in issues), (
            "nine vendors cannot come from one certificate — this check must survive"
        )

    def test_a_certificate_count_is_still_corrected_to_its_own_list(self):
        analysis = {
            "narrative": "n",
            "sections": [{"id": "q1", "question": "q", "narrative": "n"}],
            "kpis": [self._kpi(8, "certificates", ["r0", "r1"])],
        }
        out, issues = O._validate_compliance_response(
            analysis, self.ROWS, [{"id": "q1", "text": "t"}]
        )
        assert out["kpis"][0]["count"] == 2
        assert any("corrected" in i for i in issues)

    def test_a_magnitude_citing_nothing_at_all_is_still_flagged(self):
        issues = self._run(self._kpi(19, "other", []))
        assert any("trace it to" in i for i in issues)

    def test_but_not_on_a_taxonomy_answer_where_a_required_type_has_no_id(self):
        issues = self._run(self._kpi(28, "other", []), taxonomy=True)
        assert not any("trace it to" in i for i in issues)
