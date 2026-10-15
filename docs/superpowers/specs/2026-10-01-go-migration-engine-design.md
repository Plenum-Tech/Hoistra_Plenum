# Go migration engine — Python decides, Go moves the rows

**Date:** 2026-10-01
**Branch:** `go` (from `origin/Hoistra_Frontend` @ `3f869fa`)
**Scope:** `svc-ai-schema-mapper` migration graph, for `.csv` `.tsv` `.xlsx` `.xlsm` uploads (single
and multi-file), plus the migration card in `apps/frontend/src/cafm`. Not the Fiix graph, not the
schema-mapping graph, not document ingestion. `.xls` stays on the Python path (there is no Go reader
for the old binary format).

## Goal

Hussain's diagram, implemented as drawn:

| Diagram box | Side | Where it lives after this change |
|---|---|---|
| Upload workbook | Python | `start-with-upload` / `start-with-upload-multi` (unchanged contract) |
| Parse and profile — streaming, all cores | **Go** | `hoist-engine parse` (and `combine` for multi-file uploads) |
| Map columns — rules plus LLM, on profile | Python | `ingest_node` table match + `deterministic_mapper_node` (unchanged logic) |
| Review gates — keys, mapping, people | Python | PK, unique-table, routing, classification, column-mapping, column-matching, semantic, field-mapping gates (unchanged) |
| Preprocess and validate — coerce, dedupe, FK, EL-4.0 | **Go** | `hoist-engine preprocess` |
| Hierarchy decision — LLM on Go's link stats | Python | `hierarchy_node` + `verify_hierarchy_node`; the LLM prompt gains Go's measured link stats |
| Generate outputs — streamed CSV, JSON, SQL | **Go** | `hoist-engine outputs` (CSV, JSON, SQL, XLSX); PDF + structure.md stay Python |
| Write gate — person confirms the write | Python | `write_node` gate, now showing the engine's dry-run plan (EL-4.0) |
| Bulk write — COPY, staging, merge | **Go** | `hoist-engine write` (plan, then apply) |
| UDR checks and activity — report, extras, done | Python | `udr_node` (unchanged logic, memoised statistics, live stage progress) |

**Baseline** (run 45a3d654, 1 Oct 2026, 280,796 rows / 17 tables / 181 columns): 31 min wall, ~27 min
machine. UDR 797 s, write 507 s, gzip-JSON offload between nodes ~82 s, output 59 s, ingest 13 s.

**Target** on the same file: machine time ≈ LLM calls + artefact upload, about 1–2 min. Same rows in the
database as the Python writer would have written (proved by the parity harness below), same gate
screens, same answers accepted.

**Measured on this branch** (Task 25, 1 Oct 2026). Same workbook through the real nodes on the engine,
tier-1 mapping only (no model calls offline), written into the isolated parity Postgres; no Blob was
configured, so publishes and artefact uploads are not in these numbers.

| Stage | Baseline (Python, Azure) | Engine, local DB (0.1 ms round trip) | Engine, 16 ms added (19 ms round trip) | Plan's bound |
|---|---|---|---|---|
| Ingest (parse) | 13 s | 1.9 s | 2.8 s | ≤ 5 s |
| Map, tier 1 | ~5 s with the gates | 1.1 s | 2.7 s | — |
| Preprocess | inside "everything else" (< 30 s) | 2.1 s | 2.6 s | ≤ 10 s |
| Outputs (no upload) | 59 s (3 s build, the rest upload) | 4.3 s | 4.5 s | ≤ 15 s |
| Write (plan + apply) | 507 s | 35.8 s | 77.6 s | ≤ 60 s |
| UDR | 797 s | 2.9 s | 3.0 s | ≤ 30 s |

Both writers on the same cleaned rows, each into a fresh database (times include the reset and a full
dump): 47.8 s Python vs 36.7 s engine locally; 532.5 s vs 81.3 s with the 16 ms round trip — and every
row of every table identical both times (15 tables written, 280,793 rows inserted, 2 skipped, 277,016
readings placed on meters). The engine's apply is pipelined prepared INSERTs in 500-row batches (591 round trips here), not
COPY. The UDR gain is the memo, not the engine: the same rows took 545.4 s without it (prefix_columns
108.8 s, relationship graph 332.3 s, test2 102.1 s) and 3.3 s with it, on either engine.

