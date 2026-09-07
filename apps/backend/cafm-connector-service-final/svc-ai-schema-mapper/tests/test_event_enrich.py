"""F4-2 — event_log + node-entry enrichment (pure). Run: python tests/test_event_enrich.py"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from graph.event_enrich import (  # noqa: E402
    ACTOR_SYSTEM,
    append_event,
    build_node_event,
    derive_node_outcome,
    enrich_node_entry,
)


def check(name, cond):
    print(("PASS" if cond else "FAIL"), name)
    if not cond:
        raise AssertionError(name)


# ── one-line outcome derivation per node ─────────────────────────────────────
check("ingest outcome", derive_node_outcome("ingest_node", {"table_count": 3, "row_count": 420})
      == "Ingested 3 tables, 420 rows")
check("ingest singular table", derive_node_outcome("ingest_node", {"table_count": 1, "row_count": 5})
      == "Ingested 1 table, 5 rows")
check("deterministic outcome", "12 mapped" in derive_node_outcome("deterministic_mapper_node", {"mapped": 12, "unresolved": 3})
      and "3 to semantic" in derive_node_outcome("deterministic_mapper_node", {"mapped": 12, "unresolved": 3}))
check("write outcome", derive_node_outcome("write_node", {"written": 100}) == "Write complete — 100 rows")
check("hierarchy node outcome", derive_node_outcome("verify_hierarchy_node", {}) == "Hierarchy detection complete")
check("generic fallback", derive_node_outcome("canonical_schema_node", {}) == "canonical_schema_node complete")

# ── enrich_node_entry adds trigger/outcome/actor in place, doesn't clobber ───
entry = {"node_id": 1, "node_name": "ingest_node", "status": "complete", "output": {"table_count": 2, "row_count": 9}}
enrich_node_entry(entry, node_name="ingest_node")
check("enriched trigger default", entry["trigger"] == "query")
check("enriched actor default", entry["actor"] == ACTOR_SYSTEM)
check("enriched derived outcome", entry["outcome"] == "Ingested 2 tables, 9 rows")
check("status preserved", entry["status"] == "complete")

# explicit values win + existing outcome not clobbered
pre = {"node_name": "x", "outcome": "custom", "output": {}}
enrich_node_entry(pre, trigger="scheduled", actor="human")
check("existing outcome kept", pre["outcome"] == "custom")
check("explicit trigger/actor", pre["trigger"] == "scheduled" and pre["actor"] == "human")

# ── build_node_event full shape + duration ───────────────────────────────────
ev = build_node_event(node_id=9, node_name="write_node", status="completed",
                      started_at=datetime(2026, 6, 16, 12, 0, 0), completed_at=datetime(2026, 6, 16, 12, 0, 2),
                      output={"written": 50})
check("build event keys", {"node_id", "node_name", "status", "trigger", "outcome", "actor",
                           "started_at", "completed_at", "duration_ms", "output", "logs"} <= set(ev.keys()))
check("build event duration 2000ms", ev["duration_ms"] == 2000)
check("build event outcome", ev["outcome"] == "Write complete — 50 rows")

# ── append_event populates state['event_log'] (init if absent) ───────────────
st = {}
append_event(st, node_id=1, node_name="ingest_node", outcome="Ingested 2 tables",
             status="completed", trigger="query")
check("event_log initialised + 1 entry", isinstance(st["event_log"], list) and len(st["event_log"]) == 1)
e0 = st["event_log"][0]
check("event has F4-2 dims", e0["trigger"] == "query" and e0["status"] == "completed"
      and e0["actor"] == ACTOR_SYSTEM and e0["outcome"] == "Ingested 2 tables" and e0["node"] == 1)
append_event(st, node_id=9, node_name="write_node", status="completed")
check("event_log accumulates", len(st["event_log"]) == 2)

print("\nALL TESTS PASSED")
