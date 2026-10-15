"""Which runs, and which of their steps, the engine does.

A run's engine is decided once, when it starts, and kept on its state (``engine``) for every
later step and every resume — flipping MIGRATION_ENGINE mid-run must not leave half a run on
each path. Within a Go run, a step is done by the engine only if this build has it
(ENGINE_STEPS); the rest stay on their Python code, and the two hand rows to each other through
the engine's Arrow files (engine/store.py).
"""
from __future__ import annotations

import os
from collections.abc import Mapping

from .client import engine_available

ENGINE_GO = "go"
ENGINE_PYTHON = "python"

#: Steps this build of the engine does. Grows phase by phase (see the plan): write → outputs →
#: parse → preprocess.
ENGINE_STEPS: frozenset[str] = frozenset({"write", "outputs", "parse", "preprocess"})

#: The graph node whose step pause the engine makes unnecessary, per engine step.
STEP_NODE_OF = {
    "parse": "ingest_node",
    "preprocess": "preprocess_node",
    "outputs": "output_generator_node",
}

_GO_FORMATS = frozenset({".csv", ".tsv", ".xlsx", ".xlsm"})


def configured_engine() -> str:
    from ..config import get_settings

    value = (os.environ.get("MIGRATION_ENGINE") or get_settings().migration_engine or ENGINE_GO).strip().lower()
    return ENGINE_PYTHON if value == ENGINE_PYTHON else ENGINE_GO


def _ext(name: "str | None") -> str:
    base = str(name or "").strip().rsplit("/", 1)[-1]
    return ("." + base.rsplit(".", 1)[-1].lower()) if "." in base else ""


def choose_engine(*, source_filename: "str | None" = None, source_blob_path: "str | None" = None) -> str:
    if configured_engine() != ENGINE_GO or not engine_available():
        return ENGINE_PYTHON
    # The durable source decides: a multi-file upload is one combined.xlsx, whatever it was made from.
    for candidate in (source_blob_path, source_filename):
        ext = _ext(candidate)
        if ext:
            return ENGINE_GO if ext in _GO_FORMATS else ENGINE_PYTHON
    return ENGINE_PYTHON


def uses_go(state: Mapping, step: str) -> bool:
    return (state or {}).get("engine") == ENGINE_GO and step in ENGINE_STEPS


def go_auto_continue_nodes() -> frozenset[str]:
    return frozenset(STEP_NODE_OF[s] for s in ENGINE_STEPS if s in STEP_NODE_OF)
