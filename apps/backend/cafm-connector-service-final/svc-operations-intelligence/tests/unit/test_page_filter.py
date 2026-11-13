"""Lists that mix pages are narrowed row by row, not refused.

The Pending queue, the value card, the cron list, a building's documents and the building graph
each hold rows owned by several pages. A caller with an access role keeps the rows their pages
own and loses the rest; a caller with every page gets back exactly what went in.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src.engines.auth import pages as P
from src.engines.crons import jobs as cron_jobs
from src.engines.document_register import DOC_TYPES
from src.engines.energy import building_tree
from src.engines.energy.buildings import HOIST_SCORE_DOMAINS
from src.shared import page_filter as F

ALL = None
COMPLIANCE = frozenset({"compliance", "buildings"})


def test_it_names_only_real_pages():
    named = (set(F.FEATURE_PAGES.values()) | set(F.VALUE_MODULE_PAGE.values())
             | set(F.PNL_HEAD_PAGE.values()) | set(F.CRON_MODULE_PAGE.values())
             | set(F.DOC_TYPE_PAGE.values()) | set(F.BRANCH_PAGE.values())
             | set(F.HOIST_DOMAIN_PAGE.values()))
    flat = {p for item in named for p in (item if isinstance(item, tuple) else (item,))}
    assert flat <= set(P.PAGES)
    assert F.PAGES == P.PAGES


# ── the Pending queue ─────────────────────────────────────────────────────────

def item(feature, entity=None):
    return SimpleNamespace(source_feature=feature, related_entity_type=entity)


@pytest.mark.parametrize("feature,entity,expected", [
    ("A", "compliance_certificate", ("compliance",)),
    ("A", "vendor", ("compliance",)),             # a vendor's lapsed accreditation is compliance's
    ("A", None, ("compliance",)),
    ("B", "contract_sla_parameters", ("vendors",)),
    ("B", "invoice", ("vendors",)),
    ("B", "asset_criticality", ("vendors", "assets")),
    ("C", "energy_anomaly", ("energy",)),
    ("C", "meter_reading_gap", ("energy",)),
    ("P", "site", ("buildings",)),
    ("P", None, ()),                              # a platform notice belongs to no page
    ("Z", "something_new", ()),                   # unknown: owned by nothing, so hidden
    (None, None, ()),
])
def test_approval_pages(feature, entity, expected):
    assert F.approval_pages(feature, entity) == expected


def test_pending_queue_keeps_only_held_rows():
    rows = [item("A", "compliance_certificate"), item("B", "invoice"), item("C", "energy_anomaly"),
            item("P"), item("B", "asset_criticality")]
    assert F.approvals(rows, COMPLIANCE) == rows[:1]
    assert F.approvals(rows, frozenset({"assets"})) == rows[4:]
    assert F.approvals(rows, frozenset()) == []
    assert F.approvals(rows, ALL) is rows


def test_approval_allowed_reads_dicts_too():
    assert F.approval_allowed(COMPLIANCE, {"source_feature": "A"})
    assert not F.approval_allowed(COMPLIANCE, {"source_feature": "C"})
    assert F.approval_allowed(ALL, {"source_feature": "C"})


# ── the value card ────────────────────────────────────────────────────────────

def value_payload():
    def mod(key, detected, saved, counted=True):
        return {"key": key, "counted": counted, "detected": detected, "saved": saved}
    return {
        "ok": True, "year": 2026,
        "ledger": {
            "modules": [mod("energy", 100.0, 10.0), mod("vendors", 200.0, 20.0),
                        mod("maintenance", 0, 0, counted=False), mod("compliance", 50.0, 5.0),
                        mod("assets", 400.0, 40.0)],
            "counted_modules": 4, "total_detected": 750.0, "total_saved": 75.0, "note": "n",
        },
        "pnl": {"budget_connected": False, "saved": None, "note": "n",
                "heads": [{"key": "maintenance"}, {"key": "energy"}, {"key": "compliance"},
                          {"key": "unplanned"}]},
    }


def test_value_summary_is_untouched_for_an_unrestricted_caller():
    before = value_payload()
    after = F.value_summary(copy.deepcopy(before), ALL)
    assert after == before


def test_value_total_is_recomputed_from_held_modules():
    out = F.value_summary(value_payload(), frozenset({"energy", "assets"}))
    assert [m["key"] for m in out["ledger"]["modules"]] == ["energy", "assets"]
    assert out["ledger"]["counted_modules"] == 2
    assert out["ledger"]["total_detected"] == 500.0
    assert out["ledger"]["total_saved"] == 50.0
    assert [h["key"] for h in out["pnl"]["heads"]] == ["energy"]


def test_value_summary_with_no_counted_module_claims_no_total():
    out = F.value_summary(value_payload(), frozenset({"ops"}))
    assert [m["key"] for m in out["ledger"]["modules"]] == ["maintenance"]
    assert out["ledger"]["counted_modules"] == 0
    assert out["ledger"]["total_detected"] is None and out["ledger"]["total_saved"] is None
    assert [h["key"] for h in out["pnl"]["heads"]] == ["maintenance", "unplanned"]


def test_value_summary_passes_an_error_payload_through():
    assert F.value_summary({"ok": False, "error": "x"}, COMPLIANCE) == {"ok": False, "error": "x"}


# ── scheduled jobs ────────────────────────────────────────────────────────────

def test_every_catalogue_module_has_an_owner_or_is_the_question_job():
    for key, spec in cron_jobs.CATALOGUE.items():
        if key == "question":
            assert spec["module"] not in F.CRON_MODULE_PAGE
        else:
            assert spec["module"] in F.CRON_MODULE_PAGE, (key, spec["module"])


def test_question_cron_hidden_from_a_role_user():
    assert not F.cron_allowed(frozenset(P.PAGES), "Orchestrator")
    assert F.cron_allowed(ALL, "Orchestrator")


def test_cron_jobs_follow_their_module():
    assert F.cron_allowed(COMPLIANCE, "Compliance")
    assert not F.cron_allowed(COMPLIANCE, "Energy")
    assert F.cron_allowed(frozenset({"ops"}), "Maintenance")
    assert not F.cron_allowed(COMPLIANCE, "")
    assert not F.cron_allowed(COMPLIANCE, "Something new")


# ── documents ─────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("doc_type,page", [
    ("compliance_certificate", "compliance"), ("certificate", "compliance"),
    ("risk_assessment", "compliance"), ("contract", "vendors"), ("service_contract", "vendors"),
    ("invoice", "vendors"), ("vendor_invoice", "vendors"), ("inspection_report", "ops"),
    ("report", "ops"), ("warranty", "assets"), ("om_manual", "assets"), ("manual", "assets"),
    ("utility_bill", "energy"), ("energy_bill", "energy"),
    ("drawing", "buildings"), ("staff_list", "buildings"), ("other", "buildings"),
    (None, "buildings"), ("", "buildings"), ("Something Else", "buildings"),
])
def test_document_page(doc_type, page):
    assert F.document_page(doc_type) == page


def test_every_register_type_has_an_owner():
    for key in DOC_TYPES:
        assert F.document_page(key) in P.PAGES, key


def test_documents_keep_only_held_types():
    rows = [{"type": "compliance_certificate"}, {"type": "contract"}, {"type": "drawing"},
            {"doc_type": "invoice"}, {"type": None}]
    assert F.documents(rows, COMPLIANCE) == [rows[0], rows[2], rows[4]]
    assert F.documents(rows, frozenset({"vendors"})) == [rows[1], rows[3]]
    assert F.documents(rows, ALL) is rows


# ── buildings ─────────────────────────────────────────────────────────────────

def building_row():
    return {"building_id": "b1", "site_id": "b1", "name": "Harbour Point", "building_name": "Harbour Point",
            "code": "B-101", "building_code": "B-101", "country": "UK", "country_code": "GB",
            "eui_kwh_per_m2": 241.0, "floor_area_m2": 9000,
            "graph_counts": {"floors": 6, "assets": 40, "certificates": 12, "contracts": 3,
                             "contract_parameters": 2, "meters": 4, "energy_meters": 1,
                             "work_orders": 90, "documents": 7, "mystery": 5},
            "hoist_score_covered": ["assets", "compliance", "energy"],
            "hoist_score_missing": ["contracts", "maintenance"]}


def test_a_caller_without_buildings_gets_names_only():
    (row,) = F.building_rows([building_row()], frozenset({"energy"}))
    assert row == {"building_id": "b1", "site_id": "b1", "name": "Harbour Point",
                   "building_name": "Harbour Point", "code": "B-101", "building_code": "B-101",
                   "country": "UK", "country_code": "GB"}


def test_a_buildings_holder_loses_other_pages_counts():
    (row,) = F.building_rows([building_row()], COMPLIANCE)
    assert row["eui_kwh_per_m2"] == 241.0                       # the building's own profile stays
    assert row["graph_counts"] == {"floors": 6, "certificates": 12, "documents": 7}
    assert row["hoist_score_covered"] == ["compliance"]
    assert row["hoist_score_missing"] == []


def test_building_rows_are_untouched_for_an_unrestricted_caller():
    rows = [building_row()]
    assert F.building_rows(rows, ALL) is rows


def tree():
    def br(table, count, children=()):
        return {"table": table, "count": count, "available": table != "invoices",
                "children": [br(c, n) for c, n in children]}
    return {"ok": True, "building_id": "b1", "total_records": 0, "row_limit": 25,
            "branches": [br("floors", 6, [("spaces", 30)]),
                         br("assets", 40, [("equipment", 5), ("meters", 4)]),
                         br("documents", 7, [("compliance_certificates", 12)]),
                         br("contracts", 3, [("work_orders", 90), ("invoices", 0)])],
            "unavailable": ["invoices"]}


def test_every_graph_branch_has_an_owner():
    for table in building_tree.BRANCH_META:
        assert F.BRANCH_PAGE.get(table) in P.PAGES, table


def test_building_graph_drops_unheld_branches():
    out = F.building_tree(tree(), COMPLIANCE)
    assert [b["table"] for b in out["branches"]] == ["floors", "documents"]
    assert [c["table"] for c in out["branches"][1]["children"]] == ["compliance_certificates"]
    assert out["total_records"] == 6 + 30 + 7 + 12
    assert out["unavailable"] == []


def test_building_graph_keeps_a_held_child_of_an_unheld_parent():
    # Meters hang off assets in the graph; an energy holder without assets still gets them.
    out = F.building_tree(tree(), frozenset({"buildings", "energy"}))
    assets = [b for b in out["branches"] if b["table"] == "assets"]
    assert assets and assets[0]["count"] == 0 and assets[0].get("withheld") is True
    assert [c["table"] for c in assets[0]["children"]] == ["meters"]


def test_building_graph_is_untouched_for_an_unrestricted_caller():
    t = tree()
    assert F.building_tree(t, ALL) is t
    bad = {"ok": False, "error": "x"}
    assert F.building_tree(bad, COMPLIANCE) is bad


# ── the Hoist Score ───────────────────────────────────────────────────────────

def score():
    keys = [k for k, _ in HOIST_SCORE_DOMAINS]
    return {"ok": True, "root": "buildings", "buildings": 2, "score": 60,
            "domains": [{"key": k, "covered": 1, "of": 2} for k in keys],
            "rows": [{"building_id": "b1", "score": 60, "covered": ["assets", "compliance", "energy"],
                      "missing": ["contracts", "maintenance"]}]}


def test_every_hoist_domain_has_an_owner():
    for key, _ in HOIST_SCORE_DOMAINS:
        assert F.HOIST_DOMAIN_PAGE.get(key) in P.PAGES, key


def test_hoist_score_shows_only_held_bars_and_no_mean():
    out = F.hoist_score(score(), COMPLIANCE)
    assert [d["key"] for d in out["domains"]] == ["compliance"]
    assert out["score"] is None                 # the mean is over all five; not claimed in part
    assert out["rows"][0]["covered"] == ["compliance"]
    assert out["rows"][0]["missing"] == []
    assert out["rows"][0]["score"] is None


def test_hoist_score_is_whole_for_a_holder_of_every_page_it_draws_on():
    held = frozenset({"assets", "compliance", "vendors", "energy", "ops"})
    assert F.hoist_score(score(), held) == score()
    s = score()
    assert F.hoist_score(s, ALL) is s


# ── history from before access was narrowed ───────────────────────────────────

NOW = datetime(2026, 11, 12, 12, 0, tzinfo=timezone.utc)


def run(minutes):
    return SimpleNamespace(ran_at=NOW + timedelta(minutes=minutes))


def test_runs_before_narrowing_are_hidden_from_a_role_user():
    runs = [run(5), run(-5), run(0)]
    assert F.since_narrowing(runs, COMPLIANCE, NOW, when=lambda r: r.ran_at) == runs[:1]


def test_runs_are_untouched_without_a_narrowing_or_without_a_role():
    runs = [run(5), run(-5)]
    assert F.since_narrowing(runs, COMPLIANCE, None, when=lambda r: r.ran_at) is runs
    assert F.since_narrowing(runs, ALL, NOW, when=lambda r: r.ran_at) is runs


def test_a_naive_timestamp_is_read_as_utc():
    naive = SimpleNamespace(ran_at=(NOW + timedelta(minutes=5)).replace(tzinfo=None))
    undated = SimpleNamespace(ran_at=None)
    assert F.since_narrowing([naive, undated], COMPLIANCE, NOW, when=lambda r: r.ran_at) == [naive]
