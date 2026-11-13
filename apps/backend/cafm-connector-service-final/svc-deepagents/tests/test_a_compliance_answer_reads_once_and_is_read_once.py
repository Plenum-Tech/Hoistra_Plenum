"""A compliance answer reads each linked record once, shows that read as one line, and is not sent
to a second model to check prose the orchestrator rewrites anyway (12 Nov 2026).

From turn-6aee3e0dcf8a4b27, "Which certificates have lapsed at Bishopsgate Tower?" - 99 s, 31 rows in
the run trace, one tool call by the model:
  * 19 of the rows were what the certificates reach (assets, work orders, PPMs, plans, anomalies),
    read by code. Ten said the same thing twice: a certificate points at its asset by asset_id and by
    asset_code, at its building by building_id and by building_code, and each column was its own query;
  * each query was its own row, so one read looked like nineteen steps;
  * 89 of the 99 s were the analyst (38), a model review of its prose (27) and a rewrite (24) - of
    prose that is now only the orchestrator's draft.
"""
from __future__ import annotations

import asyncio
import inspect
import uuid

import pytest

from src.agents import ontology_qa as oq
from src.agents import trace
from src.http_client import caller_organization_id
from src.services.principal import Principal, caller_principal


# -- one link, whichever column reaches the record ------------------------------------------------

def _onto(asset_links=("asset_id", "asset_code"), building_links=("building_id", "building_code")):
    """Certificates as on hoistra_test: two columns to the asset, two to the building."""
    rel = {
        "ASSET_BUILDING": {"from": "Asset", "to": "Building", "cardinality": "many_to_one",
                           "join": ["from.building_id = to.building_id"]},
        "WORK_ORDER_ASSET": {"from": "WorkOrder", "to": "Asset", "cardinality": "many_to_one",
                             "join": ["from.asset_id = to.id"]},
        "ASSET_VENDOR": {"from": "Asset", "to": "Vendor", "join": ["from.vendor_id = to.id"], "fill": 1.0},
        "COMPLIANCE_CERTIFICATE_VENDOR": {"from": "ComplianceCertificate", "to": "Vendor", "join": ["from.vendor_id = to.id"]},
        "WORK_ORDER_VENDOR": {"from": "WorkOrder", "to": "Vendor", "join": ["from.vendor_id = to.id"]},
        "ENERGY_ANOMALY_ASSET": {"from": "EnergyAnomaly", "to": "Asset", "join": ["from.asset_id = to.id"], "fill": 0.3},
        "ENERGY_ANOMALY_BUILDING": {"from": "EnergyAnomaly", "to": "Building", "join": ["from.building_id = to.building_id"]},
    }
    blocks = []
    for col in asset_links:
        name = "CERT_ASSET_BY_" + col.upper()
        rel[name] = {"from": "ComplianceCertificate", "to": "Asset", "fill": 0.5,
                     "join": ["from." + col + " = to." + ("id" if col == "asset_id" else col)]}
        blocks.append({"via": [name, "WORK_ORDER_ASSET"], "label": "same asset",
                       "when": {"attribute": "cert_scope", "in": ["Asset"]}})
    blocks.append({"via": ["COMPLIANCE_CERTIFICATE_VENDOR", "WORK_ORDER_VENDOR"], "label": "same vendor",
                   "when": {"attribute": "cert_scope", "in": ["Vendor"]}})
    for col in building_links:
        name = "CERT_BUILDING_BY_" + col.upper()
        rel[name] = {"from": "ComplianceCertificate", "to": "Building", "fill": 0.9, "join": ["from." + col + " = to." + col]}
        blocks.append({"via": [name, "ASSET_BUILDING", "WORK_ORDER_ASSET"], "label": "same building",
                       "when": {"attribute": "cert_scope", "in": ["Building"]}})
    return oq.Ontology({
        "schema": "plenum_scoped",
        "concepts": {
            "Asset": {"table": "assets", "key": "id", "label": "asset_name", "hub": True,
                      "attributes": {"id": "id", "asset_name": "asset_name", "asset_code": "asset_code"}},
            "Building": {"table": "buildings", "key": "building_id", "label": "name", "hub": True,
                         "attributes": {"building_id": "building_id", "name": "name", "building_code": "building_code"}},
            "WorkOrder": {"table": "work_orders", "key": "id", "label": "title",
                          "attributes": {"id": "id", "title": "title", "status": "status"},
                          "states": {"open": {"attribute": "status", "not_in": ["Completed", "Closed"]}}},
            "Vendor": {"table": "vendors", "key": "id", "label": "vendor_name",
                       "attributes": {"id": "id", "vendor_name": "vendor_name"}},
            "ComplianceCertificate": {"table": "compliance_certificates", "key": "id", "label": "certificate_number",
                                      "attributes": {"id": "id", "certificate_number": "certificate_number",
                                                     "cert_scope": "cert_scope", "status": "status"}},
            "EnergyAnomaly": {"table": "energy_anomalies", "key": "id", "label": "anomaly_type",
                              "attributes": {"id": "id", "anomaly_type": "anomaly_type", "status": "status"},
                              "states": {"open": {"attribute": "status", "in": ["open"]}}},
        },
        "relations": rel,
        "derived": {"BLOCKS": {"from": "ComplianceCertificate", "to": "WorkOrder", "alternatives": blocks}},
    })


