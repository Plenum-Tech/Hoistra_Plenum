# Trace Lab: database set-up and runbook

This folder holds the things a person does by hand to bring Trace Lab up, and the check that the Golden Rule (G0)
still holds. It contains files only. Nothing in it runs by itself, and no test runs any of it.

| File | What it is |
|---|---|
| `create_database.sql` | The one statement that creates Trace Lab's own database, `hoist_tracelab` (step 1). |
| `README.md` | This runbook. |
| `g0_audit.sh` | The final Golden Rule audit: read-only, run from anywhere in the repository (last section). |

**Who runs what.** No Claude session runs steps 1, 3, 4 or 7. They create a database, build and start a container,
apply migrations, or change production. Hussain (or a DBA, where it says so) runs them by hand. Steps 2, 5, 6 and 8
are edits and checks a person makes at the same desk.

Commands run from the repository root (`Hoistra_Plenum/`) unless they say otherwise. The compose project is
`hoistra_plenum`, the name the running stack already uses.

## 1. Create the database

Run by Hussain or a DBA.

Trace Lab keeps its own data in a separate Postgres database, never a schema inside the main one, so the chat's
generic SQL tools and svc-udr's custom SELECT can never read it.

```sh
PGSSLMODE=require psql "postgresql://ADMIN:PASSWORD@HOST:5432/postgres" -f apps/backend/cafm-connector-service-final/svc-trace-lab/ops/create_database.sql
```

psql wants `postgresql://`, not `postgresql+asyncpg://`, and no query string (`PGSSLMODE` replaces `?sslmode=`). Do
not pass `-1`: `CREATE DATABASE` cannot run inside a transaction. The role that runs it owns the database; the file's
comments say how to hand it over and what to do if `template1` is not UTF8. Check:

```sql
SELECT datname, pg_encoding_to_char(encoding) FROM pg_database WHERE datname = 'hoist_tracelab';
```

One row, `UTF8`. The tables do not exist yet; step 4 creates them.

## 2. Set the DSN and the keys

Put these where the stack already reads its secrets. Never write them into an image or a committed file.

* Root `.env` (read by `docker compose` for substitution): `TRACE_LAB_DB_DSN=postgresql+asyncpg://USER:PASSWORD@HOST:5432/hoist_tracelab?sslmode=require`.
  `docker-compose.tracelab.yml` passes it to the container as `TRACE_LAB_DB_URL`, the name the service reads. Unset,
  the service still starts and reports itself unavailable. `PLENUM_DB_DSN` (already there) is the main database,
  which Trace Lab only reads.
* `apps/backend/.env` (the compose `env_file`): `ANTHROPIC_API_KEY` (the grader and the judges) and `OPENAI_API_KEY`
  (the chat model the golden runs ask), with the same values svc-deepagents uses. Optional: `TRACE_LAB_JUDGE_MODEL`.
  Changing an `env_file` does not recreate a container: step 3 uses `--force-recreate` when you change one.
* Spending limits have defaults (a golden run $25, golden runs $50 a month, proposal runs $100 a month, a judge run
  $5, a monitoring batch $5, eval-case grading $2). Prerequisite P4 asks Hussain to choose them before the first
  paid run; set `TRACE_LAB_GOLDEN_RUN_MAX_USD`, `TRACE_LAB_GOLDEN_MONTHLY_USD`, `TRACE_LAB_PROPOSALS_MONTHLY_USD`,
  `TRACE_LAB_JUDGE_RUN_USD_CAP`, `TRACE_LAB_MONITOR_USD_CAP` or `TRACE_LAB_EVAL_GRADING_USD_CAP` only to change one.
* `TRACE_LAB_SCHEMA` stays `verify` (the compose file sets it): boot never runs DDL. Migrations are step 4.

## 3. Build and start the trace-lab container

Run by Hussain.

```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab up -d --build --no-deps trace-lab
```

`--no-deps` leaves every other service alone. The build copies whatever is in the working tree, so check first that
no other session has half-finished work in `svc-deepagents`, which this image copies beside Trace Lab. Add
`--force-recreate` after changing a `.env` value. Nothing starts without `--profile tracelab`.

Check the container is up and answers through the gateway (which needs step 5's gateway rebuild once, for the
`/backend/trace-lab/` route):

```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab logs --tail 30 trace-lab
curl -fsS http://localhost:3000/backend/trace-lab/health
```

The second prints `{"ok":true,"service":"trace-lab"}`. Until step 4 the logs say the schema is absent and every
route except `/health` and `/status` answers 503: that is correct.

