"""A gate answer is applied once.

On 29 Sep 2026 (migration f87078d7) "Confirm — write rows" was accepted twice, at 09:13:24 and
09:15:12 UTC, and two resume_migration jobs wrote the same migration at once. The write node
re-runs on resume and re-announces its gate for about two seconds before interrupt() returns,
so the page showed Confirm again; and the gate endpoint enqueued a resume even when its own
awaiting_review → running swap had changed nothing, although its comment said that swap
blocked a second resume.

Now the swap is checked (claim_awaiting_gate) and every resume_migration job for a migration
carries the same arq job id, so arq refuses a second while one is queued or running. The job
keeps no result, so the id is free again the moment it ends.
"""
from __future__ import annotations

import asyncio
from uuid import UUID

from src.migration_runs import claim_awaiting_gate, enqueue_resume, release_gate, resume_job_id

MIG = "f87078d7-652a-4652-8029-b82795af4266"


class _Pool:
    """arq: enqueue_job answers None while a job with this id is queued or running (its
    arq:job key exists) or while a finished job's result is kept (arq:result key)."""

    def __init__(self, held_for=0, result_only=False):
        self.jobs: dict[str, dict] = {}
        self.held_for, self.result_only = held_for, result_only
        self.attempts = 0
        self.deleted: list[str] = []

    async def enqueue_job(self, function, *args, _job_id=None, **kwargs):
        self.attempts += 1
        if self.result_only or _job_id in self.jobs or self.attempts <= self.held_for:
            return None
        self.jobs[_job_id] = {"function": function, **kwargs}
        return object()

    async def exists(self, key):
        return 0 if self.result_only else int(self.attempts <= self.held_for or key[len("arq:job:"):] in self.jobs)

    async def delete(self, key):
        self.deleted.append(key)
        self.result_only = False


def _holder(gate):
    async def holder(pool, job_id):
        return gate
    return holder


def test_every_resume_of_a_migration_has_the_same_job_id():
    assert resume_job_id(MIG) == resume_job_id(UUID(MIG))
    assert resume_job_id(MIG) != resume_job_id("11111111-1111-1111-1111-111111111111")


def test_a_second_answer_while_the_first_is_applied_is_not_enqueued():
    pool = _Pool()

    async def go():
        first = await enqueue_resume(pool, migration_id=MIG, gate_type="write",
                                     decisions={"confirmed": True})
        second = await enqueue_resume(pool, migration_id=MIG, gate_type="write",
                                      decisions={"confirmed": True}, wait=0)
        return first, second

    assert asyncio.run(go()) == ("queued", "busy")
    job = pool.jobs[resume_job_id(MIG)]
    assert job == {"function": "resume_migration", "migration_id": MIG, "gate_type": "write",
                   "decisions": {"confirmed": True}}


def test_the_resume_job_keeps_no_result_so_its_id_frees_when_it_ends():
    from src.worker import WorkerSettings

    fns = {getattr(f, "name", getattr(f, "__name__", None)): f for f in WorkerSettings.functions}
    assert "resume_migration" in fns
    assert getattr(fns["resume_migration"], "keep_result_s", None) == 0


class _Result:
    def __init__(self, rowcount):
        self.rowcount = rowcount


class _Session:
    def __init__(self, rowcount):
        self.rowcount = rowcount
        self.committed = False
        self.statements = []

    async def execute(self, stmt):
        self.statements.append(stmt)
        return _Result(self.rowcount)

    async def commit(self):
        self.committed = True


def test_the_answer_that_flips_the_gate_is_the_one_applied():
    s = _Session(rowcount=1)
    assert asyncio.run(claim_awaiting_gate(s, UUID(MIG))) is True
    assert s.committed
    sql = str(s.statements[0])
    assert "awaiting_review" in str(s.statements[0].compile(compile_kwargs={"literal_binds": True}))
    assert "UPDATE" in sql


def test_an_answer_to_a_gate_no_longer_waiting_is_not():
    assert asyncio.run(claim_awaiting_gate(_Session(rowcount=0), UUID(MIG))) is False



# ── pre-push review, 29 Sep 2026 ────────────────────────────────────────────────────────
# Some gates lead straight to another inside ONE resume job (pk_review → unique_table_review,
# grouping_review → column_mapping_review → pre_semantic pass 2). The deep-agent driver
# answers the second within seconds, while the first job still holds the job id in its tail
# — and the answer was claimed, refused by arq, and reported "approved": stuck for good.


def test_an_answer_to_the_next_gate_waits_for_the_resume_still_finishing():
    pool = _Pool(held_for=3)
    ok = asyncio.run(enqueue_resume(pool, migration_id=MIG, gate_type="unique_table_review",
                                    decisions={}, wait=5, interval=0.001,
                                    holder_gate=_holder("pk_review")))
    assert ok == "queued" and pool.attempts == 4


def test_a_resume_that_does_not_finish_in_time_refuses_the_answer_rather_than_dropping_it():
    pool = _Pool(held_for=10_000)
    ok = asyncio.run(enqueue_resume(pool, migration_id=MIG, gate_type="write", decisions={},
                                    wait=0.02, interval=0.001, holder_gate=_holder("pk_review")))
    assert ok == "busy" and not pool.jobs


def test_a_finished_jobs_kept_result_does_not_hold_the_id():
    # arq keeps a FAILED job's result for the worker's keep_result (an hour) whatever the
    # function says; nothing is running, so the id is cleared and the answer goes in.
    pool = _Pool(result_only=True)
    ok = asyncio.run(enqueue_resume(pool, migration_id=MIG, gate_type="write", decisions={}, wait=0))
    assert ok == "queued" and pool.deleted == ["arq:result:" + resume_job_id(MIG)]


def test_a_refused_answer_hands_the_gate_back():
    s = _Session(rowcount=1)
    asyncio.run(release_gate(s, UUID(MIG), "unique_table_review", {"gate": "unique_table_review"}))
    assert s.committed
    params = {str(v) for v in s.statements[0].compile().params.values()}
    assert {"awaiting_review", "unique_table_review", "running"} <= params



# ── re-review, 29 Sep 2026 ──────────────────────────────────────────────────────────────
# A resumed gate node re-announces its own gate for a moment (write_gate_payload runs again
# before interrupt() returns the answer). A second answer to that SAME gate can win the claim
# in that moment. Waited out and enqueued behind the first, it would land on the NEXT gate's
# interrupt and approve it unseen; handed back after the wait, it would reopen a gate that is
# already being applied. It is the answer being applied, so it is neither queued nor handed back.


def test_a_second_answer_to_the_gate_being_applied_is_recognised_at_once():
    pool = _Pool(held_for=10_000)
    ok = asyncio.run(enqueue_resume(pool, migration_id=MIG, gate_type="pk_approval", decisions={},
                                    wait=5, interval=0.001, holder_gate=_holder("pk_approval")))
    assert ok == "duplicate"
    assert pool.attempts == 1 and not pool.jobs, "not waited on, not queued"


def test_an_unreadable_holder_is_waited_on_like_any_other():
    async def unreadable(pool, job_id):
        raise RuntimeError("redis timeout")

    pool = _Pool(held_for=2)
    ok = asyncio.run(enqueue_resume(pool, migration_id=MIG, gate_type="write", decisions={},
                                    wait=5, interval=0.001, holder_gate=unreadable))
    assert ok == "queued"
