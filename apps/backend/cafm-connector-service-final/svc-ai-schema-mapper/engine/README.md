# hoist-engine

The Go half of a Hoistra migration: the schema-mapper's LangGraph pipeline keeps every decision
(mapping, the review gates, hierarchy, the write gate, UDR), and calls this binary for the
row-heavy steps — parse and profile, preprocess and validate, generate outputs, bulk write.
Design: `docs/superpowers/specs/2026-10-01-go-migration-engine-design.md`.

## Commands

`hoist-engine <command> --job <job.json>` — `version` prints the build and the commands it has.
Each further command is added by its plan task (`write`, `outputs`, `parse`, `combine`,
`preprocess`), plus `*-eval` commands the Python oracle tests call.

## How Python calls it

`src/engine/client.py::run_engine(command, job)` writes the job file, starts the binary, and reads
stdout: newline-delimited JSON events — `progress` and `log` while it works, then exactly one
`result` or `error` (`{"code", "message"}`; codes in `internal/protocol`). Exit 0 only with a
result. Secrets (the database DSN) come in the environment, never in the job or on argv.

## Build and test

No local Go toolchain is needed; everything runs in `golang:1.25-bookworm`:

    scripts/dev.sh test            # go vet + go test
    scripts/dev.sh test-db         # also the Postgres tests, against scripts/parity_db.sh's throwaway DB
    scripts/dev.sh build-linux     # bin/hoist-engine-linux-<arch>, used by the Python tests
    scripts/dev.sh tidy            # go mod tidy

The image build (`svc-ai-schema-mapper/Dockerfile`) runs the tests and installs the binary at
`/usr/local/bin/hoist-engine`.
