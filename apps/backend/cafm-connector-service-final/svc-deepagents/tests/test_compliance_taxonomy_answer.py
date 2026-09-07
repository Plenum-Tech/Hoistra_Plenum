"""The answer to "what is required?" — both duties, and nothing dropped.

The Country Pack branch existed but rendered a flat list capped at 30 entries. The UK pack
holds 55 types, so a PM asking what UK regulation obliges them to hold was shown 30 of them
with 25 hidden behind "…and 25 more", in one undifferentiated run that never separated the
building owner's duty from the contractor's.

Both of those matter. The cap hides exactly the obligations with no certificate on file — the
ones worth knowing about. And Building and Vendor are duties on different parties: the owner
holds the certificate, the contractor holds the accreditation that lets them issue it.
"""
from __future__ import annotations

import json

import pytest

from src.agents.orchestrator import DeepAgentOrchestrator as O
from src.agents.compliance_answer import (
    _frequency_label,
    build_deterministic_compliance_answer,
)

QUESTION = (
    "what are the building and vendor regulations and certifications required as mandatory "
    "in the UK building management regulation?"
)


def _type(code, scope, trade, name=None, months=12, reg="Some Act 1998"):
    return {
        "certificate_type_code": code,
        "certificate_type_name": name or code.replace("_", " ").title(),
        "certificate_scope": scope,
        "trade_category": trade,
        "frequency_months": months,
        "regulation_reference": reg,
    }


def _pack(types, trade=None):
    payload = {"types": types}
    if trade:
        payload["trade_category"] = trade
    return [{"tool": "list_country_pack", "input": {"country_code": "UK"}, "output": payload}]


#: A pack the size of the real one, so the old 30-cap would bite.
FULL = (
    [_type(f"B_{i}", "Building", ["Fire", "Electrical", "Gas", "Water", "Energy"][i % 5])
     for i in range(27)]
    + [_type(f"V_{i}", "Vendor", ["Fire", "Electrical", "Gas", "Asbestos", "Security"][i % 5])
       for i in range(28)]
)


