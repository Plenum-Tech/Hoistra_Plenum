# Hoistra database — a fresh, empty schema

Three files that turn an empty PostgreSQL into a database Hoistra runs on, with no
portfolio data in it. Apply them in order.

| File | What it is | Optional? |
|---|---|---|
| `01_schema.sql` | 145 tables, 2 views, 349 indexes, 188 foreign keys. **No rows at all.** | No |
| `02_reference_data.sql` | Regulation packs and the verification registers — 122 rows of reference data the compliance and benchmark features read | Recommended |
| `03_bootstrap.sql` | One organisation row, so the first account can be created | Yes, but see below |

## Load it

Use the script for your shell. Both do the same three things and are the tested path.

```cmd
REM Windows — cmd.exe or PowerShell
db\setup.cmd
```

```bash
# macOS, Linux, WSL, Git Bash
./db/setup.sh
```

Then point the service at it:

```
DB_URL=postgresql+asyncpg://cafm:cafm@127.0.0.1:5432/hoistra
AUTH_JWT_SECRET=<32+ random characters>
AUTH_OTP_PEPPER=<32+ different random characters>
AUTH_DEFAULT_ORGANIZATION_ID=00000000-0000-0000-0000-000000000001
```

### Or by hand, in any shell

Three commands, no shell-specific syntax — the waiting and the looping happen *inside*
the container, where bash exists:

```
docker run -d --name hoistra-db -e POSTGRES_USER=cafm -e POSTGRES_PASSWORD=cafm -e POSTGRES_DB=hoistra -p 5432:5432 pgvector/pgvector:pg16

docker cp db hoistra-db:/db

docker exec hoistra-db bash -c "until pg_isready -U cafm -d hoistra >/dev/null 2>&1; do sleep 1; done; for f in /db/0*.sql; do psql -q -U cafm -d hoistra -v ON_ERROR_STOP=1 -o /dev/null -f $f || exit 1; echo loaded $f; done"
```

Check it landed:

```
docker exec hoistra-db psql -U cafm -d hoistra -c "\dt plenum_cafm.*" | tail -3
```

### Two things that are not optional, and why

**Wait for Postgres.** `docker run -d` returns as soon as the container is *created*, not
when Postgres accepts connections — `initdb` takes about eight seconds on first boot. The
obvious version of this,

```bash
docker run -d ... pgvector/pgvector:pg16
for f in db/0*.sql; do docker exec -i hoistra-db psql -U cafm -d hoistra < "$f"; done
```

fails on all three files with `connection to server ... failed` and leaves you a database
with **zero tables**. That is not a hypothetical; it is what happens.

**`-v ON_ERROR_STOP=1`.** psql exits **0** even when statements fail. Without this flag,
the run above — which loaded nothing at all — still reports success, and a half-loaded
database is indistinguishable from a good one until something reads the table that did
not get made.

`pgvector/pgvector:pg16` rather than plain `postgres:16` because the schema uses the
`vector` extension for document embeddings. Plain Postgres will fail on that one
statement and leave you a database that is complete apart from `document_chunks`.

## Why `03_bootstrap.sql` is not really optional

`01_schema.sql` alone gives you a correct database and a useless one. The first
registration refuses:

> This deployment has no organisation to attach an account to.

Every account belongs to an organisation, and there is not one yet. Registration refuses
rather than inventing a tenant — putting an account in the wrong one is a mistake that
never announces itself afterwards — so somebody has to create the first organisation.
That is all this file does: one row, no users, no buildings, no assets.

## Why the reference data is separate

