"""The schema-mapper's side of the hoist-engine contract (engine/README.md)."""
import asyncio
import os
import stat
import sys
import textwrap

import pytest

from src.engine.client import EngineError, engine_available, run_engine


def _fake_engine(tmp_path, body: str) -> str:
    """An executable that behaves like hoist-engine, written in Python."""
    path = tmp_path / "fake-engine"
    path.write_text(f"#!{sys.executable}\n" + textwrap.dedent(body))
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def fake(tmp_path, monkeypatch):
    def make(body):
        monkeypatch.setenv("HOIST_ENGINE_BIN", _fake_engine(tmp_path, body))
    return make


async def test_the_result_comes_back_and_progress_is_relayed(fake, tmp_path):
    fake("""
        import json, sys
        print(json.dumps({"type": "progress", "stage": "write", "table": "assets", "done": 5, "total": 10}))
        print("not json")
        print(json.dumps({"type": "result", "result": {"rows": 10, "argv": sys.argv[1:3]}}))
    """)
    seen = []

    async def on_event(ev):
        seen.append(ev)

    out = await run_engine("write", {"a": 1}, workdir=tmp_path, on_event=on_event)
    assert out == {"rows": 10, "argv": ["write", "--job"]}
    assert [e["type"] for e in seen] == ["progress"]


async def test_an_error_event_raises_with_its_code(fake, tmp_path):
    fake("""
        import json
        print(json.dumps({"type": "error", "code": "data_error", "message": "bad row"}))
        raise SystemExit(1)
    """)
    with pytest.raises(EngineError) as exc:
        await run_engine("parse", {}, workdir=tmp_path)
    assert exc.value.code == "data_error" and exc.value.message == "bad row"


async def test_a_crash_without_a_result_says_what_stderr_said(fake, tmp_path):
    fake("""
        import sys
        sys.stderr.write("segfault-ish trouble")
        raise SystemExit(3)
    """)
    with pytest.raises(EngineError) as exc:
        await run_engine("parse", {}, workdir=tmp_path)
    assert exc.value.code == "internal" and "segfault-ish trouble" in exc.value.message


async def test_a_step_that_overruns_its_timeout_is_killed(fake, tmp_path):
    fake("""
        import time
        time.sleep(60)
    """)
    with pytest.raises(EngineError) as exc:
        await run_engine("parse", {}, workdir=tmp_path, timeout_s=1)
    assert exc.value.code == "timeout"


async def test_cancelling_the_node_kills_the_engine(fake, tmp_path):
    pidfile = tmp_path / "pid"
    fake(f"""
        import os, time
        open({str(pidfile)!r}, "w").write(str(os.getpid()))
        time.sleep(60)
    """)
    task = asyncio.create_task(run_engine("parse", {}, workdir=tmp_path))
    for _ in range(100):
        if pidfile.exists() and pidfile.read_text():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pidfile.read_text())
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_no_binary_means_unavailable(monkeypatch, tmp_path):
    monkeypatch.setenv("HOIST_ENGINE_BIN", str(tmp_path / "missing"))
    assert engine_available() is False
    with pytest.raises(EngineError) as exc:
        await run_engine("parse", {}, workdir=tmp_path)
    assert exc.value.code == "unavailable"


async def test_secrets_in_env_never_reach_the_job_file(fake, tmp_path):
    fake("""
        import json, os, sys
        job = open(sys.argv[3]).read()
        print(json.dumps({"type": "result", "result": {"dsn": os.environ.get("HOIST_ENGINE_DSN"), "job": job}}))
    """)
    out = await run_engine("write", {"x": 1}, workdir=tmp_path, env={"HOIST_ENGINE_DSN": "postgres://s3cret"})
    assert out["dsn"] == "postgres://s3cret" and "s3cret" not in out["job"]


async def test_an_overlong_output_line_fails_the_step_and_kills_the_engine(fake, tmp_path, monkeypatch):
    # A line longer than the reader's limit (a result carrying a preview of huge cells) must fail
    # the step as an engine failure the node can fall back from, and must not leave the engine
    # alive, blocked on a pipe nobody reads any more.
    from src.engine import client

    monkeypatch.setattr(client, "_LINE_LIMIT", 4096)
    pidfile = tmp_path / "pid"
    fake(f"""
        import os, sys, time
        open({str(pidfile)!r}, "w").write(str(os.getpid()))
        sys.stdout.write("x" * 200000 + "\\n")
        sys.stdout.flush()
        time.sleep(60)
    """)
    with pytest.raises(EngineError) as exc:
        await asyncio.wait_for(run_engine("parse", {}, workdir=tmp_path), timeout=20)
    assert exc.value.code == "internal"
    pid = int(pidfile.read_text())
    await asyncio.sleep(0.3)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
