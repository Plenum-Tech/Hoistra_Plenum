"""The record-question engine (agents/ontology_qa.py): what reaches SQL, and who may read.

The engine's promise is that the model never writes SQL - it names concepts, states and
relations, and the SQL is compiled from the ontology with every value a bound parameter. These
tests hold that line without a database: identifiers are refused unless they are plain names,
values never appear in the SQL text, a plan naming something the ontology lacks is sent back
with the reason, and the caller's scope decides what may be read before anything is.
"""
from __future__ import annotations

import json
import uuid

import pytest

from src.agents import ontology_qa as oq
from src.services.principal import Principal

ONTO = {
    "schema": "plenum_scoped",
    "concepts": {
        "Asset": {"table": "assets", "key": "id", "label": "asset_name", "hub": True,
                  "identifiers": ["asset_code"], "search": ["asset_name", "asset_code"],
                  "attributes": {"id": "id", "asset_name": "asset_name", "asset_code": "asset_code",
                                 "status": "status", "installation_date": "installation_date"},
                  "states": {"active": {"attribute": "status", "in": ["Active"]}}},
        "Building": {"table": "buildings", "key": "building_id", "label": "name", "hub": True,
                     "identifiers": ["building_code"], "search": ["name"],
                     "attributes": {"building_id": "building_id", "name": "name"}},
        "WorkOrder": {"table": "work_orders", "key": "id", "label": "title", "identifiers": ["wo_code"],
                      "attributes": {"id": "id", "title": "title", "status": "status", "raised_at": "raised_at"},
                      "states": {"open": {"attribute": "status", "not_in": ["Completed", "Closed"]}}},
    },
    "relations": {
        "ASSET_BUILDING": {"from": "Asset", "to": "Building", "cardinality": "many_to_one",
                           "join": ["from.building_id = to.building_id"]},
        "WORK_ORDER_ASSET": {"from": "WorkOrder", "to": "Asset", "cardinality": "many_to_one",
                             "join": ["from.asset_id = to.id"]},
    },
    "derived": {},
}


def onto():
    o = oq.Ontology(json.loads(json.dumps(ONTO)))
    o.col_types = {("assets", "installation_date"): "date", ("assets", "status"): "character varying",
                   ("work_orders", "status"): "character varying"}
    return o


class TestNothingUnsafeReachesSql:
    def test_identifiers_must_be_plain_names(self):
        assert oq.qi("asset_code") == '"asset_code"'
        for bad in ('x"; DROP TABLE assets; --', "a b", "", "1abc", "assets.id"):
            with pytest.raises(oq.OntologyError):
                oq.qi(bad)

    def test_values_are_bound_never_written_into_the_sql(self):
        o, p = onto(), oq.Params()
        sql = oq.compile_condition(o, o.concepts["Asset"], "f",
                                   {"attribute": "asset_name", "contains": "Lift'); DELETE FROM assets; --"}, p)
        assert "DELETE" not in sql and ":p0" in sql
        assert "DELETE" in p.d["p0"]            # carried as a parameter, where it is only text

    def test_a_date_is_cast_and_a_relative_date_is_an_expression(self):
        o, p = onto(), oq.Params()
        a = oq.compile_condition(o, o.concepts["Asset"], "f", {"attribute": "installation_date", "lt": "2020-01-01"}, p)
        b = oq.compile_condition(o, o.concepts["Asset"], "f", {"attribute": "installation_date", "gt": "today-30d"}, p)
        assert "CAST(:p0 AS date)" in a
        assert "CURRENT_DATE - 30 * INTERVAL '1 day'" in b

    def test_a_state_compiles_from_its_values(self):
        o, p = onto(), oq.Params()
        sql = oq.compile_states(o, o.concepts["WorkOrder"], "w", ["open"], p)
        assert "<> ALL(CAST(:p0 AS text[]))" in sql and p.d["p0"] == ["completed", "closed"]

    def test_a_name_matches_loosely(self):
        assert oq.loose_pattern("Lift Asset-4471") == "Lift[^[:alnum:]]*Asset[^[:alnum:]]*4471"
        # a hyphen, an em dash or nothing between the words all match the same register name
        for typed in ("Boiler 2 - central plant", "Boiler 2 — central plant", "boiler_2 central-plant"):
            assert oq.loose_pattern(typed) == "Boiler[^[:alnum:]]*2[^[:alnum:]]*central[^[:alnum:]]*plant".replace(
                "Boiler", "Boiler" if typed[0] == "B" else "boiler")


