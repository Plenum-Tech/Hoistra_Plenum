"""The migration graph's Postgres checkpointer stores compressed state and reads old rows (parity DB)."""
import pytest

from tests.engine_parity.conftest import DSN, reset_parity_run

pytestmark = pytest.mark.skipif(not DSN, reason="parity database not available (HOIST_PARITY=1)")


async def test_the_graph_checkpointer_writes_compressed_and_reads_plain(parity_db, monkeypatch):
    import psycopg
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
    from langgraph.graph import END, START, StateGraph
    from typing_extensions import TypedDict

    from src.graph import migration_graph as mg

    monkeypatch.setenv("HOIST_CHECKPOINT_COMPRESS", "1")
    await reset_parity_run()
    monkeypatch.setattr(mg, "get_sync_db_url", lambda: DSN)
    proxy = await mg.get_migration_graph()
    saver = proxy._graph.checkpointer
    assert type(saver.serde).__name__ == "CompressedJsonPlus"

    class S(TypedDict):
        big: dict

    big = {f"k{i}": "B-301-AHU-03 supply fan bearing" * 4 for i in range(2000)}
    g = StateGraph(S)
    g.add_node("n", lambda s: {"big": {**s["big"], "seen": "yes"}})
    g.add_edge(START, "n")
    g.add_edge("n", END)
    graph = g.compile(checkpointer=saver)
    cfg = {"configurable": {"thread_id": "cmp-1"}}
    await graph.ainvoke({"big": big}, cfg)
    assert (await graph.aget_state(cfg)).values["big"]["seen"] == "yes"
    async with await psycopg.AsyncConnection.connect(DSN) as c:
        rows = await (await c.execute("SELECT type, length(blob) FROM checkpoint_blobs WHERE thread_id = 'cmp-1'")).fetchall()
    assert rows and all(t.endswith("+zlib") for t, _ in rows), rows
    assert max(n for _, n in rows) * 4 < len(JsonPlusSerializer().dumps_typed(big)[1])

    # A run checkpointed before compression (plain serde) resumes under the new one.
    plain = AsyncPostgresSaver(saver.conn)
    old = g.compile(checkpointer=plain)
    cfg2 = {"configurable": {"thread_id": "cmp-2"}}
    await old.ainvoke({"big": big}, cfg2)
    assert (await graph.aget_state(cfg2)).values["big"]["seen"] == "yes"
