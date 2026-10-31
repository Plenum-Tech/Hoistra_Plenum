# svc-trace-lab

Hoist Trace Lab: an evaluation module for the Hoist AI chat. It reads golden question sets,
grades recorded chat turns with deterministic checks, and reports the results with honest
statistics. It is a separate module in its own folder, run as its own process.

## The Golden Rule (G0)

Existing application code is never edited for Trace Lab, and the existing application must not
be affected by it. Trace Lab only ever reuses existing code by importing it (it imports
svc-deepagents as a library, for example `from src.agents.skill_lab import WRITE_INTENT`).

* Nothing in any other service folder imports `trace_lab`.
* Nothing is created or changed inside `svc-deepagents/` (its `src/` and `tests/` hold nothing
  named after Trace Lab).
* Trace Lab may read the `plenum_cafm` tables, but never writes to or changes one. It keeps its
  own data in its own schema.
* Importing `trace_lab` loads no application module.

`tests/test_trace_lab_golden_rule.py` enforces all four, and carries meta-tests so the guards
can never pass by quietly looking at nothing. An existing module may only import Trace Lab after
Hussain approves it and its path is added to `ALLOWED_ATTACHMENTS` in that test file.

## Layout

```
svc-trace-lab/
  pyproject.toml   the service's direct dependencies; pytest paths include ../svc-deepagents
  Dockerfile       the trace-lab image (build context: cafm-connector-service-final)
  trace_lab/       the package
  tests/           the tests
```

## Running the tests

From anywhere, on a fresh copy inside the testenv container (Python 3.12, svc-deepagents beside
it, every database URL pinned to a dead address):

```
sh /Users/hussain/Desktop/hoist/tracelab-work/sdd/tltest.sh tests/test_trace_lab_golden_rule.py
```

Paths are relative to this folder. Once svc-deepagents' dependencies are installed in your own
environment, plain `python -m pytest` from this folder works too.

Tests use no database, no network and no real model calls.

## Running the service

Trace Lab runs as its own process and container (`trace-lab`, port 8010):

    uvicorn trace_lab.app:app --host 0.0.0.0 --port 8010 --root-path /backend/trace-lab

The image is built from `apps/backend/cafm-connector-service-final` with `svc-trace-lab/Dockerfile`.
It copies svc-deepagents' `src` and `skills` beside this folder and installs svc-deepagents'
dependencies from its own `pyproject.toml`; nothing in svc-deepagents is edited. Locally it starts
only with the `tracelab` profile of the repository's `docker-compose.tracelab.yml`, a new file (the
existing compose files are not edited). Every setting comes from the environment (G0 addendum 2.2);
never bake a `.env` file into the image.
