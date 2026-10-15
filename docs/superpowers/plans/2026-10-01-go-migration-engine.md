# Go Migration Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement Hussain's "Python decides, Go moves the rows" diagram for the migration graph (csv/tsv/xlsx/xlsm), taking the 280k-row run from ~27 min machine time to ~1–2 min while writing the same rows.

**Architecture:** A static Go binary `hoist-engine` in the schema-mapper image, called per step as a subprocess (job JSON in, NDJSON events out). Rows travel between steps as compressed Arrow IPC files (local volume + Blob), never through the LangGraph checkpoint. Python nodes branch on `state["engine"]`; each Go step replaces the Python step it names and must equal it (Python is the oracle in tests). UDR keeps its Python logic with memoised per-column statistics.

**Tech Stack:** Go 1.24 (`golang:1.24-bookworm` build image), `github.com/apache/arrow-go/v18` (IPC + zstd), `github.com/jackc/pgx/v5`, `golang.org/x/text`; Python 3.12, pyarrow 25.0.1, LangGraph 0.3.34, asyncpg 0.31; React/TS (CAFM vendored under `apps/frontend/src/cafm`).

**Spec:** `docs/superpowers/specs/2026-10-01-go-migration-engine-design.md`

**Deviation from the spec, agreed by reading the cost table:** the spec's delivery order is parse → preprocess → outputs → write. This plan builds **write first** (it and UDR are 1,304 of the 1,620 machine seconds) and makes every phase shippable on its own: until the Go parse/preprocess land, the Python steps' rows are handed to the Go steps through the same Arrow files (the "bridge", Task 4). `ENGINE_STEPS` (Task 3) names which steps are Go in this build.

## Global Constraints

- Work on branch `go`. **Never** `git commit`, `merge`, `rebase`, `reset`, `stash`, `push`, or `checkout` of tracked files — Hussain commits. Every "Checkpoint" step is `git status --short` + `git diff --stat` only.
- **No writes to the stack database or Azure.** Database tests run only against the throwaway container `hoist-parity-pg` on the internal Docker network `hoist-parity` (Task 7), created and removed by `engine/scripts/parity_db.sh`. Never point a test at `hoistra_plenum-postgres-1`, `PLENUM_DB_DSN` or Azure.
- Rebuild or recreate `schema-mapper-app` / `schema-mapper-worker` only when `redis-cli zcard arq:queue` is `0` and `redis-cli --scan --pattern 'arq:in-progress:*'` is empty (arq re-runs jobs it was killed in), always with `-f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml --no-deps --force-recreate`.
- Engine behaviour = the Python code it replaces. The cited Python function **is** the specification; ports reproduce it rule for rule and an oracle test (Python computes the expected output at test time) pins it. The only intentional differences are the four in the spec's "Intentional behaviour changes" table.
- Gate payloads and resume bodies keep their shapes; only additive keys (`plan`, `engine`, `engine_progress`).
- Go module `hoistra/engine` at `apps/backend/cafm-connector-service-final/svc-ai-schema-mapper/engine/`, `go 1.24`, `CGO_ENABLED=0`. Dependencies limited to: `github.com/apache/arrow-go/v18`, `github.com/jackc/pgx/v5`, `golang.org/x/text`. No other third-party Go modules.
- No new Python dependencies (pyarrow, asyncpg, azure-storage-blob are already installed).
- Secrets only through the environment (`HOIST_ENGINE_DSN`): never on argv, never in a log line, never in a job file.
- Kill switch: `MIGRATION_ENGINE=python` (env) makes every new run use today's code. The choice is made once per run and stored in `state["engine"]`.
- User-facing copy is plain English and never says "Go" or "engine" outside the collapsed "Run details" section.

## Review Focus

1. **Excel dates stored as serials with custom number formats** (`dd/mm/yyyy hh:mm`, `[h]:mm`, a 1904-date workbook) must come out as the exact strings calamine + pandas produce — pinned by fixtures `dates_custom_formats.xlsx` and `dates_1904.xlsx` (Task 15) against the reader (Task 17).
2. **Two source columns renamed to the same target** must keep the *last* value at the *first* column's position, in both cleaned and renamed-full tables — pinned by the preprocess corpus case `rename_collision` (Task 19 oracle, Task 20 engine) and the CSV/XLSX frame rule for colliding sanitised names (Tasks 16–17).
3. **Re-running the same workbook into the same organisation** writes no second vendor, building, work order or meter and merges assets — pinned by DB-parity scenario `rerun_same_file` (Task 12).
4. **The database connection drops during apply**: everything rolls back, the run fails with the "nothing partial was kept" message and Retry — pinned by `TestApplyConnectionLostRollsBack` (Task 11) and `test_go_write_connection_lost_message` (Task 12).
5. **A step resumes in the other container, or after a restart, with no local engine files**: they are fetched from Blob; with neither, the run fails visibly instead of sitting at "running" — pinned by `test_publish_then_fetch_in_a_process_without_the_files` and `test_missing_engine_files_fail_the_node` (Task 4).

---

## How to run things

All paths are relative to the repository root `~/Desktop/hoist/Hoistra_Plenum` unless a command `cd`s.

```sh
SVC=apps/backend/cafm-connector-service-final/svc-ai-schema-mapper
ENG=$SVC/engine
```

- **Go tests** (no local Go toolchain needed): `$ENG/scripts/dev.sh test` — runs `go vet ./... && go test ./...` in `golang:1.24-bookworm` with module/build caches in Docker volumes `hoist-go-mod`, `hoist-go-build`. Tests that need Postgres skip unless `HOIST_PARITY_DSN` is set (Task 7 sets it via `dev.sh test-db`).
- **Engine binary for Python tests:** `$ENG/scripts/dev.sh build-linux` → `$ENG/bin/hoist-engine-linux-<arch>` (gitignored).
- **Python tests** (offline, worker image, read-only tree): `$SVC/scripts/test_offline.sh <pytest args>` (Task 2 creates it). It mounts the engine binary at `/usr/local/bin/hoist-engine` when `$ENG/bin/hoist-engine-linux-<arch>` exists, and joins `hoist-parity` when `HOIST_PARITY=1`.
- **Baseline before this work** (1 Oct 2026, `test_offline.sh tests --continue-on-collection-errors`): **353 passed, 38 errors** (3 script-style files fail at import; 35 need a live service). That is the floor: no new failures, no new errors.
- **Frontend tests:** `cd apps/frontend && . ~/.nvm/nvm.sh && nvm use 24.18.1 >/dev/null && npm test` (~120 s) then `npm run build`.

## File structure

### Go engine (new) — `$ENG/`

| File | Responsibility |
|---|---|
| `go.mod`, `go.sum`, `.gitignore`, `README.md` | module, pinned deps, ignore `bin/`, how to build/test |
| `scripts/dev.sh` | test / build / tidy inside `golang:1.24-bookworm` |
| `scripts/parity_db.sh` | create/destroy the throwaway Postgres (`up`, `down`, `reset`, `psql`) |
| `cmd/hoist-engine/main.go` | argv → command dispatch, signals, panic → error event |
| `internal/protocol/` | job decoding (strict), NDJSON emitter, error codes |
| `internal/pystr/` | Python `str` semantics: `Strip`, `Casefold`, `Lower`, `Len`, `FloatRepr`, `IntStr` |
| `internal/cell/` | the cell model (`Null`, `Str`, `Int`) with Python truthiness/`str()` |
| `internal/arrowtab/` | manifest + Arrow IPC read/write of tables (zstd, 64k-row batches) |
| `internal/pgschema/` | read-only introspection of destination tables |
| `internal/coerce/` | port of `_coerce_value_for_db_type` + `_infer_sql_type_for_value` |
| `internal/rules/` | port of `_normalize_row_for_table`, `_to_safe_identifier`, hints, `_NATURAL_KEYS`, `_system_default_for_db_type`, `_CORE_PARENTS`, write order |
| `internal/resolve/` | building / section / floor / meter / reference / asset resolvers (same SQL) |
| `internal/write/` | `write` command: plan (read-only) and apply (one transaction) |
| `internal/outputs/` | `outputs` command: CSV, SQL, JSON (incl. nested), XLSX, gzip |
| `internal/csvread/` | pandas C-tokenizer-compatible CSV reader |
| `internal/xlsxread/` | streaming XLSX reader with calamine + pandas cell conversion |
| `internal/ingest/` | `parse` and `combine` commands: tables, preview, NaN report, duplicate columns |
| `internal/preprocess/` | `preprocess` command: dedupe, null handling, dates, rename, EL-M.5, EL-4.0, link stats |
| `testdata/` | small committed fixtures + goldens generated by the Python oracle |

### Python (new) — `$SVC/`

| File | Responsibility |
|---|---|
| `src/engine/__init__.py` | re-exports |
| `src/engine/client.py` | run one engine command, relay events, timeouts, cancellation |
| `src/engine/selection.py` | `MIGRATION_ENGINE`, `ENGINE_STEPS`, `choose_engine`, auto-continue set |
| `src/engine/graph_proxy.py` | `MigrationGraphProxy`: per-run `interrupt_after` |
| `src/engine/store.py` | engine work dirs, Arrow bridge (typed), Blob publish/fetch, hydration cache |
| `src/engine/progress.py` | events → `ProgressBeat` + Redis live progress; status reader |
| `src/engine/steps.py` | the Go branch of each node (ingest, preprocess, outputs, write plan/apply) |
| `src/udr/memo.py` | memo scope for per-column UDR statistics |
| `scripts/test_offline.sh` | offline pytest runner (worker image) |
| `tests/engine_oracle/` | oracle helpers + golden generators (parse, preprocess, outputs, rules) |
| `tests/test_engine_*.py` | client, selection, proxy, store, steps, parity tests |

### Python (modified)

`src/graph/state.py` (4 channels), `src/graph/migration_graph.py` (`STEP_NODES`, proxy), `src/graph/bulk_tables.py` (go-run hydrate/dehydrate), `src/graph/nodes/{ingest_node,preprocess_node,hierarchy_node,output_generator_node,write_node,udr_node}.py`, `src/udr/{primitives,pipeline}.py`, `src/app.py` (multi-file combine branch, status fields), `src/schemas.py`, `Dockerfile`; repo root `deploy/allinone/Dockerfile`, `docker-compose.single-url.local.yml`.

### Frontend (modified/new) — `apps/frontend/`

`src/cafm/features/ai/chat-api.ts` (types), `src/cafm/hoistra-wizard-steps.js` (live progress text), `src/cafm/hoistra-migration-wizard.tsx` (progress line), `src/cafm/features/ai/pipeline/migration/gates/gate-final.tsx` + new `gate-final-plan.tsx` (write plan), tests in `test/`.

---

# Phase 1 — Foundations

### Task 1: Engine module, protocol and dev script

**Files:**
- Create: `$ENG/go.mod`, `$ENG/.gitignore`, `$ENG/README.md`, `$ENG/scripts/dev.sh`
- Create: `$ENG/cmd/hoist-engine/main.go`, `$ENG/cmd/hoist-engine/main_test.go`
- Create: `$ENG/internal/protocol/protocol.go`, `$ENG/internal/protocol/protocol_test.go`

**Interfaces:**
- Produces: CLI `hoist-engine <command> --job <path>`; stdout NDJSON events `{"type":"progress","stage","table","done","total"}`, `{"type":"log","level","message"}`, exactly one terminal `{"type":"result","result":{…}}` or `{"type":"error","code","message"}`; exit 0 only with a result, 1 on error, 2 on usage.
- Produces (Go): `protocol.NewEmitter(io.Writer) *Emitter` with `Progress(stage, table string, done, total int64)`, `Log(level, msg string)`, `Result(v any) error`, `Error(code, msg string)`; `protocol.ReadJob(path string, v any) error`; `protocol.Errorf(code, format, …) error`; `protocol.Classify(ctx, err) (code, msg string)`; codes `bad_job`, `unsupported`, `parse_error`, `data_error`, `db_error`, `connection_lost`, `ddl_failed`, `cancelled`, `internal`.
- Produces (Go): `main.register(name string, fn Command)` and `type Command func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error)`, used by every later command.

- [ ] **Step 1: Write the failing protocol tests**

`$ENG/internal/protocol/protocol_test.go`:

```go
package protocol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func lines(t *testing.T, b *bytes.Buffer) []map[string]any {
	t.Helper()
	var out []map[string]any
	for _, l := range strings.Split(strings.TrimSpace(b.String()), "\n") {
		if l == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			t.Fatalf("not JSON: %q (%v)", l, err)
		}
		out = append(out, m)
	}
	return out
}

func TestEmitterWritesOneJSONObjectPerLine(t *testing.T) {
	var b bytes.Buffer
	em := NewEmitter(&b)
	em.Log("info", "hello")
	em.Progress("parse", "Assets", 10, 10)
	if err := em.Result(map[string]any{"ok": true}); err != nil {
		t.Fatal(err)
	}
	got := lines(t, &b)
	if len(got) != 3 || got[0]["type"] != "log" || got[1]["type"] != "progress" || got[2]["type"] != "result" {
		t.Fatalf("unexpected events: %v", got)
	}
	if got[1]["table"] != "Assets" || got[1]["done"].(float64) != 10 {
		t.Fatalf("progress fields lost: %v", got[1])
	}
}

func TestProgressIsThrottledButTheLastTickAlwaysGoesOut(t *testing.T) {
	var b bytes.Buffer
	em := NewEmitter(&b)
	now := time.Unix(0, 0)
	em.now = func() time.Time { return now }
	for i := int64(1); i < 100; i++ {
		em.Progress("write", "assets", i, 100)
	}
	em.Progress("write", "assets", 100, 100)
	got := lines(t, &b)
	if len(got) != 2 {
		t.Fatalf("want first + final tick, got %d: %v", len(got), got)
	}
	if got[1]["done"].(float64) != 100 {
		t.Fatalf("final tick missing: %v", got)
	}
}

func TestReadJobRejectsUnknownFields(t *testing.T) {
	dir := t.TempDir()
	p := filepath.Join(dir, "job.json")
	os.WriteFile(p, []byte(`{"input":"a","surprise":1}`), 0o600)
	var job struct {
		Input string `json:"input"`
	}
	err := ReadJob(p, &job)
	var pe *Error
	if !errors.As(err, &pe) || pe.Code != CodeBadJob {
		t.Fatalf("want bad_job, got %v", err)
	}
}

func TestClassifyMapsCancellationAndUnknownErrors(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if code, _ := Classify(ctx, errors.New("boom")); code != CodeCancelled {
		t.Fatalf("cancelled ctx: got %s", code)
	}
	if code, _ := Classify(context.Background(), errors.New("boom")); code != CodeInternal {
		t.Fatalf("plain error: got %s", code)
	}
	if code, msg := Classify(context.Background(), Errorf(CodeData, "bad %d", 3)); code != CodeData || msg != "bad 3" {
		t.Fatalf("engine error: got %s %q", code, msg)
	}
}
```

- [ ] **Step 2: Write the failing CLI tests**

`$ENG/cmd/hoist-engine/main_test.go`:

```go
package main

import (
	"bytes"
	"context"
	"encoding/json"
	"strings"
	"testing"

	"hoistra/engine/internal/protocol"
)

func lastEvent(t *testing.T, out *bytes.Buffer) map[string]any {
	t.Helper()
	ls := strings.Split(strings.TrimSpace(out.String()), "\n")
	var m map[string]any
	if err := json.Unmarshal([]byte(ls[len(ls)-1]), &m); err != nil {
		t.Fatal(err)
	}
	return m
}

func TestVersionCommand(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"version"}, &out, &errb); code != 0 {
		t.Fatalf("exit %d, stderr %s", code, errb.String())
	}
	ev := lastEvent(t, &out)
	if ev["type"] != "result" || ev["result"].(map[string]any)["version"] != Version {
		t.Fatalf("bad result: %v", ev)
	}
}

func TestUnknownCommandIsAUsageError(t *testing.T) {
	var out, errb bytes.Buffer
	if code := run([]string{"nope"}, &out, &errb); code != 2 {
		t.Fatalf("want exit 2, got %d", code)
	}
	if ev := lastEvent(t, &out); ev["type"] != "error" || ev["code"] != protocol.CodeBadJob {
		t.Fatalf("bad event: %v", ev)
	}
}

func TestAPanicBecomesAnInternalError(t *testing.T) {
	register("test-panic", func(ctx context.Context, job string, em *protocol.Emitter) (any, error) {
		panic("kaboom")
	})
	var out, errb bytes.Buffer
	if code := run([]string{"test-panic"}, &out, &errb); code != 1 {
		t.Fatalf("want exit 1, got %d", code)
	}
	ev := lastEvent(t, &out)
	if ev["code"] != protocol.CodeInternal || !strings.Contains(ev["message"].(string), "kaboom") {
		t.Fatalf("bad event: %v", ev)
	}
	if !strings.Contains(errb.String(), "goroutine") {
		t.Fatalf("stack trace missing from stderr")
	}
}
```

- [ ] **Step 3: Create the module, dev script and ignore file**

`$ENG/go.mod`:

```
module hoistra/engine

go 1.24
```

`$ENG/.gitignore`:

```
bin/
```

`$ENG/scripts/dev.sh` (chmod +x):

```sh
#!/bin/sh
# Build and test hoist-engine without a local Go toolchain: everything runs in golang:1.24.
#   dev.sh test               go vet + go test (Postgres tests skip)
#   dev.sh test-db            same, joined to the throwaway hoist-parity network (Task 7)
#   dev.sh build-linux [arch] static binary at bin/hoist-engine-linux-<arch>
#   dev.sh tidy               go mod tidy
set -eu
ENG="$(cd "$(dirname "$0")/.." && pwd)"
IMG=golang:1.24-bookworm
ARCH_DEFAULT="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
run() {
  docker run --rm -v "$ENG":/src -v hoist-go-mod:/go/pkg/mod -v hoist-go-build:/root/.cache/go-build \
    -w /src -e CGO_ENABLED=0 -e GOFLAGS=-mod=mod "$@"
}
case "${1:-test}" in
  test) run "$IMG" sh -c 'go vet ./... && go test ./...' ;;
  test-db)
    run --network hoist-parity -e GOFLAGS=-mod=readonly -e GOPROXY=off \
      -e HOIST_PARITY_DSN="postgres://parity:parity@hoist-parity-pg:5432/parity_run?sslmode=disable" \
      "$IMG" sh -c 'go vet ./... && go test -count=1 ./...' ;;
  build-linux)
    arch="${2:-$ARCH_DEFAULT}"
    run -e GOOS=linux -e GOARCH="$arch" "$IMG" \
      go build -trimpath -ldflags "-s -w" -o "bin/hoist-engine-linux-$arch" ./cmd/hoist-engine ;;
  tidy) run "$IMG" go mod tidy ;;
  *) echo "usage: dev.sh test|test-db|build-linux [arch]|tidy" >&2; exit 2 ;;
esac
```

- [ ] **Step 4: Run the tests to see them fail**

Run: `$ENG/scripts/dev.sh test`
Expected: FAIL — `undefined: NewEmitter` / `undefined: run`.

- [ ] **Step 5: Implement the protocol**

`$ENG/internal/protocol/protocol.go`:

