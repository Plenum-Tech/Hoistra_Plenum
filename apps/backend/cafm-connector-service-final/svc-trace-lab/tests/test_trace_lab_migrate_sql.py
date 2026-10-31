"""Apply and verify on a throwaway Postgres. Skips without TRACE_LAB_TEST_DB_URL. Broken migration
files are written to tmp_path, never to migrations/."""
from __future__ import annotations

import asyncio
import shutil
from pathlib import Path

import pytest
from sqlalchemy import text

from tl_scratch import fresh_lab_schema, lab_scratch, new_engine, requires_scratch  # noqa: F401
from trace_lab import db, migrate

pytestmark = requires_scratch

EXTRA = "SET LOCAL lock_timeout = '3s';\nCREATE TABLE tracelab.extra_thing (id integer PRIMARY KEY);\n"


def _copy_all(d: Path) -> int:
    """Copy the shipped files into d; return how many there are."""
    found = migrate.files()
    for f in found:
        shutil.copy(f.path, d / f.name)
    return len(found)


async def _top(engine) -> int:
    async with engine.connect() as conn:
        return max(int(r["version"]) for r in await migrate.read_ledger(conn))


async def test_apply_all_then_verify_ready(lab_scratch):
    st = await migrate.verify(lab_scratch)
    assert st.status == "ready" and st.applied_version == st.expected_version == len(migrate.files())
    assert db.STATE is not None and db.ready()
    async with lab_scratch.connect() as conn:
        ledger = await migrate.read_ledger(conn)
    assert (ledger[0]["version"], ledger[0]["applied_by"]) == (1, "pytest")
    assert ledger[0]["checksum"] == migrate.files()[0].checksum
    assert (await migrate.apply_pending(lab_scratch, applied_by="pytest")).status == "ready"   # nothing pending


async def test_catalog_snapshot_matches_the_files(lab_scratch):
    want = migrate.expected_catalog()
    assert want["version"] == len(migrate.files())
    async with lab_scratch.connect() as conn:
        live = await migrate.read_catalog(conn)
    assert migrate.compare_catalog(want, live) == ([], [])
    assert set(live["tables"]) == {"schema_migrations", "jobs", "evidence_turns", "evidence_spans", "golden_sets",
                                   "golden_cases", "golden_runs", "golden_results", "golden_reveals",
                                   "sample_batches", "sample_items", "annotations", "annotation_events",
                                   "modes", "mode_events", "requirements", "proposal_runs", "proposals", "labels",
                                   "judge_splits", "judge_split_members", "judges", "judge_predictions", "judge_runs",
                                   "monitor_batches", "monitor_verdicts", "eval_cases", "eval_case_runs",
                                   "lab_members", "step_labels", "lab_platform_members", "lab_runs", "lab_questions",
                                   "lab_question_traces", "lab_expected_answers", "lab_failure_modes", "lab_label_cells"}
    assert set(live["functions"]) == {"refuse_change()", "supersede_only()", "judge_splits_guard()", "judges_guard()",
                                      "judges_examples_from_train()", "judge_predictions_guard()",
                                      "judge_runs_guard()", "monitor_verdicts_guard()", "question_id(code text)"}


async def test_absent_before_anything_is_applied(monkeypatch):
    engine = await fresh_lab_schema(monkeypatch, apply=False)
    st = await migrate.verify(engine)
    assert st.status == "absent" and st.pending == [f.version for f in migrate.files()]
    await engine.dispose()


async def test_verify_reports_behind_ahead_and_a_changed_file(lab_scratch, tmp_path):
    behind = tmp_path / "behind"
    behind.mkdir()
    n = _copy_all(behind)
    (behind / f"{n + 1:04d}_extra.sql").write_text(EXTRA, encoding="utf-8")
    st = await migrate.verify(lab_scratch, directory=behind)
    assert st.status == "behind" and st.pending == [n + 1] and st.applied_version == n
    await migrate.apply_pending(lab_scratch, applied_by="pytest", directory=behind)   # the database is now one ahead
    ahead = await migrate.verify(lab_scratch)                                         # this image stops at n
    assert ahead.status == "ahead" and ahead.problems == [f"{n + 1:04d}: applied, but this image has no such file"]
    edited = tmp_path / "edited"
    edited.mkdir()
    _copy_all(edited)
    with open(edited / "0001_foundation.sql", "a", encoding="utf-8") as fh:
        fh.write("-- edited after it was applied\n")
    (edited / f"{n + 1:04d}_extra.sql").write_text(EXTRA, encoding="utf-8")
    st = await migrate.verify(lab_scratch, directory=edited)
    assert st.status == "drifted" and st.problems[0].startswith("0001: checksum differs")


async def test_verify_reports_drift_on_extra_column_missing_index_changed_check(lab_scratch):
    async with lab_scratch.begin() as conn:
        await conn.exec_driver_sql("ALTER TABLE tracelab.jobs ADD COLUMN foo text")
        await conn.exec_driver_sql("DROP INDEX tracelab.ix_jobs_running_heartbeat")
        await conn.exec_driver_sql("ALTER TABLE tracelab.jobs DROP CONSTRAINT jobs_params_object,"
                                   " ADD CONSTRAINT jobs_params_object CHECK (true)")
    st = await migrate.verify(lab_scratch)
    assert st.status == "drifted"
    assert "jobs: extra column foo" in st.problems
    assert "ix_jobs_running_heartbeat: missing" in st.problems
    assert "jobs.jobs_params_object: definition changed" in st.problems