`regulation_packs` and `compliance_verification_sources` are not *your* data — they are
CIBSE TM46, ASHRAE 100, the SIA and Gas Safe registers, and the rest. Without them the
schema is still valid, but a building has no standard to be scored against and its
benchmark column comes back empty. That is a real answer ("no reference table for this
standard on the platform yet"), not a bug — but it is probably not what you want on a
first run.

Load it, or don't. Nothing else changes. `SKIP_REFERENCE=1 ./db/setup.sh` leaves it
out; the `.cmd` script has no equivalent, so on Windows just delete the file from the
`db` folder before running it, or truncate the two tables afterwards.

## What "empty" means

Every one of the 145 tables has zero rows after `01_schema.sql`. After all three files:

```
organizations                    1
regulation_packs                 4
compliance_verification_sources  118
everything else                  0
```

## How this file was produced

Not written by hand. It is `pg_dump --schema-only` of a database built by running the
real sources against an empty Postgres — the ORM models where a table is declared in
Python, the `.sql` migrations where it is declared in SQL. So it cannot drift from what
the services create at startup, and building it proved several things that only a
from-scratch database reveals.

The build order is not filename order, and that matters:

1. extensions (`pgcrypto`, `uuid-ossp`, `pg_trgm`, `vector`) and the `plenum_cafm` schema
2. **cafm-connector-service's ORM** — 62 tables. `organizations`, `roles`, `permissions`
   and `spare_parts` exist *only* here; no `.sql` in the repo creates them, which is why
   a database built from migrations alone cannot run the platform
3. `fiix_schema_expansion.sql` — 47 more tables, and columns added to the ORM's
4. **svc-operations-intelligence's 25 migrations**, retried until the failure set stops
   shrinking — filename order does not express dependencies between them
5. `udr_phase1_schema.sql` **last**, so its stale definitions skip (see below)
6. the remaining ORMs — ops-intelligence's 32 models, svc-ingestion's 10
7. **the migrations again**, because some reference tables that are declared in Python.
   The `invoices` view reads `plenum_cafm.invoice_lines`, an ORM table, so no amount of
   retrying migrations against each other will conjure it — `create_all` has to happen
   in between. This is what the service itself does at startup

## Known: three tables are declared twice, with different shapes

Whichever definition runs first takes the name. These are the ones where it matters:

| Table | The two definitions | Which wins here, and why |
|---|---|---|
| `meter_readings` | `udr_phase1` says `reading_date`; ops says `reading_at` | **ops** — `reading_at` is what seven files of running ops code read; `reading_date` is read by nothing |
| `buildings` | `udr_phase1` keys on `id`; ops keys on `building_id` | **ops** — the entire building graph keys on `building_id` |
| `contracts` | `udr_phase1` makes it a table; ops makes it a view over `contract_sla_parameters` | **ops** — one copy of a contract, which cannot disagree with the engine that maintains it |

This is why `udr_phase1_schema.sql` runs last. Applied first, as plain filename order
would, it hands the stale shapes the names and the energy engine breaks on an index that
cannot build.

Two statements in that file are therefore skipped, and this is expected:

```
ix_sites_org      ON sites(org_id)       -- ops's sites uses organization_id
ix_sites_location ON sites(location_id)  -- ops's sites has no such column
```

They belong to the superseded `sites` definition. Nothing needs them.

## Four bugs this found

None of these show up on a database that already has data in it. All are fixed in the
branch this schema was built from.

**Registration reported success and created nothing.** `plenum_cafm.users.id` is declared
with a *Python-side* default in the connector ORM, so `create_all` emits the column with
no `DEFAULT`. The auth engine's raw `INSERT` did not name `id` — NOT NULL violation,
caught by an `except IntegrityError` written for a duplicate-email race, and reported as
`202 verification_sent`. On every database built the documented way, nobody could ever
register and the API said it worked.

**The `invoices` view was silently missing.** Its `DO` block caught its own failure and
turned it into a `NOTICE`, so the migration runner saw a clean file and the converging
retry — the mechanism that exists precisely to fix ordering — never retried it.

**`work_orders` was missing three columns.** The graph migration declares `contract_id`,
`raised_at` and `closed_at` inside a `CREATE TABLE IF NOT EXISTS` that is skipped whenever
the connector ORM got there first, then indexes `contract_id`. That index has failed on
every fresh database ever built.

**Creating a building failed on a properly-built schema.** The location insert omitted
`organization_id`, which the ORM's `locations` makes NOT NULL and the migration's does
not. It now introspects the column rather than assuming either shape.

## Regenerating

`setup.sh` and `setup.cmd` only *load* these files; they do not regenerate them. The
generator lives outside the repo (it is a build tool, not a deliverable). To rebuild:
stand up an empty `pgvector/pgvector:pg16`, run the seven steps above in order, then

```bash
pg_dump -U cafm -d hoistra --schema-only --no-owner --no-privileges --no-comments \
  -f 01_schema.sql
```

Dump **inside** the container and `docker cp` it out. Piping `pg_dump` through PowerShell
re-encodes the output and corrupts every non-ASCII character in it.