```go
// Package protocol is the contract between the schema-mapper (Python) and hoist-engine:
// a job file in, newline-delimited JSON events out, exactly one result or error at the end.
package protocol

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"sync"
	"time"
)

const (
	CodeBadJob      = "bad_job"
	CodeUnsupported = "unsupported"
	CodeParse       = "parse_error"
	CodeData        = "data_error"
	CodeDB          = "db_error"
	CodeConnLost    = "connection_lost"
	CodeDDL         = "ddl_failed"
	CodeCancelled   = "cancelled"
	CodeInternal    = "internal"
)

// Error is an engine failure the Python side can act on by its code.
type Error struct{ Code, Message string }

func (e *Error) Error() string { return e.Code + ": " + e.Message }

func Errorf(code, format string, a ...any) error {
	return &Error{Code: code, Message: fmt.Sprintf(format, a...)}
}

// Classify turns any error into (code, message). A cancelled context wins: the run was stopped.
func Classify(ctx context.Context, err error) (string, string) {
	if ctx.Err() != nil {
		return CodeCancelled, "the step was cancelled"
	}
	var e *Error
	if errors.As(err, &e) {
		return e.Code, e.Message
	}
	return CodeInternal, err.Error()
}

type event struct {
	Type    string `json:"type"`
	Stage   string `json:"stage,omitempty"`
	Table   string `json:"table,omitempty"`
	Done    *int64 `json:"done,omitempty"`
	Total   *int64 `json:"total,omitempty"`
	Level   string `json:"level,omitempty"`
	Code    string `json:"code,omitempty"`
	Message string `json:"message,omitempty"`
	Result  any    `json:"result,omitempty"`
}

// Emitter writes events; it is safe for concurrent use. Progress is throttled per
// (stage, table) to one event per minGap, except the tick that reaches total.
type Emitter struct {
	mu     sync.Mutex
	w      io.Writer
	now    func() time.Time
	minGap time.Duration
	last   map[string]time.Time
}

func NewEmitter(w io.Writer) *Emitter {
	return &Emitter{w: w, now: time.Now, minGap: 250 * time.Millisecond, last: map[string]time.Time{}}
}

func (e *Emitter) write(ev event) error {
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	enc.SetEscapeHTML(false)
	if err := enc.Encode(ev); err != nil {
		return err
	}
	e.mu.Lock()
	defer e.mu.Unlock()
	_, err := e.w.Write(buf.Bytes())
	return err
}

func (e *Emitter) Progress(stage, table string, done, total int64) {
	key := stage + "\x00" + table
	now := e.now()
	e.mu.Lock()
	last, seen := e.last[key]
	due := !seen || now.Sub(last) >= e.minGap || done >= total
	if due {
		e.last[key] = now
	}
	e.mu.Unlock()
	if due {
		_ = e.write(event{Type: "progress", Stage: stage, Table: table, Done: &done, Total: &total})
	}
}

func (e *Emitter) Log(level, msg string) { _ = e.write(event{Type: "log", Level: level, Message: msg}) }

func (e *Emitter) Result(v any) error {
	if v == nil {
		v = map[string]any{}
	}
	return e.write(event{Type: "result", Result: v})
}

func (e *Emitter) Error(code, msg string) { _ = e.write(event{Type: "error", Code: code, Message: msg}) }

// ReadJob decodes a job file strictly: an unknown field means Python and Go disagree about
// the contract, which must fail loudly rather than be ignored.
func ReadJob(path string, v any) error {
	f, err := os.Open(path)
	if err != nil {
		return Errorf(CodeBadJob, "cannot open job file: %v", err)
	}
	defer f.Close()
	dec := json.NewDecoder(f)
	dec.DisallowUnknownFields()
	if err := dec.Decode(v); err != nil {
		return Errorf(CodeBadJob, "invalid job file: %v", err)
	}
	return nil
}
```

- [ ] **Step 6: Implement the CLI**

`$ENG/cmd/hoist-engine/main.go`:

```go
// hoist-engine does the row-heavy steps of a Hoistra migration for the schema-mapper.
package main

import (
	"context"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"runtime"
	"runtime/debug"
	"sort"
	"syscall"

	"hoistra/engine/internal/protocol"
)

const Version = "0.1.0"

type Command func(ctx context.Context, jobPath string, em *protocol.Emitter) (any, error)

var commands = map[string]Command{}

func register(name string, fn Command) { commands[name] = fn }

func init() {
	register("version", func(context.Context, string, *protocol.Emitter) (any, error) {
		names := make([]string, 0, len(commands))
		for n := range commands {
			if n != "version" {
				names = append(names, n)
			}
		}
		sort.Strings(names)
		return map[string]any{"version": Version, "go": runtime.Version(), "commands": names}, nil
	})
}

func main() { os.Exit(run(os.Args[1:], os.Stdout, os.Stderr)) }

func run(args []string, stdout, stderr io.Writer) int {
	em := protocol.NewEmitter(stdout)
	if len(args) == 0 {
		em.Error(protocol.CodeBadJob, "usage: hoist-engine <command> --job <file>")
		return 2
	}
	cmd, ok := commands[args[0]]
	if !ok {
		em.Error(protocol.CodeBadJob, "unknown command "+args[0])
		return 2
	}
	fs := flag.NewFlagSet(args[0], flag.ContinueOnError)
	fs.SetOutput(stderr)
	job := fs.String("job", "", "path to the job JSON")
	if err := fs.Parse(args[1:]); err != nil {
		em.Error(protocol.CodeBadJob, err.Error())
		return 2
	}
	ctx, stop := signal.NotifyContext(context.Background(), syscall.SIGTERM, os.Interrupt)
	defer stop()
	res, err := safeRun(ctx, cmd, *job, em, stderr)
	if err != nil {
		code, msg := protocol.Classify(ctx, err)
		em.Error(code, msg)
		return 1
	}
	if err := em.Result(res); err != nil {
		fmt.Fprintln(stderr, "could not write result:", err)
		return 1
	}
	return 0
}

func safeRun(ctx context.Context, cmd Command, job string, em *protocol.Emitter, stderr io.Writer) (res any, err error) {
	defer func() {
		if r := recover(); r != nil {
			fmt.Fprintf(stderr, "panic: %v\n%s", r, debug.Stack())
			err = protocol.Errorf(protocol.CodeInternal, "engine panic: %v", r)
		}
	}()
	return cmd(ctx, job, em)
}
```

- [ ] **Step 7: Run the tests to see them pass**

Run: `$ENG/scripts/dev.sh test`
Expected: `ok hoistra/engine/internal/protocol`, `ok hoistra/engine/cmd/hoist-engine`.

- [ ] **Step 8: Build the binary and smoke it**

Run: `$ENG/scripts/dev.sh build-linux && docker run --rm -v "$PWD/$ENG/bin":/b debian:bookworm-slim /b/hoist-engine-linux-$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/') version`
Expected: one line `{"type":"result","result":{"commands":[],"go":"go1.24…","version":"0.1.0"}}`.

- [ ] **Step 9: README**

`$ENG/README.md`: four short sections — what the engine is (link the spec), commands, how Python calls it (job/events), how to test (`scripts/dev.sh`). No more than 40 lines.

- [ ] **Step 10: Checkpoint (no commit)**

Run: `git status --short`. Expected: only new files under `$ENG/`.

---

### Task 2: Python engine client and offline test runner

**Files:**
- Create: `$SVC/src/engine/__init__.py`, `$SVC/src/engine/client.py`
- Create: `$SVC/scripts/test_offline.sh`
- Test: `$SVC/tests/test_engine_client.py`

**Interfaces:**
- Consumes: Task 1 CLI/event contract.
- Produces: `class EngineError(RuntimeError)` with `.code: str`, `.message: str`; `engine_binary() -> str | None` (env `HOIST_ENGINE_BIN`, default `/usr/local/bin/hoist-engine`); `engine_available() -> bool`; `async run_engine(command: str, job: dict, *, workdir: str | os.PathLike | None = None, env: dict[str, str] | None = None, on_event: Callable[[dict], Awaitable[None]] | None = None, timeout_s: float | None = None) -> dict`.

- [ ] **Step 1: Offline runner**

`$SVC/scripts/test_offline.sh` (chmod +x):

```sh
#!/bin/sh
# Offline schema-mapper tests in the worker image: no network, tree mounted read-only.
#   scripts/test_offline.sh [pytest args]       (run from svc-ai-schema-mapper)
#   HOIST_PARITY=1 scripts/test_offline.sh …    joins the throwaway hoist-parity network instead
set -eu
SVC="$(cd "$(dirname "$0")/.." && pwd)"
ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
BIN="$SVC/engine/bin/hoist-engine-linux-$ARCH"
IMG="${HOIST_TEST_IMAGE:-hoistra_plenum-schema-mapper-worker}"
docker volume inspect hoist-pytest-deps >/dev/null 2>&1 || {
  docker volume create hoist-pytest-deps >/dev/null
  docker run --rm -v hoist-pytest-deps:/pydeps "$IMG" pip install -q --target /pydeps pytest pytest-asyncio
}
NET="--network none"
[ "${HOIST_PARITY:-0}" = "1" ] && NET="--network hoist-parity -e HOIST_PARITY_DSN=postgresql://parity:parity@hoist-parity-pg:5432/parity_run -e DB_URL=postgresql+asyncpg://parity:parity@hoist-parity-pg:5432/parity_run"
ENGINE_MOUNT=""
[ -x "$BIN" ] && ENGINE_MOUNT="-v $BIN:/usr/local/bin/hoist-engine:ro"
# shellcheck disable=SC2086
exec docker run --rm $NET $ENGINE_MOUNT \
  -v "$SVC":/work:ro -v hoist-pytest-deps:/pydeps:ro -w /work \
  -e PYTHONPATH=/work:/pydeps -e USE_SQLITE_DEV=true -e PYTHONDONTWRITEBYTECODE=1 \
  -e HOIST_ENGINE_DIR=/tmp/hoist-engine \
  "$IMG" python -m pytest -q -p no:cacheprovider -o log_cli=false "$@"
```

- [ ] **Step 2: Write the failing client tests**

`$SVC/tests/test_engine_client.py`:

```python
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
        if pidfile.exists():
            break
        await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    pid = int(pidfile.read_text())
    await asyncio.sleep(0.2)
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
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_client.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.engine'`.

- [ ] **Step 4: Implement the client**

`$SVC/src/engine/__init__.py`:

```python
"""hoist-engine integration: the Go binary that does a migration's row-heavy steps.

See docs/superpowers/specs/2026-10-01-go-migration-engine-design.md.
"""
from .client import EngineError, engine_available, engine_binary, run_engine

__all__ = ["EngineError", "engine_available", "engine_binary", "run_engine"]
```

`$SVC/src/engine/client.py`:

```python
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
from typing import Any, Awaitable, Callable, Optional

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
        _kill(proc)
        await proc.wait()
        raise EngineError("timeout", f"{command} did not finish within {timeout_s:.0f}s") from None
    except asyncio.CancelledError:
        _kill(proc)
        raise
    if error is not None:
        raise error
    if proc.returncode != 0 or result is None:
        tail = stderr_tail.decode("utf-8", "replace").strip()[-2000:]
        raise EngineError("internal", f"{command} exited {proc.returncode}: {tail or 'no output'}")
    return result
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_client.py`
Expected: 7 passed.

- [ ] **Step 6: Checkpoint (no commit)** — `git status --short` shows `src/engine/`, `scripts/test_offline.sh`, `tests/test_engine_client.py`.

---

### Task 3: State channels, engine choice and the graph proxy

**Files:**
- Modify: `$SVC/src/graph/state.py` (append to `MigrationState`)
- Create: `$SVC/src/engine/selection.py`, `$SVC/src/engine/graph_proxy.py`
- Modify: `$SVC/src/graph/migration_graph.py:338-358` (`_STEP_NODES` → module-level `STEP_NODES`), `:361-432` (`get_migration_graph` returns the proxy)
- Test: `$SVC/tests/test_engine_selection.py`, `$SVC/tests/test_engine_graph_proxy.py`

**Interfaces:**
- Produces: `MigrationState` keys `engine: str`, `engine_refs: dict[str, Any]`, `engine_reports: dict[str, Any]`, `write_plan: Optional[dict[str, Any]]`.
- Produces: `selection.ENGINE_GO = "go"`, `ENGINE_PYTHON = "python"`, `ENGINE_STEPS: frozenset[str]` (names among `"parse"`, `"preprocess"`, `"outputs"`, `"write"`; grows phase by phase), `STEP_NODE_OF = {"parse": "ingest_node", "preprocess": "preprocess_node", "outputs": "output_generator_node"}`, `configured_engine() -> str`, `choose_engine(*, source_filename=None, source_blob_path=None) -> str`, `uses_go(state: Mapping, step: str) -> bool`, `go_auto_continue_nodes() -> frozenset[str]`.
- Produces: `graph_proxy.MigrationGraphProxy(compiled, step_nodes: Sequence[str])` with `async ainvoke(input, config=None, **kwargs)` and attribute delegation; `migration_graph.STEP_NODES: list[str]`.

- [ ] **Step 1: Write the failing selection tests**

`$SVC/tests/test_engine_selection.py`:

```python
import pytest

from src.engine import selection
from src.engine.selection import ENGINE_GO, ENGINE_PYTHON, choose_engine, uses_go


@pytest.fixture
def engine_present(monkeypatch):
    monkeypatch.setattr(selection, "engine_available", lambda: True)
    monkeypatch.delenv("MIGRATION_ENGINE", raising=False)


@pytest.mark.parametrize("name", ["a.csv", "B.TSV", "x.xlsx", "macro.xlsm"])
def test_spreadsheets_run_on_the_engine(engine_present, name):
    assert choose_engine(source_filename=name) == ENGINE_GO


def test_old_excel_stays_on_python(engine_present):
    assert choose_engine(source_filename="legacy.xls") == ENGINE_PYTHON


def test_the_durable_source_decides_for_a_multi_file_upload(engine_present):
    assert choose_engine(source_filename="a.csv, b.xls",
                         source_blob_path="migrations/m/source/combined.xlsx") == ENGINE_GO


def test_the_kill_switch_wins(engine_present, monkeypatch):
    monkeypatch.setenv("MIGRATION_ENGINE", "python")
    assert choose_engine(source_filename="a.csv") == ENGINE_PYTHON


def test_no_binary_no_engine(monkeypatch):
    monkeypatch.setattr(selection, "engine_available", lambda: False)
    assert choose_engine(source_filename="a.csv") == ENGINE_PYTHON


def test_a_step_uses_go_only_when_the_run_and_the_build_both_say_so(monkeypatch):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"write"}))
    assert uses_go({"engine": "go"}, "write") is True
    assert uses_go({"engine": "go"}, "parse") is False
    assert uses_go({"engine": "python"}, "write") is False
    assert uses_go({}, "write") is False
```

- [ ] **Step 2: Write the failing proxy tests (a real LangGraph graph)**

`$SVC/tests/test_engine_graph_proxy.py`:

```python
"""A Go-engine run does not stop after the steps the engine does in seconds."""
from typing import TypedDict

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph

from src.engine import selection
from src.engine.graph_proxy import MigrationGraphProxy

NODES = ["ingest_node", "deterministic_mapper_node", "preprocess_node", "hierarchy_node"]


class S(TypedDict, total=False):
    migration_id: str
    engine: str
    source_filename: str
    trail: list


def _graph():
    g = StateGraph(S)
    for n in NODES:
        g.add_node(n, lambda s, n=n: {"trail": [*(s.get("trail") or []), n]})
    g.add_edge(START, NODES[0])
    for a, b in zip(NODES, NODES[1:]):
        g.add_edge(a, b)
    g.add_edge(NODES[-1], END)
    return g.compile(checkpointer=MemorySaver(), interrupt_after=NODES)


@pytest.fixture(autouse=True)
def all_steps_go(monkeypatch):
    monkeypatch.setattr(selection, "ENGINE_STEPS", frozenset({"parse", "preprocess", "outputs", "write"}))
    monkeypatch.setattr(selection, "engine_available", lambda: True)
    monkeypatch.delenv("MIGRATION_ENGINE", raising=False)


async def test_a_go_run_skips_the_engine_step_pauses():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "g1"}}
    out = await proxy.ainvoke({"migration_id": "g1", "source_filename": "a.csv"}, cfg)
    assert out["trail"] == ["ingest_node", "deterministic_mapper_node"]  # no stop after ingest
    out = await proxy.ainvoke(None, cfg)  # resume reads the engine from the checkpoint
    assert out["trail"][-1] == "hierarchy_node"  # no stop after preprocess
    assert out["engine"] == "go"


async def test_a_python_run_keeps_every_pause():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "p1"}}
    out = await proxy.ainvoke({"migration_id": "p1", "source_filename": "a.xls"}, cfg)
    assert out["trail"] == ["ingest_node"]
    assert out["engine"] == "python"


async def test_an_explicit_interrupt_after_is_left_alone():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "e1"}}
    out = await proxy.ainvoke({"migration_id": "e1", "engine": "go"}, cfg, interrupt_after=["ingest_node"])
    assert out["trail"] == ["ingest_node"]


async def test_other_attributes_reach_the_graph():
    proxy = MigrationGraphProxy(_graph(), NODES)
    cfg = {"configurable": {"thread_id": "a1"}}
    await proxy.ainvoke({"migration_id": "a1", "engine": "python"}, cfg)
    snap = await proxy.aget_state(cfg)
    assert snap.values["engine"] == "python"
```

- [ ] **Step 3: Run both to see them fail**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_selection.py tests/test_engine_graph_proxy.py`
Expected: FAIL — `No module named 'src.engine.selection'`.

- [ ] **Step 4: Implement selection**

`$SVC/src/engine/selection.py`:

```python
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
ENGINE_STEPS: frozenset[str] = frozenset()

#: The graph node whose step pause the engine makes unnecessary, per engine step.
STEP_NODE_OF = {
    "parse": "ingest_node",
    "preprocess": "preprocess_node",
    "outputs": "output_generator_node",
}

_GO_FORMATS = frozenset({".csv", ".tsv", ".xlsx", ".xlsm"})