After the whole-branch review's fix pass (2 Oct; per-table DDL commits, the fail-closed reader, versioned
data sets): 1.9 / 1.1 / 1.7 / 3.8 / 31.2 / 2.8 s locally for the same stages, the same rows written. One
run of this workbook keeps three Arrow data sets of 8.8 MB each (parse, renamed full, cleaned) and 14.1 MB
of output files, all published to Blob; the local copies go when the run completes.

## Decisions taken with Hussain

| Question | Decision |
|---|---|
| Step pauses after the Go steps (ingest, preprocess, output) | **Auto-continue.** A Go-engine run stops only where a person decides. Legacy (Python-engine) runs keep today's pauses. Finished steps stay reviewable from the rail. |
| A database for writer parity tests | **A throwaway `postgres:16` container**, isolated, never the stack database or Azure, removed after each run. |
| Which flow | Migration only, Excel and CSV only. |

## Non-goals

- No change to any gate payload or resume shape, any LLM prompt (except the hierarchy prompt gaining link
  stats), the Fiix ingestion graph, or the schema-mapping graph.
- No new service, port or queue. The engine is a binary inside the schema-mapper image.
- No deploy to Azure. Images are rebuilt locally only; production is Hussain's call.
- No commit or push without Hussain naming it.

## Architecture

```
            Python (LangGraph, unchanged node order)                Go (hoist-engine, same image)
upload ──► ingest_node ─────────────── job.json ──────────────► parse  → tables/*.arrow + profile.json
           │ table match + dataset LLM on preview/profile ◄──── events ──┘
           ▼
           PK · unique · deterministic · routing · classification · column mapping · matching
           · semantic · field mapping      (read full_tables hydrated from the Arrow files)
           ▼
           preprocess_node  plan (renames, skips, type guard) ─► preprocess → cleaned/*.arrow,
           │                                                     prewrite.json (EL-4.0), links.json
           ▼
           hierarchy_node (LLM + link stats) · verify_hierarchy_node (gate)
           ▼
           output_generator_node  routing + hierarchy ─────────► outputs → output.json.gz, output.sql.gz,
           │ PDF, structure.md, upload                            table_*.csv.gz, output.xlsx
           ▼
           write_node ─ plan ─────────────────────────────────► write --plan  (read-only txn, rollback)
           │ GATE: person sees the plan, confirms
           └─ apply ──────────────────────────────────────────► write --apply (one txn: COPY → merge)
           ▼
           udr_node (memoised statistics, stage progress) → persist graph → Activity Log
```

### The engine binary

- Go module at `svc-ai-schema-mapper/engine/` (module `hoistra/engine`), one static binary
  `hoist-engine` (`CGO_ENABLED=0`), built in a `golang:1.23` stage of both Dockerfiles
  (`svc-ai-schema-mapper/Dockerfile`, `deploy/allinone/Dockerfile`) and copied to
  `/usr/local/bin/hoist-engine`. Cross-compiled for amd64 (production) and arm64 (Mac).
- Invoked per step by Python: `hoist-engine <command> --job <job.json>`. The job file carries every input
  (paths, plan, options). Secrets never go on argv: the database DSN and the Blob connection string are
  passed in the environment (`HOIST_ENGINE_DSN`).
- stdout is newline-delimited JSON events: `{"type":"progress","stage":…,"table":…,"done":n,"total":m}`,
  `{"type":"log","level":…,"msg":…}`, and exactly one terminal `{"type":"result",…}` or
  `{"type":"error","code":…,"message":…}`. stderr is free-form diagnostics. Exit code 0 only with a result.
- Libraries: `github.com/xuri/excelize/v2` (streaming XLSX read and write), `github.com/apache/arrow-go/v18`
  (Arrow IPC with zstd), `github.com/jackc/pgx/v5` (COPY, transactions), stdlib for CSV, gzip and JSON.
- Uses all cores: sheets and tables in parallel, per-column profiling in parallel, per-table output streams
  in parallel. Memory stays bounded by streaming rows into Arrow record batches (64k rows).

### Data between steps

Rows never go into the LangGraph checkpoint and are never gzip-JSON offloaded in a Go-engine run.