## 4. Apply the migrations

Run by Hussain, in an interactive terminal. Prerequisite P3: read the `plan` output, and the SQL itself, before you
type anything.

```sh
docker exec -it hoistra_plenum-trace-lab-1 python -m trace_lab.migrate status
docker exec -it hoistra_plenum-trace-lab-1 python -m trace_lab.migrate plan
docker exec -it hoistra_plenum-trace-lab-1 python -m trace_lab.migrate apply --expect-db hoist_tracelab --through 0011 --applied-by YOURNAME --dry-run
docker exec -it hoistra_plenum-trace-lab-1 python -m trace_lab.migrate apply --expect-db hoist_tracelab --through 0011 --applied-by YOURNAME
```

* `status` and `plan` only read. `status` prints the state as JSON and exits 0 only when it is `ready`.
* `apply` needs an interactive terminal: it asks you to type the database name (`hoist_tracelab`) and refuses
  without a TTY. With `docker compose exec` never pass `-T`, which removes the terminal.
* `--dry-run` runs the files in a transaction and rolls back. Do it first.
* `--expect-db` must equal the database the container is connected to, or it refuses. `--through 0011` is the last
  file in `svc-trace-lab/migrations/`; `--applied-by` is recorded in the ledger as `cli:YOURNAME`.
* After a real apply it prints `SHIPPED_CHECKSUMS` lines. Send them to a developer to add to
  `trace_lab/migrate.py`: that is a code change after the run, not part of it.
* No restart is needed: `/status` verifies again when its answer is older than 30 seconds. Run `status` once more;
  it should say `ready` and exit 0.

## 5. Rebuild the frontend with the flag and reload the gateway

Trace Lab's entry exists only in a build made with `VITE_TRACE_LAB=1`; without it the module's chunk is never
fetched and the navigator has no entry.

```sh
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab build --build-arg VITE_TRACE_LAB=1 frontend-app
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab up -d --no-deps frontend-app
docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab up -d --build --no-deps gateway-app
docker exec hoistra_plenum-gateway-app-1 nginx -s reload
```

Do not add `--build` to the `frontend-app` line: it would rebuild the image from the compose file, which does not
carry `VITE_TRACE_LAB`, and the flag would be off again.

The gateway is rebuilt because its `nginx.conf` carries the `/backend/trace-lab/` route (attachment A5); the reload
afterwards stops it answering 502 for services it resolved before they were recreated. The dev server on `:5174`
reads the flag from the compose overlay: `... --profile tracelab up -d --no-deps frontend-dev`, then a hard reload.

## 6. Click-test

Do it once as a **company admin** and once as a **superadmin**. Platform-only controls (Run now on Watch, creating
a failure mode, the Judges forms) must be absent for the company admin.

- [ ] Administration lists **Trace Lab** (a flask icon). A person who is not an admin does not see it. No notice
      says Trace Lab is not available (if one does, run `status`, step 4).
- [ ] **Conversations**: the list loads; open a conversation; the timeline's panels start closed; raw trace payloads
      sit behind their own collapsed panel; save a verdict (Right, or Wrong with a note on a quoted passage); another
      reviewer's verdict stays hidden until you have judged.
- [ ] **Runs**: "All live traffic" shows; Start a golden run shows its cost estimate and safety check first, and
      Start is blocked until the check passes; open a run; tick two runs and compare them.
- [ ] **Golden set**: download the blank template and the compliance question templates; upload a small Excel or CSV
      (it is checked first, saved only when you ask, all rows or none); activate and retire a version; expected
      answers stay hidden until you reveal one.
- [ ] **Model calls**: the totals line and one row per call; a row opens a side panel; Escape closes it.
- [ ] **Map**: the sampling panel previews a batch before drawing it; a point on the map opens its conversation.
- [ ] **Progress**: the tiles and the discovery curve; a figure with no data reads a dash, never zero.
- [ ] **Labels**: the reviewed answers against the failure modes; one press toggles a cell; the history opens; AI
      suggestions are dashed and count for nothing until a person accepts one.
- [ ] **Judges**: a company admin reads the table only; the superadmin also sees the platform forms.
- [ ] **Watch**: the monitoring batches and the chart per failure mode; as the superadmin (no company viewed) Run now
      shows its plan; eval cases and release checks read for a company.
- [ ] **Platform cost** (superadmin): one extra line, "Evaluation $X, not included in the totals above". The totals
      are unchanged.
