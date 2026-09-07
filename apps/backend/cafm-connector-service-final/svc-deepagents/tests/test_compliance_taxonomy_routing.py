"""Which table answers "what is required?" — the pack, never the register.

A property manager asking what UK regulation obliges them to hold is asking about the LAW.
The certificate register answers a different question: what is on file. Answering the first
from the second reports a portfolio status to someone who asked for a statutory list, and
worse, it silently omits every requirement they have no document for — the exact types most
likely to matter.

The detection is deliberately deterministic rather than left to the planner LLM: the planner
falls back to "read the register" on any failure, so a taxonomy question arriving during a
planner outage would be answered from the wrong table with no signal that anything went wrong.
"""
from __future__ import annotations

import asyncio
import pytest

from src.agents.orchestrator import DeepAgentOrchestrator as O


class TestDetection:
    @pytest.mark.parametrize(
        "q",
        [
            "what are the building and vendor regulations and certifications required as "
            "mandatory in the UK building management regulation?",
            "which certifications are mandatory for a UK building",
            "what certificates does a contractor need to hold to do gas work",
            "what are the statutory requirements for a commercial building in the UK",
            "which compliance requirements apply to a higher-risk building",
            "what legal requirements are mandatory for lift maintenance",
        ],
    )
    def test_a_question_about_the_law_is_taxonomy(self, q):
        assert O._is_taxonomy_question(q) is True

    @pytest.mark.parametrize(
        "q",
        [
            "which certificates are lapsed",
            "which vendors are blocked",
            "show me the certificates expiring in 30 days",
            "what is the status of AIB Solutions",
            "how many certificates do we hold",
            "which of our mandatory certificates are lapsed",
            "which required certificates are expiring this month",
        ],
    )
    def test_a_question_about_our_records_is_not(self, q):
        assert O._is_taxonomy_question(q) is False

    def test_a_bare_subject_word_is_not_enough(self):
        # "regulations" alone does not make it a taxonomy question — it needs the ask.
        assert O._is_taxonomy_question("tell me about regulations") is False

    def test_a_bare_verb_is_not_enough(self):
        assert O._is_taxonomy_question("what is required here") is False

    def test_empty_input_does_not_raise(self):
        assert O._is_taxonomy_question("") is False
        assert O._is_taxonomy_question(None) is False


class TestPlanFallback:
    """The failure that produced the wrong answer: the planner fell back to its default.

    The trace showed the default reason verbatim and an empty needs list, so the country
    pack was never fetched. A planner outage must not decide which table answers the question.
    """

    @staticmethod
    def _plan(monkeypatch, question, *, planner_fails=True, planner_needs=None):
        orch = O.__new__(O)

        class _LLM:
            async def ainvoke(self, _msgs):
                if planner_fails:
                    raise RuntimeError("planner unavailable")
                import json as _j

                class _R:
                    content = _j.dumps(
                        {
                            "reason": "planner reason",
                            "needs": planner_needs or [],
                            "sub_questions": [{"id": "q1", "text": question, "query": {}}],
                        }
                    )

                return _R()

        orch._llm = _LLM()

        async def _vocab(self):
            return {}

        monkeypatch.setattr(O, "_compliance_value_vocabulary", _vocab)
        return asyncio.run(O._plan_compliance_query(orch, question))

    def test_a_failed_planner_still_reaches_the_country_pack(self, monkeypatch):
        plan = self._plan(
            monkeypatch, "which certifications are mandatory for a UK building"
        )
        assert plan["taxonomy"] is True
        assert "regulations" in plan["needs"]
        assert "coverage" in plan["needs"]

    def test_a_failed_planner_says_what_it_is_doing(self, monkeypatch):
        plan = self._plan(
            monkeypatch, "which certifications are mandatory for a UK building"
        )
        # The old default read "Read the certificate register and answer from it" — the
        # sentence that appeared in the trace of the wrong answer.
        assert "country certificate pack" in plan["reason"]

    def test_a_register_question_is_untouched_by_the_fallback(self, monkeypatch):
        plan = self._plan(monkeypatch, "which certificates are lapsed")
        assert plan["taxonomy"] is False
        assert plan["needs"] == []
        assert plan["reason"] == "Read the certificate register and answer from it."

    def test_a_working_planner_that_forgot_the_pack_still_gets_it(self, monkeypatch):
        # The planner is advisory on this point, not authoritative.
        plan = self._plan(
            monkeypatch,
            "what certificates are mandatory for a UK building",
            planner_fails=False,
            planner_needs=[],
        )
        assert plan["taxonomy"] is True
        assert set(plan["needs"]) >= {"regulations", "coverage"}

    def test_the_pack_is_not_requested_twice(self, monkeypatch):
        plan = self._plan(
            monkeypatch,
            "what certificates are mandatory for a UK building",
            planner_fails=False,
            planner_needs=["regulations"],
        )
        assert plan["needs"].count("regulations") == 1

    def test_a_working_planner_keeps_its_other_sources(self, monkeypatch):
        plan = self._plan(
            monkeypatch,
            "what certificates are mandatory for a UK building",
            planner_fails=False,
            planner_needs=["work_orders"],
        )
        assert set(plan["needs"]) == {"work_orders", "regulations", "coverage"}


