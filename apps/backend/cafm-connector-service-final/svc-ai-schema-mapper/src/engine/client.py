"""Run one hoist-engine command and relay what it reports.

The engine reads a job file and writes newline-delimited JSON events on stdout: progress and
log lines while it works, then exactly one ``result`` or ``error``. This module owns the
process: it writes the job, relays events to the caller, enforces a timeout, and kills the
engine (its whole process group) when the node is cancelled, so a stopped run never leaves an
engine writing behind it.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import tempfile
from pathlib import Path
from typing import Awaitable, Callable, Optional

logger = logging.getLogger(__name__)

DEFAULT_BIN = "/usr/local/bin/hoist-engine"
_STDERR_KEEP = 64 * 1024
_LINE_LIMIT = 16 * 1024 * 1024  # results stay small (big outputs are files), but never truncate one

OnEvent = Callable[[dict], Awaitable[None]]


class EngineError(RuntimeError):
    """The engine failed. ``code`` is the engine's (protocol.go) or one of: unavailable, timeout."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


def engine_binary() -> Optional[str]:
    path = os.environ.get("HOIST_ENGINE_BIN") or DEFAULT_BIN
    return path if os.path.isfile(path) and os.access(path, os.X_OK) else None


def engine_available() -> bool:
    return engine_binary() is not None


def _kill(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


async def _reap(proc: asyncio.subprocess.Process, *streams) -> None:
    """Kill the engine and wait for it. asyncio counts a process as gone only once its pipes have
    closed too, and a pipe whose reader stopped at a full buffer is never seen to close: whatever
    those streams still hold is read and dropped first."""
    _kill(proc)
    for stream in streams:
        if stream is None:
            continue
        try:
            while await stream.read(1 << 16):
                pass
        except Exception:  # noqa: BLE001 — the stream goes with the process
            pass
    await proc.wait()


async def run_engine(
    command: str,
    job: dict,
    *,
    workdir: "str | os.PathLike | None" = None,
    env: "dict[str, str] | None" = None,
    on_event: Optional[OnEvent] = None,
    timeout_s: Optional[float] = None,
) -> dict:
    binary = engine_binary()
    if not binary:
        raise EngineError("unavailable", "hoist-engine is not installed in this image")
    wd = Path(workdir) if workdir else Path(tempfile.mkdtemp(prefix="hoist-engine-"))
    wd.mkdir(parents=True, exist_ok=True)
    job_path = wd / f"{command}.job.json"
    job_path.write_text(json.dumps(job, default=str), encoding="utf-8")

    proc = await asyncio.create_subprocess_exec(
        binary, command, "--job", str(job_path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        env={**os.environ, **(env or {})}, start_new_session=True, limit=_LINE_LIMIT,
    )
    stderr_tail = bytearray()
    result: Optional[dict] = None
    error: Optional[EngineError] = None

    async def _stdout() -> None:
        nonlocal result, error
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                logger.debug("[engine] ignored a non-JSON line from %s: %r", command, line[:200])
                continue
            kind = ev.get("type")
            if kind == "result":
                result = ev.get("result") or {}
            elif kind == "error":
                error = EngineError(str(ev.get("code") or "internal"), str(ev.get("message") or ""))
            elif on_event is not None:
                try:
                    await on_event(ev)
                except Exception as exc:  # noqa: BLE001 — a relay problem never fails the step
                    logger.warning("[engine] event relay failed: %s", exc)

    async def _stderr() -> None:
        assert proc.stderr is not None
        async for raw in proc.stderr:
            stderr_tail.extend(raw)
            if len(stderr_tail) > _STDERR_KEEP:
                del stderr_tail[: len(stderr_tail) - _STDERR_KEEP]

    try:
        await asyncio.wait_for(asyncio.gather(_stdout(), _stderr(), proc.wait()), timeout=timeout_s)
    except asyncio.TimeoutError:
        await _reap(proc, proc.stdout, proc.stderr)
        raise EngineError("timeout", f"{command} did not finish within {timeout_s:.0f}s") from None
    except asyncio.CancelledError:
        _kill(proc)
        raise
    except Exception as exc:  # noqa: BLE001 — reading its output failed (a line past the limit)
        # The engine must not outlive the step: it would sit blocked on a pipe nobody reads.
        await _reap(proc, proc.stdout)
        raise EngineError("internal", f"{command}: could not read the engine's output: {exc}") from exc
    if error is not None:
        raise error
    if proc.returncode != 0 or result is None:
        tail = stderr_tail.decode("utf-8", "replace").strip()[-2000:]
        raise EngineError("internal", f"{command} exited {proc.returncode}: {tail or 'no output'}")
    return result