class TestPlans:
    def plan(self, **over):
        raw = {"question_type": "single",
               "focus": {"concept": "Asset", "match": "Lift Asset-4471", "states": [], "filters": []},
               "constraints": [{"concept": "Building", "match": "Bishopsgate Tower"}],
               "include": [{"name": "open_wos", "concept": "WorkOrder", "states": ["open"]}]}
        raw.update(over)
        return oq.normalise_plan(raw)

    def test_the_lift_plan_is_valid_and_finds_its_paths(self):
        o = onto()
        assert oq.validate_plan(o, self.plan()) == []
        hops, _ = o.auto_path("Asset", "WorkOrder")
        assert [r.name for r, _ in hops] == ["WORK_ORDER_ASSET"]

    def test_an_unknown_concept_or_state_is_sent_back_with_the_valid_names(self):
        o = onto()
        errs = oq.validate_plan(o, self.plan(focus={"concept": "Lift", "match": "x"}))
        assert errs and "Valid: Asset, Building, WorkOrder" in errs[0]
        errs = oq.validate_plan(o, self.plan(include=[{"concept": "WorkOrder", "states": ["overdue"]}]))
        assert any("no state 'overdue'" in e for e in errs)

    def test_the_main_query_joins_the_building_and_binds_its_keys(self):
        o, p = onto(), oq.Params()
        plan = self.plan()
        plan["focus"]["keys"] = ["124925e2-ebbb-4d43-9798-b0dec4cd2380"]
        plan["constraints"][0]["keys"] = ["93b8800f-754c-5805-9bd6-f65c8d263fe8"]
        qb, where, _, paths = oq.build_main(o, plan, p)
        sql = qb.from_clause() + oq.where_sql(where)
        assert '"plenum_scoped"."assets" AS f JOIN "plenum_scoped"."buildings"' in sql
        assert "124925e2" not in sql and list(p.d.values())[0] == ["124925e2-ebbb-4d43-9798-b0dec4cd2380"]
        assert paths == ["Asset -> Building via ASSET_BUILDING"]


class TestScope:
    org = uuid.uuid4()

    def who(self, role="user", buildings=None):
        return Principal(uuid.uuid4(), "t@x", self.org, role, False, buildings)

    def test_no_caller_reads_nothing(self):
        with pytest.raises(oq.NotAllowed):
            oq.scope_settings(None)

    def test_a_user_on_no_building_reads_nothing(self):
        with pytest.raises(oq.NotAllowed):
            oq.scope_settings(self.who(buildings=()))

    def test_a_company_admin_is_held_to_their_company(self):
        s = dict(oq.scope_settings(self.who(role="admin")))
        assert s == {"app.udr_unrestricted": "0", "app.udr_org": str(self.org), "app.udr_buildings": ""}

    def test_a_user_is_held_to_their_buildings(self):
        b = uuid.uuid4()
        s = dict(oq.scope_settings(self.who(buildings=(b,))))
        assert s["app.udr_buildings"] == str(b) and s["app.udr_org"] == str(self.org)

    def test_every_read_goes_to_the_scoped_views(self):
        assert oq.SCOPED_SCHEMA == "plenum_scoped" and onto().schema == "plenum_scoped"

    @pytest.mark.asyncio
    async def test_the_tool_refuses_before_planning_when_nothing_may_be_read(self):
        called = []

        async def llm(_m):
            called.append(1)
            return "{}"
        out = await oq.answer_question("status of Lift Asset-4471", principal=self.who(buildings=()), llm=llm)
        assert out["ok"] is False and not called


def test_the_agents_that_answer_record_questions_carry_the_tool():
    from src.agents.wo_engine_agent import MAINTENANCE_READ_TOOLS, WO_ENGINE_SUBAGENT_TOOLS
    assert oq.answer_from_records in MAINTENANCE_READ_TOOLS
    assert oq.answer_from_records in WO_ENGINE_SUBAGENT_TOOLS


