"""Feature 7.10 AC6/AC7 — Test 2 resolution (define_fk / create_reference_table /
mark_coincidental) applied to the data model, then Test 2 re-runs and passes.
Run: python tests/test_udr_test2_resolve.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from udr.validation import apply_test2_resolutions, run_test2_column_fk  # noqa: E402


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# Two tables that share the same "make" values (>30% overlap) with NO defined FK.
def _tables():
    return {
        "assets": {
            "rows": [
                {"id": "1", "make": "Siemens"},
                {"id": "2", "make": "ABB"},
                {"id": "3", "make": "Siemens"},
                {"id": "4", "make": "GE"},
            ],
            "columns": ["id", "make"],
            "pk": ["id"],
        },
        "orders": {
            # 'make' deliberately repeats (Siemens twice) so it does NOT functionally
            # determine order_id — keeps the reference-table builder from carrying the
            # orders PK in as a spurious descriptor.
            "rows": [
                {"order_id": "10", "make": "Siemens"},
                {"order_id": "11", "make": "ABB"},
                {"order_id": "12", "make": "Siemens"},
            ],
            "columns": ["order_id", "make"],
            "pk": ["order_id"],
        },
    }


# ── baseline: the make↔make overlap is flagged with the 3 resolution options ──
base = run_test2_column_fk(_tables(), defined_fks=[])
flagged = base["flags"]
check("baseline flags the make/make overlap", base["flagged_unexplained"] == 1)
check("flag carries the 3 resolution options",
      flagged[0]["resolutions"] == ["define_fk", "create_reference_table", "mark_coincidental"])
check("baseline blocks UDR", base["blocks_udr"] is True)

flag = {
    "table_a": flagged[0]["table_a"], "column_a": flagged[0]["column_a"],
    "table_b": flagged[0]["table_b"], "column_b": flagged[0]["column_b"],
}

# ── option 3: mark_coincidental → re-run no longer flags it ──
r1 = apply_test2_resolutions(_tables(), [], [{**flag, "choice": "mark_coincidental"}])
check("mark_coincidental clears the flag", r1["report"]["flagged_unexplained"] == 0)
check("mark_coincidental no longer blocks", r1["report"]["blocks_udr"] is False)
check("coincidental pair recorded", len(r1["coincidental_pairs"]) == 1)

# ── option 1: define_fk → re-run explained ──
r2 = apply_test2_resolutions(_tables(), [], [{**flag, "choice": "define_fk"}])
check("define_fk clears the flag", r2["report"]["flagged_unexplained"] == 0)
check("define_fk added one FK edge", len(r2["defined_fks"]) == 1)

# ── option 2: create_reference_table → ref table + FKs from both → shared-parent explained ──
r3 = apply_test2_resolutions(_tables(), [], [{**flag, "choice": "create_reference_table"}])
check("create_reference_table makes a new ref table", len(r3["new_reference_tables"]) == 1)
check("create_reference_table adds FKs from both columns", len(r3["defined_fks"]) == 2)
check("shared-parent overlap is now explained", r3["report"]["flagged_unexplained"] == 0)

# ── unknown / incomplete resolutions are skipped, not applied ──
r4 = apply_test2_resolutions(_tables(), [], [{**flag, "choice": "bogus"}])
check("unknown choice is skipped", r4["applied"][0]["action"] == "skipped")
check("unknown choice leaves the flag", r4["report"]["flagged_unexplained"] == 1)

print("\nALL TESTS PASSED")
