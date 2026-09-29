"""A long step says it is still working.

The run card calls a run stalled after three minutes in which the job row says nothing new.
On 29 Sep 2026 (migration f87078d7, 289,606 rows) the output step spent 13 minutes uploading
180 MB of artefacts from a laptop — single files took 3.5 minutes — and the card told the user
the migration worker was probably not running while it was busy the whole time. A step that
moves bytes or rows now beats progress_pct as it goes, a few times a minute, so silence means
a stall again.
"""
from __future__ import annotations

import asyncio

from src.graph.progress_beat import ProgressBeat


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def _beat(lo=80.0, hi=88.0, every=15.0):
    written: list[float] = []

    async def write(migration_id, pct):
        assert migration_id == "m-1"
        written.append(pct)

    clock = _Clock()
    return ProgressBeat("m-1", lo, hi, every=every, write=write, clock=clock), written, clock


def test_the_first_tick_beats_and_the_next_ones_wait_their_turn():
    beat, written, clock = _beat()

    async def go():
        await beat.tick(10, 100)
        clock.t += 5
        await beat.tick(20, 100)
        clock.t += 11
        await beat.tick(30, 100)

    asyncio.run(go())
    assert written == [80.8, 82.4]


def test_the_beat_stays_inside_its_band_and_never_goes_back():
    beat, written, clock = _beat()

    async def go():
        for done in (50, 200, 30):  # 200 of 100: a hook that over-reports; 30: one that restarts
            await beat.tick(done, 100)
            clock.t += 20

    asyncio.run(go())
    assert written == [84.0, 88.0]  # the restart repeats 88.0, so it is not written again


def test_nothing_to_count_writes_nothing():
    beat, written, _ = _beat()
    asyncio.run(beat.tick(0, 0))
    assert written == []


def test_a_beat_that_cannot_be_written_never_fails_the_step():
    async def write(migration_id, pct):
        raise RuntimeError("database gone")

    beat = ProgressBeat("m-1", 80.0, 88.0, write=write, clock=_Clock())
    asyncio.run(beat.tick(5, 10))  # does not raise


def test_no_migration_id_writes_nothing():
    written: list[float] = []

    async def write(migration_id, pct):
        written.append(pct)

    asyncio.run(ProgressBeat(None, 80.0, 88.0, write=write, clock=_Clock()).tick(5, 10))
    assert written == []


# ── The output step's uploads beat as the bytes go ─────────────────────────────────────


class _Client:
    def __init__(self, path, fail):
        self.url = "https://blob.example/" + path
        self.fail = fail

    async def upload_blob(self, data, overwrite=False, progress_hook=None):
        if self.fail:
            raise RuntimeError("403 from Blob")
        if progress_hook:
            await progress_hook(len(data) // 2, len(data))
            await progress_hook(len(data), len(data))


class _Svc:
    def __init__(self, fail=()):
        self.fail = set(fail)

    def get_blob_client(self, container, blob):
        return _Client(blob, blob.rsplit("/", 1)[-1] in self.fail)


def test_every_uploaded_byte_counts_towards_the_beat():
    from src.graph.nodes.output_generator_node import upload_artefacts

    beat, written, clock = _beat(every=0.0)
    clock_ticks = iter(range(10_000))
    beat._clock = lambda: float(next(clock_ticks))
    lines: list[str] = []
    artefacts = {"output.json": "x" * 300, "output.sql": b"y" * 100}
    urls, n = asyncio.run(upload_artefacts(_Svc(), "c", "migrations/m-1", artefacts, beat, lines.append))
    assert n == 2 and set(urls) == {"output.json", "output.sql"}
    assert written == sorted(written) and written[-1] == 88.0
    assert 83.0 in written  # half of output.json: 150 of 400 bytes


def test_a_file_that_fails_to_upload_is_said_and_the_rest_still_go():
    from src.graph.nodes.output_generator_node import upload_artefacts

    beat, _, _ = _beat()
    lines: list[str] = []
    urls, n = asyncio.run(upload_artefacts(_Svc(fail={"output.json"}), "c", "migrations/m-1",
                                           {"output.json": "x", "output.sql": "y"}, beat, lines.append))
    assert n == 1 and list(urls) == ["output.sql"]
    assert any("Failed to upload output.json" in ln for ln in lines)


# ── The write step beats as the rows go ─────────────────────────────────────────────────


def test_a_counted_beat_adds_up_what_each_batch_did():
    beat, written, _ = _beat(every=0.0)
    beat.total = 1000

    async def go():
        await beat.advance(500)
        await beat.advance(500)

    asyncio.run(go())
    assert written == [84.0, 88.0]


def _insert(pending, beat, session=None):
    from tests.test_a_year_of_readings_goes_in_batches import _Session, wn

    return asyncio.run(wn._insert_rows(
        session or _Session(), schema_name="plenum_cafm", table_name="meter_readings",
        pending=pending, unique_sets=set(), nullable_cols=set(), beat=beat))


def test_each_batch_the_write_step_sends_counts_towards_its_beat():
    from tests.test_a_year_of_readings_goes_in_batches import rows

    beat, written, _ = _beat(every=0.0)
    beat.total = 1000
    _insert(rows(500), beat)
    assert written == [84.0]


def test_a_batch_retried_row_by_row_still_counts_each_row_once():
    from tests.test_a_year_of_readings_goes_in_batches import _Session, rows

    beat, written, _ = _beat(every=0.0)
    beat.total = 40
    out = _insert(rows(20), beat, _Session(fail_on=lambda p: p.get("reading_at") == "t7"))
    assert out["inserted"] == 19
    assert written == [84.0]


def test_the_sql_script_beats_per_statement(monkeypatch):
    from tests.test_a_year_of_readings_goes_in_batches import wn

    ran: list[str] = []

    class _S:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def execute(self, stmt):
            ran.append(str(stmt))

        async def commit(self):
            pass

        async def rollback(self):
            pass

    monkeypatch.setattr(wn, "get_async_session_factory", lambda: (lambda: _S()))
    beat, written, _ = _beat(every=0.0)
    script = "INSERT INTO plenum_cafm.sites (id) VALUES ('a');\nINSERT INTO plenum_cafm.sites (id) VALUES ('b');\n"
    out = asyncio.run(wn._apply_sql_artifact(script, beat=beat))
    assert out["statement_count"] == 2 and len(ran) == 2
    assert beat.total == 2 and written == [84.0, 88.0]