def configured_engine() -> str:
    value = (os.environ.get("MIGRATION_ENGINE") or ENGINE_GO).strip().lower()
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
```

- [ ] **Step 5: Implement the proxy**

`$SVC/src/engine/graph_proxy.py`:

```python
"""The compiled migration graph, minus a Go run's needless step pauses.

LangGraph's ``interrupt_after`` is fixed when a graph is compiled, but ``ainvoke`` accepts its own.
Every caller (worker run/resume, the app's inline runs, /advance, rerun-from) gets the graph from
migration_graph.get_migration_graph(), which returns this proxy, so the decision lives here once.
A caller that somehow bypasses it keeps today's pauses — the safe side.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from .selection import ENGINE_GO, choose_engine, go_auto_continue_nodes


class MigrationGraphProxy:
    def __init__(self, compiled: Any, step_nodes: Sequence[str]) -> None:
        self._graph = compiled
        self._step_nodes = list(step_nodes)

    async def ainvoke(self, input: Any, config: Any = None, **kwargs: Any) -> Any:
        if "interrupt_after" not in kwargs and await self._engine_of(input, config) == ENGINE_GO:
            skip = go_auto_continue_nodes()
            kwargs["interrupt_after"] = [n for n in self._step_nodes if n not in skip]
        return await self._graph.ainvoke(input, config, **kwargs)

    async def _engine_of(self, input: Any, config: Any) -> "str | None":
        if isinstance(input, dict):
            if input.get("engine"):
                return input["engine"]
            if input.get("migration_id") and ("source_filename" in input or "source_blob_path" in input):
                # A fresh run: decide now, and put it on the state so every resume agrees.
                input["engine"] = choose_engine(
                    source_filename=input.get("source_filename"),
                    source_blob_path=input.get("source_blob_path"),
                )
                return input["engine"]
        if not config:
            return None
        try:
            snap = await self._graph.aget_state(config)
        except Exception:  # noqa: BLE001 — unknown means the compiled pauses, the safe side
            return None
        values = getattr(snap, "values", None) or {}
        return values.get("engine")

    def __getattr__(self, name: str) -> Any:
        return getattr(self._graph, name)
```

- [ ] **Step 6: Declare the state channels**

Append inside `class MigrationState` in `$SVC/src/graph/state.py`, after `udr_activity_id`:

```python
    # ── hoist-engine (src/engine/) ───────────────────────────────────────
    #: "go" | "python": decided once at run start (engine/selection.py) and kept for the whole run,
    #: so every resume takes the same path. Must be declared: undeclared keys are dropped on checkpoint.
    engine: str
    #: Where each engine data set lives, per kind ("full", "cleaned", "outputs", "write"):
    #: {"dir": local path, "version": str, "blobs": {relative file: blob path}} (engine/store.py).
    engine_refs: dict[str, Any]
    #: Small per-step summaries the engine returned (counts, rename maps, link stats, timings).
    engine_reports: dict[str, Any]
    #: The write the engine planned before the write gate (EL-4.0 + resolution dry run).
    write_plan: Optional[dict[str, Any]]
```

- [ ] **Step 7: Return the proxy from the factory**

In `$SVC/src/graph/migration_graph.py`: move the list at lines 338-345 to module level as

```python
#: Step nodes that pause after they finish so a person can look before the run moves on.
#: Gate nodes (pk/unique/pre-semantic/human/verify/write) interrupt() themselves and are not listed.
STEP_NODES = [
    "ingest_node",
    "deterministic_mapper_node",
    "semantic_mapper_node",
    "preprocess_node",
    "hierarchy_node",
    "output_generator_node",
]
```

use `STEP_NODES` in both `graph.compile(...)` calls, and change the last lines of `get_migration_graph()` to:

```python
    graph = build_migration_graph(checkpointer)
    logger.info("Migration graph compiled and ready")
    from ..engine.graph_proxy import MigrationGraphProxy

    return MigrationGraphProxy(graph, STEP_NODES)
```

- [ ] **Step 8: Run the tests to see them pass, then the whole suite**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_selection.py tests/test_engine_graph_proxy.py`
Expected: 10 passed.
Run: `cd $SVC && scripts/test_offline.sh tests --continue-on-collection-errors -q | tail -3`
Expected: ≥ 363 passed, 38 errors (baseline + new).

- [ ] **Step 9: Checkpoint (no commit).**

---

### Task 4: Engine store — Arrow bridge, Blob publish/fetch, go-run hydration

**Files:**
- Create: `$SVC/src/engine/store.py`
- Modify: `$SVC/src/graph/bulk_tables.py` (`hydrate`, `dehydrate`: go-run branch at the top of each)
- Test: `$SVC/tests/test_engine_store.py`

**Interfaces:**
- Consumes: `state["engine"]`, `state["engine_refs"]` (Task 3); `bulk_tables._blob_conf`, `_cache_put`, `_CACHE`.
- Produces (Python):
  - `ENGINE_DIR: Path` (env `HOIST_ENGINE_DIR`, default `/var/hoist-engine`); `kind_dir(migration_id: str, kind: str) -> Path` (rejects ids that are not `[0-9A-Za-z-]{1,64}`).
  - `class BridgeTypeError(TypeError)`, `class EngineStoreMissing(RuntimeError)`.
  - `write_tables(dirpath: Path, tables: dict[str, list[dict]]) -> None` and `read_tables(dirpath: Path) -> dict[str, list[dict]]` — the bridge format below.
  - `async publish(migration_id: str, kind: str) -> dict` → ref `{"kind", "dir", "version", "blobs": {rel: blob}, "complete": bool}`.
  - `async ensure_local(migration_id: str, ref: dict) -> Path`.
  - `async save_tables(state: dict, kind: str, tables: dict) -> dict` (write + publish, returns ref) and `async load_tables(state: dict, kind: str) -> dict[str, list[dict]]` (cached per `(migration, kind, version)`).
- Produces (format, read by Go `arrowtab` in Task 8): `<dir>/manifest.json` = `{"version": 1, "tables": [{"name": str, "file": "t0001.arrow" | null, "rows": int, "columns": [str]}]}`; each file an Arrow IPC **file** (not stream), zstd-compressed, all fields `utf8` nullable, record batches ≤ 65,536 rows; a field whose metadata has `hoist.null_fill = int0` stores the integer `0` as null (the only non-string a cleaned table carries: preprocess's numeric null fill).

- [ ] **Step 1: Write the failing store tests**

`$SVC/tests/test_engine_store.py`:

```python
"""Rows between steps travel as the engine's Arrow files, never through the checkpoint."""
import pytest

from src.engine import store
from src.engine.store import BridgeTypeError, EngineStoreMissing, read_tables, write_tables


def test_bridge_round_trip_keeps_order_none_and_the_int_zero(tmp_path):
    tables = {
        "Work Orders": [{"wo": "W1", "qty": 0, "note": None}, {"wo": "W2", "qty": "5", "note": ""}],
        "Empty": [],
        "Ünïcode ✓": [{"naïve": "é"}],
    }
    write_tables(tmp_path, tables)
    back = read_tables(tmp_path)
    assert back == tables
    assert list(back) == list(tables)                      # table order
    assert list(back["Work Orders"][0]) == ["wo", "qty", "note"]   # column order
    assert back["Work Orders"][0]["qty"] == 0 and type(back["Work Orders"][0]["qty"]) is int


@pytest.mark.parametrize("bad", [
    {"T": [{"a": True}]},                    # bool
    {"T": [{"a": 1.5}]},                     # float
    {"T": [{"a": 5}]},                       # an int that is not the null fill
    {"T": [{"a": 0}, {"a": None}]},          # int0 and a real null in one column
    {"T": [{"a": "x"}, {"b": "y"}]},         # ragged rows
])
def test_the_bridge_refuses_what_it_cannot_carry_exactly(tmp_path, bad):
    with pytest.raises(BridgeTypeError):
        write_tables(tmp_path, bad)


class _FakeBlobs:
    """In-memory stand-in for azure.storage.blob.aio.BlobServiceClient."""

    def __init__(self):
        self.data = {}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def get_blob_client(self, container, blob):
        outer = self

        class _C:
            async def upload_blob(self, data, overwrite=False):
                outer.data[blob] = data.read() if hasattr(data, "read") else bytes(data)

            async def download_blob(self):
                payload = outer.data[blob]

                class _S:
                    async def readall(self):
                        return payload
                return _S()
        return _C()


@pytest.fixture
def blobs(monkeypatch, tmp_path):
    fake = _FakeBlobs()
    monkeypatch.setattr(store, "ENGINE_DIR", tmp_path / "engine")
    monkeypatch.setattr(store, "_blob_conf", lambda: ("UseDevelopmentStorage=true", "c"))
    monkeypatch.setattr(store, "_open_blob_service", lambda conn: fake)
    return fake


async def test_publish_then_fetch_in_a_process_without_the_files(blobs):
    mid = "0b498dfd-0000-4000-8000-000000000001"
    state = {"migration_id": mid, "engine": "go"}
    ref = await store.save_tables(state, "cleaned", {"T": [{"a": "1"}]})
    assert ref["complete"] and set(ref["blobs"]) == {"manifest.json", "t0001.arrow"}
    for p in store.kind_dir(mid, "cleaned").iterdir():       # the other container: no local files
        p.unlink()
    store._CACHE.clear()
    state["engine_refs"] = {"cleaned": ref}
    assert await store.load_tables(state, "cleaned") == {"T": [{"a": "1"}]}


async def test_missing_engine_files_fail_the_node(blobs):
    mid = "0b498dfd-0000-4000-8000-000000000002"
    ref = {"kind": "full", "dir": str(store.kind_dir(mid, "full")), "version": "v1",
           "blobs": {"manifest.json": "migrations/x/engine/full/manifest.json"}, "complete": True}
    with pytest.raises(EngineStoreMissing):
        await store.load_tables({"migration_id": mid, "engine": "go", "engine_refs": {"full": ref}}, "full")


def test_a_migration_id_cannot_escape_the_engine_dir():
    with pytest.raises(ValueError):
        store.kind_dir("../../etc", "full")


async def test_a_go_run_never_offloads_rows_into_the_checkpoint(blobs):
    from src.graph import bulk_tables

    mid = "0b498dfd-0000-4000-8000-000000000003"
    state = {"migration_id": mid, "engine": "go", "full_tables": {"T": [{"a": "1"}]}}
    await bulk_tables.dehydrate(state, mid, ["full_tables"])
    assert state["full_tables"] == {} and "full_tables_ref" not in state
    assert state["engine_refs"]["full"]["version"]
    store._CACHE.clear()
    await bulk_tables.hydrate(state, ["full_tables"])
    assert state["full_tables"] == {"T": [{"a": "1"}]}


async def test_a_bridge_type_error_downgrades_the_run_instead_of_losing_rows(blobs, monkeypatch):
    from src.graph import bulk_tables

    async def _no_blob_offload(*a, **k):
        return None  # legacy offload unavailable → rows stay inline, the pre-offload behaviour
    monkeypatch.setattr(bulk_tables, "offload_tables", _no_blob_offload)
    mid = "0b498dfd-0000-4000-8000-000000000004"
    state = {"migration_id": mid, "engine": "go", "cleaned_tables": {"T": [{"a": 1.5}]}}
    await bulk_tables.dehydrate(state, mid, ["cleaned_tables"])
    assert state["engine"] == "python"
    assert state["cleaned_tables"] == {"T": [{"a": 1.5}]}


async def test_the_bridge_is_all_or_nothing_across_channels(blobs, monkeypatch):
    from src.graph import bulk_tables

    async def _no_blob_offload(*a, **k):
        return None
    monkeypatch.setattr(bulk_tables, "offload_tables", _no_blob_offload)
    mid = "0b498dfd-0000-4000-8000-000000000005"
    state = {"migration_id": mid, "engine": "go",
             "full_tables": {"T": [{"a": "1"}]}, "cleaned_tables": {"T": [{"a": 1.5}]}}
    await bulk_tables.dehydrate(state, mid, ["full_tables", "cleaned_tables"])
    assert state["engine"] == "python"
    assert state["full_tables"] == {"T": [{"a": "1"}]}       # not cleared: nothing was handed over
    assert "full" not in (state.get("engine_refs") or {})


async def test_a_run_that_fell_back_to_python_still_reads_what_the_engine_holds(blobs):
    from src.graph import bulk_tables

    mid = "0b498dfd-0000-4000-8000-000000000006"
    state = {"migration_id": mid, "engine": "go"}
    state["engine_refs"] = {"full": await store.save_tables(state, "full", {"T": [{"a": "1"}]})}
    state["engine"] = "python"
    store._CACHE.clear()
    await bulk_tables.hydrate(state, ["full_tables"])
    assert state["full_tables"] == {"T": [{"a": "1"}]}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_store.py`
Expected: FAIL — `No module named 'src.engine.store'`.

- [ ] **Step 3: Implement the store**

`$SVC/src/engine/store.py`:

```python
"""Where a Go-engine run keeps its rows between steps.

Each data set (``full`` parse output, ``cleaned`` preprocess output, ``outputs`` artefacts, ``write``
plan) is a directory under ENGINE_DIR/<migration>/<kind>/ holding a manifest and one Arrow IPC file
per table. The app and the worker are separate containers locally (they share the
``hoist-engine-cache`` volume) and one container in production; Blob holds the durable copy so a
step that resumes anywhere can fetch what it needs. The checkpoint only ever carries the ref.

The bridge (write_tables/read_tables) is how Python steps hand rows to Go steps and back while
both kinds of step exist: it carries str, None, and the integer 0 that preprocess's numeric null
fill leaves — exactly, or not at all (BridgeTypeError).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from pathlib import Path
from typing import Any

import pyarrow as pa

from ..graph.bulk_tables import _CACHE, _blob_conf, _cache_put

logger = logging.getLogger(__name__)

ENGINE_DIR = Path(os.environ.get("HOIST_ENGINE_DIR") or "/var/hoist-engine")
_SAFE_ID = re.compile(r"^[0-9A-Za-z-]{1,64}$")
_KINDS = frozenset({"full", "cleaned", "outputs", "write"})
_INT0 = {b"hoist.null_fill": b"int0"}
_BATCH = 65536


class BridgeTypeError(TypeError):
    """A value the Arrow bridge cannot carry exactly."""


class EngineStoreMissing(RuntimeError):
    """An engine data set is neither on this machine nor in Blob."""


def kind_dir(migration_id: str, kind: str) -> Path:
    if not _SAFE_ID.match(str(migration_id or "")) or kind not in _KINDS:
        raise ValueError(f"unsafe engine path: {migration_id!r}/{kind!r}")
    return ENGINE_DIR / str(migration_id) / kind


def _column_array(name: str, values: list) -> "tuple[pa.Field, pa.Array]":
    has_int0 = has_null = False
    out: list = []
    for v in values:
        if v is None:
            has_null = True
            out.append(None)
        elif isinstance(v, str):
            out.append(v)
        elif type(v) is int and v == 0:
            has_int0 = True
            out.append(None)
        else:
            raise BridgeTypeError(f"column {name!r}: cannot carry {type(v).__name__} {v!r}")
    if has_int0 and has_null:
        raise BridgeTypeError(f"column {name!r}: both a null and a filled 0")
    field = pa.field(name, pa.string(), nullable=True, metadata=_INT0 if has_int0 else None)
    return field, pa.array(out, type=pa.string())


def write_tables(dirpath: Path, tables: dict) -> None:
    dirpath = Path(dirpath)
    dirpath.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for idx, (name, rows) in enumerate((tables or {}).items(), 1):
        rows = list(rows or [])
        if not rows:
            manifest.append({"name": name, "file": None, "rows": 0, "columns": []})
            continue
        columns = list(rows[0].keys())
        for r in rows:
            if not isinstance(r, dict) or list(r.keys()) != columns:
                raise BridgeTypeError(f"table {name!r}: rows do not all carry the same columns")
        fields, arrays = zip(*(_column_array(c, [r[c] for r in rows]) for c in columns))
        table = pa.Table.from_arrays(list(arrays), schema=pa.schema(list(fields)))
        fname = f"t{idx:04d}.arrow"
        opts = pa.ipc.IpcWriteOptions(compression="zstd")
        with pa.OSFile(str(dirpath / fname), "wb") as sink, pa.ipc.new_file(sink, table.schema, options=opts) as w:
            w.write_table(table, max_chunksize=_BATCH)
        manifest.append({"name": name, "file": fname, "rows": len(rows), "columns": columns})
    (dirpath / "manifest.json").write_text(json.dumps({"version": 1, "tables": manifest}), encoding="utf-8")


def read_tables(dirpath: Path) -> dict:
    dirpath = Path(dirpath)
    meta = json.loads((dirpath / "manifest.json").read_text(encoding="utf-8"))
    out: dict = {}
    for entry in meta.get("tables") or []:
        if not entry.get("file"):
            out[entry["name"]] = []
            continue
        with pa.OSFile(str(dirpath / entry["file"]), "rb") as src:
            table = pa.ipc.open_file(src).read_all()
        keep = entry.get("columns") or table.column_names
        table = table.select(keep)
        rows = table.to_pylist()
        int0 = [f.name for f in table.schema if (f.metadata or {}).get(b"hoist.null_fill") == b"int0"]
        for col in int0:
            for r in rows:
                if r[col] is None:
                    r[col] = 0
        out[entry["name"]] = rows
    return out


def _open_blob_service(conn: str):
    from azure.storage.blob.aio import BlobServiceClient

    return BlobServiceClient.from_connection_string(conn)


def _blob_path(migration_id: str, kind: str, rel: str) -> str:
    return f"migrations/{migration_id}/engine/{kind}/{rel}"


async def publish(migration_id: str, kind: str) -> dict:
    d = kind_dir(migration_id, kind)
    files = sorted(p for p in d.rglob("*") if p.is_file())
    ref: dict[str, Any] = {"kind": kind, "dir": str(d), "version": uuid.uuid4().hex[:12],
                           "blobs": {}, "complete": True}
    conn, container = _blob_conf()
    if not conn:
        ref["complete"] = False  # local only (no Azure configured): fine within one machine
        return ref
    sem = asyncio.Semaphore(4)
    async with _open_blob_service(conn) as svc:
        async def _up(p: Path) -> None:
            rel = p.relative_to(d).as_posix()
            blob = _blob_path(migration_id, kind, rel)
            async with sem:
                try:
                    with p.open("rb") as fh:
                        await svc.get_blob_client(container=container, blob=blob).upload_blob(fh, overwrite=True)
                    ref["blobs"][rel] = blob
                except Exception as exc:  # noqa: BLE001 — local copy still serves this machine
                    ref["complete"] = False
                    logger.warning("[engine-store] upload of %s failed: %s", blob, exc)
        await asyncio.gather(*(_up(p) for p in files))
    return ref


async def ensure_local(migration_id: str, ref: dict) -> Path:
    d = Path(ref.get("dir") or kind_dir(migration_id, ref["kind"]))
    if not d.exists():
        d = kind_dir(migration_id, ref["kind"])
    blobs: dict = ref.get("blobs") or {}
    missing = [rel for rel in (blobs or {"manifest.json": None}) if not (d / rel).exists()]
    if not missing:
        return d
    conn, container = _blob_conf()
    if not conn or not blobs or any(rel not in blobs for rel in missing):
        raise EngineStoreMissing(
            f"The {ref['kind']} data for this run is not on this machine and not in Blob "
            "(the container may have restarted). Retry from this step.")
    d.mkdir(parents=True, exist_ok=True)
    async with _open_blob_service(conn) as svc:
        for rel in missing:
            try:
                stream = await svc.get_blob_client(container=container, blob=blobs[rel]).download_blob()
                (d / rel).parent.mkdir(parents=True, exist_ok=True)
                (d / rel).write_bytes(await stream.readall())
            except Exception as exc:  # noqa: BLE001
                raise EngineStoreMissing(f"Could not fetch {blobs[rel]} from Blob: {exc}") from exc
    return d


async def save_tables(state: dict, kind: str, tables: dict) -> dict:
    mid = str(state.get("migration_id") or "")
    d = kind_dir(mid, kind)
    await asyncio.to_thread(write_tables, d, tables)
    ref = await publish(mid, kind)
    _cache_put(f"engine:{mid}:{kind}:{ref['version']}", tables)
    return ref


async def load_tables(state: dict, kind: str) -> dict:
    mid = str(state.get("migration_id") or "")
    ref = (state.get("engine_refs") or {}).get(kind)
    if not ref:
        return {}
    key = f"engine:{mid}:{kind}:{ref.get('version')}"
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    d = await ensure_local(mid, ref)
    tables = await asyncio.to_thread(read_tables, d)
    _cache_put(key, tables)
    return tables
```

- [ ] **Step 4: Route go-run hydration through the store**

At the top of `$SVC/src/graph/bulk_tables.py` add `_ENGINE_KIND = {"full_tables": "full", "cleaned_tables": "cleaned"}` and put these blocks first in the two functions:

```python
async def hydrate(state: dict, channels: "list[str]") -> None:
    """Load the requested bulk channels into the in-memory ``state`` before a node reads them.

    Rows the engine holds (``engine_refs``) are read from its Arrow files — whatever the run's
    engine is now, so a run that fell back to Python mid-way still finds them. Everything else
    uses the gzip-JSON offload, as before."""
    rest = []
    for ch in channels:
        kind = _ENGINE_KIND.get(ch)
        engine_ref = (state.get("engine_refs") or {}).get(kind) if kind else None
        if engine_ref and not state.get(ch) and not state.get(BULK_CHANNELS[ch]):
            from ..engine.store import load_tables

            state[ch] = await load_tables(state, kind)
        else:
            rest.append(ch)
    channels = rest
    for ch in channels:
        ...  # unchanged body


async def dehydrate(state: dict, migration_id: Optional[str], offload_channels: "list[str]") -> None:
    """Remove the bulk channels from ``state`` before the checkpoint write. (docstring as before)

    In a Go-engine run the rows a node SET go to the engine's files (the bridge) — all of them or
    none: should the bridge refuse a single value, nothing is cleared, the run continues on the
    Python path (``engine`` = "python") and the gzip-JSON offload below handles every channel, so
    no row is ever dropped. A reader's hydrated copy is simply cleared."""
    if state.get("engine") == "go":
        from ..engine.store import BridgeTypeError, save_tables

        setters = [ch for ch in _ENGINE_KIND if ch in offload_channels and state.get(ch)]
        refs: dict = {}
        try:
            for ch in setters:
                refs[_ENGINE_KIND[ch]] = await save_tables(state, _ENGINE_KIND[ch], state[ch])
        except BridgeTypeError as exc:
            logger.warning("[bulk] rows cannot go through the engine bridge (%s); this run continues "
                           "on the Python path", exc)
            state["engine"] = "python"
        else:
            state["engine_refs"] = {**(state.get("engine_refs") or {}), **refs}
            for ch, kind in _ENGINE_KIND.items():
                if state.get(ch) and (state.get("engine_refs") or {}).get(kind):
                    state[ch] = {}
            offload_channels = [c for c in offload_channels if c not in _ENGINE_KIND]
    for ch, ref_key in BULK_CHANNELS.items():
        ...  # unchanged body
