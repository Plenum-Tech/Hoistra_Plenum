"""Marking a job running reads its status, not its whole row.

The job row carries node_logs and the pending gate payload, ~2 MB by the late gates; run_migration
and resume_migration loaded all of it to check one field and flip it (4.5 s a resume on a slow
link by the end of a run, 2 Oct 2026). Same checks, same single-column UPDATE.
"""
import asyncio
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession

from src import worker


def _session_class(status: "str | None", seen: list):
    class _S(AsyncSession):
        async def execute(self, stmt, *a, **k):
            seen.append(stmt)
            rows = [] if status is None else [SimpleNamespace(status=status)]
            return SimpleNamespace(first=lambda: (rows[0].status,) if rows else None,
                                   scalar_one_or_none=lambda: (rows[0].status if rows else None))

        async def commit(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False
    return _S


def _selected_columns(stmt) -> list[str]:
    return [c.name for c in getattr(stmt, "selected_columns", [])]


def _resume(monkeypatch, status):
    seen: list = []
    monkeypatch.setattr(worker, "get_async_session_factory", lambda: _session_class(status, seen))

    async def no_graph():
        raise RuntimeError("stop after marking")
    monkeypatch.setattr(worker, "get_migration_graph", no_graph)
    try:
        out = asyncio.run(worker.resume_migration({}, "971c7322-56cd-46ad-87e8-92b558370344", "write", {"confirmed": True}))
    except RuntimeError as exc:
        out = {"stopped": str(exc)}
    return out, seen


def test_a_resume_reads_the_status_column_only(monkeypatch):
    out, seen = _resume(monkeypatch, "awaiting_review")
    assert out == {"stopped": "stop after marking"}
    selects = [s for s in seen if hasattr(s, "selected_columns")]
    assert selects and all(_selected_columns(s) == ["status"] for s in selects), [_selected_columns(s) for s in selects]
    updates = [s for s in seen if s.__visit_name__ == "update"]
    assert len(updates) == 1 and set(updates[0].compile().params) == {"status", "id_1"}


def test_a_job_that_is_not_resumable_is_refused_as_before(monkeypatch):
    out, _ = _resume(monkeypatch, "complete")
    assert out == {"status": "failed", "error": "Job not resumable: complete"}


def test_a_missing_job_is_refused_as_before(monkeypatch):
    out, _ = _resume(monkeypatch, None)
    assert out == {"status": "failed", "error": "Job not found"}