def test_the_table_descriptions_ship_with_the_engine():
    d = oq._table_descriptions()
    assert "assets" in d and d["assets"]["purpose"]


def test_an_assets_status_always_reads_what_is_pending():
    """The first live run planned the inspection and open work orders only, and answered
    "nothing pending" over a PPM due in nine days and four open energy anomalies."""
    d = json.loads(json.dumps(ONTO))
    d["concepts"]["PPMVisit"] = {"table": "ppm_visits", "key": "id", "label": "ppm_ref",
                                 "attributes": {"id": "id", "ppm_ref": "ppm_ref", "status": "status", "scheduled_date": "scheduled_date"},
                                 "states": {"scheduled": {"attribute": "status", "in": ["Scheduled"]},
                                            "completed": {"attribute": "status", "in": ["Completed"]}}}
    d["concepts"]["EnergyAnomaly"] = {"table": "energy_anomalies", "key": "id", "label": "anomaly_type",
                                      "attributes": {"id": "id", "anomaly_type": "anomaly_type", "status": "status"},
                                      "states": {"open": {"attribute": "status", "in": ["open"]}}}
    d["relations"]["PPM_VISIT_ASSET"] = {"from": "PPMVisit", "to": "Asset", "join": ["from.asset_id = to.id"]}
    d["relations"]["ENERGY_ANOMALY_ASSET"] = {"from": "EnergyAnomaly", "to": "Asset", "join": ["from.asset_id = to.id"]}
    o = oq.Ontology(d)
    plan = oq.normalise_plan({"question_type": "single", "focus": {"concept": "Asset", "match": "Boiler 2"},
                              "include": [{"name": "open_wos", "concept": "WorkOrder", "states": ["open"]}]})
    plan["focus"]["keys"] = ["a1"]
    added = oq.ensure_status_includes(o, plan)
    assert added == ["ppm_not_done", "last_ppm_done", "open_energy_anomalies"]
    assert oq.validate_plan(o, plan) == []
    # a list question is left as planned
    lst = oq.normalise_plan({"question_type": "list", "focus": {"concept": "Asset"}})
    assert oq.ensure_status_includes(o, lst) == []


# ── what a certificate reaches ───────────────────────────────────────────────────────────

def _cert_onto():
    d = json.loads(json.dumps(ONTO))
    d["concepts"]["Vendor"] = {"table": "vendors", "key": "id", "label": "vendor_name",
                               "attributes": {"id": "id", "vendor_name": "vendor_name"}}
    d["concepts"]["ComplianceCertificate"] = {
        "table": "compliance_certificates", "key": "id", "label": "certificate_number",
        "attributes": {"id": "id", "certificate_number": "certificate_number", "cert_scope": "cert_scope",
                       "status": "status", "expiry_date": "expiry_date"},
        "states": {"lapsed": {"attribute": "status", "in": ["Lapsed"]}}}
    d["concepts"]["EnergyAnomaly"] = {"table": "energy_anomalies", "key": "id", "label": "anomaly_type",
                                      "attributes": {"id": "id", "anomaly_type": "anomaly_type", "status": "status"},
                                      "states": {"open": {"attribute": "status", "in": ["open"]}}}
    d["relations"].update({
        "ASSET_VENDOR": {"from": "Asset", "to": "Vendor", "join": ["from.vendor_id = to.id"], "fill": 1.0},
        "COMPLIANCE_CERTIFICATE_ASSET": {"from": "ComplianceCertificate", "to": "Asset", "join": ["from.asset_id = to.id"]},
        "COMPLIANCE_CERTIFICATE_VENDOR": {"from": "ComplianceCertificate", "to": "Vendor", "join": ["from.vendor_id = to.id"]},
        "COMPLIANCE_CERTIFICATE_BUILDING": {"from": "ComplianceCertificate", "to": "Building", "join": ["from.building_id = to.building_id"]},
        "WORK_ORDER_VENDOR": {"from": "WorkOrder", "to": "Vendor", "join": ["from.vendor_id = to.id"]},
        "ENERGY_ANOMALY_ASSET": {"from": "EnergyAnomaly", "to": "Asset", "join": ["from.asset_id = to.id"], "fill": 0.3},
        "ENERGY_ANOMALY_BUILDING": {"from": "EnergyAnomaly", "to": "Building", "join": ["from.building_id = to.building_id"]},
    })
    d["derived"] = {"BLOCKS": {"from": "ComplianceCertificate", "to": "WorkOrder", "alternatives": [
        {"via": ["COMPLIANCE_CERTIFICATE_ASSET", "WORK_ORDER_ASSET"], "label": "same asset",
         "when": {"attribute": "cert_scope", "in": ["Asset"]}},
        {"via": ["COMPLIANCE_CERTIFICATE_VENDOR", "WORK_ORDER_VENDOR"], "label": "same vendor",
         "when": {"attribute": "cert_scope", "in": ["Vendor"]}},
        {"via": ["COMPLIANCE_CERTIFICATE_BUILDING", "ASSET_BUILDING", "WORK_ORDER_ASSET"], "label": "same building",
         "when": {"attribute": "cert_scope", "in": ["Building"]}}]}}
    return oq.Ontology(d)