- Each Go step writes Arrow IPC files (one per table, all-string columns, nullable, zstd), plus small JSON
  reports, into the run's work dir `${HOIST_ENGINE_DIR:-/var/hoist-engine}/<migration_id>/<step>/`.
- Locally the app and worker are two containers, so the compose files mount one named volume
  (`hoist-engine-cache`) at that path in both; in production's all-in-one container they already share a
  filesystem.
- After each Go step the Python wrapper uploads the step's files to Blob under
  `migrations/<id>/engine/<step>/…` in parallel, and stores the refs on state (`engine_refs`). A process that
  lacks the local copy (restart, other replica) downloads it. A run that loses both fails visibly with
  "Retry from step", never silently.
- Python nodes that still read rows (the gates, hierarchy, verify, UDR) get `full_tables` / `cleaned_tables`
  hydrated from Arrow via `pyarrow` (`Table.to_pylist()`), cached per process, so their logic and results
  are exactly as today. After preprocess, `full_tables` is the renamed-full data set preprocess writes
  next to the cleaned one.

### Choosing the engine for a run

`state["engine"]` is set once, at run start, and never changes mid-run except by the ingest fallback:

- `go` when `MIGRATION_ENGINE` (default `go`) is `go`, the binary is present, and the source is
  csv/tsv/xlsx/xlsm (by extension, confirmed by magic bytes).
- otherwise `python`: today's code path, untouched.
- If `hoist-engine parse` is missing or crashes (not a data error), `ingest_node` logs it and falls back to
  the Python parse for that run; the run is then `python` for every later step.

The compiled graph keeps today's `interrupt_after`. `get_migration_graph()` returns a thin proxy whose
`ainvoke` passes `interrupt_after` without `ingest_node`, `preprocess_node`, `output_generator_node` when
the thread's engine is `go` (read from the input, or from the checkpoint via `aget_state`). Every
invocation site already goes through that factory, so a site that is missed keeps today's pauses — the safe
failure.

## The steps

### Parse and profile (`hoist-engine parse`)

Replaces `ingest_node` steps 1–4 and the NaN scan and duplicate-column merge. Python keeps the table match
LLM, the dataset description LLM, the stored-mapping lookup, EL-M.1, the node log and the partial B7.1
cards — all of which read only the preview and the profile.

Output: `tables/<name>.arrow`, `profile.json` with, per table: column names, row count, the first 10 rows
(`parsed_tables`), `table_health` (null % on the sample, exactly as today), the `nan_report`, the
`duplicate_column_report`, and per-column stats (non-null, distinct, uniqueness, inferred type, head values).

The parse must produce **byte-identical cell strings** to today's `pandas` + `python-calamine` reads with
`dtype=str` followed by `_sanitize_records`. The rules, each held by a parity test against the real Python
reader:

- CSV/TSV: delimiter by `_detect_delimiter` (first 5 lines, `,` → `\t` → `;` → `|`); UTF-8 (BOM stripped),
  else Windows-1252; quoted fields; blank lines skipped; pandas' default NA strings become null;
  duplicate headers mangled `a`, `a.1`; missing headers `Unnamed: N` → `col_{N+1}`; a row with more fields
  than the header is a parse error, as pandas raises.
- XLSX/XLSM: banner-row detection by `_detect_header_row`; pandas' calamine cell conversion (integral
  floats → int, other floats → Python `repr`, dates → `YYYY-MM-DD HH:MM:SS`, booleans → `True`/`False`,
  times → `HH:MM:SS`); fully empty rows dropped; post-write sheets (`POST_WRITE_SHEETS`) set aside.