def _labels(onto, rule):
    return [a["label"] for a in onto.derived[rule].alternatives]


def test_what_a_certificate_reaches_is_read_once_per_target_not_once_per_column():
    o = _onto()
    oq.add_certificate_links(o)
    for rule in ("CERTIFICATE_ASSETS", "CERTIFICATE_ASSET_WORK", "CERTIFICATE_ENERGY"):
        labels = _labels(o, rule)
        assert labels.count("the certificate's own asset") == 1, rule
    assert _labels(o, "CERTIFICATE_ENERGY").count("the certificate's building") == 1
    own = next(a for a in o.derived["CERTIFICATE_ASSETS"].alternatives if a["label"] == "the certificate's own asset")
    assert own["via"] == ["CERTIFICATE_TO_ASSET"]


def test_the_one_read_reaches_the_record_by_either_column():
    o = _onto()
    oq.add_certificate_links(o)
    hops, _, end = o.expand("ComplianceCertificate", ["CERTIFICATE_TO_ASSET"])
    assert end == "Asset"
    qb = oq.QueryBuilder(o, "ComplianceCertificate")
    qb.add_path(hops)
    join = qb.from_clause()
    assert '"f"."asset_id"' in join and '"f"."asset_code"' in join and " OR " in join
    assert join.count(" JOIN ") == 1


def test_the_merged_link_is_never_offered_to_the_planner_or_walked_by_the_path_search():
    o = _onto()
    oq.add_certificate_links(o)
    assert "CERTIFICATE_TO_ASSET" in o.merged and "CERTIFICATE_TO_ASSET" not in o.relations
    hops, _ = o.auto_path("ComplianceCertificate", "Asset")
    assert [h[0].name for h in hops][0] in ("CERT_ASSET_BY_ASSET_ID", "CERT_ASSET_BY_ASSET_CODE")


def test_one_column_is_left_as_it_was():
    o = _onto(asset_links=("asset_id",), building_links=("building_id",))
    oq.add_certificate_links(o)
    own = next(a for a in o.derived["CERTIFICATE_ASSETS"].alternatives if a["label"] == "the certificate's own asset")
    assert own["via"] == ["CERT_ASSET_BY_ASSET_ID"] and o.merged == {}


def test_links_that_do_not_join_the_same_two_things_are_not_merged():
    o = _onto()
    assert oq.merge_links(o, ["CERT_ASSET_BY_ASSET_ID", "COMPLIANCE_CERTIFICATE_VENDOR"], "X") == \
        ["CERT_ASSET_BY_ASSET_ID", "COMPLIANCE_CERTIFICATE_VENDOR"]
    assert oq.merge_links(o, ["CERT_ASSET_BY_ASSET_ID"], "X") == ["CERT_ASSET_BY_ASSET_ID"]
    assert o.merged == {}


