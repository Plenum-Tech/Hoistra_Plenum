"""taxonomy.load_graph reads Trace Lab's own modes table into a ModeGraph (ids as text, lists as lists, merges and
splits followed, proposed and rejected modes kept but not live). Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import trace_lab_m4_fixtures as fx
from tl_scratch import lab_scratch, requires_scratch  # noqa: F401
from trace_lab import labels, taxonomy

pytestmark = requires_scratch
T0 = datetime(2026, 10, 1, tzinfo=timezone.utc)


async def test_load_graph_reads_modes_and_resolves_a_merge(lab_scratch):
    async with lab_scratch.begin() as conn:
        keep = await fx.add_mode(conn, "counts_cancelled_as_open", status="confirmed", requirement_ids=("WO-1",),
                                 created_at=T0, rescoped_at=T0, absorbed_at=T0 + timedelta(days=2))
        gone = await fx.add_mode(conn, "counts_void_as_open", status="retired", merged_into=keep, created_at=T0)
        child = await fx.add_mode(conn, "counts_stale_as_open", split_from=gone, created_at=T0 + timedelta(days=3))
        proposed = await fx.add_mode(conn, "counts_closed_as_open", status="proposed")
        rejected = await fx.add_mode(conn, "counts_anything_as_open", status="rejected")
    async with lab_scratch.connect() as conn:
        graph = await taxonomy.load_graph(conn)
    assert set(graph.modes) == {keep, gone, child, proposed, rejected}
    assert all(isinstance(m["id"], str) and isinstance(m["created_from"], list) for m in graph.modes.values())
    assert graph.modes[keep]["requirement_ids"] == ["WO-1"] and graph.modes[gone]["merged_into"] == keep
    assert graph.modes[child]["split_from"] == gone
    assert graph.modes[proposed]["proposer_organization_id"] == fx.ORG_A
    assert {m["id"] for m in graph.live()} == {keep, child}
    assert graph.root_of(gone) == keep and graph.root_of(proposed) is None and graph.root_of(rejected) is None
    assert sorted(p["id"] for p in graph.parts(keep)) == sorted([keep, gone])
    assert graph.split_ancestors(child)[0]["id"] == gone
    cell = labels.resolve_cell(graph, {}, "turn-1", graph.modes[keep])
    assert cell.state == "unchecked"
    assert taxonomy.payload(graph.modes[keep])["created_at"] == T0.isoformat()
