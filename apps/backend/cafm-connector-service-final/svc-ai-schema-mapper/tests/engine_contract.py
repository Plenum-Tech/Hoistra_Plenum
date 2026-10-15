"""A job a node built, sent to the real hoist-engine in check mode (``--check``): it must decode
against the command's own job struct, which refuses unknown fields. The node tests replace the
binary with fakes that take any job, so this is where the two sides' job contract is held."""
from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from src.engine.client import engine_binary


def assert_the_engine_takes(command: str, job: dict) -> None:
    binary = engine_binary()
    if not binary:
        pytest.skip("hoist-engine not built (engine/scripts/dev.sh build-linux)")
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / f"{command}.job.json"
        path.write_text(json.dumps(job, default=str), encoding="utf-8")  # as run_engine writes it
        out = subprocess.run([binary, command, "--job", str(path), "--check"],
                             capture_output=True, text=True, timeout=60)
    last = json.loads(out.stdout.strip().splitlines()[-1])
    assert last == {"type": "result", "result": {"job": "ok"}}, last
