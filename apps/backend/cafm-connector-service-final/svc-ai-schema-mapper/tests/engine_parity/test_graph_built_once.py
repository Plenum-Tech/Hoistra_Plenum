"""The worker opens its checkpoint pool once, not once per job (parity DB).

Every run_migration / resume_migration job made a new pool of up to 4 Azure connections and ran
setup(); the pools were never closed ("Task was destroyed but it is pending!" for pool-2 … pool-8 in
one run's log, 2 Oct 2026). The pool and setup() now happen once per event loop — even when several
jobs start at once — and each job still gets its own saver: a saver serialises its operations
behind one lock, so one shared saver would make concurrent jobs wait for each other's checkpoints.
"""
import asyncio

import pytest

from tests.engine_parity.conftest import DSN, reset_parity_run

pytestmark = pytest.mark.skipif(not DSN, reason="parity database not available (HOIST_PARITY=1)")


def test_one_pool_per_event_loop_and_a_saver_per_job(monkeypatch):
    from src.graph import migration_graph as mg

    asyncio.run(reset_parity_run())
    monkeypatch.setattr(mg, "get_sync_db_url", lambda: DSN)
    setups = []
    real_setup = mg._AsyncPostgresSaver.setup

    async def counting_setup(self):
        setups.append(1)
        await asyncio.sleep(0.05)  # widen the window two jobs starting together would race in
        return await real_setup(self)
    monkeypatch.setattr(mg._AsyncPostgresSaver, "setup", counting_setup)

    async def jobs():
        graphs = await asyncio.gather(*(mg.get_migration_graph() for _ in range(5)))
        savers = {id(g._graph.checkpointer) for g in graphs}
        pools = {id(g._graph.checkpointer.conn) for g in graphs}
        pool = graphs[0]._graph.checkpointer.conn
        info = (len(savers), len(pools), pool.max_size, pool.min_size, id(pool))
        await pool.close()            # close what this loop opened, so it can end
        return info

    n_savers, n_pools, max_size, min_size, first = asyncio.run(jobs())
    assert n_savers == 5 and n_pools == 1 and len(setups) == 1
    assert max_size >= 10 and min_size == 1
    *_, second = asyncio.run(jobs())  # another event loop gets its own (a pool is bound to its loop)
    assert second != first and len(setups) == 2


def test_a_failed_setup_keeps_the_postgres_saver_and_tries_again(monkeypatch):
    # A database blip at the moment the graph is first built made setup() fail. The saver used to
    # stay on its pool, which reconnects once the database is back; dropping it fell through to an
    # in-memory saver, so a run paused at a gate lost its checkpoint and its answer restarted the
    # run from Node 1 (3 Oct 2026 review). The next build must try setup() again.
    from src.graph import migration_graph as mg

    asyncio.run(reset_parity_run())
    monkeypatch.setattr(mg, "get_sync_db_url", lambda: DSN)
    calls = []
    real_setup = mg._AsyncPostgresSaver.setup

    async def flaky_setup(self):
        calls.append(1)
        if len(calls) == 1:
            raise TimeoutError("couldn't get a connection after 30.00 sec")
        return await real_setup(self)
    monkeypatch.setattr(mg._AsyncPostgresSaver, "setup", flaky_setup)

    async def two_builds():
        first = await mg.get_migration_graph()
        second = await mg.get_migration_graph()
        kinds = (type(first._graph.checkpointer), type(second._graph.checkpointer))
        for g in (first, second):
            await g._graph.checkpointer.conn.close()
        return kinds

    kinds = asyncio.run(two_builds())
    assert kinds == (mg._AsyncPostgresSaver, mg._AsyncPostgresSaver)
    assert len(calls) == 2
