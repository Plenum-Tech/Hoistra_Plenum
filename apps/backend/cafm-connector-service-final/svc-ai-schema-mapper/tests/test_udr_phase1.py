"""Offline unit tests for UDR Phase-I gate logic + ontology adapter.

Pure-logic tests (run_test_1 / run_test_2) need no service env. We stub
`cafm_shared.logging` so the modules import standalone, and load the node
modules by file path (their bottom `from ..state import` falls back to dict).

Run: python tests/test_udr_phase1.py   (also pytest-compatible)
"""
import importlib.util
import os
import sys
import types
from pathlib import Path

# ── stub cafm_shared.logging so node modules import without the service env ──
if "cafm_shared" not in sys.modules:
    pkg = types.ModuleType("cafm_shared")
    logmod = types.ModuleType("cafm_shared.logging")
    logmod.get_logger = lambda *_a, **_k: __import__("logging").getLogger("test")
    pkg.logging = logmod
    sys.modules["cafm_shared"] = pkg
    sys.modules["cafm_shared.logging"] = logmod

ROOT = Path(__file__).resolve().parent.parent  # svc-ai-schema-mapper/


def _load(name, relpath):
    spec = importlib.util.spec_from_file_location(name, ROOT / relpath)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

udr = _load("udr_tests_under_test", "src/graph/nodes/udr_tests.py")
onto_map = _load("ontology_mapping_under_test", "src/graph/nodes/ontology_mapping.py")


def test_test1_pass_and_block():
    pks = {"asset": {"MOB-AHU-001", "AST-4471"}, "manufacturer": {"Siemens 012"}}
    chunks = [
        {"chunk_id": 1, "source_entity_type": "asset", "source_entity_id": "MOB-AHU-001"},
        {"chunk_id": 2, "source_entity_type": "manufacturer", "source_entity_id": "Siemens 012"},
        {"chunk_id": 3, "source_entity_type": "manufacturer", "source_entity_id": "Bosch Series C"},  # fail
    ]
    r = udr.run_test_1(chunks, pks)
    assert r["total_checked"] == 3 and r["failed"] == 1
    assert r["failing"][0]["value"] == "Bosch Series C"
    # 1/3 = 33% > 1% -> blocked
    assert r["blocked"] is True
    # all-pass case is not blocked
    r2 = udr.run_test_1(chunks[:2], pks)
    assert r2["failed"] == 0 and r2["blocked"] is False


def test_test2_explained_vs_flagged():
    column_values = {
        ("work_orders", "asset_id"): {"A1", "A2", "A3", "A4"},
        ("assets", "asset_code"): {"A1", "A2", "A3", "A4"},          # FK -> explained
        ("sites", "postcode"): {"AB1", "AB2", "AB3", "AB4"},
        ("vendors", "postcode"): {"AB1", "AB2", "AB3", "AB4"},        # coincidental -> not flagged
        ("work_orders", "site_ref"): {"S1", "S2", "S3", "S4"},
        ("assets", "site_code"): {"S1", "S2", "S3", "S4"},            # missing FK -> flagged
    }
    fks = [{"from_table": "work_orders", "from_column": "asset_id",
            "to_table": "assets", "to_column": "asset_code"}]
    coincidental = [("sites", "postcode", "vendors", "postcode")]
    r = udr.run_test_2(column_values, fks, coincidental)
    flagged_pairs = {(d["column_a"], d["column_b"]) for d in r["flagged_detail"]}
    # the asset_id<->asset_code overlap is explained; postcode is coincidental;
    # site_ref<->site_code is an unexplained FK -> flagged (both directions).
    assert ("work_orders.site_ref", "assets.site_code") in flagged_pairs
    assert all("postcode" not in a and "postcode" not in b for a, b in flagged_pairs)
    assert r["flagged"] >= 1 and r["hits"] >= r["explained"]


def test_ontology_adapter_flag_gating():
    os.environ.pop("UDR_ONTOLOGY_ENABLED", None)
    assert onto_map.ontology_enabled() is False
    assert onto_map.ontology_table_match("WO_Header") is None  # off -> None
    assert onto_map.ontology_column_match("WONUM", "work_orders") is None
    os.environ["UDR_ONTOLOGY_ENABLED"] = "true"
    assert onto_map.ontology_enabled() is True
    os.environ.pop("UDR_ONTOLOGY_ENABLED", None)


def test_udr_tests_flag_gating():
    os.environ.pop("UDR_TESTS_ENABLED", None)
    assert udr.tests_enabled() is False   # default off -> graph unchanged
    os.environ["UDR_TESTS_ENABLED"] = "on"
    assert udr.tests_enabled() is True
    os.environ.pop("UDR_TESTS_ENABLED", None)


if __name__ == "__main__":
    test_test1_pass_and_block()
    test_test2_explained_vs_flagged()
    test_ontology_adapter_flag_gating()
    test_udr_tests_flag_gating()
    print("OK — all UDR Phase-I unit tests passed")