```

Keep each original body exactly as it is below the new block.

- [ ] **Step 5: Run the store tests, then the suite**

Run: `cd $SVC && scripts/test_offline.sh tests/test_engine_store.py tests/test_a_big_output_stays_out_of_the_checkpoint.py`
Expected: all pass (the existing offload test proves python runs are untouched).
Run: the full suite — no new failures.

- [ ] **Step 6: Checkpoint (no commit).**

---

### Task 5: UDR memoisation — each column's statistics computed once

**Files:**
- Create: `$SVC/src/udr/memo.py`
- Modify: `$SVC/src/udr/primitives.py` (memoised helpers; public signatures unchanged)
- Modify: `$SVC/src/udr/pipeline.py` (`run_udr_pipeline` body inside `with memo_scope():`), `$SVC/src/udr/column_intelligence.py` (`build_column_intelligence`), `$SVC/src/udr/table_resolution.py` (`build_table_resolution`), `$SVC/src/graph/nodes/pk_confirmation.py` (`build_pk_confirmation`), `$SVC/src/graph/nodes/column_merge.py` (`merge_duplicate_columns`) — each body wrapped in `with memo_scope():`
- Test: `$SVC/tests/test_udr_memo.py`

**Interfaces:**
- Produces: `memo.memo_scope()` (context manager; nested scopes reuse the outermost), `memo.memoized(kind: str, rows: list, col: str, compute: Callable[[], T]) -> T` (no scope or a non-list `rows` → `compute()`), `memo.enabled() -> bool`.
- The memo key is `(kind, id(rows), col)`; the scope pins every `rows` it saw so an id cannot be reused while the scope lives. Rows must not change inside a scope — Step 1 proves they do not.

- [ ] **Step 1: Prove the scoped code never changes rows or the values it is handed**

Run:
```sh
cd $SVC && grep -nE '\b(rows|records|_rows|recs)\b\s*\.(append|extend|insert|pop|remove|clear|sort)\(|\b(r|row|rec|_r)\[[^]]+\]\s*=[^=]' \
  src/udr/primitives.py src/udr/column_intelligence.py src/udr/table_resolution.py \
  src/udr/pipeline.py src/udr/unique_tables.py src/udr/validation.py src/udr/graph.py \
  src/udr/reference_tables.py src/graph/nodes/pk_confirmation.py src/graph/nodes/column_merge.py
grep -nE '(distinct_values|column_values)\([^)]*\)\s*\.(add|update|discard|append|extend|pop|remove|clear)' -r src
```
Expected: the first grep shows only report rows (`row["primary_key"]`, `rec["canonical_name"]`, `row["verdict"]`, `row["human_override"]`) and `reference_tables.py:124` (`row[c] = …` on rows of the NEW reference table it builds — not a scoped input); the second grep prints nothing. If anything else appears, keep that function out of the scope and note it in the task log.

- [ ] **Step 2: Write the failing tests**

`$SVC/tests/test_udr_memo.py`:

```python
"""UDR's pairwise tests read each column's statistics once — and get exactly the same report."""
import dataclasses
import random
import time

import pytest

from src.udr import memo
from src.udr.pipeline import run_udr_pipeline

VOLATILE = {"stage_times", "stage_durations", "sla_report"}


def _workbook(n_assets=400, n_wos=4000, seed=7):
    rnd = random.Random(seed)
    sites = [{"site_id": f"S-{i:02d}", "site_name": f"Site {i}", "city": rnd.choice(["Leeds", "York"])} for i in range(12)]
    assets = [{"asset_id": f"A-{i:04d}", "site_id": rnd.choice(sites)["site_id"],
               "asset_type": rnd.choice(["AHU", "FCU", "Chiller", "Pump"]), "serial": f"SN{rnd.randrange(10**8)}",
               "install_date": f"20{rnd.randrange(10, 25)}-0{rnd.randrange(1, 9)}-1{rnd.randrange(0, 9)}",
               "criticality": rnd.choice(["Low", "Medium", "High", None])} for i in range(n_assets)]
    vendors = [{"vendor_code": f"V{i:03d}", "vendor_name": f"Vendor {i} Ltd", "trade": rnd.choice(["Lift", "Fire", "HVAC"])}
               for i in range(30)]
    wos = [{"wo_code": f"W-{i:06d}", "asset_id": rnd.choice(assets)["asset_id"], "vendor": rnd.choice(vendors)["vendor_code"],
            "cost": str(rnd.randrange(50, 5000)), "status": rnd.choice(["open", "closed"]),
            "raised": f"2026-0{rnd.randrange(1, 9)}-0{rnd.randrange(1, 9)}"} for i in range(n_wos)]
    return {"sites": sites, "assets": assets, "vendors": vendors, "work_orders": wos}


def _run(tables, memo_on: bool, monkeypatch):
    if not memo_on:
        monkeypatch.setattr(memo, "enabled", lambda: False)
    t0 = time.perf_counter()
    res = run_udr_pipeline({k: [dict(r) for r in v] for k, v in tables.items()}, run_id="t",
                           dest_table_by_source={"sites": "sites", "assets": "assets",
                                                 "vendors": "vendors", "work_orders": "work_orders"})
    return res, time.perf_counter() - t0


def _comparable(res):
    d = dataclasses.asdict(res)
    for k in VOLATILE:
        d.pop(k, None)
    return d


def test_the_report_is_identical_with_and_without_the_memo(monkeypatch):
    tables = _workbook()
    with_memo, _ = _run(tables, True, monkeypatch)
    monkeypatch.undo()
    without, _ = _run(tables, False, monkeypatch)
    assert _comparable(with_memo) == _comparable(without)


def test_the_memo_makes_the_pairwise_stages_much_faster(monkeypatch):
    tables = _workbook(n_assets=1500, n_wos=20000)
    _, fast = _run(tables, True, monkeypatch)
    monkeypatch.undo()
    _, slow = _run(tables, False, monkeypatch)
    assert slow / fast >= 5, f"memo {fast:.2f}s vs none {slow:.2f}s"


def test_outside_a_scope_nothing_is_cached():
    rows = [{"a": "1"}]
    calls = []
    assert memo.memoized("k", rows, "a", lambda: calls.append(1) or 1) == 1
    assert memo.memoized("k", rows, "a", lambda: calls.append(1) or 1) == 1
    assert len(calls) == 2


def test_inside_a_scope_a_column_is_computed_once_and_nested_scopes_share_it():
    rows = [{"a": "1"}]
    calls = []
    with memo.memo_scope():
        memo.memoized("k", rows, "a", lambda: calls.append(1) or 1)
        with memo.memo_scope():
            memo.memoized("k", rows, "a", lambda: calls.append(1) or 1)
    assert len(calls) == 1
```

- [ ] **Step 3: Run them to see them fail**

Run: `cd $SVC && scripts/test_offline.sh tests/test_udr_memo.py`
Expected: FAIL — `cannot import name 'memo'`.

- [ ] **Step 4: Implement the memo**

`$SVC/src/udr/memo.py`:

```python
"""Per-column statistics computed once per UDR pass instead of once per column pair.

Every pairwise test in the UDR pipeline (format similarity, value-shape similarity, Jaccard and
containment overlap, referential integrity) recomputes the same per-column facts — the non-null
values, the distinct set, the format and shape distributions — for both columns of every pair:
combinations(181 columns, 2) on the 280k-row run, 797 s. Inside a memo_scope each is computed
once per (rows, column). The functions are pure in their rows, so the report is identical; the
scope pins the row lists it has seen so an id() cannot be reused while it lives.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager
from typing import Any, Callable, TypeVar

T = TypeVar("T")
_MISS = object()


class _Scope:
    __slots__ = ("cache", "pins")

    def __init__(self) -> None:
        self.cache: dict = {}
        self.pins: dict = {}


_SCOPE: "contextvars.ContextVar[_Scope | None]" = contextvars.ContextVar("udr_memo", default=None)


def enabled() -> bool:
    return True


@contextmanager
def memo_scope():
    if _SCOPE.get() is not None:
        yield
        return
    token = _SCOPE.set(_Scope())
    try:
        yield
    finally:
        _SCOPE.reset(token)


def memoized(kind: str, rows: Any, col: Any, compute: Callable[[], T]) -> T:
    scope = _SCOPE.get()
    if scope is None or not enabled() or not isinstance(rows, list):
        return compute()
    key = (kind, id(rows), col)
    hit = scope.cache.get(key, _MISS)
    if hit is _MISS:
        hit = compute()
        scope.cache[key] = hit
        scope.pins[id(rows)] = rows
    return hit  # type: ignore[return-value]
```

- [ ] **Step 5: Memoise the per-column primitives**

In `$SVC/src/udr/primitives.py` add `from .memo import memoized` and route the per-column computations through private memoised helpers. The public functions keep their signatures and return the same objects as before:

```python
def _values(rows: Records, col: str) -> list[Any]:
    """Every row's value for ``col`` (shared, read-only inside a memo scope)."""
    return memoized("values", rows, col, lambda: [r.get(col) for r in (rows or []) if isinstance(r, dict)])


def _nonnull_values(rows: Records, col: str) -> list[str]:
    return memoized("nonnull", rows, col, lambda: _nonnull(_values(rows, col)))


def column_values(rows: Records, col: str) -> list[Any]:
    return list(_values(rows, col))          # a fresh list: callers may keep or change it


def distinct_values(rows: Records, col: str) -> set[str]:
    return memoized("distinct", rows, col, lambda: set(_nonnull_values(rows, col)))


def _lower_distinct(rows: Records, col: str) -> set[str]:
    return memoized("distinct_lower", rows, col, lambda: {v.lower() for v in distinct_values(rows, col)})
```

Then, without changing any arithmetic:
- `null_rate`: `vals = _values(rows, col)` and `len(_nonnull_values(rows, col))` in place of the two scans.
- `format_distribution(rows, col, sample)`: body moved into `_format_distribution_uncached`; the function returns `dict(memoized(("format_dist", sample), rows, col, lambda: _format_distribution_uncached(rows, col, sample)))` (a copy — callers pop from it at line 267).
- `value_shape_distribution`, `column_format`, `dominant_value_shape`: same pattern, keyed `("shape_dist", sample)`, `("format", sample)`, `("dominant_shape", sample)`; inside them use `_nonnull_values(rows, col)[:sample]` in place of `_nonnull(column_values(rows, col))[:sample]`.
- `_jaccard_value_overlap` / `_containment_overlap`: `sa = _lower_distinct(rows_a, col_a)`, `sb = _lower_distinct(rows_b, col_b)`.
- `referential_integrity`: `child_vals = _nonnull_values(child_rows, child_col)`.
- `redundant_column_groups`: `vals = {c: memoized("casefold", rows, c, lambda c=c: [_norm(v).casefold() for v in _values(rows, c)]) for c in cols}`.

- [ ] **Step 6: Open the scopes**

Wrap the whole body of each function in `with memo_scope():` (import `from ..udr.memo import memo_scope` / `from .memo import memo_scope` as appropriate): `run_udr_pipeline`, `build_column_intelligence`, `build_table_resolution`, `build_pk_confirmation`, `merge_duplicate_columns`.

- [ ] **Step 7: Run the memo tests and every UDR test**

Run: `cd $SVC && scripts/test_offline.sh tests/test_udr_memo.py tests/test_udr_primitives.py tests/test_udr_pipeline.py tests/test_udr_preprocessing.py tests/test_udr_id_containment_gate.py tests/test_udr_freetext_grouping_fix.py tests/test_udr_canonical_overrides.py`
Expected: all pass; `test_the_memo_makes_the_pairwise_stages_much_faster` reports a ratio ≥ 5.
Run: full suite — no new failures (the three collection errors stay exactly as in the baseline).

- [ ] **Step 8: Checkpoint (no commit).**

---

### Task 6: Column-mapping re-targets reach the data (both engines)

**Files:**
- Modify: `$SVC/src/graph/nodes/preprocess_node.py` (apply `column_dest_overrides` before the type guard; the guard skips overridden mappings; overrides enter the rename map)
- Test: `$SVC/tests/test_a_column_mapping_retarget_reaches_the_data.py`

**Interfaces:**
- Consumes: `state["column_dest_overrides"]` = `{source_table: {source_field: "<dest column>" | "__new__"}}` (column_mapping_review_node.py:86).
- Produces: `preprocess_node._apply_column_dest_overrides(state) -> dict[str, dict[str, str]]` — mutates the tier mapping lists in place (`target_field`, `column_mapping_override=True`) and returns `{source_table: {source_field: target}}` for the rename map. `"__new__"` targets `_snake(source_field)` (the same snake_case `_collect_approved_new_columns` uses).

- [ ] **Step 1: Write the failing tests**

`$SVC/tests/test_a_column_mapping_retarget_reaches_the_data.py`:

```python
"""B14.1: "map this column to X" is the person's decision and the data must land in X.

Until 1 Oct 2026 the overrides were applied in output_generator_node to tier1_mappings /
tier2_auto_accepted / human_approved_mappings — keys MigrationState does not declare, so LangGraph
had dropped them at the first checkpoint and every re-target was silently ignored."""
import pytest

from src.graph.nodes import preprocess_node as pp


@pytest.fixture
def no_db(monkeypatch):
    import src.db as db

    async def _types():
        return {"assets": {"frequency_value": "integer", "frequency_type": "text", "asset_code": "text"}}
    monkeypatch.setattr(db, "get_plenum_cafm_column_types_by_table", _types)


def _state(overrides):
    return {
        "migration_id": None,
        "event_log": [],
        "table_routing": {"Assets": "assets"},
        "full_tables": {"Assets": [{"Tag": "A1", "Freq": "Quarterly"}, {"Tag": "A2", "Freq": "Monthly"}]},
        "tier1_mappings_by_table": {"Assets": [
            {"source_field": "Tag", "target_field": "asset_code"},
            {"source_field": "Freq", "target_field": "frequency_type"},
        ]},
        "column_dest_overrides": overrides,
    }