- Multi-file uploads: `hoist-engine combine` writes `combined.xlsx` with the same sheet-naming rules as
  `_parse_and_combine` (Excel's 31-char limit, `_2` suffixes, a lone `Sheet1` named after the file). The
  durable source stays one workbook, so re-runs and the post-write workbook-extras engine are unchanged.

### Preprocess and validate (`hoist-engine preprocess`)

Python builds the plan exactly as `preprocess_node` does today — the type guard against destination column
types, the per-table rename maps from tier-1 / tier-2 / human mappings, skip fields, custom new-column renames
— and hands it to Go. Go applies, per table, the same order of operations as today: exact-duplicate rows
dropped, all-null columns dropped, nulls filled by inferred type (numeric → `0`, text → `""`, dates left
null), date coercion for date-named columns (the five `DATE_FORMATS`, then ISO-8601, then a day-first guess;
committed only when every value or ≥ 80 % of them parse), rename, skip-field drop, EL-M.5.

Then two things today's preprocess does not do:

- **Link stats** (`links.json`): for every column pair across tables whose names or formats make it a
  plausible reference, the measured containment of one column's distinct values in the other's. Hierarchy
  detection validates FK candidates on the same numbers and the hierarchy LLM is given them.
- **EL-4.0 pre-write validation** (`prewrite.json`): every cleaned value checked against its destination
  column type (the writer's coercion rules), length limit and enum labels; null required columns with no
  default; per table and column, how many values would be dropped and a sample of each.

### Hierarchy decision

Unchanged logic over hydrated `cleaned_tables`. The schema summary sent to the hierarchy LLM gains a
`measured_links` list (top pairs from `links.json`: source, target, containment). Nothing else in the prompt
changes.

### Generate outputs (`hoist-engine outputs`)

Same artefacts, same names, same content as today, streamed and gzip-encoded as the node already sends them:
`output.json` (nested hierarchy + every table), `output.sql`, `table_<dest>.csv` (one per destination, union of
co-routed sources), `output.xlsx`. Python still builds `migration_report.pdf` and `structure.md` (both read
counts and column names only) and uploads every file with the existing `upload_artefacts`.

`output.sql` and `intermediate_schema` are no longer kept on state in a Go-engine run: the write never read
the SQL (the aligned writer always won) and the intermediate schema was only read for entity counts, which
now come from the engine.

### Write gate and bulk write (`hoist-engine write`)

**Plan (before the gate).** In a read-only transaction, Go runs the write's own preparation without
changing anything: normalises and coerces every row against the live destination schema, and reads what
is already on file. The gate payload keeps today's `summary.entity_counts` and gains `plan`: per table,
rows, rows that will merge into an existing asset, rows already on file (skipped), values that will not
fit their column (dropped, row kept), rows that cannot be written and why, new columns, widened columns,
and how many rows carry a reference still to be resolved. Building, meter and reference *outcomes* need
the rows written earlier in the same run, so they are reported after the write, as today. The plan is
cached in the work dir, so the node's re-run on resume does not repeat it.

**Apply (after the person confirms).** One transaction, the same semantics as
`_apply_records_with_schema_alignment`:

1. Resolve the organisation (`_resolve_valid_organization_id`) and the uploader's building.
2. Run all DDL first, in its own transaction committed before any row is loaded — the extra-field DDL
   all-or-nothing as today, then new tables, approved new columns and the numeric → TEXT widenings (each
   widening in a savepoint with `lock_timeout = '5s'`). No exclusive lock is held across the load (the
   23 Sep 2026 eight-minute stall), and unlike today the rows themselves are one transaction.
3. For each source table, parents first (the confirmed hierarchy plus `_CORE_PARENTS`):
   build every row with the ported rules — `_normalize_row_for_table`, safe identifiers, building / section /
   floor / meter / reference resolution (the resolvers ported 1:1, same SQL, same ambiguity and caching rules,
   inside this transaction so rows written earlier in the run are visible), `_coerce_value_for_db_type`
   (a mismatch drops the field and is counted), system defaults for required columns, generated `id`s.
4. COPY the rows into a temporary staging table, then one set-based statement per table:
   assets that match an existing code are merged with `UPDATE … FROM staging` (building only fills a gap);
   the rest are inserted with `INSERT … SELECT … WHERE NOT EXISTS (natural key already on file)` and the same
   `ON CONFLICT` clause `_build_dml_for_row` would choose. Orphan foreign keys are nulled where the column is
   nullable before the insert, so the statement cannot fail on them.
5. If a statement still fails on a row (a constraint nobody predicted), the batch is bisected under
   savepoints until the bad rows are isolated: one bad row still costs only its own row, and 100 identical
   failures in a row still abandon the table, as today.
6. Commit once. A lost connection rolls back everything and says so ("nothing partial was kept").

The engine returns the same result keys the Python writer returns (`rows_inserted`, `tables_written`,
`rows_skipped`, `rows_merged`, `buildings_linked`, `meters_linked`, `meters_created`, `meters_matched`,
`meters_unlinked`, `references`, `row_errors`) plus per-table timings, so the job row, the node log and the
UDR node read it unchanged. Progress events drive `ProgressBeat(90 → 99)`.

#### As built: the bulk load (2 Oct 2026)

Steps 4–5 shipped as the chunked path (pipelined 500-row batches, Python's row-by-row recovery) plus a
bulk load on top of it (`engine/internal/write/bulk.go`). A run of consecutive chunks that are each one
clean statement of one shape goes to the server in one piece: COPY (text format) into a temporary table
made with `CREATE TEMP TABLE … AS SELECT <destination columns> … WITH NO DATA` (so every value is parsed by
the input function of the destination's own type, typmods included, exactly as a bound parameter is),
values a column repeats sent once in dictionary tables with rows carrying codes, then
`INSERT … SELECT … ORDER BY hoist_seq ON CONFLICT DO NOTHING`. Any error rolls back to a savepoint, the
run is halved, and a lone failing chunk goes through the chunked path — so rows, counts, row errors and
log lines are the chunked path's (twin tests, the parity scenarios with the bulk load forced, the
Bishopsgate real-data parity in both modes). Not bulk-loaded: assets, a table with a trigger or rule of
its own or a foreign key to itself, a shape whose omitted columns' defaults draw on a sequence. Staging
tables are made once per destination shape and emptied between statements, within a lock-table budget
(a subtransaction's locks stay held until commit). With random ids a row's generated id is drawn by the
server. On a 2.8 Mbit/s uplink (Bishopsgate, 280,796 rows): 283 s → 85 s, 75 MB → 11.6 MB sent.

Between steps, data sets and the uploaded source no longer wait for Blob: the app and worker read the
shared `ENGINE_DIR`; Blob copies go up in the background (manifest last) and a run's files are released only
after its uploads finish.

### UDR checks and activity

`run_udr_pipeline` and everything it calls are unchanged in logic. The per-column computations that every
pairwise test repeats — `column_values`, the non-null list, `distinct_values` (and its lower-cased form),
`format_distribution`, `value_shape_distribution`, `column_format`, `dominant_value_shape` — are memoised per
(table rows, column) for the duration of one pipeline run. They are pure functions of the rows, so the report
is identical; the `combinations(…, 2)` loops become set operations on cached values. This applies to both
engines. The node also reports its stages (`progress` with stage name), so the card shows "Checking
relationships · 3 of 6" instead of "writing" for the length of the UDR run.

## Intentional behaviour changes

| Change | Why |
|---|---|
| Go-engine runs never pause after ingest, preprocess or output. | Decided with Hussain. |
| Every table is written with the aligned rules. Today a run with none of assets / work orders / meters / sections / certificates applies the literal SQL file instead, which has no duplicate check and no coercion. | One write path; re-running the same file no longer duplicates vendors or resources. |
| DDL and the `migration_field_mappings` rows are written once, after the person confirms. Today both run before the gate and again when the node re-runs on resume. | A rejected write no longer leaves columns behind; mappings are no longer recorded twice. |
| `rows_inserted` counts the rows that went in. Today a batch that succeeds counts all its rows, including rows `ON CONFLICT DO NOTHING` skipped (asyncpg's executemany reports no row count). | The number on the job is what is in the database. |
| A table abandoned after 100 identical failures says so in `row_errors`. Today the writer builds that sentence and never reports it. | The reason a table stopped is the first thing the person reads. |
| A reading with no meter reference is skipped and reported. Today, when no earlier reading had a reference, the writer raises `UnboundLocalError` and the whole write fails. | One bad row no longer fails the run. |
| **Column-mapping gate re-targets reach the data.** Today `column_dest_overrides` are applied in `output_generator_node` to `tier1_mappings` / `tier2_auto_accepted` / `human_approved_mappings`, keys `MigrationState` does not declare, so LangGraph drops them at the first checkpoint and every "map to column X" answer is silently ignored (only "new column" survives, via `_collect_approved_new_columns`). The fix applies the overrides in `preprocess_node`'s rename map, for both engines. | The person's answer is the decision. Flagged for Hussain's approval before it ships. |

## Errors, fallback and the kill switch

- `MIGRATION_ENGINE=python` in `apps/backend/.env` (then `--force-recreate` the two containers) puts every new
  run back on today's code. Runs already started keep the engine they started with.
- A Go step that fails returns `status="failed"` with the engine's message; `raise_if_node_failed` already
  ends the run visibly with "Retry from step". There is no silent switch to Python mid-run.
- Cancellation (`DELETE /api/migration/{id}`) is honoured between steps as today, and the running engine
  process is killed when its node is cancelled.
- The engine never retries a database write on its own; the run's existing retry is "Retry from step".

## Testing

1. **Go unit tests** for every reader rule, transform, coercion, resolver and SQL builder (`go test ./...`
   in the build stage, so a red test fails the image build). The tests that need Postgres — the writer,
   the resolvers, the schema reads, EL-4.0 — skip in the build (it has no database): they run against the
   throwaway parity database (`engine/scripts/dev.sh test-db`, `HOIST_PARITY=1 scripts/test_offline.sh
   tests/engine_parity`), and that run is the gate before an engine change ships.
2. **Function parity, Python as oracle:** a generated corpus of rows and values is run through the Python
   functions (`_normalize_row_for_table`, `_coerce_value_for_db_type`, the hint functions, `_natural_keys_for`,
   `_system_default_for_db_type`, `_infer_column_type` + date coercion, `export_to_sql`, the CSV and JSON
   writers) and through the engine; outputs must match exactly. Lives in the service's pytest suite, so a
   later change to a Python rule fails this test until the engine follows.
3. **File parity:** the real workbooks on hand (Northbridge B-101/B-102 16-sheet, Harbour Point upload set)
   plus generated edge-case workbooks and CSVs: Go parse vs today's parse, every cell compared.
4. **Database parity:** the throwaway Postgres container, schema from `db/01_schema.sql` and the
   operations-intelligence migrations. The same cleaned tables are written once by the Python writer and once
   by the engine into identical fresh schemas; every target table is compared row for row (ids aside), with
   resolution, merge, re-run duplicate and orphan cases seeded on purpose.
5. **End to end:** the replay stub drives the card through a Go-engine run (no step pauses, plan at the write
   gate, UDR stages), plus the frontend unit tests.
6. **Speed:** the 280k-row workbook through parse, preprocess, outputs and write against the throwaway
   database, timed per stage, compared with the baseline above.

The existing suites (schema-mapper pytest, frontend `npm test`) stay green.

## Delivery order

Ordered by where the time goes (write and UDR are 1,304 of the 1,620 machine seconds), each phase
shippable on its own: until a Go step exists, the Python step before it hands its rows over through the
same Arrow files (the "bridge").

1. Engine skeleton, protocol, Python client, storage and bridge, engine choice, graph proxy. UDR
   memoisation (both engines, immediate win). The column-mapping re-target fix.
2. Write plan + apply, with database parity. Gate payload `plan`.
3. Outputs, with artefact parity.
4. Parse + combine, with file parity.
5. Preprocess + EL-4.0 + link stats, with function parity.
6. Live progress, the write plan and UDR stages on the card. Dockerfiles and compose volume. Speed run.

Each phase ends with its tests green; the stack is rebuilt only with `arq:queue` empty and no
`arq:in-progress:*` keys (see the migration-output memory note on arq re-running jobs).

## Risks

| Risk | Mitigation |
|---|---|
| Two implementations of the write rules drift. | The oracle parity tests fail on any Python rule change until the engine follows; `write_node.py` header says so. |
| A cell renders differently from pandas (floats, dates, encodings). | File parity over real and generated files; non-UTF-8 detection is the known gap (chardet vs Windows-1252), logged when used. |
| Date inference differs from `pd.to_datetime(dayfirst=True)` on unusual strings. | Committed only under the same all / 80 % rule; parity corpus from every date column in the real workbooks. |
| Set-based insert fails where row-by-row did not. | Pre-validation (EL-4.0, orphan nulling) plus bisect fallback; database parity seeds those cases. |
| Arrow artefacts on a slow laptop uplink. | Compressed columnar files are an order of magnitude smaller than today's gzip-JSON; uploads run in parallel; production uploads are Azure-internal. |