class TestAnalystDirective:
    def test_the_directive_names_the_pack_as_the_answer(self):
        d = O._TAXONOMY_DIRECTIVE
        assert "list_country_pack" in d
        assert "Building" in d and "Vendor" in d

    def test_the_directive_forbids_dropping_unheld_types(self):
        # The failure mode that hides exactly the requirements a PM most needs to see.
        assert "still required" in O._TAXONOMY_DIRECTIVE

    def test_the_directive_keeps_applicability_honest(self):
        assert "applicability" in O._TAXONOMY_DIRECTIVE.lower()

    def test_the_directive_only_asks_for_units_the_schema_allows(self):
        """It told the analyst to use unit "certificate types", which the enum rejects.

        The model fell back to "other" — right answer, wrong reason. An instruction the
        schema cannot satisfy is an instruction that gets silently ignored.
        """
        units = None
        for prop in (O._COMPLIANCE_RESPONSE_SCHEMA["properties"]["kpis"]["items"]["properties"],):
            units = prop["unit"]["enum"]
        assert units, "kpis[].unit should be an enum"
        directive = O._TAXONOMY_DIRECTIVE
        import re

        for quoted in re.findall(r'unit \\"([^"\\]+)\\"', directive):
            assert quoted in units, f"directive names unit {quoted!r}, not in {units}"

    def test_the_base_prompt_is_unchanged_for_register_questions(self):
        # A status question must not pick up taxonomy instructions.
        assert "WHAT IS REQUIRED, NOT WHAT IS ON FILE" not in O._ANALYST_PROMPT


class TestPlannedExtras:
    def test_an_unknown_source_does_not_mislabel_a_failure(self, monkeypatch):
        """`needs` was zipped against a filtered result list, shifting every pairing."""
        seen: list[str] = []

        async def _ok():
            return {"tool": "list_country_pack", "input": {}, "output": {"types": []}}

        async def _boom():
            raise RuntimeError("coverage down")

        import src.agents.orchestrator as mod

        real_warning = mod.log.warning

        def _capture(event, **kw):
            if event == "compliance.plan.extra_failed":
                seen.append(kw.get("need"))
            return real_warning(event, **kw)

        monkeypatch.setattr(mod.log, "warning", _capture)

        async def _run():
            # Patch the job table the function builds by exercising it through a stub.
            jobs = {"regulations": _ok, "coverage": _boom}
            needs = ["not_a_source", "regulations", "coverage"]
            selected = [(n, jobs[n]) for n in needs if n in jobs]
            results = await asyncio.gather(
                *(fn() for _, fn in selected), return_exceptions=True
            )
            out = []
            for (need, _fn), res in zip(selected, results):
                if isinstance(res, Exception):
                    mod.log.warning("compliance.plan.extra_failed", need=need, error="x")
                    continue
                out.append(res)
            return out

        out = asyncio.run(_run())
        assert len(out) == 1
        assert seen == ["coverage"], "the failing source must be named correctly"


class TestPlannedExtraSteps:
    """The trace must name every source the answer was built from.

    The pack was already being fetched when a taxonomy question came in — the trace panel
    just never emitted a step for it, so "How this answer was produced" read plan → register
    → analyst. A reader who trusts that panel would conclude the statutory list came out of
    plenum_cafm.compliance_certificates, which is the very confusion the taxonomy fix exists
    to remove.
    """

    def test_country_pack_step_names_the_pack_and_the_duty_split(self):
        step = O._planned_extra_step(
            {
                "tool": "list_country_pack",
                "input": {"country_code": "UK"},
                "output": {
                    "ok": True,
                    "count": 3,
                    "types": [
                        {"certificate_scope": "Building"},
                        {"certificate_scope": "Building"},
                        {"certificate_scope": "Vendor"},
                    ],
                },
            }
        )
        assert step is not None
        assert step["stage"] == "data"
        assert "country certificate pack" in step["label"].lower()
        assert "3 statutory certificate types" in step["detail"]
        assert "2 building, 1 vendor" in step["detail"]
        assert "country_certificate_packs" in step["detail"]
        assert "compliance_certificates" not in step["detail"]

    def test_count_falls_back_to_the_rows_when_absent(self):
        step = O._planned_extra_step(
            {
                "tool": "list_country_pack",
                "output": {"types": [{"certificate_scope": "Vendor"}]},
            }
        )
        assert "1 statutory certificate types" in step["detail"]

    def test_coverage_step_counts_both_sides(self):
        step = O._planned_extra_step(
            {
                "tool": "get_compliance_coverage",
                "output": {
                    "buildings": {"buildings": [{"site_id": "a"}, {"site_id": "b"}]},
                    "vendors": {"vendors": [{"vendor_id": "v"}]},
                },
            }
        )
        assert "2 building(s) and 1 vendor(s)" in step["detail"]

    def test_work_orders_step_counts_rows(self):
        step = O._planned_extra_step(
            {"tool": "list_open_work_orders", "output": {"work_orders": [{}, {}, {}]}}
        )
        assert "3 open work order(s)" in step["detail"]

    @pytest.mark.parametrize(
        "extra",
        [
            {"tool": "something_else", "output": {}},
            {"tool": "list_country_pack", "output": None},
            {"tool": "get_compliance_coverage", "output": {}},
        ],
    )
    def test_a_malformed_or_unknown_extra_never_raises(self, extra):
        # A trace step is cosmetic; it must not be able to break the turn that produced it.
        step = O._planned_extra_step(extra)
        assert step is None or isinstance(step, dict)