def test_a_plain_relation_joins_as_before():
    o = _onto()
    qb = oq.QueryBuilder(o, "WorkOrder")
    qb.add_path(o.expand("WorkOrder", ["WORK_ORDER_ASSET"])[0])
    assert " OR " not in qb.from_clause()


# -- one line in the trace for the whole read -----------------------------------------------------

@pytest.fixture
def traced(monkeypatch):
    monkeypatch.setattr(trace, "_ready", True)

    async def fake_write(t, answer, tool_calls, summary):
        return None

    monkeypatch.setattr(trace, "_write", fake_write)
    p = Principal(user_id=uuid.uuid4(), email="fm@example.com", organization_id=uuid.uuid4(), role="admin",
                  can_ingest=True, building_ids=None)
    tp, to = caller_principal.set(p), caller_organization_id.set(str(p.organization_id))
    trace._turn.set(None)
    trace._sql_rollup.set(None)
    trace.on_activity({"agent": "orchestrator", "stage": "turn", "direction": "input", "summary": "q",
                       "payload": {"message": "q"}})
    yield trace.current()
    caller_principal.reset(tp)
    caller_organization_id.reset(to)
    trace._turn.set(None)
    trace._sql_rollup.set(None)


def _db(t):
    return [s for s in t.spans if s.kind == "db"]


def test_a_run_of_queries_is_one_span_that_keeps_every_statement(traced):
    box = trace.sql_rollup_open("linked records")
    trace.on_sql("SELECT 1 FROM assets", {}, [{"a": 1}, {"a": 2}], 5.0, label="include linked_assets")
    trace.on_sql("SELECT 1 FROM work_orders", {}, [{"w": 1}], 22.0, label="include asset_open_work_orders")
    trace.on_sql("SELECT 1 FROM ppm_visits", {}, [], 4.0, label="include asset_ppm_not_done")
    trace.sql_rollup_close(box)
    spans = _db(traced)
    assert len(spans) == 1
    s = spans[0]
    assert s.name == "linked records: 3 queries" and not s.open
    assert s.output["queries"] == 3 and s.output["rows"] == 3 and s.output["ms"] == 31.0
    assert [q["label"] for q in s.output["by"]] == ["include linked_assets", "include asset_open_work_orders",
                                                    "include asset_ppm_not_done"]
    assert s.input["statements"][1] == {"label": "include asset_open_work_orders", "sql": "SELECT 1 FROM work_orders"}


def test_a_query_outside_a_run_is_its_own_span_as_before(traced):
    trace.on_sql("SELECT 1", {}, [{"a": 1}], 3.0, label="count")
    box = trace.sql_rollup_open("linked records")
    trace.sql_rollup_close(box)                         # nothing ran: no empty line
    trace.on_sql("SELECT 2", {}, [], 3.0, label="focus")
    assert [s.name for s in _db(traced)] == ["count", "focus"]


def test_the_run_is_kept_by_the_task_that_reads(traced):
    """The linked records are read in a task of their own; a task takes a copy of its context."""
    async def read():
        box = trace.sql_rollup_open("linked records")
        try:
            await asyncio.sleep(0)
            trace.on_sql("SELECT 1", {}, [{"a": 1}], 5.0, label="include linked_assets")
            trace.on_sql("SELECT 2", {}, [{"a": 1}], 5.0, label="include open_energy_anomalies")
        finally:
            trace.sql_rollup_close(box)

    async def turn():
        await asyncio.ensure_future(read())
        trace.on_sql("SELECT 3", {}, [], 1.0, label="count")       # the turn's own query, after

    asyncio.run(turn())
    assert [s.name for s in _db(traced)] == ["linked records: 2 queries", "count"]


def test_a_failed_statement_marks_the_line(traced):
    box = trace.sql_rollup_open("linked records")
    trace.on_sql("SELECT 1", {}, None, 2.0, label="include linked_assets", error="relation does not exist")
    trace.sql_rollup_close(box)
    s = _db(traced)[0]
    assert s.ok is False and "relation does not exist" in s.error