async def test_a_retarget_moves_the_values_to_the_chosen_column(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Tag": "serial_number"}}))
    row = out["cleaned_tables"]["Assets"][0]
    assert row["serial_number"] == "A1" and "asset_code" not in row
    m = out["tier1_mappings_by_table"]["Assets"][0]
    assert m["target_field"] == "serial_number" and m["column_mapping_override"] is True


async def test_new_column_lands_under_its_snake_case_name(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Tag": "__new__"}}))
    assert out["cleaned_tables"]["Assets"][0]["tag"] == "A1"


async def test_an_explicit_choice_is_not_second_guessed_by_the_type_guard(no_db):
    out = await pp.preprocess_node(_state({"Assets": {"Freq": "frequency_value"}}))
    assert out["cleaned_tables"]["Assets"][0]["frequency_value"] == "Quarterly"


async def test_a_column_with_no_tier_mapping_is_still_renamed(no_db):
    st = _state({"Assets": {"Freq": "frequency_type"}})
    st["tier1_mappings_by_table"]["Assets"] = [{"source_field": "Tag", "target_field": "asset_code"}]
    out = await pp.preprocess_node(st)
    assert out["cleaned_tables"]["Assets"][0]["frequency_type"] == "Quarterly"


async def test_no_overrides_changes_nothing(no_db):
    out = await pp.preprocess_node(_state({}))
    assert set(out["cleaned_tables"]["Assets"][0]) == {"asset_code", "frequency_type"}
```

- [ ] **Step 2: Run them to see them fail**

Run: `cd $SVC && scripts/test_offline.sh tests/test_a_column_mapping_retarget_reaches_the_data.py`
Expected: the first four FAIL (keys stay `asset_code` / `frequency_type`), the last passes.

- [ ] **Step 3: Implement**

In `preprocess_node.py` add, above `preprocess_node`:

```python
def _snake(s: object) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(s or "").strip().lower()).strip("_") or "column"


def _apply_column_dest_overrides(state: dict) -> dict:
    """The B14.1 column-mapping gate's answers, applied where the data is renamed.

    Each override re-targets the tier mapping of that source field (marked
    ``column_mapping_override`` so the type guard leaves an explicit choice alone) and is returned
    as a rename for the field, which also covers a field no tier mapping named. Matched on
    (table, field) exactly, then case-insensitively."""
    overrides = state.get("column_dest_overrides") or {}
    renames: dict[str, dict[str, str]] = {}
    if not isinstance(overrides, dict) or not overrides:
        return renames
    by_lower = {str(t).lower(): {str(f).lower(): v for f, v in (cols or {}).items()}
                for t, cols in overrides.items() if isinstance(cols, dict)}
    for table, cols in overrides.items():
        if not isinstance(cols, dict):
            continue
        for field, choice in cols.items():
            if not choice:
                continue
            target = _snake(field) if str(choice) == "__new__" else str(choice)
            renames.setdefault(str(table), {})[str(field)] = target
    for bucket in ("tier1_mappings_by_table", "tier2_auto_by_table", "tier2_human_decisions_by_table"):
        for table, maps in (state.get(bucket) or {}).items():
            for m in maps or []:
                if not isinstance(m, dict) or not m.get("source_field"):
                    continue
                sf = str(m["source_field"])
                target = (renames.get(str(table)) or {}).get(sf)
                if target is None:
                    choice = (by_lower.get(str(table).lower()) or {}).get(sf.lower())
                    if choice:
                        target = _snake(sf) if str(choice) == "__new__" else str(choice)
                        renames.setdefault(str(table), {})[sf] = target
                if target and target != m.get("target_field"):
                    m["target_field"] = target
                    m["column_mapping_override"] = True
    return renames
```

In `preprocess_node`, as the first statement inside `try:` (before the type guard):

```python
        _override_renames = _apply_column_dest_overrides(state)
```

In the type guard loop, right after `if not isinstance(_m, dict): continue`, add:

```python
                            if _m.get("column_mapping_override"):
                                continue  # the person chose this column at the column-mapping gate
```

And after the custom-rename block (just before `cleaned_tables = {}`):

```python
        for _st, _cols in _override_renames.items():
            for _sf, _tgt in _cols.items():
                mapping_dict_by_table.setdefault(_st, {})[_sf] = _tgt
```

- [ ] **Step 4: Run the tests to see them pass, then the suite**

Run: `cd $SVC && scripts/test_offline.sh tests/test_a_column_mapping_retarget_reaches_the_data.py tests/test_a_year_first_date_is_never_day_first.py tests/test_two_destination_columns_are_two_facts.py`
Expected: all pass. Full suite: no new failures.

- [ ] **Step 5: Checkpoint (no commit).**

---

# Phase 2 — Bulk write (COPY, staging, merge)

After this phase a Go run uses the engine for the write (`ENGINE_STEPS = {"write"}`); everything
before it is unchanged and hands its cleaned rows over through the Task 4 bridge.

### Task 7: Throwaway Postgres for parity tests

**Files:**
- Create: `$ENG/scripts/parity_db.sh`, `$ENG/scripts/parity_extras.sql`
- Create: `$SVC/tests/engine_parity/__init__.py`, `$SVC/tests/engine_parity/conftest.py`
- (`dev.sh test-db` and `test_offline.sh` with `HOIST_PARITY=1`, both from Tasks 1–2, already point `HOIST_PARITY_DSN` — and for Python `DB_URL` — at `parity_run`.)

**Interfaces:**
- Produces: network `hoist-parity` (`--internal`: no route outside), container `hoist-parity-pg` (`postgres:16-alpine`, data on tmpfs), database `parity_template` (schema loaded). Tests always write to `parity_run`, recreated from the template; `HOIST_PARITY_DSN` names `parity_run`, and the admin connection is the same DSN with `/parity_run` → `/parity`.
- Produces (pytest): fixture `parity_db` → `{"dsn_async": str, "dsn_sync": str}` (skips when `HOIST_PARITY_DSN` is unset) and coroutine `reset_parity_run()` (`DROP DATABASE parity_run WITH (FORCE)` + `CREATE DATABASE parity_run TEMPLATE parity_template`, after disposing the writer's pool).
- Produces (Go tests): helper `testdb.Fresh(t) *pgx.Conn` in `$ENG/internal/testdb/testdb.go` — same recreate, skip when `HOIST_PARITY_DSN` is unset.
- Deterministic ids for parity: `parity_extras.sql` creates `plenum_cafm.gen_random_uuid()` backed by a sequence (`00000000-0000-4000-8000-<12-digit counter>`) and `ALTER ROLE parity SET search_path = plenum_cafm, pg_catalog, public`, so an unqualified `gen_random_uuid()` (CREATE_METER_SQL) is deterministic in the parity database only.

- [ ] **Step 1: Write the script**

`$ENG/scripts/parity_db.sh` (chmod +x):

```sh
#!/bin/sh
# A throwaway Postgres for writer parity tests. Never the stack database, never Azure:
# its own container on an --internal network (no route out), data on tmpfs, removed by `down`.
#   parity_db.sh up | down | reset | psql
set -eu
ROOT="$(cd "$(dirname "$0")/../../../../../.." && pwd)"   # repository root
NET=hoist-parity; PG=hoist-parity-pg
SQL="docker exec -i $PG psql -v ON_ERROR_STOP=0 -q -U parity"
case "${1:-up}" in
  up)
    docker network inspect $NET >/dev/null 2>&1 || docker network create --internal $NET >/dev/null
    docker inspect $PG >/dev/null 2>&1 || docker run -d --name $PG --network $NET \
      -e POSTGRES_USER=parity -e POSTGRES_PASSWORD=parity -e POSTGRES_DB=parity \
      --tmpfs /var/lib/postgresql/data:rw postgres:16-alpine >/dev/null
    until docker exec $PG pg_isready -U parity -q; do sleep 1; done
    $SQL -d parity -c "DROP DATABASE IF EXISTS parity_template WITH (FORCE)"
    $SQL -d parity -c "CREATE DATABASE parity_template"
    $SQL -d parity_template < "$ROOT/db/01_schema.sql" >/dev/null 2>&1 || true
    OPS="$ROOT/apps/backend/cafm-connector-service-final/svc-operations-intelligence/migrations"
    for pass in 1 2; do   # alphabetical order skips ALTERs on tables created later; a second pass applies them
      for f in $(ls "$OPS"/*.sql | sort); do $SQL -d parity_template < "$f" >/dev/null 2>&1 || true; done
    done
    $SQL -d parity_template < "$(dirname "$0")/parity_extras.sql"
    missing=$($SQL -d parity_template -At -c "SELECT string_agg(t, ',') FROM unnest(ARRAY['organizations','sites','buildings','building_sections','floors','assets','work_orders','vendors','vendor_contracts','energy_meters','meter_readings','compliance_certificates','ppm_visits','spare_parts','resources','inspections']) t WHERE to_regclass('plenum_cafm.'||t) IS NULL")
    [ -z "$missing" ] || { echo "parity schema is missing: $missing" >&2; exit 1; }
    echo "parity database ready (template: parity_template)" ;;
  reset)
    $SQL -d parity -c "DROP DATABASE IF EXISTS parity_run WITH (FORCE)"
    $SQL -d parity -c "CREATE DATABASE parity_run TEMPLATE parity_template" ;;
  psql) exec docker exec -it $PG psql -U parity -d "${2:-parity_run}" ;;
  down)
    docker rm -f $PG >/dev/null 2>&1 || true
    docker network rm $NET >/dev/null 2>&1 || true
    echo "parity database removed" ;;
  *) echo "usage: parity_db.sh up|reset|psql [db]|down" >&2; exit 2 ;;
esac
```

`$ENG/scripts/parity_extras.sql`:

```sql
-- Parity database only: make generated ids reproducible so two writers can be compared row for row.
CREATE SEQUENCE IF NOT EXISTS plenum_cafm.parity_uuid_seq;
CREATE OR REPLACE FUNCTION plenum_cafm.gen_random_uuid() RETURNS uuid LANGUAGE sql AS
  $$ SELECT ('00000000-0000-4000-8000-' || lpad(nextval('plenum_cafm.parity_uuid_seq')::text, 12, '0'))::uuid $$;
ALTER ROLE parity SET search_path = plenum_cafm, pg_catalog, public;
INSERT INTO plenum_cafm.organizations (id, name)
  SELECT '11111111-1111-4111-8111-111111111111', 'Parity Org'
  WHERE NOT EXISTS (SELECT 1 FROM plenum_cafm.organizations WHERE id::text = '11111111-1111-4111-8111-111111111111');
```

(If `organizations` has other NOT NULL columns in `01_schema.sql`, add them to that INSERT — check with `\d plenum_cafm.organizations` after the first `up`.)

- [ ] **Step 2: Bring it up and prove it is isolated**

Run: `$ENG/scripts/parity_db.sh up && docker run --rm --network hoist-parity curlimages/curl -m 5 -s https://example.com; echo "exit=$?"`
Expected: "parity database ready", then a non-zero exit from curl (no route out of the network).

- [ ] **Step 3: pytest and Go fixtures**

`$SVC/tests/engine_parity/conftest.py`:

```python
"""Fixtures for the throwaway parity database (engine/scripts/parity_db.sh). Never the stack DB."""
import os

import pytest

DSN = os.environ.get("HOIST_PARITY_DSN")          # postgresql://parity:parity@hoist-parity-pg:5432/parity_run
ADMIN_DSN = (DSN or "").replace("/parity_run", "/parity")


@pytest.fixture
def parity_db():
    if not DSN:
        pytest.skip("parity database not available (engine/scripts/parity_db.sh up; HOIST_PARITY=1)")
    return {"dsn_sync": DSN, "dsn_async": DSN.replace("postgresql://", "postgresql+asyncpg://")}


async def reset_parity_run() -> None:
    """A fresh parity_run from the template, with the writer's pool released first."""
    import asyncpg

    from src.db import get_async_engine

    try:
        await get_async_engine().dispose()
    except Exception:  # noqa: BLE001 — no pool yet
        pass
    conn = await asyncpg.connect(ADMIN_DSN)
    try:
        await conn.execute("DROP DATABASE IF EXISTS parity_run WITH (FORCE)")
        await conn.execute("CREATE DATABASE parity_run TEMPLATE parity_template")
    finally:
        await conn.close()
```

`$ENG/internal/testdb/testdb.go`:

```go
// Package testdb gives Go tests a fresh copy of the throwaway parity database (scripts/parity_db.sh).
package testdb

import (
	"context"
	"os"
	"strings"
	"testing"

	"github.com/jackc/pgx/v5"
)

func Fresh(t *testing.T) *pgx.Conn {
	t.Helper()
	dsn := os.Getenv("HOIST_PARITY_DSN")
	if dsn == "" {
		t.Skip("HOIST_PARITY_DSN not set (scripts/dev.sh test-db)")
	}
	ctx := context.Background()
	admin, err := pgx.Connect(ctx, strings.Replace(dsn, "/parity_run?", "/parity?", 1))
	if err != nil {
		t.Fatal(err)
	}
	for _, q := range []string{
		"DROP DATABASE IF EXISTS parity_run WITH (FORCE)",
		"CREATE DATABASE parity_run TEMPLATE parity_template",
	} {
		if _, err := admin.Exec(ctx, q); err != nil {
			t.Fatal(err)
		}
	}
	admin.Close(ctx)
	conn, err := pgx.Connect(ctx, dsn)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { conn.Close(context.Background()) })
	return conn
}

// DSN is the run database's DSN, for code under test that opens its own connection.
func DSN() string { return os.Getenv("HOIST_PARITY_DSN") }
```

`dev.sh test-db` passes `HOIST_PARITY_DSN=postgres://parity:parity@hoist-parity-pg:5432/parity_run?sslmode=disable`.

- [ ] **Step 4: Checkpoint (no commit).** `parity_db.sh down` at the end of every session that ran it.

---

### Task 8: Cells, Arrow tables, destination schema, and value coercion

**Files:**
- Create: `$ENG/internal/cell/cell.go` (+`_test.go`), `$ENG/internal/pystr/pystr.go` (+`_test.go`)
- Create: `$ENG/internal/arrowtab/arrowtab.go` (+`_test.go`)
- Create: `$ENG/internal/pgschema/pgschema.go` (+`_test.go`, Postgres)
- Create: `$ENG/internal/coerce/coerce.go`, `$ENG/internal/coerce/bind.go` (+`_test.go`)
- Create: `$ENG/cmd/hoist-engine/eval_coerce.go` (`coerce-eval` command, test-only use)
- Test: `$SVC/tests/test_engine_coerce_oracle.py`
- Modify: `$ENG/go.mod` (`github.com/apache/arrow-go/v18`, `github.com/jackc/pgx/v5`, `golang.org/x/text`) via `dev.sh tidy`

**Interfaces:**
- Produces `cell.Cell{K Kind; S string; I int64}`, kinds `Null`, `Str`, `Int`; `cell.Str(s)`, `cell.Int(i)`, `cell.None`; methods `IsNone()`, `Truthy()` (Python truthiness), `PyStr()` (Python `str()`, `"None"` for None), `Empty()` (`v is None or str(v) == ""`).
- Produces `pystr.Strip(s)` (Python's whitespace set incl. `\x1c-\x1f`, `\x85`, `\xa0`, U+1680, U+2000–U+200A, U+2028/9, U+202F, U+205F, U+3000), `pystr.Lower(s)` (full mapping, `x/text/cases`), `pystr.Casefold(s)`, `pystr.Len(s)` (code points), `pystr.FloatRepr(f float64) string` (Python `repr`), `pystr.ParseInt(s) (big.Int, bool)` and `pystr.ParseFloat(s) (float64, bool)` (Python `int()`/`float()` grammar: sign, underscores between digits, Unicode decimal digits, `inf`/`nan` words for float), `pystr.ParseDecimal(s) (string, bool)` (Python `Decimal()` acceptance; returns the canonical `str(Decimal(s))`).
- Produces `arrowtab.Table{Name string; Columns []string; Rows int}` with `Cell(col, row int) cell.Cell`, `ColumnIndex(name) int`; `arrowtab.ReadDir(dir) ([]*Table, error)`; `arrowtab.NewBuilder(name string, columns []string) *Builder`, `(*Builder).Append([]cell.Cell)`, `(*Builder).Build() *Table`; `arrowtab.WriteDir(dir string, tables []*Table) error` — byte-compatible with the Task 4 manifest/IPC format (Python reads Go files and vice versa).
- Produces `pgschema.Load(ctx, q Querier, schema string, tables []string) (map[string]*Table, error)` with `Table{Name; Exists bool; Columns []Column; ByName map[string]*Column; UniqueSets [][]string; FKs []FK}`, `Column{Name, DataType, UDT, FormatType string; Nullable bool; Default *string; MaxChars *int; NumPrecision, NumScale *int; EnumLabels []string; IntBits int}`, `FK{Name, Trigger string; Columns, RefColumns []string; RefSchema, RefTable string}` (FKs ordered by RI trigger name — the order Postgres reports violations). `Querier` = `Query(ctx, sql, args...) (pgx.Rows, error)`.
- Produces `coerce.Value{K Kind; S string}` where `K` ∈ `Null, Mismatch, Text, Int, Decimal, Float, Bool, Timestamp, TimestampTZ, Date, JSON, UUID, Raw`, `S` the canonical text Postgres parses; `coerce.ForType(v cell.Cell, dbType string) Value` (port of `_coerce_value_for_db_type`, write_node.py:1302-1440) and `coerce.BindCheck(v Value, col *pgschema.Column) error` (what asyncpg would refuse at bind time, see Step 5); `coerce.InferSQLType(c cell.Cell) string` (port of `_infer_sql_type_for_value`, write_node.py:1055).

- [ ] **Step 1: pystr tests first (goldens from Python)**

`$ENG/internal/pystr/pystr_test.go` — table tests, values computed in the worker image with Python 3.12 and pasted as literals:

```go
func TestFloatReprMatchesPython(t *testing.T) {
	cases := map[float64]string{
		0: "0.0", 1: "1.0", -1.5: "-1.5", 0.1: "0.1", 1e16: "1e+16", 1e15: "1000000000000000.0",
		0.0001: "0.0001", 0.00001: "1e-05", 123456789.123: "123456789.123",
		1.7976931348623157e308: "1.7976931348623157e+308", 5e-324: "5e-324", 2.5e-7: "2.5e-07",
	}
	for f, want := range cases {
		if got := FloatRepr(f); got != want {
			t.Errorf("FloatRepr(%v) = %q, want %q", f, got, want)
		}
	}
	if FloatRepr(math.Copysign(0, -1)) != "-0.0" || FloatRepr(math.NaN()) != "nan" || FloatRepr(math.Inf(-1)) != "-inf" {
		t.Error("specials")
	}
}

func TestStripUsesPythonsWhitespace(t *testing.T) {
	if got := Strip("\x1c　 a b \x1f"); got != "a b" {
		t.Fatalf("got %q", got)
	}
	if got := Strip("​ a"); got != "​ a" { // ZWSP is not whitespace to Python
		t.Fatalf("got %q", got)
	}
}

func TestParseIntAndFloatFollowPythonsGrammar(t *testing.T) {
	ints := map[string]bool{"5": true, "+5": true, "-0": true, "1_000": true, "1__0": false, "_1": false,
		"٣": true, "1.0": false, "0x10": false, "": false, " 5": false}
	for s, ok := range ints {
		if _, got := ParseInt(s); got != ok {
			t.Errorf("ParseInt(%q) ok=%v want %v", s, got, ok)
		}
	}
	floats := map[string]bool{"1e5": true, ".5": true, "5.": true, "inf": true, "-Infinity": true, "nan": true,
		"1_0.5": true, "1e": false, "e5": false, "1,0": false, "0x1p3": false}
	for s, ok := range floats {
		if _, got := ParseFloat(s); got != ok {
			t.Errorf("ParseFloat(%q) ok=%v want %v", s, got, ok)
		}
	}
}
```

(The literals above are Python 3.12's answers; the implementer re-checks any doubtful one with `docker exec hoistra_plenum-schema-mapper-worker-1 python -c 'print(repr(float(...)))'` before relying on it.)

- [ ] **Step 2: Arrow round trip with Python (cross-language)**

`$ENG/internal/arrowtab/arrowtab_test.go`: build a table with Str/Null/Int(0) cells and unicode names, `WriteDir`, `ReadDir`, compare; and `TestReadsAPythonWrittenDir` reading `testdata/py_bridge/` (generate it once: `scripts/test_offline.sh`-style one-liner calling `src.engine.store.write_tables` on the same rows into `$ENG/internal/arrowtab/testdata/py_bridge/`, committed by Hussain with the rest).

`$SVC/tests/test_engine_store.py` gains `test_python_reads_a_go_written_dir` (skips without the binary): runs `hoist-engine arrow-selftest --job` (a 15-line command in `eval_coerce.go`'s file that writes the same fixture with `arrowtab.WriteDir`) and asserts `read_tables` returns it.

- [ ] **Step 3: Implement cell, pystr, arrowtab**

`cell.go`:

```go
// Package cell is one value of a migrated table as the Python pipeline holds it: a string,
// None, or the integer 0 that preprocess's numeric null fill leaves.
package cell

import "strconv"

type Kind uint8

const (
	Null Kind = iota
	Str
	Int
)

type Cell struct {
	K Kind
	S string
	I int64
}

var None = Cell{}

func Of(s string) Cell  { return Cell{K: Str, S: s} }
func OfInt(i int64) Cell { return Cell{K: Int, I: i} }

func (c Cell) IsNone() bool { return c.K == Null }

// Truthy is Python's bool(v).
func (c Cell) Truthy() bool {
	switch c.K {
	case Str:
		return c.S != ""
	case Int:
		return c.I != 0
	}
	return false
}

// PyStr is Python's str(v).
func (c Cell) PyStr() string {
	switch c.K {
	case Str:
		return c.S
	case Int:
		return strconv.FormatInt(c.I, 10)
	}
	return "None"
}

// Empty is the writer's `v is None or str(v) == ""`.
func (c Cell) Empty() bool { return c.K == Null || (c.K == Str && c.S == "") }
```

`pystr.go`: `Strip`, `Lower`, `Casefold`, `Len`, `FloatRepr`, `ParseInt`, `ParseFloat`, `ParseDecimal`. `FloatRepr`:

```go
// FloatRepr is Python's repr(float): shortest round-trip digits, fixed notation for
// exponents -4..15, scientific with a two-digit signed exponent otherwise.
func FloatRepr(f float64) string {
	switch {
	case math.IsNaN(f):
		return "nan"
	case math.IsInf(f, 1):
		return "inf"
	case math.IsInf(f, -1):
		return "-inf"
	case f == 0:
		if math.Signbit(f) {
			return "-0.0"
		}
		return "0.0"
	}
	s := strconv.FormatFloat(f, 'e', -1, 64)
	mant, expStr, _ := strings.Cut(s, "e")
	exp, _ := strconv.Atoi(expStr)
	neg := strings.HasPrefix(mant, "-")
	mant = strings.TrimPrefix(mant, "-")
	digits := strings.Replace(mant, ".", "", 1)
	var out string
	switch {
	case exp < -4 || exp >= 16:
		out = digits[:1]
		if len(digits) > 1 {
			out += "." + digits[1:]
		}
		sign := "+"
		if exp < 0 {
			sign, exp = "-", -exp
		}
		out += fmt.Sprintf("e%s%02d", sign, exp)
	case exp >= 0:
		if len(digits) <= exp+1 {
			out = digits + strings.Repeat("0", exp+1-len(digits)) + ".0"
		} else {
			out = digits[:exp+1] + "." + digits[exp+1:]
		}
	default:
		out = "0." + strings.Repeat("0", -exp-1) + digits
	}
	if neg {
		out = "-" + out
	}
	return out
}
```

`arrowtab.go`: reader concatenates each column's chunks (`array.Concatenate`) into one `*array.String`; a field with metadata `hoist.null_fill=int0` maps null → `cell.OfInt(0)`; writer uses `ipc.NewFileWriter(f, ipc.WithSchema(s), ipc.WithZstd())`, batches of 65,536 rows, and writes `manifest.json` with the same keys as Python (`version`, `tables[].name/file/rows/columns`).

- [ ] **Step 4: pgschema against the parity database**

`pgschema_test.go` (`testdb.Fresh`): create `plenum_cafm.pg_t(id uuid primary key, code varchar(8) unique, qty integer not null, amt numeric(6,2), kind plenum_cafm.pg_kind, parent uuid references plenum_cafm.pg_t(id))` with `CREATE TYPE plenum_cafm.pg_kind AS ENUM ('a','b')`; assert `DataType` strings are exactly information_schema's (`uuid`, `character varying`, `integer`, `numeric`, `USER-DEFINED`), `MaxChars=8`, `NumPrecision=6/NumScale=2`, `EnumLabels=[a b]`, `UniqueSets` contains `{id}` and `{code}`, `FKs[0].Columns=[parent]`, `IntBits=32` for `qty`, and `Exists=false` for a missing table.

Queries (exactly these, `$1` schema, `$2` table names array):

```sql
SELECT c.table_name, c.column_name, c.data_type, c.udt_name, c.is_nullable, c.column_default,
       c.character_maximum_length, c.numeric_precision, c.numeric_scale,
       format_type(a.atttypid, a.atttypmod) AS format_type
  FROM information_schema.columns c
  JOIN pg_namespace n ON n.nspname = c.table_schema
  JOIN pg_class t ON t.relnamespace = n.oid AND t.relname = c.table_name
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attname = c.column_name
 WHERE c.table_schema = $1 AND c.table_name = ANY($2)
 ORDER BY c.table_name, c.ordinal_position;

SELECT t.relname, i.indexrelid, a.attname, i.indnullsnotdistinct
  FROM pg_index i JOIN pg_class t ON t.oid = i.indrelid JOIN pg_namespace n ON n.oid = t.relnamespace
  CROSS JOIN LATERAL unnest(i.indkey) AS k(attnum)
  JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = k.attnum
 WHERE n.nspname = $1 AND t.relname = ANY($2) AND i.indisunique AND i.indpred IS NULL;

SELECT t.relname, con.conname, tg.tgname,
       ARRAY(SELECT attname FROM unnest(con.conkey) WITH ORDINALITY k(n, o) JOIN pg_attribute ON attrelid = con.conrelid AND attnum = k.n ORDER BY o),
       rn.nspname, rt.relname,
       ARRAY(SELECT attname FROM unnest(con.confkey) WITH ORDINALITY k(n, o) JOIN pg_attribute ON attrelid = con.confrelid AND attnum = k.n ORDER BY o)
  FROM pg_constraint con JOIN pg_class t ON t.oid = con.conrelid JOIN pg_namespace n ON n.oid = t.relnamespace
  JOIN pg_class rt ON rt.oid = con.confrelid JOIN pg_namespace rn ON rn.oid = rt.relnamespace
  JOIN pg_trigger tg ON tg.tgconstraint = con.oid AND tg.tgrelid = con.conrelid
 WHERE con.contype = 'f' AND n.nspname = $1 AND t.relname = ANY($2)
 ORDER BY t.relname, tg.tgname;

SELECT t.typname, e.enumlabel FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid ORDER BY t.typname, e.enumsortorder;
```

(The FK query joins the child-side RI check trigger — `tgrelid = conrelid` — whose name order is the order Postgres raises violations in.)

- [ ] **Step 5: Coercion port + bind rules, pinned by a Python oracle**

`$SVC/tests/test_engine_coerce_oracle.py` (skips without the binary): builds every pair of the value corpus × db-type corpus below, computes Python's answer with `write_node._coerce_value_for_db_type`, normalises it to `{"k": kind, "s": text}` (`None` → `null`; the sentinel → `mismatch`; `bool` → `bool`/`"true"|"false"`; `int` → `int`/`str(v)`; `Decimal` → `decimal`/`str(v)`; `datetime` → `timestamptz` if `tzinfo` else `timestamp`, `/v.isoformat()`; `date` → `date`/`isoformat`; a `str` result keeps the branch the db type selects: text/json/uuid/raw), runs `hoist-engine coerce-eval` on the same cases, and asserts equality case by case (reporting the first 20 differences).

Value corpus (strings unless noted): `None`, `0` (int), `""`, `"  "`, `"5"`, `" 5 "`, `"-12"`, `"1_000"`, `"٣"`, `"102.0"`, `"1.9"`, `"1e3"`, `"inf"`, `"nan"`, `"Quarterly"`, `"1,234"`, `"12.50"`, `"-0.0"`, `"true"`, `"T"`, `"yes"`, `"N"`, `"2"`, `"2025-12-31"`, `"2025-12-31T10:30:00"`, `"2025-12-31 10:30"`, `"2025-12-31T10:30:00Z"`, `"2025-12-31T10:30:00+05:30"`, `"20251231"`, `"2025-W01-1"`, `"31/12/2025"`, `"2026-07-10 00:00:00"`, `"{\"a\": 1}"`, `"[1, 2]"`, `"hello"`, `"0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e"`, `"{0E9A1C1E-6B6F-4F0A-9D0E-8F3B2A1C4D5E}"`, `"0e9a1c1e6b6f4f0a9d0e8f3b2a1c4d5e"`, `"urn:uuid:0e9a1c1e-6b6f-4f0a-9d0e-8f3b2a1c4d5e"`, `"A-001"`, `"99999999999999999999"`.
DB-type corpus: `text`, `character varying`, `character`, `citext`, `name`, `integer`, `bigint`, `smallint`, `numeric`, `double precision`, `real`, `boolean`, `timestamp without time zone`, `timestamp with time zone`, `date`, `json`, `jsonb`, `uuid`, `interval`, `time without time zone`, `USER-DEFINED`, `ARRAY`, `bytea`.

`coerce.go` follows the Python branch order exactly (text-ish → interval → int → numeric/float → boolean → timestamp → date → json → uuid → raw), using `pystr` for every parse. `bind.go` encodes what asyncpg 0.31 refuses before a statement reaches Postgres — each one turns a row into a failed row in the Python writer, so the engine must fail the same rows:

| Column / value | asyncpg | `BindCheck` |
|---|---|---|
| `smallint`/`integer`/`bigint`, int outside 16/32/64-bit range | OverflowError | error "value out of int{N} range" |
| `timestamp without time zone`, aware datetime | TypeError (naive/aware subtraction) | error |
| `uuid`, braces / `urn:uuid:` / other non-hex | ValueError | error (accept only hex digits and `-`, 32 digits) |
| `json`/`jsonb`, text that is not JSON | Postgres invalid input | error |
| `interval`, `time…`, `ARRAY`, `bytea`, `point…`, any value | wrong Python type | error |
| `USER-DEFINED` enum, label not in `EnumLabels` | Postgres invalid input | error |
| `character varying(n)`/`character(n)`, longer than n code points | Postgres value too long | error |
| `numeric(p,s)`, integer digits > p−s after rounding to s | Postgres numeric overflow | error |

(Whether asyncpg treats a naive datetime for `timestamp with time zone` as UTC is settled by the Task 12 parity scenario `tz_values`; set the engine's session `TimeZone` to whatever makes that scenario equal.)

- [ ] **Step 6: Run everything**

Run: `$ENG/scripts/dev.sh tidy && $ENG/scripts/dev.sh test && $ENG/scripts/parity_db.sh up && $ENG/scripts/dev.sh test-db && $ENG/scripts/dev.sh build-linux && (cd $SVC && scripts/test_offline.sh tests/test_engine_coerce_oracle.py tests/test_engine_store.py)`
Expected: all green; the oracle reports 0 differences over 41 × 23 cases.

- [ ] **Step 7: Checkpoint (no commit).**

---

### Task 9: The write rules — normalisation, identifiers, hints, keys, defaults, order

**Files:**
- Create: `$ENG/internal/rules/{rules.go,normalize.go,hints.go,keys.go,order.go}` (+ `_test.go`)
- Create: `$ENG/cmd/hoist-engine/eval_rules.go` (`rules-eval` command)
- Create: `$SVC/src/engine/rules_spec.py` (the rule tables, read from the Python modules — single source of truth)
- Test: `$SVC/tests/test_engine_rules_oracle.py`

**Interfaces:**
- Produces (Python) `rules_spec.write_rules() -> dict` with keys taken verbatim from the modules: `natural_keys` (`write_node._NATURAL_KEYS`), `core_parents` (the `_CORE_PARENTS` dict — first move it from inside `_apply_records_with_schema_alignment` to module level in write_node.py, unchanged), `references` (`reference_link.REFERENCES`), `building_linked_tables`, `building_hint_tables`, `building_via_asset_tables`, `known_core_tables`, `system_supplied_columns`, `building_hint_keys` (`building_link._HINT_KEYS`), `site_tables` (`_SITE_TABLES`), `merge_keep` (`_MERGE_KEEP`), `meter_hint_keys`, `mpan_keys`, `mprn_keys`, `either_keys`, `section_keys`, `sub_meter_keys`, `fuel_keys`, `floor_keys`, `gas_words`, `elec_words` (meter_link), `write_chunk` (`_WRITE_CHUNK`), `max_consecutive_row_failures`, `widen_scan_cap` (5000).
- Produces (Go) `rules.Spec` (decoded from that dict) and pure functions over `rules.Row` (an ordered map `[]string` keys + `map[string]cell.Cell`):
  `Normalize(spec, table string, row Row, orgID string) Row` (port of `_normalize_row_for_table`, write_node.py:933-1052),
  `SafeIdent(raw string) (string, bool)` (port of `_to_safe_identifier`),
  `BuildingHint(spec, row) (string, bool)`, `MeterHint`, `SupplyNumbers(spec, row) (mpan, mprn string)`, `MeterTypeFor`, `IsSubMeterFor`, `SectionHint`, `FloorHint`, `ReferenceHint(spec, column string, row) (string, bool)` (ports of building_link/meter_link/reference_link, including the `_keys` normalisation of headers),
  `LooksLikeUUID(v string) bool`, `AssetMatchCode(filtered Row) (string, bool)`, `SiteNamesFromRun(spec, tables, routing) map[string]string`,
  `NaturalKeys(spec, table string, filtered TypedRow, dbCols map[string]bool) [][]string` (port of `_natural_keys_for` — "present" uses Python truthiness of the *coerced* value),
  `SystemDefault(col, dbType, orgID string) (coerce.Value, bool)` (port of `_system_default_for_db_type`),
  `WriteOrder(spec, sources []string, routing map[string]string, hierarchies []Edge) []string` (port of `_ordered_source_tables`).
- Produces CLI `hoist-engine rules-eval --job` with `{"spec": {...}, "cases": [{"fn": "normalize"|"safe_ident"|"building_hint"|…, "args": {...}}]}` → `{"results": [...]}`.

- [ ] **Step 1: The oracle test**

`$SVC/tests/test_engine_rules_oracle.py` generates a corpus of rows (≥ 400) by combining, per destination table in `["assets", "locations", "energy_meters", "meter_readings", "work_orders", "vendors", "building_sections", "ppm_visits", "compliance_certificates"]`, header spellings (`"Asset Code"`, `"asset_code"`, `"MPAN Core"`, `"Supply No."`, `"Meter Reference"`, `"site_ref"`, `"Building"`, `"floor_name"`, `"Level"`, `"is_sub_meter"`, `"Fuel"`, `"vendor_name"`, `"Contractor"`, `"wo_code"`, `"work_order_number"`, `"description"`, `"asset_id"`, `"asset_type"`, `"serial"`, `"install_date"`, `"name"`, `"timestamp"`, `"kwh"`, …) with values (`None`, `0`, `""`, `"  "`, real codes, UUIDs, `"Gas"`, `"Electricity"`, `"sub"`, `"main"`, `"yes"`, `"0"`), and for each row calls every Python function in the Interfaces list (`_normalize_row_for_table`, `building_hint`, `meter_hint`, `supply_numbers`, `meter_type_for`, `is_sub_meter_for`, `section_hint`, `floor_hint`, `hint_for` for each REFERENCES column, `asset_match_code`, `_to_safe_identifier` on every header, `_system_default_for_db_type` over the db-type corpus × columns `org_id`, `source`, `created_by`, `flag`, `note`), plus `_ordered_source_tables` over 30 random routings/hierarchies (extract that nested function to module level as `_ordered_source_tables(cleaned_tables, table_routing, confirmed_hierarchies)` first — a pure move with no behaviour change). It sends the same cases to `rules-eval` and asserts identical results, ordered dict keys included (`list(row.items())`).

- [ ] **Step 2: Run it to see it fail** (`rules-eval` unknown command).

- [ ] **Step 3: Port the rules**

Every Go function mirrors its Python source line by line; the Python docstrings move over as Go comments, trimmed. Two rules to get right, because Python's semantics are easy to miss:
- `Normalize` copies the row (`dict(row)`), so key order is the source order, a `setdefault` appends at the end only when the key is absent, `pop`+re-insert moves a key to the end, and assigning an existing key keeps its position.
- `SafeIdent` collisions: two raw keys that normalise to the same identifier — the later value wins, at the earlier position (`safe_row[safe_k] = raw_v`).

- [ ] **Step 4: Run Go tests and the oracle** — `dev.sh test && dev.sh build-linux && scripts/test_offline.sh tests/test_engine_rules_oracle.py` → 0 differences.

- [ ] **Step 5: Checkpoint (no commit).**

---

### Task 10: Resolvers — the same lookups, inside the engine's transaction

**Files:**
- Create: `$ENG/internal/resolve/{resolve.go,building.go,meter.go,reference.go,sections.go,assets.go}` (+ `_test.go` against the parity database)

**Interfaces:**
- Consumes: `rules.Spec`, `pgschema`, a `Tx` interface (`Query`, `QueryRow`, `Exec`, `Begin` for savepoints).
- Produces `resolve.New(tx Tx, spec rules.Spec, org, schema string, siteNames map[string]string) *Set` with:
  `Building(hint) (id string, ok bool)` (cache hits and misses; `Ambiguous []string`),
  `Section(buildingID, hint) (string, bool)` (cache hits only),
  `Floor(buildingID, hint) (string, bool)` (cache both),
  `MeterFind(hint) (string, bool)` (hits cached), `MeterResolve(hint string, m MeterSpec) (string, bool)` (cache both; creates through `CREATE_METER_SQL` only with a building; `Created int`, `Unlinked []string`, `MeterAmbiguous []string`),
  `Reference(column, hint) (string, bool)` (hits cached; `Reads, Resolved int`, `RefAmbiguous []string`, `Unresolved map[string][]string`),
  `ExistingAsset(code) (string, bool)` and `AssetBuilding(ref) (string, bool)` (cache both),
  `Report() map[string]any` (same keys as `ReferenceResolver.report()` / `MeterResolver.report()`).
- Each lookup runs inside its own savepoint; an error is logged ("lookup failed (treated as no match): …") and treated as no match, exactly like `_fetch`.
- The SQL is the Python SQL text with `:name` placeholders rewritten to `$n` — copied from building_link.py, meter_link.py, reference_link.py and write_node.py, not re-authored.

- [ ] **Step 1: Tests against the parity database** — one Go test per rule, each seeding rows with plain SQL in `testdb.Fresh`:
  - `TestBuildingByCodeWinsOverAmbiguousName` (B-301 + two "Bishopsgate Tower" buildings: hint "B-301" via the run's site names resolves to the code match — building_link.py:131-138);
  - `TestBuildingViaSitesTable` (hint matches `sites.site_code`, the site's name matches one building);
  - `TestBuildingAmbiguousResolvesToNothingAndIsReported`;
  - `TestSectionPrefersTheExactName` (two hits, one `by_name` — meter_link.py:207-216);
  - `TestFloorByLevel`; `TestMeterFindHitOnlyCached` (find miss, then the row is inserted, then find hits);
  - `TestMeterCreatedOnlyWithABuilding` (unlinked list otherwise); `TestMeterAmbiguousNeverCreates`;
  - `TestReferenceMissIsAskedAgainLater` (miss, insert vendor, resolve hits — the 23 Sep 2026 rule);
  - `TestLookupErrorIsNoMatchAndTheTransactionSurvives` (a lookup against a missing column, then a good query in the same transaction works).
- [ ] **Step 2: Run them to see them fail; Step 3: port; Step 4: `dev.sh test-db` green; Step 5: Checkpoint (no commit).**

---

### Task 11: The writer — plan before the gate, apply after it

**Files:**
- Create: `$ENG/internal/write/{job.go,run.go,ddl.go,rows.go,emulate.go,load.go,report.go}` (+ `_test.go`, parity database)
- Create: `$ENG/cmd/hoist-engine/cmd_write.go` (registers `write`)

**Interfaces:**
- Consumes: Tasks 8–10.
- Produces the job (strict JSON, every field required unless marked):

```go
type Job struct {
	Mode               string              `json:"mode"`        // "plan" | "apply"
	Schema             string              `json:"schema"`      // "plenum_cafm"
	OrganizationID     string              `json:"organization_id"`
	DefaultBuildingID  string              `json:"default_building_id"`  // "" when none
	CleanedDir         string              `json:"cleaned_dir"`          // engine store dir (Task 4 format)
	OutDir             string              `json:"out_dir"`              // where plan.json / result.json go
	Routing            map[string]string   `json:"table_routing"`
	ApprovedNewColumns map[string][]string `json:"approved_new_columns"`
	Hierarchies        []Edge              `json:"confirmed_hierarchies"` // {source_table, target_table}
	DDL                []DDLStatement      `json:"ddl_statements"`        // {sql, description}: extra-field DDL built in Python
	ColumnRenames      map[string]map[string]string `json:"column_renames"` // the new-column collision safety net
	Rules              rules.Spec          `json:"rules"`
	DeterministicIDs   bool                `json:"deterministic_ids,omitempty"` // parity tests only
}
```

  DSN from env `HOIST_ENGINE_DSN` only.
- Produces the result (apply): the Python writer's keys — `rows_inserted`, `tables_written`, `rows_skipped`, `rows_merged`, `buildings_linked`, `meters_linked`, `meters_created`, `meters_matched`, `meters_unlinked`, `references`, `row_errors` — plus `tables` (per table: `inserted`, `merged`, `skipped`, `already_present`, `seconds`). Counts are what happened in the database (the Python writer counted a whole successful batch as inserted; documented in the spec's table).
- Produces the plan (plan mode, `BEGIN READ ONLY`, rolled back): `{"tables": [{"source", "dest", "rows", "merge_existing_assets", "already_present", "invalid_values": [{"column", "count", "dest_type", "sample"}], "rows_cannot_write": [{"reason", "count"}], "new_columns": [...], "dropped_columns": [...], "widen_to_text": [...], "references_to_resolve": {"vendor_id": n, ...}}], "ddl_statements": n}` — no DDL, no meter creation, no inserts; written to `<out_dir>/plan.json` with the job's input hash.
- Progress: `stage` ∈ `ddl`, `resolve`, `stage_rows`, `insert`; `table` = destination; `done/total` = rows.

**The rules this task ports** (from `write_node._apply_records_with_schema_alignment` and `_insert_rows`; each is a test below):

1. One connection, `SET TIME ZONE` per Task 8's parity finding. Resolve the organisation (`_resolve_valid_organization_id`, including the first-organisation fallback and its warning) and check the uploader's building (write_node.py:1781-1795).
2. **DDL phase**, its own transaction, before any row: the job's `ddl_statements` (all-or-nothing; failure → error `ddl_failed` with the Python message format "DDL execution failed at statement i/n: 'desc'. Database error: …. All k previously executed statements were rolled back."), then per table in write order: `CREATE TABLE IF NOT EXISTS` for a missing table from the first non-empty cleaned row (`id UUID PRIMARY KEY`, `organization_id UUID`, then `InferSQLType` of each value), `ALTER TABLE … ADD COLUMN IF NOT EXISTS` for missing columns (core tables: approved only; others: all), and the numeric → TEXT widening over the first 5,000 normalised rows (`float(str(v).replace(",", ""))` semantics; `SET LOCAL lock_timeout = '5s'` in a savepoint; a failure is a warning; a system-supplied column is dropped from the column set instead). Commit. The widening reads normalised rows before references are resolved — the one place the engine looks at the data before resolution where Python looked after; resolution only writes uuids into uuid columns, so the decision is the same.
3. **Data phase**, one transaction, tables in `WriteOrder`. Per table: re-read the destination (`pgschema.Load`); build every row exactly as the Python loop does (hint from the raw row → `Normalize` → `SafeIdent` → building link → section → floor → energy-meter match/defaults → references in `REFERENCES` order → meter link), resolving each distinct hint once in first-occurrence order (meters are created in the order their first row appears).
4. Per built row, in order: filter (`in columns`, not empty, not a serial `id`), generate an `id` when missing (deterministic in parity tests), coerce (a mismatch drops the field and is tallied with the first sample), assets merge into an existing asset by code (`build_asset_merge_update`: building only fills a gap, `updated_at = now()`), system defaults for required columns with no default, natural-key "already present" check (against the table as it was before this table's inserts — the Python cache keeps misses, so a key repeated inside the file is written twice, and so does the engine), then the statement shape `_build_dml_for_row` chooses (`ON CONFLICT DO NOTHING`, or the assets upsert when its unique index exists).
5. **Python's insertion order is emulated** (`emulate.go`): pending rows flush in chunks of `write_chunk` (500); inside a chunk, rows group by statement shape in first-appearance order. That order (`ord`) decides who wins a unique key inside the file (an earlier `ord` wins; a later duplicate under `DO NOTHING` is not written; under the assets upsert, later rows fold their updatable columns onto the first), and where a run of identical failures reaches `max_consecutive_row_failures` (100) inside one chunk — then the table stops there and every later row of it is skipped, with the Python "stopped after N consecutive identical failures" message.
6. **Failures decided before COPY** (a row that would have failed in Python fails here, the rest of its batch still lands): `BindCheck`; required column missing; length; numeric overflow; enum label; foreign keys checked set-based against the parents (`= ANY($1::type[])`), in RI-trigger order — the first violated key is nulled when every one of its columns is nullable and present, a second violated key fails the row (Python retries once).
7. **Load**: `CREATE TEMP TABLE _hoist_stage (LIKE <dest>) ON COMMIT DROP` + `ord`/`grp` columns, `COPY` (pgx `CopyFrom`) of the surviving rows, then per statement shape in `ord` order one `INSERT INTO <dest> (<cols>) SELECT <cols> FROM _hoist_stage WHERE grp = $1 ORDER BY ord ON CONFLICT …`; merges as one `UPDATE … FROM` over a de-duplicated stage (last row per existing id wins). Each statement in a savepoint; if one fails anyway (a CHECK constraint nobody predicted) its rows are retried one by one, Python-style.
8. Messages are the Python strings: type mismatch ("{table}.{col}: {n} value(s) did not fit column type '{t}' (e.g. '{sample}') — dropped; re-map this column to a compatible destination at the column-mapping gate."), orphan FK, ambiguous buildings, unlinked meters, ambiguous meters; `row_errors` capped at 20 in the same order.
9. A lost connection (pgconn `SafeToRetry`/closed/`57P01`) at any point → rollback, error `connection_lost`.

- [ ] **Step 1: Tests (parity database), one per rule cluster**, each writing a small cleaned dir with `arrowtab.WriteDir` and running `write.Run` with `Mode: "apply"`:
  `TestAssetsLinkToBuildingsAndMergeByCode`, `TestWorkOrdersInheritTheirAssetsBuilding`, `TestReadingsCreateMetersOnlyWithABuilding`, `TestAThousandReadingsWithNoMeterStopAtTheHundredthIdenticalFailure` (checks the message and that rows after the stop are not written), `TestTypeMismatchDropsTheFieldKeepsTheRow`, `TestNumericColumnWidenedToTextForCodes`, `TestApprovedNewColumnIsAddedOnACoreTable`, `TestUnknownColumnIsDroppedOnACoreTable`, `TestMissingTableIsCreatedFromTheFirstRow`, `TestOrphanForeignKeyIsNulledWhenNullable`, `TestTwoViolatedForeignKeysFailTheRow`, `TestNotNullForeignKeyOrphanFailsTheRow`, `TestOverlongValueFailsOnlyItsRow`, `TestInvalidEnumFailsOnlyItsRow`, `TestDuplicateKeyInsideTheFileFirstInPythonOrderWins`, `TestAssetsUpsertFoldsLaterRows`, `TestRerunWritesNothingTwice`, `TestPlanChangesNothing` (row counts and `pg_stat_xact` show no writes; `plan.json` numbers), `TestDDLFailureIsDdlFailed`, `TestApplyConnectionLostRollsBack` (a second connection runs `pg_terminate_backend` on the writer's pid once the stage COPY has begun; the result is `connection_lost` and no target table changed).
- [ ] **Step 2: Run them to see them fail; Step 3: implement; Step 4: `dev.sh test-db` green; Step 5: Checkpoint (no commit).**

---

### Task 12: write_node's Go branch, and database parity with the Python writer

**Files:**
- Create: `$SVC/src/engine/steps.py` (`go_write_node`, `engine_db_env`)
- Modify: `$SVC/src/graph/nodes/write_node.py` — extract three helpers without behaviour change (`_persist_field_mappings(state)`, `_finish_successful_write(state)`, `_ddl_statements_for(state)` incl. the collision safety net returning `(statements, column_renames)` instead of mutating rows when asked), and branch at the top of `write_node`: `if uses_go(state, "write"): return await go_write_node(state)`
- Modify: `$SVC/src/engine/selection.py` — `ENGINE_STEPS = frozenset({"write"})`
- Test: `$SVC/tests/test_engine_write_node.py` (fake engine), `$SVC/tests/engine_parity/test_write_parity.py` (real engine + parity database)

**Interfaces:**
- Consumes: Tasks 3, 4, 11.
- Produces `steps.go_write_node(state) -> state` — the whole write node for a Go run:
  1. hydrate nothing (the cleaned rows are already in `engine_refs["cleaned"]`); build the job (`rules_spec.write_rules()`, `_collect_approved_new_columns(state)`, `_ddl_statements_for(state)`, routing, hierarchies, org, building);
  2. plan: reuse `<write dir>/plan.json` when its `input_hash` (sha256 of the job minus `mode`) matches, else `run_engine("write", job | {"mode": "plan"})`;
  3. gate payload = today's (`migration_id`, `summary{source_type, source_filename, overall_confidence, entity_counts, total_entities}`, `instructions`) + `plan`; `write_gate_payload(migration_id, "write", payload)`; `interrupt(payload)`; `clear_gate_payload`;
  4. reject → today's rejected branch; confirm → `_persist_field_mappings(state)` (once, after the confirm) → `run_engine("write", job | {"mode": "apply"}, env=engine_db_env(), timeout_s=3300)` with progress relayed to `ProgressBeat(migration_id, 90, 99)`;
  5. success → `state["handoff_status"] = "applied_sql_aligned"`, `state["svc_ingestion_response"] = {"status": "applied_sql_aligned", **result}`, then `_finish_successful_write(state)` (EL-M.9, migration_jobs update, status complete, event, node progress, registry snapshot);
  6. `EngineError("connection_lost")` → the exact message write_node.py:614-618 sets; `ddl_failed` → `status="ddl_failed"` + `write_error(..., status="ddl_failed")`; anything else → `"Schema-aligned write failed: …"`.
- `engine_db_env() -> {"HOIST_ENGINE_DSN": get_sync_db_url()}`.

- [ ] **Step 1: Node tests with a fake engine** (`test_engine_write_node.py`): the gate payload carries `plan` and today's keys; confirming calls `apply` once and writes field mappings once (count calls); a resume with an unchanged job reuses `plan.json` (the fake records how often `plan` ran); `connection_lost` sets the Python message; `ddl_failed` sets the status; `MIGRATION_ENGINE=python` keeps the legacy path (no engine call).
- [ ] **Step 2: Database parity** (`engine_parity/test_write_parity.py`, `HOIST_PARITY=1`). For each scenario: reset `parity_run`, seed, run `write_node._apply_records_with_schema_alignment(...)` with `uuid.uuid4` patched to the parity counter (same format as the engine's deterministic ids), dump every destination table (`SELECT * … ORDER BY` all columns, dropping columns whose default is `now()`/`CURRENT_TIMESTAMP` and `updated_at`), reset, run `hoist-engine write` apply with `deterministic_ids`, dump, compare row for row. Scenarios: `assets_buildings_merge`, `work_orders_vendors_references`, `meters_and_readings` (incl. a reading naming no building), `sections_and_floors`, `type_mismatch_and_widening`, `orphans_and_overlong`, `duplicates_inside_the_file`, `rerun_same_file` (run twice; the second run must add nothing), `tz_values`, `uuid_spellings`, `invalid_json`, and `northbridge_b101` (the real 16-sheet workbook's cleaned tables, produced by running the current Python ingest + preprocess on `~/Downloads/<B-101 workbook>` inside the test container — skipped when the file is not mounted).
- [ ] **Step 3: Run them to see them fail; Step 4: implement; Step 5: run both suites green; Step 6: full suite — no new failures; Step 7: Checkpoint (no commit).**

---

# Phase 3 — Outputs

`ENGINE_STEPS = frozenset({"write", "outputs"})` at the end of this phase.

### Task 13: The outputs command — CSV, SQL, JSON, XLSX, streamed and gzip-encoded

**Files:**
- Create: `$ENG/internal/outputs/{job.go,records.go,csv.go,sql.go,pyjson.go,nested.go,xlsx.go,run.go}` (+ `_test.go`), `$ENG/cmd/hoist-engine/cmd_outputs.go`
- Test: `$SVC/tests/test_engine_outputs_oracle.py`

**Interfaces:**
- Job: `{"full_dir", "cleaned_dir", "out_dir", "table_routing", "confirmed_hierarchies", "containment_hierarchy", "lookup_ddl_blocks": [str], "generated_at": str, "schema": "plenum_cafm"}`.
- Result: `{"files": [{"name": "output.json", "path": ".../output.json.gz", "gzip": true, "raw_bytes": n} …], "records_tables": [{"name", "rows", "columns"}], "routed": [{"dest", "rows"}], "xlsx_skipped_reason": "" }`.
- The rules, each the Python code it replaces (output_generator_node.py:400-700, export/*.py):
  - `records_tables` = the cleaned tables, then every full (renamed) table replacing or appending by name (dict semantics: an existing name keeps its position).
  - routed tables: for each source in `records_tables` order, `dest = routing.get(src) or src`, rows appended (`extend`).
  - `table_<dest>.csv`: `pd.DataFrame(rows).to_csv(index=False)` — columns are the union of keys in first-seen order; None → empty; Python `csv` QUOTE_MINIMAL with `"\n"` line endings (a field is quoted when it contains `,`, `"`, `\n` or `\r`, or is the only field of a row and empty).
  - `output.sql`: `export_to_sql(routed, hierarchies)` byte for byte (insertion order, `_quote_ident`, `_sql_literal`, headers, footer), then the B22.1 lookup blocks with the same section banner.
  - `output.json`: `json.dumps({"nested_hierarchy": build_nested_json(records, containment, confirmed), "tables": records, "table_count": n, "tables_included": sorted(names), "generated_at": job.generated_at}, indent=2)` — `pyjson.go` reproduces `json.dumps` (`ensure_ascii`, `", "`/`": "` separators with `indent=2`, `\uXXXX` with surrogate pairs above U+FFFF).
  - `output.xlsx`: one sheet per FULL table (openpyxl in the Python path): title `name[:31]`, a repeated title gets openpyxl's numeric suffix, header then rows; inline strings; a value starting with `=` is written as a formula like openpyxl does; any title openpyxl rejects or any cell with characters `[\x00-\x08\x0b\x0c\x0e-\x1f]` → no workbook, `xlsx_skipped_reason` set (Python's except branch logs and skips it).
  - every text artefact gzip-compressed (level 6), written once, streamed table by table (memory bounded by one table).

- [ ] **Step 1: Oracle test.** `test_engine_outputs_oracle.py` builds records with the tricky values (quotes, commas, newlines, `\r`, unicode beyond the BMP, `None`, `0`, a leading `=`, a single-column table with an empty value, two sources routed to one destination with different column sets, a hierarchy that orders inserts), computes Python's CSV/SQL/JSON exactly as output_generator_node does, runs `hoist-engine outputs` on the same data (via the Task 4 bridge), gunzips, and asserts byte equality (JSON with `generated_at` passed in); for XLSX it reads both workbooks back with `ExcelWorkbook` and compares every cell.
- [ ] **Step 2: Fail → implement → green (`dev.sh test`, oracle 0 diffs) → Checkpoint (no commit).**

### Task 14: output_generator_node's Go branch

**Files:**
- Modify: `$SVC/src/graph/nodes/output_generator_node.py` (branch after the type guard: `if uses_go(state, "outputs"): return await steps.go_outputs(state, log)`), `$SVC/src/export/intermediate_schema_builder.py` (extract `entity_type_for(table_name, table_routing) -> str` used by `build_intermediate_schema` — no behaviour change), `$SVC/src/engine/steps.py` (`go_outputs`), `$SVC/src/engine/selection.py` (`ENGINE_STEPS = {"write", "outputs"}`)
- Modify: `upload_artefacts` to accept `steps.EngineFile(path, raw_len)` values — uploaded as-is with the same `ContentSettings` `encode_artefact` builds from the artefact name.
- Test: `$SVC/tests/test_engine_outputs_node.py`

**Interfaces:**
- `go_outputs(state, log) -> state`: runs `outputs` with progress → `ProgressBeat(80, 88)`; PDF via `generate_pdf_report` (unchanged arguments); `structure.md` via `build_structure_markdown` fed a stand-in that answers `len()` and `[0].keys()` from the engine's `records_tables` counts/columns; uploads every file through `upload_artefacts`; sets exactly the state keys the Python branch sets (`output_json_url`, `output_csv_url` (the xlsx), `output_sql_url`, `migration_report_url`, `output_structure_md_url`, `exported_artefacts`, `current_step = 8`, `execution_logs`, `el_m8_passed = True`), `engine_reports["outputs"] = result`, `engine_reports["entity_counts"] = {entity_type_for(t, routing): rows …}` (non-empty tables only, summed per entity), and **does not** set `intermediate_schema` rows or `output_sql_script`; writes the node log and progress exactly as now; **does not** call `write_step_pause` (the proxy already skips the pause).
- write_node's Go branch (Task 12) reads `engine_reports["entity_counts"]` when present, else `intermediate_schema["entities"]`.

- [ ] **Step 1: Node test with a fake engine** — state keys, the upload list, no step pause written, entity counts. **Step 2:** fail → implement → green. **Step 3:** full suite. **Step 4: Checkpoint (no commit).**

---

# Phase 4 — Parse and profile

`ENGINE_STEPS = frozenset({"write", "outputs", "parse"})` at the end of this phase.

### Task 15: The parse oracle — today's parse as a pure function, and the fixture set

**Files:**
- Modify: `$SVC/src/graph/nodes/ingest_node.py` — extract steps 2–4 (encoding, delimiter, CSV-then-Excel parse, sanitising, post-write sheets) into `parse_source_tables(file_content: bytes) -> ParsedSource` (`detected_file_format`, `source_encoding`, `source_delimiter`, `parsed_tables`, `full_tables`, `set_aside_sheets`), called by the node; no behaviour change.
- Create: `$SVC/tests/engine_oracle/__init__.py`, `$SVC/tests/engine_oracle/parse_fixtures.py` (writes the fixtures below into a temp dir with openpyxl / raw bytes)
- Create: `$SVC/tests/engine_oracle/compare_parse.py` (CLI: `python -m tests.engine_oracle.compare_parse <files…>` prints per-file, per-sheet differences between the Python parse and `hoist-engine parse`)
- Test: `$SVC/tests/test_engine_parse_oracle.py` (skips without the binary)

Fixtures (each a separate file): `banner_rows.xlsx` (title row, blank row, header), `merged_title.xlsx`, `dup_headers.xlsx` (`Code, Code, code`), `empty_headers.xlsx`, `numeric_headers.xlsx` (2024, 1.5, a date), `dates_builtin.xlsx` (numFmt 14–22, 45–47), `dates_custom_formats.xlsx` (`dd/mm/yyyy hh:mm`, `yyyy-mm-dd`, `mmm-yy`, `[h]:mm`, `h:mm AM/PM`, `"Date: "dd/mm`, `[$-409]d-mmm-yyyy`, `0.00`, `#,##0`, `0%`, `@`), `dates_1904.xlsx`, `times_and_durations.xlsx`, `booleans_errors.xlsx` (TRUE, #N/A, #DIV/0!), `formulas_no_cache.xlsx`, `rich_text.xlsx`, `hidden_sheet.xlsx`, `post_write_sheets.xlsx` (ContractTerms, README, Invoices beside a data sheet), `numbers.xlsx` (0.1, 1e-7, 1e16, 123456789012, -0.0, 2^53+1, leading-zero text "007"), `na_tokens.xlsx` (NA, N/A, null, None, #N/A as text, whitespace-only), `empty_and_header_only.xlsx`, `wide_styled_empty_columns.xlsx`, `unicode.xlsx`; CSV: `comma.csv`, `semicolon.csv`, `tab.tsv` (TSV whose text contains commas — `_detect_delimiter` picks `,`), `pipe.csv`, `quoted_newlines.csv`, `bom_crlf.csv`, `blank_and_whitespace_lines.csv`, `na_tokens.csv`, `short_rows.csv`, `too_many_fields.csv` (parse error), `trailing_comma_data_rows.csv` (pandas' implicit index), `dup_and_empty_headers.csv`, `cp1252.csv` (é, €, ’ as Windows-1252 bytes), `latin1.csv`.

- [ ] **Step 1: Refactor, run the existing ingest tests (`test_excel_parser.py`, `test_a_single_workbook_sets_its_extras_aside.py`, `test_one_ingest_does_not_freeze_the_product.py`) — unchanged.**
- [ ] **Step 2: Write `test_engine_parse_oracle.py`**: for every fixture, Python = `parse_source_tables` → `merge_duplicate_columns(full_tables)` → `scan_nan_values(full_tables)`; Go = `hoist-engine parse` with `{"source": path, "out_dir": tmp, "encoding": <python's chardet answer when the bytes are not UTF-8, else "">, "known_destination_columns": sorted(column_merge.KNOWN_DESTINATION_COLUMNS), "post_write_sheets": sorted(ingest_node.POST_WRITE_SHEETS)}`; compare `detected_file_format`, `source_delimiter`, `parsed_tables` (dicts, key order included), full tables (via `store.read_tables`, which honours the manifest's kept columns), `nan_report`, `duplicate_column_report`, and the error text class for `too_many_fields.csv`.
- [ ] **Step 3: Run it to see it fail (`parse` unknown); Checkpoint (no commit).**

### Task 16: CSV reader — pandas' C tokenizer, rule for rule

**Files:** Create `$ENG/internal/csvread/{tokenize.go,frame.go}` (+ `_test.go`).

**Interfaces:** `csvread.Delimiter(sample string) byte` (port of `_detect_delimiter`: first 4,096 chars, first 5 lines, `,` → `\t` → `;` → `|`); `csvread.Read(text string, delim byte) (columns []string, rows [][]cell.Cell, err error)` — `text` is already decoded (UTF-8, BOM stripped; Windows-1252 or the codec Python named, via `x/text/encoding/charmap`).

The tokenizer is pandas' `tokenizer.c` state machine for the default dialect (`"` quote, double-quote escaping, no escape char, `skip_blank_lines=True`):

```go
// states, as tokenizer.c names them
const (
	startRecord = iota
	startField
	inField
	inQuotedField
	quoteInQuotedField
	eatCRNL
	whitespaceLine
)
```

- `startRecord`: `\n` → skip the empty line; `\r` → `eatCRNL` (skip); space/tab (not the delimiter) → `whitespaceLine`; else → `startField` with the same byte.
- `whitespaceLine`: `\n`/`\r` → the line was blank, skip it; space/tab → stay; anything else → **backtrack to the start of the line** and parse it as a normal record (its spaces are data).
- `startField`: `\n` → end field, end record; `\r` → end field, `eatCRNL`; `"` → `inQuotedField`; delimiter → empty field; else append, `inField`.
- `inField`: `\n`/`\r` → end field and record; delimiter → end field; else append (a `"` here is literal).
- `inQuotedField`: `"` → `quoteInQuotedField`; else append (newlines included).
- `quoteInQuotedField`: `"` → append `"`, back to `inQuotedField`; delimiter → end field; `\n`/`\r` → end field and record; else append the byte, `inField`.
- EOF: an open field/record is closed.

`frame.go` then applies `read_csv(dtype=str)`: header = first record; empty header → `Unnamed: {i}`; duplicate names de-duplicated with pandas' `dedup_names` (`a`, `a.1`, …, skipping names already taken); if the first data row has exactly one more field than the header, the first field of every row is the index and is dropped (pandas' implicit index); a later row with more fields than allowed → `parse_error` "Error tokenizing data. C error: Expected N fields in line L, saw M"; short rows padded with null; a field in pandas' default NA set (`""`, `"#N/A"`, `"#N/A N/A"`, `"#NA"`, `"-1.#IND"`, `"-1.#QNAN"`, `"-NaN"`, `"-nan"`, `"1.#IND"`, `"1.#QNAN"`, `"<NA>"`, `"N/A"`, `"NA"`, `"NULL"`, `"NaN"`, `"None"`, `"n/a"`, `"nan"`, `"null"`) → null. Then `_sanitize_column_names` (`Unnamed: N`/empty/`nan` → `col_{N+1}`, other names `Strip`ped) — and if two names collide after that, a row keeps the LAST value at the FIRST name's position (pandas `to_dict(orient="records")`).

- [ ] **Step 1: Go table tests for each state transition and each frame rule (literal inputs → literal outputs). Step 2: fail → implement → green. Step 3: Checkpoint (no commit).**

### Task 17: XLSX reader — calamine's cells, pandas' frame

**Files:** Create `$ENG/internal/xlsxread/{zip.go,workbook.go,sst.go,styles.go,sheet.go,convert.go,frame.go}` (+ `_test.go`).

**Interfaces:** `xlsxread.Open(path string) (*Workbook, error)` (`SheetNames() []string` in workbook order, hidden sheets included; `Date1904() bool`); `(*Workbook).Rows(sheet string, fn func(rowIdx int, cells []cell.Cell) error) error` streaming the rectangular range from A1 (pandas passes `skip_empty_area=False`); `xlsxread.Frame(wb, sheet) (columns []string, rows [][]cell.Cell, err error)` applying `_detect_header_row` + `read(sheet, header=h, dtype=str)` + `_sanitize_column_names`.

- `sheet.go`: a byte scanner over the sheet XML (no `encoding/xml` in the hot loop): `<row r>`, `<c r t s>` with `<v>`, `<is><t>`/`<r><t>`; XML entities and `&#…;`; OOXML `_xHHHH_` escapes; a formula cell's cached `<v>`; no `<v>` → empty.
- `convert.go` reproduces python-calamine's value types and pandas' `_convert_cell` + `dtype=str`: number → integral float → `int` text (arbitrary size via `math/big`), else `FloatRepr`; bool → `True`/`False`; error → as calamine returns it (decided by the `booleans_errors.xlsx` oracle); string → as is; date-formatted number → serial → date/datetime/time/timedelta per calamine (1900 leap bug, 1904 system, rounding) → pandas text (`YYYY-MM-DD HH:MM:SS[.ffffff]`, `HH:MM:SS`, `D days HH:MM:SS`); empty → `""`, which pandas' NA set turns into null — as does any string cell equal to an NA token.
- `styles.go`: `cellXfs` → `numFmtId`; built-in ids 14–22 and 45–47 are dates/times (46 elapsed), custom formats classified by calamine's `detect_custom_number_format` (ignore quoted text, `\`/`_` escapes, `[…]` except elapsed `[h]`/`[m]`/`[s]`, only the first `;` section; `d`/`m`/`y`/`h`/`s` → date, `AM/PM` handling) — every case in `dates_custom_formats.xlsx` decides.
- `frame.go`: the header detection reads the first 10 rows (`header=None`), counts non-null cells, picks the first row with ≥ max(2, widest/2); then the frame drops fully empty rows as pandas' parser does, names columns from the header row (NA → `Unnamed: i`, de-duplicated), and pads to the range width.

- [ ] **Step 1: Unit tests per rule (small XML strings into `sheet.go`, number formats into `styles.go`, serials into `convert.go`). Step 2: run the Task 15 oracle — iterate until every xlsx fixture matches. Step 3: Checkpoint (no commit).**

### Task 18: The parse command, ingest_node's Go branch, and multi-file combine

**Files:**
- Create: `$ENG/internal/ingest/{parse.go,nan.go,dupcols.go,preview.go,combine.go}` (+ `_test.go`), `$ENG/cmd/hoist-engine/cmd_parse.go`
- Modify: `$SVC/src/graph/nodes/ingest_node.py` (Go branch for steps 1–4 + NaN scan + duplicate merge; everything after stays Python), `$SVC/src/app.py` (`_parse_and_combine` → `hoist-engine combine` when the run is a Go run), `$SVC/src/engine/steps.py` (`go_parse`, `go_combine`), `$SVC/src/engine/selection.py` (`ENGINE_STEPS` + `"parse"`)
- Test: the Task 15 oracle (now green), `$SVC/tests/test_engine_ingest_node.py`

**Interfaces:**
- `parse` job: `{"source", "out_dir", "encoding", "known_destination_columns", "post_write_sheets", "preview_rows": 10, "nan_sample_rows": 20}`; result `{"detected_file_format", "source_delimiter", "source_encoding", "tables": [{"name", "rows", "columns", "kept_columns", "preview": [ {col: value} ]}], "nan_report": {…scan_nan_values shape…}, "duplicate_column_report": {…merge_duplicate_columns shape…}, "set_aside_sheets": [...]}`; writes the `full` data set (manifest `columns` = kept columns, so a merged-away column is never read).
- Sheets are parsed in parallel (bounded by `GOMAXPROCS`), tables kept in workbook order.
- `nan.go` = `scan_nan_values` (None, or whitespace-only by `pystr.Strip`; `columns` ordered by count desc, ties in first-seen order — Python's stable sort); `dupcols.go` = `merge_duplicate_columns` + `redundant_column_groups` (values `pystr.Casefold(pystr.Strip(v))`, ≥ 3 non-blank rows of evidence, two or more known destination columns → declined, one → it is kept, else the longest name by code points, ties by source order).
- `combine` job: `{"files": [{"path", "name"}], "out_path"}` → `combined.xlsx` with `_parse_and_combine`'s naming (`_safe_sheet`: invalid chars → `_`, 31 chars, `_2`… suffixes case-insensitively; a lone generic `Sheet1` takes the file stem) and `df.fillna("")` (empty cells).
- `ingest_node` Go branch: decode-free; if the bytes are not UTF-8 and not a zip, run `chardet` (as today) and pass its encoding; on `EngineError` with a code other than `data_error`/`parse_error` → log and fall back to `parse_source_tables` with `state["engine"] = "python"`; else build `parsed_tables`, `table_health`, `overall_summary`, `row_count`, `column_count`, `nan_report`, `duplicate_column_report` with the SAME Python code as today (the result feeds it the preview and counts), set `engine_refs["full"]`, never put rows on the state, log `"Parsed N rows × M columns (engine)"`, set `engine = "go"` in the node log output, and — because the step no longer pauses — add the pause payload's `table_health` and `format` to the node log output so the card's ingest snapshot is complete.

- [ ] **Step 1: Go unit tests (nan, dupcols, combine naming). Step 2: `test_engine_ingest_node.py` (fake engine: state keys equal to the Python branch's for the same preview; fallback on crash; no rows on state; no step pause written when parse is a Go step). Step 3: fail → implement → green; oracle green. Step 4: real files —** `scripts/test_offline.sh` with `-v ~/Downloads:/data:ro` added via `PT_EXTRA_ARGS`, run `python -m tests.engine_oracle.compare_parse /data/*.xlsx /data/*.csv` → no differences (any difference is a bug to fix before moving on). **Step 5: Checkpoint (no commit).**

---

# Phase 5 — Preprocess and validate

`ENGINE_STEPS` = all four at the end of this phase.

### Task 19: The preprocess oracle — today's cleaning as a pure function

**Files:**
- Modify: `$SVC/src/graph/nodes/preprocess_node.py` — extract the per-table loop (preprocess_node.py:213-326, steps 1–8 + EL-M.5 per table) and the full-table rename (345-367) into `preprocess_tables(tables, mapping_dict_by_table, skip_fields_by_table) -> PreprocessResult` (`cleaned_tables`, `renamed_full`, `row_count_post_dedup_by_table`, `dedup_drop_count_by_table`, `warnings`, `total_original_rows`, `total_cleaned_rows`); the node keeps the type guard, the plan building (Task 6 included) and everything after; no behaviour change.
- Create: `$SVC/tests/engine_oracle/preprocess_corpus.py` — tables exercising: exact duplicate rows (incl. None == None), all-null columns, numeric inference (`"5"`, `" 5 "`, `"1e3"`, `"1,000"`, `"inf"`, `"True"`, `"٣"`), text/date inference (`_contains_dates` on the first 10 values), every `DATE_FORMATS` entry, ISO with time, `"2026-07-10 00:00:00"`, day-first with time (`"10/07/2026 14:30"`), month-first fallbacks (`"07/13/2026"`), year-first with slashes, month names (`"31 Dec 2025"`, `"Dec 31, 2025"`), 2-digit years, mixed columns at the 80 % edge, `"date_code"` holding codes, rename collisions (`rename_collision`: two sources → one target), skip fields, EL-M.5 below 80 %.
- Test: `$SVC/tests/test_engine_preprocess_oracle.py` (skips without the binary).

- [ ] **Step 1: Refactor; existing preprocess tests unchanged. Step 2: write the oracle test (Python `preprocess_tables` vs `hoist-engine preprocess` on the corpus via the bridge; compare cleaned rows exactly (types: `0` int, ISO strings), renamed full rows, counts, warnings). Step 3: Checkpoint (no commit).**

### Task 20: Go preprocess — dedupe, nulls, dates, rename, EL-M.5

**Files:** Create `$ENG/internal/preprocess/{job.go,clean.go,numeric.go,dates.go,rename.go}` (+ `_test.go`), `$ENG/cmd/hoist-engine/cmd_preprocess.go`; modify `$SVC/src/engine/steps.py` (`go_preprocess`).

**Interfaces:**
- Job: `{"full_dir", "out_dir", "rename_by_table": {src: {field: target}}, "skip_by_table": {src: [field]}, "dest_schema": {...} | null, "links": {"max_pairs": 200}}`; result `{"tables": [{"name", "rows_in", "rows_out", "dedup_dropped", "null_columns_dropped", "date_columns", "renamed", "skipped"}], "warnings": [...], "el_m5_ratio": float, "python_date_columns": [{"table", "column", "renamed_to"}], "prewrite": {...}, "links": [...]}`; writes the `cleaned` data set and a renamed `full` data set (the rename applied to the parse output, collisions: last value at first position).
- `numeric.go`: `IsPandasNumeric(values []string) bool` = `pd.to_numeric(non_null)` succeeding — decided value by value with the corpus as oracle.
- `dates.go`: `DATE_FORMATS` via a port of Python's `_strptime` regexes for `%Y %m %d` (incl. `%d`'s `" 1"` form); the ISO-8601 path (`^\s*\d{4}-\d{1,2}-\d{1,2}([ T]|$)` on ≥ 80 % of values, then pandas' ISO parser); the day-first path guesses a format from the FIRST non-null value for the families numeric `D/M/Y` (separators `/ - .`, optional ` H:MM[:SS[.f]]`, optional AM/PM) and month names (`%d %b %Y`, `%d %B %Y`, `%b %d, %Y`, `%d-%b-%Y`), using dateutil's day-first resolution (first > 12 → day; second > 12 → month first; both ≤ 12 → day first; year-first with day-first → Y-D-M); any other first value → the column goes to `python_date_columns` and Python applies today's `_coerce_dates` to it after the engine returns (reading and rewriting just that column with pyarrow), so no format pandas would accept is lost. Commit rule: all values parse, or the best attempt keeps ≥ 80 % of non-null values (`""` and `0` count as non-null, exactly as `notna()` does after the null fill). Output text = `Timestamp.isoformat()`.
- Order per table is preprocess_node's: dedupe → drop all-null columns → null fill by inferred type (`0` / `""` / leave) → date coercion on date-hinted names (`date`, `time`, `created`, `due`, `completed` in the lower-cased name) → rename → skip-field drop → EL-M.5.

- [ ] **Step 1: Go unit tests per rule. Step 2: run the Task 19 oracle until 0 differences. Step 3: real files — `compare_preprocess.py` (like `compare_parse.py`, with each workbook's own tier-1 mappings from a dry Python run) → no differences. Step 4: Checkpoint (no commit).**

### Task 21: EL-4.0 pre-write validation, link stats, and preprocess_node's Go branch

**Files:**
- Create: `$ENG/internal/preprocess/{prewrite.go,links.go}` (+ `_test.go`)
- Modify: `$SVC/src/graph/nodes/preprocess_node.py` (Go branch after the plan is built), `$SVC/src/graph/nodes/hierarchy_node.py` (`schema_summary[t]["measured_links"]` from `engine_reports["preprocess"]["links"]` when present), `$SVC/src/engine/steps.py`, `$SVC/src/engine/selection.py` (`ENGINE_STEPS` + `"preprocess"`)
- Test: `$SVC/tests/test_engine_preprocess_node.py`, Go unit tests

**Interfaces:**
- `prewrite` (EL-4.0, computed in the engine from the cleaned rows, the routing and the destination schema it reads with `pgschema` when `HOIST_ENGINE_DSN` is set — read-only): per destination table and column: values that would be dropped by `coerce.ForType`, would fail `BindCheck`, are too long, or are not an enum label; required columns with no default that every row lacks; samples (first 3 each). Shown on the preprocess step and carried into the write plan.
- `links`: for each pair of tables, each column pair whose names relate (same name; one ends `_id`/`_code`/`_ref`/`_no` and contains the other table's singular name; or the other is that table's detected key) — containment `|distinct(a) ∩ distinct(b)| / |distinct(a)|` on `lower(strip(v))` values, top 200 by containment. `hierarchy_node` adds `{"measured_links": [{"column", "references", "containment"} …]}` per table to the schema summary sent to the hierarchy model; nothing else in the prompt changes.
- preprocess_node Go branch: Python builds everything it builds today up to the per-table loop (type guard, overrides, mapping dicts, skip sets), runs `go_preprocess`, applies `python_date_columns`, then sets the same state keys as the Python branch (`cleaned_tables` stays empty on state — `engine_refs["cleaned"]` and `engine_refs["full"]` point at the engine's data sets — `row_count_post_dedup_by_table`, `dedup_drop_count_by_table`, `data_quality_warnings`, `el_m5_passed`, `current_step = 5`, event, node log) and does not call `write_step_pause`; the node log output carries what the pause payload used to (`rows_cleaned`, `warnings`, `warning_messages`, `tables`, `table_previews` from the engine's first 3 cleaned rows, plus `prewrite`).

- [ ] **Step 1: Tests (fake engine for the node; Go for prewrite/links). Step 2: fail → implement → green. Step 3: full suite. Step 4: Checkpoint (no commit).**

---

# Phase 6 — Visible progress, the card, deploy, and the speed run

### Task 22: Live progress, status fields, node logs for the steps that no longer pause, UDR stages

**Files:**
- Create: `$SVC/src/engine/progress.py`
- Modify: `$SVC/src/engine/steps.py` (every `run_engine` call relays events through `EngineProgress`), `$SVC/src/udr/pipeline.py` (`run_udr_pipeline(..., on_stage=None)`: `_stamp` calls `on_stage(stage, index, total)`), `$SVC/src/graph/nodes/udr_node.py` (passes `on_stage` that schedules `EngineProgress.stage(...)` on the loop with `asyncio.run_coroutine_threadsafe`), `$SVC/src/schemas.py` (`MigrationStatusResponse.engine: Optional[str]`, `engine_progress: Optional[dict]`), `$SVC/src/app.py` (status route fills them)
- Test: `$SVC/tests/test_engine_progress.py`

**Interfaces:**
- `EngineProgress(migration_id, step, beat: ProgressBeat | None)` with `async on_event(ev)` and `async stage(name, index, total)`; writes Redis key `hoist:engine:progress:<migration_id>` = JSON `{"engine": "go", "step": "write", "stage": "insert", "table": "meter_readings", "done": 120000, "total": 277412, "rate_per_s": 91000, "at": iso}` with a 120 s TTL, at most every 0.5 s; best-effort (Redis down → nothing, never an error).
- `async read_engine_progress(redis, migration_id) -> dict | None` used by the status route; `engine` is read from the ingest node log output (`output.engine`).
- UDR stages: `preprocessing, unique_tables, prefix_columns, vector_chunking, test1, test2, hierarchy` → `{"step": "udr", "stage": name, "done": index, "total": 7}`.

- [ ] **Step 1: Tests (fake Redis): throttling, TTL, payload shape, a Redis error swallowed, status route returns the fields. Step 2: fail → implement → green. Step 3: Checkpoint (no commit).**

### Task 23: The migration card — live progress, the write plan, UDR stages

**Files:**
- Modify: `apps/frontend/src/cafm/features/ai/chat-api.ts` (types `MigrationStatus.engine?`, `engine_progress?`, `MigrationFinalGatePayload.plan?`)
- Modify: `apps/frontend/src/cafm/hoistra-wizard-steps.js` (`progressLine(engineProgress, nowMs) -> string | null`, e.g. "Writing meter_readings — 120,000 of 277,412 rows", "Reading the workbook — Assets", "Checking relationships — 3 of 7"; stale (> 30 s old) → null)
- Modify: `apps/frontend/src/cafm/hoistra-migration-wizard.tsx` (one line under the step title while running; "Run details" shows `engine` and per-step timings)
- Create: `apps/frontend/src/cafm/features/ai/pipeline/migration/gates/gate-final-plan.tsx` — a compact table: destination, rows, will merge, already on file (skipped), values that do not fit (expandable: column, count, example, destination type), rows that cannot be written (reason, count), new columns; nothing rendered when `payload.plan` is absent (Python-engine runs look exactly as today)
- Modify: `apps/frontend/src/cafm/features/ai/pipeline/migration/gates/gate-final.tsx` (renders `<GateFinalPlan plan={payload.plan} />` above the confirm buttons)
- Test: `apps/frontend/test/migrationEngineProgress.test.mjs` (pure `progressLine` cases), `apps/frontend/test/gateFinalPlan.test.mjs` (render to string: empty plan, a plan with every section)

- [ ] **Step 1: Tests. Step 2: fail → implement → green (`npm test`), `npm run build` clean. Step 3: replay-stub walk (the recipe in the hoistra-ui-verification note: a stub serving a Go-engine run's status documents — no step pauses, `engine_progress` while running, `plan` at the write gate, UDR stages after) in Chrome via playwright-core: light, dark and narrow; 0 page errors. Step 4: Checkpoint (no commit).**

### Task 24: Images and compose

**Files:**
- Modify: `apps/backend/cafm-connector-service-final/svc-ai-schema-mapper/Dockerfile` — first stage:

```dockerfile
FROM golang:1.24-bookworm AS engine
WORKDIR /src
COPY svc-ai-schema-mapper/engine/go.mod svc-ai-schema-mapper/engine/go.sum ./
RUN go mod download
COPY svc-ai-schema-mapper/engine/ ./
RUN CGO_ENABLED=0 go vet ./... && CGO_ENABLED=0 go test ./... \
 && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/hoist-engine ./cmd/hoist-engine
```
  and in the final stage `COPY --from=engine /out/hoist-engine /usr/local/bin/hoist-engine` plus `RUN mkdir -p /var/hoist-engine`.
- Modify: `deploy/allinone/Dockerfile` — the same stage (paths under `${BE}/svc-ai-schema-mapper/engine/`) and copy.
- Modify: `docker-compose.single-url.local.yml` — named volume `hoist-engine-cache` mounted at `/var/hoist-engine` in `schema-mapper-app` and `schema-mapper-worker`.
- Test: container smoke.

- [ ] **Step 1: Check the queue is empty** (`docker exec hoistra_plenum-redis-1 redis-cli zcard arq:queue` → 0; `redis-cli --scan --pattern 'arq:in-progress:*'` → nothing). If not, stop and tell Hussain.
- [ ] **Step 2: Build and recreate the two mapper containers only:** `docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml build schema-mapper-app schema-mapper-worker && docker compose -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml up -d --no-deps --force-recreate schema-mapper-app schema-mapper-worker`, then reload the gateway's nginx (`docker exec hoistra_plenum-gateway-app-1 nginx -s reload`).
- [ ] **Step 3: Smoke:** `docker exec hoistra_plenum-schema-mapper-worker-1 hoist-engine version` → the version line; both containers see the same `/var/hoist-engine`; `docker logs` shows no startup errors.
- [ ] **Step 4: Rebuild the frontend image** (`up -d --build --no-deps frontend-app`) and check the bundle hash changed. **Step 5: Checkpoint (no commit).**

### Task 25: The speed run and the whole-branch verification

- [ ] **Step 1: Speed** — inside a worker-image container on the parity network (never the stack DB): parse → (Python mapping decisions replayed from the 1 Oct run's saved mappings, or tier-1 only) → preprocess → outputs (written to a temp dir, no upload) → write apply into `parity_run`, on the 280k-row workbook; record each stage's seconds next to the baseline table in the spec. Expected: parse ≤ 5 s, preprocess ≤ 10 s, outputs ≤ 15 s (no upload), write ≤ 60 s, UDR ≤ 30 s.
- [ ] **Step 2: Every suite** — `dev.sh test`, `dev.sh test-db`, `scripts/test_offline.sh tests --continue-on-collection-errors` (≥ baseline + new, no new failures/errors), `HOIST_PARITY=1 scripts/test_offline.sh tests/engine_parity tests/test_engine_*.py`, frontend `npm test` + `npm run build`.
- [ ] **Step 3: `parity_db.sh down`.**
- [ ] **Step 4: Whole-branch review** by a fresh reviewer (superpowers:requesting-code-review), findings fixed test-first.
- [ ] **Step 5: Report to Hussain**: what is in the running containers, what was proved (parity counts, timings), what was not (a real run against Azure — his click-through), the kill switch, and `git status` (nothing committed).