def test_a_vendor_certificate_reaches_the_assets_its_vendor_maintains():
    o = _cert_onto()
    added = oq.add_certificate_links(o)
    assert "CERTIFICATE_ASSETS" in added and "CERTIFICATE_ENERGY" in added
    via = {a["label"]: a["via"] for a in o.derived["CERTIFICATE_ASSETS"].alternatives}
    assert via["assets the certificate's vendor maintains"] == ["COMPLIANCE_CERTIFICATE_VENDOR", "ASSET_VENDOR"]
    assert via["the certificate's own asset"] == ["COMPLIANCE_CERTIFICATE_ASSET"]
    energy = {a["label"]: a["via"] for a in o.derived["CERTIFICATE_ENERGY"].alternatives}
    assert energy["the certificate's building"] == ["COMPLIANCE_CERTIFICATE_BUILDING", "ENERGY_ANOMALY_BUILDING"]
    # each alternative is selected by the certificate's own scope
    assert all(a["when"]["attribute"] == "cert_scope" for a in o.derived["CERTIFICATE_ASSETS"].alternatives)


def test_a_certificate_question_reads_what_it_reaches():
    o = _cert_onto()
    oq.add_certificate_links(o)
    plan = oq.normalise_plan({"question_type": "list", "focus": {"concept": "ComplianceCertificate", "states": ["lapsed"]}})
    added = oq.ensure_compliance_includes(o, plan)
    assert "linked_assets" in added and "open_energy_anomalies" in added and "asset_open_work_orders" in added
    assert oq.validate_plan(o, plan) == []


def test_the_reach_is_grouped_by_asset_and_kept_within_budget():
    recs = [
        {"attributes": {"certificate_number": "GAS-1", "status": "Lapsed", "cert_scope": "Vendor"},
         "related": {"linked_assets (Asset)": [{"asset_code": "B-1", "asset_name": "Boiler 1", "via Vendor": "Corvane"}],
                     "asset_open_work_orders (WorkOrder)": [{"wo_code": "WO-9", "status": "Draft", "title": "burner",
                                                             "via Asset": "Boiler 1"}],
                     "open_energy_anomalies (EnergyAnomaly)": [{"anomaly_type": "baseline_drift", "metric_pct": "120",
                                                                "financial_gbp": "7605", "via Asset": "Boiler 1"}]}},
        {"attributes": {"certificate_number": "GAS-2", "status": "Current", "cert_scope": "Vendor"},
         "related": {"linked_assets (Asset)": [{"asset_code": "B-1", "asset_name": "Boiler 1"}]}},
        {"attributes": {"certificate_number": "EPC-1", "cert_scope": "Building"}, "related": {}},
    ]
    out = oq.shape_certificate_reach(recs)
    d = json.loads(out["records"])
    assert out["certificates_reaching_something"] == 2 and out["assets"] == 1, "one asset, listed once"
    assert d["certificates"][0]["vendor_name"] == "Corvane" and d["certificates"][0]["covers_assets"] == ["Boiler 1"]
    a = d["assets"]["Boiler 1"]
    assert a["summary"]["open_work_orders"] == 1 and a["summary"]["anomaly_cost_per_year"] == 7605
    assert a["open_energy_anomalies"] == ["baseline_drift +120%, GBP 7,605/yr"]
    small = oq.shape_certificate_reach(recs, budget=300)
    assert small["detail"] == "summaries" and "open_work_orders\": [" not in small["records"]