def test_the_linked_records_read_is_wrapped():
    import src.agents.orchestrator as O

    body = inspect.getsource(O.DeepAgentOrchestrator._certificate_reach_call)
    assert 'trace.sql_rollup_open("linked records")' in body and "trace.sql_rollup_close(box)" in body
    assert "asyncio.ensure_future(_read())" in body


# -- the analyst is briefed for cards, and a second model reads only what needs a reader -----------

def _O():
    import src.agents.orchestrator as O

    return O.DeepAgentOrchestrator


def test_a_second_model_reads_the_answer_only_when_something_needs_a_reader():
    O = _O()
    assert O._model_review_needed([], False) is False                 # the turn from the trace: 51 s saved
    assert O._model_review_needed(["8 lapsed in the answer, 9 in the pack"], False) is True
    assert O._model_review_needed([], True) is True                    # what the law requires: a reader's job
    assert O._model_review_needed(None, False) is False


def test_both_compliance_paths_ask_before_calling_the_reviewer():
    O = _O()
    for fn in (O._maybe_compliance_posture_shortcut, O._compose_structured_compliance):
        body = inspect.getsource(fn)
        assert "reviewed = self._model_review_needed(code_findings, is_taxonomy)" in body, fn.__name__
        assert ") if reviewed else None" in body, fn.__name__
        assert "The orchestrator writes and checks the wording." in body, fn.__name__


def test_the_analyst_is_told_how_its_output_is_used_once():
    O = _O()
    assert "ONLY what the question asks for" in O._CARDS_BRIEF and "Leave `groups` empty" in O._CARDS_BRIEF
    body = inspect.getsource(O._claude_analyse_compliance)
    assert "if not query_notes.startswith(self._CARDS_BRIEF):" in body
    assert body.index("self._CARDS_BRIEF + query_notes") < body.index('"query_notes": query_notes')


def test_an_engine_answer_with_clean_figures_is_not_sent_to_the_reviewer(monkeypatch):
    O = _O()
    o = O.__new__(O)
    calls = {"analyst": 0, "review": 0}
    card = {"narrative": "One certificate has lapsed at Kingsway House.", "kpis": [], "groups": [], "sections": [],
            "certificates": [], "actions": [], "insights": [], "pending": []}

    async def analyst(self, question, data_json, **kw):
        calls["analyst"] += 1
        return dict(card)

    async def review(self, *a, **k):
        calls["review"] += 1
        return {"verdict": "pass", "findings": [], "drop_panels": [], "missing_type_codes": []}

    async def no_reach(self, rows, session_id, step=None):
        return None

    monkeypatch.setattr(O, "_claude_analyse_compliance", analyst)
    monkeypatch.setattr(O, "_review_compliance_answer", review)
    monkeypatch.setattr(O, "_certificate_reach_call", no_reach)
    monkeypatch.setattr(O, "_taxonomy_findings", lambda self, analysis, pack_facts: [])
    rows = [{"id": "c-1", "certificate_type_code": "EICR", "building_name": "Kingsway House", "status": "Lapsed",
             "expiry_date": "2026-08-01", "days_to_expiry": -103, "cert_scope": "Building"}]
    tool_calls = [{"tool": "list_building_certificates", "input": {}, "output": {"certificates": rows}}]
    structured, steps = asyncio.run(o._compose_structured_compliance(
        "Which certificates have lapsed at Kingsway House?", tool_calls, "s-1", "the agent's notes"))
    assert structured is not None and calls == {"analyst": 1, "review": 0}
    said = next(s for s in steps if s.get("stage") == "review")
    assert said["detail"].startswith("Figures checked against the rows in code")

    # a figure the code checks dispute still goes to the reader, and back for one correction
    monkeypatch.setattr(O, "_pack_facts", lambda self, calls_, rows_: {"by_scope": {"building": {"lapsed": 9}}})
    monkeypatch.setattr(O, "_taxonomy_findings", lambda self, analysis, pack_facts: ["8 lapsed in the answer, 9 in the pack"])
    calls.update(analyst=0, review=0)
    asyncio.run(o._compose_structured_compliance(
        "Which certificates have lapsed at Kingsway House?", tool_calls, "s-2", "the agent's notes"))
    assert calls["review"] == 1 and calls["analyst"] == 2
