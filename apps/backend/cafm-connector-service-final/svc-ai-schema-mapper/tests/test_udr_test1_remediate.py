"""Feature 7.9 AC6 — 'Fix and Retest': remediate Test-1 failures (promote shared
attribute to a reference-table PK) and re-run Test 1, which then passes.
Run: python tests/test_udr_test1_remediate.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.reference_tables import remediate_test1_failures  # noqa: E402
from udr.validation import run_test1_chunk_pk  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


def _tables():
    return {
        "assets": {
            "rows": [
                {"id": "A1", "make": "Siemens"},
                {"id": "A2", "make": "ABB"},
            ],
            "columns": ["id", "make"],
            "pk": ["id"],
        },
    }


# A chunk wrongly anchored to a SHARED ATTRIBUTE value ("Siemens" is an asset.make,
# not a primary key) → Test 1 fails.
chunks = [{"chunk_id": "c1", "entity_id": "Siemens"}]

before = run_test1_chunk_pk(chunks, _tables())
check("Test 1 fails before remediation", before["failed"] == 1 and before["blocks_udr"] is True)
check("failure located in the shared-attribute column",
      before["failures"][0]["found_in"] == [{"table": "assets", "column": "make"}])

# Fix: promote the shared attribute 'make' to a reference table whose PK is the value.
tables = _tables()
plan = remediate_test1_failures(tables, before)
check("remediation builds a reference table", len(plan["reference_tables"]) == 1)
check("remediation emits FK rewrites", len(plan["fk_rewrites"]) >= 1)
check("located value is retagged (resolvable)", "Siemens" in plan["retag"])
check("no unresolved dangling values", plan["unresolved"] == [])

# Add the reference table(s) to the model and re-run Test 1 → now passes.
for ref in plan["reference_tables"]:
    tables[ref["table"]] = {"rows": ref["rows"], "columns": ref["columns"], "pk": ref["pk"]}

after = run_test1_chunk_pk(chunks, tables)
check("Test 1 passes after remediation", after["failed"] == 0 and after["blocks_udr"] is False)
check("'Siemens' now resolves to a primary key", after["pass_rate"] == 1.0)

print("\nALL TESTS PASSED")