def test_both_compliance_answer_paths_hand_the_analyst_what_certificates_reach(monkeypatch):
    """The first live question after the links shipped went down the sub-agent path, which had
    no hook, and its answer named twelve certificates and none of what they affect."""
    import asyncio
    import inspect
    from src.agents import orchestrator as orch

    seen = {}

    async def fake(ids, principal=None, budget=0):
        seen["ids"], seen["budget"] = ids, budget
        return {"ok": True, "certificates_reaching_something": 1, "assets": 2, "records": "{}"}
    monkeypatch.setattr(oq, "linked_for_certificates", fake)
    steps = []

    async def step(s):
        steps.append(s)
    call = asyncio.run(orch.DeepAgentOrchestrator._certificate_reach_call(
        None, [{"id": "c1"}, {"id": "c2"}, {"no_id": 1}], "s1", step))
    assert call["tool"] == "certificate_reach" and seen["ids"] == ["c1", "c2"] and seen["budget"] == 25000
    assert steps and steps[0]["stage"] == "links"
    # and both paths call it
    src = inspect.getsource(orch.DeepAgentOrchestrator)
    assert src.count("await self._certificate_reach_call(") == 2


def _vendor_onto():
    d = json.loads(json.dumps(ONTO))
    d["concepts"]["Vendor"] = {"table": "vendors", "key": "id", "label": "vendor_name", "synonyms": ["contractor", "supplier"],
                               "attributes": {"id": "id", "vendor_name": "vendor_name"}}
    # Two links from work orders to vendors, the empty one listed first - as on hoistra_test.
    d["relations"]["WORK_ORDER_ASSIGNED_VENDOR"] = {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                                                    "join": ["from.assigned_vendor = to.id"], "fill": 0.0}
    d["relations"]["WORK_ORDER_VENDOR"] = {"from": "WorkOrder", "to": "Vendor", "cardinality": "many_to_one",
                                           "join": ["from.vendor_id = to.id"], "fill": 1.0}
    d["relations"]["WORK_ORDER_BUILDING"] = {"from": "WorkOrder", "to": "Building", "cardinality": "many_to_one",
                                             "join": ["from.building_id = to.building_id"], "fill": 1.0}
    return oq.Ontology(d)


def test_a_join_takes_the_link_that_is_filled_not_the_first_listed():
    o = _vendor_onto()
    hops, _ = o.auto_path("WorkOrder", "Vendor")
    assert [h[0].name for h in hops] == ["WORK_ORDER_VENDOR"]


def test_a_ranking_or_a_breakdown_is_counted_never_listed():
    o = _vendor_onto()

    def plan():
        return oq.normalise_plan({"question_type": "list", "focus": {"concept": "WorkOrder"},
                                  "constraints": [{"concept": "Building", "match": "Bishopsgate Tower"}],
                                  "include": [], "aggregate": None})
    p = plan()
    notes = oq.ensure_grouping(o, p, "List the vendors with the most work orders at Bishopsgate Tower, with the count for each.")
    assert p["aggregate"] == {"count_by": [{"concept": "Vendor", "attribute": "vendor_name"}]} and notes
    assert any(c["concept"] == "Vendor" for c in p["constraints"]) and p["question_type"] == "count"
    p = plan()
    oq.ensure_grouping(o, p, "How many work orders per contractor?")
    assert p["aggregate"]["count_by"][0]["concept"] == "Vendor"
    for q in ("Which assets at Bishopsgate Tower have open work orders, and who is the vendor on each?",
              "What is the most recent work order at Bishopsgate Tower?", "Show the open work orders at Bishopsgate Tower"):
        p = plan()
        assert oq.ensure_grouping(o, p, q) == [] and p["aggregate"] is None, q
    # A named record is explained, not counted.
    p = oq.normalise_plan({"focus": {"concept": "Vendor", "match": "Apex Mechanical"}, "constraints": [], "include": []})
    assert oq.ensure_grouping(o, p, "Which building has the most work orders for Apex Mechanical?") == []


