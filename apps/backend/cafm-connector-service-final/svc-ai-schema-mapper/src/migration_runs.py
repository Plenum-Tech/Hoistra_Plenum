"""Migration graph runs in flight in this process, by migration id."""
from __future__ import annotations

import asyncio


#: Migration graph runs in flight in THIS process, by migration id. A node writes its
#: step_paused row before LangGraph has finished the run and saved the pause checkpoint, so for a
#: moment the database says "paused" while the run that paused is still going. An /advance that
#: lands in that window used to flip the job to running and start a second run on the same
#: thread while the first was still finishing; the first then wrote step_paused again and the
#: migration sat there, because every client advances a given pause only once (migration
#: 117b1beb, 24 Sep 2026, stuck after preprocessing until advanced by hand).
MIGRATION_RUNS: dict[str, asyncio.Task] = {}


def track_migration_run(migration_id, coro) -> asyncio.Task:
    """Start a migration graph run as a task and remember it until it ends."""
    task = asyncio.create_task(coro)
    key = str(migration_id)
    MIGRATION_RUNS[key] = task

    def _forget(done: asyncio.Task, key: str = key) -> None:
        if MIGRATION_RUNS.get(key) is done:
            MIGRATION_RUNS.pop(key, None)

    task.add_done_callback(_forget)
    return task


async def await_migration_run(migration_id, timeout: float = 120.0) -> bool:
    """Wait for this migration's in-flight run to finish. False if it is still going after
    ``timeout`` seconds. Never raises: the run's own outcome is recorded by the run."""
    task = MIGRATION_RUNS.get(str(migration_id))
    if task is None or task.done():
        return True
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        return False
    except BaseException:  # noqa: BLE001 - the run failed; that is its own report
        pass
    return True


#: The node that writes each step pause (write_step_pause's step_key → graph node). These are
#: the graph's interrupt_after nodes, so once the pause is saved the checkpoint's next is the
#: node AFTER them.
STEP_PAUSE_NODES: dict[str, str] = {
    "step_1_ingest": "ingest_node",
    "step_2_deterministic_mapping": "deterministic_mapper_node",
    "step_3_semantic_mapping": "semantic_mapper_node",
    "step_5_preprocess": "preprocess_node",
    "step_7_hierarchy": "hierarchy_node",
    "step_8_output_generation": "output_generator_node",
}


def paused_node_for(step_key) -> str | None:
    """The node that wrote this step pause, or None for a gate or an unknown key."""
    return STEP_PAUSE_NODES.get(str(step_key)) if step_key else None