async def test_verify_runs_read_only(lab_scratch, monkeypatch):
    seen: list[str] = []
    real = migrate._verify_in

    async def spy(conn, found, expected, directory):
        seen.append((await conn.execute(text("SHOW transaction_read_only"))).scalar())
        return await real(conn, found, expected, directory)

    monkeypatch.setattr(migrate, "_verify_in", spy)
    assert (await migrate.verify(lab_scratch)).status == "ready" and seen == ["on"]


async def test_concurrent_apply_applies_once(monkeypatch):
    engine = await fresh_lab_schema(monkeypatch, apply=False)
    other = new_engine()
    a, b = await asyncio.gather(migrate.apply_pending(engine, applied_by="pytest"),
                                migrate.apply_pending(other, applied_by="pytest"))
    assert a.status == b.status == "ready"
    async with engine.connect() as conn:
        assert [int(r["version"]) for r in await migrate.read_ledger(conn)] == [f.version for f in migrate.files()]
    await other.dispose()
    await engine.dispose()


async def test_a_failing_file_rolls_back_whole(lab_scratch, tmp_path):
    n = _copy_all(tmp_path)
    (tmp_path / f"{n + 1:04d}_broken.sql").write_text(
        "SET LOCAL lock_timeout = '3s';\nCREATE TABLE tracelab.half_done (id integer PRIMARY KEY);\nSELECT 1 / 0;\n",
        encoding="utf-8")
    st = await migrate.apply_pending(lab_scratch, applied_by="pytest", directory=tmp_path)
    assert st.status == "unreadable" and st.problems[0].startswith(f"{n + 1:04d}: failed at: SELECT 1 / 0")
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT to_regclass('tracelab.half_done')"))).scalar() is None
    assert await _top(lab_scratch) == n


async def test_a_lock_timeout_aborts_instead_of_queueing(lab_scratch, tmp_path, monkeypatch):
    n = _copy_all(tmp_path)
    (tmp_path / f"{n + 1:04d}_note.sql").write_text(
        "SET LOCAL lock_timeout = '3s';\nALTER TABLE tracelab.jobs ADD COLUMN note text;\n", encoding="utf-8")
    monkeypatch.setattr(migrate, "LOCK_RETRIES", 2)
    monkeypatch.setattr(migrate, "LOCK_RETRY_SLEEP_S", 0.1)
    holder = new_engine()
    async with holder.connect() as conn:
        trans = await conn.begin()
        await conn.exec_driver_sql("LOCK TABLE tracelab.jobs IN ACCESS EXCLUSIVE MODE")
        t0 = asyncio.get_running_loop().time()
        st = await migrate.apply_pending(lab_scratch, applied_by="pytest", directory=tmp_path)
        took = asyncio.get_running_loop().time() - t0
        await trans.rollback()
    assert st.status == "unreadable" and "could not get its lock after 2 attempts" in st.problems[0]
    assert took < 20
    assert await _top(lab_scratch) == n
    await holder.dispose()


TURN = "turn-aaaa000000000001"
ORG = "00000000-0000-0000-0000-0000000000a1"


async def _seed_evidence(engine) -> None:
    async with engine.begin() as conn:
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn, reason)"
            " VALUES (:t, CAST(:o AS uuid), CAST(:j AS jsonb), 'golden')"),
            {"t": TURN, "o": ORG, "j": '{"question": "q", "answer": "a"}'})
        await conn.execute(text(
            "INSERT INTO tracelab.evidence_spans (turn_id, span_id, seq, span)"
            " VALUES (:t, 's1', 0, CAST(:j AS jsonb)), (:t, 's2', 1, CAST(:j AS jsonb))"),
            {"t": TURN, "j": '{"kind": "llm"}'})


async def test_evidence_is_append_only(lab_scratch):
    await _seed_evidence(lab_scratch)
    for stmt in ("UPDATE tracelab.evidence_turns SET reason = 'sampled'",
                 "DELETE FROM tracelab.evidence_spans WHERE span_id = 's1'",
                 "TRUNCATE tracelab.evidence_spans",
                 "UPDATE tracelab.evidence_spans SET seq = 5"):
        with pytest.raises(Exception, match="append-only"):
            async with lab_scratch.begin() as conn:
                await conn.exec_driver_sql(stmt)
    async with lab_scratch.begin() as conn:      # the offboarding hatch allows DELETE ...
        await conn.exec_driver_sql("SET LOCAL tracelab.allow_purge = 'on'")
        await conn.exec_driver_sql("DELETE FROM tracelab.evidence_spans WHERE turn_id = 'turn-aaaa000000000001'")
        await conn.exec_driver_sql("DELETE FROM tracelab.evidence_turns WHERE turn_id = 'turn-aaaa000000000001'")
    async with lab_scratch.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM tracelab.evidence_turns"))).scalar() == 0


async def test_evidence_spans_need_their_turn_and_a_json_object(lab_scratch):
    with pytest.raises(Exception, match="evidence_spans_turn_id_fkey"):
        async with lab_scratch.begin() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO tracelab.evidence_spans (turn_id, span_id, seq, span) VALUES ('nope', 's', 0, '{}')")
    with pytest.raises(Exception, match="evidence_turns_turn_check"):
        async with lab_scratch.begin() as conn:
            await conn.exec_driver_sql(
                "INSERT INTO tracelab.evidence_turns (turn_id, organization_id, turn)"
                " VALUES ('t', '00000000-0000-0000-0000-0000000000a1', '[1]')")