class TestNothingIsDropped:
    def test_every_type_in_a_55_type_pack_is_listed(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        for t in FULL:
            assert t["certificate_type_code"] in out, f"{t['certificate_type_code']} was dropped"

    def test_the_old_truncation_notice_is_gone(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "more in the Country Pack" not in out

    def test_the_counts_match_what_is_listed(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "**55**" in out
        assert "**27** building" in out
        assert "**28** vendor" in out


class TestTwoDuties:
    def test_building_and_vendor_get_their_own_sections(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "### Building certificates" in out
        assert "### Vendor accreditations" in out
        assert out.index("### Building certificates") < out.index("### Vendor accreditations")

    def test_each_section_names_who_holds_the_duty(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "owner / occupier" in out
        assert "contractor" in out.lower()

    def test_a_building_only_pack_omits_the_vendor_heading(self):
        out = build_deterministic_compliance_answer(
            QUESTION, _pack([t for t in FULL if t["certificate_scope"] == "Building"])
        )
        assert "### Building certificates" in out
        assert "### Vendor accreditations" not in out

    def test_types_are_grouped_by_trade(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        for trade in ("Fire", "Electrical", "Gas"):
            assert f"**{trade}**" in out


class TestSubstance:
    def test_each_type_carries_its_regulation(self):
        out = build_deterministic_compliance_answer(
            QUESTION,
            _pack([_type("FRA", "Building", "Fire", reg="Regulatory Reform (Fire Safety) Order 2005")]),
        )
        assert "Regulatory Reform (Fire Safety) Order 2005" in out

    def test_each_type_carries_its_interval(self):
        out = build_deterministic_compliance_answer(
            QUESTION, _pack([_type("LOLER", "Building", "Lifts", months=6)])
        )
        assert "6-monthly" in out

    def test_it_says_the_list_is_the_law_not_the_register(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "independent of the certificates on file" in out
        assert "still required" in out

    def test_it_says_applicability_varies(self):
        # Without this the list reads as "your building owes all 55", which is false.
        out = build_deterministic_compliance_answer(QUESTION, _pack(FULL))
        assert "Applicability varies" in out

    @pytest.mark.parametrize(
        "months,label",
        [(1, "Monthly"), (6, "6-monthly"), (12, "Annual"), (60, "5-yearly"),
         (None, "As required"), (0, "As required"), ("nonsense", "As required"), (9, "9-monthly")],
    )
    def test_frequency_labels(self, months, label):
        assert _frequency_label(months) == label


class TestEdges:
    def test_a_single_type_still_answers_as_one_type(self):
        # The existing single-type branch is the right shape for "is Gas Safe building or vendor?"
        out = build_deterministic_compliance_answer(
            "is gas safe a building or vendor certificate",
            _pack([_type("GAS_SAFE", "Vendor", "Gas")]),
        )
        assert "**Vendor**" in out

    def test_a_trade_filter_with_no_types_says_so(self):
        out = build_deterministic_compliance_answer(QUESTION, _pack([], trade="Nonsense"))
        # An empty pack falls through rather than printing an empty heading.
        assert out is None or "no certificate types" in out.lower()

    def test_the_register_path_is_untouched(self):
        # A lapsed-certificates question must still answer from the register.
        rows = [{
            "id": "1", "certificate_type_name": "EICR", "status": "Lapsed",
            "days_to_expiry": -10, "expiry_date": "2026-01-01", "building_name": "Tower A",
        }]
        out = build_deterministic_compliance_answer(
            "which building certificates are lapsed",
            [{"tool": "list_building_certificates", "input": {}, "output": {"certificates": rows}}],
        )
        assert "lapsed (expired) building certificate" in out
        # The renewal guidance may mention the pack; what must not appear is the taxonomy
        # answer itself, which would mean a register question was answered from the pack.
        assert "### Building certificates" not in out
        assert "Applicability varies" not in out


# --- Context budget: the pack must survive to the analyst -----------------------------

class TestPackSurvivesTheContextBudget:
    """The pack was being truncated out of the very answer it is the source for.

    _compliance_data_json sliced the finished JSON string at 90k. The extras are appended
    after the register, so on a real portfolio the cut landed inside the pack: 26 of 55 types
    reached the model and the coverage report reached it not at all. The model then supplied
    the missing types from its own knowledge of UK compliance, which is why a 28-type vendor
    pack was reported as 31 with two types absent — a failure that looks like sloppy counting
    and is actually an absent input.
    """

    @staticmethod
    def _pack(n_building=27, n_vendor=28):
        types = []
        for i in range(n_building):
            types.append({
                "certificate_type_code": f"B{i:03d}",
                "certificate_type_name": f"Building type {i}",
                "certificate_scope": "Building",
                "trade_category": "Fire" if i % 2 else "Water",
                "regulation_reference": "Some Regulations 2005",
                "frequency_months": 12,
                # The bulk a real pack row carries and an answer never cites.
                "evidence_fields": "x" * 900,
                "alert_thresholds": {"current_gt": 90, "lapsed_lte": 7},
            })
        for i in range(n_vendor):
            types.append({
                "certificate_type_code": f"V{i:03d}",
                "certificate_type_name": f"Vendor type {i}",
                "certificate_scope": "Vendor",
                "trade_category": "Water" if i % 3 else "Fire",
                "regulation_reference": "Some Regulations 2005",
                "frequency_months": 36,
                "evidence_fields": "x" * 900,
                "alert_thresholds": {"current_gt": 90, "lapsed_lte": 7},
            })
        return {"ok": True, "count": len(types), "country_code": "UK", "types": types}

    @staticmethod
    def _fat_register(n=26):
        return {"ok": True, "count": n, "certificates": [
            {"id": f"id-{i}", "certificate_type_code": "EICR", "cert_scope": "Building",
             "country_code": "UK", "filler": "y" * 2600}
            for i in range(n)
        ]}

    def _tool_calls(self):
        return [
            {"tool": "list_compliance_certificates", "output": self._fat_register()},
            {"tool": "list_country_pack", "output": self._pack()},
            {"tool": "get_compliance_coverage", "output": {"ok": True, "buildings": {"buildings": [{"site_id": "s"}]}}},
        ]

    def test_every_pack_type_reaches_the_analyst(self):
        o = O.__new__(O)
        raw = json.dumps(self._tool_calls(), default=str)
        assert len(raw) > 90000, "fixture must actually exceed the budget"
        out = o._compliance_data_json(self._tool_calls(), taxonomy=True)
        for code in [f"B{i:03d}" for i in range(27)] + [f"V{i:03d}" for i in range(28)]:
            assert f'"{code}"' in out, f"pack type {code} was truncated out of the answer's own source"

    def test_the_coverage_report_is_not_the_thing_that_gets_dropped(self):
        o = O.__new__(O)
        out = o._compliance_data_json(self._tool_calls(), taxonomy=True)
        assert "get_compliance_coverage" in out

    def test_the_result_is_still_valid_json(self):
        # The old path sliced mid-object and appended a note, so the payload the model
        # received did not parse.
        o = O.__new__(O)
        json.loads(o._compliance_data_json(self._tool_calls(), taxonomy=True))

    def test_pack_rows_are_compacted_not_dropped(self):
        o = O.__new__(O)
        out = o._compliance_data_json(self._tool_calls(), taxonomy=True)
        assert "evidence_fields" not in out, "bulk fields should be shed before whole types are"
        assert "regulation_reference" in out, "what the answer cites must survive"

    def test_a_register_question_keeps_working(self):
        o = O.__new__(O)
        out = o._compliance_data_json(self._tool_calls(), taxonomy=False)
        json.loads(out)
        assert len(out) <= 90000 + 4000


class TestPackFacts:
    """Counting is not reasoning: the totals are computed, not tallied by the model."""

    def _facts(self):
        tool_calls = [{"tool": "list_country_pack", "output": {
            "country_code": "UK",
            "types": [
                {"certificate_type_code": "FRA", "certificate_scope": "Building", "trade_category": "Fire"},
                {"certificate_type_code": "EICR", "certificate_scope": "Building", "trade_category": "Electrical"},
                {"certificate_type_code": "LCA", "certificate_scope": "Vendor", "trade_category": "Water"},
                {"certificate_type_code": "GAS_SAFE", "certificate_scope": "Vendor", "trade_category": "Gas"},
                {"certificate_type_code": "NICEIC", "certificate_scope": "Vendor", "trade_category": "Electrical"},
            ]}}]
        rows = [
            {"certificate_type_code": "FRA", "cert_scope": "Building", "country_code": "UK"},
            {"certificate_type_code": "GAS_SAFE", "cert_scope": "Vendor", "country_code": "UK"},
            {"certificate_type_code": "US_GL_INSURANCE", "cert_scope": "Vendor", "country_code": "US"},
        ]
        return O._pack_facts(tool_calls, rows)

    def test_totals_are_per_scope(self):
        f = self._facts()
        assert f["total_types"] == 5
        assert f["by_scope"]["Building"]["required_types"] == 2
        assert f["by_scope"]["Vendor"]["required_types"] == 3

    def test_held_plus_missing_equals_the_total(self):
        f = self._facts()
        for scope in ("Building", "Vendor"):
            s = f["by_scope"][scope]
            assert s["types_held"] + s["types_with_nothing_on_record"] == s["required_types"]

    def test_the_types_most_likely_to_be_dropped_are_named(self):
        f = self._facts()
        assert "LCA" in f["by_scope"]["Vendor"]["codes_with_nothing_on_record"]
        assert "NICEIC" in f["by_scope"]["Vendor"]["codes_with_nothing_on_record"]

    def test_a_foreign_row_cannot_pass_as_coverage(self):
        f = self._facts()
        assert f["rows_from_another_country"] == ["US"]
        assert [r["certificate_type_code"] for r in f["rows_not_matching_any_pack_type"]] == [
            "US_GL_INSURANCE"
        ]

    def test_no_pack_means_no_facts(self):
        assert O._pack_facts([{"tool": "list_compliance_certificates", "output": {}}], []) is None