- [ ] Reload on any tab: the same tab opens again. Open Home, Buildings and one other page: they behave as before.

## 7. Azure

Run by Hussain, in the Container App portal. Azure is not redeployed by anyone else, and not from this runbook's
local steps. Do not start until prerequisite **P1** holds: hoist-f5's company-boundary fixes are on the branch that
is built, because the all-in-one image rebuilds svc-deepagents, svc-udr and svc-operations-intelligence from it.
Also P3 (read each migration before it is applied), P4 (spending limits) and P5 (the trace purge date).

1. Build the all-in-one image from the repository root with the flag on, and push it the usual way (a manual
   rebuild): `docker build -f deploy/allinone/Dockerfile --build-arg VITE_TRACE_LAB=1 -t IMAGE .`.
   Attachment A6 copies `svc-trace-lab` into it; its dependencies are all svc-deepagents' own, so it adds no venv.
2. In the Container App, set: `TRACE_LAB_ENABLED=1`, `TRACE_LAB_DB_URL` (the DSN of `hoist_tracelab`, with
   `?sslmode=require`; this name, not `TRACE_LAB_DB_DSN`) and `TRACE_LAB_SCHEMA=verify`. `DB_URL` stays the main
   database; the keys are already set for svc-deepagents. Do not set the proxy and upstream variables listed below the
   steps: the supervisord line carries them.
3. Create the database (step 1) and apply the migrations (step 4) from a terminal in the container
   (`az containerapp exec`), where `--expect-db hoist_tracelab` protects against the wrong database.
4. Click-test (step 6) at the Container App's address.

Without `TRACE_LAB_ENABLED=1` the supervisord program only sleeps, `/backend/trace-lab/` answers 502, and nothing else
changes.

**The proxy wiring (approved by Hussain, 17 Oct).** A golden run asks the real chat inside the trace-lab process, and
the safety check (`assert_isolated`) refuses to start one unless every downstream address of that process points at
Trace Lab's allow-list proxy. The image sets those four addresses to the real services, and a Container App variable
reaches every program, so only the `[program:trace-lab]` block in `deploy/allinone/supervisord.conf` can change them for
this program alone. Its `environment=` line therefore sets the four proxy addresses:

* `UDR_BASE_URL=http://127.0.0.1:8010/__proxy/udr`
* `OPERATIONS_INTELLIGENCE_BASE_URL=http://127.0.0.1:8010/__proxy/oi`
* `WO_MANAGEMENT_BASE_URL=http://127.0.0.1:8010/__proxy/wom`
* `DOC_RAG_BASE_URL=http://127.0.0.1:8010/__proxy/docrag`

and, so the proxy knows where to send the reads it allows, the four upstream addresses. They are the loopback ports of
the neighbouring programs in that same file, which are also the addresses the image already gives the other services:

* `TRACE_LAB_UPSTREAM_UDR=http://127.0.0.1:8006` (program `udr`)
* `TRACE_LAB_UPSTREAM_OI=http://127.0.0.1:8009` (program `ops-intelligence`)
* `TRACE_LAB_UPSTREAM_WOM=http://127.0.0.1:8007` (program `work-order`)
* `TRACE_LAB_UPSTREAM_DOCRAG=http://127.0.0.1:8004` (program `doc-rag`)

A program's own `environment=` line wins over the container's environment, so do not set these eight in the portal: a
value set there is ignored for this program. To change one, edit that line and rebuild the image. Nothing outside the
trace-lab block was edited; the tests pin that against the base commit
(`tests/test_trace_lab_azure_attachments.py`). A wrong value does not break the rest of Trace Lab: a golden run
refuses to start and its message names the variable.

## 8. Roll back

Both switches are off by default, so rolling back is unsetting them.

* Frontend: rebuild without `VITE_TRACE_LAB` (the build argument defaults to 0). The navigator entry, the Platform
  cost line and the module's chunk are gone; the rest of the app is unchanged.
* Backend, local: `docker compose -p hoistra_plenum -f docker-compose.single-url.local.yml -f docker-compose.azure-safe.yml -f docker-compose.tracelab.yml --profile tracelab rm -sf trace-lab`.
* Backend, Azure: unset `TRACE_LAB_ENABLED` (or set it to 0). The program sleeps; the route answers 502.
* The `hoist_tracelab` database stays. Nothing else reads it; dropping it is a decision for Hussain or the DBA.

## Optional: a SELECT-only role for the main database