async def await_checkpoint_past(graph, config, node: str, timeout: float = 120.0,
                                interval: float = 2.0) -> bool:
    """Wait until the SAVED checkpoint is past ``node``. False if it still is not after
    ``timeout`` seconds.

    MIGRATION_RUNS only knows this process. A run in the ARQ worker writes its pause and then
    saves the checkpoint on its own time — minutes, for a large output step (migration f87078d7,
    29 Sep 2026) — and an advance that resumed before the save continued from the checkpoint
    before the step, running the step a second time. Until the save lands, ``node`` is still
    the checkpoint's next. Never raises; a checkpoint that cannot be read does not block the
    advance, whose own ainvoke reads it and reports the error.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        try:
            snap = await graph.aget_state(config)
        except Exception:  # noqa: BLE001 - see docstring
            return True
        # snapshot.next leaves out a task whose writes are already saved (LangGraph 0.3:
        # `if not t.writes`), and those are saved before the checkpoint after the node — so a
        # node still in snapshot.tasks is not past yet either.
        pending = set(getattr(snap, "next", None) or ()) | {
            getattr(t, "name", None) for t in (getattr(snap, "tasks", None) or ())}
        if node not in pending:
            return True
        if loop.time() >= deadline:
            return False
        await asyncio.sleep(interval)


def resume_job_id(migration_id) -> str:
    """The one arq job id every resume_migration of a migration carries.

    arq refuses to enqueue a job whose id is queued or running, so two answers to the same
    gate cannot become two resumes writing the same migration at once (f87078d7, 29 Sep 2026:
    two "Confirm — write rows" 108 s apart, two write runs). resume_migration keeps no result
    (worker.py), so the id is free again the moment the job ends.
    """
    return f"resume_migration:{migration_id}"


async def _arq_holder_gate(pool, job_id: str):
    """The gate_type of the resume_migration job holding ``job_id``, or None."""
    from arq.jobs import Job

    info = await Job(job_id, pool).info()
    return (getattr(info, "kwargs", None) or {}).get("gate_type") if info else None


async def enqueue_resume(pool, *, migration_id, gate_type, decisions, wait: float = 60.0,
                         interval: float = 1.0, holder_gate=_arq_holder_gate) -> str:
    """Enqueue resume_migration once no other resume of this migration is queued or running.

    "queued" when enqueued. While another holds the id, wait for it (up to ``wait`` s): some
    gates lead straight to another inside ONE resume job (pk_review → unique_table_review,
    grouping_review → column_mapping_review), and the deep-agent driver answers the second
    within seconds, while the first job is still finishing — a refusal there, reported as
    "approved", left the migration running for good (pre-push review, 29 Sep 2026). "busy"
    when it is still held after the wait; the caller must then hand the gate back.

    "duplicate" when the job holding the id is applying this same gate. A resumed gate node
    re-announces its gate for a moment, and a second answer can win the claim then; waited
    out, it would land on the NEXT gate's interrupt and approve it unseen (re-review, 29 Sep
    2026). It is the answer already being applied: neither queued nor handed back.

    A finished job's kept result also holds an arq id — and arq keeps a FAILED job's result
    for the worker's keep_result (an hour) whatever the function says. With no job behind it
    (no arq:job key) it is not a resume in flight, so it is cleared and the enqueue retried.
    """
    jid = resume_job_id(migration_id)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    cleared = False
    while True:
        job = await pool.enqueue_job(
            "resume_migration",
            _job_id=jid,
            migration_id=migration_id,
            gate_type=gate_type,
            decisions=decisions,
        )
        if job is not None:
            return "queued"
        try:
            holder = await holder_gate(pool, jid)
        except Exception:  # noqa: BLE001 - an unreadable holder is waited on like any other
            holder = None
        if holder is not None and holder == gate_type:
            return "duplicate"
        if not cleared and not await pool.exists("arq:job:" + jid):
            await pool.delete("arq:result:" + jid)
            cleared = True
            continue
        if loop.time() >= deadline:
            return "busy"
        await asyncio.sleep(interval)


async def claim_awaiting_gate(session, migration_id_uuid) -> bool:
    """Flip awaiting_review → running for the answer that finds the gate waiting. False when
    it was not waiting (already answered, or the run is past it): that answer is not applied."""
    from sqlalchemy import update

    from .models.migration import MigrationJob

    result = await session.execute(
        update(MigrationJob)
        .where(MigrationJob.id == migration_id_uuid, MigrationJob.status == "awaiting_review")
        .values(status="running", pending_gate_type=None, pending_gate_payload=None)
    )
    await session.commit()
    return getattr(result, "rowcount", 0) == 1


async def release_gate(session, migration_id_uuid, gate_type, payload) -> None:
    """Hand an answered gate back: running → awaiting_review at ``gate_type`` with its payload.
    For an answer that claimed the gate but could not be queued — the page, or the driver,
    then answers again instead of the migration running for good with nothing to resume it."""
    from sqlalchemy import update

    from .models.migration import MigrationJob

    await session.execute(
        update(MigrationJob)
        .where(MigrationJob.id == migration_id_uuid, MigrationJob.status == "running",
               MigrationJob.pending_gate_type.is_(None))
        .values(status="awaiting_review", pending_gate_type=gate_type, pending_gate_payload=payload)
    )
    await session.commit()
