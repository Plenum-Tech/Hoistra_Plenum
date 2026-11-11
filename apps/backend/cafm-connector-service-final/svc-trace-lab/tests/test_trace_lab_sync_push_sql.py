"""Pushing the eval store to another Trace Lab database (11 Nov 2026, task plan B5), on two throwaway databases.

B5.2 says "running the sync twice creates no duplicates", and B5.4 that sessions, traces, spans, scores and
annotations arrive. Both follow from one rule: every row is inserted under its own key or not at all, never changed.
So these tests push twice, break a push in the middle and push again, add a score and push again, and count both
sides every time. The refusals matter as much: the same database, a database with no Trace Lab in it, and one on
another migration are all refused BEFORE anything is written, in words that never contain the address.
Skips without TRACE_LAB_TEST_DB_URL."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from eval_fixtures import T0, member, seed_score, seed_session
from tl_scratch import SCRATCH_URL, lab_scratch, requires_scratch  # noqa: F401
from trace_lab import migrate, runs, scenario_file, scenario_gen, sync_push, trace_labels

pytestmark = requires_scratch

DEST_DB = "tracelab_scratch_dest"
SECRET = "s3cret-never-shown"
COUNTED = ("run_tags", "scenarios", "sessions", "traces", "observations", "scores")
_PORT = make_url(SCRATCH_URL).port
#: What is shown of the other database: host and database, and the port when it is not the usual one.
PEER = f"127.0.0.1{'' if _PORT in (None, 5432) else f':{_PORT}'}/{DEST_DB}"
MT = "scn__baseline_v0__MT-001__1"
SD = "scn__baseline_v0__SD-001__1"


def _nonzero(counts: dict[str, int]) -> dict[str, int]:
    return {k: v for k, v in counts.items() if v}


def _url(database: str, *, password: str | None = None) -> str:
    u = make_url(SCRATCH_URL).set(database=database)
    if password:
        u = u.set(password=password)
    return u.render_as_string(hide_password=False)


async def _fresh_database(name: str) -> None:
    admin = create_async_engine(_url("postgres"), poolclass=NullPool, isolation_level="AUTOCOMMIT")
    async with admin.connect() as c:
        if not (await c.execute(text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name})).scalar():
            await c.execute(text(f'CREATE DATABASE "{name}"'))
    await admin.dispose()
    engine = create_async_engine(_url(name), poolclass=NullPool)
    async with engine.begin() as c:
        await c.exec_driver_sql("DROP SCHEMA IF EXISTS tracelab CASCADE")
    await engine.dispose()


@pytest.fixture
async def dest(monkeypatch):
    """The other Trace Lab database, with every migration applied, and DEV_DB_URL pointing at it (with a password
    the trust-auth scratch server ignores, so a test can prove it is never written anywhere)."""
    await _fresh_database(DEST_DB)
    engine = create_async_engine(_url(DEST_DB), poolclass=NullPool)
    st = await migrate.apply_pending(engine, applied_by="pytest")
    assert st.status == "ready", st.as_dict()
    monkeypatch.setenv("DEV_DB_URL", _url(DEST_DB, password=SECRET))
    yield engine
    await engine.dispose()


async def _counts(engine) -> dict[str, int]:
    async with engine.connect() as c:
        return {t: (await c.execute(text(f"SELECT count(*) FROM tracelab.{t}"))).scalar_one() for t in COUNTED}


async def _seed(lab) -> None:
    await seed_session(lab)                                                        # 1 session, 2 traces, 8 spans
    await seed_session(lab, "scn__baseline_v0__SD-001__1", turn_ids=("turn-3",))    # 1 session, 1 trace, 4 spans
    await seed_score(lab, "turn-1")
    await seed_score(lab, "turn-1", name="ux", source="ANNOTATION", verdict="fail", value=0.25, comment="Too long.")


async def _log(lab) -> list[dict]:
    async with lab.connect() as c:
        return [dict(r) for r in (await c.execute(text(
            "SELECT peer, dry_run, status, watermark, pushed::text AS pushed, error, started_by_email"
            " FROM tracelab.sync_log ORDER BY finished_at"))).mappings().all()]


async def test_everything_arrives_and_the_result_says_how_much(lab_scratch, dest):
    await _seed(lab_scratch)
    out = await sync_push.push(by=member())
    assert out["status"] == "done" and out["dry_run"] is False and out["peer"] == PEER
    assert {t: out["pushed"][t] for t in COUNTED} == {"run_tags": 1, "scenarios": 0, "sessions": 2, "traces": 3,
                                                      "observations": 12, "scores": 2}
    assert await _counts(dest) == await _counts(lab_scratch)
    async with dest.connect() as c:
        got = (await c.execute(text("SELECT string_value, value, comment, author_email FROM tracelab.scores"
                                    " WHERE source = 'ANNOTATION'"))).mappings().one()
    assert dict(got) == {"string_value": "fail", "value": pytest.approx(0.25), "comment": "Too long.",
                         "author_email": "reviewer@example.com"}


async def test_the_rows_arrive_exactly_as_they_were(lab_scratch, dest):
    await _seed(lab_scratch)
    await sync_push.push()
    q = ("SELECT to_jsonb(o)::text FROM tracelab.observations o ORDER BY trace_id, id")
    async with lab_scratch.connect() as a, dest.connect() as b:
        assert (await a.execute(text(q))).all() == (await b.execute(text(q))).all()
        q2 = "SELECT to_jsonb(t)::text FROM tracelab.traces t ORDER BY id"
        assert (await a.execute(text(q2))).all() == (await b.execute(text(q2))).all()


async def test_pushing_twice_creates_no_duplicates(lab_scratch, dest):
    await _seed(lab_scratch)
    await sync_push.push()
    again = await sync_push.push(full=True)
    assert again["status"] == "done" and all(n == 0 for n in again["pushed"].values()), again["pushed"]
    assert await _counts(dest) == await _counts(lab_scratch)


async def test_an_interrupted_push_is_finished_by_the_next_one(lab_scratch, dest, monkeypatch):
    await _seed(lab_scratch)
    real = sync_push._insert_batch

    async def breaks_at_traces(conn, table, rows):
        if table == "traces":
            raise RuntimeError("connection lost while writing the row (a tenant's answer text)")
        return await real(conn, table, rows)

    monkeypatch.setattr(sync_push, "_insert_batch", breaks_at_traces)
    first = await sync_push.push()
    assert first["status"] == "failed" and "traces" in first["error"] and first["pushed"]["sessions"] == 2
    assert first["error"] == "traces: an unexpected error (RuntimeError)", "the kind of failure, never the driver's words"
    assert "answer text" not in repr(await _log(lab_scratch))
    assert (await _counts(dest))["traces"] == 0
    monkeypatch.setattr(sync_push, "_insert_batch", real)
    second = await sync_push.push()
    assert second["status"] == "done" and second["pushed"]["sessions"] == 0 and second["pushed"]["traces"] == 3
    assert await _counts(dest) == await _counts(lab_scratch)
    assert [r["status"] for r in await _log(lab_scratch)] == ["failed", "done"]


async def test_a_score_added_after_the_first_push_is_sent_by_the_next(lab_scratch, dest):
    await _seed(lab_scratch)
    await sync_push.push()
    await seed_score(lab_scratch, "turn-3", "scn__baseline_v0__SD-001__1", name="domain", source="ANNOTATION",
                     verdict="pass", value=1.0)
    out = await sync_push.push()
    assert out["pushed"]["scores"] == 1 and out["pushed"]["sessions"] == 0
    assert (await _counts(dest))["scores"] == 3


async def test_only_rows_newer_than_the_last_finished_push_are_read(lab_scratch, dest, monkeypatch):
    await _seed(lab_scratch)
    assert await sync_push.since() is None, "nothing pushed yet: everything is read"
    start = datetime.now(timezone.utc)
    await sync_push.push()
    mark = await sync_push.since()
    assert mark is not None and start - sync_push.OVERLAP - timedelta(seconds=5) <= mark <= datetime.now(timezone.utc)
    assert await sync_push.since(full=True) is None

    async def later(full=False):
        return datetime.now(timezone.utc) + timedelta(hours=1)

    monkeypatch.setattr(sync_push, "since", later)
    await _fresh_database(DEST_DB)                                  # an empty dev database again...
    engine = create_async_engine(_url(DEST_DB), poolclass=NullPool)
    await migrate.apply_pending(engine, applied_by="pytest")
    out = await sync_push.push()                                    # ...and nothing is "new", so nothing is sent
    assert all(n == 0 for n in out["pushed"].values())
    assert (await _counts(engine))["sessions"] == 0
    await engine.dispose()


async def test_a_dry_run_counts_what_would_go_and_writes_nothing(lab_scratch, dest):
    await _seed(lab_scratch)
    out = await sync_push.push(dry_run=True)
    assert out["dry_run"] is True and out["pushed"]["sessions"] == 2 and out["pushed"]["observations"] == 12
    assert (await _counts(dest))["sessions"] == 0
    assert await sync_push.since() is None, "a dry run is not a finished push"
    await sync_push.push()
    assert (await sync_push.push(dry_run=True))["pushed"]["sessions"] == 0


async def test_the_same_database_is_refused_and_nothing_is_written(lab_scratch, monkeypatch):
    await _seed(lab_scratch)
    monkeypatch.setenv("DEV_DB_URL", _url("tracelab_scratch", password=SECRET))
    with pytest.raises(sync_push.Refused) as e:
        await sync_push.push()
    assert e.value.reason == "same_database" and SECRET not in str(e.value.error)
    log = await _log(lab_scratch)
    assert [r["status"] for r in log] == ["refused"] and log[0]["peer"] == f"{PEER.rsplit('/', 1)[0]}/{make_url(SCRATCH_URL).database}"


async def test_a_database_without_trace_lab_is_refused(lab_scratch, monkeypatch):
    await _fresh_database(DEST_DB)                                  # exists, but no tracelab schema in it
    monkeypatch.setenv("DEV_DB_URL", _url(DEST_DB))
    with pytest.raises(sync_push.Refused) as e:
        await sync_push.push()
    assert e.value.reason == "schema_absent"


async def test_a_database_on_another_migration_is_refused_and_says_which(lab_scratch, monkeypatch):
    await _seed(lab_scratch)
    await _fresh_database(DEST_DB)
    engine = create_async_engine(_url(DEST_DB), poolclass=NullPool)
    await migrate.apply_pending(engine, applied_by="pytest", through=13)
    await engine.dispose()
    monkeypatch.setenv("DEV_DB_URL", _url(DEST_DB))
    with pytest.raises(sync_push.Refused) as e:
        await sync_push.push()
    assert e.value.reason == "schema_version"
    assert e.value.error == "The dev database is on schema 13 and this one is on 15. Apply the same migrations to both, then push again."


async def test_the_address_and_its_password_are_never_written_or_returned(lab_scratch, dest):
    await _seed(lab_scratch)
    out = await sync_push.push(by=member())
    log = await _log(lab_scratch)
    assert SECRET not in repr(out) and SECRET not in repr(log)
    assert log[0]["peer"] == PEER and log[0]["started_by_email"] == "reviewer@example.com"
    st = await sync_push.status()
    assert SECRET not in repr(st)
    assert st["enabled"] is True and st["peer"] == PEER and st["last"]["status"] == "done"
    assert st["last"]["pushed"]["sessions"] == 2


async def test_status_says_when_there_is_nowhere_to_push_to(lab_scratch, monkeypatch):
    monkeypatch.delenv("DEV_DB_URL", raising=False)
    assert await sync_push.status() == {"enabled": False, "peer": None, "last": None}


# ── what the dev database already holds is never changed, and never stops a push ───────────────

async def test_a_trace_the_dev_database_cannot_take_is_counted_and_does_not_stop_the_push(lab_scratch, dest):
    await seed_session(dest, turn_ids=("turn-x",))       # another machine filed this session's first turn as another trace
    await _seed(lab_scratch)
    out = await sync_push.push()
    assert out["status"] == "done", out["error"]
    assert _nonzero(out["conflicts"]) == {"traces": 1, "observations": 4, "scores": 2}
    assert (out["pushed"]["traces"], out["pushed"]["observations"], out["pushed"]["scores"]) == (2, 8, 0)
    async with dest.connect() as c:
        kept = (await c.execute(text("SELECT id FROM tracelab.traces WHERE session_id = :s ORDER BY turn_index"),
                                {"s": MT})).scalars().all()
    assert kept == ["turn-x", "turn-2"], "what the dev database held is left as it was"
    assert "7 rows were left out" in sync_push.summary_line(out)
    st = await sync_push.status()
    assert st["last"]["conflicts"]["traces"] == 1 and set(st["last"]["pushed"]) == set(sync_push.TABLES)
    again = await sync_push.push(full=True)
    assert again["status"] == "done" and all(n == 0 for n in again["pushed"].values())
    assert _nonzero(again["conflicts"]) == {"traces": 1, "observations": 4, "scores": 2}


async def test_a_row_the_dev_database_holds_differently_is_counted_and_left_as_it_is(lab_scratch, dest):
    await seed_session(dest, SD, turn_ids=("turn-3",), at=T0 + timedelta(hours=1))    # the same ids, other times
    await _seed(lab_scratch)
    for dry_run in (True, False):
        out = await sync_push.push(dry_run=dry_run)
        assert _nonzero(out["conflicts"]) == {"sessions": 1, "traces": 1, "observations": 4}, f"dry_run={dry_run}"
    async with dest.connect() as c:
        started = (await c.execute(text("SELECT started_at FROM tracelab.sessions WHERE id = :s"), {"s": SD})).scalar()
    assert started == T0 + timedelta(hours=1)


async def test_the_same_rows_made_on_two_machines_are_not_a_conflict(lab_scratch, dest):
    await _seed(dest)                                   # only when each was made and by which machine differs
    await _seed(lab_scratch)
    out = await sync_push.push()
    assert out["status"] == "done" and _nonzero(out["conflicts"]) == {}
    assert _nonzero(out["pushed"]) == {"scores": 2}, "a score is its own row on each machine"


async def test_what_counts_as_new_is_measured_on_the_databases_clock_not_this_machines(lab_scratch, dest, monkeypatch):
    class WrongClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2000, 1, 1, tzinfo=timezone.utc)

    await _seed(lab_scratch)
    monkeypatch.setattr(sync_push, "datetime", WrongClock)
    await sync_push.push()
    async with lab_scratch.connect() as c:
        now = (await c.execute(text("SELECT now()"))).scalar()
    mark = (await _log(lab_scratch))[-1]["watermark"]
    assert abs((now - mark).total_seconds()) < 120


async def test_a_full_batch_of_scenarios_is_counted_and_pushed_without_harming_either_database(lab_scratch, dest):
    # A full batch of wide rows already on the dev database once crashed the server (signal 11) while the rows were
    # compared: removing the bookkeeping keys with a bound text[] did it. Nothing pushed scenarios before this test.
    scenarios, _dropped = scenario_gen.generate(count=250, seed=3)
    await scenario_file.save_scenarios(member(), scenarios, source="generator")
    first = await sync_push.push()
    assert first["status"] == "done" and first["pushed"]["scenarios"] == 250, first["error"]
    more, _dropped = scenario_gen.generate(count=250, seed=4)        # one batch of 500: half already there
    await scenario_file.save_scenarios(member(), more, source="generator")
    for dry_run, new in ((True, 250), (False, 250), (False, 0)):
        again = await sync_push.push(dry_run=dry_run, full=True)
        assert again["status"] == "done", again["error"]
        assert again["pushed"]["scenarios"] == new and _nonzero(again["conflicts"]) == {}, f"dry_run={dry_run}"


async def test_failure_mode_labels_on_traces_are_not_pushed_until_their_modes_travel_too(lab_scratch, dest):
    # A label names its failure mode by lineage, and the modes themselves are not pushed (they are numbered per
    # database), so a pushed label would point at a mode the dev database does not have and never show there.
    await _seed(lab_scratch)
    lin = (await runs.save_mode(member(), name="Wrong building"))["lineage_id"]
    await trace_labels.set_label(member(), "turn-1", mode=lin, value="positive")
    out = await sync_push.push()
    assert out["status"] == "done" and "trace_label_cells" not in out["pushed"], out["error"]
    assert "label" not in sync_push.summary_line(out)
    async with dest.connect() as c:
        assert (await c.execute(text("SELECT count(*) FROM tracelab.trace_label_cells"))).scalar_one() == 0