Run by the DBA, on the main database (the one holding the `plenum_cafm` schema; `plenum_agent` on Azure). This
statement block lives here and not in a `.sql` file because the Golden Rule guard (`tests/test_trace_lab_golden_rule.py`)
refuses any privilege statement on a `plenum_cafm` object in a `.sql` file under this folder, comments included.

```sql
-- Optional hardening. Trace Lab already opens its main connection read-only (default_transaction_read_only);
-- this makes the database itself refuse anything else.
CREATE ROLE tracelab_ro LOGIN PASSWORD 'CHANGE-ME-generated-by-the-DBA';
GRANT CONNECT ON DATABASE main_database_name TO tracelab_ro;
GRANT USAGE ON SCHEMA plenum_cafm TO tracelab_ro;
GRANT SELECT ON plenum_cafm.agent_trace_turns, plenum_cafm.agent_trace_spans,
                plenum_cafm.chat_threads, plenum_cafm.agent_activity_log TO tracelab_ro;
ALTER ROLE tracelab_ro SET default_transaction_read_only = on;
```

**What it does not cover: golden runs.** A golden run asks the real chat, and the chat's data tools read many more
tables (work orders, assets, buildings, documents) over this same main connection. With a role limited to the four
tables above, every such read fails with "permission denied", and each answer comes back degraded.

**What it does not cover: the sampling's read of the accounts table.** Sampling (the Map tab, the review batches, the
Progress figures) and the Monitor also read `plenum_cafm.users` over the same main connection: the superadmins' ids
and organisations, and each asker's platform role. The role above has no grant on that table, so the read gets
"permission denied". Trace Lab treats the accounts table as optional evidence, so a refused read counts as no rows and
no error reaches the page. It fails silently: the rule that leaves out a superadmin's answers given while viewing as
another company (R20, "view-as") stops applying, so sampled batches and the Map can include answers they should
exclude, and the asker-role dimension is empty. Nothing on screen says so; the only trace is a
`trace_lab.sampling.accounts_unavailable` warning in the service log. This role is therefore **not** a safe default for
any tab that samples. Two ways out. Do not use the role (Trace Lab already opens the main connection read-only). Or
widen it to a column-level grant on that one table, `GRANT SELECT (id, organization_id, platform_role) ON
plenum_cafm.users` (it keeps the password hashes unreadable); that adds a fifth table to a role this runbook defines
as exactly four, so it needs Hussain's approval and is not in the block above.

Without the accounts grant the role only suits reading stored conversations, labelling and judging answers that are
already stored. To use it at all, point the trace-lab container's `DB_URL` at this role: that needs an edit to
`docker-compose.tracelab.yml` locally (not made here), and cannot be done on Azure's all-in-one container, where
`DB_URL` is shared by every program.

## The final Golden Rule audit

```sh
sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit.sh
```

Read-only. It lists every modified or deleted tracked file and exits non-zero (`RESULT: FAIL`) if one is not an
approved attachment (A1 to A8) or if anything under `svc-deepagents`, `svc-udr` or `svc-operations-intelligence` has
changed. Another session's files are listed apart as "not Trace Lab's" and never counted: the instance picker's
(`logic/instances.js` and four more), the token-saving session's trimmed `apps/backend/cafm-connector-service-final/CLAUDE.md`,
and the untracked files of `apps/backend/cafm-connector-service-final/docs/` (a tracked file edited there still counts).
With only those extra files the audit prints `RESULT: PASS`. For each approved file that changed it prints
`git diff --numstat`, beside what the addendum says to expect.

To prove both outcomes without touching this repository, run the self-test. It builds throwaway git repositories,
runs the audit in each (only the extra files: PASS; any other unapproved file, or any change under the three services:
FAIL; outside a repository: exit 2) and prints `SELF-TEST: PASS` when every outcome is as expected:

```sh
sh apps/backend/cafm-connector-service-final/svc-trace-lab/ops/g0_audit.sh --self-test
```

(`--self-test` runs `ops/g0_audit_selftest.sh`; both do the same.)

The tests for these files run with no database and no network:

```sh
cd apps/backend/cafm-connector-service-final/svc-trace-lab
python3 -m pytest tests/test_trace_lab_azure_attachments.py tests/test_trace_lab_ops_files.py tests/test_trace_lab_g0_audit_script.py tests/test_trace_lab_g0_audit_selftest.py -q -p no:cacheprovider
```

They run on the host (the testenv copy has no `deploy/` folder and no `git`, and skips what needs them).
