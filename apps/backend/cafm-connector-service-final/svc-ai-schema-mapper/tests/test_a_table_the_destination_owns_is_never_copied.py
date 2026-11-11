"""A column the destination already has a table for is a reference to that table, never a copy.

Run 5ea53444 (9 Nov 2026), five sheets, no buildings or vendors sheet among them. The classifier
got it right: building_id and vendor_id were Foreign Keys to the existing buildings and vendors
tables (owned_by_destination). At the classification gate both were answered "Shared", and the
run was about to write shared_buildings and shared_vendors -- two new tables keyed on the same ids
as buildings and vendors, with every building_id and vendor_id column pointed at the copies:
"two tables preparing the same primary key id". The workbook carries a primary id on every sheet
already; nothing needed a new table.

The gate screen no longer offers it, but a gate can also be answered by the chat or the API, so
the rule is enforced where decisions are applied.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from src.udr.column_intelligence import apply_classification_decisions

TABLES = {
    "contracts": {"columns": ["contract_id", "building_id", "vendor_id"],
                  "rows": [{"contract_id": "CTR-001", "building_id": "BLD-CDF-0001", "vendor_id": "VEN-001"},
                           {"contract_id": "CTR-002", "building_id": "BLD-CDF-0001", "vendor_id": "VEN-002"}]},
    "ppm_schedules": {"columns": ["schedule_id", "vendor_id", "contract_id"],
                      "rows": [{"schedule_id": "PPM-001", "vendor_id": "VEN-004", "contract_id": "CTR-004"},
                               {"schedule_id": "PPM-002", "vendor_id": "VEN-001", "contract_id": "CTR-001"}]},
}


def _run_5ea53444():
    """The two groups as the classifier left them, before the gate."""
    return {
        "classification": [
            {"group_id": "G1", "canonical_name": "building_id", "verdict": "Foreign Key relationship",
             "members": ["contracts.building_id", "ppm_schedules.building_id"],
             "has_pk": "buildings.building_id", "owned_by_destination": "buildings.building_id"},
            {"group_id": "G4", "canonical_name": "vendor_id", "verdict": "Foreign Key relationship",
             "members": ["contracts.vendor_id", "ppm_schedules.vendor_id"],
             "has_pk": "vendors.id", "owned_by_destination": "vendors.id"},
            # A group that only links two uploaded sheets: no destination owner.
            {"group_id": "G5", "canonical_name": "contract_id", "verdict": "Foreign Key relationship",
             "members": ["contracts.contract_id", "ppm_schedules.contract_id"],
             "has_pk": "contracts.contract_id", "owned_by_destination": None},
        ],
        "fk_candidates": [],
        "shared_attribute_tables": [],
    }


def _rows(ci):
    return {r["group_id"]: r for r in ci["classification"]}


def test_shared_is_refused_for_a_column_the_destination_owns():
    ci = _run_5ea53444()
    result = apply_classification_decisions(ci, TABLES, overrides={"G1": "shared", "G4": "shared"})
    rows = _rows(ci)
    for gid, owner in (("G1", "buildings.building_id"), ("G4", "vendors.id")):
        assert rows[gid]["verdict"] == "Foreign Key relationship", gid
        assert rows[gid]["has_pk"] == owner, f"{gid} lost its reference to {owner}"
        assert rows[gid]["override_refused"] == "already_a_key"
        assert "human_override" not in rows[gid]
        assert gid in result["overrides_refused"]
    assert ci["shared_attribute_tables"] == [], "a copy of buildings or vendors was planned"
    assert result["lookup_tables_added"] == []


def test_a_group_keyed_on_an_uploaded_sheet_is_refused_too():
    """contract_id is the contracts sheet's own primary id. A shared_contracts table would be a
    second table keyed on CTR-001.. -- the same duplication, from a sheet instead of the
    destination. Asked on 9 Nov: no id may be the key of more than one table."""
    ci = _run_5ea53444()
    result = apply_classification_decisions(ci, TABLES, overrides={"G5": "shared"})
    row = _rows(ci)["G5"]
    assert row["verdict"] == "Foreign Key relationship"
    assert row["has_pk"] == "contracts.contract_id"
    assert "contracts.contract_id" in result["overrides_refused"]["G5"]
    assert ci["shared_attribute_tables"] == []


def test_an_owned_group_already_made_shared_can_be_linked_back():
    """Run 5ea53444 as it stands: G1 and G4 were made shared at the gate, has_pk cleared and the
    copies planned. Re-run from that gate and answered "link", they must point at buildings and
    vendors again and the copies must go -- before this, asking for the link took the
    shared-attribute branch and rebuilt the lookup table."""
    ci = _run_5ea53444()
    rows = _rows(ci)
    for gid, table in (("G1", "shared_buildings"), ("G4", "shared_vendors")):
        rows[gid].update(verdict="Shared Attribute", human_override="fk_to_shared",
                         has_pk=None, canonical_has_pk=None)
        ci["shared_attribute_tables"].append({"from_group": gid, "table_name": table})

    apply_classification_decisions(ci, TABLES, overrides={"G1": "fk", "G4": "fk"})

    rows = _rows(ci)
    assert rows["G1"]["verdict"] == "Foreign Key relationship"
    assert rows["G1"]["has_pk"] == "buildings.building_id"
    assert rows["G4"]["has_pk"] == "vendors.id"
    assert ci["shared_attribute_tables"] == [], [t["table_name"] for t in ci["shared_attribute_tables"]]
    targets = {(f["src_table"], f["src_column"]): (f["dst_table"], f["dst_column"]) for f in ci["fk_candidates"]}
    assert targets[("contracts", "building_id")] == ("buildings", "building_id")
    assert targets[("ppm_schedules", "vendor_id")] == ("vendors", "id")


def test_excluding_an_owned_group_still_works():
    ci = _run_5ea53444()
    result = apply_classification_decisions(ci, TABLES, rejected=["G1"])
    assert "G1" in result["rejected_applied"] or "G1" not in _rows(ci)


# -- the alignment call that crashed on every run --------------------------------------------------

def _keywords_passed_to(func_name: str, source_path: Path) -> list[set[str]]:
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            name = f.id if isinstance(f, ast.Name) else getattr(f, "attr", None)
            if name == func_name:
                out.append({k.arg for k in node.keywords if k.arg})
    return out


def test_every_keyword_passed_to_the_alignment_exists_on_it():
    """dest_pk_by_table was pasted onto this call on 30 Oct. align_buckets_to_b21 has no such
    parameter, so the call raised TypeError on every run and the except logged it and skipped the
    alignment of the mappings that drive the write. Read from the source, so any keyword the
    function does not take is caught here -- not in a warning nobody reads."""
    from src.udr.gate_alignment import align_buckets_to_b21

    accepted = set(inspect.signature(align_buckets_to_b21).parameters)
    node = Path(__file__).resolve().parent.parent / "src" / "graph" / "nodes" / "pre_semantic_review_node.py"
    calls = _keywords_passed_to("align_buckets_to_b21", node)
    assert calls, "the alignment call was not found"
    for kw in calls:
        assert kw <= accepted, f"passes keywords align_buckets_to_b21 does not take: {sorted(kw - accepted)}"
