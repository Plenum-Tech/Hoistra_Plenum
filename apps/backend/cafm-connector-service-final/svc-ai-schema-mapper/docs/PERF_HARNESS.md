# Migration performance harness (CEO #1 / #12 — 1 GB in ~30 s, migration < 60 s)

Measure **where the seconds and megabytes go** before changing anything. Three pieces:

| Piece | File | What it gives you |
|-------|------|-------------------|
| Phase profiler | `src/udr/profiling.py` | `span()` / `record()` / `emit_report()` — zero overhead unless `MIGRATION_PROFILE` is set |
| In-path instrumentation | `src/graph/nodes/ingest_node.py` | gated spans around the CSV/Excel parse + `full_tables` build; logs a `[perf]` phase table per run |
| Standalone benchmark | `tests/bench_migration.py` | isolates the ingest hot path with **no DB / AI / Azure**; runs anywhere with pandas |

## How to run

**Isolated benchmark (no infra — get numbers now):**
```
python tests/bench_migration.py 200000 12     # rows, cols
```
Prints per-phase wall time, rows/s + MB/s, peak Python heap (tracemalloc), and a 1 GB extrapolation.

**In-environment per-phase log (real migration, real DB/AI):**
```
MIGRATION_PROFILE=1   # set on svc-ai-schema-mapper + its worker, then run a migration
```
Node 1 emits `[perf] run=<id> ...` with `ingest.csv.read_full`, `ingest.csv.read_preview`,
`ingest.csv.sanitize_full` (and the Excel equivalents). Per-**node** totals are already in
`MigrationJob.node_logs[*].duration_ms` — the profiler adds the sub-node breakdown. Add `span()`
calls to other nodes (semantic map, preprocess, hierarchy, write) the same way to widen coverage.

## First findings (isolated bench, synthetic CSV, 50k–100k × 12)

```
read_csv (full)           ~17%
read_csv (preview x10)    ~1%    <- the redundant CSV second parse: NOT the problem
to_dict + _sanitize_records  ~83%   <- THE bottleneck (pure-Python per-cell loop)
throughput               ~4 MB/s  ->  1 GB ≈ 260 s   (target 30 s → ~9x over)
peak heap                ~14x the file size  ->  1 GB file ≈ ~14 GB RAM as list-of-dicts
```

So the intuition-first guesses are **wrong**: the redundant parse and the read are cheap. The cost
is (a) the per-cell Python sanitize and (b) materialising the whole file as a list-of-dicts that is
then checkpointed to Postgres.

## Roadmap the data points to (in priority order — do NOT start until measured in-env too)

1. **Kill the per-cell sanitize loop (~83%).** `_sanitize_records` runs `_clean` on every cell in
   Python. Vectorize: `df = df.astype(object).where(pd.notna(df), None)` once per frame (NaN→None in
   C), handle timestamps column-wise, then `to_dict` — no per-cell Python. Expected: the 83% phase
   collapses toward the parse cost.
2. **Stop holding + checkpointing the whole file (~14x RAM).** `state["full_tables"]` is the list of
   every row, msgpack-checkpointed to Postgres each step — the real 1 GB/10 GB ceiling. Stream the
   full data to the DB in batches (asyncpg COPY) at the write phase and keep only the 10-row preview
   in graph state. This is the re-architecture, and the biggest win for both time and memory.
3. **Remove the redundant CSV second parse (~1%, but free).** Reuse `df_full.head(10)` like the Excel
   path already does, instead of re-`read_csv(nrows=10)`.
4. **Measure the AI/embedding nodes in-env** (`MIGRATION_PROFILE=1`) and batch embedding/mapping
   requests — likely the dominant cost on the *migration* clock (#12) once ingest is fixed.

Re-run the benchmark after each change to confirm the phase actually shrank (regression guard).