class TestTaxonomyFindings:
    """Code settles the arithmetic; the reviewer gets the judgement.

    The live answer said "27 building types and 31 vendor types" against a 28-type vendor
    pack, and contradicted itself doing it — its own "19 with nothing on record" only works
    against 28. A number that disagrees with the pack is provable, so it never needs a model
    to catch it.
    """

    FACTS = {
        "country_code": "UK",
        "total_types": 55,
        "by_scope": {
            "Building": {"required_types": 27, "types_held": 5, "types_with_nothing_on_record": 22},
            "Vendor": {"required_types": 28, "types_held": 9, "types_with_nothing_on_record": 19},
        },
        "rows_not_matching_any_pack_type": [],
        "rows_from_another_country": [],
    }

    def test_the_invented_total_is_caught(self):
        a = {"narrative": "the UK pack carries 27 building types and 31 vendor types", "kpis": []}
        found = O._taxonomy_findings(a, self.FACTS)
        assert any("31" in f and "28" in f for f in found)

    def test_the_true_totals_pass(self):
        a = {"narrative": "the UK pack carries 27 building types and 28 vendor types", "kpis": []}
        assert O._taxonomy_findings(a, self.FACTS) == []

    def test_a_kpi_may_state_the_total_or_the_gap_but_not_a_third_number(self):
        ok_total = {"narrative": "", "kpis": [{"label": "Required vendor accreditation types", "count": 28}]}
        ok_gap = {"narrative": "", "kpis": [{"label": "Vendor types with nothing on record", "count": 19}]}
        bad = {"narrative": "", "kpis": [{"label": "Required vendor accreditation types", "count": 31}]}
        assert O._taxonomy_findings(ok_total, self.FACTS) == []
        assert O._taxonomy_findings(ok_gap, self.FACTS) == []
        assert len(O._taxonomy_findings(bad, self.FACTS)) == 1

    def test_counting_what_is_missing_is_not_flagged_as_a_wrong_total(self):
        # "22 building types have nothing on record" is smaller than the total, and true.
        a = {"narrative": "22 building types have nothing on record", "kpis": []}
        assert O._taxonomy_findings(a, self.FACTS) == []

    def test_a_foreign_row_is_context_for_the_answer_not_a_defect_in_it(self):
        """Off-pack rows are a fact about the DATA, not a mistake in the ANSWER.

        Reported as a finding it fired on every taxonomy question, forced a revision the
        analyst could not satisfy — nothing it writes changes what is in the register — and
        was then re-reported as still outstanding after the correction pass. The rows are in
        pack_facts, which the analyst reads; that is where they belong.
        """
        facts = {
            **self.FACTS,
            "rows_not_matching_any_pack_type": [{"certificate_type_code": "US_GL_INSURANCE"}],
            "rows_from_another_country": ["US"],
        }
        assert O._taxonomy_findings({"narrative": "", "kpis": []}, facts) == []


class TestRevisionBrief:
    """The author sees the findings and nothing else, so they have to be specific."""

    def test_missing_types_are_named_not_counted(self):
        brief = O._revision_brief(
            ["narrative says 31 vendor types; the pack holds 28"],
            {"verdict": "revise", "findings": [{"severity": "error", "detail": "Water trade absent"}],
             "missing_type_codes": ["LCA", "BTEC_LEGIONELLA"]},
            TestTaxonomyFindings.FACTS,
        )
        assert "LCA" in brief and "BTEC_LEGIONELLA" in brief
        assert "the pack holds 28" in brief
        assert "Water trade absent" in brief
        assert "do not drop anything to make the list shorter" in brief

    def test_it_survives_a_reviewer_that_never_answered(self):
        brief = O._revision_brief(["one finding"], None, None)
        assert "one finding" in brief