def test_a_calendar_period_is_a_half_open_range_never_a_word_in_the_sql():
    o, p = onto(), oq.Params()
    o.col_types[("work_orders", "raised_at")] = "timestamp with time zone"
    wo = o.concepts["WorkOrder"]
    for cond in ({"attribute": "raised_at", "within": "last_month"}, {"attribute": "raised_at", "eq": "Last month"},
                 {"attribute": "raised_at", "between": ["last_month", "last_month"]}):
        sql = oq.compile_condition(o, wo, "f", cond, p)
        assert ">= (date_trunc('month', CURRENT_DATE) - INTERVAL '1 month')" in sql
        assert "< date_trunc('month', CURRENT_DATE)" in sql and "last" not in str(p.d).lower()
    sql = oq.compile_condition(o, wo, "f", {"attribute": "raised_at", "between": ["last_month_start", "last_month_end"]}, p)
    assert "INTERVAL '1 microsecond'" in sql and "last_month" not in str(p.d)
    with pytest.raises(oq.OntologyError):
        oq.compile_condition(o, wo, "f", {"attribute": "raised_at", "within": "the other day"}, p)


@pytest.mark.asyncio
async def test_an_empty_date_column_is_swapped_for_the_one_that_holds_the_dates(monkeypatch):
    d = json.loads(json.dumps(ONTO))
    d["concepts"]["WorkOrder"]["attributes"]["reported_at"] = "reported_at"
    o = oq.Ontology(d)
    filled = {("work_orders", "raised_at"): False, ("work_orders", "reported_at"): True}

    async def fake_filled(r, onto_, table, col):
        return filled.get((table, col), True)
    monkeypatch.setattr(oq, "_filled", fake_filled)
    plan = oq.normalise_plan({"focus": {"concept": "WorkOrder", "filters": [
        {"attribute": "raised_at", "op": "within", "value": "last_month"}]}, "constraints": [], "include": [],
        "aggregate": {"count_by": [{"concept": "WorkOrder", "attribute": "status"}]}})
    notes = await oq.fill_date_filters(None, o, plan)
    assert plan["focus"]["filters"][0]["attribute"] == "reported_at" and "reported_at" in notes[0]
    assert "Completed" in oq.finished_states_note(o, plan)[0]


def test_a_trade_is_the_category_before_its_separator():
    assert oq._trade("HVAC \u00b7 Terminal Units") == "HVAC"
    assert oq._trade("Life Safety \u2014 Fire Detection") == "Life Safety"
    assert oq._trade("Water Hygiene") == "Water Hygiene" and oq._trade(None) == "Unclassified"


def test_pending_rules_ask_for_the_open_work_by_trade_and_next_actions():
    assert "by trade" in oq.PENDING_RULES and "Next actions by vendor" in oq.PENDING_RULES and "| WO number | What | Trade | Vendor |" in oq.PENDING_RULES


def test_open_work_that_can_endanger_people_is_flagged_with_its_reason():
    def r(**kw):
        return oq.life_risk(kw)
    assert "fire" in r(category="Life Safety \u00b7 Fire Detection", title="Loop 2 earth fault")
    assert "carbon monoxide" in r(category="HVAC \u00b7 Boilers", title="Boiler 1 - flue gas CO up 30% to 150 ppm")
    assert "LOLER" in r(category="Vertical Transport", title="LOLER thorough examination for Lift Asset-4471")
    assert "Legionella" in r(category="Water Hygiene", title="Calorifier descale and TMV cartridge replacement")
    assert "standby" in r(category="Electrical \u00b7 Standby Power", title="monthly run test")
    # Filed under Water Hygiene, but a sealed heating loop: no Legionella or scald risk.
    assert r(category="Water Hygiene", title="Closed-system inhibitor top-up and dosing pot valve replacement") is None
    assert r(category="HVAC \u00b7 Terminal Units", title="FCU L4-12 not holding set point") is None
    assert "Risk to life" in oq.PENDING_RULES
